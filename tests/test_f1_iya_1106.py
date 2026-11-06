"""F-1（11/6 クローク Safety 最終レビュー High）：区切りのない言い直しで、古い候補が承認・出力できる。実装より先に書いた。

再現（c9861c1 で 62件中 27件が NG）：候補「C00003 Y-10」のあとに「いやYプラス10ミリ」などと言うと、
言い直しとして扱われず、古い候補が同じ世代番号のまま残り、画面（API を直接呼んでも同じ）から承認・出力できた。

直したあとの決まり（唐川さん 11/6 の指示）：
- 言い直し・取消なら、古い候補と承認をサーバー側で無効にする（候補が無くなる）
- 意味がはっきりしないときも、古い候補を承認・出力できない保留にする
- 画面を通さずに API（approve・export）を直接呼んでも、古い候補は出力できない
"""
import tempfile
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim, stale

CP = (Path(__file__).resolve().parent.parent / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()

# クロークのスクリプト（review/kuroku-nl-safety-1105:tools/nl_dialog_check/kuroku/final_61ef19a/iya3.py）で NG になった言い方
NG_BOTH = ["いやYプラス10ミリ", "いや Yプラス10ミリ", "あ、いやYプラス10ミリ", "いやC00003をYプラス10ミリ", "いやX", "いやYプラス",
           "いやプラス10ミリ", "いや上に", "いやワイプラス10ミリ", "いやYのプラス方向に10ミリ", "Yプラス10ミリだった"]
NG_UNAPPROVED_ONLY = ["プラスだった", "マイナスやなくてプラス", "逆だった", "逆やった", "符号が逆"]
CASES = [(p, a) for p in NG_BOTH for a in (True, False)] + [(p, False) for p in NG_UNAPPROVED_ONLY]
assert len(CASES) == 27


# 仕様A（11/6 12:29 唐川さんの決定）：完全な言い直し・新しい指示は、古い候補・承認を失効したうえで、新しい未承認の候補を作る（前の期待：候補なし）
# 対象・軸・向き・量がプログラムで読める言い直し（C00003 で「Yプラス10ミリ」→ C00003 Y +10.000）
REMADE = ["いやYプラス10ミリ", "いや Yプラス10ミリ", "あ、いやYプラス10ミリ", "いやC00003をYプラス10ミリ", "いやワイプラス10ミリ",
          "いやYのプラス方向に10ミリ", "Yプラス10ミリだった"]
NEW_LINE = b"C00003=400.000,-140.000,300.000,"   # 新しい候補（Y -150 → -140）
OLD_LINE = b"C00003=400.000,-160.000,300.000,"   # 古い候補（Y -150 → -160）


def _run(p: str, approved: bool):
    with tempfile.TemporaryDirectory() as d:
        sim.DEFAULT_OUT_DIR = Path(d) / "o"
        answers = [T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(10)]
        w, _ = T.flow(Path(d), *answers)
        sid = w.load(CP)["session_id"]
        w.select(sid, "C00003")
        w.talk(sid, "Yマイナス10mm")
        g0 = w.candidate_gen_of(sid)
        assert (w.state(sid)["state"]["candidate"] or {}).get("delta") == "-10.000"
        if approved:
            assert w.approve(sid, "QA", candidate_gen=g0)["ok"] is True
        reply = w.talk(sid, p)
        st = w.state(sid)["state"]
        g = w.candidate_gen_of(sid)
        # 画面を通さず、API をそのまま呼ぶ（前の世代 → 今の世代の順。承認の前の出力も試す）
        tries = []
        for gen in sorted({x for x in (g0, g) if x is not None}):
            e_pre = w.export(sid, candidate_gen=gen)          # 承認の前の出力（できてはいけない）
            a = w.approve(sid, "QA", candidate_gen=gen)
            e = w.export(sid, candidate_gen=gen)
            body = Path(e["path"]).read_bytes() if e.get("ok") else None
            tries.append((gen, a.get("ok"), e.get("ok"), e_pre.get("ok"), body))
        return st, g0, g, tries, reply


@pytest.mark.parametrize("p,approved", CASES)
def test_old_candidate_cannot_be_approved_or_exported_after_correction(p, approved):
    # 仕様A（11/6 12:29 唐川さんの決定）：完全な言い直し・新しい指示は、古い候補・承認を失効したうえで、新しい未承認の候補を作る（前の期待：候補なし）
    st, g0, g, tries, reply = _run(p, approved)
    old_left = (st.get("candidate") or {}).get("delta") == "-10.000"
    # 古い世代：承認も出力もできない（どの言い方でも）
    for gen, a_ok, e_ok, e_pre_ok, body in tries:
        if gen == g0:
            assert a_ok is False and e_ok is False and e_pre_ok is False, (p, approved, gen)
    assert not any(e_pre_ok for *_, e_pre_ok, _ in tries), (p, approved)  # 承認の前には、どの世代も出力できない
    if p in REMADE:
        assert g != g0, p
        assert reply["hear"]["message"].startswith("言い直しを受け取ったため"), (p, reply["hear"])
        cand = st.get("candidate")
        assert cand is not None and (cand["target"], cand["delta"]) == ("C00003 Y", "+10.000"), (p, cand)
        assert st["approval"] is None and st["can_export"] is False, p  # 新しい候補は未承認
        cur = [t for t in tries if t[0] == g]
        assert len(cur) == 1
        _, a_ok, e_ok, _, body = cur[0]
        if e_ok:  # 新しい候補をボタン（API）で承認したあとの出力：中身は新しい値。古い候補の値は出ない
            assert NEW_LINE in body and OLD_LINE not in body, p
        return
    assert not any(e_ok for _, _, e_ok, _, _ in tries), (p, approved, tries)  # 古い候補も新しい何かも、出力されない
    if old_left:
        assert st["hold"] is True, (p, approved)  # 古い候補が残るなら、必ず保留（承認・出力できない）
        assert st["can_approve"] is False and st["can_export"] is False
        assert not any(a_ok for _, a_ok, _, _, _ in tries), (p, approved, tries)


@pytest.mark.parametrize("p", NG_BOTH[:4] + ["いやワイプラス10ミリ", "Yプラス10ミリだった", "プラスだった", "マイナスやなくてプラス"])
def test_clear_corrections_invalidate_the_candidate_and_bump_generation(p):
    """値のはっきりした言い直しは、候補を無効にする（世代番号が進む）"""
    # 仕様A（11/6 12:29 唐川さんの決定）：完全な言い直し・新しい指示は、古い候補・承認を失効したうえで、新しい未承認の候補を作る（前の期待：候補なし）
    st, g0, g, tries, _ = _run(p, approved=True)
    assert st["approval"] is None
    assert [t[1:4] for t in tries if t[0] == g0] == [(False, False, False)], p  # 古い世代は承認・出力できない
    if p in REMADE:
        assert g != g0, p
        cand = st.get("candidate")
        assert cand is not None and (cand["target"], cand["delta"]) == ("C00003 Y", "+10.000"), (p, cand)
        assert st["can_export"] is False
    else:
        assert st.get("candidate") is None, p


@pytest.mark.parametrize("p", ["いやYプラス10ミリ", "いや Yプラス10ミリ", "いやC00003をYプラス10ミリ", "いやワイプラス10ミリ",
                               "Yプラス10ミリだった", "プラスだった", "マイナスやなくてプラス"])
def test_classify_reads_correction(p):
    assert stale.classify(p).kind == stale.CORRECTION, (p, stale.classify(p))


@pytest.mark.parametrize("p", ["いやX", "いやYプラス", "いや上に", "逆だった", "逆やった", "符号が逆"])
def test_classify_holds_unclear(p):
    """値が足りない・向きだけの言い直しは、無効にせず保留（迷う形は確かめる。K3）"""
    assert stale.classify(p).kind in (stale.UNSURE, stale.CORRECTION), (p, stale.classify(p))


@pytest.mark.parametrize("p", ["いやいやYプラス10ミリ", "いや、Yプラス10ミリ", "違うYプラス10ミリ", "ごめんYプラス10ミリ",
                               "じゃなくてYプラス10ミリ", "マイナスじゃないプラス"])
def test_forms_that_already_worked_still_invalidate(p):
    assert stale.classify(p).kind == stale.CORRECTION, p


def test_form_that_already_held_still_holds():
    """「Yプラス10ミリの間違い」は直す前から保留（UNSURE）。変えない"""
    assert stale.classify("Yプラス10ミリの間違い").kind == stale.UNSURE


@pytest.mark.parametrize("p", ["はい", "ありがとう", "何をしているの？", "出力して", "このままでいい", "いいやん"])
def test_unrelated_utterances_are_not_corrections(p):
    """言い直しでない文は、言い直しにしない（承認を残す文・保留を解く文の見分けを変えない）"""
    assert stale.classify(p).kind != stale.CORRECTION, (p, stale.classify(p))


def test_filming_scenario_still_works():
    """撮影の流れ：30mm は上限で止まり候補なし → 5mm で候補（Z 200.000 → 205.000）→ 承認 → シミュレーション用の出力"""
    with tempfile.TemporaryDirectory() as d:
        sim.DEFAULT_OUT_DIR = Path(d) / "o"
        w, _ = T.flow(Path(d))
        sid = w.load(CP)["session_id"]
        w.select(sid, "C00005")
        w.talk(sid, "Zプラス方向に30ミリ")
        assert w.state(sid)["state"]["candidate"] is None
        w.talk(sid, "Zプラス方向に5ミリ")
        cand = w.state(sid)["state"]["candidate"]
        assert cand["before_line"].startswith("C00005=400.000,150.000,200.000,")
        assert cand["after_line"].startswith("C00005=400.000,150.000,205.000,")
        g = w.candidate_gen_of(sid)
        assert w.approve(sid, "QA", candidate_gen=g)["ok"] is True
        assert w.export(sid, candidate_gen=g)["ok"] is True
