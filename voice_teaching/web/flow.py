"""画面の一連の流れ（シミュレーションモード）：読込 → 位置選択 → 指示 → 変更候補 → プレビュー → ボタン承認 → 出力。

- 画面ごとの Session はメモリの中だけに持つ（ディスクに保存しない）。数を超えたら古いものから捨てる。
- 指示（hear）は Session.hear → 解釈の係（本物の Claude か偽のClaude。claude_interpreter.from_env）→ Session の select / propose / compare / undo。
  指示の経路からは承認も出力も呼ばない（「承認」「OK」などの発話は Session が止める）。
- 承認（approve）は画面の承認ボタンからだけ呼ぶ。sim.approve を使い、承認した人の名前が要る。
- 出力（export）は sim.export_sim（合成ジョブだけ）。失敗は理由を返し、成功と表示させない。
- 承認の後で新しい指示・元に戻す・位置の選択があったら、承認を取り消す。
- 実機用の出力・転送・ロボットの起動は無い。
- 承認待ちの候補があるときの移動・速度の指示は、解釈の係（Claude）を呼ぶ前に断る（呼ばない）。
- Claude との会話（「置くところを少し手前にして」。voice_teaching/dialog.py）：場所の言葉があれば、位置の候補を
  解釈の係に頼み（合成ジョブだけ）、プログラムが照合して、人がボタン（か短い肯定）で選ぶ。方向は6つのボタン、
  量は人の言葉。集めたことは決まった文（「C00005をYのマイナス3ミリ」）にして、ふつうの指示と同じ流れに通す。
  会話の選択肢は choose（/api/dialog）で受け取り、今の選択肢の番号（turn）と合わなければ使わない。
"""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .. import claude_interpreter, jbi, mock_claude, sim
from .. import dialog as dg
from .. import utterance as ut
from ..session import PENDING_MESSAGE, Comparison, Reply, Session
from . import api

MAX_SESSIONS = 8
MAX_TEXT = 500
SOURCES = ("voice", "text")
NO_SESSION = "このジョブの画面の情報がありません（時間が経って捨てたか、サーバーを起動し直しました）。ジョブを読み込み直してください"
NO_POSITION = "位置が決まっていないため、変更の提案をしていません。位置を選ぶか、位置番号（C と5桁の数字）で言ってください"
NOT_DECIDED = "指示から変更の内容を決められませんでした。位置番号・方向・量（mm）を、もう一度指示してください"
APP_SPEAKER = "アプリ"  # プログラムの決まった文の話し手（Claude の文ではない）
NOT_APPROVED = "承認されていないため、出力しません（承認は確認表示の承認ボタンで行います）"


@dataclass
class _Entry:
    session: Session
    sim_error: str | None = None                  # シミュレーションの候補も作れなかった理由（最後の1つ）
    comparison: tuple | None = None               # (候補, Comparison) 候補ごとの比較
    sim_proposal: sim.SimProposal | None = None
    approval: sim.Approval | None = None
    approved_candidate: object | None = None      # 承認したときの候補（同じものか確かめる）
    exported: str | None = None
    dialog: dg.Dialog = field(default_factory=dg.Dialog)  # Claude との会話の状態（メモリの中だけ。保存しない）
    frame: object | None = None                   # 案B：FRAME（まだ無い。あれば label・aliases で位置を決める）
    lock: threading.Lock = field(default_factory=threading.Lock)

    # Session に渡すシミュレーション（実機用の検査に断られたときだけ呼ばれる）
    def simulate(self, job: jbi.Job, p: ut.ProposedChange, reason: str) -> jbi.ChangeProposal | None:
        self.sim_error = None
        if p.kind != "shift" or p.axis is None:
            self.sim_error = "シミュレーションでも、位置の移動のほかは候補を作りません"
            return None
        try:
            sp = sim.simulate_shift(job, p.position, p.axis, p.delta_mm)
            new_job = jbi.parse_bytes(sp.new_bytes)
        except (sim.SimError, jbi.JbiError) as e:
            self.sim_error = str(e)
            return None
        users = tuple(f"{i + 1}行目「{t}」（この位置を使う命令）" for i, t in sp.users)
        warnings = tuple(w for w in sp.warnings if not w.startswith("実機用の検査では止まります"))
        return jbi.ChangeProposal(
            kind="shift", target=f"{p.position} {p.axis}", line_index=sp.line_index,
            before_line=sp.before_line, after_line=sp.after_line, requested=sp.requested,
            before_value=sp.before_value, after_value=sp.after_value,
            affected=users or ("（この位置を使う命令はありません）",), warnings=warnings, new_job=new_job)


class WebFlow:
    def __init__(self, max_sessions: int = MAX_SESSIONS, interpreter=None):
        # 解釈の係：本物の Claude（ANTHROPIC_API_KEY があるとき）か、偽のClaude（モック）
        self.interpreter = interpreter if interpreter is not None else claude_interpreter.from_env()
        self.max_sessions = max_sessions
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    # ---- 読込 ----
    def load(self, data: bytes) -> dict:
        view = api.job_view(data)
        if not view.get("ok"):
            return {"ok": False, "error": view.get("error") or "ジョブを読めません"}
        job = jbi.parse_bytes(data)
        if not job.name:
            return {"ok": False, "error": "ジョブ名の行（//NAME）が読めないため、読み込みません"}
        entry = _Entry(session=None)  # type: ignore[arg-type]
        entry.session = Session(job, simulate=entry.simulate)
        entry.session.log.append((self._who(), self.interpreter.notice))
        sid = secrets.token_urlsafe(16)
        with self._lock:
            self._entries[sid] = entry
            while len(self._entries) > self.max_sessions:
                self._entries.popitem(last=False)
        return {"ok": True, "session_id": sid, "view": view, "state": self._state(sid, entry)}

    def state(self, sid: object) -> dict:
        return self._run(sid, lambda e: {"ok": True})

    # ---- 指示（音声・文字）。ここからは承認・出力を呼ばない ----
    def hear(self, sid: object, text: object, source: object = "text") -> dict:
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "指示の文字がありません"}
        if len(text) > MAX_TEXT:
            return {"ok": False, "error": f"指示の文字が長すぎます（{MAX_TEXT}文字まで）"}
        if source not in SOURCES:
            return {"ok": False, "error": "指示の種類（voice か text）が分かりません"}

        def run(e: _Entry) -> dict:
            self._cancel_approval(e, "新しい指示があったため")
            f0 = ut.read_utterance(text)
            talking = e.dialog.step is not None
            out = (self._dialog_turn(e, text, source, f0) if talking
                   else self._maybe_start_dialog(e, text, source, f0))
            if out is not None:
                return out
            out = self._instruction(e, text, source)
            s = e.session
            if out.get("candidate_made") or (s.facts is not None and s.facts.op == "undo" and out.get("status") == "ok"):
                e.dialog.estimated = None  # ふつうの指示が通った：推定（未確定）の名前は使わない（断られた発話では消さない）
            if talking:  # 会話の途中の新しい指示：会話をやめたことを知らせる
                out["message"] = dg.ENDED_NEW + (out.get("message") or "")
                out["dialog"] = True
                e.dialog.say(APP_SPEAKER, out["message"], out.get("status") or "ok")
            return out
        return self._run(sid, run)

    def _instruction(self, e: _Entry, text: str, source: str) -> dict:
        """ふつうの指示：Session.hear → 解釈の係 → select / propose / compare / undo。"""
        s = e.session
        r = s.hear(text, source=source)
        calls: list[dict] = []
        if r.status == "ok":
            f = s.facts
            if f is not None and f.op in ("move", "speed") and s.candidate is not None:
                # 承認待ちの候補がある：解釈の係（Claude）には送らない（呼ぶ前に断る）
                r = s.pending_reply(f)
            elif f is not None and f.op in ("move", "speed") and (f.position or s.selected) is None:
                # 位置が決まらなければ、解釈の係（Claude）には送らない
                s.log.append(("聞き返し", NO_POSITION))
                r = Reply("ask", NO_POSITION, facts=f)
            else:
                try:
                    calls = self.interpreter.decide(s.facts, s.selected, list(s.original.positions))
                except claude_interpreter.InterpreterError as ex:
                    # モックへ自動で切り替えない。固定の文で止める
                    s.log.append(("止めた理由", str(ex)))
                    r = Reply("stop", str(ex), facts=f)
                else:
                    r = self._dispatch(e, r, calls)
        return {"ok": True, "heard": text, "source": source, "facts": _facts(s.facts),
                "tool_calls": calls, "dialog": False, **self._reply(e, r)}

    # ---- Claude との会話（voice_teaching/dialog.py） ----
    def _speaker(self) -> str:
        return "Claude" if self.interpreter.kind == "claude" else dg.MOCK_SPEAKER

    def _dialog_out(self, e: _Entry, text: str, source: str, f0, status: str, message: str,
                    who: str | None = None, choices: tuple = ()) -> dict:
        """会話の1回分の応答（ここでは候補を作らない）。会話の欄と作業ログに残す。

        話し手の表示は正直に：プログラムの決まった文は「アプリ」。Claude（モック）の位置の候補を示す文だけを
        who=self._speaker() で出す（_locate の最後だけ）。
        """
        s = e.session
        s.forget_question()
        e.dialog.say(who or APP_SPEAKER, message, status)
        s.log.append(({"ok": "結果", "ask": "聞き返し", "stop": "止めた理由"}[status], message))
        return {"ok": True, "heard": text, "source": source, "facts": _facts(f0), "tool_calls": [], "dialog": True,
                "status": status, "message": message, "blocked_reason": None, "candidate_made": False,
                "choices": [{"label": label, "text": t} for label, t in choices]}

    def _human(self, e: _Entry, text: str, source: str) -> None:
        e.dialog.say("人", text, "human")
        e.session.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}"))

    def _maybe_start_dialog(self, e: _Entry, text: str, source: str, f0) -> dict | None:
        """場所の言葉（「置くところ」）で始まる会話。ほかの pre_check の結果（打ち消し・繰り返し・疑問の形など）は変えない。"""
        s, d = e.session, e.dialog
        word = dg.find_place_word(text, f0)
        if word is None:
            return None
        ops = [k for k in f0.kinds if k in ut.OPS]
        if (f0.negations or f0.undo_negations or getattr(f0, "repeat", None) or getattr(f0, "question", False)
                or getattr(f0, "place_as_destination", False)
                or f0.positions or f0.bad_positions or len(ops) > 1 or f0.op in ("speed", "compare", "undo")
                or any(r != ut.FRONT_BACK_MESSAGE for r in f0.stop_reasons)):
            return None  # 今の流れのまま（打ち消し・繰り返し・疑問の形・行き先の場所の言葉・承認の言葉 など）
        rel = dg.find_relative_dir(text, f0)
        if rel and (dg.has_neighbour_relative(text, word) or f0.axes or f0.vertical or dg.has_sign_word(text)):
            # 向きの言葉と、軸・上げ下げ・符号が一緒（「置くところを奥へ3mm下げて」）、または「置くところの手前」：
            # 向きの言葉を捨てて読まない。会話にせず、今の流れ（止める・聞き返す）のまま
            return None
        two = dg.count_place_words(text) >= 2
        fs = None
        refused = None
        if not two:
            sub = dg.substitute_opening(text, word)
            fs = ut.read_utterance(sub)
            if not fs.kinds and not fs.unknown_words and not fs.unclear_amounts:
                # 「置くところを3mm」「置くところ」：動かす指示として読む（方向・量が無いのは、会話が受け持つ理由）
                fs = ut.read_utterance(sub + "移動")
            if fs.place_ref:
                two = True
            elif not dg.handled_by_dialogue(fs):
                # 会話が受け持たない理由（D1 の2つの読み方・知らない言葉・符号 など）：その答えをそのまま返す。
                # 選択肢の文には場所の言葉が無く、別の位置に当たるおそれがあるので、選択肢は出さない
                refused = ut.pre_check(fs)
        est = d.estimated
        d.start(word, rel, dg.Slots())
        d.estimated = est
        self._human(e, text, source)
        if two:  # 場所の言葉が2つ：1つずつ（候補は頼まない）
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "ask", dg.TWO_PLACES)
        if refused is not None:
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, refused.status, refused.message)
        if s.candidate is not None:  # 承認待ちの候補がある：位置の候補も頼まない
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "stop", PENDING_MESSAGE, who="アプリ")
        fs = ut.read_utterance(dg.substitute_opening(text, word))
        if fs.unknown_words:  # B1：知らない言葉を捨てて読まない
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(fs.unknown_words, "move"),
                                    who="アプリ")
        slots = dg.read_slots(fs, opening=True)
        if slots.problem:
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        d.estimated = None  # 新しい会話を始めた：前の推定は使わない
        d.amount, d.axis, d.sign = slots.amount, slots.axis, slots.sign
        if rel:  # 向きの言葉だけ：方向は必ず6つのボタンで聞く（始めの発話からは決めない）
            d.axis = d.sign = None
        d.turn += 1
        return self._locate(e, text, source, f0, word)

    def _locate(self, e: _Entry, text: str, source: str, f0, word: str) -> dict:
        s, d = e.session, e.dialog
        job = s.original
        frame_pos = dg.frame_lookup(e.frame, word)
        cand = dg.frame_candidate(job, frame_pos) if frame_pos else None
        if cand is not None:  # 案B：専門家が確定した名前。Claude は呼ばない
            d.candidates = [cand]
        elif not sim._is_synthetic(s.original_bytes):  # 合成でないジョブの中身は送らない
            d.step = "pick"
            return self._dialog_out(e, text, source, f0, "ask", dg.NOT_SYNTHETIC.format(word=word), who="アプリ")
        else:
            locate = getattr(self.interpreter, "locate", None)
            if locate is None:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.NO_LOCATOR, who="アプリ")
            try:
                raw = locate(text, word, list(job.positions), dg.instruction_lines(job))
            except claude_interpreter.InterpreterError as ex:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_ERROR.format(error=ex), who="アプリ")
            except Exception:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_FAILED.format(word=word), who="アプリ")
            d.candidates = dg.verify_candidates(job, raw)
            if not d.candidates:  # 照合に通らない答えは使わない（推測で補わない）
                s.log.append((self._who(), "位置の候補は、照合に通りませんでした"))
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_FAILED.format(word=word), who="アプリ")
            s.log.append((self._who(), "位置の候補：" + "・".join(
                f"{c.position}（根拠 {'・'.join(str(x.line_no) for x in c.evidence)}行目）" for c in d.candidates)))
        d.step = "confirm_position"
        msg = (dg.ASK_ONE.format(word=word, pos=d.candidates[0].position) if len(d.candidates) == 1
               else dg.ASK_MANY.format(word=word, n=len(d.candidates)))
        # Claude（モック）の位置の候補を示す文だけは、その話し手として出す。FRAME で決まったものは「アプリ」
        who = APP_SPEAKER if d.candidates[0].confirmed_by_frame else self._speaker()
        out = self._dialog_out(e, text, source, f0, "ask", msg, who=who)
        d.proposal_mark = len(d.turns)  # 声の肯定は、この直後だけ使える
        return out

    def _dialog_turn(self, e: _Entry, text: str, source: str, f0) -> dict | None:
        """会話の途中の発話。None を返したら、会話をやめて、ふつうの指示として読む。"""
        d = e.dialog
        if d.step == "pick":
            # 手で選ぶ段：位置番号だけの文なら、選んだことにする。それ以外は段を変えずに聞く（集めた量・方向は捨てない）
            name = ut.normalize_position(text.strip().strip("。、.!！"))
            self._human(e, text, source)
            if name is not None:
                r = e.session.select(name)
                if r.status != "ok":
                    return self._dialog_out(e, text, source, f0, "stop", r.message)
                return self._dialog_pick(e)
            v = ut.pre_check(f0)
            if v.status == "stop":
                return self._dialog_out(e, text, source, f0, "stop", v.message)
            return self._dialog_out(e, text, source, f0, "ask", dg.PICK_PROMPT)
        v = ut.pre_check(f0)
        if d.step == "confirm_position":
            fresh = len(d.turns) == d.proposal_mark  # 最後のやり取りが位置の候補の問いか
            self._human(e, text, source)
            if dg.is_affirmative(text):  # 選択であって承認ではない（候補が1つ・問いの直後のときだけ）
                if not fresh:
                    return self._dialog_out(e, text, source, f0, "ask", dg.AFFIRM_BUTTON)
                if len(d.candidates) == 1:
                    return self._confirm_position(e, text, source, f0, d.candidates[0].position)
                return self._dialog_out(e, text, source, f0, "ask", dg.AFFIRM_MANY)
            if f0.op == "undo" and v.status == "ok":
                d.end()
                return self._dialog_out(e, text, source, f0, "ok", dg.END_UNDO)
            if v.status == "stop":  # 打ち消し・承認の言葉など：止めて、同じ所にいる（選択肢もそのまま）
                return self._dialog_out(e, text, source, f0, "stop", v.message, who="アプリ")
            return self._dialog_out(e, text, source, f0, "ask", dg.CONFIRM_STAY)
        # ask_move：方向と量を聞いている
        if f0.negations or f0.undo_negations:
            self._human(e, text, source)
            return self._dialog_out(e, text, source, f0, "stop", v.message, who="アプリ")
        if f0.op == "undo" and v.status == "ok":
            self._human(e, text, source)
            d.end()
            return self._dialog_out(e, text, source, f0, "ok", dg.END_UNDO)
        if f0.positions or f0.bad_positions or f0.op in ("speed", "compare") or "select" in f0.kinds:
            d.end()  # 新しい指示：会話をやめて、ふつうに読む（hear が「会話をやめて…」と知らせる）
            return None
        self._human(e, text, source)
        if d.confirm_go:  # 手で選んだ位置の確かめ：［go］のボタンのほかでは進めない
            return self._dialog_out(e, text, source, f0, v.status if v.status == "stop" else "ask",
                                    v.message if v.status == "stop" else dg.GO_BUTTON)
        if getattr(f0, "repeat", None) or getattr(f0, "question", False):
            # 繰り返し・疑問の形は、今の pre_check の答えのまま（量として使わない）
            return self._dialog_out(e, text, source, f0, v.status if v.status != "ok" else "ask",
                                    v.message or d.ask_move_message(), who="アプリ", choices=v.choices)
        others = [r for r in f0.stop_reasons if r != ut.FRONT_BACK_MESSAGE]
        if others:  # 承認・起動の言葉など
            return self._dialog_out(e, text, source, f0, "stop", "。".join(others), who="アプリ")
        rel = dg.find_relative_dir(text, f0)
        if rel:  # 手前・右などは軸にしない。6つのボタンで聞く
            d.relative = rel
            return self._dialog_out(e, text, source, f0, "ask", dg.RELATIVE_ASK.format(rel=rel))
        if f0.unknown_words:  # B1
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(f0.unknown_words, "move"),
                                    who="アプリ")
        if f0.place_ref:  # 答えの中の場所の言葉：会話をやめて、今の流れ（聞き返し）のまま
            d.end()
            return None
        if f0.kinds and not dg.handled_by_dialogue(f0):
            # 会話が受け持たない理由（2つの操作・D1 の2つの読み方 など）：今の pre_check の答えをそのまま返す
            v = ut.pre_check(f0)
            return self._dialog_out(e, text, source, f0, v.status, v.message, choices=v.choices)
        slots = dg.read_slots(f0)
        if slots.problem:
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        if slots.amount is None and slots.axis is None and slots.sign is None:
            return self._dialog_out(e, text, source, f0, "ask", dg.NOTHING_READ + d.ask_move_message())
        return self._merge_slots(e, text, source, f0, slots.amount, slots.axis, slots.sign)

    def _merge_slots(self, e: _Entry, text, source, f0, amount, axis, sign) -> dict:
        d = e.dialog
        d.confirm_go = False
        if amount is not None:
            d.amount = amount
        problem = None
        if sign is not None and d.sign is not None and sign != d.sign:
            problem = dg.SIGN_CONFLICT
        elif axis is not None and d.axis is not None and axis != d.axis:
            problem = dg.AXIS_CONFLICT
        if problem:  # 前の答えと食い違う：どちらも使わず、ボタンで選び直してもらう
            d.axis = d.sign = None
            d.turn += 1
            return self._dialog_out(e, text, source, f0, "ask", problem)
        d.axis = axis if axis is not None else d.axis
        d.sign = sign if sign is not None else d.sign
        if d.complete():
            return self._run_canonical(e, source)
        d.turn += 1
        return self._dialog_out(e, text, source, f0, "ask", d.ask_move_message())

    def _confirm_position(self, e: _Entry, text: str, source: str, f0, position: str) -> dict:
        """人が選んだ位置（ボタンか短い肯定）。選んだだけで、ジョブ・承認は変えない。"""
        s, d = e.session, e.dialog
        cand = next((c for c in d.candidates if c.position == position), None)
        r = s.select(position)
        if r.status != "ok":
            return self._dialog_out(e, text, source, f0, "stop", r.message, who="アプリ")
        d.position = position
        d.estimated = (None if cand is None or cand.confirmed_by_frame else
                       {"position": position, "word": d.word, "label": dg.ESTIMATE_LABEL, "by": self._speaker()})
        d.candidates = []
        d.step = "ask_move"
        if d.complete():
            return self._run_canonical(e, source)
        d.turn += 1
        return self._dialog_out(e, text, source, f0, "ask", d.ask_move_message())

    def _run_canonical(self, e: _Entry, source: str) -> dict:
        """集めたことを決まった文にして、ふつうの指示と同じ流れに通す（ガードはすべてそのまま）。"""
        d = e.dialog
        text = dg.canonical_text(d.position, d.axis, d.sign, d.amount)
        est = d.estimated
        d.end()
        out = self._instruction(e, text, source)
        if out.get("candidate_made"):
            d.estimated = est  # 会話で選んだ位置の候補にだけ「推定（未確定）」を残す（この画面の中だけ）
            d.say(APP_SPEAKER, dg.DONE, "ok")  # 決まった文（プログラム）
        else:
            d.say("アプリ", out.get("message") or "", out.get("status") or "stop")
        out["dialog"] = True
        return out

    def choose(self, sid: object, choice: object, turn: object) -> dict:
        """会話の選択肢のボタン。今の選択肢の番号（turn）と id が合うときだけ使う。承認・出力は呼ばない。"""
        def run(e: _Entry) -> dict:
            d = e.dialog
            ids = {c["id"]: c["label"] for c in d.choices()}
            if (d.step is None or isinstance(turn, bool) or not isinstance(turn, int) or turn != d.turn
                    or not isinstance(choice, str) or choice not in ids):
                e.session.log.append(("止めた理由", dg.STALE))
                return {"ok": True, "status": "stop", "message": dg.STALE, "blocked_reason": None,
                        "candidate_made": False, "choices": [], "dialog": True, "tool_calls": []}
            self._cancel_approval(e, "新しい指示があったため")
            label = ids[choice]
            d.say("人", label, "human")
            e.session.log.append(("選択", f"ボタン：{label}"))
            if choice.startswith("pos:"):
                return self._confirm_position(e, label, "text", None, choice[4:])
            if choice == "go":  # 手で選んだ位置で、方向と量がそろったことの確かめ
                return self._run_canonical(e, "text")
            if choice == "other":
                d.step, d.candidates = "pick", []
                d.turn += 1
                return self._dialog_out(e, label, "text", None, "ask", dg.PICK_PROMPT, who="アプリ")
            direction = choice[4:]
            return self._merge_slots(e, label, "text", None, None, direction[0], 1 if direction[1] == "+" else -1)
        return self._run(sid, run)

    def _dispatch(self, e: _Entry, heard: Reply, calls: list[dict]) -> Reply:
        s = e.session
        who = self._who()
        if not calls:
            f = s.facts
            s.log.append((who, "道具は呼びませんでした"))
            if f is not None and f.op in ("move", "speed"):
                s.log.append(("聞き返し", NOT_DECIDED))
                return Reply("ask", NOT_DECIDED, facts=f)
            return heard
        r = heard
        for c in calls:
            tool, args = c["tool"], c["args"]
            s.log.append((who, f"道具 {tool} を呼びました：{_args_text(args)}"))
            if tool == "ask_user":
                q = str(args.get("question") or NOT_DECIDED)
                s.log.append(("聞き返し", q))
                r = Reply("ask", q, facts=s.facts)
            elif tool == "select":
                r = s.select(args["position"])
            elif tool == "propose_change":
                e.sim_error = None
                r = s.propose(args)
                if r.status != "ok" and r.blocked_reason and e.sim_error:
                    msg = f"{r.message}。シミュレーションの候補も作りません：{e.sim_error}"
                    s.log.append(("止めた理由", f"シミュレーションの候補も作りません：{e.sim_error}"))
                    r = Reply(r.status, msg, facts=r.facts, blocked_reason=r.blocked_reason)
            elif tool == "compare":
                r = s.compare()
            elif tool == "undo":
                r = s.undo()
            else:  # 決めた道具のほかは使わない
                r = Reply("stop", "知らない道具の呼び出しのため、使いません")
            self._remember(e, r)
            if r.status != "ok":
                break
        return r

    def _who(self) -> str:
        return "Claude" if self.interpreter.kind == "claude" else "モック"

    # ---- 画面の操作 ----
    def select(self, sid: object, position: object) -> dict:
        def run(e: _Entry) -> dict:
            self._cancel_approval(e, "位置を選び直したため")
            r = e.session.select(position if isinstance(position, str) else "")
            d = e.dialog
            if r.status == "ok":
                d.estimated = None  # 人が選び直した：推定の名前は使わない
                if d.step is not None:
                    d.say("人", f"{e.session.selected} を選びました", "human")
                    return self._dialog_pick(e)
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    def _dialog_pick(self, e: _Entry) -> dict:
        """会話の途中で人が位置を選んだ（「ほかの位置」のあとなど）：その位置で、方向と量を聞く。"""
        d = e.dialog
        d.estimated = None
        d.position, d.candidates, d.step = e.session.selected, [], "ask_move"
        d.turn += 1
        if d.complete():
            # 手で選んだだけでは候補にしない。ボタン1つで確かめる（声では進めない）
            d.confirm_go = True
            return self._dialog_out(e, "", "text", None, "ask", d.confirm_go_message())
        return self._dialog_out(e, "", "text", None, "ask", d.ask_move_message())

    def compare(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            r = e.session.compare()
            self._remember(e, r)
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    def undo(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            self._cancel_approval(e, "元に戻したため")
            if e.dialog.step is not None:
                e.dialog.end()
            e.dialog.estimated = None
            r = e.session.undo()
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    # ---- 承認（画面の承認ボタンからだけ呼ぶ） ----
    def approve(self, sid: object, approver: object) -> dict:
        def run(e: _Entry) -> dict:
            s = e.session
            ch = s.candidate
            if ch is None:
                return self._fail(e, "承認する変更候補がありません")
            if not isinstance(approver, str) or not approver.strip():
                return self._fail(e, "承認した人の名前がないため、承認できません")
            if ch.kind != "shift":
                return self._fail(e, "シミュレーションの出力は位置の移動だけです。この候補は承認できません")
            if s.candidate_blocked_reason:
                # 実機用の検査で止まった候補は、合成ジョブでも承認しない（CHECKER_019 R1）
                return self._fail(e, f"実機用の検査で止まった変更候補は、承認できません（理由：{s.candidate_blocked_reason}）")
            position, axis = ch.target.split()
            try:
                sp = sim.simulate_shift(s.original, position, axis, ch.requested)
            except sim.SimError as ex:
                return self._fail(e, f"承認できません：{ex}")
            if sp.original_bytes != s.original_bytes or sp.new_bytes != ch.new_job.render_bytes():
                return self._fail(e, "変更候補と、出力する中身が合わないため、承認できません")
            try:
                ap = sim.approve(sp, approver)
            except sim.SimError as ex:
                return self._fail(e, str(ex))
            e.sim_proposal, e.approval, e.approved_candidate = sp, ap, ch
            e.exported = None
            s.log.append(("承認", f"{ap.approved_by} さんが承認ボタンで承認しました（{ap.approved_at}）"))
            return {"ok": True, "state": None}
        return self._run(sid, run)

    # ---- シミュレーション用の出力（合成ジョブだけ） ----
    def export(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            if not self._approved(e):
                return self._fail(e, NOT_APPROVED)
            try:
                path = sim.export_sim(e.sim_proposal, e.approval)
            except sim.SimError as ex:
                return self._fail(e, f"出力しませんでした：{ex}")
            full = str(Path(path).resolve())
            e.exported = full
            e.session.log.append(("出力", f"シミュレーション用のジョブを出力しました：{full}（実機には入れないでください）"))
            return {"ok": True, "path": full, "file_name": Path(path).name}
        return self._run(sid, run)

    # ---- 内部 ----
    def _run(self, sid: object, fn) -> dict:
        with self._lock:
            e = self._entries.get(sid) if isinstance(sid, str) else None
            if e is not None:
                self._entries.move_to_end(sid)
        if e is None:
            return {"ok": False, "error": NO_SESSION}
        with e.lock:
            out = fn(e)
            self._sync(e)
            out["state"] = self._state(sid, e)
            return out

    def _fail(self, e: _Entry, message: str) -> dict:
        e.session.log.append(("止めた理由", message))
        return {"ok": False, "error": message}

    def _remember(self, e: _Entry, r: Reply) -> None:
        if r.comparison is not None and e.session.candidate is not None:
            e.comparison = (e.session.candidate, r.comparison)

    def _approved(self, e: _Entry) -> bool:
        return (e.approval is not None and e.sim_proposal is not None
                and e.session.candidate is not None and e.approved_candidate is e.session.candidate)

    def _cancel_approval(self, e: _Entry, why: str) -> None:
        if e.approval is not None:
            e.approval = e.sim_proposal = e.approved_candidate = None
            e.session.log.append(("承認", f"{why}、承認を取り消しました。もう一度確かめて、承認ボタンで承認してください"))

    def _sync(self, e: _Entry) -> None:
        if e.approval is not None and not self._approved(e):
            self._cancel_approval(e, "変更候補が変わったため")

    def _reply(self, e: _Entry, r: Reply) -> dict:
        return {"status": r.status, "message": r.message, "blocked_reason": r.blocked_reason,
                "candidate_made": r.candidate_made,
                "choices": [{"label": label, "text": text} for label, text in r.choices]}

    def _state(self, sid: str, e: _Entry) -> dict:
        s = e.session
        cand = None
        ch = s.candidate
        if ch is not None:
            if e.comparison is None or e.comparison[0] is not ch:
                cand = None
            else:
                cand = _comparison_dict(e.comparison[1], ch, s)
                est = e.dialog.estimated
                cand["estimated"] = est if est and est.get("position") == cand["target"].split()[0] else None
        approved = self._approved(e)
        return {
            "session_id": sid,
            "mode": api.MODE,
            "job_name": s.original.name,
            "selected": s.selected,
            "facts": _facts(s.facts),
            # 確認済みの方向の名前だけ（未確認の 右・前 などは入れない）
            "direction_names": ut.DIRECTION_NAMES, "direction_names_source": ut.DIRECTION_NAMES_SOURCE,
            "candidate": cand,
            "confirmation_text": s.confirmation_text(),
            "blocked_reason": s.candidate_blocked_reason if ch is not None else None,
            "approval": ({"approved_by": e.approval.approved_by, "approved_at": e.approval.approved_at}
                         if approved else None),
            "can_approve": (ch is not None and ch.kind == "shift" and not approved
                            and not s.candidate_blocked_reason),
            "can_export": approved,
            "exported": e.exported if approved else None,
            "out_dir": str(Path(sim.DEFAULT_OUT_DIR).resolve()),
            "mock_notice": self.interpreter.notice,
            "interpreter": {"kind": self.interpreter.kind, "model": self.interpreter.model,
                            "notice": self.interpreter.notice},
            "log": _log_entries(s.log),
            "dialog": self._dialog_state(e),
        }

    def _dialog_state(self, e: _Entry) -> dict:
        d = e.dialog
        est = d.estimated if d.estimated and d.estimated.get("position") == e.session.selected else None
        on = d.step is not None
        return {
            "step": d.step, "turn": d.turn,
            "word": d.word if on else None, "relative": d.relative if on else None,
            "candidates": [c.as_dict() for c in d.candidates] if d.step == "confirm_position" else [],
            "choices": d.choices(),
            "position": d.position if on else None,
            "amount_mm": d.amount if on else None,
            "direction": d.direction if on else None,
            "estimated": est,  # 推定（未確定）。この画面の中だけ。保存しない
            "speaker": self._speaker(),
            "locator": {"kind": self.interpreter.kind,
                        "label": getattr(self.interpreter, "locator_label", None) or self._speaker()},
            "turns": list(d.turns),
        }


def _xyz(job: jbi.Job, name: str) -> list[str] | None:
    pos = job.positions.get(name)
    if pos is None or len(pos.values) < 3 or not pos.is_robot_coordinates():
        return None  # ロボット座標（直交）でなければ X・Y・Z として出さない
    return [f"{v:.3f}" for v in pos.xyz]


def _comparison_dict(c: Comparison, ch: jbi.ChangeProposal, s) -> dict:
    d = {
        "kind": c.kind, "target": c.target, "line_no": c.line_number,
        "before_line": c.before_line, "after_line": c.after_line,
        "before_value": c.before_value, "after_value": c.after_value, "delta": c.delta,
        "affected": list(c.affected), "warnings": list(c.warnings),
        "blocked_reason": c.blocked_reason,
        "heard": s.candidate_utterance,  # 認識した文字（または打った文字）
    }
    if ch.kind == "shift":
        fig = api.figure_data(ch.new_job)
        name = c.target.split()[0]
        d.update(after_positions=fig["positions"], after_segments=fig["segments"], moved=name,
                 before_xyz=_xyz(s.original, name), after_xyz=_xyz(ch.new_job, name))  # 変更前後の X・Y・Z（プログラムが読んだ値）
    return d


def _facts(f: ut.UtteranceFacts | None) -> dict | None:
    if f is None:
        return None
    return {
        "text": f.raw,
        "kinds": list(f.kinds),
        "positions": list(f.positions),
        "bad_positions": list(f.bad_positions),
        "amounts": [a.text for a in f.amounts],
        "unclear_amounts": list(f.unclear_amounts),
        "axes": list(f.axes),
        "sides": list(f.sides),
        "refers_to_selection": f.refers_to_selection,
        "requested_value": f.requested_value(),
        "stop_reasons": list(f.stop_reasons),
        "unknown_words": list(f.unknown_words),  # 読み取りで使わなかった言葉（移動・速度の指示なら聞き返す）
        "negations": list(f.negations),  # 打ち消しの言葉（あれば止める）
        "undo_negations": list(f.undo_negations),  # 元に戻す言葉の打ち消し（「キャンセルはやめて」など。あれば止める）
        # 2026-11-03 夜の決定（docs/UNKNOWN_WORDS_PLAN.md 7章）
        "question": f.question, "question_text": f.question_text,
        "repeat": list(f.repeat) if f.repeat else None,
        "place_ref": f.place_ref, "place_as_destination": f.place_as_destination,
        "relative_dir": list(f.relative_dir),
    }


def _log_entries(log: list) -> list[dict]:
    """作業ログ。候補の直後に同じ文の「結果」が続くときは1つにする（同じ行が2回出ないように）。"""
    out: list[dict] = []
    for k, t in log:
        if k == "結果" and out and out[-1]["kind"] == "候補" and out[-1]["text"] == t:
            continue
        out.append({"kind": k, "text": t})
    return out


def _args_text(args: dict) -> str:
    return "、".join(f"{k}={v}" for k, v in args.items()) or "（引数なし）"
