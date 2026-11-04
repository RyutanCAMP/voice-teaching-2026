"""One-clone, credential-free 169-case replay with exact reviewed semantic gates."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'output/p0-20261105'

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', required=True, help='New result name; never overwrite an existing run')
    ap.add_argument('--commit', default='HEAD')
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', a.run): ap.exit(2, 'Invalid run name\n')
    out = BASE / a.run
    if out.exists(): ap.exit(2, 'Existing run preserved; choose a new name\n')
    out.mkdir(parents=True)
    cmd = [sys.executable, '-X', 'utf8', '-B', str(HERE / 'run.py')]
    if subprocess.run(cmd + ['--restore'], cwd=ROOT).returncode: ap.exit(2, 'Recording restore failed\n')
    variant = a.run + '-source'
    if subprocess.run(cmd + ['--snapshot', variant, '--commit', a.commit], cwd=ROOT).returncode:
        ap.exit(2, 'Committed source snapshot failed\n')
    sets = [('base','baseline-live','natural_cases_v1.json'), ('extra','extra-live','natural_extra_v1.json'),
            ('context','context-live','context_supplement_v1.json'), ('fraction','fraction-live','fraction_boundary_v1.json')]
    attempts = []
    records = {}
    for tag, recorded, asset in sets:
        name = a.run + '-' + tag
        with (out / (tag + '.log')).open('wb') as log:
            code = subprocess.run(cmd + ['--mode','replay','--variant',variant,'--run',name,'--recorded',recorded,
                '--asset',str(HERE / 'portable' / asset)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
        p = BASE / name / 'results.json'
        result = read(p) if p.exists() else {}
        attempts.append(dict(set=tag, exit_code=code, finished=bool(result.get('finished_at'))))
        for r in result.get('cases', []): records[r['id']] = r
    # Original recordings contain no SDK response for the newly reached context
    # path. Preserve that failed attempt and replay its separately recaptured answer.
    name = a.run + '-recaptured-context'
    with (out / 'recaptured-context.log').open('wb') as log:
        code = subprocess.run(cmd + ['--mode','replay','--variant',variant,'--run',name,
            '--recorded','recaptured-context','--ids','SUP-D05-dialog',
            '--asset',str(HERE / 'portable/context_supplement_v1.json')], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    p = BASE / name / 'results.json'
    result = read(p) if p.exists() else {}
    attempts.append(dict(set='recaptured-context', exit_code=code, finished=bool(result.get('finished_at'))))
    for r in result.get('cases', []): records[r['id']] = r

    spec = importlib.util.spec_from_file_location('semantic_projection', HERE / 'portable/semantics.py')
    semantics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(semantics)
    baseline = read(HERE / 'reports/20261105/case_verdicts.json')
    rows = []
    for expected in baseline['cases']:
        id = expected['id']; r = records.get(id)
        reference = baseline['context_recaptured'] if id == 'SUP-D05-dialog' else expected['layers']
        if not r: status, reason = 'NOT_RUN', 'No complete case result'
        elif r.get('status') == 'ERROR' or 'response' not in r:
            status, reason = 'EXECUTION_ERROR', 'Replay/precondition failed; not a semantic pass'
        elif semantics.projection(r) != reference:
            status, reason = 'REVIEW_REQUIRED', 'Changed response/state requires case-specific review'
        else:
            status, reason = expected['combined_after'], 'Exact reviewed semantic projection matches ce7c981'
        rows.append(dict(id=id, status=status, reviewed_baseline=expected['combined_after'],
                         reason=reason, contract=expected['contract']))
    counts = dict(Counter(r['status'] for r in rows))
    summary = dict(commit=read(BASE / variant / 'manifest.json')['commit'], mode='saved_real_response_replay',
        api_calls=0, credential_read=False, real_microphone='NOT_RUN', cases=rows, summary=counts,
        original_recording_attempts=attempts,
        reference='168 original saved responses + 1 separately recaptured context response; not new live trials',
        asset_manifest_sha256=sha(HERE / 'portable/manifest.json'),
        jobs={p.name:sha(p) for p in (ROOT / 'samples/dummy').glob('DEMO*JBI')})
    (out / 'semantic_results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'REPORT.md').write_text('# 169-case replay\n\n' + json.dumps(counts, ensure_ascii=False) +
        '\n\nAPI calls: 0. Microphone: NOT_RUN. H02/H16 remain filming blockers.\n'
        'The original context recording cannot run the new path; its error and separate recapture are retained.\n', encoding='utf-8')
    print(json.dumps(dict(commit=summary['commit'], summary=counts, api_calls=0, output=str(out)), ensure_ascii=False))
    raise SystemExit(2 if counts.get('EXECUTION_ERROR') or counts.get('NOT_RUN') else 1 if counts.get('FAIL') else 2 if counts.get('REVIEW_REQUIRED') else 0)
