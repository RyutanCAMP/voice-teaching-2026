"""K1（2026-11-06 唐川さんの許可）：候補があるときの「もうちょっと」「もうちょっと上」など、変更の意思はあるが情報が足りない発言。

実 Claude の確認（REPORT_KUROKU_NL_QUALITY_1106 R1・R2）で、古い未承認の候補が承認できるまま残った（3/3・3版とも）。要件：
- 古い候補をそのまま承認できない（保留）。承認済みなら承認も取り消す（取り消しの印も立てる）。保留中は出力できない
- 「このまま残す」でも、承認は画面のボタンでやり直す
- 前の値・軸を勝手に引き継がない。新しい候補を勝手に作らない
- 足りないこと（量・向き）を自然に聞き返す
偽の Claude・合成ジョブだけ。出力は一時フォルダ。同じ Claude（クローク）が書いたテスト。クロの独立 Safety レビューを受ける。
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim, stale

CP = T.OTHER.read_bytes()

MORE = ["もうちょっと", "もうちょっと上", "もう少し", "もう少し上に", "もうちょい", "もっと", "もっと上", "少しだけ", "あと少し",
        "もうちょっと？", "もう少し右へ", "もうちょっとお願い", "もうちょっと下", "モウチョット", "もうすこし"]
# K1 にしない言葉（今の道のまま）
NOT_MORE = ["ちょっと待って", "もう少し説明して", "もっと詳しく教えて", "もうちょっとって何？", "このままでいい", "うん"]


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    return d


def files(d: Path) -> list:
    return sorted(d.iterdir()) if d.exists() else []


def made(tmp_path, state):
    w, fake = T.flow(tmp_path, *[T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(30)])
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Zプラス方向に5ミリ")
    if state in ("approved", "exported"):
        assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    if state == "exported":
        assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    return w, fake, sid


def say(w, sid, entry, text):
    if entry == "talk":
        return w.talk(sid, text, "voice")
    if entry == "hear":
        return w.hear(sid, text, "voice")
    return w.ask(sid, text, source="voice")


@pytest.mark.parametrize("said", MORE)
def test_classify_more_is_vague_hold(said):
    v = stale.classify(said)
    assert v.kind == stale.UNSURE and v.rule.startswith("10"), (said, v)


@pytest.mark.parametrize("said", NOT_MORE)
def test_classify_not_more(said):
    assert stale.classify(said).rule != "10 more", said


@pytest.mark.parametrize("entry", ["talk", "hear", "ask"])
@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
@pytest.mark.parametrize("said", MORE)
def test_more_holds_old_candidate(tmp_path, out, entry, state, said):
    w, fake, sid = made(tmp_path, state)
    e = w._entries[sid]
    old, approval, g0 = e.session.candidate, e.approval, w.candidate_gen_of(sid)
    before_vals = (old.target, old.after_value, old.after_line)
    before = files(out)
    r = say(w, sid, entry, said)
    st = w.state(sid)["state"]
    # 新しい候補を作らない・前の値を書き換えない（引き継がない）・範囲の確認も無い
    assert e.session.candidate is old and (old.target, old.after_value, old.after_line) == before_vals, (entry, state, said)
    assert st["repeat_range"] is None
    # 保留：承認・出力できない。承認済みなら承認も取り消す（取り消しの印）
    assert st["hold"] is True and st["approval"] is None, (entry, state, said, r.get("message") or r.get("reply"))
    assert st["can_approve"] is False and st["can_export"] is False
    if approval is not None:
        assert approval.revocation.revoked
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"]
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert files(out) == before
    assert not fake.requests  # Claude に送らない（プログラムが決まった文で聞き返す）
    msg = r.get("message") or r.get("reply") or (r.get("hear") or {}).get("message") or ""
    assert "保留" in msg and "承認・出力はできません" in msg, msg
    if approval is not None:
        assert "前の承認を取り消しました" in msg, msg
    if entry != "ask":
        assert "どのくらい動かしますか" in msg, msg  # 足りないもの（量）を聞く
        assert "5ミリ" not in msg and "+5" not in msg  # 前の量を案内に使わない


@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
def test_more_then_keep_requires_button_reapproval(tmp_path, out, state):
    w, _, sid = made(tmp_path, state)
    w.talk(sid, "もうちょっと", "voice")
    r = w.talk(sid, "このままでいい", "voice")
    st = r["state"]
    assert st["hold"] is False and st["approval"] is None
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]


@pytest.mark.parametrize("state", ["pending", "approved"])
def test_more_then_amount_only_does_not_inherit_axis(tmp_path, out, state):
    """「もうちょっと」→「8ミリ」：前の軸・向き（Zプラス）を引き継いで候補にしない。古い候補は無効、新しい候補も無い。"""
    w, _, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, "もうちょっと", "voice")
    w.talk(sid, "8ミリ", "voice")
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["approval"] is None
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"] and not w.export(sid, candidate_gen=g0)["ok"]


@pytest.mark.parametrize("state", ["pending", "approved"])
def test_more_then_complete_instruction_makes_new_unapproved(tmp_path, out, state):
    """「もうちょっと」→「Zプラス8ミリ」：完全な指示は仕様 A のとおり新しい未承認の候補（検査を通る）。古い世代は使えない。"""
    w, _, sid = made(tmp_path, state)
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, "もうちょっと", "voice")
    w.talk(sid, "Zプラス8ミリ", "voice")
    st = w.state(sid)["state"]
    c = w._entries[sid].session.candidate
    assert c is not None and c.target.split()[-1] == "Z" and abs(c.requested - 8.0) < 1e-9
    assert st["approval"] is None and st["hold"] is False
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"] and not w.export(sid, candidate_gen=g0)["ok"]


def test_more_then_cancel_drops_candidate(tmp_path, out):
    w, _, sid = made(tmp_path, "approved")
    g0 = w.candidate_gen_of(sid)
    w.talk(sid, "もうちょっと上", "voice")
    w.talk(sid, "取り消して", "voice")
    assert w._entries[sid].session.candidate is None
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"] and not w.export(sid, candidate_gen=g0)["ok"]


def test_more_without_candidate_makes_nothing(tmp_path, out):
    w, _ = T.flow(tmp_path, *[T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(5)])
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "もうちょっと上", "voice")
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["repeat_range"] is None


def test_more_on_repeat_candidate_asks_count(tmp_path, out):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, sid)
    g0 = w.candidate_gen_of(sid)
    assert w.approve(sid, "QA", candidate_gen=g0)["ok"]
    r = w.talk(sid, "もうちょっと", "voice")
    st = w.state(sid)["state"]
    assert st["hold"] is True and st["approval"] is None and st["candidate"] is not None
    msg = (r.get("hear") or {}).get("message") or ""
    assert "回" in msg and "どのくらい動かしますか" not in msg, msg
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert files(out) == []


@pytest.mark.parametrize("said", ["ちょっと待って", "うん"])
def test_not_more_pending_is_unchanged(tmp_path, out, said):
    """K1 の言葉でないもの：未承認の候補のとき、今までどおりの道（この変更で動きを変えない）。"""
    w, _, sid = made(tmp_path, "pending")
    before = w._entries[sid].session.candidate
    w.talk(sid, said, "voice")
    assert w._entries[sid].session.candidate is before
