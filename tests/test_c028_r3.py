"""受入テスト：差分の再レビュー（fa72550..77f6ef3）の QA P1-2・QA P2-1〜5・Safety P2-a。実装より先に書いた。

QA P1-2：承認のあとに候補も捨てる指示（元に戻して・キャンセル・別の位置の選択）では、「変更候補は残しています」の決まった文を
  使わない。候補が残るときだけ決まった文（APPROVED_PENDING_MESSAGE）、候補も捨てたときは別の定数
  （flow.APPROVAL_AND_CANDIDATE_CANCELLED。唐川さんの決定の文）。
QA P2-1・P2-2：同じ位置・ジョブに無い位置の選択では、位置の選び直しの文を出さない。声とボタンで同じ考え方の文にする
  （候補を捨てたかどうかで決める）。ジョブに無い位置を選ぼうとしただけでは、承認を取り消さない（何も変わっていない）。
QA P2-3：「〜します」の言い直しの文にするのは、答えを待つ形の質問（needs_answer）だけ。2つの方向・単位・2つの量・
  はっきりしない量などの具体的な理由はそのまま返す。
QA P2-4：「Zに3mm動かします」は、［Zのプラス 3mm］［Zのマイナス 3mm］のボタンと、その選び方を言う文をそろえる。
QA P2-5・Safety P2-a：POS 部の値の形が違う指示行（///TOOL 64、///RECTAN X）は、最後の位置より後ろでも止め、
  ツール番号が範囲外なら、そのことが分かる理由にする。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi
from voice_teaching import session as session_mod
from voice_teaching import utterance as ut
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
NL = b"\r\n" if b"\r\n" in DEMO_C else b"\n"
SOURCES = ["voice", "text"]
KEPT = session_mod.APPROVED_PENDING_MESSAGE
DROPPED = flow_mod.APPROVAL_AND_CANDIDATE_CANCELLED
RESELECT = "位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。"


def approved(tmp_path):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=tmp_path / "none.json")
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    assert w.hear(sid, "Zマイナス3ミリ")["state"]["candidate"] is not None
    assert w.approve(sid, "唐川")["ok"] is True
    return w, sid


def test_constants():
    assert KEPT == "新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。内容を確認して、もう一度承認してください。"
    assert DROPPED == "前の承認と変更候補を取り消しました。元のJOBは変更されていません。"
    assert not ut.contains_forbidden(DROPPED)


# ======================================================================
# QA P1-2・P2-1・P2-2：候補が残るか残らないかで文を決める
# ======================================================================
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["元に戻して", "キャンセル", "C00004を選択して"])
def test_p1_2_dropping_the_candidate_uses_the_other_text(text, source, tmp_path):
    w, sid = approved(tmp_path)
    r = w.hear(sid, text, source)
    st = r["state"]
    assert st["approval"] is None and st["candidate"] is None
    assert r["message"].startswith(DROPPED), r["message"]
    assert "変更候補は残しています" not in r["message"]
    assert r["message"].count("元のJOBは変更されていません") == 1 and "元のジョブは変わっていません" not in r["message"], r["message"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["C00005を選択して", "比較して", "えーと"])
def test_p1_2_keeping_the_candidate_uses_the_decided_text(text, source, tmp_path):
    w, sid = approved(tmp_path)
    r = w.hear(sid, text, source)
    assert r["state"]["approval"] is None and r["state"]["candidate"] is not None
    assert r["message"].startswith(KEPT) and DROPPED not in r["message"]


def test_p2_button_select_other_position(tmp_path):
    w, sid = approved(tmp_path)
    r = w.select(sid, "C00004")
    assert r["state"]["approval"] is None and r["state"]["candidate"] is None
    assert r["message"].startswith(DROPPED) and "変更候補は残しています" not in r["message"]
    assert r["message"].count("元のJOBは変更されていません") == 1 and "元のジョブは変わっていません" not in r["message"], r["message"]


def test_p2_1_button_select_same_position(tmp_path):
    w, sid = approved(tmp_path)
    r = w.select(sid, "C00005")
    assert r["state"]["approval"] is None and r["state"]["candidate"] is not None
    assert r["message"].startswith(KEPT) and RESELECT not in r["message"], r["message"]


@pytest.mark.parametrize("pos", ["C99999", "X1", ""])
def test_p2_1_button_select_missing_position_changes_nothing(pos, tmp_path):
    w, sid = approved(tmp_path)
    r = w.select(sid, pos)
    assert r["status"] == "stop"
    assert RESELECT not in r["message"] and KEPT not in r["message"] and DROPPED not in r["message"]
    st = r["state"]
    assert st["approval"] is not None and st["candidate"] is not None  # 何も変わっていない


def test_p2_2_voice_and_button_select_say_the_same(tmp_path):
    w1, s1 = approved(tmp_path)
    w2, s2 = approved(tmp_path)
    a = w1.hear(s1, "C00004を選択して")["message"]
    b = w2.select(s2, "C00004")["message"]
    assert a.startswith(DROPPED) and b.startswith(DROPPED)
    w3, s3 = approved(tmp_path)
    w4, s4 = approved(tmp_path)
    assert w3.hear(s3, "C00005を選択して")["message"].startswith(KEPT)
    assert w4.select(s4, "C00005")["message"].startswith(KEPT)


def test_p2_no_text_without_approval(tmp_path):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=tmp_path / "none.json")
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.hear(sid, "Zマイナス3ミリ")
    for m in (w.select(sid, "C00005")["message"], w.hear(sid, "元に戻して")["message"]):
        assert KEPT not in m and DROPPED not in m and RESELECT not in m


def test_undo_button_keeps_its_text(tmp_path):
    w, sid = approved(tmp_path)
    r = w.undo(sid)
    assert r["message"] == "変更候補を捨てました。元のジョブは変わっていません"


# ======================================================================
# QA P2-3：具体的な理由はそのまま返す
# ======================================================================
@pytest.mark.parametrize("text,part", [
    ("XとYに3mm動かします", "2つ"),
    ("Zマイナス3%動かします", "mm（ミリ）"),
    ("Zマイナス3mmと5mm動かします", "量が2つ以上"),
    ("Zマイナス三ミリ動かします", "プラス・マイナスがどこに付くか"),  # ふつうの指示と同じ具体的な理由
    ("少し下げて動かします", "量がはっきりしません"),
])
def test_p2_3_specific_reasons_are_kept(text, part, make_updown):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=make_updown())
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text)
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    assert part in r["message"] and "一緒に、もう一度言ってください" not in r["message"], (text, r["message"])
    # 「〜します」を外したふつうの指示と同じ答え（具体的な理由）
    w0 = WebFlow(interpreter=ci.MockInterpreter(), updown_config=make_updown(name="b.json"))
    s0 = w0.load(DEMO_C)["session_id"]
    w0.select(s0, "C00005")
    plain = w0.hear(s0, text.replace("動かします", "動かして"))
    assert plain["message"] == r["message"], (plain["message"], r["message"])


@pytest.mark.parametrize("text", ["動かします", "3mmずらします", "下に動かします"])
def test_p2_3_waiting_questions_still_become_restate(text, make_updown):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=make_updown())
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text)
    assert "一緒に、もう一度言ってください" in r["message"] and r["choices"] == []


# ======================================================================
# QA P2-4：ボタンと文をそろえる
# ======================================================================
@pytest.mark.parametrize("text", ["Zに3mm動かします", "Zを3mm動かします"])
def test_p2_4_sign_buttons_and_message_match(text, tmp_path):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=tmp_path / "none.json")
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text)
    labels = [c["label"] for c in r["choices"]]
    assert labels == ["Zのプラス 3mm", "Zのマイナス 3mm"]
    assert "プラスかマイナスかを、ボタンで選んでください" in r["message"], r["message"]
    r2 = w.hear(sid, r["choices"][1]["text"])  # 押したボタンは、ふつうの指示として全部の検査を通る
    assert (r2["state"]["candidate"]["target"], r2["state"]["candidate"]["delta"]) == ("C00005 Z", "-3.000")


# ======================================================================
# QA P2-5・Safety P2-a：POS 部の値の形が違う指示行
# ======================================================================
def _insert_before_inst(line: bytes) -> bytes:
    return DEMO_C.replace(NL + b"//INST" + NL, NL + line + NL + b"//INST" + NL, 1)


@pytest.mark.parametrize("line", [b"///TOOL 64", b"///RECTAN X", b"///USER A", b"///POSTYPE robot", b"///RCONF x"])
def test_safety_p2a_bad_directive_after_last_position_stops_the_job(line):
    data = _insert_before_inst(line)
    assert data != DEMO_C
    job = jbi.parse_bytes(data)
    assert job.problems, line
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00005", "Z", -3)


@pytest.mark.parametrize("data", [
    DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 64" + NL),
    _insert_before_inst(b"///TOOL 64"),
])
def test_p2_5_tool_out_of_range_says_so(data):
    job = jbi.parse_bytes(data)
    text = "".join(job.problems)
    assert "ツール番号" in text and "0〜63" in text, job.problems
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00005", "Z", -3)


def test_good_directives_are_unchanged():
    assert not jbi.parse_bytes(DEMO_C).problems
    assert not jbi.parse_bytes(DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 63" + NL)).problems


# ======================================================================
# 唐川さんの決定：画面の文と内部の状態が必ず一致する（どの操作でも）
# ======================================================================
def _ops():
    def hear(t, src="voice"):
        return lambda w, sid: w.hear(sid, t, src)

    def select(p):
        return lambda w, sid: w.select(sid, p)

    def choose_after_dialog(w, sid):
        # 承認のあとに、場所の言葉で会話を始めようとした（承認待ちの候補があるので止まる）
        return w.hear(sid, "置くところを少し手前に")

    return {
        "声 元に戻して": hear("元に戻して"), "文字 元に戻して": hear("元に戻して", "text"),
        "声 キャンセル": hear("キャンセル"), "声 C00004を選択して": hear("C00004を選択して"),
        "文字 C00004を選択して": hear("C00004を選択して", "text"), "声 C00005を選択して": hear("C00005を選択して"),
        "声 C99999を選択して": hear("C99999を選択して"), "声 比較して": hear("比較して"), "声 えーと": hear("えーと"),
        "声 下げて": hear("下げて"), "声 Yのマイナス3ミリ": hear("Yのマイナス3ミリ"), "声 動かします": hear("動かします"),
        "会話の始め": choose_after_dialog,
        "ボタン C00004": select("C00004"), "ボタン C00005": select("C00005"), "ボタン C99999": select("C99999"),
        "ボタン 形の違う位置": select("X1"),
    }


@pytest.mark.parametrize("op", list(_ops()))
def test_message_and_state_always_agree(op, tmp_path):
    w, sid = approved(tmp_path)
    r = _ops()[op](w, sid)
    msg = r.get("message") or ""
    st = r["state"]
    if msg.startswith(KEPT):
        assert st["candidate"] is not None and st["approval"] is None, op
    elif msg.startswith(DROPPED):
        assert st["candidate"] is None and st["approval"] is None, op
    else:
        # どちらの文も出さないのは、承認が残っているときだけ（何も変えていない）
        assert st["approval"] is not None and st["candidate"] is not None, (op, msg)
    assert KEPT not in msg[1:] and DROPPED not in msg[1:], (op, msg)  # 頭のほかに重ねない


@pytest.mark.parametrize("op", list(_ops()))
def test_neither_text_without_approval(op, tmp_path):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=tmp_path / "none.json")
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.hear(sid, "Zマイナス3ミリ")
    r = _ops()[op](w, sid)
    msg = r.get("message") or ""
    assert KEPT not in msg and DROPPED not in msg, (op, msg)
