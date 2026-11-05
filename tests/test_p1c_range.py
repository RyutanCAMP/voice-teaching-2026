"""P1-C（11/5）：範囲を言った回数の指示が、言った範囲と違う範囲に広がらない。

「C00002からC00003を3回にして」→ 入口（/api/talk）の「回数の変更」の道が、プログラムが見つけた区間（C00001〜C00003）の
決まった文に置き換えて、範囲の確認（→ 候補 → 承認）へ進めていた。直したあと：
- 位置・範囲を言った回数の文は、指示の道へ（言ったとおりの範囲だけを調べる。区間と合わなければ止める。Claude を呼ばない）
- 言った範囲が区間とちょうど合うとき（C00001〜C00003）だけ、その範囲の確認になる
- 範囲を言わない文（「じゃあ3回にして」）は今までどおり（区間を示して、ボタンで確かめる）
"""
import pytest

import test_claude_talk as T
from voice_teaching.web import flow as flow_mod

BLOCK = (21, 22, 23, 24, 25, 26)
CHANGE = {"kind": "repeat", "from_count": 2, "to_count": 3}
NOT_THE_BLOCK = ["C00002からC00003を3回にして", "C00002〜C00003を3回にして", "C2からC3を3回にして",
                 "c00002からc00003を3回にして", "ｃ００００２からｃ００００３を３回にして", "C00001からC00002を3回にして",
                 "じゃあ、C00002からC00003を3回にして", "C00002からC00003を三回にして", "C00002からC00003まで3回にして",
                 "C00002からC00003を2回から3回にして", "C00002からC00003の作業を3回にして",
                 "C00002からC00003のくり返しを3回にして", "C00002からC00003を3回くり返して", "C00002とC00003を3回くり返して",
                 "2番から3番を3回にして", "C00002からC00003を1回追加して", "C00002からC00003をもう1回増やして"]


def start(tmp_path, *responses):
    w, f = T.flow(tmp_path, *responses)
    return w, f, w.load(T.demo_bytes())["session_id"]


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text", NOT_THE_BLOCK)
def test_said_range_is_not_widened_to_the_block(tmp_path, text, source):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, text, source)
    st = r["state"]
    assert r["via"] == ["hear"]  # 回数の変更の道（区間の決まった文への置き換え）を通らない
    assert st["repeat_range"] is None and st["candidate"] is None and st["approval"] is None
    assert len(f.requests) == 0  # Claude を呼ばない
    assert "C00001〜C00003" not in (r["hear"].get("message") or "")
    assert st["status_line"] == "アプリの記録：変更の候補 なし／承認 まだ／出力 まだ"


@pytest.mark.parametrize("text", ["C00002からC00003を3回にして", "C00001からC00002を3回にして"])
def test_said_range_is_refused_with_the_said_range(tmp_path, text):
    w, f, sid = start(tmp_path)
    msg = w.talk(sid, text)["hear"]["message"]
    a, b = text[:6], text[8:14]
    assert f"区間（{a}〜{b}）" in msg and "複写しません" in msg  # 言った範囲のまま調べて止める


@pytest.mark.parametrize("text", ["C00002からC00003を3回くり返して", "C00002とC00003を3回くり返して"])
def test_the_two_choices_do_not_widen_the_range(tmp_path, text):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, text)
    ch = r["hear"].get("choices") or []
    # 11/5 唐川さん：行き止まりの二択はやめた。選択肢があっても、どれも言った範囲を区間に広げない
    assert "C00001〜C00003" not in (r["hear"].get("message") or "") and r["state"]["repeat_range"] is None
    for c in ch:
        w2, f2, sid2 = start(tmp_path)
        w2.talk(sid2, text)
        st = w2.hear(sid2, c["text"])["state"]
        assert st["repeat_range"] is None and st["candidate"] is None


@pytest.mark.parametrize("text", ["C00001からC00003を3回にして", "C00001〜C00003を3回にして", "C1からC3を3回にして"])
def test_the_exact_block_still_shows_only_that_range(tmp_path, text):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, text)
    rr = r["state"]["repeat_range"]
    assert r["via"] == ["hear"] and len(f.requests) == 0
    assert rr["positions"] == ["C00001", "C00002", "C00003"] and rr["total"] == 3
    assert r["state"]["candidate"] is None  # 候補は［この範囲を繰り返す］のボタンのあと
    c = w.repeat_choose(sid, "yes", rr["turn"])["state"]["candidate"]
    assert c is not None and "承認 まだ" in w.state(sid)["state"]["status_line"]


def test_no_range_said_keeps_the_conversation_and_the_range_check(tmp_path):
    # 範囲を言わない文は今までどおり：会話の道（Claude）＋区間を示して、ボタンで確かめる
    w, f, sid = start(tmp_path, T.answer("回数を変えたいのですね。", BLOCK, "change_request", CHANGE))
    r = w.talk(sid, "じゃあ、3回にして")
    assert r["via"] == ["ask", "hear"] and r["state"]["repeat_range"]["positions"] == ["C00001", "C00002", "C00003"]
    assert r["state"]["candidate"] is None


def test_said_range_after_approval_does_not_make_a_widened_candidate(tmp_path):
    w, f, sid = start(tmp_path)
    T.repeat_to_three(w, sid)
    w.approve(sid, "テスト")
    before = w.state(sid)["state"]["candidate"]
    st = w.talk(sid, "C00002からC00003を3回にして")["state"]
    assert st["repeat_range"] is None and st["candidate"] == before  # 広がった範囲の確認も、新しい候補も作らない


@pytest.mark.parametrize("text,said", [
    ("C00002からC00003を3回にして", True), ("C2〜C3を3回", True), ("2番から3番を3回", True), ("ここの位置を3回", True),
    ("じゃあ、3回にして", False), ("2回から3回にして", False), ("じゃあ2回から3回にして", False), ("この動きを3回にして", False),
])
def test_said_range_reading(text, said):
    assert flow_mod._said_range(text) is said
