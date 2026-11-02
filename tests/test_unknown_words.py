"""知らない言葉を捨てて、別の軸の候補にしない（2026-11-03 唐川さんの優先課題④、docs/UNKNOWN_WORDS_PLAN.md）。

実マイク（docs/VOICE_DIAGNOSIS.md）：「Yを3ミリ下げて」→ Chrome が「バイオ 3ミリ 下げて」と返し、
「バイオ」は読まれずに捨てられ、「下げて」だけから Z −3 の候補になった。

- 再現テスト（xfail strict）：今は候補になってしまう形。直したら xfail を外す（外さないと strict で失敗する）
- 守るテスト：今の正しい動き（軸を言わない上げ下げ・座標の上げ下げ・D1 の2択）を、直したあとも変えない
"""
from pathlib import Path

import pytest

from voice_teaching.web.flow import WebFlow

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def hear(text, source="voice"):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return w.hear(sid, text, source=source)


def cand(r):
    c = r["state"]["candidate"]
    return (c["target"].split()[1], c["delta"]) if c else None


# （聞こえた文, 文の中の知らない言葉）。軸を言っていない上げ下げに、知らない言葉が付いている
MISHEARD = [
    ("バイオ 3ミリ 下げて", "バイオ"),   # 実マイクで返った文（「Yを3ミリ下げて」）
    ("バイオ3ミリ下げて", "バイオ"),
    ("ワイド3ミリ下げて", "ワイド"),
    ("ワイヤーを3mm下げて", "ワイヤー"),
    ("バイ 3ミリ 上げて", "バイ"),
    ("ハイを3ミリ下げて", "ハイ"),
    ("WHY 3ミリ下げて", "WHY"),
    ("BUY 3mm上げて", "BUY"),
    ("愛を3ミリ下げて", "愛"),
]


@pytest.mark.xfail(strict=True, reason="未対策（docs/UNKNOWN_WORDS_PLAN.md）。直したら xfail を外す")
@pytest.mark.parametrize("heard,word", MISHEARD)
@pytest.mark.parametrize("source", ["voice", "text"])
def test_unknown_word_is_not_dropped_into_another_axis(heard, word, source):
    r = hear(heard, source=source)
    assert r["status"] == "ask" and cand(r) is None, (heard, r["status"], r.get("message"))
    assert f"「{word}」" in r["message"]  # 何が分からなかったかを示す


@pytest.mark.parametrize("heard,word", MISHEARD)
def test_unknown_word_today_becomes_z(heard, word):
    # 今の動き（問題の記録）。対策を入れたら、このテストは消す
    assert cand(hear(heard))[0] == "Z"


# ---- 直したあとも変えない動き ----
@pytest.mark.parametrize("text,want", [
    ("下に3mm", ("Z", "-3.000")),
    ("上に3mm", ("Z", "+3.000")),
    ("この位置を上に2ミリ", ("Z", "+2.000")),
    ("この位置を上に2 mm", ("Z", "+2.000")),     # 実マイクで返った文
    ("C00005を下に3mm", ("Z", "-3.000")),
    ("Y座標を3ミリ下げて", ("Y", "-3.000")),
    ("Y 座標を 3ミリ 下げて", ("Y", "-3.000")),  # 実マイクで返った文
    ("Xの -5ミリ", ("X", "-5.000")),
    ("ワイプラス3ミリ", ("Y", "+3.000")),
])
def test_known_words_still_make_candidates(text, want):
    r = hear(text)
    assert r["status"] == "ok" and cand(r) == want, (text, r.get("message"))


def test_d1_two_choices_are_kept():
    r = hear("Yを3ミリ下げて")
    assert r["status"] == "ask" and cand(r) is None
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm", "下方向へ 3mm"]
