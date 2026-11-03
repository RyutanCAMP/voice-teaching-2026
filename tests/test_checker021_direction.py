"""CHECKER_021（クロ・独立レビュー）の D1・D2 の受入テスト（同 10章の表）。

本人の方針（2026-11-03）：
- 明確なら候補を作る
- 符号が不明なら、音声・文字の両方で必ず聞き返す（「Y方向に3mm」を Y+3 にしない）
- 未設定の方向は推測しない
- 解釈が2つあれば聞き返す
- どの指示も安全制限を通す
- 「Y座標を3mm下げて」は Y−3（座標の値を明示しているため。本人の判断）
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()

# 上下の向き（CHECKER_026 M2）：このファイルは、発話の読み取り（「上・下・上げ・下げ」を Z± として読むことを含む）を
# 確かめるテスト。上・下を Z にするのは上下の向きの照合に通ったときだけなので、ファイルの全テストに
# 一時フォルダの確認済みの設定（DEMO_C_PLACE。conftest.updown_demo_c）を当てる。設定が無いときの動きは tests/test_updown.py
pytestmark = pytest.mark.usefixtures("updown_demo_c")


def hear(*texts, source="voice", interpreter=None):
    w = WebFlow(interpreter=interpreter) if interpreter else WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = None
    for t in texts:
        r = w.hear(sid, t, source=source)
    return r


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"], c["delta"]) if c else None


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text,target,delta", [
    ("Y座標を3mm下げて", "C00005 Y", "-3.000"),
    ("Yの値を3mm減らして", "C00005 Y", "-3.000"),
    ("Y座標を3mm上げて", "C00005 Y", "+3.000"),
    ("Yの値を3mm増やして", "C00005 Y", "+3.000"),
    ("Yプラスに3mm", "C00005 Y", "+3.000"),
    ("Yマイナスに3mm", "C00005 Y", "-3.000"),
    ("上に3mm", "C00005 Z", "+3.000"),       # FRAME v1.1 6章の今の決まり
    ("下に3mm", "C00005 Z", "-3.000"),
    ("Zを3mm下げて", "C00005 Z", "-3.000"),  # Z の値を下げる＝下へ（読み方が1つ）
    # 音声認識の表記ゆれ
    ("ワイプラス 3mm", "C00005 Y", "+3.000"),
    ("Y+3mm", "C00005 Y", "+3.000"),
    ("Y−3mm", "C00005 Y", "-3.000"),
    ("ワイ座標を3ミリ下げて", "C00005 Y", "-3.000"),
])
def test_clear_instructions_make_candidates(text, target, delta, source):
    r = hear(text, source=source)
    assert r["status"] == "ok" and cand(r) == (target, delta), (text, r.get("message"))


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text", [
    "Yを3mm下げて", "Xを5mm上げて", "ワイを3ミリ上げて",        # D1：Y の値か、下へ（Z）か
    "Yを下に3mm", "Y方向に3mm上げて", "Y座標を下に3mm",         # 軸が2つ（Y と 上下＝Z）
])
def test_two_readings_are_asked_back(text, source):
    r = hear(text, source=source)
    assert r["status"] == "ask" and cand(r) is None
    assert "2つの読み方" in r["message"] or "2つ以上" in r["message"]


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text", ["Y方向に3mm", "Yに3mm", "Y軸を3ミリ", "C00005をX方向に10mm移動して"])
def test_unknown_sign_is_asked_back(text, source):
    # D2：符号が分からなければ、音声・文字とも聞き返す（＋を補わない）
    r = hear(text, source=source)
    assert r["status"] == "ask" and cand(r) is None
    assert "プラスですか、マイナスですか" in r["message"]


def test_answering_the_sign_question_continues():
    assert cand(hear("Y方向に3mm", "マイナス")) == ("C00005 Y", "-3.000")
    assert cand(hear("Y方向に3mm", "プラス")) == ("C00005 Y", "+3.000")


@pytest.mark.parametrize("text", ["右に3mm", "左に3mm"])
def test_unset_directions_are_not_guessed(text):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None


def test_every_wording_goes_through_the_limit():
    r = hear("Y座標を25mm下げて")
    assert r["status"] == "stop" and cand(r) is None


class FakeMessages:
    def __init__(self, response):
        self.response = response

    def create(self, **kw):
        return self.response


def propose(direction, amount, position="C00005"):
    block = NS(type="tool_use", name="propose_position", id="toolu_1",
               input={"position": position, "direction": direction, "amount_mm": amount})
    return NS(stop_reason="tool_use", content=[block])


@pytest.mark.parametrize("direction", ["下", "Z-"])
def test_claude_answer_on_another_axis_is_asked_back(direction):
    # 「Y座標を3mm下げて」に Claude が Z−3（下）と答える → プログラムが読んだ軸（Y）と違うので聞き返す
    client = NS(messages=FakeMessages(propose(direction, 3)))
    r = hear("Y座標を3mm下げて", interpreter=ci.ClaudeInterpreter("sk-ant-TEST-0000", client=client))
    assert r["status"] == "ask" and cand(r) is None


def test_claude_matching_answer_makes_candidate():
    client = NS(messages=FakeMessages(propose("Y-", 3)))
    r = hear("Y座標を3mm下げて", interpreter=ci.ClaudeInterpreter("sk-ant-TEST-0000", client=client))
    assert cand(r) == ("C00005 Y", "-3.000")
