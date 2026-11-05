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
    # 独立チェック（11/5 夜）で前の検査を通った文：ひらがな・カタカナの軸や量・ボタン操作の指示・言い切り・状態・約束
    "ごばんめの点を、ぜっとのほうへ、ごみり、うえにですね", "ゼットをさげたいのですね", "ワイのほうへ、じゅうみりですね",
    "にじゅっせんちですね", "エックスですね", "みぎですね", "さっきのぎゃくですね", "さっきと同じ向きですね", "にばいですね",
    "すこーしですね", "最初の点ですね", "正の向きですね", "アップですね", "合っているを押してください",
    "あっているを押してください", "はいを押して進めてください", "違うは押さないでください", "ボタンを押してください",
    "その向きで合っています", "案は正しいです", "間違いありません", "候補はもうできています", "これで決まりです",
    "すぐ動き出します", "実機でも動きます", "動かしてよいです", "範囲の外です", "キャンセルしますね", "言われたとおりにします",
    # 一覧の文の言い換え・付け足し・3つ以上・同じ文の重ね
    "教えてくれてありがとうございます", "教えてくれて、ありがとうございます！合っています",
    "さっきの続きですね。ここは確かめさせてください。一緒に確かめさせてください",
    "さっきの続きですね。さっきの続きですね",
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


@pytest.mark.parametrize("good", list(lead.PHRASES) + ["さっきの続きですね。ここは確かめさせてください",
                                                     "教えてくれて、ありがとうございます。"])
def test_good_leads_pass(good):
    assert lead.problem(good) is None


def test_phrases_pass_existing_checks():
    # 一覧の文は、数・評価・状態・大きさ・安全の言葉・上下・命令の文字列の検査（前からの検査）もそのまま通る
    import re as _re
    for x in lead.PHRASES:
        assert talk.reply_mvp_problem(x) is None and cx.check_reply(x, set(), False) is None, x
        assert not _re.search(r"[0-9０-９〇一二三四五六七八九十百A-Za-zＡ-Ｚａ-ｚ?？]", x.replace("一緒", "")), x
        assert not _re.search(r"押|選|ボタン|承認|出力|合って|正し|動き出|実機|安全|大丈夫|上|下|右|左|前|後|戻|やめ|作", x), x


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


def test_enabled_only_for_real_claude_with_key():
    real, mock = SimpleNamespace(kind="claude"), SimpleNamespace(kind="mock")
    key, nokey = SimpleNamespace(has_key=True), SimpleNamespace(has_key=False)
    assert lead.enabled(real, key, {}) is True
    assert lead.enabled(real, key, {lead.ENV: "1"}) is True
    assert lead.enabled(real, key, {lead.ENV: "0"}) is False
    assert lead.enabled(real, key, {lead.ENV: " 0 "}) is False
    assert lead.enabled(mock, key, {}) is False
    assert lead.enabled(real, nokey, {}) is False


def test_launcher_uses_enabled(monkeypatch):
    # 起動（python -m voice_teaching.web）が lead.enabled の結果を使う：サーバーを作る所を差し替えて確かめる
    from voice_teaching.web import __main__ as m

    made = {}

    class Srv:
        server_address = ("127.0.0.1", 0)

        def __init__(self):
            self.flow = SimpleNamespace(claude_lead=None)

        def serve_forever(self):
            made["lead"] = self.flow.claude_lead
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


def test_usage_is_logged(tmp_path):
    f = make_flow(tmp_path, LeadClient(OK_LEAD))
    f.talk(start(f), "Yに10mm")
    lines = (tmp_path / "u.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and KEY not in lines[0]


def test_count_route_does_not_call_lead(tmp_path):
    # 回数の変更の希望（会話の道＋指示の道。via が2つ）：Claude の説明があるので、一言は足さない
    class Dual(LeadClient):
        """説明（cx.SYSTEM）には回数の変更の希望の答え、言い添えには相づちを返す。"""

        def create(self, **kw):
            if kw.get("system") == cx.SYSTEM:
                self.sent.append(kw)
                data = {"reply": "回数を変えたいのですね。", "evidence_lines": [], "intent": "change_request",
                        "change_request": {"kind": "repeat", "from_count": 2, "to_count": 3}}
                return SimpleNamespace(stop_reason="tool_use", usage=usage(), content=[
                    SimpleNamespace(type="tool_use", id="toolu_c", name="answer", input=data)])
            return super().create(**kw)

    c = Dual(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f, "DEMO_BUTTON_REPEAT.JBI", None)
    out = f.talk(sid, "3回にして")
    assert out["via"] == ["ask", "hear"], out["via"]
    assert out["state"]["repeat_range"]
    assert all(kw.get("system") != lead.SYSTEM for kw in c.sent)
    assert "claude_lead" not in (out.get("hear") or {})


def test_reset_clears_history(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f)
    f.talk(sid, "Yに10mm")
    f.ask(sid, reset=True)
    f.talk(sid, "10mm動かして")
    assert c.payload()["history"] == []


def test_short_timeout_without_retry(tmp_path):
    class WithOptions(LeadClient):
        def with_options(self, **kw):
            self.options = kw
            return self

    c = WithOptions(OK_LEAD)
    f = make_flow(tmp_path, c)
    f.talk(start(f), "Yに10mm")
    assert c.options == {"timeout": lead.TIMEOUT_SECONDS, "max_retries": 0}
    assert c.payload()["phrases"] == list(lead.PHRASES)
