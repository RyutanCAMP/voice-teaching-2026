"""11/6 の撮影に向けた、自然な会話の受入テスト（唐川さんの最優先・2026-11-05）。

役割分担（唐川さんの方針）：Claude は言葉の意味をくみ取り、足りない情報をやさしく聞き返す。数・軸・位置・方向・範囲を
確かめて変更候補を作るのはプログラム。承認は人がボタンで行う。足りない単位・位置・方向・符号を推測で補わない。

- 案1-a（設計書 docs/DESIGN_NATURAL_DIRECTION_20261105.md の A1-01〜A1-55 から、プログラムの読み取りで決まるもの。
  設計書はこのブランチには無く、ブランチ docs/direction-words-design（59fec81）にある）：
  「軸 → 符号 → 方向」の順で、間に「軸」「の」（各1回まで）と空白だけが入る言い方を、「Y-方向」と同じに読む。
  符号の言葉は + − プラス マイナス だけ。それ以外の揺れ・2つの読み方・打ち消し・条件・単位の無い量は、今どおり候補にしない
- 本物の Claude は呼ばない（偽の Claude・モックの解釈・合成ジョブだけ）
- 同じ Claude が書いたテスト。外部の独立レビュー（チャッピー）の代わりにはならない
"""
from pathlib import Path

import pytest

import test_acceptance_nl_safety_1105 as A
import test_claude_talk as T
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


# ---- 5. 上下の向きが未確認の「上に3mm」：意図を受け止めてから、分からないことだけを聞く（唐川さんの決定 11/5） ----
# 数と上下の言葉はプログラムが人の文から読んで入れる。Zプラス・Zマイナスは決めない。候補は作らない。
# 状態の表示（state.updown.message・候補の表示）は今の文（updown.MSG_UNCONFIRMED）のまま。変えるのは会話の聞き返しだけ。
# 「3Dの矢印を見ながら確認できます」は出さない（画面が 3D と 2D を切り替えられ、サーバーからは 3D が出ているか確かめられない）
@pytest.mark.parametrize("text,head,word", [
    ("上に3mm", "3mm上に動かしたいんですね。", "上"),
    ("下に3mm", "3mm下に動かしたいんですね。", "下"),
    ("3ミリ上げて", "3mm上げたいんですね。", "上"),
    ("3mm下げて", "3mm下げたいんですね。", "下"),
    ("上へ5mm", "5mm上へ動かしたいんですね。", "上"),
    ("C00004を上に1.5mm", "1.5mm上に動かしたいんですね。", "上"),
    ("上方向に3mm", "3mm上方向に動かしたいんですね。", "上"),
])
def test_unconfirmed_updown_question_takes_the_intent_first(tmp_path, text, head, word):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"] and not r["state"]["approval"]
    assert msg.startswith(head), msg
    assert f"この設備では、まだ「{word}」の方向が決まっていません。" in msg, msg
    assert "ZプラスとZマイナスのどちらになりますか？" in msg, msg
    assert "3D" not in msg and not ut.contains_forbidden(msg)
    # 状態の表示は今の文のまま
    assert r["state"]["updown"]["message"] == updown.MSG_UNCONFIRMED


@pytest.mark.parametrize("text,word", [("上に動かして", "上"), ("下げて", "下")])
def test_unconfirmed_updown_without_amount_asks_direction_and_amount(tmp_path, text, word):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert msg.startswith(f"{word}"), msg  # 言っていない量を入れない
    assert f"この設備では、まだ「{word}」の方向が決まっていません。" in msg, msg
    assert "ZプラスとZマイナスのどちらに、どれだけ動かしますか？" in msg, msg


@pytest.mark.parametrize("text", ["上に3mm", "3mm下げて", "上に三ミリ", "上に3cm"])
def test_unconfirmed_updown_never_makes_a_candidate(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"] and not r["state"]["approval"]
    assert "Zプラス" in msg and "Zマイナス" in msg  # 片方だけを例にしない（上＝Z＋と決めつけない）
    assert not ut.contains_forbidden(msg)


@pytest.mark.parametrize("text", ["上に三ミリ", "上に3cm"])
def test_unconfirmed_updown_does_not_quote_an_unclear_amount(tmp_path, text):
    """量がはっきりしないときは、量を言い切らない（「3mm上に動かしたい」と読み替えない）。"""
    w, _, sid = A.place_flow(tmp_path)
    msg = hear_msg(w.talk(sid, text))
    assert msg.startswith("上に動かしたいんですね。"), msg


def test_unconfirmed_updown_answer_with_axis_and_amount_makes_the_candidate(tmp_path):
    """聞き返しのあと「Zマイナス3ミリ」と言えば、ふつうの指示として候補になる（承認はボタンだけ）。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "上に3mm")
    r = w.talk(sid, "Zマイナス3ミリ")
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Z" and "-3.000mm" in hear_msg(r) and not r["state"]["approval"]


def test_unconfirmed_updown_sign_only_answer_is_not_used(tmp_path):
    """「プラス」だけの答えで、上＝Z＋として候補にしない。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "上に3mm")
    r = w.talk(sid, "プラス")
    assert not r["state"]["candidate"]


def test_unconfirmed_updown_mismatch_state_keeps_its_message(tmp_path):
    """回帰：未確認でない状態（不一致・変更済み）の文は、今の状態ごとの文のまま（会話の文に変えるのは未確認だけ）。"""
    st = updown.UpdownStatus(updown.MISMATCH, updown.MSG_MISMATCH)
    v = ut.pre_check(ut.read_utterance("上に3mm"), "C00003", st)
    assert v.status == "ask" and v.message == updown.MSG_MISMATCH


def test_confirmed_updown_is_not_changed(tmp_path):
    """回帰：上下の向きが確認済みなら、今どおり Z+3 の候補（聞き返さない）。"""
    w, _, sid = A.place_flow(tmp_path, confirmed=True)
    r = w.talk(sid, "上に3mm")
    assert r["state"]["candidate"]["target"] == "C00003 Z"


# ---- 7. 単位の聞き返しのあとも、言った符号を捨てない（Safety High-1・Medium-1・Medium-2） ----
@pytest.mark.parametrize("first,second,confirmed", [
    ("Yプラスの方向にマイナス3", "3ミリ", False),
    ("Yプラスの方向に-3", "3ミリ", False),
    ("C00004をYプラスの方向にマイナス3", "3ミリ", False),
    ("Yプラスの方向にマイナス3", "3mm", False),
    ("Y+方向に-3", "3ミリ", False),
    ("Y-方向に-3", "3ミリ", False),
    ("上にマイナス3", "3ミリ", True),
])
def test_sign_said_with_a_unitless_number_is_not_dropped(tmp_path, first, second, confirmed):
    w, _, sid = A.place_flow(tmp_path, confirmed=confirmed)
    r1 = w.talk(sid, first)
    assert not r1["state"]["candidate"]
    assert "向きの指定が2つあります" in hear_msg(r1), hear_msg(r1)  # 1回目で止める（mm まで言ったときと同じ）
    r2 = w.talk(sid, second)
    assert not r2["state"]["candidate"], hear_msg(r2)


@pytest.mark.parametrize("text", ["Yプラスの方向にマイナス3", "Y+方向に-3", "Y-方向に-3", "速度を+10上げて", "Z+3"])
def test_unitless_number_sign_is_kept_in_facts(text):
    f = ut.read_utterance(text)
    assert f.unclear_signs and f.stray_signs == 0


@pytest.mark.parametrize("answer", ["マイナス3ミリ", "-3mm"])
def test_sign_mismatch_between_question_and_answer_is_shown(tmp_path, answer):
    """「Z+3」→「マイナス3ミリ」：食い違いを示して確かめる。どちらの候補も作らない。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    r = w.talk(sid, answer)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert "最初はプラス、今はマイナスと聞こえました。どちらですか？" in msg, msg


def test_sign_mismatch_after_sign_question(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    w.talk(sid, "3ミリ")  # 前の + は使わず、もう一度プラスかマイナスかを聞く（今の扱い）
    r = w.talk(sid, "マイナス")
    assert not r["state"]["candidate"]
    assert "最初はプラス、今はマイナスと聞こえました" in hear_msg(r)


def test_earlier_sign_of_an_unclear_amount_is_not_dropped(tmp_path):
    """「Yマイナス10mmくらい」→「10mm」→「プラス」：前の「マイナス」を捨てて Y+10 にしない。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Yマイナス10mmくらい")
    w.talk(sid, "10mm")
    r = w.talk(sid, "プラス")
    assert not r["state"]["candidate"]


def test_unit_answer_without_sign_still_asks_the_sign(tmp_path):
    """「Z+3」→「3ミリ」：前の + を使わず、もう一度聞く（推測しない。今の扱いを変えない）。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    r = w.talk(sid, "3ミリ")
    assert not r["state"]["candidate"] and "プラスですか、マイナスですか" in hear_msg(r)


@pytest.mark.parametrize("seq", [("Z+3", "+3ミリ"), ("Z+3", "3ミリ", "プラス")])
def test_same_sign_said_again_makes_the_candidate(tmp_path, seq):
    """同じ向きを言い直したときだけ候補になる（承認はボタンだけ）。"""
    w, _, sid = A.place_flow(tmp_path)
    for s in seq:
        r = w.talk(sid, s)
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Z" and "+3.000mm" in hear_msg(r) and not r["state"]["approval"]


@pytest.mark.parametrize("text,bad", [("Z+3", ("マイナス",)), ("Zのマイナス5", ("プラス",)),
                                      ("速度を+10", ("下げ",)), ("速度を-10", ("上げ",))])
def test_unit_question_example_does_not_flip_the_sign(tmp_path, text, bad):
    w, _, sid = A.place_flow(tmp_path)
    msg = hear_msg(w.talk(sid, text))
    assert "単位" in msg
    assert not any(b in msg for b in bad), msg


# ---- 8. 案1-a の読み方で、一覧より少し広く読む形（Safety Low-1。候補になる形として決める。唐川さんに確認する） ----
@pytest.mark.parametrize("text", ["Y-の軸方向に10mm", "Yマイナスの軸方向に10mm", "Yマイナスの方向で10mm",
                                  "Yマイナスの方向10mm"])
def test_wider_natural_direction_forms_make_the_candidate(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Y" and "-10.000mm" in hear_msg(r) and not r["state"]["approval"]


# ---- 9. 説明の経路の全体で、安全の言葉の付いた「終わります」は隠す（Safety Low-2） ----
@pytest.mark.parametrize("reply", ["同じ動きをくり返したあと、安全に戻って終わります。",
                                   "同じ動きをくり返したあと、ぶつからずに戻って終わります。"])
def test_safety_words_with_motion_ending_stay_hidden_in_the_explanation_path(tmp_path, reply):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    fake.add(T.answer(reply, (21, 22, 23, 24, 25, 26)))
    r = w.ask(sid, "どんな動き？")
    assert r["reply_by"] == "app" and reply not in r["reply"] and "戻って終わります" not in r["reply"], r["reply"]


def test_motion_ending_is_shown_in_the_explanation_path(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    reply = "同じ動きをくり返したあと、最後の位置へ移って終わります。"
    fake.add(T.answer(reply, (21, 22, 23, 24, 25, 26)))
    r = w.ask(sid, "どんな動き？")
    assert r["reply_by"] == "claude" and r["reply"] == reply


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


# ---- 10. QA 001（92ee57d）の指摘 ----
# M1：Claude には座標値を送っていないので、移動の大きさ（大きく・少し など）はデータから言えない。その言葉の入った説明は隠す。
# 「ゆっくり」「速く」は、ジョブの速度があるので隠さない
MAGNITUDE_REPLIES = [
    "はじめに大きく動いたあと、同じ動きをくり返し、最後に別の場所へ動いて終わります。",
    "はじめに大きく動いてから、同じ動きをくり返し、最後に別の場所へ移ります。",
    "Z方向に少し動いて戻る動きをくり返します。",
    "わずかに動いたあと、最初の位置へ戻ります。",
    "小さく動く動作を同じ形でくり返します。",
    "遠くの位置へ移ってから、同じ動きをくり返します。",
    "長く動いたあと、最初の位置へ戻ります。",
    "ちょっと動いてから、同じ動きをくり返します。",
]


@pytest.mark.parametrize("reply", MAGNITUDE_REPLIES)
def test_movement_size_words_are_hidden(reply):
    assert talk.reply_mvp_problem(reply) is not None


@pytest.mark.parametrize("reply", ["同じ動きをゆっくりくり返したあと、最後の位置へ移って終わります。",
                                   "速く動いたあと、同じ動きをくり返します。",
                                   "ボタンを押す動作をくり返したあと、最後に別の位置へ関節の動きで移って終わります。"])
def test_speed_words_are_not_size_words(reply):
    assert talk.reply_mvp_problem(reply) is None


def test_movement_size_word_hidden_in_the_explanation_path(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    reply = "はじめに大きく動いたあと、同じ動きをくり返し、最後に別の場所へ動いて終わります。"
    fake.add(T.answer(reply, (21, 22, 23, 24, 25, 26)))
    r = w.ask(sid, "この動きについて確かに言えることだけ教えて")
    assert r["reply_by"] == "app" and reply not in r["reply"]


def test_push_and_end_is_shown_as_a_job_motion(tmp_path):
    """L2：「ボタンを押して終わります」は、ジョブの動作の説明として出す（今の動きを決めておく。唐川さんに確認する）。
    「承認ボタン」「出力ボタン」は「承認」「出力」で今どおり隠す。"""
    assert talk.reply_mvp_problem("同じ動きをくり返し、最後にボタンを押して終わります。") is None
    assert talk.reply_mvp_problem("最後に承認ボタンを押して終わります。") is not None
    assert talk.reply_mvp_problem("最後に出力ボタンを押して終わります。") is not None


# M2：打ち消しの引用は、言葉のまとまりの初めから（切れ端にしない）
@pytest.mark.parametrize("text,whole", [
    ("じゃあもういいや", "もういいや"), ("ああもういいや", "もういいや"), ("えっともういいや", "もういいや"),
    ("やっぱりもういいや", "もういいや"), ("今日はもういいや", "もういいや"), ("それならもういいや", "もういいや"),
    ("うーんやっぱりいいや", "やっぱりいいや"), ("これもう動かさないで", "もう動かさないで"),
])
def test_negation_quote_starts_at_the_phrase(text, whole):
    f = ut.read_utterance(text)
    assert f.negations == (whole,), f.negations


@pytest.mark.parametrize("text", ["じゃあもういいや", "うーんやっぱりいいや", "これもう動かさないで"])
def test_negation_quote_in_the_reply_is_whole(tmp_path, text):
    w, _, sid = A.pending_three(tmp_path)
    s = shown(w.talk(sid, text))
    assert "「ういいや」" not in s and "「んやっぱり" not in s and "「う動かさないで」" not in s, s


# L1：2つの数・2つの変更は、単位より先に「1回に1つ」で聞く
@pytest.mark.parametrize("text", ["Z+3、Y+4", "Y-の方向に10と20", "Z+3と5mm"])
def test_two_numbers_are_asked_one_at_a_time_before_the_unit(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert "1つずつ" in msg and "単位が付いていません" not in msg, msg


# ---- 11. QA 002（7c6c955）の指摘 ----
# N1：前の発話に出た符号の言葉（+ − プラス マイナス・上 下 など）は、読み取りのどの道を通っても全部残す。
# 答えと合わせたとき、前の符号の言葉と1つでも食い違えば候補を作らない。前の文の符号の言葉が互いに食い違えば止める
N1_FIRST = [
    "Yプラス方向にマイナス", "Y+方向にマイナス", "Yプラス方向、マイナス", "Y+にマイナス", "Yプラスにマイナス",
    "Y+方向に-3.0000001", "Y+方向にマイナス3,5", "Yプラス方向に-3.5.", "Y+方向にマイナス3.0000001",
    "Yプラスの方向にマイナス三ミリ", "Yのプラス方向でマイナス", "Yマイナス方向に+3,0",
    # 似た言い方（自分で足した）
    "Yプラスの方向にマイナス", "Y軸プラス方向にマイナス", "Ｙ＋方向にマイナス", "Y + 方向に マイナス", "Yプラス方向にマイナス少し",
    "Yマイナスにプラス", "Y-方向に+", "Yのマイナス方向にプラス三ミリ", "Xプラス方向にマイナス", "Zマイナス方向にプラス",
    "C00004をYプラス方向にマイナス",
]


@pytest.mark.parametrize("second", ["3ミリ", "3mm", "3ミリです"])
@pytest.mark.parametrize("first", N1_FIRST)
def test_conflicting_sign_words_are_never_merged_into_a_candidate(tmp_path, first, second):
    w, _, sid = A.place_flow(tmp_path)
    r1 = w.talk(sid, first)
    assert not r1["state"]["candidate"], hear_msg(r1)
    r2 = w.talk(sid, second)
    assert not r2["state"]["candidate"], (first, second, hear_msg(r2))
    assert not r2["state"]["approval"]


@pytest.mark.parametrize("first", ["上にマイナス", "下にプラス", "上にマイナス三ミリ"])
def test_conflicting_updown_and_sign_words_confirmed(tmp_path, first):
    w, _, sid = A.place_flow(tmp_path, confirmed=True)
    w.talk(sid, first)
    r = w.talk(sid, "3ミリ")
    assert not r["state"]["candidate"], hear_msg(r)


@pytest.mark.parametrize("text", ["Yプラス方向にマイナス", "Y+方向に-3.0000001", "Yプラスの方向にマイナス三ミリ", "上にマイナス"])
def test_every_sign_word_is_kept_in_facts(text):
    f = ut.read_utterance(text)
    assert set(f.said_signs) == {1, -1}, f.said_signs


def test_conflicting_earlier_signs_stop_without_merging(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Yプラス方向にマイナス")
    r = w.talk(sid, "3ミリ")
    assert r["hear"]["status"] == "stop" and "向き" in hear_msg(r), hear_msg(r)
    r = w.talk(sid, "プラス")  # 止めたあとの答えは、前の発話に足さない
    assert not r["state"]["candidate"]


@pytest.mark.parametrize("seq", [
    ("Zプラス", "3ミリ"),              # 軸と符号 → 量（今どおり候補）
    ("Yに10mm", "マイナス"),           # C1
    ("10mm動かして", "Yマイナス"),     # C2
    ("Z+3", "3ミリ", "プラス"),        # 同じ向きの言い直し
])
def test_consistent_sign_words_still_make_the_candidate(tmp_path, seq):
    """回帰：前と今の符号の言葉が同じなら、今どおり候補になる（承認はボタンだけ）。"""
    w, _, sid = A.place_flow(tmp_path)
    for s in seq:
        r = w.talk(sid, s)
    assert r["state"]["candidate"] and not r["state"]["approval"], hear_msg(r)


def test_confirmed_down_then_amount_is_still_a_candidate(tmp_path):
    """回帰：上下が確認済みの「下げて」→「3ミリ」は、今どおり Z−3（前の「下」と今の向きが同じ）。"""
    w, _, sid = A.place_flow(tmp_path, confirmed=True)
    w.talk(sid, "下げて")
    r = w.talk(sid, "3ミリ")
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Z" and "-3.000mm" in hear_msg(r)


# N3：速度の話（「速度」「%」がある）なら、上下のやさしい聞き返し（位置の上下として受け止める文）を使わない
@pytest.mark.parametrize("seq", [("速度を-10", "10%下げて"), ("10%下げて",), ("10%上げて",)])
def test_speed_talk_is_not_taken_as_position_updown(tmp_path, seq):
    w, _, sid = A.place_flow(tmp_path)
    for s in seq:
        r = w.talk(sid, s)
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert "動かしたいんですね" not in msg and "下げたいんですね" not in msg and "上げたいんですね" not in msg, msg


# 唐川さんの判断（11/5）：「ボタンを押して終わります」は JOB の内容の説明として表示してよい。
# 実際にボタンを押した・操作が終わった、と取れる文は出さない
@pytest.mark.parametrize("reply", ["同じ動きをくり返し、最後にボタンを押して終わります。",
                                   "ボタンを押す動作をくり返したあと、最後の位置へ移って終わります。"])
def test_job_content_push_and_end_is_shown(reply):
    assert talk.reply_mvp_problem(reply) is None


@pytest.mark.parametrize("reply", ["ボタンを押しました。", "ボタンを押して完了しました。", "ボタンを押し終わりました。",
                                   "操作が終わりました。", "ボタンを押して終わりました。", "ボタンを押し終わります。",
                                   "ボタンを押したので、終わりです。", "押して操作を完了します。", "ボタンを押して終了です。"])
def test_done_push_or_done_operation_is_hidden(reply):
    assert talk.reply_mvp_problem(reply) is not None


# ---- 12. Safety 002（7c6c955）の指摘 ----
# Medium-1：聞き返しのもとの発話で符号の言葉を言っていたら、ボタンの道でも、全部言い直す道でも、食い違う向きで
# 候補を作る前に必ず食い違いを示して確かめる。同じ向きなら今までどおり候補にする
def _choice(r, label_part):
    return next(c["text"] for c in (r.get("hear") or {}).get("choices", []) if label_part in c["label"])


def test_sign_question_mentions_the_sign_said_first(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    r = w.talk(sid, "3ミリ")
    msg = hear_msg(r)
    assert msg.startswith("Z方向のプラスですか、マイナスですか？") and "最初は「プラス」と聞こえました" in msg, msg


def test_button_against_the_first_sign_is_confirmed_first(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    q = w.talk(sid, "3ミリ")
    r = w.talk(sid, _choice(q, "マイナス"))
    assert not r["state"]["candidate"]
    assert "最初はプラス、今はマイナスと聞こえました" in hear_msg(r), hear_msg(r)


def test_button_with_the_first_sign_makes_the_candidate(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "Z+3")
    q = w.talk(sid, "3ミリ")
    r = w.talk(sid, _choice(q, "プラス"))
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Z" and "+3.000mm" in hear_msg(r) and not r["state"]["approval"]


@pytest.mark.parametrize("first,again,said,now", [("Z-3", "Zプラス3ミリ", "マイナス", "プラス"),
                                                  ("Z+3", "Zマイナス3ミリ", "プラス", "マイナス"),
                                                  ("Yプラス", "Yマイナス3ミリ", "プラス", "マイナス")])
def test_full_restatement_against_the_first_sign_is_confirmed_first(tmp_path, first, again, said, now):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, first)
    r = w.talk(sid, again)
    assert not r["state"]["candidate"]
    assert f"最初は{said}、今は{now}と聞こえました" in hear_msg(r), hear_msg(r)
    # 食い違いを示したあと、もう一度はっきり言えば、ふつうの指示として候補になる（承認はボタンだけ）
    r = w.talk(sid, again)
    assert r["state"]["candidate"] and not r["state"]["approval"], hear_msg(r)


@pytest.mark.parametrize("first,again", [("Z-3", "Zマイナス3ミリ"), ("Z+3", "Zプラス3ミリ")])
def test_full_restatement_with_the_first_sign_makes_the_candidate(tmp_path, first, again):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, first)
    r = w.talk(sid, again)
    assert r["state"]["candidate"] and not r["state"]["approval"], hear_msg(r)


# Medium-2：動く大きさ・距離を見積もる言い方も隠す（網には限りがある。残存リスクに書く）
@pytest.mark.parametrize("word", ["ずいぶん", "一気に", "大胆に", "ぐんと", "おおきく", "ちいさく", "大移動", "最大まで",
                                  "微妙に", "微小に", "広く", "狭い範囲で", "数センチ", "数ミリ", "十センチ", "何センチか"])
def test_more_size_and_distance_words_are_hidden(word):
    assert talk.reply_mvp_problem(f"はじめに{word}動いてから、同じ動きをくり返します。") is not None


# Low-2：速度の食い違いの文は、速度の言い方にする（「軸と…」を出さない）
def test_speed_sign_mismatch_uses_speed_words(tmp_path):
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "C00003の速度を+10")
    r = w.talk(sid, "-10%")
    msg = hear_msg(r)
    assert not r["state"]["candidate"]
    assert "聞こえました" in msg and "軸" not in msg and "速度" in msg, msg
