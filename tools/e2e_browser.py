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
class FakeRecognition {
  constructor() { window.__rec = this; this.processLocally = false; }
  start() { setTimeout(() => {
    this.onstart && this.onstart();
    this.onresult && this.onresult({ results: [{ 0: { transcript: "この位置を" }, isFinal: false }] });
  }, 30); }
  stop() { setTimeout(() => {
    this.onresult && this.onresult({ results: [{ 0: { transcript: window.__fakeText }, isFinal: true }] });
    this.onend && this.onend();
  }, 30); }
  abort() {}
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
            until(pg, lambda: pg.inner_text("#selected-name") == "C00004")
            check(not pg.is_enabled("#mic-button"), "処理方法を選ぶまでマイクのボタンは使えない")
            pg.check("#voice-mode-browser")
            check(pg.is_enabled("#mic-button"), "処理方法を選ぶとマイクのボタンが使える")

            # 取消：入力欄は変わらない
            pg.fill("#text-input", "手で書いた文")
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-cancel"))
            until(pg, lambda: pg.inner_text("#voice-partial") == "この位置を")
            check(all(not pg.is_enabled(s) for s in ["#file-input", "#text-send", "#text-input"]),
                  "録音中は読込・入力・指示を送るが使えない")
            check(pg.is_enabled("#voice-stop") and pg.is_enabled("#voice-cancel"), "録音中は終了と取消が使える")
            pg.click("#voice-cancel")
            until(pg, lambda: not pg.is_enabled("#voice-cancel"))
            check(pg.input_value("#text-input") == "手で書いた文", "取消で入力欄が変わらない")
            check(pg.inner_text("#voice-partial") == "", "取消で未確定の文が消える")

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

            # 承認の後の録音中は、承認・出力が押せない
            pg.click("#mic-button")
            until(pg, lambda: pg.is_enabled("#voice-cancel"))
            check(not pg.is_enabled("#export-button") and not pg.is_enabled("#approve-button"),
                  "録音中は承認・出力のボタンが使えない")
            pg.click("#voice-cancel")
            until(pg, lambda: pg.is_enabled("#export-button"))

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
    """4ステップの画面：ドラッグ＆ドロップ → 位置を選ぶ → 指示 → 変更前後の再生 → 確認表示（承認はしない）。"""
    import base64

    def shot(page, name):
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / name), full_page=not name.startswith("6_"))  # 確認表示は画面の範囲だけ

    # ドロップのデータを作るのに evaluate を使うので、この確認のページだけ CSP を外す（画面のファイルは変えない）
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, bypass_csp=True)
    pg = ctx.new_page()
    pg.add_init_script(FAKE_RECOGNITION)
    pg.goto(url)
    check(pg.get_attribute("#step-1", "aria-current") == "step" and pg.is_visible("#drop-zone")
          and "primary" in (pg.get_attribute("#drop-zone", "class") or ""),
          "最初の画面：ステップ1が今のステップで、ドロップの枠が目立つ")
    shot(pg, "1_start.png")

    data = (REPO / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()
    dt = pg.evaluate_handle(DROP_FILE, ["DEMO_C_PLACE.JBI", base64.b64encode(data).decode("ascii")])
    pg.dispatch_event("#drop-zone", "drop", {"dataTransfer": dt})
    pg.wait_for_selector("#job-area:not([hidden])")
    check(pg.get_attribute("#step-2", "aria-current") == "step", "ドロップで読み込むと、ステップ2（位置を選ぶ）になる")
    check(pg.get_attribute('.view-tab[data-view="yz"]', "aria-selected") == "true",
          "X が一定の DEMO_C_PLACE では、最初に Y・Z の図を大きく出す")
    shot(pg, "2_loaded.png")

    pg.locator('#fig-main g.pt[data-name="C00005"] circle').last.click()  # 点そのもの（丸）を押す
    until(pg, lambda: pg.inner_text("#selected-name") == "C00005")
    check(pg.get_attribute("#step-3", "aria-current") == "step", "図の点を押すと位置が選ばれ、ステップ3になる")
    pg.check("#voice-mode-browser")
    check("primary" in (pg.get_attribute("#mic-button", "class") or ""), "処理方法を選ぶと、マイクのボタンが目立つ")
    shot(pg, "3_selected.png")

    pg.click("#mic-button")
    until(pg, lambda: pg.is_enabled("#voice-stop"))
    pg.click("#voice-stop")
    until(pg, lambda: pg.input_value("#text-input") == FAKE_TEXT)
    check("primary" in (pg.get_attribute("#text-send", "class") or ""), "認識文が入ると「指示を送る」が目立つ")
    pg.click("#text-send")
    until(pg, lambda: pg.get_attribute("#step-4", "aria-current") == "step")
    until(pg, lambda: "変更前" in pg.inner_text("#play-status") or "変更後" in pg.inner_text("#play-status"))
    check(True, f"候補ができると、変更前後の経路の再生が始まる：{pg.inner_text('#play-status')}")
    cand = pg.inner_text("#candidate-view")
    check("C00005" in cand and "200.000 → 210.000" in cand, f"右のカードに変更の要点：{cand[:40]}…")
    pg.wait_for_timeout(1500)
    shot(pg, "4_playing.png")
    until(pg, lambda: "終わりました" in pg.inner_text("#play-status"), timeout_ms=60000)
    check("ロボット本体の動きではありません" in pg.inner_text("#play-note"), "再生の限界（ロボット本体の動きではない）を出す")

    pg.click('.view-tab[data-view="xz"]')
    check(pg.get_attribute('.view-tab[data-view="xz"]', "aria-selected") == "true", "図の向きを切り替えられる")
    pg.click('.view-tab[data-view="yz"]')

    check(pg.is_hidden("#engineer-view"), "エンジニア表示は最初は閉じている")
    pg.click("#engineer-toggle")
    check(pg.is_visible("#engineer-view") and "C00005" in pg.inner_text("#work-log"),
          "エンジニア表示を開くと、作業ログ・表が同じ画面に出る")
    shot(pg, "5_engineer.png")
    pg.click("#engineer-toggle")

    pg.click("#open-confirm")
    pg.wait_for_selector("#confirm-overlay:not([hidden])")
    check(pg.is_visible("#confirm-figure svg") and pg.is_visible("#approve-button"),
          "全画面の確認表示に、図と承認ボタンがある（承認はボタンだけ）")
    shot(pg, "6_confirm.png")
    pg.click("#confirm-close")
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
