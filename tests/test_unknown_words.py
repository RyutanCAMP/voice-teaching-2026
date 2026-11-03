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


# ---- CHECKER_023A B6・B7：打ち消し・取り消し・言い直しの言葉は、知らない言葉より先に止める ----
NEGATED = [
    ("Y座標を3mm下げないで", "下げないで"), ("Yマイナス3mmにしないで", "しないで"),
    ("Xのマイナス5ミリじゃなくて", "じゃなく"), ("Y座標を3mm下げるな", "下げるな"),
    ("Yマイナス3mmは駄目", "駄目"), ("Y座標を3mm下げたくない", "下げたくない"),
    ("3mm下げないで", "下げないで"), ("下げないで3mm", "下げないで"), ("3mm下げなくていい", "下げなくて"),
    ("3mm下げたら駄目", "下げたら駄目"), ("3mmは下げない", "下げない"), ("3mm下げたらだめ", "下げたらだめ"), ("3mm下げたらダメ", "下げたらダメ"),
    ("C00004じゃなくて3mm下げて", "じゃなく"), ("C00004ではなく3mm下げて", "ではなく"),
    ("Xのマイナス5ミリはやめて", "やめ"), ("Y座標を3mm下げるのは禁止", "禁止"), ("Y座標を3mm下げるのは違う", "違う"),
    ("下に3mmはちがう", "ちがう"), ("下に3mmを取り消し", "取り消し"), ("下に3mm動かさなかった", "動かさなかっ"),
    ("Yを3mmでなく下に3mm", "でなく"), ("じゃないY座標を3mm下げて", "じゃない"), ("速度を10%上げないで", "上げないで"),
    ("下に3mm動かすな", "動かすな"),
]


@pytest.mark.parametrize("text,word", NEGATED)
@pytest.mark.parametrize("source", ["voice", "text"])
def test_negation_stops_without_candidate(text, word, source):
    r = hear(text, source=source)
    assert r["status"] == "stop" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    assert f"「{word}」" in r["message"] and "変更しません" in r["message"]


def test_negation_is_checked_before_unknown_words_and_in_answers():
    # 知らない言葉があっても、打ち消しを先に示して止める
    r = hear("バイオを3mm下げないで")
    assert r["status"] == "stop" and "「下げないで」" in r["message"]
    # 聞き返しへの答えの打ち消し（merge_answer）・上げ下げの答えの打ち消し（combine_axes）も止める
    r1, r2 = hear_all("Y方向に3mm", "マイナスじゃない")
    assert r2["status"] == "stop" and cand(r2) is None
    r1, r2 = hear_all("Y座標を下げて", "3mm下げないで")
    assert r2["status"] == "stop" and cand(r2) is None
    prev = ut.read_utterance("Y座標を下げて")
    assert ut.merge_answer(prev, ut.read_utterance("3ミリじゃない")).negations
    assert ut.combine_axes(prev, ut.read_utterance("3mm下げないで")).negations


def test_negation_in_check_proposal():
    facts = ut.read_utterance("C00005をY座標で3mm下げないで")
    p = ut.ProposedChange(kind="shift", position="C00005", axis="Y", delta_mm=-3.0)
    assert ut.check_proposal(facts, p, "C00005").status == "stop"


@pytest.mark.parametrize("text", ["やめて", "元に戻して", "取り消して", "キャンセル"])
def test_undo_without_move_keeps_its_behaviour(text):
    # 移動・速度の無い「やめて」「元に戻して」は、今までどおり元に戻す（打ち消しで止めない）
    f = ut.read_utterance(text)
    assert f.op == "undo" and ut.pre_check(f).status == "ok"
    r = hear(text)
    assert r["status"] == "ok"


def test_negation_words_are_never_allowed_words():
    allowed = (ut.FILLER_WORDS + ut.VERB_ENDINGS + ut.AMOUNT_PREFIXES + ut.PARTICLES + ut.TE_FORMS + ut.REQUESTS
               + ut.POLITE_AFTER_TE + ut.FINALS + ut.HESITATIONS + ut.OPENERS)
    for n in ut.NEGATION_WORDS + ("な",):
        for w in allowed:
            assert n not in w, (n, w)
    for n in ut.NEGATION_WORDS + ("NG",):
        f = ut.read_utterance(f"下に3mm {n}")
        assert f.negations or f.undo_negations, n
        assert ut.pre_check(f).status == "stop", n


# ---- CHECKER_023A の SHOULD・内部レビューの指摘 ----
@pytest.mark.parametrize("text,want", [
    ("あと3mm下げて", ("Z", "-3.000")), ("もう3mm下げて", ("Z", "-3.000")),
    ("Y座標を3ミリ下げて下さい", ("Y", "-3.000")),                 # QA P2-1：「下さい」を下（Z）と読まない
    ("X軸方向にマイナス5ミリ", ("X", "-5.000")), ("Z軸方向にマイナス3ミリ", ("Z", "-3.000")),  # QA P2-2
    ("下に3ミリでお願いします", ("Z", "-3.000")), ("じゃあ下に3mmで", ("Z", "-3.000")), ("下に3ミリね", ("Z", "-3.000")),
    ("3mm下げてください", ("Z", "-3.000")), ("えーと、3mm下げて", ("Z", "-3.000")), ("3mm下げてお願いします", ("Z", "-3.000")),
    ("じゃあ3mm下げて", ("Z", "-3.000")), ("3mm下へ", ("Z", "-3.000")),
])
def test_natural_forms_make_candidates(text, want):
    r = hear(text)
    assert r["status"] == "ok" and cand(r) == want, (text, r.get("message"))


@pytest.mark.parametrize("text,word", [
    ("下さい3mm", "下さい"),          # て の後でない「下さい」
    ("もう少し下げて", None),          # あいまいな量
    # Safety P3-1：短いつなぎの言葉を並べたものは通さない
    ("もも3mm下げて", "もも"), ("でも3mm下げて", "でも"), ("とも3mm下げて", "とも"), ("よ 3mm下げて", "よ"),
    ("ね3mm下げて", "ね"), ("のに3mm下げて", "のに"), ("えっとを3mm下げて", "えっとを"), ("3mm下げては", "ては"),
    ("3mm下げても", "ても"), ("下に3mmはね", "はね"),
    # Safety P3-2
    ("そこそこ下に3mm", None), ("ここここ下に3mm", "ここここ"),
    # Safety P3-4：半角の 0〜9 のほかの数字
    ("٣mm下げて", "٣"),
    # SHOULD_NO_CANDIDATE・B9
    ("ツールを3mm下げて", "ツール"), ("手首を3mm下げて", "手首"), ("アームを3mm下げて", "アーム"),
    ("ロボットを3mm下げて", "ロボット"), ("斜め下に3mm", "斜め"),
])
def test_asks_without_candidate(text, word):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    if word:
        assert f"「{word}" in r["message"], r["message"]


def test_qa_p3_1_after_unknown_word_ask_down_is_read_fresh():
    # session の _awaiting_answer=None を消すと、「下に3ミリ」が前の発話と合わさって聞き返しになる（その変異を見つける）
    r1, r2 = hear_all("バイオ下げて", "下に3ミリ")
    assert r1["status"] == "ask" and r2["status"] == "ok" and cand(r2) == ("Z", "-3.000")


def test_safety_p3_3_only_unknown_words():
    r = hear("バイオ")
    assert r["status"] == "ask" and r["message"].startswith("「バイオ」が分かりませんでした")


@pytest.mark.parametrize("text,shown", [
    ("下に3mm\u200b", "（見えない文字）"), ("下に3mm\u0007", "（見えない文字）"), ("\ufeff下に3mm", "（見えない文字）"),
    ("下に3mm👍", "👍"),
])
def test_qa_p3_5_invisible_and_emoji(text, shown):
    r = hear(text)
    assert r["status"] == "ask" and cand(r) is None and f"「{shown}」" in r["message"]
    assert "「\u200b」" not in r["message"] and "「」" not in r["message"]


# ---- a8f6193 の内部レビュー（QA P2-1・P3-2〜P3-5・P3-9、Safety P3-1・P3-3）----
def flow_with_candidate(source):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "下に3mm", source=source)
    assert r["status"] == "ok" and cand(r) == ("Z", "-3.000")
    return w, sid


@pytest.mark.parametrize("source", ["voice", "text"])
def test_negated_select_does_not_change_selection(source):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "C00004は選択しない", source=source)
    assert r["status"] == "stop" and "「選択しない」" in r["message"] and r["tool_calls"] == []
    assert r["state"]["selected"] == "C00005"
    r = w.hear(sid, "下に3mm", source=source)
    assert r["state"]["candidate"]["target"] == "C00005 Z"  # 打ち消した C00004 に当てない


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text,word", [("元に戻さないで", "戻さないで"), ("比較しないで", "比較しないで"),
                                       ("C00004を選択しないで", "選択しないで")])
def test_negated_undo_compare_select_keep_candidate(text, word, source):
    w, sid = flow_with_candidate(source)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "stop" and f"「{word}」" in r["message"] and r["choices"] == []
    assert r["tool_calls"] == []                        # 元に戻す・比較・選択を呼ばない
    assert cand(r) == ("Z", "-3.000") and r["state"]["selected"] == "C00005"


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text", ["やめて", "元に戻して", "取り消して", "キャンセル"])
def test_plain_undo_still_discards_the_candidate(text, source):
    w, sid = flow_with_candidate(source)
    r = w.hear(sid, text, source=source)
    assert r["status"] == "ok" and cand(r) is None


NEW_NEGATIONS = [
    ("3mm下げません", "下げません"), ("下げずに3mm", "下げずに"), ("3mm下げず", "下げず"), ("下に3mmぬ", "ぬ"),
    ("下に3mmはなし", "なし"), ("下に3mmNG", "NG"), ("下に3mm NG", "NG"), ("3mm下げるのは不要", "不要"),
    ("下に3mmストップ", "ストップ"), ("下に3mm中止", "中止"), ("いや下に3mm", "いや"), ("下げるのは嫌3mm", "嫌"),
    ("3mm下げるなよ", "下げるなよ"), ("3mm下げんな", "下げんな"), ("下に3mmはいらない", "いらない"),
]


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text,word", NEW_NEGATIONS)
def test_more_negation_words_stop(text, word, source):
    r = hear(text, source=source)
    assert r["status"] == "stop" and cand(r) is None and r["choices"] == [], (text, r.get("message"))
    assert f"「{word}」" in r["message"] and "変更しません" in r["message"]


@pytest.mark.parametrize("text", ["ストップ", "中止", "いらない", "いや"])
def test_negation_only_utterance_gets_the_negation_message(text):
    r = hear(text)
    assert r["status"] == "stop" and "変更しません" in r["message"] and "分かりませんでした" not in r["message"]


def test_merge_and_combine_keep_previous_negations():
    # 変異 N2・N3：前の発話の打ち消しを落とさない
    neg = ut.read_utterance("Y座標を下げないで")
    assert neg.negations
    assert ut.merge_answer(neg, ut.read_utterance("3ミリ")).negations == neg.negations
    assert ut.combine_axes(neg, ut.read_utterance("3mm下げて")).negations == neg.negations
    und = ut.read_utterance("Y座標を下げるのはやめて")
    assert und.undo_negations
    assert ut.merge_answer(und, ut.read_utterance("3ミリ")).undo_negations == und.undo_negations
    assert ut.combine_axes(und, ut.read_utterance("3mm下げて")).undo_negations == und.undo_negations
    assert ut.pre_check(ut.merge_answer(neg, ut.read_utterance("3ミリ"))).status == "stop"


def test_sokosoko_is_vague_but_kokokoko_is_unknown():
    r = hear("そこそこ下に3mm")
    assert r["status"] == "ask" and "量がはっきりしません" in r["message"] and "「そこそこ」" in r["message"]
    assert "分かりませんでした" not in r["message"]
    r = hear("ここここ下に3mm")
    assert r["status"] == "ask" and r["message"].startswith("「ここここ」が分かりませんでした")


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text,word", [("値を3mm下げて", "値"), ("エクス座標を3mm下げて", "エクス座標"),
                                       ("座標を3mm下げて", "座標")])
def test_b8_axis_letter_lost_both_paths(text, word, source):
    r = hear(text, source=source)
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [] and f"「{word}」" in r["message"]


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("seq", [["バイオ下げて", "3mm"], ["バイオ3mm", "下げて"]])
def test_b1_two_step_both_paths(seq, source):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    for t in seq:
        r = w.hear(sid, t, source=source)
    assert r["status"] in ("ask", "stop") and cand(r) is None


@pytest.mark.parametrize("source", ["voice", "text"])
@pytest.mark.parametrize("text", ["3mm下げてください", "えーと3mm下げて", "えーと、3mm下げて", "3mm下げてお願いします",
                                  "じゃあ3mm下げて", "あと3mm下げて", "もう3mm下げて", "3mm下へ", "この位置を3mm下げて"])
def test_checker023_should_sentences_both_paths(text, source):
    r = hear(text, source=source)
    assert r["status"] == "ok" and cand(r) == ("Z", "-3.000"), (text, r.get("message"))


@pytest.mark.parametrize("text,shown", [("えーとえーと下に3mm", "えーとえーと"), ("あのあの下に3mm", "あのあの"),
                                        ("あともう下に3mm", "あともう")])
def test_shown_unknown_word_is_not_cut(text, shown):
    r = hear(text)
    assert r["status"] == "ask" and r["message"].startswith(f"「{shown}」が分かりませんでした")


def test_negation_shown_word_is_not_cut():
    r = hear("下に3mmとか言わない")
    assert r["status"] == "stop" and "「言わない」" in r["message"]


def test_question_mark_is_unchanged_for_now():
    # 「？」の扱いは唐川さんの判断待ち。今は今までどおり候補になる（変えていないことの記録）
    assert cand(hear("3mm下げて？")) == ("Z", "-3.000")
