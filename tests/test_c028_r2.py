"""受入テスト：内部再レビュー（fa72550）の QA P1-A・承認が黙って消える P2・QA P2-6。実装より先に書いた。

QA P1-A：「〜します」で量か方向が足りないとき、答えを待つ形の質問（「何mm動かしますか？」）を出していたが、
  「〜します」の発話には答えを足さない決まりなので「3mm」と答えても受け付けなかった。答えを待つ形の質問にせず、
  量と向きを一緒に言い直してもらう文にする（答えを前の発話に足さない動きは変えない）。
P2：新しい指示（どんな発話でも・会話のボタンでも）で承認を実際に取り消したときは、返事の頭に決まった文を付ける。
  位置の選び直しで取り消したときは、選び直しの決まった文と「承認も取り消しました。」。承認していなければ何も付けない。
QA P2-6：D1 の選択肢の文は「C00005のZ座標を3mm下げて」（「を」を続けない）。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import session as session_mod
from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SOURCES = ["voice", "text"]
CANCELLED = "新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。内容を確認して、もう一度承認してください。"
RESELECT = "位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。"
TAIL = "それとも操作の説明でしょうか？"


def start(cfg, select="C00005"):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg)
    sid = w.load(DEMO_C)["session_id"]
    if select:
        w.select(sid, select)
    return w, sid


def cand(r):
    c = r["state"]["candidate"]
    return None if c is None else (c["target"], c["delta"])


def cfg_of(mode, make_updown, tmp_path):
    return make_updown() if mode == "confirmed" else tmp_path / "none.json"


# ======================================================================
# QA P1-A
# ======================================================================
SHIMASU_SHORT = [("動かします", "none"), ("動かします", "confirmed"), ("下に動かします", "confirmed"),
                 ("3mmずらします", "none"), ("3mmずらします", "confirmed"), ("Zに動かします", "none"),
                 ("Yのマイナスに移動します", "confirmed"), ("少し下げて動かします", "confirmed")]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text,mode", SHIMASU_SHORT)
def test_p1a_shimasu_short_asks_to_restate_not_to_answer(text, mode, source, make_updown, tmp_path):
    w, sid = start(cfg_of(mode, make_updown, tmp_path))
    r = w.hear(sid, text, source)
    assert r["status"] == "ask" and cand(r) is None and TAIL not in r["message"], r["message"]
    assert "一緒に、もう一度言ってください" in r["message"], r["message"]
    assert "何mm動かしますか？" != r["message"] and not r["message"].startswith("どの方向")
    if mode == "none":  # 未確認なら上下の言葉を例に出さない
        assert not any(x in r["message"] for x in ("下に", "上に", "下げ", "上げ"))
    # 答えを前の発話には足さない（今のまま）。「3mm」だけの答えは、新しい指示として読む
    r2 = w.hear(sid, "3mm", source)
    assert cand(r2) is None and r2["status"] == "ask"
    # 量と向きを一緒に言い直せば候補になる
    r3 = w.hear(sid, "Zマイナス3ミリ", source)
    assert cand(r3) == ("C00005 Z", "-3.000"), r3["message"]


def test_p1a_restate_example_follows_updown(make_updown, tmp_path):
    r = start(cfg_of("confirmed", make_updown, tmp_path))
    w, sid = r
    m_conf = w.hear(sid, "動かします")["message"]
    w2, sid2 = start(cfg_of("none", make_updown, tmp_path))
    m_none = w2.hear(sid2, "動かします")["message"]
    assert "Zマイナス3ミリ" in m_none and "下に3ミリ" not in m_none
    assert "Zマイナス3ミリ" in m_conf and "下に3ミリ" in m_conf


def test_p1a_plain_questions_are_unchanged(make_updown, tmp_path):
    # 「〜します」でないふつうの指示は、今どおり答えを待つ質問（答えと合わせる）
    w, sid = start(cfg_of("none", make_updown, tmp_path))
    assert w.hear(sid, "Zマイナス方向")["message"] == "何mm動かしますか？"
    assert cand(w.hear(sid, "3mm")) == ("C00005 Z", "-3.000")


# ======================================================================
# P2：承認が黙って消えない
# ======================================================================
def approved(make_updown, select="C00005"):
    w, sid = start(make_updown(), select=select)
    assert cand(w.hear(sid, "Zマイナス3ミリ")) == ("C00005 Z", "-3.000")
    assert w.approve(sid, "唐川")["ok"] is True
    assert w.state(sid)["state"]["approval"] is not None
    return w, sid


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("text", ["比較して", "えーと", "下げて", "置くところを少し上に", "3ミリ", "バイオ", "承認して",
                                  "Yのマイナス3ミリ", "動かします", "下げないで"])
def test_p2_any_utterance_that_cancels_approval_says_so(text, source, make_updown):
    w, sid = approved(make_updown)
    r = w.hear(sid, text, source)
    assert r["state"]["approval"] is None
    assert r["message"].startswith(CANCELLED), (text, r["message"])
    assert r["message"].count(CANCELLED) == 1
    # 2回目はもう承認されていないので付けない
    r = w.hear(sid, text, source)
    assert CANCELLED not in r["message"]


def test_p2_explain_choice_does_not_cancel(make_updown):
    w, sid = approved(make_updown)
    r = w.hear(sid, "説明です")
    assert r["state"]["approval"] is not None and CANCELLED not in r["message"]


def test_p2_dialog_button_after_approval_says_so(make_updown):
    w, sid = start(make_updown(), select="C00001")
    w.hear(sid, "置くところを少し手前に")
    r = w.hear(sid, "そうそう")
    r = w.hear(sid, "Zマイナス3ミリ")  # 会話をやめて新しい指示として読む → 候補
    assert cand(r) == ("C00005 Z", "-3.000")
    assert w.approve(sid, "唐川")["ok"] is True
    r = w.hear(sid, "置くところを少し手前に")  # 承認待ちの候補がある：止める
    assert r["message"].startswith(CANCELLED) and r["state"]["approval"] is None


@pytest.mark.parametrize("pos", ["C00005", "C00004"])
def test_p2_reselect_after_approval_says_so(pos, make_updown):
    w, sid = approved(make_updown)
    r = w.select(sid, pos)
    assert r["state"]["approval"] is None
    assert r["message"].startswith(RESELECT + "承認も取り消しました。"), r["message"]
    assert r["message"].count(RESELECT) == 1


def test_p2_no_text_when_not_approved(make_updown):
    w, sid = start(make_updown())
    w.hear(sid, "Zマイナス3ミリ")
    for text in ("比較して", "えーと", "下げて"):
        assert CANCELLED not in w.hear(sid, text)["message"]
    assert "承認も取り消しました" not in w.select(sid, "C00005")["message"]


def test_p2_undo_keeps_its_own_text(make_updown):
    w, sid = approved(make_updown)
    r = w.undo(sid)
    assert CANCELLED not in r["message"] and r["state"]["approval"] is None


def test_p2_texts_are_the_decided_ones():
    assert session_mod.APPROVED_PENDING_MESSAGE == CANCELLED
    assert dg.ENDED_RESELECT == RESELECT


# ======================================================================
# QA P2-6：D1 の選択肢の文
# ======================================================================
@pytest.mark.parametrize("mode", ["none", "confirmed"])
@pytest.mark.parametrize("text,choice,want", [
    ("Yを3mm下げて", "C00005のY座標を3mm下げて", ("C00005 Y", "-3.000")),
    ("Zを3mm下げて", "C00005のZ座標を3mm下げて", ("C00005 Z", "-3.000")),
    ("C00004をYを3mm上げて", "C00004のY座標を3mm上げて", ("C00004 Y", "+3.000")),
])
def test_p2_6_d1_choice_text(text, choice, want, mode, make_updown, tmp_path):
    w, sid = start(cfg_of(mode, make_updown, tmp_path))
    r = w.hear(sid, text)
    assert r["choices"][0]["text"] == choice, r["choices"]
    assert "をY座標を" not in r["choices"][0]["text"] and "をZ座標を" not in r["choices"][0]["text"]
    f = ut.read_utterance(choice)
    assert not f.unknown_words
    r2 = w.hear(sid, choice)
    assert cand(r2) == want, r2["message"]
    assert r2["state"]["candidate"]["heard"] == choice
