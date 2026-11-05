"""デバッグ専用の起動（tools/debug_review_only.py）：承認・出力をサーバー側で断る。本物の Claude は呼ばない。"""
import base64
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import debug_review_only as dro  # noqa: E402
from voice_teaching import sim  # noqa: E402
from voice_teaching.claude_interpreter import MockInterpreter  # noqa: E402
from voice_teaching.web.flow import WebFlow  # noqa: E402
from voice_teaching.web.server import make_server  # noqa: E402

DEMO_C = (REPO / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def _candidate(flow):
    sid = flow.load(DEMO_C)["session_id"]
    flow.select(sid, "C00003")
    r = flow.talk(sid, "Zプラス方向に5mm")
    assert r["state"]["candidate"], r
    return sid


def test_control_unlocked_flow_can_approve(tmp_path, monkeypatch):
    """対照：差し替えない flow では、同じ候補を承認できる（テストが意味を持つことの確かめ）。"""
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    w = WebFlow(interpreter=MockInterpreter(), updown_config=None)
    sid = _candidate(w)
    assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]


def test_python_calls_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    w = dro.lock_down(WebFlow(interpreter=MockInterpreter(), updown_config=None))
    sid = _candidate(w)
    st = w.state(sid)["state"]
    assert st["candidate"] and st["can_approve"] is False and st["can_export"] is False and st["debug_review_only"]
    assert not w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert w.state(sid)["state"]["approval"] is None
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def _post(port, path, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", path, body=json.dumps(body).encode("utf-8"),
              headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}",
                       "Origin": f"http://127.0.0.1:{port}"})
    r = c.getresponse()
    out = json.loads(r.read().decode("utf-8"))
    c.close()
    return out


@pytest.fixture
def locked_server(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    srv = make_server(port=0, interpreter=MockInterpreter(), updown_config=None)
    dro.lock_down(srv.flow)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, tmp_path / "out"
    srv.shutdown()
    srv.server_close()


def test_http_approve_and_export_are_refused(locked_server):
    srv, out = locked_server
    port = srv.server_address[1]
    sid = _post(port, "/api/sample", {})["session_id"]
    _post(port, "/api/select", {"session_id": sid, "position": "C00003"})
    r = _post(port, "/api/talk", {"session_id": sid, "text": "Zプラス方向に5mm", "source": "text"})
    assert r["state"]["candidate"] and r["state"]["can_approve"] is False
    a = _post(port, "/api/approve", {"session_id": sid, "approver": "QA"})
    assert a["ok"] is False and a.get("debug_review_only")
    x = _post(port, "/api/export", {"session_id": sid})
    assert x["ok"] is False and x.get("debug_review_only")
    st = _post(port, "/api/state", {"session_id": sid})["state"]
    assert st["approval"] is None and st["can_export"] is False and st["exported"] is None
    assert not out.exists() or not any(out.iterdir())


def test_repeat_candidate_cannot_be_approved(tmp_path, monkeypatch):
    """回数の変更の候補（範囲のボタンで作る）も承認できない。"""
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    import test_claude_talk as T
    w, _ = T.flow(tmp_path)
    dro.lock_down(w)
    sid = w.load(T.demo_bytes())["session_id"]
    out = T.repeat_to_three(w, sid)
    assert out["state"]["candidate"] and out["state"]["can_approve"] is False
    assert not w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]


def test_words_still_do_not_approve(locked_server):
    srv, _ = locked_server
    port = srv.server_address[1]
    sid = _post(port, "/api/sample", {})["session_id"]
    r = _post(port, "/api/talk", {"session_id": sid, "text": "承認して", "source": "text"})
    assert r["state"]["approval"] is None
