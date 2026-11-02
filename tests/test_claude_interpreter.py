"""本物の Claude への接続（claude_interpreter）のテスト。SDK の呼び出し部分は偽物に差し替える（実 API は呼ばない）。"""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import utterance as u
from voice_teaching.web.flow import WebFlow

ORIGINAL = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()
KEY = "sk-ant-TEST-SECRET-0123456789"
NAMES = ["C00000", "C00001", "C00004"]


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


def resp(*blocks, stop="tool_use"):
    return NS(stop_reason=stop, content=list(blocks))


def facts(text):
    return u.read_utterance(text)


def up10():
    return resp(NS(type="thinking", thinking=""), NS(type="text", text="C00004 を 10mm"),
                tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": 10}))


# ---- 有効になる条件 ----
def test_without_key_mock_is_used():
    assert ci.from_env({}).kind == "mock"
    assert ci.from_env({"ANTHROPIC_API_KEY": "  "}).kind == "mock"
    assert "モック" in ci.from_env({}).notice


def test_with_key_claude_is_used_and_default_model():
    it = ci.from_env({"ANTHROPIC_API_KEY": KEY})
    assert it.kind == "claude" and it.model == "claude-opus-5-5"
    assert ci.from_env({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_MODEL": "claude-sonnet-5-5"}).model == "claude-sonnet-5-5"
    assert KEY not in repr(it) and KEY not in it.notice


def test_sdk_client_is_built_with_timeout_and_few_retries(monkeypatch):
    made = {}
    fake_sdk = types.ModuleType("anthropic")

    def Anthropic(**kw):
        made.update(kw)
        return client(up10())
    fake_sdk.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    it = ci.from_env({"ANTHROPIC_API_KEY": KEY})
    it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    assert made["api_key"] == KEY and made["timeout"] == 30.0 and made["max_retries"] in (0, 1)


def test_missing_sdk_stops_without_mock(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import できない
    it = ci.ClaudeInterpreter(KEY)
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    assert KEY not in str(e.value)


# ---- SDK に渡す引数の形 ----
def test_request_shape():
    c = client(up10())
    it = ci.ClaudeInterpreter(KEY, client=c)
    calls = it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    kw = c.messages.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["max_tokens"] == 2048
    assert kw["output_config"] == {"effort": "low"}
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}  # forced（any / tool）でない
    assert "thinking" not in kw
    assert [t["name"] for t in kw["tools"]] == ["propose_position", "ask_user"]
    for t in kw["tools"]:
        assert t["strict"] is True
        sch = t["input_schema"]
        assert sch["additionalProperties"] is False and set(sch["required"]) == set(sch["properties"])
    assert kw["tools"][0]["input_schema"]["properties"]["direction"]["enum"][:4] == ["上", "下", "右", "左"]  # 軸と向き（X+ など）は test_claude_axis
    # 送るのは発話・選んでいる位置・位置番号の一覧だけ
    body = json.loads(kw["messages"][0]["content"])
    assert body == {"utterance": "この位置を上に10mm移動して", "selected_position": "C00004",
                    "available_positions": NAMES}
    assert "どちらか1つ" in kw["system"]
    assert calls == [{"tool": "propose_change",
                      "args": {"kind": "shift", "position": "C00004", "axis": "Z", "delta_mm": 10.0}}]


def test_down_is_minus_z_and_side_asks():
    r = resp(tool_use("propose_position", {"position": "C00004", "direction": "下", "amount_mm": 5}))
    it = ci.ClaudeInterpreter(KEY, client=client(r))
    assert it.decide(facts("この位置を下に5mm移動して"), "C00004", NAMES)[0]["args"]["delta_mm"] == -5.0
    r = resp(tool_use("propose_position", {"position": "C00004", "direction": "右", "amount_mm": 5}))
    it = ci.ClaudeInterpreter(KEY, client=client(r))
    calls = it.decide(facts("この位置をZプラス方向に5mm移動して"), "C00004", NAMES)
    assert calls == [{"tool": "ask_user", "args": {"question": u.SIDE_MESSAGE}}]


def test_ask_user_text_from_claude_is_not_used():
    r = resp(tool_use("ask_user", {"question": "15mm にしますか"}))
    it = ci.ClaudeInterpreter(KEY, client=client(r))
    calls = it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    assert calls == [{"tool": "ask_user", "args": {"question": ci.ASK_FIXED}}]


def test_select_compare_undo_do_not_call_claude():
    c = client(up10())
    it = ci.ClaudeInterpreter(KEY, client=c)
    assert it.decide(facts("比較して"), "C00004", NAMES) == [{"tool": "compare", "args": {}}]
    assert it.decide(facts("C00001を選択して"), None, NAMES) == [{"tool": "select", "args": {"position": "C00001"}}]
    assert it.decide(facts("C00004の速度を10%上げて"), None, NAMES) == []
    assert c.messages.calls == []


# ---- 異常な応答は止める ----
@pytest.mark.parametrize("response", [
    resp(stop="refusal"),
    resp(NS(type="text", text="10mm 上げます"), stop="end_turn"),
    resp(),  # tool_use 0件
    resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": 10}),
         tool_use("ask_user", {"question": "?"})),  # 2件
    resp(tool_use("approve", {})),  # 知らない道具
    resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": 10,
                                       "after_line": "C00004=..."})),  # 余計な項目
    resp(tool_use("propose_position", {"position": "C00009", "direction": "上", "amount_mm": 10})),
    resp(tool_use("propose_position", {"position": "C00004", "direction": "前", "amount_mm": 10})),
    resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": -10})),
    resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": "10"})),
    resp(tool_use("ask_user", {"question": "?", "extra": 1})),
])
def test_bad_responses_raise(response):
    it = ci.ClaudeInterpreter(KEY, client=client(response))
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    assert KEY not in str(e.value)


def test_refusal_has_its_own_fixed_message():
    it = ci.ClaudeInterpreter(KEY, client=client(resp(stop="refusal")))
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(facts("この位置を上に10mm移動して"), "C00004", NAMES)
    assert str(e.value) == ci.REFUSED


# ---- 画面の流れ：失敗はモックへ切り替えず止める・キーを出さない ----
def flow_with(c, tmp_path):
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(ORIGINAL)["session_id"]
    w.select(sid, "C00004")
    return w, sid


def test_flow_with_claude_makes_candidate_after_check(tmp_path):
    w, sid = flow_with(client(up10()), tmp_path)
    r = w.hear(sid, "この位置を上に10mm移動して", source="voice")
    assert r["status"] == "ok"
    assert r["state"]["candidate"]["after_line"] == "C00004=450.000,190.000,130.000,180.00,0.00,0.00"
    assert r["state"]["interpreter"]["kind"] == "claude"
    assert any(x["kind"] == "Claude" for x in r["state"]["log"])


def test_flow_claude_result_still_checked_against_utterance(tmp_path):
    # 発話は 10mm、Claude は 15mm → 照らし合わせで聞き返し
    c = client(resp(tool_use("propose_position", {"position": "C00004", "direction": "上", "amount_mm": 15})))
    w, sid = flow_with(c, tmp_path)
    r = w.hear(sid, "この位置を上に10mm移動して")
    assert r["status"] == "ask" and r["state"]["candidate"] is None


def test_flow_failure_does_not_switch_to_mock_and_hides_key(tmp_path):
    c = client(exc=RuntimeError(f"401 invalid x-api-key {KEY}"))
    w, sid = flow_with(c, tmp_path)
    r = w.hear(sid, "この位置を上に10mm移動して")
    assert r["status"] == "stop" and r["message"] == ci.FAILED
    assert r["tool_calls"] == []
    assert r["state"]["candidate"] is None
    text = json.dumps(r, ensure_ascii=False)
    assert KEY not in text and "401" not in text and "x-api-key" not in text


def test_flow_without_position_does_not_send_to_claude(tmp_path):
    c = client(up10())
    w = WebFlow(interpreter=ci.ClaudeInterpreter(KEY, client=c))
    sid = w.load(ORIGINAL)["session_id"]
    assert w.hear(sid, "この位置を上に10mm移動して")["status"] == "ask"
    assert c.messages.calls == []


def test_load_dotenv_reads_only_two_keys(tmp_path):
    p = tmp_path / ".env"
    p.write_text("ANTHROPIC_API_KEY=abc\nANTHROPIC_MODEL=claude-opus-5-5\nOTHER=1\n", encoding="utf-8")
    env = {"ANTHROPIC_MODEL": "x"}
    ci.load_dotenv(p, env)
    assert env == {"ANTHROPIC_API_KEY": "abc", "ANTHROPIC_MODEL": "x"}
