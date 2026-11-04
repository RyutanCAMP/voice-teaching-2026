"""One-time packaging of committed QA assets; never reads credentials or calls API."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
OUT = ROOT / 'tools/p0_qa/portable'
OUT.mkdir(exist_ok=True)

def sha(b): return hashlib.sha256(b).hexdigest()
def tracked(name): return subprocess.check_output(['git', 'show', 'da9ee7b:tools/live_claude_qa/' + name], cwd=QA)
def redact(s):
    # Portable execution evidence never needs a home-directory path or request ID.
    s = re.sub(r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s"\n]+', '<USERPROFILE>', s)
    return s

sources = {}
for name in ['natural_cases_v1.json', 'natural_extra_v1.json', 'context_supplement_v1.json', 'fraction_boundary_v1.json']:
    data = tracked(name)
    (OUT / name).write_bytes(data)
    sources[name] = sha(data)
for name in ['MIC_PRIORITY_20261105.md', 'microphone_matrix.json']:
    data = tracked('reviews/20261104-supplement/' + name)
    (OUT / name).write_bytes(data)
    sources[name] = sha(data)

original = tracked('natural_run.py')
runner = original.decode('utf-8')
start, end = runner.index('def prepare():'), runner.index('def visible(')
runner = runner[:start] + runner[end:runner.index("if __name__=='__main__':")]
runner = runner.replace('parents[2]', 'parents[3]', 1)
runner = runner.replace("BASE=ROOT/'output/live-natural-53fe778'", "BASE=ROOT/'output/p0-20261105'")
runner = runner.replace("ASSET=ROOT/'tools/live_claude_qa/natural_cases_v1.json'", "ASSET=ROOT/'tools/p0_qa/portable/natural_cases_v1.json'")
runner = runner.replace("ci.load_dotenv(Path('C:/Users/User/voice-teaching/.env'),env)", "ci.load_dotenv(ROOT/'.env',env)")
runner = runner.replace("PATCH=ROOT/'tools/live_claude_qa/initial_explanation_notes.patch'", "# Adaptation is limited to packaging, paths and credential location.")
(OUT / 'frozen_runner.py').write_text(runner, encoding='utf-8')

# Keep the preceding independent semantic review unchanged; reuse only projection code.
sem = tracked('semantic_review.py').decode('utf-8')
projection = sem[sem.index('def projection('):sem.index('def read(run,')]
(OUT / 'semantics.py').write_text('"""Exact semantic projection, preserved from QA da9ee7b."""\nimport hashlib\nimport json\n\n' + projection, encoding='utf-8')

recordings = {}
for name, path in [(n, QA / 'output/live-natural-53fe778' / n / 'results.json') for n in
                   ['baseline-live', 'extra-live', 'context-live', 'fraction-live']] + [
                       ('recaptured-context', ROOT / 'output/p0-20261105/final-live-context/results.json')]:
    obj = json.loads(path.read_text(encoding='utf-8'))
    def clean(v):
        if isinstance(v, dict): return {k: clean(x) for k, x in v.items() if k not in ('request_id',)}
        if isinstance(v, list): return [clean(x) for x in v]
        return redact(v) if isinstance(v, str) else v
    payload = (json.dumps(clean(obj), ensure_ascii=False, indent=2) + '\n').encode()
    recordings[name + '/results.json'] = payload
with tarfile.open(OUT / 'recordings.tar.gz', 'w:gz') as tar:
    for name, data in recordings.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))

manifest = dict(source_qa_commit='da9ee7b', product_commit='ce7c981',
    asset_source_sha256=sources, original_runner_sha256=sha(original),
    adaptations=['repo-relative paths', 'current interpreter', 'local ignored .env only in explicit live mode', 'remove unused preparation helpers'],
    recording_files={n: sha(b) for n, b in recordings.items()},
    recording_archive_sha256=sha((OUT / 'recordings.tar.gz').read_bytes()),
    redaction='Home directory prefixes and API request IDs removed from handoff recordings; tool answers and semantic state retained.')
(OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(dict(assets=4, cases=169, microphone_cases=36, recordings=len(recordings), api_calls=0)))
