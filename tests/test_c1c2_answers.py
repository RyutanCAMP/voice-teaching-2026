"""聞き返しへの答えを受け取る（C1・C2）と「あと N 回」の確認（クロクラウド 11/5・唐川さんの指示 11/5）。

- C1：アプリが「プラスですか、マイナスですか？」と聞いたあとの「マイナス」を、会話の道（Claude の説明）へ回さず、
  指示の道で前の発話に足す
- C2：前に言った量（10mm）を覚えたまま、軸と符号だけの答え（「Yマイナス」）を足す。足すのは、
  位置が決まっている（口で言った・画面で選んでいる）とき、または前の発話の軸と同じ軸のときだけ
- 足したあとも、今までの検査（pre_check → 提案 → check_proposal → plan_shift → 確認表示 → 承認ボタン）をすべて通る
- 「あと1回」は合計1回として扱わない。今の回数をプログラムが数えて、合計を確かめる（候補は作らない）
- API は呼ばない（偽の Claude・合成ジョブだけ）
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import claude_talk as talk
from voice_teaching import jbi, updown

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def place(tmp_path, confirmed=False, select="C00003"):
    cfg = None
    if confirmed:
        obj = updown.build_config(jbi.parse_bytes(DEMO_C), updown.DEFAULT_ROBOT, "QA", "test evidence",
                                  confirmed_at="2026-11-05T00:00:00+09:00")
        cfg = updown.write_config(obj, tmp_path / "updown.json", overwrite=True)
    w, fake = T.flow(tmp_path, updown_config=cfg)
    sid = w.load(DEMO_C)["session_id"]
    if select:
        w.select(sid, select)
    return w, fake, sid


def msg(r):
    return (r.get("hear") or {}).get("message", "") + (r.get("ask") or {}).get("reply", "")


def shifted(r, target, before, after):
    """候補が target で、確認の文に変更前 → 変更後の値（プログラムの計算）がある。承認はされていない。"""
    c = r["state"]["candidate"]
    assert c and c["target"] == target, msg(r)
    assert f"{before} → {after}" in msg(r)
    assert r["state"]["approval"] is None and not r["state"]["can_export"]


# ---- C1：符号だけの答え ----
@pytest.mark.parametrize("answer, after", [("マイナス", "-160.000"), ("マイナスで", "-160.000"), ("プラス", "-140.000"),
                                           ("-", "-160.000")])
def test_c1_sign_answer_completes_the_move(tmp_path, answer, after):
    w, fake, sid = place(tmp_path)
    q = w.talk(sid, "Yに10mm")
    assert "プラスですか、マイナスですか" in msg(q) and not q["state"]["candidate"]
    r = w.talk(sid, answer)
    assert r["via"] == ["hear"]
    shifted(r, "C00003 Y", "-150.000", after)
    assert not fake.requests  # Claude の説明（会話の道）を呼ばない


def test_c1_sign_answer_with_explicit_position(tmp_path):
    w, fake, sid = place(tmp_path, select=None)
    w.talk(sid, "C00002をYに10mm")
    r = w.talk(sid, "マイナス")
    assert r["state"]["candidate"] and r["state"]["candidate"]["target"] == "C00002 Y"


@pytest.mark.parametrize("answer", ["マイナス？", "たぶんマイナス", "プラスかな", "マイナスじゃない", "マイナスです"])
def test_c1_unsure_or_question_answers_do_not_fill_the_sign(tmp_path, answer):
    w, _, sid = place(tmp_path)
    w.talk(sid, "Yに10mm")
    r = w.talk(sid, answer)
    assert not r["state"]["candidate"]


def test_c1_sign_alone_without_a_question_makes_nothing(tmp_path):
    w, _, sid = place(tmp_path)
    r = w.talk(sid, "マイナス")
    assert not r["state"]["candidate"]


def test_c1_answer_after_reselecting_is_not_applied(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "Yに10mm")
    w.select(sid, "C00002")
    r = w.talk(sid, "マイナス")
    assert not r["state"]["candidate"]


def test_c1_merged_answer_still_hits_the_limit(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "Yに30mm")
    r = w.talk(sid, "マイナス")
    assert not r["state"]["candidate"]
    assert "20mm" in msg(r)  # 上限の文（プログラム）。量を変えた候補を作らない


def test_c1_unconfirmed_updown_question_is_not_answered_by_a_sign(tmp_path):
    """上下の向きが未確認の「上に10mm」のあとの「プラス」を Z+ にしない（CHECKER_026 再修正 P0-1 と同じ考え方）。"""
    w, _, sid = place(tmp_path)
    w.talk(sid, "上に10mm")
    r = w.talk(sid, "プラス")
    assert not r["state"]["candidate"]


def test_c1_sign_answer_while_candidate_pending_changes_nothing(tmp_path):
    w, _, sid = place(tmp_path)
    first = w.talk(sid, "Zマイナス3ミリ")["state"]["candidate"]
    r = w.talk(sid, "マイナス")
    assert r["state"]["candidate"] == first


# ---- C2：前に言った量を覚えたまま、軸と符号の答え ----
@pytest.mark.parametrize("first", ["Yの方向に10mm", "Yに10mm"])
def test_c2_same_axis_answer_keeps_amount(tmp_path, first):
    w, fake, sid = place(tmp_path)
    w.talk(sid, first)
    r = w.talk(sid, "Yマイナス")
    shifted(r, "C00003 Y", "-150.000", "-160.000")
    assert "何mm" not in msg(r)


@pytest.mark.parametrize("answer, target, before, after", [
    ("Yマイナス", "C00003 Y", "-150.000", "-160.000"),
    ("Yのマイナス", "C00003 Y", "-150.000", "-160.000"),
    ("Zプラス", "C00003 Z", "300.000", "310.000"),
    ("Xプラス方向", "C00003 X", "400.000", "410.000"),
])
def test_c2_direction_answer_for_selected_position_keeps_amount(tmp_path, answer, target, before, after):
    w, fake, sid = place(tmp_path)
    q = w.talk(sid, "10mm動かして")
    assert "どの方向" in msg(q)
    r = w.talk(sid, answer)
    shifted(r, target, before, after)
    assert not fake.requests


def test_c2_spoken_position_still_works(tmp_path):
    """回帰：コックさんの修正（位置番号を口で言った指示 → 「Zプラス」）。"""
    w, _, sid = place(tmp_path, select=None)
    w.talk(sid, "C00003を10mm動かして")
    shifted(w.talk(sid, "Zプラス"), "C00003 Z", "300.000", "310.000")


def test_c2_different_axis_answer_does_not_borrow_amount(tmp_path):
    """「Yの方向に10mm」と言ったのに「Zマイナス」：10mm を Z に当てない。"""
    w, _, sid = place(tmp_path)
    w.talk(sid, "Yの方向に10mm")
    r = w.talk(sid, "Zマイナス")
    assert not r["state"]["candidate"]


def test_c2_answer_with_its_own_amount_is_a_new_instruction(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "10mm動かして")
    shifted(w.talk(sid, "Yマイナス5mm"), "C00003 Y", "-150.000", "-155.000")


def test_c2_answer_after_reselecting_does_not_borrow_amount(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "10mm動かして")
    w.select(sid, "C00002")
    r = w.talk(sid, "Yマイナス")
    assert not r["state"]["candidate"]


def test_c2_signed_amount_is_not_given_a_second_sign(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "-10mm動かして")
    r = w.talk(sid, "Yプラス")
    c = r["state"]["candidate"]
    assert not c or "-140.000" not in msg(r) and "-160.000" not in msg(r)


@pytest.mark.parametrize("answer", ["Yマイナスかな", "たぶんYマイナス", "Yマイナスじゃない", "Yマイナス？", "Yマイナスにします"])
def test_c2_unsure_answers_do_not_borrow_amount(tmp_path, answer):
    w, _, sid = place(tmp_path)
    w.talk(sid, "10mm動かして")
    r = w.talk(sid, answer)
    assert not r["state"]["candidate"]


def test_c2_merged_answer_still_hits_the_limit(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "Yの方向に25mm")
    r = w.talk(sid, "Yマイナス")
    assert not r["state"]["candidate"] and "20mm" in msg(r)


def test_c2_vertical_answer_needs_confirmed_updown(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "10mm動かして")
    r = w.talk(sid, "上")
    assert not r["state"]["candidate"]


def test_c2_never_approves(tmp_path):
    w, _, sid = place(tmp_path)
    w.talk(sid, "10mm動かして")
    w.talk(sid, "Yマイナス")
    assert not w.export(sid)["ok"]  # 承認はボタンだけ。承認していなければ出力しない


# ---- 6.「あと N 回」：合計 N 回として扱わず、今の回数を踏まえて確かめる ----
def blocks():
    return talk.repeat_blocks(jbi.parse_bytes(T.demo_bytes()))


@pytest.mark.parametrize("text, total", [("あと1回押して", 3), ("あと一回押して", 3), ("あと2回やって", 4), ("あと1回", 3)])
def test_ato_n_kai_asks_with_the_program_total(text, total):
    r = talk.read_count(text, blocks())
    assert r is not None and r.total is None and r.stop
    assert "今2回" in r.stop and f"合計{total}回" in r.stop


def test_ato_n_kai_makes_no_range_and_calls_no_api(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, "あと1回押して")
    assert not r["state"]["repeat_range"] and not r["state"]["candidate"]
    assert "合計3回" in msg(r) and not fake.requests


def test_add_verb_still_reads_relative_total():
    """回帰：「あと1回増やして」「もう1回足して」は今までどおり合計3回（今2回＋1）。"""
    assert talk.read_count("あと1回増やして", blocks()).total == 3
    assert talk.read_count("もう1回足して", blocks()).total == 3
