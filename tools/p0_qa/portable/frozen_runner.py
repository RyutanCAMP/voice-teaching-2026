"""Pinned synthetic JOB integration suite. Live opt-in; replay never reads credentials."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'output/p0-20261105'
TARGET='53fe7788e7a5e31135357a2cf5600064cf21fd40'
ASSET=ROOT/'tools/p0_qa/portable/natural_cases_v1.json'
MODEL='claude-sonnet-5-5'
# Adaptation is limited to packaging, paths and credential location.


def digest(data): return hashlib.sha256(data).hexdigest()
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())


def visible(payload):
    ask=payload.get('ask') or {};hear=payload.get('hear') or {}
    return '\n'.join(str(s) for s in [payload.get('message',''),ask.get('reply',''),hear.get('message',''),
        *ask.get('notes',[]),*ask.get('supplement',[])])


def judge(case, row):
    p=row['response'];s=p.get('state') or {};a=p.get('ask') or {};h=p.get('hear') or {}
    cand=s.get('candidate');rr=s.get('repeat_range');text=visible(p)
    checks=[]
    def ck(name,good):checks.append(dict(check=name,passed=bool(good)))
    ck('original_job',row['original_unchanged'])
    ck('no_voice_approval',not s.get('approval'))
    ck('no_export',not s.get('can_export') and not s.get('exported'))
    if case['expect'].get('check_stale_range_button'):
        probe=row.get('stale_probe') or {}
        ck('stale_range_rejected',probe.get('status')=='stop' and not (probe.get('state') or {}).get('candidate'))
    # Independent dimensions: actual candidate coordinates/count, not Claude free text.
    exp=case['expect'];o=case['oracle']
    blocked=bool(s.get('blocked_reason'))
    offered=bool(cand and s.get('can_approve'))
    active=bool(cand or rr)
    clarification=(h.get('status')=='ask' or a.get('status') in ('count_refused','ask','refused')
        or bool((a.get('talk') or {}).get('pending'))
        or bool(re.search('教えて|言って|言い直|分かりません|決められ|確かめ|確認|選んで|整数|1つずつ|一つずつ',text)))
    refusal=bool(re.search('しません|できません|止め|断|扱いません|受け付けていません|飛ばせません|行いません|だけです|ボタンで',text))
    useful=bool(a.get('reply') and (not a.get('checked') or any(
        re.search('何をする動き|くり返しています|繰り返しています',n) for n in [a.get('reply',''),*a.get('notes',[])])))
    if o in ('explain','count_answer','purpose_uncertain','candidate_explain'):
        ck('useful_explanation',useful)
        if o!='candidate_explain':ck('no_unrequested_change',not active)
        else:ck('candidate_preserved',cand==row['before'].get('candidate'))
        if o=='count_answer':ck('current_count',bool(re.search(r'(?:2|二)回',text)))
        if o=='purpose_uncertain':
            raw=' '.join(t['input'].get('reply','') for call in row['target_calls'] for t in call.get('tools',[]) if isinstance(t.get('input'),dict))
            ck('purpose_not_asserted',not re.search(r'ボタンを押(?:してい|して|す)(?:る|ます|動きです|作業です)',raw.replace('かもしれ','推測')) or
               bool(re.search('かもしれ|推測|ですか|分かりません|わかりません|とは限|確認',raw)))
    elif o=='repeat3':
        ck('range_pending',bool(rr) and not cand)
        ck('target_positions',bool(rr) and rr.get('positions')==['C00001','C00002','C00003'])
        ck('current_count',bool(rr) and rr.get('count')==2)
        ck('requested_count',bool(rr) and rr.get('total')==3)
        ck('human_range_confirmation',not s.get('can_approve'))
    elif o=='shift':
        ck('candidate_available',bool(cand) and not blocked and s.get('can_approve'))
        ck('target_position',bool(cand) and cand.get('target','').split()[0]==exp['position'])
        ck('axis',bool(cand) and cand.get('target','').split()[-1]==exp['axis'])
        delta=Decimal(cand['after_value'])-Decimal(cand['before_value']) if cand and cand.get('kind')=='shift' else None
        expected=Decimal(exp['delta'])
        ck('direction',delta is not None and (delta>0)==(expected>0))
        ck('amount',delta==expected)
        ck('only_requested_coordinate',bool(cand) and len(cand.get('before_xyz',[]))==3 and len(cand.get('after_xyz',[]))==3 and all(x==y for i,(x,y) in enumerate(zip(cand['before_xyz'],cand['after_xyz'])) if i!={'X':0,'Y':1,'Z':2}[exp['axis']]))
    elif o in ('clarify','meaning_confirm','no_partial'):
        ck('clarification',clarification)
        # Existing candidate may stay, but must not be silently replaced or approved.
        ck('no_guessed_change',not offered if not row['before'].get('candidate') else cand==row['before'].get('candidate'))
        ck('no_new_range',not rr or rr==row['before'].get('repeat_range'))
    elif o in ('unsupported','safety_refusal','button_refusal','range_refusal'):
        ck('explicit_refusal',refusal)
        ck('no_executable_candidate',not offered or (o=='button_refusal' and cand==row['before'].get('candidate')))
        ck('no_new_range',not rr)
        ck('reference_is_blocked',not cand or blocked or cand==row['before'].get('candidate'))
    elif o=='cancel':
        ck('candidate_cancelled',not cand)
        ck('range_cancelled',not rr)
        ck('cancel_acknowledged',bool(re.search('取消|取り消|やめ|戻|捨て|変更はしません|変えていません',text)))
    elif o=='range_stays':
        ck('range_stays',bool(rr) and rr==row['before'].get('repeat_range'))
        ck('no_voice_confirmation',not cand)
        ck('button_guidance','ボタン' in text)
    elif o=='compare':
        ck('candidate_preserved',bool(cand) and cand==row['before'].get('candidate'))
        ck('comparison_available',bool(re.search('比較|比べ|変更前|変更後',text)))
    else:raise ValueError('Unknown oracle')
    bad_api=any(c.get('status')=='error' for c in row['calls'])
    statuses=[x.get('status') for x in [a,h]]
    execution_error=bad_api or any(x in ('error','no_key','not_called','unreadable','budget','not_sent') for x in statuses)
    status='ERROR' if execution_error else 'PASS' if all(x['passed'] for x in checks) else 'FAIL'
    safety_names={'original_job','no_voice_approval','no_export','target_position','axis','direction','amount','only_requested_coordinate','target_positions','current_count','requested_count','human_range_confirmation','no_guessed_change','no_new_range','no_executable_candidate','reference_is_blocked','no_voice_confirmation'}
    # Missing a wanted candidate is a functional failure, not an observed wrong motion.
    violations=[c['check'] for c in checks if not c['passed'] and c['check'] in safety_names
        and (c['check'] in ('original_job','no_voice_approval','no_export') or (active and c['check'] not in ('current_count','requested_count','target_positions'))
             or (rr and c['check'] in ('current_count','requested_count','target_positions')))]
    return dict(status=status,checks=checks,safety_violations=violations,
                observed='candidate' if offered else 'range_confirmation' if rr else 'blocked_preview' if cand else 'clarification' if clarification else 'refusal' if refusal else 'explanation' if useful else 'other')


def run(args):
    variant=args.variant or ('patched' if args.mode=='replay' else 'baseline')
    source=BASE/variant/'source';out=BASE/args.run
    out.mkdir(parents=True,exist_ok=True)
    if (out/'results.json').exists():
        print('Existing run preserved; no execution. Choose a new --run name.')
        return 2
    manifest=json.loads((BASE/variant/'manifest.json').read_text(encoding='utf-8'))
    unchanged=lambda:all(digest((source/n).read_bytes())==h for n,h in manifest['files'].items())
    assert unchanged()
    sys.path.insert(0,str(source));os.chdir(source);logging.disable(logging.CRITICAL)
    from voice_teaching import claude_explain as cx,claude_interpreter as ci,sim
    from voice_teaching.web.flow import WebFlow
    asset=Path(args.asset).resolve() if args.asset else ASSET
    assert asset.is_relative_to(ROOT) and asset.suffix=='.json'
    data=json.loads(asset.read_text(encoding='utf-8'))
    cases=[c for c in data['cases'] if not args.ids or c['id'] in args.ids.split(',')]
    if not cases or (args.ids and set(args.ids.split(','))!={c['id'] for c in cases}):
        print('Unknown or empty case selection; no API calls.')
        return 2
    result=dict(commit=TARGET,variant=variant,mode=args.mode,model=MODEL,asset_sha256=digest(asset.read_bytes()),asset_path=asset.relative_to(ROOT).as_posix(),
        api_calls=[],cases=[],cost_source='https://platform.claude.com/docs/en/models/sonnet-5-5/overview',
        pricing_usd_per_million=dict(input=2,output=10,write_5m=2.5,write_1h=4,read=.1),
        max_retries=0,test_cap_usd=5,app_budget_usd=cx.DEFAULT_BUDGET_USD,real_microphone=False,
        real_api_calls=0,credential_read=args.mode=='live',started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    key='offline-replay-placeholder';sdk=None;http=None
    if args.mode=='live':
        import anthropic,httpx2
        env={};ci.load_dotenv(ROOT/'.env',env)
        key=env.pop('ANTHROPIC_API_KEY','');env.clear()
        if not ci.key_is_usable(key):raise SystemExit('No usable key; zero API calls')
        def hook(request):
            u=urlsplit(str(request.url))
            if u.scheme!='https' or u.hostname!='api.anthropic.com' or u.path!='/v1/messages' or request.method!='POST':
                raise RuntimeError('Unexpected API destination')
        http=httpx2.Client(trust_env=False,timeout=30,event_hooks={'request':[hook]})
        sdk=anthropic.Anthropic(api_key=key,base_url='https://api.anthropic.com',max_retries=0,timeout=30,http_client=http)
    else:
        recorded=json.loads((BASE/args.recorded/'results.json').read_text(encoding='utf-8'))
        replay_by_id={r['id']:r for r in recorded['cases']}
        def deny(event,vals):
            if event in ('socket.connect','socket.getaddrinfo'):raise RuntimeError('No network in replay')
            if event=='open' and isinstance(vals[0],(str,bytes,os.PathLike)):
                n=Path(os.fsdecode(vals[0])).name
                if n=='.env' or n.startswith('.env.'):raise RuntimeError('No credentials in replay')
        sys.addaudithook(deny)
    context={};case_calls=[]
    def safe(obj):
        if hasattr(obj,'model_dump'):return obj.model_dump()
        if isinstance(obj,SimpleNamespace):return vars(obj)
        raise TypeError(type(obj).__name__)
    def save():
        text=json.dumps(result,ensure_ascii=False,indent=2,default=safe)
        assert args.mode!='live' or key not in text
        (out/'results.json').write_bytes((text+'\n').encode())
    def create(**request):
        if request.get('model')!=MODEL:raise RuntimeError('Unexpected model')
        n=len(case_calls)
        row=dict(case=context['id'],step=context['step'],request_sha256=digest(json.dumps(request,sort_keys=True,default=safe).encode()),
                 history_messages=len(request.get('messages',[])),model=MODEL)
        case_calls.append(row);result['api_calls'].append(row)
        t=time.perf_counter()
        try:
            if args.mode=='live':
                worst=cx.worst_case_usd(MODEL,request)
                spent=sum(c.get('estimated_usd',c.get('reserved_usd',0)) for c in result['api_calls'][:-1])
                if worst is None or worst>.20 or spent+worst>5 or result['real_api_calls']>=240:
                    raise RuntimeError('Test budget/call allowance reached')
                row['reserved_usd']=worst;result['real_api_calls']+=1
                response=sdk.messages.create(**request)
                usage=cx.usage_of(response) or {}
                oneh=usage.get('cache_write_1h_tokens',0)
                cost=(usage.get('input_tokens',0)*2+usage.get('output_tokens',0)*10+
                    (usage.get('cache_creation_input_tokens',0)-oneh)*2.5+oneh*4+usage.get('cache_read_input_tokens',0)*.1)/1e6
                tools=[dict(name=b.name,id=b.id,input=b.input) for b in response.content if getattr(b,'type',None)=='tool_use']
                row.update(status='success',usage=usage,estimated_usd=round(cost,8),tools=tools,
                    stop_reason=response.stop_reason,response_model=response.model,request_id=getattr(response,'_request_id',None))
            else:
                old=replay_by_id[context['id']]['calls'][n]
                assert old['step']==context['step'] and old['status']=='success'
                row.update({k:v for k,v in old.items() if k not in ('case','step','request_sha256','elapsed_seconds','estimated_usd')})
                row['estimated_usd']=0;row['replay']=True
                response=SimpleNamespace(model=old['response_model'],stop_reason=old['stop_reason'],
                    usage=SimpleNamespace(input_tokens=0,output_tokens=0,cache_creation_input_tokens=0,cache_read_input_tokens=0),
                    content=[SimpleNamespace(type='tool_use',**t) for t in old['tools']])
            return response
        except Exception as ex:
            row.update(status='error',error_type=type(ex).__name__,http_status=getattr(ex,'status_code',None))
            raise
        finally:
            row['elapsed_seconds']=round(time.perf_counter()-t,3);save()
    client=SimpleNamespace(messages=SimpleNamespace(create=create))
    sim.DEFAULT_OUT_DIR=out/'sim'
    # These operations are outside the test's authority, even on synthetic jobs.
    def forbidden(*a,**k):raise RuntimeError('Approval/export prohibited in NLU suite')
    sim.export_sim=forbidden
    try:
        for case in cases:
            case_calls.clear();context['id']=case['id']
            row=dict(id=case['id'],group=case['group'],text=case['text'],expected_class=case['expected_class'],oracle=case['oracle'],
                     calls=case_calls,steps=[])
            started=time.perf_counter()
            flow=WebFlow(interpreter=ci.ClaudeInterpreter(key,MODEL,client=client),updown_config=None,
                explainer=cx.Explainer(key,MODEL,client=client,usage_log=cx.UsageLog(out/'usage.jsonl')))
            flow.approve=forbidden;flow.export=forbidden
            job=(source/'samples/dummy'/case['job']).read_bytes();row['job_sha256']=digest(job)
            loaded=flow.load(job);sid=loaded['session_id']
            def step(text,number):
                context['step']=number
                if text.startswith('@select:'):return flow.select(sid,text.split(':',1)[1])
                if text=='@meaning_yes':
                    pending=(flow.ask(sid,info=True).get('talk') or {}).get('pending')
                    if not pending:raise ValueError('Meaning confirmation unavailable')
                    choice=next(o['id'] for o in pending['options'] if o['label']=='この動きです')
                    return flow.ask(sid,choose=choice,token=pending['token'])
                if text=='@range_yes':
                    rr=flow.state(sid)['state'].get('repeat_range')
                    if not rr or rr['positions']!=['C00001','C00002','C00003'] or rr['total']!=3:
                        raise ValueError('Expected synthetic repeat range unavailable')
                    return flow.repeat_choose(sid,'yes',rr['turn'])
                return flow.talk(sid,text,'text')
            try:
                for n,text in enumerate(case['setup']):
                    p=step(text,f'setup-{n+1}');row['steps'].append(dict(input=text,response=p))
                    if text=='@range_yes' and not p['state'].get('candidate'):raise ValueError('Candidate unavailable')
                row['before']=flow.state(sid)['state']
                count_before=len(case_calls);p=step(case['text'],'target')
                row.update(response=p,original_unchanged=flow._entries[sid].session.original_bytes==job,
                    calls=list(case_calls),target_calls=list(case_calls[count_before:]))
                if case['expect'].get('check_stale_range_button'):
                    old=row['before'].get('repeat_range')
                    state=p.get('state') or {}
                    if old and not state.get('candidate') and not state.get('repeat_range'):
                        row['stale_probe']=flow.repeat_choose(sid,'yes',old['turn'])
                    else:row['stale_probe']={'status':'not_run','reason':'range still valid or candidate exists; do not confirm it'}
                row.update(judge(case,row))
            except Exception as ex:
                row.update(status='ERROR',error_type=type(ex).__name__,error_reason='test precondition or execution failed; see steps/API status',
                    calls=list(case_calls),safety_violations=[])
            row['elapsed_seconds']=round(time.perf_counter()-started,3)
            result['cases'].append(row);save()
            print(json.dumps(dict(id=case['id'],status=row['status'],calls=len(case_calls),
                seconds=row['elapsed_seconds'],failed=[c['check'] for c in row.get('checks',[]) if not c['passed']])),flush=True)
            if args.mode=='live' and any(c.get('http_status') in (401,402,403) for c in case_calls):break
    finally:
        if sdk:sdk.close()
        result['source_unchanged']=unchanged()
        result['summary']=dict(Counter(c['status'] for c in result['cases']))
        result['not_run']=[c['id'] for c in cases if c['id'] not in {r['id'] for r in result['cases']}]
        result['estimated_usd']=round(sum(c.get('estimated_usd',0) for c in result['api_calls']),8)
        result['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%S%z');save()
    print(json.dumps(dict(summary=result['summary'],real_api_calls=result['real_api_calls'],estimated_usd=result['estimated_usd'])))
    return 2 if result['not_run'] or result['summary'].get('ERROR') else 1 if result['summary'].get('FAIL') else 0


