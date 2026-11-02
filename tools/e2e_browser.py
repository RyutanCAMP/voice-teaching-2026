"""ブラウザーでの通し確認（Playwright・同梱の Chromium）。実マイク・実 API は使わない。

偽の SpeechRecognition を注入し、「この位置を上に10mm」を final に返させて、
入力欄に入る（自動では送らない）→ 人の操作として「指示を送る」→ 確認表示 → 承認 → 合成JOBの出力 まで通す。
あわせて、録音中に承認・出力ボタンが押せないこと、取消で入力欄が変わらないこと、
SpeechRecognition が無いブラウザーでの表示を確かめる。

使い方:  python tools/e2e_browser.py        （playwright が無ければ何もせず終わる）
出力は一時フォルダに書く（output/ は使わない）。
"""
from __future__ import annotations

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
    srv = server.make_server(port=0, interpreter=MockInterpreter())
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
            check(pg.is_visible("#voice-hint") and "＋として扱います" in pg.inner_text("#voice-hint"),
                  "声から入った文に軸の向きが無ければ、送る前に「＋として扱います」と知らせる")
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

            # 4ステップの画面（DEMO_C_PLACE をドラッグ＆ドロップ）
            check_four_steps(b, url, check, shots)

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
            pg3.evaluate("window.__fails = ['network']")
            pg3.click("#mic-button")
            check("提供元のサーバー" in pg3.inner_text("#consent-text"), "Chrome 以外の同意の文は、送り先を決めつけない")
            pg3.click("#consent-yes")
            until(pg3, lambda: pg3.is_visible("#voice-error"))
            check("Google Chrome で開いてください" in pg3.inner_text("#voice-error")
                  and pg3.inner_text("#mic-label") == "マイク OFF",
                  "Chrome 以外で通信に失敗したら、聞き直さずに Chrome で開くよう案内する")
            pg3.close()

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


DROP_FILE = """
([name, b64]) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], name));
  return dt;
}
"""


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
    check(primaries(pg) == 1 and buttons(pg) <= 2, f"最初の画面：目立つものは1つ（ボタン {buttons(pg)}個）")
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

    # 回転・拡大縮小・視点を戻す
    a0 = pos_of(pg, "C00005")
    box = pg.locator("#fig-3d").bounding_box()
    cx, cy = box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.85
    pg.mouse.move(cx, cy); pg.mouse.down(); pg.mouse.move(cx + 120, cy - 40, steps=8); pg.mouse.up()
    a1 = pos_of(pg, "C00005")
    check(a0 != a1, f"ドラッグで視点が回る（C00005 の画面の位置 {a0} → {a1}）")
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
    check(pos_of(pg, "C00005") == a0, "「視点を戻す」で最初の向き・大きさに戻る")

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
    check("押すと OFF" in pg.inner_text("#mic-label") and pg.is_enabled("#text-input")
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
    check("C00005" in cand and "200.000 → 210.000" in cand, f"右のカードに変更の要点：{' '.join(cand.split())[:40]}…")
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
