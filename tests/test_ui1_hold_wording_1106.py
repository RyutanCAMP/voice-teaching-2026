"""UI-1（11/6 クローク Safety レビュー Medium・Issue #8 の5）：承認のあとに保留になったときの、サーバーの決まった文。実装より先に書いた。

再現（a044212）：「Zプラス方向に5ミリ」→ 承認 →「ちょっと待って」で、サーバーは approval あり・hold true・can_export false なのに、
- 返事が「承認済みです。出力は画面の［シミュレーション用に出力］のボタンで行います…」（出力できないのに出力を勧める）
- 状態の1行（アプリの記録）が「…／承認 済み（ボタンで承認）／…」
直す範囲は保留のときの文の言い方だけ。保留・承認・出力の判定（hold・approval・can_approve・can_export）は変えない。
"""
import tempfile
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import sim
from voice_teaching.web import flow

CP = (Path(__file__).resolve().parent.parent / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()
OUTPUT_ADVICE = ("承認済みです", "［シミュレーション用に出力］のボタンで行います", "出力してください")


def _msg(out: dict) -> str:
    """talk の返事の文（指示の道の答え）"""
    return ((out.get("hear") or {}).get("message")) or ""


def _approved(d: Path):
    sim.DEFAULT_OUT_DIR = d / "o"
    answers = [T.answer("この位置へ動く動作です。", (21, 22, 23, 24, 25, 26)) for _ in range(10)]
    w, _ = T.flow(d, *answers)
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Zプラス方向に5ミリ")
    g = w.candidate_gen_of(sid)
    assert w.approve(sid, "QA", candidate_gen=g)["ok"] is True
    st = w.state(sid)["state"]
    assert st["approval"] and st["can_export"] is True and st["hold"] is False
    assert "承認 済み（ボタンで承認）" in st["status_line"]
    return w, sid, g


@pytest.mark.parametrize("said", ["ちょっと待って", "違くない？"])
def test_hold_after_approval_does_not_advise_export(said):
    with tempfile.TemporaryDirectory() as d:
        w, sid, g = _approved(Path(d))
        out = w.talk(sid, said)
        st = w.state(sid)["state"]
        # 判定は今までどおり（保留・承認の記録は残る・承認も出力もできない）
        assert st["hold"] is True
        assert st["approval"] is not None
        assert st["can_export"] is False and st["can_approve"] is False
        msgs = [_msg(out)] + [t.get("text", "") for t in ((out.get("ask") or {}).get("turns") or [])]
        for m in msgs:
            for bad in OUTPUT_ADVICE:
                assert bad not in m, (said, m)
        assert "承認・出力" in _msg(out), _msg(out)
        # 状態の1行：承認を「済み」と言わない（保留中）
        assert "承認 済み" not in st["status_line"], st["status_line"]
        assert "承認 保留中" in st["status_line"], st["status_line"]
        # サーバーも出力を断る（前と同じ）
        assert w.export(sid, candidate_gen=g)["ok"] is not True


def test_chotto_hold_reply_is_the_fixed_hold_sentence():
    with tempfile.TemporaryDirectory() as d:
        w, sid, _ = _approved(Path(d))
        out = w.talk(sid, "ちょっと待って")
        assert _msg(out) == flow.M_HOLD


def test_plain_ack_after_approval_still_says_how_to_export():
    """保留でない承認済み（「わかった」）の返事は今のまま（出力はボタンで、の案内）"""
    with tempfile.TemporaryDirectory() as d:
        w, sid, _ = _approved(Path(d))
        out = w.talk(sid, "わかった")
        st = w.state(sid)["state"]
        assert st["hold"] is False and st["can_export"] is True
        assert _msg(out) == flow.STEP_F_APPROVED_NEXT
        assert "承認 済み（ボタンで承認）" in st["status_line"]


def test_keep_after_hold_needs_new_approval_then_status_is_done_again():
    with tempfile.TemporaryDirectory() as d:
        w, sid, _ = _approved(Path(d))
        w.talk(sid, "ちょっと待って")
        w.talk(sid, "このままでいい")
        st = w.state(sid)["state"]
        assert st["hold"] is False and st["approval"] is None  # 残すと、承認はもう一度（今の決まり）
        assert "承認 まだ" in st["status_line"]
        assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"] is True
        st = w.state(sid)["state"]
        assert st["can_export"] is True and "承認 済み（ボタンで承認）" in st["status_line"]
