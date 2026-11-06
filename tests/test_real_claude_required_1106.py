"""起動のときに本物の Claude を確かめる（11/6 唐川さんの決定）。実装より先に書いた。

- filming / release：本物の Claude が使えない設定（解釈の係がモック、または説明の係にキーが無い）なら起動を止める
  （終了コードは 0 以外・理由の文。キーの値は出さない）。.env を読めないときも止める
- dev：モックは --mock を付けたときだけ。付けずにキーが無ければ dev でも止め、「--mock を付けてください」と出す
- --mock と filming / release を一緒に指定したら止める
- tools/demo_mock.py はモック専用（明示した扱い）。filming / release と一緒なら止める
- tools/debug_review_only.py も python -m voice_teaching.web と同じ決まり
- 本物の API は呼ばない（偽のキー・make_server を偽物に差し替える）
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_teaching import claude_explain, claude_interpreter
from voice_teaching.web import __main__ as web_main
from voice_teaching.web import server

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-test-dummy-REALGUARD-0123456789"
MOCK_BANNER = "モックで起動しています（自動テスト・試し用。撮影には使いません）"


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(f"{name}_realguard", ROOT / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def no_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "VT_CHANNEL", "VOICE_TEACHING_CLAUDE_LEAD"):
        monkeypatch.delenv(k, raising=False)


class FakeSrv:
    server_address = ("127.0.0.1", 0)

    def __init__(self, kw):
        self.kw = kw
        self.flow = SimpleNamespace(claude_lead=None, _state=lambda sid, e: {})  # debug の lock_down が _state を包む
        self.build = None

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


def _fake(monkeypatch, mod):
    made = []
    monkeypatch.setattr(mod, "make_server", lambda **kw: made.append(kw) or FakeSrv(kw))
    return made


def _launchers():
    return [("web", web_main), ("debug", _load_tool("debug_review_only"))]


def _run(mod, argv, capsys):
    try:
        code = mod.main(argv)
    except SystemExit as e:  # argparse の ap.error など
        code = e.code
    out = capsys.readouterr()
    return code, out.out + out.err


# ---- キーが無い --------------------------------------------------------------

@pytest.mark.parametrize("name", ["web", "debug"])
def test_no_key_dev_without_mock_stops(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    code, text = _run(mod, ["--port", "0", "--env-file", str(tmp_path / "none.env")], capsys)
    assert code not in (0, None) and made == []
    assert "本物の Claude のキーがありません" in text and "--mock" in text


@pytest.mark.parametrize("name", ["web", "debug"])
def test_no_key_dev_with_mock_starts_as_mock(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    code, text = _run(mod, ["--port", "0", "--mock", "--env-file", str(tmp_path / "none.env")], capsys)
    assert code == 0 and len(made) == 1
    assert made[0]["interpreter"].kind == "mock" and made[0]["explainer"].has_key is False
    assert MOCK_BANNER in text


@pytest.mark.parametrize("name", ["web", "debug"])
@pytest.mark.parametrize("channel", ["filming", "release"])
@pytest.mark.parametrize("by_env", [False, True])
def test_no_key_strict_channels_stop(name, channel, by_env, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    argv = ["--port", "0", "--env-file", str(tmp_path / "none.env")]
    if by_env:
        monkeypatch.setenv("VT_CHANNEL", channel)
    else:
        argv += ["--channel", channel]
    code, text = _run(mod, argv, capsys)
    assert code not in (0, None) and made == []
    assert channel in text and "本物の Claude" in text and "起動を止めます" in text


@pytest.mark.parametrize("name", ["web", "debug"])
@pytest.mark.parametrize("channel", ["filming", "release"])
@pytest.mark.parametrize("with_key", [False, True])
def test_mock_with_strict_channel_stops(name, channel, with_key, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    if with_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    code, text = _run(mod, ["--port", "0", "--mock", "--channel", channel,
                            "--env-file", str(tmp_path / "none.env")], capsys)
    assert code not in (0, None) and made == []
    assert "モック" in text and channel in text and KEY not in text


@pytest.mark.parametrize("name", ["web", "debug"])
def test_mock_with_vt_channel_filming_stops(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    monkeypatch.setenv("VT_CHANNEL", "filming")
    code, _ = _run(mod, ["--port", "0", "--mock", "--env-file", str(tmp_path / "none.env")], capsys)
    assert code not in (0, None) and made == []


# ---- 偽のキーがあるとき --------------------------------------------------------

@pytest.mark.parametrize("name", ["web", "debug"])
@pytest.mark.parametrize("channel", ["dev", "filming"])
def test_fake_key_starts_real(name, channel, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    code, text = _run(mod, ["--port", "0", "--channel", channel, "--env-file", str(tmp_path / "none.env")], capsys)
    assert code == 0 and len(made) == 1
    assert made[0]["interpreter"].kind == "claude" and made[0]["explainer"].has_key is True
    assert "解釈：本物の Claude（" in text and MOCK_BANNER not in text
    assert KEY not in text


@pytest.mark.parametrize("name", ["web", "debug"])
def test_fake_key_from_dotenv_starts_real(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    envf = tmp_path / "ok.env"
    envf.write_text(f"ANTHROPIC_API_KEY={KEY}\n", encoding="utf-8")
    code, text = _run(mod, ["--port", "0", "--channel", "filming", "--env-file", str(envf)], capsys)
    assert code == 0 and len(made) == 1 and made[0]["interpreter"].kind == "claude"
    assert KEY not in text


@pytest.mark.parametrize("name", ["web", "debug"])
def test_mock_flag_with_key_in_dev_uses_mock_and_no_key(name, no_env, monkeypatch, capsys, tmp_path):
    # --mock は「モックで起動」。キーが環境にあっても本物の Claude は使わない（解釈の係も説明の係も）
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    code, text = _run(mod, ["--port", "0", "--mock", "--env-file", str(tmp_path / "none.env")], capsys)
    assert code == 0 and len(made) == 1
    assert made[0]["interpreter"].kind == "mock" and made[0]["explainer"].has_key is False
    assert MOCK_BANNER in text and KEY not in text


@pytest.mark.parametrize("name", ["web", "debug"])
@pytest.mark.parametrize("channel", ["dev", "filming"])
@pytest.mark.parametrize("interp_kind,explainer_key", [("claude", False), ("mock", True), ("mock", False)])
def test_one_side_missing_stops(name, channel, interp_kind, explainer_key, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    monkeypatch.setattr(mod.claude_interpreter, "from_env",
                        lambda *a, **k: SimpleNamespace(kind=interp_kind, model="claude-x"))
    monkeypatch.setattr(mod.claude_explain.Explainer, "from_env",
                        classmethod(lambda cls, *a, **k: SimpleNamespace(has_key=explainer_key, model="claude-x")))
    code, text = _run(mod, ["--port", "0", "--channel", channel, "--env-file", str(tmp_path / "none.env")], capsys)
    assert code not in (0, None) and made == []
    assert "本物の Claude" in text


# ---- .env が読めない -----------------------------------------------------------

@pytest.mark.parametrize("name", ["web", "debug"])
def test_dotenv_missing_filming_without_key_stops(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    code, _ = _run(mod, ["--port", "0", "--channel", "filming", "--env-file", str(tmp_path / "nope" / ".env")], capsys)
    assert code not in (0, None) and made == []


@pytest.mark.parametrize("name", ["web", "debug"])
@pytest.mark.parametrize("env_key", [False, True])
def test_dotenv_unreadable_filming_stops(name, env_key, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    envf = tmp_path / "sjis.env"
    envf.write_bytes("# 日本語のメモ\nANTHROPIC_API_KEY=x\n".encode("shift_jis"))
    if env_key:  # 環境変数にキーがあっても、filming で .env を読めないままは起動しない
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    code, text = _run(mod, ["--port", "0", "--channel", "filming", "--env-file", str(envf)], capsys)
    assert code not in (0, None) and made == []
    assert ".env" in text and KEY not in text


@pytest.mark.parametrize("name", ["web", "debug"])
def test_dotenv_directory_filming_stops(name, no_env, monkeypatch, capsys, tmp_path):
    # パスがフォルダ（開けない）
    mod = dict(_launchers())[name]
    made = _fake(monkeypatch, mod)
    code, _ = _run(mod, ["--port", "0", "--channel", "filming", "--env-file", str(tmp_path)], capsys)
    assert code not in (0, None) and made == []


# ---- tools/demo_mock.py --------------------------------------------------------

def test_demo_mock_dev_starts_as_mock(no_env, monkeypatch, capsys):
    mod = _load_tool("demo_mock")
    made = _fake(monkeypatch, mod)
    code, text = _run(mod, ["--port", "0"], capsys)
    assert code == 0 and len(made) == 1 and made[0]["interpreter"].kind == "mock"
    assert MOCK_BANNER in text


@pytest.mark.parametrize("channel", ["filming", "release"])
@pytest.mark.parametrize("by_env", [False, True])
def test_demo_mock_strict_channels_stop(channel, by_env, no_env, monkeypatch, capsys):
    mod = _load_tool("demo_mock")
    made = _fake(monkeypatch, mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    argv = ["--port", "0"]
    if by_env:
        monkeypatch.setenv("VT_CHANNEL", channel)
    else:
        argv += ["--channel", channel]
    code, text = _run(mod, argv, capsys)
    assert code not in (0, None) and made == []
    assert "モック" in text and channel in text and KEY not in text


# ---- キーの値を出さない ---------------------------------------------------------

@pytest.mark.parametrize("name", ["web", "debug"])
def test_key_not_in_channel_error(name, no_env, monkeypatch, capsys, tmp_path):
    mod = dict(_launchers())[name]
    _fake(monkeypatch, mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("VT_CHANNEL", "bogus")
    code, text = _run(mod, ["--port", "0", "--env-file", str(tmp_path / "none.env")], capsys)
    assert code not in (0, None) and KEY not in text


def test_key_not_in_launch_guard_messages():
    from voice_teaching import launch_guard as g
    texts = [g.refuse_mock(c) for c in ("dev", "filming", "release", "bogus")]
    for c in ("dev", "filming", "release", "bogus"):
        for st in (None, claude_interpreter.DOTENV_OK, claude_interpreter.DOTENV_MISSING,
                   claude_interpreter.DOTENV_UNREADABLE):
            texts.append(g.refuse_real(c, claude_interpreter.MockInterpreter(), claude_explain.Explainer(KEY), st))
            texts.append(g.refuse_real(c, claude_interpreter.ClaudeInterpreter(KEY, "m"), claude_explain.Explainer(""), st))
    assert all(KEY not in (t or "") for t in texts)


def test_key_not_in_api_answers(no_env, monkeypatch):
    # 本物の Claude の設定（偽のキー）で立てたサーバー：/api の答え・画面にキーを出さない。Claude は呼ばない
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    interp = claude_interpreter.from_env()
    explainer = claude_explain.Explainer.from_env()
    assert interp.kind == "claude" and explainer.has_key
    srv = server.make_server(port=0, interpreter=interp, explainer=explainer)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        def post(path, obj):
            req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return r.read().decode("utf-8")

        bodies = []
        with urllib.request.urlopen(base + "/") as r:
            bodies.append(r.read().decode("utf-8"))
        with urllib.request.urlopen(base + "/api/version") as r:
            bodies.append(r.read().decode("utf-8"))
        loaded = post("/api/sample", {})
        bodies.append(loaded)
        sid = json.loads(loaded)["session_id"]
        bodies.append(post("/api/state", {"session_id": sid}))
        bodies.append(post("/api/ask", {"session_id": sid, "info": True}))
        assert all(KEY not in b for b in bodies)
        assert json.loads(bodies[-1])["has_key"] is True
    finally:
        srv.shutdown()
        srv.server_close()
