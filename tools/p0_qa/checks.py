"""Run checks with an environment containing no API credentials or Node hooks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
OUT = ROOT / 'output/p0-20261105'

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('suite', choices=['core', 'browser'])
    ap.add_argument('--label', default='clean', choices=['clean', 'final'])
    a = ap.parse_args()
    keep = {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(TEMP=str(OUT), TMP=str(OUT), TMPDIR=str(OUT), PYTHONDONTWRITEBYTECODE='1',
               PLAYWRIGHT_BROWSERS_PATH=str(QA / 'output/qa9417/browsers'))
    cmd = [str(QA / 'output/qa9417/venv/Scripts/python.exe'), '-X', 'utf8', '-B', '-m', 'pytest', '-q', '--tb=short']
    cmd += ['--ignore=tests/test_e2e_browser.py'] if a.suite == 'core' else ['tests/test_e2e_browser.py', '-o', 'faulthandler_timeout=45']
    log = OUT / f'{a.label}-{a.suite}.txt'
    with log.open('wb') as f:
        try:
            code = subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT,
                                  timeout=240 if a.suite == 'core' else 300).returncode
            result = dict(suite=a.suite, exit_code=code, status='COMPLETE')
        except subprocess.TimeoutExpired:
            code = 2
            result = dict(suite=a.suite, exit_code=code, status='EXECUTION_TIMEOUT')
    log.with_suffix('.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    raise SystemExit(code)
