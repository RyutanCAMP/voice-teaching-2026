"""Compare exact saved semantics; changed responses require explicit case review."""
from collections import Counter
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
BASE = ROOT / 'output/p0-20261105'
spec = importlib.util.spec_from_file_location('prior_review', QA / 'tools/live_claude_qa/semantic_review.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
old = json.loads((QA / 'tools/live_claude_qa/reviews/20261104-supplement/semantic_review.json').read_text(encoding='utf-8'))
old = {r['id']: r for r in old['cases']}


def read_run(prefix):
    return {r['id']: r for group in ['base', 'extra', 'context', 'fraction']
            for r in json.loads((BASE / f'{prefix}-{group}/results.json').read_text(encoding='utf-8'))['cases']}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefix', default='release')
    prefix = ap.parse_args().prefix
    before, after = read_run('before'), read_run(prefix)
    changed, identical, errors = [], [], []
    pre_mismatch = []
    for id, r in after.items():
        o = old[id]
        if 'response' in before[id] and prior.fingerprint(before[id]) != o['fingerprint']:
            pre_mismatch.append(id)
        if r['status'] == 'ERROR':
            errors.append(dict(id=id, reason=r.get('error_reason'), calls=r['calls']))
            continue
        if prior.fingerprint(r) == o['fingerprint']:
            identical.append(dict(id=id, status=o['status'], reason='Exact semantic projection unchanged'))
        else:
            p = r['response']
            changed.append(dict(id=id, before_status=o['status'], contract=o['contract'],
                before=o['evidence'], after=prior.projection(r)))
    doc = dict(before_mismatch=pre_mismatch, identical=identical, changed=changed, errors=errors,
               unchanged_summary=dict(Counter(x['status'] for x in identical)))
    (BASE / 'semantic_diff.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(before_mismatch=pre_mismatch, unchanged=doc['unchanged_summary'], changed=len(changed), errors=len(errors))))
    for c in changed:
        p = c['after']; s = p['state']; a = p['ask']; h = p['hear']; b = c['before']
        print(json.dumps(dict(id=c['id'], was=c['before_status'], input=p['input'],
            old_reply=b['ask'].get('reply'), old_hear=b['hear'].get('message'),
            reply=a['reply'], notes=a['notes'], hear=h['message'],
            candidate=(s['candidate'] or {}).get('target'), range=s['repeat_range'],
            raw=p['raw_tools']), ensure_ascii=False))
    print(json.dumps(dict(errors=errors), ensure_ascii=False))
