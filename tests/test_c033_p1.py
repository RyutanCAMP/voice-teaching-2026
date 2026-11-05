"""CHECKER_033 の P1（唐川さんの要約 11/4）と、唐川さんの方針①〜⑥（11/4）の受入テスト。

※ CHECKER_033 の原本・クロちゃんのテストのスクリプトは、まだ受け取っていない。ここのケースはシクロが要約から作った再現ケース。
  原本のケースで確かめたことにはしない（原本を受け取ったら、そのまま流す）。

- P1-A：変更・承認・出力の状態を、Claude の自由文で言わせない（状態はプログラムの確定情報から status_line で出す）
  ① 状態はプログラム ② 変更の会話はプログラムの定型文 ③ MVP では Claude の自由文に数を含めない
  ④ 目的は推定として示す ⑤ 禁止表現の検査だけに頼らない ⑥ 人が教えた目的は「ユーザー登録情報」として分ける
- P1-B：1つの文に2つ以上の変更（「3回にして、あと3ミリ下げて」）は、どれも受け付けない（候補・範囲の確認・承認を変えない）
- 「登録」「保存」：作業目的の意味付けとジョブの変更・出力を分ける（あいまいなら確かめる。状態を変えない）
"""
import pytest

import test_claude_talk as T
import test_koko_position as K
from voice_teaching import claude_talk as talk
from voice_teaching.web import flow as flow_mod

BLOCK = (21, 22, 23, 24, 25, 26)
CHANGE = {"kind": "repeat", "from_count": 2, "to_count": 3}

A_CHANGED = ["C00002を3ミリ下げておきました。", "3回に変えておきました。", "3回にしました。", "変更済みです。", "変更が完了しました。",
             "ジョブを書き換えています。", "修正しました。", "反映しました。", "変更を適用しました。", "更新しました。",
             "回数が3回になりました。", "位置が下がりました。", "C00002を移動しました。", "変わりました。", "もう直っています。"]
B_DONE = ["承認されました。", "承認済みです。", "出力が完了しました。", "出力済みです。", "書き出しました。", "ロボットに送りました。",
          "転送完了です。", "これで実行できます。", "作業は完了です。", "保存されました。", "あとはロボットで動かすだけです。"]
C_NUMBERS = ["3センチ下げる候補です。", "0.3cm 下げます。", "3ミリ下げる候補です。", "三ミリ下げます。", "4回くり返しています。",
             "2回から4回にしますか？", "速度は50%です。", "10秒かかります。", "C00099を通ります。", "3回目の動きです。",
             "30センチ動きます。", "同じ動きを2回くり返しています。", "3か所を通ります。", "同じ動きを二回くり返しています。"]
D_SAFETY = ["安全です。", "問題ありません。", "大丈夫です。", "ぶつかりません。", "余裕があります。", "十分な距離があります。",
            "クリアランスは確保されています。", "安心して動かせます。", "危なくありません。", "リスクはありません。", "心配ありません。",
            "問題なく動きます。", "周りに何もないので動かせます。", "当たりません。", "壁から離れています。"]
ALL_BAD = A_CHANGED + B_DONE + C_NUMBERS + D_SAFETY


def explain(tmp_path, reply):
    w, f = T.flow(tmp_path, T.answer(reply, BLOCK))
    sid = w.load(T.demo_bytes())["session_id"]
    return w, sid, w.talk(sid, "このロボット、何をしている？")


@pytest.mark.parametrize("reply", ALL_BAD)
def test_p1a_bad_reply_is_not_shown_in_an_explanation(tmp_path, reply):
    w, sid, r = explain(tmp_path, reply)
    a = r["ask"]
    assert a["reply_by"] == "app" and reply not in a["reply"]
    st = r["state"]
    assert st["candidate"] is None and st["approval"] is None and not st["exported"]
    assert st["status_line"] == "アプリの記録：変更の候補 なし／承認 まだ／出力 まだ"  # 状態はプログラムの確定情報


@pytest.mark.parametrize("reply", ALL_BAD + ["ボタンを押す動作を2回から3回に変更しますか？"])
def test_p1a_claude_text_is_never_shown_in_a_change_turn(tmp_path, reply):
    w, f = T.flow(tmp_path, T.answer(reply, BLOCK, "change_request", CHANGE))
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, "じゃあ、3回にして")
    a = r["ask"]
    assert a["reply_by"] == "app" and reply not in a["reply"]  # プログラムの定型文（数はプログラムが数えたもの）
    assert r["state"]["candidate"] is None and r["state"]["approval"] is None  # 範囲はボタンで決める
    # 11/5 P0: a detected interpretation mismatch stops before the range.
    if (a.get("change_record") or {}).get("mismatch"):
        assert r["state"]["repeat_range"] is None and not r["state"]["can_approve"]
        assert "候補 なし" in r["state"]["status_line"]
    else:
        assert "範囲の確認中" in r["state"]["status_line"]


@pytest.mark.parametrize("reply", [
    "同じ動きをくり返しています。何をする作業ですか？", "待機の位置から動いて、また戻ります。",
    "ボタンを押す動きのようです。合っていますか？", "部品をつかんで置く作業かもしれません。どんな作業ですか？",
])
def test_natural_explanations_are_still_shown(tmp_path, reply):
    w, sid, r = explain(tmp_path, reply)
    assert r["ask"]["reply_by"] == "claude" and r["ask"]["reply"] == reply  # 書き換えない


@pytest.mark.parametrize("reply", ["溶接する動きのようです。", "ねじ締めの作業ですか？", "スイッチを押す動きですね。",
                                   "検査をしている動きかもしれません。"])
def test_purpose_guesses_are_marked_as_ai_guess(tmp_path, reply):
    w, sid, r = explain(tmp_path, reply)
    assert r["ask"]["reply_by"] == "claude" and r["ask"]["guesses"] == [reply]  # AI の推定（未確定）。登録しない
    assert T.meanings(w, sid) == []


def test_taught_purpose_is_user_registered_information_not_a_guess():
    assert talk.HUMAN_MARK.startswith("ユーザー登録情報") and talk.AI_MARK == "AI の推定（未確定）"
    assert talk.ai_guesses("ボタンを押す動作ですね。", ["ボタンを押している"]) == []  # 人が教えた言葉は推定にしない


# ---- 状態の1行（方針①）：プログラムの確定情報だけ ----
def test_status_line_follows_the_program_state(tmp_path):
    w, f = T.flow(tmp_path, updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    assert w.state(sid)["state"]["status_line"].endswith("変更の候補 なし／承認 まだ／出力 まだ")
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    assert "変更の候補 あり（まだ動作データは変えていません）／承認 まだ" in w.state(sid)["state"]["status_line"]
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    assert "承認 済み（ボタンで承認）／出力 まだ" in w.state(sid)["state"]["status_line"]


def test_status_line_for_a_reference_only_candidate(tmp_path):
    w, f = T.flow(tmp_path, updown_config=T.confirmed_config(tmp_path))
    sid = w.load(T.demo_bytes())["session_id"]
    w.select(sid, "C00001")
    w.talk(sid, "ここの位置を3ミリ下げて")
    assert "参考の表示だけ（変更しません・承認できません）" in w.state(sid)["state"]["status_line"]


# ---- P1-B：複数の変更 ----
SEVERAL = ["3回にして、あと3ミリ下げて", "3回にして3ミリ下げて", "じゃあ3回にして、それとZマイナス3ミリ", "Zマイナス3ミリと、3回にして",
           "3回にして、速度を10%下げて", "Zマイナス3ミリにして、あと3回くり返して", "3回にして、それから元に戻して"]


@pytest.mark.parametrize("text", SEVERAL)
def test_p1b_several_changes_are_all_refused(tmp_path, text):
    w, f = T.flow(tmp_path, updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.talk(sid, text, "voice")
    assert r["via"] == ["several"] and r["hear"]["message"] == flow_mod.STEP_F_ONE_AT_A_TIME
    st = r["state"]
    assert st["candidate"] is None and st["repeat_range"] is None and st["approval"] is None
    assert len(f.requests) == 0  # Claude を呼ばない


@pytest.mark.parametrize("text", ["Zマイナス3ミリ、速度を10%上げて", "C00005をZマイナス3ミリ、C00006をZプラス2ミリ",
                                  "Zマイナス3ミリとXプラス2ミリ"])
def test_p1b_several_moves_are_refused_by_the_instruction_path(tmp_path, text):
    w, f = T.flow(tmp_path, updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.talk(sid, text)
    assert r["state"]["candidate"] is None and "1つ" in str(r["hear"])


def test_p1b_several_changes_keep_a_pending_candidate_and_the_approval(tmp_path):
    w, f = T.flow(tmp_path, updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    before = w.state(sid)["state"]
    r = w.talk(sid, "3回にして、あと3ミリ下げて")
    assert r["via"] == ["several"]
    assert r["state"]["candidate"] == before["candidate"] and r["state"]["approval"] == before["approval"]


# ---- 「登録」「保存」：作業目的の意味付けと、ジョブの変更・出力を分ける（11/4 の決定4） ----
def test_purpose_registration_goes_to_the_conversation(tmp_path):
    w, f = T.flow(tmp_path, T.answer("ボタンを押す動作ですね。どの動きのことですか？", BLOCK))
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, "ボタンを押す作業として登録して")
    assert r["via"] == ["ask"] and r["state"]["candidate"] is None and r["state"]["approval"] is None


@pytest.mark.parametrize("text", ["3回にして、ボタンを押す作業として登録して", "Zマイナス3ミリにして、押す作業として保存して"])
def test_purpose_and_change_together_is_confirmed_without_changing_state(tmp_path, text):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, text)
    assert r["via"] == ["purpose_or_change"] and r["state"]["candidate"] is None
    assert r["state"]["repeat_range"] is None and len(f.requests) == 0


@pytest.mark.parametrize("text", ["保存して", "出力して", "承認して", "ロボットに転送して"])
def test_save_or_output_words_alone_stay_button_only(tmp_path, text):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, text)
    assert r["via"] == ["button"] and r["state"]["approval"] is None and not r["state"]["exported"]
