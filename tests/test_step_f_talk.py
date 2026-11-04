"""Step F：発話の入口を1つにする（/api/talk・WebFlow.talk）の受入テスト（唐川さんの指示 11/3 夜、CHECKER_031 3章、
docs/STEP_F_UNIFIED_INPUT.md）。

- 「このロボット、何をしている？」「ボタンを押してるんだよ」「じゃあ、3回にして」「Zマイナス3ミリ」を、同じ入口で受ける
- 振り分けはまずプログラムが決める（決定論）：承認・起動などの言葉、会話の途中、範囲の確認の途中、はっきりした位置・速度・選択・
  元に戻す の指示 → 指示の道（hear。Claude を呼ばない）。それ以外（説明・意味を教える・回数を変えたい）→ 会話の道（ask）
- 回数の変更の希望：会話の道で Claude が答え（読み取りだけ・回数を照らし合わせる）、変更の道へは
  **プログラムが作った決まった文**（rc.canonical）だけを渡す → 範囲の確認のボタン → 候補 → 確認表示 → 承認ボタン
- Claude の答えの数・座標は使わない。候補を作るのは今までの道（_accept・confirm_repeat）だけ。声だけで承認しない
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import claude_talk as talk
from voice_teaching import repeat_count as rc

BLOCK = (21, 22, 23, 24, 25, 26)
DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def start(tmp_path, *answers, data=None, updown_config=None):
    w, f = T.flow(tmp_path, *answers, updown_config=updown_config)
    sid = w.load(T.demo_bytes() if data is None else data)["session_id"]
    return w, f, sid


def calls(f):
    return len(f.requests)


# ---- Scene 3：同じ入口で 説明 → 教える → ボタンで確かめる → 3回にしたい → 範囲のボタン → 候補 ----
def test_scene_3_through_one_entry(tmp_path):
    w, f, sid = start(tmp_path, T.answer("同じ動きをくり返しています。" + T.ASKED, BLOCK))
    r = w.talk(sid, "このロボット、何をしている？", "voice")
    assert r["ok"] and r["via"] == ["ask"] and r["ask"]["reply_by"] == "claude" and r["hear"] is None
    assert w._entries[sid].session.candidate is None

    r = w.talk(sid, "ボタンを押してるんだよ", "voice")
    assert r["via"] == ["ask"] and r["ask"]["status"] == "ask_which"  # どの動きか、ボタンで確かめる（Claude を呼ばない）
    r = T.press(w, sid)
    assert r["status"] == "taught"

    f.add(T.answer("ボタンを押す動作を2回から3回に変更しますか？", BLOCK, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.talk(sid, "じゃあ、3回にして", "voice")
    assert r["via"] == ["ask", "hear"]
    assert r["ask"]["reply_by"] == "app"  # CHECKER_033 P1-A：変更の会話はプログラムの定型文（Claude は意図を読むだけ）
    rr = r["state"]["repeat_range"]
    assert rr and rr["total"] == 3 and rr["count"] == 2 and rr["positions"] == ["C00001", "C00002", "C00003"]
    assert r["hear"]["heard"] == rc.canonical("C00001", "C00003", 2, 3)  # 変更の道へはプログラムの決まった文だけ
    assert r["state"]["candidate"] is None and r["state"]["approval"] is None  # まだ候補も承認も無い
    from voice_teaching.web import flow as flow_mod
    assert r["ask"]["notes"] == [flow_mod.STEP_F_RANGE_NOTE] and r["ask"]["change_record"] is None  # 状態と合う文

    c = w.repeat_choose(sid, "yes", rr["turn"])  # 範囲は人がボタンで決める
    assert c["candidate_made"] and c["state"]["candidate"]["kind"] == "repeat"
    assert c["state"]["approval"] is None and not c["state"]["can_export"]  # 承認は確認表示のボタンだけ


def test_claude_count_mismatch_stops_before_range_confirmation(tmp_path):
    w, f, sid = start(tmp_path, T.answer("同じ動きをくり返しています。" + T.ASKED, BLOCK))
    w.talk(sid, "このロボット、何をしている？")
    f.add(T.answer("ボタンを押す動作を2回から5回に変更しますか？", BLOCK, "change_request",
                   {"kind": "repeat", "from_count": 2, "to_count": 5}))
    r = w.talk(sid, "じゃあ、3回にして")
    assert r["ask"]["reply_by"] == "app" and r["ask"]["reply"] == talk.COUNT_MISMATCH  # 検証エラー（書き換えない）
    # 11/5 P0: an interpretation mismatch must not advance past a clarification.
    assert r["state"]["repeat_range"] is None and r["state"]["candidate"] is None


def test_negated_count_makes_no_range(tmp_path):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, "3回にしないで")
    assert not r["state"]["repeat_range"] and r["state"]["candidate"] is None and calls(f) == 0


def test_pending_candidate_blocks_new_change_requests(tmp_path):
    w, f, sid = start(tmp_path, T.answer("回数のご希望ですね。", BLOCK, "change_request",
                                      {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.talk(sid, "じゃあ、3回にして")
    w.repeat_choose(sid, "yes", r["state"]["repeat_range"]["turn"])
    n = calls(f)
    r = w.talk(sid, "4回にして")
    assert r["state"]["repeat_range"] is None and r["state"]["candidate"]["kind"] == "repeat"
    assert calls(f) == n  # 承認待ちの候補があるときは、変更の希望で Claude を呼ばない


# ---- はっきりした指示：プログラムだけ（Claude を呼ばない） ----
def test_clear_position_instruction_goes_to_the_program(tmp_path):
    w, f, sid = start(tmp_path, data=DEMO_C)
    w.select(sid, "C00005")
    r = w.talk(sid, "Zマイナス3ミリ", "voice")
    assert r["via"] == ["hear"] and calls(f) == 0
    c = r["state"]["candidate"]
    assert c["target"] == "C00005 Z" and c["delta"] == "-3.000" and r["state"]["approval"] is None


@pytest.mark.parametrize("said", ["承認して", "承認します", "出力して", "ロボットを動かして", "転送して"])
def test_approve_and_start_words_never_approve(tmp_path, said):
    w, f, sid = start(tmp_path, data=DEMO_C)
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    r = w.talk(sid, said, "voice")
    assert r["via"] in (["hear"], ["button"]) and calls(f) == 0
    assert r["hear"]["status"] in ("stop", "ask")  # 止める・聞き返す（実行しない）
    assert r["state"]["approval"] is None and not r["state"]["can_export"]


def test_reference_only_position_is_said_clearly(tmp_path):
    # DEMO_BUTTON_REPEAT の C00001 は2つの命令が共用：「分かりません」ではなく、変更できない理由（参考の表示だけ）
    w, f, sid = start(tmp_path)
    w.select(sid, "C00001")
    r = w.talk(sid, "Zマイナス3ミリ", "voice")
    assert r["via"] == ["hear"]
    assert r["hear"]["message"].startswith("【参考の表示だけ・変更しません】") and "共用" in r["hear"]["message"]
    assert not r["state"]["can_approve"]


def test_explanation_never_creates_candidates(tmp_path):
    w, f, sid = start(tmp_path, T.answer("同じ動きをくり返しています。" + T.ASKED, BLOCK))
    r = w.talk(sid, "このロボット、何をしている？")
    assert r["via"] == ["ask"] and r["state"]["candidate"] is None and r["state"]["repeat_range"] is None


def test_without_key_count_change_stops_before_the_range_button(tmp_path):
    from voice_teaching import claude_explain as cx
    from voice_teaching.web.flow import WebFlow
    from voice_teaching.claude_interpreter import MockInterpreter
    w = WebFlow(interpreter=MockInterpreter(), explainer=cx.Explainer(None, "claude-sonnet-5-5"))
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, "じゃあ、3回にして")
    assert r["ask"]["status"] == "no_key"
    assert r["state"]["repeat_range"] is None and r["state"]["candidate"] is None


@pytest.mark.parametrize("bad", [None, "", "   ", 3])
def test_bad_text_is_refused(tmp_path, bad):
    w, f, sid = start(tmp_path)
    assert w.talk(sid, bad)["ok"] is False


def test_unknown_session(tmp_path):
    w, f, sid = start(tmp_path)
    assert w.talk("nope", "このロボット、何をしている？")["ok"] is False


def test_output_words_after_approval_do_not_cancel_or_export(tmp_path):
    w, f, sid = start(tmp_path, data=DEMO_C)
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト")
    r = w.talk(sid, "出力して", "voice")
    assert r["via"] == ["button"] and r["state"]["approval"] is not None  # 承認はそのまま（取り消さない）
    assert not r["state"]["exported"]  # 声では出力しない（出力ボタンだけ）


# ---- 内部 Safety 再確認（7af2288）P2-3・P2-4：ボタンだけの操作の言葉・承認のあとの相づちで、承認を取り消さない ----
@pytest.mark.parametrize("said", ["保存して", "転送して", "書き出して", "承認して", "起動して", "はい", "OK", "確定して",
                                  "エクスポートして", "ダウンロードして", "登録して", "上書きして", "出力して"])
def test_words_after_approval_do_not_cancel_or_export(tmp_path, said):
    w, f, sid = start(tmp_path, data=DEMO_C)
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト")
    n = calls(f)
    r = w.talk(sid, said, "voice")
    assert r["state"]["approval"] is not None, said  # 承認は取り消さない
    assert not r["state"]["exported"] and calls(f) == n, said  # 出力しない・Claude を呼ばない


def test_a_real_new_instruction_after_approval_still_cancels_it(tmp_path):
    # 新しい変更の指示は、今まで通り承認を取り消す（承認したものと違う候補にしないため）
    w, f, sid = start(tmp_path, data=DEMO_C)
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト")
    r = w.talk(sid, "Zマイナス2ミリ")
    assert r["state"]["approval"] is None


def test_approve_word_during_range_confirmation_explains_the_two_steps(tmp_path):
    w, f, sid = start(tmp_path, T.answer("回数のご希望ですね。", BLOCK, "change_request",
                                      {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.talk(sid, "じゃあ、3回にして")
    turn = r["state"]["repeat_range"]["turn"]
    r = w.talk(sid, "承認して", "voice")
    assert r["via"] == ["hear"] and r["hear"]["message"] == rc.RANGE_APPROVE_GUIDE
    assert r["state"]["repeat_range"]["turn"] == turn and r["state"]["candidate"] is None


def test_question_after_approval_is_answered_without_cancelling(tmp_path):
    w, f, sid = start(tmp_path, T.answer("同じ動きをくり返しています。" + T.ASKED, BLOCK), data=DEMO_C)
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト")
    r = w.talk(sid, "このロボット、何をしている？")
    assert r["via"] == ["ask"] and r["state"]["approval"] is not None and not r["state"]["exported"]
