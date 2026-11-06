"""P0 H02（言い直し）・H16（取消＋説明）：取消・言い直しを受けた時点で、古い変更候補を無効にする。
設計：docs/DESIGN_P0_STALE_CANDIDATES_20261105.md（0章の唐川さんの判断が優先）。本物の Claude は呼ばない（偽の client）。

- 無効：候補・承認を捨て、承認の記録に取り消した印を立て、候補の世代を進める。承認・出力は断る（K1・K5・K6）
- 保留：はっきりしない文（「3回じゃなくて4回？」）。候補は残すが、承認・出力は断る。「このままでいい」で保留を解くと承認は消える
- 世代：承認・出力は画面が見ていた候補の世代が要る（省略・古い番号は断る。Q-4）
- 2層目：取り消した Approval を手に持ったまま sim.export_sim を呼んでも書かない（0-2 (1)）
- 同じ Claude が書いたテスト。外部の独立レビューの代わりにはならない
"""
import re
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim, stale
from voice_teaching.web import flow as F

REPO = Path(__file__).resolve().parent.parent
DEMO_C = (REPO / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def pending_three(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    out = T.repeat_to_three(w, sid)
    assert out["state"]["candidate"] and out["state"]["can_approve"]
    return w, fake, sid


def approve(w, sid):
    r = w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))
    assert r["ok"], r
    return r


def files(d: Path) -> list:
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def voided(w, sid, gen_before):
    """V：候補なし・承認なし・世代が進んだ・どの世代でも承認・出力できない。"""
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["approval"] is None
    assert not st["can_approve"] and not st["can_export"]
    assert st["repeat_range"] is None and st["dialog"]["step"] is None
    assert st["candidate_gen"] > gen_before
    assert st["voided"] is not None
    for gen in (gen_before, st["candidate_gen"]):
        assert not w.approve(sid, "QA", candidate_gen=gen)["ok"]
        assert not w.export(sid, candidate_gen=gen)["ok"]
    return st


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    return d


# ---- 1. H02 言い直し ----
CORRECTIONS = ["さっきの3回じゃなくて2回", "3回じゃなくて4回", "3回じゃなくて4回にして", "3回ではなく4回", "3回でなく4回",
               "違う、4回", "違う違う、4回にして", "いや、4回", "やっぱり4回", "やっぱり4回にして", "さっきのは間違い、4回",
               "4回に変えて", "4回に直して", "4回に変更して", "取り消して、4回にして", "いや、2回のままでいい",
               "3回じゃなくて2.5回", "5回じゃなくて4回"]


@pytest.mark.parametrize("text", CORRECTIONS)
def test_h02_correction_voids_old_candidate(tmp_path, text):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    r = w.talk(sid, text)
    st = voided(w, sid, gen)
    msg = r["hear"]["message"]
    assert "言い直しを受け取ったため" in msg and "2回 → 3回" in msg and "承認・出力はできません" in msg
    assert st["voided"]["why"] == "言い直し"
    assert not fake.requests  # Claude は呼ばない（K8）
    assert not r["state"]["repeat_range"] and not r["state"]["candidate"]  # 新しい候補・範囲は作らない（Q-2 案A）


def test_h02_new_count_is_restated_not_made(tmp_path):
    # 仕様A（11/6 12:29 唐川さんの決定）：完全な言い直し・新しい指示は、古い候補・承認を失効したうえで、新しい未承認の候補を作る（前の期待：候補なし）
    # 回数の頼み「4回」は新しい指示としてふつうの道へ通す。ふつうの道は 2回→3回 だけを扱うので、4回の候補・範囲の確かめは作らない
    w, fake, sid = pending_three(tmp_path)
    old_gen = w.candidate_gen_of(sid)
    r = w.talk(sid, "3回じゃなくて4回")
    m = r["hear"]["message"]
    assert m.startswith("言い直しを受け取ったため、前の変更候補（C00001〜C00003 の作業を 2回 → 3回）を取り消しました。"), m
    assert "新しい指示として読みます" in m and "4回にはしません" in m
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["approval"] is None and st["repeat_range"] is None  # 古い 3回の候補は残らない
    assert st["candidate_gen"] > old_gen
    assert w.approve(sid, "QA", candidate_gen=old_gen)["ok"] is False
    assert w.export(sid, candidate_gen=old_gen)["ok"] is False
    assert not fake.requests  # Claude は呼ばない
    w2, _, sid2 = pending_three(tmp_path)
    m = w2.talk(sid2, "いや、2回のままでいい")["hear"]["message"]
    assert "今2回です" in m
    w3, _, sid3 = pending_three(tmp_path)
    assert "整数" in w3.talk(sid3, "3回じゃなくて2.5回")["hear"]["message"]


def test_h02_after_approval_and_export(tmp_path, out_dir):
    w, _, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    assert w.export(sid, candidate_gen=gen)["ok"]
    before = files(out_dir)
    r = w.talk(sid, "さっきの3回じゃなくて2回")
    voided(w, sid, gen)
    msg = r["hear"]["message"]
    assert "前の承認も取り消しました" in msg and "そのまま残っています" in msg and "実機には入れないでください" in msg
    assert files(out_dir) == before  # ファイルは消さない・増やさない


def test_h02_move_candidate_does_not_borrow_old_values(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Yマイナス方向に10mm")
    assert w.state(sid)["state"]["candidate"]
    gen = w.candidate_gen_of(sid)
    r = w.talk(sid, "10mmじゃなくて5mm")
    voided(w, sid, gen)
    assert "位置・方向・量（mm）をそろえて" in r["hear"]["message"]


# ---- 2. H16 取消＋説明 ----
@pytest.mark.parametrize("text,part", [
    ("今の提案は取り消して、何をしているか説明して", "何をしているか説明して"),
    ("変更は取り消して、何してるか教えて", "何してるか教えて"),
    ("取り消してから説明して", "説明して"),
    ("キャンセルして、何をしているか教えて", "何をしているか教えて")])
def test_h16_cancel_then_explain(tmp_path, text, part):
    w, fake, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    r = w.talk(sid, text)
    voided(w, sid, gen)
    assert r["via"] == ["hear", "ask"]
    assert "前の変更候補（C00001〜C00003 の作業を 2回 → 3回）を取り消しました" in r["hear"]["message"]
    assert "前の承認も取り消しました" in r["hear"]["message"]
    assert len(fake.requests) == 1
    sent = str(fake.requests[0]["messages"][-1])
    assert part in sent  # 説明の節だけを送る（取消の節は送らない）
    assert "取り消して" not in sent and "キャンセル" not in sent


def test_h16_approve_during_claude_wait_is_refused(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    seen = {}
    real = w.explainer.ask

    def ask_and_try(*a, **k):  # Claude を待つ間に、前の世代で承認を送る
        seen["approve"] = w.approve(sid, "QA", candidate_gen=gen)
        return real(*a, **k)
    w.explainer.ask = ask_and_try
    fake.add(T.answer("説明です。", [21]))
    w.talk(sid, "取り消して、説明して")
    assert seen["approve"]["ok"] is False
    voided(w, sid, gen)


# ---- 3. 取消だけ・自然な言い方 ----
@pytest.mark.parametrize("text", ["取り消して", "今の提案は取り消して", "変更は取り消して", "今の候補を取り消して",
                                  "今の変更はやめて", "やっぱりやめる", "今のなし", "取り消し", "キャンセル"])
def test_cancel_voids(tmp_path, text):
    w, fake, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, text)
    voided(w, sid, gen)
    assert not fake.requests


# ---- 4. 保留（はっきりしない） ----
@pytest.mark.parametrize("text", ["3回じゃなくて4回？", "3回じゃなくて4回かな", "4回のほうがいいかな", "取り消すか迷っている",
                                  "違う", "今の提案は違う", "やっぱり", "取り消して承認して", "3回はやめて",
                                  "取り消して、3回にした理由を説明して"])
def test_unsure_holds_without_discarding(tmp_path, text):
    w, fake, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert st["candidate"] is not None and st["hold"] is True  # 捨てない（K3）
    assert not st["can_approve"] and not st["can_export"]
    assert st["candidate_gen"] == gen  # 世代は進めない
    assert not w.export(sid, candidate_gen=gen)["ok"]  # 承認済みでも、保留の間は出力しない
    assert "保留" in st["status_line"]
    assert not fake.requests


def test_hold_keep_requires_reapproval(tmp_path, out_dir):
    w, _, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, "3回じゃなくて4回？")
    r = w.talk(sid, "このままでいい")
    st = r["state"]
    assert st["hold"] is False and st["candidate"] is not None and st["approval"] is None  # 承認は消える
    assert "前の承認は取り消しました" in r["hear"]["message"]
    assert not w.export(sid, candidate_gen=gen)["ok"]
    approve(w, sid)  # もう一度ボタンで承認すれば、今までどおり出力できる
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert files(out_dir)


def test_hold_then_cancel_voids(tmp_path):
    w, _, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, "3回じゃなくて4回かな")
    w.talk(sid, "取り消して")
    voided(w, sid, gen)


def test_hold_question_keeps_hold(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    w.talk(sid, "3回じゃなくて4回？")
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    w.talk(sid, "このロボット、何をしているの？")
    assert w.state(sid)["state"]["hold"] is True


def test_hold_undo_button_voids(tmp_path):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, "違う")
    w.undo(sid)
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["hold"] is False


# ---- 5. 何も変えない（K2） ----
@pytest.mark.parametrize("text", ["取り消さないで", "キャンセルはやめて", "取り消すとどうなる？", "取り消し方を教えて"])
def test_keep_and_how_to_cancel_change_nothing(tmp_path, text):
    w, fake, sid = pending_three(tmp_path)
    approve(w, sid)
    before = w.state(sid)["state"]
    w.talk(sid, text)
    after = w.state(sid)["state"]
    # 候補・世代・保留は変えない。承認は、新しい発話で取り消すことがある（今までの決まり。止める側）
    for k in ("candidate", "candidate_gen", "hold"):
        assert after[k] == before[k], k
    assert after["voided"] is None


@pytest.mark.parametrize("text", ["このロボット、何をしているの？", "説明して", "どこが変わるの？", "なぜ3回なの？",
                                  "今の候補は何回？", "3回にするとどうなる？"])
def test_questions_do_not_void(tmp_path, text):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert st["candidate"] is not None and st["can_approve"] and st["candidate_gen"] == gen and not st["hold"]


def test_no_candidate_keeps_current_behaviour(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    fake.add(T.answer("回数の変更ですね。", [21], intent="change_request", change={"kind": "repeat"}))
    r = w.talk(sid, "2回じゃなくて3回に変更して")
    assert r["hear"] is None or "stale" not in r["hear"]
    assert w.state(sid)["state"]["voided"] is None


# ---- 6. 入口ごと（/api/hear・/api/ask も同じ見分け。0-2 (2)） ----
def test_hear_entrance_voids(tmp_path):
    w, _, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    r = w.hear(sid, "3回じゃなくて4回")
    assert r["stale"] == stale.CORRECTION
    voided(w, sid, gen)


def test_hear_entrance_cancel_explain_does_not_call_claude(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    r = w.hear(sid, "取り消して、説明して")
    voided(w, sid, gen)
    assert "何をしているか教えて" in r["message"] and not fake.requests


def test_ask_entrance_voids(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    r = w.ask(sid, "3回じゃなくて4回")
    assert r["stale"] == stale.CORRECTION and not fake.requests
    voided(w, sid, gen)


def test_ask_entrance_holds(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    w.ask(sid, "3回じゃなくて4回かな")
    assert w.state(sid)["state"]["hold"] is True and not fake.requests


def test_ask_entrance_cancel_explain_sends_only_explain(tmp_path):
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    r = w.ask(sid, "取り消して、何をしているか教えて")
    voided(w, sid, gen)
    assert r["ok"] and len(fake.requests) == 1
    assert "取り消して" not in str(fake.requests[0]["messages"][-1])


# ---- 7. 候補の世代（Q-4。PY-01〜PY-03） ----
def test_approve_and_export_need_generation(tmp_path):
    w, _, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    for bad in (None, "1", 1.0, True, gen - 1, gen + 1):
        assert w.approve(sid, "QA", candidate_gen=bad)["error"] in (F.M_NOGEN, F.M_STALEGEN)
    with pytest.raises(TypeError):
        w.approve(sid, "QA")  # 省略はできない（引数の名前を指定して渡す）
    with pytest.raises(TypeError):
        w.export(sid)
    assert w.approve(sid, "QA", candidate_gen=gen)["ok"]
    for bad in (None, gen + 1):
        assert not w.export(sid, candidate_gen=bad)["ok"]


def test_new_candidate_after_correction_has_new_generation(tmp_path):
    """H02-11：言い直しのあと、新しい候補は新しい世代。前の世代の承認は断る。"""
    w, _, sid = pending_three(tmp_path)
    old = w.candidate_gen_of(sid)
    w.talk(sid, "3回じゃなくて4回")
    r = T.repeat_to_three(w, sid)  # 試作が扱う回数の変更は 2回→3回 だけ。もう一度ボタンで作り直す
    new = r["state"]["candidate_gen"]
    assert r["state"]["candidate"] and new > old
    assert not w.approve(sid, "QA", candidate_gen=old)["ok"]
    assert w.approve(sid, "QA", candidate_gen=new)["ok"]


def test_state_reports_generation_and_status_line(tmp_path):
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, "3回じゃなくて4回")
    st = w.state(sid)["state"]
    assert isinstance(st["candidate_gen"], int)
    assert "前の候補は言い直しのため取り消しました" in st["status_line"]


# ---- 8. 2層目：取り消した承認を手に持ったまま出力しても書かない（PY-04〜PY-07） ----
@pytest.mark.parametrize("how", ["cancel", "correction", "reload", "undo_button"])
def test_revoked_approval_cannot_export_directly(tmp_path, out_dir, how):
    w, _, sid = pending_three(tmp_path)
    approve(w, sid)
    e = w._entries[sid]
    proposal, approval = e.sim_proposal, e.approval
    if how == "cancel":
        w.talk(sid, "取り消して")
    elif how == "correction":
        w.talk(sid, "3回じゃなくて4回")
    elif how == "reload":
        w.load(T.demo_bytes(), previous=sid)
    else:
        w.undo(sid)
    assert approval.revocation.revoked is True
    with pytest.raises(sim.SimError, match="取り消されている"):
        sim.export_sim(proposal, approval)
    assert files(out_dir) == []


def test_current_approval_still_exports(tmp_path, out_dir):
    w, _, sid = pending_three(tmp_path)
    approve(w, sid)
    e = w._entries[sid]
    assert sim.export_sim(e.sim_proposal, e.approval)
    assert files(out_dir)


def test_only_webflow_calls_export_sim():
    """PY-08：アプリの中で sim.export_sim・sim.approve を呼ぶのは web/flow.py だけ。"""
    hits = []
    for p in (REPO / "voice_teaching").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if re.search(r"\bsim\.(export_sim|approve)\(", t):
            hits.append(p.relative_to(REPO).as_posix())
    assert hits == ["voice_teaching/web/flow.py"]


# ---- 9. 文の決まり（K9） ----
def test_messages_have_no_assertive_safety_words():
    words = ("安全", "安心", "当たる", "ぶつかる", "干渉", "衝突", "問題ない", "大丈夫")
    for name in dir(F):
        if name.startswith("M_"):
            text = getattr(F, name)
            assert not any(w in text for w in words), name


# ---- 10. クロークさんの申し送り（Issue #5）：承認済みの移動の候補のあとの言い直し ----
def approved_move(tmp_path, out_dir=None):
    w, fake = T.flow(tmp_path)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Yマイナス方向に10mm")
    assert w.state(sid)["state"]["candidate"]
    approve(w, sid)
    return w, fake, sid


@pytest.mark.parametrize("text", ["やっぱり5ミリ", "やっぱり5mm", "さっきのは間違い、5ミリ", "違う、5ミリ", "いや、5mm",
                                  "10mmじゃなくて5mm", "５ミリに変えて", "ヤッパリ5ミリ"])
def test_approved_move_restated_is_voided(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    r = w.talk(sid, text)
    voided(w, sid, gen)
    assert "前の承認も取り消しました" in r["hear"]["message"]
    assert files(out_dir) == [] and not fake.requests


def test_approved_move_restated_via_ask_is_voided(tmp_path, out_dir):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    w.ask(sid, "やっぱり5ミリ")
    voided(w, sid, gen)
    assert files(out_dir) == []


# ---- 11. 独立チェック（11/5 夜 Safety FAIL・QA CONDITIONAL PASS）の再現の文 ----
SAFETY_LEAKS = ["やっぱりやめた", "やっぱりいいや", "やっぱいいや", "10じゃなくて5", "やっぱり五ミリ", "じゃなくて五ミリ",
                "とりけして", "トリケシテ", "取りけして", "もどして", "元にもどして", "戻す", "今のナシ", "ナシで", "やっぱなし", "なしなし",
                "撤回して", "なかったことにして", "消して", "候補を消して", "破棄して", "リセットして", "さっきの間違ってた",
                "ストップ", "待って", "やっぱ5ミリ", "ヤッパリ5ミリ", "チガウ、5ミリ", "いや5ミリ", "いやいや5ミリ", "いえ、5ミリ",
                "ごめん5ミリ", "や っぱり5ミリ", "取り下げて", "さっきのはやめて", "今のはやめて", "それはやめて",
                "今の変更は無しで", "今の変更いらない", "ヤメて", "リセット"]


@pytest.mark.parametrize("text", SAFETY_LEAKS)
def test_after_approval_no_leak_can_export(tmp_path, out_dir, text):
    """承認済みの古い候補が、どの言い方のあとも出力できない（無効か保留）。"""
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"] and not st["can_approve"], (text, st["status_line"])
    for g in (gen, st["candidate_gen"]):
        assert not w.export(sid, candidate_gen=g)["ok"], text
        assert not w.approve(sid, "QA", candidate_gen=g)["ok"], text
    assert files(out_dir) == []


@pytest.mark.parametrize("text", ["3じゃなくて4", "とりけして", "やっぱりやめた"])
def test_after_approval_count_no_leak(tmp_path, out_dir, text):
    w, _, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    w.talk(sid, text)
    assert not w.export(sid, candidate_gen=gen)["ok"] and not w.state(sid)["state"]["can_export"]
    assert files(out_dir) == []


@pytest.mark.parametrize("text", ["3回と4回ではどう違う？", "何が違う？", "C00003とC00004は何が違うの？", "これは溶接じゃないの？",
                                  "間違いないですか？", "やっぱりこれで合ってる？", "元に戻すボタンはどこ？"])
def test_questions_with_marker_words_do_not_hold(tmp_path, text):
    """QA M-1：言い直しの印の言葉が入った、ふつうの質問は保留にしない（承認の前。候補・世代を変えない）。
    承認のあとは、否定の言葉の入った質問は保留にする（Safety 再検証を優先。止める側。test_after_approval_doubt_question_holds）"""
    w, fake, sid = pending_three(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    w.talk(sid, text, source="voice")
    st = w.state(sid)["state"]
    assert st["hold"] is False and st["candidate"] is not None and st["candidate_gen"] == gen and st["voided"] is None, text


def test_hold_undo_button_voids_after_hold(tmp_path):
    """QA L-1：保留になったことを確かめてから［元に戻す］。"""
    w, _, sid = pending_three(tmp_path)
    w.talk(sid, "違う")
    assert w.state(sid)["state"]["hold"] is True
    gen = w.candidate_gen_of(sid)
    w.undo(sid)
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["hold"] is False and st["candidate_gen"] > gen


# ---- 12. Safety 再検証（e145ff6・2回目）：承認のあとに承認を残すのは、相づちと否定の無い質問だけ ----
SAFETY_LEAKS_2 = ["消しといて", "けしといて", "戻しといて", "もどしといて", "止めて", "中止", "中止して", "それいらない", "これ要らない",
                  "さっきのやつナシ", "アンドゥ", "undo", "cancel", "待った", "まった", "ちょいまち", "ちゃう", "マチガエタ",
                  "さっきのはおかしい", "違くない？", "それ違くない？", "今のはなし？", "さっきの間違ってたよね？", "やっぱり違うんじゃない？",
                  "すみません、5ミリでした", "言い直す、5ミリ", "ちゃうちゃう、5ミリ", "5ミリじゃなかった？", "うーん"]


@pytest.mark.parametrize("text", SAFETY_LEAKS_2)
def test_after_approval_second_round_no_leak(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]), T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"], (text, st["status_line"])
    for g in (gen, st["candidate_gen"]):
        assert not w.export(sid, candidate_gen=g)["ok"], text
    assert files(out_dir) == []


@pytest.mark.parametrize("text", ["はい", "OK", "ありがとう", "このロボット、何をしているの？", "どこへ動くの？", "何回動くの？"])
def test_after_approval_ack_and_plain_question_keep_approval(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert st["approval"] is not None and st["can_export"] and not st["hold"], text


@pytest.mark.parametrize("text", ["ここで止まってる？", "動きは決まってるの？", "いいやり方ある？", "けしてぶつからない？",
                                  "ここではきちんと止まる？"])
def test_unapproved_questions_do_not_hold(tmp_path, text):
    """Safety 再検証 M-1：文の途中の「まって」「いいや」「けして」「はき」で保留にしない（承認の前）。"""
    w, fake, sid = pending_three(tmp_path)
    fake.add(T.answer("C00001〜C00003 の動きをくり返しています。", [21]))
    w.talk(sid, text)
    assert w.state(sid)["state"]["hold"] is False, text


def test_after_approval_button_word_keeps_approval(tmp_path):
    """「出力して」はボタンで行う案内だけ（保留にしない。撮影の流れを止めない）。"""
    w, _, sid = approved_move(tmp_path)
    w.talk(sid, "出力して")
    st = w.state(sid)["state"]
    assert st["approval"] is not None and st["can_export"]


@pytest.mark.parametrize("text", ["何が違う？", "これは溶接じゃないの？", "やっぱりこれで合ってる？"])
def test_after_approval_doubt_question_holds(tmp_path, out_dir, text):
    """承認のあとの、否定・言い直しの言葉の入った質問は保留（承認・出力できない。「このままでいい」で解き、ボタンで承認し直す）。"""
    w, fake, sid = approved_move(tmp_path)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"] and st["candidate"] is not None, text


# ---- 13. Safety 再検証（5381f22・3回目）：ボタンの言葉の道・回数の道・止める理由の道でも、承認を残さない ----
@pytest.mark.parametrize("text", ["承認しなきゃよかった", "承認は早かった", "出力しないで", "まだ出力しないで", "出力はまだ", "保存しないで",
                                  "書き出さないで", "OK、でもちょっと", "前に5ミリ", "え？", "は？", "あれ？", "んん？", "ほんと？",
                                  "本当に？", "そうなの"])
def test_after_approval_third_round_move(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]), T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"] and not w.export(sid, candidate_gen=gen)["ok"], (text, st["status_line"])
    assert files(out_dir) == []


@pytest.mark.parametrize("text", ["4回にして", "4回", "5回にして", "4回がいい", "4回でいい", "4回でお願い"])
def test_after_approval_third_round_count(tmp_path, out_dir, text):
    w, fake, sid = pending_three(tmp_path)
    approve(w, sid)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("回数の変更ですね。", [21], intent="change_request", change={"kind": "repeat"}))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"] and not w.export(sid, candidate_gen=gen)["ok"], (text, st["status_line"])
    assert files(out_dir) == []


def test_filming_flow_still_works(tmp_path, out_dir):
    """撮影の流れ：30mm は止める → 5mm の候補 → ボタンで承認 →「はい」→ 出力。"""
    w, fake = T.flow(tmp_path)
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.talk(sid, "Zプラス方向に30ミリ")
    assert r["state"]["candidate"] is None
    r = w.talk(sid, "Zプラス方向に5ミリ")
    assert r["state"]["candidate"] and r["state"]["can_approve"] and not r["state"]["hold"]
    approve(w, sid)
    w.talk(sid, "はい")
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert files(out_dir)


# ---- 14. Safety 再検証（6a38914・4回目）：許す形の一覧（ボタンの頼み・中身を疑わない質問）だけが承認を残す ----
ROUND4 = ["出力不要", "出力は不要", "出力NG", "出力不可", "出力禁止", "出力せず", "出力するな", "出力すんな", "出力せんで", "出力やだ",
          "出力は後で", "出力はあとで", "出力は後回し", "出力は保留", "出力は中断", "出力ナイ", "承認は保留", "承認を無効にして", "承認無効",
          "承認ミス", "誤承認", "承認は誤り", "承認やり直し", "承認し直し", "これ逆？", "方向が逆？", "逆だよね？", "向きは反対？", "マイナス？",
          "上？", "下だよね？", "誤ってる？", "誤り？", "変？", "変だよね？", "本当にいいの？", "これで本当にいいの？", "これでいいの？",
          "危険？", "ぶつかる？", "大丈夫？"]


@pytest.mark.parametrize("text", ROUND4)
def test_after_approval_fourth_round(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]), T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    st = w.state(sid)["state"]
    assert not st["can_export"] and not w.export(sid, candidate_gen=gen)["ok"], (text, st["status_line"])
    assert files(out_dir) == []


@pytest.mark.parametrize("text", ["出力して", "出力してください", "承認して", "では出力お願いします"])
def test_after_approval_plain_button_request_keeps_approval(tmp_path, text):
    w, _, sid = approved_move(tmp_path)
    w.talk(sid, text)
    assert w.state(sid)["state"]["can_export"], text


# ---- 15. Safety 再検証（1d434f3・5回目 CONDITIONAL PASS）の Medium ----
@pytest.mark.parametrize("text", ["何これ？", "なにこれ？", "なんだこれ？", "どういうこと？", "どうなってるの？", "なんで？", "どうして？",
                                  "なんでそっち？", "どうしてそっちに動くの？", "なんで高いの？", "なんでこんなに動くの？", "なぜ3回も？",
                                  "何回もやるの？", "何でこうなるの", "いいです", "大丈夫です"])
def test_after_approval_fifth_round(tmp_path, out_dir, text):
    w, fake, sid = approved_move(tmp_path)
    gen = w.candidate_gen_of(sid)
    fake.add(T.answer("C00005 へ直線で動きます。", [21]), T.answer("C00005 へ直線で動きます。", [21]))
    w.talk(sid, text)
    assert not w.state(sid)["state"]["can_export"] and not w.export(sid, candidate_gen=gen)["ok"], text


# ---- 16. QA 再レビュー M-2：保留の返事は、ボタンで答えられる（［取り消す］［このまま残す］。押すと /api/hear へ決まった文） ----
def test_hold_reply_has_buttons_that_work(tmp_path):
    w, fake, sid = approved_move(tmp_path)
    r = w.talk(sid, "違くない？")
    ch = r["hear"]["choices"]
    assert [c["text"] for c in ch] == ["取り消して", "このままでいい"] and r["state"]["hold"]
    r2 = w.hear(sid, ch[1]["text"])  # ［このまま残す］：保留を解き、承認は消える（もう一度ボタンで承認）
    assert r2["state"]["hold"] is False and r2["state"]["approval"] is None and r2["state"]["candidate"] is not None
    w.talk(sid, "違くない？")
    r3 = w.hear(sid, ch[0]["text"])  # ［取り消す］：無効
    assert r3["state"]["candidate"] is None
