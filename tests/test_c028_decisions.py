"""受入テスト：唐川さんの決定（2026-11-03、CHECKER_028 のあと）。

1. 位置を選び直したときは、未確定の方向・量・対象の位置を捨て、
   「位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。」と出す。
   新しい位置に、前の指示が引き継がれない。
2. 「////FRAME ROBOT」は ///ATTR の直後の行にあるときだけ認める（実機で確かめた形だけ）。ほかの場所は止める。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import jbi
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
RJ = (ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI").read_bytes()
NL = b"\r\n"
RESET = "位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。"


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"], c["delta"]) if c else None


def start(config=None, select="C00005"):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=config)
    sid = w.load(DEMO_C)["session_id"]
    assert w.select(sid, select)["status"] == "ok"
    return w, sid


# ---- 1. 位置の選び直し ----
def test_reset_message_is_the_decided_text():
    assert dg.ENDED_RESELECT == RESET


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("opening", [["置くところを少し手前にして", "そうそう", "3ミリ"],
                                     ["置くところを少し上に", "そうそう"]])
def test_reselect_in_dialogue_resets_and_nothing_carries_over(opening, confirmed, make_updown):
    w, sid = start(make_updown() if confirmed else None)
    for t in opening:
        r = w.hear(sid, t)
    if r["state"]["dialog"]["step"] is None:
        pytest.skip("この状態では会話が始まらない（上下が未確認の案内で止まる）")
    r = w.select(sid, "C00004")
    assert RESET in r["message"]
    d = r["state"]["dialog"]
    assert d["step"] is None and d["amount_mm"] is None and d["direction"] is None
    assert r["state"]["selected"] == "C00004" and cand(r) is None
    for t in ["3ミリ", "そうそう", "はい", "Zマイナス"]:
        r = w.hear(sid, t)
        assert cand(r) is None, t


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("first", ["下げて", "Yのマイナス", "3ミリ", "Zマイナス方向", "Yを3mm下げて"])
def test_reselect_outside_dialogue_drops_the_pending_answer(first, confirmed, make_updown):
    w, sid = start(make_updown() if confirmed else None)
    r = w.hear(sid, first)
    assert cand(r) is None
    w.select(sid, "C00004")
    for t in ["3ミリ", "下げて", "マイナス"]:
        r = w.hear(sid, t)
        assert cand(r) is None, (first, t)


# ---- 2. FRAME の行の場所 ----
def test_frame_right_after_attr_is_accepted():
    job = jbi.parse_bytes(RJ)
    assert job.problems == [] and job.relative_frame == "ROBOT"


def _move_frame_after(marker: bytes) -> bytes:
    frame = b"////FRAME ROBOT" + NL
    assert RJ.count(frame) == 1 and RJ.count(marker) == 1
    data = RJ.replace(frame, b"")
    return data.replace(marker, marker + frame)


@pytest.mark.parametrize("marker", [b"///GROUP1 RB1" + NL, b"NOP" + NL])
def test_frame_not_right_after_attr_is_stopped(marker):
    job = jbi.parse_bytes(_move_frame_after(marker))
    assert job.relative_frame is None
    assert any("FRAME" in p for p in job.problems)
    for name in ("C00002", "C00003", "C00004"):  # FRAME を認めたときに変更できる位置も、止まる
        with pytest.raises(jbi.JbiError):
            job.plan_shift(name, "Z", -3)


def test_comment_between_attr_and_frame_is_stopped():
    attr = b"///ATTR SC,RW,RJ" + NL
    data = RJ.replace(attr, attr + b"///COMM X" + NL) if RJ.count(attr) == 1 else None
    assert data is not None
    job = jbi.parse_bytes(data)
    assert job.relative_frame is None and any("直後" in p for p in job.problems)
