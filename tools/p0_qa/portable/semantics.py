"""Exact semantic projection, preserved from QA da9ee7b."""
import hashlib
import json

def projection(r):
    p=r['response'];a=p.get('ask') or {};h=p.get('hear') or {}
    def state(s):return {k:s.get(k) for k in ['selected','facts','candidate','repeat_range','approval','exported','can_approve','can_export','blocked_reason']}
    return dict(input=r['text'],job_sha256=r['job_sha256'],setup=[s['input'] for s in r['steps']],
        raw_tools=[dict(name=t['name'],input=t['input']) for c in r['target_calls'] for t in c.get('tools',[])],
        before=state(r['before']),state=state(p['state']),
        ask={k:a.get(k) for k in ['reply','reply_by','checked','notes','supplement','intent','change_request','change_record','highlight','talk']},
        hear={k:h.get(k) for k in ['status','message','facts','tool_calls']},
        stale_probe={k:(r.get('stale_probe') or {}).get(k) for k in ['status','message']})


def fingerprint(r):return hashlib.sha256(json.dumps(projection(r),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
