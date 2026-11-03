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


# ---- 位置の候補（「置くところ」など）。決まった規則だけ（モック・テスト用）。本物の Claude の代わりに会話を試すため ----
# 案A と同じ材料（命令の行と行番号）だけを見る。規則：
#   置く側の言葉（置くところ・置き場所・置く位置・離すところ）：直線移動（MOVL）のすぐ後（次の移動命令より前）に DOUT … OFF がある位置
#   取る側の言葉（取るところ・つかむところ）：同じく DOUT … ON がある位置
# 根拠の行は、その MOVL の行から DOUT の行まで（5行まで）。DOUT・TIMER の意味は決めつけない（規則に合う行を返すだけ）。
# 0件か4件以上なら、何も返さない（人が選ぶ）。返した答えは、本物の Claude と同じくプログラムが照合する（dialog.verify_candidates）。
import re as _re

_PICK_WORDS = ("取るところ", "つかむところ")
_MOVL = _re.compile(r"^MOVL\s+(C[0-9]{5})\b")
_ANY_MOVE = _re.compile(r"^(MOV[LJCS]|SMOV|IMOV)")
_DOUT = _re.compile(r"^DOUT\s+OT#\([0-9]+\)\s+(ON|OFF)\s*$")


def mock_locate(word: str, lines) -> list[dict]:
    want = "ON" if word in _PICK_WORDS else "OFF"
    lines = list(lines)
    found: list[dict] = []
    for i, (n, text) in enumerate(lines):
        m = _MOVL.match(text)
        if not m:
            continue
        ev = [n]
        for n2, t2 in lines[i + 1:i + 5]:
            if _ANY_MOVE.match(t2):
                break
            ev.append(n2)
            d = _DOUT.match(t2)
            if d and d.group(1) == want:
                found.append({"position": m.group(1), "evidence_lines": ev, "rank": len(found) + 1})
                break
    return found if 1 <= len(found) <= 3 else []
