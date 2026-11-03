"""受入テスト：唐川さんの新しい決定（2026-11-03 夕方）。実装より先に書いた。

1. 相対ジョブ（RJ）では、INST 部の始めの指示行がちょうど ///DATE … → ///ATTR SC,RW,RJ → ////FRAME ROBOT → ///GROUP1 RB1 の順
   （その後に命令）のときだけ扱う。ほかの並びは止める。
   相対ジョブでないジョブには並びを求めない：samples/dummy の PAT_SJIS_COMMENT（・_CRLF）が ///DATE と ///ATTR の間に
   ///COMM を持つため（決定の「違うものがあれば相対ジョブだけに限る」）。
2. ///ATTR の行が無い JOB は変更の対象にしない（problems「///ATTR の行がありません」。全部の位置を止める）。
3. 「3ミリ下げます」（〜ます）は変更の希望として受け付ける。方向の確認（上下の設定）と承認（ボタン）は今どおり必須。
   「〜します」（M3）の扱いは変えない。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi
from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy"
RJ = (DUMMY / "DEMO_RJ_XYZ.JBI").read_bytes()
DEMO_C = (DUMMY / "DEMO_C_PLACE.JBI").read_bytes()
NL = b"\r\n" if b"\r\n" in RJ else b"\n"
DATE = b"///DATE 2026/11/03 16:00"
ATTR = b"///ATTR SC,RW,RJ"
FRAME = b"////FRAME ROBOT"
GROUP = b"///GROUP1 RB1"
HEAD = DATE + NL + ATTR + NL + FRAME + NL + GROUP + NL
assert HEAD in RJ


def with_head(*lines: bytes) -> bytes:
    return RJ.replace(HEAD, b"".join(x + NL for x in lines), 1)


def test_rj_sample_in_the_right_order_is_editable():
    job = jbi.parse_bytes(RJ)
    assert not job.problems and job.relative_frame == "ROBOT"
    job.plan_shift("C00002", "Z", -3)


@pytest.mark.parametrize("name,lines", [
    ("ATTR・FRAME が DATE の前", (ATTR, FRAME, DATE, GROUP)),
    ("ATTR・FRAME が GROUP1 の後ろ", (DATE, GROUP, ATTR, FRAME)),
    ("DATE が無い", (ATTR, FRAME, GROUP)),
    ("GROUP1 が DATE の前", (GROUP, DATE, ATTR, FRAME)),
    ("DATE と ATTR の間にコメントの指示行", (DATE, b"///COMM TEST", ATTR, FRAME, GROUP)),
    ("FRAME と GROUP1 の間に別の行", (DATE, ATTR, FRAME, b"///COMM TEST", GROUP)),
    ("GROUP1 の後ろに別の指示行", (DATE, ATTR, FRAME, GROUP, b"///LVARS 0,0")),
    ("GROUP1 が無い", (DATE, ATTR, FRAME)),
])
def test_rj_other_orders_stop(name, lines):
    job = jbi.parse_bytes(with_head(*lines))
    assert job.problems, name
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00002", "Z", -3)


def test_rj_order_reason_is_short_and_clear():
    job = jbi.parse_bytes(with_head(DATE, GROUP, ATTR, FRAME))
    assert any("並び" in p for p in job.problems), job.problems


def test_non_rj_jobs_do_not_need_the_order():
    # 相対ジョブでないジョブは、今どおり（///COMM が間にある合成ジョブも読む）
    for name in ("PAT_SJIS_COMMENT.JBI", "PAT_SJIS_COMMENT_CRLF.JBI", "DEMO_C_PLACE.JBI", "PAT_LVARS.JBI"):
        job = jbi.parse_bytes((DUMMY / name).read_bytes())
        assert not any("並び" in p for p in job.problems), (name, job.problems)


def test_all_samples_have_attr():
    for p in DUMMY.glob("*.JBI"):
        assert b"///ATTR " in p.read_bytes(), p.name


@pytest.mark.parametrize("data", [
    DEMO_C.replace(b"///ATTR SC,RW" + NL, b""),
    RJ.replace(ATTR + NL, b"").replace(FRAME + NL, b""),
])
def test_no_attr_stops_every_position(data):
    job = jbi.parse_bytes(data)
    assert "///ATTR の行がありません" in job.problems, job.problems
    for name in job.positions:
        with pytest.raises(jbi.JbiError):
            job.plan_shift(name, "Z", -1)


# ======================================================================
# 3. 「〜ます」は変更の希望として受け付ける（方向の確認・承認は今どおり）
# ======================================================================
MASU = [
    ("3ミリ下げます", "updown", ("C00005 Z", "-3.000")),
    ("下に3ミリ下げます", "updown", ("C00005 Z", "-3.000")),
    ("Zマイナス3ミリ下げます", "any", None),           # 符号が2つ（Zマイナス＋下げ）：聞き返し
    ("Zマイナス3ミリずらします", "shimasu", None),      # 「〜します」は M3 のまま（2択）
    ("Yのマイナス3ミリ移動させます", "any", ("C00005 Y", "-3.000")),
    ("Z座標を3ミリ下げます", "any", ("C00005 Z", "-3.000")),
    ("3ミリ上げます", "updown", ("C00005 Z", "+3.000")),
]


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("mode", ["none", "confirmed"])
@pytest.mark.parametrize("text,kind,want", MASU)
def test_masu_is_a_request_with_the_usual_checks(text, kind, want, mode, source, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text, source)
    c = r["state"]["candidate"]
    got = None if c is None else (c["target"], c["delta"])
    if kind == "shimasu":
        assert got is None and [x["label"] for x in r["choices"]] == ["変更したい", "説明です"], r["message"]
        return
    if kind == "updown" and mode == "none":
        assert got is None and r["message"] == w._entries[sid].session.updown.message, r["message"]
        return
    if kind == "any" and want is None:
        assert got is None and r["status"] == "ask", r["message"]
        return
    assert got == want, (text, mode, r["message"])
    assert not ut.read_utterance(text).unknown_words
    # 承認は画面のボタン（approve）だけ。「〜ます」の言葉では承認しない
    assert r["state"]["approval"] is None and r["state"]["can_approve"] is True
