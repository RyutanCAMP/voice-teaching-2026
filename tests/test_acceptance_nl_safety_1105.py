"""クロクラウドの独立レビュー（2026-11-05）で書いた受入テスト。実装はしていない。

- 基点：fix/cock-p0-conversation 0f32435（本体の検証済み SHA ce7c981）。API は呼ばない（偽の Claude・合成ジョブだけ）
- xfail(strict=True) は「今のコードで失敗することを確かめた」項目。直したら xfail を外す（strict なので、直ったのに
  外し忘れると落ちる）。印の無いテストは、今も守れている安全の線の回帰テスト
- 期待値は「承認できないこと」だけを決め、候補を捨てるか・保留にするかは決めていない（設計は
  claude-hackathon-idea-lab の docs/review/NL_SAFETY_REVIEW_20261105.md）
- 同じ Claude が書いたテスト。外部の独立レビュー（チャッピー）の代わりにはならない
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import claude_talk as talk
from voice_teaching import jbi, sim, updown
from voice_teaching.web.flow import WebFlow
from voice_teaching import claude_interpreter as ci

DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
P0 = "今のコードで失敗（クロクラウド 11/5 に再現）"


def pending_three(tmp_path):
    """2回→3回の候補が承認待ち（範囲は人がボタンで決めた）。"""
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    out = T.repeat_to_three(w, sid)
    assert out["state"]["candidate"] and out["state"]["can_approve"]
    return w, fake, sid


def not_approvable(w, sid):
    st = w.state(sid)["state"]
    assert not st["can_approve"]
    assert not w.approve(sid, "QA")["ok"]
    assert not w.export(sid)["ok"]


def place_flow(tmp_path, confirmed=False):
    cfg = None
    if confirmed:
        obj = updown.build_config(jbi.parse_bytes(DEMO_C), updown.DEFAULT_ROBOT, "QA", "test evidence",
                                  confirmed_at="2026-11-05T00:00:00+09:00")
        cfg = updown.write_config(obj, tmp_path / "updown.json", overwrite=True)
    w, fake = T.flow(tmp_path, updown_config=cfg)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00003")
    return w, fake, sid


# ---- 1. 古い候補が承認できるまま残る（最優先） ----
@pytest.mark.xfail(strict=True, reason=P0 + " H02")
@pytest.mark.parametrize("text", ["さっきの3回じゃなくて2回", "3回じゃなくて4回", "やっぱり2回でいいや"])
def test_restated_count_leaves_no_approvable_old_candidate(tmp_path, text):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, text)
    not_approvable(w, sid)


@pytest.mark.xfail(strict=True, reason=P0 + " 別の回数の希望で古い候補が残る")
@pytest.mark.parametrize("text", ["やっぱり4回にして", "4回にして"])
def test_different_count_wish_freezes_old_candidate(tmp_path, text):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, text)
    not_approvable(w, sid)


@pytest.mark.xfail(strict=True, reason=P0 + " H16")
@pytest.mark.parametrize("text", ["変更は取り消して、何してるか教えて", "取り消して。何してるか教えて",
                                  "キャンセル、それと説明"])
def test_cancel_plus_question_leaves_no_approvable_candidate(tmp_path, text):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, text)
    not_approvable(w, sid)


@pytest.mark.xfail(strict=True, reason=P0 + " 自然な取り消しの言い方")
@pytest.mark.parametrize("text", ["今の変更はやめて", "今の変更は取り消して", "変更はやめて", "やっぱりやめる",
                                  "今のなし", "取り消し"])
def test_natural_cancellation_leaves_no_approvable_candidate(tmp_path, text):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, text)
    not_approvable(w, sid)


@pytest.mark.parametrize("text", ["今の提案は取り消さないで", "キャンセルはやめて"])
def test_negated_cancellation_keeps_candidate(tmp_path, text):
    """回帰：取り消しの打ち消しでは候補を捨てない（コックさんの修正の確認）。"""
    w, _, sid = pending_three(tmp_path)
    before = w.state(sid)["state"]["candidate"]
    w.talk(sid, text)
    assert w.state(sid)["state"]["candidate"] == before


# ---- 2. ジョブを読み直したあと、前の画面の番号で承認・出力できる（A4-18） ----
@pytest.mark.xfail(strict=True, reason=P0 + " A4-18")
def test_reload_with_previous_invalidates_old_session(tmp_path):
    w, _, sid = pending_three(tmp_path)
    w.load(T.demo_bytes(), previous=sid)
    assert not w.approve(sid, "QA")["ok"]
    assert not w.export(sid)["ok"]


@pytest.mark.xfail(strict=True, reason=P0 + " A4-18（previous なし・別のタブ）")
def test_reload_without_previous_invalidates_old_session(tmp_path):
    w, _, sid = pending_three(tmp_path)
    w.load(T.demo_bytes())
    assert not w.approve(sid, "QA")["ok"]


@pytest.mark.xfail(strict=True, reason=P0 + " 古い番号の出力が新しい出力のファイル名を先に取る")
def test_stale_export_cannot_take_the_new_sessions_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    w, _, old = pending_three(tmp_path)
    assert w.approve(old, "QA")["ok"]
    new = w.load(T.demo_bytes(), previous=old)["session_id"]
    T.repeat_to_three(w, new)
    assert w.approve(new, "QA")["ok"]
    w.export(old)  # 古い番号（画面からは呼ばない。サーバー側の保証を見る）
    assert w.export(new)["ok"]


# ---- 3. 回数の読み違い ----
@pytest.mark.xfail(strict=True, reason=P0 + " 「あと1回」を合計1回と読む（2回→1回の向き）")
def test_ato_ikkai_is_not_read_as_total_one():
    blocks = talk.repeat_blocks(jbi.parse_bytes(T.demo_bytes()))
    r = talk.read_count("あと1回押して", blocks)
    assert r is None or r.total != 1


@pytest.mark.parametrize("text", ["2.3回にして", "3回くらいにして", "たぶん3回"])
def test_unclear_counts_never_make_a_candidate(tmp_path, text):
    """回帰：小数・くらい・ためらいは候補にしない。"""
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    r = w.talk(sid, text)
    assert not r["state"]["candidate"] and not r["state"]["repeat_range"]


# ---- 4. 方向の自然な言い方（安全の線は変えない） ----
@pytest.mark.xfail(strict=True, reason=P0 + " 案1-a 未実装")
@pytest.mark.parametrize("text", ["Yマイナスの方向に10mm", "Y-の方向に10mm", "Yのマイナス方向に10mm",
                                  "Y軸マイナス方向に10mm"])
def test_y_minus_direction_variants_make_the_same_candidate(tmp_path, text):
    w, _, sid = place_flow(tmp_path)
    r = w.talk(sid, text)
    c = r["state"]["candidate"]
    assert c and c["target"] == "C00003 Y" and not r["state"]["approval"]
    assert "-10.000" in (r.get("hear") or {}).get("message", "")


@pytest.mark.parametrize("text", ["Yの方向に10mm", "Yに10mm", "上に3mm", "上に三ミリ", "上に3cm", "右に10mm",
                                  "Yーの方向に10mm"])
def test_unclear_direction_or_amount_never_makes_a_candidate(tmp_path, text):
    """回帰：符号なし・上下が未確認・漢数字・単位違い・右左・長音は候補にしない（推測で確定しない）。"""
    w, _, sid = place_flow(tmp_path)
    r = w.talk(sid, text)
    assert not r["state"]["candidate"]


def test_up_3mm_only_with_confirmed_updown(tmp_path):
    """回帰：「上に3mm」は、上下の向きの設定が照合に通ったときだけ Z+3。"""
    w, _, sid = place_flow(tmp_path, confirmed=True)
    r = w.talk(sid, "上に3mm")
    assert r["state"]["candidate"]["target"] == "C00003 Z" and not r["state"]["approval"]


@pytest.mark.parametrize("text", ["いいね、承認して", "これでOK", "実機に送って"])
def test_words_never_approve(tmp_path, text):
    """回帰：承認はボタンだけ。"""
    w, _, sid = pending_three(tmp_path)
    r = w.talk(sid, text)
    assert r["state"]["approval"] is None and not r["state"]["can_export"]


# ---- 5. 聞き返しのやさしさ ----
@pytest.mark.xfail(strict=True, reason=P0 + " 「終わり」で正しい説明まで隠す（A08・A13・A14・A38・A48 ほか）")
@pytest.mark.parametrize("reply", ["まず最初の位置へ動き、次にボタンを押す動作を同じ形でくり返します。そのあと最後の位置へ移って終わります。",
                                   "ボタンを押す動作をくり返したあと、最後に別の位置へ関節の動きで移って終わります。"])
def test_plain_motion_explanation_is_shown(reply):
    assert talk.reply_mvp_problem(reply) is None


@pytest.mark.parametrize("reply", ["3回に変更しました。", "承認済みです。", "周りに十分な余裕があります。", "出力しました"])
def test_state_and_safety_claims_stay_hidden(reply):
    """回帰：状態・数・安全の決めつけは、これまでどおり出さない。"""
    assert talk.reply_mvp_problem(reply) is not None


@pytest.mark.xfail(strict=True, reason=P0 + " 言葉の切れ端を引用する（「っぱりいいや」）")
def test_clarification_does_not_quote_a_broken_fragment(tmp_path):
    w, _, sid = pending_three(tmp_path)
    r = w.talk(sid, "やっぱりいいや")
    shown = (r.get("ask") or {}).get("reply", "") + (r.get("hear") or {}).get("message", "")
    assert "「っぱり" not in shown


@pytest.mark.xfail(strict=True, reason=P0 + " 単位が無いのに「符号の付く所」を聞き返す")
def test_missing_unit_is_what_the_question_is_about(tmp_path):
    w, _, sid = place_flow(tmp_path)
    r = w.talk(sid, "Z+3")
    msg = (r.get("hear") or {}).get("message", "")
    assert not r["state"]["candidate"] and ("mm" in msg or "ミリ" in msg or "単位" in msg)
    assert "どこに付くか" not in msg
