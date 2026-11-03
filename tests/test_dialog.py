"""受入テスト：Claude との会話（「置くところを少し手前にして」）。実装より先に書いた。

唐川さんの決め事（2026-11-03 夜）：
- Claude（ここではモックか偽の Claude）は、意図の理解・位置の候補・聞き返しだけ。座標の変更・ガード・数字・承認はプログラム。
- 「置くところ」の位置は 案A：合成ジョブの命令の行と行番号だけを渡して候補を出させる（座標・//POS は渡さない）。
  答えはプログラムが照合し、根拠の文もプログラムが行の中身から作る。1つに絞れなければ人がボタンで選ぶ。
- 位置の確認だけは、短い「はい／そう／そうそう／それ／うん／お願いします」でも選べる（選択であって承認ではない）。
- 手前・奥・前・後ろ・右・左は軸に当てはめない。方向は X・Y・Z の＋−の6つのボタン、量は人の言葉だけ。
- 組み立てた指示は、プログラムが作る決まった文（「C00005をYのマイナス3ミリ」）にして、今の流れ（照らし合わせ・
  plan_shift・20mm・R1・B1/B6・D1/D2）にそのまま通す。
- 承認待ちの候補がある間は、新しい移動の指示を Claude に送らない（呼ぶ前に止める）。
- 失敗（通信・断り・思わぬ答え・合成でないジョブ）は候補を作らず、決まった短い文で位置の手動の選択へ。
実 API は呼ばない（偽のクライアントと偽の解釈の係だけ）。
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import jbi, sim
from voice_teaching import utterance as u
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
DEMO = (ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()
# 合成でないジョブ（コメントの1文字だけ違う。samples/dummy のどれとも同じバイト列ではない）
NOT_SYNTH = DEMO_C.replace(b"NOT A REAL ROBOT JOB", b"NOT A REAL ROBOT JOB.")
KEY = "sk-ant-TEST-SECRET-0123456789"
OPENING = "置くところを少し手前にして"
ASSERTIVE = list(u.FORBIDDEN_WORDS) + ["離す", "つかむ", "掴む", "放す"]


class Counting:
    """解釈の係を包み、呼ばれた回数を数える。"""

    def __init__(self, inner):
        self.inner = inner
        self.kind, self.model, self.notice = inner.kind, inner.model, inner.notice
        self.locator_label = getattr(inner, "locator_label", None)
        self.decides = 0
        self.locates = 0
        self.locate_args = []

    def decide(self, *a, **k):
        self.decides += 1
        return self.inner.decide(*a, **k)

    def locate(self, *a, **k):
        self.locates += 1
        self.locate_args.append((a, k))
        return self.inner.locate(*a, **k)


class FixedLocator(ci.MockInterpreter):
    """位置の候補を、決まった答え（生の形）で返す偽物。"""

    def __init__(self, answer=None, exc=None):
        self.answer, self.exc = answer, exc

    def locate(self, utterance, word, positions, lines):
        if self.exc is not None:
            raise self.exc
        return self.answer


@pytest.fixture
def counting():
    return Counting(ci.MockInterpreter())


def start(interp=None, data=DEMO_C, select=None):
    w = WebFlow(interpreter=interp or ci.MockInterpreter())
    r = w.load(data)
    assert r["ok"], r
    sid = r["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid


def dlg(r):
    return r["state"]["dialog"]


def choice_ids(r):
    return [c["id"] for c in dlg(r)["choices"]]


def choose(w, sid, r, cid):
    return w.choose(sid, cid, dlg(r)["turn"])


# ======================================================================
# 1. 唐川さんの会話（モック）：置くところ → そうそう → 3mm → 方向のボタン → 確認表示 → ボタンの承認
# ======================================================================
# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_owner_dialogue_end_to_end_with_mock(counting, tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "sim")
    w, sid = start(counting, select="C00001")
    r = w.hear(sid, OPENING, source="voice")
    d = dlg(r)
    assert r["ok"] and r["status"] == "ask"
    assert d["step"] == "confirm_position"
    assert [c["position"] for c in d["candidates"]] == ["C00005"]
    ev = d["candidates"][0]["evidence"]
    assert [e["line_no"] for e in ev] == [30, 31, 32]
    assert ev[0]["text"] == "MOVL C00005 V=50.0 PL=0" and ev[1]["note"] == "待つ" and ev[2]["note"] == "信号を切る"
    assert r["message"] == "「置くところ」は C00005 のことでしょうか？"
    assert d["relative"] == "手前"
    assert choice_ids(r) == ["pos:C00005", "other"]
    assert [c["label"] for c in d["choices"]] == ["C00005 です", "ほかの位置"]
    assert d["speaker"] == "モック（テスト用）"
    assert r["state"]["candidate"] is None and r["state"]["selected"] == "C00001"  # まだ何も変えていない
    assert counting.locates == 1 and counting.decides == 0

    r = w.hear(sid, "そうそう", source="voice")
    d = dlg(r)
    assert r["status"] == "ask" and d["step"] == "ask_move"
    assert r["state"]["selected"] == "C00005"
    assert d["estimated"] == {"position": "C00005", "word": "置くところ", "label": "推定（未確定）",
                              "by": "モック（テスト用）"}
    assert r["message"].startswith("どちらの方向（X・Y・Z の＋−）に、何mm動かしますか？")
    assert "「手前」" in r["message"]  # 手前は軸に当てはめていないことを伝える
    assert choice_ids(r) == ["dir:X+", "dir:X-", "dir:Y+", "dir:Y-", "dir:Z+", "dir:Z-"]
    assert r["state"]["candidate"] is None and r["state"]["approval"] is None

    r = w.hear(sid, "3mm", source="voice")
    d = dlg(r)
    assert r["status"] == "ask" and d["step"] == "ask_move" and d["amount_mm"] == 3.0
    assert "3mm" in r["message"] and "方向" in r["message"]
    assert [c["label"] for c in d["choices"]] == ["X＋ 3mm", "X− 3mm", "Y＋ 3mm", "Y− 3mm", "Z＋（上）3mm", "Z−（下）3mm"]
    assert r["state"]["candidate"] is None
    assert counting.decides == 0  # まだ Claude（モック）には変更を頼んでいない

    r = choose(w, sid, r, "dir:Y-")
    st = r["state"]
    assert r["ok"] and r["status"] == "ok" and r["candidate_made"] is True
    assert st["candidate"]["target"] == "C00005 Y" and st["candidate"]["delta"] == "-3.000"
    assert st["candidate"]["heard"] == "C00005をYのマイナス3ミリ"  # プログラムが作った決まった文を、今の流れに通した
    assert st["candidate"]["estimated"]["label"] == "推定（未確定）"
    assert st["dialog"]["step"] is None
    assert st["approval"] is None and st["can_approve"] is True  # 承認はまだ。ボタンだけ
    assert counting.decides == 1
    who = [t["who"] for t in st["dialog"]["turns"]]
    assert who[0] == "人" and "モック（テスト用）" in who
    assert st["dialog"]["turns"][-1]["text"] == dg.DONE

    # 承認はボタン（approve）だけ。出力に「推定」の名前は残らない
    assert w.approve(sid, "唐川")["ok"] is True
    out = w.export(sid)
    assert out["ok"] is True
    data = Path(out["path"]).read_bytes()
    assert "推定".encode() not in data and "置くところ".encode("utf-8") not in data
    diff = [a for a, b in zip(DEMO_C.splitlines(), data.splitlines()) if a != b]
    assert len(diff) == 2  # //NAME と C00005 の行だけ


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_full_instruction_after_confirmation_makes_candidate_immediately():
    w, sid = start()
    r = w.hear(sid, "置くところを3mm下げて")
    assert dlg(r)["step"] == "confirm_position"
    r = w.hear(sid, "はい")
    st = r["state"]
    assert r["candidate_made"] and st["candidate"]["target"] == "C00005 Z" and st["candidate"]["delta"] == "-3.000"
    assert st["candidate"]["heard"] == "C00005をZのマイナス3ミリ"


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_pick_place_word_uses_dout_on_rule_in_mock():
    w, sid = start()
    r = w.hear(sid, "取るところを上に2mm")
    assert [c["position"] for c in dlg(r)["candidates"]] == ["C00002"]
    assert [e["note"] for e in dlg(r)["candidates"][0]["evidence"]] == ["C00002 へ直線で動く", "信号を入れる"]


def test_talking_first_without_selection_works():
    w, sid = start()
    r = w.hear(sid, OPENING)
    assert r["status"] == "ask" and dlg(r)["step"] == "confirm_position" and r["state"]["selected"] is None


# ======================================================================
# 2. 位置の確認：短い肯定は「選択」だけ。承認の言葉は今どおり断る。打ち消しは止まって同じ所にいる
# ======================================================================
@pytest.mark.parametrize("word", ["はい", "そう", "そうそう", "それ", "うん", "お願いします", "そうそう。", " はい "])
def test_short_affirmatives_select_the_single_candidate(word):
    w, sid = start()
    w.hear(sid, OPENING)
    r = w.hear(sid, word, source="voice")
    assert r["state"]["selected"] == "C00005" and dlg(r)["step"] == "ask_move"
    assert r["state"]["candidate"] is None and r["state"]["approval"] is None


@pytest.mark.parametrize("word", ["OK", "承認", "承認して", "これでいい", "決定", "確定して", "オーケー"])
def test_approval_words_are_refused_in_position_step(word, no_approve_from_words):
    w, sid = start(select="C00001")
    w.hear(sid, OPENING)
    r = w.hear(sid, word, source="voice")
    assert r["status"] == "stop" and u.APPROVE_MESSAGE in r["message"]
    assert r["state"]["selected"] == "C00001" and dlg(r)["step"] == "confirm_position"
    assert choice_ids(r) == ["pos:C00005", "other"]


@pytest.mark.parametrize("word", ["そうじゃなくて", "違う", "いいえ", "C00005じゃない", "選ばないで"])
def test_negation_in_position_step_stops_and_keeps_step(word, counting):
    w, sid = start(counting, select="C00001")
    w.hear(sid, OPENING)
    r = w.hear(sid, word)
    # CHECKER_026 S-pick：「ほかの位置」のボタンが見えているので、次にすることを案内する（止まる動きは今どおり）
    assert r["status"] == "stop" and r["message"] == dg.PICK_AFTER_NEGATION
    assert r["state"]["selected"] == "C00001" and dlg(r)["step"] == "confirm_position"
    assert counting.locates == 1 and counting.decides == 0


@pytest.mark.parametrize("word", ["C00004", "それで", "選んで", "えーと", "置くところ", "はいはい", "そうですね"])
def test_other_words_in_position_step_ask_and_do_not_select(word):
    w, sid = start(select="C00001")
    w.hear(sid, OPENING)
    r = w.hear(sid, word)
    assert r["status"] in ("ask", "stop")
    assert r["state"]["selected"] == "C00001" and dlg(r)["step"] == "confirm_position"


def test_affirmative_with_several_candidates_asks_for_button():
    loc = FixedLocator([{"position": "C00005", "evidence_lines": [30, 31, 32], "rank": 1},
                        {"position": "C00004", "evidence_lines": [29], "rank": 2}])
    w, sid = start(loc, select="C00001")
    r = w.hear(sid, OPENING)
    assert [c["position"] for c in dlg(r)["candidates"]] == ["C00005", "C00004"]
    assert choice_ids(r) == ["pos:C00005", "pos:C00004", "other"]
    assert "2つ" in r["message"]
    r = w.hear(sid, "はい")
    assert r["status"] == "ask" and r["state"]["selected"] == "C00001" and dlg(r)["step"] == "confirm_position"
    r = choose(w, sid, r, "pos:C00004")
    assert r["state"]["selected"] == "C00004" and dlg(r)["estimated"]["position"] == "C00004"


def test_other_position_button_goes_to_manual_selection_then_continues():
    w, sid = start(select="C00001")
    r = w.hear(sid, OPENING)
    r = choose(w, sid, r, "other")
    assert dlg(r)["step"] == "pick" and r["state"]["selected"] == "C00001"
    r = w.select(sid, "C00004")
    assert dlg(r)["step"] == "ask_move" and r["state"]["selected"] == "C00004" and dlg(r)["estimated"] is None
    r = w.hear(sid, "Y座標を3ミリ下げて")
    assert r["candidate_made"] and r["state"]["candidate"]["target"] == "C00004 Y"


def test_stale_choice_is_refused():
    w, sid = start(select="C00001")
    r0 = w.hear(sid, OPENING)
    r1 = w.hear(sid, "そうそう")
    assert dlg(r1)["step"] == "ask_move"
    r = w.choose(sid, "pos:C00005", dlg(r0)["turn"])  # 古い選択肢
    assert r["status"] == "stop" and r["message"] == dg.STALE
    r = w.choose(sid, "dir:Y-", dlg(r0)["turn"])
    assert r["status"] == "stop" and r["state"]["candidate"] is None
    r = w.choose(sid, "bogus", dlg(r1)["turn"])
    assert r["status"] == "stop" and r["state"]["candidate"] is None


# ======================================================================
# 3. 方向は6つのボタン（プログラムが作る）。量は人の言葉だけ。手前・右などは軸にしない
# ======================================================================
@pytest.mark.parametrize("cid,axis,delta", [("dir:X+", "X", "+3.000"), ("dir:X-", "X", "-3.000"),
                                            ("dir:Y+", "Y", "+3.000"), ("dir:Y-", "Y", "-3.000"),
                                            ("dir:Z+", "Z", "+3.000"), ("dir:Z-", "Z", "-3.000")])
def test_six_direction_buttons(cid, axis, delta):
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "3ミリ")
    r = choose(w, sid, r, cid)
    c = r["state"]["candidate"]
    assert c["target"] == f"C00005 {axis}" and c["delta"].lstrip("+") == delta.lstrip("+")


def test_direction_first_then_amount():
    w, sid = start()
    w.hear(sid, OPENING)
    r = w.hear(sid, "はい")
    r = choose(w, sid, r, "dir:Z+")
    assert dlg(r)["step"] == "ask_move" and dlg(r)["direction"] == "Z+" and r["state"]["candidate"] is None
    assert "何mm" in r["message"] and dlg(r)["choices"] == []  # 量のボタンは出さない（量は人の言葉だけ）
    r = w.hear(sid, "2mm")
    assert r["state"]["candidate"]["target"] == "C00005 Z" and r["state"]["candidate"]["delta"] == "+2.000"


@pytest.mark.parametrize("text", ["手前に3mm", "右に3mm", "奥へ3mm", "前に3ミリ", "後ろに3mm", "左に3mm"])
def test_relative_words_are_not_mapped_to_axes(text):
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, text)
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    assert dlg(r)["step"] == "ask_move" and dlg(r)["direction"] is None and dlg(r)["amount_mm"] is None
    assert choice_ids(r)[:6] == ["dir:X+", "dir:X-", "dir:Y+", "dir:Y-", "dir:Z+", "dir:Z-"]


def test_axis_without_sign_offers_only_that_axis():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "Y方向に3mm")
    assert r["status"] == "ask" and choice_ids(r) == ["dir:Y+", "dir:Y-"] and r["state"]["candidate"] is None


def test_sign_without_axis_is_asked_and_buttons_are_not_filtered():
    # 内部 Safety レビュー（91285fe）P1-3 の直しで期待を変えた：軸の無い符号でボタンを絞らない（前は X−・Y−・Z− に絞っていた）
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "マイナス3mm")
    assert r["status"] == "ask" and r["message"] == dg.SIGN_NEEDS_AXIS and len(choice_ids(r)) == 6
    assert dlg(r)["amount_mm"] is None and r["state"]["candidate"] is None


def test_two_axes_in_answer_asks_with_buttons_d1():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "Yを3mm下げて")  # Y と 下げ（Z）：推測しない
    assert r["status"] == "ask" and r["state"]["candidate"] is None and dlg(r)["direction"] is None


def test_unknown_word_in_answer_asks_and_keeps_nothing_b1():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "バイオ3ミリ下げて")
    assert r["status"] == "ask" and "バイオ" in r["message"]
    assert dlg(r)["amount_mm"] is None and dlg(r)["direction"] is None and r["state"]["candidate"] is None


def test_negation_in_answer_stops_b6():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "3mm下げないで")
    assert r["status"] == "stop" and dlg(r)["amount_mm"] is None and r["state"]["candidate"] is None
    assert dlg(r)["step"] == "ask_move"


def test_vague_amount_in_answer_asks():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "少し")
    assert r["status"] == "ask" and dlg(r)["amount_mm"] is None


def test_over_limit_amount_goes_through_guard_and_is_not_approvable():
    w, sid = start()
    w.hear(sid, "置くところを30mm下げて")
    r = w.hear(sid, "はい")
    st = r["state"]
    assert st["can_approve"] is False
    assert r["status"] != "ok" or st["candidate"]["blocked_reason"]


def test_undo_word_ends_dialogue():
    w, sid = start(select="C00001")
    w.hear(sid, OPENING)
    r = w.hear(sid, "やめて")
    assert dlg(r)["step"] is None and r["state"]["selected"] == "C00001" and r["state"]["candidate"] is None


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_new_instruction_with_position_ends_dialogue_and_runs_normally():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "C00004を上に2mm")
    assert dlg(r)["step"] is None and r["state"]["candidate"]["target"] == "C00004 Z"


# ======================================================================
# 4. 始めの発話の検査（呼ぶ前に止める）
# ======================================================================
@pytest.mark.parametrize("text", ["置くところを下げないで", "置くところじゃなくて取るところ", "置くところは動かさないで"])
def test_negation_in_opening_stops_without_locate(text, counting):
    w, sid = start(counting)
    r = w.hear(sid, text)
    assert r["status"] == "stop" and counting.locates == 0 and counting.decides == 0
    assert dlg(r)["step"] is None


def test_unknown_word_in_opening_asks_without_locate(counting):
    w, sid = start(counting)
    r = w.hear(sid, "置くところをバイオ3ミリ下げて")
    assert r["status"] == "ask" and "バイオ" in r["message"] and counting.locates == 0


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_pending_candidate_blocks_dialogue_without_locate(counting):
    w, sid = start(counting, select="C00004")
    assert w.hear(sid, "この位置を上に2mm")["candidate_made"]
    r = w.hear(sid, OPENING)
    assert r["status"] == "stop" and r["message"].startswith("承認待ちの変更候補があります")
    assert counting.locates == 0 and dlg(r)["step"] is None


def test_non_synthetic_job_never_locates_and_asks_to_pick(counting):
    w, sid = start(counting, data=NOT_SYNTH)
    r = w.hear(sid, OPENING)
    assert counting.locates == 0
    assert dlg(r)["step"] == "pick" and r["state"]["candidate"] is None and dlg(r)["candidates"] == []
    assert "位置を選んでください" in r["message"]


@pytest.mark.parametrize("answer", [
    [{"position": "C00099", "evidence_lines": [30], "rank": 1}],               # 一覧に無い位置
    [{"position": "C00005", "evidence_lines": [5], "rank": 1}],                # 命令の範囲外の行（//POS の行）
    [{"position": "C00005", "evidence_lines": [24, 25], "rank": 1}],           # その位置と関係の無い行だけ
    [{"position": "C00005", "evidence_lines": [], "rank": 1}],                 # 根拠なし
    [{"position": "C00005", "evidence_lines": [30, 30, 30, 30, 30, 30], "rank": 1}],  # 多すぎる
    [{"position": "C00005", "evidence_lines": [30], "rank": 1, "reason": "置く"}],  # 余計な項目
    [{"position": "C00005", "evidence_lines": [30], "rank": 1}] * 4,           # 4件以上
    [{"position": "C00005", "evidence_lines": ["30"], "rank": 1}],             # 数でない
    [{"position": "C00005", "evidence_lines": [True], "rank": 1}],
    [{"position": "C00005", "evidence_lines": [30], "rank": 9}],
    [],
    None,
    "C00005",
])
def test_bad_locate_answers_never_become_candidates(answer, counting):
    w, sid = start(Counting(FixedLocator(answer)), select="C00001")
    r = w.hear(sid, OPENING)
    assert dlg(r)["step"] == "pick" and dlg(r)["candidates"] == [] and r["state"]["selected"] == "C00001"
    assert r["state"]["candidate"] is None
    assert r["message"] == dg.LOCATE_FAILED.format(word="置くところ")


@pytest.mark.parametrize("exc", [ci.InterpreterError(ci.BUSY), ci.InterpreterError(ci.REFUSED),
                                 ci.InterpreterError(ci.FAILED)])
def test_locate_errors_fall_back_to_manual_selection(exc):
    w, sid = start(FixedLocator(exc=exc))
    r = w.hear(sid, OPENING)
    assert dlg(r)["step"] == "pick" and r["state"]["candidate"] is None
    assert str(exc) in r["message"] and "位置を選んでください" in r["message"]


def test_interpreter_without_locate_falls_back():
    class Plain:
        kind, model, notice = "mock", None, "x"

        def decide(self, facts, selected, positions=()):
            return []
    w, sid = start(Plain())
    r = w.hear(sid, OPENING)
    assert dlg(r)["step"] == "pick"


def test_verify_drops_bad_and_keeps_good_sorted_by_rank():
    job = jbi.parse_bytes(DEMO_C)
    got = dg.verify_candidates(job, [
        {"position": "C00004", "evidence_lines": [29], "rank": 2},
        {"position": "C00005", "evidence_lines": [31, 32], "rank": 1},   # 位置の行のすぐ後（となり）を含む
        {"position": "C00006", "evidence_lines": [25], "rank": 3},       # 関係の無い行
    ])
    assert [c.position for c in got] == ["C00005", "C00004"]


# ======================================================================
# 5. くり返しのあいまいさ（「2回」）：feat/nl-polish の pre_check が2択で聞き、どちらでも止める。
#    対話の層はこれを変えない（解釈の係を呼ばない・候補を作らない）
# ======================================================================
def test_repeat_count_asks_then_explains(counting):
    w, sid = start(counting, select="C00001")
    r = w.hear(sid, "C00001からC00002を2回移動させて")
    assert r["status"] == "ask" and [c["label"] for c in r["choices"]] == ["2回の移動", "2往復"]
    assert counting.decides == 0 and counting.locates == 0 and dlg(r)["step"] is None
    r = w.hear(sid, r["choices"][1]["text"])
    assert r["status"] == "stop" and r["message"] == u.REPEAT_STOP_MESSAGE
    assert r["state"]["candidate"] is None and counting.decides == 0


def test_repeat_roundtrip_explains_directly(counting):
    w, sid = start(counting, select="C00001")
    r = w.hear(sid, "C00001とC00002を2往復させて")
    assert r["message"] == u.REPEAT_STOP_MESSAGE and r["state"]["candidate"] is None and counting.decides == 0


def test_repeat_with_place_word_is_not_a_dialogue(counting):
    w, sid = start(counting)
    r = w.hear(sid, "置くところを2回動かして")
    assert r["status"] == "ask" and dlg(r)["step"] is None and counting.locates == 0


def test_repeat_negated_is_stopped_b6(counting):
    w, sid = start(counting, select="C00001")
    r = w.hear(sid, "2回移動させないで")
    assert r["status"] == "stop" and dlg(r)["step"] is None


def test_repeat_inside_dialogue_is_not_used_as_amount():
    w, sid = start()
    w.hear(sid, OPENING)
    w.hear(sid, "はい")
    r = w.hear(sid, "2回")
    assert r["status"] == "ask" and dlg(r)["amount_mm"] is None and r["state"]["candidate"] is None


# ======================================================================
# 6. 承認待ちの候補がある間は、新しい移動の指示を解釈の係に送らない（呼ぶ前に止める）
# ======================================================================
# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_pending_candidate_stops_new_move_before_interpreter(counting):
    w, sid = start(counting, select="C00004")
    assert w.hear(sid, "この位置を上に2mm")["candidate_made"]
    assert counting.decides == 1
    r = w.hear(sid, "この位置を下に3mm")
    assert r["status"] == "stop" and r["message"] == "承認待ちの変更候補があります。比べるか、元に戻してから指示してください"
    assert counting.decides == 1 and r["tool_calls"] == []


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_pending_candidate_with_real_claude_sends_nothing():
    c = NS(messages=NS(calls=[], create=lambda **kw: (_ for _ in ()).throw(AssertionError("送った"))))
    w, sid = start(ci.ClaudeInterpreter(KEY, client=c), select="C00004")
    s = w._entries[sid].session
    s.hear("この位置を上に2mm")
    s.propose({"kind": "shift", "position": "C00004", "axis": "Z", "delta_mm": 2.0})
    assert s.candidate is not None
    r = w.hear(sid, "この位置を下に3mm")
    assert r["status"] == "stop" and r["message"].startswith("承認待ち")


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_undo_and_compare_still_work_with_pending_candidate(counting):
    w, sid = start(counting, select="C00004")
    w.hear(sid, "この位置を上に2mm")
    assert w.hear(sid, "比べて")["status"] == "ok"
    assert w.hear(sid, "元に戻して")["state"]["candidate"] is None


# ======================================================================
# 7. 本物の Claude の位置の候補（偽のクライアント）：送る形・送らないもの・応答の確かめ
# ======================================================================
class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc is not None:
            raise self.exc
        return self.response


def tool_use(name, inp):
    return NS(type="tool_use", name=name, input=inp, id="toolu_1")


def resp(*blocks, stop="tool_use"):
    return NS(stop_reason=stop, content=list(blocks))


GOOD = {"candidates": [{"position": "C00005", "evidence_lines": [30, 31, 32], "rank": 1}]}


def claude_flow(response=None, exc=None, data=DEMO_C):
    fm = FakeMessages(response, exc)
    w, sid = start(ci.ClaudeInterpreter(KEY, client=NS(messages=fm)), data=data, select="C00001")
    return w, sid, fm


def test_locate_request_contract():
    w, sid, fm = claude_flow(resp(tool_use("propose_target", GOOD)))
    r = w.hear(sid, OPENING)
    assert [c["position"] for c in dlg(r)["candidates"]] == ["C00005"] and dlg(r)["speaker"] == "Claude"
    assert len(fm.calls) == 1
    kw = fm.calls[0]
    assert set(kw) == {"model", "max_tokens", "output_config", "tools", "tool_choice", "system", "messages"}
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 8000 and kw["output_config"] == {"effort": "low"}
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    for banned in ("thinking", "temperature", "top_p", "top_k"):
        assert banned not in kw
    assert [t["name"] for t in kw["tools"]] == ["propose_target", "ask_user"]
    for t in kw["tools"]:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
    item = kw["tools"][0]["input_schema"]["properties"]["candidates"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"position", "evidence_lines", "rank"}
    assert item["properties"]["position"]["enum"] == [f"C0000{i}" for i in range(8)]
    assert kw == ci.build_locate_request("claude-opus-5-5", OPENING, "置くところ",
                                         [f"C0000{i}" for i in range(8)], dg.instruction_lines(jbi.parse_bytes(DEMO_C)))


def test_locate_request_sends_instruction_lines_but_never_coordinates_or_key():
    w, sid, fm = claude_flow(resp(tool_use("propose_target", GOOD)))
    w.hear(sid, OPENING)
    body = json.loads(fm.calls[0]["messages"][0]["content"])
    assert set(body) == {"utterance", "place_word", "available_positions", "instructions"}
    assert body["place_word"] == "置くところ" and body["utterance"] == OPENING
    assert {"line": 30, "text": "MOVL C00005 V=50.0 PL=0"} in body["instructions"]
    assert {"line": 32, "text": "DOUT OT#(1) OFF"} in body["instructions"]
    whole = json.dumps(fm.calls[0], ensure_ascii=False)
    for secret in (KEY, "sk-ant", "C00005=", "400.000", "150.000", "-150.000", "//POS", "NPOS", "RCONF", "RECTAN",
                   "//NAME", "DEMO_C_PLACE", "/JOB", "///DATE"):
        assert secret not in whole, secret


def test_locate_is_never_sent_for_non_synthetic_jobs():
    w, sid, fm = claude_flow(resp(tool_use("propose_target", GOOD)), data=NOT_SYNTH)
    r = w.hear(sid, OPENING)
    assert fm.calls == [] and dlg(r)["step"] == "pick"


def test_plain_moves_still_send_no_job_text():
    """「置くところ」の無いふつうの指示では、今どおりジョブの中身を送らない。"""
    fm = FakeMessages(resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": 2})))
    w, sid = start(ci.ClaudeInterpreter(KEY, client=NS(messages=fm)), select="C00004")
    w.hear(sid, "この位置を上に2mm")
    whole = json.dumps(fm.calls, ensure_ascii=False)
    assert "MOVL" not in whole and "instructions" not in whole


@pytest.mark.parametrize("response", [
    resp(tool_use("propose_target", GOOD), stop="max_tokens"),
    resp(tool_use("propose_target", GOOD), stop="end_turn"),
    resp(tool_use("propose_target", GOOD), stop="refusal"),
    resp(tool_use("propose_target", GOOD), tool_use("propose_target", GOOD)),
    resp(),
    resp(tool_use("ask_user", {"question": "どれ？"})),
    resp(tool_use("propose_position", {"position": "C00005", "direction": "上", "amount_mm": 3})),
    resp(tool_use("propose_target", {"candidates": GOOD["candidates"], "note": "置く位置"})),
    resp(tool_use("propose_target", {"candidates": [dict(GOOD["candidates"][0], why="置く")]})),
    resp(tool_use("propose_target", {"candidates": [GOOD["candidates"][0]] * 4})),
    resp(tool_use("propose_target", {"candidates": "C00005"})),
    resp(tool_use("propose_target", {"candidates": [{"position": "C00099", "evidence_lines": [30], "rank": 1}]})),
    resp(tool_use("propose_target", {"candidates": [{"position": "C00005", "evidence_lines": [3], "rank": 1}]})),
    NS(stop_reason="tool_use", content=None),
    "壊れた応答",
])
def test_bad_claude_locate_responses_fall_back_without_candidate(response):
    w, sid, fm = claude_flow(response)
    r = w.hear(sid, OPENING)
    assert dlg(r)["step"] == "pick" and dlg(r)["candidates"] == []
    assert r["state"]["candidate"] is None and r["state"]["selected"] == "C00001"
    assert "位置を選んでください" in r["message"]
    whole = json.dumps(r, ensure_ascii=False)
    assert "どれ？" not in whole and KEY not in whole


def test_claude_locate_network_error_falls_back_with_fixed_text():
    class APIConnectionError(Exception):
        pass
    w, sid, fm = claude_flow(exc=APIConnectionError("secret raw error " + KEY))
    r = w.hear(sid, OPENING)
    assert dlg(r)["step"] == "pick" and ci.NETWORK in r["message"] and KEY not in json.dumps(r, ensure_ascii=False)


# ======================================================================
# 8. 表示の文：プログラムが作る。断定の言葉・DOUT/TIMER の意味の断定を使わない。推定は保存しない
# ======================================================================
def test_evidence_notes_are_facts_only():
    assert dg.evidence_note("DOUT OT#(1) OFF") == "信号を切る"
    assert dg.evidence_note("DOUT OT#(1) ON") == "信号を入れる"
    assert dg.evidence_note("TIMER T=3.00") == "待つ"
    assert dg.evidence_note("MOVL C00005 V=50.0 PL=0") == "C00005 へ直線で動く"
    assert dg.evidence_note("NOP") == ""


def test_all_dialogue_texts_have_no_assertive_words():
    texts = [v for k, v in vars(dg).items() if k.isupper() and isinstance(v, str)]
    assert texts
    for t in texts:
        for w in ASSERTIVE:
            assert w not in t, (t, w)


def test_dialogue_responses_have_no_assertive_words():
    w, sid = start()
    out = [w.hear(sid, OPENING), w.hear(sid, "そうそう"), w.hear(sid, "3mm")]
    out.append(choose(w, sid, out[-1], "dir:Y-"))
    whole = json.dumps(out, ensure_ascii=False)
    for word in ASSERTIVE:
        assert word not in whole, word


def test_estimate_is_session_only_and_cleared_by_manual_select():
    w, sid = start()
    w.hear(sid, OPENING)
    r = w.hear(sid, "はい")
    assert dlg(r)["estimated"]["position"] == "C00005"
    r = w.select(sid, "C00003")
    assert dlg(r)["estimated"] is None
    # 新しく読み込んだ画面には残らない
    r2 = w.load(DEMO_C)
    assert r2["state"]["dialog"]["estimated"] is None and r2["state"]["dialog"]["turns"] == []


def test_mock_label_is_clear_in_state():
    w, sid = start()
    r = w.hear(sid, OPENING)
    assert dlg(r)["speaker"] == "モック（テスト用）"
    assert dlg(r)["locator"] == {"kind": "mock", "label": "モック（テスト用）"}


# ======================================================================
# 9. 案B（FRAME の label・aliases）のつなぎ口：一致すれば Claude を呼ばず、確定した名前として出す
# ======================================================================
def test_frame_lookup_hook():
    frame = {"positions": {"C00005": {"label": "置き場所", "aliases": ["置くところ", "置く位置"]},
                           "C00002": {"label": "取り場所", "aliases": ["取るところ"]}}}
    assert dg.frame_lookup(frame, "置くところ") == "C00005"
    assert dg.frame_lookup(frame, "置き場所") == "C00005"
    assert dg.frame_lookup(frame, "つかむところ") is None
    assert dg.frame_lookup(None, "置くところ") is None
    # 2つの位置に同じ別名があれば決めない
    frame["positions"]["C00004"] = {"label": "x", "aliases": ["置くところ"]}
    assert dg.frame_lookup(frame, "置くところ") is None


def test_frame_hook_in_flow_skips_claude(counting):
    w, sid = start(counting)
    w._entries[sid].frame = {"positions": {"C00005": {"label": "置き場所", "aliases": ["置くところ"]}}}
    r = w.hear(sid, OPENING)
    assert counting.locates == 0 and [c["position"] for c in dlg(r)["candidates"]] == ["C00005"]
    assert dlg(r)["candidates"][0]["confirmed_by_frame"] is True


# ======================================================================
# 10. place_ref など、別のブランチ（feat/nl-polish）の facts の項目があれば使い、無くても動く
# ======================================================================
def test_place_word_detection_with_and_without_facts_field():
    assert dg.find_place_word("置くところを少し手前にして") == "置くところ"
    assert dg.find_place_word("置き場所を3mm上げて") == "置き場所"
    assert dg.find_place_word("C00005を上に3mm") is None
    assert dg.find_place_word("なにか", NS(place_ref="離すところ")) == "離すところ"
    assert dg.find_place_word("なにか", NS(place_ref=None)) is None
    assert dg.find_relative_dir("置くところを少し手前にして") == "手前"
    assert dg.find_relative_dir("x", NS(relative_dir="奥")) == "奥"


@pytest.fixture
def no_approve_from_words(monkeypatch):
    from voice_teaching.web import flow as flow_mod

    def boom(*a, **k):
        raise AssertionError("指示の経路から承認が呼ばれました")
    monkeypatch.setattr(flow_mod.sim, "approve", boom)


# ======================================================================
# 11. HTTP（/api/dialog）
# ======================================================================
def test_http_dialog_route(tmp_path, monkeypatch):
    import http.client
    import threading
    from voice_teaching.web import server
    monkeypatch.chdir(tmp_path)
    srv = server.make_server(port=0, interpreter=ci.MockInterpreter())
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def post(path, obj=None, raw=None):
        conn = http.client.HTTPConnection(*srv.server_address[:2], timeout=5)
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/octet-stream" if raw is not None
                                                       else "application/json"})
        out = json.loads(conn.getresponse().read())
        conn.close()
        return out
    try:
        sid = post("/api/load", raw=DEMO_C)["session_id"]
        r = post("/api/hear", {"session_id": sid, "text": OPENING, "source": "text"})
        r = post("/api/dialog", {"session_id": sid, "choice": "pos:C00005", "turn": dlg(r)["turn"]})
        assert r["state"]["selected"] == "C00005" and dlg(r)["step"] == "ask_move"
        r = post("/api/hear", {"session_id": sid, "text": "3mm", "source": "text"})
        r = post("/api/dialog", {"session_id": sid, "choice": "dir:Y-", "turn": dlg(r)["turn"]})
        assert r["candidate_made"] and r["state"]["approval"] is None
        r = post("/api/dialog", {"session_id": sid, "choice": "approve", "turn": dlg(r)["turn"]})
        assert r["status"] == "stop" and r["state"]["approval"] is None
    finally:
        srv.shutdown()
        srv.server_close()


# ======================================================================
# 12. 画面（JS・HTML）：会話のボタンは /api/dialog だけ。承認は呼ばない。文はテキストのノード
# ======================================================================
STATIC = ROOT / "voice_teaching" / "web" / "static"


def test_dialog_ui_only_posts_choices_and_never_approves():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert js.count('"/api/dialog"') == 1
    part = js[js.index("function renderDialog()"):js.index("// ---- 4. 変更候補")]
    for w in ("/api/approve", "/api/export", "innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert w not in part, w
    assert "モック（テスト用）" in part  # モックのときは、本物の Claude ではないと出す
    for i in ("dialog-panel", "dialog-turns", "dialog-evidence", "dialog-choices", "dialog-engine", "talk-first"):
        assert f'id="{i}"' in html, i
    for w in ASSERTIVE:
        assert w not in html.split('id="dialog-panel"')[1].split("</section>")[0], w


# ======================================================================
# 13. 話し手の表示は正直に（2026-11-03 夜 リードの決定）：Claude（モック）と出すのは、位置の候補を示す文だけ。
#     プログラムの決まった文（方向と量の問い・「変更前後を表示します」・照合や手動の選択の案内など）は「アプリ」
# ======================================================================
def _ai_turns(state):
    return [t for t in state["dialog"]["turns"] if t["who"] in ("Claude", dg.MOCK_SPEAKER)]


def _is_locate_proposal(text):
    import re
    return bool(re.fullmatch(r"「.+」は C\d{5} のことでしょうか？", text) or re.fullmatch(r"「.+」の候補が \d+つあります。どれですか？", text))


@pytest.mark.parametrize("interp", ["mock", "claude"])
def test_template_turns_are_never_labelled_claude(interp):
    if interp == "mock":
        w, sid = start(select="C00001")
    else:
        w, sid, _ = claude_flow(resp(tool_use("propose_target", GOOD)))  # 方向のボタンの前まで（偽の応答は1つだけ）
    states = []
    r = w.hear(sid, OPENING); states.append(r["state"])
    r = w.hear(sid, "OK"); states.append(r["state"])           # 承認の言葉（止める）
    r = w.hear(sid, "そうじゃなくて"); states.append(r["state"])  # 打ち消し
    r = w.hear(sid, "えーと"); states.append(r["state"])        # その他
    r = w.hear(sid, "そうそう"); states.append(r["state"])  # ほかのやり取りのあとの肯定は使えない（P3a）→ ボタンで
    r = w.choose(sid, "pos:C00005", r["state"]["dialog"]["turn"]); states.append(r["state"])
    r = w.hear(sid, "手前に3mm"); states.append(r["state"])
    r = w.hear(sid, "3mm"); states.append(r["state"])
    if interp == "mock":
        r = choose(w, sid, r, "dir:Y-"); states.append(r["state"])
        assert r["state"]["candidate"] is not None
    final = states[-1]
    ai = _ai_turns(final)
    assert ai, "位置の候補の文は Claude（モック）として出す"
    for t in ai:
        assert _is_locate_proposal(t["text"]), t
    texts_by_app = [t["text"] for t in final["dialog"]["turns"] if t["who"] == "アプリ"]
    assert any(x.startswith("どちらの方向") for x in texts_by_app)
    if interp == "mock":
        assert final["dialog"]["turns"][-1] == {"who": "アプリ", "text": dg.DONE, "kind": "ok"}


def test_fallback_and_pick_guidance_are_app():
    w, sid = start(FixedLocator([]), select="C00001")
    r = w.hear(sid, OPENING)
    assert _ai_turns(r["state"]) == [] and r["state"]["dialog"]["turns"][-1]["who"] == "アプリ"
    w, sid = start(data=NOT_SYNTH)
    r = w.hear(sid, OPENING)
    assert _ai_turns(r["state"]) == []


def test_frame_confirmed_candidate_is_app(counting):
    w, sid = start(counting)
    w._entries[sid].frame = {"positions": {"C00005": {"label": "置き場所", "aliases": ["置くところ"]}}}
    r = w.hear(sid, OPENING)
    assert _ai_turns(r["state"]) == []


def test_ui_labels_bubbles_from_server_who_only():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    part = js[js.index("function renderDialog()"):js.index("// ---- 4. 変更候補")]
    assert 't.who' in part and '"Claude"' not in part  # 画面で話し手を決めない（サーバーの who をそのまま出す）
