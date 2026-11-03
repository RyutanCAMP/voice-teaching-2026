"""ブラウザーでの通し確認（Playwright・同梱の Chromium）。実マイク・実 API は使わない。

偽の SpeechRecognition を注入し、「この位置を上に10mm」を final に返させて、
入力欄に入る（自動では送らない）→ 人の操作として「指示を送る」→ 確認表示 → 承認 → 合成JOBの出力 まで通す。
あわせて、録音中に承認・出力ボタンが押せないこと、取消で入力欄が変わらないこと、
SpeechRecognition が無いブラウザーでの表示を確かめる。

使い方:  python tools/e2e_browser.py        （playwright が無ければ何もせず終わる）
出力は一時フォルダに書く（output/ は使わない）。

上下の向き（CHECKER_026 M2）：「上に10mm」を Z+10 と読むのは、上下の向きの設定の照合に通ったときだけ。
この確認では、一時フォルダに仮の設定（実機で確かめたものではない）を書いてサーバーに渡す。records/ には書かない。
設定が無いときの動き（案内で聞き返す・方向のボタンに「（上）」「（下）」を付けない）は check_updown_unconfirmed で確かめる。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FAKE_TEXT = "この位置を上に10mm"
FAKE_RECOGNITION = """
window.__fakeText = %r;
// Google Chrome として振る舞う（window.__asOther を先に立てたページは、アプリの中のブラウザーなど Chrome 以外）
if (!window.__asOther) Object.defineProperty(navigator, "userAgentData",
  { value: { brands: [{ brand: "Google Chrome", version: "156" }] }, configurable: true });
class FakeRecognition {
  constructor() { window.__rec = this; this.processLocally = false; }
  start() { setTimeout(() => {
    // 認識の失敗（例：Chrome の network）をまねる。window.__fails は、続けて起こす失敗の並び
    const code = (window.__fails && window.__fails.length) ? window.__fails.shift() : window.__failNext;
    window.__failNext = null;
    if (code) {
      this.onerror && this.onerror({ error: code });
      return;
    }
    this.onstart && this.onstart();
    this.onresult && this.onresult({ results: [{ 0: { transcript: "この位置を" }, isFinal: false }] });
  }, 30); }
  stop() { setTimeout(() => {
    this.onresult && this.onresult({ results: [{ 0: { transcript: window.__fakeText }, isFinal: true }] });
    this.onend && this.onend();
  }, 30); }
  abort() {}
  static async available() { return window.__localAvail || "available"; }
  static async install() { window.__localAvail = "available"; return true; }
}
window.SpeechRecognition = FakeRecognition;
window.webkitSpeechRecognition = FakeRecognition;
""" % FAKE_TEXT
NO_RECOGNITION = """
Object.defineProperty(window, "SpeechRecognition", { value: undefined, configurable: true });
Object.defineProperty(window, "webkitSpeechRecognition", { value: undefined, configurable: true });
"""


def write_test_updown(folder: Path) -> dict:
    """一時フォルダに、上下の向きの仮の設定（DEMO_PLACE・DEMO_C_PLACE の座標系）を書く。名前 → 場所。

    上下の向きの設定は JOB の中身を持たない（CHECKER_028 K4）。座標系（ツール番号など）を読むためにジョブを使う。
    """
    from voice_teaching import jbi, updown
    out = {}
    for name in ("DEMO_PLACE.JBI", "DEMO_C_PLACE.JBI"):
        obj = updown.build_config(jbi.load(REPO / "samples" / "dummy" / name), updown.DEFAULT_ROBOT,
                                  "e2e（一時フォルダの仮の設定）", "e2e 用の仮の設定。実機で確かめたものではない")
        out[name] = updown.write_config(obj, Path(folder) / f"updown_{name}.json", overwrite=True)
    return out


def label_overlaps_page(page, host="#fig-3d"):
    """3D の点の名前と、動かす点の名前（変更量つき）の四角が重なっている組。"""
    return page.evaluate("""(host) => {
      const els = [...document.querySelectorAll(host + ' g.pt3 > text, ' + host + ' text.moved-label')];
      const r = els.map((e) => [e.textContent, e.getBoundingClientRect()]);
      const out = [];
      for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
        const a = r[i][1], b = r[j][1];
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) out.push([r[i][0], r[j][0]]);
      }
      return out; }""", host)


def _primaries(page):
    return page.locator("button.primary:not([disabled]):visible, .drop-zone.primary:visible").count()


def check_ux_polish(browser, url, check, shots: Path | None) -> None:
    """UX 監査の直し（feat/ux-polish）：版・使い方の小窓・サンプルで試す・390px・1280×720・止まった候補・読めなかった言葉。"""
    def shot(page, name, full=False):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name), full_page=full)

    def no_hscroll(page):
        w = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        return w[0] <= w[1], w

    def note_gap(page):
        # 画面のいちばん上（scrollY=0）で、図の注意の下の端と、下の固定の帯の上の端の間（負なら隠れている）
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(100)
        return page.evaluate("""() => document.querySelector('.foot').getBoundingClientRect().top
                                - document.querySelector('#figure-note').getBoundingClientRect().bottom""")


    # ---- 1280×720：版・使い方の小窓・サンプルで試す・図の注意・止まった候補の帯 ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    check(pg.is_visible("#app-version") and pg.inner_text("#app-version") == "Ver.1.00-dev",
          f"左下に版を出す：{pg.inner_text('#app-version')}")
    check(pg.is_hidden("#help-pop"), "使い方の小窓は最初は閉じている")
    pg.click("#help-button")
    pop = pg.inner_text("#help-pop") if pg.is_visible("#help-pop") else ""
    check("Y座標を3ミリ下げて" in pop and "承認は確認表示のボタンだけです" in pop
          and pg.get_attribute("#help-button", "aria-expanded") == "true", "「？」で使い方の小窓が開く")
    shot(pg, "9_help.png")
    pg.keyboard.press("Escape")
    check(pg.is_hidden("#help-pop"), "Esc で使い方の小窓が閉じる")
    pg.click("#help-button")
    pg.mouse.click(640, 600)
    check(pg.is_hidden("#help-pop"), "小窓の外を押すと閉じる")

    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    check(pg.inner_text("#file-name") == "DEMO_C_PLACE.JBI" and pg.get_attribute("#step-2", "aria-current") == "step"
          and pg.is_visible("#fig-3d svg"), "「サンプルで試す」で DEMO_C_PLACE を読み、3D で位置を選ぶステップになる")
    # 図の注意は、読み込んだ直後の画面（スクロールしない）で、下の帯に隠れない（682d1ab では隠れていた）
    gap = note_gap(pg)
    check(gap >= 0, f"1280×720：読み込んだ直後、下の帯に図の注意が隠れない（間 {gap:.0f}px）")
    shot(pg, "L1280_a_loaded.png")

    # 止まる位置（C00000）：確認表示を開くと、赤い帯が見える所にある。変更後はオレンジにしない
    pg.locator("#position-chips button", has_text="C00000").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00000")
    pg.fill("#text-input", FAKE_TEXT)
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    pg.wait_for_timeout(300)
    gap = note_gap(pg)
    check(gap >= 0, f"1280×720：変更候補のステップでも、下の帯に図の注意が隠れない（間 {gap:.0f}px）")
    after = pg.eval_on_selector_all("#fig-3d line.seg.after",
                                    "els => els.map(e => [e.getAttribute('stroke'), e.getAttribute('stroke-dasharray')])")
    est = set(pg.eval_on_selector_all("#fig-3d line.seg.before[stroke-dasharray]",
                                      "els => els.map(e => e.getAttribute('stroke-dasharray'))"))
    check(after and all(c != "#ff6a1a" and d and d not in est for c, d in after),
          f"止まった候補の変更後は、オレンジにしない（目安の破線 {sorted(est)} と違う点線）：{after}")
    to_color = pg.evaluate("() => getComputedStyle(document.querySelector('#candidate-view .cand-change .to')).color")
    check(pg.is_visible("#legend-stopped") and pg.is_hidden("#legend-after")
          and "灰色の点線＝止まった候補（承認できません）" in pg.inner_text("#legend") and to_color != "rgb(196, 71, 10)",
          f"止まった候補：凡例に「灰色の点線＝止まった候補」、カードの変更後の値はオレンジにしない（{to_color}）")
    ov = label_overlaps_page(pg)
    check(not ov, f"同じ座標の位置（C00000・C00007）の名前と、動かす点の名前が重ならない：{ov}")
    shot(pg, "L1280_b_blocked_card.png")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.wait_for_timeout(100)
    r = pg.evaluate("() => { const b = document.querySelector('#confirm-blocked').getBoundingClientRect(); return [b.top, b.bottom]; }")
    check(pg.is_visible("#confirm-blocked") and r[0] >= 0 and r[1] <= 720 and not pg.is_enabled("#approve-button"),
          f"1280×720：止まった候補の確認表示で、赤い「承認できません」の帯が見える（上 {r[0]:.0f}px・下 {r[1]:.0f}px）")
    to_color = pg.evaluate("() => getComputedStyle(document.querySelector('#confirm-xyz .cand-change .to')).color")
    check(to_color != "rgb(196, 71, 10)" and pg.is_visible("#confirm-stopped-legend")
          and "灰色の点線＝止まった候補（承認できません）" in pg.inner_text("#confirm-stopped-legend"),
          f"確認表示でも、止まった候補の変更後の値はオレンジにせず（{to_color}）、凡例に止まった候補の線を出す")
    check("ドラッグで回す" not in pg.text_content("#confirm-figure svg"), "確認表示の図には操作のヒントを出さない")
    shot(pg, "L1280_c_blocked_confirm.png")
    pg.click("#confirm-close")
    pg.close()

    # ---- 390px：横にはみ出さない ----
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    ok, w = no_hscroll(pg)
    check(ok, f"390px：最初の画面は横にはみ出さない（{w}）")
    shot(pg, "P390_a_start.png", full=True)
    pg.click("#help-button")
    hb = pg.locator("#help-pop").bounding_box()
    check(hb is not None and hb["x"] >= 0 and hb["x"] + hb["width"] <= 390 and no_hscroll(pg)[0],
          f"390px：使い方の小窓は画面の内側に収まる（x {hb and round(hb['x'])}・幅 {hb and round(hb['width'])}）")
    shot(pg, "P390_d_help.png")
    pg.keyboard.press("Escape")
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.wait_for_timeout(200)
    ok, w = no_hscroll(pg)
    check(ok, f"390px：読み込んだあとも横にはみ出さない（{w}）")
    shot(pg, "P390_b_loaded.png", full=True)
    pg.locator("#position-chips button", has_text="C00005").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
    pg.fill("#text-input", FAKE_TEXT)
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    pg.wait_for_timeout(200)
    ok, w = no_hscroll(pg)
    check(ok, f"390px：変更候補のステップも横にはみ出さない（{w}）")
    shot(pg, "P390_c_candidate.png", full=True)
    pg.close()

    # ---- 読めなかった言葉（CHECKER_023 B1）：facts.unknown_words を目立たせ、言い直しを求める ----
    # このブランチのサーバーはまだ unknown_words を返さないので、別のブランチの返し方をまねる：
    # 「バイオ」を含む文は、聞き返しになる文（「少し動かして」）に差し替えて送り、返事に unknown_words と元の文を足す（画面の確認だけ）
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.add_init_script(FAKE_RECOGNITION)

    def fake_unknown(route):
        req = json.loads(route.request.post_data or "{}")
        if "バイオ" not in str(req.get("text", "")):
            return route.continue_()
        heard = req["text"]
        req["text"] = "少し動かして"
        res = route.fetch(post_data=json.dumps(req))
        body = res.json()
        if body.get("facts") is not None and not body["facts"].get("unknown_words"):
            body["facts"]["unknown_words"] = ["バイオ"]
        body["heard"] = heard
        route.fulfill(response=res, body=json.dumps(body, ensure_ascii=False),
                      headers={**res.headers, "content-type": "application/json; charset=utf-8"})

    pg.route("**/api/hear", fake_unknown)
    pg.goto(url)
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.locator("#position-chips button", has_text="C00005").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
    pg.evaluate("window.__fakeText = 'バイオ 3ミリ 下げて'")
    pg.click("#mic-button")
    pg.click("#consent-yes")
    until(pg, lambda: pg.is_enabled("#voice-stop"))
    pg.click("#voice-stop")
    until(pg, lambda: pg.input_value("#text-input") == "バイオ 3ミリ 下げて")
    pg.click("#voice-cancel")
    pg.click("#text-send")
    until(pg, lambda: pg.is_visible("#reply-message"))
    marks = pg.locator("#reply-message mark.unknown").all_inner_texts()
    check(marks == ["バイオ"] and "バイオ 3ミリ 下げて" in pg.inner_text("#reply-message")
          and pg.inner_text("#next-action") == "もう一度、言い直してください"
          and pg.get_attribute("#step-3", "aria-current") == "step",
          f"読めなかった言葉は聞き取った文の中で目立たせ、見出しは「もう一度、言い直してください」：{marks}")
    pg.wait_for_timeout(400)
    shot(pg, "3f_unknown_word.png")
    pg.close()


# ---- CHECKER_026：M1（狭い画面のはみ出し）・S-MOVJ（関節移動の区間）・S5（確認表示の見出し）・上下の向きの状態 ----
# はみ出しの検査：横スクロールが無いだけでなく、見えている要素の右の端が「画面の幅 − 8px」の内側にあること。
# 画面いっぱいの入れ物（左 0・右がちょうど画面の幅）だけは除く。さらに文字を広げた条件（letter-spacing）でも確かめる
# （CHECKER_026 では Linux のフォントで 390px が 429px にはみ出した。Windows のフォントは境目で通っていた）
OVERFLOW_JS = """(m) => {
  const vw = document.documentElement.clientWidth, out = [];
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.position === 'absolute' && r.width <= 1) continue;  // 読み上げだけの文字（sr-only）
    if (r.left >= -0.5 && r.right <= vw + 0.5 && r.width >= vw - 2) continue;  // 画面いっぱいの入れ物
    if (r.right > vw - m + 0.5 || r.left < -0.5) {
      const id = e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.getAttribute('class') ? '.' + e.getAttribute('class').split(' ')[0] : '');
      out.push(id + ' ' + Math.round(r.left) + '〜' + Math.round(r.right));
    }
  }
  return { scroll: document.documentElement.scrollWidth, vw, out: out.slice(0, 8) };
}"""
WIDE_FONT_CSS = "body, body * { letter-spacing: 0.08em !important; }"
MOVJ_LABEL = "関節移動（MOVJ）：実際の通り道は直線ではありません"


def check_c026(browser, url, check, shots: Path | None) -> None:
    def shot(page, name, full=False):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name), full_page=full)

    def fits(page, what, margin=8):
        r = page.evaluate(OVERFLOW_JS, margin)
        check(r["scroll"] <= r["vw"] and not r["out"],
              f"{what}：横にはみ出さず、右の端は画面の幅 − {margin}px の内側（{r['scroll']}/{r['vw']}・{r['out']}）")

    def load(page, name):
        page.set_input_files("#file-input", str(REPO / "samples/dummy" / name))
        until(page, lambda: page.inner_text("#file-name") == name)
        page.wait_for_timeout(200)

    def to_candidate(page, position, text):
        page.locator("#position-chips button", has_text=position).click()
        until(page, lambda: page.inner_text("#voice-position strong") == position)
        page.fill("#text-input", text)
        page.click("#text-send")
        until(page, lambda: page.get_attribute("#step-4", "aria-current") == "step")
        page.wait_for_timeout(200)

    # ---- M1：360px・390px（ふつうの文字と、広げた文字）・1280px ----
    for w in (360, 390):
        for wide in (False, True):
            tag = f"{w}px" + ("・文字を広げた条件" if wide else "")
            ctx = browser.new_context(viewport={"width": w, "height": 844}, bypass_csp=True)
            pg = ctx.new_page()
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            if wide:
                pg.add_style_tag(content=WIDE_FONT_CSS)
            fits(pg, f"M1 {tag}：最初の画面")
            pg.click("#sample-button")
            pg.wait_for_selector("#job-area:not([hidden])")
            pg.wait_for_timeout(200)
            check(pg.is_visible("#interp-badge") and pg.is_visible("#mode-banner"),
                  f"M1 {tag}：「シミュレーションモード」と解釈の印は、狭い画面でも見えている")
            rows = pg.evaluate("""() => ['.top h1', '#mode-banner', '#interp-badge'].map(
                (q) => Math.round(document.querySelector(q).getBoundingClientRect().top))""")
            check(rows[1] > rows[0] + 10 and abs(rows[1] - rows[2]) <= 4,
                  f"M1 {tag}：1行目はアプリ名、2行目に「シミュレーションモード」と解釈の印を並べる（上の端 {rows}）")
            fits(pg, f"M1 {tag}：読み込んだあと")
            if w == 390 and not wide:
                shot(pg, "C026_390_a_loaded.png")
            to_candidate(pg, "C00005", FAKE_TEXT)
            fits(pg, f"M1 {tag}：変更候補のステップ")
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            pg.wait_for_timeout(150)
            fits(pg, f"M1 {tag}：確認表示")
            if w == 390 and not wide:
                shot(pg, "C026_390_b_confirm.png")
            ctx.close()
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.wait_for_timeout(200)
    fits(pg, "M1 1280px：読み込んだあと")
    hb = pg.locator(".top").bounding_box()
    check(hb is not None and hb["height"] < 80, f"M1 1280px：上の帯は1行のまま（高さ {hb and round(hb['height'])}px）")
    pg.close()

    # ---- S-MOVJ：MOVJ の区間は MOVL と違う見た目・ラベル・再生の印は直線の上を動かない ----
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    load(pg, "PAT_MOVJ.JBI")
    lines = pg.eval_on_selector_all("#fig-3d line.seg.before", """els => els.map(e => ({
        kind: e.dataset.kind, cls: e.getAttribute('class'), dash: e.getAttribute('stroke-dasharray'),
        stroke: e.getAttribute('stroke'), width: parseFloat(e.getAttribute('stroke-width')),
        title: e.querySelector('title') ? e.querySelector('title').textContent : ''}))""")
    movj = [x for x in lines if x["kind"] == "movj"]
    movl = [x for x in lines if x["kind"] == "movl"]
    check(len(movj) == 1 and len(movl) == 5, f"S-MOVJ 3D：PAT_MOVJ の区間は MOVJ 1本・MOVL 5本（{[x['kind'] for x in lines]}）")
    if movj and movl:
        j, l = movj[0], movl[0]
        check("seg-movj" in j["cls"] and j["dash"] and not l["dash"] and j["stroke"] != l["stroke"] and j["width"] < l["width"],
              f"S-MOVJ 3D：MOVJ は細い灰色の点線、MOVL は実線（MOVJ {j['dash']}・{j['stroke']}・{j['width']} ／ MOVL {l['dash']}・{l['stroke']}・{l['width']}）")
        check(MOVJ_LABEL in j["title"] and MOVJ_LABEL not in l["title"],
              f"S-MOVJ 3D：MOVJ の区間に、マウスを重ねると「{MOVJ_LABEL}」が出る（{j['title']!r}）")
    lg = pg.inner_text("#legend-kinds") if pg.is_visible("#legend-kinds") else ""
    check("MOVL：直線移動" in lg and "MOVJ：関節移動（点線は順番だけ。実際の通り道ではありません）" in lg and pg.is_visible("#legend-movj"),
          f"S-MOVJ：凡例に「MOVL：直線移動」「MOVJ：関節移動（点線は順番だけ。実際の通り道ではありません）」を出す（{lg!r}）")
    shot(pg, "C026_1280_a_movj_3d.png")

    def watch_playback(host_marker, seconds=14.0):
        """再生中の状態の文と、印（見えているか）を見続ける。[(文, 印が見えているか)]"""
        seen = []
        js = """(sel) => { const m = document.querySelector(sel);
          const shown = !!m && m.getAttribute('visibility') !== 'hidden';
          return [document.querySelector('#play-status').textContent, shown,
                  document.querySelector('#play-button').classList.contains('playing')]; }"""
        t = 0.0
        while t < seconds:
            s, shown, playing = pg.evaluate(js, host_marker)
            seen.append((s, shown))
            if not playing and t > 0.3:
                break
            pg.wait_for_timeout(25)
            t += 0.025
        return seen

    pg.click("#play-button")
    seen = watch_playback("#fig-3d circle.marker")
    in_j = [shown for s, shown in seen if "MOVJ：関節移動" in s]
    in_l = [shown for s, shown in seen if "再生中" in s and "MOVJ" not in s]
    check(in_j and not any(in_j) and any(in_l),
          f"S-MOVJ 3D 再生：MOVJ の区間では印を直線の上に出さない（MOVJ の間 {len(in_j)}回見て、印あり {sum(in_j)}回・MOVL の間 印あり {sum(in_l)}回）")
    check(seen and seen[-1][0] == "再生が終わりました", f"S-MOVJ 3D 再生：最後まで再生する（{seen[-1][0] if seen else ''}）")

    pg.click('.view-tab[data-view="xz"]')
    pg.wait_for_timeout(150)
    lines2 = pg.eval_on_selector_all("#fig-main line.seg.before", """els => els.map(e => ({
        kind: e.dataset.kind, dash: e.getAttribute('stroke-dasharray'), stroke: e.getAttribute('stroke'),
        width: parseFloat(e.getAttribute('stroke-width')), title: e.querySelector('title') ? e.querySelector('title').textContent : ''}))""")
    j2 = [x for x in lines2 if x["kind"] == "movj"]
    l2 = [x for x in lines2 if x["kind"] == "movl"]
    check(len(j2) == 1 and l2 and j2[0]["dash"] and not l2[0]["dash"] and j2[0]["stroke"] != l2[0]["stroke"]
          and j2[0]["width"] < l2[0]["width"] and MOVJ_LABEL in j2[0]["title"],
          f"S-MOVJ 2D：X・Z の図でも、MOVJ は細い灰色の点線でラベルつき（{j2}）")
    shot(pg, "C026_1280_b_movj_2d.png")
    pg.click("#play-button")
    seen = watch_playback("#fig-main circle.marker")
    in_j = [shown for s, shown in seen if "MOVJ：関節移動" in s]
    in_l = [shown for s, shown in seen if "再生中" in s and "MOVJ" not in s]
    check(in_j and not any(in_j) and any(in_l),
          f"S-MOVJ 2D 再生：MOVJ の区間では印を直線の上に出さない（MOVJ の間 {len(in_j)}回見て、印あり {sum(in_j)}回・MOVL の間 印あり {sum(in_l)}回）")

    # 変更候補（C00003 は前後が MOVL）の確認表示：MOVJ の凡例を出す
    pg.click('.view-tab[data-view="3d"]')
    to_candidate(pg, "C00003", "Zプラス方向に5mm")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.wait_for_timeout(150)
    cj = pg.eval_on_selector_all("#confirm-figure line.seg[data-kind='movj']", "els => els.map(e => e.getAttribute('stroke-dasharray'))")
    check(pg.is_visible("#confirm-movj-legend") and cj and all(cj),
          f"S-MOVJ 確認表示：図の MOVJ の区間は点線で、凡例に関節移動の説明を出す（{cj}）")
    shot(pg, "C026_1280_c_movj_confirm.png")
    pg.close()

    # MOVJ の無いジョブでは、MOVJ の凡例を出さない（PL=0 の MOVL は実線）
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(url)
    load(pg, "PAT_PL0.JBI")
    kinds = pg.eval_on_selector_all("#fig-3d line.seg.before", "els => els.map(e => [e.dataset.kind, e.getAttribute('stroke-dasharray')])")
    check(kinds and all(k == "movl" and d is None for k, d in kinds) and pg.is_hidden("#legend-movj"),
          f"S-MOVJ：PL=0 の MOVL は実線で、MOVJ の凡例は出さない（{kinds}）")
    pg.close()

    # ---- S5：確認表示の見出しは1行「C00005 を Z +10mm … 動かします」。同じ内容を大きな文字で繰り返さない ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    to_candidate(pg, "C00005", "Zプラス方向に10mm")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.wait_for_timeout(150)
    head = pg.inner_text("#confirm-title")
    m = pg.evaluate("""() => { const h = document.querySelector('#confirm-title'), t = document.querySelector('#confirm-text');
        const lh = parseFloat(getComputedStyle(h).lineHeight);
        return [h.getBoundingClientRect().height, lh, parseFloat(getComputedStyle(h).fontSize), parseFloat(getComputedStyle(t).fontSize)]; }""")
    check(head.startswith("C00005 を Z +10mm") and head.endswith("動かします") and "\n" not in head and m[0] < m[1] * 1.5,
          f"S5：見出しは1行（{head!r}・高さ {m[0]:.0f}px・行の高さ {m[1]:.0f}px）")
    conf = pg.inner_text("#confirm-text")
    check("C00005" in conf and m[3] < m[2] and pg.locator("#confirm-xyz .cand-big").count() == 0,
          f"S5：長い説明は見出しの下の小さな文（{m[3]:.0f}px < {m[2]:.0f}px）。表の上に同じ内容の大きな文字を出さない")
    check(pg.is_visible("#approve-button"), "S5：見出しを変えても、承認はボタンだけ（承認ボタンがある）")
    # QA P2：確認済みの設定で作った候補：見出しの向きの名前と、確認した人・日時の1行（タッチ端末でも読めるように文で出す）
    up = pg.inner_text("#confirm-updown") if pg.is_visible("#confirm-updown") else ""
    check("（上へ）" in head and up.startswith("上下の向き：e2e（一時フォルダの仮の設定）さんが確認（") and up.endswith("）"),
          f"QA P2：確認済みの候補の確認表示に「上下の向き：〇〇さんが確認（日時）」の1行を出す（{up!r}）")
    shot(pg, "C026_1280_d_confirm_heading.png")
    pg.close()
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    to_candidate(pg, "C00000", "Zプラス方向に10mm")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    head = pg.inner_text("#confirm-title")
    check(head.startswith("C00000 を Z +10mm") and head.endswith("動かす候補（承認できません）") and not pg.is_enabled("#approve-button"),
          f"S5：止まった候補の見出しは「動かします」と言わない（{head!r}）")
    pg.close()

    # ---- 上下の向きの状態：サーバーの state.updown があるときだけ、位置の欄の近くに小さく出す ----
    updown = {"value": None}

    def add_updown(route):
        res = route.fetch()
        body = res.json()
        if isinstance(body, dict) and isinstance(body.get("state"), dict):
            if updown["value"] is None:
                body["state"].pop("updown", None)  # 返さないサーバーの代わり（M2 と合わせたあとのサーバーは返す）
            else:
                body["state"]["updown"] = updown["value"]
        route.fulfill(response=res, body=json.dumps(body, ensure_ascii=False),
                      headers={**res.headers, "content-type": "application/json; charset=utf-8"})

    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.route("**/api/*", add_updown)
    pg.goto(url)
    pg.click("#sample-button")
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.locator("#position-chips button", has_text="C00005").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
    check(pg.is_hidden("#updown-status"), "上下の向き：サーバーが返さないときは何も出さない")
    cases = [
        ({"status": "unconfirmed", "message": "このジョブの上下の向きは確認されていません"}, "上下の向き：未確認（Z＋・Z−で指示）"),
        ({"status": "confirmed", "confirmed_by": "唐川", "confirmed_at": "2026-11-03T14:00:00+09:00", "message": "", "fingerprint": "x"},
         "上下の向き：確認済み"),
        ({"status": "mismatch", "message": ""}, "上下の向き：確認した座標系・ツールと違います"),
        ({"status": "changed", "message": ""}, "上下の向き：設定が変わっています"),
    ]
    pg.close()
    for value, want in cases:
        updown["value"] = value
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.route("**/api/*", add_updown)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00005").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        got = pg.inner_text("#updown-status") if pg.is_visible("#updown-status") else ""
        check(got == want, f"上下の向き：{value['status']} は「{want}」と出す（{got!r}）")
        if value["status"] == "unconfirmed":
            fs = pg.evaluate("() => parseFloat(getComputedStyle(document.querySelector('#updown-status')).fontSize)")
            check(fs <= 13, f"上下の向き：目立たせすぎない（文字 {fs}px）")
            shot(pg, "C026_1280_e_updown.png")
        pg.close()


REPEAT_SAY = "C00001からC00003の作業を2回から3回にして"


def check_repeat(browser, url, check, shots: Path | None, out: Path, errors: list) -> None:
    """デモ用ジョブ（DEMO_BUTTON_REPEAT）を読む → 指示 → 候補（×2 → ×3）→ 3D の再生で「3回目」→ 確認表示（見出し・差分・注意）
    → 承認ボタン → 出力。声の「はい」では承認しないことは tests/test_repeat_count.py で確かめる。"""
    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name))

    pg = browser.new_page(viewport={"width": 1400, "height": 1000})
    pg.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.click('.view-tab[data-view="3d"]')
    shot(pg, "R1_loaded_3d.png")
    pg.locator("#position-chips button", has_text="C00001").first.click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00001")
    pg.fill("#text-input", REPEAT_SAY)
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    card = pg.inner_text("#candidate-view")
    check("C00001〜C00003" in card and "×2 → ×3" in card, f"回数の変更：候補のカードに区間と ×2 → ×3：{card[:60]}")
    check("新しい移動の区間はありません" in card, "回数の変更：候補の注意に「新しい移動の区間はありません」")
    detail = pg.text_content("#candidate-detail") or ""
    check(detail.count("+ ") == 3 and "MOVL C00002 V=30.0" in detail, f"回数の変更：詳細に足す3行の差分：{detail[:120]}")
    check(pg.locator("#fig-3d text.repeat-label").count() == 1
          and "×2 → ×3" in (pg.text_content("#fig-3d text.repeat-label") or ""), "回数の変更：3D に「×2 → ×3」の印")
    # 再生（候補ができると自動で始まる）：区間の線ごとに「1回目」〜「3回目」。MOVJ は直線で動かさない
    seen, shot_done, waited = set(), False, 0
    while waited < 30000:
        t = pg.inner_text("#play-status")
        if t:
            seen.add(t)
        if "変更後" in t and "3回目" in t and not shot_done:
            shot(pg, "R2_playing_3rd.png")
            shot_done = True
        if t == "再生が終わりました":
            break
        pg.wait_for_timeout(40)
        waited += 40
    joined = " / ".join(sorted(seen))
    check(any("変更前" in x and "2回目" in x for x in seen) and any("変更後" in x and "3回目" in x for x in seen),
          f"回数の変更：再生で、変更前は2回目まで・変更後は3回目まで数える：{joined[:200]}")
    check(not any("変更前" in x and "3回目" in x for x in seen), "回数の変更：変更前の再生には3回目が無い")
    check(any("MOVJ：関節移動" in x for x in seen), "回数の変更：MOVJ の区間は関節移動として出す（直線で再生しない）")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    title = pg.inner_text("#confirm-title")
    check(title == "C00001〜C00003 の作業を 2回 → 3回 にします", f"回数の変更：確認表示の見出し：{title}")
    diff = pg.inner_text("#confirm-diff")
    check(diff.count("+ ") == 3 and "MOVL C00001 V=150.0" in diff, f"回数の変更：確認表示の差分は足す3行：{diff}")
    warn = pg.inner_text("#confirm-warnings")
    check(all(w in warn for w in ("同じ動きを1回ぶん足します", "サイクルの時間が長くなります",
                                  "物理的な安全の保証ではありません", "新しい移動の区間はありません")),
          "回数の変更：確認表示に固定の注意をすべて出す")
    check(not any(w in pg.inner_text("#confirm-overlay") for w in ("安全です", "当たります", "干渉します")),
          "回数の変更：確認表示に安全・距離を断定する言葉が無い")
    check(pg.locator("#confirm-figure text.repeat-label").count() == 1, "回数の変更：確認表示の 3D にも ×2 → ×3")
    check(pg.is_enabled("#approve-button"), "回数の変更：承認ボタンが使える")
    shot(pg, "R3_confirm.png")
    pg.fill("#approver-name", "e2e確認")
    pg.click("#approve-button")
    until(pg, lambda: pg.is_enabled("#export-button"))
    shot(pg, "R4_approved.png")
    pg.click("#export-button")
    pg.wait_for_selector("#export-result:not([hidden])")
    f = out / "sim" / "DEMO_BUTTON_REPEAT_SIM.JBI.txt"
    check(f.is_file() and "出力しました" in pg.inner_text("#export-result"), f"回数の変更：出力 {f.name}")
    if f.is_file():
        a = (REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI").read_bytes().splitlines()
        b = f.read_bytes().splitlines()
        check(len(b) == len(a) + 3 and b.count(b"MOVL C00002 V=30.0") == 3 and b[1] == b"//NAME DEMO_BUTTON_REPEAT_SIM",
              f"回数の変更：出力は3行増えてジョブ名だけ変わる（{len(a)} → {len(b)}行）")
    shot(pg, "R5_exported.png")
    pg.close()


def until(pg, cond, timeout_ms: int = 5000) -> None:
    """条件が成り立つまで待つ（画面の CSP が文字列の評価を禁じているので、Python 側で確かめる）。"""
    waited = 0
    while not cond():
        if waited >= timeout_ms:
            raise TimeoutError("待っても画面が変わりませんでした")
        pg.wait_for_timeout(50)
        waited += 50


def run(out_dir: Path | None = None, log=print, shots: Path | None = None) -> list[str]:
    """通し確認をして、失敗の一覧を返す（空なら全部通った）。shots を渡すと、画面のスクリーンショットをそこに置く。"""
    from playwright.sync_api import sync_playwright

    from voice_teaching import sim
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp())
    # 出力先は sim.DEFAULT_OUT_DIR に固定（呼び出し側からは選べない）。この確認の間だけ一時フォルダに差し替え、最後に戻す
    saved_out_dir = sim.DEFAULT_OUT_DIR
    sim.DEFAULT_OUT_DIR = out / "sim"
    updown_cfg = write_test_updown(out / "updown")
    # はじめの通し確認は DEMO_PLACE（C00004 を「上に10mm」）。DEMO_C_PLACE の確認の前に設定を差し替える（読込のときに決まる）
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=updown_cfg["DEMO_PLACE.JBI"])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    fails: list[str] = []
    errors: list[str] = []

    def check(ok: bool, what: str) -> None:
        log(("OK  " if ok else "NG  ") + what)
        if not ok:
            fails.append(what)

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(viewport={"width": 1400, "height": 1000})
            pg.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            check(pg.get_attribute("#voice-controls", "hidden") is None
                  and pg.get_attribute("#voice-unsupported", "hidden") is not None, "音声の欄が出る（使えない旨の文は出ない）")
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_PLACE.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            pg.locator("#position-chips button", has_text="C00004").first.click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00004")
            check(pg.is_enabled("#mic-button") and not pg.is_checked("#voice-mode-browser"),
                  "方式を選ばなくても、マイクのボタンを押せる（方式は未選択のまま）")
            pg.click("#mic-button")
            check(pg.is_visible("#voice-consent") and "Google のサーバー" in pg.inner_text("#consent-text")
                  and pg.inner_text("#mic-label") == "マイク OFF",
                  "初めて 🎤 を押すと、声をどこで文字にするかを伝えて同意を聞く（まだ聞かない）")
            pg.click("#consent-no")
            check(pg.is_hidden("#voice-consent") and not pg.is_checked("#voice-mode-browser")
                  and pg.inner_text("#mic-label") == "マイク OFF", "「文字で入力する」を選ぶと、音声は使わない")

            # 取消：入力欄は変わらない
            pg.fill("#text-input", "手で書いた文")
            pg.click("#mic-button")
            pg.click("#consent-yes")
            check(pg.is_checked("#voice-mode-browser") and pg.evaluate("localStorage.getItem('vt-voice-mode')") == "browser",
                  "「使う」で同意すると、ブラウザー標準を使い、このブラウザーに覚える")
            until(pg, lambda: pg.is_enabled("#voice-cancel"))
            until(pg, lambda: pg.inner_text("#voice-partial") == "この位置を")
            check(all(not pg.is_enabled(s) for s in ["#file-input", "#text-send", "#text-input"]),
                  "録音中は読込・入力・指示を送るが使えない")
            check(pg.is_enabled("#voice-stop") and pg.is_enabled("#voice-cancel"), "録音中は終了と取消が使える")
            pg.click("#voice-cancel")
            until(pg, lambda: not pg.is_enabled("#voice-cancel"))
            check(pg.input_value("#text-input") == "手で書いた文", "取消で入力欄が変わらない")
            check(pg.inner_text("#voice-partial") == "", "取消で未確定の文が消える")

            # 認識で「マイナス」が落ちた文（実マイクで「Xマイナス方向に5ミリ」→「X 方向に 5mm」）：送る前に知らせる
            pg.fill("#text-input", "")
            pg.evaluate("window.__fakeText = 'X 方向に 5mm'")
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-stop"))
            pg.click("#voice-stop")
            until(pg, lambda: pg.input_value("#text-input") == "X 方向に 5mm")
            check(pg.is_visible("#voice-hint") and "＋か−が入っていません" in pg.inner_text("#voice-hint"),
                  "声から入った文に軸の向きが無ければ、送る前に「＋か−が入っていません」と知らせる")
            pg.fill("#text-input", "X マイナス方向に 5mm")
            check(pg.is_hidden("#voice-hint"), "文を直して向きを入れると、知らせは消える")
            pg.fill("#text-input", "")
            pg.click("#voice-cancel")
            until(pg, lambda: pg.inner_text("#mic-label") == "マイク OFF")
            pg.evaluate("window.__fakeText = %r" % FAKE_TEXT)

            # 録音 → 終了 → 入力欄に入るだけ（自動では送らない）
            pg.fill("#text-input", "")
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-stop"))
            pg.click("#voice-stop")
            until(pg, lambda: pg.input_value("#text-input") == FAKE_TEXT)
            check(True, f"final「{FAKE_TEXT}」が入力欄に入る")
            pg.wait_for_timeout(300)
            check(pg.inner_text("#heard-text") == "（まだありません）" and pg.is_hidden("#confirm-overlay"),
                  "自動では送らない（指示の結果も確認表示も出ない）")

            # 人が「指示を送る」→ 変更候補（経路を再生）→「確認して承認へ」→ 全画面の確認表示 → 承認
            pg.click("#text-send")
            until(pg, lambda: pg.is_enabled("#open-confirm"))
            check(pg.is_hidden("#confirm-overlay"), "候補ができても確認表示は自動で開かない（先に図で確かめる）")
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            confirm = pg.inner_text("#confirm-text")
            check("C00004" in confirm and "+10.000mm" in confirm, f"確認表示：{confirm[:60]}…")
            pg.fill("#approver-name", "e2e確認")
            pg.click("#approve-button")
            until(pg, lambda: pg.is_enabled("#export-button"))
            check(True, "承認すると出力ボタンが使える")

            # 承認・出力の段階（ステップ4）では、マイクのカードを出さない（録音と承認・出力が重ならない）
            check(pg.is_hidden("#mic-button") and pg.is_hidden("#instruction-panel"),
                  "ステップ4ではマイクを出さない（録音しながら承認・出力できない）")

            pg.click("#export-button")
            pg.wait_for_selector("#export-result:not([hidden])")
            result = pg.inner_text("#export-result")
            files = list((out / "sim").glob("*"))
            check([f.name for f in files] == ["DEMO_PLACE_SIM.JBI.txt"], f"出力の名前は .JBI.txt：{[f.name for f in files]}")
            check("出力しました" in result and len(files) == 1, f"出力：{[f.name for f in files]}")
            if files:
                a = (REPO / "samples/dummy/DEMO_PLACE.JBI").read_bytes().splitlines()
                c = files[0].read_bytes().splitlines()
                diff = [(x.decode(), y.decode()) for x, y in zip(a, c) if x != y]
                check(len(a) == len(c) and len(diff) == 2, f"元との差は2行：{diff}")
            log_text = pg.inner_text("#work-log")
            check("音声：" + FAKE_TEXT in log_text, "作業ログに音声として記録（source=voice）")

            # ここからは DEMO_C_PLACE の確認が中心：上下の向きの仮の設定を DEMO_C_PLACE のものにする
            srv.flow.updown_config = updown_cfg["DEMO_C_PLACE.JBI"]
            # 4ステップの画面（DEMO_C_PLACE をドラッグ＆ドロップ）
            check_four_steps(b, url, check, shots)
            # 作業の回数を変える（2回 → 3回。docs/REPEAT_COUNT_DESIGN.md）
            check_repeat(b, url, check, shots, out, errors)

            # Chrome 以外（ChatGPT のアプリの中のブラウザーなど）：Chrome で開くよう案内し、通信の失敗は聞き直さない
            pg3 = b.new_page()
            pg3.add_init_script("window.__asOther = true")
            pg3.add_init_script(FAKE_RECOGNITION)
            pg3.goto(url)
            pg3.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_PLACE.JBI"))
            pg3.wait_for_selector("#job-area:not([hidden])")
            pg3.locator("#position-chips button", has_text="C00004").first.click()
            until(pg3, lambda: pg3.inner_text("#voice-position strong") == "C00004")
            check(pg3.is_visible("#browser-note") and "Google Chrome" in pg3.inner_text("#browser-note")
                  and pg3.is_visible("#copy-url"), "Chrome 以外では「Google Chrome で開いてください」とアドレスのコピーを出す")
            if shots is not None:
                pg3.screenshot(path=str(shots / "8_other_selected.png"))
            pg3.evaluate("window.__fails = ['network']")
            pg3.click("#mic-button")
            check("提供元のサーバー" in pg3.inner_text("#consent-text"), "Chrome 以外の同意の文は、送り先を決めつけない")
            pg3.click("#consent-yes")
            until(pg3, lambda: pg3.is_visible("#voice-error"))
            check("Google Chrome で開いてください" in pg3.inner_text("#voice-error")
                  and pg3.inner_text("#mic-label") == "マイク OFF",
                  "Chrome 以外で通信に失敗したら、聞き直さずに Chrome で開くよう案内する")
            side_text = pg3.inner_text(".side")
            mic_op = float(pg3.evaluate("() => getComputedStyle(document.querySelector('#mic-button')).opacity"))
            check(side_text.count("Google Chrome で開いてください") == 1 and pg3.is_visible("#copy-url")
                  and "primary" in (pg3.get_attribute("#copy-url", "class") or "") and _primaries(pg3) == 1 and mic_op < 0.5,
                  f"Chrome 以外の案内は1回だけ（{side_text.count('Google Chrome で開いてください')}回）、"
                  f"「アドレスをコピー」だけが目立ち、マイクは薄い（不透明度 {mic_op}）")
            if shots is not None:
                pg3.screenshot(path=str(shots / "8b_other_network.png"))
            # 聞き返しの見出しは、「この画面では音声を使えません」より先に出す
            pg3.fill("#text-input", "少し動かして")
            pg3.click("#text-send")
            until(pg3, lambda: pg3.is_visible("#reply-message"))
            check(pg3.inner_text("#next-action") == "もう一度、言い直してください"
                  and "primary" in (pg3.get_attribute("#copy-url", "class") or ""),
                  f"Chrome 以外でも、聞き返しの間は聞き返しの見出しを出す：{pg3.inner_text('#next-action')}")
            # 別のジョブを読んでも、Chrome 以外なら案内は残す
            pg3.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI"))
            until(pg3, lambda: pg3.inner_text("#file-name") == "DEMO_C_PLACE.JBI")
            pg3.locator("#position-chips button", has_text="C00005").first.click()
            until(pg3, lambda: pg3.inner_text("#voice-position strong") == "C00005")
            check("need-chrome" in (pg3.get_attribute("body", "class") or "")
                  and pg3.inner_text("#next-action") == "この画面では音声を使えません",
                  "Chrome 以外では、別のジョブを読んでも Chrome の案内を残す")
            pg3.close()

            # 聞き返しの短い選択肢（CHECKER_021 10章・CHECKER_022）：「Yを3mm下げて」は2択。押すと候補を作るだけ（承認しない）
            pg4 = b.new_page(viewport={"width": 1400, "height": 1000})
            pg4.add_init_script(FAKE_RECOGNITION)
            pg4.goto(url)
            pg4.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI"))
            pg4.wait_for_selector("#job-area:not([hidden])")
            pg4.locator("#position-chips button", has_text="C00005").first.click()
            until(pg4, lambda: pg4.inner_text("#voice-position strong") == "C00005")
            pg4.fill("#text-input", "Yを3mm下げて")
            pg4.click("#text-send")
            until(pg4, lambda: pg4.locator("#reply-message button.choice").count() == 2)
            labels = pg4.locator("#reply-message button.choice").all_inner_texts()
            check(labels == ["Y座標を −3mm", "下方向へ 3mm"] and pg4.get_attribute("#step-3", "aria-current") == "step",
                  f"「Yを3mm下げて」は候補を作らず、短い2択で聞き返す：{labels}")
            check(pg4.inner_text("#next-action") == "どちらですか？",
                  f"選択肢で聞き返している間の見出しは「どちらですか？」：{pg4.inner_text('#next-action')}")
            check(pg4.locator("#reply-message button.choice.primary").count() == 0,
                  "選択肢のボタンはどれも同じ重さ（目立たせない）")
            if shots is not None:
                pg4.screenshot(path=str(shots / "3e_choices.png"))
            pg4.locator("#reply-message button.choice", has_text="Y座標を").click()
            until(pg4, lambda: pg4.get_attribute("#step-4", "aria-current") == "step")
            cv = pg4.inner_text("#candidate-view")
            # 表示のマイナスは "−"（U+2212）。数はサーバーの値のまま（以前の確認は ASCII の "-" を見ていた）
            check("Y方向に −3.000mm" in cv and "150.000" in cv and "147.000" in cv and pg4.is_hidden("#export-button")
                  and pg4.is_enabled("#open-confirm"),
                  "選択肢を押すと Y −3mm の候補（変更前後の XYZ 付き）になるだけで、承認・出力はされない（確認へ進む）")
            ov4 = label_overlaps_page(pg4)
            check(not ov4, f"3D の名前どうしが重ならない（Y −3mm の候補）：{ov4}")
            change = pg4.inner_text("#candidate-view .cand-change")
            check(re.sub(r"\s", "", change) == "Y150.000→147.000",
                  f"候補のカードに、変わる値を大きく（サーバーの値のまま）：{change}")
            segs = pg4.eval_on_selector_all("#fig-3d line.seg.after",
                                            "els => els.map(e => [e.dataset.from, e.dataset.to, e.getAttribute('stroke')])")
            check(segs and all("C00005" in (f, t) and color == "#ff6a1a" for f, t, color in segs)
                  and len(segs) == 2,
                  f"3D の変更後（オレンジ）は、動かす点に触れる区間だけ：{segs}")
            labels3 = [t for t in pg4.eval_on_selector_all("#fig-3d svg text", "els => els.map(e => e.textContent)")
                       if "C00005" in t]
            check(len(labels3) == 1 and "Y −3.000mm" in labels3[0],
                  f"動かす点の名前は1つだけ（変更量つき）：{labels3}")
            if shots is not None:
                pg4.screenshot(path=str(shots / "4b_candidate_y.png"))
            pg4.click("#open-confirm")
            pg4.wait_for_selector("#confirm-overlay:not([hidden])")
            cx = pg4.inner_text("#confirm-xyz")
            check("指示：「C00005をY座標を3mm下げて」" in cx and "147.000" in cx and "未確認" in cx,
                  "確認表示に、指示の文・変更量・変更後の XYZ と、X・Y の向きの名前は未確認であることを出す")
            if shots is not None:
                pg4.screenshot(path=str(shots / "6b_confirm_xyz.png"))
            pg4.close()

            check_ux_polish(b, url, check, shots)
            check_c026(b, url, check, shots)
            check_dialog(b, check, shots, errors, updown_cfg["DEMO_C_PLACE.JBI"])
            check_talk(b, check, shots, errors, out)
            check_updown_unconfirmed(b, check, shots, errors)

            # SpeechRecognition が無いブラウザー
            pg2 = b.new_page()
            pg2.add_init_script(NO_RECOGNITION)
            pg2.goto(url)
            check(pg2.get_attribute("#voice-unsupported", "hidden") is None
                  and pg2.get_attribute("#voice-controls", "hidden") is not None
                  and "このブラウザでは音声入力を使えません。文字で入力してください" in pg2.text_content("#voice-unsupported"),
                  "音声認識が無いブラウザーでは文字入力を案内する")
            b.close()
    finally:
        srv.shutdown()
        sim.DEFAULT_OUT_DIR = saved_out_dir
        srv.server_close()
    check(not errors, f"画面のエラー：{errors or 'なし'}")
    return fails


def check_talk(browser, check, shots: Path | None, errors: list, out: Path) -> None:
    """Claude に聞く（CHECKER_031・台本 Ver.6.1 Scene 3）。偽の client（本物の API は呼ばない）。
    返事のたびに根拠の区間が自動で光る・「考えています…」・人が教えた意味の印（吹き出し・覚えていること・3D）・話し手・390px。"""
    import time
    from types import SimpleNamespace

    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    def ans(i, reply, lines, intent="explain", change=None):
        data = {"reply": reply, "evidence_lines": lines, "intent": intent, "change_request": change}
        return SimpleNamespace(stop_reason="tool_use", content=[
            SimpleNamespace(type="tool_use", id=f"toolu_e2e{i}", name="answer", input=data)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0, cache_creation=None))

    block = [21, 22, 23, 24, 25, 26]
    # 1ターン目は本物の Claude の答え（2026-11-03 シクロの通し）と同じ文・同じ根拠の行（20〜27。MOVJ の行を含む）
    replies = [ans(1, "最初の位置に動いたあと、同じ3か所をめぐる動きを2回くり返し、最後に別の位置へ移ります。"
                      "作業の目的は分からないので、何の作業か教えてもらえますか？", list(range(20, 28))),
               ans(3, "ボタンを押す動きを、2回から3回にしますか？", block, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 3})]

    class Slow:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            time.sleep(0.6)  # 「考えています…」を見るため
            return replies.pop(0)

    ex = cx.Explainer("sk-ant-api03-E2E-FAKE", "claude-sonnet-5-5", client=Slow(),
                      usage_log=cx.UsageLog(out / "talk_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        for width in (1280, 390):
            pg = browser.new_page(viewport={"width": width, "height": 900})
            pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            pg.goto(url)
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
            pg.wait_for_selector("#ask-panel:not([hidden])")
            until(pg, lambda: pg.is_enabled("#ask-send"))
            if width == 390:
                w = pg.evaluate("document.documentElement.scrollWidth")
                check(w <= 390, f"会話 390px：「Claude に聞く」の欄があっても横にはみ出さない（{w}px）")
                pg.close()
                continue
            pg.fill("#ask-input", "このロボット、何をしている？")
            pg.click("#ask-send")
            pg.wait_for_selector("#ask-turns .thinking", timeout=3000)
            check("考えています" in pg.inner_text("#ask-turns .thinking"), "会話：返事を待つ間「考えています…」が出る")
            pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
            until(pg, lambda: pg.locator("#fig-3d line.ask-hl").count() == 5)
            check(sorted(pg.eval_on_selector_all("#fig-3d line.ask-hl", "els => els.map(e => +e.dataset.line)")) == [22, 23, 24, 25, 26],
                  "会話：返事が出たら、くり返しの動き（22〜26行目の線）が 3D で自動で強く光る（押さなくてよい）")
            ctx = pg.locator("#fig-3d line.ask-hl-context")
            check(sorted(pg.eval_on_selector_all("#fig-3d line.ask-hl-context", "els => els.map(e => +e.dataset.line)")) == [21, 27]
                  and all(float(x) < 0.5 for x in pg.eval_on_selector_all("#fig-3d line.ask-hl-context", "els => els.map(e => e.getAttribute('stroke-opacity'))")),
                  "会話：区間に入ってくる移動（21行目・C00000 から）と、区間のあとの移動（27行目）は、控えめな色で出す")
            movj = pg.locator("#fig-3d line.ask-hl-context.seg-movj")
            check(movj.count() == 1 and movj.first.get_attribute("stroke-dasharray") == "2 6"
                  and pg.locator('#fig-3d line.seg.before.seg-movj[data-to="C00004"]').get_attribute("stroke-dasharray") == "2 6",
                  "会話：控えめに出しても MOVJ の区間は点線のまま（直線の通り道に見せない）")
            if shots is not None:
                pg.screenshot(path=str(shots / "talk_1_highlight.png"))
            check(pg.locator("#ask-turns .bubble.claude").count() == 1, "会話：本物と同じ文（3か所・2回）がそのまま表示される")
            check("21〜26行目：C00001→C00002→C00003（2回）" in pg.inner_text("#ask-turns .supplement"),
                  "会話：番号は補足として小さく出る")
            whos = pg.locator("#ask-turns .bubble .who").all_inner_texts()
            check(any(x.startswith("Claude") for x in whos) and "人" in whos, f"会話：話し手（人・Claude）が出る：{whos}")
            pg.fill("#ask-input", "ボタンを押してるんだよ")
            pg.click("#ask-send")
            pg.wait_for_selector("#ask-turns .talk-choice")
            labels = pg.locator("#ask-turns .talk-choice").all_inner_texts()
            until(pg, lambda: pg.locator("#fig-3d line.ask-hl").count() == 5)
            check(labels == ["この動きです", "ほかの動き"] and "この光っている動き（21〜26行目のくり返し）" in pg.inner_text("#ask-turns")
                  and pg.locator("#fig-3d line.ask-hl").count() == 5 and pg.locator("#fig-3d g.taught-mark").count() == 0,
                  f"会話：区間が1つなら、その区間を光らせて［この動きです］［ほかの動き］で確かめる（押すまで覚えない）：{labels}")
            pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
            until(pg, lambda: pg.locator("#fig-3d g.taught-mark").count() == 1)
            check(pg.locator("#ask-turns .bubble.taught .mark-human").count() == 1
                  and pg.is_visible("#ask-memory-box") and "ボタンを押してる" in pg.inner_text("#ask-memory"),
                  "会話：人が教えた意味が「人が教えた（この会話の中だけ）」の印で吹き出しと覚えていることの欄に出る")
            check("（人が教えた）" in (pg.text_content("#fig-3d g.taught-mark") or ""), "会話：3D にも「人が教えた」の印が出る")
            pg.fill("#ask-input", "じゃあ、3回にして")
            pg.click("#ask-send")
            until(pg, lambda: pg.locator("#ask-turns .bubble.record").count() == 1)
            txt = pg.inner_text("#ask-turns")
            check("ボタンを押す動きを、2回から3回にしますか？" in txt and cx.READ_ONLY_NOTE in txt
                  and pg.is_disabled("#approve-button") and pg.is_hidden("#approval-status"),
                  "会話：「3回にして」は確かめる文とアプリの決まった文だけ（候補・承認は作らない）")
            check(pg.locator("#ask-turns .who", has_text="アプリ").count() >= 2, "会話：アプリの文は「アプリ」と出る")
            if shots:
                pg.screenshot(path=str(shots / "TALK_1280_scene3.png"), full_page=True)
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


class CountingInterpreter:
    """モックを包み、解釈の係が呼ばれた回数を数える（会話の確認用。本物の Claude は呼ばない）。"""

    def __init__(self):
        from voice_teaching.claude_interpreter import MockInterpreter
        self.inner = MockInterpreter()
        self.kind, self.model, self.notice = self.inner.kind, self.inner.model, self.inner.notice
        self.locator_label = self.inner.locator_label
        self.decides = 0
        self.locates = 0

    def decide(self, *a, **k):
        self.decides += 1
        return self.inner.decide(*a, **k)

    def locate(self, *a, **k):
        self.locates += 1
        return self.inner.locate(*a, **k)


def check_dialog(browser, check, shots: Path | None, errors: list, updown_config=None) -> None:
    """Claude との会話（モック）：唐川さんの会話・くり返しの2択・6つの方向・承認待ちの候補・途中の打ち消し・合成でないジョブ。

    この確認だけ、呼ばれた回数を数える解釈の係でサーバーを別に立てる（本物の Claude は呼ばない）。
    """
    import base64

    from voice_teaching.web import server

    interp = CountingInterpreter()
    srv = server.make_server(port=0, interpreter=interp, updown_config=updown_config)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name))

    def send(page, text):
        page.fill("#text-input", text)
        page.click("#text-send")

    def bubbles(page):
        return page.eval_on_selector_all("#dialog-turns li", "els => els.map(e => [e.querySelector('.who').textContent, e.querySelector('.said').textContent])")

    def labels(page):
        return [t.strip() for t in page.locator("#dialog-choices button").all_inner_texts()]

    def last_said(page):
        b = bubbles(page)
        return b[-1][1] if b else ""

    def watch(page):
        page.on("pageerror", lambda e: errors.append(f"pageerror（会話）: {e}"))
        page.on("console", lambda m: errors.append(f"console（会話） {m.type}: {m.text}") if m.type == "error" else None)

    try:
        # ---- 1. 唐川さんの会話：置くところ → そうそう → 3mm → 方向のボタン → 確認表示 → ボタンの承認 ----
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, bypass_csp=True)
        pg = ctx.new_page()
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        check(pg.is_visible("#talk-first"), "会話：ステップ2に「位置を選ばずに話す」がある")
        pg.click("#talk-first")
        until(pg, lambda: pg.get_attribute("#step-3", "aria-current") == "step")
        check(pg.inner_text("#voice-position strong") == "位置は未選択" and pg.is_hidden("#dialog-panel"),
              "会話：位置を選ばずにステップ3へ進める（会話の欄は、まだ出さない）")
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: pg.is_visible("#dialog-panel") and len(bubbles(pg)) >= 2)
        b = bubbles(pg)
        ev = pg.inner_text("#dialog-evidence")
        check(b[0] == ["人", "置くところを少し手前にして"] and b[1] == ["モック（テスト用）", "「置くところ」は C00005 のことでしょうか？"],
              f"会話：人の吹き出しと、モックの問いかけ（位置の候補）：{b}")
        check("モック（テスト用）" in pg.inner_text("#dialog-engine") and "本物の Claude ではありません" in pg.inner_text("#dialog-engine"),
              f"会話：モックであることを、会話の欄にはっきり出す：{pg.inner_text('#dialog-engine')}")
        check("30行目 MOVL C00005 V=50.0 PL=0" in ev and "31行目 TIMER T=3.00" in ev and "（待つ）" in ev
              and "32行目 DOUT OT#(1) OFF" in ev and "（信号を切る）" in ev and "推定・未確定" in ev
              and "離す" not in ev and "つかむ" not in ev,
              "会話：根拠のカードは、プログラムが命令の行から作った文（待つ・信号を切る。意味は断定しない）")
        check(labels(pg) == ["C00005 です", "ほかの位置"], f"会話：選択肢は［C00005 です］［ほかの位置］：{labels(pg)}")
        check(pg.locator('#fig-3d g.pt3.cand[data-names~="C00005"] circle.cand-ring').count() == 1
              and pg.locator("#fig-3d g.pt3.cand").count() == 1,
              "会話：3D で候補の点（C00005）だけを輪で光らせる（選んではいない）")
        check(pg.get_attribute("#step-3", "aria-current") == "step" and pg.is_hidden("#reply-message")
              and pg.inner_text("#next-action") == "この位置で合っていますか？",
              "会話：まだ変更の候補は無い（ステップ3）。見出しは「この位置で合っていますか？」")
        check(interp.locates == 1 and interp.decides == 0, f"会話：位置の候補を1回だけ頼む（{interp.locates}・{interp.decides}）")
        shot(pg, "d1_locate.png")

        send(pg, "そうそう")
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        check(last_said(pg).startswith("どちらの方向（X・Y・Z の＋−）に、何mm動かしますか？") and "「手前」" in last_said(pg),
              f"会話：「そうそう」で C00005 を選び、方向と量を聞く（手前は軸にしない）：{last_said(pg)}")
        check(labels(pg) == ["X＋", "X−", "Y＋", "Y−", "Z＋（上）", "Z−（下）"],
              f"会話：方向は X・Y・Z の＋−の6つのボタン：{labels(pg)}")
        check(bubbles(pg)[-1][0] == "アプリ" and [w for w, _ in bubbles(pg)].count("モック（テスト用）") == 1,
              f"会話：方向と量の問いは決まった文なので「アプリ」。モックと出すのは位置の候補の文だけ：{[w for w, _ in bubbles(pg)]}")
        check(pg.is_visible("#dialog-estimated") and "推定（未確定）" in pg.inner_text("#dialog-estimated"),
              "会話：選んだ位置に「推定（未確定）」と残す")
        shot(pg, "d2_ask_move.png")

        send(pg, "3mm")
        until(pg, lambda: labels(pg) and labels(pg)[0] == "X＋ 3mm")
        check(labels(pg) == ["X＋ 3mm", "X− 3mm", "Y＋ 3mm", "Y− 3mm", "Z＋（上）3mm", "Z−（下）3mm"]
              and "3mm ですね" in last_said(pg) and pg.get_attribute("#step-3", "aria-current") == "step",
              f"会話：「3mm」のあとも方向が無いので、量つきの6つのボタンで聞く：{labels(pg)}")
        check(interp.decides == 0, "会話：方向が決まるまで、変更は頼まない")
        shot(pg, "d3_amount.png")

        pg.locator("#dialog-choices button", has_text="Y− 3mm").click()
        until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
        cv = pg.inner_text("#candidate-view")
        check("Y方向に −3.000mm" in cv and "C00005をYのマイナス3ミリ" in cv and "推定（未確定）" in cv,
              f"会話：ボタンで方向を選ぶと、決まった文を今の流れに通して変更の候補になる：{cv[:80]}")
        check(pg.is_visible("#dialog-panel") and last_said(pg).startswith("変更前後を表示します。これでよろしいですか？")
              and bubbles(pg)[-1][0] == "アプリ",
              "会話：最後に「変更前後を表示します。これでよろしいですか？」（プログラムの決まった文なので話し手は「アプリ」）")
        check(interp.decides == 1, f"会話：変更の解釈は1回だけ（{interp.decides}）")
        pg.wait_for_timeout(300)
        shot(pg, "d4_candidate.png")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        ab = pg.locator("#approve-button").bounding_box()
        check("推定で選んだ位置（「置くところ」・推定（未確定））" in pg.inner_text("#confirm-xyz") and pg.is_visible("#approve-button")
              and ab is not None and ab["y"] + ab["height"] <= 720,
              f"会話：確認表示にも「推定で選んだ位置」を出す。承認ボタンは 1280×720 でスクロールせずに見える（下の端 {ab and ab['y'] + ab['height']:.0f}px）")
        shot(pg, "d5_confirm.png")
        pg.fill("#approver-name", "テスト")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        check(True, "会話：承認ボタンで承認できる（声では承認しない）")

        # 承認待ち（承認済み・未出力）の候補がある間：新しい移動の指示は、解釈の係を呼ぶ前に断る
        sid = next(iter(srv.flow._entries))
        before = interp.decides
        r = pg.request.post(url + "api/hear", data={"session_id": sid, "text": "この位置を上に2mm", "source": "text"}).json()
        r2 = pg.request.post(url + "api/hear", data={"session_id": sid, "text": "置くところを上に2mm", "source": "text"}).json()
        # 1つ目は承認したあと（承認を取り消したときだけの決まった文。唐川さんの決定）、2つ目は承認前の文
        check(r["status"] == "stop" and r["message"] == "新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。内容を確認して、もう一度承認してください。"
              and r2["status"] == "stop" and r2["message"].startswith("承認待ちの変更候補があります")
              and interp.decides == before and interp.locates == 1,
              f"承認待ちの候補があるとき、新しい移動の指示は解釈の係を呼ばずに断る（{interp.decides - before}回）")
        ctx.close()

        # ---- 2. 途中の打ち消し（「そうじゃなくて」）→ 止まって同じ所。ほかの位置 → 手で選ぶ → 6つの方向 ----
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        send(pg, "そうじゃなくて")
        until(pg, lambda: "「ほかの位置」を押してください" in last_said(pg))
        check(pg.inner_text("#voice-position strong") == "位置は未選択" and labels(pg) == ["C00005 です", "ほかの位置"]
              and pg.get_attribute("#step-3", "aria-current") == "step"
              and last_said(pg) == "分かりました。合っていなければ「ほかの位置」を押してください",
              "会話：途中の「そうじゃなくて」は止まり、位置は選ばれず、同じ問いのまま（S-pick：「ほかの位置」を案内する）")
        shot(pg, "d6_negation.png")
        pg.locator("#dialog-choices button", has_text="ほかの位置").click()
        until(pg, lambda: pg.get_attribute("#step-2", "aria-current") == "step")
        check(pg.inner_text("#next-action") == "位置を選んでください" and pg.is_visible("#dialog-panel"),
              "会話：［ほかの位置］で、手で位置を選ぶステップに戻る")
        pg.locator("#position-chips button", has_text="C00004").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00004")
        check(labels(pg) == ["X＋", "X−", "Y＋", "Y−", "Z＋（上）", "Z−（下）"] and pg.is_hidden("#dialog-estimated"),
              f"会話：手で選んだ位置でも、方向は6つのボタン（推定の印は付けない）：{labels(pg)}")
        send(pg, "右に3mm")
        until(pg, lambda: "「右」が X・Y・Z のどれに当たるか" in last_said(pg))
        # （このあと 6つの方向のボタンの確認）
        check(pg.get_attribute("#step-3", "aria-current") == "step" and len(labels(pg)) == 6,
              "会話：「右に3mm」は軸にしない（6つのボタンで聞き直す）")
        shot(pg, "d7_six_directions.png")
        pg.close()

        # ---- 2b. 会話の中で止めた・聞き返した理由は、いつも見える（内部 QA レビュー 91285fe P1-1） ----
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        send(pg, "置くところをバイオ3ミリ下げて")
        until(pg, lambda: pg.is_visible("#reply-message"))
        check("バイオ" in pg.inner_text("#reply-message") and pg.inner_text("#next-action") == "もう一度、言い直してください",
              f"会話の始めの知らない言葉（B1）の聞き返しが見える：{pg.inner_text('#reply-message')[:40]}")
        shot(pg, "d10_b1_opening.png")
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        send(pg, "やめて")
        until(pg, lambda: pg.is_visible("#reply-message") and "会話をやめました" in pg.inner_text("#reply-message"))
        check(pg.is_visible("#dialog-panel") and last_said(pg).startswith("会話をやめました") and bubbles(pg)[-1][0] == "アプリ",
              "「やめて」で会話を終えたことが、返事の欄と会話のカード（アプリ）の両方に見える")
        shot(pg, "d11_undo_word.png")
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        send(pg, "はい")
        until(pg, lambda: len(labels(pg)) == 6)
        send(pg, "25mm")
        until(pg, lambda: labels(pg) and labels(pg)[0] == "X＋ 25mm")
        pg.locator("#dialog-choices button", has_text="Z−（下）25mm").click()
        until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step" or pg.is_visible("#reply-message"))
        reply = pg.inner_text("#reply-message") if pg.is_visible("#reply-message") else ""
        last = bubbles(pg)[-1] if bubbles(pg) else ["", ""]
        check("-25mm" in reply and "20mm" in reply and "超える" in reply
              and last[0] == "アプリ" and "-25mm" in last[1] and "超える" in last[1],
              f"会話の最後の 25mm は 20mm の検査で止まり、その理由が返事の欄とアプリの吹き出しに見える：{reply[:50]}")
        if pg.get_attribute("#step-4", "aria-current") == "step":
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            check(not pg.is_enabled("#approve-button"), "会話の最後の 25mm は承認できない")
            pg.click("#confirm-close")
        shot(pg, "d12_limit.png")
        pg.close()

        # ---- 2c. 手で選んだ位置の確かめ（［go］）：見出しは「この内容で動かしますか？」、文字では進めない ----
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        send(pg, "置くところをYのマイナス3mm")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        pg.locator("#dialog-choices button", has_text="ほかの位置").click()
        until(pg, lambda: pg.get_attribute("#step-2", "aria-current") == "step")
        pg.locator("#position-chips button", has_text="C00004").click()
        until(pg, lambda: labels(pg) == ["C00004 を Y −3mm 動かす"])
        check(pg.inner_text("#next-action") == "この内容で動かしますか？" and last_said(pg) == "C00004 を Y −3mm 動かしますか？",
              "手で選んだ位置では、すぐ候補にせず［C00004 を Y −3mm 動かす］のボタン1つで確かめる（見出し「この内容で動かしますか？」）")
        shot(pg, "d13_go_step.png")
        send(pg, "3mm")
        until(pg, lambda: "ボタンを押してください" in last_said(pg))
        check(pg.get_attribute("#step-3", "aria-current") == "step" and labels(pg) == ["C00004 を Y −3mm 動かす"],
              "確かめの段では、文字の「3mm」では進めない（ボタンを押すよう求める）")
        pg.locator("#dialog-choices button", has_text="C00004 を Y −3mm 動かす").click()
        until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
        check("Y方向に −3.000mm" in pg.inner_text("#candidate-view") and "推定" not in pg.inner_text("#candidate-view"),
              "ボタンで確かめると候補になる（手で選んだ位置なので推定の印は付けない）")
        pg.close()

        # ---- 2d. 390px：送ったあと、見出しが上の帯の下に隠れない ----
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        pg.wait_for_timeout(200)
        gap = pg.evaluate("""() => document.querySelector('#next-action').getBoundingClientRect().top
                              - document.querySelector('.top').getBoundingClientRect().bottom""")
        check(gap >= 0, f"390px：送ったあと、見出し（次にすること）が上の帯の下に隠れない（間 {gap:.0f}px）")
        shot(pg, "d14_390.png")
        pg.close()

        # ---- 3. くり返しの2択（「2回」）→ どちらでも、動きは足せないと伝える。候補なし・解釈の係も呼ばない ----
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00001").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00001")
        before = (interp.decides, interp.locates)
        send(pg, "C00001からC00002を2回移動させて")
        until(pg, lambda: pg.is_visible("#reply-message button.choice"))
        rc = [t.strip() for t in pg.locator("#reply-message button.choice").all_inner_texts()]
        check(rc == ["2回の移動", "2往復"], f"くり返し：「2回」は［2回の移動］［2往復］で聞く：{rc}")
        shot(pg, "d8_repeat.png")
        pg.locator("#reply-message button.choice", has_text="2往復").click()
        until(pg, lambda: "動きの追加・繰り返しはできません" in pg.inner_text("#reply-message"))
        check(pg.get_attribute("#step-3", "aria-current") == "step" and (interp.decides, interp.locates) == before,
              "くり返し：どちらを選んでも、動きは足せないと伝える（候補なし・解釈の係を呼ばない）")
        pg.close()

        # ---- 3b. 言い終わりの「〜します」（CHECKER_026 M3）→［変更したい］［説明です］の2択。候補にしない ----
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00004").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00004")
        before = (interp.decides, interp.locates)
        send(pg, "Yのマイナス3ミリずらします")
        until(pg, lambda: pg.is_visible("#reply-message button.choice"))
        rc = [t.strip() for t in pg.locator("#reply-message button.choice").all_inner_texts()]
        reply = pg.inner_text("#reply-message")
        check(rc == ["変更したい", "説明です"] and "Yのマイナス方向に3mm動かす変更をご希望ですか？" in reply
              and "それとも操作の説明でしょうか？" in reply and pg.get_attribute("#step-3", "aria-current") == "step"
              and pg.is_hidden("#candidate-panel") and (interp.decides, interp.locates) == before,
              f"「〜します」：候補にせず［変更したい］［説明です］で確かめる（解釈の係も呼ばない）：{rc} {reply[:60]}")
        shot(pg, "d8b_shimasu.png")
        pg.locator("#reply-message button.choice", has_text="説明です").click()
        until(pg, lambda: "分かりました。変更はしません" in pg.inner_text("#reply-message"))
        check(pg.get_attribute("#step-3", "aria-current") == "step" and pg.is_hidden("#candidate-panel")
              and (interp.decides, interp.locates) == before
              and pg.locator("#reply-message button.choice").count() == 0,
              "［説明です］：何もしない（候補なし・解釈の係を呼ばない・選択肢は消える）")
        send(pg, "Yのマイナス3ミリずらします")
        until(pg, lambda: pg.is_visible("#reply-message button.choice"))
        pg.locator("#reply-message button.choice", has_text="変更したい").click()
        until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
        cv = pg.inner_text("#candidate-view")
        check("Y方向に −3.000mm" in cv and pg.is_hidden("#approval-status") and pg.is_enabled("#open-confirm")
              and pg.is_hidden("#export-button"),
              f"［変更したい］：ふつうの指示として候補になるだけ（承認・出力はされない。確認表示と承認ボタンへ）：{cv[:60]}")
        pg.close()

        # ---- 4. 合成でないジョブ：Claude（モック）に頼まず、手で選んでもらう ----
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, bypass_csp=True)
        pg = ctx.new_page()
        watch(pg)
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        data = (REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes().replace(b"NOT A REAL ROBOT JOB", b"NOT A REAL ROBOT JOB.")
        dt = pg.evaluate_handle(DROP_FILE, ["MY_JOB.JBI", base64.b64encode(data).decode("ascii")])
        pg.dispatch_event("#drop-zone", "drop", {"dataTransfer": dt})
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        before = interp.locates
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: pg.get_attribute("#step-2", "aria-current") == "step" and pg.is_visible("#dialog-panel"))
        check("架空のサンプルではない" in last_said(pg) and interp.locates == before
              and pg.inner_text("#next-action") == "位置を選んでください" and pg.is_hidden("#candidate-panel"),
              f"合成でないジョブ：中身を送らず（呼ばない）、位置を手で選んでもらう：{last_said(pg)[:40]}")
        shot(pg, "d9_not_synthetic.png")
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()


DROP_FILE = """
([name, b64]) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], name));
  return dt;
}
"""


def check_updown_unconfirmed(browser, check, shots: Path | None, errors: list) -> None:
    """上下の向きの設定が無いサーバー（CHECKER_026 M2）：「3ミリ下げて」は案内で聞き返し、「Zマイナス3ミリ」は候補。
    会話の方向のボタンに「（上）」「（下）」を付けず、会話の「下に3ミリ」も同じ案内で聞き返す。"""
    from voice_teaching import updown
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def labels(page):
        return [t.strip() for t in page.locator("#dialog-choices button").all_inner_texts()]

    def last_said(page):
        said = page.eval_on_selector_all("#dialog-turns li .said", "els => els.map(e => e.textContent)")
        return said[-1] if said else ""

    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror（上下の設定なし）: {e}"))
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00005").first.click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        # QA P1-2：設定なしの画面の例文は、上下の言葉を使わない（軸と符号）。上下の向きの表示に次の行動を添える
        ph = pg.get_attribute("#text-input", "placeholder") or ""
        howto = pg.text_content("#howto-examples") or ""
        helps = pg.text_content("#help-examples") or ""
        lure = ("上に", "下に", "上へ", "下へ", "上方向", "下方向")
        check(ph == "Zプラス方向に10mm" and not any(w in ph + howto + helps for w in lure),
              f"上下の設定なし：入力欄・使い方・？の例文に上下の言葉を使わない（{ph!r}）")
        check(pg.inner_text("#updown-status") == "上下の向き：未確認（Z＋・Z−で指示）",
              f"上下の設定なし：「上下の向き：未確認（Z＋・Z−で指示）」と出す（{pg.inner_text('#updown-status')!r}）")
        side = len("".join(pg.inner_text(".side").split()))
        check(side <= 80, f"上下の設定なし：次にすることを足しても、音声のカードの右側の文字は80文字まで（{side}文字）")
        # 不一致・変更済みの表示の文に入れ替えても 80文字まで（表示の文だけを入れ替えて数える。サーバーの状態は変えない）
        for other in ("上下の向き：確認した座標系・ツールと違います", "上下の向き：設定が変わっています"):
            pg.evaluate("t => { document.querySelector('#updown-status').textContent = t; }", other)
            side2 = len("".join(pg.inner_text(".side").split()))
            check(side2 <= 80, f"上下の向き「{other}」の表示でも、音声のカードの右側の文字は80文字まで（{side2}文字）")
        pg.evaluate("t => { document.querySelector('#updown-status').textContent = t; }", "上下の向き：未確認（Z＋・Z−で指示）")
        pg.fill("#text-input", ph)
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        cv = pg.inner_text("#candidate-view")
        check("Z方向に +10.000mm" in cv and "（上へ）" not in cv,
              f"上下の設定なし：入力欄の例文をそのまま送ると候補になる（向きの名前は付けない）：{cv[:60]}")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        check(pg.is_hidden("#confirm-updown") and "（上へ）" not in pg.inner_text("#confirm-title"),
              "上下の設定なし：確認表示に「〇〇さんが確認」の行と向きの名前を出さない")
        pg.click("#confirm-close")
        pg.click("#undo-button")
        until(pg, lambda: not pg.is_enabled("#open-confirm"))
        pg.fill("#text-input", "3ミリ下げて")
        pg.click("#text-send")
        until(pg, lambda: pg.is_visible("#reply-message") and updown.MSG_UNCONFIRMED in pg.inner_text("#reply-message"))
        check(pg.locator("#reply-message button.choice").count() == 0 and not pg.is_enabled("#open-confirm")
              and pg.get_attribute("#step-4", "aria-current") != "step",
              "上下の設定なし：「3ミリ下げて」は候補にせず、案内で聞き返す（選択肢も出さない）")
        if shots is not None:
            pg.screenshot(path=str(shots / "m2_unconfirmed.png"))
        pg.fill("#text-input", "Zマイナス3ミリ")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        cv = pg.inner_text("#candidate-view")
        check("Z方向に −3.000mm" in cv and "197.000" in cv,
              f"上下の設定なし：軸と符号を明示した「Zマイナス3ミリ」は候補にする：{cv[:60]}")
        ctx.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror（上下の設定なし・会話）: {e}"))
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.click("#talk-first")
        until(pg, lambda: pg.get_attribute("#step-3", "aria-current") == "step")
        pg.fill("#text-input", "置くところ")
        pg.click("#text-send")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        pg.fill("#text-input", "そうそう")
        pg.click("#text-send")
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        check(labels(pg) == ["X＋", "X−", "Y＋", "Y−", "Z＋", "Z−"],
              f"上下の設定なし：会話の方向のボタンに「（上）」「（下）」を付けない：{labels(pg)}")
        pg.fill("#text-input", "下に3ミリ")
        pg.click("#text-send")
        until(pg, lambda: last_said(pg) == updown.MSG_UNCONFIRMED)
        check(not pg.is_enabled("#open-confirm") and not any("（下）" in x for x in labels(pg)),
              "上下の設定なし：会話の「下に3ミリ」も同じ案内で聞き返す（候補にしない）")
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_four_steps(browser, url, check, shots: Path | None) -> None:
    """4ステップの画面（3D）：ドラッグ＆ドロップ → 3D で位置を選ぶ → マイク ON で続けて指示 → 変更前後の3D再生 → 確認表示。

    「説明が要らない画面」の確かめ：見える文字の量、目立つボタンが1つだけ、今のステップのカードだけを出す、
    音声のカードは「位置・マイク・入力欄・次へ」、エラーは次に何をすればよいかを出す、止まった候補は承認できない。
    3D：ジョブの座標そのもの、回転・拡大縮小・視点を戻す・点を押して選ぶ・重なった点の選び分け・変更前後・再生。
    U1：確認表示に候補の注意をすべてと図の注意を出す。
    """
    import base64
    import re

    from voice_teaching.web import api

    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name), full_page=not name.startswith(("6_", "7_")))  # 確認表示は画面の範囲だけ

    def primaries(page):
        return page.locator("button.primary:not([disabled]):visible, .drop-zone.primary:visible").count()

    def buttons(page):
        return page.locator("button:visible").count()

    def chars(page, sel="body"):
        return len("".join(page.inner_text(sel).split()))

    def cards(page):
        return [c for c in ["select-panel", "instruction-panel", "candidate-panel"] if page.is_visible("#" + c)]

    def pt(page, name):
        return page.locator(f'#fig-3d g.pt3[data-names~="{name}"] circle').last

    def press(page, name, ms=80):
        # 人の押し方：その場所でボタンを押し、少し待って離す（再生中に図が描き直されても、要素の安定を待たない）
        x, y = pos_of(page, name)
        page.mouse.move(x, y); page.mouse.down(); page.wait_for_timeout(ms); page.mouse.up()

    def pos_of(page, name):
        # 再生中は描き直されるので、探す・測るを1回で行う
        x, y = page.evaluate("""(name) => {
          const g = [...document.querySelectorAll('#fig-3d g.pt3')].find((e) => e.dataset.names.split(' ').includes(name));
          const cs = g.querySelectorAll('circle'); const r = cs[cs.length - 1].getBoundingClientRect();
          return [r.x + r.width / 2, r.y + r.height / 2]; }""", name)
        return (round(x), round(y))

    # ドロップのデータを作るのに evaluate を使うので、この確認のページだけ CSP を外す（画面のファイルは変えない）
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, bypass_csp=True)
    pg = ctx.new_page()
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    check(pg.get_attribute("#step-1", "aria-current") == "step" and pg.is_visible("#drop-zone")
          and "primary" in (pg.get_attribute("#drop-zone", "class") or ""),
          "最初の画面：ステップ1が今のステップで、ドロップの枠が目立つ")
    n = chars(pg)
    check(n <= 160, f"最初の画面の文字は少ない（{n}文字。160文字まで）")
    # ボタンは「？」（使い方）・「サンプルで試す」・「詳細表示」の3つまで（以前は2つまで。？とサンプルを足した）
    check(primaries(pg) == 1 and buttons(pg) <= 3, f"最初の画面：目立つものは1つ（ボタン {buttons(pg)}個）")
    check(pg.is_visible("#mode-banner") and pg.is_visible("#readonly-note"),
          "最初の画面にも、シミュレーションモードと「実機用の出力・転送・ロボットの起動はしません」を出す")
    shot(pg, "1_start.png")

    data = (REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()
    dt = pg.evaluate_handle(DROP_FILE, ["DEMO_C_PLACE.JBI", base64.b64encode(data).decode("ascii")])
    pg.dispatch_event("#drop-zone", "drop", {"dataTransfer": dt})
    pg.wait_for_selector("#job-area:not([hidden])")
    check(pg.get_attribute("#step-2", "aria-current") == "step", "ドロップで読み込むと、ステップ2（位置を選ぶ）になる")
    check(pg.get_attribute('.view-tab[data-view="3d"]', "aria-selected") == "true" and pg.is_visible("#fig-3d svg"),
          "読み込むと、最初に 3D を大きく出す")
    check(pg.inner_text("#interp-badge") == "解釈：モック", f"解釈のしかたを上の帯で区別：{pg.inner_text('#interp-badge')}")
    check(cards(pg) == ["select-panel"], f"右側は今のステップのカードだけ：{cards(pg)}")
    check(all(pg.is_visible(s) for s in ["#figure-note", "#play-note", "#view3d-note"]),
          "図の注意・再生の限界・3D の限界（ロボット本体なし・保証しない）は、いつも見える")

    # 3D の点はジョブの座標そのもの（名前・行・数がジョブと一致する。作った点は無い）
    lines = {p["name"]: p["line"] for p in api.job_view(data)["positions"] if p["xyz"]}
    titles = pg.eval_on_selector_all("#fig-3d g.pt3 > title", "els => els.map(e => e.textContent)")
    shown = {}
    for t in titles:
        for row in t.split("\n"):
            name, _, line = row.partition("  ")
            shown[name] = line
    check(shown == lines, f"3D の点はジョブの位置そのもの（{len(shown)}点。ジョブは {len(lines)}点）")
    shot(pg, "2_loaded.png")

    # 回転・拡大縮小・視点を戻す（「視点を戻す」は回したあとだけ出す）
    check(pg.is_hidden("#view-reset") and "ドラッグで回す" in pg.text_content("#fig-3d svg"),
          "最初は「視点を戻す」を出さず、図の中に薄く「ドラッグで回す・ホイールで拡大」を出す")
    a0 = pos_of(pg, "C00005")
    box = pg.locator("#fig-3d").bounding_box()
    cx, cy = box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.85
    pg.mouse.move(cx, cy); pg.mouse.down(); pg.mouse.move(cx + 120, cy - 40, steps=8); pg.mouse.up()
    a1 = pos_of(pg, "C00005")
    check(a0 != a1 and pg.is_visible("#view-reset"), f"ドラッグで視点が回り、「視点を戻す」が出る（C00005 の画面の位置 {a0} → {a1}）")
    check("ドラッグで回す" not in pg.text_content("#fig-3d svg"), "一度回すと、操作のヒントは消える")
    check(pg.get_attribute("#step-2", "aria-current") == "step", "回しただけでは位置は選ばれない")
    shot(pg, "2b_rotated.png")
    p, q = pos_of(pg, "C00002"), pos_of(pg, "C00005")
    d1 = abs(p[0] - q[0]) + abs(p[1] - q[1])
    pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pg.mouse.wheel(0, -500)
    p, q = pos_of(pg, "C00002"), pos_of(pg, "C00005")
    d2 = abs(p[0] - q[0]) + abs(p[1] - q[1])
    check(d2 > d1 * 1.3, f"ホイールで拡大できる（点の間 {d1}px → {d2}px）")
    pg.click("#view-reset")
    check(pos_of(pg, "C00005") == a0 and pg.is_hidden("#view-reset"), "「視点を戻す」で最初の向き・大きさに戻る（ボタンは隠れる）")
    check("ドラッグで回す" not in pg.text_content("#fig-3d svg"), "視点を戻しても、操作のヒントは出し直さない")

    # 重なって見える点（C00000 と C00007）は、押すと選び分けの一覧が出る
    pt(pg, "C00000").click()
    pg.wait_for_selector("#fig-3d .chooser")
    names = pg.eval_on_selector_all("#fig-3d .chooser button[data-name]", "els => els.map(e => e.dataset.name)")
    check(sorted(names) == ["C00000", "C00007"], f"重なった点は選び分けの一覧を出す：{names}")
    shot(pg, "2c_chooser.png")
    pg.click('#fig-3d .chooser button[data-name="C00000"]')
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00000")
    check(True, "選び分けの一覧から C00000 を選べる")
    pg.click("#voice-position")  # 選び直す

    # 再生中でも、点を押して（人の押し方：80ms）選べる
    pg.click("#play-button")
    pg.wait_for_timeout(300)
    playing = pg.locator("#fig-3d circle.marker").count() == 1
    press(pg, "C00005")  # 点そのもの（丸）を押す
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
    check(playing and pg.get_attribute("#step-3", "aria-current") == "step",
          "3D の点を押すと位置が選ばれ、ステップ3になる（再生中に 80ms 押しても選べる）")
    check(pg.locator('#fig-3d g.pt3.sel[data-names~="C00005"]').count() == 1, "選んだ点が 3D で強調される")
    check(cards(pg) == ["instruction-panel"], f"右側は音声のカードだけ：{cards(pg)}")
    check(pg.is_hidden("#voice-mode-set") and pg.is_hidden("#browser-note")
          and "primary" in (pg.get_attribute("#mic-button", "class") or "") and primaries(pg) == 1,
          "方式を選ぶ欄は出さず（詳細表示の中）、マイクのボタンだけが目立つ")
    check(pg.inner_text("#mic-label") == "マイク OFF" and pg.get_attribute("#mic-button", "aria-pressed") == "false",
          "マイクは最初 OFF と出る")
    side = chars(pg, ".side")
    check(all(pg.is_visible(s) for s in ["#voice-position", "#mic-button", "#text-input", "#text-send"])
          and pg.inner_text("#text-send") == "次へ" and side <= 80,
          f"音声のカードは「位置・マイク・入力欄・次へ」（右側の文字 {side}文字。80文字まで）")
    check(pg.is_hidden("#heard-text") and pg.is_hidden("#work-log") and pg.is_hidden("#voice-notice"),
          "聞き取った文字・ログ・音声の方式の説明は詳細表示の中")
    shot(pg, "3_selected.png")

    # 初めて 🎤：同意を聞いてから聞き始める。通信の失敗が1回だけなら、マイク ON のまま聞き直す
    pg.evaluate("window.__fails = ['network']")
    pg.click("#mic-button")
    check(pg.is_visible("#voice-consent") and primaries(pg) == 1
          and "primary" in (pg.get_attribute("#consent-yes", "class") or ""),
          "初めて 🎤 を押すと同意を聞く（「使う」だけが目立つ）")
    shot(pg, "3_consent.png")
    pg.click("#consent-yes")
    until(pg, lambda: pg.inner_text("#voice-partial") == "この位置を")
    check(pg.is_hidden("#voice-error") and "聞いています" in pg.inner_text("#mic-label"),
          "通信の失敗が1回なら、マイク ON のまま聞き直す")
    pg.click("#voice-cancel")

    # 続けて失敗（Chrome の network）：マイクを OFF にして、次にすることと文字の入力へ案内する
    pg.evaluate("window.__fails = ['network', 'network']")
    pg.click("#mic-button")
    until(pg, lambda: pg.is_visible("#voice-error"))
    err = pg.inner_text("#voice-error")
    check(pg.inner_text("#mic-label") == "マイク OFF" and "接続" in err and "文字で入力" in err and pg.is_enabled("#text-input"),
          f"通信の失敗が続くと OFF にして、次にすることと文字入力を案内：{' / '.join(err.splitlines())[:60]}")
    shot(pg, "3a_voice_error.png")

    # 声が無いまま止まる（no-speech）：Web会議のように、マイク ON のまま聞き直す
    pg.evaluate("window.__fails = ['no-speech', 'no-speech']")
    pg.click("#mic-button")
    until(pg, lambda: pg.inner_text("#voice-partial") == "この位置を")
    check(pg.is_hidden("#voice-error") and "聞いています" in pg.inner_text("#mic-label")
          and pg.evaluate("window.__fails.length") == 0, "声が無いまま止まっても、マイク ON のまま聞き直す（2回）")
    pg.click("#voice-cancel")

    # マイクが許可されていない：どこで許可するかを出す
    pg.evaluate("window.__fails = ['not-allowed']")
    pg.click("#mic-button")
    until(pg, lambda: pg.is_visible("#voice-error"))
    check("許可" in pg.inner_text("#voice-error") and pg.inner_text("#mic-label") == "マイク OFF",
          "マイクが許可されていないときは、許可の場所を案内して OFF")

    # 入力欄に文があるまま失敗（network）→ 一時停止。あとで入力欄を消しても、勝手に聞き始めない（内部 QA P2-1）
    pg.fill("#text-input", "手で書いた")
    pg.evaluate("window.__fails = ['network']")
    pg.click("#mic-button")
    until(pg, lambda: pg.evaluate("window.__fails.length") == 0 and not pg.is_enabled("#voice-stop"))
    pg.fill("#text-input", "")
    pg.wait_for_timeout(1200)
    check(not pg.is_enabled("#voice-stop") and "一時停止" in pg.inner_text("#mic-label"),
          "入力欄に文があるまま失敗したあと、入力欄を消しても勝手に聞き始めない")
    pg.click("#mic-button")  # 一時停止中に押すと OFF
    until(pg, lambda: pg.inner_text("#mic-label") == "マイク OFF")

    # 「端末内のみ」（詳細表示で選ぶ・試験的）で日本語の音声データが無いとき：入れるボタンを出し、押したときだけ入れる
    pg.click("#engineer-toggle")
    pg.click('label:has(#voice-mode-local)')
    pg.evaluate("window.__localAvail = 'downloadable'; window.__fails = ['language-not-supported']")
    pg.click("#mic-button")
    until(pg, lambda: pg.is_visible("#local-install"))
    check("端末内のみでは日本語" in pg.inner_text("#voice-error"), "端末内のみで日本語が無いときは、音声データを入れるボタンを出す")
    pg.click("#local-install")
    until(pg, lambda: "入れました" in pg.inner_text("#voice-status"))
    check(pg.is_hidden("#local-install"), "押すと音声データを入れて、もう一度マイクを押すよう案内する")
    pg.click('label:has(#voice-mode-browser)')
    pg.click("#engineer-toggle")

    # マイクを一度 ON にすると、続けて話せる（Web会議のように）
    pg.click("#mic-button")
    until(pg, lambda: pg.is_enabled("#voice-stop"))
    check(pg.get_attribute("#mic-button", "aria-pressed") == "true" and "聞いています" in pg.inner_text("#mic-label")
          and primaries(pg) == 1 and "primary" in (pg.get_attribute("#voice-stop", "class") or ""),
          "マイク ON：聞いていることが一目で分かり、「話し終わった」だけが目立つ")
    check(pg.is_hidden("#voice-error"), "前の失敗の文は消える（もう一度 ON にできる）")
    check(pg.is_hidden("#voice-error"), "前の失敗の文は消える")
    shot(pg, "3c_recording.png")
    pg.click("#voice-stop")
    until(pg, lambda: pg.input_value("#text-input") == FAKE_TEXT)
    check(pg.is_visible("#heard-label") and pg.inner_text("#heard-label") == "聞き取った文（直せます）"
          and "heard" in (pg.get_attribute("#text-input", "class") or ""),
          "声から入った文は「聞き取った文（直せます）」と大きく出す")
    pg.wait_for_timeout(400)  # ボタンの色の切り替え（0.15秒）が終わってから撮る
    shot(pg, "3d_heard.png")
    check("一時停止" in pg.inner_text("#mic-label") and pg.is_enabled("#text-input")
          and "primary" in (pg.get_attribute("#text-send", "class") or "") and primaries(pg) == 1,
          "話し終わると認識文が入力欄に入り、マイクは ON のまま一時停止（確かめて「次へ」）")
    pg.wait_for_timeout(800)
    check(pg.input_value("#text-input") == FAKE_TEXT and pg.is_hidden("#reply-message")
          and pg.get_attribute("#step-3", "aria-current") == "step", "自動では送らない（入力欄の文を直せる）")

    pg.fill("#text-input", "")
    pg.wait_for_timeout(1000)
    check(not pg.is_enabled("#voice-stop") and pg.is_enabled("#text-input"),
          "一時停止中に入力欄を消しても、勝手に聞き始めない（文字で打ち直せる）")
    # 入力欄の文を直して送る → 聞き返し → マイクが ON のまま、また聞き始める
    pg.fill("#text-input", "少し動かして")
    pg.click("#text-send")
    until(pg, lambda: pg.is_visible("#reply-message"))
    reply = pg.inner_text("#reply-message")
    check(pg.get_attribute("#step-3", "aria-current") == "step" and pg.is_visible("#reply-message .friendly strong"),
          f"はっきりしない指示には、次にすることを出す：{' / '.join(reply.splitlines())[:60]}")
    check(pg.inner_text("#next-action") == "もう一度、言い直してください",
          f"選択肢の無い聞き返しの見出しは「もう一度、言い直してください」：{pg.inner_text('#next-action')}")
    shot(pg, "3b_ask.png")
    until(pg, lambda: pg.is_enabled("#voice-stop"), timeout_ms=4000)
    check("聞いています" in pg.inner_text("#mic-label"), "結果が出ると、マイク ON のまま続けて聞き始める（2回目の指示）")
    pg.click("#voice-stop")
    until(pg, lambda: pg.input_value("#text-input") == FAKE_TEXT)
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    until(pg, lambda: "変更前" in pg.inner_text("#play-status") or "変更後" in pg.inner_text("#play-status"))
    check(True, f"候補ができると、3D で変更前後の経路の再生が始まる：{pg.inner_text('#play-status')}")
    check(not pg.is_enabled("#voice-stop") and pg.is_hidden("#mic-button"), "ステップ4では聞かない（マイクのカードを出さない）")
    cand = pg.inner_text("#candidate-view")
    check("C00005" in cand and "200.000" in cand and "210.000" in cand and "指示：「" in cand and "上へ" in cand, f"右のカードに変更の要点・指示の文・変更前後の XYZ・確認済みの向きの名前：{' '.join(cand.split())[:40]}…")
    check(pg.locator("#fig-3d line.seg.after").count() > 0 and pg.locator("#fig-3d circle.after-pt").count() == 1,
          "3D に変更後の経路（オレンジ）と変更後の点を出す")
    check(pg.text_content("#fig-3d .inset-amt") == "Z +10.000mm",
          f"小さな変更も見えるよう、拡大の窓に向きと量を出す：{pg.text_content('#fig-3d .inset-amt')}")
    gap = pg.evaluate("""() => {  // 再生中は描き直されるので、同じ時点の2つの四角を1回で測る
      const h = document.querySelector('#fig-3d .inset-head').getBoundingClientRect();
      const a = document.querySelector('#fig-3d .inset-amt').getBoundingClientRect();
      return a.top - h.bottom; }""")
    check(gap > 0, f"拡大の窓の見出しと量が重ならない（間 {gap:.0f}px）")
    check(cards(pg) == ["candidate-panel"] and primaries(pg) == 1
          and "primary" in (pg.get_attribute("#open-confirm", "class") or ""),
          "ステップ4は「確認して承認へ」だけが目立つ")
    check(pg.is_hidden("#export-button"), "承認する前は、出力のボタンを出さない")
    pg.wait_for_timeout(1500)
    check(pg.locator("#fig-3d circle.marker").count() == 1, "再生の印が 3D の中を動く")
    shot(pg, "4_playing.png")
    until(pg, lambda: "終わりました" in pg.inner_text("#play-status"), timeout_ms=60000)
    check("ロボット本体の動きではありません" in pg.inner_text("#play-note"), "再生の限界（ロボット本体の動きではない）を出す")
    press(pg, "C00002")
    pg.wait_for_timeout(200)
    check(pg.get_attribute("#step-4", "aria-current") == "step" and "選び直せません" in pg.inner_text("#play-status"),
          "候補がある間は、図の点を押しても候補を取り消さない（理由を出す）")

    pg.click('.view-tab[data-view="yz"]')
    check(pg.get_attribute('.view-tab[data-view="yz"]', "aria-selected") == "true" and pg.is_visible("#fig-main svg")
          and pg.is_hidden("#fig-3d"), "2D（Y・Z）に切り替えられる")
    orange2d = pg.eval_on_selector_all("#fig-main line", "els => els.filter(e => e.getAttribute('stroke') === '#ff6a1a').length")
    check(orange2d == 2, f"2D でも、変更後（オレンジ）は動かす点に触れる区間だけ（{orange2d}本）")
    pg.click('.view-tab[data-view="3d"]')

    check(pg.is_hidden("#engineer-view"), "詳細表示は最初は閉じている")
    pg.click("#engineer-toggle")
    check(pg.is_visible("#engineer-view") and "C00005" in pg.inner_text("#work-log")
          and pg.is_visible("#voice-notice"),
          "詳細表示を開くと、作業ログ・表・音声と通信の説明が同じ画面に出る")
    shot(pg, "5_engineer.png")
    pg.click("#engineer-toggle")
    pg.evaluate("window.scrollTo(0, 0)")

    # U1：確認表示に、候補の注意をすべてと図の注意を出す
    warns = [w.strip() for w in pg.locator("#candidate-view .cand-warn li").all_inner_texts()]
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    shown_w = [w.strip() for w in pg.locator("#confirm-warnings li").all_inner_texts()]
    check(warns and shown_w == warns and pg.is_visible("#confirm-warnings"),
          f"U1：確認表示に候補の注意をすべて出す（{len(shown_w)}件）")
    check(pg.is_visible("#confirm-overlay .confirm-figure-note")
          and "当たらないとは限りません" in pg.inner_text("#confirm-overlay .confirm-figure-note"),
          "U1：確認表示に図の注意を出す")
    check(pg.is_visible("#confirm-figure svg") and pg.is_visible("#approve-button"),
          "全画面の確認表示に、3D の図と承認ボタンがある（承認はボタンだけ）")
    pg.click("#approve-button")
    check(pg.is_visible("#confirm-error") and "名前" in pg.inner_text("#confirm-error"),
          "名前が無いと、名前を入れるように出す（承認しない）")
    shot(pg, "6_confirm.png")
    pg.click("#confirm-close")

    # 実機用の検査で止まる位置（C00000）：参考の表示だけで、承認できない。次にすることを出す
    pg.click("#undo-button")
    until(pg, lambda: pg.get_attribute("#step-3", "aria-current") == "step")
    if pg.is_enabled("#voice-cancel"):
        pg.click("#voice-cancel")  # 元に戻すとまた聞き始めるので、ここではマイクを OFF にする
    until(pg, lambda: pg.inner_text("#mic-label") == "マイク OFF")
    check(True, "「マイクをOFF」で OFF にできる")
    pg.click("#voice-position")
    check(cards(pg) == ["select-panel"] and pg.get_attribute("#step-2", "aria-current") == "step",
          "位置の「変える」で、位置のボタンに戻れる")
    pg.locator("#position-chips button", has_text="C00000").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00000")
    pg.fill("#text-input", FAKE_TEXT)
    pg.click("#text-send")
    pg.wait_for_timeout(500)
    if pg.get_attribute("#step-4", "aria-current") == "step":
        text = pg.inner_text("#candidate-view")
        check("別の位置" in text and pg.is_visible("#candidate-view .friendly.blocked .reason")
              and "primary" in (pg.get_attribute("#undo-button", "class") or ""),
              "止まった候補：次にすること（元に戻して別の位置）と理由を出し、「元に戻す」が目立つ")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        check(pg.is_visible("#confirm-blocked") and not pg.is_enabled("#approve-button"),
              "止まった候補は、確認表示でも承認できない")
        check(re.search("止ま", pg.inner_text("#confirm-warnings")) is not None,
              "止まった候補の確認表示にも、注意（止まった理由）を出す")
        shot(pg, "7_blocked.png")
        pg.click("#confirm-close")
    else:
        reply = pg.inner_text("#reply-message")
        check(pg.is_visible("#reply-message .friendly strong"), f"止まった指示には、次にすることを出す：{reply[:60]}")
        shot(pg, "7_blocked.png")
    ctx.close()


def main() -> int:
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("playwright が無いので、確認をしませんでした（pip install playwright と playwright install chromium）")
        return 0
    shots = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--shots":
        shots = Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as d:
        fails = run(Path(d), shots=shots)
    print("結果：", "すべて通りました" if not fails else f"{len(fails)}件 通りませんでした")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
