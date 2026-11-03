"""2026-11-03 夜の唐川さんの決定（docs/UNKNOWN_WORDS_PLAN.md 7章）。実装より先に書いた。

1. 疑問の形（「3mm下げて？」「下に3mmですか」）は候補にしない。「「…」でよいですか？」と、？を外した同じ指示の選択肢を1つだけ出す
2. 「下げます」、読んだ指示の後の文の終わりの「です」、ひらがなの「さげて」「あげて」を読む
   （「動かします」「移動します」は CHECKER_026 M3（2026-11-03 13:27）で取り消し：候補にせず2択で確かめる。tests/test_c026_m3.py）
5. 繰り返し・往復（「2回移動させて」「2往復」「行き来」「繰り返し」）は候補にしない。最後は必ず止める
6. 「置くところ」などの場所の言葉は facts.place_ref。位置番号が無ければ聞き返す
7. 手前・奥・前・後ろ・右・左は今までどおり止める・聞き返す。facts.relative_dir に出す
8. 「少し」「ちょっと」はあいまいな量のまま
"""
from pathlib import Path

import pytest

from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()

# 上下の向き（CHECKER_026 M2）：このファイルは、発話の読み取り（「上・下・上げ・下げ」を Z± として読むことを含む）を
# 確かめるテスト。上・下を Z にするのは上下の向きの照合に通ったときだけなので、ファイルの全テストに
# 一時フォルダの確認済みの設定（DEMO_C_PLACE。conftest.updown_demo_c）を当てる。設定が無いときの動きは tests/test_updown.py
pytestmark = pytest.mark.usefixtures("updown_demo_c")
SOURCES = ["voice", "text"]
REPEAT_STOP = ("往復や、範囲の決まらない繰り返しは、このアプリでは扱いません。"  # 回数の変更と食い違わない文（QA P1-1）
               "回数を変えるときは「C00001からC00003の作業を2回から3回にして」のように、範囲を言ってください")


def new():
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return w, sid


def hear(text, source="voice"):
    w, sid = new()
    return w.hear(sid, text, source=source)


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"].split()[1], c["delta"]) if c else None


# ---- 1. 疑問の形 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,plain", [
    ("3mm下げて？", "3mm下げて"), ("3mm下げて?", "3mm下げて"), ("下に3mmですか", "下に3mmです"),
    ("Y座標を3mm下げますか", "Y座標を3mm下げて"),  # CHECKER_030 S30-2：「〜ます」も外して確かめる
    ("下に3mm？", "下に3mm"), ("下に3mmですか？", "下に3mmです"),
])
def test_question_asks_for_confirmation_with_one_choice(text, plain, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))
    assert r["message"] == f"「{plain}」でよいですか？"
    assert len(r["choices"]) == 1
    choice = r["choices"][0]["text"]
    assert "？" not in choice and "?" not in choice and not choice.endswith("か")
    f = ut.read_utterance(choice)
    assert f.unknown_words == () and f.negations == () and not f.question
    # 押すと、その文を指示として送る。ふつうの流れ（候補→確認表示→承認ボタン）。承認はされない
    r2 = w.hear(sid, choice, source="text")
    assert r2["status"] == "ok" and cand(r2) is not None and r2["state"]["approval"] is None


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["下げないの？", "3mm下げないの？", "Y座標を3mm下げないですか", "下に3mmはだめ？"])
def test_negated_question_is_a_negation_stop(text, source):
    r = hear(text, source)
    assert r["status"] == "stop" and cand(r) is None and r["choices"] == [] and "変更しません" in r["message"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["バイオ3mm下げて？", "バイオを下に3mmですか", "比較バイオして？", "ワイざひょうを3mm下げて？"])
def test_question_with_unknown_words_has_no_choice(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == []
    assert "分かりませんでした" in r["message"]


@pytest.mark.parametrize("source", SOURCES)
def test_question_that_needs_other_question_keeps_program_choices(source):
    # 「Yを3mm下げて？」は D1 の2択（プログラムが作る選択肢）。候補にはしない
    r = hear("Yを3mm下げて？", source)
    assert r["status"] == "ask" and cand(r) is None
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm", "下方向へ 3mm"]


def test_question_facts():
    f = ut.read_utterance("3mm下げて？")
    assert f.question is True and f.question_text == "3mm下げて"
    f = ut.read_utterance("3mm下げて")
    assert f.question is False and f.question_text is None


# ---- 2. 言い終わり・ひらがなの上げ下げ ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,want", [
    # 「3mm下げます」は CHECKER_030 S30-2（2026-11-03 17:14）で2択に変えた（下の test_s_p3_3_single_endings_still_work）
    ("下に3ミリです", ("Z", "-3.000")), ("Xのマイナス5ミリです", ("X", "-5.000")),
    ("3mmさげて", ("Z", "-3.000")), ("3mmあげて", ("Z", "+3.000")), ("Y座標を 3 ミリ さげて", ("Y", "-3.000")),
    ("Y座標を3mmあげてください", ("Y", "+3.000")), ("えーとさげて3mm", ("Z", "-3.000")),
])
def test_allowed_endings_and_hiragana_verbs(text, want, source):
    r = hear(text, source)
    assert r["status"] == "ok" and cand(r) == want, (text, r.get("message"))


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,status", [
    ("さげないで", "stop"), ("3mmさげないで", "stop"), ("下に3mm動かしません", "stop"), ("3mm下げませんです", "stop"),
    ("3mmですか", "ask"), ("さげてはだめ", "stop"), ("3mmさげてはだめ", "stop"), ("3mmあげないで", "stop"),
    ("3mmです", "ask"), ("バイオです下に3mm", "ask"), ("バイオ3mmです", "ask"), ("下にですバイオ3mm", "ask"),
    ("ささげて3mm", "ask"), ("ですです下に3mm", "ask"),
])
def test_endings_do_not_open_holes(text, status, source):
    r = hear(text, source)
    assert r["status"] == status and cand(r) is None, (text, r["status"], r.get("message"))


def test_hiragana_verb_is_read_without_guessing_an_axis():
    # 「さげて」は「下げて」と同じ。軸は足さない（Y を推測しない）
    f = ut.read_utterance("3mmさげて")
    assert f.axes == ("Z",) and f.vertical
    f = ut.read_utterance("ワイ 3mmさげて")
    assert ut.pre_check(f).status == "ask"  # D1 の2択（Y と上下）。片方に決めない


def test_wai_zahyou_still_asks_without_choices():
    r = hear("ワイざひょうを3mm下げて")
    assert r["status"] == "ask" and r["choices"] == [] and "「ざひょう」" in r["message"]


@pytest.mark.parametrize("text", ["キャンセルはやめて", "元に戻すのはやめて"])
def test_complex_negation_still_stops(text):
    assert hear(text)["status"] == "stop"


# ---- 5. 繰り返し・往復 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,count", [
    ("C00001からC00002を2回移動させて", 2), ("2回移動させて", 2), ("3回下に3mm", 3), ("行き来させて", None),
    ("繰り返し動かして", None), ("C00001とC00002を行き来", None),
])
def test_ambiguous_repeat_asks_with_two_program_choices_then_stops(text, count, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))
    assert len(r["choices"]) == 2
    n = f"{count}" if count else ""
    assert [c["label"] for c in r["choices"]] == ([f"{n}回の移動", f"{n}往復"] if count else ["繰り返しの移動", "往復"])
    for c in r["choices"]:
        w2, sid2 = new()
        w2.hear(sid2, text, source=source)
        r2 = w2.hear(sid2, c["text"], source="text")
        assert r2["status"] == "stop" and cand(r2) is None and r2["message"] == REPEAT_STOP, (c, r2.get("message"))


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["2往復", "C00001とC00002を2往復させて", "往復させて", "2回の移動", "繰り返しの移動",
                                  "2往復しないで"])
def test_explicit_repeat_stops(text, source):
    r = hear(text, source)
    assert r["status"] == "stop" and cand(r) is None and r["choices"] == []
    if "ないで" not in text:
        assert r["message"] == REPEAT_STOP


def test_repeat_facts():
    assert ut.read_utterance("2回移動させて").repeat == (2, "ambiguous")
    assert ut.read_utterance("2往復").repeat == (2, "round_trip")
    assert ut.read_utterance("2回の移動").repeat == (2, "times")
    assert ut.read_utterance("行き来させて").repeat == (None, "ambiguous")
    assert ut.read_utterance("下に3mm").repeat is None


# ---- 6. 場所の言葉 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("phrase", ["置くところ", "置き場所", "置く位置", "取るところ", "つかむところ", "離すところ"])
def test_place_ref_without_position_asks(phrase, source):
    # 発話の検査（pre_check）は今どおり聞き返す（位置は決めない）
    f = ut.read_utterance(f"{phrase}を下に3mm")
    assert f.place_ref == phrase and phrase not in f.unknown_words
    assert ut.pre_check(f).message == f"「{phrase}」がどの位置か決まっていません。位置を選んでください"
    # 位置を選んでいるときの文（nl-polish QA P3-4。選んでいる位置を自動では使わない）
    assert ut.pre_check(f, "C00005").message == (f"「{phrase}」がどの位置か決まっていません。選んでいる C00005 のことなら"
                                              "「C00005を下に3mm」と言うか、位置を選び直してください")
    # 画面の流れでは、feat/claude-dialog の会話の層がこの聞き返しを受け持つ：位置の候補を出して、人に選んでもらう。
    # 候補（変更）はまだ作らない。どの位置かは人がボタンか短い肯定で選ぶ（tests/test_dialog.py）
    r = hear(f"{phrase}を下に3mm", source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == []
    assert r["state"]["dialog"]["step"] in ("confirm_position", "pick")
    assert r["state"]["selected"] == "C00005"


def test_place_ref_with_position_number_is_confirmed_not_trusted():
    # Safety P3-4 で変えた：位置番号があっても、場所の言葉がその位置かは確かめる（候補にしない）
    r = hear("C00005の置くところを下に3mm")
    assert r["status"] == "ask" and cand(r) is None and [c["text"] for c in r["choices"]] == ["C00005を下に3mm"]
    assert ut.read_utterance("下に3mm").place_ref is None


def test_place_ref_with_unknown_word_still_asks_unknown():
    r = hear("置くところをバイオ3mm下げて")
    assert r["status"] == "ask" and "「バイオ」" in r["message"]


# ---- 7. 向きの言葉 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,word,status", [
    ("手前に3mm", "手前", "stop"), ("奥に3mm", "奥", "stop"), ("前に3mm", "前", "stop"), ("後ろに3mm", "後ろ", "stop"),
    ("右に3mm", "右", "ask"), ("左に3mm", "左", "ask"),
])
def test_relative_direction_is_exposed_but_never_mapped(text, word, status, source):
    r = hear(text, source)
    assert r["status"] == status and cand(r) is None
    assert ut.read_utterance(text).relative_dir == (word,)
    assert r["facts"]["relative_dir"] == [word]


# ---- 8. あいまいな量 ----
@pytest.mark.parametrize("text", ["少し下げて", "ちょっと下げて", "下に少し", "下にちょっと"])
def test_vague_amount_still_asks(text):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None and "量がはっきりしません" in r["message"]


def test_new_facts_are_in_the_response():
    r = hear("3mm下げて？")
    for k in ("question", "question_text", "repeat", "place_ref", "relative_dir"):
        assert k in r["facts"], k


# ==== 3b4cac1 の内部レビュー（Safety・QA）への修正。実装より先に書いた ====
PENDING = "承認待ちの変更候補があります"


def with_candidate(source):
    w, sid = new()
    r = w.hear(sid, "下に3mm", source=source)
    assert cand(r) == ("Z", "-3.000")
    return w, sid


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["5mm下げて？", "Y座標を5mm下げますか", "下に5mmですか"])
def test_s_p2_1_question_with_pending_candidate_returns_pending_message(text, source):
    w, sid = with_candidate(source)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "stop" and r["message"].startswith(PENDING) and r["choices"] == []
    assert cand(r) == ("Z", "-3.000")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,word", [("下に999999ミリ？", "上限"), ("下に20.001ミリ？", "上限"), ("下に0mm？", "0")])
def test_s_p3_1_question_that_would_stop_stops_now(text, word, source):
    r = hear(text, source)
    assert r["status"] == "stop" and cand(r) is None and r["choices"] == [] and word in r["message"], r.get("message")
    assert "でよいですか" not in r["message"]


@pytest.mark.parametrize("source", SOURCES)
def test_s_p3_1_question_within_limit_still_confirms(source):
    r = hear("下に20ミリ？", source)
    assert r["status"] == "ask" and r["message"] == "「下に20ミリ」でよいですか？" and len(r["choices"]) == 1


# ---- QA P2-1：変異 M2・M3・M7・M8 など ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["下にです3mm", "です下に3mm", "3mmです下げて", "3mm移動してあげて", "3mm動かしてさげて",
                                  "3mmしてあげて"])
def test_qa_p2_1_mid_desu_and_te_hiragana_verbs_ask(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("first", ["下げて？", "2回下げて"])
def test_qa_p2_1_answer_keeps_question_and_repeat(first, source):
    w, sid = new()
    r1 = w.hear(sid, first, source=source)
    assert r1["status"] == "ask"
    r2 = w.hear(sid, "3ミリ", source=source)
    assert r2["status"] in ("ask", "stop") and cand(r2) is None, r2.get("message")


# ---- QA P2-3：どの操作でも、疑問の形は同じように確かめる ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,plain", [("元に戻しますか", "元に戻します"), ("取り消しますか", "取り消します"),
                                        ("元に戻して？", "元に戻して"), ("比較して？", "比較して"),
                                        ("比較しますか", "比較します"), ("C00004を選択しますか", "C00004を選択します")])
def test_qa_p2_3_question_for_every_operation(text, plain, source):
    w, sid = with_candidate(source)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and r["message"] == f"「{plain}」でよいですか？" and len(r["choices"]) == 1
    assert r["tool_calls"] == [] and cand(r) == ("Z", "-3.000") and r["state"]["selected"] == "C00005"


def test_qa_p2_3_undo_question_choice_undoes():
    w, sid = with_candidate("voice")
    r = w.hear(sid, "元に戻しますか", source="voice")
    r2 = w.hear(sid, r["choices"][0]["text"], source="text")
    assert r2["status"] == "ok" and cand(r2) is None


@pytest.mark.parametrize("text", ["元に戻します", "下げします", "3mm下げします"])
def test_qa_p2_3_shimasu_is_not_allowed_outside(text):
    f = ut.read_utterance(text)
    if f.op == "move":
        assert ut.pre_check(f).status == "ask"


# ---- QA P2-4：疑問符のゆれ・文の途中の疑問符 ----
@pytest.mark.parametrize("mark", ["？", "?", "﹖", "⁇", "‽", "❓", "︖"])
def test_qa_p2_4_question_mark_variants(mark):
    r = hear(f"3mm下げて{mark}")
    assert r["status"] == "ask" and cand(r) is None and r["message"] == "「3mm下げて」でよいですか？"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["？3mm下げて", "3mm？下げて", "3mm下げて？ね", "下に⁇3mm", "下‽に3mm"])
def test_qa_p2_4_question_mark_in_the_middle(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))


# ---- Safety P3-2：示す言葉は聞こえたとおり ----
@pytest.mark.parametrize("text,shown", [("3mmさげません", "さげません"), ("3mmあげないで", "あげないで"),
                                        ("さげてはだめ", "だめ")])
def test_s_p3_2_shown_words_are_as_heard(text, shown):
    r = hear(text)
    assert r["status"] == "stop" and f"「{shown}」" in r["message"]
    assert "下げ" not in r["message"].split("など")[0] and "上げ" not in r["message"].split("など")[0]


def test_s_p3_2_unknown_shown_as_heard():
    r = hear("3mmさげてあげて")
    assert r["status"] == "ask" and "あげて」" in r["message"] and "上げ" not in r["message"].split("が分かりません")[0]


# ---- Safety P3-3・QA P3-2：言い終わりを重ねない ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["下に3mm動かしますです", "下に3mm移動しますです", "3mm下げるですか", "下に3mm移動しますね",
                                  "3mm下げします", "下に3mm動かしますね", "3mm下げますです", "3mm下げるです"])
def test_s_p3_3_no_stacked_endings(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))


def test_s_p3_3_single_endings_still_work():
    # CHECKER_030 S30-2（唐川さんの決定 2026-11-03 17:14）：「3mm下げます」は候補にせず、「〜して動かします」と同じ2択
    r = hear("3mm下げます")
    assert cand(r) is None and [c["label"] for c in r["choices"]] == ["変更したい", "説明です"]
    assert cand(hear("3mm下げる")) == ("Z", "-3.000")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["下に3mmずらします", "下に3mm動かします", "下に3mm移動します"])
def test_shimasu_endings_ask_two_choices(text, source):
    # CHECKER_026 M3（唐川さんの判断 2026-11-03 13:27）：言い終わりの「〜します」は候補にせず、2択で確かめる
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))
    assert [c["label"] for c in r["choices"]] == ["変更したい", "説明です"]


# ---- Safety P3-4：場所の言葉と位置番号 ----
@pytest.mark.parametrize("source", SOURCES)
def test_s_p3_4_place_ref_with_position_is_confirmed(source):
    w, sid = new()
    r = w.hear(sid, "C00006の置くところを下に3mm", source=source)
    assert r["status"] == "ask" and cand(r) is None
    assert r["message"] == "「置くところ」が C00006 かどうかは確かめていません。C00006 でよいですか？"
    assert [c["text"] for c in r["choices"]] == ["C00006を下に3mm"]
    r2 = w.hear(sid, r["choices"][0]["text"], source="text")
    assert r2["status"] == "ok" and r2["state"]["candidate"]["target"] == "C00006 Z"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["C00005を置くところに3mm下げて", "C00005を置き場所へ3mm", "C00005を置く位置まで下げて"])
def test_s_p3_4_place_as_destination_asks_without_choice(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))


# ---- Safety P3-7・QA P3-1：繰り返しは2回以上・往復・行き来・繰り返しだけ ----
@pytest.mark.parametrize("text", ["2回下げて", "２回下げて", "3回下に3mm"])
def test_repeat_counts_two_or_more(text):
    r = hear(text)
    assert r["status"] == "ask" and len(r["choices"]) == 2


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["2回転させて", "2回目を下に3mm", "1回下げて", "もう1回下に3mm", "1回の移動"])
def test_not_repeat_is_unknown(text, source):
    r = hear(text, source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    assert ut.read_utterance(text).repeat is None


# ---- QA P3-4：選んでいる位置があるときの場所の言葉の文 ----
def test_qa_p3_4_place_ref_message_mentions_selection():
    # 発話の検査（pre_check）の文。画面の流れでは feat/claude-dialog の会話の層が受け持つ（下）
    f = ut.read_utterance("置くところを下に3mm")
    v = ut.pre_check(f, "C00005")
    assert v.status == "ask" and v.choices == ()
    assert v.message == ("「置くところ」がどの位置か決まっていません。選んでいる C00005 のことなら"
                         "「C00005を下に3mm」と言うか、位置を選び直してください")
    r = hear("置くところを下に3mm")  # 会話の層：位置の候補を出す。選んでいる位置を自動では使わない・候補（変更）は作らない
    assert r["status"] == "ask" and cand(r) is None and r["state"]["selected"] == "C00005"
    assert r["state"]["dialog"]["step"] in ("confirm_position", "pick")


def test_qa_p3_4_place_ref_message_without_selection():
    f = ut.read_utterance("置くところを下に3mm")
    assert ut.pre_check(f).message == "「置くところ」がどの位置か決まっていません。位置を選んでください"
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    r = w.hear(sid, "置くところを下に3mm")  # 会話の層：位置の候補の確認（人が選ぶまで候補は作らない）
    assert r["status"] == "ask" and r["state"]["candidate"] is None and r["state"]["selected"] is None


@pytest.mark.parametrize("text,want", [("下に3mm です", ("Z", "-3.000")), ("下に3ミリ です", ("Z", "-3.000"))])
def test_desu_after_amount_with_space(text, want):
    assert cand(hear(text)) == want


@pytest.mark.parametrize("text", ["3mm下げます ね", "下に3mm動かします です"])
def test_no_stacking_with_space(text):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None


@pytest.mark.parametrize("text", ["3mm下にです", "Y座標をです3mm下げて", "下げてです3mm"])
def test_desu_only_right_after_an_amount(text):
    # 「です」は量のすぐ後の文の終わりだけ（読んだ言葉の後でも、量の後でなければ聞き返す）
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))


# ---- 対話のブランチの Safety 再レビュー：場所の言葉を行き先にした指示は、量・向きを聞く前に断る ----
DEST_MSG = "「{p}」へ動かす指示は扱いません"


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("text,place", [
    ("置くところへ動かして", "置くところ"), ("置くところに動かして", "置くところ"), ("置き場所へ移動", "置き場所"),
    ("置き場所まで動かして", "置き場所"), ("置くところへ3mm動かして", "置くところ"), ("置く位置まで3mm移動して", "置く位置"),
    ("置くところに下に3mm", "置くところ"),
])
def test_place_as_destination_is_refused_before_amount_questions(text, place, selected, source):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    if selected:
        w.select(sid, "C00004")
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    assert r["message"].startswith(DEST_MSG.format(p=place)), r["message"]
    assert "何mm" not in r["message"]
    # 続けて「下に3mm」と言っても、行き先の続きとしては読まない（新しい指示。量の答えにしない）
    r2 = w.hear(sid, "3mm", source=source)
    assert cand(r2) is None


@pytest.mark.parametrize("text", ["置き場所の置くところを下に3mm", "置くところを手前手前に", "置くところを手前の奥に3mm",
                                  "置き場所の置くところに3mm", "C00005の置くところへ動かして"])
def test_odd_place_sentences_make_no_candidate(text):
    for source in SOURCES:
        r = hear(text, source)
        assert r["status"] in ("ask", "stop") and cand(r) is None, (text, r.get("message"))
