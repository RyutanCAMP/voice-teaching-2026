"""受入テスト：CHECKER_026 の M3・S-msg・S-pick（唐川さんの判断 2026-11-03 13:27）。実装より先に書いた。

M3（確定）
- 言い終わりの「〜します」（動かします・移動します・ずらします）は候補にしない。
  「3mm下げる変更をご希望ですか？ それとも操作の説明でしょうか？」と確かめ、選択肢は［変更したい］［説明です］。
- ［変更したい］：「〜します」を除いた指示の文を、ふつうの指示として送り直す（確認・検査・上限・確認表示・承認ボタンを全部通る）。
- ［説明です］：何もしない（候補を作らない。ジョブ・承認も変えない）。
- 打ち消し・知らない言葉があれば、選択肢を出さずに今どおり止める・聞き返す。
- 疑問の形「3mm下げて動かしますか？」は、今の疑問の扱い（候補にしない）に合わせる。
S-msg：承認したあとの新しい指示は「承認した変更候補があります…」（承認前は今の文のまま）。
S-pick：会話の途中の打ち消し（「ほかの位置」のボタンが見えているとき）は「分かりました。合っていなければ「ほかの位置」を押してください」。

上下（M2）と合わせたあと（fix/checker026）：このファイルの M3 のテストは、DEMO_C_PLACE の確認済みの上下の設定（一時フォルダ）を当てて動かす。
未確認のときの「〜します」は、下の test_unconfirmed_* で確かめる（2択は出すが、［変更したい］のあとは上下の案内で止まる）。
「下げて」の候補の有無そのものは、ここでは「ふつうの指示と同じ結果になる」ことで確かめる。
軸と符号をはっきり言った指示（「Yのマイナス3ミリ」）で、候補 → 確認表示 → 承認ボタンまでを確かめる。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import session as session_mod
from voice_teaching import utterance as ut
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SOURCES = ["voice", "text"]

pytestmark = pytest.mark.usefixtures("updown_demo_c")
LABELS = ["変更したい", "説明です"]
TAIL = "それとも操作の説明でしょうか？"
EXPLAIN_REPLY = "分かりました。変更はしません"
APPROVED_PENDING = "承認した変更候補があります。"
PICK_AFTER_NEGATION = "分かりました。合っていなければ「ほかの位置」を押してください"


def new(select="C00005"):
    w = WebFlow(interpreter=ci.MockInterpreter())
    sid = w.load(DEMO_C)["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"], c["delta"]) if c else None


def labels(r):
    return [c["label"] for c in r["choices"]]


def press(w, sid, r, label):
    c = next(c for c in r["choices"] if c["label"] == label)
    return w.hear(sid, c["text"], source="text")


@pytest.fixture
def no_approve_from_words(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("指示の経路から承認が呼ばれました")
    monkeypatch.setattr(flow_mod.sim, "approve", boom)


# ======================================================================
# 1. 「〜します」は2択の聞き返し。候補にしない
# ======================================================================
SHIMASU = [
    ("3mm下げて動かします", "3mm下げて", "3mm下げる変更をご希望ですか？"),
    ("下に3mm移動します", "下に3mm移動して", "3mm下げる変更をご希望ですか？"),
    ("Yのマイナス3ミリずらします", "Yのマイナス3ミリずらして", "Yのマイナス方向に3mm動かす変更をご希望ですか？"),
    ("下に3mm動かします。", "下に3mm動かして", "3mm下げる変更をご希望ですか？"),
    ("C00004を3mm上げて移動します", "C00004を3mm上げて", "C00004を3mm上げる変更をご希望ですか？"),
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,plain,head", SHIMASU)
def test_shimasu_asks_two_choices_and_makes_no_candidate(text, plain, head, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None, (text, r.get("message"))
    assert r["message"] == f"{head} {TAIL}"
    assert labels(r) == LABELS
    assert r["tool_calls"] == []  # 解釈の係（Claude）は呼ばない
    assert r["state"]["approval"] is None


@pytest.mark.parametrize("text,plain,head", SHIMASU)
def test_shimasu_facts_and_pre_check(text, plain, head):
    f = ut.read_utterance(text)
    assert f.shimasu_text == plain
    assert f.raw == text
    v = ut.pre_check(f)
    assert v.status == "ask" and [c[0] for c in v.choices] == LABELS
    assert v.choices[0][1] == plain


def test_shimasu_without_a_clear_phrase_uses_fixed_words():
    # 量が無いなど、言い直しの文を作れないときは「この変更を」
    w, sid = new()
    r = w.hear(sid, "動かします")
    assert r["status"] == "ask" and r["message"] == f"この変更をご希望ですか？ {TAIL}" and labels(r) == LABELS
    assert cand(r) is None


def test_shimasu_choice_keeps_the_position_of_the_question():
    # 選択肢の文には、聞き返したときの位置を入れる（あとで別の位置を選んでも、その位置に当てない）
    w, sid = new(select="C00005")
    r = w.hear(sid, "Yのマイナス3ミリずらします")
    assert r["choices"][0]["text"] == "C00005をYのマイナス3ミリずらして"
    assert w.select(sid, "C00004")["status"] == "ok"
    r2 = press(w, sid, r, "変更したい")
    assert r2["status"] == "ok" and cand(r2) == ("C00005 Y", "-3.000")


def test_plain_instructions_still_make_candidates():
    # 「〜して」の指示は今どおり（2択にしない）
    for text in ("Yのマイナス3ミリずらして", "Yのマイナス3ミリ移動して", "Yのマイナス3ミリ動かして"):
        w, sid = new()
        r = w.hear(sid, text)
        assert r["status"] == "ok" and cand(r) == ("C00005 Y", "-3.000"), text


# ======================================================================
# 2. ［変更したい］：ふつうの流れを全部通る。承認はボタンだけ
# ======================================================================
@pytest.mark.parametrize("source", SOURCES)
def test_want_change_goes_through_candidate_confirmation_and_button(source, no_approve_from_words, monkeypatch):
    w, sid = new()
    r = w.hear(sid, "Yのマイナス3ミリずらします", source=source)
    r2 = press(w, sid, r, "変更したい")
    assert r2["status"] == "ok" and r2["candidate_made"] and cand(r2) == ("C00005 Y", "-3.000")
    st = r2["state"]
    assert "C00005" in st["confirmation_text"] and "承認は画面のボタンで行います" in st["confirmation_text"]
    assert st["approval"] is None and st["can_approve"] is True and st["can_export"] is False
    # 声の「はい」では承認されない
    r3 = w.hear(sid, "はい", source="voice")
    assert r3["status"] == "stop" and r3["state"]["approval"] is None
    # 承認は画面のボタン（API の approve）だけ
    monkeypatch.undo()
    w2, sid2 = new()
    r = w2.hear(sid2, "Yのマイナス3ミリずらします", source=source)
    r2 = press(w2, sid2, r, "変更したい")
    a = w2.approve(sid2, "テスト太郎")
    assert a["ok"] is True and a["state"]["approval"]["approved_by"] == "テスト太郎"


@pytest.mark.parametrize("text", [t for t, _, _ in SHIMASU])
def test_want_change_gives_the_same_result_as_the_plain_instruction(text):
    # ［変更したい］は「〜します」を除いた文をふつうの指示として送るだけ（上下の扱い・検査・上限はふつうの指示と同じ）
    w, sid = new()
    r = w.hear(sid, text)
    r2 = press(w, sid, r, "変更したい")
    w0, sid0 = new()
    direct = w0.hear(sid0, r["choices"][0]["text"])
    assert (r2["status"], r2["message"], cand(r2), r2["state"]["approval"]) == \
        (direct["status"], direct["message"], cand(direct), direct["state"]["approval"])


def test_want_change_still_hits_the_limit():
    w, sid = new()
    r = w.hear(sid, "Yのマイナス25ミリずらします")
    assert r["status"] == "ask" and labels(r) == LABELS and cand(r) is None
    r2 = press(w, sid, r, "変更したい")
    assert r2["status"] == "stop" and cand(r2) is None and "上限" in r2["message"]


# ======================================================================
# 3. ［説明です］：何もしない
# ======================================================================
@pytest.mark.parametrize("source", SOURCES)
def test_explanation_does_nothing(source):
    w, sid = new()
    before = w.state(sid)["state"]
    r = w.hear(sid, "Yのマイナス3ミリずらします", source=source)
    r2 = press(w, sid, r, "説明です")
    assert r2["status"] == "ok" and r2["message"] == EXPLAIN_REPLY and not r2["candidate_made"]
    assert r2["choices"] == [] and r2["tool_calls"] == []
    st = r2["state"]
    assert st["candidate"] is None and st["approval"] is None and st["selected"] == before["selected"]
    assert w._entries[sid].session.original_bytes == DEMO_C


def test_explanation_keeps_an_existing_candidate_and_approval():
    # 古い［説明です］を、候補を作って承認したあとに押しても、候補・承認は変わらない
    w, sid = new()
    old = w.hear(sid, "Yのマイナス3ミリずらします")
    r = w.hear(sid, "Yのマイナス3ミリずらして")
    assert cand(r) == ("C00005 Y", "-3.000")
    assert w.approve(sid, "テスト太郎")["ok"] is True
    r2 = press(w, sid, old, "説明です")
    assert r2["status"] == "ok" and r2["message"] == EXPLAIN_REPLY
    assert cand(r2) == ("C00005 Y", "-3.000")
    assert r2["state"]["approval"]["approved_by"] == "テスト太郎" and r2["state"]["can_export"] is True


def test_explanation_in_session_alone():
    s = session_mod.Session.from_file(ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI")
    s.select("C00005")
    r = s.hear("Yのマイナス3ミリずらします")
    assert r.status == "ask" and [c[0] for c in r.choices] == LABELS
    assert r.choices[1][1] == ut.EXPLAIN_CHOICE_TEXT  # 位置番号を足さない
    r2 = s.hear(r.choices[1][1])
    assert r2.status == "ok" and r2.message == EXPLAIN_REPLY and s.candidate is None


def test_old_want_change_after_other_work_is_a_normal_instruction():
    # 古い［変更したい］を後で押しても、ふつうの指示として全部の検査を通る（承認待ちの候補があれば断る）
    w, sid = new()
    old = w.hear(sid, "Yのマイナス3ミリずらします")
    r = w.hear(sid, "Xのプラス2ミリ移動して")
    assert cand(r) == ("C00005 X", "+2.000")
    r2 = press(w, sid, old, "変更したい")
    assert r2["status"] == "stop" and r2["message"].startswith("承認待ちの変更候補があります")
    assert cand(r2) == ("C00005 X", "+2.000") and r2["tool_calls"] == []


def test_after_two_choices_an_answer_is_not_merged():
    # 2択のあとに「3mm」だけ言っても、前の発話と合わせて候補にしない（ボタンで選んでもらう）
    w, sid = new()
    r = w.hear(sid, "下に動かします")
    assert labels(r) == LABELS
    r2 = w.hear(sid, "3mm")
    assert r2["status"] == "ask" and cand(r2) is None


def test_shimasu_answer_after_amount_question_keeps_the_earlier_axis():
    # 「Y座標を下げて」→「何mm？」→「3mm下げて動かします」：前に言った軸（Y）を捨てた［変更したい］（「3mm下げて」）を出さない。
    # 今どおり、軸と上げ下げの2つの読み方として聞き返す（D1）
    w, sid = new()
    assert w.hear(sid, "Y座標を下げて")["message"] == "何mm動かしますか？"
    r = w.hear(sid, "3mm下げて動かします")
    assert r["status"] == "ask" and cand(r) is None
    assert "変更したい" not in labels(r) and TAIL not in r["message"]
    assert all("Y" in c["text"] or "上" in c["text"] or "下" in c["text"] for c in r["choices"])


def test_shimasu_with_pending_candidate_says_pending():
    w, sid = new()
    assert cand(w.hear(sid, "Xのプラス2ミリ移動して")) == ("C00005 X", "+2.000")
    r = w.hear(sid, "Yのマイナス3ミリずらします")
    assert r["status"] == "stop" and r["message"].startswith("承認待ちの変更候補があります") and r["choices"] == []


# ======================================================================
# 4. 打ち消し・知らない言葉・疑問の形
# ======================================================================
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["動かしません", "3mm下げて動かしません", "下に3mm移動しません", "Yのマイナス3ミリずらしません",
                                  "3mm下げないで動かします", "やっぱり違う3mm下げて動かします"])
def test_negation_stops_without_choices(text, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None, (text, r.get("message"))
    assert "打ち消し" in r["message"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["バイオ3mm下げて動かします", "下に3mmバイオ移動します", "Yのマイナス3ミリずらしますね",
                                  "下に3mm動かしますです", "3mm下げします"])
def test_unknown_words_ask_without_two_choices(text, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and r["choices"] == [] and cand(r) is None, (text, r.get("message"))
    assert TAIL not in r["message"]


def test_unknown_word_message_names_the_word():
    w, sid = new()
    r = w.hear(sid, "バイオ3mm下げて動かします")
    assert "「バイオ」" in r["message"] and TAIL not in r["message"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,plain", [("3mm下げて動かしますか？", "3mm下げて"), ("下に3mm移動しますか", "下に3mm移動して"),
                                        ("Yのマイナス3ミリずらしますか?", "Yのマイナス3ミリずらして")])
def test_question_form_follows_question_handling(text, plain, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None
    assert r["message"] == f"「{plain}」でよいですか？" and len(r["choices"]) == 1
    assert TAIL not in r["message"]


@pytest.mark.parametrize("text", ["2往復動かします", "承認して動かします", "起動して動かします"])
def test_other_stops_stay_stops(text):
    w, sid = new()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None, (text, r.get("message"))


def test_messages_have_no_forbidden_words():
    for text, _, _ in SHIMASU:
        w, sid = new()
        r = w.hear(sid, text)
        assert not ut.contains_forbidden(r["message"])
        assert not any(ut.contains_forbidden(c["label"]) for c in r["choices"])
    assert not ut.contains_forbidden(EXPLAIN_REPLY)
    assert not ut.contains_forbidden(session_mod.APPROVED_PENDING_MESSAGE)
    assert not ut.contains_forbidden(dg.PICK_AFTER_NEGATION)


def test_place_word_with_shimasu_asks_two_choices_before_the_dialogue():
    w, sid = new(select="C00001")
    r = w.hear(sid, "置くところを3mm下げて動かします")
    assert r["status"] == "ask" and labels(r) == LABELS and cand(r) is None
    assert r["state"]["dialog"]["step"] is None  # 会話（位置の候補）は、まだ始めない
    # ［変更したい］の文に、選んでいる位置（C00001）を足さない（置くところがどの位置かは、会話で人が選ぶ）
    assert r["choices"][0]["text"] == "置くところを3mm下げて"
    r2 = press(w, sid, r, "変更したい")
    assert r2["state"]["dialog"]["step"] in ("confirm_position", "pick") and cand(r2) is None
    assert r2["state"]["selected"] == "C00001"


def test_shimasu_in_dialogue_asks_two_choices_and_keeps_the_dialogue():
    w, sid = new(select="C00001")
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "そうそう")
    assert r["state"]["dialog"]["step"] == "ask_move"
    r = w.hear(sid, "Yのマイナス3ミリずらします")
    assert r["status"] == "ask" and labels(r) == LABELS and cand(r) is None
    assert r["state"]["dialog"]["step"] == "ask_move"
    r2 = press(w, sid, r, "説明です")
    assert r2["message"] == EXPLAIN_REPLY and cand(r2) is None and r2["state"]["dialog"]["step"] == "ask_move"
    r3 = press(w, sid, r, "変更したい")
    assert r3["status"] == "ok" and cand(r3) == ("C00005 Y", "-3.000") and r3["state"]["approval"] is None


# ======================================================================
# 5. S-msg：承認したあとの新しい指示
# ======================================================================
def test_s_msg_after_approval():
    w, sid = new()
    assert cand(w.hear(sid, "Xのプラス2ミリ移動して")) == ("C00005 X", "+2.000")
    assert w.approve(sid, "テスト太郎")["ok"] is True
    r = w.hear(sid, "Yのマイナス3ミリ移動して")
    assert r["status"] == "stop" and r["message"].startswith(APPROVED_PENDING)
    assert "出力するか、元に戻してから指示してください" in r["message"]
    assert "承認待ち" not in r["message"]
    assert r["tool_calls"] == [] and cand(r) == ("C00005 X", "+2.000")
    # 新しい指示があったので、承認は今どおり取り消す（文もそれを伝える）
    assert r["state"]["approval"] is None and "承認は取り消しました" in r["message"]


def test_s_msg_before_approval_is_unchanged():
    w, sid = new()
    assert cand(w.hear(sid, "Xのプラス2ミリ移動して")) == ("C00005 X", "+2.000")
    r = w.hear(sid, "Yのマイナス3ミリ移動して")
    assert r["status"] == "stop" and r["message"] == session_mod.PENDING_MESSAGE


def test_s_msg_question_after_approval():
    w, sid = new()
    assert cand(w.hear(sid, "Xのプラス2ミリ移動して")) == ("C00005 X", "+2.000")
    assert w.approve(sid, "テスト太郎")["ok"] is True
    r = w.hear(sid, "Yのマイナス3ミリ移動して？")
    assert r["status"] == "stop" and r["message"].startswith(APPROVED_PENDING)
    # 2回目は、もう承認されていないので今の文
    r = w.hear(sid, "Yのマイナス3ミリ移動して")
    assert r["message"] == session_mod.PENDING_MESSAGE


def test_s_msg_place_word_after_approval():
    w, sid = new()
    assert cand(w.hear(sid, "Xのプラス2ミリ移動して")) == ("C00005 X", "+2.000")
    assert w.approve(sid, "テスト太郎")["ok"] is True
    r = w.hear(sid, "置くところを少し手前にして")
    assert r["status"] == "stop" and r["message"].startswith(APPROVED_PENDING)


# ======================================================================
# 6. S-pick：会話の途中の打ち消し
# ======================================================================
@pytest.mark.parametrize("word", ["やっぱり違う", "そうじゃなくて", "違う", "いいえ", "C00005じゃない"])
def test_s_pick_negation_in_position_step(word):
    w, sid = new(select="C00001")
    r = w.hear(sid, "置くところを少し手前にして")
    assert r["state"]["dialog"]["step"] == "confirm_position"
    turn = r["state"]["dialog"]["turn"]
    r = w.hear(sid, word)
    assert r["status"] == "stop" and r["message"] == PICK_AFTER_NEGATION
    d = r["state"]["dialog"]
    assert d["step"] == "confirm_position" and d["turn"] == turn  # 止まる動きは今どおり（同じ所にいる）
    assert [c["id"] for c in d["choices"]] == ["pos:C00005", "other"]
    assert r["state"]["selected"] == "C00001" and r["state"]["candidate"] is None


def test_s_pick_not_used_when_other_button_is_not_shown():
    # 方向と量を聞いている段（「ほかの位置」のボタンが無い）では、今の打ち消しの文のまま
    w, sid = new(select="C00001")
    r = w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "そうそう")
    assert r["state"]["dialog"]["step"] == "ask_move"
    r = w.hear(sid, "やっぱり違う")
    assert r["status"] == "stop" and "打ち消し" in r["message"] and r["message"] != PICK_AFTER_NEGATION


def test_s_pick_not_used_outside_dialogue():
    w, sid = new()
    r = w.hear(sid, "やっぱり違う")
    assert r["status"] == "stop" and "打ち消し" in r["message"]


def test_s_pick_approval_words_keep_their_message():
    w, sid = new(select="C00001")
    w.hear(sid, "置くところを少し手前にして")
    r = w.hear(sid, "承認して")
    assert r["status"] == "stop" and ut.APPROVE_MESSAGE in r["message"]


# ---- M2 と合わせたあと：上下が未確認のときの「〜します」（fix/checker026 で追加） ----
@pytest.fixture
def unconfirmed(monkeypatch):
    """このファイルの既定（確認済み）を外し、設定なし（未確認）にする。"""
    monkeypatch.setattr(flow_mod, "DEFAULT_UPDOWN_CONFIG", None)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,plain", [("3mm下げて動かします", "3mm下げて"), ("下に3mm移動します", "下に3mm移動して")])
def test_unconfirmed_shimasu_never_becomes_candidate(unconfirmed, text, plain, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None
    r2 = w.hear(sid, plain, source=source)  # ［変更したい］を押したのと同じ文
    assert r2["status"] == "ask" and cand(r2) is None
    assert "上下の向きがまだ確認されていません" in r2["message"]
    assert all("（上）" not in c["label"] and "（下）" not in c["label"] for c in r2.get("choices") or [])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["3mm下げて動かしますか？", "下に3mm移動しますか"])
def test_unconfirmed_question_shimasu_asks_without_candidate(unconfirmed, text, source):
    w, sid = new()
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ask" and cand(r) is None
    assert "上下の向きがまだ確認されていません" in r["message"]


@pytest.mark.parametrize("source", SOURCES)
def test_unconfirmed_explicit_axis_shimasu_still_reaches_button(unconfirmed, source):
    w, sid = new()
    r = w.hear(sid, "Zのマイナス3ミリずらします", source=source)
    assert r["status"] == "ask" and cand(r) is None and labels(r) == LABELS
    r2 = press(w, sid, r, "変更したい")
    assert cand(r2) == ("C00005 Z", "-3.000")
