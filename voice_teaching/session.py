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
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import jbi
from . import repeat_count as rc
from . import updown as ud
from . import utterance as ut

# simulate(元のジョブ, 提案, 実機用の検査に断られた理由) → プレビュー用の候補（作れなければ None）
Simulator = Callable[[jbi.Job, ut.ProposedChange, str], "jbi.ChangeProposal | None"]

_NUM = jbi._NUM
# 位置番号のあとに「の」でつなぐ文の始め（「C00005のY座標を3mm下げて」「C00005の速度を…」。「を」を続けない）
_OF_HEAD = re.compile(r"(?:[XYZ]\s?(?:座標|の値)|速度)")
# 承認待ちの候補があるときの文。web/flow.py は、これを解釈の係（Claude）を呼ぶ前に返す（呼ばない）
PENDING_MESSAGE = "承認待ちの変更候補があります。比べるか、元に戻してから指示してください"
# 承認したあとの候補があるときの文（CHECKER_026 S-msg）。新しい指示があると承認は今どおり取り消すので、そのことも書く。
# web/flow.py が、承認していたときだけ Session.pending_message をこれにする
APPROVED_PENDING_MESSAGE = ("新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。"
                            "内容を確認して、もう一度承認してください。")  # 唐川さんの決定の文（一字一句このまま）
# 位置が決まらない移動・速度の指示（web/flow.py も、解釈の係を呼ぶ前にこの文で聞き返す）
NO_POSITION = "位置が決まっていないため、変更の提案をしていません。位置を選ぶか、位置番号（C と5桁の数字）で言ってください"


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
    # 回数の変更（kind="repeat"）のときだけ：見出しと、足す（+）・消す（-）行の差分 (符号, 1から数えた行番号, 行)
    heading: str = ""
    diff: tuple[tuple[str, int, str], ...] = ()


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
        # 上下の案内で聞き返した直後の印（その時の照合の結果, 上下の言葉）。次の1回の答え（「3ミリ」）に案内を返すためだけ
        self._updown_followup: tuple[ud.UpdownStatus, str] | None = None
        self._candidate_value_change = False  # 候補が「Z座標を3mm下げて」のような軸の値の増減の指示から来たか
        self._locked = False
        self.log: list[tuple[str, str]] = []
        # 承認待ちの候補があるときの文。web/flow.py が承認の有無で選ぶ（承認は Session の外にある）
        self.pending_message = PENDING_MESSAGE
        # 回数の変更の範囲の確認（ボタンを待っている間だけ。dict）。新しい指示・選び直し・元に戻すで捨てる
        self.repeat_range: dict | None = None
        self._range_turn = 0

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
        if self.repeat_range is not None and rc.is_range_confirm_word(text):
            # 範囲の確認を待っている間の「この範囲を繰り返す」「はい」など（QA P1-A）：確定はせず、範囲の確認は残して案内する
            self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
            self._locked = True
            return self._reply("ask", rc.RANGE_APPROVE_GUIDE if rc.is_approve_word(text) else rc.RANGE_BUTTON_ONLY)
        self.forget_range()  # 新しい指示（打ち消し・別の指示・別の回数）：範囲の確認は捨てる（声の「はい」でも確定しない）
        if ut.is_explain_choice(text):
            return self.explain_only(text, source)
        if rc.is_repeat_request(text):
            return self._hear_repeat(text, source)  # 回数の変更：プログラムだけで読む（docs/REPEAT_COUNT_DESIGN.md）
        facts = ut.read_utterance(text)
        prev = self._awaiting_answer
        followup, self._updown_followup = self._updown_followup, None
        if prev is not None and (not facts.kinds or ut.is_explicit_direction_answer(prev, facts)
                                 or ut.is_direction_answer(prev, facts, self.selected)):
            facts = ut.merge_answer(prev, facts)
        elif (prev is not None and prev.op == "move" and prev.amount is None and not prev.unclear_amounts
              and prev.axes and facts.op == "move" and facts.vertical and facts.axes == ("Z",) and not facts.positions):
            # 量を聞き返した後の「3mm下げて」：前に言った軸（Y座標 など）を捨てて Z にしない。両方を合わせて聞き返す
            facts = ut.combine_axes(prev, facts)
        self.facts = facts
        self._locked = False
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        st = self.updown_status()
        v = ut.pre_check(facts, self.selected, st)
        if v.confirm_position is not None and v.confirm_position not in self._job.positions:
            # 4桁以下の位置番号の確かめ：ジョブに無い番号なら、ボタンを出さずに止める（唐川さんの決定 11/5）
            v = ut.Verdict("stop", f"{v.confirm_position} はこのジョブにありません")
        if v.status == "ask" and facts.question and v.choices and facts.op in ("move", "speed"):
            v = self._check_question(facts, v)  # 疑問の形の確かめ（2026-11-03 夜）
        if ut.is_shimasu_confirmation(v):
            v = self.check_shimasu(facts, v)  # 「〜します」：外した文が検査・上限まで通るときだけ2択（QA P1-1）
        if followup is not None and v.status == "ask" and not facts.kinds and not facts.unknown_words:
            # 上下の案内のすぐあとの「3ミリ」：前の発話とはつながない。次にどう言えばよいかを案内する（CHECKER_028 D）
            v = ut.Verdict("ask", ut.updown_followup_message(*followup))
        if v.status != "ok":
            self._locked = True  # 聞き返し・止めた発話では、Claude が量を作って提案しても使わない
        # 次の発話を答えとして前の発話に足さない（言い直しを新しい指示として読む）のは、次のとき：
        # - 知らない言葉で聞き返した（前の発話の残り「下げて」と合わせて、知らない言葉を捨てた候補にしない）
        # - 「〜します」の発話（「3mm」と答えても、確かめを飛ばして候補にしない。ボタンで選んでもらう）
        # - 上下の向きが確認できていない「上・下」で聞き返した（CHECKER_026 再修正 P0-1。「下げて」→「3ミリ」を Z− にしない）
        updown_ask = facts.vertical and not st.confirmed
        self._awaiting_answer = (facts if v.status == "ask" and not facts.unknown_words and facts.shimasu_text is None
                                 and not updown_ask else None)
        if updown_ask and v.status == "ask":
            self._updown_followup = (st, ut.updown_word(facts))
        return self._reply(v.status, v.message, facts=facts, choices=self.pin_choices(v.choices))

    def pin_choices(self, choices: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        """選択肢の文に、聞き返したときの選んでいる位置を入れる（あとで別の位置を選んでも、その位置に当てない）。
        ［説明です］（何もしない）と、場所の言葉の入った文（位置は会話で人が選ぶ）には足さない。"""
        if self.selected is None:
            return tuple(choices)
        return tuple((label, text if (ut.normalize_position(text[:6]) or ut.is_explain_choice(text)
                                      or ut.read_utterance(text).place_ref)
                      else f"{self.selected}{'の' if _OF_HEAD.match(text) else 'を'}{text}")
                     for label, text in choices)

    def check_shimasu(self, facts: ut.UtteranceFacts, v: ut.Verdict) -> ut.Verdict:
        """「〜します」の2択（v）を出す前に、外した文がふつうの指示としてどうなるかを、候補を作らずに確かめる（QA P1-1）。

        承認待ちの候補・位置が決まらない・実機用の検査（上限・0mm・位置がジョブに無い など）で断られるなら、その答えを返す
        （2択で上書きしない）。疑問の形（_check_question）と違い、シミュレーションの参考の候補が作れても2択にしない
        （「〜します」からは参考の候補も作らないため、断られた理由をそのまま知らせる）。
        """
        if not ut.is_shimasu_confirmation(v):
            return v
        if self.candidate is not None:
            return ut.Verdict("stop", self.pending_message)  # 「〜します」の確かめより先に、承認待ちの候補を知らせる
        position, value = facts.position or self.selected, facts.requested_value()
        if position is None:
            return ut.Verdict("ask", NO_POSITION)
        if value is None or (facts.op == "move" and facts.axis is None) or facts.op not in ("move", "speed"):
            return ut.Verdict("ask", ut.shimasu_restate_message(facts, self.updown))
        p = (ut.ProposedChange(kind="shift", position=position, axis=facts.axis, delta_mm=float(value))
             if facts.op == "move" else ut.ProposedChange(kind="speed", position=position, percent=float(value)))
        try:
            self._plan(p)  # 試すだけ（候補にしない）
        except jbi.JbiError as e:
            return ut.Verdict("stop", str(e))
        return v

    def _hear_repeat(self, text: str, source: str) -> Reply:
        """「C00001からC00003の作業を2回から3回にして」：読み取り・聞き返し・範囲の確認まで、プログラムだけで行う（Claude は呼ばない）。

        先に今の検査（打ち消し・承認の言葉・ほかの変更との混ざり）をかける。数はジョブから数えて文に入れる。
        候補はここでは作らない。範囲を示して、人がボタン（confirm_repeat）で確定してから作る（唐川さんの決定 2026-11-03）。
        疑問の形（「…3回にして？」）も、範囲の確認のボタン1回で確かめる（QA P2-9）。
        この発話では、解釈の係のほかの提案を受け付けない（_locked）。
        """
        facts = ut.read_utterance(text)
        body = facts.question_text or text
        # 読めなかった言葉は、回数の変更の決まった形で読めなかった言葉だけ（「から」「作業」を読めない言葉として示さない）
        facts = replace(facts, unknown_words=rc.leftovers(body) if rc.read(body) is None else ())
        self.facts = facts
        self._locked = True
        self.forget_question()
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        self.updown_status()

        def stop(msg: str) -> Reply:
            return self._reply("stop", msg, facts=facts)

        def stop_job(msg: str) -> Reply:  # ジョブを調べて止まったとき：対象外のことも短く添える
            return stop(f"{msg.rstrip('。')}。{jbi.REPEAT_SCOPE_NOTE}")

        found = facts.negations + facts.undo_negations
        if found:
            return stop(ut.NEGATION_MESSAGE.format(word="".join(f"「{w}」" for w in found)))
        if facts.stop_reasons:
            return stop("。".join(facts.stop_reasons))
        if (facts.amounts or set(facts.kinds) & {"move", "speed", "compare", "undo", "select"} or facts.axes
                or facts.sides or facts.vertical or facts.relative_dir or facts.place_ref):
            return stop(rc.MIXED_MESSAGE)
        if rc.has_decimal_count(body):
            return stop(rc.DECIMAL_MESSAGE)
        if rc.has_negative_count(body):
            return stop(rc.NEGATIVE_MESSAGE)
        req = rc.read(body)
        if req is None:
            return self._reply("ask", rc.unknown_message(rc.leftovers(body)), facts=facts)
        if self.candidate is not None:
            return stop(self.pending_message)
        confirm_block = False
        if req.start is not None:
            try:
                block = self._job.find_repeat(req.start, req.end)
            except jbi.JbiError as e:
                # 「C0001とC0002の動きを3回繰り返して」：言った位置がくり返しの区間とぴったり合わない。両方を含む区間が
                # 1つだけなら、その区間を示して確かめる（候補にも範囲の確認にもしない。唐川さんの指示 11/5 c）
                # 「…の動きを」と言ったときだけ（範囲・位置そのものを言ったときは、今どおり言った範囲で止める。P1-c）
                blocks = [b for b in self._job.find_repeat_blocks() if req.start in b.positions and req.end in b.positions]
                if not req.motion or len(blocks) != 1:
                    return stop_job(str(e))
                block, confirm_block = blocks[0], True
        else:
            # 範囲を言わない：ジョブに数えられる区間が1つだけなら、その区間を示して確かめる（QA P1-1）
            if req.position is not None and req.position not in self._job.positions:
                return stop(f"位置 {req.position} はこのジョブにありません")  # Safety P2-6
            blocks = self._job.find_repeat_blocks()
            if req.position is not None:  # 「C00001を3回」：その位置を含む区間だけ
                blocks = [b for b in blocks if req.position in b.positions]
            if not blocks:
                # 数えられる区間が無い：理由に合う文にする（Safety P2-2）。DOUT・TIMER があるジョブのときだけ対象外の文を添える
                has_signal = any(i.command in ("DOUT", "TIMER", "WAIT") for i in self._job.instructions)
                tail = f"。{jbi.REPEAT_SCOPE_NOTE}" if has_signal else ""
                return self._reply("ask", f"{rc.NO_BLOCK}。{rc.RANGE_NEEDED}{tail}", facts=facts)
            if len(blocks) != 1:
                return self._reply("ask", rc.RANGE_NEEDED, facts=facts)
            block = blocks[0]
        start, end = block.positions[0], block.positions[-1]
        n, target = block.count, block.target
        ok = lambda m: jbi.repeat_count_problem(n, m, target) is None  # noqa: E731
        if req.add is not None:
            # 「3回追加して」：合計か、足すのかが分からない。増やす向きで、押せば候補になるものだけを出す（P1-2・P2-1・P2-2）
            k = req.add
            opts = [m for m in dict.fromkeys((k, n + k)) if m > n and ok(m)]
            if not opts:
                return stop(jbi.repeat_count_problem(n, n + k, target) or rc.FORM_UNKNOWN)
            choices = tuple((f"合計{m}回にする" if m == k else f"{k}回足して合計{m}回にする", rc.canonical(start, end, n, m))
                            for m in opts)
            return self._reply("ask", rc.add_message(target, n, k), facts=facts, choices=choices)
        total = n + 1 if req.one_more else req.total
        if req.said_count is not None and req.said_count != n:
            # 言った今の回数がジョブと違う（S-P2-1）：言った目標が、この区間で押せば先に進む回数のときだけ、その1択で聞く。
            # 言った目標と違う回数は選択肢に出さない。ほかは止める（どう答えても止まるので聞き返さない）
            if ok(total):
                return self._reply("ask", rc.mismatch_message(target, n), facts=facts,
                                   choices=((f"合計{total}回にする", rc.canonical(start, end, n, total)),))
            why = jbi.REPEAT_DECREASE_MESSAGE if total < req.said_count else jbi.REPEAT_ONLY_MESSAGE
            return stop(f"このジョブでは、{target} は今 {n} 回です。{why}")
        why = jbi.repeat_count_problem(n, total, target)
        if why is not None:
            return stop(why)
        try:
            self._job.plan_repeat(start, end, total)  # 試すだけ（候補はボタンのあと）
        except jbi.JbiError as e:
            return stop_job(str(e))
        if confirm_block:
            # 選択肢は決まった文（今の回数を入れる）を送り、同じ検査を通ってから［この範囲を繰り返す］のボタンで確かめる
            return self._reply("ask", rc.block_confirm_message(start, end, total), facts=facts,
                               choices=((f"この区間を{total}回にする", rc.canonical(start, end, n, total)),))
        self._range_turn += 1
        texts = tuple(_strip_ending(x) for x in block.cycle_lines)
        first, last = block.first_line + 1, block.first_line + block.cycle_len
        side = self._side_lines(block)  # 区間のすぐ前後の DOUT・TIMER など（繰り返さない行。S-P2-4）
        side_note = (f"区間のすぐ前・後ろに信号（DOUT）や待ち時間（TIMER）の行があります"
                     f"（{'・'.join(f'{i}行目「{t}」' for i, t in side)}）。それらは繰り返しません") if side else None
        self.repeat_range = {
            "turn": self._range_turn, "start": start, "end": end, "target": target, "total": total, "count": n,
            "positions": list(block.positions), "cycle_lines": list(texts),
            "line_nos": list(range(first, last + 1)),
            "block_line_nos": list(range(first, block.end_line + 1)),
            "cycles": _cycles(block),  # 各回の (最初の行, 最後の行)。1 から
            "yes_label": rc.RANGE_YES_LABEL, "no_label": rc.RANGE_NO_LABEL, "note": jbi.REPEAT_SCOPE_NOTE,
            "side_note": side_note, "heading": rc.RANGE_HEADING, "button_note": rc.RANGE_BUTTON_NOTE,
        }
        msg = rc.range_message(_cycles(block), texts, total)
        return self._reply("ask", f"{msg}。{side_note}" if side_note else msg, facts=facts)

    def _side_lines(self, block: "jbi.RepeatBlock") -> list[tuple[int, str]]:
        """区間のすぐ前・すぐ後ろに続く DOUT・TIMER の行（1 から数えた行番号, 行）。繰り返さないことを知らせるため。"""
        by = {ins.index: ins for ins in self._job.instructions}
        out: list[tuple[int, str]] = []
        for start, step in ((block.first_line - 1, -1), (block.end_line, 1)):
            i, found = start, []
            while 0 <= i < len(self._job.lines) and i in by and by[i].command in ("DOUT", "TIMER"):
                found.append((i + 1, by[i].text))
                i += step
            out += sorted(found)
        return out

    def forget_range(self) -> None:
        """範囲の確認を捨てる（新しい指示・位置の選び直し・元に戻す）。古いボタンを押しても候補にならない。"""
        self.repeat_range = None

    def confirm_repeat(self, turn: object, choice: object) -> Reply:
        """範囲の確認のボタン（画面からだけ）。今の範囲の確認の番号（turn）と合うときだけ使う。声では呼ばない。"""
        rr = self.repeat_range
        if (rr is None or isinstance(turn, bool) or not isinstance(turn, int) or turn != rr["turn"]
                or choice not in ("yes", "no")):
            self.repeat_range = None  # 断ったら範囲の確認も捨てる（「もう使えません」の文と状態を合わせる。QA P2-E）
            return self._reply("stop", rc.RANGE_STALE)
        self.repeat_range = None
        if choice == "no":
            self._log("選択", f"ボタン：{rc.RANGE_NO_LABEL}")
            return self._reply("ok", rc.RANGE_NO_REPLY)
        self._log("選択", f"ボタン：{rc.RANGE_YES_LABEL}（{rr['target']}・{rr['line_nos'][0]}〜{rr['line_nos'][-1]}行目）")
        if self.candidate is not None:
            return self._reply("stop", self.pending_message)
        try:
            change = self._job.plan_repeat(rr["start"], rr["end"], rr["total"])  # 元のジョブから作り直す
        except jbi.JbiError as e:
            return self._reply("stop", str(e))
        self.candidate = change
        self.candidate_utterance = self.facts.raw if self.facts is not None else None
        self.candidate_blocked_reason = None
        self.candidate_updown = self.updown_status()
        self._candidate_value_change = False
        self._candidate_position = None  # 位置ごとの候補ではない（位置を選び直すと捨てる）
        self._locked = True
        c = self._comparison()
        self._log("候補", c.confirmation)
        return self._reply("ok", c.confirmation, comparison=c, candidate_made=True)

    def explain_only(self, text: str, source: str = "text") -> Reply:
        """［説明です］（「〜します」の2択）：何もしない。候補・選択中の位置・元のジョブは変えない（CHECKER_026 M3）。"""
        self.forget_range()  # 新しい操作：回数の変更の範囲の確認は捨てる（S-P2-2）
        self.forget_question()
        self._log("発話", f"{'音声' if source == 'voice' else '文字'}：{text}")
        return self._reply("ok", ut.EXPLAIN_REPLY)

    @property
    def awaiting_answer(self) -> bool:
        """聞き返しへの答えを待っているか（振り分けが、短い答えを指示の道へ送るかを決めるのに使う。C1）。"""
        return self._awaiting_answer is not None

    def forget_question(self) -> None:
        """聞き返しへの答えを待つのをやめる（会話の機能が自分で聞き返したとき・選び直し・元に戻す・承認の取り消し。
        次の発話を前の発話に足さない）。未確定の状態（答えを待つ発話・上下の案内の印）を全部捨てる。"""
        self._awaiting_answer = None
        self._updown_followup = None

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
        self.forget_range()  # 位置の選択（ジョブに無い位置・形の違う番号を含む）：範囲の確認は捨てる（S-P2-2）
        name = ut.normalize_position(position) if isinstance(position, str) else None
        if name is None:
            return self._reply("stop", "位置番号の形が分かりません。C と5桁の数字で指定してください")
        if name not in self._job.positions:
            return self._reply("stop", f"位置 {name} はこのジョブにありません")
        self.selected = name
        self.forget_question()  # 位置の選び直し：前の聞き返しの答えを、この位置に当てない（CHECKER_028 A）
        self.forget_range()
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
        self.forget_range()  # 比べる：新しい操作なので、範囲の確認は捨てる（S-P2-2）
        if self.candidate is None:
            return self._reply("stop", "比べる変更候補がありません")
        c = self._comparison()
        return self._reply("ok", c.confirmation, comparison=c, blocked_reason=c.blocked_reason)

    def undo(self) -> Reply:
        self.forget_question()  # 元に戻す・キャンセル：未確定の聞き返しも捨てる（CHECKER_028 A）
        self.forget_range()
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
        f = self.facts
        self._candidate_value_change = bool(p.kind == "shift" and f is not None and p.axis in f.value_axes
                                            and not f.vertical)
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
        self._candidate_value_change = False
        self._candidate_position = None

    def _comparison(self) -> Comparison:
        ch = self.candidate
        if ch.kind == "repeat":
            return _repeat_comparison(ch)
        before, after = _changed_token(ch.before_line, ch.after_line)
        delta = Decimal(after) - Decimal(before)
        delta_text = f"{'+' if delta >= 0 else ''}{delta}"
        if ch.kind == "shift":
            pos, axis = ch.target.split()
        blocked = self.candidate_blocked_reason
        if ch.kind == "shift":
            pos, axis = ch.target.split()
            if self._candidate_value_change:
                # 「Z座標を3mm下げて」：軸の値の増減として書く。物理の上下（下へ・上へ）とは書かない（CHECKER_028 K2）
                size = format(abs(delta).normalize(), "f")
                verb = ("増やした場合" if delta > 0 else "減らした場合") if blocked else ("増やします" if delta > 0 else "減らします")
                head = f"位置 {pos} の {axis} の値を {size}mm {verb}"
            else:
                head = f"位置 {pos} を {axis}方向に {delta_text}mm " + ("動かした場合" if blocked else "動かします")
            if blocked:
                text = f"{head}の図です（{axis}：{before} → {after}）。"
            else:
                text = (f"{head}（{axis}：{before} → {after}）。"
                        "この位置へ入る移動と、この位置から出る移動の通り道も変わります。")
        else:
            text = (f"{ch.target}の速度を V={before} から V={after} にした場合の図です。" if blocked
                    else f"{ch.target}の速度を V={before} から V={after} に変えます。")
        if blocked:
            # 止まった候補は承認・出力できない（CHECKER_019 R1）。最初に「参考の表示だけ・変更しません」と出す（CHECKER_032 P1-2）
            text = (f"【参考の表示だけ・変更しません】実機用の検査で止まりました：{blocked}。{text}"
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


def _cycles(block: "jbi.RepeatBlock") -> list[tuple[int, int]]:
    """各回の (最初の行, 最後の行)。1 から数える。"""
    size = block.cycle_len
    return [(block.first_line + k * size + 1, block.first_line + (k + 1) * size) for k in range(block.count)]


def _strip_ending(line: str) -> str:
    return line.rstrip("\r\n")


def _repeat_comparison(ch: "jbi.RepeatProposal") -> Comparison:
    """回数の変更の比較。数はすべてプログラムが入れる。"""
    head = f"{ch.target} の作業を {ch.before_count}回 → {ch.after_count}回 にします"
    diff = tuple(("+", i + 1, t) for i, t in ch.added) + tuple(("-", i + 1, t) for i, t in ch.removed)
    how = "同じ動きを1回ぶん足します（新しい位置・新しい移動の線は作りません）。"
    where = rc.ranges_text(_cycles(ch.block), tuple(_strip_ending(x) for x in ch.block.cycle_lines))
    text = f"{head}（変更前の範囲 {where}）。{how}これでいいですか？ 承認は画面のボタンで行います"
    return Comparison(
        kind="repeat", target=ch.target, line_number=ch.line_index + 1, before_line="", after_line="",
        before_value=str(ch.before_count), after_value=str(ch.after_count),
        delta=f"{ch.after_count - ch.before_count:+d}", affected=tuple(ch.affected), warnings=tuple(ch.warnings),
        confirmation=text, heading=head, diff=diff)


def _changed_token(before_line: str, after_line: str) -> tuple[str, str]:
    """変更前後の行で、ただ1つ違う数値の書き方を返す。"""
    a, b = re.findall(_NUM, before_line), re.findall(_NUM, after_line)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    if len(a) != len(b) or len(diff) != 1:
        raise jbi.JbiError("変更前後の行の違いが1つの数値ではないため、比べられません")
    return diff[0]
