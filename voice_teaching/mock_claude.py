"""偽のClaude（モック）。本物の Claude API は呼ばない。

- 入口は decide() の1つだけ。後で本物の Claude（道具の使用）に差し替えるときは、この関数を置き換える。
- 発話から取り出した事実（utterance.UtteranceFacts）と、画面で選んでいる位置だけを見て、道具の呼び出しを返す。
- 事実で足りないとき（量・軸・位置が決まらない）は、何も提案しない。聞き返しは Session / utterance に任せる。
- 返す道具は select / propose_change / compare / undo だけ。承認・書き出し・転送・ロボットの起動の道具は無い。
- 道具の引数は ProposedChange の6項目（kind・position・axis・side・delta_mm・percent）だけを使う。
  ジョブの文字列や説明文は作らない。数字は利用者が言った量（facts.requested_value）をそのまま入れる。
"""
from __future__ import annotations

from .utterance import UtteranceFacts

MOCK_NOTICE = "偽のClaude（モック）が解釈しています。本物の Claude は呼んでいません"
TOOLS = ("select", "propose_change", "compare", "undo")


def decide(facts: UtteranceFacts | None, selected: str | None) -> list[dict]:
    """道具の呼び出しの並び [{"tool": 名前, "args": {...}}] を返す。何もしないときは []。"""
    if facts is None:
        return []
    op = facts.op
    said = facts.position
    if op is None:
        if "select" in facts.kinds and said is not None:
            return [{"tool": "select", "args": {"position": said}}]
        return []
    if op == "compare":
        return [{"tool": "compare", "args": {}}]
    if op == "undo":
        return [{"tool": "undo", "args": {}}]

    position = said or selected
    value = facts.requested_value()
    amount = facts.amount
    if position is None or value is None or amount is None or facts.sides:
        return []
    if amount.unit != ("mm" if op == "move" else "%"):
        return []
    if op == "move":
        if facts.axis is None:
            return []
        return [{"tool": "propose_change", "args": {
            "kind": "shift", "position": position, "axis": facts.axis, "delta_mm": float(value)}}]
    if op == "speed":
        return [{"tool": "propose_change", "args": {
            "kind": "speed", "position": position, "percent": float(value)}}]
    return []
