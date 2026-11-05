"""受入テスト：CHECKER_030 S30-2（唐川さんの決定 2026-11-03 17:14）。実装より先に書いた。

- 言い終わりの「〜ます」（3mm下げます・3mm上げます・Yのマイナス3ミリ移動させます など）は、
  「3mm下げて動かします」と同じ2択（［変更したい］［説明です］）にする。候補にしない。
  外した文がふつうの検査を通るときだけ2択。通らなければ具体的な理由。量・方向が足りなければ言い直しの文。
  → ここでは「〜ます」の発話の答えが、同じ意味の「〜して動かします」の答えと（状態・文・選択肢まで）同じであることで確かめる。
- 命令形の「3mm下げて」は今どおり（確認済みなら候補）。
- 打ち消し（「下げません」）は止める。「ください」「お願いします」は今どおり。
- 7e6f4e8 の tests/test_attr_order.py（「〜ます」を候補にする期待）は、この決定に合わせて2択の期待に直した。
"""
from pathlib import Path

import re
import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SOURCES = ["voice", "text"]
MODES = ["none", "confirmed"]
LABELS = ["変更したい", "説明です"]


def flow(mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return w, sid


def summary(r):
    # d（11/5）：向きの確かめのボタンの文には、その時だけの番号が入る（比べるときは番号を除く）
    return (r["status"], r["message"], [(c["label"], re.sub(r"#[0-9a-f]{16}$", "#番号", c["text"])) for c in r["choices"]],
            r["state"]["candidate"])


# 「〜ます」と、同じ意味の「〜して動かします」（M3 の2択）
PAIRS = [
    ("3mm下げます", "3mm下げて動かします"),
    ("3mm上げます", "3mm上げて動かします"),
    ("3ミリ下げます。", "3ミリ下げて動かします。"),
    ("下に3ミリ下げます", "下に3ミリ下げて動かします"),
    ("下に3mm上げます", "下に3mm上げて動かします"),          # 符号が2つ：聞き返し
    ("Zマイナス3mm下げます", "Zマイナス3mm下げて動かします"),  # 符号が2つ：聞き返し
    ("Z座標を3ミリ下げます", "Z座標を3ミリ下げて動かします"),
    ("Yのマイナス3ミリ移動させます", "Yのマイナス3ミリずらします"),
    ("Yのマイナス3ミリ寄せます", "Yのマイナス3ミリずらします"),
    ("3mmさげます", "3mmさげて動かします"),
    ("C00004を3mm上げます", "C00004を3mm上げて動かします"),
    ("下げます", "下げて動かします"),                          # 量が無い：言い直しの文
    ("Yのマイナス25ミリ移動させます", "Yのマイナス25ミリずらします"),  # 上限：具体的な理由
    ("下に0ミリ下げます", "下に0ミリ下げて動かします"),        # 0mm
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("masu,shimasu", PAIRS)
def test_masu_gives_the_same_answer_as_shimasu(masu, shimasu, mode, source, make_updown, tmp_path):
    w1, s1 = flow(mode, make_updown, tmp_path)
    w2, s2 = flow(mode, make_updown, tmp_path)
    r1, r2 = w1.hear(s1, masu, source), w2.hear(s2, shimasu, source)
    assert r1["state"]["candidate"] is None, (masu, r1["message"])
    assert summary(r1) == summary(r2), (masu, mode)
    assert r1["state"]["approval"] is None


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["3mm下げます", "3mm上げます", "Yのマイナス3ミリ移動させます", "Z座標を3ミリ下げます"])
def test_masu_is_two_choices_when_the_plain_instruction_passes(text, mode, source, make_updown, tmp_path):
    w, sid = flow(mode, make_updown, tmp_path)
    r = w.hear(sid, text, source)
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    if mode == "none" and ut.read_utterance(text).vertical:
        # 上下の向きが未確認：上げ・下げの「〜ます」は、「〜して動かします」と同じく上下の案内（2択も候補も出さない）
        assert all(ut.read_facing_choice(c["text"]) for c in r["choices"]) and (ut.is_updown_unset_ask(r["message"])
                                        if w._entries[sid].session.updown.status == "unconfirmed"
                                        else r["message"] == w._entries[sid].session.updown.message), r["message"]  # 11/5 唐川さんの決定：未確認の会話の聞き返しは ut.updown_ask_message（状態の文は MSG_UNCONFIRMED のまま）
        return
    assert [c["label"] for c in r["choices"]] == LABELS, (text, mode, r["message"])
    assert r["message"].endswith("変更をご希望ですか？ それとも操作の説明でしょうか？")


@pytest.mark.parametrize("source", SOURCES)
def test_want_change_after_masu_goes_to_the_button(source, make_updown, tmp_path):
    # ［変更したい］：外した文をふつうの指示として送る → 候補 → 承認はボタンだけ
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "3mm下げます", source)
    t = next(c["text"] for c in r["choices"] if c["label"] == "変更したい")
    assert t == "C00005を3ミリ下げて"
    r2 = w.hear(sid, t, source="text")
    assert r2["status"] == "ok" and (r2["state"]["candidate"]["target"], r2["state"]["candidate"]["delta"]) == ("C00005 Z", "-3.000")
    assert r2["state"]["approval"] is None and r2["state"]["can_approve"] is True


@pytest.mark.parametrize("source", SOURCES)
def test_explain_after_masu_does_nothing(source, make_updown, tmp_path):
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "Yのマイナス3ミリ移動させます", source)
    t = next(c["text"] for c in r["choices"] if c["label"] == "説明です")
    r2 = w.hear(sid, t, source="text")
    assert r2["message"] == ut.EXPLAIN_REPLY and r2["state"]["candidate"] is None


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["3mm下げません", "3mm上げません", "Yのマイナス3ミリ移動させません", "下げませんです"])
def test_masu_negation_stops(text, mode, source, make_updown, tmp_path):
    w, sid = flow(mode, make_updown, tmp_path)
    r = w.hear(sid, text, source)
    assert r["status"] == "stop" and r["choices"] == [] and r["state"]["candidate"] is None, (text, r["message"])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["下げます3mm", "3mm下げますです", "3mm下げます ね"])
def test_masu_not_at_the_end_is_not_read(text, source, make_updown, tmp_path):
    # 文の終わりでない「ます」・言い終わりを重ねたものは、知らない言葉として聞き返す（候補にも2択にもしない）
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text, source)
    assert r["status"] == "ask" and r["state"]["candidate"] is None and r["choices"] == [], (text, r["message"])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,want", [
    ("3mm下げて", ("C00005 Z", "-3.000")), ("下に3ミリ", ("C00005 Z", "-3.000")),
    ("3mm下げてください", ("C00005 Z", "-3.000")), ("下に3ミリお願いします", ("C00005 Z", "-3.000")),
    ("Yのマイナス3ミリ移動させて", ("C00005 Y", "-3.000")), ("Yのマイナス3ミリお願いします", ("C00005 Y", "-3.000")),
    ("3mm下げる", ("C00005 Z", "-3.000")),
])
def test_imperative_and_requests_are_unchanged(text, want, source, make_updown, tmp_path):
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text, source)
    c = r["state"]["candidate"]
    assert r["status"] == "ok" and c and (c["target"], c["delta"]) == want, (text, r["message"])


def test_masu_question_follows_question_handling(make_updown, tmp_path):
    # 「3mm下げますか」は「〜して動かしますか」と同じく、「〜ます」も外した文で確かめる
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, "Y座標を3mm下げますか", "text")
    assert r["status"] == "ask" and r["state"]["candidate"] is None
    assert r["message"] == "「Y座標を3mm下げて」でよいですか？", r["message"]
    assert len(r["choices"]) == 1 and r["choices"][0]["text"].endswith("Y座標を3mm下げて")


def test_masu_facts():
    f = ut.read_utterance("3mm下げます")
    assert f.shimasu_text == "3mm下げて" and not f.unknown_words
    f = ut.read_utterance("Yのマイナス3ミリ移動させます")
    assert f.shimasu_text == "Yのマイナス3ミリ移動させて" and not f.unknown_words
    assert ut.read_utterance("3mm下げて").shimasu_text is None
    assert ut.read_utterance("下に3ミリお願いします").shimasu_text is None
    assert ut.read_utterance("3mm下げてください").shimasu_text is None


# ---- 内部の Safety レビュー（シクロ）で足した経路：「〜させます」・答えの足し込み・疑問の形 ----
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("masu,shimasu", [
    ("Yのマイナス3ミリ移動させます", "Yのマイナス3ミリずらします"),
    ("Zマイナス3ミリ移動させます", "Zマイナス3ミリずらします"),
    ("Yのマイナス3ミリずらさせます", "Yのマイナス3ミリずらします"),
    ("3ミリ下げさせます", "3ミリ下げて動かします"),
    ("移動させます", "動かします"),
])
def test_saseru_masu_is_two_choices_like_shimasu(masu, shimasu, mode, source, make_updown, tmp_path):
    w1, s1 = flow(mode, make_updown, tmp_path)
    w2, s2 = flow(mode, make_updown, tmp_path)
    r1, r2 = w1.hear(s1, masu, source), w2.hear(s2, shimasu, source)
    assert r1["state"]["candidate"] is None
    assert summary(r1) == summary(r2), (masu, mode)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("first,answer", [
    ("移動させます", "3ミリ"), ("Y座標を移動させます", "マイナス3ミリ"), ("下げます", "3ミリ"), ("Yのマイナス移動させます", "3ミリ"),
])
def test_answer_after_masu_is_not_merged(first, answer, mode, source, make_updown, tmp_path):
    # 「〜します」と同じく、量の答えを前の「〜ます」の発話に足さない（候補にしない）
    w, sid = flow(mode, make_updown, tmp_path)
    r1 = w.hear(sid, first, source)
    assert r1["state"]["candidate"] is None and "何mm" not in r1["message"], r1["message"]
    r2 = w.hear(sid, answer, source)
    assert r2["state"]["candidate"] is None, (first, answer, r2["message"])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["Yのマイナス3ミリ移動させますか？", "3ミリ下げますか", "Yのマイナス3ミリずらさせますか"])
def test_masu_question_choice_does_not_end_with_masu(text, source, make_updown, tmp_path):
    # 疑問の形は「〜しますか」と同じく、「〜ます」を外した文の選択肢1つ（押すとふつうの指示）。選択肢の文は「ます」で終わらない
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text, source)
    assert r["status"] == "ask" and r["state"]["candidate"] is None and len(r["choices"]) == 1, r["message"]
    t = r["choices"][0]["text"]
    assert not t.endswith("ます") and ut.read_utterance(t).shimasu_text is None and not ut.read_utterance(t).question


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,status", [
    ("Yのマイナス3ミリ移動させません", "stop"), ("Yのマイナス3ミリ移動させないです", "stop"),
    ("Yのマイナス3ミリ移動させました", "ask"), ("Yのマイナス3ミリ移動させましょう", "ask"), ("3ミリ下げました", "ask"),
])
def test_other_masu_forms_keep_their_handling(text, status, source, make_updown, tmp_path):
    w, sid = flow("confirmed", make_updown, tmp_path)
    r = w.hear(sid, text, source)
    assert r["status"] == status and r["choices"] == [] and r["state"]["candidate"] is None, (text, r["message"])
