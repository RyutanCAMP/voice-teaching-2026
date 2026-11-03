"""Claude の答えの中の回数を、プログラムが数えた・読んだ回数と照らし合わせる（家のPCの内部レビュー P1-1・P2-1、唐川さん 11/3 夜）。

- 変更の確認の文（「2回から3回にしますか？」）の回数は、プログラムが数えた今の回数（from）と、
  利用者の文からプログラムが読んだ回数（to）と同じでなければならない。違えば **検証エラー**：
  Claude の文を正しい説明として出さない（書き換えない）。アプリの決まった文で知らせ、食い違いとして記録する
- Claude の change_request の from_count も、プログラムが数えた今の回数と同じでなければならない
- 説明の文の「N回」は、プログラムが数えたくり返しの回数か、利用者が言った回数だけ。時間（秒・分）はプログラムが数えていないので使わない
- どれも記録と表示だけ。変更候補・承認は作らない（今まで通り）
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import claude_talk as talk

BLOCK = (21, 22, 23, 24, 25, 26)


def taught(tmp_path):
    w, f = T.flow(tmp_path)
    sid = T.first_turn(w, f)
    T.taught_button(w, f, sid)
    return w, f, sid


@pytest.mark.parametrize("reply,change", [
    ("ボタンを押す動きを、2回から5回にしますか？", {"kind": "repeat", "from_count": 2, "to_count": 3}),  # 文の to が違う
    ("ボタンを押す動きを、5回から3回にしますか？", {"kind": "repeat", "from_count": 2, "to_count": 3}),  # 文の from が違う
    ("ボタンを押す動きを、2回から3回にしますか？", {"kind": "repeat", "from_count": 5, "to_count": 3}),  # from_count が違う
    ("ボタンを押す動きを、6回くり返すようにしますか？", {"kind": "repeat", "from_count": 2, "to_count": 3}),  # 関係の無い回数
])
def test_wrong_count_in_change_confirmation_is_a_verification_error(tmp_path, reply, change):
    w, f, sid = taught(tmp_path)
    f.add(T.answer(reply, BLOCK, "change_request", change))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["ok"] and r["reply_by"] == "app" and reply not in r["reply"]  # Claude の文として出さない
    assert r["reply"] == talk.COUNT_MISMATCH and r["checked"] == talk.COUNT_MISMATCH  # 決まった文（書き換えない）
    assert r["change_record"]["mismatch"] is True
    st = w._entries[sid].session
    assert st.candidate is None  # 候補・承認は作らない


def test_matching_counts_are_shown(tmp_path):
    w, f, sid = taught(tmp_path)
    f.add(T.answer("ボタンを押す動作を2回から3回に変更しますか？", BLOCK, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "claude" and r["reply"] == "ボタンを押す動作を2回から3回に変更しますか？"
    assert r["change_record"]["mismatch"] is False and w._entries[sid].session.candidate is None


@pytest.mark.parametrize("reply", [
    "同じ動きを10回くり返しています。何の作業ですか？",   # 10 は命令の数。回数ではない
    "同じ動きを6回くり返しています。何の作業ですか？",    # 6 は直線移動の数
    "約25秒で同じ動きを2回くり返しています。何の作業ですか？",  # 時間はプログラムが数えていない
])
def test_unverified_counts_in_explanations_are_not_shown(tmp_path, reply):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer(reply, BLOCK))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["ok"] and r["reply_by"] == "app" and reply not in r["reply"]


def test_verified_counts_in_explanations_are_shown(tmp_path):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer("3か所を通る同じ動きを2回くり返しています。何の作業ですか？", BLOCK))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "claude"


@pytest.mark.parametrize("text,ok", [
    ("2回から3回にしますか？", True), ("２回から３回にしますか？", True), ("二回から三回にしますか？", True),
    ("2回から5回にしますか？", False), ("5回から3回にしますか？", False), ("6回にしますか？", False),
])
def test_reply_count_check_unit(text, ok):
    assert (talk.reply_count_problem(text, allowed_counts={2, 3}, from_to=(2, 3)) is None) == ok


# ---- 再レビュー（65ba391：FAIL）：区間をプログラムが1つに決められないとき（根拠が 20〜27）も、回数の照らし合わせを止めない ----
WIDE = tuple(range(20, 28))  # 本物の通しと同じ（区間の外の行も根拠に入る）


def wide_taught(tmp_path, first=WIDE):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer("3か所を通る同じ動きを2回くり返しています。何の作業ですか？", first))
    w.ask(sid, "このロボット、何をしている？")
    T.taught_button(w, f, sid)
    return w, f, sid


@pytest.mark.parametrize("first", [WIDE, BLOCK])
@pytest.mark.parametrize("reply", ["ボタンを押す動作を3回から2回に変更しますか？", "ボタンを押す動作を2回から5回に変更しますか？"])
def test_count_check_holds_when_the_block_is_not_pinned(tmp_path, first, reply):
    w, f, sid = wide_taught(tmp_path, first)
    f.add(T.answer(reply, BLOCK, "change_request", {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "app" and r["reply"] == talk.COUNT_MISMATCH and r["change_record"]["mismatch"] is True


@pytest.mark.parametrize("first", [WIDE, BLOCK])
def test_correct_confirmation_still_shows_when_the_block_is_not_pinned(tmp_path, first):
    w, f, sid = wide_taught(tmp_path, first)
    f.add(T.answer("ボタンを押す動作を2回から3回に変更しますか？", BLOCK, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "claude" and r["change_record"]["mismatch"] is False


def test_earlier_user_counts_do_not_pass_a_change_confirmation(tmp_path):
    w, f, sid = wide_taught(tmp_path)
    f.add(T.answer("できますが、何回にしますか？", BLOCK))
    w.ask(sid, "5回にもできる？")
    f.add(T.answer("ボタンを押す動作を5回にしますか？", BLOCK, "change_request", {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "app" and r["change_record"]["mismatch"] is True


@pytest.mark.parametrize("reply", ["5か所を通る同じ動きを2回くり返しています。何の作業ですか？",
                                   "3か所を通る同じ動きを3度くり返しています。何の作業ですか？"])
def test_places_and_do_are_checked_too(tmp_path, reply):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer(reply, BLOCK))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "app"


# ---- 再レビュー（029b290：FAIL）と CHECKER_032 P1-4・K03 ----
@pytest.mark.parametrize("first", [WIDE, BLOCK])
def test_reversed_count_with_do_is_caught(tmp_path, first):
    w, f, sid = wide_taught(tmp_path, first)
    f.add(T.answer("ボタンを押す動作を3度から2度に変更しますか？", BLOCK, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "app" and r["change_record"]["mismatch"] is True


def test_explain_intent_with_a_count_in_the_utterance_uses_change_rules(tmp_path):
    # Claude が intent=explain と答えても、この文に回数があれば、変更の確認と同じ照らし合わせ
    w, f, sid = wide_taught(tmp_path)
    f.add(T.answer("できますが、何回にしますか？", BLOCK))
    w.ask(sid, "5回にもできる？")
    f.add(T.answer("ボタンを押す動作を2回から5回に変更しますか？", BLOCK))
    r = w.ask(sid, "じゃあ、3回にして")
    assert r["reply_by"] == "app" and reply_not_shown(r, "2回から5回")


def test_user_number_does_not_confirm_an_explanation_claim(tmp_path):
    w, f, sid = wide_taught(tmp_path)
    f.add(T.answer("はい、同じ動きを5回くり返しています。", BLOCK))
    r = w.ask(sid, "5回くり返してる？")
    assert r["reply_by"] == "app" and reply_not_shown(r, "5回くり返しています")


def reply_not_shown(r, part):
    return part not in r["reply"]


@pytest.mark.parametrize("reply", [
    "C00002 のZを30.0ミリ変えると良いです。",   # K14（30.0 は速度の V=30.0 と同じ数）
    "C00002 を3ミリ下げましょう。",             # K07（上下が確認済みでも）
    "Zを5mm下げると、押す深さが変わります。",
    "三ミリ下げるとよいです。",
])
def test_amounts_in_claude_text_are_not_shown(tmp_path, reply):
    w, f = T.flow(tmp_path, updown_config=T.confirmed_config(tmp_path))
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer(reply, BLOCK))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "app" and reply not in r["reply"]
    assert w._entries[sid].session.candidate is None


def test_position_not_in_the_job_is_not_shown(tmp_path):
    # K03：ジョブに無い位置番号（C00009）が入った答えは出さない
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    f.add(T.answer("C00009 でボタンを押しています。何の作業ですか？", BLOCK))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "app" and "C00009" not in r["reply"]
