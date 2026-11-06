"""Q-3／F-2 の受入テスト（2026-11-06・唐川さんの方針「新しい変更の指示では古い候補・承認を無効にする」・クローク）。

チャッピーの仕様案（reviews/REVIEW_KUROKU_F1_FIX_f42676a.md 6-1）を、候補が未承認・承認済み・出力済みのどれでも同じに：
| 発話                     | 期待                                               |
| 「やっぱり3ミリにして」  | 古い候補を無効にする（今までどおり）               |
| 「Zプラス方向に3ミリ」   | 新しい変更の指示：古い候補・承認を無効にする       |
| 「5ミリで合ってる？」    | 古い候補を保留にして確かめる（承認・出力できない） |
| 「今のなし」             | 古い候補・承認を無効にする（今までどおり）         |
| 「はい」「OK」           | 今の有効な状態のまま（声では承認しない）           |
- 仕様 A（12:29 正式採用）：完全な新しい指示なら、失効したうえで検査を通した新しい未承認の候補を作る。足りなければ聞き返す
- 数の無い量の言葉（「半分にして」「もう少し小さく」）は、直す先が分からないので保留
- ふつうの質問（「C00003はどこ？」）は何も変えない

この受入テストは、直す前（f42676a）に書いて失敗を確かめた。偽の Claude・合成ジョブだけ。出力は一時フォルダ。
同じ Claude（クローク）が書いたテスト。外部の独立レビュー・実機の安全の認証の代わりにはならない。
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import jbi, sim, stale

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


# 仕様 A（11/6 12:29 唐川さんの決定）：完全な新しい指示 → 古い候補・承認を失効し、安全の検査を通した新しい未承認の候補を作る
FULL = [("Zプラス方向に3ミリ", "C00005 Z", "+3.000"), ("Zマイナス方向に5ミリ", "C00005 Z", "-5.000"),
        ("Yプラス方向に5ミリ", "C00005 Y", "+5.000"), ("C00004をZプラス方向に5ミリ", "C00004 Z", "+5.000"),
        ("Zプラス3ミリ動かして", "C00005 Z", "+3.000"), ("C00005、Zプラス方向に3ミリ", "C00005 Z", "+3.000"),
        ("いやZプラス3ミリ", "C00005 Z", "+3.000"), ("Yのマイナス3ミリ", "C00005 Y", "-3.000")]
# 情報の足りない言い直し（軸・向きが無い）：前の候補の値を引き継がず、旧候補・承認を失効して聞き返す（12:34 唐川さんの決定）
PARTIAL = ["3ミリにして", "3ミリにしといて", "3ミリでお願い", "3ミリで", "量は3ミリ", "2ミリ減らして", "やっぱり3ミリにして"]


@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
@pytest.mark.parametrize("said,target,delta", FULL)
def test_q3_full_instruction_invalidates_old_and_makes_new_unapproved_candidate(tmp_path, out, state, said, target, delta):
    w, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    before = files(out)
    r = w.talk(sid, said, "voice")
    e = w._entries[sid]
    c = r["state"]["candidate"]
    assert c is not None and (c["target"], c["delta"]) == (target, delta), (state, said, c)
    assert e.approval is None and r["state"]["can_export"] is False  # 新しい候補は未承認
    assert w.candidate_gen_of(sid) != g0
    assert "取り消しました" in r["hear"]["message"]
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"]   # 古い世代では承認・出力できない
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]  # 新しい候補もボタンで承認するまで出力できない
    assert files(out) == before
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]  # 人のボタンで承認できる
    if state != "exported":  # 出力済みは同じ名前があるので出力しない（上書きしない）
        assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
        got = jbi.parse_bytes(files(out)[-1].read_bytes())
        pos, axis = target.split()
        orig = jbi.parse_bytes(CP).positions[pos].values[jbi.AXES[axis]]
        assert got.positions[pos].values[jbi.AXES[axis]] == round(orig + float(delta), 3)  # 承認した新しい値（古い 205 ではない）
        if target != "C00005 Z":
            assert got.positions["C00005"].values[jbi.AXES["Z"]] == 200.0  # 古い候補（C00005 Z 205）は入らない


@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
@pytest.mark.parametrize("said", PARTIAL)
def test_q3_partial_instruction_invalidates_old_and_asks_back(tmp_path, out, state, said):
    w, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    before = files(out)
    r = w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.session.candidate is None, (state, said, "前の候補の値を引き継いで候補を作った")
    assert e.approval is None
    assert w.candidate_gen_of(sid) != g0
    assert not r["hear"].get("candidate_made")
    assert "取り消しました" in r["hear"]["message"]
    assert r["hear"]["status"] == "ask"  # 足りない情報を聞き返す
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
    w.talk(sid, "3ミリにして")  # 足りない指示：失効して聞き返す
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


# ---- 11/6 別の視点の Safety レビュー（F1〜F5）への対応 ----
@pytest.mark.parametrize("said", ["下げて", "5ミリで合ってる？", "マイナス", "3ミリで出力して", "5ミリくらい", "5ミリでいい"])
def test_q3_f1_rule10_hold_also_revokes_approval(tmp_path, out, said):
    """F1：規則 10 で保留にしたときは、承認も取り消す（取り消しの印を立てる）。保留の印だけに頼らない。"""
    w, sid = made(tmp_path, "approved")
    approval = w._entries[sid].approval
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, said, "voice")
    e = w._entries[sid]
    assert e.hold and e.approval is None, said
    assert approval.revocation.revoked
    with pytest.raises(sim.SimError):
        sim.export_sim(sim.simulate_shift(e.session.original, "C00005", "Z", 5.0), approval)
    old_is_unusable(w, sid, g0, out, [])
    w.talk(sid, "このままでいい")  # 保留を解いても、承認はもう一度ボタンで
    assert not w._entries[sid].hold and w._entries[sid].approval is None
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]


@pytest.mark.parametrize("text,kind", [
    ("何を動かしているの？", stale.OTHER), ("どこを動かしているの？", stale.OTHER),          # F2：ふつうの質問
    ("5ミリでいい", stale.UNSURE), ("5ミリのままでいい", stale.UNSURE), ("3回だよね", stale.UNSURE),  # F3：確かめるだけ
    ("うん5ミリ", stale.UNSURE),
    ("C00004をZプラス", stale.UNSURE),                                                       # F4：符号だけ（位置番号の数字は数えない）
    ("ごミリ", stale.UNSURE), ("上にじゅうミリ", stale.UNSURE), ("3センチ", stale.UNSURE),  # F5：単位だけ読めた量
    ("上に", stale.UNSURE), ("もう少し上に", stale.UNSURE),                                 # F5：方向だけ
    ("Zプラス方向に3ミリ", stale.CORRECTION), ("はい", stale.OTHER), ("このままでいい", stale.KEEP),
])
def test_q3_review_classify(text, kind):
    assert stale.classify(text).kind == kind, (text, stale.classify(text))
