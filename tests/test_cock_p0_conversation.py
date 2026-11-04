"""P0 reproduction. Saved real Claude answer; no API, synthetic JOBs only."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_claude_talk as T


def repeat_candidate(w, sid):
    out = T.repeat_to_three(w, sid)
    assert out['state']['can_approve']


@pytest.mark.parametrize('text', ['2.3回にして', '0.3回にして', '3.5回にして', '２．３回にして', '-3回にして', '2、3回にして', '3e3回にして', '2点3回にして'])
def test_fraction_never_becomes_integer(tmp_path, text):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, text)
    assert not fake.requests
    assert not r['state']['candidate'] and not r['state']['repeat_range']
    assert not r['state']['can_approve']
    visible = ((r.get('ask') or {}).get('reply', '') + (r.get('hear') or {}).get('message', ''))
    assert '整数' in visible or (text == '2点3回にして' and visible)


@pytest.mark.parametrize('text', ['3回くらい', '3回くらいにして', '三回ぐらいにして', '3回程度にして', '約3回にして', 'だいたい3回にして'])
def test_approximate_count_needs_confirmation(tmp_path, text):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, text)
    assert not fake.requests
    assert not r['state']['candidate'] and not r['state']['repeat_range']
    assert '3回でよいですか' in r['ask']['reply']
    # A vague affirmative must not create a range or candidate.
    after = w.talk(sid, 'はい')
    assert not after['state']['candidate'] and not after['state']['repeat_range']


def test_cancelled_candidate_cannot_be_approved_or_exported(tmp_path, monkeypatch):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    repeat_candidate(w, sid)
    old_turn = w._entries[sid].session._range_turn
    r = w.talk(sid, '今の提案は取り消して')
    assert not r['state']['candidate'] and not r['state']['can_approve']
    # Guard against the negative checks accidentally producing an artifact.
    def forbidden(*a, **kw):
        raise AssertionError('Output generation must not be reached')
    monkeypatch.setattr(T.sim, 'export_sim', forbidden)
    assert not w.approve(sid, 'QA')['ok']
    assert not w.export(sid)['ok']
    assert w.repeat_choose(sid, 'yes', old_turn)['status'] == 'stop'
    assert not w.state(sid)['state']['candidate']


@pytest.mark.parametrize('text', ['今の提案は取り消さないで', 'キャンセルはやめて', '元に戻すのはやめて'])
def test_negated_cancel_keeps_candidate(tmp_path, text):
    w, _ = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    repeat_candidate(w, sid)
    old = w.state(sid)['state']['candidate']
    r = w.talk(sid, text)
    assert r['state']['candidate'] == old


@pytest.mark.parametrize('text', ['キャンセルするか迷っている', 'キャンセルかな', '多分キャンセル'])
def test_uncertain_cancellation_is_a_question_not_an_action(tmp_path, text):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    repeat_candidate(w, sid)
    old = w.state(sid)['state']['candidate']
    r = w.talk(sid, text)
    assert r['state']['candidate'] == old and r['hear']['status'] == 'ask'
    assert not fake.requests


def test_approximate_count_with_existing_candidate_still_asks(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    repeat_candidate(w, sid)
    old = w.state(sid)['state']['candidate']
    r = w.talk(sid, '3回くらい')
    assert '3回でよいですか' in r['ask']['reply']
    assert r['state']['candidate'] == old and not r['state']['approval']
    assert not fake.requests and not r['state']['repeat_range']


@pytest.mark.parametrize('text', ['取り消して', 'やっぱりやめて', 'キャンセル', '元に戻して'])
def test_explicit_cancellation_stays_available(tmp_path, text):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    repeat_candidate(w, sid)
    r = w.talk(sid, text)
    assert not r['state']['candidate'] and not r['state']['can_approve']
    assert not fake.requests


def test_explicit_position_amount_followed_by_axis(tmp_path):
    w, _ = T.flow(tmp_path)
    sid = w.load(T.OTHER.read_bytes())['session_id']
    w.select(sid, 'C00005')
    r = w.talk(sid, 'C00002を10mm動かして')
    assert not r['state']['candidate']
    r = w.talk(sid, 'Zプラス')
    c = r['state']['candidate']
    assert c and c['target'] == 'C00002 Z'
    assert float(c['after_value']) - float(c['before_value']) == 10
    assert not r['state']['approval'] and not r['state']['can_export']


def test_reselection_drops_pending_amount(tmp_path):
    w, _ = T.flow(tmp_path)
    sid = w.load(T.OTHER.read_bytes())['session_id']
    w.talk(sid, 'C00002を10mm動かして')
    w.select(sid, 'C00005')
    r = w.talk(sid, 'Zプラス')
    assert not r['state']['candidate']


def test_real_saved_clarification_has_safe_app_question(tmp_path):
    # Exact answer tool input from the preceding real E02 run.
    saved = json.loads((Path(__file__).parent / 'fixtures/cock_p0_saved_answers.json').read_text(encoding='utf-8'))
    raw = saved['tools'][0]['input']
    assert raw['intent'] == 'ask_user'
    w, _ = T.flow(tmp_path, T.answer(raw['reply'], raw['evidence_lines'], raw['intent'], raw['change_request']))
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, 'いい感じにして')
    assert 'どのようにしたいか' in '\n'.join([r['ask']['reply'], *r['ask']['notes']])
    assert not r['state']['candidate'] and not r['state']['repeat_range']


def test_real_transfer_is_not_advertised(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, '実機にジョブを送って')
    msg = r['hear']['message']
    assert '実機への転送' in msg and '対応していません' in msg
    assert not fake.requests and not r['state']['candidate']


def test_normal_two_to_three_still_waits_for_range_button(tmp_path):
    w, _ = T.flow(tmp_path, T.answer('回数のご希望ですね。', (21,), 'change_request',
                                       {'kind': 'repeat', 'from_count': 2, 'to_count': 3}))
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, '2回を3回に変更して')
    assert r['state']['repeat_range']['total'] == 3
    assert not r['state']['candidate'] and not r['state']['can_approve']


@pytest.mark.parametrize('response', [
    T.answer('整数で指定してください。', (), 'ask_user'),
    T.answer('同じ動きをくり返しています。', (21,), 'explain'),
    T.answer('回数のご希望ですね。', (21,), 'change_request',
             {'kind': 'repeat', 'from_count': 2, 'to_count': 4}),
])
def test_model_missing_or_wrong_count_does_not_create_range(tmp_path, response):
    w, _ = T.flow(tmp_path, response)
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, '3回にして')
    assert not r['state']['candidate'] and not r['state']['repeat_range']


def test_api_execution_error_does_not_create_range(tmp_path):
    w, _ = T.flow(tmp_path)  # Fake SDK deliberately has no response.
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, '3回にして')
    assert not r['ask']['ok']
    assert not r['state']['repeat_range'] and not r['state']['candidate']


def test_wrong_explicit_current_count_is_not_dropped(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, '99回を3回にして')
    assert not fake.requests
    assert not r['state']['repeat_range'] and not r['state']['candidate']
    assert '今2回' in r['ask']['reply']


@pytest.mark.parametrize('reply', ['承認しました。何を変えたいですか？', '安全です。何を変えたいですか？'])
def test_clarification_fallback_never_exposes_false_claim(tmp_path, reply):
    w, _ = T.flow(tmp_path, T.answer(reply, (), 'ask_user'))
    sid = w.load(T.demo_bytes())['session_id']
    r = w.talk(sid, 'いい感じにして')
    assert r['ask']['reply_by'] == 'app'
    visible = '\n'.join([r['ask']['reply'], *r['ask']['notes']])
    assert '承認しました' not in visible and '安全です' not in visible
    assert not r['state']['candidate'] and not r['state']['repeat_range']


@pytest.mark.parametrize('answer', ['上', '右', 'Zプラス5mm', 'C00005をZプラス', 'Zプラスに動かして'])
def test_direction_followup_does_not_reuse_amount_for_new_command(tmp_path, answer):
    w, _ = T.flow(tmp_path)
    sid = w.load(T.OTHER.read_bytes())['session_id']
    w.talk(sid, 'C00002を10mm動かして')
    r = w.talk(sid, answer)
    c = r['state']['candidate']
    assert not c or c['target'] != 'C00002 Z' or float(c['after_value']) - float(c['before_value']) != 10
