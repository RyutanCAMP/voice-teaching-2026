"""受入テスト：画面の状態（Session）。発話 → 変更候補 → 比較 → 元に戻す。

仕様v2.1 11章の 5・7・11・15・16・17・18・22 に対応。実装より先に書いた。
Claude はまだ呼ばない。道具の呼び出しは、決まった返事を返す偽のClaude（FakeClaude）で試す。
"""
from pathlib import Path

import pytest

from voice_teaching import jbi
from voice_teaching import utterance as u
from voice_teaching.session import Session

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"
ORIGINAL = DUMMY.read_bytes()


class FakeClaude:
    """偽のClaude：発話を受けたら、決めておいた道具の呼び出しをそのまま session に渡す。"""

    def __init__(self, session):
        self.s = session

    def call(self, tool, args=None):
        if tool == "select":
            return self.s.select(args["position"])
        if tool == "propose_change":
            return self.s.propose(args)
        if tool == "compare":
            return self.s.compare()
        if tool == "undo":
            return self.s.undo()
        raise AssertionError(tool)


SHIFT_10 = {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": 10}


@pytest.fixture
def s():
    return Session.from_file(DUMMY)


def collect(replies):
    out = []
    for r in replies:
        out.append(r.message)
        if r.comparison is not None:
            c = r.comparison
            out += [c.confirmation, *c.affected, *c.warnings]
    return out


# ---- 想定する発話の通しの流れ ----
def test_select_move_compare_undo(s):
    claude = FakeClaude(s)
    replies = []

    r = s.hear("C00005を選択して", source="voice")
    assert r.status == "ok" and r.facts.position == "C00005"
    r = claude.call("select", {"position": r.facts.position})
    replies.append(r)
    assert r.status == "ok" and s.selected == "C00005"
    assert s.candidate is None

    r = s.hear("この位置をX方向に10mm移動して", source="voice")
    assert r.status == "ok"
    r = claude.call("propose_change", SHIFT_10)
    replies.append(r)
    assert r.status == "ok"
    assert s.candidate is not None
    assert s.candidate.before_value == 450.0 and s.candidate.after_value == 460.0
    assert s.candidate.new_job.positions["C00005"].xyz == (460.0, 190.0, 250.0)
    # 17. 大きな確認表示の文はプログラムが差分から作る
    assert "C00005" in r.message and "+10.000mm" in r.message and "460.000" in r.message
    assert r.message == s.confirmation_text()
    # 元のジョブは変わらない
    assert s.original_bytes == ORIGINAL
    assert s.original.render_bytes() == ORIGINAL

    r = s.hear("変更前と比較して", source="voice")
    assert r.status == "ok"
    r = claude.call("compare")
    replies.append(r)
    c = r.comparison
    assert c.before_line == "C00005=450.000,190.000,250.000,180.00,0.00,0.00"
    assert c.after_line == "C00005=460.000,190.000,250.000,180.00,0.00,0.00"
    assert (c.before_value, c.after_value, c.delta) == ("450.000", "460.000", "+10.000")
    assert c.line_number == 14
    assert any("MOVL C00005" in a for a in c.affected)

    r = s.hear("元に戻して", source="voice")
    assert r.status == "ok"
    r = claude.call("undo")
    replies.append(r)
    assert r.status == "ok"
    assert s.candidate is None
    assert s.selected == "C00005"
    assert s.original.render_bytes() == ORIGINAL
    assert s.original_bytes == ORIGINAL
    assert not any(u.contains_forbidden(m) for m in collect(replies))


def test_speed_proposal_is_disabled_by_default():
    """既定では速度の変更を止めている。枠の値があっても候補は作らない"""
    assert jbi.SPEED_CHANGE_ENABLED is False
    s = Session.from_file(DUMMY, max_speed_change_percent=30)
    assert s.hear("C00004の速度を20%上げて").status == "ok"
    r = s.propose({"kind": "speed", "position": "C00004", "percent": 20})
    assert r.status == "stop"
    assert s.candidate is None
    assert jbi.SPEED_DISABLED_MESSAGE in r.message
    assert s.original_bytes == ORIGINAL


@pytest.mark.usefixtures("speed_enabled")
def test_speed_proposal_with_frame_value():
    s = Session.from_file(DUMMY, max_speed_change_percent=30)
    assert s.hear("C00004の速度を20%上げて").status == "ok"
    r = s.propose({"kind": "speed", "position": "C00004", "percent": 20})
    assert r.status == "ok"
    assert s.candidate.kind == "speed"
    assert s.candidate.before_line == "MOVL C00004 V=80.0"
    assert s.candidate.after_line == "MOVL C00004 V=96.0"
    assert "V=80.0" in r.message and "V=96.0" in r.message
    assert s.original_bytes == ORIGINAL


@pytest.mark.usefixtures("speed_enabled")
def test_speed_over_frame_value_stops():
    s = Session.from_file(DUMMY, max_speed_change_percent=30)
    s.hear("C00004の速度を40%上げて")
    r = s.propose({"kind": "speed", "position": "C00004", "percent": 40})
    assert r.status == "stop"
    assert "30" in r.message
    assert s.candidate is None


@pytest.mark.usefixtures("speed_enabled")
def test_speed_without_frame_value_stops(s):
    s.hear("C00004の速度を20%上げて")
    r = s.propose({"kind": "speed", "position": "C00004", "percent": 20})
    assert r.status == "stop"
    assert s.candidate is None


@pytest.mark.usefixtures("speed_enabled")
def test_speed_on_shared_position_stops():
    """C00000 は2つの MOVL が使うので、どの命令の速度か1つに決まらない"""
    s = Session.from_file(DUMMY, max_speed_change_percent=30)
    s.hear("C00000の速度を20%上げて")
    assert s.propose({"kind": "speed", "position": "C00000", "percent": 20}).status == "stop"
    assert s.candidate is None


# ---- 5. 量が分からない指示 ----
def test_no_amount_asks_then_answer_continues(s):
    r = s.hear("C00005をX方向に移動して")
    assert r.status == "ask" and "何mm" in r.message
    # 聞き返し中に Claude が量を作って出しても使わない
    assert s.propose(SHIFT_10).status == "stop"
    assert s.candidate is None
    # 聞き返しへの答えは新しい発話。前の発話の位置・軸に、答えの量を足して読む
    r = s.hear("10ミリ")
    assert r.status == "ok"
    assert r.facts.position == "C00005" and r.facts.axis == "X" and r.facts.requested_value() == 10.0
    assert s.propose(SHIFT_10).status == "ok"


def test_kanji_amount_never_becomes_candidate(s):
    assert s.hear("C00005をX方向に十ミリ移動して").status == "ask"
    assert s.propose(SHIFT_10).status == "stop"
    assert s.candidate is None
    assert s.original_bytes == ORIGINAL


def test_fullwidth_utterance_works(s):
    assert s.hear("Ｃ００００５をＸ方向に１０ｍｍ移動して").status == "ok"
    assert s.propose(SHIFT_10).status == "ok"


# ---- 存在しない位置番号 ----
def test_unknown_position(s):
    r = s.select("C00099")
    assert r.status == "stop" and "C00099" in r.message
    assert s.selected is None
    s.hear("C00099をX方向に10mm移動して")
    r = s.propose({**SHIFT_10, "position": "C00099"})
    assert r.status == "stop" and "C00099" in r.message
    assert s.candidate is None


# ---- 7. 右・左は軸が決まっていないので進まない（聞き返し。Claude の左右の提案は止める） ----
def test_right_left_stops(s):
    r = s.hear("置く位置を右に10ミリ")
    assert r.status == "ask" and "軸" in r.message
    assert s.propose({"kind": "shift", "position": "C00004", "side": "右", "delta_mm": 10}).status == "stop"
    assert s.propose({"kind": "shift", "position": "C00004", "axis": "X", "delta_mm": 10}).status == "stop"
    assert s.candidate is None
    assert s.original_bytes == ORIGINAL


# ---- 16. 量の食い違い → 聞き返し。15. 断られた発話では出し直せない ----
def test_amount_mismatch_then_retry_is_refused(s):
    s.hear("C00005をX方向に10mm移動して")
    r = s.propose({**SHIFT_10, "delta_mm": 15})
    assert r.status == "ask"
    assert s.candidate is None
    r = s.propose(SHIFT_10)  # 同じ発話の中で量を変えて出し直す
    assert r.status == "stop"
    assert s.candidate is None
    # 新しい発話なら受け付ける
    s.hear("C00005をX方向に10mm移動して")
    assert s.propose(SHIFT_10).status == "ok"


def test_rejected_by_job_check_then_retry_is_refused(s):
    s.hear("C00005をX方向に60mm移動して")
    r = s.propose({**SHIFT_10, "delta_mm": 60})
    assert r.status == "stop" and "上限" in r.message
    # 実機用の検査で止まった理由と、候補を作れたかを分けて返す
    assert r.blocked_reason == r.message
    assert r.candidate_made is False
    assert s.propose({**SHIFT_10, "delta_mm": 50}).status == "stop"
    assert s.candidate is None


def test_ok_reply_has_no_blocked_reason(s):
    s.hear("C00005をX方向に10mm移動して")
    r = s.propose(SHIFT_10)
    assert r.candidate_made is True and r.blocked_reason is None
    assert s.candidate_blocked_reason is None


# ---- シミュレーションモード：実機用の検査に断られても、プレビュー用の候補を作れる形 ----
def preview_only(job, proposal, reason):
    """後で voice_teaching/sim.py が作る部分の代わり（ここでは C00005 の候補を作るだけ）。"""
    preview_only.calls.append((proposal, reason))
    return job.plan_shift("C00005", "X", 10)


def test_simulate_makes_preview_candidate_with_blocked_reason():
    preview_only.calls = []
    s = Session.from_file(DUMMY, simulate=preview_only)
    s.hear("C00000をX方向に10mm移動して")  # C00000 は2つの命令が共用 → 実機用の検査で止まる
    r = s.propose({**SHIFT_10, "position": "C00000"})
    assert r.status == "ok" and r.candidate_made is True
    assert r.blocked_reason and "共用" in r.blocked_reason
    assert preview_only.calls[0][1] == r.blocked_reason
    assert s.candidate_blocked_reason == r.blocked_reason
    c = s.compare().comparison
    assert c.blocked_reason == r.blocked_reason
    assert any(r.blocked_reason in w for w in c.warnings)
    assert "シミュレーション" in c.confirmation
    assert s.original_bytes == ORIGINAL


def test_simulate_is_not_used_for_utterance_mismatch():
    preview_only.calls = []
    s = Session.from_file(DUMMY, simulate=preview_only)
    s.hear("C00005をX方向に10mm移動して")
    assert s.propose({**SHIFT_10, "delta_mm": 15}).status == "ask"
    s.hear("置く位置を右に10ミリ")
    assert s.propose({"kind": "shift", "position": "C00004", "side": "右", "delta_mm": 10}).status == "stop"
    assert preview_only.calls == []
    assert s.candidate is None


def test_position_mismatch_asks(s):
    s.hear("C00005をX方向に10mm移動して")
    assert s.propose({**SHIFT_10, "position": "C00004"}).status == "ask"
    assert s.candidate is None


def test_propose_before_any_utterance_stops(s):
    assert s.propose(SHIFT_10).status == "stop"


def test_tool_args_with_job_text_stop(s):
    s.hear("C00005をX方向に10mm移動して")
    r = s.propose({**SHIFT_10, "after_line": "C00005=460.000,190.000,250.000,180.00,0.00,0.00"})
    assert r.status == "stop"
    assert s.candidate is None


# ---- 11. 2つ以上を変える提案は聞き返し。承認前はジョブが変わらない ----
def test_two_changes_ask(s):
    s.hear("C00005をX方向に10mm移動して")
    r = s.propose([SHIFT_10, {**SHIFT_10, "position": "C00004"}])
    assert r.status == "ask"
    assert s.candidate is None
    assert s.original_bytes == ORIGINAL


def test_candidate_does_not_change_original(s):
    s.hear("C00005をX方向に10mm移動して")
    s.propose(SHIFT_10)
    assert s.candidate.new_job.render_bytes() != ORIGINAL
    assert s.original.render_bytes() == ORIGINAL


def test_new_proposal_while_candidate_exists_stops(s):
    s.hear("C00005をX方向に10mm移動して")
    s.propose(SHIFT_10)
    first = s.candidate
    s.hear("C00005をX方向に5mm移動して")
    r = s.propose({**SHIFT_10, "delta_mm": 5})
    assert r.status == "stop"
    assert s.candidate is first


def test_selecting_other_position_discards_candidate(s):
    s.hear("C00005をX方向に10mm移動して")
    s.propose(SHIFT_10)
    r = s.select("C00004")
    assert r.status == "ok"
    assert s.candidate is None
    assert s.selected == "C00004"


def test_compare_and_undo_without_candidate(s):
    assert s.compare().status == "stop"
    r = s.undo()
    assert r.status == "ok"
    assert s.original_bytes == ORIGINAL


# ---- 承認・書き出し・転送・起動は作らない ----
def test_voice_approval_does_nothing(s):
    s.hear("C00005をX方向に10mm移動して")
    s.propose(SHIFT_10)
    r = s.hear("はい、承認して")
    assert r.status == "stop" and "ボタン" in r.message
    assert s.propose(SHIFT_10).status == "stop"
    assert s.original_bytes == ORIGINAL


@pytest.mark.parametrize("name", ["approve", "export", "write", "save", "transfer", "send", "start", "run"])
def test_session_has_no_approve_or_output_methods(s, name):
    assert not hasattr(s, name)


# ---- 22. 音声と文字入力で同じ結果 ----
def test_voice_and_text_give_same_result():
    results = []
    for source in ("voice", "text"):
        s = Session.from_file(DUMMY)
        r1 = s.hear("この位置をX方向に10mm移動して", source=source)
        s.select("C00005")
        r2 = s.propose(SHIFT_10)
        results.append((r1.status, r1.facts.position, r1.facts.requested_value(),
                        r2.status, r2.message, s.candidate.new_job.render_bytes()))
    assert results[0] == results[1]


# ---- 18. 断定の言葉が出ない ----
def test_no_forbidden_words_in_any_message():
    s = Session.from_file(DUMMY, max_speed_change_percent=30)
    replies = []
    for text, tool, args in [
        ("C00005を選択して", "select", {"position": "C00005"}),
        ("この位置をX方向に10mm移動して", "propose_change", {**SHIFT_10, "delta_mm": 15}),
        ("この位置をX方向に10mm移動して", "propose_change", SHIFT_10),
        ("変更前と比較して", "compare", None),
        ("元に戻して", "undo", None),
        ("C00005をX方向に60mm移動して", "propose_change", {**SHIFT_10, "delta_mm": 60}),
        ("C00000をX方向に10mm移動して", "propose_change", {**SHIFT_10, "position": "C00000"}),
        ("置く位置を右に10ミリ", None, None),
        ("C00005をX方向に十ミリ移動して", None, None),
        ("はい", None, None),
        ("ロボットを起動して", None, None),
        ("C00004の速度を20%上げて", "propose_change", {"kind": "speed", "position": "C00004", "percent": 20}),
        ("変更前と比較して", "compare", None),
    ]:
        replies.append(s.hear(text))
        if tool:
            replies.append(FakeClaude(s).call(tool, args))
    messages = collect(replies) + [t for _, t in s.log]
    assert messages
    bad = [m for m in messages if u.contains_forbidden(m)]
    assert bad == []


def test_session_source_has_no_forbidden_words():
    src = (Path(__file__).resolve().parent.parent / "voice_teaching" / "session.py").read_text(encoding="utf-8")
    assert not u.contains_forbidden(src)
