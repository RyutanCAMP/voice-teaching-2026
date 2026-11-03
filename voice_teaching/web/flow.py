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
- 上下の向き（CHECKER_026 M2・11章）：毎回の指示・承認・出力・状態の表示のときに、上下の向きの設定（updown_config）を
  読み直してジョブと照合する。照合に通ったときだけ「上・下」を Z± にし、方向のボタンに「（上）」「（下）」を付ける。
  候補を作ったときの照合の結果と、承認・出力のときの結果が違えば（設定が1バイトでも変われば）、承認を消して断る。
  承認の記録と作業ログに、使った設定の指紋と確認した人を残す。設定は読むだけ（画面・API・声からは書かない）。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .. import claude_explain, claude_interpreter, jbi, mock_claude, sim
from .. import claude_talk as talk
from .. import repeat_count as rc
from .. import dialog as dg
from .. import updown as ud
from .. import utterance as ut
from ..session import APPROVED_PENDING_MESSAGE, NO_POSITION, PENDING_MESSAGE, Comparison, Reply, Session
from . import api

MAX_SESSIONS = 8
MAX_TEXT = 500
SOURCES = ("voice", "text")
NO_SESSION = "このジョブの画面の情報がありません（時間が経って捨てたか、サーバーを起動し直しました）。ジョブを読み込み直してください"
NOT_DECIDED = "指示から変更の内容を決められませんでした。位置番号・方向・量（mm）を、もう一度指示してください"
APP_SPEAKER = "アプリ"  # プログラムの決まった文の話し手（Claude の文ではない）
NOT_APPROVED = "承認されていないため、出力しません（承認は確認表示の承認ボタンで行います）"
UPDOWN_CHANGED_APPROVE = ("変更候補を作ったあとで、上下の向きの設定（または照合の結果）が変わったため、承認できません。"
                          "元に戻して、もう一度指示してください")
UPDOWN_CHANGED_EXPORT = ("承認のあとで、上下の向きの設定（または照合の結果）が変わったため、出力しません。"
                         "承認を取り消しました")
UPDOWN_CHANGED_WHY = "上下の向きの設定（または照合の結果）が変わったため"
# 承認のあとの新しい指示で、承認と一緒に変更候補も捨てたときの文（元に戻して・キャンセル・別の位置の選択）。
# 決まった文（APPROVED_PENDING_MESSAGE）は候補が残るときだけ使う。この文も唐川さんの決定の文（一字一句このまま）
APPROVAL_AND_CANDIDATE_CANCELLED = "前の承認と変更候補を取り消しました。元のJOBは変更されていません。"
# 候補を捨てたことを言う Session の返事の文（APPROVAL_AND_CANDIDATE_CANCELLED を付けるときは、重ねないよう外す）
_DROPPED_PHRASES = ("変更候補を捨てました。元のジョブは変わっていません", "。別の位置の変更候補は捨てました（元のジョブは変わっていません）")
# 上下の向きの設定ファイルの場所の既定。None ＝ 設定なし（未確認。上・下を Z にしない）。
# サーバーは起動オプション（--updown-config。既定 records/updown.json）の場所を渡す。テストでは一時フォルダの設定に置き換える
DEFAULT_UPDOWN_CONFIG: "str | Path | None" = None
USE_DEFAULT = object()  # updown_config を渡さなかった印（DEFAULT_UPDOWN_CONFIG を使う）
# 承認のあとの3つの状態の決まった文（台本 Ver.6.1 Scene 3-3・チャッピーの修正③）。「変更できた」とは言わない
ORIGINAL_UNCHANGED = "変わっていません"
CANDIDATE_APPROVED = "人が承認しました（シミュレーション用）"
NOT_SENT = "していません"  # このアプリにはロボットへ送る・転送する処理が無い
ORIGINAL_UNKNOWN_NOTE = "元の動作データが読み込んだときと同じか確かめられないため、「元の動作データ」の行は出しません"
EXPORTED_LINE = "シミュレーション用に出力しました（{name}。実機には入れないでください）"
_UPDOWN_STATUS_NAMES = {"unconfirmed": "未確認", "mismatch": "不一致", "changed": "変更済み"}


@dataclass
class _Entry:
    session: Session
    sim_error: str | None = None                  # シミュレーションの候補も作れなかった理由（最後の1つ）
    comparison: tuple | None = None               # (候補, Comparison) 候補ごとの比較
    sim_proposal: sim.SimProposal | None = None
    approval: sim.Approval | None = None
    approved_candidate: object | None = None      # 承認したときの候補（同じものか確かめる）
    approval_updown: ud.UpdownStatus | None = None  # 承認したときの上下の向きの照合の結果
    # 最後に承認を消した理由が、上下の向きの設定（照合の結果）の変更か。出力の断り文に使う（P2-3）。
    # 承認したとき・ほかの理由で承認を消したとき・新しい指示・選び直し・元に戻すで False に戻す
    updown_cancelled: bool = False
    exported: str | None = None
    loaded_sha256: str | None = None              # 読み込んだときのバイト列の sha256（承認のあとの「元の動作データ：変わっていません」に使う）
    dialog: dg.Dialog = field(default_factory=dg.Dialog)  # Claude との会話の状態（メモリの中だけ。保存しない）
    frame: object | None = None                   # 案B：FRAME（まだ無い。あれば label・aliases で位置を決める）
    explain: "claude_explain.Conversation | None" = None  # Claude に聞く（読み取りだけ）の会話。メモリの中だけ
    # 人が教えた意味（この画面の会話の中だけ。ファイル・records/・作業ログに書かない。/api/hear の流れは読まない）
    talk: "talk.TalkMemory | None" = None
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
    def __init__(self, max_sessions: int = MAX_SESSIONS, interpreter=None,
                 updown_config: "str | Path | None | object" = USE_DEFAULT, robot: str = ud.DEFAULT_ROBOT,
                 explainer: "claude_explain.Explainer | None" = None):
        # Claude に聞く係（読み取りだけ。/api/ask）。答えでジョブ・候補・承認は変えない
        self.explainer = explainer if explainer is not None else claude_explain.Explainer.from_env()
        # 解釈の係：本物の Claude（ANTHROPIC_API_KEY があるとき）か、偽のClaude（モック）
        self.interpreter = interpreter if interpreter is not None else claude_interpreter.from_env()
        self.max_sessions = max_sessions
        # 上下の向きの設定ファイルの場所（None なら設定なし＝未確認）と、アプリで使うロボットの名前（照合で比べるだけ）
        self.updown_config = DEFAULT_UPDOWN_CONFIG if updown_config is USE_DEFAULT else updown_config
        self.robot = robot
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    # ---- 読込 ----
    def load(self, data: bytes, previous: object = None) -> dict:
        """読み込む。previous（前に見ていた画面の番号）があり、中身（//NAME と ///DATE を除いた指紋）が同じなら、
        人が教えた意味を引き継ぐ（E2-1）。違えば引き継がない（E2-2）。ファイル名では決めない。"""
        view = api.job_view(data)
        if not view.get("ok"):
            return {"ok": False, "error": view.get("error") or "ジョブを読めません"}
        job = jbi.parse_bytes(data)
        if not job.name:
            return {"ok": False, "error": "ジョブ名の行（//NAME）が読めないため、読み込みません"}
        entry = _Entry(session=None, loaded_sha256=hashlib.sha256(data).hexdigest())  # type: ignore[arg-type]
        entry.session = Session(job, simulate=entry.simulate,
                                updown_check=ud.checker(self.updown_config, self.robot))
        entry.session.log.append((self._who(), self.interpreter.notice))
        entry.talk = talk.TalkMemory(content_hash=job.normalized_hash())
        with self._lock:
            prev = self._entries.get(previous) if isinstance(previous, str) else None
        if prev is not None:
            with prev.lock:
                if prev.talk is not None and prev.talk.content_hash == entry.talk.content_hash:
                    entry.talk.meanings = list(prev.talk.meanings)
                    entry.talk.recheck(job)  # 同じ中身でも、対象の区間が見つかるかを確かめ直す
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
            if e.session.repeat_range is not None and rc.is_range_confirm_word(text):
                # 範囲の確認の最中の「この範囲を繰り返す」「はい」など：確定せず、範囲の確認を残して案内する（QA P1-A）。
                # 承認・候補・会話の状態は変えない（候補が無い間だけ範囲の確認がある）
                r = e.session.hear(text, source)
                return {"ok": True, "heard": text, "source": source, "facts": None, "tool_calls": [],
                        "dialog": False, **self._reply(e, r)}
            if ut.is_explain_choice(text):
                # ［説明です］（「〜します」の2択。CHECKER_026 M3）：何もしない。承認・候補・会話の状態も変えない
                r = e.session.explain_only(text, source)
                return {"ok": True, "heard": text, "source": source, "facts": None, "tool_calls": [],
                        "dialog": False, "no_change": True, **self._reply(e, r)}
            cancelled = self._begin_instruction(e)
            out = self._hear_body(e, text, source)
            # 新しい指示（どんな発話でも）で承認を実際に取り消したときは、返事の頭に決まった文（唐川さんの決定②）
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def _hear_body(self, e: _Entry, text: str, source: str) -> dict:
        """指示の本体（会話の続き・会話の始め・ふつうの指示）。承認の取り消しの文は呼んだ側（hear）が付ける。"""
        f0 = ut.read_utterance(text)
        talking = e.dialog.step is not None
        if rc.is_repeat_request(text):
            # 回数の変更（docs/REPEAT_COUNT_DESIGN.md）：会話にしない。会話の途中なら会話をやめて、ふつうの指示として読む
            if talking:
                e.dialog.end()
            out = None
        else:
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

    def _instruction(self, e: _Entry, text: str, source: str) -> dict:
        """ふつうの指示：Session.hear → 解釈の係 → select / propose / compare / undo。"""
        s = e.session
        r = s.hear(text, source=source)
        calls: list[dict] = []
        if r.candidate_made:
            self._remember(e, r)  # 回数の変更：プログラムが候補まで作った。解釈の係（Claude）は呼ばない
        elif r.status == "ok":
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
                or getattr(f0, "shimasu_text", None) is not None
                or getattr(f0, "place_as_destination", False)
                or f0.positions or f0.bad_positions or len(ops) > 1 or f0.op in ("speed", "compare", "undo")
                or any(r != ut.FRONT_BACK_MESSAGE for r in f0.stop_reasons)):
            return None  # 今の流れのまま（打ち消し・繰り返し・疑問の形・「〜します」・行き先の場所の言葉・承認の言葉 など）
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
            elif not dg.handled_by_dialogue(fs, s.updown):
                # 会話が受け持たない理由（D1 の2つの読み方・知らない言葉・符号 など）：その答えをそのまま返す。
                # 選択肢の文には場所の言葉が無く、別の位置に当たるおそれがあるので、選択肢は出さない
                refused = ut.pre_check(fs, None, s.updown)
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
            return self._dialog_out(e, text, source, f0, "stop", s.pending_message, who="アプリ")
        fs = ut.read_utterance(dg.substitute_opening(text, word))
        if fs.unknown_words:  # B1：知らない言葉を捨てて読まない
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(fs.unknown_words, "move", s.updown),
                                    who="アプリ")
        slots = dg.read_slots(fs, opening=True, updown=s.updown)
        if slots.problem:
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        d.estimated = None  # 新しい会話を始めた：前の推定は使わない
        d.amount, d.axis, d.sign = slots.amount, slots.axis, slots.sign
        d.vertical = slots.vertical
        if rel:  # 向きの言葉だけ：方向は必ず6つのボタンで聞く（始めの発話からは決めない）
            d.axis = d.sign = None
            d.vertical = False
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
            v = ut.pre_check(f0, None, e.session.updown)
            if v.status == "stop":
                return self._dialog_out(e, text, source, f0, "stop", v.message)
            return self._dialog_out(e, text, source, f0, "ask", dg.PICK_PROMPT)
        v = ut.pre_check(f0, None, e.session.updown)
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
                if (f0.negations or f0.undo_negations) and any(c["id"] == "other" for c in d.choices()):
                    # 打ち消し（「やっぱり違う」）：「ほかの位置」のボタンが見えているので、次にすることを案内する（CHECKER_026 S-pick）
                    return self._dialog_out(e, text, source, f0, "stop", dg.PICK_AFTER_NEGATION, who="アプリ")
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
        if getattr(f0, "shimasu_text", None) is not None and not f0.question:
            # 「〜します」：ふつうの指示と同じく、選んでいる位置で外した文を確かめる（検査・上限まで通るときだけ2択）。
            # ［変更したい］の文には、その位置番号を入れる（あとで別の位置を選んでも、その位置に当てない。P2-1）
            v = e.session.check_shimasu(f0, ut.pre_check(f0, e.session.selected, e.session.updown))
        if getattr(f0, "repeat", None) or getattr(f0, "question", False) or getattr(f0, "shimasu_text", None) is not None:
            # 繰り返し・疑問の形・「〜します」は、今の pre_check の答えのまま（量として使わない）
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
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(f0.unknown_words, "move", e.session.updown),
                                    who="アプリ")
        if f0.place_ref:  # 答えの中の場所の言葉：会話をやめて、今の流れ（聞き返し）のまま
            d.end()
            return None
        if f0.kinds and not dg.handled_by_dialogue(f0, e.session.updown):
            # 会話が受け持たない理由（2つの操作・D1 の2つの読み方・上下の向きが未確認 など）：今の pre_check の答えをそのまま返す
            v = ut.pre_check(f0, None, e.session.updown)
            # 選択肢（D1 など）の文には、選んでいる位置を入れる（あとで別の位置を選んでも、その位置に当てない）
            return self._dialog_out(e, text, source, f0, v.status, v.message, choices=e.session.pin_choices(v.choices))
        slots = dg.read_slots(f0, updown=e.session.updown)
        if slots.problem:
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        if slots.amount is None and slots.axis is None and slots.sign is None:
            return self._dialog_out(e, text, source, f0, "ask", dg.NOTHING_READ + d.ask_move_message())
        return self._merge_slots(e, text, source, f0, slots.amount, slots.axis, slots.sign, slots.vertical)

    def _merge_slots(self, e: _Entry, text, source, f0, amount, axis, sign, vertical: bool = False) -> dict:
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
            d.vertical = False
            d.turn += 1
            return self._dialog_out(e, text, source, f0, "ask", problem)
        if axis is not None:
            # 軸を「上に・下げて」から読んだか（軸の文字・方向のボタンなら False）。前の答えと同じ軸なら、どちらかの印を残す
            d.vertical = vertical or (d.vertical and d.axis == axis)
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
        text = dg.canonical_text(d.position, d.axis, d.sign, d.amount, vertical=d.vertical)
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
                e.session.forget_range()  # ほかのボタンを押した：回数の変更の範囲の確認も捨てる（止める側）
                e.session.log.append(("止めた理由", dg.STALE))
                return {"ok": True, "status": "stop", "message": dg.STALE, "blocked_reason": None,
                        "candidate_made": False, "choices": [], "dialog": True, "tool_calls": []}
            cancelled = self._begin_instruction(e)
            label = ids[choice]
            d.say("人", label, "human")
            e.session.log.append(("選択", f"ボタン：{label}"))
            if choice.startswith("pos:"):
                out = self._confirm_position(e, label, "text", None, choice[4:])
            elif choice == "go":  # 手で選んだ位置で、方向と量がそろったことの確かめ
                out = self._run_canonical(e, "text")
            elif choice == "other":
                d.step, d.candidates = "pick", []
                d.turn += 1
                out = self._dialog_out(e, label, "text", None, "ask", dg.PICK_PROMPT, who="アプリ")
            else:
                direction = choice[4:]
                out = self._merge_slots(e, label, "text", None, None, direction[0], 1 if direction[1] == "+" else -1)
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def repeat_choose(self, sid: object, choice: object, turn: object) -> dict:
        """回数の変更の範囲の確認のボタン（［この範囲を繰り返す］［違う］）。画面のボタンからだけ呼ぶ。
        今の範囲の確認の番号（turn）と合わなければ使わない。承認・出力は呼ばない（承認は今どおり確認表示のボタン）。"""
        def run(e: _Entry) -> dict:
            r = e.session.confirm_repeat(turn, choice)
            if r.candidate_made:
                self._remember(e, r)
            return {"ok": True, "tool_calls": [], **self._reply(e, r)}
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
            s = e.session
            name = ut.normalize_position(position) if isinstance(position, str) else None
            if name is None or name not in s.original.positions:
                # 形の違う位置・ジョブに無い位置：何も変えない（承認も取り消さない。選び直しの文も出さない）
                return {"ok": True, **self._reply(e, s.select(position if isinstance(position, str) else ""))}
            cancelled = self._cancel_approval(e, "位置を選び直したため")
            out = self._select_body(e, name)
            # 承認を実際に取り消したときは、候補が残ったか（同じ位置）・捨てたか（別の位置）で文を決める。声の「C00004を選択して」と同じ
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def _select_body(self, e: _Entry, position: object) -> dict:
        """位置の選び直しの本体。承認の取り消しの文は呼んだ側（select）が付ける。"""
        r = e.session.select(position if isinstance(position, str) else "")
        d = e.dialog
        if r.status == "ok":
            d.estimated = None  # 人が選び直した：推定の名前は使わない
            if d.step == "pick":  # 「ほかの位置」のあとの手で選ぶ段：選んだ位置で続ける
                d.say("人", f"{e.session.selected} を選びました", "human")
                return self._dialog_pick(e)
            if d.step is not None:
                # 位置の候補の確かめ・方向と量を聞いている段での選び直し：会話をやめる。持っていた量・方向を
                # 別の位置に当てない（CHECKER_028 A）
                d.say("人", f"{e.session.selected} を選びました", "human")
                d.end()
                d.amount = d.axis = d.sign = None
                d.vertical = False
                r = Reply("ok", dg.ENDED_RESELECT + r.message)
        return {"ok": True, **self._reply(e, r)}

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
            was_approved = self._approved(e)
            self._cancel_approval(e, "元に戻したため")
            if e.dialog.step is not None:
                e.dialog.end()
            e.dialog.estimated = None
            r = e.session.undo()
            if was_approved and e.session.candidate is None:
                # 承認のあとの「元に戻す」ボタンも、声の「元に戻して」と同じ決まった文（唐川さんの決定・内部 QA N-2）
                r = Reply(r.status, APPROVAL_AND_CANDIDATE_CANCELLED, facts=getattr(r, "facts", None))
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
            if ch.kind == "repeat":
                return self._approve_repeat(e, ch, approver)
            if ch.kind != "shift":
                return self._fail(e, "シミュレーションの出力は位置の移動と回数の変更だけです。この候補は承認できません")
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
            st = s.updown_status()  # 設定を読み直す。候補を作ったときと違えば断る
            if s.candidate_updown is None or s.candidate_updown.token != st.token:
                return self._fail(e, UPDOWN_CHANGED_APPROVE)
            try:
                ap = sim.approve(sp, approver, updown=st)
            except sim.SimError as ex:
                return self._fail(e, str(ex))
            e.sim_proposal, e.approval, e.approved_candidate = sp, ap, ch
            e.approval_updown = st
            e.updown_cancelled = False
            e.exported = None
            s.log.append(("承認", f"{ap.approved_by} さんが承認ボタンで承認しました（{ap.approved_at}）。{_updown_note(st)}"))
            return {"ok": True, "state": None}
        return self._run(sid, run)

    def _approve_repeat(self, e: _Entry, ch, approver: str) -> dict:
        """回数の変更の承認（ボタンから）。元のジョブから同じ候補を作り直し、バイト列が同じときだけ承認する。"""
        s = e.session
        try:
            sp = sim.simulate_repeat(s.original, ch.start, ch.end, ch.after_count)
        except sim.SimError as ex:
            return self._fail(e, f"承認できません：{ex}")
        if sp.original_bytes != s.original_bytes or sp.new_bytes != ch.new_job.render_bytes():
            return self._fail(e, "変更候補と、出力する中身が合わないため、承認できません")
        st = s.updown_status()  # 位置の移動と同じく、候補を作ったときの照合の結果と比べる（ガードを変えない）
        if s.candidate_updown is None or s.candidate_updown.token != st.token:
            return self._fail(e, UPDOWN_CHANGED_APPROVE)
        try:
            ap = sim.approve(sp, approver, updown=st)
        except sim.SimError as ex:
            return self._fail(e, str(ex))
        e.sim_proposal, e.approval, e.approved_candidate = sp, ap, ch
        e.approval_updown = st
        e.updown_cancelled = False
        e.exported = None
        s.log.append(("承認", f"{ap.approved_by} さんが承認ボタンで承認しました（{ap.approved_at}）。{_updown_note(st)}"))
        return {"ok": True, "state": None}

    # ---- シミュレーション用の出力（合成ジョブだけ） ----
    def export(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            if not self._approved(e):
                # 上下の向きの設定が変わって承認を消した（間に状態の要求があっても）なら、その理由を出す
                return self._fail(e, UPDOWN_CHANGED_EXPORT if e.updown_cancelled else NOT_APPROVED)
            if not self._updown_unchanged(e):  # 設定を読み直す。変わっていれば承認を消して断る
                self._cancel_approval(e, UPDOWN_CHANGED_WHY)
                return self._fail(e, UPDOWN_CHANGED_EXPORT)
            try:
                path = sim.export_sim(e.sim_proposal, e.approval)
            except sim.SimError as ex:
                return self._fail(e, f"出力しませんでした：{ex}")
            full = str(Path(path).resolve())
            e.exported = full
            e.session.log.append(("出力", f"シミュレーション用のジョブを出力しました：{full}（実機には入れないでください）。"
                                          f"{_updown_note(e.approval_updown)}"))
            return {"ok": True, "path": full, "file_name": Path(path).name}
        return self._run(sid, run)

    # ---- Claude に聞く（読み取りだけ。docs/CLAUDE_API.md 8章・CHECKER_031 E1〜E7） ----
    def ask(self, sid: object, text: object = None, reset: object = False, info: object = False,
            source: object = "text", choose: object = None, token: object = None, sync: object = False) -> dict:
        """読み込んだジョブについて Claude に聞く。Session（候補・承認・選択・作業ログ）には触らない。
        会話の文脈と人が教えた意味は、この画面の中だけ（保存しない）。データ（ジョブ・候補・承認・上下の向きの照合）が
        変わったら、会話を区切る（新しい会話にして「データが変わったので…」と知らせる）。

        - sync：データの変化を確かめ、人が教えた意味と知らせだけを返す（Claude を呼ばない）
        - choose・token：「どの動きのことですか？」のボタン（Claude を呼ばない。番号が今のものと合うときだけ）
        - source：voice か text（どちらも同じ検査を通る。記録して返すだけ）
        """
        with self._lock:
            e = self._entries.get(sid) if isinstance(sid, str) else None
        if e is None:
            return {"ok": False, "error": NO_SESSION}
        if info is True:
            with e.lock:
                notices = self._talk_sync(e)
                tstate = self._talk_state(e, notices)
            return {"ok": True, "status": "info", **self.explainer.info(), "talk": tstate}
        if source not in SOURCES:
            return {"ok": False, "error": "聞く文の種類（voice か text）が分かりません"}
        notice = None
        with e.lock:  # 状態は読むだけ。Claude を呼ぶ間はこの錠を持たない（ほかの操作を止めない）
            s = e.session
            notices = self._talk_sync(e)
            if sync is True:
                return {"ok": True, "status": "sync", "talk": self._talk_state(e, notices)}
            if choose is not None:
                o = e.talk.choose(choose, token, s.original, self._current_job(e))
                return self._talk_reply(e, o, notices, source)
            st = s.updown_status()
            key = (hashlib.sha256(s.original_bytes).hexdigest(), st.token)
            if reset is True or e.explain is None or e.explain.key != key:
                if e.explain is not None and e.explain.turns and reset is not True:
                    notice = "ジョブか上下の向きの設定が変わったため、新しい会話にしました"
                e.explain = claude_explain.Conversation.start(s.original_bytes, s.original, st)
                if e.explain.facts is not None:
                    e.explain.facts["change_state"] = self._change_state(e)
                e.talk.last_evidence = ()
            if reset is True:
                e.talk.clear()  # 会話をやり直す：人が教えた意味も、この会話の中だけなので消す
                return {"ok": True, "status": "reset", **self.explainer.info(),
                        "talk": self._talk_state(e, notices)}
            conv = e.explain
            if not isinstance(text, str) or not text.strip() or len(text) > claude_explain.MAX_TEXT:
                return {**self.explainer.ask(conv, text), "talk": self._talk_state(e, notices)}
            text = text.strip()
            pending = s.candidate is not None
            if pending and talk.looks_like_change(text):
                # 承認待ちの候補がある：Claude に変更の希望を受けさせない（呼ばない）。説明だけ
                return self._app_reply(e, talk.PENDING_EXPLAIN_ONLY, notices, source, "explain_only", text)
            blocks = talk.repeat_blocks(s.original)
            count = talk.read_count(text, blocks, e.talk.last_evidence)
            if count is not None and count.stop:
                # 回数の言葉に打ち消し・ためらい（「3回にしないで」「そう、じゃなくて4回」「たぶん3回」）：読まない・呼ばない
                return self._app_reply(e, count.stop, notices, source, "count_refused", text)
            o = e.talk.consider(text, s.original, self._current_job(e))
            if o.kind in ("ask", "refused"):
                return self._talk_reply(e, o, notices, source, said=text)
            taught_note = o.message if o.kind in ("taught", "replaced") else None
            taught = e.talk.facts_for_claude(s.original)
            version = e.talk.signature
        out = self.explainer.ask(conv, text, taught=taught, allow_change=not pending, count=count)
        with e.lock:
            if out.get("ok") and e.explain is conv and e.talk.signature is version:
                e.talk.last_evidence = tuple(ev["line"] for ev in out.get("evidence", []))
            hl, sup = talk.highlight_and_supplement(conv.job, out.get("evidence", []) if out.get("ok") else [])
            out.update(highlight=hl, supplement=sup, source=source, heard=text,
                       taught_note=taught_note, talk=self._talk_state(e, notices))
        if notice:
            out["conversation_notice"] = notice
        return out

    def _current_job(self, e: _Entry) -> jbi.Job:
        """今のデータ：承認待ち・承認済みの候補があれば、その変更後のジョブ。無ければ読み込んだジョブ。"""
        ch = e.session.candidate
        return ch.new_job if ch is not None and getattr(ch, "new_job", None) is not None else e.session.original

    def _change_state(self, e: _Entry) -> dict:
        ch = e.session.candidate
        kinds = {"shift": "位置の移動", "repeat": "回数の変更", "speed": "速度の変更"}
        return {"pending_candidate": ch is not None and not self._approved(e), "approved": self._approved(e),
                "kind": kinds.get(getattr(ch, "kind", None)) if ch is not None else None,
                "note": "説明は、読み込んだ元の動作データについて。変更はアプリのプログラムと承認ボタンで行う"}

    def _talk_sync(self, e: _Entry) -> list[str]:
        """データ（ジョブの中身・候補・承認）が変わったか。変わったら、意味を確かめ直し、会話を区切る。知らせの文を返す。"""
        s = e.session
        sig = (e.talk.content_hash, s.candidate, e.approval)
        if e.talk.sync(sig, self._current_job(e)):
            if e.explain is not None and e.explain.turns:
                e.talk.notices.insert(0, talk.DATA_CHANGED)
            e.explain = None  # 会話を区切る：次に聞くときは新しい事実（変更候補の状態も）で始める
        return e.talk.take_notices()

    def _talk_state(self, e: _Entry, notices: list[str]) -> dict:
        return {**e.talk.state(self._current_job(e)), "notices": list(notices)}

    def _app_reply(self, e: _Entry, message: str, notices: list[str], source: str, status: str,
                   heard: str | None = None) -> dict:
        """アプリの決まった文で答える（Claude は呼ばない）。"""
        return {"ok": True, "status": status, "reply": message, "reply_by": "app", "checked": None, "notes": [],
                "evidence": [], "intent": None, "change_request": None, "change_record": None, "guesses": [],
                "highlight": {"lines": [], "positions": []}, "supplement": [], "source": source, "heard": heard,
                "taught_note": None, "talk": self._talk_state(e, notices)}

    def _talk_reply(self, e: _Entry, o: "talk.Outcome", notices: list[str], source: str,
                    said: str | None = None) -> dict:
        status = {"ask": "ask_which", "refused": "not_taught", "taught": "taught", "replaced": "taught"}[o.kind]
        out = self._app_reply(e, o.message or "", notices, source, status, said)
        if o.kind in ("taught", "replaced"):  # ボタンで選んで登録した：アプリの知らせだけ（Claude は呼ばない）
            out["taught_note"], out["reply"] = o.message, ""
        if o.block is not None:  # 確かめている区間を光らせる（行番号からプログラムが決める）
            out["highlight"], out["supplement"] = talk.highlight_and_supplement(
                e.session.original, [{"line": o.block.first_line}])
        return out

    # ---- 内部 ----
    def _run(self, sid: object, fn) -> dict:
        with self._lock:
            e = self._entries.get(sid) if isinstance(sid, str) else None
            if e is not None:
                self._entries.move_to_end(sid)
        if e is None:
            return {"ok": False, "error": NO_SESSION}
        with e.lock:
            st = e.session.updown_status()  # 要求のたびに、上下の向きの設定を読み直す
            e.dialog.direction_names = st.direction_names
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

    def _after_approval(self, e: _Entry) -> dict:
        """承認のあとに並べて出す状態（プログラムの決まった文だけ。表示だけで、承認・出力の条件は変えない）。

        「元の動作データ：変わっていません」は、元のジョブを書き戻したバイト列と、Session が最初に取った写しが、
        どちらも読み込んだときのバイト列（の sha256）と同じときだけ出す。確かめられなければ行を出さずに理由（note）を出す。
        """
        s = e.session
        same = (e.loaded_sha256 is not None
                and hashlib.sha256(s.original.render_bytes()).hexdigest() == e.loaded_sha256
                and hashlib.sha256(s.original_bytes).hexdigest() == e.loaded_sha256)
        rows = ([{"label": "元の動作データ", "value": ORIGINAL_UNCHANGED}] if same else []) + [
            {"label": "変更候補", "value": CANDIDATE_APPROVED}, {"label": "ロボットへの送信", "value": NOT_SENT}]
        exported = EXPORTED_LINE.format(name=Path(e.exported).name) if e.exported else None
        return {"rows": rows, "note": None if same else ORIGINAL_UNKNOWN_NOTE, "exported": exported}

    def _approved(self, e: _Entry) -> bool:
        return (e.approval is not None and e.sim_proposal is not None
                and e.session.candidate is not None and e.approved_candidate is e.session.candidate)

    def _updown_unchanged(self, e: _Entry) -> bool:
        """承認したときの上下の向きの照合の結果と、今（読み直した）結果が同じか。"""
        return e.approval_updown is not None and e.approval_updown.token == e.session.updown_status().token

    def _begin_instruction(self, e: _Entry) -> bool:
        """新しい指示の始め：承認を今どおり取り消す。承認していたときは、承認待ちの文を決まった文にする（S-msg）。
        承認を実際に取り消したら True（返事の頭に決まった文を付けるため。_say_cancelled）。"""
        e.session.pending_message = APPROVED_PENDING_MESSAGE if self._approved(e) else PENDING_MESSAGE
        e.session.forget_range()  # 回数の変更の範囲の確認も捨てる（古いボタンを押しても候補にならない）
        return self._cancel_approval(e, "新しい指示があったため")

    def _say_cancelled(self, e: _Entry, out: dict) -> dict:
        """承認を実際に取り消した返事の頭に、文を付ける（唐川さんの決定②。承認していなければ呼ばない）。
        変更候補が残っていれば決まった文（APPROVED_PENDING_MESSAGE）、候補も捨てていれば APPROVAL_AND_CANDIDATE_CANCELLED。
        候補を捨てたときは、同じことを言う返事の文（「変更候補を捨てました。元のジョブは変わっていません」など）を重ねない。
        会話の欄の最後の文が同じ返事なら、そちらにも付ける。"""
        kept = e.session.candidate is not None
        head = APPROVED_PENDING_MESSAGE if kept else APPROVAL_AND_CANDIDATE_CANCELLED
        msg = out.get("message") or ""
        if msg.startswith(head):
            return out
        new = msg
        if not kept:
            for phrase in _DROPPED_PHRASES:
                new = new.replace(phrase, "")
        out["message"] = head + new
        turns = e.dialog.turns
        if out.get("dialog") and turns and turns[-1].get("text") == msg and turns[-1].get("who") != "人":
            turns[-1] = {**turns[-1], "text": head + new}
        return out

    def _cancel_approval(self, e: _Entry, why: str) -> bool:
        """承認を取り消す。実際に取り消したら True。"""
        if why != UPDOWN_CHANGED_WHY:
            e.updown_cancelled = False  # 新しい指示・選び直し・元に戻す：上下の文はもう出さない
        if e.approval is None:
            return False
        e.session.forget_question()  # 承認の取り消し：未確定の聞き返しも捨てる（CHECKER_028 A）
        e.updown_cancelled = why == UPDOWN_CHANGED_WHY
        e.approval = e.sim_proposal = e.approved_candidate = e.approval_updown = None
        e.session.log.append(("承認", f"{why}、承認を取り消しました。もう一度確かめて、承認ボタンで承認してください"))
        return True

    def _sync(self, e: _Entry) -> None:
        if e.approval is not None and not self._approved(e):
            self._cancel_approval(e, "変更候補が変わったため")
        if e.approval is not None and not self._updown_unchanged(e):
            self._cancel_approval(e, UPDOWN_CHANGED_WHY)

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
        st = s.updown_status()
        e.dialog.direction_names = st.direction_names
        same_updown = s.candidate_updown is not None and s.candidate_updown.token == st.token
        if cand is not None:
            # 候補の向きの名前（「（下へ）」）と、確認表示の「上下の向き：〇〇さんが確認」の1行は、候補を作ったときの照合の結果から。
            # 今の照合の結果と違えば（設定が変わった）名前は付けない（承認もできない）
            cu = s.candidate_updown
            cand["direction_names"] = cu.direction_names if (cu is not None and same_updown) else {}
            cand["updown"] = cu.as_state() if (cu is not None and same_updown) else None
        return {
            "session_id": sid,
            "mode": api.MODE,
            "job_name": s.original.name,
            "selected": s.selected,
            "facts": _facts(s.facts),
            # 確認済みの方向の名前だけ（上下の向きの照合に通ったときだけ Z の 上・下。右・前 などは入れない）
            "direction_names": st.direction_names, "direction_names_source": st.direction_names_source,
            # 上下の向きの照合の結果（CHECKER_026 M2）。画面の表示は別の担当
            # app_robot：アプリの --robot の値（どの状態でも。画面にロボット名を常に出すため。CHECKER_030 S30-1）
            "updown": {**st.as_state(), "app_robot": self.robot},
            "candidate": cand,
            "confirmation_text": s.confirmation_text(),
            "blocked_reason": s.candidate_blocked_reason if ch is not None else None,
            "approval": ({"approved_by": e.approval.approved_by, "approved_at": e.approval.approved_at,
                          "updown_status": e.approval.updown_status,
                          "updown_fingerprint": e.approval.updown_fingerprint,
                          "updown_confirmed_by": e.approval.updown_confirmed_by}
                         if approved else None),
            "can_approve": (ch is not None and ch.kind in ("shift", "repeat") and not approved
                            and not s.candidate_blocked_reason and same_updown),
            # 出力できるのは合成ジョブ（samples/dummy と同じバイト列）だけ。実機の JOB は承認しても出力ボタンを押せない（画面で明示する）
            "exportable": sim._is_synthetic(s.original_bytes),
            "can_export": approved and sim._is_synthetic(s.original_bytes),
            "exported": e.exported if approved else None,
            # 承認のあとの3つの状態（台本 Ver.6.1 Scene 3-3）。表示だけ。承認していなければ None
            "after_approval": self._after_approval(e) if approved else None,
            "out_dir": str(Path(sim.DEFAULT_OUT_DIR).resolve()),
            "mock_notice": self.interpreter.notice,
            "interpreter": {"kind": self.interpreter.kind, "model": self.interpreter.model,
                            "notice": self.interpreter.notice},
            "log": _log_entries(s.log),
            "dialog": self._dialog_state(e),
            # 回数の変更の範囲の確認（ボタンを待っている間だけ。行番号・命令の行・回数はプログラムが入れたもの）
            "repeat_range": _range_state(s.repeat_range),
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


def _range_state(rr: dict | None) -> dict | None:
    if rr is None:
        return None
    keep = ("turn", "target", "total", "count", "positions", "cycle_lines", "line_nos", "block_line_nos",
            "yes_label", "no_label", "note", "side_note", "heading", "cycles", "button_note")
    return {k: rr[k] for k in keep}


def _updown_note(st: "ud.UpdownStatus | None") -> str:
    """承認・出力の作業ログに残す、上下の向きの設定の事実（状態・確認した人・指紋）。"""
    if st is None:
        return "上下の向きの設定：記録なし"
    if st.confirmed:
        return f"上下の向きの設定：確認済み（確認した人 {st.confirmed_by}・{st.confirmed_at}・指紋 {st.fingerprint}）"
    return f"上下の向きの設定：{_UPDOWN_STATUS_NAMES.get(st.status, st.status)}（上・下は Z として読んでいません）"


def _xyz(job: jbi.Job, name: str) -> list[str] | None:
    pos = job.positions.get(name)
    if pos is None or len(pos.values) < 3 or not pos.is_robot_coordinates():
        return None  # ロボット座標（直交）でなければ X・Y・Z として出さない
    return [f"{v:.3f}" for v in pos.xyz]


def _with_cycles(segments: list[dict], block: "jbi.RepeatBlock") -> list[dict]:
    """図の区間の線に、何回目の区間の線か（cycle。区間の外は None）を付ける。再生で「2回目」と数えるため。"""
    return [{**seg, "cycle": block.cycle_of(seg["line_no"] - 1)} for seg in segments]


def _beside_block(job: jbi.Job, block: "jbi.RepeatBlock") -> tuple[dict | None, dict | None]:
    """区間のすぐ前の移動と、すぐ後の移動（行き先の位置・行番号・命令の行。ジョブの行のまま）。無ければ None。"""
    moves = [i for i in job.instructions if api._is_move(i) and i.position]
    pick = lambda i: {"position": i.position, "line_no": i.index + 1, "text": i.text}  # noqa: E731
    entry = next((pick(i) for i in reversed(moves) if i.index < block.first_line), None)
    exit_ = next((pick(i) for i in moves if i.index >= block.end_line), None)
    return entry, exit_


def _repeat_sequence(original: jbi.Job, ch: "jbi.RepeatProposal") -> dict:
    """動きの並びのカード（［C00000（区間の前）］→［①］［②］［③ 追加］→［C00004（区間の後）］）と差分の要約の材料。
    すべて変更前後のジョブと区間（RepeatBlock）から作る。位置の意味（待機など）は決めつけない。
    name は区間の名前（今は target と同じ。人が教えた意味の名前を、あとで差し込めるように別に持つ）。"""
    def side(job: jbi.Job, block: "jbi.RepeatBlock") -> dict:
        entry, exit_ = _beside_block(job, block)
        return {"cycles": list(range(1, block.count + 1)), "entry": entry, "exit": exit_}
    by_line = {i.index: i for i in ch.new_job.instructions}
    added_ins = [by_line.get(n) for n, _ in ch.added]
    if any(i is None for i in added_ins):
        added_ins = []  # 足した行が命令として読めない（ありえないはず）：要約は出さない
    parts = []
    if added_ins:
        only_movl = all(i.command == "MOVL" for i in added_ins)
        parts.append(f"足した行：{len(added_ins)}行" + ("（直線移動だけ）" if only_movl else ""))
        if set(ch.new_job.positions) == set(original.positions):
            parts.append("新しい位置はありません")
    return {
        "target": ch.target, "name": ch.target,
        "before": side(original, ch.block), "after": side(ch.new_job, ch.new_block),
        "added": list(range(ch.before_count + 1, ch.after_count + 1)),
        "summary": "。".join(parts),
    }


def _comparison_dict(c: Comparison, ch: jbi.ChangeProposal, s) -> dict:
    if ch.kind == "repeat":
        after, before = api.figure_data(ch.new_job), api.figure_data(s.original)
        return {
            "kind": "repeat", "target": c.target, "line_no": c.line_number, "heading": c.heading,
            "before_line": "", "after_line": "",
            "before_value": c.before_value, "after_value": c.after_value, "delta": c.delta,
            "diff": [{"sign": sign, "line_no": n, "text": t} for sign, n, t in c.diff],
            "affected": list(c.affected), "warnings": list(c.warnings), "blocked_reason": c.blocked_reason,
            "heard": s.candidate_utterance,
            "repeat": {"positions": list(ch.block.positions), "before_count": ch.before_count,
                       "after_count": ch.after_count},
            # 図：変更前と変更後の区間の線（回の番号つき）。位置は変えないので、変更後の位置は変更前と同じ
            "before_segments": _with_cycles(before["segments"], ch.block),
            "after_segments": _with_cycles(after["segments"], ch.new_block),
            "after_positions": after["positions"], "moved": None, "before_xyz": None, "after_xyz": None,
            "sequence": _repeat_sequence(s.original, ch),  # 動きの並びのカード・差分の要約（feat/demo-3d）
        }
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
        # 軸の文字で言った軸・軸の値の増減で言った軸・上下の言葉から補った Z（区別して持つ。CHECKER_026 再修正）
        "explicit_axes": list(f.explicit_axes), "value_axes": list(f.value_axes), "vertical": f.vertical,
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
