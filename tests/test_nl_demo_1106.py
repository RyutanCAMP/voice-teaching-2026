"""11/6 の撮影に向けた、自然な会話の受入テスト（唐川さんの最優先・2026-11-05）。

役割分担（唐川さんの方針）：Claude は言葉の意味をくみ取り、足りない情報をやさしく聞き返す。数・軸・位置・方向・範囲を
確かめて変更候補を作るのはプログラム。承認は人がボタンで行う。足りない単位・位置・方向・符号を推測で補わない。

- 案1-a（docs/DESIGN_NATURAL_DIRECTION_20261105.md の A1-01〜A1-55 から、プログラムの読み取りで決まるもの）：
  「軸 → 符号 → 方向」の順で、間に「軸」「の」（各1回まで）と空白だけが入る言い方を、「Y-方向」と同じに読む。
  符号の言葉は + − プラス マイナス だけ。それ以外の揺れ・2つの読み方・打ち消し・条件・単位の無い量は、今どおり候補にしない
- 本物の Claude は呼ばない（偽の Claude・モックの解釈・合成ジョブだけ）
- 同じ Claude が書いたテスト。外部の独立レビュー（チャッピー）の代わりにはならない
"""
from pathlib import Path

import pytest

import test_acceptance_nl_safety_1105 as A
from voice_teaching import claude_talk as talk
from voice_teaching import updown
from voice_teaching import utterance as ut


def hear_msg(r) -> str:
    return (r.get("hear") or {}).get("message", "") or ""


def shown(r) -> str:
    return (r.get("ask") or {}).get("reply", "") + hear_msg(r)


# ---- 1. 案1-a：読めるようにする言い方（選んでいる位置は C00003。上下の向きは未確認） ----
@pytest.mark.parametrize("text,target,delta", [
    ("Y-の方向に10mm", "C00003 Y", "-10.000"),                # A1-01
    ("Yマイナスの方向に10mm", "C00003 Y", "-10.000"),          # A1-02
    ("Yプラスの方向に10mm", "C00003 Y", "+10.000"),            # A1-03
    ("Y+の方向に10mm", "C00003 Y", "+10.000"),                 # A1-03
    ("Yのマイナス方向に10mm", "C00003 Y", "-10.000"),          # A1-04
    ("Yのマイナスの方向に10mm", "C00003 Y", "-10.000"),        # A1-05
    ("Yの-の方向に10mm", "C00003 Y", "-10.000"),               # A1-05
    ("Y軸マイナス方向に10mm", "C00003 Y", "-10.000"),          # A1-06
    ("Y軸のマイナス方向に10mm", "C00003 Y", "-10.000"),        # A1-06
    ("Y軸の-方向に10mm", "C00003 Y", "-10.000"),               # A1-06
    ("Y-の方向へ10mm", "C00003 Y", "-10.000"),                 # A1-07
    ("Y - の 方向 に 10 mm", "C00003 Y", "-10.000"),           # A1-08（音声認識の空白）
    ("Ｙ－の方向に１０ｍｍ", "C00003 Y", "-10.000"),           # A1-09（全角）
    ("ワイマイナスの方向に10ミリ", "C00003 Y", "-10.000"),     # A1-10
    ("Y MINUS の方向に 10 mm", "C00003 Y", "-10.000"),         # A1-10
    ("Y-の方向に1.5mm", "C00003 Y", "-1.500"),                 # A1-13（丸めない）
    ("Xの-の方向に5mm", "C00003 X", "-5.000"),                 # A1-15
    ("Zプラスの方向に3mm", "C00003 Z", "+3.000"),              # A1-15（Z は軸の文字。上下の照合に依らない）
    # A1-14 回帰：今と同じ候補
    ("Y-方向に10mm", "C00003 Y", "-10.000"),
    ("Yマイナス方向に10mm", "C00003 Y", "-10.000"),
    ("Yマイナス10mm", "C00003 Y", "-10.000"),
    ("Yのマイナス10mm", "C00003 Y", "-10.000"),
    ("Yの方向にマイナス10mm", "C00003 Y", "-10.000"),
])
def test_natural_direction_makes_the_same_candidate(tmp_path, text, target, delta):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    c = r["state"]["candidate"]
    assert c and c["target"] == target, (text, hear_msg(r))
    assert not r["state"]["approval"] and not r["state"]["can_export"]
    assert f"{delta}mm" in hear_msg(r), hear_msg(r)


def test_said_position_is_used_not_the_selected_one(tmp_path):
    """A1-12：C00003 を選んだまま「C00004をY-の方向に10mm」→ 言った位置 C00004。"""
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, "C00004をY-の方向に10mm")
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00004 Y" and "-10.000mm" in hear_msg(r)


@pytest.mark.parametrize("text", ["Y-の方向に10mm", "Yのマイナス方向に10mm", "Y軸マイナス方向に10mm"])
def test_natural_direction_reads_one_axis_and_one_sign(text):
    """読み方の中身：軸1つ・軸に付いた符号1つ・はぐれた符号なし・知らない言葉なし（同じ所を2回数えない）。"""
    f = ut.read_utterance(text)
    assert f.axes == ("Y",) and f.explicit_axes == ("Y",) and f.axis_signs == (-1,)
    assert f.stray_signs == 0 and not f.unknown_words and f.requested_value() == -10


# ---- 1'. 案1-a でも候補にしない（今どおり聞き返す・止める） ----
@pytest.mark.parametrize("text", [
    "Yの方向に10mm",                     # A1-16 符号なし（＋を補わない）
    "マイナスYの方向に10mm",             # A1-17 符号が先
    "Yーの方向に10mm",                   # A1-18 長音
    "Y正の方向に10mm", "Y負の方向に10mm",  # A1-19
    "Yマイナス側に10mm", "Y-の向きに10mm", "Y-の方へ10mm",  # A1-20
    "Y--方向に10mm", "Y+-の方向に10mm",   # A1-21 符号が2つ
    "Y-の方向にマイナス10mm", "Y-の方向に+10mm",  # A1-22・23 向きの指定が2つ
    "Y-の方向に10mm動かさないで", "Y-の方向に10mmは駄目", "Y-の方向に10mm、いや20mm",
    "Y-の方向じゃなくてY+の方向に10mm",  # A1-24・25 打ち消し
    "もしY-の方向に10mmなら", "Y-の方向に10mm動かしたら", "Y-の方向に10mmだったら",  # A1-26・27 条件
    "Y-の方向に10mm？", "Y-の方向に10mm動かしますか",  # A1-28 疑問
    "Y-の方向に10mm動かします",          # A1-29 「〜します」
    "Y-の方向に10mm、X+の方向に5mm",     # A1-30 変更が2つ
    "Y-とX-の方向に10mm", "XY-の方向に10mm",  # A1-31 方向が2つ
    "Y-の方向に10mmと20mm", "Y-の方向に10mmか20mm",  # A1-32 量が2つ
    "Y-の方向に10mmかX-の方向に5mm",     # A1-33
    "C00003とC00004をY-の方向に10mm",    # A1-34 位置が2つ
    "Y-の方向に10mm下げて",              # A1-35 Y と上下
    "Y-の方向に10mmを2回",               # A1-36 繰り返し
    "右のY-の方向に10mm",                # A1-37 右左
    "Y-の方向に1cm", "Y-の方向に1センチ",  # A1-38 単位違い（換算しない）
    "Y-の方向に10",                      # A1-39 単位なし
    "Y-の方向に十ミリ",                  # A1-40 漢数字
    "Y-の方向に.5mm", "Y-の方向に10,5mm",  # A1-41 区切りのある数字
    "Y-の方向に1.0000001mm",             # A1-42 細かすぎる小数
    "Y-の方向に0mm",                     # A1-43 移動量0
    "Y-の方向に25mm",                    # A1-44 1回の上限
    "Y-の方向に約10mm", "Y-の方向に10mmくらい", "たぶんY-の方向に10mm", "Y-の方向に少し",  # A1-46
    "Y-の方向以外に10mm", "Y-の方向の逆に10mm", "Y-の方向と反対に10mm",  # A1-47
    "Y-の方向に合計10mm", "Y-の方向にも10mm", "Y-の方向に10mmずつ", "Y-の方向に10mmまで",  # A1-48
])
def test_natural_direction_still_never_makes_an_unclear_candidate(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    assert not r["state"]["candidate"], (text, hear_msg(r))
    assert not r["state"]["approval"]


def test_natural_direction_with_pending_candidate_is_refused(tmp_path):
    """A1-54：承認待ちの候補があるときは、今どおり断る（新しい候補にしない）。"""
    w, _, sid = A.place_flow(tmp_path)
    first = w.talk(sid, "Y-方向に10mm")["state"]["candidate"]
    r = w.talk(sid, "Y-の方向に5mm")
    assert r["state"]["candidate"] == first and not r["state"]["approval"]


def test_natural_direction_candidate_needs_the_button(tmp_path):
    """A1-55：候補になっても承認されていない。承認ボタンを押すまで出力を断る。"""
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, "C00003をY-の方向に10mm")
    assert r["state"]["candidate"] and r["state"]["approval"] is None
    assert not w.export(sid)["ok"]


# ---- 2. 動きの説明の「終わります」は出す。状態の言い切りは隠す ----
@pytest.mark.parametrize("reply", [
    "最初の位置から動き、最後に元の位置へ戻って終わります。",
    "部品を置いたあと、上の位置へ移って終わります。",
])
def test_motion_ending_explanations_are_shown(reply):
    assert talk.reply_mvp_problem(reply) is None


@pytest.mark.parametrize("reply", [
    "変更が終わりました。", "作業は終わりです。", "これで終わりです。", "準備が終わりました",
    "書き換えて終わります。", "承認して終わります。", "出力して終わります。", "送って終わります。",
    "直して終わります。", "変更は終わります。", "動きの変更が終わります。",
])
def test_state_endings_stay_hidden(reply):
    """回帰：「終わり」を状態の言い切りに使った文は、これまでどおり出さない。"""
    assert talk.reply_mvp_problem(reply) is not None


# ---- 3. 打ち消しの言葉は、言葉のまとまり全体で引用する（切れ端にしない） ----
@pytest.mark.parametrize("text,whole", [("やっぱりいいや", "やっぱりいいや"), ("もういいや", "もういいや")])
def test_negation_word_is_quoted_whole(tmp_path, text, whole):
    w, _, sid = A.pending_three(tmp_path)
    r = w.talk(sid, text)
    s = shown(r)
    assert f"「{whole}」" in s, s


@pytest.mark.parametrize("text,word", [("3mm下げないで", "下げないで"), ("Zマイナス3ミリは駄目", "駄目"),
                                       ("C00003を移動しないで", "移動しないで")])
def test_negation_word_quotes_unchanged(text, word):
    """回帰：今まで正しく引用していた打ち消しの言葉は、同じに引用する。"""
    assert ut.read_utterance(text).negations == (word,)


@pytest.mark.parametrize("text", ["やっぱりいいや", "もういいや"])
def test_negation_is_still_found(text):
    """打ち消しとして見つけることは変えない（候補を捨てる・残すの扱いは P0 の担当）。"""
    f = ut.read_utterance(text)
    assert f.negations or f.undo_negations


# ---- 4. 単位が無いときは、単位をやさしく聞く（単位を補わない） ----
@pytest.mark.parametrize("text", ["Z+3", "Zのマイナス5", "Zプラス3", "Z-3", "Yマイナス10", "Y-の方向に10"])
def test_missing_unit_question(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert "単位" in msg and ("ミリ" in msg or "mm" in msg), msg
    assert "どこに付くか" not in msg, msg
    assert msg.rstrip("）").endswith("？") or "？" in msg, msg  # 聞き返し（命令口調にしない）


@pytest.mark.parametrize("text", ["Z+3", "Zプラス3"])
def test_missing_unit_answer_is_not_borrowed(tmp_path, text):
    """単位を聞いたあとの「ミリ」だけの答えで、前の数に単位を補った候補を作らない。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, text)
    r = w.talk(sid, "ミリ")
    assert not r["state"]["candidate"]


def test_stray_sign_question_is_kept_when_the_sign_is_really_loose(tmp_path):
    """回帰：数にも軸にも付かない符号は、今どおり「どこに付くか」を聞く（単位の話にしない）。"""
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, "マイナスYの方向に10mm")
    assert not r["state"]["candidate"] and "どこに付くか" in hear_msg(r)


# ---- 5. 上下の向きが未確認の「上に3mm」：候補にせず、やさしく確かめる ----
@pytest.mark.parametrize("text", ["上に3mm", "3mm下げて"])
def test_unconfirmed_updown_question_is_gentle(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert msg == updown.MSG_UNCONFIRMED
    assert "？" in msg and "Zプラス" in msg and "Zマイナス" in msg  # 片方だけを例にしない（上＝Z＋と決めつけない）
    assert not ut.contains_forbidden(msg)


# ---- 6. 聞き返しへの答え（C1・C2）：回帰 ----
def test_c1_sign_question_then_minus(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    q = w.talk(sid, "Yに10mm動かして")
    assert not q["state"]["candidate"] and "プラスですか、マイナスですか？" in hear_msg(q)
    r = w.talk(sid, "マイナス")
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Y" and "-10.000mm" in hear_msg(r) and not r["state"]["approval"]


def test_c2_amount_then_axis_and_sign(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    q = w.talk(sid, "10mm動かして")
    assert not q["state"]["candidate"] and "どの方向" in hear_msg(q)
    r = w.talk(sid, "Yマイナス")
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Y" and "-10.000mm" in hear_msg(r) and not r["state"]["approval"]
