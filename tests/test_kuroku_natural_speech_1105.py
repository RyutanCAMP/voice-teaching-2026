"""受入テスト：自然な話し言葉の受け取り（クローク 2026-11-05 夜。唐川さんの実マイクで見つかった）。

唐川さんの実マイク：「この位置を上に3mm上げたい」→「「たい」が分かりませんでした。軸と向きと量を…」（厳しすぎる）。
方針（唐川さん・チャッピー 11/5 夜）：言葉の理解は柔軟に、安全判定は厳格に。
- 文の終わりの希望の言い方（〜たい・〜たいです・〜てほしい・〜てもらいたい・〜ていただきたい・〜たいんだけど など）は、
  同じ文の「〜て」（お願い）と同じに読む。読んだあとの検査（上下・向きの確かめ・上限・単位・打ち消し・位置・承認待ち）は全部そのまま
- 「3ミリメートル」は「3ミリ」と同じ量。「3メートル」「3センチ」は今どおり聞き返す
- 候補は今と同じ条件でしか作らない。上下・向きが未確認なら、画面のボタンで確かめるまで作らない。承認はボタンだけ
- P0（取消・言い直し）はシクロの担当なので、ここでは扱わない
"""
from __future__ import annotations

import json

import pytest

import test_acceptance_nl_safety_1105 as A
import test_claude_talk as T
from voice_teaching import claude_explain as cx
from voice_teaching import utterance as ut


def hear(r) -> dict:
    return r.get("hear") or {}


def labels(r) -> list:
    return [c["label"] for c in hear(r).get("choices") or []]


def cand(r):
    return (r.get("state") or {}).get("candidate")


def outcome(tmp_path, text, select="C00003"):
    """1つの発話を新しい画面で送り、比べるもの（候補・返事の文・ボタン・Claude を呼んだ回数）を返す。"""
    w, fake = T.flow(tmp_path)
    sid = w.load(A.DEMO_C)["session_id"]
    if select:
        w.select(sid, select)
    r = w.talk(sid, text, "voice")
    c = dict(cand(r) or {})
    c.pop("heard", None)  # 聞こえた文（そのまま残すもの）だけは違ってよい。ほかの中身は全部同じであること
    return {"candidate": json.dumps(c, ensure_ascii=False, sort_keys=True) if c else None,
            "message": hear(r).get("message"), "labels": labels(r), "via": r.get("via"),
            "api": len(fake.requests), "can_approve": r["state"].get("can_approve")}


# ---- 1. 最優先：唐川さんの実マイクの発話 ----
def test_karakawa_mic_utterance_goes_to_the_direction_check(tmp_path):
    w, fake, sid = A.place_flow(tmp_path)
    r = w.talk(sid, "この位置を上に3mm上げたい", "voice")
    msg = hear(r).get("message", "")
    assert not cand(r) and not r["state"]["approval"]
    assert msg.startswith("3mm上に動かしたいんですね。"), msg
    assert "上は Zプラス で合っていますか？" in msg, msg
    assert "「たい」" not in msg and "分かりませんでした" not in msg, msg
    assert labels(r) == ["合っている", "違う"]
    assert not w.approve(sid, "QA")["ok"]  # 確かめる前は承認できるものが無い
    assert len(fake.requests) == 0  # 読み取りはプログラム。Claude は呼ばない
    # ［合っている］を押して初めて候補（C00003 Z +3）。承認はまだ
    yes = next(c["text"] for c in hear(r)["choices"] if c["label"] == "合っている")
    r2 = w.hear(sid, yes, "text")
    c = r2["state"]["candidate"]
    assert c and c["target"] == "C00003 Z" and c["delta"] == "+3.000", c
    assert not r2["state"]["approval"]


def test_karakawa_mic_utterance_by_voice_yes_is_not_a_confirmation(tmp_path):
    """向きの確かめはボタンだけ（d・唐川さんの決定 11/5）。声の「そうです」「はい」では候補にしない。"""
    w, _, sid = A.place_flow(tmp_path)
    w.talk(sid, "この位置を上に3mm上げたい", "voice")
    for said in ("そうです", "はい", "合っている"):
        r = w.talk(sid, said, "voice")
        assert not cand(r), said


# ---- 2. 希望の言い方は、同じ文の「〜て」と全く同じに扱う（新しい道を作らない） ----
PAIRS = [
    ("この位置を上に3mm上げたい", "この位置を上に3mm上げて"),
    ("この位置を上に3mm上げたいです", "この位置を上に3mm上げて"),
    ("3mm上げたい", "3mm上げて"),
    ("3ミリ下げたいです", "3ミリ下げて"),
    ("この位置を3ミリ下げてほしい", "この位置を3ミリ下げて"),
    ("この位置を3ミリ下げてほしいです", "この位置を3ミリ下げて"),
    ("Zプラスに3mm動かしたい", "Zプラスに3mm動かして"),
    ("Zプラスに3mm動かしたいです", "Zプラスに3mm動かして"),
    ("Yマイナスに10ミリずらしたいです", "Yマイナスに10ミリずらして"),
    ("Yマイナス10ミリ動かしてもらいたい", "Yマイナス10ミリ動かして"),
    ("Yマイナス10ミリ動かしていただきたいです", "Yマイナス10ミリ動かして"),
    ("10ミリ動かしたいんだけど", "10ミリ動かして"),
    ("Zプラス方向に5ミリ動かしたいんですけど", "Zプラス方向に5ミリ動かして"),
    ("Zプラス方向に5ミリ移動したい", "Zプラス方向に5ミリ移動して"),
    ("Zプラス方向に5ミリ移動させたい", "Zプラス方向に5ミリ移動させて"),
    ("Yマイナスに10ミリ寄せたい", "Yマイナスに10ミリ寄せて"),
    ("えーと、Zプラス3ミリ動かしたい", "えーと、Zプラス3ミリ動かして"),
    ("C00004をZプラス3ミリ動かしたい", "C00004をZプラス3ミリ動かして"),
    # 安全側で止まる形も、「〜て」と同じに止まる
    ("右に10mm動かしたい", "右に10mm動かして"),
    ("Zプラス方向に30ミリ動かしたい", "Zプラス方向に30ミリ動かして"),
    ("Zプラス3動かしたい", "Zプラス3動かして"),
    ("Zプラス3センチ上げたい", "Zプラス3センチ上げて"),
    ("Zプラス約3ミリ動かしたい", "Zプラス約3ミリ動かして"),
    ("少し上げたい", "少し上げて"),
    ("上げたい", "上げて"),
]


@pytest.mark.parametrize("wish,plain", PAIRS)
def test_wish_ending_is_read_exactly_like_the_request(tmp_path, wish, plain):
    a = outcome(tmp_path / "a", wish)
    b = outcome(tmp_path / "b", plain)
    assert a == b, (wish, a, b)


@pytest.mark.parametrize("wish,plain", [("この位置を上に3mm上げたい", "この位置を上に3mm上げて"),
                                          ("Zプラス3ミリ動かしたい", "Zプラス3ミリ動かして")])
def test_wish_ending_without_a_selected_position_is_read_like_the_request(tmp_path, wish, plain):
    """位置を選んでいないときも「〜て」と同じ（選んでいる位置が無ければ、今どおり位置を聞く）。"""
    a = outcome(tmp_path / "a", wish, select=None)
    b = outcome(tmp_path / "b", plain, select=None)
    assert a == b and a["candidate"] is None, (a, b)


def test_wish_ending_with_a_pending_candidate_is_read_like_the_request(tmp_path):
    """承認待ちの候補があるときも「〜て」と同じ返事（候補を作り直さない。P0 の扱いはシクロの担当なので比べるだけ）。"""
    out = []
    for i, text in enumerate(("Zプラス5ミリ動かしたい", "Zプラス5ミリ動かして")):
        w, _, sid = A.place_flow(tmp_path / str(i))
        w.talk(sid, "Zプラス3ミリ")
        r = w.talk(sid, text, "voice")
        out.append((hear(r).get("message"), r["state"]["candidate"]["delta"]))
    assert out[0] == out[1] and out[0][1] == "+3.000", out


# ---- 3. 止めるべきものは今どおり止める ----
@pytest.mark.parametrize("text", [
    "3mm上げたくない", "Zプラス3ミリ動かしたくない", "3mm上げたいかな", "3mm上げたいですか", "Zプラス3ミリ動かしたい？",
    "Zプラス3ミリ動かしたいけどやめて", "Zプラス3ミリ動かしたいけど", "Zプラス3ミリくらい動かしたい", "たい", "したい",
    "Zプラス3ミリ動かしたいし、Yマイナス3ミリ動かしたい", "Zプラス3ミリたい", "Zプラス3ミリ動かしたいな",
    "Zプラス3ミリ動かしたいと思う", "Zプラス3ミリ動かしたかった", "Zプラス3ミリ動かしたいので承認して",
])
def test_wish_ending_never_makes_an_unsafe_candidate(tmp_path, text):
    w, fake, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text, "voice")
    assert not cand(r), (text, hear(r).get("message"))
    assert not r["state"]["approval"]


def test_wish_ending_reads_the_same_facts():
    a, b = ut.read_utterance("この位置を上に3mm上げたい"), ut.read_utterance("この位置を上に3mm上げて")
    assert (a.kinds, a.amounts, a.vertical, a.unknown_words, a.stop_reasons) == \
           (b.kinds, b.amounts, b.vertical, b.unknown_words, b.stop_reasons)
    assert a.raw == "この位置を上に3mm上げたい"  # 聞こえた文は変えずに残す（作業ログ・画面の表示）


# ---- 4. 「ミリメートル」は「ミリ」と同じ量 ----
@pytest.mark.parametrize("long,short", [
    ("Zプラス3ミリメートル", "Zプラス3ミリ"),
    ("Zプラス方向に5ミリメートル", "Zプラス方向に5ミリ"),
    ("Zプラス30ミリメートル", "Zプラス30ミリ"),
    ("Yマイナス1.5ミリメートル", "Yマイナス1.5ミリ"),
    ("この位置を上に3ミリメートル上げたい", "この位置を上に3ミリ上げて"),
])
def test_millimetre_word_is_read_like_miri(tmp_path, long, short):
    assert outcome(tmp_path / "a", long) == outcome(tmp_path / "b", short)


def test_millimetre_word_amount():
    f = ut.read_utterance("Zプラス3ミリメートル")
    assert [(x.value, x.unit) for x in f.amounts] == [(3.0, "mm")] and not f.unknown_words and not f.unclear_amounts


@pytest.mark.parametrize("text", ["Zプラス3メートル", "Zプラス3センチメートル", "Zプラス3ミリメートルくらい",
                                  "Zプラス3ミリメートルメートル", "Zプラス三ミリメートル", "Zプラス3"])
def test_other_units_still_stop(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text, "voice")
    assert not cand(r), (text, hear(r).get("message"))


# ---- 5. 撮影の台本は今どおり ----
def test_filming_script_is_unchanged(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(A.DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r30 = w.talk(sid, "Zプラス方向に30ミリ", "voice")
    assert not cand(r30) and "上限" in hear(r30).get("message", "")
    r5 = w.talk(sid, "Zプラス方向に5ミリ", "voice")
    c = cand(r5)
    assert c["target"] == "C00005 Z" and c["delta"] == "+5.000" and "205.000" in c["after_line"], c
    assert not r5["state"]["approval"]


# ---- 6. Claude の答えの口調（お礼・気持ちの言葉を書かない） ----
def test_prompt_asks_for_short_plain_replies_without_courtesy():
    assert "お礼" in cx.SYSTEM and "お気持ち" in cx.SYSTEM


# ---- 7. 向きの確かめ（［合っている］）のあとの書き換え（9dab764 では「3mm上げて」→［合っている］→「「Z」「方向」が分かりませんでした」） ----
@pytest.mark.parametrize("text,target,delta", [
    ("この位置を上に3mm上げたい", "C00003 Z", "+3.000"),
    ("この位置を上に3mm上げて", "C00003 Z", "+3.000"),
    ("3mm上げて", "C00003 Z", "+3.000"),
    ("3mm上げたい", "C00003 Z", "+3.000"),
    ("上に3mm", "C00003 Z", "+3.000"),
    ("下に3mm下げて", "C00003 Z", "-3.000"),
    ("3ミリ下げてほしい", "C00003 Z", "-3.000"),
    ("上へ3mm上げて", "C00003 Z", "+3.000"),
    ("3mm右に動かして", "C00003 Y", "+3.000"),
    ("右に10mm動かしたい", "C00003 Y", "+10.000"),
    ("この位置を上に3ミリメートル上げたい", "C00003 Z", "+3.000"),
])
def test_after_the_facing_button_the_candidate_follows_what_was_said(tmp_path, text, target, delta):
    w, fake, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text, "voice")
    assert not cand(r)
    yes = next(c["text"] for c in hear(r)["choices"] if c["label"] == "合っている")
    r2 = w.hear(sid, yes, "text")
    c = r2["state"]["candidate"]
    assert c and (c["target"], c["delta"]) == (target, delta), (text, r2.get("hear"), c)
    assert not r2["state"]["approval"] and len(fake.requests) == 0


@pytest.mark.parametrize("text", ["上に3mm下げて", "下に3mm上げて", "上に3mm下げたい", "この位置を下に3mm上げたい"])
def test_conflicting_up_and_down_words_still_stop(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text, "voice")
    assert not cand(r) and "合っている" not in labels(r), (text, hear(r))


def test_facing_rewrite_keeps_axis_words_readable():
    for said, include in (("3mm上げて", True), ("この位置を上に3mm上げて", True), ("3mm右に動かして", False)):
        f = ut.read_utterance(ut.facing_rewrite(said, include))
        assert not f.unknown_words and not f.stop_reasons and len(f.axes) == 1, (said, f)


# ---- 8. 2回目（実 Claude のケース集 197件で見つかった言い方。唐川さん「AI が人の話し方に合わせる」） ----
PAIRS2 = [
    # 博多弁・北九州・話し言葉の頼み方（＝〜しておいて・〜してしまって）は「〜て」と同じ
    ("Zプラス3ミリ動かしとって", "Zプラス3ミリ動かして"),
    ("3ミリ上げとって", "3ミリ上げて"),
    ("Yマイナス10ミリずらしといて", "Yマイナス10ミリずらして"),
    ("Zプラスに3ミリ動かしちゃって", "Zプラスに3ミリ動かして"),
    ("Zプラス3ミリ動かしてやって", "Zプラス3ミリ動かして"),
    ("Zプラスに3ミリ動かしてみて", "Zプラスに3ミリ動かして"),
    ("Zプラス3ミリ動かしときー", "Zプラス3ミリ動かして"),
    ("Zプラス3ミリ動かしてほしいっちゃけど", "Zプラス3ミリ動かして"),
    ("Zプラス3ミリ動かしたいっちゃん", "Zプラス3ミリ動かして"),
    ("3ミリ下げてほしいんです", "3ミリ下げて"),
    ("この位置ば上に3ミリ上げたいっちゃけど", "この位置ば上に3ミリ上げて"),  # 「ば」は今どおり分からない言葉
    # 「〜てくれん」（＝〜してくれない？）は「〜てくれる」と同じ2択
    ("Zプラスに3ミリ動かしてくれんね", "Zプラスに3ミリ動かしてくれる"),
    ("この位置、上に3ミリ上げてくれん？", "この位置、上に3ミリ上げてくれる？"),
    ("Yマイナスに10ミリずらしてくれんやろか", "Yマイナスに10ミリずらしてくれる？"),  # 文の終わりの「か」で疑問の形
    ("Zプラス3ミリ動かしてくれんかいな", "Zプラス3ミリ動かしてくれる"),
    ("Yマイナスに10ミリ動かしてもらえん？", "Yマイナスに10ミリ動かしてもらえる？"),
    # 言いよどみ
    ("えーっと、Zプラス3ミリ", "えーと、Zプラス3ミリ"),
    ("あのう、Yマイナス10ミリ", "あの、Yマイナス10ミリ"),
    ("えー、Zプラス3ミリ", "えーと、Zプラス3ミリ"),
    ("えっとー、Xプラス2ミリ", "えっと、Xプラス2ミリ"),
    ("えー、この位置を、Zプラスに、3ミリ", "えーと、この位置を、Zプラスに、3ミリ"),
    # ひらがなの「みり」（数のすぐ後）
    ("Zプラス3みり", "Zプラス3ミリ"),
    ("Yマイナス10 みり", "Yマイナス10 ミリ"),
    ("Zプラス3みりメートル", "Zプラス3ミリ"),
    ("えーっと、えーっと、Zプラス3ミリ", "えーと、えーと、Zプラス3ミリ"),  # 読点で区切った言いよどみの繰り返しは今も通る
    # 選んでいる位置を指す言葉
    ("今の位置をZプラス3ミリ", "この位置をZプラス3ミリ"),
    ("選んでいる位置をZプラス3ミリ", "この位置をZプラス3ミリ"),
    ("この場所を上に3ミリ上げたい", "この位置を上に3ミリ上げて"),
]


@pytest.mark.parametrize("new,old", PAIRS2)
def test_round2_new_wording_is_read_like_the_known_wording(tmp_path, new, old):
    assert outcome(tmp_path / "a", new) == outcome(tmp_path / "b", old), (new, old)


@pytest.mark.parametrize("new,old", [("今の位置をZプラス3ミリ", "この位置をZプラス3ミリ"),
                                     ("Zプラス3ミリ動かしとって", "Zプラス3ミリ動かして")])
def test_round2_without_selected_position(tmp_path, new, old):
    a, b = outcome(tmp_path / "a", new, select=None), outcome(tmp_path / "b", old, select=None)
    assert a == b and a["candidate"] is None


@pytest.mark.parametrize("text", [
    "Zプラス3ミリ動かしよって", "Zプラス3ミリ動かしとらん", "Zプラス3ミリ動かさんといて", "Zプラス3ミリ動かさんで",
    "Zプラスに3ミリ動かしてくれんね", "この位置、上に3ミリ上げてくれん？", "Zプラスみり", "みり3",
    "えーっと、たぶんZプラス3ミリ", "あのう、Zプラス3ミリくらい", "Zプラス30ミリ動かしとって", "Zプラス3動かしとって",
    "今の位置をZプラス30ミリ",
])
def test_round2_still_no_candidate(tmp_path, text):
    w, _, sid = A.place_flow(tmp_path)
    r = w.talk(sid, text, "voice")
    assert not cand(r), (text, hear(r).get("message"))


def test_filler_words_stay_within_the_cap():
    assert len(ut.FILLER_WORDS) <= 30  # tests/test_unknown_words.py の上限（変えない）
