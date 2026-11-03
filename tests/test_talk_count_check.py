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
