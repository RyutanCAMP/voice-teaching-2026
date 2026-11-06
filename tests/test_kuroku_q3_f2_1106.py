"""Q-3／F-2 の受入テスト（2026-11-06・唐川さんの方針「新しい変更の指示では古い候補・承認を無効にする」・クローク）。

チャッピーの仕様案（reviews/REVIEW_KUROKU_F1_FIX_f42676a.md 6-1）を、候補が未承認・承認済み・出力済みのどれでも同じに：
| 発話                     | 期待                                               |
| 「やっぱり3ミリにして」  | 古い候補を無効にする（今までどおり）               |
| 「Zプラス方向に3ミリ」   | 新しい変更の指示：古い候補・承認を無効にする       |
| 「5ミリで合ってる？」    | 古い候補を保留にして確かめる（承認・出力できない） |
| 「今のなし」             | 古い候補・承認を無効にする（今までどおり）         |
| 「はい」「OK」           | 今の有効な状態のまま（声では承認しない）           |
- 新しい候補はその場で作らない（古い候補を無効にして、新しい指示の確かめからやり直す）
- 数の無い量の言葉（「半分にして」「もう少し小さく」）は、直す先が分からないので保留
- ふつうの質問（「C00003はどこ？」）は何も変えない

この受入テストは、直す前（f42676a）に書いて失敗を確かめた。偽の Claude・合成ジョブだけ。出力は一時フォルダ。
同じ Claude（クローク）が書いたテスト。外部の独立レビュー・実機の安全の認証の代わりにはならない。
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim, stale

CP = T.OTHER.read_bytes()


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    return d


def files(d: Path) -> list:
    return sorted(d.iterdir()) if d.exists() else []


def made(tmp_path, state):
    """C00005 Z+5 の候補（未承認・承認済み・出力済み）。"""
    w, _ = T.flow(tmp_path, *[T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(30)])
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Zプラス方向に5ミリ")
    assert w._entries[sid].session.candidate.after_value == 205.0
    if state in ("approved", "exported"):
        assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    if state == "exported":
        assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    return w, sid


def old_is_unusable(w, sid, g0, out, before_files):
    e = w._entries[sid]
    c = e.session.candidate
    assert not (c is not None and c.after_value == 205.0 and not e.hold), "古い候補が、保留でも無効でもなく残る"
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"]
    assert not w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"] or (c is not None and c.after_value != 205.0)
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert files(out) == before_files


NEW_INSTRUCTIONS = ["Zプラス方向に3ミリ", "Zマイナス方向に5ミリ", "Yプラス方向に5ミリ", "C00004をZプラス方向に5ミリ",
                    "3ミリにして", "3ミリにしといて", "3ミリでお願い", "3ミリで", "量は3ミリ", "2ミリ減らして",
                    "Zプラス3ミリ動かして", "C00005、Zプラス方向に3ミリ"]


@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
@pytest.mark.parametrize("said", NEW_INSTRUCTIONS)
def test_q3_new_change_instruction_invalidates_old_candidate_and_approval(tmp_path, out, state, said):
    w, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    before = files(out)
    r = w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.session.candidate is None, (state, said, "新しい変更の指示なのに、古い候補が残る")
    assert e.approval is None
    assert w.candidate_gen_of(sid) != g0
    assert not r["hear"].get("candidate_made")  # 新しい候補はその場で作らない
    assert "取り消しました" in r["hear"]["message"]
    old_is_unusable(w, sid, g0, out, before)


CHECK_QUESTIONS = ["5ミリで合ってる？", "Zプラスで良かった？", "3ミリのほうがよくない？", "これ5ミリで大丈夫？"]


@pytest.mark.parametrize("state", ["pending", "approved"])
@pytest.mark.parametrize("said", CHECK_QUESTIONS)
def test_q3_check_question_holds_old_candidate(tmp_path, out, state, said):
    w, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.session.candidate is not None and e.hold, (state, said)
    old_is_unusable(w, sid, g0, out, [])


VAGUE = ["半分にして", "もう少し小さく", "ちょっと減らして", "少なめで", "もうちょい増やして", "もっと大きく"]


@pytest.mark.parametrize("state", ["pending", "approved"])
@pytest.mark.parametrize("said", VAGUE)
def test_q3_vague_amount_change_holds(tmp_path, out, state, said):
    w, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, said, "voice")
    old_is_unusable(w, sid, g0, out, [])


@pytest.mark.parametrize("said", ["はい", "OK", "うん", "どんな変更？", "この変更は何？", "C00003はどこ？"])
def test_q3_ack_and_plain_question_keep_pending_candidate(tmp_path, out, said):
    """未承認の候補：相づち・ふつうの質問では何も変えない（声では承認しない）。"""
    w, sid = made(tmp_path, "pending")
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.session.candidate is not None and e.session.candidate.after_value == 205.0 and not e.hold
    assert e.approval is None
    assert w.candidate_gen_of(sid) == g0
    assert files(out) == []


@pytest.mark.parametrize("said", ["はい", "OK", "うん", "ありがとう"])
def test_q3_ack_keeps_approval(tmp_path, out, said):
    """承認済み：はっきりした相づちだけは承認が残る（質問は今までどおり保留：P0 の安全レビューの決まり）。"""
    w, sid = made(tmp_path, "approved")
    w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.approval is not None and not e.hold
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]  # ボタンの出力は今までどおり


@pytest.mark.parametrize("said", ["今のなし", "やっぱり3ミリにして", "取り消して"])
def test_q3_existing_cancel_and_marked_correction_unchanged(tmp_path, out, said):
    w, sid = made(tmp_path, "approved")
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, said, "voice")
    assert w._entries[sid].session.candidate is None
    old_is_unusable(w, sid, g0, out, [])


def test_q3_after_invalidation_new_instruction_makes_a_new_candidate_to_approve(tmp_path, out):
    """無効にしたあと、もう一度指示すれば新しい候補ができ、人のボタンで承認・出力できる（やり直しの道）。"""
    w, sid = made(tmp_path, "pending")
    w.talk(sid, "Zプラス方向に3ミリ")
    assert w._entries[sid].session.candidate is None
    w.select(sid, "C00005")
    w.talk(sid, "Zプラス方向に3ミリ")
    c = w._entries[sid].session.candidate
    assert c is not None and c.after_value == 203.0
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]


def test_q3_filming_scenario_still_works(tmp_path, out):
    """撮影の流れ：30mm は上限で止まり候補なし → 5mm で候補 → 承認 → 出力。"""
    w, _ = T.flow(tmp_path, *[T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(10)])
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "C00005、Zプラス方向に30ミリ")
    assert w._entries[sid].session.candidate is None
    w.talk(sid, "C00005、Zプラス方向に5ミリ")
    c = w._entries[sid].session.candidate
    assert c is not None and (c.before_value, c.after_value) == (200.0, 205.0)
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert len(files(out)) == 1


# 見分けの関数（プログラムの決まり）
@pytest.mark.parametrize("text,kind", [
    ("Zプラス方向に3ミリ", stale.CORRECTION), ("3ミリにして", stale.CORRECTION), ("C00004をZプラス方向に5ミリ", stale.CORRECTION),
    ("4回にして", stale.CORRECTION), ("5ミリで合ってる？", stale.UNSURE), ("半分にして", stale.UNSURE),
    ("はい", stale.OTHER), ("どんな変更？", stale.OTHER), ("C00003はどこ？", stale.OTHER), ("いやです", stale.OTHER),
])
def test_q3_classify(text, kind):
    assert stale.classify(text).kind == kind, (text, stale.classify(text))
