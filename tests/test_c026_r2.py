"""受入テスト：CHECKER_026 再修正（内部 Safety レビュー FAIL・QA レビュー FAIL の指摘。fix/c026-r2）。実装より先に書いた。

P0-1：上下が未確認・不一致・変更済みでも、2回に分けて話すと「下げて」→「3ミリ」が Z− になり、承認と出力まで通った。
  直すこと（多重の止め）
  1. merge_answer で vertical（上・下から Z を補った印）を引き継ぐ。ほかの項目も落とさない
  2. 上下の案内で聞き返したあとは、次の発話を答えとして前の発話に足さない
  3. 最後の止め：check_proposal は、上下が確認済みでないときの Z の提案を、発話の中で Z を軸の文字として
     はっきり言ったとき（explicit_axes）だけ通す
P1-1・QA P1-2：未確認・不一致・変更済みのときに、上下の言葉を例に出す案内を出さない（確認済みのときだけ上下の例）。
P2-1・P2-2・QA P1-1：「〜します」の2択は、外した文がふつうの指示として通るとき（検査・上限・0mm・位置も含む）だけ。
  それ以外は外した文の答えをそのまま返す。［変更したい］の文はプログラムが読んだ facts から作り、位置番号を入れる。
P2-3：承認を消した理由が上下の設定の変更なら、出力のときもその文を出す。

設定は一時フォルダの仮の設定だけを使う（conftest.make_updown。実機で確かめたものではない）。
"""
import dataclasses
import json
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import jbi, updown
from voice_teaching import session as session_mod
from voice_teaching import utterance as ut
from voice_teaching.session import Session
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C_PATH = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
DEMO_C = DEMO_C_PATH.read_bytes()
DEMO = (ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()
NL = b"\r\n" if b"\r\n" in DEMO_C else b"\n"
WITH_USER = DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 0" + NL + b"///USER 1" + NL)
TOOL_1 = DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 1" + NL)
# 相対ジョブ（RJ・FRAME ROBOT）にした DEMO_C（ツール番号は DEMO_RJ_XYZ と同じ 8）。FRAME USER 1 にしたもの
RJ_ROBOT = (DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 8" + NL)
            .replace(b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL + b"////FRAME ROBOT" + NL))
FRAME_USER1 = DEMO_C.replace(b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL + b"////FRAME USER 1" + NL)
assert TOOL_1 != DEMO_C and RJ_ROBOT.count(b"////FRAME ROBOT") == 1 and FRAME_USER1 != DEMO_C
SOURCES = ["voice", "text"]
LABELS = ["変更したい", "説明です"]
TAIL = "それとも操作の説明でしょうか？"
UPDOWN_MSGS = (updown.MSG_UNCONFIRMED, updown.MSG_MISMATCH, updown.MSG_CHANGED)


def updown_said(expected: str, message: str, exact: bool = False) -> bool:
    """会話の聞き返しが、状態ごとの案内になっているか。未確認のときは、会話の文（ut.updown_ask_message）の形。
    11/5 唐川さんの決定：未確認の会話の聞き返しだけを、意図を受け止める文にした。状態の表示の文は MSG_UNCONFIRMED のまま。"""
    if expected == updown.MSG_UNCONFIRMED:
        return ut.is_updown_unset_ask(message)
    return message == expected if exact else expected in message


def _rewrite(path, fn):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    fn(obj)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _config(kind, make_updown, tmp_path):
    """(設定の場所, ジョブ, ロボット名, 案内の文)。"""
    if kind == "missing":  # ファイルなし
        return tmp_path / "none.json", DEMO_C, updown.DEFAULT_ROBOT, updown.MSG_UNCONFIRMED
    path = make_updown()
    if kind == "unread":  # 未確認（形が正しくない設定は読まない：根拠が空）
        _rewrite(path, lambda o: o.__setitem__("evidence", ""))
        return path, DEMO_C, updown.DEFAULT_ROBOT, updown.MSG_UNCONFIRMED
    if kind == "changed":  # 変更済み（指紋が合わない）
        _rewrite(path, lambda o: o.__setitem__("evidence", o["evidence"] + "。"))
        return path, DEMO_C, updown.DEFAULT_ROBOT, updown.MSG_CHANGED
    if kind == "mismatch":  # 不一致（///USER 付き）
        return path, WITH_USER, updown.DEFAULT_ROBOT, updown.MSG_MISMATCH
    if kind == "other_job_same_coordinate":  # 別のジョブでも座標系が同じなら、上下の向きは確認済み（CHECKER_028 K4）
        return path, DEMO, updown.DEFAULT_ROBOT, None
    if kind == "mismatch_tool":  # 不一致（ツール番号が違う）
        return path, TOOL_1, updown.DEFAULT_ROBOT, updown.MSG_MISMATCH
    if kind == "mismatch_frame_user":  # 不一致（FRAME USER 1）
        return path, FRAME_USER1, updown.DEFAULT_ROBOT, updown.MSG_MISMATCH
    if kind == "mismatch_rj":  # 不一致（RJ の有無だけが違う：相対ジョブの設定で、相対でないジョブ）
        rj_path = make_updown(job=RJ_ROBOT, name="updown_rj.json")
        return rj_path, DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 8" + NL), updown.DEFAULT_ROBOT, updown.MSG_MISMATCH
    if kind == "mismatch_robot":  # 不一致（ロボット名が違う）
        return path, DEMO_C, "GP25", updown.MSG_MISMATCH
    if kind == "confirmed":
        return path, DEMO_C, updown.DEFAULT_ROBOT, None
    raise AssertionError(kind)


def start(kind, make_updown, tmp_path, select="C00005"):
    path, data, robot, msg = _config(kind, make_updown, tmp_path)
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=path, robot=robot)
    r = w.load(data)
    assert r["ok"], r
    sid = r["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid, msg


def cand(r):
    c = r["state"]["candidate"]
    return None if c is None else (c["target"], c["delta"])


def labels(r):
    return [c["label"] for c in r["choices"]]


NOT_CONFIRMED = ["missing", "unread", "changed", "mismatch"]
FIRST = ["下げて", "上に", "下方向", "さげて", "ちょっと下げて", "下げてください", "C00004を下げて", "この位置を上に"]
SECOND = ["3ミリ", "3ミリです", "3mmお願いします", "5ミリ"]


# ======================================================================
# P0-1：2回に分けた「下げて」→「3ミリ」
# ======================================================================
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("second", SECOND)
@pytest.mark.parametrize("first", FIRST)
@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p0_two_step_up_down_never_becomes_z(kind, first, second, source, make_updown, tmp_path):
    w, sid, msg = start(kind, make_updown, tmp_path)
    r = w.hear(sid, first, source=source)
    assert r["status"] == "ask" and cand(r) is None, (first, r["message"])
    assert updown_said(msg, r["message"]), r["message"]
    r = w.hear(sid, second, source=source)
    assert r["status"] != "ok" and cand(r) is None and not r["candidate_made"], (first, second, r["message"])
    assert r["state"]["can_approve"] is False
    a = w.approve(sid, "テスト太郎")
    assert a["ok"] is False
    x = w.export(sid)
    assert x["ok"] is False and x["state"]["exported"] is None


@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p0_answer_after_updown_ask_is_read_as_new_instruction(kind, make_updown, tmp_path):
    # 上下の案内のあとは、知らない言葉で聞き返したときと同じ扱い（前の発話に足さない）
    w, sid, msg = start(kind, make_updown, tmp_path)
    w.hear(sid, "下げて")
    r = w.hear(sid, "3ミリ")
    # CHECKER_028 D：つながないのは今どおり。文は、次にどう言えばよいかの案内にした
    assert r["status"] == "ask" and r["message"] == ut.updown_followup_message(
        w._entries[sid].session.updown, "下げて") and cand(r) is None
    assert r["state"]["facts"]["text"] == "3ミリ"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("first", ["Zマイナス", "Zのマイナス", "Zマイナス方向", "Zマイナス方向に", "Z座標を下げて"])
@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p0_explicit_axis_two_step_still_makes_candidate(kind, first, source, make_updown, tmp_path):
    # 軸の文字と向きをはっきり言った指示は、どの状態でも今どおり（2回に分けても）候補にする
    w, sid, _ = start(kind, make_updown, tmp_path)
    r = w.hear(sid, first, source=source)
    assert r["status"] == "ask" and r["message"] == "何mm動かしますか？", r["message"]
    r = w.hear(sid, "3ミリ", source=source)
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "-3.000"), r["message"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("second", SECOND)
@pytest.mark.parametrize("first", ["下げて", "下方向", "さげて", "下げてください"])
def test_p0_confirmed_two_step_down_is_still_z_minus(first, second, source, make_updown, tmp_path):
    # 退行しない：確認済みのときは「下げて」→「3ミリ」で今どおり Z−
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, first, source=source)
    assert r["status"] == "ask" and r["message"] == "何mm動かしますか？"
    r = w.hear(sid, second, source=source)
    v = second[0]
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", f"-{v}.000"), r["message"]


def test_p0_confirmed_up_and_position_two_step(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    w.hear(sid, "この位置を上に")
    assert cand(w.hear(sid, "3ミリ")) == ("C00005 Z", "+3.000")
    w2, sid2, _ = start("confirmed", make_updown, tmp_path)
    w2.hear(sid2, "C00004を下げて")
    r = w2.hear(sid2, "3ミリ")
    # C00004 は設定の対象の位置ではないが、ジョブの照合は structure_hash（C00005 の値だけ伏せる）なので確認済みのまま
    assert cand(r) == ("C00004 Z", "-3.000"), r["message"]


def test_p0_config_removed_between_the_two_steps(make_updown, tmp_path):
    # 「下げて」のときは確認済み、「3ミリ」のときに設定が無くなった：Z にしない
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    assert w.hear(sid, "下げて")["message"] == "何mm動かしますか？"
    Path(w.updown_config).unlink()
    r = w.hear(sid, "3ミリ")
    assert r["status"] == "ask" and ut.is_updown_unset_ask(r["message"]) and cand(r) is None


# ---- Session だけ（設定なし） ----
@pytest.mark.parametrize("first", FIRST)
def test_p0_session_two_step_and_propose(first):
    s = Session.from_file(DEMO_C_PATH)
    s.select("C00005")
    s.hear(first)
    r = s.hear("3ミリ")
    assert r.status != "ok"
    r = s.propose({"kind": "shift", "position": "C00005", "axis": "Z", "delta_mm": -3})
    assert r.status != "ok" and s.candidate is None


def test_p0_merge_answer_keeps_vertical():
    prev = ut.read_utterance("下げて")
    assert prev.vertical and prev.explicit_axes == ()
    m = ut.merge_answer(prev, ut.read_utterance("3ミリ"))
    assert m.vertical is True and m.axes == ("Z",) and m.explicit_axes == ()
    assert ut.pre_check(m).status == "ask" and ut.is_updown_unset_ask(ut.pre_check(m).message)


def _non_default(f: dataclasses.Field):
    t = str(f.type)
    if f.name == "repeat":
        return (2, "ambiguous")
    if f.name == "amounts":
        return (ut.Amount(1.0, "mm", None, "1mm"),)
    if t.startswith("tuple[int"):
        return (1,)
    if t.startswith("tuple"):
        return ("あ",)
    if t == "bool":
        return True
    if t == "int":
        return 1
    return "あ"


def test_p0_merge_answer_drops_no_field():
    # UtteranceFacts のどの項目も、答えと合わせたときに黙って落とさない（新しい項目を足したときも、このテストで分かる）
    names = [f.name for f in dataclasses.fields(ut.UtteranceFacts)]
    prev = ut.UtteranceFacts(raw="前", text="前", **{f.name: _non_default(f) for f in dataclasses.fields(ut.UtteranceFacts)
                                                   if f.name not in ("raw", "text")})
    answer = ut.UtteranceFacts(raw="答え", text="答え")
    m = ut.merge_answer(prev, answer)
    # 答えと合わせたときに、わざと空にする項目（確かめの選択肢の文は作り直さない）。
    # said_signs は「その発話の向きの言葉」なので答えのものにし、前の発話の言葉は earlier_signs に移して残す（QA 002 N1）
    cleared = {"question_text", "shimasu_text", "said_signs"}
    for n in names:
        if n in ("raw", "text") or n in cleared:
            continue
        assert getattr(m, n) == getattr(prev, n), n
    assert m.question_text is None and m.shimasu_text is None
    assert m.said_signs == answer.said_signs and set(prev.said_signs) <= set(m.earlier_signs)


def test_p0_combine_axes_keeps_explicit_axes_and_vertical():
    prev = ut.read_utterance("Y座標を")
    ans = ut.read_utterance("3mm下げて")
    m = ut.combine_axes(prev, ans)
    assert m.vertical and set(m.explicit_axes) == {"Y"} and set(m.axes) == {"Y", "Z"}


def test_p0_explicit_axes_reading():
    def ex(t):
        return ut.read_utterance(t).explicit_axes
    assert ex("Zマイナス3ミリ") == ("Z",)
    assert ex("Zマイナス方向に3mm") == ("Z",)
    assert ex("Z座標を3mm下げて") == ("Z",)
    assert ex("Z-3mm") == ("Z",)
    assert ex("Zマイナス") == ("Z",)
    assert ut.read_utterance("Zマイナス").axis_signs == (-1,)
    assert ex("3ミリ下げて") == ()
    assert ex("下に3mm") == ()
    assert ex("Yを3mm下げて") == ("Y",)


@pytest.mark.parametrize("st_kind", ["none", "mismatch", "changed"])
def test_p0_check_proposal_last_guard(st_kind, updown_ok):
    # 最後の止め：pre_check をすり抜けた（vertical を落とした）事実でも、Z を軸の文字で言っていなければ Z の提案は通さない
    st = {"none": None,
          "mismatch": updown.UpdownStatus(updown.MISMATCH, updown.MSG_MISMATCH),
          "changed": updown.UpdownStatus(updown.CHANGED, updown.MSG_CHANGED)}[st_kind]
    f = dataclasses.replace(ut.read_utterance("3ミリ下げて"), vertical=False)
    assert f.axes == ("Z",) and f.explicit_axes == ()
    p = ut.ProposedChange(kind="shift", position="C00005", axis="Z", delta_mm=-3.0)
    v = ut.check_proposal(f, p, "C00005", st)
    assert v.status != "ok" and v.message in UPDOWN_MSGS
    # 軸の文字で言った Z は通す
    f2 = ut.read_utterance("Zマイナス3ミリ")
    assert ut.check_proposal(f2, p, "C00005", st).status == "ok"
    # 確認済みなら、上・下から読んだ Z も今どおり通す
    f3 = ut.read_utterance("3ミリ下げて")
    assert ut.check_proposal(f3, p, "C00005", updown_ok).status == "ok"


def test_p0_check_proposal_rejects_axis_not_in_utterance(updown_ok):
    # 発話に無い軸の提案は、どの状態でも通さない
    f = dataclasses.replace(ut.read_utterance("Yのマイナス3ミリ"), axes=("X",))
    p = ut.ProposedChange(kind="shift", position="C00005", axis="X", delta_mm=-3.0)
    assert ut.check_proposal(f, p, "C00005", updown_ok).status != "ok"


# ---- M3 の［変更したい］の先 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p0_shimasu_path_never_becomes_z(kind, source, make_updown, tmp_path):
    w, sid, msg = start(kind, make_updown, tmp_path)
    r = w.hear(sid, "下げて動かします", source=source)
    assert r["status"] == "ask" and updown_said(msg, r["message"]) and labels(r) != LABELS and cand(r) is None
    r = w.hear(sid, "3ミリ", source=source)
    assert r["status"] != "ok" and cand(r) is None
    # 前の版の［変更したい］が送っていた文（「C00005を下げて」）のあとでも同じ
    w.hear(sid, "C00005を下げて", source=source)
    r = w.hear(sid, "3ミリ", source=source)
    assert r["status"] != "ok" and cand(r) is None
    r = w.hear(sid, "3mm下げて動かします", source=source)
    assert r["status"] == "ask" and updown_said(msg, r["message"]) and labels(r) != LABELS and cand(r) is None


@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p0_dialog_answer_two_step(kind, make_updown, tmp_path):
    # 会話の中：「下げて」→「3ミリ」も Z にしない
    w, sid, msg = start(kind, make_updown, tmp_path, select="C00001")
    r = w.hear(sid, "置くところ")
    if r["state"]["dialog"]["step"] == "confirm_position":
        r = w.hear(sid, "そうそう")
    else:
        r = w.select(sid, "C00005")
    assert r["state"]["dialog"]["step"] == "ask_move"
    r = w.hear(sid, "下げて")
    assert r["status"] == "ask" and updown_said(msg, r["message"])
    r = w.hear(sid, "3ミリ")
    assert cand(r) is None and r["state"]["dialog"]["direction"] is None


def test_p0_dialog_direction_from_up_down_rechecked_at_the_end(make_updown, tmp_path):
    # 会話の始めは確認済みで「上に」を Z＋ として持った。量を答える前に設定が無くなった：Z にしない
    w, sid, _ = start("confirmed", make_updown, tmp_path, select="C00001")
    r = w.hear(sid, "置くところを少し上に")
    r = w.hear(sid, "そうそう")
    assert r["state"]["dialog"]["direction"] == "Z+"
    Path(w.updown_config).unlink()
    r = w.hear(sid, "3ミリ")
    assert cand(r) is None and r["status"] != "ok"
    assert ut.is_updown_unset_ask(r["message"])


def test_p0_dialog_confirmed_up_still_works(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path, select="C00001")
    w.hear(sid, "置くところを少し上に")
    w.hear(sid, "そうそう")
    r = w.hear(sid, "3ミリ")
    assert cand(r) == ("C00005 Z", "+3.000"), r["message"]


# QA P2 で足した「別のジョブ」は、CHECKER_028 K4（上下の向きはジョブの中身で照合しない）で不一致ではなくなった。
# 不一致の例は、ロボット名違い・ツール番号違い・///USER 付き（mismatch）・FRAME USER 1・RJ の有無の違いに置き換えた。
# 「別のジョブで座標系が同じ」は確認済みになることを test_other_job_same_coordinate_is_confirmed で確かめる
@pytest.mark.parametrize("kind", ["mismatch_robot", "mismatch_tool", "mismatch", "mismatch_frame_user", "mismatch_rj"])
@pytest.mark.parametrize("answer", ["上に3ミリ", "3ミリ下げて"])
def test_dialog_mismatch_other_job_and_robot(kind, answer, make_updown, tmp_path):
    # QA P2：会話の不一致（ロボット名違いと、座標系・ツールの違い）
    w, sid, msg = start(kind, make_updown, tmp_path, select="C00001")
    r = w.hear(sid, "置くところ")
    if r["state"]["dialog"]["step"] == "confirm_position":
        r = w.hear(sid, "そうそう")
    else:
        r = w.select(sid, r["state"]["dialog"]["candidates"][0]["position"] if r["state"]["dialog"]["candidates"]
                     else "C00004")
    assert r["state"]["dialog"]["step"] == "ask_move"
    assert r["state"]["updown"]["status"] == "mismatch"
    r = w.hear(sid, answer)
    assert r["status"] == "ask" and r["message"] == msg and cand(r) is None
    assert not any("（上）" in c["label"] or "（下）" in c["label"] for c in r["state"]["dialog"]["choices"])


# ======================================================================
# P1-1・QA P1-2：未確認のときに上下の言葉で言うよう誘わない
# ======================================================================
_UPDOWN_WORDS = ("上げ", "下げ", "上に", "下に", "上へ", "下へ", "上方向", "下方向")


def _lures(msg: str) -> bool:
    return any(w in msg for w in _UPDOWN_WORDS)


@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p1_axis_question_has_no_up_down_example(kind, make_updown, tmp_path):
    w, sid, _ = start(kind, make_updown, tmp_path)
    r = w.hear(sid, "3mm動かして")
    assert r["status"] == "ask" and "どの方向" in r["message"] and not _lures(r["message"]), r["message"]
    assert "Zプラス" in r["message"] or "Zマイナス" in r["message"]


def test_p1_axis_question_confirmed_keeps_up_down(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "3mm動かして")
    assert r["message"] == "どの方向（X・Y・Z、または上げる・下げる）に動かしますか？"


@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p1_unknown_words_and_side_messages(kind, make_updown, tmp_path):
    w, sid, _ = start(kind, make_updown, tmp_path)
    r = w.hear(sid, "バイオ3ミリ動かして")
    assert "「バイオ」" in r["message"] and not _lures(r["message"]), r["message"]
    r = w.hear(sid, "右に3mm")
    assert "右" in r["message"] and not _lures(r["message"]), r["message"]


def test_p1_messages_confirmed_keep_up_down_examples(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "バイオ3ミリ動かして")
    assert "下に3ミリ" in r["message"]
    r = w.hear(sid, "右に3mm")
    # d（11/5）：右・左は、上下の登録が確認済みでも、画面で初めてなら向きの確かめ（ロボットを正面から見た向き）
    assert "右は Yプラス で合っていますか？" in r["message"] and all(ut.read_facing_choice(c["text"]) for c in r["choices"])


def test_p1_fixed_messages_have_no_up_down_example():
    # 状態を知らずに出す固定の文は、上下の例を出さない
    assert not _lures(ut.SIDE_MESSAGE)
    assert not _lures(ut.unknown_words_message(["バイオ"], "move"))
    assert not _lures(ci.ASK_FIXED)
    assert ut.AXIS_ASK in dg._HANDLED_ASKS and ut.AXIS_ASK_UPDOWN in dg._HANDLED_ASKS


@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p1_dialog_axis_question_still_handled(kind, make_updown, tmp_path):
    # 「置くところを3mm」は今どおり会話が受け持つ（方向を聞く文をそろえたため）
    w, sid, _ = start(kind, make_updown, tmp_path, select="C00001")
    assert dg.handled_by_dialogue(ut.read_utterance("この位置を3mm移動"))
    r = w.hear(sid, "置くところを3mm動かして")
    assert r["state"]["dialog"]["step"] in ("confirm_position", "pick")


@pytest.mark.parametrize("example", ["Zプラス方向に10mm", "この位置をZプラス方向に10mm"])
@pytest.mark.parametrize("kind", NOT_CONFIRMED)
def test_p1_unconfirmed_examples_make_candidates(kind, example, make_updown, tmp_path):
    # QA P1-2：未確認の画面に出す例文は、そのまま送ると候補になる
    w, sid, _ = start(kind, make_updown, tmp_path)
    r = w.hear(sid, example)
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "+10.000"), r["message"]


def test_p1_index_html_examples_switch_by_updown():
    html = (ROOT / "voice_teaching" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "voice_teaching" / "web" / "static" / "highlight.js").read_text(encoding="utf-8")
    app = (ROOT / "voice_teaching" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "speechExamples(u)" in app and "howto-examples" in app and "help-examples" in app
    # 既定（読み込む前・未確認）の例文は軸と符号。上下の例は確認済みのときだけ app.js が入れる
    assert 'placeholder="Zプラス方向に10mm"' in html
    assert "この位置を上に10mm" not in html.split('id="text-input"')[1].split(">")[0]
    assert 'id="howto-examples"' in html
    assert "この位置を上に10mm" in js and "Zプラス方向に10mm" in js


# ======================================================================
# QA P1-1・P2-1・P2-2：「〜します」の2択は、外した文が通るときだけ
# ======================================================================
SPECIFIC = [
    ("下に3mmを2回移動します", "2回の移動ですか、2往復ですか？"),
    ("右に3mm動かします", "右は Yプラス で合っていますか？"),  # d（11/5）：向きの確かめ
    ("Yを3mm下げて動かします", "2つの読み方"),
    ("少し下げて動かします", "量がはっきりしません"),  # 差分の再レビュー QA P2-3：具体的な理由をそのまま返す（戻した）
    ("置くところまで3mm動かします", "へ動かす指示は扱いません"),
    ("下に25mm移動します", "上限"),
    ("Yのマイナス0mm動かします", "移動量が0"),
    ("C00099を3mm下げて動かします", "C00099"),
    # 量・方向が足りない「〜します」は、答えを待つ形にせず言い直してもらう（内部再レビュー QA P1-A）
    ("動かします", "量と向きを一緒に、もう一度言ってください"),
    ("下げて動かします", "量と向きを一緒に、もう一度言ってください"),
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,part", SPECIFIC)
def test_qa_p1_shimasu_does_not_hide_specific_answers(text, part, source, make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text, source=source)
    assert r["status"] in ("ask", "stop") and cand(r) is None, (text, r["message"])
    assert TAIL not in r["message"] and labels(r) != LABELS, (text, r["message"])
    assert part in r["message"], (text, r["message"])


def test_qa_p1_unconfirmed_shimasu_gives_updown_message(make_updown, tmp_path):
    w, sid, msg = start("missing", make_updown, tmp_path)
    r = w.hear(sid, "3mm下げて動かします")
    assert r["status"] == "ask" and updown_said(msg, r["message"], exact=True) and labels(r) != LABELS and cand(r) is None


def test_qa_p1_no_position_shimasu(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path, select=None)
    r = w.hear(sid, "Yのマイナス3ミリずらします")
    assert r["status"] == "ask" and labels(r) != LABELS and "位置" in r["message"] and cand(r) is None


def test_qa_p1_speed_shimasu_without_two_choices(make_updown, tmp_path):
    # 速度の変更は既定で止めている：「〜します」でも2択にせず、その理由
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "速度を10%下げて動かします")
    assert r["status"] == "stop" and labels(r) != LABELS and cand(r) is None and "速度" in r["message"]


def test_p2_2_speed_shimasu_choice_text(speed_enabled):
    # 速度の変更を試すときだけ：［変更したい］の文は「C00005の速度を10%下げて」（facts から作る）
    s = Session.from_file(DEMO_C_PATH, max_speed_change_percent=20)
    s.select("C00005")
    r = s.hear("速度を10%下げて動かします")
    assert r.status == "ask" and [c[0] for c in r.choices] == LABELS, r.message
    assert r.choices[0][1] == "C00005の速度を10%下げて"
    f = ut.read_utterance(r.choices[0][1])
    assert not f.unknown_words and f.op == "speed" and f.requested_value() == -10


@pytest.mark.parametrize("text,choice", [
    ("Yのマイナス3ミリずらします", "C00005をYのマイナス3ミリ"),
    ("Y座標を3mm下げて動かします", "C00005をYのマイナス3ミリ"),
    ("3mm下げて動かします", "C00005を3ミリ下げて"),
    ("下に3mm移動します", "C00005を3ミリ下げて"),
    ("C00004を3mm上げて移動します", "C00004を3ミリ上げて"),
    ("Zのプラス2ミリずらします", "C00005をZのプラス2ミリ"),
])
def test_p2_choice_text_is_built_from_facts_with_position(text, choice, make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text)
    assert labels(r) == LABELS, r["message"]
    assert r["choices"][0]["text"] == choice
    f = ut.read_utterance(choice)
    assert not f.unknown_words and f.shimasu_text is None


def test_p2_1_dialog_shimasu_choice_has_position(make_updown, tmp_path):
    # 会話の途中の「〜します」の［変更したい］にも位置番号を入れる。あとで別の位置を選んでも、その位置に当てない
    w, sid, _ = start("confirmed", make_updown, tmp_path, select="C00001")
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "そうそう")
    assert r["state"]["dialog"]["step"] == "ask_move"
    r = w.hear(sid, "Yのマイナス3ミリずらします")
    assert labels(r) == LABELS
    assert r["choices"][0]["text"] == "C00005をYのマイナス3ミリ"
    assert w.select(sid, "C00004")["state"]["selected"] == "C00004"  # 会話の途中なので、方向と量を聞き直す
    r2 = w.hear(sid, r["choices"][0]["text"])
    assert cand(r2) == ("C00005 Y", "-3.000")


def test_p2_1_dialog_shimasu_still_checked_by_session(make_updown, tmp_path):
    # 会話の途中の「〜します」も、上限などの検査を通るときだけ2択
    w, sid, _ = start("confirmed", make_updown, tmp_path, select="C00001")
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "そうそう")
    r = w.hear(sid, "Yのマイナス25ミリずらします")
    assert labels(r) != LABELS and "上限" in r["message"] and cand(r) is None


# ======================================================================
# P2-3：上下の設定の変更で承認を消したときの、出力の断り文
# ======================================================================
def test_p2_3_export_message_after_state_request(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    assert cand(w.hear(sid, "Zマイナス3ミリ")) == ("C00005 Z", "-3.000")
    assert w.approve(sid, "テスト太郎")["ok"] is True
    _rewrite(w.updown_config, lambda o: o.__setitem__("evidence", o["evidence"] + "。"))
    st = w.state(sid)
    assert st["state"]["approval"] is None
    x = w.export(sid)
    assert x["ok"] is False and x["error"] == flow_mod.UPDOWN_CHANGED_EXPORT


def test_p2_3_other_cancel_reasons_keep_not_approved(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    w.hear(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト太郎")
    _rewrite(w.updown_config, lambda o: o.__setitem__("evidence", o["evidence"] + "。"))
    w.state(sid)
    w.undo(sid)  # 元に戻した：もう上下の文ではない
    x = w.export(sid)
    assert x["ok"] is False and x["error"] == flow_mod.NOT_APPROVED


# ======================================================================
# QA P2：S-msg の文
# ======================================================================
APPROVAL_CANCELLED = "新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。内容を確認して、もう一度承認してください。"


def test_s_msg_text_is_the_decided_one():
    # 唐川さんの決定（一字一句このとおり）
    assert session_mod.APPROVED_PENDING_MESSAGE == APPROVAL_CANCELLED


def test_s_msg_only_when_approval_was_really_cancelled(make_updown, tmp_path):
    w, sid, _ = start("confirmed", make_updown, tmp_path)
    assert cand(w.hear(sid, "Zマイナス3ミリ")) == ("C00005 Z", "-3.000")
    # 承認していない：今の「承認待ちの変更候補があります…」のまま
    r = w.hear(sid, "Yのマイナス3ミリ")
    assert r["status"] == "stop" and r["message"] == session_mod.PENDING_MESSAGE
    # 承認した：新しい指示で承認を取り消したときだけ、決まった文
    assert w.approve(sid, "テスト太郎")["ok"] is True
    r = w.hear(sid, "Yのマイナス3ミリ", source="voice")
    assert r["status"] == "stop" and r["message"] == APPROVAL_CANCELLED and r["state"]["approval"] is None
    assert cand(r) == ("C00005 Z", "-3.000")
    # もう承認されていない：次は今の文
    r = w.hear(sid, "Yのマイナス3ミリ")
    assert r["message"] == session_mod.PENDING_MESSAGE


def test_messages_have_no_forbidden_words():
    for m in (ut.SIDE_MESSAGE, ut.AXIS_ASK, ut.AXIS_ASK_UPDOWN, ci.ASK_FIXED, session_mod.APPROVED_PENDING_MESSAGE,
              ut.unknown_words_message(["あ"], "move"), session_mod.NO_POSITION):
        assert not ut.contains_forbidden(m)


# ======================================================================
# 唐川さんの判断①：「Z座標を3mm下げて」は Z の値を減らす指示として今どおり許可。物理的な上下の指示とは facts の上で区別する
#   value_axes：軸の文字＋値の増減（「Z座標を…下げて」「Zの値を減らして」）。vertical：上下の言葉から補った Z
#   会話を分けても、前の発話の上下の言葉・打ち消し・知らない言葉・疑問の形を引き継ぐ。迷う組み合わせは聞き返す側
# ======================================================================
def test_value_axes_and_vertical_are_separate_facts():
    f = ut.read_utterance("Z座標を3mm下げて")
    assert f.value_axes == ("Z",) and f.vertical is False and f.explicit_axes == ("Z",)
    f = ut.read_utterance("Zの値を3mm減らして")
    assert f.value_axes == ("Z",) and f.vertical is False
    f = ut.read_utterance("3mm下げて")
    assert f.value_axes == () and f.vertical is True and f.explicit_axes == ()
    f = ut.read_utterance("Z座標を下に3mm")  # 値の言葉と上下の言葉の両方：上下として扱う（未確認なら聞き返す）
    assert f.value_axes == ("Z",) and f.vertical is True
    f = ut.read_utterance("Zマイナス3ミリ")
    assert f.value_axes == () and f.vertical is False and f.explicit_axes == ("Z",)
    m = ut.merge_answer(ut.read_utterance("Z座標を下げて"), ut.read_utterance("3ミリ"))
    assert m.value_axes == ("Z",) and m.vertical is False
    m = ut.combine_axes(ut.read_utterance("Z座標を"), ut.read_utterance("3mm下げて"))
    assert m.value_axes == ("Z",) and m.vertical is True


Z3 = ("C00005 Z", "-3.000")
VALUE_COMBOS = [
    # (順に話す文, 未確認のときの候補, 確認済みのときの候補)
    (["Z座標を3mm下げて"], Z3, Z3),
    (["Zの値を3mm減らして"], Z3, Z3),
    (["Z座標を下げて", "3ミリ"], Z3, Z3),
    (["Zの値を減らして", "3ミリ"], Z3, Z3),
    (["3mm下げて"], None, Z3),
    (["下げて", "3ミリ"], None, Z3),
    # 「下げて」のあとの「Z座標」「Zの3ミリ」は新しい指示として読む（向きを言っていないので聞き返す。前の「下げて」を値の増減にしない）
    (["下げて", "Z座標", "3ミリ"], None, None),
    (["下げて", "Zの3ミリ"], None, None),
    # 分けて言った「Z座標を」→「3mm下げて」：あとの「下げて」は上下の言葉として読む（迷うので未確認なら聞き返す）
    (["Z座標を", "3mm下げて"], None, Z3),
    (["Z座標を下げて", "3ミリ下げて"], None, Z3),
    (["Z座標を下に", "3ミリ"], None, Z3),
    # 前の発話の打ち消し・知らない言葉・疑問の形は、答えのあとも残す（候補にしない）
    (["Z座標を下げないで", "3ミリ"], None, None),
    (["Z座標をバイオ下げて", "3ミリ"], None, None),
    (["Z座標を下げて？", "3ミリ"], None, None),
    (["下げて？", "3ミリ"], None, None),
    (["バイオ下げて", "3ミリ"], None, None),
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("kind", ["missing", "changed", "mismatch", "confirmed"])
@pytest.mark.parametrize("texts,unconf,conf", VALUE_COMBOS)
def test_value_change_and_up_down_combinations(texts, unconf, conf, kind, source, make_updown, tmp_path):
    w, sid, _ = start(kind, make_updown, tmp_path)
    for t in texts:
        r = w.hear(sid, t, source=source)
    want = conf if kind == "confirmed" else unconf
    assert cand(r) == want, (texts, kind, r["message"])
    if want is None:
        assert r["status"] != "ok" and r["state"]["can_approve"] is False
        assert w.approve(sid, "テスト太郎")["ok"] is False and w.export(sid)["ok"] is False


@pytest.mark.parametrize("answer,want", [("上に3ミリ", ("C00004 Z", "+3.000")), ("3ミリ下げて", ("C00004 Z", "-3.000"))])
def test_other_job_same_coordinate_is_confirmed(answer, want, make_updown, tmp_path):
    # CHECKER_028 K4：別のジョブ（DEMO_PLACE）でも、ロボット名・座標系・ツール番号が同じなら上下の向きは確認済み
    w, sid, msg = start("other_job_same_coordinate", make_updown, tmp_path, select="C00004")
    assert msg is None and w.state(sid)["state"]["updown"]["status"] == "confirmed"
    r = w.hear(sid, answer)
    assert cand(r) == want, r["message"]
