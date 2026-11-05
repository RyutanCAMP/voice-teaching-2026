"""Isolated UI QA: synthetic JOB, mock interpretation, ephemeral port, no approval/export."""
from pathlib import Path
import argparse
import json
import sys
import threading
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.server import make_server
from voice_teaching import claude_explain as cx
from tools.demo_mock import ScriptClient, MOCK_MODEL


def run(out, baseline=False):
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    # No environment configuration is read; both model paths have no live key.
    cx.PRICES[MOCK_MODEL] = cx.Price(input=0, output=0, cache_write_5m=0, cache_write_1h=0, cache_read=0)
    srv = make_server(port=0, interpreter=MockInterpreter(), updown_config=None,
                      explainer=cx.Explainer('mock-only-no-network', MOCK_MODEL, client=ScriptClient(),
                                            usage_log=cx.UsageLog(out / 'mock_usage.jsonl')))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    checks = []
    errors = []
    requests = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            for width, height in [(1920, 1080), (1280, 720), (390, 844)]:
                page = browser.new_page(viewport=dict(width=width, height=height), reduced_motion='reduce')
                page.set_default_timeout(10000)
                page.on('pageerror', lambda ex: errors.append(str(ex)))
                page.on('request', lambda req: requests.append(req.url) if req.method == 'POST' else None)
                if baseline:
                    for filename, content_type in [('style.css', 'text/css'), ('highlight.js', 'text/javascript')]:
                        body = subprocess.check_output(['git', 'show', 'cf0b3fb:voice_teaching/web/static/'+filename], cwd=ROOT)
                        page.route('**/static/'+filename, lambda route, request, body=body, ct=content_type: route.fulfill(body=body, content_type=ct))
                page.goto(url)
                page.evaluate("sessionStorage.setItem('vt-opening-shown', '1')")
                if page.locator('#opening').is_visible():
                    page.locator('#op-skip').click()
                page.screenshot(path=str(out / f'{width}-start.png'), full_page=True)
                page.set_input_files('#file-input', str(ROOT / 'samples/dummy/DEMO_C_PLACE.JBI'))
                page.locator('#file-name', has_text='DEMO_C_PLACE.JBI').wait_for(state='visible')
                page.wait_for_timeout(150)
                page.screenshot(path=str(out / f'{width}-loaded.png'), full_page=True)
                check(f'{width}: no horizontal overflow', page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                page.locator('#position-chips button', has_text='C00002').click()
                if not page.locator('#text-input').is_visible():
                    page.locator('#kbd-button').click()
                page.locator('#text-input').fill('Zプラス方向に3mm')
                page.locator('#text-send').click()
                page.locator('#step-4[aria-current="step"]').wait_for(state='attached')
                if page.locator('#play-pause').is_enabled():
                    page.locator('#play-pause').click()
                page.wait_for_timeout(250)
                page.screenshot(path=str(out / f'{width}-difference.png'), full_page=True)
                check(f'{width}: candidate values visible', page.locator('.cand-change').is_visible())
                check(f'{width}: review button visible', page.locator('#open-confirm').is_visible())
                check(f'{width}: 3D exists', page.locator('#fig-3d svg').count() == 1)
                check(f'{width}: exact server values retained', page.locator('#candidate-view .xyz td').all_text_contents() ==
                      ['400.000', '-150.000', '200.000', '400.000', '-150.000', '203.000'])
                shape = page.locator('#fig-3d line.seg').evaluate_all("(els) => els.map(e => ['x1','y1','x2','y2','stroke-dasharray'].map(k => e.getAttribute(k)))")
                (out / f'{width}-geometry.json').write_text(json.dumps(shape, indent=2)+'\n', encoding='utf-8')
                if page.locator('.figure-help').count():
                    check(f'{width}: technical notes initially closed', not page.locator('.figure-help').evaluate('(e) => e.open'))
                    check(f'{width}: persistent limitation visible', page.locator('#figure-note').is_visible())
                    page.locator('.figure-help summary').click()
                    check(f'{width}: technical notes can open', page.locator('#view3d-note').is_visible())
                    page.locator('.figure-help summary').click()
                page.locator('#open-confirm').click()
                page.wait_for_selector('#confirm-overlay:not([hidden])')
                page.screenshot(path=str(out / f'{width}-review.png'), full_page=False)
                check(f'{width}: approval content visible', page.locator('#confirm-warnings').is_visible())
                check(f'{width}: approval button visible', page.locator('#approve-button').is_visible())
                check(f'{width}: warning content retained', '物理的な安全の保証ではありません' in page.locator('#confirm-warnings').inner_text())
                page.locator('#confirm-close').click()
                # Fresh synthetic JOB for the independent rejected-position
                # scene; do not bypass any stale-screen/selection lock.
                page.reload()
                if page.locator('#opening').is_visible():
                    page.locator('#op-skip').click()
                page.set_input_files('#file-input', str(ROOT / 'samples/dummy/DEMO_C_PLACE.JBI'))
                page.locator('#file-name', has_text='DEMO_C_PLACE.JBI').wait_for(state='visible')
                page.locator('#position-chips button', has_text='C00000').click()
                if not page.locator('#text-input').is_visible():
                    page.locator('#kbd-button').click()
                page.locator('#text-input').fill('Zプラス方向に3mm')
                page.locator('#text-send').click()
                page.wait_for_timeout(350)
                page.screenshot(path=str(out / f'{width}-blocked.png'), full_page=True)
                check(f'{width}: rejection visible', 'ここでは変更できません' in page.locator('#candidate-view').inner_text())
                page.locator('#open-confirm').click()
                page.locator('#confirm-overlay:not([hidden])').wait_for(state='visible')
                check(f'{width}: blocked candidate cannot be approved', page.locator('#approve-button').is_disabled())
                check(f'{width}: rejection is visible in review', page.locator('#confirm-blocked').is_visible())
                page.screenshot(path=str(out / f'{width}-blocked-review.png'), full_page=False)
                page.close()
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    check('no script errors', not errors)
    check('no approval/export request', not any('/api/approve' in u or '/api/export' in u for u in requests))
    result = dict(checks=checks, errors=errors, protected_ports_touched=False,
                  live_api_calls=0, microphone='NOT_RUN', port='ephemeral')
    (out / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(passed=sum(c['passed'] for c in checks), checks=len(checks), errors=errors), ensure_ascii=False))
    return 0 if all(c['passed'] for c in checks) else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--baseline', action='store_true')
    args = ap.parse_args()
    raise SystemExit(run(args.out, args.baseline))
