"""Reuse the frozen 169-case assets and real responses without touching their originals."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
BASE = ROOT / 'output/p0-20261105'
OLD = QA / 'output/live-natural-53fe778'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def snapshot(name, committed=False, commit='HEAD'):
    out = BASE / name
    if out.exists():
        raise RuntimeError('Snapshot already exists; preserve it')
    files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit, '--', 'voice_teaching', 'samples/dummy', 'tests', 'requirements.txt'], cwd=ROOT).decode().splitlines()
    hashes = {}
    for n in files:
        data = subprocess.check_output(['git', 'show', commit + ':' + n], cwd=ROOT) if committed else (ROOT / n).read_bytes()
        p = out / 'source' / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        hashes[n] = hashlib.sha256(data).hexdigest()
    doc = dict(commit=subprocess.check_output(['git', 'rev-parse', commit], cwd=ROOT).decode().strip(), files=hashes)
    (out / 'manifest.json').write_text(json.dumps(doc, indent=2), encoding='utf-8')


def worker(args):
    n = module('frozen_runner', QA / 'tools/live_claude_qa/natural_run.py')
    n.ROOT = QA
    n.BASE = BASE
    n.TARGET = json.loads((BASE / args.variant / 'manifest.json').read_text())['commit']
    # Recordings are immutable; replay uses the original real answer tool envelopes.
    args.recorded = str(OLD / args.recorded)
    return n.run(args)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot')
    ap.add_argument('--commit', default='HEAD')
    ap.add_argument('--mode', choices=['replay', 'live'], default='replay')
    ap.add_argument('--variant', default='before')
    ap.add_argument('--run', default='before-base')
    ap.add_argument('--recorded', default='baseline-live')
    ap.add_argument('--asset', default=str(QA / 'tools/live_claude_qa/natural_cases_v1.json'))
    ap.add_argument('--ids')
    ap.add_argument('--child', action='store_true')
    a = ap.parse_args()
    if a.snapshot:
        snapshot(a.snapshot, committed=True, commit=a.commit)
    elif a.child:
        raise SystemExit(worker(a))
    else:
        keep = {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(BASE), TMP=str(BASE), TMPDIR=str(BASE))
        cmd = [str(QA / 'output/qa9417/venv/Scripts/python.exe'), '-X', 'utf8', '-I', '-B', str(Path(__file__).resolve()), '--child', *sys.argv[1:]]
        raise SystemExit(subprocess.run(cmd, cwd=ROOT, env=env).returncode)
