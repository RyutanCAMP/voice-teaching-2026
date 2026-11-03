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

上下の向き（CHECKER_026 M2）:
- updown_check（ジョブ → updown.UpdownStatus）を、指示（hear）と提案（propose）のたびに呼んで設定を読み直す。
  渡さなければ「未確認」として扱う（上・下を Z にしない）。
- 候補を作ったときの照合の結果を candidate_updown に残す（承認・出力のときに、今の結果と比べる。web/flow.py）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import jbi
from . import updown as ud
from . import utterance as ut

# simulate(元のジョブ, 提案, 実機用の検査に断られた理由) → プレビュー用の候補（作れなければ None）
Simulator = Callable[[jbi.Job, ut.ProposedChange, str], "jbi.ChangeProposal | None"]

_NUM = jbi._NUM
# 承認待ちの候補があるときの文。web/flow.py は、これを解釈の係（Claude）を呼ぶ前に返す（呼ばない）
PENDING_MESSAGE = "承認待ちの変更候補があります。比べるか、元に戻してから指示してください"
# 承認したあとの候補があるときの文（CHECKER_026 S-msg）。新しい指示があると承認は今どおり取り消すので、そのことも書く。
# web/flow.py が、承認していたときだけ Session.pending_message をこれにする
APPROVED_PENDING_MESSAGE = ("承認した変更候補があります。新しい指示があったため、承認は取り消しました。"
                            "もう一度承認して出力するか、元に戻してから指示してください")


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
    choices: tuple[tuple[str, str], ...] = ()  # 聞き返しの短い選択肢（見せる文, 指示の文）


class Session:
    def __init__(self, job: jbi.Job, *, max_shift_mm: float = jbi.DEFAULT_MAX_SHIFT_MM,
                 max_speed_change_percent: float | None = None,
                 simulate: Simulator | None = None,
                 updown_check: "Callable[[jbi.Job], ud.UpdownStatus] | None" = None):
        self._job = job
        self._updown_check = updown_check
        self.updown: ud.UpdownStatus = ud.NOT_CONFIGURED  # 最後に読んだ上下の向きの照合の結果
        self.candidate_updown: ud.UpdownStatus | None = None  # 候補を作ったときの照合の結果
        self._original_bytes = job.render_bytes()
        self.max_shift_mm = max_shift_mm
        # 速度の1回の変更量の上限（元の速度に対する%）。専門家の枠の値。無ければ速度は変えない
        self.max_speed_change_percent = max_speed_change_percent
        self._simulate = simulate
        self.selected: str | None = None
        self.candidate: jbi.ChangeProposal | None = None
        self.candidate_utterance: str | None = None  # 候補を作った指示の文（認識した文字・打った文字）
        self.candidate_blocked_reason: str | None = None
        self._candidate_position: str | None = None
        self.facts: ut.UtteranceFacts | None = None
        self._awaiting_answer: ut.UtteranceFacts | None = None
        self._locked = False
        self.log: list[tuple[str, str]] = []
        # 承認待ちの候補があるときの文。web/flow.py が承認の有無で選ぶ（承認は Session の外にある）
        self.pending_message = PENDING_MESSAGE

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

    # ---- 上下の向き ----
    def updown_status(self) -> ud.UpdownStatus:
        """上下の向きの設定を読み直して、このジョブと照らし合わせる（呼ぶたびに読む）。照合で例外が出ても未確認にする。"""
        st = ud.NOT_CONFIGURED
        if self._updown_check is not None:
            try:
                got = self._updown_check(self._job)
            except Exception:  # noqa: BLE001  分からないときは止める側（未確認）
                got = ud.unconfirmed("上下の向きの照合ができませんでした")
            st = got if isinstance(got, ud.UpdownStatus) else ud.unconfirmed("上下の向きの照合の結果が読めません")
        self.updown = st
        return st

    # ---- 発話 ----
    def hear(self, text: str, source: str = "text") -> Reply:
        """新しい発話。source（voice / text）は記録だけに使い、結果は変えない。"""
        if ut.is_explain_choice(text):
            return self.explain_only(text, source)
        facts = ut.read_utterance(text)
        prev = self._awaiting_answer
        if prev is not None and not facts.kinds:
            facts = ut.merge_answer(prev, facts)
        elif (prev is not None and prev.op == "move" and prev.amount is None and not prev.unclear_amounts
              and prev.axes and facts.op == "move" and facts.vertical and facts.axes == ("Z",) and not facts.positions):
            # 量を聞き返した後の「3mm下げて」：前に言った軸（Y座標 など）を捨てて Z にしない。両方を合わせて聞き返す
            facts = ut.combine_axes(prev, facts)
        self.facts = facts
        self._locked = False
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        v = ut.pre_check(facts, self.selected, self.updown_status())
        if v.status == "ask" and facts.question and v.choices and facts.op in ("move", "speed"):
            v = self._check_question(facts, v)  # 疑問の形の確かめ（2026-11-03 夜）
        shimasu = ut.is_shimasu_confirmation(v)
        if shimasu and self.candidate is not None:
            v = ut.Verdict("stop", self.pending_message)  # 「〜します」の確かめより先に、承認待ちの候補を知らせる
        if v.status != "ok":
            self._locked = True  # 聞き返し・止めた発話では、Claude が量を作って提案しても使わない
        # 知らない言葉で聞き返したときは、次の発話を答えとして前の発話に足さない（言い直しを新しい指示として読む。
        # 前の発話の残り「下げて」と合わせて、知らない言葉を捨てた候補にしない）。
        # 「〜します」の2択のあとも足さない（「3mm」と答えても、確かめを飛ばして候補にしない。ボタンで選んでもらう）
        self._awaiting_answer = facts if v.status == "ask" and not facts.unknown_words and not shimasu else None
        choices = v.choices
        if self.selected is not None:
            # 選択肢の文には、聞き返したときの位置を必ず入れる（あとで別の位置を選んでも、その位置に当てない）。
            # ［説明です］（何もしない）と、場所の言葉の入った文（位置は会話で人が選ぶ）には足さない
            choices = tuple((label, text if (ut.normalize_position(text[:6]) or ut.is_explain_choice(text)
                                             or ut.read_utterance(text).place_ref) else f"{self.selected}を{text}")
                            for label, text in choices)
        return self._reply(v.status, v.message, facts=facts, choices=choices)

    def explain_only(self, text: str, source: str = "text") -> Reply:
        """［説明です］（「〜します」の2択）：何もしない。候補・選択中の位置・元のジョブは変えない（CHECKER_026 M3）。"""
        self._awaiting_answer = None
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        return self._reply("ok", ut.EXPLAIN_REPLY)

    def forget_question(self) -> None:
        """聞き返しへの答えを待つのをやめる（会話の機能が自分で聞き返したとき。次の発話を前の発話に足さない）。"""
        self._awaiting_answer = None

    def pending_reply(self, facts: ut.UtteranceFacts | None) -> Reply:
        """承認待ちの候補があるのに来た移動・速度の指示を、解釈の係を呼ばずに断る。"""
        self._locked = True
        return self._reply("stop", self.pending_message, facts=facts)

    def _check_question(self, facts: ut.UtteranceFacts, v: ut.Verdict) -> ut.Verdict:
        """疑問の形の移動・速度（「5mm下げて？」）：確かめる前に、どうせ止まるものは今止める。候補は作らない。

        承認待ちの候補があれば、その知らせ。量の上限・0 などで実機用の検査とシミュレーションの両方に断られるなら、その理由。
        """
        if self.candidate is not None:
            return ut.Verdict("stop", self.pending_message)
        position, value = facts.position or self.selected, facts.requested_value()
        if position is None or value is None or (facts.op == "move" and facts.axis is None):
            return v  # 位置・量・軸が決まらなければ、確かめたあとの指示で聞き返す
        p = (ut.ProposedChange(kind="shift", position=position, axis=facts.axis, delta_mm=float(value))
             if facts.op == "move" else ut.ProposedChange(kind="speed", position=position, percent=float(value)))
        try:
            self._plan(p)  # 試すだけ（候補にしない）
        except jbi.JbiError as e:
            if self._simulate is None or self._simulate(self._job, p, str(e)) is None:
                return ut.Verdict("stop", str(e))
        return v

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
            return self._refuse("stop", self.pending_message)
        v = ut.check_proposal(self.facts, changes, self.selected, self.updown_status())
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
        self.candidate_utterance = self.facts.raw if self.facts is not None else None
        self.candidate_blocked_reason = blocked
        self.candidate_updown = self.updown  # この候補を作ったときの上下の向きの照合（propose で読み直したもの）
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
        self.candidate_utterance = None
        self.candidate_blocked_reason = None
        self.candidate_updown = None
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
            # 止まった候補は承認・出力できない（CHECKER_019 R1）。シミュレーションの図と差分で確かめるだけ
            text += (f"【参考の表示だけ】実機用の検査で止まりました：{blocked}。"
                     "この候補は承認も出力もできません（シミュレーションの図と差分で確かめるだけです）")
        else:
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
