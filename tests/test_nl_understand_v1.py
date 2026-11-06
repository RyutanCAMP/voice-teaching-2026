"""意味理解（本番候補 v1.0・voice_teaching/nl_understand.py）のテスト。偽の Claude（決めた答え）だけ。実 API は呼ばない。

確かめること：
- Claude の読みを Python が確かめる（根拠の文字列・数の読み直し・cm → mm・位置の一覧・方向の言葉）
- あいまいな量（少し・くらい）は候補にしない（聞き返す）
- 聞き返しの続き（「もう少し右に寄せて」→「5ミリ」）
- 決まった文にして今までの道に通す：右・左・上・下は今までの確かめ（ボタン）・20mm の上限・承認はボタンだけ
- Claude がでたらめを返しても候補を作らない
- 接続の失敗と理解の失敗を分けて言う
- 偽の Claude の解釈の係（MockInterpreter）では、今までの道だけ（意味理解は使わない）
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import nl_understand as nu
from voice_teaching import sim
from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
CP = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
POS = ["C00000", "C00001", "C00002", "C00003", "C00004", "C00005"]


def ans(**kw):
    d = {"intent": "move", "status": "ready", "position_ref": "selected", "position": None, "position_evidence": None,
         "direction": None, "direction_evidence": None, "amount_kind": "none", "amount_value": None, "amount_unit": None,
         "amount_evidence": None, "missing": [], "question": None, "note": ""}
    d.update(kw)
    return nu.check_shape(d)


class FakeUnderstand(ci.MockInterpreter):
    """解釈の係はモックの決まり、意味理解だけ決めた答えを順に返す。答えが足りなければテストが落ちる。"""
    kind = "claude"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def understand(self, state, utterance):
        self.calls.append((state, utterance))
        assert self.answers, f"意味理解を呼ぶはずのない所で呼んだ：{utterance}"
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "sim"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    return d


def setup(*answers, select="C00005"):
    fake = FakeUnderstand(*answers)
    w = WebFlow(interpreter=fake, updown_config=None)
    sid = w.load(CP)["session_id"]
    if select:
        w.select(sid, select)
    return w, sid, fake


def cand(w, sid):
    return w._entries[sid].session.candidate


def press(w, sid, r, label):
    ch = [c for c in (r.get("hear") or {}).get("choices") or [] if c["label"] == label]
    assert ch, r
    return w.hear(sid, ch[0]["text"])


# ---- Python の確かめ（interpret）----
def test_amount_is_reread_by_python_and_cm_is_converted():
    o = nu.interpret(ans(direction="Y+", direction_evidence="Yプラス", amount_kind="stated", amount_value=0.5,
                         amount_unit="cm", amount_evidence="0.5センチ"),
                     "Yプラスに0.5センチ", positions=POS, selected="C00005", previous=None, frame=None)
    assert o.kind == "canonical" and o.text == "C00005をYのプラス5ミリ"


@pytest.mark.parametrize("text,value,unit,ev", [
    ("Yプラスに少し", 5, "mm", "少し"),          # 根拠に数が無い
    ("Yプラスに3ミリ", 5, "mm", "3ミリ"),        # 数が違う
    ("Yプラスに3ミリ", 3, "cm", "3ミリ"),        # 単位が違う
    ("Yプラスに3ミリ", 3, "mm", "5ミリ"),        # 根拠が発話に無い
])
def test_invented_or_mismatched_amount_is_not_used(text, value, unit, ev):
    o = nu.interpret(ans(direction="Y+", direction_evidence="Yプラス", amount_kind="stated", amount_value=value,
                         amount_unit=unit, amount_evidence=ev),
                     text, positions=POS, selected="C00005", previous=None, frame=None)
    assert o.kind == "ask" and "amount" in o.rejected


def test_vague_and_approx_amount_ask_and_never_invent():
    o = nu.interpret(ans(direction="右", direction_evidence="右", amount_kind="vague", status="need_clarification",
                         missing=["amount"]), "もう少し右に寄せて", positions=POS, selected="C00005", previous=None, frame=None)
    assert o.kind == "ask" and o.frame.amount_mm is None and "何ミリ" in o.text
    o = nu.interpret(ans(direction="上", direction_evidence="上に", amount_kind="stated", amount_value=1, amount_unit="cm",
                         amount_evidence="1センチ"), "上に1センチくらい", positions=POS, selected="C00005", previous=None,
                     frame=None)
    assert o.kind == "ask" and o.frame.amount_mm is None and "10ミリくらい" in o.text


def test_position_must_be_said_and_in_the_job():
    o = nu.interpret(ans(position_ref="said", position="C00009", position_evidence="C00009", direction="Z+",
                         direction_evidence="Zプラス", amount_kind="stated", amount_value=3, amount_unit="mm",
                         amount_evidence="3ミリ"), "C00009をZプラス3ミリ", positions=POS, selected=None, previous=None,
                     frame=None)
    assert o.kind == "ask" and "position" in o.rejected
    o = nu.interpret(ans(position_ref="said", position="C00004", position_evidence="C00004", direction="Z+",
                         direction_evidence="Zプラス", amount_kind="stated", amount_value=3, amount_unit="mm",
                         amount_evidence="3ミリ"), "Zプラス3ミリ", positions=POS, selected="C00005", previous=None,
                     frame=None)
    assert o.kind == "ask" and "position" in o.rejected  # 言っていない位置
    o = nu.interpret(ans(position_ref="selected", direction="Z+", direction_evidence="Zプラス", amount_kind="stated",
                         amount_value=3, amount_unit="mm", amount_evidence="3ミリ"),
                     "Zプラス3ミリ", positions=POS, selected=None, previous=None, frame=None)
    assert o.kind == "ask" and o.frame.missing() == ["position"]


@pytest.mark.parametrize("d,ev,text", [
    ("Z+", "Zマイナス", "Zマイナス3ミリ"),   # 符号が逆
    ("Y+", "Zプラス", "Zプラス3ミリ"),       # 軸が違う
    ("右", "左", "左に3ミリ"),               # 右左が逆
    ("Z+", "上", "上に3ミリ"),               # 上を Claude が Z にした
])
def test_direction_needs_matching_words(d, ev, text):
    o = nu.interpret(ans(direction=d, direction_evidence=ev, amount_kind="stated", amount_value=3, amount_unit="mm",
                         amount_evidence="3ミリ"), text, positions=POS, selected="C00005", previous=None, frame=None)
    assert o.kind == "ask" and "direction" in o.rejected


def test_frame_keeps_slots_and_fills_from_answer():
    f = nu.Frame(position="C00005", direction="右")
    o = nu.interpret(ans(direction="右", amount_kind="stated", amount_value=5, amount_unit="mm", amount_evidence="5ミリ"),
                     "5ミリ", positions=POS, selected="C00005", previous=None, frame=f)
    assert o.kind == "canonical" and o.text == "C00005を右に5ミリ動かして"
    # 根拠なしに前と違う方向にはしない
    o = nu.interpret(ans(direction="左", amount_kind="stated", amount_value=5, amount_unit="mm", amount_evidence="5ミリ"),
                     "5ミリ", positions=POS, selected="C00005", previous=None, frame=f)
    assert o.kind == "ask" and "direction" in o.rejected


def test_same_and_additional_use_python_arithmetic():
    prev = nu.Previous("C00005", "Z", 5.0)
    o = nu.interpret(ans(position_ref="previous", amount_kind="same_as_previous", direction="Z+",
                         direction_evidence="Zプラス"), "さっきと同じだけZプラス", positions=POS, selected="C00005",
                     previous=prev, frame=None)
    assert o.text == "C00005をZのプラス5ミリ"
    o = nu.interpret(ans(amount_kind="additional", amount_value=2, amount_unit="mm", amount_evidence="2ミリ"),
                     "あと2ミリ", positions=POS, selected="C00005", previous=prev, frame=None)
    assert o.text == "C00005をZのプラス7ミリ"  # 元からの合計（上限は今までの判定）
    o = nu.interpret(ans(amount_kind="additional", amount_value=2, amount_unit="mm", amount_evidence="2ミリ"),
                     "あと2ミリ", positions=POS, selected="C00005", previous=None, frame=None)
    assert o.kind == "ask" and "amount" in o.rejected


def test_not_move_and_frame_gives_up_after_three_turns():
    assert nu.interpret(ans(intent="question", status="not_a_move"), "何してるの", positions=POS, selected=None,
                        previous=None, frame=None).kind == "not_move"
    f = nu.Frame(turns=nu.MAX_FRAME_TURNS)
    o = nu.interpret(ans(amount_kind="vague"), "少し", positions=POS, selected="C00005", previous=None, frame=f)
    assert o.kind == "ask" and o.frame is None


def test_shape_check_rejects_extra_or_bad_fields():
    good = ans()
    for bad in ({**good, "extra": 1}, {**good, "intent": "approve"}, {**good, "amount_value": "5"},
                {**good, "missing": ["speed"]}, {**good, "direction": "斜め"}):
        with pytest.raises(nu.UnderstandError):
            nu.check_shape(bad)


def test_claude_question_is_filtered():
    f = nu.Frame(position="C00005", direction="Z+", amount_mm=3.0)
    assert nu.ask_text(f, "承認しましたか？") == "C00005 をZプラスに3mmですね。もう一度、言い方を変えて教えてください"
    assert nu.ask_text(nu.Frame(), None) == nu._ASK["position"]


def test_request_shape_has_schema_and_no_unsupported_params():
    r = nu.build_request("claude-sonnet-5-5", nu.build_state(POS, "C00005", None, None, []), "右に5ミリ")
    assert r["output_config"]["format"]["type"] == "json_schema"
    for k in ("temperature", "top_p", "top_k", "tools", "tool_choice", "thinking"):
        assert k not in r
    assert "座標" not in r["messages"][0]["content"] or "values" not in r["messages"][0]["content"]


# ---- 画面の流れ（WebFlow）----
def test_followup_dialog_right_then_5mm_goes_through_facing_button(out):
    w, sid, fake = setup(
        ans(direction="右", direction_evidence="右", amount_kind="vague", status="need_clarification", missing=["amount"]),
        ans(direction="右", amount_kind="stated", amount_value=5, amount_unit="mm", amount_evidence="5ミリ"))
    r = w.talk(sid, "もう少し右に寄せて")
    assert r["hear"]["status"] == "ask" and "何ミリ" in r["hear"]["message"] and cand(w, sid) is None
    r = w.talk(sid, "5ミリ")
    # 右は軸が決まっていない：今までの向きの確かめ（ボタン）。勝手に Y にしない
    assert cand(w, sid) is None and r["hear"]["status"] == "ask"
    assert [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]
    r = press(w, sid, r, "合っている")
    c = cand(w, sid)
    assert c is not None and c.target == "C00005 Y" and c.after_value - c.before_value == pytest.approx(5.0)
    # 承認はボタンだけ（声の「はい」では承認しない）
    r = w.talk(sid, "はい")
    assert w._entries[sid].approval is None


def test_vertical_words_go_through_updown_gate_when_unconfirmed(out):
    w, sid, fake = setup(ans(direction="上", direction_evidence="上に", amount_kind="stated", amount_value=1,
                             amount_unit="cm", amount_evidence="1センチ"))
    r = w.talk(sid, "ちょっとだけ上に1センチ動かして")
    assert cand(w, sid) is None  # 上下の向きが未確認：Z にしない
    assert r["hear"]["status"] in ("ask", "stop")


def test_over_limit_is_refused_by_existing_check(out):
    w, sid, fake = setup(ans(direction="Z+", direction_evidence="Zのプラス", amount_kind="stated", amount_value=3,
                             amount_unit="cm", amount_evidence="3センチ"))
    r = w.talk(sid, "Zのプラスに3センチずらしてほしい")
    assert cand(w, sid) is None and r["hear"]["status"] == "stop"
    assert "20" in r["hear"]["message"]


def test_invented_answer_makes_no_candidate(out):
    w, sid, fake = setup(ans(direction="Z+", direction_evidence="Zプラス", amount_kind="stated", amount_value=5,
                             amount_unit="mm", amount_evidence="5ミリ"))
    r = w.talk(sid, "Zプラスにちょっとだけ")
    assert cand(w, sid) is None and r["hear"]["status"] == "ask"


def test_api_error_is_told_as_connection_problem(out):
    w, sid, fake = setup(nu.UnderstandError("api", ci.NETWORK))
    r = w.talk(sid, "もうちょい右へ")
    assert cand(w, sid) is None and r["hear"]["status"] == "stop"
    assert "ネットワーク" in r["hear"]["message"] and "分かりませんでした" not in r["hear"]["message"]


def test_malformed_is_not_reported_as_network(out):
    w, sid, fake = setup(nu.UnderstandError("malformed", nu.MALFORMED))
    r = w.talk(sid, "もうちょい右へ")
    assert cand(w, sid) is None and "形が崩れて" in r["hear"]["message"] and "ネットワーク" not in r["hear"]["message"]


def test_clear_instruction_does_not_call_claude_understand(out):
    w, sid, fake = setup()
    r = w.talk(sid, "Zプラス方向に5ミリ")
    assert cand(w, sid) is not None and fake.calls == []


def test_no_position_is_rescued_and_filled_after_select(out):
    w, sid, fake = setup(
        ans(position_ref="none", direction="Z+", direction_evidence="Zプラス", amount_kind="stated", amount_value=5,
            amount_unit="mm", amount_evidence="5ミリ", status="need_clarification", missing=["position"]),
        ans(position_ref="selected", amount_kind="none"), select=None)
    r = w.talk(sid, "Zプラス方向に5ミリ")
    assert cand(w, sid) is None and "どの位置" in r["hear"]["message"]
    w.select(sid, "C00005")
    r = w.talk(sid, "その位置でお願い")
    c = cand(w, sid)
    assert c is not None and c.target == "C00005 Z" and c.after_value - c.before_value == pytest.approx(5.0)


def test_frame_is_dropped_when_another_position_is_selected(out):
    w, sid, fake = setup(ans(direction="右", direction_evidence="右", amount_kind="vague",
                             status="need_clarification", missing=["amount"]))
    w.talk(sid, "もう少し右に寄せて")
    assert w._entries[sid].nl_frame is not None
    w.select(sid, "C00004")
    assert w._entries[sid].nl_frame is None


def test_buttons_words_and_pending_candidate_never_call_understand(out):
    w, sid, fake = setup()
    for t in ("承認して", "出力して", "はい"):
        w.talk(sid, t)
    w.talk(sid, "Zプラス方向に5ミリ")
    assert cand(w, sid) is not None
    w.talk(sid, "もうちょっと右")  # 候補がある：今までの道（K1 の保留）
    assert fake.calls == []


def test_mock_interpreter_keeps_legacy_path():
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    r = w.talk(sid, "もう少し右に寄せて")
    assert "nl" not in (r.get("hear") or {})


def test_question_goes_to_explainer_not_understand(out):
    w, sid, fake = setup()
    w.talk(sid, "このロボットは何をしているの？")
    assert fake.calls == []


def test_state_sent_to_claude_has_no_coordinates(out):
    w, sid, fake = setup(ans(direction="右", direction_evidence="右", amount_kind="vague",
                             status="need_clarification", missing=["amount"]))
    w.talk(sid, "もう少し右に寄せて")
    st, said = fake.calls[0]
    assert said == "もう少し右に寄せて"
    assert set(st) == {"positions", "selected_position", "previous_operation", "pending", "recent_turns"}
    assert "." not in "".join(str(v) for v in st["positions"])


def test_facing_confirmation_kept_only_for_the_same_position(out):
    """D5（11/6 唐川さんの決定）：右・左の対応は、確かめた位置・JOB の中だけで覚える。位置が変われば確かめ直す。"""
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    r = w.talk(sid, "右に3ミリ動かして")
    assert [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]
    press(w, sid, r, "合っている")
    assert cand(w, sid) is not None
    w.undo(sid)
    r = w.talk(sid, "右に2ミリ動かして")  # 同じ位置：もう一度は聞かない
    assert cand(w, sid) is not None and not r["hear"]["choices"]
    w.undo(sid)
    w.select(sid, "C00004")
    r = w.talk(sid, "右に2ミリ動かして")  # 位置が変わった：確かめ直す
    assert cand(w, sid) is None and [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]
    sid2 = w.load(CP)["session_id"]  # JOB を読み直した：覚えていない
    w.select(sid2, "C00005")
    r = w.talk(sid2, "右に2ミリ動かして")
    assert [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]


def test_answer_after_legacy_updown_question_keeps_direction_but_needs_button(out):
    """C06（11/7 実 Claude の評価）：「上に動かしたい」（今までの道が上下の向きを確かめる）→「4ミリ」。
    前の発話の「上」を枠に残し、決まった文（4ミリ上げて）にしてから、もう一度ボタンで確かめる（黙って Z にしない：P0-1）。"""
    w, sid, fake = setup(ans(position_ref="selected", amount_kind="stated", amount_value=4, amount_unit="mm",
                             amount_evidence="4ミリ"))
    r = w.talk(sid, "上に動かしたい")
    assert [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"] and fake.calls == []
    r = w.talk(sid, "4ミリ")
    assert fake.calls and fake.calls[0][0]["pending"]["direction"] == "上"
    assert cand(w, sid) is None and [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]
    press(w, sid, r, "合っている")
    c = cand(w, sid)
    assert c is not None and c.target == "C00005 Z" and c.after_value - c.before_value == pytest.approx(4.0)


def test_followup_may_repeat_the_position_already_in_frame():
    """C02：前の発話で言った位置を、Claude が続きの答えでも said で返す（今の発話には無い）。枠と同じなら使う。"""
    f = nu.Frame(position="C00003", direction="Y-")
    o = nu.interpret(ans(position_ref="said", position="C00003", position_evidence="C00003", direction="Y-",
                         amount_kind="stated", amount_value=3, amount_unit="mm", amount_evidence="3ミリ"),
                     "3ミリ", positions=POS, selected="C00004", previous=None, frame=f)
    assert o.kind == "canonical" and o.text == "C00003をYのマイナス3ミリ"
    o = nu.interpret(ans(position_ref="said", position="C00002", position_evidence="C00002", direction="Y-",
                         amount_kind="stated", amount_value=3, amount_unit="mm", amount_evidence="3ミリ"),
                     "3ミリ", positions=POS, selected="C00004", previous=None, frame=f)
    assert o.kind == "ask" and "position" in o.rejected  # 枠と違う位置は、今の発話に根拠が無ければ使わない


def test_legacy_keeps_spoken_position_across_answers(out):
    """NLR-C02（11/7 モックの評価で見つけた、今までの道の誤った候補）：「C00003を動かしたい」→「Yのマイナス」→「3ミリ」で、
    口で言った C00003 を捨てて、選んでいる C00004 の候補を作っていた。今は C00003 のまま聞き返しを続ける。"""
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load((ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI").read_bytes())["session_id"]
    w.select(sid, "C00004")
    w.talk(sid, "C00003を動かしたい")
    w.talk(sid, "Yのマイナス")
    w.talk(sid, "3ミリ")
    c = cand(w, sid)
    assert c is not None and c.target == "C00003 Y" and c.after_value - c.before_value == pytest.approx(-3.0)


@pytest.mark.parametrize("cancel", ["やっぱりやめる", "だめ", "取り消して", "いや、いいです"])
def test_frame_is_dropped_on_cancel_or_negation(out, cancel):
    """聞き返しの途中で、やめる・否定の言葉：持っていた方向・量を、次の文（「5ミリ」）に当てない（11/7）。"""
    w, sid, fake = setup(ans(direction="右", direction_evidence="右", amount_kind="vague",
                             status="need_clarification", missing=["amount"]),
                         ans(amount_kind="stated", amount_value=5, amount_unit="mm", amount_evidence="5ミリ"))
    w.talk(sid, "もう少し右に寄せて")
    assert w._entries[sid].nl_frame is not None
    w.talk(sid, cancel)
    assert w._entries[sid].nl_frame is None
    w.talk(sid, "5ミリ")
    assert cand(w, sid) is None
