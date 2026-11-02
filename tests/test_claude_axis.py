"""本物の Claude の道具で、軸と向き（X+・Y− など）を言った指示も扱えること（実 API は呼ばない）。

唐川さんの最終指示（2026-11-02 夜）：「Yプラスに3mm」「Xマイナスに5mm」に対応したい。
- これまでの道具の方向は 上・下・右・左 だけで、本物の Claude では「Yプラスに3mm」を表せなかった
- 軸と向きの方向（X+ … Z−）を足す。上・下 は今どおり Z±（FRAME v1.1 6章：枠が無ければ今どおり）。右・左 は聞き返し
- Claude の提案は、今どおりプログラムが発話から取り出した事実（軸・量）と照らし合わせる。違えば聞き返す
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
KEY = "sk-ant-TEST-SECRET-0123456789"
AXIS_DIRS = ["X+", "X-", "Y+", "Y-", "Z+", "Z-"]


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kw):
        self.calls.append(kw)
        return self.response


def propose(direction, amount, position="C00005"):
    block = NS(type="tool_use", name="propose_position", id="toolu_1",
               input={"position": position, "direction": direction, "amount_mm": amount})
    return NS(stop_reason="tool_use", content=[block])


def hear(text, response):
    c = NS(messages=FakeMessages(response))
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return w.hear(sid, text, source="voice"), c.messages.calls


def test_tool_accepts_axis_directions():
    enum = ci._tools(["C00005"])[0]["input_schema"]["properties"]["direction"]["enum"]
    assert enum[:4] == ["上", "下", "右", "左"]
    assert enum[4:] == AXIS_DIRS


@pytest.mark.parametrize("text,direction,amount,target,delta", [
    ("Yプラスに3mm", "Y+", 3, "C00005 Y", "+3.000"),
    ("Xマイナスに5mm", "X-", 5, "C00005 X", "-5.000"),
    ("この位置を上に2ミリ", "上", 2, "C00005 Z", "+2.000"),
    ("Z方向に-4mm", "Z-", 4, "C00005 Z", "-4.000"),
])
def test_axis_and_up_down_make_candidates(text, direction, amount, target, delta):
    r, calls = hear(text, propose(direction, amount))
    assert len(calls) == 1
    c = r["state"]["candidate"]
    assert r["status"] == "ok" and c["target"] == target and c["delta"] == delta


def test_claude_axis_must_match_the_utterance():
    # 発話は Y+、Claude は X+ → プログラムの照らし合わせで聞き返す（候補を作らない）
    r, _ = hear("Yプラスに3mm", propose("X+", 3))
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    # 発話は Y+、Claude は Y− → 量（符号）が違うので聞き返す
    r, _ = hear("Yプラスに3mm", propose("Y-", 3))
    assert r["status"] == "ask" and r["state"]["candidate"] is None


def test_side_still_asks_and_unknown_direction_fails():
    r, _ = hear("右に5mm", propose("右", 5))
    assert r["state"]["candidate"] is None and r["status"] in ("ask", "stop")
    with pytest.raises(ci.InterpreterError):
        ci._convert(propose("Y", 3), ["C00005"])
    with pytest.raises(ci.InterpreterError):
        ci._convert(propose("前", 3), ["C00005"])


def test_two_directions_in_one_utterance_are_not_sent_as_one_change():
    # 「上に2mm、Yプラスに3mm」：プログラムが先に聞き返す（Claude に1つにまとめさせない）
    r, calls = hear("上に2mm、Yプラスに3mm", propose("Z+", 2))
    assert r["state"]["candidate"] is None and r["status"] == "ask"
