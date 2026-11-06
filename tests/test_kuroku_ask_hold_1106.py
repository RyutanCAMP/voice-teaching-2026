"""/api/ask（読み取りの入口）に、変更の指示・承認した中身への確認の質問が来たとき（2026-11-06 唐川さんの決定：Option 1 保留）。

CHECKER_039（クロ）で、f42676a では承認済みのまま ask に変更の文を送ると、前の承認が残り、ボタンで古い中身を出力できた（23件）。
要件：
- ask は変更候補を作らない（新しい候補も、範囲の確認も作らない）
- 変更の指示・承認した中身への確認の質問では、前の承認を取り消して（取り消しの印も立てる）、候補を保留にする
- 保留中は承認・出力できない。「このまま残す」でも、承認は画面のボタンでやり直す
- 単純な説明の質問の動きは変えない（承認も残る）
- 保留中の返事の文と、承認・保留の状態を一致させる
偽の Claude・合成ジョブだけ。出力は一時フォルダ。同じ Claude（クローク）が書いたテスト。クロの独立レビューを受ける。
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim

CP = T.OTHER.read_bytes()


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


CHANGE = ["Zプラス方向に3ミリ", "3ミリにして", "C00004をZプラス方向に5ミリ", "下げて", "Zプラス3ミリ動かして", "4回にして",
          "3ミリで出力して"]
CONFIRM = ["5ミリで合ってる？", "Zプラスで良かった？", "これ5ミリで大丈夫？", "違くない？", "ちょっと待って", "これ逆？"]


@pytest.mark.parametrize("state", ["pending", "approved", "exported"])
@pytest.mark.parametrize("said", CHANGE + CONFIRM)
def test_ask_change_or_confirm_revokes_approval_and_holds(tmp_path, out, state, said):
    w, fake, sid = made(tmp_path, state)
    e = w._entries[sid]
    old = e.session.candidate
    approval = e.approval
    g0 = w.candidate_gen_of(sid)
    before = files(out)
    r = w.ask(sid, said, source="voice")
    st = w.state(sid)["state"]
    # ask は変更候補を作らない：候補は前のまま（保留）・範囲の確認も無い
    assert e.session.candidate is old and st["repeat_range"] is None, (state, said)
    assert st["hold"] is True and st["approval"] is None, (state, said, r.get("reply"))
    assert st["can_approve"] is False and st["can_export"] is False
    if approval is not None:
        assert approval.revocation.revoked  # 取り消しの印（手に持って直接出力しても書かない）
        assert "前の承認を取り消しました" in r["reply"], r["reply"]
    else:
        assert "前の承認を取り消しました" not in r["reply"]
    assert "保留" in r["reply"] and r.get("reply_by") == "app"  # 返事の文と状態が一致する（決まった文）
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"]
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert files(out) == before
    assert not fake.requests  # 変更の指示は Claude に送らない


@pytest.mark.parametrize("state", ["approved", "exported"])
def test_ask_hold_then_keep_requires_button_reapproval(tmp_path, out, state):
    w, _, sid = made(tmp_path, state)
    w.ask(sid, "Zプラス方向に3ミリ", source="voice")
    r = w.talk(sid, "このままでいい")
    st = r["state"]
    assert st["hold"] is False and st["approval"] is None  # 保留は解けるが、承認は戻らない
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]  # 画面のボタンで承認し直す
    if state == "approved":
        assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]


def test_ask_hold_then_cancel_drops_candidate(tmp_path, out):
    w, _, sid = made(tmp_path, "approved")
    g0 = w.candidate_gen_of(sid)
    w.ask(sid, "Zプラス方向に3ミリ", source="voice")
    w.talk(sid, "取り消して")
    assert w._entries[sid].session.candidate is None
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"] and not w.export(sid, candidate_gen=g0)["ok"]


@pytest.mark.parametrize("said", ["何をする動き？", "この動作データは何をしていますか？", "C00003はどこ？"])
@pytest.mark.parametrize("state", ["pending", "approved"])
def test_ask_plain_explain_question_is_unchanged(tmp_path, out, said, state):
    w, _, sid = made(tmp_path, state)
    e = w._entries[sid]
    old, approval, g0 = e.session.candidate, e.approval, w.candidate_gen_of(sid)
    r = w.ask(sid, said, source="voice")
    assert r["ok"]
    assert e.session.candidate is old and e.approval is approval and not e.hold
    assert w.candidate_gen_of(sid) == g0


@pytest.mark.parametrize("said", ["取り消して", "やっぱり3ミリ", "いやZプラス3ミリ"])
def test_ask_explicit_cancel_or_marked_correction_still_invalidates(tmp_path, out, said):
    """はっきりした取消・印のある言い直しは、今まで（P0 設計 0-2 (2)）どおり ask でも失効する（新しい候補は作らない）。"""
    w, _, sid = made(tmp_path, "approved")
    g0 = w.candidate_gen_of(sid)
    w.ask(sid, said, source="voice")
    e = w._entries[sid]
    assert e.session.candidate is None and e.approval is None
    assert not w.approve(sid, "QA", candidate_gen=g0)["ok"] and not w.export(sid, candidate_gen=g0)["ok"]


def test_ask_repeat_approved_change_holds(tmp_path, out):
    """回数の候補（承認済み）でも同じ：ask の変更の指示で承認を取り消して保留。"""
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, sid)
    g0 = w.candidate_gen_of(sid)
    assert w.approve(sid, "QA", candidate_gen=g0)["ok"]
    r = w.ask(sid, "4回にして", source="voice")
    st = w.state(sid)["state"]
    assert st["hold"] is True and st["approval"] is None and st["candidate"] is not None
    assert "保留" in r["reply"] and "前の承認を取り消しました" in r["reply"]
    assert not w.export(sid, candidate_gen=g0)["ok"]
    assert files(out) == []
