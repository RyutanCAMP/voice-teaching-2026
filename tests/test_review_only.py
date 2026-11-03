"""受入テスト：実機の JOB（合成ジョブでない）は、承認しても出力できない。画面で「変更案の確認のみ」と明示する
（唐川さんの指示 2026-11-03）。安全の制限（出力は合成ジョブだけ）は今のまま。"""
from pathlib import Path

from voice_teaching import claude_interpreter as ci
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
NOT_SYNTHETIC = DEMO_C.replace(b"//NAME DEMO_C_PLACE", b"//NAME DEMO_C_PLACX", 1)  # 1文字違う＝合成ジョブではない
NOTICE = "変更案の確認のみ。実機用JOBは出力できません"


def approved_state(data, tmp_path, monkeypatch):
    from voice_teaching import sim
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path)
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(data)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "Zマイナス3ミリ")
    assert r["state"]["candidate"] is not None and r["state"]["can_approve"]
    r = w.approve(sid, "テスト")
    return w, sid, r["state"]


def test_synthetic_job_is_exportable(tmp_path, monkeypatch):
    w, sid, st = approved_state(DEMO_C, tmp_path, monkeypatch)
    assert st["exportable"] is True and st["can_export"] is True


def test_real_job_can_be_reviewed_but_not_exported(tmp_path, monkeypatch):
    assert NOT_SYNTHETIC != DEMO_C
    w, sid, st = approved_state(NOT_SYNTHETIC, tmp_path, monkeypatch)
    assert st["exportable"] is False and st["can_export"] is False
    r = w.export(sid)
    assert r.get("ok") is not True and not list(Path(tmp_path).iterdir())  # 出力は今どおり断る


def test_state_before_candidate_also_says_not_exportable():
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(NOT_SYNTHETIC)["session_id"]
    assert w.state(sid)["state"]["exportable"] is False


def test_notice_text_is_in_the_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count(NOTICE) == 2  # 確認表示と、承認のあとの出力の欄
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '"confirm-review-only"' in js and '"export-review-only"' in js
