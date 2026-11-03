"""Claude に聞く（読み取りだけ。voice_teaching/claude_explain.py）の受入テスト。

本物の API は呼ばない（偽の client だけ）。確かめること：
- 送る内容に //POS の座標の値・ジョブ名・見出し・コメント・キーが無い。合成でないジョブでは呼ばない
- 道具の答えの検査（範囲外の行・禁止の言葉・送っていない数・未確認の上下・命令のような文字列・変えたような言い方）
- change_request でジョブ・候補・承認・作業ログが変わらない
- 会話の履歴が書き換えられずに次の要求に入る（前の content のブロックはそのまま。結果は tool_result で返す）
- 使用量と料金の計算・累計の上限で呼ばない・jsonl にキーや中身が入らない
- エラーの扱い（何も変えない・キーを出さない）。CLI は --call と yes が無ければ呼ばない
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_teaching import claude_explain as cx
from voice_teaching import jbi, sim
from voice_teaching import updown as ud
from voice_teaching.web import server
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"
STATIC = ROOT / "voice_teaching" / "web" / "static"
KEY = "sk-ant-api03-TESTKEY-DO-NOT-SHOW-1234567890"
MODEL = "claude-sonnet-5-5"


def demo_bytes() -> bytes:
    return DEMO.read_bytes()


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=lambda o: vars(o))


# ---- 偽の SDK の返事 ----
def usage(i=1000, o=200, cw=0, cr=0, one_h=None):
    cc = SimpleNamespace(ephemeral_1h_input_tokens=one_h, ephemeral_5m_input_tokens=cw - (one_h or 0)) if one_h else None
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw,
                           cache_read_input_tokens=cr, cache_creation=cc)


def answer(reply, lines=(), intent="explain", change=None, tool_id="toolu_01", stop="tool_use", extra=None, u=None):
    data = {"reply": reply, "evidence_lines": list(lines), "intent": intent, "change_request": change}
    if extra:
        data.update(extra)
    content = [SimpleNamespace(type="thinking", thinking="", signature="sig-" + tool_id),
               SimpleNamespace(type="tool_use", id=tool_id, name="answer", input=data)]
    return SimpleNamespace(stop_reason=stop, content=content, usage=u or usage())


def text_only(reply="Z方向の往復が2回あります。"):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=reply)], usage=usage())


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.snapshots = []  # 送ったときの messages の中身（あとで書き換えられていないか比べる）
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        self.snapshots.append(dump(kw["messages"]))
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class NoCall:
    """呼ばれたら失敗する client。"""

    def __init__(self):
        self.messages = self

    def create(self, **kw):  # pragma: no cover - 呼ばれたらテストが落ちる
        raise AssertionError("Claude を呼んではいけない")


def explainer(tmp_path, client, model=MODEL, key=KEY, budget=cx.DEFAULT_BUDGET_USD):
    return cx.Explainer(key, model, client=client, usage_log=cx.UsageLog(tmp_path / "api_usage.jsonl"),
                        budget_usd=budget)


def conv(updown=None, data=None):
    data = demo_bytes() if data is None else data
    return cx.Conversation.start(data, None, updown)


def confirmed():
    return ud.UpdownStatus(ud.CONFIRMED, "確認済み", confirmed_by="テスト", confirmed_at="2026-11-03T10:00:00+09:00",
                           fingerprint="f" * 64, file_sha256="a" * 64)


# ---- 1. 送る内容 ----
def test_facts_have_instructions_moves_repeats_but_no_coordinates_name_or_comment():
    c = conv()
    assert c.problem is None and c.synthetic
    f = c.facts
    assert [d["line"] for d in f["instructions"]] == [18, 20, 21, 22, 23, 24, 25, 26, 27, 28]
    assert f["positions"] == ["C00000", "C00001", "C00002", "C00003", "C00004"]
    by_line = {m["line"]: m for m in f["moves"]}
    assert by_line[20]["from"] is None and by_line[20]["change_mm"] is None  # ジョブの初めから：変化量は出さない
    assert by_line[22] == {"line": 22, "command": "MOVL", "from": "C00001", "to": "C00002",
                           "change_mm": {"X": 0.0, "Y": 0.0, "Z": -45.0}}
    assert by_line[27]["command"] == "MOVJ"
    assert f["repeats"] == [{"positions": ["C00001", "C00002", "C00003"], "count": 2, "points_per_cycle": 3, "first_line": 21,
                             "last_line": 26, "cycles": [{"cycle": 1, "lines": [21, 23]},
                                                         {"cycle": 2, "lines": [24, 26]}]}]
    assert f["updown"]["status"] == "unconfirmed"
    text = dump(cx.preview(c, MODEL, "このロボット、何をしている？"))
    job_text = demo_bytes().decode("utf-8")
    for line in job_text.splitlines():
        if line.startswith("C0000") and "=" in line:  # //POS の行と座標の値
            assert line not in text
            for v in line.split("=", 1)[1].split(","):
                if v not in ("0.00",):
                    assert v not in text, v
    for w in ("DEMO_BUTTON_REPEAT", "//NAME", "///DATE", "2026/11/03", "SYNTHETIC", "NOT A REAL", "//POS", "RCONF"):
        assert w not in text, w
    assert "（伏せています）" in text


def test_preview_and_requests_never_contain_the_key(tmp_path):
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21]))
    ex = explainer(tmp_path, fake)
    c = conv()
    out = io.StringIO()
    cx.main(["--job", str(DEMO), "--say", "何をしている？", "--usage-file", str(tmp_path / "u.jsonl"),
             "--env-file", str(tmp_path / "none.env")], env={"ANTHROPIC_API_KEY": KEY}, out=out)
    assert KEY not in out.getvalue() and "TESTKEY" not in out.getvalue()
    ex.ask(c, "何をしている？")
    assert KEY not in dump(fake.requests) and "TESTKEY" not in repr(ex)


def test_request_shape_follows_sonnet_5_5_rules(tmp_path):
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21]))
    explainer(tmp_path, fake).ask(conv(), "何をしている？")
    req = fake.requests[0]
    assert req["model"] == MODEL
    assert req["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert req["output_config"] == {"effort": "low"}
    assert req["max_tokens"] == cx.MAX_TOKENS <= 4000
    for k in ("thinking", "temperature", "top_p", "top_k"):
        assert k not in req
    (tool,) = req["tools"]
    assert tool["name"] == "answer" and tool["strict"] is True
    sch = tool["input_schema"]
    assert sch["additionalProperties"] is False
    assert set(sch["required"]) == set(sch["properties"]) == {"reply", "evidence_lines", "intent", "change_request"}
    assert sch["properties"]["evidence_lines"]["items"]["enum"] == [18, 20, 21, 22, 23, 24, 25, 26, 27, 28]
    for alt in sch["properties"]["change_request"]["anyOf"]:
        if alt.get("type") == "object":
            assert alt["additionalProperties"] is False and set(alt["required"]) == set(alt["properties"])
    # 未確認なら「Z方向」とだけ言うよう、システムの文でも伝える
    assert "unconfirmed" in req["system"] and "Z方向" in req["system"]


def test_updown_confirmed_is_sent_as_confirmed():
    assert conv(confirmed()).facts["updown"]["status"] == "confirmed"
    assert conv(ud.unconfirmed()).facts["updown"]["status"] == "unconfirmed"
    mismatch = ud.UpdownStatus(ud.MISMATCH, "不一致")
    assert conv(mismatch).facts["updown"]["status"] == "unconfirmed"


def test_non_synthetic_job_is_never_sent(tmp_path):
    data = demo_bytes().replace(b"NOP", b"NOP ", 1)
    assert not sim._is_synthetic(data)
    c = cx.Conversation.start(data, None, None)
    assert c.facts is None and c.problem == cx.NOT_SYNTHETIC
    r = explainer(tmp_path, NoCall()).ask(c, "何をしている？")
    assert r["ok"] is False and r["error"] == cx.NOT_SYNTHETIC
    assert not (tmp_path / "api_usage.jsonl").exists()


# ---- 2. 答えの検査 ----
def ask_one(tmp_path, resp, updown=None, text="このロボット、何をしている？"):
    fake = FakeClient(resp)
    c = conv(updown)
    return explainer(tmp_path, fake).ask(c, text), c


def test_good_answer_is_shown_with_evidence_lines_and_their_text(tmp_path):
    r, c = ask_one(tmp_path, answer("Z方向の往復動作が2回あります。ボタンを押す動作として設定されていますか？",
                                    [21, 22, 23, 99, 19, 21, True]))
    assert r["ok"] and r["reply_by"] == "claude" and r["checked"] is None
    assert r["reply"].startswith("Z方向の往復動作が2回")
    # 99（無い行）・19（コメントの行。命令の範囲に無い）・重複・bool は捨てる
    assert [e["line"] for e in r["evidence"]] == [21, 22, 23]
    assert r["evidence"][0] == {"line": 21, "text": "MOVL C00001 V=150.0", "note": "C00001 へ直線で動く",
                                "positions": ["C00001"]}
    assert r["intent"] == "explain" and r["change_request"] is None and r["notes"] == []


@pytest.mark.parametrize("reply, fixed", [
    ("この動きは安全です。", cx.FIXED_FORBIDDEN),
    ("ここでは干渉しません。", cx.FIXED_FORBIDDEN),
    ("ワークに当たる位置かもしれません。", cx.FIXED_FORBIDDEN),
    ("Z方向の往復が7回あります。", cx.FIXED_NUMBER),
    ("およそ3.5秒かかります。", cx.FIXED_NUMBER),
    ("Z方向の往復が七回あります。", cx.FIXED_NUMBER),
    ("上下の往復動作が2回あります。", cx.FIXED_UPDOWN),
    ("C00002 でツールを下げています。", cx.FIXED_UPDOWN),
    ("MOVL C00002 V=30.0 でゆっくり動きます。", cx.FIXED_JOB_TEXT),
    ("C00002 は V=30.0 です。", cx.FIXED_JOB_TEXT),
    ("3回に変更しました。", cx.FIXED_CLAIM),
    ("", cx.FIXED_EMPTY),
    ("あ" * (cx.MAX_REPLY + 1), cx.FIXED_TOO_LONG),
])
def test_bad_reply_is_replaced_with_fixed_text(tmp_path, reply, fixed):
    r, c = ask_one(tmp_path, answer(reply, [21]))
    assert r["ok"] and r["reply"] == fixed and r["reply_by"] == "app" and r["checked"] == fixed
    # 次の要求で、表示しなかったことを Claude に伝える（tool_result の文はアプリが作る）
    assert c.pending_result == cx.RESULT_HIDDEN.format(why=fixed)


def test_updown_words_allowed_only_when_confirmed(tmp_path):
    r, _ = ask_one(tmp_path, answer("上下の往復動作が2回あります。", [21]), updown=confirmed())
    assert r["reply_by"] == "claude"
    # 「以下」「下さい」などは上下の言葉として数えない
    r, _ = ask_one(tmp_path, answer("以下の行を見て下さい。", [21]))
    assert r["reply_by"] == "claude"


def test_numbers_from_facts_and_user_are_allowed(tmp_path):
    # 位置番号は最初の説明では使わない（CHECKER_031 E1。tests/test_claude_talk.py）。2回目の答えで数の検査を確かめる
    fake = FakeClient(answer("同じ動きを2回くり返しています。", [21]),
                      answer("C00001〜C00003 の動きが、21〜26行目に2回あります。Zのマイナス45mm動きます。", [21],
                             tool_id="toolu_02"))
    ex, c = explainer(tmp_path, fake), conv()
    ex.ask(c, "このロボット、何をしている？")
    r = ex.ask(c, "くわしく教えて")
    # CHECKER_032 P1-4（唐川さん 11/3 夜）：移動量（数＋mm）は Claude の文に書かせない。事実の数と同じでも表示しない
    assert r["reply_by"] == "app" and r["reply"] == cx.FIXED_AMOUNT, r["reply"]
    r, _ = ask_one(tmp_path, answer("この動作を3回に変更しますか？", [21], "change_request",
                                    {"kind": "repeat", "from_count": 2, "to_count": 3}), text="そう。じゃあ3回にして")
    assert r["reply_by"] == "claude"


@pytest.mark.parametrize("resp, msg", [
    (text_only(), cx.UNREADABLE),
    (answer("x", stop="end_turn"), cx.UNREADABLE),
    (answer("x", stop="refusal"), cx.REFUSED),
    (answer("x", stop="max_tokens"), cx.TRUNCATED),
    (answer("x", intent="decide"), cx.UNREADABLE),
    (answer("x", extra={"approve": True}), cx.UNREADABLE),
    (answer("x", intent="change_request", change={"kind": "repeat", "from_count": 2, "to_count": 3, "apply": True}),
     cx.UNREADABLE),
    (answer("x", intent="change_request", change={"kind": "repeat", "from_count": "2", "to_count": 3}), cx.UNREADABLE),
    (answer("x", intent="change_request", change={"kind": "delete"}), cx.UNREADABLE),
    (SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", id="a", name="answer", input={}),
        SimpleNamespace(type="tool_use", id="b", name="answer", input={})], usage=usage()), cx.UNREADABLE),
    (SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", id="a", name="propose_change", input={})], usage=usage()), cx.UNREADABLE),
    (SimpleNamespace(stop_reason="tool_use", content="oops", usage=usage()), cx.UNREADABLE),
])
def test_unusable_answers_are_dropped_and_history_is_unchanged(tmp_path, resp, msg):
    r, c = ask_one(tmp_path, resp)
    assert r["ok"] is False and r["error"] == msg and "reply" not in r
    assert c.messages == [] and c.turns == 0 and c.pending_tool_id is None
    # 料金はかかったので記録する
    assert len((tmp_path / "api_usage.jsonl").read_text(encoding="utf-8").splitlines()) == 1


# ---- 3. change_request は記録と表示だけ ----
def test_change_request_shows_read_only_note(tmp_path):
    r, _ = ask_one(tmp_path, answer("この動作を3回に変更しますか？", [21, 24], "change_request",
                                    {"kind": "repeat", "from_count": 2, "to_count": 3}), text="そう。じゃあ3回にして")
    assert r["intent"] == "change_request"
    assert r["change_request"] == {"kind": "repeat", "from_count": 2, "to_count": 3}
    assert r["notes"] == [cx.READ_ONLY_NOTE]
    # intent が change_request でなければ、change_request は捨てる
    r, _ = ask_one(tmp_path, answer("Z方向の往復が2回あります。", [21], "explain", {"kind": "shift"}))
    assert r["change_request"] is None and r["notes"] == []


def web_flow(tmp_path, *responses):
    fake = FakeClient(*responses)
    w = WebFlow(interpreter=__import__("voice_teaching.claude_interpreter", fromlist=["x"]).MockInterpreter(),
                updown_config=None, explainer=explainer(tmp_path, fake))
    return w, fake


def test_change_request_changes_no_job_candidate_approval_or_log(tmp_path):
    w, fake = web_flow(tmp_path,
                       answer("Z方向の往復動作が2回あります。ボタンを押す動作として設定されていますか？", [21, 24]),
                       answer("この動作を3回に変更しますか？", [21, 24], "change_request",
                              {"kind": "repeat", "from_count": 2, "to_count": 3}, tool_id="toolu_02"))
    sid = w.load(demo_bytes())["session_id"]
    e = w._entries[sid]
    before_state = dump(w.state(sid)["state"])
    before_job = e.session.original_bytes
    r1 = w.ask(sid, "このロボット、何をしている？")
    r2 = w.ask(sid, "そう。じゃあ3回にして")
    assert r1["ok"] and r2["ok"] and r2["notes"] == [cx.READ_ONLY_NOTE]
    after = w.state(sid)["state"]
    assert dump(after) == before_state  # 候補・承認・選択・作業ログ・会話（位置の候補）がそのまま
    assert after["candidate"] is None and after["approval"] is None and after["can_approve"] is False
    assert e.session.candidate is None and e.approval is None and e.session.original_bytes == before_job
    assert len(fake.requests) == 2


def test_ask_while_candidate_pending_does_not_touch_candidate(tmp_path):
    w, fake = web_flow(tmp_path, answer("この動作を3回に変更しますか？", [21], "change_request",
                                        {"kind": "repeat", "from_count": 2, "to_count": 3}))
    sid = w.load(demo_bytes())["session_id"]
    w.select(sid, "C00004")
    w.hear(sid, "Zプラス方向に10mm", "text")
    st = w.state(sid)["state"]
    assert st["candidate"] is not None
    before = dump(st)
    assert w.ask(sid, "じゃあ3回にして")["ok"]
    assert dump(w.state(sid)["state"]) == before


# ---- 4. 会話の文脈（書き換えずに次へ） ----
def test_history_is_replayed_unchanged_and_tool_result_comes_first(tmp_path):
    r1 = answer("Z方向の往復動作が2回あります。ボタンを押す動作として設定されていますか？", [21])
    r2 = answer("この動作を3回に変更しますか？", [21], "change_request",
                {"kind": "repeat", "from_count": 2, "to_count": 3}, tool_id="toolu_02")
    fake = FakeClient(r1, r2)
    ex = explainer(tmp_path, fake)
    c = conv()
    ex.ask(c, "このロボット、何をしている？")
    ex.ask(c, "そう。じゃあ3回にして")
    m1, m2 = fake.requests[0]["messages"], fake.requests[1]["messages"]
    assert len(m1) == 1 and len(m2) == 3
    assert m2[0] is m1[0] and dump(m2[0]) == json.dumps(json.loads(fake.snapshots[0])[0], ensure_ascii=False)
    assert m2[1]["role"] == "assistant" and m2[1]["content"] is r1.content  # content のブロックそのまま
    assert m2[1]["content"][0].type == "thinking" and m2[1]["content"][0].signature == "sig-toolu_01"
    first, *rest = m2[2]["content"]
    assert first == {"type": "tool_result", "tool_use_id": "toolu_01", "content": cx.RESULT_SHOWN}
    assert [b["type"] for b in rest] == ["text"] and "job_facts" not in rest[0]["text"]  # 事実は最初の回だけ
    assert "job_facts" in m1[0]["content"][0]["text"]
    # system と tools は会話の間で変えない
    assert fake.requests[0]["system"] == fake.requests[1]["system"]
    assert fake.requests[0]["tools"] == fake.requests[1]["tools"]


def test_failed_turn_keeps_pending_tool_result_for_next_try(tmp_path):
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21]), text_only(),
                      answer("この動作を3回に変更しますか？", [21], "change_request",
                             {"kind": "repeat", "from_count": 2, "to_count": 3}, tool_id="toolu_03"))
    ex = explainer(tmp_path, fake)
    c = conv()
    ex.ask(c, "何をしている？")
    assert ex.ask(c, "3回にして")["error"] == cx.UNREADABLE
    assert len(c.messages) == 2
    ex.ask(c, "3回にして")
    m3 = fake.requests[2]["messages"]
    assert len(m3) == 3 and m3[2]["content"][0]["tool_use_id"] == "toolu_01"


def test_turn_limit_stops_without_calling(tmp_path):
    fake = FakeClient(*[answer("Z方向の往復が2回あります。", [21], tool_id=f"t{i}") for i in range(cx.MAX_TURNS)])
    ex = explainer(tmp_path, fake)
    c = conv()
    for _ in range(cx.MAX_TURNS):
        assert ex.ask(c, "何をしている？")["ok"]
    r = ex.ask(c, "何をしている？")
    assert r["ok"] is False and r["error"] == cx.TOO_MANY_TURNS and len(fake.requests) == cx.MAX_TURNS


def test_web_reset_and_new_job_start_new_conversation(tmp_path):
    w, fake = web_flow(tmp_path, answer("Z方向の往復が2回あります。", [21]), answer("Z方向の往復が2回あります。", [21]))
    sid = w.load(demo_bytes())["session_id"]
    w.ask(sid, "何をしている？")
    assert w._entries[sid].explain.turns == 1
    assert w.ask(sid, reset=True)["status"] == "reset"
    assert w._entries[sid].explain.turns == 0
    w.ask(sid, "何をしている？")
    assert len(fake.requests[1]["messages"]) == 1  # 前の会話は入れない


# ---- 5. 使用量と料金 ----
def test_cost_table_and_calculation():
    assert cx.PRICES[MODEL] == cx.Price(2.0, 10.0, 2.5, 4.0, 0.2)
    assert cx.cost_usd(MODEL, {"input_tokens": 1000, "output_tokens": 200}) == pytest.approx(0.004)
    u = {"input_tokens": 100, "output_tokens": 100, "cache_creation_input_tokens": 2000,
         "cache_read_input_tokens": 5000, "cache_write_1h_tokens": 0}
    assert cx.cost_usd(MODEL, u) == pytest.approx((100 * 2 + 100 * 10 + 2000 * 2.5 + 5000 * 0.2) / 1e6)
    assert cx.cost_usd("claude-haiku-5-5", u) is None
    assert cx.money(None) == "料金不明" and cx.money(0.0123) == "$0.0123"
    one_h = cx.usage_of(SimpleNamespace(usage=usage(10, 10, cw=300, one_h=100)))
    assert one_h["cache_write_1h_tokens"] == 100
    assert cx.cost_usd(MODEL, one_h) == pytest.approx((10 * 2 + 10 * 10 + 200 * 2.5 + 100 * 4.0) / 1e6)


def test_usage_jsonl_has_counts_only_and_accumulates(tmp_path):
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21], u=usage(1000, 200)),
                      answer("この動作を3回に変更しますか？", [21], "change_request",
                             {"kind": "repeat", "from_count": 2, "to_count": 3}, tool_id="t2", u=usage(1500, 100, cr=800)))
    ex = explainer(tmp_path, fake)
    c = conv()
    r1 = ex.ask(c, "このロボット、何をしている？")
    r2 = ex.ask(c, "そう。じゃあ3回にして")
    path = tmp_path / "api_usage.jsonl"
    text = path.read_text(encoding="utf-8")
    recs = [json.loads(x) for x in text.splitlines()]
    assert len(recs) == 2
    assert set(recs[0]) == {"at", "kind", "model", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                            "cache_read_input_tokens", "cost_usd", "estimated_worst_case"}
    for w in (KEY, "TESTKEY", "ロボット", "3回にして", "MOVL", "C0000", "reply", "Z方向"):
        assert w not in text
    assert r1["cost_usd"] == pytest.approx(0.004)
    assert r2["cost_usd"] == pytest.approx((1500 * 2 + 100 * 10 + 800 * 0.2) / 1e6)
    assert r2["total_cost_usd"] == pytest.approx(r1["cost_usd"] + r2["cost_usd"])
    assert r2["session_usage"]["calls"] == 2 and r2["session_usage"]["input_tokens"] == 2500
    assert cx.UsageLog(path).total() == pytest.approx(r2["total_cost_usd"])


def test_budget_over_does_not_call(tmp_path):
    path = tmp_path / "api_usage.jsonl"
    path.write_text(json.dumps({"cost_usd": 4.99}) + "\n", encoding="utf-8")
    ex = explainer(tmp_path, NoCall(), budget=5.0)
    r = ex.ask(conv(), "何をしている？")
    assert r["ok"] is False and r["status"] == "budget" and "上限" in r["error"]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("raw, want", [("", 5.0), ("1.5", 1.5), ("0", None), ("-1", None), ("abc", None),
                                       ("nan", None), ("inf", None)])
def test_budget_env(raw, want):
    assert cx.read_budget({cx.BUDGET_ENV: raw}) == want


def test_invalid_budget_unknown_price_or_unreadable_log_do_not_call(tmp_path):
    assert explainer(tmp_path, NoCall(), budget=None).ask(conv(), "x")["error"] == cx.BUDGET_INVALID
    assert explainer(tmp_path, NoCall(), model="claude-haiku-5-5").ask(conv(), "x")["error"] == cx.PRICE_UNKNOWN
    (tmp_path / "api_usage.jsonl").write_text("{broken\n", encoding="utf-8")
    assert explainer(tmp_path, NoCall()).ask(conv(), "x")["error"] == cx.USAGE_UNREADABLE


def test_missing_usage_is_recorded_as_worst_case(tmp_path):
    resp = answer("Z方向の往復が2回あります。", [21])
    resp.usage = None
    r, _ = ask_one(tmp_path, resp)
    assert r["estimated_worst_case"] is True and r["cost_usd"] > 0
    rec = json.loads((tmp_path / "api_usage.jsonl").read_text(encoding="utf-8"))
    assert rec["estimated_worst_case"] is True


def test_unwritable_usage_log_stops_further_calls(tmp_path):
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21]))
    class Unwritable(cx.UsageLog):
        def append(self, *a, **k):
            raise cx.ExplainError(cx.USAGE_UNWRITABLE)
    ex = cx.Explainer(KEY, MODEL, client=fake, usage_log=Unwritable(tmp_path / "u.jsonl"))
    c = conv()
    r = ex.ask(c, "何をしている？")
    assert r["usage_notice"] == cx.USAGE_UNWRITABLE
    assert ex.ask(c, "もう一度")["error"] == cx.USAGE_UNWRITABLE and len(fake.requests) == 1


# ---- 6. エラー ----
def _err(name, status=None, base=Exception):
    cls = type(name, (base,), {})
    e = cls(f"raw error with key {KEY}")
    if status is not None:
        e.status_code = status
    return e


APIStatusError = type("APIStatusError", (Exception,), {})


@pytest.mark.parametrize("ex, msg", [
    (_err("AuthenticationError", 401, APIStatusError), cx._ERRORS[cx.ci.AUTH]),
    (_err("NotFoundError", 404, APIStatusError), cx._ERRORS[cx.ci.MODEL_NOT_FOUND]),
    (_err("RateLimitError", 429, APIStatusError), cx._ERRORS[cx.ci.BUSY]),
    (_err("APITimeoutError"), cx._ERRORS[cx.ci.NETWORK]),
    (_err("APIConnectionError"), cx._ERRORS[cx.ci.NETWORK]),
    (_err("BadRequestError", 400, APIStatusError), cx._ERRORS[cx.ci.REJECTED]),
    (_err("APIStatusError", 402), cx.BILLING),
    (RuntimeError("boom " + KEY), cx._ERRORS[cx.ci.FAILED]),
])
def test_api_errors_change_nothing_and_hide_the_key(tmp_path, ex, msg):
    fake = FakeClient(ex)
    c = conv()
    r = explainer(tmp_path, fake).ask(c, "何をしている？")
    assert r["ok"] is False and r["error"] == msg and KEY not in dump(r)
    assert c.messages == [] and c.turns == 0
    assert not (tmp_path / "api_usage.jsonl").exists()


def test_no_key_does_not_create_a_client(tmp_path):
    class SDK:
        def Anthropic(self, **kw):  # pragma: no cover
            raise AssertionError("作らない")
    ex = cx.Explainer("", MODEL, sdk=SDK(), usage_log=cx.UsageLog(tmp_path / "u.jsonl"))
    r = ex.ask(conv(), "何をしている？")
    assert r["status"] == "no_key" and r["error"] == cx.NO_KEY == "APIキーが設定されていません（.env の ANTHROPIC_API_KEY）。Claude には聞けません"
    assert ex.info()["has_key"] is False and ex.info()["notice"] == cx.NO_KEY
    bad = cx.Explainer("見本のキー", MODEL, sdk=SDK(), usage_log=cx.UsageLog(tmp_path / "u.jsonl"))
    assert bad.ask(conv(), "x")["error"] == cx.KEY_UNUSABLE
    badm = cx.Explainer(KEY, "gpt-4", sdk=SDK(), usage_log=cx.UsageLog(tmp_path / "u.jsonl"))
    assert badm.ask(conv(), "x")["error"] == cx.MODEL_INVALID and "gpt" not in repr(badm)


def test_bad_text_is_refused_without_calling(tmp_path):
    ex = explainer(tmp_path, NoCall())
    assert ex.ask(conv(), "")["error"] == cx.NO_TEXT
    assert ex.ask(conv(), None)["error"] == cx.NO_TEXT
    assert ex.ask(conv(), "あ" * (cx.MAX_TEXT + 1))["error"] == cx.TEXT_TOO_LONG


def test_model_from_env_option_and_default():
    assert cx.Explainer.from_env({}).model == "claude-sonnet-5-5"
    assert cx.Explainer.from_env({"ANTHROPIC_MODEL": "claude-opus-5-5"}).model == "claude-opus-5-5"
    assert cx.Explainer.from_env({"ANTHROPIC_MODEL": "claude-opus-5-5"}, model="claude-sonnet-5-5").model == MODEL


# ---- 7. CLI ----
def cli(tmp_path, args, env=None, answer_text="", client=None):
    out = io.StringIO()
    asked = []

    def inp(prompt):
        asked.append(prompt)
        return answer_text

    def make(env_, model=None, usage_path=None):
        return cx.Explainer.from_env(env_, model=model, usage_path=usage_path, client=client or NoCall())
    code = cx.main(["--job", str(DEMO), "--usage-file", str(tmp_path / "u.jsonl"),
                    "--env-file", str(tmp_path / "none.env")] + args,
                   env=env if env is not None else {"ANTHROPIC_API_KEY": KEY}, out=out, input_fn=inp, make_explainer=make)
    return code, out.getvalue(), asked


def test_cli_without_call_only_shows_what_would_be_sent(tmp_path):
    code, text, asked = cli(tmp_path, ["--say", "このロボット、何をしている？", "--say", "そう。じゃあ3回にして"])
    assert code == 0 and asked == []
    assert "送る内容" in text and "job_facts" in text and "（伏せています）" in text
    assert "ANTHROPIC_API_KEY：あり" in text and KEY not in text
    assert "claude-sonnet-5-5" in text and "$2" in text
    assert not (tmp_path / "u.jsonl").exists()


def test_cli_without_key_shows_content_but_call_refuses(tmp_path):
    code, text, asked = cli(tmp_path, ["--say", "何をしている？"], env={})
    assert code == 0 and "ANTHROPIC_API_KEY：なし" in text and "送る内容" in text
    code, text, asked = cli(tmp_path, ["--say", "何をしている？", "--call"], env={})
    assert code == 2 and asked == [] and cx.NO_KEY in text


def test_cli_call_needs_yes(tmp_path):
    for a in ("", "no", "y", "YES please"):
        code, text, asked = cli(tmp_path, ["--say", "何をしている？", "--call"], answer_text=a)
        assert code == 3 and len(asked) == 1 and "送りませんでした" in text


def test_cli_call_with_yes_runs_the_conversation(tmp_path):
    fake = FakeClient(answer("Z方向の往復動作が2回あります。ボタンを押す動作として設定されていますか？", [21, 22, 23]),
                      answer("この動作を3回に変更しますか？", [21, 24], "change_request",
                             {"kind": "repeat", "from_count": 2, "to_count": 3}, tool_id="t2"))
    code, text, asked = cli(tmp_path, ["--say", "このロボット、何をしている？", "--say", "そう。じゃあ3回にして", "--call"],
                            answer_text="yes", client=fake)
    assert code == 0 and len(fake.requests) == 2
    assert "Claude（claude-sonnet-5-5）「Z方向の往復動作が2回あります。" in text
    assert "根拠 21行目：MOVL C00001 V=150.0（C00001 へ直線で動く）" in text
    assert cx.READ_ONLY_NOTE in text and '"to_count": 3' in text
    assert "推定料金 $0.0040" in text and "累計 $0.0080" in text
    assert KEY not in text


def test_cli_refuses_non_synthetic_job_without_showing_it(tmp_path):
    job = tmp_path / "REAL.JBI"
    job.write_bytes(demo_bytes().replace(b"NOP", b"NOP ", 1))
    out = io.StringIO()
    code = cx.main(["--job", str(job), "--say", "x", "--env-file", str(tmp_path / "n.env"),
                    "--usage-file", str(tmp_path / "u.jsonl")], env={}, out=out)
    assert code == 2 and cx.NOT_SYNTHETIC in out.getvalue() and "MOVL" not in out.getvalue()


def test_cli_reads_env_file(tmp_path):
    envf = tmp_path / "x.env"
    envf.write_text(f"ANTHROPIC_API_KEY={KEY}\nANTHROPIC_MODEL=claude-opus-5-5\n", encoding="utf-8")
    out = io.StringIO()
    cx.main(["--job", str(DEMO), "--say", "x", "--env-file", str(envf), "--usage-file", str(tmp_path / "u.jsonl")],
            env={}, out=out)
    assert "ANTHROPIC_API_KEY：あり" in out.getvalue() and "claude-opus-5-5" in out.getvalue()
    assert KEY not in out.getvalue()


# ---- 8. Web ----
@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeClient(answer("Z方向の往復が2回あります。", [21]))
    srv = server.make_server(port=0, explainer=explainer(tmp_path, fake))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, fake
    srv.shutdown()
    srv.server_close()


def post(srv, path, obj):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    body = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
    conn.request("POST", path, body=body)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_web_ask_route(running):
    srv, fake = running
    st, data = post(srv, "/api/load", demo_bytes())
    sid = json.loads(data)["session_id"]
    st, data = post(srv, "/api/ask", {"session_id": sid, "info": True})
    info = json.loads(data)
    assert info["ok"] and info["has_key"] is True and KEY.encode() not in data and "TESTKEY" not in data.decode()
    st, data = post(srv, "/api/ask", {"session_id": sid, "text": "このロボット、何をしている？"})
    r = json.loads(data)
    assert r["ok"] and r["reply"] == "Z方向の往復が2回あります。" and r["evidence"][0]["line"] == 21
    assert "session_usage" in r and "total_cost_usd" in r and KEY not in data.decode()
    st, data = post(srv, "/api/ask", {"session_id": "nope", "text": "x"})
    assert json.loads(data)["ok"] is False


def test_web_without_key_says_so_and_does_not_mock(tmp_path):
    w = WebFlow(updown_config=None, explainer=cx.Explainer("", MODEL, client=NoCall(),
                                                           usage_log=cx.UsageLog(tmp_path / "u.jsonl")))
    sid = w.load(demo_bytes())["session_id"]
    assert w.ask(sid, info=True)["notice"] == cx.NO_KEY
    r = w.ask(sid, "何をしている？")
    assert r["ok"] is False and r["error"].startswith("APIキーが設定されていません") and "reply" not in r


def test_web_non_synthetic_job_is_not_sent(tmp_path):
    w, fake = web_flow(tmp_path)
    data = demo_bytes().replace(b"NOP", b"NOP ", 1)
    sid = w.load(data)["session_id"]
    r = w.ask(sid, "何をしている？")
    assert r["ok"] is False and r["error"] == cx.NOT_SYNTHETIC and fake.requests == []


def test_static_ask_panel_is_read_only_and_uses_text_nodes():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "explain.js").read_text(encoding="utf-8")
    assert 'id="ask-panel"' in html and "Claude に聞く（読み取りだけ）" in html
    assert '<script type="module" src="/static/explain.js"></script>' in html
    assert "/api/ask" in js and "innerHTML" not in js
    for path in ("/api/approve", "/api/export", "/api/hear", "/api/select", "/api/dialog"):
        assert path not in js
    assert server._STATIC["/static/explain.js"][0] == "explain.js"
