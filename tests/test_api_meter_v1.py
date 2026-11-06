"""解釈の係・意味理解の料金も、説明の係と同じ使用量の記録と上限に入れる（本番候補 v1.0・11/7）。偽の SDK だけ。"""
from types import SimpleNamespace

import pytest

from voice_teaching import claude_explain as cx
from voice_teaching import claude_interpreter as ci
from voice_teaching import nl_understand as nu

KEY = "sk-ant-api03-TESTKEY-METER-0000000000"
MODEL = "claude-sonnet-5-5"
ANSWER = ('{"intent":"move","status":"ready","position_ref":"selected","position":null,"position_evidence":null,'
          '"direction":"Z+","direction_evidence":"Zプラス","amount_kind":"stated","amount_value":5,"amount_unit":"mm",'
          '"amount_evidence":"5ミリ","missing":[],"question":null,"note":""}')


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=ANSWER)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=0))


def interp(tmp_path, budget):
    it = ci.ClaudeInterpreter(KEY, MODEL, client=FakeClient())
    it.meter = cx.ApiMeter(cx.UsageLog(tmp_path / "api_usage.jsonl"), budget)
    return it


def state():
    return nu.build_state(["C00001"], "C00001", None, None, [])


def test_understand_is_recorded_with_kind(tmp_path):
    it = interp(tmp_path, 5.0)
    it.understand(state(), "Zプラス5ミリ")
    lines = (tmp_path / "api_usage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"kind": "understand"' in lines[0] and KEY not in lines[0]
    assert it.meter.usage_log.total() > 0


def test_over_budget_is_not_called(tmp_path):
    it = interp(tmp_path, 0.000001)
    with pytest.raises(nu.UnderstandError) as e:
        it.understand(state(), "Zプラス5ミリ")
    assert e.value.kind == "api" and "上限" in str(e.value) and "ネットワーク" not in str(e.value)
    assert it._client.calls == 0 and not (tmp_path / "api_usage.jsonl").exists()


def test_unreadable_usage_log_stops_calls(tmp_path):
    p = tmp_path / "api_usage.jsonl"
    p.write_text("こわれた行\n", encoding="utf-8")
    it = interp(tmp_path, 5.0)
    with pytest.raises(nu.UnderstandError):
        it.understand(state(), "Zプラス5ミリ")
    assert it._client.calls == 0


def test_decide_over_budget_says_budget_not_network(tmp_path):
    from voice_teaching import utterance as ut
    it = interp(tmp_path, 0.000001)
    with pytest.raises(ci.InterpreterError) as e:
        it.decide(ut.read_utterance("Zプラス方向に5ミリ"), "C00001", ["C00001"])
    assert "上限" in str(e.value) and it._client.calls == 0


def test_main_wires_the_meter():
    import inspect
    from voice_teaching.web import __main__ as m
    src = inspect.getsource(m)
    assert "interp.meter = claude_explain.ApiMeter(explainer.usage_log, explainer.budget_usd)" in src
