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


def fill_text(pg, text) -> None:
    """指示の欄に文字を入れる（人の操作）。① 初期の画面（大きなマイク・唐川さん承認のデザイン 11/4）では、入力欄は
    キーボードのボタンを押すまで出ない。出ていなければ、人と同じようにキーボードのボタンを押してから入れる。送るのは別（［送る］）。"""
    if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
        pg.click("#kbd-button")
    pg.fill("#text-input", text)

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


HEADER_JS = """() => {
  const top = document.querySelector('header.top') || document.querySelector('header');
  const vis = (e) => e && !e.closest('[hidden]') && e.getClientRects().length > 0;
  const rect = (e) => { const r = e.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom, cy: r.top + r.height / 2 }; };
  const box = (s) => { const e = document.querySelector(s); return vis(e) ? rect(e) : null; };
  const qs = [...top.querySelectorAll('button')].filter(vis).map((e) => e.textContent.trim());
  const cs = (s) => { const st = getComputedStyle(document.querySelector(s));
    return [st.fontSize, st.fontWeight, st.color, st.backgroundColor, st.borderTopLeftRadius, st.borderTopColor, st.paddingLeft]; };
  const tip = document.querySelector('[data-tip=status]');
  const before = tip ? getComputedStyle(tip, '::before') : null;
  const chips = [...document.querySelectorAll('#status-line .st-chip')].filter(vis).map((e) => Object.assign(rect(e), { t: e.textContent.trim() }));
  // ⓘ の丸（::before）の左の端：ボタンの左の端 + 丸の left + 平行移動（translate）の分
  let circleX = null;
  if (tip && vis(tip)) {
    const m = new DOMMatrixReadOnly(before.transform === 'none' ? undefined : before.transform);
    circleX = tip.getBoundingClientRect().left + parseFloat(before.left) + m.m41;
  }
  return { header: box('header'), sw: top.scrollWidth, cw: top.clientWidth, texts: qs,
    doc: [document.documentElement.scrollWidth, innerWidth],
    items: Object.fromEntries(['.logo-mark', '.logo', '#mode-banner', '#interp-badge', '#status-line', '[data-tip=status]', '#approval-icon',
      '#help-button', '#engineer-toggle'].map((s) => [s, box(s)])),
    chips, chip: chips.length ? chips[chips.length - 1] : null, circleX,
    help: cs('#help-button'), eng: cs('#engineer-toggle'),
    tipText: tip ? tip.textContent.trim() : null, tipLabel: tip ? tip.getAttribute('aria-label') : null,
    circle: before ? [parseFloat(before.width), before.borderTopStyle] : null };
}"""


def _overlap(a, b) -> float:
    """2つの四角の重なりの面積（px²）。どちらかが無ければ 0"""
    if not a or not b:
        return 0.0
    return max(0.0, min(a["r"], b["r"]) - max(a["x"], b["x"])) * max(0.0, min(a["b"], b["b"]) - max(a["y"], b["y"]))


def _r1(v):
    return None if v is None or v is False else round(v, 1)


def talk_first(pg) -> None:
    """Issue #7：［位置を選ばずに話す］の文字のボタンは画面に出さない（位置を選ばずにマイクで話せる）。
    会話の流れの確かめのために、同じ状態（ステップ3・位置は未選択）へ入る"""
    pg.evaluate("document.querySelector('#talk-first').click()")


def click_opening_replay(pg) -> None:
    """Issue #7：［▶ オープニング］はアプリ情報（上の帯の ⓘ）の中。開いてから押す"""
    if not pg.is_visible("#opening-replay"):
        pg.click(".app-info > summary")
    pg.click("#opening-replay")


def check_header_help(browser, url, check, shots: Path | None) -> None:
    """ヘッダーの「?」2つの直し（案3）：使い方は「詳細表示」と同じ見た目の文字ボタン、アプリの記録の説明はチップの直後の ⓘ。
    360×800・390×844・1150×800・1280×720・1280×800・1920×1080 の、読み込み前・読み込み後・変更候補ありで確かめる：
    ⓘ はどの幅でも最後のチップと同じ行のすぐ右（チップが2行に折り返しても）・押せる所は 44×44 以上で、チップ・「使い方」・
    「詳細表示」と重ならない・横スクロールが出ない。1280px 以上ではヘッダーが1行で折り返さない。
    使い方の小窓の開閉・Esc でフォーカスを戻す・外を押して閉じる、記録の説明の吹き出しも確かめる。"""
    def layout(pg, tag, phase):
        r = pg.evaluate(HEADER_JS)
        hb, eb = r["items"]["#help-button"], r["items"]["#engineer-toggle"]
        check(r["doc"][0] <= r["doc"][1] and r["sw"] <= r["cw"],
              f"{tag}・{phase}：横スクロールが出ない（ページ {r['doc'][0]}/{r['doc'][1]}・ヘッダー {r['sw']}/{r['cw']}）")
        check(hb and eb and _overlap(hb, eb) == 0 and not [c["t"] for c in r["chips"] if _overlap(c, hb) or _overlap(c, eb)],
              f"{tag}・{phase}：チップ・「使い方」・「詳細表示」は重ならない")
        return r

    def tip_checks(pg, tag, w, phase):
        r = layout(pg, tag, phase)
        it, chip = r["items"]["[data-tip=status]"], r["chip"]
        hb, eb = r["items"]["#help-button"], r["items"]["#engineer-toggle"]
        check(r["tipText"] == "i" and r["tipLabel"] == "アプリの記録の説明" and r["circle"] and r["circle"][1] != "none",
              f"{tag}・{phase}：記録の説明は丸の中の「i」（ⓘ）・名前は「アプリの記録の説明」（{r['tipText']}・{r['tipLabel']}・{r['circle']}）")
        check(r["circle"] and r["circle"][0] >= 20, f"{tag}・{phase}：ⓘ の丸はほかの「?」に近い大きさ（{r['circle'] and r['circle'][0]}px）")
        check(bool(it and chip and abs(it["cy"] - chip["cy"]) <= 3 and it["y"] < chip["b"] and it["b"] > chip["y"]),
              f"{tag}・{phase}：ⓘ は最後のチップ「{chip and chip['t']}」と同じ行（縦の中心のずれ "
              f"{_r1(it and chip and it['cy'] - chip['cy'])}px）")
        gap = it["x"] - chip["r"] if it and chip else None
        vgap = r["circleX"] - chip["r"] if chip and r["circleX"] is not None else None
        check(gap is not None and vgap is not None and 0 <= gap <= 8 and 0 <= vgap <= 8,
              f"{tag}・{phase}：ⓘ は最後のチップのすぐ右（押せる所のすきま {_r1(gap)}px・丸のすきま {_r1(vgap)}px。"
              f"最後のチップの右 {chip and round(chip['r'])}px・ⓘ の左 {it and round(it['x'])}px）")
        check(bool(it and it["w"] >= 44 and it["h"] >= 44),
              f"{tag}・{phase}：ⓘ の押せる所は 44×44 以上（{it and (round(it['w'], 1), round(it['h'], 1))}）")
        hits = [c["t"] for c in r["chips"] if _overlap(it, c) > 0] + [n for n, b in (("使い方", hb), ("詳細表示", eb)) if _overlap(it, b) > 0]
        check(bool(it) and not hits, f"{tag}・{phase}：ⓘ の押せる所は、チップ・「使い方」・「詳細表示」と重ならない（重なり：{hits}）")
        check(bool(it and it["x"] >= 0 and it["r"] <= w),
              f"{tag}・{phase}：ⓘ の押せる所は画面の横の内側（{it and (round(it['x']), round(it['r']))}）")
        bad = pg.evaluate(BUTTON_SIZE_JS)
        check(not [b for b in bad if "使い方" in b or "help-button" in b or b.startswith("i（")],
              f"{tag}・{phase}：「使い方」と ⓘ は文字 14px 以上・高さ 44px 以上（{bad}）")
        pg.hover("[data-tip=status]")
        until(pg, lambda: pg.is_visible("#tip-pop"))
        t = pg.inner_text("#tip-pop")
        check("アプリの記録" in t and "会話の答えでは変わりません" in t, f"{tag}・{phase}：ⓘ に合わせると、アプリの記録の説明が出る（{t!r}）")
        pg.mouse.move(5, pg.viewport_size["height"] - 5)
        until(pg, lambda: not pg.is_visible("#tip-pop"))
        return r

    # 1150×800：PC の並びで記録のチップが2行に折り返す幅（ⓘ が上の行のチップと重ならないこと）
    for (w, h) in [(360, 800), (390, 844), (1150, 800), (1280, 720), (1280, 800), (1920, 1080)]:
        pc = w >= 1100
        one_line = w >= 1280  # ヘッダーが1行で折り返さないことを確かめる幅
        clip = {"x": 0, "y": 0, "width": w, "height": 90 if pc else 240}
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        tag = f"案3 ヘッダー（{w}×{h}）"
        r = layout(pg, tag, "読み込み前")
        check(not any(t in ("?", "？") or "?" in t or "？" in t for t in r["texts"]),
              f"{tag}：ヘッダーに「?」「？」のボタンが無い（{r['texts']}）")
        # 本番UI（ミニマル確定案 11/5 夜）：使い方・詳細表示は同じ見た目のアイコン。名前は aria-label
        check(pg.get_attribute("#help-button", "aria-label") == "使い方" and "icon-btn" in (pg.get_attribute("#help-button", "class") or "").split(),
              f"{tag}：使い方のボタンは「使い方」の名前のアイコン")
        check(r["help"] == r["eng"], f"{tag}：「使い方」は「詳細表示」と同じ見た目（{r['help']} / {r['eng']}）")
        hb, eb = r["items"]["#help-button"], r["items"]["#engineer-toggle"]
        check(bool(hb and eb and abs(hb["cy"] - eb["cy"]) <= 1 and abs(hb["h"] - eb["h"]) <= 1 and hb["r"] <= eb["x"]),
              f"{tag}：「使い方」と「詳細表示」は同じ高さで横に並ぶ（{hb} / {eb}）")
        check(bool(eb and eb["r"] >= w - 40), f"{tag}：読み込む前も「使い方」「詳細表示」は右の端（詳細表示の右 {eb and round(eb['r'])}px）")
        check(r["items"]["[data-tip=status]"] is None, f"{tag}：読み込む前（記録が出ていない）は ⓘ を出さない")
        check(pg.get_attribute("#approval-icon", "data-appr") == "none" and "読み込んでいません" in (pg.get_attribute("#approval-icon", "aria-label") or ""),
              f"{tag}：読み込む前の承認のアイコンは、承認と見間違えない形（点線の丸）・名前「JOB を読み込んでいません」")
        # 使い方の小窓：押して開く → Esc で閉じて「使い方」にフォーカスが戻る → もう一度押して開く・押して閉じる → 外を押して閉じる
        check(pg.is_hidden("#help-pop") and pg.get_attribute("#help-button", "aria-expanded") == "false", f"{tag}：使い方の小窓は最初は閉じている")
        pg.click("#help-button")
        check(pg.is_visible("#help-pop") and pg.get_attribute("#help-button", "aria-expanded") == "true"
              and "承認は確認表示のボタンだけです" in pg.inner_text("#help-pop"), f"{tag}：「使い方」を押すと小窓が開く")
        pb = pg.locator("#help-pop").bounding_box()
        check(pb is not None and pb["x"] >= 0 and pb["x"] + pb["width"] <= w, f"{tag}：小窓は画面の横の内側（{pb}）")
        pg.keyboard.press("Escape")
        check(pg.is_hidden("#help-pop") and pg.get_attribute("#help-button", "aria-expanded") == "false"
              and pg.evaluate("document.activeElement.id") == "help-button", f"{tag}：Esc で閉じて「使い方」にフォーカスが戻る")
        pg.click("#help-button")
        pg.click("#help-button")
        check(pg.is_hidden("#help-pop") and pg.get_attribute("#help-button", "aria-expanded") == "false", f"{tag}：もう一度押すと閉じる")
        pg.click("#help-button")
        pg.click(".logo")  # 小窓の外（アプリ名。押しても何も起きない所）
        check(pg.is_hidden("#help-pop"), f"{tag}：小窓の外を押すと閉じる")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / f"H3_{w}x{h}_before.png"), clip=clip)
        # サンプルを読み込んだあと（Issue #7・11/6）：アプリの記録は上の帯に出さない（全文はアプリ情報）。上の帯には承認の状態のアイコンだけ
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: "アプリの記録：" in (pg.text_content("#app-info-status") or ""))
        pg.wait_for_timeout(300)

        def appr_checks(r, phase, want):
            ai, hb, eb = r["items"]["#approval-icon"], r["items"]["#help-button"], r["items"]["#engineer-toggle"]
            label = pg.get_attribute("#approval-icon", "aria-label") or ""
            check(r["items"]["#status-line"] is None and r["items"]["[data-tip=status]"] is None
                  and pg.text_content("#app-info-status") == pg.text_content("#status-line"),
                  f"{tag}・{phase}：記録の帯は上の帯に出さず、アプリ情報にサーバーの文のまま出す（{pg.text_content('#app-info-status')}）")
            check(bool(ai) and pg.get_attribute("#approval-icon", "data-appr") == want and label.startswith("承認の状態：")
                  and label == (pg.get_attribute("#approval-icon", "title") or "") and pg.get_attribute("#approval-icon", "role") == "img"
                  and _overlap(ai, hb) == 0 and _overlap(ai, eb) == 0 and ai["x"] >= 0 and ai["r"] <= w,
                  f"{tag}・{phase}：上の帯に承認の状態のアイコン（{want}・名前「{label}」）。ほかのボタンと重ならない")
            if one_line:
                vis = {k: v for k, v in r["items"].items() if v and k != ".logo"}
                cys = [v["cy"] for v in vis.values()]
                check(bool(r["sw"] <= r["cw"] and max(cys) - min(cys) <= 8 and r["header"]["h"] <= 72),
                      f"{tag}・{phase}：ヘッダーは1行で折り返さない（高さ {round(r['header']['h'])}px・中心のずれ {round(max(cys) - min(cys), 1)}px）")

        appr_checks(layout(pg, tag, "読み込み後"), "読み込み後", "pending")
        if shots is not None:
            pg.screenshot(path=str(shots / f"H3_{w}x{h}_loaded.png"), clip=clip)
        # 変更候補あり：C00005（PL=0 の MOVL。止まらない位置）を選んで文字を送る。承認の前なので未承認のまま
        pg.locator("#position-chips button", has_text="C00005").first.click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        fill_text(pg, "Zプラス方向に10mm")
        pg.click("#text-send")
        until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
        until(pg, lambda: "候補 あり" in pg.text_content("#status-line"))
        pg.wait_for_timeout(300)
        appr_checks(layout(pg, tag, "変更候補あり"), "変更候補あり", "pending")
        if shots is not None:
            pg.evaluate("window.scrollTo(0, 0)")
            pg.screenshot(path=str(shots / f"H3_{w}x{h}_candidate.png"), clip=clip)
        pg.close()


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
        # Issue #7：いつも見える図の注意は短い1行（#figure-short。全文は ⓘ）
        return page.evaluate("""() => document.querySelector('.foot').getBoundingClientRect().top
                                - document.querySelector('#figure-short').getBoundingClientRect().bottom""")


    # ---- 1280×720：版・使い方の小窓・サンプルで試す・図の注意・止まった候補の帯 ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    # Issue #7：版は最下部の帯からアプリ情報（上の帯の ⓘ）へ移した
    pg.click(".app-info > summary")
    check(pg.is_visible("#app-version") and pg.inner_text("#app-version") == "Ver.1.00-dev",
          f"アプリ情報に版を出す：{pg.inner_text('#app-version')}")
    pg.click(".app-info > summary")
    check(pg.is_hidden("#help-pop"), "使い方の小窓は最初は閉じている")
    pg.click("#help-button")
    pop = pg.inner_text("#help-pop") if pg.is_visible("#help-pop") else ""
    check("Y座標を3ミリ下げて" in pop and "承認は確認表示のボタンだけです" in pop
          and pg.get_attribute("#help-button", "aria-expanded") == "true", "「使い方」で使い方の小窓が開く")
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
    fill_text(pg, FAKE_TEXT)
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
          and "灰色の点線と ✕＝変更できない案（参考・承認できません）" in pg.inner_text("#legend") and to_color != "rgb(196, 71, 10)",
          f"止まった候補：凡例に「灰色の点線と ✕＝変更できない案」、カードの変更後の値はオレンジにしない（{to_color}）")
    xs = pg.locator("#fig-3d .stopped-x").count()
    lab = pg.text_content("#fig-3d .moved-label") or ""
    link = pg.text_content("#candidate-view .friendly.blocked") or ""
    check(xs == 2 and "変更できません" in lab and "灰色の点線と ✕" in link,
          f"止まった候補（11/5 残件3）：3D の変更後の点に ✕、名前の横に「変更できません」、赤い帯に 3D の印の説明（✕ {xs}・{lab}）")
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
          and "灰色の点線と ✕＝変更できない案（参考・承認できません）" in pg.inner_text("#confirm-stopped-legend"),
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
    fill_text(pg, FAKE_TEXT)
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
        # 入口は1つ（/api/talk）：指示の道の答えは body["hear"] の中
        h = body.get("hear") or {}
        if h.get("facts") is not None and not h["facts"].get("unknown_words"):
            h["facts"]["unknown_words"] = ["バイオ"]
        h["heard"] = heard
        body["heard"] = heard
        route.fulfill(response=res, body=json.dumps(body, ensure_ascii=False),
                      headers={**res.headers, "content-type": "application/json; charset=utf-8"})

    pg.route("**/api/talk", fake_unknown)
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
    # 話し終わるとマイクは自動で OFF（「マイクをOFF」は押さなくてよい）
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


REVIEW_ONLY = "変更案の確認のみ。実機用JOBは出力できません"
UNDO_HINT = "もう一度直すときは［元に戻す］を押してください"  # CHECKER_030 S30-3（唐川さんの決定 2026-11-03 17:14）
# 承認のあとの3つの状態（台本 Ver.6.1 Scene 3-3・チャッピーの修正③）
AFTER_ROWS = ["元の動作データ：変わっていません", "変更候補：人が承認しました（シミュレーション用）", "ロボットへの送信：していません"]
AFTER_ROWS_JS = """() => [...document.querySelectorAll('#approval-states .state-row')]
  .map(r => r.querySelector('dt').textContent + '：' + r.querySelector('dd').textContent)"""


def check_ui_1105(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 の残件：① の［位置を選ばずに話す］が画面の中・② の番号の1行のはみ出しの札（ほか N ›）・見出しの文言。
    ② に入るために、モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）で質問を1つ送る。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "u1105_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    for (w, h) in [(1280, 720), (1920, 1080)]:
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: "hero" in (pg.get_attribute("body", "class") or ""))
        pg.wait_for_timeout(200)
        # 下の帯の上の端と、切り取る親（overflow が visible でないもの）の下の端の、小さい方より上にあること
        # Issue #7：［位置を選ばずに話す］の文字は出さない。位置を選ばなくても、マイクをそのまま押して話せる（マイクが下の帯より上に見える）
        r = pg.evaluate("""() => { const b = document.querySelector('#mic-button'); let lim = document.querySelector('.foot').getBoundingClientRect().top;
            for (let e = b.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e);
              if (o.overflowY !== 'visible' || o.overflowX !== 'visible') lim = Math.min(lim, e.getBoundingClientRect().bottom); }
            return [b.getBoundingClientRect().bottom, lim]; }""")
        check(pg.is_hidden("#talk-first") and pg.is_visible("#mic-button") and pg.is_enabled("#mic-button") and r[0] <= r[1],
              f"11/5 残件4・Issue #7（{w}×{h}）：① で位置を選ばずにマイクを押せる・下の帯より上に見える（下端 {r[0]:.0f}px・帯 {r[1]:.0f}px）")
        check(pg.inner_text("#next-action") == "ロボットの動きについて話しかけてください" and "ロボットに" not in pg.inner_text("#next-action"),
              f"11/5 残件2（{w}×{h}）：① の見出しは、ロボットに直接命令するように読めない言い方")
        check(pg.is_hidden("#chips-more"), f"11/5 残件1（{w}×{h}）：① では番号を折り返すので、はみ出しの札を出さない")
        fill_text(pg, "このロボット、何をしている？")
        pg.click("#text-send")
        until(pg, lambda: "hero" not in (pg.get_attribute("body", "class") or ""))
        until(pg, lambda: pg.is_visible("#select-panel"))
        pg.wait_for_timeout(300)
        sw, cw = pg.evaluate("() => { const c = document.querySelector('#position-chips'); return [c.scrollWidth, c.clientWidth]; }")
        if sw > cw + 1:
            until(pg, lambda: pg.is_visible("#chips-more"))
            t = pg.inner_text("#chips-more")
            check(t.startswith("ほか") and t.endswith("›") and pg.get_attribute("#position-chips", "class").count("more-right"),
                  f"11/5 残件1（{w}×{h}）：② で番号がはみ出すと、右端をぼかして「ほか N ›」の札を出す（{t}）")
            for _ in range(5):  # 1回で 8 割ずつ送る。送りきるまで押す
                if pg.inner_text("#chips-more") == "‹ 戻る":
                    break
                pg.click("#chips-more")
                pg.wait_for_timeout(700)
            seen = pg.evaluate("""() => { const c = document.querySelector('#position-chips').getBoundingClientRect();
                const b = [...document.querySelectorAll('#position-chips .chip')].pop().getBoundingClientRect();
                return b.right <= c.right + 1; }""")
            check(seen and pg.inner_text("#chips-more") == "‹ 戻る",
                  f"11/5 残件1（{w}×{h}）：札を押すと横に送られ、最後の番号が見える。送りきったら「‹ 戻る」")
            pg.click("#chips-more")
            until(pg, lambda: pg.evaluate("() => document.querySelector('#position-chips').scrollLeft") == 0)
            check(True, f"11/5 残件1（{w}×{h}）：「‹ 戻る」で、はじめの番号に戻る")
        else:
            check(pg.is_hidden("#chips-more"), f"11/5 残件1（{w}×{h}）：番号がはみ出さないときは札を出さない")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / f"U1105_{w}_chips.png"))
        pg.close()
    # ---- 個別ヘルプ（11/5）：マウス・押す・キーボード。次にすること（間違った入力の案内） ----
    def pop_open(pg):
        return pg.is_visible("#tip-pop")
    for (w, h) in [(1280, 720), (1920, 1080)]:
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: pg.is_visible("[data-tip=mic]"))
        tips = pg.eval_on_selector_all("button.tip[data-tip]", "els => els.filter(e => e.getClientRects().length).map(e => e.dataset.tip)")
        # Issue #7：移動量・位置・上下の説明の行は常に出さない（その「？」も一緒に出さない）。記録の「ⓘ」は記録が上の帯に出るときだけ
        check({"mic", "play"} <= set(tips) and not ({"count", "shift", "position", "updown", "status"} & set(tips)),
              f"11/5 個別ヘルプ・Issue #7・11/6（{w}×{h}）：マイク・再生の「？」が見える。回数の「？」は 3D の下に出さない・説明の行の「？」は出さない（{tips}）")
        pg.hover("[data-tip=mic]")
        until(pg, lambda: pop_open(pg))
        t = pg.inner_text("#tip-pop")
        box = pg.locator("#tip-pop").bounding_box()
        check("例：「Zプラス方向に10mm」" in t and box and box["x"] >= 0 and box["x"] + box["width"] <= w and box["y"] + box["height"] <= h,
              f"11/5 個別ヘルプ（{w}×{h}）：マウスを合わせると、短い説明と例（画面の中）")
        pg.mouse.move(5, h - 5)
        until(pg, lambda: not pop_open(pg))
        check(True, f"11/5 個別ヘルプ（{w}×{h}）：マウスを外すと消える")
        pg.click("[data-tip=play]")
        pg.mouse.move(5, h - 5)
        pg.wait_for_timeout(200)
        check(pop_open(pg) and "3D の再生" in pg.inner_text("#tip-pop") and pg.get_attribute("[data-tip=play]", "aria-expanded") == "true",
              f"11/5 個別ヘルプ（{w}×{h}）：押すと開いたまま（マウスを外しても消えない）（11/6：回数の「？」を外したので再生の「？」で確かめる）")
        pg.click("#tip-pop .tip-close")
        until(pg, lambda: not pop_open(pg))
        check(True, f"11/5 個別ヘルプ（{w}×{h}）：× で閉じる")
        pg.evaluate("document.activeElement && document.activeElement.blur()")  # × で「？」に戻ったフォーカスを外してから、もう一度合わせる
        pg.focus("[data-tip=play]")
        until(pg, lambda: pop_open(pg))
        pg.keyboard.press("Escape")
        until(pg, lambda: not pop_open(pg))
        check(pg.evaluate("document.activeElement.dataset.tip") == "play",
              f"11/5 個別ヘルプ（{w}×{h}）：キーボード（フォーカスで開く・Esc で閉じて「？」に戻る）")
        fill_text(pg, "C00002からC00003を3回にして")
        pg.click("#text-send")
        until(pg, lambda: pg.is_visible("#reply-message .next-step"))
        rm = pg.inner_text("#reply-message")
        check("区間（C00002〜C00003）の中に、区間の外の位置" in rm and "指定された範囲では変更できません。対象の位置を確認してください" in rm
              and pg.locator("#reply-message .next-step-ex").count() == 0 and pg.is_hidden("#range-yes"),
              f"11/5 次にすること（{w}×{h}）：範囲が合わない：理由をそのまま出し、案内を添える（正しい範囲を推測して例に出さない・範囲の確認にしない）")
        pg.locator("#position-chips button", has_text="C00004").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00004")
        fill_text(pg, "10ミリ動かして")
        pg.click("#text-send")
        until(pg, lambda: pg.is_visible("#reply-message .next-step"))
        ns = pg.inner_text("#reply-message .next-step")
        check("移動する方向を指定してください" in ns and "例：「Zプラス方向に10mm」" in ns and pg.input_value("#text-input") == "",
              f"11/5 次にすること（{w}×{h}）：方向が無い：次の行動と例（例は欄に入れない）：{ns}")
        if shots is not None:
            pg.screenshot(path=str(shots / f"U1105_{w}_next_step.png"))
        pg.close()
    # ---- オープニング（11/5）：飛ばせる・自分で終わる・出し直せる・動きを減らす設定・読み込みの邪魔をしない・接続を言わない ----
    for (w, h) in [(1280, 720), (1920, 1080)]:
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.goto(url)
        pg.wait_for_timeout(300)
        check(pg.is_hidden("#opening"), f"11/5 オープニング（{w}×{h}）：自動の試験では自動で出さない")
        pg.goto(url + "?opening=1")
        until(pg, lambda: pg.is_visible("#opening"))
        t = pg.inner_text("#opening")
        check("SIMULATION ONLY" in t and "実機に接続しません" in t and "NOT CHECKED" in t and "NOT LOADED" in t
              and "実機の接続・安全の確認ではありません" in t and "CONNECTED" not in t and "接続済み" not in t,
              f"11/5 オープニング（{w}×{h}）：ROBOT・TOOL・JOB・READY は、確かめていない接続・安全を言わない")
        until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=5000)
        check(True, f"11/5 オープニング（{w}×{h}）：数秒で自分で終わり、メインの画面になる")
        pg.goto(url + "?opening=1")
        until(pg, lambda: pg.is_visible("#opening"))
        pg.keyboard.press("Escape")
        until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=1500)
        check(True, f"11/5 オープニング（{w}×{h}）：Esc で飛ばせる")
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        click_opening_replay(pg)
        until(pg, lambda: pg.is_visible("#opening"))
        check("LOADED" in pg.inner_text("#op-job") and "DEMO_BUTTON_REPEAT.JBI" in pg.inner_text("#op-job-note"),
              f"11/5 オープニング（{w}×{h}）：［▶ オープニング］で出し直せる。JOB は読み込んだファイルの名前だけ")
        pg.click("#opening")
        until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=1500)
        check(pg.is_visible("#mic-button"), f"11/5 オープニング（{w}×{h}）：押すと飛ばせる。下の画面はそのまま")
        pg.close()
    ctx = browser.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(url + "?opening=1")
    until(pg, lambda: pg.is_visible("#opening"))
    still = "still" in (pg.get_attribute("#opening", "class") or "")
    until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=3000)
    check(still, "11/5 オープニング：動きを減らす設定では、止めた絵だけ（動かさない）で、すぐ終わる")
    ctx.close()
    srv.shutdown()
    srv.server_close()


def check_answer_after_approval(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 F1：承認のあとに［送る］で質問すると、いちばん新しい答えの吹き出しが会話の欄の中に見える
    （1280×720・1366×768 で欄の上に隠れていた）。モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "f1_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    n_turns = "() => document.querySelectorAll('#ask-turns > li').length"
    try:
        for (w, h) in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            until(pg, lambda: pg.is_enabled("#text-send"))
            fill_text(pg, "このロボット、何をしている？")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
            fill_text(pg, "ボタンを押してるんだよ")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .talk-choice")
            pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
            pg.wait_for_selector("#ask-turns .bubble.taught")
            fill_text(pg, "じゃあ、3回にして")
            pg.click("#text-send")
            pg.wait_for_selector("#range-yes", timeout=5000)
            pg.click("#range-yes")
            until(pg, lambda: pg.is_enabled("#open-confirm"))
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            pg.fill("#approver-name", "e2e確認")
            pg.click("#approve-button")
            until(pg, lambda: pg.is_visible("#approval-status"))
            pg.wait_for_timeout(300)  # すぐに次の質問を送る人（承認のカードを見せてから 1.5 秒の内）
            before = pg.evaluate(n_turns)
            fill_text(pg, "このロボット、何をしている？")
            pg.click("#text-send")
            until(pg, lambda: pg.evaluate(n_turns) > before
                  and pg.locator("#ask-turns .thinking").count() == 0)
            pg.wait_for_timeout(400)  # 送りの描き直し（requestAnimationFrame）を待つ
            r = pg.evaluate("""() => { const box = document.querySelector('#side-scroll').getBoundingClientRect();
                const last = [...document.querySelectorAll('#ask-turns > li')].pop().getBoundingClientRect();
                return [last.top, last.bottom, box.top, box.bottom]; }""")
            check(r[0] >= r[2] - 1 and r[1] <= r[3] + 1,
                  f"11/5 F1（{w}×{h}）：承認のあとの質問の答えの吹き出しが会話の欄の中に見える"
                  f"（吹き出し {r[0]:.0f}〜{r[1]:.0f}px・欄 {r[2]:.0f}〜{r[3]:.0f}px）")
            # Issue #7：ふだんの記録は上の帯に出さない（承認の状態は候補のポップアップ）。全文はアプリ情報
            check(pg.is_visible("#text-send") and pg.is_visible("#approval-states")
                  and "承認 済み" in (pg.text_content("#app-info-status") or ""),
                  f"11/5 F1（{w}×{h}）：入力欄の［送る］と承認の状態は見えたまま")
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"F1_{w}_after_approval_answer.png"))
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_send_while_waiting(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 F3：答えを待っている間（「考えています」）に［送る］・Enter を押しても、書いた文が黙って消えない。
    待っている間は送らず、文は入力欄に残る（答えのあとに送れる）。声も入力欄に入るだけ（自動では送らない）。
    モックの台本の答え（tools/demo_mock.py の ScriptClient）を、サーバーの側で遅らせる。本物の Claude は呼ばない。"""
    import time
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    class SlowClient(dm.ScriptClient):
        def create(self, **kw):
            time.sleep(1.5)  # 本物の Claude は数秒かかる
            return super().create(**kw)

    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=SlowClient(), usage_log=cx.UsageLog(out / "f3_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    humans = "() => document.querySelectorAll('#ask-turns .bubble.human').length"
    thinking = "() => document.querySelectorAll('#ask-turns .thinking').length"
    try:
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        pg.add_init_script("try { localStorage.setItem('vt-voice-mode', 'browser'); } catch (_) {}")
        pg.add_init_script(FAKE_RECOGNITION)
        talks = []
        pg.on("request", lambda r: talks.append(r.url) if "/api/talk" in r.url else None)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: pg.is_enabled("#text-send"))
        fill_text(pg, "このロボット、何をしている？")
        pg.click("#text-send")
        until(pg, lambda: pg.evaluate(thinking) == 1)
        second = "ボタンを押してるんだよ"
        fill_text(pg, second)
        pg.click("#text-send", force=True)  # 押せないように見えても、押そうとする人
        pg.press("#text-input", "Enter")
        pg.wait_for_timeout(200)
        waiting = pg.evaluate(thinking) == 1
        kept = pg.input_value("#text-input")
        check(waiting and kept == second,
              f"11/5 F3：答えを待っている間に［送る］・Enter を押しても、書いた文は入力欄に残る（入力欄「{kept}」）")
        # 声：待っている間に話しても、入力欄に入るだけ（送らない）
        mic_ok = pg.is_visible("#mic-button") and pg.is_enabled("#mic-button")
        if mic_ok:
            pg.fill("#text-input", "")
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-stop"))
            pg.click("#voice-stop")
            until(pg, lambda: pg.input_value("#text-input") == FAKE_TEXT)
            pg.fill("#text-input", second)
        until(pg, lambda: pg.evaluate(thinking) == 0, 8000)
        pg.wait_for_timeout(300)
        check(len(talks) == 1 and pg.evaluate(humans) == 1 and pg.input_value("#text-input") == second,
              f"11/5 F3：待っている間は送らない（/api/talk {len(talks)}回・人の吹き出し {pg.evaluate(humans)}つ）、"
              f"答えのあとも文は残る（声でも送らない：マイク{'を使った' if mic_ok else 'は出ていない'}）")
        until(pg, lambda: pg.is_enabled("#text-send"))
        pg.click("#text-send")
        until(pg, lambda: pg.evaluate(humans) == 2)
        check(len(talks) == 2 and pg.input_value("#text-input") == "",
              f"11/5 F3：答えのあとに［送る］で、残っていた文を送れる（/api/talk {len(talks)}回）")
        # Issue #7：ふだんの記録（候補なし）は上の帯に出さない。全文はアプリ情報
        check(pg.is_visible("#text-send") and pg.is_hidden("#status-line") and "アプリの記録：" in (pg.text_content("#app-info-status") or ""),
              "11/5 F3・Issue #7：［送る］は見えたまま・ふだんの記録はアプリ情報")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / "F3_send_while_waiting.png"))
        until(pg, lambda: pg.evaluate(thinking) == 0, 8000)
        pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_load_while_waiting(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 G2：答えを待っている間（「考えています」）は、別の JOB を読み込めない（ファイルを選ぶ・ドロップ・［サンプルで試す］）。
    前の JOB の答え（補足・3D の光）が、新しい JOB の空の会話に出ないように。答えが来たあとは、ふつうに読み込める。
    モックの台本の答え（tools/demo_mock.py の ScriptClient）を、サーバーの側で 4 秒遅らせる。本物の Claude は呼ばない。"""
    import base64
    import time
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    class SlowClient(dm.ScriptClient):
        def create(self, **kw):
            time.sleep(4)  # 本物の Claude は長いと 60 秒ほどかかる
            return super().create(**kw)

    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=SlowClient(), usage_log=cx.UsageLog(out / "g2_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    humans = "() => document.querySelectorAll('#ask-turns .bubble.human').length"
    answers = "() => document.querySelectorAll('#ask-turns .bubble.claude:not(.thinking)').length"
    thinking = "() => document.querySelectorAll('#ask-turns .thinking').length"
    lit = "() => { const h = window.vtAskHighlight; return h ? ((h.positions || []).length + (h.lines || []).length) : 0; }"
    other = base64.b64encode((REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()).decode("ascii")
    try:
        # ドロップは DataTransfer を作るので、画面の CSP を外した中で（ほかのドロップの確かめと同じ）
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, bypass_csp=True)
        pg = ctx.new_page()
        pg.add_init_script("try { localStorage.setItem('vt-voice-mode', 'browser'); } catch (_) {}")
        pg.add_init_script(FAKE_RECOGNITION)
        talks, loads = [], []
        pg.on("request", lambda r: talks.append(r.url) if "/api/talk" in r.url else
              (loads.append(r.url) if "/api/load" in r.url or "/api/sample" in r.url else None))
        talked = []  # 答えが届いた /api/talk（読み込みで「考えています」が消えても、届くまで待つため）
        pg.on("requestfinished", lambda r: talked.append(r.url) if "/api/talk" in r.url else None)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: pg.is_enabled("#text-send"))
        fill_text(pg, "このロボット、何をしている？")
        pg.click("#text-send")
        until(pg, lambda: pg.evaluate(thinking) == 1)

        def same_job(tag):
            name = pg.inner_text("#file-name")
            check(name == "DEMO_BUTTON_REPEAT.JBI" and len(loads) == 1 and pg.evaluate(thinking) == 1
                  and pg.evaluate(humans) == 1,
                  f"11/5 G2：答えを待っている間に{tag}しても、別の JOB は読み込まない（JOB「{name}」・読み込み {len(loads)}回・"
                  f"「考えています」{pg.evaluate(thinking)}つ・人の吹き出し {pg.evaluate(humans)}つ）")

        # ファイルを選ぶ欄と［サンプルで試す］は、待っている間は押せない
        check(pg.is_disabled("#file-input") and pg.is_disabled("#sample-button"),
              "11/5 G2：答えを待っている間は、ファイルを選ぶ欄と［サンプルで試す］は押せない")
        try:
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI"), timeout=1000)
        except Exception:
            pass  # 押せない欄には入れられない（それで良い）
        pg.wait_for_timeout(300)
        same_job("ファイルを選ぼうと")
        # ページの上にドロップ（読み込んだあとの画面）と、ドロップの枠へのドロップ
        for target, tag in (("#ask-panel", "ページにドロップ"), ("#drop-zone", "ドロップの枠にドロップ")):
            dt = pg.evaluate_handle(DROP_FILE, ["DEMO_C_PLACE.JBI", other])
            pg.dispatch_event(target, "drop", {"dataTransfer": dt})
            pg.wait_for_timeout(300)
            same_job(tag)
        until(pg, lambda: len(talked) == 1 and pg.evaluate(thinking) == 0, 10000)
        pg.wait_for_timeout(300)
        name = pg.inner_text("#file-name")
        check(name == "DEMO_BUTTON_REPEAT.JBI" and len(loads) == 1 and len(talks) == 1
              and pg.evaluate(humans) == 1 and pg.evaluate(answers) == 1,
              f"11/5 G2：答えは、聞いた JOB の会話に出る（JOB「{name}」・読み込み {len(loads)}回・/api/talk {len(talks)}回・"
              f"人 {pg.evaluate(humans)}つ・答え {pg.evaluate(answers)}つ）")
        # 答えが来たあとは、ふつうに読み込める。新しい JOB の会話は空で、3D は光らない
        until(pg, lambda: pg.is_enabled("#file-input") and pg.is_enabled("#sample-button"))
        dt = pg.evaluate_handle(DROP_FILE, ["DEMO_C_PLACE.JBI", other])
        pg.dispatch_event("#ask-panel", "drop", {"dataTransfer": dt})
        until(pg, lambda: pg.inner_text("#file-name") == "DEMO_C_PLACE.JBI")
        pg.wait_for_timeout(500)
        n_h, n_a, n_lit = pg.evaluate(humans), pg.evaluate(answers), pg.evaluate(lit)
        check(len(loads) == 2 and n_h == 0 and n_a == 0 and n_lit == 0,
              f"11/5 G2：答えのあとはドロップで読み込める。新しい JOB の会話は空・3D は光らない"
              f"（読み込み {len(loads)}回・人 {n_h}つ・答え {n_a}つ・光 {n_lit}）")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / "G2_load_while_waiting.png"))
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_reload_same_file(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 G5：同じファイルを、ファイルを選ぶ欄からもう一度選んでも読み込む（ドロップと同じ決まり。同じ中身なら人が教えた意味を引き継ぐ）。
    Chrome は、欄に残っている前のファイルと同じものを選ぶと change を出さない。だから、読んだあとは欄が空であること
    （次に何を選んでも change が出る）を確かめる。読めなかったとき（読み込みの枠が見えたまま）も同じ。"""
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    job = str(REPO / "samples/dummy/DEMO_C_PLACE.JBI")
    bad = out / "g5_not_a_job.JBI"
    bad.write_text("これは JOB ではありません\n", encoding="utf-8")
    try:
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        loads = []
        pg.on("request", lambda r: loads.append(r.headers.get("x-vt-previous-session")) if "/api/load" in r.url else None)
        pg.goto(url)
        left = "() => { const i = document.querySelector('#file-input'); return [i.value, i.files.length]; }"
        pg.set_input_files("#file-input", job)
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: len(loads) == 1 and pg.inner_text("#file-name") == "DEMO_C_PLACE.JBI")
        v = pg.evaluate(left)
        check(v == ["", 0], f"11/5 G5：JOB を読んだあと、ファイルを選ぶ欄は空（同じファイルを選び直せる）（欄 {v!r}）")
        first = pg.evaluate("() => document.querySelector('#file-name').textContent")
        pg.set_input_files("#file-input", job)
        try:
            until(pg, lambda: len(loads) == 2, 3000)
        except TimeoutError:
            pass  # 読み込まなかった（下の確かめで NG にする）
        pg.wait_for_timeout(300)
        check(len(loads) == 2 and loads[1] is not None and pg.inner_text("#file-name") == first,
              f"11/5 G5：同じファイルを選び直すと、もう一度読み込む（ドロップと同じく前の画面の番号を送る）"
              f"（読み込み {len(loads)}回・前の番号 {'あり' if len(loads) > 1 and loads[1] else 'なし'}）")
        # 読めなかったとき：読み込みの枠が見えたまま。直さずに同じファイルを選び直しても、もう一度読む
        pg2 = browser.new_page(viewport={"width": 1280, "height": 720})
        n = []
        pg2.on("request", lambda r: n.append(r.url) if "/api/load" in r.url else None)
        pg2.goto(url)
        pg2.set_input_files("#file-input", str(bad))
        pg2.wait_for_selector("#load-error:not([hidden])")
        v2 = pg2.evaluate(left)
        check(pg2.is_visible("#drop-zone") and v2 == ["", 0] and len(n) == 1,
              f"11/5 G5：読めなかったあとも、ファイルを選ぶ欄は空（同じファイルを選び直せる）（欄 {v2!r}・読み込み {len(n)}回）")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / "G5_reload_same_file.png"))
        pg.close()
        pg2.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_voice_error_typing(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 G7：① の画面（入力欄はキーボードのボタンを押すまで出ない）で声の認識に失敗すると、
    「下の欄に文字で入力することもできます」と出る。そのとき、言っている欄が本当に見えていること（何も送らない）。
    ① の決まり（1280×720 で［位置を選ばずに話す］が下の帯より上・ページの縦横のスクロールなし）も崩さない。
    ② 会話の画面（入力欄はいつも見える）でも同じ文が正しいこと。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "g7_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    body = lambda pg: pg.get_attribute("body", "class") or ""  # noqa: E731
    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            for code in ["not-allowed", "no-speech", "network", "audio-capture"]:
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.add_init_script(FAKE_RECOGNITION)
                sends = []
                pg.on("request", lambda r: sends.append(r.url) if r.method == "POST" and "/api/load" not in r.url else None)
                pg.goto(url)
                pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
                pg.wait_for_selector("#job-area:not([hidden])")
                until(pg, lambda: "hero" in body(pg))
                check(pg.is_hidden("#text-input"), f"11/5 G7（{w}×{h}・{code}）：前提：① の初めは入力欄が出ていない")
                pg.wait_for_timeout(300)
                sends.clear()  # 読み込みのときの知らせ（/api/ask）は数えない。ここから先、🎤 の失敗で何も送らないこと
                pg.evaluate(f"window.__fails = [{code!r}]")
                pg.click("#mic-button")
                if pg.is_visible("#consent-yes"):
                    pg.click("#consent-yes")
                until(pg, lambda: pg.is_visible("#voice-error"))
                pg.wait_for_timeout(400)
                err = pg.inner_text("#voice-error")
                inp = pg.locator("#text-input").bounding_box()
                foot = pg.evaluate("() => document.querySelector('.foot').getBoundingClientRect().top")
                check("hero" in body(pg) and "下の欄に文字で入力" in err and pg.is_visible("#text-input")
                      and pg.is_visible("#text-send") and inp is not None and inp["y"] + inp["height"] <= foot
                      and pg.input_value("#text-input") == "" and not sends,
                      f"11/5 G7（{w}×{h}・{code}）：① で声の失敗のあと、文の言う「下の欄」（入力欄と［送る］）が帯より上に見える。"
                      f"何も送らない（欄 {'見える' if pg.is_visible('#text-input') else '無い'}・送信 {len(sends)}）")
                # Issue #7：［位置を選ばずに話す］は出さない。代わりに位置の番号のボタンが帯より上に見えること
                r = pg.evaluate("""() => { const b = document.querySelector('#position-chips'); let lim = document.querySelector('.foot').getBoundingClientRect().top;
                    for (let e = b.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e);
                      if (o.overflowY !== 'visible' || o.overflowX !== 'visible') lim = Math.min(lim, e.getBoundingClientRect().bottom); }
                    return [b.getBoundingClientRect().bottom, lim]; }""")
                sc = pg.evaluate(PAGE_SCROLL_JS)
                check(pg.is_hidden("#talk-first") and pg.is_visible("#position-chips") and r[0] <= r[1] and sc[0] <= sc[2] and sc[1] <= sc[3],
                      f"11/5 G7・Issue #7（{w}×{h}・{code}）：入力欄を出しても位置の番号は帯より上（下端 {r[0]:.0f}px・帯 {r[1]:.0f}px）、"
                      f"ページのスクロールなし（{sc}）")
                if shots is not None and code == "not-allowed":
                    shots.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(shots / f"G7_hero_error_{w}.png"))
                if code == "not-allowed":
                    # ② 会話の画面：入力欄はいつも見える。失敗の文の「下の欄」はそのまま正しい
                    fill_text(pg, "このロボット、何をしている？")
                    pg.click("#text-send")
                    until(pg, lambda: "hero" not in body(pg))
                    until(pg, lambda: pg.is_enabled("#text-send"))
                    pg.evaluate("window.__fails = ['no-speech']")
                    pg.click("#mic-button")
                    if pg.is_visible("#consent-yes"):
                        pg.click("#consent-yes")
                    until(pg, lambda: pg.is_visible("#voice-error"))
                    check("下の欄に文字で入力" in pg.inner_text("#voice-error") and pg.is_visible("#text-input"),
                          f"11/5 G7（{w}×{h}）：② で声の失敗のあとも、入力欄が見える")
                pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_mic_after_undo(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 実マイク（8825）：声で C00005 の候補を作る → ［元に戻す］→ 位置を選び直す と、マイクのボタンが灰色のまま押せなかった
    （ステップ4で押せなくしたまま、ステップ3に戻っても戻さなかった）。候補が無くなったら、マイクがまた押せて、続けて声で話せること。
    ステップ4（候補を確かめている間）は今までどおり押せない。会話の欄に、候補を捨てたアプリの文が出ること。承認・出力は押さない。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "mic_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def speak(pg, text):
        pg.evaluate("t => { window.__fakeText = t; }", text)
        pg.click("#mic-button")
        if pg.is_visible("#consent-yes"):
            pg.click("#consent-yes")
        until(pg, lambda: pg.is_enabled("#voice-stop"))
        pg.click("#voice-stop")
        until(pg, lambda: pg.input_value("#text-input") == text)
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#text-send"), 15000)
        pg.wait_for_timeout(300)
    try:
        for (w, h) in [(1920, 1080), (1366, 768)]:
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url + "?opening=0")
            pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            pg.locator("#position-chips button", has_text="C00005").first.click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
            speak(pg, "Yマイナス方向に10mm")
            until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
            check(pg.is_disabled("#mic-button"), f"11/5 夜 マイク（{w}×{h}）：候補を確かめている間（ステップ4）はマイクを押せない（今までの決まり）")
            pg.click("#undo-button")
            until(pg, lambda: "候補 なし" in pg.text_content("#status-line"))
            pg.wait_for_timeout(300)
            check(pg.is_visible("#mic-button") and pg.is_enabled("#mic-button"),
                  f"11/5 夜 マイク（{w}×{h}）：［元に戻す］で候補が無くなったら、マイクがまた押せる（灰色のままにしない）")
            notes = [" ".join(x.split()) for x in pg.locator("#ask-turns li.app").all_inner_texts()]
            check(any("変更候補を捨てました" in x for x in notes),
                  f"11/5 夜 マイク（{w}×{h}）：会話の欄に、候補を捨てたアプリの文が出る（前の候補の文だけを残さない）")
            speak(pg, "このロボット、何をしているの？")
            check(pg.is_enabled("#mic-button"), f"11/5 夜 マイク（{w}×{h}）：続けて声で話したあとも、マイクを押せる")
            speak(pg, "上に3mm")
            check(pg.is_enabled("#mic-button"), f"11/5 夜 マイク（{w}×{h}）：3回続けて声で話せる")
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_hold_after_approval(browser, check, shots: Path | None, out: Path) -> None:
    """P0（11/6）：承認のあとに、相づち・否定の無い質問ではない文（「違くない？」）を言うと保留。画面は
    ［取り消す］［このまま残す］のボタンを出し、出力は押せず、見出しは保留の文。［このまま残す］で承認は消え、もう一度承認ボタンから。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching import sim
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "hold_usage.jsonl"))
    old_out = sim.DEFAULT_OUT_DIR
    sim.DEFAULT_OUT_DIR = out / "hold_sim"
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        pg = browser.new_page(viewport={"width": 1920, "height": 1080})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url + "?opening=0")
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00005").first.click()
        fill_text(pg, "Zプラス方向に5ミリ")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"), 10000)
        pg.click("#open-confirm")
        pg.fill("#approver-name", "e2e確認")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"), 10000)
        check(pg.is_enabled("#export-button"), "P0 保留：前提：承認のあとは出力ボタンを押せる")
        check(pg.get_attribute("#approval-icon", "data-appr") == "ok" and "承認済み" in (pg.get_attribute("#approval-icon", "aria-label") or ""),
              f"承認のアイコン：承認のあとは緑のチェック・名前に「承認済み」（{pg.get_attribute('#approval-icon', 'aria-label')}）")
        fill_text(pg, "違くない？")
        pg.click("#text-send")
        until(pg, lambda: pg.locator("#reply-message button").count() >= 2, 10000)
        btns = [b.inner_text() for b in pg.locator("#reply-message button").all()]
        check(btns[:2] == ["取り消す", "このまま残す（もう一度承認）"] and pg.is_disabled("#export-button")
              and "保留" in pg.text_content("#status-line") and pg.inner_text("#next-action").startswith("取り消すか確かめています")
              and pg.get_attribute("#approval-icon", "data-appr") == "warn" and "保留" in (pg.get_attribute("#approval-icon", "aria-label") or "")
              and "保留" in (pg.get_attribute("#approval-icon", "title") or ""),
              f"P0 保留：承認のあとの「違くない？」で保留。ボタン {btns}・出力は押せない・見出しは保留の文・承認のアイコンは緑のチェックではなく警告（名前・小さな説明に「保留」）")
        pg.locator("#reply-message button", has_text="このまま残す").first.click()
        until(pg, lambda: "保留" not in pg.text_content("#status-line"), 10000)
        check(pg.is_hidden("#approval-status") and pg.is_disabled("#export-button") and pg.is_enabled("#open-confirm")
              and pg.get_attribute("#approval-icon", "data-appr") == "pending",
              "P0 保留：［このまま残す］で保留を解くと、承認は消え、もう一度［確認して承認へ］から（出力は押せない）")
        pg.close()
    finally:
        sim.DEFAULT_OUT_DIR = old_out
        srv.shutdown()
        srv.server_close()


def check_export_twice(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 H1：出力に成功したあと［シミュレーション用に出力］をもう一度押す（2回押す・ダブルクリック）と、
    2回目が「既にあるため、上書きしません」の赤い失敗になり、「出力しました」の見出し・「出力 済み」と食い違っていた。
    出力に成功したら、そのボタンは押せない（2回目を送らない）。ダブルクリックでも /api/export は1回だけ。
    サーバーの「上書きしない」決まりは変えない。1回目が本当に失敗したとき（同じ名前のファイルが既にある）は、
    今までどおり失敗を出す。出力は一時フォルダ（out の下）に書く。"""
    from voice_teaching import sim
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    saved_out_dir = sim.DEFAULT_OUT_DIR

    def approved_page(w, h):
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        exports = []
        pg.on("request", lambda r: exports.append(r.url) if r.method == "POST" and "/api/export" in r.url else None)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00003").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
        fill_text(pg, "Zプラス方向に10mm")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        pg.fill("#approver-name", "e2e確認")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        until(pg, lambda: pg.is_enabled("#export-button"))
        return pg, exports

    def no_contradiction(pg):
        box = pg.inner_text("#export-result")
        return (pg.get_attribute("#export-result", "data-ok") == "true" and "出力しました" in box
                and "上書きしません" not in box and "出力していません" not in box)

    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            for how in ["click2", "dblclick"]:
                sim.DEFAULT_OUT_DIR = out / f"h1_sim_{w}_{how}"
                pg, exports = approved_page(w, h)
                if how == "click2":
                    pg.click("#export-button")
                    pg.wait_for_selector("#export-result:not([hidden])")
                    pg.wait_for_timeout(300)
                    if pg.is_enabled("#export-button"):  # 押せるなら、人と同じようにもう一度押す
                        pg.click("#export-button")
                else:
                    pg.dblclick("#export-button")
                    pg.wait_for_selector("#export-result:not([hidden])")
                pg.wait_for_timeout(800)
                check(no_contradiction(pg) and len(exports) == 1 and pg.is_disabled("#export-button")
                      and "出力 済み" in (pg.text_content("#status-line") or "")
                      and pg.inner_text("#next-action") == "出力しました",
                      f"11/5 H1（{w}×{h}・{how}）：出力に成功したあとは［シミュレーション用に出力］を押せず、2回目を送らない。"
                      f"赤い「上書きしません」を出さない（送信 {len(exports)}回・ボタン {'押せない' if pg.is_disabled('#export-button') else '押せる'}・"
                      f"結果「{' '.join(pg.inner_text('#export-result').split())[:60]}」）")
                if shots is not None and how == "click2":
                    shots.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(shots / f"H1_{w}_export_twice.png"))
                pg.close()
        # 1回目が本当に失敗したとき（同じ名前のファイルが既にある）は、今までどおり失敗を出す（隠さない）
        d = out / "h1_sim_exists"
        d.mkdir(parents=True, exist_ok=True)
        (d / "DEMO_C_PLACE_SIM.JBI.txt").write_text("e2e：前からあるファイル（上書きしないことを確かめる）", encoding="utf-8")
        sim.DEFAULT_OUT_DIR = d
        pg, exports = approved_page(1280, 720)
        pg.click("#export-button")
        pg.wait_for_selector("#export-result:not([hidden])")
        pg.wait_for_timeout(300)
        box = pg.inner_text("#export-result")
        check(pg.get_attribute("#export-result", "data-ok") == "false" and "上書きしません" in box and len(exports) == 1
              and "出力 まだ" in (pg.text_content("#status-line") or "")
              and (d / "DEMO_C_PLACE_SIM.JBI.txt").read_text(encoding="utf-8").startswith("e2e："),
              f"11/5 H1：1回目の出力が本当に失敗したときは、失敗をそのまま出す（ファイルは上書きしない）：{' '.join(box.split())[:60]}")
        pg.close()
    finally:
        sim.DEFAULT_OUT_DIR = saved_out_dir
        srv.shutdown()
        srv.server_close()


# 11/5 夜 F6：1920×1080 で撮る動画の文字。測る要素（会話の吹き出し・話し手・状態の1行・返事の札・凡例・図の注意書き）
TEXT_SIZE_SELS = [".bubble.human .said", ".bubble.claude .said", ".bubble.app .said", ".bubble .who", ".status-line",
                  ".status-line .st-chip", "#reply-message", "#reply-message .range-lines", ".legend", ".legend.kinds",
                  ".figure-notes", ".figure-short", ".range-button-note", ".cand-heard", ".cand-warn"]
TEXT_SIZE_JS = """(sels) => { const r = {}; for (const s of sels) {
  const v = [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).map(e => parseFloat(getComputedStyle(e).fontSize));
  if (v.length) r[s] = [Math.min(...v), Math.max(...v)]; } return r; }"""
# 直す前（9671245）に同じ手順で測った値。1280×720・1366×768・1440×900 はこのまま（小さな画面の文字は変えない）
_TS_SIDE = {".bubble.human .said": [14, 14], ".bubble.claude .said": [15, 15], ".bubble .who": [11, 11]}
# Issue #7・11/6：記録（.status-line）は上の帯に出さない（承認の状態はアイコンだけ）。図の注意は短い1行（.figure-short・全文は ⓘ）
_TS_STATUS = {".status-line": [12, 12], ".status-line .st-chip": [12, 12]}
_TS_ALL = {".legend.kinds": [12, 12], ".figure-notes": [12, 12], ".figure-short": [12, 12]}
TEXT_SIZE_BEFORE = {
    "load": {**_TS_ALL, ".legend": [12, 12]},
    "conversation": {**_TS_SIDE, **_TS_ALL, ".legend": [12, 12]},
    "range": {**_TS_SIDE, **_TS_ALL, ".bubble.app .said": [14, 14], "#reply-message": [14, 14], "#reply-message .range-lines": [14, 14],
              ".legend": [12, 12], ".range-button-note": [12, 12]},
    "candidate": {**_TS_SIDE, **_TS_ALL, ".bubble.app .said": [13, 14], ".legend": [12, 13], ".cand-heard": [13, 13], ".cand-warn": [13, 13]},
    "approved": {**_TS_SIDE, **_TS_ALL, ".bubble.app .said": [13, 14], "#reply-message": [14, 14], ".legend": [12, 13], ".cand-warn": [12, 12]},
}
# 1920×1080 で、これより小さくしない（会話の文字 16px・話し手 13px・状態の1行 14px・凡例と注意書き 13px）
TEXT_SIZE_MIN_1080 = {".bubble.human .said": 16, ".bubble.claude .said": 16, ".bubble.app .said": 15, ".bubble .who": 13,
                      ".status-line": 14, ".status-line .st-chip": 14, "#reply-message": 16, "#reply-message .range-lines": 15,
                      ".legend": 13, ".legend.kinds": 13, ".figure-notes": 13, ".figure-short": 13, ".range-button-note": 13, ".cand-heard": 14, ".cand-warn": 14}
# 1920×1080 の 3D の大きさ（直す前 9671245 に同じ手順で測った値）。これより小さくしない
FIG_BEFORE_1080 = {"load": (1342, 742), "conversation": (1342, 742), "range": (1342, 742), "candidate": (1342, 681), "approved": (1342, 681)}


def check_opening_click_through(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 H3：オープニングを飛ばすときのダブルクリック・3回押し・Enter/Space の2回押しの2回目が、消えかけ（約0.45秒）の間に
    下の画面へ届き、ドロップ欄（ファイルを選ぶ窓）・［サンプルで試す］・位置の番号を押してしまっていた。
    飛ばすための押す・キーは、消えかけの間も下の画面に届かない。消え終わったら、すぐ（待ちを足さずに）ふだんどおり押せる。
    最初の表示（?opening=1）と、JOB を読み込んだあとの出し直し（［▶ オープニング］）の両方で、1920×1080 で確かめる。"""
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    # 下の画面に届いた押す・キーを記録する（オープニングの中に向いたものは数えない）
    spy = """() => { window.__leak = []; if (window.__spy) return; window.__spy = true;
      const rec = (ev) => { const o = document.getElementById('opening');
        if (!o.contains(ev.target)) window.__leak.push(ev.type + ':' + (ev.target.id || ev.target.tagName)); };
      for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click', 'dblclick']) document.addEventListener(t, rec, true);
      document.addEventListener('keydown', rec, false); }"""
    w, h = 1920, 1080
    pg = browser.new_page(viewport={"width": w, "height": h})
    pg.add_init_script(FAKE_RECOGNITION)
    choosers, posts = [], []
    pg.on("filechooser", lambda fc: choosers.append(1))
    pg.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/api/" in r.url else None)

    def centre(sel):
        b = pg.locator(sel).first.bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def gestures():
        pts = {"中央": (w / 2, h / 2)}
        for sel, name in [("#sample-button", "［サンプルで試す］"), ("#drop-zone", "ドロップ欄"), ("#position-chips button", "位置の番号")]:
            if pg.locator(sel).count() and pg.locator(sel).first.is_visible():
                pts[name] = centre(sel)
        res = []
        for name, (x, y) in pts.items():
            res.append((f"{name}をダブルクリック", lambda x=x, y=y: pg.mouse.dblclick(x, y)))
            res.append((f"{name}を3回押す", lambda x=x, y=y: pg.mouse.click(x, y, click_count=3)))
            res.append((f"{name}を0.2秒あけて2回押す", lambda x=x, y=y: (pg.mouse.click(x, y), pg.wait_for_timeout(200), pg.mouse.click(x, y))))
        for key in ["Enter", "Space", "Escape"]:
            res.append((f"{key} を2回", lambda key=key: (pg.keyboard.press(key), pg.wait_for_timeout(120), pg.keyboard.press(key))))
        return res

    def chosen():
        return pg.inner_text("#voice-position") if pg.is_visible("#voice-position") else ""

    def run_case(phase, show, name, do):
        show()
        until(pg, lambda: pg.is_visible("#opening"))
        pg.wait_for_timeout(400)
        pg.evaluate(spy)
        n_ch, n_posts, sel_before = len(choosers), len(posts), chosen()
        t0 = pg.evaluate("() => performance.now()")
        do()
        until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=2000)
        gone = pg.evaluate("() => performance.now()") - t0
        pg.wait_for_timeout(250)
        leak = pg.evaluate("() => window.__leak")
        ok = not leak and len(choosers) == n_ch and len(posts) == n_posts and chosen() == sel_before
        check(ok, f"11/5 夜 H3 オープニング（{phase}）：{name}で飛ばしても、下の画面に届かない"
                  f"（届いた：{leak or 'なし'}・ファイルの窓 {len(choosers) - n_ch}・送信 {len(posts) - n_posts}）")
        check(gone < 1100, f"11/5 夜 H3 オープニング（{phase}）：{name}：消えるまで {gone:.0f}ms（消えかけの分だけ。待ちを足さない）")

    # ① 最初の表示（JOB はまだ）
    pg.goto(url)
    for name, do in gestures():
        run_case("最初の表示", lambda: pg.goto(url + "?opening=1"), name, do)
    # 消え終わったら、すぐふだんどおり押せる（［サンプルで試す］で読み込める）
    pg.goto(url + "?opening=1")
    until(pg, lambda: pg.is_visible("#opening"))
    pg.mouse.dblclick(w / 2, h / 2)
    until(pg, lambda: pg.is_hidden("#opening"), timeout_ms=2000)
    samples = lambda: sum(1 for u in posts if "/api/sample" in u)
    n_samples = samples()
    pg.click("#sample-button", timeout=1000)
    until(pg, lambda: pg.is_visible("#job-area"), timeout_ms=3000)
    check(samples() == n_samples + 1, "11/5 夜 H3 オープニング：消え終わったら、すぐ［サンプルで試す］を押せる（1回だけ送る）")
    # ② JOB を読み込んだあとの出し直し
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_BUTTON_REPEAT.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.wait_for_timeout(300)
    for name, do in gestures():
        run_case("出し直し", lambda: click_opening_replay(pg), name, do)
    if shots is not None:
        pg.screenshot(path=str(shots / "H3_opening_after_skip.png"))
    pg.close()
    srv.shutdown()
    srv.server_close()


def check_export_path_short(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 H10（撮影）：出力のあとの結果と出力先の注意に、PC の利用者のフォルダ（C:\\Users\\…）まで出さない。
    画面にはファイル名と、アプリの中の場所（…\\output\\sim\\）だけを出す。絶対パスは要素の title（マウスを乗せると出る）に残す。
    1920×1080 と 1280×720 で確かめる。サーバーの返す値・書く場所は変えない"""
    from voice_teaching import sim
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    saved_out_dir = sim.DEFAULT_OUT_DIR
    name = "DEMO_C_PLACE_SIM.JBI.txt"
    try:
        for (w, h) in [(1920, 1080), (1280, 720)]:
            d = (out / f"h10_{w}" / "output" / "sim").resolve()
            sim.DEFAULT_OUT_DIR = d
            full_dir, full_file = str(d), str(d / name)
            above = str(d.parent.parent)  # アプリのフォルダにあたるところより上（C:\Users\… を含む）
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            pg.locator("#position-chips button", has_text="C00003").click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
            fill_text(pg, "Zプラス方向に10mm")
            pg.click("#text-send")
            until(pg, lambda: pg.is_enabled("#open-confirm"))
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            pg.fill("#approver-name", "e2e確認")
            pg.click("#approve-button")
            until(pg, lambda: pg.is_enabled("#export-button"))
            note = " ".join(pg.inner_text("#export-note").split()) if pg.is_visible("#export-note") else ""
            note_title = pg.get_attribute("#out-dir", "title") or ""
            check(pg.is_visible("#export-note") and "output\\sim" in note and above not in note and "Users" not in note
                  and "実機には入れないでください" in note and note_title == full_dir,
                  f"11/5 H10（{w}×{h}）：承認後の出力先の注意は、アプリの中の場所（output\\sim）だけを見せる（利用者のフォルダを出さない）。"
                  f"絶対パスは title に残す：「{note[:70]}」 title={'絶対パス' if note_title == full_dir else note_title[:40]}")
            pg.click("#export-button")
            pg.wait_for_selector("#export-result:not([hidden])")
            until(pg, lambda: "出力しました" in pg.inner_text("#export-result"))
            box = " ".join(pg.inner_text("#export-result").split())
            titles = pg.evaluate("() => Array.from(document.querySelectorAll('#export-result [title]')).map((e) => e.title)")
            note2 = " ".join(pg.inner_text("#export-note").split()) if pg.is_visible("#export-note") else ""
            check((d / name).is_file() and name in box and "output\\sim" in box and above not in box and "Users" not in box
                  and "実機には入れないでください" in box and full_file in titles
                  and above not in note2 and "Users" not in note2,
                  f"11/5 H10（{w}×{h}）：出力のあとの結果は、ファイル名と output\\sim だけを見せる（利用者のフォルダを出さない）。"
                  f"絶対パスは title に残す：「{box[:90]}」 title={titles}")
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"H10_{w}_export_path.png"))
            pg.close()
    finally:
        sim.DEFAULT_OUT_DIR = saved_out_dir
        srv.shutdown()
        srv.server_close()


def check_heading_matches(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 N4：大きな見出し（#next-action）が、今の操作と合う。
    (a) 声で入った文を確かめるときは、本当のボタン［送る］の名前で言う（古い「次へ」を言わない）。
    (b) 会話のあと（位置を選ぶ前）は、話す・書くこともできると言う（「点か番号を押してください」だけにしない）。
    (c) 指示の答えを待っている間は、待っていると言う（「🎤 を押して話してください」と言わない）。
    目立たせるボタンは1つまで。モックの解釈の係（遅らせる）と台本の答え（tools/demo_mock.py）。本物の Claude は呼ばない。"""
    import time
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    class SlowInterpreter:
        """モックを包み、解釈に少し時間をかける（待っている間の見出しを見るため）。"""

        def __init__(self):
            self.inner = MockInterpreter()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def decide(self, *a, **k):
            time.sleep(1.2)
            return self.inner.decide(*a, **k)

    head = lambda pg: pg.inner_text("#next-action")  # noqa: E731
    # (a)(c) 位置の指示（DEMO_C_PLACE・C00003・声で「Zプラス方向に10mm」）
    srv = server.make_server(port=0, interpreter=SlowInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.add_init_script("window.__fakeText = 'Zプラス方向に10mm'")
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00003").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
        pg.click("#mic-button")
        if pg.is_visible("#consent-yes"):
            pg.click("#consent-yes")
        until(pg, lambda: pg.is_enabled("#voice-stop"))
        pg.click("#voice-stop")
        until(pg, lambda: pg.input_value("#text-input") == "Zプラス方向に10mm")
        pg.wait_for_timeout(200)
        h = head(pg)
        check(h == "文を確かめて［送る］" and "次へ" not in h and pg.inner_text("#text-send") == "送る"
              and "primary" in (pg.get_attribute("#text-send", "class") or "") and _primaries(pg) == 1,
              f"11/5 N4(a)：声で入った文を確かめるときの見出しは、本当のボタン［送る］の名前で言う（目立つのは［送る］だけ）：{h}")
        pg.click("#text-send")
        until(pg, lambda: pg.input_value("#text-input") == "" and pg.is_disabled("#text-send"), 3000)
        h = head(pg)
        check(h == "少しお待ちください" and _primaries(pg) <= 1,
              f"11/5 N4(c)：指示の答えを待っている間は、待っていると言う（🎤 を押すように言わない）：{h}")
        until(pg, lambda: pg.is_enabled("#open-confirm"), 6000)
        check(head(pg) == "動きを確かめてください", f"11/5 N4(c)：答えのあとは、ふだんの見出しに戻る：{head(pg)}")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / "N4_after_send.png"))
        pg.close()
    finally:
        srv.shutdown()
        srv.server_close()
    # (b) 会話（DEMO_BUTTON_REPEAT・台本の答え）。位置を選ばずに「じゃあ、3回にして」まで
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "n4_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: pg.is_enabled("#text-send"))
        talk = "話しかけるか、点か番号を押してください"
        fill_text(pg, "このロボット、何をしている？")
        pg.click("#text-send")
        pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
        pg.wait_for_timeout(200)
        h = head(pg)
        check(h == talk and _primaries(pg) <= 1,
              f"11/5 N4(b)：会話の最初の答えのあと（位置を選ぶ前）は、話す・書くこともできると言う：{h}")
        fill_text(pg, "ボタンを押してるんだよ")
        pg.click("#text-send")
        pg.wait_for_selector("#ask-turns .talk-choice")
        pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
        pg.wait_for_selector("#ask-turns .bubble.taught")
        pg.wait_for_timeout(200)
        h = head(pg)
        check(h == talk, f"11/5 N4(b)：教えたあと（「じゃあ、3回にして」の前）も、話す・書くことができると言う：{h}")
        fill_text(pg, "じゃあ、3回にして")
        pg.click("#text-send")
        pg.wait_for_selector("#range-yes", timeout=5000)
        pg.wait_for_timeout(200)
        h = head(pg)
        check(h == "この範囲を繰り返しますか？" and _primaries(pg) <= 1,
              f"11/5 N4：範囲の確認の見出しは変えない：{h}")
        pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_approve_twice(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 N2：確認表示の［承認する］をダブルクリックすると /api/approve が2回送られ、作業ログに同じ承認が2行残っていた。
    1回の操作で送るのは1回だけ（位置の変更 DEMO_C_PLACE と回数の変更 DEMO_BUTTON_REPEAT）。
    本当の失敗（名前が空・サーバーの失敗）は今までどおり文を出し、もう一度押せる。承認はボタンだけ（声では承認しない）。"""
    import json as _json
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def confirm_page(job):
        pg = browser.new_page(viewport={"width": 1280, "height": 720})
        pg.add_init_script(FAKE_RECOGNITION)
        sent = []
        pg.on("request", lambda r: sent.append(r.url) if r.method == "POST" and "/api/approve" in r.url else None)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / job))
        pg.wait_for_selector("#job-area:not([hidden])")
        if job == "DEMO_C_PLACE.JBI":
            pg.locator("#position-chips button", has_text="C00003").click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
            fill_text(pg, "Zプラス方向に10mm")
            pg.click("#text-send")
        else:
            pg.locator("#position-chips button", has_text="C00001").first.click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00001")
            fill_text(pg, REPEAT_SAY)
            pg.click("#text-send")
            pg.wait_for_selector("#range-yes")
            pg.click("#range-yes")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        return pg, sent

    approvals = lambda pg: (pg.text_content("#work-log") or "").count("さんが承認ボタンで承認しました")  # noqa: E731
    try:
        for job in ["DEMO_C_PLACE.JBI", "DEMO_BUTTON_REPEAT.JBI"]:
            pg, sent = confirm_page(job)
            # 名前が空：文を出すだけで送らない。名前を入れてダブルクリック → 送るのは1回
            pg.click("#approve-button")
            empty_ok = pg.is_visible("#confirm-error") and "名前を入れて" in pg.inner_text("#confirm-error") and len(sent) == 0
            pg.fill("#approver-name", "e2e確認")
            pg.dblclick("#approve-button")
            until(pg, lambda: pg.is_visible("#approval-status"))
            pg.wait_for_timeout(800)
            n = approvals(pg)
            check(empty_ok and len(sent) == 1 and n == 1 and pg.is_hidden("#confirm-overlay")
                  and "e2e確認 さんが承認しました" in pg.inner_text("#approval-status"),
                  f"11/5 N2（{job}）：［承認する］をダブルクリックしても /api/approve は1回、作業ログの承認は1行"
                  f"（名前が空のときは送らず文を出す：{empty_ok}・送信 {len(sent)}回・ログ {n}行）")
            if shots is not None and job == "DEMO_C_PLACE.JBI":
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / "N2_approve_twice.png"))
            pg.close()
        # サーバーの失敗：ダブルクリックでも送るのは1回、失敗の文を出し、もう一度押せば承認できる
        pg, sent = confirm_page("DEMO_C_PLACE.JBI")
        body = _json.dumps({"ok": False, "error": "e2e：わざと失敗させた"}, ensure_ascii=False)
        pg.route("**/api/approve", lambda r: r.fulfill(status=500, content_type="application/json", body=body))
        pg.fill("#approver-name", "e2e確認")
        pg.dblclick("#approve-button")
        until(pg, lambda: pg.is_visible("#confirm-error"))
        pg.wait_for_timeout(500)
        fail_ok = (len(sent) == 1 and pg.is_visible("#confirm-overlay") and pg.is_enabled("#approve-button")
                   and pg.is_hidden("#approval-status") and approvals(pg) == 0)
        pg.unroute("**/api/approve")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        pg.wait_for_timeout(300)
        check(fail_ok and len(sent) == 2 and approvals(pg) == 1,
              f"11/5 N2：承認が本当に失敗したときは文を出し、もう一度押せる（失敗のとき {fail_ok}・送信 {len(sent)}回・ログ {approvals(pg)}行）")
        pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


# 人が教えた意味の札（g.taught-mark の rect）と、ほかの 3D の文字・点・窓・枠との重なり（2px の間をあける）
TAUGHT_PILL_OVERLAP_JS = """() => {
  const svg = document.querySelector('#fig-3d svg');
  const pills = [...document.querySelectorAll('#fig-3d g.taught-mark rect')];
  if (!svg || !pills.length) return ['札が無い'];
  const a = svg.getBoundingClientRect(), h = document.querySelector('#fig-3d').getBoundingClientRect();
  const L = Math.max(a.left, h.left, 0), R = Math.min(a.right, h.right, innerWidth);
  const T = Math.max(a.top, h.top, 0), B = Math.min(a.bottom, h.bottom, innerHeight);
  const others = [...svg.querySelectorAll('g.pt3 > text, text.moved-label, text.repeat-label, g.inset > rect')]
    .map((e) => [e.textContent || e.parentNode.getAttribute('class'), e.getBoundingClientRect()]);
  const dots = [...svg.querySelectorAll('g.pt3')].map((g) => {
    const c = [...g.querySelectorAll('circle')].find((q) => q.getAttribute('r') === '6');
    return ['点 ' + g.dataset.names, c.getBoundingClientRect()]; });
  const bad = [];
  for (const p of pills) {
    const r = p.getBoundingClientRect();
    if (r.left < L || r.right > R || r.top < T || r.bottom > B) bad.push(`枠の外（札 ${Math.round(r.left)},${Math.round(r.top)}-${Math.round(r.right)},${Math.round(r.bottom)} 枠 ${Math.round(L)},${Math.round(T)}-${Math.round(R)},${Math.round(B)}）`);
    for (const [s, q] of others.concat(dots)) if (r.left < q.right + 2 && r.right > q.left - 2 && r.top < q.bottom + 2 && r.bottom > q.top - 2)
      bad.push(`${s}（${Math.round(q.left)},${Math.round(q.top)}-${Math.round(q.right)},${Math.round(q.bottom)} 札 ${Math.round(r.left)},${Math.round(r.top)}-${Math.round(r.right)},${Math.round(r.bottom)}）`);
  }
  return bad; }"""


def check_taught_pill_clear(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 G6：DEMO_BUTTON_REPEAT で意味を教えたあと（「✓ ボタンを押してる（人が教えた）」の札）、教えた直後・範囲の確認・
    回数の変更の候補・承認のあとで、札が点の名前（C00003 など）・点・動かす点の名前・回数の札・右上の窓・枠の端に重ならない
    （2px の間をあける。1280×720・1920×1080）。札を置き直すだけで、点は動かさない：同じ段階の、教えていないときの点の画面の位置と
    同じ（1px 以内）。モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "g6_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def flow(w, h, teach):
        """読み込み →（教える）→ 範囲の確認 → 候補 → 承認。段階ごとに、点の画面の位置と札の重なりを返す。"""
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: pg.is_enabled("#text-send"))
        res = {}

        def stage(name):
            pg.wait_for_timeout(400)
            res[name] = (pg.evaluate(PT3_CENTERS_JS), pg.evaluate(TAUGHT_PILL_OVERLAP_JS) if teach else None)
            if shots is not None and teach:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"G6_{w}_{name}.png"))

        if teach:
            fill_text(pg, "このロボット、何をしている？")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
            fill_text(pg, "ボタンを押してるんだよ")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .talk-choice")
            pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
            pg.wait_for_selector("#ask-turns .bubble.taught")
            until(pg, lambda: pg.locator("#fig-3d g.taught-mark").count() > 0)
        stage("taught")  # 教えていないときは、読み込んだあとの同じ所
        fill_text(pg, "じゃあ、3回にして")
        pg.click("#text-send")
        pg.wait_for_selector("#range-yes", timeout=5000)
        stage("range")
        pg.click("#range-yes")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        stage("repeat_candidate")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        pg.fill("#approver-name", "e2e確認")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        pg.wait_for_timeout(300)
        stage("approved")
        pg.close()
        return res

    def moved(a, b):
        if set(a) != set(b):
            return {"点の組が違う": (sorted(a), sorted(b))}
        return {n: (round(a[n][0] - b[n][0], 1), round(a[n][1] - b[n][1], 1)) for n in a
                if abs(a[n][0] - b[n][0]) > 1 or abs(a[n][1] - b[n][1]) > 1}
    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            ref, got = flow(w, h, False), flow(w, h, True)
            for name in ("taught", "range", "repeat_candidate", "approved"):
                bad = got[name][1]
                check(not bad, f"11/5 G6（{w}×{h}・{name}）：人が教えた意味の札が、点の名前・点・回数の札・窓・枠の端に重ならない（重なり {bad or 'なし'}）")
                diff = moved(got[name][0], ref[name][0])
                check(not diff, f"11/5 G6（{w}×{h}・{name}）：札を出しても点は動かない（教えていないときと比べて 1px より動いた点 {diff or 'なし'}）")
    finally:
        srv.shutdown()
        srv.server_close()


# 要素がスクロールせずに見えて、左寄り・中央・右寄りの3点でいちばん上にあるか（ほかの要素に隠れていない）
IN_VIEW_TOP_JS = """(s) => { const e = document.querySelector(s); if (!e || !e.getClientRects().length) return false;
  const b = e.getBoundingClientRect(); if (b.top < 0 || b.bottom > innerHeight || b.left < 0 || b.right > innerWidth) return false;
  const y = b.top + b.height / 2;
  return [0.2, 0.5, 0.8].every((f) => { const t = document.elementFromPoint(Math.min(innerWidth - 1, b.left + b.width * f), Math.min(innerHeight - 1, y));
    return !!t && (t === e || e.contains(t)); }); }"""
EXPORT_EXISTS_NEXT = "次にすること：同じ名前のファイルが output\\sim\\ にあります。消してから、もう一度［出力］"


def check_export_fail_next(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 確定版 1（K1・K2）：出力に失敗したとき（同じ名前のファイルが既にある）、結果の欄にサーバーの理由をそのまま出し、
    「次にすること：同じ名前のファイルが output\\sim\\ にあります。消してから、もう一度［出力］」を出す。［出力］は押せるまま
    （目立たせない）、見出しは「出力できませんでした」。上書きはしない（ファイルの中身と日時が変わらない）。消してから押すと出力できる。
    承認が消えて失敗したとき（承認のあとで上下の向きの設定が変わった）は、理由＋「次にすること：もう一度承認してください」。
    出力は一時フォルダ（out の下）に書く。"""
    from voice_teaching import jbi, sim, updown
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cfg_dir = out / "k1_updown"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    job = jbi.load(REPO / "samples" / "dummy" / "DEMO_C_PLACE.JBI")
    cfg = updown.write_config(updown.build_config(job, updown.DEFAULT_ROBOT, "e2e（一時フォルダの仮の設定）", "e2e 用の仮の設定"),
                              cfg_dir / "updown_DEMO_C_PLACE.JBI.json", overwrite=True)
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    saved_out_dir = sim.DEFAULT_OUT_DIR

    def approved_page(w, h):
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.add_init_script(FAKE_RECOGNITION)
        exports = []
        pg.on("request", lambda r: exports.append(r.url) if r.method == "POST" and "/api/export" in r.url else None)
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy" / "DEMO_C_PLACE.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00003").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
        fill_text(pg, "Zプラス方向に10mm")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        pg.fill("#approver-name", "e2e確認")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        until(pg, lambda: pg.is_enabled("#export-button"))
        return pg, exports

    def press_export(pg, exports):
        n = len(exports)
        pg.click("#export-button")
        until(pg, lambda: len(exports) > n and pg.is_visible("#export-result"))
        pg.wait_for_timeout(400)
        return " ".join(pg.inner_text("#export-result").split())

    def primary(pg):
        return "primary" in (pg.get_attribute("#export-button", "class") or "")

    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            d = out / f"k1_sim_exists_{w}"
            d.mkdir(parents=True, exist_ok=True)
            f = d / "DEMO_C_PLACE_SIM.JBI.txt"
            f.write_text("e2e：前からあるファイル（上書きしないことを確かめる）", encoding="utf-8")
            before = (f.read_bytes(), f.stat().st_mtime_ns)
            sim.DEFAULT_OUT_DIR = d
            pg, exports = approved_page(w, h)
            for i in (1, 2):  # 押し直しても同じ：理由と次にすること。上書きしない
                box = press_export(pg, exports)
                check(pg.get_attribute("#export-result", "data-ok") == "false"
                      and "出力しませんでした：DEMO_C_PLACE_SIM.JBI.txt が既にあるため、上書きしません" in box
                      and EXPORT_EXISTS_NEXT in box,
                      f"11/5 確定版 K2（{w}×{h}・{i}回目）：「既にある」で失敗したら、サーバーの理由のまま＋次にすること（消してから、もう一度［出力］）：{box[:120]}")
                check(pg.is_enabled("#export-button") and not primary(pg) and pg.inner_text("#next-action") == "出力できませんでした",
                      f"11/5 確定版 K2（{w}×{h}・{i}回目）：失敗のあと［出力］は押せる・目立たせない・見出しは「出力できませんでした」"
                      f"（押せる={pg.is_enabled('#export-button')}・目立つ={primary(pg)}・見出し「{pg.inner_text('#next-action')}」）")
                check(str(out) not in box and not re.search(r"[A-Za-z]:\\|(/[A-Za-z0-9._-]+){2,}", box),
                      f"11/5 確定版 K2（{w}×{h}・{i}回目）：失敗の欄に絶対パスを出さない：{box[:120]}")
                check((f.read_bytes(), f.stat().st_mtime_ns) == before and len(list(d.iterdir())) == 1,
                      f"11/5 確定版 K1（{w}×{h}・{i}回目）：同じ名前のファイルは上書きしない（中身と日時が同じ・ほかのファイルを作らない）")
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"K2_{w}_export_exists.png"))
            f.unlink()  # 次にすること（消してから、もう一度［出力］）のとおりにすると、出力できる
            box = press_export(pg, exports)
            check(pg.get_attribute("#export-result", "data-ok") == "true" and "出力しました" in box and "次にすること" not in box
                  and pg.is_disabled("#export-button") and pg.inner_text("#next-action") == "出力しました" and f.exists(),
                  f"11/5 確定版 K2（{w}×{h}）：消してから［出力］を押すと出力でき、そのあと押せない（H1）：{box[:80]}")
            pg.close()
        # 失敗のあと［元に戻す］→ 新しい候補：前の結果の欄は消す（もう今の承認のものではない。11/5 確定版 1 の表）
        d = out / "k1_sim_exists_undo"
        d.mkdir(parents=True, exist_ok=True)
        (d / "DEMO_C_PLACE_SIM.JBI.txt").write_text("e2e：前からあるファイル", encoding="utf-8")
        sim.DEFAULT_OUT_DIR = d
        pg, exports = approved_page(1280, 720)
        press_export(pg, exports)
        pg.click("#undo-button")
        until(pg, lambda: not pg.is_visible("#approval-status"))
        pg.wait_for_timeout(300)
        after_undo = pg.is_visible("#export-result")
        fill_text(pg, "Zプラス方向に10mm")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        pg.wait_for_timeout(400)
        after_new = pg.is_visible("#export-result")
        check(not after_undo and not after_new,
              f"11/5 確定版 1（元に戻した・新しい候補）：失敗の結果の欄を消す（元に戻したあと 見える={after_undo}・新しい候補のあと 見える={after_new}）")
        pg.close()
        # 承認が消えて失敗したとき（承認のあとで上下の向きの設定が変わった）：理由＋「もう一度承認してください」
        sim.DEFAULT_OUT_DIR = out / "k1_sim_updown"
        pg, exports = approved_page(1280, 720)
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # 設定が1バイト変わる
        box = press_export(pg, exports)
        check(pg.get_attribute("#export-result", "data-ok") == "false" and "承認を取り消しました" in box
              and "次にすること：もう一度承認してください" in box and "output\\sim\\" not in box
              and not (out / "k1_sim_updown").exists(),
              f"11/5 確定版 1（そのほかの失敗）：承認が消えたときは、理由＋「次にすること：もう一度承認してください」：{box[:120]}")
        pg.close()
    finally:
        sim.DEFAULT_OUT_DIR = saved_out_dir
        srv.shutdown()
        srv.server_close()


def check_ask_limit_reset(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 確定版 4（U-02）：質問の上限（「上限に達しました」）のあと、［会話をやり直す］を、スクロールせずに押せる
    （1280×720・1920×1080。見えていて、3点でいちばん上）。#ask-reset は1つだけ。押すと会話が空になり、また聞ける。
    やり直したあとは「詳しく」の中に戻る。モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "u02_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def ask_once(pg, text):
        n = pg.locator("#ask-turns > li").count()
        fill_text(pg, text)
        pg.click("#text-send")
        until(pg, lambda: pg.locator("#ask-turns > li").count() > n + 1 and pg.locator("#ask-turns .thinking").count() == 0, 8000)
        pg.wait_for_timeout(200)

    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            until(pg, lambda: pg.is_enabled("#text-send"))
            pg.locator("#position-chips button:not([disabled])").nth(1).click()
            pg.wait_for_timeout(300)
            for _ in range(cx.MAX_TURNS + 1):
                ask_once(pg, "このロボット、何をしている？")
            limit = "上限に達しました" in pg.inner_text("#ask-turns")
            pg.wait_for_timeout(400)
            seen = pg.evaluate(IN_VIEW_TOP_JS, "#ask-reset")
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"U02_{w}_ask_limit.png"))
            check(limit and seen and pg.is_enabled("#ask-reset") and pg.locator("#ask-reset").count() == 1,
                  f"11/5 確定版 4（{w}×{h}）：質問の上限のあと、［会話をやり直す］がスクロールせずに見えて押せる"
                  f"（上限の文={limit}・見える={seen}・押せる={pg.is_enabled('#ask-reset')}・数={pg.locator('#ask-reset').count()}）")
            if not seen:
                pg.close()
                continue
            pg.click("#ask-reset")  # 人と同じように押す（force は使わない）
            until(pg, lambda: "上限に達しました" not in pg.inner_text("#ask-turns"))
            pg.wait_for_timeout(300)
            home = pg.evaluate("() => !!document.querySelector('.talk-more #ask-reset')")
            ask_once(pg, "このロボット、何をしている？")
            again = (pg.locator("#ask-turns .bubble.claude:not(.thinking)").count() >= 1
                     and "上限に達しました" not in pg.inner_text("#ask-turns"))
            check(home and again and pg.locator("#ask-reset").count() == 1,
                  f"11/5 確定版 4（{w}×{h}）：［会話をやり直す］を押すと会話が空になり、また聞ける。ボタンは「詳しく」の中に戻る"
                  f"（戻った={home}・聞けた={again}）")
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


APPROVE_TIP_BODY = "［確認して承認へ］を押し、名前を入れて［承認する］。声では承認しません。"


def check_approve_tip_error(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 確定版 3（U-07・N-2）：候補があるとき、承認の「？」を開いても［確認して承認へ］が隠れない（見えていて、3点でいちばん上）。
    説明の文は「［確認して承認へ］を押し、名前を入れて［承認する］。声では承認しません。」。
    確認表示で名前なしに［承認する］を押すと、#confirm-error が下の固定の帯の中（名前の欄の上）に、見えていて3点でいちばん上に出る。
    確認表示のほかの中身（見出し・注意）は今までどおり見える。1280×720・1920×1080。サンプル（DEMO_C_PLACE）・モックの解釈。"""
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            pg.click("#sample-button")
            pg.wait_for_selector("#job-area:not([hidden])")
            until(pg, lambda: pg.is_enabled("#text-send"))
            pg.locator("#position-chips button:not([disabled])").nth(3).click()
            pg.wait_for_timeout(300)
            fill_text(pg, "Zプラス方向に10mm")
            pg.click("#text-send")
            until(pg, lambda: pg.is_enabled("#open-confirm"), 8000)
            pg.wait_for_timeout(300)
            pg.hover('button.tip[data-tip="approve"]')
            pg.wait_for_timeout(300)
            pop = pg.is_visible("#tip-pop")
            body = pg.inner_text("#tip-pop .tip-body") if pop else ""
            seen = pg.evaluate(IN_VIEW_TOP_JS, "#open-confirm")
            pop_in = pg.evaluate("() => { const b = document.querySelector('#tip-pop').getBoundingClientRect();"
                                 " return b.top >= 0 && b.bottom <= innerHeight && b.left >= 0 && b.right <= innerWidth; }")
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pg.screenshot(path=str(shots / f"U07_{w}_approve_tip.png"))
            check(pop and pop_in and body == APPROVE_TIP_BODY and seen,
                  f"11/5 確定版 3（{w}×{h}）：承認の「？」を開いても［確認して承認へ］が隠れない。説明は画面の中で、文は決まった文"
                  f"（説明={pop}・画面の中={pop_in}・文={body!r}・ボタンが見える={seen}）")
            pg.mouse.move(2, 2)
            pg.wait_for_timeout(200)
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            pg.wait_for_timeout(400)
            pg.click("#approve-button")
            until(pg, lambda: pg.is_visible("#confirm-error"))
            pg.wait_for_timeout(300)
            err = pg.evaluate(IN_VIEW_TOP_JS, "#confirm-error")
            where = pg.evaluate("""() => { const e = document.querySelector('#confirm-error'), n = document.querySelector('#approver-name');
              return !!e.closest('.confirm-actions') && e.getBoundingClientRect().bottom <= n.getBoundingClientRect().top; }""")
            name_seen = pg.evaluate(IN_VIEW_TOP_JS, "#approver-name")
            title_seen = pg.evaluate(IN_VIEW_TOP_JS, "#confirm-title")
            warn_seen = pg.evaluate(IN_VIEW_TOP_JS, "#confirm-warnings")
            if shots is not None:
                pg.screenshot(path=str(shots / f"U07_{w}_no_name.png"))
            check(err and where and name_seen and title_seen and warn_seen and "名前を入れて" in pg.inner_text("#confirm-error"),
                  f"11/5 確定版 3（{w}×{h}）：名前なしで［承認する］→ 注意が下の帯の中（名前の欄の上）に見える。見出し・注意も見える"
                  f"（注意が見える={err}・帯の中で名前の上={where}・名前の欄={name_seen}・見出し={title_seen}・注意={warn_seen}）")
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_text_size_1080(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 F6：1920×1080（デモの動画を撮る大きさ）で、会話・話し手・状態の1行・返事の札・凡例・図の注意書きの文字を大きく。
    1280×720・1366×768・1440×900 の文字の大きさは前のまま。1920×1080 でもページはスクロールせず、3D は小さくならず、
    いちばん新しい吹き出し・入力欄・［送る］（出ている状態で）・［この範囲を繰り返す］・［確認して承認へ］が見える。
    モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "f6_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    fig_js = "() => { const r = document.querySelector('#fig-3d svg').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }"
    not_seen = """(sels) => sels.filter(s => { const e = document.querySelector(s); if (!e || e.offsetParent === null) return true;
      const r = e.getBoundingClientRect(); return r.top < -0.5 || r.bottom > innerHeight + 0.5 || r.width === 0; })"""
    # 会話の欄の中に切れずに見えないもの（"last" は、いちばん新しい吹き出し）
    out_of_talk = """(sels) => { const box = document.querySelector('#side-scroll').getBoundingClientRect();
      return sels.filter(s => { const e = s === 'last' ? [...document.querySelectorAll('#ask-turns > li')].pop() : document.querySelector(s);
        if (!e || e.offsetParent === null) return true;
        const r = e.getBoundingClientRect(); return r.top < box.top - 1 || r.bottom > box.bottom + 1; }); }"""
    try:
        for (w, h) in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
            big = (w, h) == (1920, 1080)
            pg = browser.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(FAKE_RECOGNITION)
            pg.goto(url)
            pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
            pg.wait_for_selector("#job-area:not([hidden])")
            until(pg, lambda: pg.is_enabled("#text-send"))

            def stage(name, keys, newest):
                pg.wait_for_timeout(400)
                got = pg.evaluate(TEXT_SIZE_JS, TEXT_SIZE_SELS)
                if not big:
                    same = got == TEXT_SIZE_BEFORE[name]
                    check(same, f"11/5 F6（{w}×{h}・{name}）：文字の大きさは前のまま（{'同じ' if same else got}）")
                    return
                sizes = {s: v[0] for s, v in got.items()}
                small = {s: v for s, v in sizes.items() if v < TEXT_SIZE_MIN_1080[s]}
                check(not small and (name == "load" or ".bubble .who" in got),
                      f"11/5 F6（1920×1080・{name}）：文字が大きい（{sizes}・小さいもの {small or 'なし'}）")
                fig = pg.evaluate(fig_js)
                fb = FIG_BEFORE_1080[name]
                sw, sh, iw, ih = pg.evaluate(PAGE_SCROLL_JS)
                hidden = pg.evaluate(not_seen, keys) + pg.evaluate(out_of_talk, newest)
                check(fig[0] >= fb[0] and fig[1] >= fb[1] and sw <= iw and sh <= ih and not hidden,
                      f"11/5 F6（1920×1080・{name}）：3D は小さくならない（{fig[0]}×{fig[1]}・前 {fb[0]}×{fb[1]}）・"
                      f"ページのスクロールなし（{sw}×{sh}）・見えないもの {hidden or 'なし'}（いちばん新しい答え・札は会話の欄の中）")
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(shots / f"F6_1920_{name}.png"))

            pg.wait_for_timeout(300)
            stage("load", [], [])
            fill_text(pg, "このロボット、何をしている？")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
            stage("conversation", ["#text-input", "#text-send"], ["last"])
            fill_text(pg, "ボタンを押してるんだよ")
            pg.click("#text-send")
            pg.wait_for_selector("#ask-turns .talk-choice")
            pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
            pg.wait_for_selector("#ask-turns .bubble.taught")
            fill_text(pg, "じゃあ、3回にして")
            pg.click("#text-send")
            pg.wait_for_selector("#range-yes", timeout=5000)
            stage("range", ["#text-input", "#text-send", "#range-yes"], ["last", "#reply-message .range-where"])
            pg.click("#range-yes")
            until(pg, lambda: pg.is_enabled("#open-confirm"))
            stage("candidate", ["#open-confirm"], [])
            pg.click("#open-confirm")
            pg.wait_for_selector("#confirm-overlay:not([hidden])")
            pg.fill("#approver-name", "e2e確認")
            pg.click("#approve-button")
            until(pg, lambda: pg.is_visible("#approval-status"))
            pg.wait_for_timeout(1800)  # 承認のカードを見せたあとの答え
            stage("approved", ["#text-input", "#text-send"], ["#reply-message", "#approval-status"])  # 承認のあとは、返事の札と承認のカードがいちばん新しい
            pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


# 3D の図の中の文字のうち、見える枠（svg の四角・#fig-3d の四角・画面の重なり）から出ているもの。
# cutOnly のときは、枠の端で切れている（一部だけ見える）ものだけ（区間を大きく見せる図では、区間の外は図の外に出てよい）
TEXT3D_OUTSIDE_JS = """(cutOnly) => {
  const svg = document.querySelector('#fig-3d svg');
  if (!svg) return ['svg なし'];
  const a = svg.getBoundingClientRect(), h = document.querySelector('#fig-3d').getBoundingClientRect();
  const L = Math.max(a.left, h.left, 0), R = Math.min(a.right, h.right, innerWidth);
  const T = Math.max(a.top, h.top, 0), B = Math.min(a.bottom, h.bottom, innerHeight);
  return [...svg.querySelectorAll('text')].filter((t) => t.textContent.trim()).map((t) => [t.textContent, t.getBoundingClientRect()])
    .filter(([, r]) => r.width > 0 && (r.left < L - 0.5 || r.right > R + 0.5 || r.top < T - 0.5 || r.bottom > B + 0.5)
      && (!cutOnly || (r.right > L && r.left < R && r.bottom > T && r.top < B)))
    .map(([s, r]) => `${s}（${Math.round(r.left)},${Math.round(r.top)}-${Math.round(r.right)},${Math.round(r.bottom)} 枠 ${Math.round(L)},${Math.round(T)}-${Math.round(R)},${Math.round(B)}）`); }"""


def check_3d_labels_inside(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 F7：3D の文字（軸の X−・Y+・点の名前・回数の窓・注意）が、見える 3D の枠の中に切れずに収まる。
    1280×720・1366×768・1440×900・1920×1080 で、DEMO_C_PLACE（読み込んだ直後・候補・再生中）と
    DEMO_BUTTON_REPEAT（読み込んだ直後・再生中・範囲の確認）。回数の変更の候補（data.focus で区間の3点を大きく見せる図。
    区間の外の点は図の外に出てよい・view3d.js の setData）では、枠の端で切れる文字が無いこと。文字を小さくして収めるのではない（大きさは check_text_size_1080 と
    check_ui_contrast で見る）。モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "f7_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        for (w, h) in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
            for job in ("DEMO_C_PLACE", "DEMO_BUTTON_REPEAT"):
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.add_init_script(FAKE_RECOGNITION)
                pg.goto(url)
                pg.set_input_files("#file-input", str(REPO / "samples/dummy" / f"{job}.JBI"))
                pg.wait_for_selector("#job-area:not([hidden])")
                until(pg, lambda: pg.is_enabled("#text-send") or pg.is_visible("#kbd-button"))

                def stage(name, cut_only=False):
                    pg.wait_for_timeout(400)
                    bad = pg.evaluate(TEXT3D_OUTSIDE_JS, cut_only)
                    what = "枠の端で切れる文字が無い（区間を大きく見せる図）" if cut_only else "文字が枠の中に収まる"
                    check(not bad, f"11/5 F7（{w}×{h}・{job}・{name}）：3D の{what}（はみ出し {bad or 'なし'}）")
                    if shots is not None and w in (1280, 1920):
                        shots.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(shots / f"F7_{w}_{job}_{name}.png"))

                def playback(name, cut_only=False):
                    pg.click("#play-button")
                    until(pg, lambda: pg.locator("#fig-3d .marker, #fig-3d .dest-mark").count() > 0)
                    pg.wait_for_timeout(300)
                    stage(name, cut_only)
                    if pg.is_enabled("#play-stop"):
                        pg.click("#play-stop")

                stage("load")
                if job == "DEMO_C_PLACE":
                    pg.locator("#position-chips button", has_text="C00003").click()
                    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
                    fill_text(pg, "Zプラス方向に10mm")
                    pg.click("#text-send")
                    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
                    stage("candidate")
                    playback("candidate_playback")
                else:
                    playback("load_playback")
                    fill_text(pg, "じゃあ、3回にして")
                    pg.click("#text-send")
                    pg.wait_for_selector("#range-yes", timeout=5000)
                    stage("range")
                    pg.click("#range-yes")
                    until(pg, lambda: pg.is_enabled("#open-confirm"))
                    stage("repeat_candidate", True)
                    playback("repeat_playback", True)
                pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


# 3D の点の画面の上の真ん中（名前 → [x, y]）。押しやすい大きさの透明な円の四角の中心
PT3_CENTERS_JS = """() => Object.fromEntries([...document.querySelectorAll('#fig-3d g.pt3')].map((g) => {
  const r = g.querySelector('circle').getBoundingClientRect(); return [g.dataset.names, [r.left + r.width / 2, r.top + r.height / 2]]; }))"""


def check_3d_fit_stable(browser, check, shots: Path | None, out: Path) -> None:
    """11/5 夜 F7 の続き：3D を枠に収める割合とずれは、1つの JOB を読んでいる間は変わらない。
    DEMO_C_PLACE：位置を選んだ → 候補（C00003・「Zプラス方向に10mm」）→ 再生 →［元に戻す］で、点の画面の位置が同じ（1px 以内）。
    DEMO_BUTTON_REPEAT：読み込んだ直後と範囲の確認で同じ。4つの大きさで。候補が出たときに図が跳ぶと、撮影で選んだ点が逃げて見える。
    モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "f7s_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def moved(a, b):
        return {n: (round(a[n][0] - b[n][0]), round(a[n][1] - b[n][1])) for n in a
                if n not in b or abs(a[n][0] - b[n][0]) > 1 or abs(a[n][1] - b[n][1]) > 1} if set(a) == set(b) else {"点の組が違う": (sorted(a), sorted(b))}
    try:
        for (w, h) in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
            for job in ("DEMO_C_PLACE", "DEMO_BUTTON_REPEAT"):
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.add_init_script(FAKE_RECOGNITION)
                pg.goto(url)
                pg.set_input_files("#file-input", str(REPO / "samples/dummy" / f"{job}.JBI"))
                pg.wait_for_selector("#job-area:not([hidden])")
                until(pg, lambda: pg.is_enabled("#text-send") or pg.is_visible("#kbd-button"))
                pg.wait_for_timeout(300)

                def same(ref, name):
                    pg.wait_for_timeout(400)
                    got = pg.evaluate(PT3_CENTERS_JS)
                    diff = moved(got, ref)
                    check(not diff, f"11/5 F7（{w}×{h}・{job}・{name}）：3D の点が動かない（1px より動いた点 {diff or 'なし'}）")

                if job == "DEMO_C_PLACE":
                    pg.locator("#position-chips button", has_text="C00003").click()
                    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
                    pg.wait_for_timeout(400)
                    ref = pg.evaluate(PT3_CENTERS_JS)
                    fill_text(pg, "Zプラス方向に10mm")
                    pg.click("#text-send")
                    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
                    same(ref, "位置を選んだ → 候補")
                    pg.click("#play-button")
                    until(pg, lambda: pg.locator("#fig-3d .marker, #fig-3d .dest-mark").count() > 0)
                    same(ref, "位置を選んだ → 候補の再生中")
                    if pg.is_enabled("#play-stop"):
                        pg.click("#play-stop")
                    pg.click("#undo-button")
                    until(pg, lambda: pg.locator("#fig-3d .after-pt").count() == 0)
                    same(ref, "位置を選んだ → 元に戻す")
                else:
                    ref = pg.evaluate(PT3_CENTERS_JS)
                    fill_text(pg, "じゃあ、3回にして")
                    pg.click("#text-send")
                    pg.wait_for_selector("#range-yes", timeout=5000)
                    same(ref, "読み込んだ直後 → 範囲の確認")
                pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


def check_review_only(browser, url, check, shots: Path | None) -> None:
    """実機の JOB（合成ジョブでない）：確認表示と承認のあとに「変更案の確認のみ」の帯。出力ボタンを出さず、出力を促さない。
    合成ジョブでは帯を出さず、今どおり出力ボタンが出る（唐川さんの指示・内部 QA の再レビュー P1-1・P2-6）。"""
    data = (REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()
    with tempfile.TemporaryDirectory() as d:
        cases = [("DEMO_C_PLACE.JBI", REPO / "samples/dummy/DEMO_C_PLACE.JBI", True)]
        other = Path(d) / "DEMO_C_PLACX.JBI"  # 名前の行を1文字変えた写し＝合成ジョブではない（実機の JOB の代わり）
        other.write_bytes(data.replace(b"//NAME DEMO_C_PLACE", b"//NAME DEMO_C_PLACX", 1))
        cases.append(("DEMO_C_PLACX.JBI", other, False))
        for name, path, synthetic in cases:
            for width in (1280, 390):
                pg = browser.new_page(viewport={"width": width, "height": 900})
                pg.goto(url)
                pg.set_input_files("#file-input", str(path))
                until(pg, lambda: pg.inner_text("#file-name") == name)
                pg.locator("#position-chips button", has_text="C00005").click()
                until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
                fill_text(pg, "Zマイナス3ミリ")
                pg.click("#text-send")
                until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
                pg.click("#open-confirm")
                pg.wait_for_selector("#confirm-overlay:not([hidden])")
                band = pg.is_visible("#confirm-review-only")
                check(band == (not synthetic) and (synthetic or pg.inner_text("#confirm-review-only") == REVIEW_ONLY),
                      f"{name}（{width}px）：確認表示の「確認のみ」の帯は、合成ジョブでないときだけ出る（{band}）")
                check(pg.is_hidden("#approval-states"), f"3つの状態 {name}（{width}px）：承認の前は出さない")
                pg.fill("#approver-name", "e2e")
                pg.click("#approve-button")
                until(pg, lambda: pg.is_visible("#approval-status") or pg.is_hidden("#confirm-overlay"))
                if pg.is_visible("#confirm-close"):
                    pg.click("#confirm-close")
                pg.wait_for_timeout(300)
                head = pg.inner_text("#next-action")
                exp_btn = pg.is_visible("#export-button")
                band2 = pg.is_visible("#export-review-only")
                hint = pg.inner_text("#next-hint") if pg.is_visible("#next-hint") else ""
                rows = pg.evaluate(AFTER_ROWS_JS) if pg.is_visible("#approval-states") else []
                o3 = pg.evaluate(OVERFLOW_JS, 8)
                check(rows == AFTER_ROWS and (synthetic or pg.is_visible("#export-review-only"))
                      and o3["scroll"] <= o3["vw"] and not o3["out"],
                      f"3つの状態 {name}（{width}px）：承認のあとに3行を出す（{rows}）。実機の JOB は「確認のみ」の帯と一緒（{o3['out']}）")
                if synthetic and width == 1280:
                    pg.click("#export-button")
                    until(pg, lambda: pg.is_visible("#export-result"))
                    line = pg.inner_text("#approval-exported") if pg.is_visible("#approval-exported") else ""
                    check(line.startswith("シミュレーション用に出力しました（") and line.endswith("。実機には入れないでください）")
                          and ".JBI.txt" in line, f"3つの状態：出力のあとに出力した行を足す（{line!r}）")
                if synthetic:
                    check(hint == "", f"S30-3 {name}（{width}px）：合成ジョブの承認のあとは、元に戻す案内を出さない（{hint!r}）")
                else:
                    o = pg.evaluate(OVERFLOW_JS, 8)
                    check(head == "承認しました（変更案の確認のみ。実機用JOBは出力できません）" and hint == UNDO_HINT
                          and pg.is_visible("#undo-button") and "primary" not in (pg.get_attribute("#undo-button", "class") or "")
                          and o["scroll"] <= o["vw"] and not o["out"],
                          f"S30-3 {name}（{width}px）：承認のあとに「{UNDO_HINT}」を1行出す（{hint!r}・{o['out']}）。元に戻すは目立たせない")
                if synthetic:
                    check(exp_btn and not band2 and "出力してください" in head,
                          f"{name}（{width}px）：承認のあと、出力ボタンが出て出力を案内する（{head!r}）")
                else:
                    check(not exp_btn and band2 and "出力してください" not in head and "確認のみ" in head
                          and "primary" not in (pg.get_attribute("#export-button", "class") or ""),
                          f"{name}（{width}px）：承認のあと、出力ボタンを出さず、出力を促さない（見出し {head!r}）")
                    if shots is not None:
                        shots.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(shots / f"review_only_{width}.png"))
                    # 元に戻すと、案内は消える（ステップ3に戻る）
                    pg.click("#undo-button")
                    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") != "step")
                    check(pg.is_hidden("#next-hint"), f"S30-3 {name}（{width}px）：元に戻したあとは案内を出さない")
                    check(pg.is_hidden("#approval-states"), f"3つの状態 {name}（{width}px）：元に戻して承認が消えたら出さない")
                pg.close()


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
        fill_text(page, text)
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
            # Issue #8（11/6 唐川さん）：解釈の印（解釈：モック）は上の帯に出さない。SIM の印は狭い画面でも見える。
            # 解釈のしかた（モックか本物の Claude か）はアプリ情報（ⓘ）にサーバーの値のまま出る
            check(pg.is_hidden("#interp-badge") and pg.is_visible("#mode-banner")
                  and (pg.text_content("#app-info-interp") or "").startswith("解釈："),
                  f"M1 {tag}：SIM の印は狭い画面でも見えている。解釈の印は上の帯に出さず、アプリ情報に出す（{pg.text_content('#app-info-interp')}）")
            rows = pg.evaluate("""() => ['.top h1', '#mode-banner'].map(
                (q) => Math.round(document.querySelector(q).getBoundingClientRect().top))""")
            check(rows[1] > rows[0] + 10,
                  f"M1 {tag}：1行目はアプリ名、2行目に SIM の印（上の端 {rows}）")
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
    check("MOVL" in lg and "MOVJ" in lg and pg.is_visible("#legend-movj") and "実際の通り道ではありません" not in lg,
          f"S-MOVJ：凡例は短いラベル「MOVL」「MOVJ」と ? だけ（11/6：説明文はいつも出さない）（{lg!r}）")
    pg.hover('#legend-movj button.tip')
    pg.wait_for_timeout(200)
    tip = pg.inner_text("#tip-pop") if pg.is_visible("#tip-pop") else ""
    check("関節を使って移動" in tip and "実際の通り道ではありません" in tip,
          f"S-MOVJ：MOVJ の ? に合わせると「関節を使って移動…点線は順番だけで、実際の通り道ではありません」が出る（{tip!r}）")
    pg.mouse.move(5, 5)
    pg.keyboard.press("Escape")
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
    # CHECKER_030 S30-1：確認済みのサーバー：図の欄の上に、ロボット名と確認した人・日時を、マウスを重ねなくても出す
    det = pg.inner_text("#updown-detail") if pg.is_visible("#updown-detail") else ""
    check(det.startswith("上下の向き：HC20DT・e2e（一時フォルダの仮の設定）（") and det.endswith("）で確認済み"),
          f"S30-1：確認済みは「上下の向き：HC20DT・確認した人（日時）で確認済み」を常に出す（{det!r}）")
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
    check(head.startswith("参考の表示だけ・変更しません：C00000 を Z +10mm") and head.endswith("（承認できません）") and not pg.is_enabled("#approve-button"),
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
    # CHECKER_030 S30-1：ロボット名と確認した人・日時の1行（図の欄の上。マウスを重ねなくても見える）
    details = {
        "unconfirmed": "上下の向き：未確認（HC20DT）",
        "confirmed": "上下の向き：HC20DT・唐川（11/3 14:00）で確認済み",
        "mismatch": "上下の向き：確認した設定と違います（HC20DT）",
        "changed": "上下の向き：設定が変わっています（HC20DT）",
    }
    for value, _ in cases:
        value["app_robot"] = "HC20DT"
        value["robot"] = "HC20DT" if value["status"] == "confirmed" else None
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
        # Issue #7：左の「上下の向き」の行は出さない（文は作る）。同じ状態は図の欄の上の1行（#updown-detail）にいつも見える（下の check）
        got = pg.text_content("#updown-status") or ""
        check(got == want and pg.is_hidden("#updown-status"), f"上下の向き：{value['status']} は「{want}」（左の行は出さない・Issue #7）（{got!r}）")
        det = pg.inner_text("#updown-detail") if pg.is_visible("#updown-detail") else ""
        # 音声のカード（.side）の外に置く：右側の 80文字の決まり（下の check_updown_unconfirmed・4ステップで数える）を変えない
        inside = pg.evaluate("() => !!document.querySelector('.side #updown-detail')")
        check(det == details[value["status"]] and not inside,
              f"S30-1：{value['status']} は図の欄の上に「{details[value['status']]}」（{det!r}）。音声のカードの外に置く")
        if value["status"] == "unconfirmed":
            fs = pg.evaluate("() => parseFloat(getComputedStyle(document.querySelector('#updown-status')).fontSize)")
            check(fs <= 13, f"上下の向き：目立たせすぎない（文字 {fs}px）")
            shot(pg, "C026_1280_e_updown.png")
        pg.close()
    # 長い名前（設定の上限 40文字ずつ）でも、狭い画面ではみ出さず、音声のカードの文字数は変わらない
    updown["value"] = {"status": "confirmed", "confirmed_by": "確" * 40, "confirmed_at": "2026-11-03T14:00:00+09:00",
                       "message": "", "fingerprint": "x", "robot": "R" * 40, "app_robot": "R" * 40}
    for width in (1280, 390, 360):
        pg = browser.new_page(viewport={"width": width, "height": 900})
        pg.route("**/api/*", add_updown)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips button", has_text="C00005").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        det = pg.inner_text("#updown-detail") if pg.is_visible("#updown-detail") else ""
        o = pg.evaluate(OVERFLOW_JS, 8)
        check("R" * 40 in "".join(det.split()) and "確" * 40 in "".join(det.split()) and o["scroll"] <= o["vw"] and not o["out"],
              f"S30-1 {width}px：長い名前でも全部出し、はみ出さない（{o['out']}）")
        pg.close()


REPEAT_SAY = "C00001からC00003の作業を2回から3回にして"


def svg_text_outside(page, host):
    """図（host の svg）の中の回数の印の文字のうち、svg の枠からはみ出しているもの。"""
    return page.evaluate("""(host) => {
      const svg = document.querySelector(host + ' svg');
      if (!svg) return ['svg なし'];
      const b = svg.getBoundingClientRect();
      return [...svg.querySelectorAll('text.repeat-label, text.repeat-now, text.repeat-was')]
        .filter((t) => { const r = t.getBoundingClientRect();
          return r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.top < b.top - 0.5 || r.bottom > b.bottom + 0.5; })
        .map((t) => t.textContent); }""", host)


def check_repeat(browser, url, check, shots: Path | None, out: Path, errors: list) -> None:
    """デモ用ジョブ（DEMO_BUTTON_REPEAT）を読む（3D に ×2）→ 指示 → 範囲の確認（3D で範囲の点と線が光る）
    →［この範囲を繰り返す］→ 候補（×2 → ×3）→ 3D の再生で「3回目」→ 確認表示（見出し・差分・対象外の文）→ 承認ボタン → 出力。
    1280px・390px で、回数の印の文字が図からはみ出さないこと。声の「はい」で確定しないことは tests/test_repeat_count.py で確かめる。"""
    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name))

    def watch(page, sel):
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror（{sel}）: {e}"))

    def to_range(page, threed=True):
        page.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        page.wait_for_selector("#job-area:not([hidden])")
        if threed:
            page.click('.view-tab[data-view="3d"]')
        page.locator("#position-chips button", has_text="C00001").first.click()
        until(page, lambda: page.inner_text("#voice-position strong") == "C00001")
        fill_text(page, REPEAT_SAY)
        page.click("#text-send")
        page.wait_for_selector("#range-yes")

    pg = browser.new_page(viewport={"width": 1400, "height": 1000})
    watch(pg, "回数の変更")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.click('.view-tab[data-view="3d"]')
    check("×2" in (pg.text_content("#fig-3d text.repeat-now") or "")
          and "C00001〜C00003 ×2" in (pg.text_content("#fig-3d text.repeat-label") or ""),
          "回数の変更：読み込んだ直後から、3D に「C00001〜C00003 ×2」")
    shot(pg, "R1_loaded_3d.png")
    pg.locator("#position-chips button", has_text="C00001").first.click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00001")
    fill_text(pg, REPEAT_SAY)
    pg.click("#text-send")
    pg.wait_for_selector("#range-yes")
    reply = pg.inner_text("#reply-message")
    check(all(x in reply for x in ("1回目：21〜23行目", "MOVL C00002 V=30.0",
                                    "2回目：24〜26行目", "全体：21〜26行目（2回）", "今は2回続けて書かれています")),
          f"回数の変更：範囲の確認に1回目・2回目・全体の範囲と命令の行：{reply[:160]}")
    shot(pg, "R2_range_confirm.png")
    check("繰り返すのは移動の行だけです" in reply, "回数の変更：範囲の確認に、対象外の固定文")
    check(pg.inner_text("#range-yes") == "この範囲を繰り返す" and pg.inner_text("#range-no") == "違う",
          "回数の変更：［この範囲を繰り返す］［違う］のボタン")
    cls = pg.get_attribute("#range-yes", "class") or ""
    bg = pg.evaluate("() => [getComputedStyle(document.querySelector('#range-yes')).backgroundColor,"
                     " getComputedStyle(document.querySelector('#range-no')).borderTopStyle]")
    check("range-go" in cls and "primary" not in cls and bg[0] == "rgb(10, 132, 255)" and bg[1] == "solid"
          and "承認は次の確認表示で行います" in pg.inner_text("#dock-range"),  # 注記はボタンと一緒に下の操作の欄（唐川さん承認のデザイン 11/4）
          f"回数の変更：［この範囲を繰り返す］は青（承認のオレンジとは別）、［違う］は枠のある控えめなボタン、承認ではない注記：{bg}")
    check(pg.inner_text("#next-action") == "この範囲を繰り返しますか？"
          and "もう少し教えてください" not in pg.inner_text("#reply-message") and pg.is_hidden("#max-shift-label"),
          "回数の変更：［この範囲を繰り返す］が目立つボタン、見出しは1回だけ、「1回に動かせる量」は隠す")
    # 声・文字で「はい」：確定せず、範囲の確認を残して、ボタンで決めるよう案内する（QA P1-A）
    fill_text(pg, "はい")
    pg.click("#text-send")
    until(pg, lambda: "ボタンで決めてください" in pg.inner_text("#reply-message"))
    check(pg.is_visible("#range-yes") and pg.get_attribute("#step-4", "aria-current") != "step",
          "回数の変更：「はい」では確定せず、範囲の確認とボタンは残る")
    check(pg.get_attribute("#step-4", "aria-current") != "step" and pg.is_hidden("#open-confirm"),
          "回数の変更：範囲をボタンで確定するまで、候補にしない")
    check(pg.locator("#fig-3d g.pt3.cand").count() >= 1 and pg.locator("#fig-3d line.range").count() == 5,
          f"回数の変更：3D で範囲の点と線を光らせる（線 {pg.locator('#fig-3d line.range').count()}本）")
    shot(pg, "R2_range_confirm.png")
    pg.click("#range-yes")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    card = pg.inner_text("#candidate-view")
    check("C00001〜C00003" in card and "×2 → ×3" in card, f"回数の変更：候補のカードに区間と ×2 → ×3：{card[:60]}")
    check("新しい移動の区間はありません" in card and "繰り返すのは移動の行だけです" in card,
          "回数の変更：候補のカードに「新しい移動の区間はありません」と対象外の固定文")
    detail = pg.text_content("#candidate-detail") or ""
    check(detail.count("+ ") == 3 and "MOVL C00002 V=30.0" in detail, f"回数の変更：詳細に足す3行の差分：{detail[:120]}")
    check("C00001〜C00003 ×2 → ×3" in (pg.text_content("#fig-3d text.repeat-label") or ""),
          f"回数の変更：3D の区間の横に「C00001〜C00003 ×2 → ×3」：{pg.text_content('#fig-3d text.repeat-label')}")
    check("×3" in (pg.text_content("#fig-3d text.repeat-now") or "")
          and "×2" in (pg.text_content("#fig-3d text.repeat-was") or ""), "回数の変更：右上の窓は ×3 を大きく、×2 を灰色で")
    check(svg_text_outside(pg, "#fig-3d") == [], f"回数の変更：1280px 相当で文字が図からはみ出さない：{svg_text_outside(pg, '#fig-3d')}")
    seen, shot_done, waited = set(), False, 0
    while waited < 30000:
        t = pg.inner_text("#play-status")
        if t:
            seen.add(t)
        if "変更後" in t and "3回目" in t and not shot_done:
            shot(pg, "R3_playing_3rd.png")
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
    pg.wait_for_timeout(200)
    title = pg.inner_text("#confirm-title")
    check(title == "C00001〜C00003 の作業を 2回 → 3回 にします", f"回数の変更：確認表示の見出し：{title}")
    diff = pg.inner_text("#confirm-diff")
    check(diff.count("+ ") == 3 and "MOVL C00001 V=150.0" in diff, f"回数の変更：確認表示の差分は足す3行：{diff}")
    warn = pg.inner_text("#confirm-warnings")
    check(all(w in warn for w in ("同じ動きを1回ぶん足します", "サイクルの時間が長くなります",
                                  "物理的な安全の保証ではありません", "新しい移動の区間はありません",
                                  "繰り返すのは移動の行だけです")),
          "回数の変更：確認表示に固定の注意をすべて出す（対象外の文を含む）")
    check(not any(w in pg.inner_text("#confirm-overlay") for w in ("安全です", "当たります", "干渉します")),
          "回数の変更：確認表示に安全・距離を断定する言葉が無い")
    check(pg.locator("#confirm-figure text.repeat-label").count() == 1, "回数の変更：確認表示の 3D にも ×2 → ×3")
    check(svg_text_outside(pg, "#confirm-figure") == [],
          f"回数の変更：確認表示の 3D で文字が図からはみ出さない：{svg_text_outside(pg, '#confirm-figure')}")
    check(pg.is_enabled("#approve-button"), "回数の変更：承認ボタンが使える")
    shot(pg, "R4_confirm.png")
    pg.fill("#approver-name", "e2e確認")
    pg.click("#approve-button")
    until(pg, lambda: pg.is_enabled("#export-button"))
    shot(pg, "R5_approved.png")
    pg.click("#export-button")
    pg.wait_for_selector("#export-result:not([hidden])")
    f = out / "sim" / "DEMO_BUTTON_REPEAT_SIM.JBI.txt"
    check(f.is_file() and "出力しました" in pg.inner_text("#export-result"), f"回数の変更：出力 {f.name}")
    if f.is_file():
        a = (REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI").read_bytes().splitlines()
        b = f.read_bytes().splitlines()
        check(len(b) == len(a) + 3 and b.count(b"MOVL C00002 V=30.0") == 3 and b[1] == b"//NAME DEMO_BUTTON_REPEAT_SIM",
              f"回数の変更：出力は3行増えてジョブ名だけ変わる（{len(a)} → {len(b)}行）")
    shot(pg, "R6_exported.png")
    pg.close()

    # ［違う］は何もしない
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    watch(pg, "回数の変更・違う")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    to_range(pg)
    pg.click("#range-no")
    pg.wait_for_timeout(300)
    check(pg.get_attribute("#step-4", "aria-current") != "step" and pg.locator("#range-yes").count() == 0
          and "変更はしません" in pg.inner_text("#reply-message"), "回数の変更：［違う］は何もしない（候補を作らない）")
    pg.close()

    # 390px：範囲の確認 → 候補 → 確認表示。文字が図からはみ出さない
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    watch(pg, "回数の変更・390px")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    to_range(pg)
    pg.wait_for_timeout(200)
    vis = pg.evaluate("""() => { const b = document.querySelector('#range-yes').getBoundingClientRect();
      return b.top >= 0 && b.bottom <= window.innerHeight; }""")
    check(vis, "回数の変更 390px：範囲の確認のボタンが、最初の画面（スクロールしたあと）に見えている")
    shot(pg, "R7_390_range.png")
    pg.click("#range-yes")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.wait_for_timeout(200)
    check(svg_text_outside(pg, "#confirm-figure") == [],
          f"回数の変更 390px：確認表示の 3D で文字が図からはみ出さない：{svg_text_outside(pg, '#confirm-figure')}")
    r = pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check(r[0] <= r[1], f"回数の変更 390px：横にはみ出さない（{r}）")
    shot(pg, "R8_390_confirm.png")
    ctx.close()


def check_demo3d(browser, url, check, shots: Path | None, errors: list) -> None:
    """再生の表示（feat/demo-3d）：図の上の「いま：24行目 MOVL C00001 V=150.0（2回目）」、MOVJ の区間の文、
    一時停止・続ける・1つ進む・止める、回数の変更の候補があるときだけの［変更前を再生］［変更後を再生］
    （変更前は2回目まで・変更後は3回目＝足した分まで光らせる）。候補が無い・位置の移動の候補では比較を出さない。
    1280px・390px で、はみ出さない・安全の表示（承認ボタン・注意）を隠さない。"""
    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name))

    def watch(page, sel):
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror（{sel}）: {e}"))

    def now(page):
        return page.inner_text("#play-now") if page.is_visible("#play-now") else ""

    def collect(page, until_end=True, timeout=40000, on_text=None):
        """再生が終わるまで、図の上の「いま」の文と、そのときの図の様子を集める。"""
        seen = []
        waited = 0
        while waited < timeout:
            info = page.evaluate("""() => {
              const n = document.querySelector('#play-now');
              const svg = document.querySelector('#fig-3d svg');
              return { text: n && !n.hidden ? n.innerText : '',
                       added: !!document.querySelector('#play-now .now-cycle.added'),
                       marker: svg ? svg.querySelectorAll('circle.marker').length : 0,
                       dest: document.querySelectorAll('.dest-mark').length,
                       glow: svg ? svg.querySelectorAll('line.seg-glow').length : 0,
                       after: svg ? svg.querySelectorAll('line.seg.after').length : 0,
                       status: document.querySelector('#play-status').textContent,
                       tally: document.querySelector('#seq-tally').textContent,
                       side: document.querySelector('#play-seq').hidden ? '' : document.querySelector('#seq-side').textContent,
                       nowChip: [...document.querySelectorAll('#seq-chips .seq-chip.now')].map((e) => e.textContent).join(' ') }; }""")
            if info["text"] and (not seen or seen[-1]["text"] != info["text"] or seen[-1]["marker"] != info["marker"]
                                 or seen[-1]["tally"] != info["tally"]):
                seen.append(info)
                if on_text:
                    on_text(info)
            if until_end and info["status"] == "再生が終わりました":
                break
            page.wait_for_timeout(30)
            waited += 30
        return seen

    def load_repeat(page, threed=True):
        page.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        page.wait_for_selector("#job-area:not([hidden])")
        if threed:
            page.click('.view-tab[data-view="3d"]')

    def to_repeat_candidate(page):
        page.locator("#position-chips button", has_text="C00001").first.click()
        until(page, lambda: page.inner_text("#voice-position strong") == "C00001")
        fill_text(page, REPEAT_SAY)
        page.click("#text-send")
        page.wait_for_selector("#range-yes")
        page.click("#range-yes")
        until(page, lambda: page.get_attribute("#step-4", "aria-current") == "step")
        page.wait_for_timeout(200)

    def boxes_overlap(page, a, b):
        return page.evaluate("""([a, b]) => {
          const x = document.querySelector(a), y = document.querySelector(b);
          if (!x || !y) return false;
          const r = x.getBoundingClientRect(), s = y.getBoundingClientRect();
          if (!r.width || !s.width) return false;
          return r.left < s.right && r.right > s.left && r.top < s.bottom && r.bottom > s.top; }""", [a, b])

    banned = ("安全です", "当たります", "干渉します", "当たりません", "秒", "サイクルの時間")

    # ---- 1280×720：候補が無いときの再生（命令の順）。行番号・命令の行・何回目・MOVJ の文 ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    watch(pg, "再生の表示")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    load_repeat(pg)
    check(pg.is_hidden("#play-compare") and pg.is_hidden("#play-now") and pg.is_hidden("#play-seq"),
          "再生の表示：候補が無いときは［変更前を再生］［変更後を再生］を出さない（「いま」の表示も再生の前は出さない）")
    check(pg.is_enabled("#play-step") and not pg.is_enabled("#play-pause") and not pg.is_enabled("#play-stop"),
          "再生の表示：再生の前は「1つ進む」だけ使える（一時停止・止めるは押せない）")
    shot_done = []

    def shot_2nd(info):
        if "2回目" in info["text"] and info["marker"] and not shot_done:
            shot(pg, "D1_1280_playing_2nd.png")
            shot_done.append(1)
    pg.click("#play-button")
    seen = collect(pg, on_text=shot_2nd)
    texts = [s["text"] for s in seen]
    joined = " / ".join(dict.fromkeys(t.replace("\n", " ") for t in texts))
    check(any("いま：21行目 MOVL C00001 V=150.0" in t and "1回目" in t for t in texts)
          and any("いま：24行目 MOVL C00001 V=150.0" in t and "2回目" in t for t in texts),
          f"再生の表示：図の上に行番号・命令の行・何回目を出す：{joined[:220]}")
    check(not any("3回目" in t for t in texts), "再生の表示：今のジョブの再生には3回目が無い")
    movj = [s for s in seen if "MOVJ C00004" in s["text"]]
    check(bool(movj) and all("関節移動：通り道は表示していません" in s["text"] for s in movj)
          and all(s["marker"] == 0 for s in movj),
          f"再生の表示：MOVJ の区間では「関節移動：通り道は表示していません」と出し、印を直線の上に出さない（{len(movj)}回見た）")
    check(all(s["dest"] == 1 for s in seen), "再生の表示：再生中は、行き先の点の上に小さな印を1つ出す")
    check(not any(w in t for t in texts for w in banned), "再生の表示：時間・安全や距離を断定する言葉を出さない")
    check(pg.is_hidden("#play-now") and pg.locator(".dest-mark").count() == 0, "再生の表示：終わると「いま」と点の印を消す")

    # 一時停止・続ける・止める
    pg.click("#play-button")
    until(pg, lambda: "22行目" in now(pg), timeout_ms=10000)
    pg.click("#play-pause")
    a = now(pg)
    pg.wait_for_timeout(900)
    check(pg.get_attribute("#play-pause", "aria-label") == "続ける" and now(pg) == a and a != "",
          f"再生の表示：一時停止すると、その場で止まる（{a.splitlines()[0] if a else ''}）")
    pg.click("#play-pause")
    until(pg, lambda: now(pg) != a, timeout_ms=10000)
    check(pg.get_attribute("#play-pause", "aria-label") == "一時停止", "再生の表示：「続ける」で続きから動く")
    pg.click("#play-stop")
    check(pg.is_hidden("#play-now") and pg.locator("#fig-3d circle.marker").count() == 0
          and pg.inner_text("#play-status") == "再生を止めました", "再生の表示：「止める」で印と「いま」を消す")

    # 1つ進む：止まっているところから、1回押すごとに1区間
    pg.click("#play-step")
    t1 = now(pg)
    pg.click("#play-step")
    t2 = now(pg)
    pg.wait_for_timeout(900)
    check("いま：21行目" in t1 and "いま：22行目" in t2 and now(pg) == t2
          and pg.locator("#fig-3d circle.marker").count() == 1,
          f"再生の表示：「1つ進む」は1回押すごとに1区間ずつ（{t1.splitlines()[-1] if t1 else ''} → {t2.splitlines()[-1] if t2 else ''}）")
    shot(pg, "D2_1280_step.png")
    pg.click("#play-stop")
    pg.close()

    # ---- 位置の移動の候補（DEMO_C_PLACE）：比較の再生は出さない ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    watch(pg, "再生の表示・位置の移動")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.locator("#position-chips button", has_text="C00005").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
    fill_text(pg, "Zプラス方向に10mm")
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    pg.wait_for_timeout(300)
    check(pg.is_hidden("#play-compare") and pg.is_hidden("#play-seq"),
          "再生の表示：位置の移動の候補では、回数の比較の再生と動きの並びを出さない")
    until(pg, lambda: "いま：" in now(pg), timeout_ms=5000)
    check("×" not in now(pg) and "回目" not in now(pg), f"再生の表示：位置の移動の候補の再生では、回数を出さない：{now(pg)[:60]}")
    pg.close()

    # ---- 1280×720：回数の変更の候補。変更前は2回目まで、変更後は3回目（足した分）まで ----
    pg = browser.new_page(viewport={"width": 1280, "height": 720})
    watch(pg, "再生の表示・比較")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    load_repeat(pg)
    to_repeat_candidate(pg)
    check(pg.is_visible("#play-compare") and pg.inner_text("#play-before") == "変更前を再生"
          and pg.inner_text("#play-after") == "変更後を再生", "比較の再生：回数の変更の候補では［変更前を再生］［変更後を再生］を出す")
    pg.click("#play-stop")  # 候補ができたときの再生を止める（止まっているときの並びは変更後）
    chips = pg.eval_on_selector_all("#seq-chips .seq-chip", "els => els.map(e => [e.textContent, e.className])")
    texts_c = [c[0] for c in chips]
    check(texts_c == ["C00000（区間の前）", "→", "C00001〜C00003", "①", "②", "③ 追加", "→", "C00004（区間の後）"]
          and "added" in chips[5][1] and not any("added" in c[1] for c in chips[:5])
          and pg.inner_text("#seq-side") == "変更後 ×3",
          f"動きの並び：［C00000（区間の前）］→ 区間 ①②③（③は追加・変更後の色）→［C00004（区間の後）］：{texts_c}")
    check(not any("待機" in t for t in texts_c), "動きの並び：位置の意味（待機など）を決めつけない")
    tops = pg.eval_on_selector_all("#seq-chips .seq-chip", "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    check(max(tops) - min(tops) < 12, f"動きの並び：1280px で1行に並ぶ（行の上の端 {sorted(set(tops))}）")
    check(pg.inner_text("#seq-summary") == "足した行：3行（直線移動だけ）。新しい位置はありません",
          f"動きの並び：差分の要約（数はプログラム）：{pg.inner_text('#seq-summary')}")
    pg.click("#play-before")
    before = collect(pg)
    tb = [s["tally"] for s in before]
    check(before[-1]["tally"] == "1・2" and not any("3" in t for t in tb) and all(s["side"] == "変更前 ×2" for s in before)
          and any(s["nowChip"] == "②" for s in before),
          f"比較の再生：変更前は区間を通るたびに「1・2」と数え、並びは ①② だけ：{list(dict.fromkeys(tb))}")
    bt = [s["text"] for s in before]
    check(bool(bt) and all(t.startswith("変更前") and "×2" in t for t in bt)
          and any("2回目" in t for t in bt) and not any("3回目" in t for t in bt) and not any("変更後" in t for t in bt),
          f"比較の再生：［変更前を再生］は変更前だけ・×2・2回目まで：{' / '.join(dict.fromkeys(t.replace(chr(10), ' ') for t in bt))[:200]}")
    check(all(s["after"] == 0 for s in before), "比較の再生：変更前の再生の間は、足した回の線を重ねない")
    shot_done = []

    def shot_3rd(info):
        if info["added"] and info["marker"] and not shot_done:
            shot(pg, "D3_1280_compare_after_3rd.png")
            shot_done.append(info)
    def shot_3rd_seq(info):
        shot_3rd(info)
        if info["added"] and info["marker"] and info["nowChip"] == "③ 追加" and not shot_seq:
            shot(pg, "D6_1280x720_seq_after_3rd.png")
            fit = pg.evaluate("""() => { const f = document.querySelector('.foot').getBoundingClientRect().top;
              const b = (s) => document.querySelector(s).getBoundingClientRect();
              return [f, b('#fig-3d svg').top, b('#play-seq').bottom, b('#play-compare').bottom, b('#figure-short').bottom,
                      window.scrollY]; }""")
            shot_seq.append(fit)
    shot_seq = []
    pg.evaluate("window.scrollTo(0, 0)")
    pg.click("#play-after")
    after = collect(pg, on_text=shot_3rd_seq)
    ta = [s["tally"] for s in after]
    check(after[-1]["tally"] == "1・2・3" and pg.inner_text("#seq-tally .tally-n.added") == "3"
          and all(s["side"] == "変更後 ×3" for s in after),
          f"比較の再生：変更後は「1・2・3」と数え、3 だけ変更後の色：{list(dict.fromkeys(ta))}")
    fit = shot_seq[0] if shot_seq else None
    check(fit is not None and fit[5] == 0 and fit[1] >= 0 and max(fit[2], fit[3], fit[4]) <= fit[0],
          f"1280×720：スクロールせずに、3D・動きの並び・比較のボタン・図の注意が1画面に収まる（下の帯の上 {fit and round(fit[0])}px・"
          f"並び {fit and round(fit[2])}px・比較 {fit and round(fit[3])}px・注意 {fit and round(fit[4])}px）")
    at = [s["text"] for s in after]
    third = [s for s in after if "3回目" in s["text"]]
    check(bool(at) and all(t.startswith("変更後") and "×3" in t for t in at) and not any("変更前" in t for t in at),
          "比較の再生：［変更後を再生］は変更後だけ・×3")
    check(bool(third) and all("3回目（足した分）" in s["text"] and s["added"] for s in third)
          and any("いま：27行目 MOVL C00001 V=150.0" in s["text"] for s in third)
          and not any("（足した分）" in s["text"] for s in after if "3回目" not in s["text"]),
          f"比較の再生：変更後の3回目だけを「3回目（足した分）」と出す（{len(third)}回見た）")
    check(any(s["glow"] == 1 for s in third if s["marker"]) and all(s["glow"] == 0 for s in after if "3回目" not in s["text"]),
          "比較の再生：3回目の区間の線を変更後の色で光らせる（ほかの回では光らせない）")
    check(any("MOVJ C00004" in t and "30行目" in t and "関節移動：通り道は表示していません" in t for t in at),
          "比較の再生：変更後の MOVJ は 30行目（3行足したあとの行番号。サーバーの線のまま）")
    check(not any(w in t for t in bt + at for w in banned), "比較の再生：時間・安全や距離を断定する言葉を出さない")
    tabs = pg.locator(".view-tab").first.bounding_box()
    # UI 修正 11/4：ボタンの高さは 44px 以上（依頼書2章）。1行のままかどうかは、2行に折り返した高さ（約 64px）より低いことで見る
    check(tabs is not None and tabs["height"] < 52, f"比較の再生：「いま」の表示が出ても、図の向きのボタンは1行のまま（高さ {tabs and round(tabs['height'])}px）")
    check(not boxes_overlap(pg, "#play-now", "#fig-3d svg") and not boxes_overlap(pg, "#play-now", ".view-tabs"),
          "比較の再生：「いま」の表示は図の上の帯に出し、図（点・回数の窓）と図の向きのボタンに重ねない")
    # 再生しながらでも、安全の表示（候補の注意・確認して承認へ）は隠さない
    pg.click("#play-after")
    until(pg, lambda: "変更後" in now(pg), timeout_ms=5000)
    check(not boxes_overlap(pg, "#play-now", "#candidate-panel") and not boxes_overlap(pg, ".play-row", "#candidate-panel")
          and pg.is_visible("#open-confirm"), "比較の再生：再生の表示・ボタンは、候補のカード（注意・確認して承認へ）に重ならない")
    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.wait_for_timeout(200)
    ab = pg.locator("#approve-button").bounding_box()
    check(pg.is_visible("#approve-button") and pg.is_visible("#confirm-warnings")
          and ab is not None and ab["y"] + ab["height"] <= 720,
          "比較の再生：確認表示の承認ボタン・注意は、そのまま見える（再生の表示は確認表示に出ない）")
    check(not pg.locator("#confirm-overlay #play-now").count(), "比較の再生：確認表示の中に再生の表示を入れない")
    pg.close()

    # ---- 2D（Y・Z）でも、変更後の3回目を足した分として出す ----
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(pg, "再生の表示・比較 2D")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    load_repeat(pg, threed=False)
    pg.click('.view-tab[data-view="yz"]')
    to_repeat_candidate(pg)
    pg.click("#play-after")
    after2 = collect(pg)
    check(any("3回目（足した分）" in s["text"] for s in after2) and not any("変更前" in s["text"] for s in after2),
          "比較の再生 2D：変更後だけを再生し、3回目を足した分として出す")
    pg.close()

    # ---- 390px：候補が無い再生・比較の再生。横にはみ出さない・図に重ねない ----
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    watch(pg, "再生の表示・390px")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    load_repeat(pg)
    pg.click("#play-button")
    until(pg, lambda: "2回目" in now(pg), timeout_ms=15000)
    pg.evaluate("""() => { const h = document.querySelector('.top').getBoundingClientRect().height;
      const y = document.querySelector('#figure-panel').getBoundingClientRect().top + window.scrollY - h - 8;
      window.scrollTo(0, Math.max(0, y)); }""")
    pg.wait_for_timeout(100)
    shot(pg, "D4_390_playing.png")
    r = pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check(r[0] <= r[1], f"再生の表示 390px：横にはみ出さない（{r}）")
    check(not boxes_overlap(pg, "#play-now", "#fig-3d svg") and not boxes_overlap(pg, ".play-row", "#fig-3d svg"),
          "再生の表示 390px：「いま」の表示（図の上）と再生のボタン（図の下）は、図に重ねない（点を隠さない）")
    pg.click("#play-stop")
    to_repeat_candidate(pg)
    pg.click("#play-after")
    until(pg, lambda: "3回目（足した分）" in now(pg), timeout_ms=30000)
    pg.evaluate("""() => { const h = document.querySelector('.top').getBoundingClientRect().height;
      const y = document.querySelector('#figure-panel').getBoundingClientRect().top + window.scrollY - h - 8;
      window.scrollTo(0, Math.max(0, y)); }""")
    pg.wait_for_timeout(100)
    shot(pg, "D5_390_compare_after_3rd.png")
    r = pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check(r[0] <= r[1] and pg.is_visible("#play-compare"), f"比較の再生 390px：横にはみ出さない（{r}）")
    check(svg_text_outside(pg, "#fig-3d") == [], f"比較の再生 390px：回数の印の文字が図からはみ出さない：{svg_text_outside(pg, '#fig-3d')}")
    ctx.close()


def controls_over_figure_text(page, fig="#fig-3d"):
    """再生の操作の段（▶・一時停止・比較のボタン）が、図の中の文字（点の名前・回数の印など）や図そのものに重なっているもの。"""
    return page.evaluate("""(fig) => {
      const svg = document.querySelector(fig + ' svg');
      if (!svg) return ['svg なし'];
      const hit = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      const btns = [...document.querySelectorAll('.play-row button')].filter((b) => b.offsetParent)
        .map((b) => [b.textContent, b.getBoundingClientRect()]);
      const out = [];
      const s = svg.getBoundingClientRect();
      for (const [n, r] of btns) {
        if (hit(r, s)) out.push(n + ' ↔ 図');
        for (const t of svg.querySelectorAll('text')) {
          // 図の枠の外の文字は描かれない（切れる）ので、図の枠の中の部分だけで比べる
          const b = t.getBoundingClientRect();
          const q = { left: Math.max(b.left, s.left), right: Math.min(b.right, s.right),
                      top: Math.max(b.top, s.top), bottom: Math.min(b.bottom, s.bottom) };
          if (q.right > q.left && q.bottom > q.top && hit(r, q)) out.push(n + ' ↔ ' + t.textContent);
        }
      }
      return out; }""", fig)


def check_demo3d_qa(browser, url, check, shots: Path | None, errors: list) -> None:
    """QA の指摘（feat/demo-3d f61bfd1）の直し：操作ボタンは図の外、一時停止中の文、終わったあとの「1つ進む」、
    描ける区間が無いとき、2D の変更後の再生の線、止まった回数の変更の候補では比較と並びを出さない。"""
    def watch(page, sel):
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror（{sel}）: {e}"))

    def now(page):
        return page.inner_text("#play-now") if page.is_visible("#play-now") else ""

    def to_repeat(page, threed=True, size=(1280, 720)):
        page.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        page.wait_for_selector("#job-area:not([hidden])")
        page.click('.view-tab[data-view="%s"]' % ("3d" if threed else "yz"))
        page.locator("#position-chips button", has_text="C00001").first.click()
        until(page, lambda: page.inner_text("#voice-position strong") == "C00001")
        fill_text(page, REPEAT_SAY)
        page.click("#text-send")
        page.wait_for_selector("#range-yes")
        page.click("#range-yes")
        until(page, lambda: page.get_attribute("#step-4", "aria-current") == "step")
        page.wait_for_timeout(200)

    # P1：操作ボタンは図の中の文字（点の名前・回数の印）と重ならない（1280×720・1280×900・390px）
    for w, h in ((1280, 720), (1280, 900), (390, 844)):
        ctx = browser.new_context(viewport={"width": w, "height": h})
        pg = ctx.new_page()
        watch(pg, f"QA {w}×{h}")
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        to_repeat(pg)
        pg.click("#play-after")
        until(pg, lambda: "3回目" in now(pg), timeout_ms=30000)
        bad = controls_over_figure_text(pg)
        check(bad == [], f"QA P1 {w}×{h}：再生の操作ボタンは図の外に置き、図の中の文字（点の名前・回数の印）に重ならない：{bad[:4]}")
        label = pg.text_content("#fig-3d text.repeat-label") or ""
        check("×2 → ×3" in label, f"QA P1 {w}×{h}：3D の回数の印は「×2 → ×3」まで見える：{label}")
        st = pg.inner_text("#play-status")
        check(st.startswith("変更後：3回目を再生中") and "/" not in st,
              f"QA P2-2 {w}×{h}：状態の文は「変更後：3回目を再生中」（区間の番号は出さない）：{st}")
        check(pg.locator("#fig-3d text.cycle-label").count() == 0, f"QA P2-2 {w}×{h}：印の横の「3回目」は出さない（帯と並びに出す）")
        ctx.close()

    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(pg, "QA 一時停止・終わり")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.click('.view-tab[data-view="3d"]')
    bad = controls_over_figure_text(pg)
    check(bad == [], f"QA P1 1280×900：候補が無いときも、操作ボタンは図の文字に重ならない：{bad[:4]}")
    # P2-3：一時停止中・1つ進むで止めたときは「（一時停止中）」
    pg.click("#play-button")
    until(pg, lambda: "22行目" in now(pg), timeout_ms=10000)
    pg.click("#play-pause")
    st = pg.inner_text("#play-status")
    check("（一時停止中）" in st and "再生中" not in st, f"QA P2-3：一時停止中の状態の文：{st}")
    pg.click("#play-step")
    st = pg.inner_text("#play-status")
    check("（一時停止中）" in st and "再生中" not in st, f"QA P2-3：1つ進むで止めたときの状態の文：{st}")
    pg.click("#play-pause")
    st = pg.inner_text("#play-status")
    check("を再生中" in st, f"QA P2-3：続けると「再生中」に戻る：{st}")
    # P2-4：終わったあと「1つ進む」で、はじめから1区間ずつ
    until(pg, lambda: pg.inner_text("#play-status") == "再生が終わりました", timeout_ms=30000)
    check(pg.is_enabled("#play-step"), "QA P2-4：終わったあとも「1つ進む」を押せる")
    pg.click("#play-step")
    check("いま：21行目" in now(pg) and "（一時停止中）" in pg.inner_text("#play-status"),
          f"QA P2-4：終わったあとの「1つ進む」は、はじめの区間から：{now(pg).splitlines()[0] if now(pg) else ''}")
    pg.close()

    # P2-5：3D で描ける区間が0件（線の行き先の点が図に無い。画面の受け取るデータだけを差し替える）
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(pg, "QA 区間0件")
    pg.add_init_script(FAKE_RECOGNITION)

    def no_drawable(route):
        res = route.fetch()
        body = res.json()
        if body.get("view"):
            for seg in body["view"]["segments"]:
                seg["from"] = "C99999"  # 図に無い点（3D では描けない）
        route.fulfill(response=res, body=json.dumps(body, ensure_ascii=False),
                      headers={**res.headers, "content-type": "application/json; charset=utf-8"})
    pg.route("**/api/load", no_drawable)
    pg.goto(url)
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
    pg.wait_for_selector("#job-area:not([hidden])")
    pg.click('.view-tab[data-view="3d"]')
    pg.click("#play-button")
    pg.wait_for_timeout(300)
    check("playing" not in (pg.get_attribute("#play-button", "class") or "")
          and pg.inner_text("#play-status") == "図に描ける区間が無いため、再生できません" and pg.is_hidden("#play-now"),
          f"QA P2-5：描ける区間が0件なら、▶ を再生中の見た目のまま残さない：{pg.inner_text('#play-status')}")
    pg.close()

    # P2-6：2D の変更後の再生で、1回目・2回目の線も（再生している間）見える
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(pg, "QA 2D 変更後")
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    to_repeat(pg, threed=False)
    pg.click("#play-after")
    until(pg, lambda: "1回目" in now(pg) and "22行目" in now(pg), timeout_ms=15000)
    strokes = pg.eval_on_selector_all("#fig-main line.seg.after", "els => els.filter(e => e.getAttribute('stroke-width') === '5').map(e => e.getAttribute('stroke'))")
    check(strokes and all(s != "transparent" for s in strokes), f"QA P2-6：2D の変更後の再生で、1回目の線も見える（{strokes}）")
    until(pg, lambda: pg.inner_text("#play-status") == "再生が終わりました", timeout_ms=30000)
    left = pg.eval_on_selector_all("#fig-main line.seg.after", "els => els.filter(e => e.getAttribute('stroke') === '#6e6e73').length")
    check(left == 0, f"QA P2-6：再生が終わると、変更前と同じ線は元どおり（重ねて描かない。残り {left}本）")
    pg.close()

    # P2-7：止まった回数の変更の候補（blocked_reason あり）では、比較のボタンと並びのカードを出さない
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    watch(pg, "QA 止まった回数の候補")
    pg.add_init_script(FAKE_RECOGNITION)

    def blocked(route):
        res = route.fetch()
        body = res.json()
        c = (body.get("state") or {}).get("candidate")
        if c:
            c["blocked_reason"] = "e2e の確かめ用に、止まった候補として表示します"
        route.fulfill(response=res, body=json.dumps(body, ensure_ascii=False),
                      headers={**res.headers, "content-type": "application/json; charset=utf-8"})
    pg.route("**/api/repeat", blocked)
    pg.goto(url)
    to_repeat(pg)
    check(pg.is_hidden("#play-compare") and pg.is_hidden("#play-seq"),
          "QA P2-7：止まった回数の変更の候補では、比較の再生と動きの並びを出さない")
    pg.close()


def until(pg, cond, timeout_ms: int = 5000) -> None:
    """条件が成り立つまで待つ（画面の CSP が文字列の評価を禁じているので、Python 側で確かめる）。"""
    waited = 0
    while not cond():
        if waited >= timeout_ms:
            raise TimeoutError("待っても画面が変わりませんでした")
        pg.wait_for_timeout(50)
        waited += 50


# 3D の台座の目印・正面の矢印（唐川さんの決定 2026-11-05 夜。docs/DECISION_3D_ROBOT_BASE_20261105.md）。図の中の様子を集める（viewBox の単位）
ROBOT3D_JS = """(host) => {
  const svg = document.querySelector(host + ' svg');
  if (!svg) return null;
  // 画面の px → viewBox の単位（図は枠の中で縦横の比を保って真ん中に置かれるので、getScreenCTM で戻す）
  const vb = svg.viewBox.baseVal, m = svg.getScreenCTM();
  const toVb = (r) => ({ x0: (r.left - m.e) / m.a, x1: (r.right - m.e) / m.a, y0: (r.top - m.f) / m.d, y1: (r.bottom - m.f) / m.d });
  const box = (e) => toVb(e.getBoundingClientRect());
  const num = (e, a) => parseFloat(e.getAttribute(a));
  const line = (e) => e ? [num(e, 'x1'), num(e, 'y1'), num(e, 'x2'), num(e, 'y2')] : null;
  const origin = svg.querySelector('.robot-origin');
  const ring = svg.querySelector('g.robot-base polygon');
  const ringC = ring ? (() => { const ps = ring.getAttribute('points').trim().split(/\\s+/).map((s) => s.split(',').map(Number));
    return [ps.reduce((a, p) => a + p[0], 0) / ps.length, ps.reduce((a, p) => a + p[1], 0) / ps.length]; })() : null;
  const lab = (cls) => [...svg.querySelectorAll('text.' + cls)].map((t) => [t.textContent, box(t)]);
  const others = [...svg.querySelectorAll('g.pt3 > text, text.moved-label, text.repeat-label, text.axis3, text.axis, text.hint3')].map((t) => [t.textContent, box(t)])
    .concat([...svg.querySelectorAll('g.taught-mark, g.inset')].map((g) => [g.textContent.slice(0, 12), box(g)]))
    .concat([...svg.querySelectorAll('g.pt3')].map((g) => ['点 ' + g.dataset.names, box(g.querySelector('circle'))]))
    .concat([...svg.querySelectorAll('circle.after-pt')].map((c) => ['変更後の点', box(c)]));
  const rl = lab('robot-label');
  const ov = (a, b) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
  const overlaps = [];
  for (const [s, r] of rl) for (const [t, q] of others) if (ov(r, q)) overlaps.push([s, t]);
  for (let i = 0; i < rl.length; i++) for (let j = i + 1; j < rl.length; j++) if (ov(rl[i][1], rl[j][1])) overlaps.push([rl[i][0], rl[j][0]]);
  // 点が押せる（台座の印・矢印・札・ほかの点の下に隠れていない）：点の真ん中で一番上にあるのが、その点の g.pt3。
  // 変更候補の変更後の点（オレンジの点と輪）・再生の印が変更前の点に重なるのは今までどおり（候補の間は選び直せない）
  const topAt = (g) => { const r = g.querySelector('circle').getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); };
  const hidden = [...svg.querySelectorAll('g.pt3')].filter((g) => {
    const e = topAt(g);
    if (e && e.closest('g.pt3') === g) return false;
    if (!e || e.closest('g.pt3, .robot-base') || (e.closest('g') && e.closest('g').querySelector(':scope > text.robot-label'))) return true;
    return !(e.matches('circle.after-pt, circle.marker, .dest-mark') || e.closest('g.inset') || (e.matches('line, circle') && e.getAttribute('stroke') === '#ff6a1a'));
  }).map((g) => { const e = topAt(g); return g.dataset.names + '（上にあるもの：' + (e ? e.outerHTML.slice(0, 90) : 'なし') + '）'; });
  const inFrame = (r) => r.x0 >= -0.5 && r.y0 >= -0.5 && r.x1 <= vb.width + 0.5 && r.y1 <= vb.height + 0.5;
  const out = [...svg.querySelectorAll('g.pt3')].map((g) => [g.dataset.names, box(g.querySelector('circle'))])
    .concat(origin ? [['台座の印', box(origin)]] : []).concat(ring ? [['台座の輪', box(ring)]] : [])
    .concat([...svg.querySelectorAll('.robot-front, .robot-front-head')].map((e) => ['正面の矢印', box(e)]))
    .filter(([, r]) => !inFrame(r)).map(([n]) => n);
  const ax = (n) => { const e = svg.querySelector('line.axis-line[data-axis="' + n + '"]'); return e ? [line(e), Number(e.dataset.sign)] : null; };
  const pt = (n) => { const g = [...svg.querySelectorAll('g.pt3')].find((x) => x.dataset.names.split(' ').includes(n));
    if (!g) return null; const c = g.querySelector('circle'); return [num(c, 'cx'), num(c, 'cy')]; };
  const pts = Object.fromEntries([...svg.querySelectorAll('g.pt3')].map((g) => [g.dataset.names, [num(g.querySelector('circle'), 'cx'), num(g.querySelector('circle'), 'cy')]]));
  return { W: vb.width, H: vb.height, origin: origin ? [num(origin, 'cx'), num(origin, 'cy')] : null, ringC,
    front: line(svg.querySelector('line.robot-front')), axX: ax('X'), axY: ax('Y'),
    labels: lab('robot-label').map(([s]) => s), overlaps, hidden, out,
    pt: { C00000: pt('C00000'), C00004: pt('C00004'), C00001: pt('C00001'), C00002: pt('C00002') }, pts,
    client: Object.fromEntries([...svg.querySelectorAll('g.pt3')].map((g) => { const r = g.querySelector('circle').getBoundingClientRect();
      return [g.dataset.names, [r.left + r.width / 2, r.top + r.height / 2]]; })),
    model: svg.querySelectorAll('.robot-model, .robot-part, .robot-caption, .robot-caption-g').length,
    clickable: [...svg.querySelectorAll('g.robot-base, g.robot-base *')].filter((e) => getComputedStyle(e).pointerEvents !== 'none').length };
}"""

BASE2D_JS = """(host) => {
  const svg = document.querySelector(host + ' svg');
  if (!svg) return null;
  const o = svg.querySelector('.base2d'), f = svg.querySelector('.front2d');
  const t = [...svg.querySelectorAll('text.base2d-label')].map((e) => e.textContent);
  return { origin: !!o, front: f ? [f.getAttribute('x1'), f.getAttribute('y1'), f.getAttribute('x2'), f.getAttribute('y2')].map(Number) : null, labels: t }; }"""

MODEL_WORDS = ("HC20 を模した", "メーカーの形状データ", "目安の静止モデル", "ロボットの姿勢は、実機・ジョブの動きと関係ありません")


def _cos(u, v):
    import math
    nu, nv = math.hypot(*u), math.hypot(*v)
    return (u[0] * v[0] + u[1] * v[1]) / (nu * nv) if nu and nv else 0.0


def check_3d_robot_base(browser, check, shots: Path | None, out: Path) -> None:
    """唐川さんの決定（2026-11-05 夜の2回目。docs/DECISION_3D_ROBOT_BASE_20261105.md）：3D に、ロボット座標の原点（台座の中心）の
    黄色の丸「ロボット（台座の中心）」と、原点から X＋ の矢印「ロボットの正面（X＋）」を出す。HC20 のモデル（腕・関節・胴体）と
    表示・非表示のボタンは出さない。正面の矢印は床の X の軸の＋の向き（床の Y の軸はジョブの点で確かめる）。
    最初の視点と［視点を戻す］で台座と全部の点が枠に入る・札がほかの札と重ならない・点を押せる・2D の3つの図の原点と X＋ の印・
    注意書き（干渉を保証しない文が残り、モデル用の文が無い）。1280×720 と 1920×1080。モックの台本の答え（本物の Claude は呼ばない）。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "base_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    want_labels = sorted(["ロボット（台座の中心）", "ロボットの正面（X＋）"])

    def marks(tag, r, job):
        """台座の丸・正面の矢印・札・点を押せる・全体が枠に入る"""
        if r is None:
            check(False, f"台座（{tag}）：3D の図が無い")
            return
        check(r["model"] == 0, f"台座（{tag}）：HC20 のモデル（腕・関節・胴体）とモデルの札は出さない：{r['model']}")
        o, rc = r["origin"], r["ringC"]
        check(bool(o and rc) and abs(o[0] - rc[0]) < 3 and abs(o[1] - rc[1]) < 3,
              f"台座（{tag}）：黄色の丸と床の輪が、原点（台座の中心）の同じ所にある：丸 {o}・輪 {rc}")
        check(r["clickable"] == 0, f"台座（{tag}）：台座の印・矢印は押しても何も起きない（pointer-events なし）：{r['clickable']}")
        fr, axx = r["front"], r["axX"]
        fv = [fr[2] - fr[0], fr[3] - fr[1]] if fr else [0, 0]
        xv = [(axx[0][2] - axx[0][0]) * axx[1], (axx[0][3] - axx[0][1]) * axx[1]] if axx else [0, 0]
        check(bool(o and fr and axx) and abs(fr[0] - o[0]) < 1 and abs(fr[1] - o[1]) < 1 and _cos(fv, xv) > 0.97,
              f"台座（{tag}）：正面の矢印は原点から出て、床の X の軸の＋の向きを指す（cos {round(_cos(fv, xv), 3)}）")
        # ジョブの点はどれも X＞0：原点から点の真ん中へは、正面の矢印の側（投影した点で確かめる）
        if o and r["pts"]:
            cx_ = sum(v[0] for v in r["pts"].values()) / len(r["pts"]); cy_ = sum(v[1] for v in r["pts"].values()) / len(r["pts"])
            c = _cos([cx_ - o[0], cy_ - o[1]], fv)
            check(c > 0, f"台座（{tag}）：X＞0 のジョブの点は、原点から見て正面の矢印の側にある（cos {round(c, 3)}）")
        # 床の Y の軸は、ジョブの点で確かめる（Y だけが違う2点：DEMO_C_PLACE C00001→C00004・DEMO_BUTTON_REPEAT C00000→C00004）
        a, b = ("C00001", "C00004") if job == "DEMO_C_PLACE" else ("C00000", "C00004")
        pa, pb, axy = r["pt"][a], r["pt"][b], r["axY"]
        yv = [(axy[0][2] - axy[0][0]) * axy[1], (axy[0][3] - axy[0][1]) * axy[1]] if axy else [0, 0]
        c = _cos([pb[0] - pa[0], pb[1] - pa[1]], yv) if pa and pb else 0
        check(c > 0.95, f"台座（{tag}）：床の Y の軸の＋は、ジョブの点の Y＋ の向き（{a}→{b}、cos {round(c, 3)}）")
        check(sorted(r["labels"]) == want_labels, f"台座（{tag}）：台座と正面の札が出る：{r['labels']}")
        check(not r["overlaps"], f"台座（{tag}）：台座の札・正面の札が、点・名前・変更の札・回数の札・窓・軸の文字と重ならない：{r['overlaps']}")
        check(not r["hidden"], f"台座（{tag}）：ジョブの点はどれも、台座の印・矢印・札に隠れず押せる：{r['hidden']}")
        check(not r["out"], f"台座（{tag}）：台座の印・矢印とジョブの点が、全部枠に入る（はみ出し {r['out']}）")

    try:
        for (w, h) in [(1280, 720), (1920, 1080)]:
            for job in ("DEMO_C_PLACE", "DEMO_BUTTON_REPEAT"):
                tag = f"{w}×{h}・{job}"
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.add_init_script(FAKE_RECOGNITION)
                pg.goto(url)
                pg.set_input_files("#file-input", str(REPO / "samples/dummy" / f"{job}.JBI"))
                pg.wait_for_selector("#job-area:not([hidden])")
                until(pg, lambda: pg.is_enabled("#text-send") or pg.is_visible("#kbd-button"))
                pg.wait_for_timeout(400)
                r0 = pg.evaluate(ROBOT3D_JS, "#fig-3d")
                marks(f"{tag}・読み込んだ直後", r0, job)
                ovl = label_overlaps_page(pg)
                check(not ovl, f"台座（{tag}・読み込んだ直後）：点の名前どうしが重ならない：{ovl}")
                outside = pg.evaluate(TEXT3D_OUTSIDE_JS, False)
                check(not outside, f"台座（{tag}）：3D の文字が枠の中に収まる：{outside}")
                heads = pg.evaluate("""() => [...document.querySelectorAll('#figure-panel .stage-head button')].map((b) => b.id || b.dataset.view)""")
                # 本番UI：「正面に戻す」（#view-reset）は 3D の左上（図の上）。見出しの欄は図の向きのタブだけ
                check(heads == ["3d", "yz", "xz", "xy"] and pg.locator("#model-toggle").count() == 0 and pg.is_visible("#view-reset"),
                      f"台座（{tag}）：3D の上の欄は図の向き（3D・Y・Z・X・Z・X・Y）、正面に戻すは図の上にいつも見える：{heads}")
                notes = pg.text_content("#figure-panel .figure-notes")  # 全文（Issue #7：ⓘ の中）
                body = pg.inner_text("body")
                check("ロボット本体・関節は描いていません" in notes and "動作や干渉を保証するシミュレーターではありません" in notes
                      and "当たらないとは限りません" in notes and "腕やツールの根元は描いていません" in notes and "参考表示であり" in notes
                      and not any(x in body for x in MODEL_WORDS)
                      and not pg.evaluate("(ws) => [...document.querySelectorAll('[title],[aria-label]')].some((e) => ws.some((w) => (e.title + (e.getAttribute('aria-label') || '')).includes(w)))", list(MODEL_WORDS)),
                      f"台座（{tag}）：干渉を保証しない注意書きが出て、モデル用の文はどこにも無い")
                tip = pg.evaluate("() => { const t = document.querySelector('#fig-3d g.robot-base > title'); return t ? t.textContent : null; }")
                check(tip == "台座の印は、ロボット座標の原点（0,0,0）の位置です。ロボットの大きさ・形ではありません。",
                      f"台座（{tag}）：台座の印にマウスを重ねると、原点の位置でありロボットの大きさ・形ではないと出る：{tip}")
                if r0 is None:
                    pg.close()
                    continue
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(shots / f"BASE_{w}_{job}_load.png"))
                # 回してから［視点を戻す］
                fb = pg.locator("#fig-3d svg").bounding_box()
                sx, sy = fb["x"] + 30, fb["y"] + fb["height"] - 60
                pg.mouse.move(sx, sy)
                pg.mouse.down()
                pg.mouse.move(sx + 120, sy - 40, steps=6)
                pg.mouse.up()
                pg.wait_for_timeout(200)
                r1 = pg.evaluate(ROBOT3D_JS, "#fig-3d")
                check(r1["front"] != r0["front"] and pg.is_visible("#view-reset"), f"台座（{tag}）：回すと向きが変わり［視点を戻す］が出る")
                fr1, ax1 = r1["front"], r1["axX"]
                c1 = _cos([fr1[2] - fr1[0], fr1[3] - fr1[1]], [(ax1[0][2] - ax1[0][0]) * ax1[1], (ax1[0][3] - ax1[0][1]) * ax1[1]]) if fr1 and ax1 else 0
                check(c1 > 0.97, f"台座（{tag}・回したあと）：正面の矢印は床の X の軸の＋の向きのまま（cos {round(c1, 3)}）")
                pg.click("#view-reset")
                pg.wait_for_timeout(200)
                r2 = pg.evaluate(ROBOT3D_JS, "#fig-3d")
                same = bool(r2["front"]) and all(abs(a - b) < 1 for a, b in zip(r2["front"], r0["front"])) and all(
                    n in r2["pts"] and abs(r2["pts"][n][0] - r0["pts"][n][0]) < 1 and abs(r2["pts"][n][1] - r0["pts"][n][1]) < 1 for n in r0["pts"])
                check(same and not r2["out"], f"台座（{tag}）：［視点を戻す］で最初の視点に戻る（台座と全部の点が枠に入る。はみ出し {r2['out']}）")
                # 点を押して選べる
                c2 = r2["client"].get("C00002")
                if c2:
                    pg.mouse.click(c2[0], c2[1])
                    pg.wait_for_timeout(500)
                check(pg.inner_text("#voice-position strong") == "C00002", f"台座（{tag}）：3D の点 C00002 を押して選べる")
                # 2D の3つの図（大きな図と、詳細表示の小さな図）
                for view, front in (("yz", False), ("xz", True), ("xy", True)):
                    pg.click(f'.view-tab[data-view="{view}"]')
                    pg.wait_for_timeout(150)
                    b2 = pg.evaluate(BASE2D_JS, "#fig-main")
                    ok = bool(b2 and b2["origin"] and (b2["front"] is not None) == front and "ロボット（台座の中心）" in b2["labels"])
                    if ok and front:
                        ok = b2["front"][2] > b2["front"][0] and abs(b2["front"][3] - b2["front"][1]) < 0.5 and "ロボットの正面（X＋）" in b2["labels"]
                    if ok and not front:
                        ok = any("X＋" in s for s in b2["labels"])
                    check(ok, f"台座（{tag}・2D {view}）：原点の印と札、X＋（正面）の向きの印が出る：{b2}")
                    th = pg.evaluate(BASE2D_JS, f"#fig-{view}")
                    check(bool(th and th["origin"] and (th["front"] is not None) == front), f"台座（{tag}・小さな図 {view}）：原点と X＋ の印が出る：{th}")
                    ov2 = pg.evaluate("""() => { const svg = document.querySelector('#fig-main svg');
                      const r = (e) => e.getBoundingClientRect(); const ov = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
                      const mine = [...svg.querySelectorAll('text.base2d-label')], others = [...svg.querySelectorAll('g.pt text, g.pt circle, text.axis')]
                        .concat([...svg.querySelectorAll(':scope > text')].filter((t) => !t.classList.contains('base2d-label')));
                      const out = []; for (const a of mine) for (const b of others) if (ov(r(a), r(b))) out.push([a.textContent, b.textContent || b.tagName]);
                      const vb = r(svg); for (const a of mine) { const q = r(a); if (q.left < vb.left - 0.5 || q.right > vb.right + 0.5 || q.top < vb.top - 0.5 || q.bottom > vb.bottom + 0.5) out.push([a.textContent, '枠の外']); }
                      return out; }""")
                    check(not ov2, f"台座（{tag}・2D {view}）：原点・正面の札が、点・名前・軸の文字と重ならず、図の中：{ov2}")
                pg.click('.view-tab[data-view="3d"]')
                pg.wait_for_timeout(200)
                if job == "DEMO_C_PLACE":  # 読み込み直して（上で C00002 を選んだため）、C00003 を Zプラス方向に5mm
                    pg.goto(url)
                    pg.set_input_files("#file-input", str(REPO / "samples/dummy" / f"{job}.JBI"))
                    pg.wait_for_selector("#job-area:not([hidden])")
                    pg.locator("#position-chips button", has_text="C00003").click()
                    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
                    fill_text(pg, "Zプラス方向に5mm")
                    pg.click("#text-send")
                    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
                    pg.wait_for_timeout(300)
                    r5 = pg.evaluate(ROBOT3D_JS, "#fig-3d")
                    marks(f"{tag}・変更候補（C00003 Z+5mm）", r5, job)
                    ovl = label_overlaps_page(pg)
                    check(not ovl, f"台座（{tag}・変更候補）：点の名前と変更の札が重ならない：{ovl}")
                    outside = pg.evaluate(TEXT3D_OUTSIDE_JS, False)
                    check(not outside and pg.locator("#fig-3d text.moved-label").count() == 1,
                          f"台座（{tag}・変更候補）：変更の札が出て、文字が枠の中：{outside}")
                    check(pg.is_visible("#candidate-view") and pg.is_visible("#play-button"),
                          f"台座（{tag}・変更候補）：候補のカードと再生のボタンが出ている")
                    if shots is not None:
                        pg.screenshot(path=str(shots / f"BASE_{w}_{job}_candidate.png"))
                pg.close()
    finally:
        srv.shutdown()
        srv.server_close()


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
            fill_text(pg, "手で書いた文")
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
            fill_text(pg, "")
            pg.evaluate("window.__fakeText = 'X 方向に 5mm'")
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-stop"))
            pg.click("#voice-stop")
            until(pg, lambda: pg.input_value("#text-input") == "X 方向に 5mm")
            check(pg.is_visible("#voice-hint") and "＋か−が入っていません" in pg.inner_text("#voice-hint"),
                  "声から入った文に軸の向きが無ければ、送る前に「＋か−が入っていません」と知らせる")
            fill_text(pg, "X マイナス方向に 5mm")
            check(pg.is_hidden("#voice-hint"), "文を直して向きを入れると、知らせは消える")
            fill_text(pg, "")
            check(pg.inner_text("#mic-label") == "マイク OFF" and not pg.is_enabled("#voice-cancel"),
                  "1回話し終わると、マイクは自動で OFF（「マイクをOFF」を押さなくてよい）")
            pg.evaluate("window.__fakeText = %r" % FAKE_TEXT)

            # 録音 → 終了 → 入力欄に入るだけ（自動では送らない）
            fill_text(pg, "")
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
            check(pg.is_hidden("#mic-button") and pg.is_disabled("#mic-button"),
                  "ステップ4ではマイクを出さない（録音しながら承認・出力できない）")
            # 11/4 唐川さんの確認項目6：承認のあとは、説明の質問のために文字の欄だけ出す（マイクは出さない）
            check(pg.is_visible("#text-input") and pg.is_enabled("#text-send"),
                  "承認のあとは、文字の欄から説明の質問ができる（マイクは出さない）")

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
            # 再生の表示（いま：何行目・何回目）と、回数の変更の比較の再生（feat/demo-3d）
            check_demo3d(b, url, check, shots, errors)
            check_demo3d_qa(b, url, check, shots, errors)

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
            fill_text(pg3, "少し動かして")
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
            fill_text(pg4, "Yを3mm下げて")
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
            check("指示：「C00005のY座標を3mm下げて」" in cx and "147.000" in cx and "未確認" in cx,
                  "確認表示に、指示の文・変更量・変更後の XYZ と、X・Y の向きの名前は未確認であることを出す")
            if shots is not None:
                pg4.screenshot(path=str(shots / "6b_confirm_xyz.png"))
            pg4.close()

            check_header_help(b, url, check, shots)
            check_ux_polish(b, url, check, shots)
            check_c026(b, url, check, shots)
            check_review_only(b, url, check, shots)
            check_ui_1105(b, check, shots, out)
            check_answer_after_approval(b, check, shots, out)
            check_send_while_waiting(b, check, shots, out)
            check_text_size_1080(b, check, shots, out)
            check_3d_labels_inside(b, check, shots, out)
            check_3d_fit_stable(b, check, shots, out)
            check_3d_robot_base(b, check, shots, out)
            check_load_while_waiting(b, check, shots, out)
            check_reload_same_file(b, check, shots, out)
            check_voice_error_typing(b, check, shots, out)
            check_mic_after_undo(b, check, shots, out)
            check_hold_after_approval(b, check, shots, out)
            check_export_twice(b, check, shots, out)
            check_opening_click_through(b, check, shots, out)
            check_export_path_short(b, check, shots, out)
            check_heading_matches(b, check, shots, out)
            check_approve_twice(b, check, shots, out)
            check_taught_pill_clear(b, check, shots, out)
            check_export_fail_next(b, check, shots, out)
            check_ask_limit_reset(b, check, shots, out)
            check_approve_tip_error(b, check, shots, out)
            check_dialog(b, check, shots, errors, updown_cfg["DEMO_C_PLACE.JBI"])
            check_talk(b, check, shots, errors, out)
            check_updown_unconfirmed(b, check, shots, errors)
            check_above_fold(b, check, shots, errors, out, log)

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
    # 1ターン目は本物の Claude の答え（2026-11-03 シクロの通し）から数を除いた文（唐川さんの方針③ 11/4：MVP では Claude の
    # 自由文に数を含めない）・同じ根拠の行（20〜27。MOVJ の行を含む）
    replies = [ans(1, "最初の位置に動いたあと、同じ場所をめぐる動きをくり返し、最後に別の位置へ移ります。"
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
            # ① 初期の画面（唐川さん承認のデザイン 11/4）では、会話の欄は最初の送信まで見せない（欄そのものは用意される）
            pg.wait_for_selector("#ask-panel:not([hidden])", state="attached")
            # 入口は1つ（11/4 唐川さんの指示③）：会話も指示も、指示の欄（#text-input・#text-send）から
            until(pg, lambda: pg.is_enabled("#text-send"))
            check(pg.locator("#ask-input").count() == 0 and pg.locator("#voice-to-ask").count() == 0,
                  "入口は1つ：「Claude に聞く」の入力欄と「🎤 の文をこの欄に入れる」は無い")
            if width == 390:
                w = pg.evaluate("document.documentElement.scrollWidth")
                check(w <= 390, f"会話 390px：「Claude に聞く」の欄があっても横にはみ出さない（{w}px）")
                pg.close()
                continue
            fill_text(pg, "このロボット、何をしている？")
            pg.click("#text-send")
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
            check(pg.locator("#ask-turns .bubble.claude").count() == 1, "会話：数を含まない Claude の文がそのまま表示される")
            # Issue #7：ふだんの記録は上の帯に出さず、アプリ情報に出す（文はサーバーのまま）
            check(pg.is_hidden("#status-line") and "承認 まだ" in (pg.text_content("#app-info-status") or ""),
                  f"状態の1行（プログラムの確定情報）はアプリ情報にある：{pg.text_content('#app-info-status')}")
            check("21〜26行目：C00001→C00002→C00003（2回）" in pg.inner_text("#ask-turns .supplement"),
                  "会話：番号は補足として小さく出る")
            whos = pg.locator("#ask-turns .bubble .who").all_inner_texts()
            check(any(x.startswith("Claude") for x in whos) and "人" in whos, f"会話：話し手（人・Claude）が出る：{whos}")
            fill_text(pg, "ボタンを押してるんだよ")
            pg.click("#text-send")
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
                  "会話：人が教えた意味が「ユーザー登録情報」の印で吹き出しと覚えていることの欄に出る")
            check("（人が教えた）" in (pg.text_content("#fig-3d g.taught-mark") or ""), "会話：3D にも「人が教えた」の印が出る")
            fill_text(pg, "じゃあ、3回にして")
            pg.click("#text-send")
            until(pg, lambda: "範囲を確かめてください" in pg.inner_text("#ask-turns"))
            txt = pg.inner_text("#ask-turns")
            check("ボタンを押す動きを、2回から3回にしますか？" not in txt and "2回から3回にするご希望として受け取りました" in txt
                  and "範囲を確かめてください" in txt
                  and pg.is_disabled("#approve-button") and pg.is_hidden("#approval-status"),
                  "会話：「3回にして」は Claude の文を出さず、アプリの定型文だけ（候補・承認は作らない。CHECKER_033 P1-A）")
            check(pg.locator("#ask-turns .who", has_text="アプリ").count() >= 2, "会話：アプリの文は「アプリ」と出る")
            # Step F：同じ入口から、プログラムが作った決まった文で範囲の確認のボタンへ（候補はボタンを押してから）
            pg.wait_for_selector("#range-yes", timeout=5000)
            check(pg.is_visible("#range-yes") and pg.get_attribute("#step-4", "aria-current") != "step"
                  and "C00001から" not in pg.input_value("#text-input"),
                  "Step F：会話の欄の「じゃあ、3回にして」から、範囲の確認のボタンが出る（まだ候補ではない）")
            if shots:
                pg.screenshot(path=str(shots / "TALK_1280_scene3.png"), full_page=True)
            pg.click("#range-yes")
            until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
            check(pg.is_hidden("#approval-status") and pg.is_enabled("#open-confirm"),
                  "Step F：範囲のボタンを押すと回数の変更の候補になる（承認はまだ。確認表示のボタンで）")
            if shots:
                pg.screenshot(path=str(shots / "TALK_1280_scene3_candidate.png"), full_page=True)
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
        fill_text(page, text)
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
        # Issue #7：［位置を選ばずに話す］の文字は出さない（位置を選ばずにマイクで話せる）。会話の流れを確かめるため、同じ状態に入る
        check(pg.is_hidden("#talk-first") and pg.is_enabled("#mic-button"), "会話・Issue #7：ステップ2で、位置を選ばずにマイクを押せる（文字のボタンは出さない）")
        talk_first(pg)
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
        talk_first(pg)
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
        talk_first(pg)
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
        talk_first(pg)
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
        talk_first(pg)
        send(pg, "置くところを少し手前にして")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        pg.wait_for_timeout(200)
        gap = pg.evaluate("""() => document.querySelector('#next-action').getBoundingClientRect().top
                              - document.querySelector('.top').getBoundingClientRect().bottom""")
        check(gap >= 0, f"390px：送ったあと、見出し（次にすること）が上の帯の下に隠れない（間 {gap:.0f}px）")
        shot(pg, "d14_390.png")
        pg.close()

        # ---- 3. くり返しの「2回」（d6f6a7c・11/5 唐川さん：行き止まりの二択はやめ、1回目で言い方を示して止める）----
        #      候補なし・選択肢なし・解釈の係も呼ばない
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
        until(pg, lambda: "範囲の決まらない繰り返しは、このアプリでは扱いません" in pg.inner_text("#reply-message"))
        check("回数を変えるときは" in pg.inner_text("#reply-message"),
              "くり返し：「2回」は、二択にせず、回数を変える言い方を示して止める")
        shot(pg, "d8_repeat.png")
        check(pg.locator("#reply-message button.choice").count() == 0 and not pg.is_visible("#range-yes")
              and pg.get_attribute("#step-3", "aria-current") == "step" and (interp.decides, interp.locates) == before,
              "くり返し：選択肢・範囲の確認を出さない（候補なし・解釈の係を呼ばない）")
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
        talk_first(pg)
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
        check(pg.text_content("#updown-status") == "上下の向き：未確認（Z＋・Z−で指示）",
              f"上下の設定なし：「上下の向き：未確認（Z＋・Z−で指示）」と出す（{pg.text_content('#updown-status')!r}）")
        det = pg.inner_text("#updown-detail") if pg.is_visible("#updown-detail") else ""
        check(det == "上下の向き：未確認（HC20DT）", f"S30-1：設定なしのサーバーでもロボット名（--robot）を出す（{det!r}）")
        side = len("".join(pg.evaluate("""() => { const tips = [...document.querySelectorAll('button.tip')]; tips.forEach((b) => { b.style.display = 'none'; });
            const s = [...document.querySelectorAll('.side > :not(#ask-panel):not(#status-line):not(#side-scroll), #side-scroll > :not(#ask-panel)')]
            .filter((e) => e.offsetParent !== null).map((e) => e.innerText).join('');
            tips.forEach((b) => { b.style.display = ''; }); return s; }""").split()))  # 会話の欄（PC 版で右側）は数えない
        check(side <= 80, f"上下の設定なし：次にすることを足しても、音声のカードの右側の文字は80文字まで（{side}文字）")
        # 不一致・変更済みの表示の文に入れ替えても 80文字まで（表示の文だけを入れ替えて数える。サーバーの状態は変えない）
        for other in ("上下の向き：確認した座標系・ツールと違います", "上下の向き：設定が変わっています"):
            pg.evaluate("t => { document.querySelector('#updown-status').textContent = t; }", other)
            side2 = len("".join(pg.evaluate("""() => { const tips = [...document.querySelectorAll('button.tip')]; tips.forEach((b) => { b.style.display = 'none'; });
            const s = [...document.querySelectorAll('.side > :not(#ask-panel):not(#status-line):not(#side-scroll), #side-scroll > :not(#ask-panel)')]
            .filter((e) => e.offsetParent !== null).map((e) => e.innerText).join('');
            tips.forEach((b) => { b.style.display = ''; }); return s; }""").split()))  # 会話の欄（PC 版で右側）は数えない
            check(side2 <= 80, f"上下の向き「{other}」の表示でも、音声のカードの右側の文字は80文字まで（{side2}文字）")
        pg.evaluate("t => { document.querySelector('#updown-status').textContent = t; }", "上下の向き：未確認（Z＋・Z−で指示）")
        fill_text(pg, ph)
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
        fill_text(pg, "3ミリ下げて")
        pg.click("#text-send")
        # 11/5 唐川さんの決定：上下が未確認のときの会話の聞き返しは、意図を受け止める文（utterance.updown_ask_message）。
        # 状態の表示の文（updown.MSG_UNCONFIRMED）は変えていない。ここは #reply-message の会話の文を見る
        from voice_teaching import utterance as ut
        until(pg, lambda: pg.is_visible("#reply-message") and ut.is_updown_unset_ask(pg.inner_text("#reply-message")))
        check("3mm下げたいんですね。" in pg.inner_text("#reply-message") and "この設備では、まだ「下」の方向が決まっていません。"
              in pg.inner_text("#reply-message"), "上下の設定なし：「3ミリ下げて」の聞き返しは、言った量と向きを受け止める文")
        # d（11/5 唐川さんの決定）：画面で初めての向きの言葉は、向きの確かめのボタン［合っている］［違う］だけを出す
        # （Z を決める選択肢は出さない。押すまで候補にしない）
        btns = [pg.locator("#reply-message button.choice").nth(i).inner_text()
                for i in range(pg.locator("#reply-message button.choice").count())]
        check(btns == ["合っている", "違う"] and not pg.is_enabled("#open-confirm")
              and pg.get_attribute("#step-4", "aria-current") != "step",
              f"上下の設定なし：「3ミリ下げて」は候補にせず、向きの確かめのボタンだけで聞き返す（Z を決める選択肢は出さない）：{btns}")
        if shots is not None:
            pg.screenshot(path=str(shots / "m2_unconfirmed.png"))
        fill_text(pg, "Zマイナス3ミリ")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        cv = pg.inner_text("#candidate-view")
        check("Z方向に −3.000mm" in cv and "197.000" in cv,
              f"上下の設定なし：軸と符号を明示した「Zマイナス3ミリ」は候補にする：{cv[:60]}")
        # 承認のあとに「元に戻して」と言う：承認と候補を両方取り消した決まった文を出し、画面からも候補が消える（唐川さんの決定）
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        pg.fill("#approver-name", "テスト")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        # 承認のあとの画面では入力欄を出さないので、画面と同じ API（/api/hear）で送る
        sid = list(srv.flow._entries)[-1]
        res = pg.request.post(url + "api/hear", data={"session_id": sid, "text": "元に戻して", "source": "voice"}).json()
        rm = res.get("message") or ""
        st = res.get("state") or {}
        check(rm.startswith("前の承認と変更候補を取り消しました。元のJOBは変更されていません。")
              and "変更候補は残しています" not in rm and st.get("candidate") is None and st.get("approval") is None,
              f"承認のあとの「元に戻して」：承認と候補を取り消した文を出し、状態からも候補と承認が消える：{rm[:60]!r}")
        ctx.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror（上下の設定なし・会話）: {e}"))
        pg.add_init_script(FAKE_RECOGNITION)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        talk_first(pg)
        until(pg, lambda: pg.get_attribute("#step-3", "aria-current") == "step")
        fill_text(pg, "置くところ")
        pg.click("#text-send")
        until(pg, lambda: labels(pg) == ["C00005 です", "ほかの位置"])
        fill_text(pg, "そうそう")
        pg.click("#text-send")
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00005")
        check(labels(pg) == ["X＋", "X−", "Y＋", "Y−", "Z＋", "Z−"],
              f"上下の設定なし：会話の方向のボタンに「（上）」「（下）」を付けない：{labels(pg)}")
        fill_text(pg, "下に3ミリ")
        pg.click("#text-send")
        until(pg, lambda: ut.is_updown_unset_ask(last_said(pg)))  # 会話の文（状態の文 MSG_UNCONFIRMED は変えていない）
        check("3mm下に動かしたいんですね。" in last_said(pg), f"上下の設定なし：会話の「下に3ミリ」は、言った量と向きを受け止める文：{last_said(pg)[:60]}")
        check(not pg.is_enabled("#open-confirm") and not any("（下）" in x for x in labels(pg)),
              "上下の設定なし：会話の「下に3ミリ」も同じ案内で聞き返す（候補にしない）")
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ---- UI 修正 11/4（唐川さんの依頼書 UI_FIX_REQUEST_9db4044・REVIEW_UI U1〜U4）：重要な操作をスクロールなしで見せる ----
# measure.js（依頼書に同梱）の考え方：要素の全体が画面の中にあり、上の帯にもフッターにも隠れない。
# ここではさらに、会話の欄（スクロールする入れ物）に切られていないこと、真ん中の点がほかの要素に覆われていないことも確かめる。
FOLD_JS = """(sels) => {
  const vh = innerHeight, vw = innerWidth;
  const top = document.querySelector('.top').getBoundingClientRect().bottom;
  const foot = vh - document.querySelector('.foot').getBoundingClientRect().top;
  const out = {};
  for (const s of sels) {
    const e = document.querySelector(s);
    if (!e || e.closest('[hidden]') || e.offsetParent === null) { out[s] = '出ていない'; continue; }
    const b = e.getBoundingClientRect();
    let why = '';
    if (b.width === 0 || b.height === 0) why = '大きさ0';
    // 上の帯の中の要素（状態のチップ。唐川さん承認のデザイン 11/4 で上の帯に置いた）は、帯の中で画面に入っていればよい
    else if (!e.closest('.top') && b.top < top - 0.5) why = `上の帯に隠れる（上 ${Math.round(b.top)}・帯 ${Math.round(top)}）`;
    else if (e.closest('.top') && b.top < -0.5) why = '画面の上に外れる';
    else if (b.bottom > vh - foot + 0.5) why = `フッターに隠れる・画面の外（下 ${Math.round(b.bottom)}・画面 ${Math.round(vh - foot)}）`;
    else if (b.left < 0 || b.right > vw) why = '横に外れる';
    for (let a = e.parentElement; a && !why; a = a.parentElement) {
      const o = getComputedStyle(a).overflowY;
      if (o === 'auto' || o === 'scroll' || o === 'hidden') {
        const c = a.getBoundingClientRect();
        if (b.top < c.top - 0.5 || b.bottom > c.bottom + 0.5) why = `入れ物（${a.id || a.className}）に切られる`;
      }
    }
    if (!why) {
      const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      if (!hit || !(hit === e || e.contains(hit) || hit.contains(e))) why = 'ほかの要素に覆われる';
    }
    out[s] = why ? 'NG ' + why : `OK ${Math.round(b.top)}-${Math.round(b.bottom)}`;
  }
  return out;
}"""
# ボタンの文字 14px 以上・高さ 44px 以上（見えているボタン。詳細表示の中と、吹き出しの中の根拠の行のリンク（explain.js が作る）は除く）
BUTTON_SIZE_JS = """() => {
  const bad = [];
  for (const e of document.querySelectorAll('button')) {
    if (e.closest('[hidden], #engineer-view, .bubble .evidence, details:not([open])') || e.offsetParent === null) continue;
    const b = e.getBoundingClientRect();
    if (b.width === 0 || b.height === 0 || b.bottom < 0 || b.top > innerHeight) continue;
    const fs = parseFloat(getComputedStyle(e).fontSize);
    if (fs < 14 || b.height < 43.5) bad.push(`${e.id || e.textContent.trim().slice(0, 10)}（${fs}px・高さ ${Math.round(b.height)}px）`);
  }
  return bad;
}"""
PAGE_SCROLL_JS = """() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight, innerWidth, innerHeight]"""
# 3D の大きさ：直す前（434165d）に同じ手順で測った値。これより小さくしない（候補のあとは、動きの並び・凡例の分だけ前から小さかった）
FIG_BEFORE = {(1280, 720): {"before_candidate": (689, 360), "candidate": (689, 230)},
              (1440, 900): {"before_candidate": (738, 492), "candidate": (738, 492)}}


# 自動のスクロールを止める（QA の測るスクリプト qa_measure.py の NOFOLLOW=1 より強くした。差し戻し QA P1-1）：
# 会話の欄（#side-scroll）を見張る仕組みを付けさせないうえ、画面のプログラムからのスクロール（scrollTop の書き込み・scrollTo・
# scroll・scrollBy・scrollIntoView）もすべて止める。人のスクロールは、マウスのホイール（ブラウザーそのもののスクロール）でまねる。
# これで合格すれば、画面は自動のスクロールに頼らずに重要な操作を見せている（前回 QA P2-1・P2-2）
NOFOLLOW_JS = """(() => { const MO = window.MutationObserver; window.MutationObserver = class extends MO {
  observe(t, o) { if (t && t.id === 'side-scroll') return; return super.observe(t, o); } };
  const d = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
  Object.defineProperty(Element.prototype, 'scrollTop', { configurable: true, get() { return d.get.call(this); }, set(v) {} });
  for (const k of ['scrollTo', 'scroll', 'scrollBy', 'scrollIntoView']) { Element.prototype[k] = function () {}; }
  window.scrollTo = function () {}; window.scroll = function () {}; window.scrollBy = function () {}; })();"""
# 範囲の確認：カードの要約（1回目・全体の行）と1回目の行が、会話の欄の中で切れずに見えるか（Safety P2-3：1回目の行は畳まない）
RANGE_TOGETHER_JS = """() => {
  const area = document.querySelector('#side-scroll').getBoundingClientRect();
  const inArea = (s) => { const e = document.querySelector(s); if (!e || e.offsetParent === null) return false;
    const r = e.getBoundingClientRect(); return r.top >= area.top - 0.5 && r.bottom <= area.bottom + 0.5; };
  return { where: inArea('#reply-message .range-where'), all: inArea('#reply-message .range-all'),
    lines: inArea('#reply-message .range-lines') && !document.querySelector('#reply-message .range-lines').closest('details') }; }"""
# 左の列から横にはみ出していないか（長い出力先：QA P2-1）
INSIDE_SIDE_JS = """(sels) => { const s = document.querySelector('.side').getBoundingClientRect(); const bad = [];
  for (const q of sels) { const e = document.querySelector(q); if (!e || e.offsetParent === null || !e.closest('.side')) continue;
    const r = e.getBoundingClientRect(); if (r.left < s.left - 0.5 || r.right > s.right + 0.5) bad.push(`${q} ${Math.round(r.left)}〜${Math.round(r.right)}（列 ${Math.round(s.left)}〜${Math.round(s.right)}）`); }
  return bad; }"""
LONG_TAIL = ("同じ順番の動きを何度も通る流れです。最初の位置と最後の位置は別の場所です。気になるところがあれば、位置の番号で教えてください。"
             "教えてもらった意味は、次の説明に使います。作業の名前が分かると、説明が短くなります。")


def check_above_fold(browser, check, shots: Path | None, errors: list, out: Path, log=print) -> dict:
    """S1〜S7（依頼書2章＋出力後）と、① 初期（大きなマイク）・② 会話・長い答え（約190字。前回 QA P1-3）を、
    1280×720・1440×900 で、重要な操作がスクロールなしで全部見えるか確かめる。自動のスクロールを止めた版（NF）でも同じに確かめる。
    人が会話の履歴を上へ戻しても、重要なボタン（下の操作の欄）が見えていることも確かめる（↑）。
    差し戻し（QA P1-1・P2-1）：位置を選ばずに質問する流れ（S2n・S2nL）、Chrome 以外の案内の帯が出ている状態（〜C）、
    長い出力先（S6P・S7P）も確かめる。表（OK/NG）を返す。
    モックの台本の答え（tools/demo_mock.py の ScriptClient。本物の Claude は呼ばない）を使う。"""
    sys.path.insert(0, str(REPO / "tools"))
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server

    class LongClient(dm.ScriptClient):
        """台本の答えに長い文を足す（前回 QA の srv.py long と同じ文。約190字）。"""
        def create(self, **kw):
            r = super().create(**kw)
            d = r.content[0].input
            if d.get("intent") == "explain":
                d["reply"] = d["reply"] + LONG_TAIL
            return r

    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    servers = []
    for client in (dm.ScriptClient(), LongClient()):
        ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=client, usage_log=cx.UsageLog(out / "fold_usage.jsonl"))
        s = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
    url = f"http://127.0.0.1:{servers[0].server_address[1]}/"
    url_long = f"http://127.0.0.1:{servers[1].server_address[1]}/"
    table: dict = {}
    head = ["#next-action"]  # Issue #7：ふだんの記録（#status-line）は上の帯に出さない
    entry = ["#text-input", "#text-send", "#mic-button"]

    def stage(pg, w, h, name, sels, fig_kind, extra_ok=True, extra_what="", tag=""):
        pg.wait_for_timeout(500)  # 吹き出しのふわっと出る動き・マイクの移る動きが終わってから測る
        r = pg.evaluate(FOLD_JS, sels)
        bad = {k: v for k, v in r.items() if not v.startswith("OK")}
        sw, sh, iw, ih = pg.evaluate(PAGE_SCROLL_JS)
        fig = pg.locator("#fig-3d").bounding_box()
        need = FIG_BEFORE[(w, h)][fig_kind]
        fig_ok = fig is not None and fig["width"] >= need[0] and fig["height"] >= need[1]
        small = pg.evaluate(BUTTON_SIZE_JS)
        outside = pg.evaluate(INSIDE_SIDE_JS, [s for s in sels if not s.startswith("#status-line")])
        ok = not bad and sw <= iw and sh <= ih and fig_ok and not small and extra_ok and not outside
        table[(tag + name, f"{w}x{h}")] = "OK" if ok else "NG"
        label = f"{tag}{name}（{w}×{h}）"
        check(not bad, f"{label}：{'・'.join(sels)} がスクロールなしで全部見える{'' if not bad else '：' + str(bad)}")
        check(sw <= iw and sh <= ih, f"{label}：ページに縦横のスクロールが出ない（{sw}×{sh}・画面 {iw}×{ih}）")
        check(not outside, f"{label}：左の列から横にはみ出さない：{outside}")
        check(fig_ok, f"{label}：3D は 434165d より小さくしない（{fig and round(fig['width'])}×{fig and round(fig['height'])}・"
                      f"434165d {need[0]}×{need[1]}）")
        check(not small, f"{label}：見えているボタンは文字 14px 以上・高さ 44px 以上：{small}")
        if extra_what:
            check(extra_ok, f"{label}：{extra_what}")
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(shots / f"FOLD_{tag.strip()}{w}x{h}_{name.split()[0]}.png"))

    def wheel_side(pg, dy):
        box = pg.locator("#side-scroll").bounding_box()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        pg.mouse.wheel(0, dy)
        pg.wait_for_timeout(350)

    def scrolled_up(pg, w, h, name, sels, tag):
        """人が会話の履歴をいちばん上まで（マウスのホイールで）戻しても、重要なボタン（下の操作の欄）は見えている。"""
        wheel_side(pg, -6000)
        top = pg.evaluate("() => Math.round(document.querySelector('#side-scroll').scrollTop)")
        r = pg.evaluate(FOLD_JS, sels)
        bad = {k: v for k, v in r.items() if not v.startswith("OK")}
        table[(tag + name + "↑", f"{w}x{h}")] = "OK" if not bad else "NG"
        check(not bad, f"{tag}{name}（{w}×{h}）：会話の履歴を上へ戻しても（scrollTop {top}）{'・'.join(sels)} が見える"
                       f"{'' if not bad else '：' + str(bad)}")
        if shots is not None:
            pg.screenshot(path=str(shots / f"FOLD_{tag.strip()}{w}x{h}_{name.split()[0]}_up.png"))
        wheel_side(pg, 6000)  # 下の端へ戻す（人の操作）

    def new_page(w, h, nofollow, other=False):
        ctx = browser.new_context(viewport={"width": w, "height": h})
        if nofollow:
            ctx.add_init_script(NOFOLLOW_JS)
        if other:
            ctx.add_init_script("window.__asOther = true")  # Chrome 以外（アプリの中のブラウザーなど）：案内の帯が出る
        ctx.add_init_script(FAKE_RECOGNITION)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        return pg

    def position_flow(w, h, nofollow, tag, long_path):
        """位置の変更（サンプル DEMO_C_PLACE → C00003 → 「Zプラス方向に10mm」→ 確認表示 → 承認 → 出力）。
        long_path：出力先のパスを約120字にする（QA P2-1。S6P・S7P だけを測る）"""
        p = "P" if long_path else ""
        pg = new_page(w, h, nofollow)
        pg.goto(url)
        pg.click("#sample-button")
        pg.wait_for_selector("#job-area:not([hidden])")
        until(pg, lambda: "アプリの記録：" in (pg.text_content("#app-info-status") or ""))  # Issue #7：ふだんはアプリ情報
        if not long_path:
            hero = "hero" in (pg.get_attribute("body", "class") or "")
            stl = pg.text_content("#status-line") or ""
            check(stl == "アプリの記録：変更の候補 なし／承認 まだ／出力 まだ" and pg.locator("#status-line .st-chip").count() == 3,
                  f"{tag}状態のチップ（{w}×{h}）：文はサーバーの状態の1行のまま、区切りごとに3つのチップ（{stl}）")
            stage(pg, w, h, "S1a 読込直後（①）", head + ["#mic-button", "#kbd-button"], "before_candidate",
                  hero and pg.is_hidden("#side-scroll") and pg.is_hidden("#text-input")
                  and pg.inner_text("#next-action") == "ロボットの動きについて話しかけてください",
                  "① 初期：大きなマイクとキーボードだけ（会話の履歴・入力欄は出さない）。見出しは「ロボットの動きについて話しかけてください」",
                  tag)
            mic = pg.locator("#mic-button").bounding_box()
            check(mic is not None and mic["width"] >= 120, f"{tag}① 初期（{w}×{h}）：マイクは大きい（{mic and round(mic['width'])}px）")
        pg.locator("#position-chips button", has_text="C00003").click()
        until(pg, lambda: pg.inner_text("#voice-position strong") == "C00003")
        if not long_path:
            sp = pg.inner_text("#sel-pos")
            stage(pg, w, h, "S1b 位置を選んだあと（①）", head + ["#mic-button", "#kbd-button", "#voice-position", "#sel-pos"],
                  "before_candidate", "C00003" in sp and "300.000" in sp and "変更前" not in sp,
                  f"3D の下に「選択中」の1行（値はファイルの行のまま。候補の前は「変更前」を付けない）：{sp}", tag)
        pg.click("#kbd-button")
        if not long_path:
            stage(pg, w, h, "S1k キーボードを押したあと（①）", head + entry, "before_candidate", tag=tag)
        pg.fill("#text-input", "Zプラス方向に10mm")
        pg.click("#text-send")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        if not long_path:
            change = pg.inner_text("#candidate-view .cand-change") if pg.locator("#candidate-view .cand-change").count() else ""
            ch = " ".join(change.split())
            sp = pg.inner_text("#sel-pos")
            s4 = head + ["#open-confirm", "#undo-button", "#candidate-view .cand-change", "#dock-actions .role-note", "#sel-pos"]
            stage(pg, w, h, "S4 候補表示", s4, "candidate",
                  "300.000" in ch and "310.000" in ch and "変更前" in sp,
                  f"Z の変更前→変更後が見え、3D の下の値には「変更前」と付く（{ch}／{sp}）", tag)
            scrolled_up(pg, w, h, "S4 候補表示", ["#open-confirm", "#undo-button"], tag)
            # Safety P2-1：再生中も上下の向きの表示が見える
            pg.click("#play-button")
            until(pg, lambda: pg.is_visible("#play-now"))
            r = pg.evaluate(FOLD_JS, ["#updown-detail", "#play-now"])
            ok = all(v.startswith("OK") for v in r.values()) and "上下の向き" in pg.inner_text("#updown-detail")
            table[(tag + "S4p 再生中の上下の向き", f"{w}x{h}")] = "OK" if ok else "NG"
            check(ok, f"{tag}S4p 再生中（{w}×{h}）：「いま」の帯と一緒に、上下の向きの表示も見える：{r}")
            if shots is not None:
                pg.screenshot(path=str(shots / f"FOLD_{tag.strip()}{w}x{h}_S4p.png"))
            pg.click("#play-stop")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        if not long_path:
            stage(pg, w, h, "S5 確認表示", ["#approve-button", "#approver-name"], "candidate", tag=tag)
        pg.fill("#approver-name", "テスト")
        pg.click("#approve-button")
        until(pg, lambda: pg.is_visible("#approval-status"))
        st = pg.inner_text("#approval-status")
        s6 = head + ["#export-button", "#undo-button", "#approval-status", "#approval-states", "#candidate-view .cand-change",
                     "#export-note", "#text-input", "#text-send"]
        od = pg.inner_text("#out-dir")
        stage(pg, w, h, f"S6{p} 承認後" + ("（長い出力先）" if long_path else ""), s6, "candidate",
              "テスト さんが承認しました" in st and pg.is_hidden("#mic-button") and (len(od) >= 100 if long_path else True),
              f"「テスト さんが承認しました」・3つの状態・変更前→変更後が出て、承認のあとは文字の欄だけ（🎤 は出さない）。出力先 {len(od)}字", tag)
        scrolled_up(pg, w, h, f"S6{p} 承認後", ["#export-button", "#undo-button", "#text-input", "#text-send"], tag)
        pg.click("#export-button")
        pg.wait_for_selector("#export-result:not([hidden])")
        s7 = head + ["#export-result", "#approval-status", "#undo-button", "#text-input", "#text-send"]
        stage(pg, w, h, f"S7{p} 出力後" + ("（長い出力先）" if long_path else ""), s7, "candidate",
              "出力しました" in pg.inner_text("#export-result"), "出力の結果が出る", tag)
        scrolled_up(pg, w, h, f"S7{p} 出力後", ["#export-result", "#undo-button", "#text-input", "#text-send"], tag)
        pg.close()

    def talk_flow(w, h, nofollow, tag, select, long, other):
        """回数の変更（DEMO_BUTTON_REPEAT → 〔C00001〕→ 質問 → 「じゃあ3回にして」→ 範囲の確認 → 候補）。
        select=False：位置を選ばずに質問する（docs/CHROME_CHECK_MOCK.md の手順1〜2。QA P1-1）。other：Chrome 以外の案内の帯"""
        name = "S2" + ("" if select else "n") + ("L" if long else "") + ("C" if other else "")
        what = ("位置を選んで" if select else "位置を選ばずに") + ("長い答え" if long else "質問") + ("・Chrome 以外の帯" if other else "")
        pg = new_page(w, h, nofollow, other)
        pg.goto(url_long if long else url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        if other:
            stage(pg, w, h, "S1aC 読込直後（①・Chrome 以外の帯）", head + ["#mic-button", "#kbd-button", "#browser-note"], "before_candidate",
                  pg.locator("#next-action").bounding_box()["height"] >= 20,
                  "① 初期：Chrome 以外の案内の帯が出ても、見出しがしぼまない（QA P3-1）", tag)
        if select:
            pg.locator("#position-chips button", has_text="C00001").click()
            until(pg, lambda: pg.inner_text("#voice-position strong") == "C00001")
        fill_text(pg, "このロボット、何してる？")
        pg.click("#text-send")
        pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
        last = "#ask-turns > li:last-child"
        said = pg.inner_text(f"{last} .said")
        sels = head + entry + [last] + (["#browser-note"] if other else [])
        stage(pg, w, h, f"{name} 会話のあと（{what}）", sels, "before_candidate",
              "hero" not in (pg.get_attribute("body", "class") or "") and pg.is_visible("#side-scroll") and (len(said) >= 150 if long else True),
              f"② 会話：最新の答え（{len(said)}字）の吹き出しの全体と、入力欄が見える", tag)
        if long or other or not select:
            pg.close()
            return
        fill_text(pg, "じゃあ3回にして")
        pg.click("#text-send")
        pg.wait_for_selector("#range-yes")
        tog = pg.evaluate(RANGE_TOGETHER_JS)
        rng = pg.locator("#status-line .st-chip.range")
        check(rng.count() == 1 and "範囲の確認中" in rng.inner_text() and pg.locator("#status-line .st-chip.on").count() == 0,
              f"{tag}S3（{w}×{h}）：状態のチップの「範囲の確認中」は青（範囲＝青。承認へ進むオレンジにしない。Safety P3-3）")
        stage(pg, w, h, "S3 範囲の確認", head + ["#range-yes", "#range-no", "#dock-range .range-button-note",
                                             "#reply-message .range-where", "#reply-message .range-lines", "#reply-message .range-all"],
              "before_candidate", tog["where"] and tog["all"] and tog["lines"],
              "範囲の中身の要約（1回目・全体）と1回目の命令の行（畳まない）と［この範囲を繰り返す］［違う］が同時に見える", tag)
        scrolled_up(pg, w, h, "S3 範囲の確認", ["#range-yes", "#range-no"], tag)
        pg.click("#range-yes")
        until(pg, lambda: pg.is_enabled("#open-confirm"))
        check(pg.locator("#dock-range button").count() == 0 and pg.is_hidden("#dock-range"),
              f"{tag}S3（{w}×{h}）：範囲の確認が終わると、操作の欄から範囲のボタンも消える（前の答えのボタンを残さない）")
        stage(pg, w, h, "S4r 回数の候補", head + ["#open-confirm", "#candidate-view .cand-change"], "candidate", tag=tag)
        pg.close()

    from voice_teaching import sim
    saved_out_dir = sim.DEFAULT_OUT_DIR
    try:
        for nofollow in (False, True):
            tag = "NF " if nofollow else ""
            for w, h in [(1280, 720), (1440, 900)]:
                nf = "_nf" if nofollow else ""
                sim.DEFAULT_OUT_DIR = out / f"fold_sim_{w}{nf}"  # 出力は一時フォルダ（同じ名前は上書きしない決まりのため分ける）
                position_flow(w, h, nofollow, tag, False)
                # 長い出力先（QA P2-1。約120字）
                sim.DEFAULT_OUT_DIR = out / f"fold_long_{w}{nf}" / ("長い出力先のフォルダ_" * 3 + "x" * 40) / "simulation_output_folder"
                position_flow(w, h, nofollow, tag, True)
                for select, long, other in [(True, False, False), (True, True, False), (False, False, False), (False, True, False),
                                            (False, False, True), (False, True, True)]:
                    talk_flow(w, h, nofollow, tag, select, long, other)
        check_hero_motion(browser, url, check, shots, errors)
    finally:
        sim.DEFAULT_OUT_DIR = saved_out_dir
        for s in servers:
            s.shutdown()
            s.server_close()
    names = sorted({k[0] for k in table}, key=lambda s: (s.startswith("NF"), s.replace("NF ", "").split()[0]))
    log("唐川さん承認のデザイン 11/4：スクロールなしで見えるか（S1〜S7・2つの画面の大きさ。NF＝自動のスクロールを止めた版。↑＝履歴を上へ戻したとき）")
    log("| 状態 | 1280x720 | 1440x900 |")
    for n in names:
        log(f"| {n} | {table.get((n, '1280x720'), '—')} | {table.get((n, '1440x900'), '—')} |")
    return table


def check_hero_motion(browser, url, check, shots: Path | None, errors: list) -> None:
    """① 初期 → ② 会話の切り替え（唐川さん承認のデザイン 11/4）：
    - 最初の送信で、大きなマイクが小さくなりながら入力欄の横へ移る（FLIP の動き）。動きを減らす設定のときは動かさない
    - 録音中は「聞いている」間だけ波形が動く（声の大きさは使わない）。1回話すと文は入力欄に入るだけ（自動で送らない）
    - 「会話をやり直す」で、候補・範囲の確認・会話の途中が無ければ ① に戻る。別のデータを読んだら ① に戻る"""
    for reduced in (False, True):
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce" if reduced else "no-preference")
        ctx.add_init_script(FAKE_RECOGNITION)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.goto(url)
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")

        def body():
            return pg.get_attribute("body", "class") or ""

        check("hero" in body(), f"① 初期（動きを減らす={reduced}）：読み込んだあとは大きなマイクの画面")
        if not reduced:
            pg.evaluate("() => { document.querySelector('#voice-mode-browser').checked = true; }")  # 同意ずみ（詳細表示の方式）
            pg.evaluate("window.__fakeText = 'このロボット、何してる？'")
            pg.click("#mic-button")
            until(pg, lambda: "listening" in body())
            anim = pg.evaluate("() => getComputedStyle(document.querySelector('.mic-wave i')).animationName")
            check(anim not in ("", "none"), f"録音中：「聞いている」間は波形が動く（{anim}）")
            if shots is not None:
                pg.screenshot(path=str(shots / "HERO_1c_listening.png"))
            pg.click("#voice-stop")
            until(pg, lambda: pg.input_value("#text-input") == "このロボット、何してる？")
            anim2 = pg.evaluate("() => getComputedStyle(document.querySelector('.mic-wave i')).animationName")
            check("listening" not in body() and anim2 in ("", "none") and pg.is_visible("#text-input") and "hero" in body()
                  and pg.locator("#ask-turns > li").count() == 0,
                  f"1回話すと波形は止まり（{anim2}）、聞き取った文は入力欄に入るだけ（自動で送らない。① のまま）")
            if shots is not None:
                pg.screenshot(path=str(shots / "HERO_1d_heard.png"))
        else:
            fill_text(pg, "このロボット、何してる？")
        before = pg.locator("#mic-button").bounding_box()
        pg.click("#text-send")
        pg.wait_for_timeout(40)
        running = pg.evaluate("() => document.querySelector('#mic-button').getAnimations().length")
        if shots is not None and not reduced:
            for k in range(4):
                pg.screenshot(path=str(shots / f"HERO_1e_move{k}.png"))
                pg.wait_for_timeout(80)
        pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
        pg.wait_for_timeout(500)
        after = pg.locator("#mic-button").bounding_box()
        check("hero" not in body() and after["width"] < before["width"] and pg.is_visible("#side-scroll"),
              f"② 会話（動きを減らす={reduced}）：最初の送信で、マイクは小さくなって入力欄の横へ（{round(before['width'])}→{round(after['width'])}px）")
        check((running >= 1) if not reduced else (running == 0),
              f"マイクの移る動き：{'FLIP の動きがある' if not reduced else '動きを減らす設定では動かさない'}（動き {running}個）")
        if shots is not None and not reduced:
            pg.screenshot(path=str(shots / "HERO_2_conversation.png"))
        # 「会話をやり直す」：候補・範囲の確認・会話の途中が無いので ① に戻る
        if not pg.is_visible("#ask-reset"):
            pg.click(".talk-more > summary")  # 「詳しく」を開く（開いていれば、そのまま）
        pg.click("#ask-reset")
        until(pg, lambda: "hero" in body())
        check("hero" in body() and pg.is_hidden("#side-scroll"), f"会話をやり直す（動きを減らす={reduced}）：① 初期の画面に戻る")
        # 別のデータを読んだ → ① に戻る
        fill_text(pg, "このロボット、何してる？")
        pg.click("#text-send")
        pg.wait_for_selector("#ask-turns .bubble.claude:not(.thinking)")
        pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI"))
        until(pg, lambda: pg.inner_text("#file-name") == "DEMO_C_PLACE.JBI")
        until(pg, lambda: "hero" in body())
        check("hero" in body() and pg.locator("#ask-turns > li").count() == 0,
              f"別のデータを読んだら ① 初期の画面に戻る（動きを減らす={reduced}）")
        # 聞き返しのあとに「会話をやり直す」：候補・範囲の確認・会話の途中が無いので、前の聞き返しも消して ① に戻る（QA P3-2）
        fill_text(pg, "少し動かして")
        pg.click("#text-send")
        until(pg, lambda: pg.is_visible("#reply-message"))
        if not pg.is_visible("#ask-reset"):
            pg.click(".talk-more > summary")  # 「詳しく」を開く（開いていれば、そのまま）
        pg.click("#ask-reset")
        until(pg, lambda: "hero" in body())
        check("hero" in body() and pg.is_hidden("#reply-message"),
              f"聞き返しのあとに「会話をやり直す」を押すと、前の聞き返しを消して ① 初期の画面に戻る（動きを減らす={reduced}）")
        ctx.close()


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
    # Issue #7：最初の画面の印は短い「SIM」。名前（読み上げ・小さな説明）に正式な名前と「実機への出力・転送・ロボットの起動はしません」
    check(pg.is_visible("#mode-banner .mb-short") and pg.inner_text("#mode-banner .mb-short") == "SIM"
          and "シミュレーションモード" in (pg.get_attribute("#mode-banner", "aria-label") or "")
          and "実機への出力・転送・ロボットの起動はしません" in (pg.get_attribute("#mode-banner", "aria-label") or "")
          and "実機への出力・転送・ロボットの起動はしません" in (pg.get_attribute("#mode-banner", "title") or ""),
          "最初の画面にも、シミュレーションモード（SIM）と「実機への出力・転送・ロボットの起動はしません」を出す")
    shot(pg, "1_start.png")

    data = (REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()
    dt = pg.evaluate_handle(DROP_FILE, ["DEMO_C_PLACE.JBI", base64.b64encode(data).decode("ascii")])
    pg.dispatch_event("#drop-zone", "drop", {"dataTransfer": dt})
    pg.wait_for_selector("#job-area:not([hidden])")
    check(pg.get_attribute("#step-2", "aria-current") == "step", "ドロップで読み込むと、ステップ2（位置を選ぶ）になる")
    check(pg.get_attribute('.view-tab[data-view="3d"]', "aria-selected") == "true" and pg.is_visible("#fig-3d svg"),
          "読み込むと、最初に 3D を大きく出す")
    # Issue #8（11/6 唐川さん）：解釈の印は上の帯に出さない。モックであることはアプリ情報（サーバーの値のまま）で区別できる
    check(pg.is_hidden("#interp-badge") and pg.text_content("#app-info-interp") == "解釈：モック",
          f"解釈のしかたは上の帯に出さず、アプリ情報で区別：{pg.text_content('#app-info-interp')}")
    # 入口は1つ（11/4 唐川さんの指示③）：ステップ2でも、指示の欄（会話の入口）を出す（「何をしている？」を位置を選ぶ前に聞ける）
    check(cards(pg) == ["select-panel", "instruction-panel"], f"右側は今のステップのカードと、入口（指示の欄）：{cards(pg)}")
    # Issue #7・#8：短い1行（目安・実機の動作と干渉は保証しない）をいつも出し、全文（本体の動きではない・当たらないとは限らない）は ⓘ で開ける
    short = pg.inner_text("#figure-short") if pg.is_visible("#figure-short") else ""
    full = pg.text_content("#figure-panel .figure-notes") or ""
    check(all(x in short for x in ["目安", "実機の動作・干渉は保証しません"]) and len(short) <= 30
          and all(x in full for x in ["ロボット本体の動きではありません", "当たらないとは限りません", "動作や干渉を保証するシミュレーターではありません"]),
          f"図の注意：短い1行（目安・実機の動作と干渉は保証しない）はいつも見え、全文（本体の動きではない・当たらないとは限らない）は ⓘ の中にある（{short!r}）")
    pg.click(".fig-notes-more > summary")
    check(all(pg.is_visible(s) for s in ["#figure-note", "#play-note", "#view3d-note"]),
          "図の注意の全文（ロボット本体・関節は描かない・動作や干渉を保証しない）は ⓘ で読める")
    pg.click(".fig-notes-more > summary")

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

    # 回転・拡大縮小・正面に戻す（本番UI：「正面に戻す」は 3D のときいつも出す。ミニマル確定案）
    check(pg.is_visible("#view-reset") and pg.get_attribute("#view-reset", "aria-label") == "正面に戻す"
          and "ドラッグで回す" in pg.text_content("#fig-3d svg"),
          "最初から「正面に戻す」を出し、図の中に薄く「ドラッグで回す・ホイールで拡大」を出す")
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
    check(pos_of(pg, "C00005") == a0 and pg.is_visible("#view-reset"), "「正面に戻す」で最初の向き・大きさに戻る（ボタンは出たまま）")
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
    # 右側の文字の量は、操作のカード（会話の欄は PC 版の画面で右側に移したので数えない）
    # ① 初期の画面（唐川さん承認のデザイン 11/4）：入力欄はキーボードのボタンを押すと出る（人と同じように押してから数える）
    if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
        pg.click("#kbd-button")
    side = len("".join(pg.evaluate("""() => { const tips = [...document.querySelectorAll('button.tip')]; tips.forEach((b) => { b.style.display = 'none'; });
            const s = [...document.querySelectorAll('.side > :not(#ask-panel):not(#status-line):not(#side-scroll), #side-scroll > :not(#ask-panel)')]
        .filter((e) => e.offsetParent !== null).map((e) => e.innerText).join('');
            tips.forEach((b) => { b.style.display = ''; }); return s; }""").split()))
    check(all(pg.is_visible(s) for s in ["#voice-position", "#mic-button", "#text-input", "#text-send"])
          and pg.inner_text("#text-send") == "送る" and side <= 80,
          f"音声のカードは「位置・マイク・入力欄・送る」（右側の文字 {side}文字。80文字まで。状態の1行は数えない）")
    check(pg.is_hidden("#heard-text") and pg.is_hidden("#work-log") and pg.is_hidden("#voice-notice"),
          "聞き取った文字・ログ・音声の方式の説明は詳細表示の中")
    shot(pg, "3_selected.png")

    # 初めて 🎤：同意を聞いてから聞き始める。通信の失敗は聞き直さず、必ず OFF（11/3 夜の決定）
    pg.evaluate("window.__fails = ['network']")
    pg.click("#mic-button")
    check(pg.is_visible("#voice-consent") and primaries(pg) == 1
          and "primary" in (pg.get_attribute("#consent-yes", "class") or ""),
          "初めて 🎤 を押すと同意を聞く（「使う」だけが目立つ）")
    shot(pg, "3_consent.png")
    pg.click("#consent-yes")
    until(pg, lambda: pg.is_visible("#voice-error"))
    err = pg.inner_text("#voice-error")
    pg.wait_for_timeout(1500)
    check(pg.inner_text("#mic-label") == "マイク OFF" and not pg.is_enabled("#voice-stop")
          and "接続" in err and "もう一度 🎤" in err and "文字で入力" in err and pg.is_enabled("#text-input"),
          f"通信の失敗は聞き直さずに OFF にして、もう一度 🎤 と文字入力を案内：{' / '.join(err.splitlines())[:60]}")
    shot(pg, "3a_voice_error.png")

    # 声が無いまま止まる（no-speech）：聞き直さずに OFF。もう一度 🎤 を押すよう案内する
    pg.evaluate("window.__fails = ['no-speech', 'no-speech']")
    pg.click("#mic-button")
    until(pg, lambda: pg.is_visible("#voice-error"))
    pg.wait_for_timeout(1500)
    check(pg.inner_text("#mic-label") == "マイク OFF" and not pg.is_enabled("#voice-stop")
          and pg.evaluate("window.__fails.length") == 1 and "もう一度 🎤" in pg.inner_text("#voice-error"),
          "声を聞き取れなかったときも、聞き直さずに OFF（失敗は1回分だけ使った）")
    pg.evaluate("window.__fails = []")

    # マイクが許可されていない：どこで許可するかを出す
    pg.evaluate("window.__fails = ['not-allowed']")
    pg.click("#mic-button")
    until(pg, lambda: pg.is_visible("#voice-error"))
    check("許可" in pg.inner_text("#voice-error") and pg.inner_text("#mic-label") == "マイク OFF",
          "マイクが許可されていないときは、許可の場所を案内して OFF")

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
    check(pg.inner_text("#mic-label") == "マイク OFF" and pg.get_attribute("#mic-button", "aria-pressed") == "false"
          and pg.is_enabled("#text-input")
          and "primary" in (pg.get_attribute("#text-send", "class") or "") and primaries(pg) == 1,
          "話し終わると認識文が入力欄に入り、マイクは OFF（確かめて「次へ」）")
    pg.wait_for_timeout(800)
    check(pg.input_value("#text-input") == FAKE_TEXT and pg.is_hidden("#reply-message")
          and pg.get_attribute("#step-3", "aria-current") == "step", "自動では送らない（入力欄の文を直せる）")

    fill_text(pg, "")
    pg.wait_for_timeout(1000)
    check(not pg.is_enabled("#voice-stop") and pg.is_enabled("#text-input"),
          "入力欄を消しても、勝手に聞き始めない（文字で打ち直せる）")
    # 入力欄の文を直して送る → 聞き返し → マイクは OFF のまま（自動では聞かない）。次は人が 🎤 を押す
    fill_text(pg, "少し動かして")
    pg.click("#text-send")
    until(pg, lambda: pg.is_visible("#reply-message"))
    reply = pg.inner_text("#reply-message")
    check(pg.get_attribute("#step-3", "aria-current") == "step" and pg.is_visible("#reply-message .friendly strong"),
          f"はっきりしない指示には、次にすることを出す：{' / '.join(reply.splitlines())[:60]}")
    check(pg.inner_text("#next-action") == "もう一度、言い直してください",
          f"選択肢の無い聞き返しの見出しは「もう一度、言い直してください」：{pg.inner_text('#next-action')}")
    shot(pg, "3b_ask.png")
    pg.wait_for_timeout(1500)
    check(not pg.is_enabled("#voice-stop") and pg.inner_text("#mic-label") == "マイク OFF",
          "結果が出ても、マイクは自動では ON にならない（次は人が 🎤 を押す）")
    pg.click("#mic-button")  # 2回目の指示：人が 🎤 を押す
    until(pg, lambda: pg.is_enabled("#voice-stop"))
    check("聞いています" in pg.inner_text("#mic-label"), "もう一度 🎤 を押すと、また聞く（2回目の指示）")
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
    check("目安" in pg.inner_text("#figure-short") and "ロボット本体の動きではありません" in pg.text_content("#play-note"),
          "再生の限界を出す（いつも見える短い1行は「目安」・ⓘ の全文に「ロボット本体の動きではない」）")
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
    check(cards(pg) == ["select-panel", "instruction-panel"] and pg.get_attribute("#step-2", "aria-current") == "step",
          "位置の「変える」で、位置のボタンに戻れる（入口の欄は出たまま）")
    pg.locator("#position-chips button", has_text="C00000").click()
    until(pg, lambda: pg.inner_text("#voice-position strong") == "C00000")
    fill_text(pg, FAKE_TEXT)
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
