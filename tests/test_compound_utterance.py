"""2つの変更が入った指示（「上に2mm、Yプラスに3mm」）：各軸の量を示して、1つずつ指示してもらう（1つにまとめない）。

唐川さんの最終指示（2026-11-02 夜）：複数の変更量を合成する場合は、各軸の最終変更量を明示し、既存の安全ガードで検証。
今は1回に1つの方向だけを変える決まり（ガード・承認・出力は1つの変更が前提）なので、まとめずに聞き返し、
各軸の量（プログラムが読んだ値）を示す。まとめて動かす機能は、ガードの設計とあわせて別に決める。
"""
from pathlib import Path

from voice_teaching import utterance as u
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def ask(text):
    return u.pre_check(u.read_utterance(text))


def test_two_moves_are_listed_per_axis_and_asked_one_by_one():
    v = ask("上に2mm、Yプラスに3mm")
    assert v.status == "ask"
    assert "Z方向 +2mm" in v.message and "Y方向 +3mm" in v.message and "1つずつ" in v.message


def test_minus_and_three_moves():
    v = ask("Xマイナスに5mm、上に2mm、Y-に1mm")
    assert v.status == "ask"
    for w in ["X方向 -5mm", "Z方向 +2mm", "Y方向 -1mm", "3つ"]:
        assert w in v.message, w


def test_unreadable_part_falls_back_to_the_old_question():
    v = ask("上に2mm、右に3mm")  # 右は軸が決まっていない → 分けて示さない
    assert v.status == "ask" and "Z方向" not in v.message


def test_flow_makes_no_candidate_for_two_moves():
    wf = WebFlow(interpreter=MockInterpreter())
    sid = wf.load(DEMO_C)["session_id"]
    wf.select(sid, "C00005")
    r = wf.hear(sid, "上に2mm、Yプラスに3mm", "voice")
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    for w in u.FORBIDDEN_WORDS:
        assert w not in r["message"]
