"""Compare the legacy E2E playback wait against pinned baseline/UI (mock only)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright
from tools.check_filming_main import BASE
from tools.e2e_browser import FAKE_RECOGNITION
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.server import make_server


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    srv = make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    rows = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            for variant in ("baseline", "ui"):
                for attempt in range(2):
                    page = browser.new_page(viewport=dict(width=1280, height=900))
                    page.add_init_script(FAKE_RECOGNITION)
                    if variant == "baseline":
                        for name in ("index.html", "style.css", "highlight.js", "explain.js"):
                            content = subprocess.check_output(["git", "show", f"{BASE}:voice_teaching/web/static/{name}"], cwd=ROOT)
                            pattern = url if name == "index.html" else "**/static/" + name
                            ct = "text/html" if name.endswith("html") else "text/css" if name.endswith("css") else "text/javascript"
                            page.route(pattern, lambda route, request, body=content, ct=ct: route.fulfill(body=body, content_type=ct))
                    page.goto(url)
                    if page.locator("#opening").is_visible():
                        page.locator("#op-skip").click()
                    page.set_input_files("#file-input", str(ROOT / "samples/dummy/DEMO_BUTTON_REPEAT.JBI"))
                    page.locator("#job-area:not([hidden])").wait_for(state="visible")
                    page.locator('.view-tab[data-view="3d"]').click()
                    page.locator("#play-button").click()
                    seen, states = False, []
                    for tick in range(100):
                        val = page.locator("#play-now").text_content()
                        if not states or states[-1]["now"] != val:
                            states.append(dict(tick=tick, now=val, status=page.locator("#play-status").text_content()))
                        if "22行目" in val:
                            seen = True
                            break
                        page.wait_for_timeout(100)
                    rows.append(dict(variant=variant, attempt=attempt+1, line22_seen=seen, states=states))
                    page.screenshot(path=str(out / f"{variant}-{attempt+1}.png"))
                    page.close()
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    result = dict(base=BASE, real_api_calls=0, cases=rows)
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
