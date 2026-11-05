"""デバッグ専用の起動（tools/debug_review_only.py）：言い添えは claude_lead.enabled で決め、承認・出力の断りは変えない。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from voice_teaching import claude_lead as lead

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("debug_review_only_t", ROOT / "tools" / "debug_review_only.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_debug_launcher_uses_enabled_and_keeps_lock_down(monkeypatch):
    m = load_tool()
    made = {}

    class Srv:
        server_address = ("127.0.0.1", 0)

        def __init__(self):
            from voice_teaching.web.flow import WebFlow
            from voice_teaching.claude_interpreter import MockInterpreter
            self.flow = WebFlow(interpreter=MockInterpreter())

        def serve_forever(self):
            made["lead"] = self.flow.claude_lead
            made["locked"] = getattr(self.flow, "debug_review_only", False)
            made["approve"] = self.flow.approve("x", "唐川", candidate_gen=self.flow.candidate_gen_of("x"))
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(m, "make_server", lambda **kw: Srv())
    monkeypatch.setattr(m.claude_interpreter, "load_dotenv", lambda path: None)
    monkeypatch.setattr(m.claude_explain.Explainer, "from_env",
                        classmethod(lambda cls, model=None: SimpleNamespace(has_key=True, model="m")))
    for env_value, kind, expect in (("", "claude", True), ("0", "claude", False), ("", "mock", False)):
        monkeypatch.setenv(lead.ENV, env_value)
        monkeypatch.setattr(m.claude_interpreter, "from_env", lambda k=kind: SimpleNamespace(kind=k, model="m"))
        m.main(["--port", "0"])
        assert made["lead"] is expect, (env_value, kind)
        assert made["locked"] is True and made["approve"]["ok"] is False and made["approve"].get("debug_review_only")
