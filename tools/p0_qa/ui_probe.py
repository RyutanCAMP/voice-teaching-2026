"""Four focused UI replays; no actual API, microphone, approval or output."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
OUT = ROOT / 'output/p0-20261105/ui'

if '--child' not in sys.argv:
    OUT.mkdir(parents=True, exist_ok=True)
    keep = {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(TEMP=str(OUT), TMP=str(OUT), PLAYWRIGHT_BROWSERS_PATH=str(QA / 'output/qa9417/browsers'))
    with (OUT / 'execution.txt').open('wb') as log:
        try:
            code = subprocess.run([str(QA / 'output/qa9417/venv/Scripts/python.exe'), '-X', 'utf8', '-B',
                                   str(Path(__file__)), '--child'], cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT, timeout=90).returncode
        except subprocess.TimeoutExpired:
            code = 2
    print(json.dumps(dict(exit_code=code)))
    raise SystemExit(code)

sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from playwright.sync_api import sync_playwright
import test_claude_talk as T
from voice_teaching.web import server

def audit(event, args):
    if event == 'socket.connect' and isinstance(args[1], tuple) and args[1][0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('External network is forbidden')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        if Path(os.fsdecode(args[0])).name.startswith('.env'):
            raise RuntimeError('Credential access forbidden')
sys.addaudithook(audit)
old = json.loads((QA / 'output/live-natural-53fe778/baseline-live/results.json').read_text(encoding='utf-8'))
raw = {r['id']: r['target_calls'][0]['tools'][0]['input'] for r in old['cases'] if r['id'] in ('A02', 'E02')}
rows = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    for id, text, expected in [('decimal', '2.3回にして', '整数'), ('approx', '3回くらい', '3回でよいですか'),
                               ('E02', 'いい感じにして', 'どのようにしたいか'), ('A02', 'どんな動き？', '2回')]:
        answer = raw.get(id)
        client = T.FakeClient(*([T.answer(answer['reply'], answer['evidence_lines'], answer['intent'], answer['change_request'])] if answer else []))
        ex = T.cx.Explainer('offline-replay-placeholder', T.MODEL, client=client, usage_log=T.cx.UsageLog(OUT / f'{id}.jsonl'))
        srv = server.make_server(port=0, interpreter=T.MockInterpreter(), explainer=ex, updown_config=None)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        page = browser.new_page(viewport=dict(width=1440, height=900))
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname == '127.0.0.1' else route.abort())
        try:
            page.goto(f'http://127.0.0.1:{srv.server_address[1]}/')
            page.set_input_files('#file-input', str(ROOT / 'samples/dummy/DEMO_BUTTON_REPEAT.JBI'))
            page.wait_for_selector('#job-area:not([hidden])')
            if page.locator('#text-input').is_hidden():
                page.click('#kbd-button')
            page.fill('#text-input', text)
            page.click('#text-send')
            page.wait_for_function('(s) => document.querySelector("#ask-turns").textContent.includes(s)', arg=expected)
            page.wait_for_timeout(1000)  # Capture after the conversation entrance animation.
            visible = page.locator('#ask-turns').inner_text()
            opacity = page.locator('#ask-turns').evaluate('(e) => getComputedStyle(e).opacity')
            rows.append(dict(id=id, input=text, expected=expected, visible=visible, errors=errors,
                             opacity=opacity, sdk_replays=len(client.requests), status='PASS' if expected in visible and not errors else 'FAIL'))
            page.screenshot(path=str(OUT / f'{id}.png'), full_page=True)
        finally:
            page.close()
            srv.shutdown()
            srv.server_close()
    browser.close()
(OUT / 'results.json').write_text(json.dumps(dict(api_calls=0, microphone='NOT_RUN', cases=rows), ensure_ascii=False, indent=2), encoding='utf-8')
raise SystemExit(0 if all(r['status'] == 'PASS' for r in rows) else 1)
