"""画面の状態（選択中の位置・変更候補・元のジョブ）を持つ小さなクラス。

- 元のジョブは最初に読んだまま変えない（original_bytes で確かめられる）。
- 変更候補は jbi.plan_shift / plan_speed が作る。Claude はジョブの文字列を書かない。
- 承認・書き出し・転送・ロボットの起動は、ここには置かない（承認はボタンから、別のモジュールで作る）。
- 1つの発話の中で、照らし合わせ・検査に断られたら、その発話ではもう提案を受け付けない。

シミュレーションモード（合成ジョブで一通り動かすため）:
- jbi の検査（実機用の検査）に断られた理由は blocked_reason として返し、候補を作れたか（candidate_made）と分けて返す。
- simulate を渡したときだけ、断られた提案でもプレビュー用の候補を作れる（後で voice_teaching/sim.py が作る）。
  その候補には blocked_reason が付いたままになり、比較の注意書きにも出る。
- 発話との照らし合わせ（量・位置・軸の食い違い、左右、2つ以上の変更など）で断られたものは、simulate でも候補にしない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import jbi
from . import utterance as ut

# simulate(元のジョブ, 提案, 実機用の検査に断られた理由) → プレビュー用の候補（作れなければ None）
Simulator = Callable[[jbi.Job, ut.ProposedChange, str], "jbi.ChangeProposal | None"]

_NUM = jbi._NUM


@dataclass(frozen=True)
class Comparison:
    """変更前後の比較（プレビュー用）。数字はすべてジョブの書き方のまま、プログラムが入れる。"""
    kind: str
    target: str
    line_number: int          # 1から数えた行番号
    before_line: str
    after_line: str
    before_value: str
    after_value: str
    delta: str                # 符号つき（位置は mm）
    affected: tuple[str, ...]
    warnings: tuple[str, ...]
    confirmation: str         # 大きな確認表示の文
    blocked_reason: str | None = None


@dataclass(frozen=True)
class Reply:
    status: str               # "ok" / "ask"（聞き返す） / "stop"（止めた。何も変えていない）
    message: str
    facts: ut.UtteranceFacts | None = None
    comparison: Comparison | None = None
    blocked_reason: str | None = None   # 実機用の検査（jbi）に断られた理由
    candidate_made: bool = False        # この操作で変更候補を作ったか


class Session:
    def __init__(self, job: jbi.Job, *, max_shift_mm: float = jbi.DEFAULT_MAX_SHIFT_MM,
                 max_speed_change_percent: float | None = None,
                 simulate: Simulator | None = None):
        self._job = job
        self._original_bytes = job.render_bytes()
        self.max_shift_mm = max_shift_mm
        # 速度の1回の変更量の上限（元の速度に対する%）。専門家の枠の値。無ければ速度は変えない
        self.max_speed_change_percent = max_speed_change_percent
        self._simulate = simulate
        self.selected: str | None = None
        self.candidate: jbi.ChangeProposal | None = None
        self.candidate_blocked_reason: str | None = None
        self._candidate_position: str | None = None
        self.facts: ut.UtteranceFacts | None = None
        self._awaiting_answer: ut.UtteranceFacts | None = None
        self._locked = False
        self.log: list[tuple[str, str]] = []

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "Session":
        return cls(jbi.load(path), **kw)

    # ---- 元のジョブ ----
    @property
    def original(self) -> jbi.Job:
        return self._job

    @property
    def original_bytes(self) -> bytes:
        return self._original_bytes

    # ---- 発話 ----
    def hear(self, text: str, source: str = "text") -> Reply:
        """新しい発話。source（voice / text）は記録だけに使い、結果は変えない。"""
        facts = ut.read_utterance(text)
        if self._awaiting_answer is not None and not facts.kinds:
            facts = ut.merge_answer(self._awaiting_answer, facts)
        self.facts = facts
        self._locked = False
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        v = ut.pre_check(facts)
        if v.status != "ok":
            self._locked = True  # 聞き返し・止めた発話では、Claude が量を作って提案しても使わない
        self._awaiting_answer = facts if v.status == "ask" else None
        return self._reply(v.status, v.message, facts=facts)

    # ---- 操作 ----
    def select(self, position: str) -> Reply:
        name = ut.normalize_position(position) if isinstance(position, str) else None
        if name is None:
            return self._reply("stop", "位置番号の形が分かりません。C と5桁の数字で指定してください")
        if name not in self._job.positions:
            return self._reply("stop", f"位置 {name} はこのジョブにありません")
        self.selected = name
        msg = f"位置 {name} を選びました"
        if self.candidate is not None and self._candidate_position != name:
            self._discard()
            msg += "。別の位置の変更候補は捨てました（元のジョブは変わっていません）"
        return self._reply("ok", msg)

    def propose(self, args: "Mapping | ut.ProposedChange | Sequence") -> Reply:
        """Claude（またはモック）の道具 propose_change の引数を受け取り、変更候補を作る。"""
        if self.facts is None:
            return self._reply("stop", "先に指示（音声か文字）を入れてください")
        if self._locked:
            return self._reply("stop", "この指示では、もう変更の提案を受け付けません。新しく指示してください")
        try:
            changes = self._parse(args)
        except ValueError as e:
            return self._refuse("stop", f"{e}。この提案は使いません")
        if self.candidate is not None:
            return self._refuse("stop", "承認待ちの変更候補があります。比べるか、元に戻してから指示してください")
        v = ut.check_proposal(self.facts, changes, self.selected)
        if v.status != "ok":
            return self._refuse(v.status, v.message)
        p = changes[0]
        try:
            change = self._plan(p)
        except jbi.JbiError as e:
            return self._blocked(p, str(e))
        return self._accept(p, change, None)

    def compare(self) -> Reply:
        if self.candidate is None:
            return self._reply("stop", "比べる変更候補がありません")
        c = self._comparison()
        return self._reply("ok", c.confirmation, comparison=c, blocked_reason=c.blocked_reason)

    def undo(self) -> Reply:
        if self.candidate is None:
            return self._reply("ok", "元に戻す変更候補はありません。元のジョブはそのままです")
        self._discard()
        return self._reply("ok", "変更候補を捨てました。元のジョブは変わっていません")

    def confirmation_text(self) -> str | None:
        return None if self.candidate is None else self._comparison().confirmation

    # ---- 内部 ----
    def _parse(self, args) -> list[ut.ProposedChange]:
        if isinstance(args, (ut.ProposedChange, Mapping)):
            args = [args]
        if not isinstance(args, Sequence) or isinstance(args, (str, bytes)):
            raise ValueError("提案の形が読めません")
        return [a if isinstance(a, ut.ProposedChange) else ut.ProposedChange.from_tool_args(a) for a in args]

    def _plan(self, p: ut.ProposedChange) -> jbi.ChangeProposal:
        if p.kind == "shift":
            return self._job.plan_shift(p.position, p.axis, p.delta_mm, max_abs_delta_mm=self.max_shift_mm)
        limit = self.max_speed_change_percent
        if limit is None:
            raise jbi.JbiError("速度の1回の変更量が専門家の枠で決まっていないため、速度は変更しません")
        if abs(p.percent) > limit:
            raise jbi.JbiError(f"速度の変更 {p.percent:+g}% は、1回の変更量の上限 {limit:g}% を超えるため、変更しません")
        targets = [i for i in self._job.instructions if i.command == "MOVL" and i.position == p.position]
        if p.position not in self._job.positions:
            raise jbi.JbiError(f"位置 {p.position} はこのジョブにありません")
        if len(targets) != 1:
            raise jbi.JbiError(
                f"位置 {p.position} へ入る直線移動（MOVL）の命令が {len(targets)}つあり、"
                "どの命令の速度か1つに決まらないため、変更しません")
        ins = targets[0]
        if ins.speed is None:
            raise jbi.JbiError(f"{ins.index + 1}行目の速度は数値で書かれていないため、変更できません")
        # 今の速度に対する割合から、新しい V= をプログラムが計算し、小数第1位に丸める（仕様5-2）
        new = (Decimal(str(ins.speed)) * (Decimal(1) + Decimal(str(p.percent)) / Decimal(100))
               ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return self._job.plan_speed(ins.index, float(new))

    def _blocked(self, p: ut.ProposedChange, reason: str) -> Reply:
        """実機用の検査に断られた。simulate があればプレビュー用の候補だけ作る。"""
        self._locked = True
        change = None
        if self._simulate is not None:
            change = self._simulate(self._job, p, reason)
        if change is None:
            return self._reply("stop", reason, blocked_reason=reason)
        return self._accept(p, change, reason)

    def _accept(self, p: ut.ProposedChange, change: jbi.ChangeProposal, blocked: str | None) -> Reply:
        self.candidate = change
        self.candidate_blocked_reason = blocked
        self._candidate_position = p.position
        self.selected = p.position
        self._locked = True  # 1つの発話で作る候補は1つだけ
        c = self._comparison()
        self._log("候補", c.confirmation)
        return self._reply("ok", c.confirmation, comparison=c, blocked_reason=blocked, candidate_made=True)

    def _refuse(self, status: str, message: str) -> Reply:
        self._locked = True
        return self._reply(status, message)

    def _discard(self) -> None:
        self.candidate = None
        self.candidate_blocked_reason = None
        self._candidate_position = None

    def _comparison(self) -> Comparison:
        ch = self.candidate
        before, after = _changed_token(ch.before_line, ch.after_line)
        delta = Decimal(after) - Decimal(before)
        delta_text = f"{'+' if delta >= 0 else ''}{delta}"
        if ch.kind == "shift":
            pos, axis = ch.target.split()
            text = (f"位置 {pos} を {axis}方向に {delta_text}mm 動かします（{axis}：{before} → {after}）。"
                    "この位置へ入る移動と、この位置から出る移動の通り道も変わります。")
        else:
            text = f"{ch.target}の速度を V={before} から V={after} に変えます。"
        blocked = self.candidate_blocked_reason
        if blocked:
            text += f"【シミュレーションだけの候補】実機用の検査では止まりました：{blocked}。"
        text += "これでいいですか？ 承認は画面のボタンで行います"
        warnings = tuple(ch.warnings) + ((f"実機用の検査で止まった理由：{blocked}",) if blocked else ())
        return Comparison(
            kind=ch.kind, target=ch.target, line_number=ch.line_index + 1,
            before_line=ch.before_line, after_line=ch.after_line,
            before_value=before, after_value=after, delta=delta_text,
            affected=tuple(ch.affected), warnings=warnings, confirmation=text, blocked_reason=blocked)

    def _reply(self, status: str, message: str, **kw) -> Reply:
        if status != "ok" or message:
            kind = {"ok": "結果", "ask": "聞き返し", "stop": "止めた理由"}[status]
            self._log(kind, message)
        return Reply(status, message, **kw)

    def _log(self, kind: str, text: str) -> None:
        self.log.append((kind, text))


def _changed_token(before_line: str, after_line: str) -> tuple[str, str]:
    """変更前後の行で、ただ1つ違う数値の書き方を返す。"""
    a, b = re.findall(_NUM, before_line), re.findall(_NUM, after_line)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    if len(a) != len(b) or len(diff) != 1:
        raise jbi.JbiError("変更前後の行の違いが1つの数値ではないため、比べられません")
    return diff[0]
