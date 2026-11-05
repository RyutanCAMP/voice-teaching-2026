"""本番 UI の撮影の流れを、モックのサーバーと偽の音声認識で通し、場面ごとのスクリーンショットを撮る（API・実マイクは使わない）。

    python tools/shoot_filming.py --out output/filming-shots [--width 1920 --height 1080]

流れ：起動（オープニング）→ JOB 読込 → 3D・位置選択 → マイクで話す →「Zプラス方向に30ミリ」（上限で止める）→
「Zプラス方向に5ミリ」（候補）→ 差分の確認 → 画面で承認 → 出力。承認・出力は一時フォルダへのシミュレーション出力だけ。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import e2e_browser as E  # noqa: E402


def serve(out: Path):
    import demo_mock as dm
    from voice_teaching import claude_explain as cx
    from voice_teaching import sim
    from voice_teaching.claude_interpreter import MockInterpreter
    from voice_teaching.web import server
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    sim.DEFAULT_OUT_DIR = out / "sim"
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def speak(pg, text):
    pg.evaluate("t => { window.__fakeText = t; }", text)
    pg.click("#mic-button")
    if pg.is_visible("#consent-yes"):
        pg.click("#consent-yes")
    E.until(pg, lambda: pg.is_enabled("#voice-stop"))
    pg.click("#voice-stop")
    E.until(pg, lambda: pg.input_value("#text-input") == text)
    pg.click("#text-send")
    E.until(pg, lambda: pg.is_enabled("#text-send") or not pg.is_visible("#text-send"), 40000)
    pg.wait_for_timeout(700)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "output" / "filming-shots"))
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--job", default="DEMO_C_PLACE.JBI")
    ap.add_argument("--position", default="C00005")
    ap.add_argument("--url", default=None, help="起動中のサーバー（例 http://127.0.0.1:8835/）。無ければモックのサーバーを自分で起動する")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vt-shoot-"))
    srv, url = (None, a.url) if a.url else serve(tmp)
    from playwright.sync_api import sync_playwright
    w, h = a.width, a.height
    shot = lambda pg, name: pg.screenshot(path=str(out / f"{w}x{h}_{name}.png"))  # noqa: E731
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.add_init_script(E.FAKE_RECOGNITION)
            pg.add_init_script("try { localStorage.setItem('vt-voice-mode', 'browser'); } catch (_) {}")
            pg.goto(url + "?opening=1")
            pg.wait_for_timeout(1200)
            shot(pg, "00_opening")
            pg.wait_for_timeout(3500)
            shot(pg, "01_start")
            pg.set_input_files("#file-input", str(REPO / "samples/dummy" / a.job))
            pg.wait_for_selector("#job-area:not([hidden])")
            pg.wait_for_timeout(900)
            shot(pg, "02_loaded")
            pg.locator("#position-chips button", has_text=a.position).first.click()
            pg.wait_for_timeout(700)
            shot(pg, "03_selected")
            speak(pg, "Zプラス方向に30ミリ")
            shot(pg, "04_refused_30mm")
            speak(pg, "Zプラス方向に5ミリ")
            pg.wait_for_timeout(600)
            shot(pg, "05_candidate")
            if pg.is_visible("#open-confirm") and pg.is_enabled("#open-confirm"):
                pg.click("#open-confirm")
                pg.wait_for_timeout(700)
            shot(pg, "06_confirm")
            if pg.is_visible("#approver-name"):
                pg.fill("#approver-name", "自動確認（シクロ）")
                if pg.is_enabled("#approve-button"):
                    pg.click("#approve-button")
                    pg.wait_for_timeout(900)
            shot(pg, "07_approved")
            if pg.is_visible("#export-button") and pg.is_enabled("#export-button"):
                pg.click("#export-button")
                pg.wait_for_timeout(900)
            shot(pg, "08_exported")
            br.close()
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
    print("shots:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
