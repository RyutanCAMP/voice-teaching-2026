"""画面の一連の流れ（シミュレーションモード）：読込 → 位置選択 → 指示 → 変更候補 → プレビュー → ボタン承認 → 出力。

- 画面ごとの Session はメモリの中だけに持つ（ディスクに保存しない）。数を超えたら古いものから捨てる。
- 指示（hear）は Session.hear → 解釈の係（本物の Claude か偽のClaude。claude_interpreter.from_env）→ Session の select / propose / compare / undo。
  指示の経路からは承認も出力も呼ばない（「承認」「OK」などの発話は Session が止める）。
- 承認（approve）は画面の承認ボタンからだけ呼ぶ。sim.approve を使い、承認した人の名前が要る。
- 出力（export）は sim.export_sim（合成ジョブだけ）。失敗は理由を返し、成功と表示させない。
- 承認の後で新しい指示・元に戻す・位置の選択があったら、承認を取り消す。
- 実機用の出力・転送・ロボットの起動は無い。
"""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .. import claude_interpreter, jbi, mock_claude, sim
from .. import utterance as ut
from ..session import Comparison, Reply, Session
from . import api

MAX_SESSIONS = 8
MAX_TEXT = 500
SOURCES = ("voice", "text")
NO_SESSION = "このジョブの画面の情報がありません（時間が経って捨てたか、サーバーを起動し直しました）。ジョブを読み込み直してください"
NO_POSITION = "位置が決まっていないため、変更の提案をしていません。位置を選ぶか、位置番号（C と5桁の数字）で言ってください"
NOT_DECIDED = "指示から変更の内容を決められませんでした。位置番号・方向・量（mm）を、もう一度指示してください"
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
            s = e.session
            self._cancel_approval(e, "新しい指示があったため")
            r = s.hear(text, source=source)
            calls: list[dict] = []
            if r.status == "ok":
                f = s.facts
                if f is not None and f.op in ("move", "speed") and (f.position or s.selected) is None:
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
                    "tool_calls": calls, **self._reply(e, r)}
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
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    def compare(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            r = e.session.compare()
            self._remember(e, r)
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    def undo(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            self._cancel_approval(e, "元に戻したため")
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
                "candidate_made": r.candidate_made}

    def _state(self, sid: str, e: _Entry) -> dict:
        s = e.session
        cand = None
        ch = s.candidate
        if ch is not None:
            if e.comparison is None or e.comparison[0] is not ch:
                cand = None
            else:
                cand = _comparison_dict(e.comparison[1], ch)
        approved = self._approved(e)
        return {
            "session_id": sid,
            "mode": api.MODE,
            "job_name": s.original.name,
            "selected": s.selected,
            "facts": _facts(s.facts),
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
        }


def _comparison_dict(c: Comparison, ch: jbi.ChangeProposal) -> dict:
    d = {
        "kind": c.kind, "target": c.target, "line_no": c.line_number,
        "before_line": c.before_line, "after_line": c.after_line,
        "before_value": c.before_value, "after_value": c.after_value, "delta": c.delta,
        "affected": list(c.affected), "warnings": list(c.warnings),
        "blocked_reason": c.blocked_reason,
    }
    if ch.kind == "shift":
        fig = api.figure_data(ch.new_job)
        d.update(after_positions=fig["positions"], after_segments=fig["segments"],
                 moved=c.target.split()[0])
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
