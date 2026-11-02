"""知らない言葉を捨てて、別の軸の候補にしない（2026-11-03 唐川さんの優先課題④、docs/UNKNOWN_WORDS_PLAN.md）。

実マイク（docs/VOICE_DIAGNOSIS.md）：「Yを3ミリ下げて」→ Chrome が「バイオ 3ミリ 下げて」と返し、
「バイオ」は読まれずに捨てられ、「下げて」だけから Z −3 の候補になった。

対策（案2＋つなぎの言葉の一覧、2026-11-03 実装）：移動・速度の指示で、読み取りで使わなかった言葉
（つなぎの言葉の一覧 utterance.FILLER_WORDS にも無いもの）が1つでも残れば、選択肢なしで聞き返す。

- 再現テスト：対策前は Z の候補になった形。今は聞き返し・候補なし・知らない言葉を「」で示す
- 守るテスト：今の正しい動き（軸を言わない上げ下げ・座標の上げ下げ・D1 の2択・つなぎの言葉）を変えない
"""
from pathlib import Path

import pytest

from voice_teaching import utterance as ut
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


@pytest.mark.parametrize("heard,word", MISHEARD)
@pytest.mark.parametrize("source", ["voice", "text"])
def test_unknown_word_is_not_dropped_into_another_axis(heard, word, source):
    r = hear(heard, source=source)
    assert r["status"] == "ask" and cand(r) is None, (heard, r["status"], r.get("message"))
    assert f"「{word}」" in r["message"]  # 何が分からなかったかを示す

    assert r["choices"] == []  # ［下方向へ 3mm］も［Y座標…］も出さない（言葉を捨てるか、推測になるため）


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
    ("X の -5ミリ", ("X", "-5.000")),            # 実マイクで返った文
    # つなぎの言葉（FILLER_WORDS）は残ってよい
    ("えーと下に3ミリ", ("Z", "-3.000")),
    ("C00005を下に3mmお願いします", ("Z", "-3.000")),
    ("あのー、じゃあY座標を3ミリ下げてください", ("Y", "-3.000")),
    ("まず下に3ミリ移動して", ("Z", "-3.000")),
    ("Y-方向に3mm", ("Y", "-3.000")),
    ("Y座標値を3mm下げて", ("Y", "-3.000")),
])
def test_known_words_still_make_candidates(text, want):
    r = hear(text)
    assert r["status"] == "ok" and cand(r) == want, (text, r.get("message"))


def test_d1_two_choices_are_kept():
    r = hear("Yを3ミリ下げて")
    assert r["status"] == "ask" and cand(r) is None
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm", "下方向へ 3mm"]


# ---- 2026-11-03 の対策で足したテスト ----
JUNK_WITH_AXIS = [
    ("バイオ Y座標を3ミリ下げて", "バイオ"),   # 軸をはっきり言っていても、知らない言葉は捨てない
    ("Y座標をバイオ3ミリ下げて", "バイオ"),
    ("バイオ Yのマイナス3ミリ", "バイオ"),
    ("C00005をハワイ下に3mm", "ハワイ"),
    ("why 3ミリ下げて", "WHY"),               # 小文字の英字（大文字に揃えて示す）
    ("ざひょうを3ミリ下げて", "ざひょう"),      # 一覧に無いひらがな
    ("座標を3ミリ下げて", "座標"),              # 軸の無い「座標」は使わない言葉（Z にしない）
    ("方向に3ミリ下げて", "方向"),
]


@pytest.mark.parametrize("heard,word", JUNK_WITH_AXIS)
def test_unknown_word_with_axis_or_position_is_asked(heard, word):
    r = hear(heard)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (heard, r.get("message"))
    assert f"「{word}」" in r["message"]


@pytest.mark.parametrize("heard", [h for h, _ in MISHEARD + JUNK_WITH_AXIS])
def test_unknown_word_ask_never_shows_choices(heard):
    assert hear(heard)["choices"] == []


def test_unknown_word_in_speed_instruction_is_asked():
    r = hear("速度をバイオ10%上げて")
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == []
    assert "「バイオ」" in r["message"]


def test_axis_word_in_speed_instruction_is_not_dropped():
    # 速度の指示では軸を読まない。読まなかった「Y」を捨てて速度の候補にしない
    r = hear("Yの速度を10%上げて")
    assert r["status"] == "ask" and "「Y」" in r["message"]


def hear_all(*texts):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    return [w.hear(sid, t) for t in texts]


@pytest.mark.parametrize("first,answer,word", [
    ("Y方向に3mm", "バイオ マイナス", "バイオ"),     # 符号の聞き返しへの答え（merge_answer）
    ("Y座標を下げて", "バイオ3ミリ", "バイオ"),      # 量の聞き返しへの答え（merge_answer）
    ("Y座標を下げて", "バイオ3ミリ下げて", "バイオ"),  # 量の聞き返しへの上げ下げの答え（combine_axes）
])
def test_answer_to_question_with_unknown_word_is_asked(first, answer, word):
    r1, r2 = hear_all(first, answer)
    assert r1["status"] == "ask"
    assert r2["status"] == "ask" and cand(r2) is None and r2["choices"] == [], r2.get("message")
    assert f"「{word}」" in r2["message"]


def test_after_unknown_word_ask_the_next_utterance_is_read_fresh():
    # 知らない言葉の聞き返しのあとは、言い直しを新しい指示として読む（前の「下げて」と合わせない）
    r1, r2 = hear_all("バイオ3ミリ下げて", "Y座標を3ミリ下げて")
    assert r1["status"] == "ask" and r2["status"] == "ok" and cand(r2) == ("Y", "-3.000")
    r1, r2 = hear_all("バイオ3ミリ下げて", "下に3ミリ")
    assert r2["status"] == "ok" and cand(r2) == ("Z", "-3.000")
    # 量や符号だけの答えは、知らない言葉のあった発話に足さない（候補にしない）
    r1, r2 = hear_all("バイオ下げて", "3ミリ")
    assert r2["status"] == "ask" and cand(r2) is None
    r1, r2 = hear_all("バイオ3ミリ下げて", "マイナス")
    assert r2["status"] == "ask" and cand(r2) is None


def test_merge_and_combine_keep_unknown_words():
    prev = ut.read_utterance("Y座標を下げて")
    assert ut.merge_answer(prev, ut.read_utterance("バイオ3ミリ")).unknown_words == ("バイオ",)
    assert ut.combine_axes(prev, ut.read_utterance("バイオ3ミリ下げて")).unknown_words == ("バイオ",)
    assert ut.merge_answer(ut.read_utterance("バイオ下げて"), ut.read_utterance("3ミリ")).unknown_words == ("バイオ",)


def test_check_proposal_refuses_when_unknown_words_remain():
    # 本物の Claude が「バイオ」を捨てて Z −3 を提案しても、照らし合わせ（check_proposal → pre_check）で断る
    facts = ut.read_utterance("C00005をバイオ3ミリ下げて")
    p = ut.ProposedChange(kind="shift", position="C00005", axis="Z", delta_mm=-3.0)
    v = ut.check_proposal(facts, p, "C00005")
    assert v.status == "ask" and v.choices == () and "「バイオ」" in v.message


class ProposeZMinus3:
    """発話を見ずに Z −3 を提案する解釈の係（知らない言葉を捨てる Claude の代わり）。"""
    kind, model, notice = "claude", "fake", "テスト用"

    def __init__(self):
        self.called = 0

    def decide(self, facts, selected, positions=()):
        self.called += 1
        return [{"tool": "propose_change",
                 "args": {"kind": "shift", "position": "C00005", "axis": "Z", "delta_mm": -3.0}}]


def test_interpreter_is_not_called_and_no_candidate_with_unknown_words():
    it = ProposeZMinus3()
    w = WebFlow(interpreter=it)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "バイオ 3ミリ 下げて")
    assert r["status"] == "ask" and cand(r) is None and it.called == 0
    assert r["facts"]["unknown_words"] == ["バイオ"]


def test_choice_texts_have_no_unknown_words():
    # 選択肢を押すと、その文を指示として送る。プログラムが作る文に、知らない言葉が入らないこと
    for first in ("Yを3ミリ下げて", "X方向に5mm"):
        r = hear(first)
        assert r["choices"]
        for c in r["choices"]:
            assert ut.read_utterance(c["text"]).unknown_words == (), c["text"]
            r2 = hear(c["text"])
            assert r2["status"] == "ok" and cand(r2) is not None, (c["text"], r2.get("message"))


def test_filler_words_are_a_short_named_list():
    assert len(ut.FILLER_WORDS) <= 30
    assert all(w for w in ut.FILLER_WORDS)
    # 軸・向き・量になる言葉や、カタカナの聞き違いは入れない
    for w in ("バイオ", "ワイド", "座標", "方向", "ざひょう", "X", "Y", "Z"):
        assert w not in ut.FILLER_WORDS


# ---- 外部レビュー CHECKER_023 の B1（この不具合）----
@pytest.mark.parametrize("heard,word", [
    ("バイオ 3ミリ 下げて", "バイオ"),
    ("バイオ3mm下げて", "バイオ"),
    ("ライ 3mm下げて", "ライ"),
    ("愛 3ミリ下げて", "愛"),       # 漢字1文字も捨てない
    ("わい 3mm下げて", "わい"),     # ひらがなの「わい」も捨てない（Y と読み替えもしない）
])
@pytest.mark.parametrize("source", ["voice", "text"])
def test_checker023_b1_unknown_word_asks(heard, word, source):
    r = hear(heard, source=source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (heard, r.get("message"))
    assert f"「{word}」" in r["message"]
    assert word in r["facts"]["unknown_words"]  # 画面で知らない言葉を目立たせるため、応答に入れる


def test_checker023_b1_two_step_unknown_word_asks():
    r1, r2 = hear_all("バイオ下げて", "3mm")
    assert r1["status"] == "ask" and "「バイオ」" in r1["message"] and r1["choices"] == []
    assert r2["status"] == "ask" and cand(r2) is None


@pytest.mark.parametrize("text", ["えーと3mm下げて", "この位置を3mm下げて"])
def test_checker023_b1_no_over_asking(text):
    r = hear(text)
    assert r["status"] == "ok" and cand(r) == ("Z", "-3.000"), (text, r.get("message"))


# ---- 動きの言葉の後の言い終わり・位置番号の後の「位置」（2026-11-03 シクロの判断）----
@pytest.mark.parametrize("text", [
    "下に3ミリ移動させて", "下に3ミリ移動させ", "下に3ミリずらす", "3mm下げる", "3ミリ下げます", "3mm下げです",
    "C00005の位置を下に3mm", "C00005 の 位置を下に3mm",
])
def test_allowed_endings_and_position_word(text):
    r = hear(text)
    assert r["status"] == "ok" and cand(r) == ("Z", "-3.000"), (text, r.get("message"))


@pytest.mark.parametrize("text,word", [
    ("3mmだけ下げて", "だけ"),      # 量があいまいになる
    ("下に3ミリだけ", "だけ"),
    ("下に3mmほど", "ほど"),
    ("下に3ミリな", "な"),
    ("はい下に3mm", "はい"),        # 承認の言葉
    ("る 3mm下げて", "る"),         # 動きの言葉の後でない言い終わりは使わない
    ("下に3ミリです", "です"),
    ("位置を下に3mm", "位置"),       # 位置番号・この・その の後でない「位置」
])
def test_kept_asks(text, word):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    assert f"「{word}」" in r["message"]
