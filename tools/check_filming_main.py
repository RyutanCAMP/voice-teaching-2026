"""Chrome UI regression. Synthetic JOB + existing mock clients only; no live API/mic.

python tools/check_filming_main.py --out output/filming-main/check
python tools/check_filming_main.py --serve  # isolated mock preview, ephemeral port
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voice_teaching import claude_explain as cx
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.server import make_server
from tools.demo_mock import MOCK_MODEL, ScriptClient
from tools.e2e_browser import FAKE_RECOGNITION

BASE = "63577a6ba29f8d8fd48c75a9fd2522cf795a0828"


def server(out):
    out.mkdir(parents=True, exist_ok=True)
    cx.PRICES[MOCK_MODEL] = cx.Price(input=0, output=0, cache_write_5m=0, cache_write_1h=0, cache_read=0)
    # No dotenv loader or real SDK client. Only this in-process ScriptClient runs.
    return make_server(port=0, interpreter=MockInterpreter(), updown_config=None,
                       explainer=cx.Explainer("mock-only-no-network", MOCK_MODEL, client=ScriptClient(),
                                             usage_log=cx.UsageLog(out / "mock_usage.jsonl")))


def run(out):
    from playwright.sync_api import sync_playwright
    srv = server(out)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    checks, errors, posts, external = [], [], [], []

    def check(name, value, detail=None):
        checks.append(dict(name=name, passed=bool(value), detail=detail))

    def reachable(page, selector):
        loc = page.locator(selector)
        if not loc.is_visible():
            return False
        loc.scroll_into_view_if_needed()
        return loc.evaluate("""e => {
          const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
          const hit=document.elementFromPoint(x,y);
          return x>=0 && y>=0 && x<innerWidth && y<innerHeight && (hit===e || e.contains(hit));
        }""")

    def send(page, text):
        if not page.locator("#text-input").is_visible():
            page.locator("#kbd-button").click()
        page.locator("#text-input").fill(text)
        with page.expect_response("**/api/talk"):
            page.locator("#text-send").click()
        page.wait_for_function("() => !document.querySelector('#text-send').disabled")

    def load(page):
        page.goto(url)
        if page.locator("#opening").is_visible():
            page.locator("#op-skip").click()
        page.set_input_files("#file-input", str(ROOT / "samples/dummy/DEMO_C_PLACE.JBI"))
        page.locator("#file-name", has_text="DEMO_C_PLACE.JBI").wait_for(state="visible")
        page.wait_for_function("() => !document.querySelector('#mic-button').disabled")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            chrome_version = browser.version
            for width, height in [(1920, 1080), (1280, 720), (390, 844)]:
                page = browser.new_page(viewport=dict(width=width, height=height), reduced_motion="reduce")
                page.set_default_timeout(10000)
                # The existing E2E SpeechRecognition double never opens a mic.
                page.add_init_script(FAKE_RECOGNITION)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("request", lambda r: posts.append(r.url.split("/")[-1]) if r.method == "POST" else None)
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url)
                           else (external.append(route.request.url), route.abort()))
                prefix = str(width) + ": "
                load(page)
                page.screenshot(path=str(out / f"{width}-microphone.png"), full_page=width < 1100)
                check(prefix + "no page horizontal overflow", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                check(prefix + "hero mic large and reachable", reachable(page, "#mic-button") and page.locator("#mic-button").bounding_box()["width"] >= 120)
                check(prefix + "text alternative has visible words", "文字で入力" in page.locator("#kbd-button").inner_text())
                check(prefix + "model folded initially", not page.locator("#interp-badge").is_visible())
                page.locator(".app-info > summary").click()
                check(prefix + "model and records retrievable", page.locator("#interp-badge").is_visible() and page.locator("#status-line").is_visible())
                page.locator(".app-info > summary").click()
                check(prefix + "unconfirmed orientation visible", page.locator("#updown-detail").is_visible() and "未確認" in page.locator("#updown-detail").inner_text())
                page.locator("#mic-button").click()
                check(prefix + "mic consent visible", page.locator("#voice-consent").is_visible())
                page.locator("#consent-yes").click()
                page.locator("#voice-stop:not([disabled])").wait_for(state="visible")
                check(prefix + "recording controls reachable", reachable(page, "#voice-stop") and reachable(page, "#voice-cancel"))
                page.evaluate("window.__fakeText='このロボット、何してる？'")
                talks = posts.count("talk")
                page.locator("#voice-stop").click()
                page.wait_for_function("() => document.querySelector('#text-input').value === 'このロボット、何してる？'")
                check(prefix + "transcript enters input without sending", posts.count("talk") == talks)
                with page.expect_response("**/api/talk"):
                    page.locator("#text-send").click()
                page.wait_for_function("() => document.querySelector('#ask-turns').textContent.includes('何の作業か')")
                check(prefix + "Claude mock explanation visible", page.locator("#ask-turns .said").last.is_visible())
                check(prefix + "conversation mic reachable", reachable(page, "#mic-button"))
                check(prefix + "text send reachable", reachable(page, "#text-send"))
                page.screenshot(path=str(out / f"{width}-conversation.png"), full_page=width < 1100)
                # Only presentation must be folded; the interpretation remains unchanged.
                page.locator("#position-chips button", has_text="C00002").click()
                send(page, "上に3mm")
                question = page.locator("#reply-message").inner_text()
                check(prefix + "direction question retained", page.locator("#reply-message").is_visible() and
                      any(t in question for t in ("上下", "向き", "Z+", "Zプラス")), question)
                check(prefix + "no candidate while direction unknown", page.locator("#open-confirm").is_disabled())
                page.screenshot(path=str(out / f"{width}-question.png"), full_page=width < 1100)
                send(page, "Zプラス方向に3mm")
                page.locator(".cand-change").wait_for(state="visible")
                if page.locator("#play-pause").is_enabled():
                    page.locator("#play-pause").click()
                check(prefix + "candidate exact XYZ", page.locator("#candidate-view .xyz td").all_text_contents() ==
                      ["400.000", "-150.000", "200.000", "400.000", "-150.000", "203.000"])
                check(prefix + "candidate warning visible", page.locator(".cand-warn").is_visible())
                check(prefix + "review button reachable", reachable(page, "#open-confirm"))
                page.locator('.view-tab[data-view="xz"]').click()
                check(prefix + "2D tab works", page.locator("#fig-main svg").is_visible())
                page.locator('.view-tab[data-view="3d"]').click()
                check(prefix + "3D tab works", page.locator("#fig-3d svg").is_visible())
                page.locator("#fig-3d").hover()
                page.mouse.wheel(0, -180)
                page.locator("#view-reset").wait_for(state="visible")
                check(prefix + "view reset reachable", reachable(page, "#view-reset"))
                page.locator("#view-reset").click()
                # Playback geometry and selected target stay owned by existing JS.
                page.locator("#play-step").click()
                check(prefix + "step playback works", page.locator("#play-now").is_visible())
                page.locator("#play-stop").click()
                page.screenshot(path=str(out / f"{width}-difference.png"), full_page=width < 1100)
                check(prefix + "physical limitation always visible", page.locator("#figure-note").is_visible())
                page.locator(".figure-help > summary").click()
                check(prefix + "3D technical help opens", page.locator("#view3d-note").is_visible())
                page.locator(".figure-help > summary").click()
                page.locator("#open-confirm").click()
                page.locator("#confirm-overlay:not([hidden])").wait_for(state="visible")
                check(prefix + "review warning visible", page.locator("#confirm-warnings").is_visible())
                check(prefix + "review exact XYZ", page.locator("#confirm-xyz .xyz td").all_text_contents() ==
                      ["400.000", "-150.000", "200.000", "400.000", "-150.000", "203.000"])
                check(prefix + "approver input reachable", reachable(page, "#approver-name"))
                check(prefix + "approve control reachable", reachable(page, "#approve-button"))
                page.screenshot(path=str(out / f"{width}-review.png"), full_page=False)
                page.locator("#confirm-close").click()
                # Reload invalidates previous session. A new synthetic JOB is used.
                load(page)
                page.locator("#position-chips button", has_text="C00000").click()
                send(page, "Zプラス方向に3mm")
                check(prefix + "rejected position reason visible", "ここでは変更できません" in page.locator("#candidate-view").inner_text())
                page.locator("#open-confirm").click()
                check(prefix + "blocked review reason visible", page.locator("#confirm-blocked").is_visible())
                check(prefix + "blocked candidate approval disabled", page.locator("#approve-button").is_disabled())
                page.screenshot(path=str(out / f"{width}-refusal.png"), full_page=False)
                page.close()
            # Display-only fixture, not a real model call: check the non-mock
            # model-label branch while retaining the same in-process client.
            label_page = browser.new_page(viewport=dict(width=1920, height=1080), reduced_motion="reduce")
            label_page.add_init_script(FAKE_RECOGNITION)
            label_page.on("pageerror", lambda e: errors.append(str(e)))
            def label_fixture(route):
                response = route.fetch()
                data = response.json()
                if "model" in data:
                    data["model"] = "claude-ui-label-fixture"
                if data.get("ask") and "model" in data["ask"]:
                    data["ask"]["model"] = "claude-ui-label-fixture"
                route.fulfill(response=response, json=data)
            label_page.route("**/api/ask", label_fixture)
            label_page.route("**/api/talk", label_fixture)
            load(label_page)
            send(label_page, "このロボット、何してる？")
            label_page.locator("#ask-turns .bubble.claude .who").wait_for(state="visible")
            check("display fixture: conversational speaker is simply Claude",
                  label_page.locator("#ask-turns .bubble.claude .who").last.inner_text() == "Claude")
            label_page.locator(".talk-more > summary").click()
            check("display fixture: exact model available in details",
                  label_page.locator("#ask-model").is_visible() and "claude-ui-label-fixture" in label_page.locator("#ask-model").inner_text())
            label_page.close()
            browser.close()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        check("all planned browser scenes executed", False, str(exc))
    finally:
        srv.shutdown()
        srv.server_close()
    check("no browser script errors", not errors, errors)
    check("no external HTTP requests", not external, external)
    check("no approval/export HTTP requests", not any(x in ("approve", "export") for x in posts))
    # Compare every app Python file and all existing JS except the PR #3 display helper.
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE, "voice_teaching"], cwd=ROOT, text=True).splitlines()
    protected = [n for n in names if n.endswith(".py") or
                 (n.endswith(".js") and n.rsplit("/", 1)[-1] not in ("highlight.js", "explain.js"))]
    # Git checkout uses CRLF on Windows; compare canonical LF bytes on both sides.
    changed = [n for n in protected if subprocess.check_output(["git", "show", f"{BASE}:{n}"], cwd=ROOT).replace(b"\r\n", b"\n")
               != (ROOT / n).read_bytes().replace(b"\r\n", b"\n")]
    check("Python and shared runtime JS unchanged", not changed, changed)
    old_explain = subprocess.check_output(["git", "show", f"{BASE}:voice_teaching/web/static/explain.js"], cwd=ROOT).decode("utf-8").replace("\r\n", "\n")
    expected = old_explain.replace('isClaude ? speaker(r.model) : "アプリ"',
                                  'isClaude ? (/^claude-mock/.test(r.model || "") ? speaker(r.model) : "Claude") : "アプリ"')
    check("explain.js limited to one speaker display expression", expected == (ROOT / "voice_teaching/web/static/explain.js").read_text(encoding="utf-8"))
    class Ids(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
        def handle_starttag(self, tag, attrs):
            self.ids.extend(v for k, v in attrs if k == "id")
    before, after = Ids(), Ids()
    before.feed(subprocess.check_output(["git", "show", f"{BASE}:voice_teaching/web/static/index.html"], cwd=ROOT).decode("utf-8"))
    after.feed((ROOT / "voice_teaching/web/static/index.html").read_text(encoding="utf-8"))
    check("existing HTML event target IDs retained", set(before.ids) <= set(after.ids))
    check("HTML IDs unique", len(after.ids) == len(set(after.ids)))
    result = dict(base_sha=BASE, tested_parent_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  asset_hashes={n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in
                                ["voice_teaching/web/static/" + f for f in ("index.html", "style.css", "highlight.js", "explain.js")]},
                  job_sha256=hashlib.sha256((ROOT / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()).hexdigest(),
                  chrome_version=locals().get("chrome_version"), checks=checks, errors=errors,
                  live_api_calls=0, microphone="MOCK_RECOGNITION_ONLY; REAL_MIC_NOT_RUN", approval_export_requests=0,
                  protected_files_count=len(protected), protected_ports_touched=False)
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=sum(c["passed"] for c in checks), failed=sum(not c["passed"] for c in checks),
                          errors=errors), ensure_ascii=False))
    return 0 if checks and all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("output/filming-main/check"))
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        srv = server(args.out)
        print(f"MOCK ONLY: http://127.0.0.1:{srv.server_address[1]}/", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
    else:
        raise SystemExit(run(args.out))
