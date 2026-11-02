"""本物の Claude API を、キーを入れた最初の1回から使えるようにするための約束のテスト（実 API は呼ばない）。

- 送る形の約束（claude-opus-5-5）：tool_choice は auto だけ（any / tool は 400）。thinking・temperature・top_p・top_k は
  送らない（400）。effort は low を明示。考える分も max_tokens に入るので、余裕のある値にする。
- 応答の確かめ：thinking の段があっても、tool_use がちょうど1件のときだけ使う。それ以外は候補を作らない。
- SDK のエラーは、短い日本語の案内に分ける。キーや生のエラー文は出さない。モックへ勝手に切り替えない。
- 本物の SDK を使うテストは、偽の通信路（httpx2.MockTransport）を使う。ネットワークには出ない。
"""
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_check
from voice_teaching import claude_interpreter as ci
from voice_teaching import utterance as u
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO = (ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
KEY = "sk-ant-TEST-SECRET-0123456789"
NAMES = ["C00000", "C00001", "C00004"]
UP10 = "この位置を上に10mm移動して"


class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc is not None:
            raise self.exc
        return self.response


def client(response=None, exc=None):
    return NS(messages=FakeMessages(response, exc))


def tool_use(name, inp):
    return NS(type="tool_use", name=name, input=inp, id="toolu_1")


def thinking():
    return NS(type="thinking", thinking="", signature="sig")


def resp(*blocks, stop="tool_use"):
    return NS(stop_reason=stop, content=list(blocks))


def prop(position="C00004", direction="上", amount=10):
    return {"position": position, "direction": direction, "amount_mm": amount}


def decide(response, text=UP10, selected="C00004", names=NAMES):
    c = client(response)
    return ci.ClaudeInterpreter(KEY, client=c).decide(u.read_utterance(text), selected, names), c.messages.calls


# ======================================================================
# 1. 送る形の約束
# ======================================================================
def test_request_contract_for_opus_5_5():
    calls, sent = decide(resp(thinking(), tool_use("propose_position", prop())))
    assert len(sent) == 1
    kw = sent[0]
    assert set(kw) == {"model", "max_tokens", "output_config", "tools", "tool_choice", "system", "messages"}
    assert kw["model"] == "claude-opus-5-5"
    assert 8000 <= kw["max_tokens"] <= 16000  # 考える分も max_tokens に入る。2048 では足りないことがある
    assert kw["output_config"] == {"effort": "low"}
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    for banned in ("thinking", "temperature", "top_p", "top_k", "stream", "fallbacks", "metadata"):
        assert banned not in kw
    assert [t["name"] for t in kw["tools"]] == ["propose_position", "ask_user"]
    for t in kw["tools"]:
        assert t["strict"] is True
        sch = t["input_schema"]
        assert sch["type"] == "object" and sch["additionalProperties"] is False
        assert set(sch["required"]) == set(sch["properties"])
    assert kw["tools"][0]["input_schema"]["properties"]["position"]["enum"] == NAMES
    assert calls[0]["tool"] == "propose_change"


def test_user_content_is_only_utterance_selected_and_position_names():
    _, sent = decide(resp(tool_use("propose_position", prop())))
    kw = sent[0]
    assert len(kw["messages"]) == 1 and kw["messages"][0]["role"] == "user"
    body = json.loads(kw["messages"][0]["content"])
    assert body == {"utterance": UP10, "selected_position": "C00004", "available_positions": NAMES}
    whole = json.dumps(kw, ensure_ascii=False)
    assert KEY not in whole and "sk-ant" not in whole
    for job_text in ("C00004=", "450.000", "190.000", "120.000", "MOVL", "//NAME", "/JOB", "NPOS", "DEMO_PLACE"):
        assert job_text not in whole


def test_flow_sends_no_job_text_or_coordinates():
    c = client(resp(tool_use("propose_position", prop())))
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(DEMO)["session_id"]
    w.select(sid, "C00004")
    assert w.hear(sid, UP10)["status"] == "ok"
    whole = json.dumps(c.messages.calls, ensure_ascii=False)
    for job_text in ("C00004=", "450.000", "190.000", "MOVL", "//NAME", "/JOB", KEY):
        assert job_text not in whole
    assert json.loads(c.messages.calls[0]["messages"][0]["content"])["available_positions"] == [
        "C00000", "C00001", "C00002", "C00003", "C00004", "C00005"]


def test_build_request_is_the_same_as_what_decide_sends():
    _, sent = decide(resp(tool_use("propose_position", prop())))
    assert ci.build_request("claude-opus-5-5", UP10, "C00004", NAMES) == sent[0]


# 本物の SDK に通しても、同じ形で送られる（偽の通信路で受け取る。ネットワークには出ない）
def _sdk_client(handler, retries=0):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    return anthropic.Anthropic(api_key=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
                               max_retries=retries, timeout=30.0)


def _ok_body(content, stop="tool_use"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": content}


def test_real_sdk_serializes_the_same_body_and_parses_thinking_then_tool_use():
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=_ok_body([
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "toolu_1", "name": "propose_position", "input": prop()}]))
    it = ci.ClaudeInterpreter(KEY, client=_sdk_client(handler))
    calls = it.decide(u.read_utterance(UP10), "C00004", NAMES)
    assert calls == [{"tool": "propose_change",
                      "args": {"kind": "shift", "position": "C00004", "axis": "Z", "delta_mm": 10.0}}]
    assert seen[0] == ci.build_request("claude-opus-5-5", UP10, "C00004", NAMES)


# ======================================================================
# 2. 応答の確かめ
# ======================================================================
def test_thinking_and_text_blocks_are_ignored_when_exactly_one_tool_use():
    calls, _ = decide(resp(thinking(), NS(type="text", text="15mm にします"),
                           tool_use("propose_position", prop(amount=10))))
    assert calls[0]["args"]["delta_mm"] == 10.0


BAD = {
    "no tool_use": resp(thinking()),
    "two tool_use": resp(tool_use("propose_position", prop()), tool_use("propose_position", prop())),
    "propose and ask": resp(tool_use("propose_position", prop()), tool_use("ask_user", {"question": "?"})),
    "unknown tool": resp(tool_use("write_job", {"text": "C00004=..."})),
    "extra key": resp(tool_use("propose_position", {**prop(), "after_line": "C00004=1,2,3"})),
    "missing key": resp(tool_use("propose_position", {"position": "C00004", "direction": "上"})),
    "position int": resp(tool_use("propose_position", prop(position=4))),
    "position not in list": resp(tool_use("propose_position", prop(position="C00009"))),
    "position bad form": resp(tool_use("propose_position", prop(position="P00004"))),
    "direction unknown": resp(tool_use("propose_position", prop(direction="手前"))),
    "direction list": resp(tool_use("propose_position", prop(direction=["上"]))),
    "amount str": resp(tool_use("propose_position", prop(amount="10"))),
    "amount nan": resp(tool_use("propose_position", prop(amount=math.nan))),
    "amount inf": resp(tool_use("propose_position", prop(amount=math.inf))),
    "amount bool": resp(tool_use("propose_position", prop(amount=True))),
    "amount negative": resp(tool_use("propose_position", prop(amount=-10))),
    "amount zero": resp(tool_use("propose_position", prop(amount=0))),
    "amount none": resp(tool_use("propose_position", prop(amount=None))),
    "input not dict": resp(tool_use("propose_position", ["C00004", "上", 10])),
    "ask extra key": resp(tool_use("ask_user", {"question": "?", "amount_mm": 15})),
    "text only end_turn": resp(NS(type="text", text="C00004 を 10mm 上げます"), stop="end_turn"),
    "max_tokens with tool_use": resp(thinking(), tool_use("propose_position", prop()), stop="max_tokens"),
    "max_tokens thinking only": resp(thinking(), stop="max_tokens"),
    "pause_turn": resp(tool_use("propose_position", prop()), stop="pause_turn"),
    "stop_sequence": resp(tool_use("propose_position", prop()), stop="stop_sequence"),
    "no stop_reason": resp(tool_use("propose_position", prop()), stop=None),
    "content none": NS(stop_reason="tool_use", content=None),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_bad_responses_give_no_candidate_with_fixed_message(name):
    with pytest.raises(ci.InterpreterError) as e:
        decide(BAD[name])
    assert str(e.value) == ci.FAILED
    assert KEY not in str(e.value)


@pytest.mark.parametrize("blocks", [(), (tool_use("propose_position", prop()),), (thinking(),)])
def test_refusal_gives_no_candidate_even_with_tool_use(blocks):
    with pytest.raises(ci.InterpreterError) as e:
        decide(resp(*blocks, stop="refusal"))
    assert str(e.value) == ci.REFUSED


@pytest.mark.parametrize("name", ["max_tokens with tool_use", "pause_turn", "text only end_turn", "unknown tool"])
def test_bad_responses_in_flow_stop_without_candidate(name):
    c = client(BAD[name])
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(DEMO)["session_id"]
    w.select(sid, "C00004")
    r = w.hear(sid, UP10)
    assert r["status"] == "stop" and r["message"] == ci.FAILED
    assert r["state"]["candidate"] is None and r["tool_calls"] == []


@pytest.mark.parametrize("text,claude", [
    ("Yプラスに3mm", prop("C00005", "Z+", 3)),      # 発話は Y、Claude は Z
    ("Yプラスに3mm", prop("C00005", "Y-", 3)),      # 向きが逆
    ("Yプラスに3mm", prop("C00005", "Y+", 5)),      # 量が違う
    ("C00005をYプラスに3mm", prop("C00003", "Y+", 3)),  # 位置が違う
    ("この位置を上に2mm", prop("C00005", "下", 2)),   # 上げ下げが逆
])
def test_real_path_proposals_still_checked_against_the_utterance(text, claude):
    c = client(resp(thinking(), tool_use("propose_position", claude)))
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text, source="voice")
    assert len(c.messages.calls) == 1
    assert r["status"] == "ask" and r["state"]["candidate"] is None


# ======================================================================
# 3. エラーの案内（キーと生のエラーは出さない・モックへ切り替えない）
# ======================================================================
def _status_error(status, etype, retries=0):
    httpx2 = pytest.importorskip("httpx2")

    def handler(req):
        return httpx2.Response(status, json={"type": "error", "error": {"type": etype, "message": f"raw {etype} {KEY}"}},
                               headers={"retry-after-ms": "0", "request-id": "req_raw_123"})
    return _sdk_client(handler, retries)


def _conn_error(exc_name):
    httpx2 = pytest.importorskip("httpx2")

    def handler(req):
        raise getattr(httpx2, exc_name)(f"raw boom {KEY}")
    return _sdk_client(handler)


@pytest.mark.parametrize("make,expected", [
    (lambda: _status_error(401, "authentication_error"), "AUTH"),
    (lambda: _status_error(403, "permission_error"), "AUTH"),
    (lambda: _status_error(404, "not_found_error"), "MODEL_NOT_FOUND"),
    (lambda: _status_error(429, "rate_limit_error"), "BUSY"),
    (lambda: _status_error(529, "overloaded_error"), "BUSY"),
    (lambda: _status_error(500, "api_error"), "BUSY"),
    (lambda: _status_error(503, "api_error"), "BUSY"),
    (lambda: _status_error(400, "invalid_request_error"), "REJECTED"),
    (lambda: _status_error(413, "request_too_large"), "REJECTED"),
    (lambda: _conn_error("ConnectError"), "NETWORK"),
    (lambda: _conn_error("ReadTimeout"), "NETWORK"),
])
def test_real_sdk_errors_map_to_short_distinct_guidance(make, expected):
    it = ci.ClaudeInterpreter(KEY, client=make())
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(u.read_utterance(UP10), "C00004", NAMES)
    msg = str(e.value)
    assert msg == getattr(ci, expected)
    for raw in (KEY, "sk-ant", "raw", "Error code", "req_raw_123", "_error", "boom"):
        assert raw not in msg
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_error_messages_are_distinct_and_say_what_to_do():
    msgs = [ci.AUTH, ci.MODEL_NOT_FOUND, ci.BUSY, ci.NETWORK, ci.REJECTED, ci.FAILED, ci.REFUSED]
    assert len(set(msgs)) == len(msgs)
    assert "APIキーを確認してください" in ci.AUTH
    assert "モデル" in ci.MODEL_NOT_FOUND and "ANTHROPIC_MODEL" in ci.MODEL_NOT_FOUND
    assert "混み合っています。少し待って" in ci.BUSY
    assert "ネットワークにつながりません" in ci.NETWORK
    for m in msgs:
        assert "変更候補は作りません" in m
        for word in ("安全です", "当たります", "干渉します"):
            assert word not in m


def test_errors_without_sdk_classes_map_by_class_name_and_status():
    # SDK を差し替えたときも、クラスの名前と status_code で分ける。ふつうの例外は「その他」
    class AuthenticationError(Exception):
        status_code = 401
    class RateLimitError(Exception):
        status_code = 429
    class APITimeoutError(Exception):
        pass
    assert ci.error_message(AuthenticationError("x")) == ci.AUTH
    assert ci.error_message(RateLimitError("x")) == ci.BUSY
    assert ci.error_message(APITimeoutError("x")) == ci.NETWORK
    assert ci.error_message(RuntimeError("401 invalid x-api-key")) == ci.FAILED
    assert ci.error_message(ValueError("Streaming is required")) == ci.FAILED


@pytest.mark.parametrize("expected,make", [
    ("AUTH", lambda: _status_error(401, "authentication_error")),
    ("BUSY", lambda: _status_error(529, "overloaded_error")),
    ("NETWORK", lambda: _conn_error("ConnectError")),
])
def test_flow_shows_guidance_never_falls_back_to_mock(expected, make):
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=make()))
    sid = w.load(DEMO)["session_id"]
    w.select(sid, "C00004")
    r = w.hear(sid, UP10, source="voice")
    assert r["status"] == "stop" and r["message"] == getattr(ci, expected)
    assert r["tool_calls"] == [] and r["state"]["candidate"] is None
    assert r["state"]["interpreter"]["kind"] == "claude"
    text = json.dumps(r, ensure_ascii=False)
    assert KEY not in text and "Error code" not in text and "req_raw_123" not in text
    assert not any(x["kind"] == "モック" for x in r["state"]["log"])  # モックへ切り替えていない
    assert {"kind": "止めた理由", "text": getattr(ci, expected)} in r["state"]["log"]


@pytest.mark.parametrize("bad_key", ["ここに自分のAPIキーを入れる", "sk-ant xyz", "sk-ant-\nxyz", "ｓｋ－ａｎｔ"])
def test_unusable_key_text_stops_before_any_request(bad_key, monkeypatch):
    made = []
    sdk = NS(Anthropic=lambda **kw: made.append(kw) or client(resp()))
    it = ci.ClaudeInterpreter(bad_key, sdk=sdk)
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(u.read_utterance(UP10), "C00004", NAMES)
    assert str(e.value) == ci.AUTH and made == []
    assert bad_key not in str(e.value)


def test_state_shows_which_interpreter_is_active():
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, model="claude-opus-5-5", client=client(resp())))
    st = w.load(DEMO)["state"]
    assert st["interpreter"]["kind"] == "claude" and st["interpreter"]["model"] == "claude-opus-5-5"
    assert "本物の Claude" in st["interpreter"]["notice"] and KEY not in json.dumps(st, ensure_ascii=False)
    st = WebFlow(interpreter=ci.MockInterpreter()).load(DEMO)["state"]
    assert st["interpreter"] == {"kind": "mock", "model": None, "notice": ci.MockInterpreter.notice}


def test_badge_shows_claude_model_name():
    js = (ROOT / "voice_teaching" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "st.interpreter.model" in js
    assert '"解釈：モック"' in js


# ======================================================================
# 4. キーなしの自己点検（python -m voice_teaching.claude_check）
# ======================================================================
class NoNetwork:
    """自己点検が SDK のクライアントを作ったら失敗にする。"""
    def __init__(self, **kw):
        raise AssertionError("自己点検でクライアントを作った（通信しようとした）")


@pytest.fixture
def no_client(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    monkeypatch.setattr(anthropic, "Anthropic", NoNetwork)


def run_check(args, env, input_text="", make_interpreter=None):
    out = io.StringIO()
    code = claude_check.main(args, env=env, out=out, input_fn=lambda _p: input_text,
                             make_interpreter=make_interpreter)
    return code, out.getvalue()


def test_check_without_key(tmp_path, no_client):
    code, out = run_check(["--env", str(tmp_path / ".env")], {})
    assert code == 0
    assert ".env ファイル：なし" in out and "ANTHROPIC_API_KEY：なし" in out
    assert "モック" in out and "claude-opus-5-5" in out
    assert "anthropic" in out and "通信はしていません" in out


def test_check_with_key_in_dotenv_never_prints_key_or_length(tmp_path, no_client):
    p = tmp_path / ".env"
    p.write_text(f"ANTHROPIC_API_KEY={KEY}\nANTHROPIC_MODEL=claude-sonnet-5-5\n", encoding="utf-8")
    code, out = run_check(["--env", str(p)], {})
    assert code == 0
    assert ".env ファイル：あり" in out and "ANTHROPIC_API_KEY：あり" in out
    assert KEY not in out and "sk-ant" not in out and str(len(KEY)) not in out and KEY[-6:] not in out
    assert "claude-sonnet-5-5" in out and "本物の Claude" in out
    # 送る内容（キーは伏せる）
    start = out.index("{")
    shown = json.loads(out[start:out.rindex("}") + 1])
    assert shown["client"]["api_key"] == claude_check.REDACTED
    assert shown["request"] == ci.build_request("claude-sonnet-5-5", claude_check.SAMPLE_UTTERANCE,
                                                claude_check.SAMPLE_SELECTED, shown["request"]["tools"][0]
                                                ["input_schema"]["properties"]["position"]["enum"])
    assert "thinking" not in shown["request"] and "temperature" not in shown["request"]


def test_check_env_var_wins_and_placeholder_is_flagged(tmp_path, no_client):
    p = tmp_path / ".env"
    p.write_text("ANTHROPIC_API_KEY=ここに自分のAPIキーを入れる\n", encoding="utf-8")
    code, out = run_check(["--env", str(p)], {})
    assert "ANTHROPIC_API_KEY：あり" in out and "使えない文字" in out
    assert "ここに自分のAPIキーを入れる" not in out
    code, out = run_check(["--env", str(p)], {"ANTHROPIC_API_KEY": KEY})
    assert "環境変数" in out and "使えない文字" not in out and KEY not in out


def test_check_call_refuses_without_key(tmp_path, no_client):
    made = []
    code, out = run_check(["--call", "--env", str(tmp_path / ".env")], {}, input_text="yes",
                          make_interpreter=lambda k, m: made.append(1))
    assert code != 0 and made == [] and "実行しません" in out


def test_check_call_refuses_with_unusable_key(tmp_path, no_client):
    made = []
    code, out = run_check(["--call"], {"ANTHROPIC_API_KEY": "ここに自分のAPIキーを入れる"}, input_text="yes",
                          make_interpreter=lambda k, m: made.append(1))
    assert code != 0 and made == [] and "実行しません" in out


def test_check_call_warns_cost_and_needs_yes(tmp_path, no_client):
    made = []
    code, out = run_check(["--call", "--env", str(tmp_path / ".env")], {"ANTHROPIC_API_KEY": KEY}, input_text="no",
                          make_interpreter=lambda k, m: made.append(1))
    assert "料金" in out and code != 0 and made == []


def test_check_call_without_input_does_not_send(tmp_path, no_client):
    made = []

    def eof(_prompt):
        raise EOFError
    out = io.StringIO()
    code = claude_check.main(["--call", "--env", str(tmp_path / ".env")], env={"ANTHROPIC_API_KEY": KEY}, out=out,
                             input_fn=eof, make_interpreter=lambda k, m: made.append(1))
    assert code != 0 and made == [] and "送りませんでした" in out.getvalue()


def test_check_call_with_fake_client_runs_one_request_through_check(tmp_path, no_client):
    c = client(resp(thinking(), tool_use("propose_position", prop())))
    code, out = run_check(["--call", "--env", str(tmp_path / ".env")], {"ANTHROPIC_API_KEY": KEY}, input_text="yes",
                          make_interpreter=lambda k, m: ci.ClaudeInterpreter(k, m, client=c))
    assert code == 0 and len(c.messages.calls) == 1
    assert "料金" in out and "ok" in out and "C00004 Z" in out and KEY not in out


def test_check_call_failure_shows_guidance_only(tmp_path, no_client):
    c = client(exc=RuntimeError(f"raw {KEY}"))
    code, out = run_check(["--call"], {"ANTHROPIC_API_KEY": KEY}, input_text="yes",
                          make_interpreter=lambda k, m: ci.ClaudeInterpreter(k, m, client=c))
    assert code != 0 and ci.FAILED in out and KEY not in out and "raw" not in out


def test_env_files_stay_ignored():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gi and ".env.*" in gi and "!.env.example" in gi
