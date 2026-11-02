"""CHECKER_022（クロ・独立レビュー、c395400）の受入テスト。

- 3章の表：CHECKER_021 10章の26文（モック）。D1・D2 の NG 11件を含む
- 4章 P1-a：実マイクで符号が落ちた形（Chrome が返した文）を、勝手にプラスにしない
- 6章 4：選択肢を押しても承認されず、ふつうの確認の流れになる
- 10章「聞き返しの選択肢の決まり」：短い・プログラムが作る・読めた解釈だけ・未決A
"""
from pathlib import Path

import pytest

from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def flow():
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return w, sid


def hear(text, source="voice"):
    w, sid = flow()
    return w.hear(sid, text, source=source)


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"].split()[1], c["delta"]) if c else None


# CHECKER_022 3章の26文（期待：("Y", "-3.000") は候補、"ask" は聞き返し、"stop" は止める）
TABLE_26 = [
    ("Y座標を3mm下げて", ("Y", "-3.000")),
    ("Y座標を3mm上げて", ("Y", "+3.000")),
    ("Yの値を3mm減らして", ("Y", "-3.000")),
    ("Yの値を3mm増やして", ("Y", "+3.000")),
    ("Yを3mm下げて", "ask"),
    ("Xを5mm上げて", "ask"),
    ("Yを下に3mm", "ask"),
    ("Y方向に3mm上げて", "ask"),
    ("Y方向に3mm", "ask"),
    ("Yに3mm", "ask"),
    ("Y軸を3ミリ", "ask"),
    ("X方向に5mm", "ask"),
    ("Yプラスに3mm", ("Y", "+3.000")),
    ("Yマイナスに3mm", ("Y", "-3.000")),
    ("ワイプラス3ミリ", ("Y", "+3.000")),
    ("Yの+3ミリ", ("Y", "+3.000")),
    ("Y−3mm", ("Y", "-3.000")),
    ("上に3mm", ("Z", "+3.000")),
    ("下に3mm", ("Z", "-3.000")),
    ("右に3mm", "ask"),
    ("左に3mm", "ask"),
    ("Y座標を25mm下げて", "stop"),
    ("マイナスX方向に5ミリ", "ask"),
    ("ハワイ方向に5ミリ", "ask"),
    ("上に2mm、Yプラスに3mm", "ask"),
    ("Y座標値を3mm下げて", ("Y", "-3.000")),
]


def test_table_has_26_sentences():
    assert len(TABLE_26) == 26 and len({t for t, _ in TABLE_26}) == 26


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text,want", TABLE_26)
def test_checker021_table(text, want, source):
    r = hear(text, source)
    if isinstance(want, tuple):
        assert r["status"] == "ok" and cand(r) == want, (text, r.get("message"))
    else:
        assert r["status"] == want and cand(r) is None, (text, r["status"], r.get("message"))


# 4章 P1-a：実マイクで Chrome が返した文（docs/VOICE_DIAGNOSIS.md）。符号が落ちても＋にしない
@pytest.mark.parametrize("heard,said", [
    ("X 方向に 5mm", "Xマイナス方向に5ミリ"),
    ("x5ミリ", "Xマイナス5ミリ"),
    ("x25ミリ", "Xマイナスに5ミリ"),
    ("-x 方向に 5mm", "マイナスX方向に5ミリ"),
])
def test_dropped_sign_from_real_mic_is_never_plus(heard, said):
    r = hear(heard)
    assert r["status"] in ("ask", "stop") and cand(r) is None, (heard, said, r.get("message"))


def test_misheard_number_is_stopped_by_limit():
    # 「Yプラスに3ミリ」→「Y +23 mm」（2回とも）。上限 20mm で止まる
    r = hear("Y +23 mm")
    assert r["status"] == "stop" and cand(r) is None


@pytest.mark.parametrize("heard,want", [
    ("ワイプラス 3mm", ("Y", "+3.000")),
    ("Y の Plus 3ミリ", ("Y", "+3.000")),
    ("Y の + 3ミリ", ("Y", "+3.000")),
    ("X の -5ミリ", ("X", "-5.000")),
    ("この位置を上に2 mm", ("Z", "+2.000")),
    ("この位置を下に 3mm", ("Z", "-3.000")),
])
def test_real_mic_spellings_that_keep_the_sign(heard, want):
    assert cand(hear(heard)) == want


def test_voice_and_text_judge_the_same():
    for text, _ in TABLE_26:
        a, b = hear(text, "voice"), hear(text, "text")
        assert (a["status"], cand(a), a["message"]) == (b["status"], cand(b), b["message"]), text


# ---- 聞き返しの選択肢 ----
def test_two_readings_offer_two_short_choices():
    r = hear("Yを3mm下げて")
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm", "下方向へ 3mm"]
    assert all(len(c["label"]) <= 12 for c in r["choices"])


def test_unknown_sign_offers_plus_and_minus():
    r = hear("X方向に5mm")
    assert [c["label"] for c in r["choices"]] == ["Xのプラス 5mm", "Xのマイナス 5mm"]


@pytest.mark.parametrize("text", ["Yを3mm下げて", "Xを5mm上げて", "Yを下に3mm", "Y方向に3mm", "X方向に5mm"])
def test_choices_only_restate_what_was_read(text):
    # 選択肢は、その発話から読めた解釈だけ（量を変えない・別の軸を足さない）。押すと候補を作るだけで承認しない
    w, sid = flow()
    r = w.hear(sid, text, source="voice")
    facts = ut.read_utterance(text)
    for c in r["choices"]:
        r2 = w.hear(sid, c["text"], source="text")
        got = r2["state"]["candidate"]
        assert got is not None, c
        assert abs(float(got["delta"])) == facts.amount.value
        assert got["target"].split()[1] in facts.axes
        assert r2["state"]["approval"] is None and not r2["state"]["can_export"]  # 承認されていない
        w.undo(sid)


def test_choice_goes_through_the_limit():
    w, sid = flow()
    r = w.hear(sid, "Yを25mm下げて", source="voice")
    assert r["status"] == "ask"
    for c in r["choices"]:
        r2 = w.hear(sid, c["text"], source="text")
        assert r2["status"] == "stop" and r2["state"]["candidate"] is None


def test_no_choices_when_amount_is_unclear():
    assert hear("Yを少し下げて")["choices"] == []


def test_undecided_a_switch_removes_updown(monkeypatch):
    # 未決A：上・下＝Z± を「未設定」とした場合、上下を確定させず、［下方向へ］を出さない
    monkeypatch.setattr(ut, "UPDOWN_IS_Z", False)
    r = hear("Yを3mm下げて")
    assert r["status"] == "ask" and [c["label"] for c in r["choices"]] == ["Y座標を −3mm"]
    assert "上下の設定を確認してください" in r["message"]
    r = hear("下に3mm")
    assert r["status"] == "ask" and cand(r) is None and "上下の設定を確認してください" in r["message"]
    assert cand(hear("Y座標を3mm下げて")) == ("Y", "-3.000")  # 座標の値の増減は上下の設定に関係しない


def test_direction_names_are_only_the_confirmed_ones():
    w, sid = flow()
    st = w.hear(sid, "上に2mm", source="voice")["state"]
    assert st["direction_names"] == {"Z": {"+": "上", "-": "下"}}  # 右・前 などは無い
    c = st["candidate"]
    assert c["heard"] == "上に2mm" and c["before_xyz"] == ["400.000", "150.000", "200.000"]
    assert c["after_xyz"] == ["400.000", "150.000", "202.000"]
