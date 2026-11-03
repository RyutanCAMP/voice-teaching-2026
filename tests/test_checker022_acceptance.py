"""CHECKER_022（クロ・独立レビュー、c395400）の受入テスト。

- 3章の表：CHECKER_021 10章の26文（モック）。D1・D2 の NG 11件を含む
- 4章 P1-a：実マイクで符号が落ちた形（Chrome が返した文）を、勝手にプラスにしない
- 6章 4：選択肢を押しても承認されず、ふつうの確認の流れになる
- 10章「聞き返しの選択肢の決まり」：短い・プログラムが作る・読めた解釈だけ・未決A
"""
from pathlib import Path

import pytest

from voice_teaching import updown as ud
from voice_teaching import utterance as ut
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()

# 上下の向き（CHECKER_026 M2）：このファイルは、発話の読み取り（「上・下・上げ・下げ」を Z± として読むことを含む）を
# 確かめるテスト。上・下を Z にするのは上下の向きの照合に通ったときだけなので、ファイルの全テストに
# 一時フォルダの確認済みの設定（DEMO_C_PLACE。conftest.updown_demo_c）を当てる。設定が無いときの動きは tests/test_updown.py
pytestmark = pytest.mark.usefixtures("updown_demo_c")


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
    # 未決A → CHECKER_026 M2 で決定（A案）：上下の向きの設定が無いときは、上下を確定させず、［下方向へ］を出さない。
    # 旧：utterance.UPDOWN_IS_Z = False に切り替えて確かめた。今は全体の固定値が無く、設定ファイルの照合で決まる。
    # 期待を「聞き返し」に直した（案内の文は updown.MSG_UNCONFIRMED。旧文「上下の設定を確認してください」は使わない）
    monkeypatch.setattr(flow_mod, "DEFAULT_UPDOWN_CONFIG", None)  # このテストだけ、設定なし
    r = hear("Yを3mm下げて")
    assert r["status"] == "ask" and [c["label"] for c in r["choices"]] == ["Y座標を −3mm"]
    assert ud.MSG_UNCONFIRMED in r["message"]
    r = hear("下に3mm")
    assert r["status"] == "ask" and cand(r) is None and r["message"] == ud.MSG_UNCONFIRMED
    assert cand(hear("Y座標を3mm下げて")) == ("Y", "-3.000")  # 座標の値の増減は上下の設定に関係しない


def test_direction_names_are_only_the_confirmed_ones(monkeypatch):
    w, sid = flow()
    st = w.hear(sid, "上に2mm", source="voice")["state"]
    assert st["direction_names"] == {"Z": {"+": "上", "-": "下"}}  # 右・前 などは無い（確認済みの設定のとき）
    c = st["candidate"]
    assert c["heard"] == "上に2mm" and c["before_xyz"] == ["400.000", "150.000", "200.000"]
    assert c["after_xyz"] == ["400.000", "150.000", "202.000"]
    # CHECKER_026 M2：設定が無ければ方向の名前は1つも無い（期待を足した）
    monkeypatch.setattr(flow_mod, "DEFAULT_UPDOWN_CONFIG", None)
    w, sid = flow()
    assert w.state(sid)["state"]["direction_names"] == {}


# ---- 内部 Safety レビュー（955f47c）の P1・P2 ----
@pytest.mark.parametrize("text", ["ワイ 3mm下げて", "ワイ、3mm下げて", "ワイ3ミリ下げて", "ワイは3mm下げて",
                                  "エックス 5mm上げて"])
def test_spoken_axis_without_particle_is_not_dropped(text):
    # P1：カタカナの軸名の後に「の・を」などが無くても、軸を捨てて Z にしない（2つの読み方 → 2択）
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None and len(r["choices"]) == 2


def test_spoken_axis_with_hiragana_zahyou_is_asked_without_choices():
    # P1 の「ワイざひょうを3mm下げて」：2026-11-03 の知らない言葉の対策（docs/UNKNOWN_WORDS_PLAN.md）から、
    # ひらがなの「ざひょう」は読み取りで使わない言葉なので、2択ではなく、選択肢なしで聞き返す（軸を捨てて Z にしないのは同じ）
    r = hear("ワイざひょうを3mm下げて")
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == []
    assert "「ざひょう」" in r["message"]


@pytest.mark.parametrize("text", ["XY座標を3mm下げて", "XYを3mm下げて"])
def test_axis_letter_runs_are_all_axes(text):
    # P2-1：「XY」は X と Y（軸が2つ以上 → 聞き返す）
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None


def test_amount_follow_up_keeps_the_earlier_axis():
    # P2-2：「Y座標を」→「何mm？」→「3mm下げて」：前の Y を捨てて Z にしない（2択で聞き返す）
    w, sid = flow()
    assert w.hear(sid, "Y座標を", source="voice")["status"] == "ask"
    r = w.hear(sid, "3mm下げて", source="voice")
    assert r["status"] == "ask" and cand(r) is None
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm", "下方向へ 3mm"]


def test_choice_after_d1_question_is_not_asked_again():
    # 2択の［下方向へ 3mm］（「下に3mm」）を押したら、そのとおり Z−3（もう一度聞き返さない）
    w, sid = flow()
    w.hear(sid, "Yを3mm下げて", source="voice")
    assert cand(w.hear(sid, "下に3mm", source="text")) == ("Z", "-3.000")


@pytest.mark.parametrize("text", ["ワイヤーを3mm動かして", "ハワイ方向に5ミリ", "ワイド3mm"])
def test_katakana_words_are_not_axes(text):
    assert ut.read_utterance(text).axes == ()


def test_choice_keeps_the_position_it_was_asked_about():
    # 内部 QA P2：聞き返しの後に別の位置を選んでも、古い選択肢はその位置に当たらない（選択肢の文に位置を入れる）
    w, sid = flow()
    r = w.hear(sid, "Yを3mm下げて", source="voice")
    # 「C00005のY座標を…」（「を」を続けない。fix/c026-r2 QA P2-6）・「C00005を下に…」
    assert all(c["text"].startswith(("C00005を", "C00005の")) for c in r["choices"])
    w.hear(sid, "C00004を選択して", source="text")
    r2 = w.hear(sid, r["choices"][0]["text"], source="text")
    got = r2["state"]["candidate"]
    assert got is not None and got["target"] == "C00005 Y" and got["delta"] == "-3.000"
