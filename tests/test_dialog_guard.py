"""受入テスト：会話の始めの発話の扱い（内部 Safety レビュー feat/claude-dialog 91285fe の FAIL・P1×4 と P3）。直す前に書いた。

決まり：会話を始める・続けてよいのは、場所の言葉を確かめた位置に置き換えた文が、ふつうの pre_check で
通るか、会話が受け持つ理由（位置が無い・方向が無い・量が無い・向きの言葉だけ）で聞き返されるときだけ。
ほかの止める・聞き返す理由は、そのまま返す（会話にしない）。「はい」1つで候補にならない。
- P1-1 向きの言葉（手前・奥・前・後ろ・右・左）を捨てない。軸・上げ下げと一緒なら会話にしない。
  向きの言葉だけなら、必ず6つの方向のボタンで聞く（始めの発話から方向を決めない）。「置くところの手前」は会話にしない
- P1-2 場所の言葉が2つ → 会話にしない。「位置が2つあります。1つずつ指示してください」
- P1-3 符号の重なり・はぐれた符号 → ふつうの流れと同じく聞き返す（ボタンを符号で絞らない）
- P1-4 行き先の場所の言葉（「置くところに」）→ 会話にしない（nl-polish の聞き返し。選択は変えない）
- P3 (a) 声の短い肯定は、最後のやり取りが位置の候補の問いのときだけ (b) 手で点を選んだら、すぐ候補にせず
  「C00005 を Y −3mm 動かしますか？」とボタン1つで確かめる (c) 0mm は、解釈の係を呼ぶ前に断る
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SOURCES = ["voice", "text"]
CORPUS = [l for l in (Path(__file__).parent / "dialog_stop_corpus.txt").read_text(encoding="utf-8").splitlines() if l]


class Counting:
    def __init__(self):
        self.inner = ci.MockInterpreter()
        self.kind, self.model, self.notice = "mock", None, self.inner.notice
        self.locator_label = self.inner.locator_label
        self.decides = self.locates = 0

    def decide(self, *a, **k):
        self.decides += 1
        return self.inner.decide(*a, **k)

    def locate(self, *a, **k):
        self.locates += 1
        return self.inner.locate(*a, **k)


def start(select=None, interp=None):
    w = WebFlow(interpreter=interp or ci.MockInterpreter())
    sid = w.load(DEMO_C)["session_id"]
    if select:
        w.select(sid, select)
    return w, sid


def dlg(r):
    return r["state"]["dialog"]


def no_candidate_after_yes(text, source, select=None):
    w, sid = start(select)
    r1 = w.hear(sid, text, source=source)
    r2 = w.hear(sid, "はい", source=source)
    return r1, r2


# ---- P1-1 向きの言葉 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["置くところの手前を3mm下げて", "置くところを奥へ3mm下げて", "置くところを右に下げて3mm",
                                  "置くところの後ろを3mm上げて", "置くところを手前にYのマイナス3mm", "置くところを左にマイナス3mm",
                                  "置くところの手前", "置くところの右を3mm", "置くところを前にZのプラス3ミリ"])
def test_p1_1_relative_with_axis_or_neighbour_is_not_a_dialogue(text, source):
    r1, r2 = no_candidate_after_yes(text, source, select="C00001")
    assert r1["status"] in ("ask", "stop") and dlg(r1)["step"] is None
    assert r2["state"]["candidate"] is None and r2["state"]["selected"] == "C00001"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["置くところを少し手前にして", "置くところを3mm手前に動かして", "置くところを右に3mm"])
def test_p1_1_relative_alone_always_asks_six_buttons(text, source):
    w, sid = start()
    r = w.hear(sid, text, source=source)
    assert dlg(r)["step"] == "confirm_position"
    r = w.hear(sid, "はい", source=source)
    assert r["state"]["candidate"] is None and dlg(r)["step"] == "ask_move"
    assert [c["id"] for c in dlg(r)["choices"]] == ["dir:X+", "dir:X-", "dir:Y+", "dir:Y-", "dir:Z+", "dir:Z-"]
    assert dlg(r)["relative"] and "がX・Y・Z" not in r["message"] and dlg(r)["relative"] in r["message"]


# ---- P1-2 場所の言葉が2つ ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["置くところと取るところを3mm下げて", "置くところも取るところも3mm下げて",
                                  "置くところを3mm下げて、取るところも", "置き場所と置くところを下に3mm",
                                  "置くところを3mm下げて取るところを2mm上げて"])
def test_p1_2_two_place_words_ask_one_at_a_time(text, source, ):
    c = Counting()
    w, sid = start(interp=c)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and r["message"] == dg.TWO_PLACES and dlg(r)["step"] is None and c.locates == 0
    r = w.hear(sid, "はい", source=source)
    assert r["state"]["candidate"] is None


# ---- P1-3 符号 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["置くところをYのマイナスマイナス3mm", "置くところをYのマイナス3mmマイナス",
                                  "置くところをマイナス3mmプラス", "置くところをY-方向にマイナス3mm"])
def test_p1_3_repeated_or_stray_sign_in_opening_asks_like_normal(text, source):
    r1, r2 = no_candidate_after_yes(text, source)
    assert r1["status"] == "ask" and dlg(r1)["step"] is None
    assert r2["state"]["candidate"] is None


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("answer", ["Yのマイナス3mmマイナス", "Yのマイナスマイナス3mm", "マイナスマイナス3mm",
                                    "Y-方向にマイナス3mm", "プラス3mmマイナス"])
def test_p1_3_repeated_or_stray_sign_in_answer_asks_and_does_not_filter(answer, source):
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして", source=source)
    w.hear(sid, "はい", source=source)
    r = w.hear(sid, answer, source=source)
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    assert dlg(r)["amount_mm"] is None and dlg(r)["direction"] is None
    assert len(dlg(r)["choices"]) == 6


def test_p1_3_sign_alone_answer_does_not_filter_buttons():
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    r = w.hear(sid, "マイナス3mm")
    assert r["state"]["candidate"] is None and len(dlg(r)["choices"]) == 6


# ---- P1-4 行き先 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["置くところに3mm下げて", "置くところへ動かして", "置き場所まで動かして", "置くところに移動して"])
def test_p1_4_destination_is_not_a_dialogue(text, source):
    c = Counting()
    w, sid = start(select="C00001", interp=c)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and r["choices"] == [] and dlg(r)["step"] is None and c.locates == 0
    from voice_teaching import utterance as ut
    assert r["message"] == ut.pre_check(ut.read_utterance(text), "C00001").message  # nl-polish の聞き返しのまま
    r = w.hear(sid, "はい", source=source)
    assert r["state"]["candidate"] is None and r["state"]["selected"] == "C00001"


# ---- 決まり：ふつうの流れで止まる・聞き返す文は、会話にしても「はい」で候補にならない ----
@pytest.mark.parametrize("prefix", ["置くところを", "置くところ", "取るところを"])
@pytest.mark.parametrize("text", CORPUS)
def test_property_stop_sentences_never_become_candidate_via_dialogue(prefix, text):
    w, sid = start(select="C00001")
    w.hear(sid, prefix + text)
    r = w.hear(sid, "はい")
    assert r["state"]["candidate"] is None, (prefix + text)
    r = w.hear(sid, "そうそう")
    assert r["state"]["candidate"] is None, (prefix + text)


@pytest.mark.parametrize("text", CORPUS)
def test_property_stop_sentences_as_answers_never_become_candidate(text):
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    r = w.hear(sid, text)
    assert r["state"]["candidate"] is None, text


# ---- P3 (a) 声の肯定は、最後のやり取りが位置の候補の問いのときだけ ----
def test_p3a_affirmative_only_right_after_the_proposal():
    w, sid = start(select="C00001")
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "えーと")  # ほかのやり取りが挟まった
    assert dlg(r)["step"] == "confirm_position"
    r = w.hear(sid, "はい")
    assert r["state"]["selected"] == "C00001" and dlg(r)["step"] == "confirm_position"
    assert r["message"] == dg.AFFIRM_BUTTON
    r = w.choose(sid, "pos:C00005", dlg(r)["turn"])  # ボタンなら選べる
    assert r["state"]["selected"] == "C00005"


# ---- P3 (b) 手で点を選んだら、すぐ候補にせず、ボタン1つで確かめる ----
def test_p3b_manual_pick_asks_confirmation_before_candidate():
    c = Counting()
    w, sid = start(interp=c)
    r = w.hear(sid, "置くところをYのマイナス3mm")
    r = w.choose(sid, "other", dlg(r)["turn"])
    assert dlg(r)["step"] == "pick"
    r = w.select(sid, "C00004")
    assert r["state"]["candidate"] is None and c.decides == 0
    assert dlg(r)["step"] == "ask_move" and r["message"] == "C00004 を Y −3mm 動かしますか？"
    assert [x["id"] for x in dlg(r)["choices"]] == ["go"]
    r = w.hear(sid, "はい")  # 声では進めない（承認の言葉として断る）
    assert r["state"]["candidate"] is None
    r = w.choose(sid, "go", dlg(r)["turn"])
    assert r["candidate_made"] and r["state"]["candidate"]["target"] == "C00004 Y"


# ---- P3 (c) 0mm は解釈の係を呼ぶ前に断る ----
@pytest.mark.parametrize("text", ["0mm", "0ミリ", "Yのマイナス0mm", "0.0mm"])
def test_p3c_zero_amount_in_answer_is_refused(text):
    c = Counting()
    w, sid = start(interp=c)
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["message"] == dg.ZERO_AMOUNT and c.decides == 0
    assert dlg(r)["amount_mm"] is None


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_p3c_zero_amount_in_opening_is_refused():
    c = Counting()
    w, sid = start(interp=c)
    r = w.hear(sid, "置くところを0mm下げて")
    assert r["status"] == "stop" and r["message"] == dg.ZERO_AMOUNT and c.locates == 0 and dlg(r)["step"] is None


# ======================================================================
# 内部 QA レビュー（91285fe）FAIL の直し
# ======================================================================
# P1-1：会話の中の止めた・聞き返しの理由を、いつも返す（画面は「アプリ」の吹き出しと返事の欄に出す。e2e で確かめる）
# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
@pytest.mark.parametrize("text,status", [("置くところをバイオ3ミリ下げて", "ask"), ("置くところを0mm下げて", "stop"),
                                         ("置くところと取るところを3mm下げて", "ask")])
def test_qa_p1_1_opening_refusals_carry_message_and_app_turn(text, status):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == status and r["message"] and dlg(r)["step"] is None
    if r["dialog"]:  # 会話の層が答えたときは「アプリ」の吹き出しにも残す（ふつうの流れの答えは返事の欄）
        assert dlg(r)["turns"][-1] == {"who": "アプリ", "text": r["message"], "kind": status}


def test_qa_p1_1_guard_refusal_at_end_is_returned():
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    w.hear(sid, "25mm")
    r = w.choose(sid, "dir:Z-", dlg(w.state(sid))["turn"])
    assert r["state"]["can_approve"] is False
    assert "20" in (r["message"] + str(r["state"]["blocked_reason"]))


def test_qa_p1_1_undo_word_message_is_returned():
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "やめて")
    assert r["message"] == dg.END_UNDO and dlg(r)["turns"][-1]["who"] == "アプリ"


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
# P2-1：推定（未確定）は、元に戻す・ふつうの指示・会話の終わり（候補なし）・新しいジョブで消す
def test_qa_p2_1_estimate_cleared_on_undo_and_normal_instruction():
    w, sid = start()
    w.hear(sid, "置くところを3mm下げて")
    r = w.hear(sid, "はい")
    assert r["state"]["candidate"]["estimated"] is not None
    r = w.undo(sid)
    assert dlg(r)["estimated"] is None
    r = w.hear(sid, "C00005を上に2mm")
    assert r["state"]["candidate"]["estimated"] is None and dlg(r)["estimated"] is None


def test_qa_p2_1_estimate_cleared_when_dialogue_ends_without_candidate():
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "はい")
    assert dlg(r)["estimated"] is not None
    r = w.hear(sid, "やめて")
    assert dlg(r)["estimated"] is None


# P3-1：古い選択肢：文を出し、state も返す
def test_qa_p3_1_stale_choice_has_message_and_state():
    w, sid = start()
    r0 = w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    r = w.choose(sid, "pos:C00005", dlg(r0)["turn"])
    assert r["status"] == "stop" and r["message"] == dg.STALE == "この選択肢は古くなりました。もう一度話してください"
    assert "state" in r and r["state"]["dialog"]["step"] == "ask_move"


# P3-4：手で選ぶ段での文字：位置番号なら選んだことにする。それ以外は段を変えずに聞く（集めた量・方向を捨てない）
def test_qa_p3_4_typing_in_pick_step():
    w, sid = start()
    r = w.hear(sid, "置くところをYのマイナス3mm")
    r = w.choose(sid, "other", dlg(r)["turn"])
    r = w.hear(sid, "えーと")
    assert dlg(r)["step"] == "pick" and r["status"] == "ask" and r["message"] == dg.PICK_PROMPT
    r = w.hear(sid, "C00004")
    assert r["state"]["selected"] == "C00004" and dlg(r)["step"] == "ask_move"
    assert dlg(r)["amount_mm"] == 3.0 and dlg(r)["direction"] == "Y-" and r["state"]["candidate"] is None
    assert [c["id"] for c in dlg(r)["choices"]] == ["go"]


# P3-7：モックのときの注意書き（画面の文）
def test_qa_p3_7_note_mentions_mock():
    js = (ROOT / "voice_teaching" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "モック（テスト用）は、決まった規則で候補を出し" in js


# P3-5：会話のカードは、入力欄より前（DOM の順）
def test_qa_p3_5_dialog_card_before_input_in_dom():
    html = (ROOT / "voice_teaching" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert html.index('id="dialog-panel"') < html.index('id="instruction-panel"')


# ======================================================================
# 再レビュー（fd8ec24）の直し
# ======================================================================
def go_step():
    w, sid = start()
    r = w.hear(sid, "置くところをYのマイナス3mm")
    r = w.choose(sid, "other", dlg(r)["turn"])
    r = w.select(sid, "C00004")
    assert [c["id"] for c in dlg(r)["choices"]] == ["go"]
    return w, sid, r


# QA P2-A：［go］の段では、ボタンのほかは進めない（位置・軸・符号・量のそろった新しい指示だけは、会話をやめてふつうの流れへ）
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["3mm", "下に3mm", "Yのマイナス3mm", "はい", "そうそう"])
def test_qa_p2a_go_step_needs_the_button(text, source):
    w, sid, r = go_step()
    r = w.hear(sid, text, source=source)
    assert r["state"]["candidate"] is None and dlg(r)["step"] == "ask_move"
    assert [c["id"] for c in dlg(r)["choices"]] == ["go"]
    if text not in ("はい", "そうそう"):
        assert r["message"] == dg.GO_BUTTON


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
@pytest.mark.parametrize("source", SOURCES)
def test_qa_p2a_complete_new_instruction_ends_dialogue_and_says_so(source):
    w, sid, r = go_step()
    r = w.hear(sid, "C00004を下に3mm", source=source)
    assert r["candidate_made"] and r["state"]["candidate"]["target"] == "C00004 Z"
    assert dlg(r)["step"] is None and r["message"].startswith(dg.ENDED_NEW)
    assert dlg(r)["turns"][-1]["who"] == "アプリ" and dlg(r)["turns"][-1]["text"].startswith(dg.ENDED_NEW)


# QA P2-B：始めの関門（handled_by_dialogue）を固定する
def test_qa_p2b_gate_amount_only_starts_dialogue_and_asks_direction():
    w, sid = start()
    r = w.hear(sid, "置くところを3mm")
    assert r["status"] == "ask" and dlg(r)["step"] == "confirm_position"
    assert r["message"] == "「置くところ」は C00005 のことでしょうか？"
    r = w.hear(sid, "はい")
    assert dlg(r)["step"] == "ask_move" and dlg(r)["amount_mm"] == 3.0 and len(dlg(r)["choices"]) == 6
    assert r["message"] == dg.ASK_DIR.format(amount="3")


def test_qa_p2b_gate_place_word_alone_starts_dialogue_and_asks_both():
    w, sid = start()
    r = w.hear(sid, "置くところ")
    assert r["status"] == "ask" and dlg(r)["step"] == "confirm_position"
    r = w.hear(sid, "はい")
    assert dlg(r)["step"] == "ask_move" and r["message"] == dg.ASK_MOVE and len(dlg(r)["choices"]) == 6


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_qa_p2b_gate_d1_gives_two_readings_message():
    c = Counting()
    w, sid = start(select="C00001", interp=c)
    r = w.hear(sid, "置くところをYに3mm下げて")
    assert r["status"] == "ask" and dlg(r)["step"] is None and c.locates == 0
    assert r["message"].startswith("Y と、上げ・下げ（上下＝Z）の2つの読み方があります")
    assert r["choices"] == []  # 選択肢の文には場所の言葉が無い（別の位置に当てない）ので出さない
    assert r["state"]["selected"] == "C00001"


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
# QA P3-A：推定（未確定）は、断られた・聞き返された発話では消さない
@pytest.mark.parametrize("text", ["バイオ", "承認して", "そうじゃなくて", "置くところと取るところを3mm下げて"])
def test_qa_p3a_estimate_survives_refused_utterances(text):
    w, sid = start()
    w.hear(sid, "置くところを3mm下げて")
    r = w.hear(sid, "はい")
    assert r["state"]["candidate"]["estimated"] is not None
    r = w.hear(sid, text)
    assert r["state"]["candidate"] is not None and r["state"]["candidate"]["estimated"] is not None


def test_qa_p3a_estimate_survives_refusal_in_ask_move():
    w, sid = start()
    w.hear(sid, "置くところを少し手前にして")
    w.hear(sid, "はい")
    for t in ("バイオ", "OK", "下げないで"):
        r = w.hear(sid, t)
        assert dlg(r)["estimated"] is not None, t
