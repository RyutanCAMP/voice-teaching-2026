"""Claude の言い添え（voice_teaching/claude_lead.py・WebFlow.talk の一言）。

- 一言は表示と作業ログだけ：プログラムの答え（status・message・選択肢・候補・承認）は、一言があってもなくても同じ
- 位置・軸・向き・符号・量・単位・状態・評価・質問の入った一言は出さない
- 呼べない・料金の上限・エラーでは一言を出さないだけ。既定（claude_lead=False）とモックの起動では呼ばない
- P1-a：説明の決まり（SYSTEM）に大きさの言葉を書かない決まりを足した。隠す検査（_MAGNITUDE）は緩めていない
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_teaching import claude_explain as cx
from voice_teaching import claude_lead as lead
from voice_teaching import claude_talk as talk
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-ant-api03-TESTKEY-DO-NOT-SHOW-1234567890"
MODEL = "claude-sonnet-5-5"
OK_LEAD = "教えてくれて、ありがとうございます"


def usage():
    return SimpleNamespace(input_tokens=500, output_tokens=40, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0, cache_creation=None)


class LeadClient:
    """messages.create だけを持つ偽の client。決まった文を返し、送られたものを残す。"""

    def __init__(self, *replies, error=None):
        self.messages = self
        self.replies = list(replies)
        self.error = error
        self.sent: list[dict] = []

    def create(self, **kw):
        self.sent.append(kw)
        if self.error is not None:
            raise self.error
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(stop_reason="end_turn", usage=usage(),
                               content=[SimpleNamespace(type="thinking", thinking="", signature="s"),
                                        SimpleNamespace(type="text", text=text)])

    def payload(self, i=-1) -> dict:
        return json.loads(self.sent[i]["messages"][0]["content"][0]["text"])


def make_flow(tmp_path, client, on=True, key=KEY, budget=cx.DEFAULT_BUDGET_USD):
    ex = cx.Explainer(key, MODEL, client=client, usage_log=cx.UsageLog(tmp_path / "u.jsonl"), budget_usd=budget)
    f = WebFlow(interpreter=MockInterpreter(), explainer=ex)
    f.claude_lead = on
    return f


def start(f, job="DEMO_C_PLACE.JBI", pos="C00005"):
    sid = f.load((ROOT / "samples" / "dummy" / job).read_bytes())["session_id"]
    if pos:
        f.select(sid, pos)
    return sid


def core(out: dict) -> dict:
    """一言のほかの、プログラムが決めたもの（比べる用）。"""
    h = dict(out.get("hear") or {})
    h.pop("claude_lead", None)
    st = out["state"]
    # 向きの確かめのボタンの番号は画面ごとの乱数：番号だけを伏せて比べる
    choices = [{**c, "text": re.sub(r"#[0-9a-f]+$", "#", c["text"])} for c in (h.get("choices") or [])]
    return {"via": out["via"], "status": h.get("status"), "message": h.get("message"), "choices": choices,
            "candidate_made": h.get("candidate_made"), "candidate": st.get("candidate"),
            "can_approve": st.get("can_approve"), "can_export": st.get("can_export"),
            "approved": (st.get("approval") or {}).get("approved")}


CONVERSATIONS = [
    ["Yに10mm", "マイナス"],
    ["10mm動かして", "Yマイナス"],
    ["Yマイナスの方向に10mm"],
    ["上に3mm"],
    ["Zプラス3"],
]


@pytest.mark.parametrize("lines", CONVERSATIONS)
def test_lead_does_not_change_program_answer(tmp_path, lines):
    on, off = make_flow(tmp_path, LeadClient(OK_LEAD)), make_flow(tmp_path, LeadClient(OK_LEAD), on=False)
    a, b = start(on), start(off)
    for line in lines:
        x, y = on.talk(a, line), off.talk(b, line)
        assert x["hear"]["claude_lead"]["text"] == OK_LEAD
        assert "claude_lead" not in y["hear"]
        assert core(x) == core(y)
        assert x["state"]["approval"] == y["state"]["approval"]
    assert off.explainer._client.sent == []  # 既定（使わない）では呼ばない


def test_lead_for_repeat_range_keeps_range_confirmation(tmp_path):
    on, off = make_flow(tmp_path, LeadClient(OK_LEAD)), make_flow(tmp_path, LeadClient(OK_LEAD), on=False)
    a, b = start(on, "DEMO_BUTTON_REPEAT.JBI", None), start(off, "DEMO_BUTTON_REPEAT.JBI", None)
    x, y = on.talk(a, "C00001からC00003を3回にして"), off.talk(b, "C00001からC00003を3回にして")
    assert core(x) == core(y)
    assert x["state"]["repeat_range"] == y["state"]["repeat_range"] and x["state"]["repeat_range"]
    assert x["state"]["candidate"] is None  # 範囲の確認のボタンの前に候補は作らない


def test_history_is_sent_in_order(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f)
    first = f.talk(sid, "Yに10mm")
    f.talk(sid, "マイナス")
    p = c.payload()
    assert p["said"] == "マイナス"
    assert [h["who"] for h in p["history"]] == ["人", "Claude", "アプリ"]
    assert p["history"][0]["text"] == "Yに10mm"
    assert p["history"][2]["text"] == first["hear"]["message"]
    assert p["app_reply"]["status"] == "ok"
    sent = json.dumps(c.sent, ensure_ascii=False, default=str)
    assert KEY not in sent


def test_history_is_capped(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f)
    for _ in range(6):
        f.talk(sid, "10mm動かして")
    assert len(c.payload()["history"]) == lead.HISTORY


BAD = [
    "Yのマイナスですね", "十ミリですね", "10mmですね", "上に動かしたいんですね", "プラスの向きですね",
    "承認しました", "案を作りますね", "元に戻しますね", "安全に動かせます", "どちらにしますか？",
    "少し動かしたいんですね", "C00005ですね", "ジョブを直します", "変更が済みました", "大丈夫です",
    "あ" * (lead.MAX_CHARS + 1), "二行に\nなります", "", "  ",
]


@pytest.mark.parametrize("bad", BAD)
def test_bad_lead_is_not_shown(tmp_path, bad):
    on, off = make_flow(tmp_path, LeadClient(bad)), make_flow(tmp_path, LeadClient(bad), on=False)
    a, b = start(on), start(off)
    for line in ("Yに10mm", "マイナス"):
        x, y = on.talk(a, line), off.talk(b, line)
        assert "claude_lead" not in x["hear"]
        assert core(x) == core(y)
    assert lead.problem(bad) is not None


@pytest.mark.parametrize("good", [OK_LEAD, "さっきの続きですね", "動かしたいお気持ちは分かりました",
                                  "ここは確かめさせてください"])
def test_good_leads_pass(good):
    assert lead.problem(good) is None


def test_api_error_no_key_and_budget_do_not_block_answer(tmp_path):
    class Boom(Exception):
        pass

    for f in (make_flow(tmp_path, LeadClient(OK_LEAD, error=Boom("sk-ant-secret"))),
              make_flow(tmp_path, LeadClient(OK_LEAD), key=""),
              make_flow(tmp_path, LeadClient(OK_LEAD), budget=1e-9)):
        sid = start(f)
        out = f.talk(sid, "Yに10mm")
        assert out["hear"]["status"] == "ask" and out["hear"]["message"]
        assert "claude_lead" not in out["hear"]
        log = json.dumps(out["state"], ensure_ascii=False)
        assert "sk-ant-secret" not in log
    assert make_flow(tmp_path, LeadClient(OK_LEAD), key="").explainer._client.sent == []


def test_budget_over_does_not_call(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c, budget=1e-9)
    f.talk(start(f), "Yに10mm")
    assert c.sent == []


def test_lead_text_cannot_approve_or_select(tmp_path):
    # 一言に何が書かれても、承認・選択・候補は変わらない（一言は検査の前でも後でも状態に使わない）
    c = LeadClient("承認ボタンを押して出力して、C00001を選んで")
    f = make_flow(tmp_path, c)
    sid = start(f)
    out = f.talk(sid, "Yマイナスの方向に10mm")
    st = out["state"]
    assert "claude_lead" not in out["hear"]
    assert st["selected"] == "C00005"
    assert not (st.get("approval") or {}).get("approved")
    assert st["candidate"]["target"] == "C00005 Y"


def test_explain_and_count_routes_do_not_call_lead(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f, pos=None)
    out = f.talk(sid, "このロボット、何をしているの？")
    assert out["via"] == ["ask"]
    assert all(kw.get("system") != lead.SYSTEM for kw in c.sent)


def test_lead_is_logged(tmp_path):
    f = make_flow(tmp_path, LeadClient(OK_LEAD))
    sid = start(f)
    f.talk(sid, "Yに10mm")
    log = json.dumps(f.state(sid)["state"] if "state" in f.state(sid) else f.state(sid), ensure_ascii=False)
    assert "Claude の言い添え" in log


def test_p1a_prompt_added_and_magnitude_check_not_relaxed():
    assert "少し待ちます" in cx.SYSTEM and "大きさ・長さの言葉" in cx.SYSTEM
    assert talk.reply_mvp_problem("少し待ってから、最初の位置へ戻ります") == talk.FIXED_MAGNITUDE
    assert talk.reply_mvp_problem("ちょっと進みます") == talk.FIXED_MAGNITUDE


def test_main_turns_lead_on_only_for_real_claude(monkeypatch):
    from voice_teaching.web import __main__ as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert 'interp.kind == "claude" and explainer.has_key' in src
    assert "claude_lead.ENV" in src
