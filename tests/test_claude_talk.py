"""Claude との会話（CHECKER_031 7章 E1〜E7・E2-1〜E2-7）の受入テスト。voice_teaching/claude_talk.py と web/flow.ask。

本物の API は呼ばない（偽の client だけ）。Claude の答えはテストで決める。Claude が意味を勝手に登録しようとしても、
登録するかはプログラムが人の文と事実で決めることを確かめる。区間が2つある合成ジョブは、このテストの中で作る。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_teaching import claude_explain as cx
from voice_teaching import claude_talk as talk
from voice_teaching import jbi, sim
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web import server
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"
OTHER = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
STATIC = ROOT / "voice_teaching" / "web" / "static"
KEY = "sk-ant-api03-TESTKEY-DO-NOT-SHOW-1234567890"
MODEL = "claude-sonnet-5-5"
ASKED = "何をする動きか、教えてもらえますか？"  # Claude の最初の問い（テストで決める）


def demo_bytes() -> bytes:
    return DEMO.read_bytes()


# 区間が2つある合成ジョブ（このテストの中だけで作る。samples には置かない）
TWO_BLOCKS = (
    "/JOB\r\n//NAME TALK_TWO_BLOCKS\r\n//POS\r\n///NPOS 7,0,0,0,0,0\r\n///TOOL 0\r\n///POSTYPE ROBOT\r\n///RECTAN\r\n"
    "///RCONF 0,0,0,0,0,0,0,0\r\n"
    "C00000=350.000,-60.000,420.000,180.00,0.00,0.00\r\n"
    "C00001=450.000,120.000,260.000,180.00,0.00,0.00\r\n"
    "C00002=450.000,120.000,215.000,180.00,0.00,0.00\r\n"
    "C00003=420.000,120.000,300.000,180.00,0.00,0.00\r\n"
    "C00004=450.000,0.000,260.000,180.00,0.00,0.00\r\n"
    "C00005=450.000,0.000,215.000,180.00,0.00,0.00\r\n"
    "C00006=420.000,0.000,300.000,180.00,0.00,0.00\r\n"
    "//INST\r\n///DATE 2026/11/03 20:00\r\n///ATTR SC,RW\r\n///GROUP1 RB1\r\nNOP\r\n"
    "MOVJ C00000 VJ=25.00\r\n"
    + "MOVL C00001 V=150.0\r\nMOVL C00002 V=30.0\r\nMOVL C00003 V=150.0\r\n" * 2
    + "MOVL C00004 V=150.0\r\nMOVL C00005 V=30.0\r\nMOVL C00006 V=150.0\r\n" * 2
    + "MOVJ C00000 VJ=25.00\r\nEND\r\n").encode("ascii")


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=lambda o: vars(o))


# ---- 偽の SDK ----
def usage():
    return SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0, cache_creation=None)


_ids = iter(range(1, 10_000))


def answer(reply, lines=(), intent="explain", change=None):
    tid = f"toolu_{next(_ids):04d}"
    data = {"reply": reply, "evidence_lines": list(lines), "intent": intent, "change_request": change}
    return SimpleNamespace(stop_reason="tool_use", usage=usage(), content=[
        SimpleNamespace(type="thinking", thinking="", signature="sig-" + tid),
        SimpleNamespace(type="tool_use", id=tid, name="answer", input=data)])


class FakeClient:
    """決めた答えを順に返す。答えが無いのに呼ばれたらテストが落ちる（呼ばない場面の確かめ）。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def add(self, *responses):
        self.responses.extend(responses)

    def create(self, **kw):
        self.requests.append(kw)
        assert self.responses, "Claude を呼んではいけない場面で呼んだ"
        return self.responses.pop(0)


def flow(tmp_path, *responses, updown_config=None):
    fake = FakeClient(*responses)
    ex = cx.Explainer(KEY, MODEL, client=fake, usage_log=cx.UsageLog(tmp_path / "api_usage.jsonl"))
    return WebFlow(interpreter=MockInterpreter(), updown_config=updown_config, explainer=ex), fake


@pytest.fixture
def synthetic_two(monkeypatch):
    """テストで作った区間が2つの合成ジョブも、合成ジョブとして Claude に送れるようにする（テストの中だけ）。"""
    real = sim._is_synthetic
    monkeypatch.setattr(sim, "_is_synthetic", lambda data: data == TWO_BLOCKS or real(data))


def repeat_to_three(w, sid):
    """2回→3回（統合のあと）：範囲は［この範囲を繰り返す］のボタンで決めてから候補になる（feat/repeat-count・唐川さんの決定）。"""
    h = w.hear(sid, "C00001からC00003の作業を2回から3回にして", "text")
    assert not h.get("candidate_made") and h["state"]["repeat_range"]
    return w.repeat_choose(sid, "yes", h["state"]["repeat_range"]["turn"])


def first_turn(w, fake, data=None, lines=(21, 22, 23, 24, 25, 26), reply="同じ動きをくり返しています。" + ASKED):
    """「このロボット、何をしている？」→ Claude が問いを返す、まで。"""
    sid = w.load(demo_bytes() if data is None else data)["session_id"]
    fake.add(answer(reply, lines))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["ok"] and r["reply_by"] == "claude", r
    return sid


def meanings(w, sid):
    return w.ask(sid, sync=True)["talk"]["meanings"]


def press(w, sid, choice="confirm:yes"):
    """「〜のことですか？」のボタンを押す（人がボタンで確かめる。Claude は呼ばない）。"""
    p = w.ask(sid, sync=True)["talk"]["pending"]
    assert p is not None
    return w.ask(sid, choose=choice, token=p["token"])


def taught_button(w, fake, sid, said="そう、ボタンを押しているんだよ"):
    """人が意味を言う → その区間を光らせて確かめる → ［この動きです］を押して登録（唐川さんの判断：必ずボタンで確かめる）。"""
    n = len(fake.requests)
    r = w.ask(sid, said)
    assert r["status"] == "ask_which" and len(fake.requests) == n, r  # 確かめる間は Claude を呼ばない
    assert [o["id"] for o in r["talk"]["pending"]["options"]] == ["confirm:yes", "confirm:no"]
    assert meanings(w, sid) == []  # 押すまで登録しない
    r = press(w, sid)
    assert r["ok"] and r["status"] == "taught" and r["taught_note"], r
    return r


# ======== E1 やさしい言葉 ========
@pytest.mark.parametrize("reply", [
    "C00001〜C00003 の直線移動を2回くり返しています。",
    "MOVL で2回くり返しています。",
    "このジョブは同じ動きをくり返しています。",
    "この JOB は同じ動きをくり返します。",
    "C00004 へ関節移動で戻ります。",
])
def test_e1_first_reply_with_numbers_or_commands_becomes_plain_fixed_text(tmp_path, reply):
    w, fake = flow(tmp_path, answer(reply, [21, 22, 23]))
    sid = w.load(demo_bytes())["session_id"]
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["ok"] and r["reply_by"] == "app" and r["first_turn"] is True
    assert r["reply"] == talk.PLAIN_ONE.format(count=2) == "同じ動きを2回くり返しています。何をする動きか、教えてもらえますか？"  # アプリの文（数はプログラムが数えた）
    # 作り直しは頼まない（1回だけ呼ぶ）。番号と命令は、プログラムが根拠の行から補足として出す
    assert len(fake.requests) == 1
    assert r["supplement"] == ["21〜26行目：C00001→C00002→C00003（2回）"]
    assert [e["line"] for e in r["evidence"]] == [21, 22, 23]
    assert w._entries[sid].explain.pending_result == cx.RESULT_HIDDEN.format(why=cx.PLAIN_HIDDEN)


def test_e1_plain_first_reply_is_shown_and_fixed_text_has_no_numbers_commands_or_updown():
    for facts in ({"repeats": [{"count": 2}]}, {"repeats": [{"count": 2}, {"count": 2}]}, {"repeats": []}):
        t = talk.plain_fixed(facts)
        assert not talk._CODE_WORDS.search(t) and "上" not in t and "下" not in t and t.count("？") == 1


def test_e1_plain_first_reply_from_claude_is_used(tmp_path):
    w, fake = flow(tmp_path, answer("待機の位置から動いて、同じ動きをくり返しています。何をする動きですか？", [21]))
    sid = w.load(demo_bytes())["session_id"]
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "claude" and r["reply"].startswith("待機の位置から")


@pytest.mark.parametrize("reply, fixed", [
    ("何をする動きですか？ 目的は何ですか？", talk.FIXED_QUESTIONS),
    ("ボタンですか?スイッチですか?", talk.FIXED_QUESTIONS),
])
def test_e1_two_questions_in_one_reply_are_not_used_on_any_turn(tmp_path, reply, fixed):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer(reply, [21]))
    r = w.ask(sid, "くわしく教えて")
    assert r["reply"] == fixed and r["reply_by"] == "app"


def test_e1_job_word_is_not_used_later_but_positions_are_allowed_after_first(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("このジョブは2回くり返します。", [21]))
    assert w.ask(sid, "くわしく")["reply"] == talk.FIXED_JOB_WORD
    fake.add(answer("C00001〜C00003 の動きをくり返しています。", [21]))
    # 唐川さんの方針③（11/4）：MVP では、最初の説明のあとも Claude の自由文に位置番号を含めない（番号はアプリの補足が出す）
    assert w.ask(sid, "番号で言うと？")["reply"] == talk.FIXED_NO_NUMBER


def test_e1_updown_unconfirmed_plain_words_with_down_are_not_used(tmp_path):
    w, fake = flow(tmp_path, answer("待機の位置から下りて戻る動きをくり返しています。", [21]))
    sid = w.load(demo_bytes())["session_id"]
    assert w.ask(sid, "何をしている？")["reply"] == cx.FIXED_UPDOWN


def test_e1_system_has_the_rules():
    for w in ("位置番号（C00001 など）・命令の名前（MOVL・MOVJ など）", "「ジョブ」「JOB」とは言わず", "動作データ",
              "質問は1回の返事に1つまで", "taught_by_human", "change_state", "決めつけず"):
        assert w in cx.SYSTEM, w


# ======== E2-7 登録の条件 ========
@pytest.mark.parametrize("said", ["そう", "うん", "うん、そうだね", "はい", "そうそう", "OK", "そう。"])
def test_e2_7_backchannel_alone_does_not_register(tmp_path, said):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("ありがとうございます。何をする動きか、教えてもらえますか？", [21]))
    r = w.ask(sid, said)
    assert r["ok"] and r["taught_note"] is None and meanings(w, sid) == []
    assert "taught_by_human" not in dump(fake.requests[-1]["messages"][-1])


def test_e2_7_claude_cannot_register_by_saying_so(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("ボタンを押す作業として覚えました。", [21]))
    r = w.ask(sid, "うん、そうだね")
    assert r["reply"] == cx.FIXED_CLAIM and meanings(w, sid) == []


def test_e2_7_concrete_meaning_in_own_words_is_registered_from_human_text(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    r = taught_button(w, fake, sid)
    assert r["taught_note"] == ("分かりました。この動き（21〜26行目のくり返し）を「ボタンを押している」として、"
                                "この会話の中で覚えます")
    m = meanings(w, sid)
    assert [(x["text"], x["lines"], x["mark"], x["by"]) for x in m] == [
        ("ボタンを押している", [21, 22, 23, 24, 25, 26], talk.HUMAN_MARK, "human")]
    # 登録した意味は、次の要求から事実として渡す（人が教えた・この会話の中だけ）。Claude の言い換えは登録しない
    fake.add(answer("スイッチを操作する動きですね。", [22]))
    w.ask(sid, "くわしく")
    sent = dump(fake.requests[-1]["messages"][-1])
    assert "taught_by_human" in sent and "ボタンを押している" in sent and "21〜26行目の動き" in sent
    assert [x["text"] for x in meanings(w, sid)] == ["ボタンを押している"]


def test_e2_meaning_is_used_in_the_next_answer(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    fake.add(answer("ボタンを押す動作を2回から3回に変更しますか？", [21, 24], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ3回にして")
    # CHECKER_033 P1-A（唐川さんの方針② 11/4）：変更の会話はプログラムの定型文（Claude の文は出さない）
    assert r["reply_by"] == "app" and "2回から3回" in r["reply"] and "受け取りました" in r["reply"]
    assert r["notes"] == [cx.READ_ONLY_NOTE]
    rec = r["change_record"]
    assert rec["program_total"] == 3 and rec["claude_total"] == 3 and rec["mismatch"] is False
    assert rec["target"] == "21〜26行目：C00001→C00002→C00003（2回）"
    msg = fake.requests[-1]["messages"][-1]
    assert "ボタンを押している" in dump(msg)
    # 前のメッセージは書き換えない（同じ物をそのまま送る）
    assert fake.requests[-1]["messages"][0] is fake.requests[0]["messages"][0]
    assert w._entries[sid].session.candidate is None


def test_e2_7_two_blocks_ask_which_with_buttons_then_register(tmp_path, synthetic_two):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=TWO_BLOCKS, lines=())
    n = len(fake.requests)
    r = w.ask(sid, "ボタンを押してる")
    assert r["status"] == "ask_which" and r["reply"] == talk.ASK_WHICH_BLOCK and r["reply_by"] == "app"
    assert len(fake.requests) == n  # 聞き返しは Claude を呼ばない
    p = r["talk"]["pending"]
    assert [o["label"] for o in p["options"]] == ["22〜27行目：C00001→C00002→C00003（2回）",
                                                  "28〜33行目：C00004→C00005→C00006（2回）"]
    assert meanings(w, sid) == []
    # 声や文字で「2つ目」と言っても登録しない（ボタンだけ）
    fake.add(answer("どちらの動きですか？", []))
    w.ask(sid, "2つ目", source="voice")
    assert meanings(w, sid) == []
    # 古い番号では使わない
    assert w.ask(sid, choose="block:1", token=p["token"] + 1)["reply"] == talk.STALE_CHOICE
    r = w.ask(sid, choose="block:1", token=p["token"])
    assert r["status"] == "taught" and len(fake.requests) == n + 1
    assert [(x["text"], x["lines"][0], x["lines"][-1]) for x in meanings(w, sid)] == [("ボタンを押してる", 28, 33)]
    assert w.ask(sid, choose="block:0", token=p["token"])["reply"] == talk.STALE_CHOICE  # 1回だけ


def test_e2_7_two_blocks_evidence_in_one_block_registers_that_block(tmp_path, synthetic_two):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=TWO_BLOCKS, lines=(28, 29, 30))
    r = w.ask(sid, "ボタンを押してるんだよ")
    assert r["status"] == "ask_which" and r["highlight"]["lines"] == list(range(28, 34)) and meanings(w, sid) == []
    assert "28〜33行目のくり返し" in r["reply"]
    press(w, sid)
    assert [x["lines"][0] for x in meanings(w, sid)] == [28]


def test_e2_7_evidence_across_blocks_or_outside_asks(tmp_path, synthetic_two):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=TWO_BLOCKS, lines=(22, 28))
    assert w.ask(sid, "ボタンを押してる")["status"] == "ask_which"
    w2, fake2 = flow(tmp_path)
    sid2 = first_turn(w2, fake2, lines=(20, 21))  # 区間の外の行（20行目）も話題：区間は1つなので、その区間か確かめる
    r = w2.ask(sid2, "ボタンを押してる")
    assert r["status"] == "ask_which" and [o["id"] for o in r["talk"]["pending"]["options"]] == ["confirm:yes", "confirm:no"]
    assert meanings(w2, sid2) == []


def test_e2_7_this_is_button_unknown_point_asks_then_registers_point(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    n = len(fake.requests)
    r = w.ask(sid, "これはボタン")
    assert r["status"] == "ask_which" and r["reply"] == talk.ASK_WHICH_POINT and len(fake.requests) == n
    p = r["talk"]["pending"]
    assert [o["id"] for o in p["options"]] == ["pos:C00001", "pos:C00002", "pos:C00003"]
    assert meanings(w, sid) == []
    r = w.ask(sid, choose="pos:C00002", token=p["token"])
    assert r["status"] == "taught"
    assert [(x["text"], x["position"]) for x in meanings(w, sid)] == [("ボタン", "C00002")]


@pytest.mark.parametrize("said, why", [
    ("ボタンを押しているんだよ、たぶん", talk.HESITANT.format(word="たぶん")),
    ("ボタンを押してるかも", talk.HESITANT.format(word="かも")),
    ("ボタンを押してない", talk.NEGATED.format(word="押してない")),
    ("安全な位置だよ", talk.FORBIDDEN_MEANING),
])
def test_e2_7_hesitation_negation_forbidden_do_not_register_and_do_not_call(tmp_path, said, why):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    n = len(fake.requests)
    r = w.ask(sid, said)
    assert r["status"] == "not_taught" and r["reply"] == why and r["reply_by"] == "app"
    assert len(fake.requests) == n and meanings(w, sid) == []


def test_e2_7_correction_replaces_previous_meaning(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    n = len(fake.requests)
    r = w.ask(sid, "ボタンじゃなくてスイッチ")
    assert r["status"] == "ask_which" and len(fake.requests) == n
    assert r["reply"] == talk.CONFIRM_REPLACE.format(old="ボタンを押している", new="スイッチを押している",
                                                     target="21〜26行目の動き（C00001→C00002→C00003）")
    assert [x["text"] for x in meanings(w, sid)] == ["ボタンを押している"]  # 押すまで置き換えない
    assert r["change_record"] is None and r["change_request"] is None  # 変更の指示の打ち消しとは混ぜない
    r = press(w, sid, "replace:yes")
    assert r["taught_note"].startswith("「ボタンを押している」を「スイッチを押している」に置き換えました")
    assert [x["text"] for x in meanings(w, sid)] == ["スイッチを押している"]
    fake.add(answer("スイッチを押す動作ですね。", [22]))
    w.ask(sid, "くわしく")
    sent = dump(fake.requests[-1]["messages"][-1])
    assert "スイッチを押している" in sent and "ボタンを押している" not in sent


def test_e2_7_correction_not_confirmed_keeps_old(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    w.ask(sid, "ボタンじゃなくてスイッチ")
    assert press(w, sid, "replace:no")["reply"] == talk.NOT_REPLACED
    assert [x["text"] for x in meanings(w, sid)] == ["ボタンを押している"]


def test_e2_7_correction_without_previous_meaning_is_not_registered(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    r = w.ask(sid, "ボタンじゃなくてスイッチ")
    assert r["reply"] == talk.NO_OLD_MEANING.format(old="ボタン") and meanings(w, sid) == []


# ======== E3 事実と合わない意味 ========
@pytest.mark.parametrize("said, msg", [
    ("ボタンは C00004 だよ", talk.NOT_IN_BLOCK.format(pos="C00004", target="21〜26行目の動き（C00001→C00002→C00003）")),
    ("ボタンは C00009 だよ", talk.NOT_IN_JOB.format(pos="C00009")),
    ("ボタンは C00001 と C00002", talk.TWO_POSITIONS),
])
def test_e3_meaning_against_facts_is_not_taken(tmp_path, said, msg):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    r = w.ask(sid, said)
    assert r["reply"] == msg and meanings(w, sid) == []


def test_e3_point_in_the_block_is_taken(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    r = w.ask(sid, "ボタンは C00002 だよ")
    assert r["status"] == "ask_which" and meanings(w, sid) == []
    assert [o["label"] for o in r["talk"]["pending"]["options"]] == [talk.CONFIRM_POINT_YES, talk.CONFIRM_POINT_NO]
    assert press(w, sid)["taught_note"]
    assert [(x["text"], x["position"]) for x in meanings(w, sid)] == [("ボタン", "C00002")]


# ======== E2-1・E2-2 読み直し ========
def _with_name(data: bytes, name: bytes) -> bytes:
    return data.replace(b"//NAME DEMO_BUTTON_REPEAT", b"//NAME " + name)


@pytest.mark.parametrize("data", [
    demo_bytes(),
    _with_name(demo_bytes(), b"RENAMED_JOB"),                       # ファイル名・ジョブ名を変えた
    demo_bytes().replace(b"\r\n", b"\n"),                            # 改行コードだけ違う
    demo_bytes().replace(b"2026/11/03 20:00", b"2026/11/04 09:30"),  # 日付の行だけ違う
])
def test_e2_1_same_content_reload_keeps_meaning(tmp_path, data):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    sid2 = w.load(data, previous=sid)["session_id"]
    assert [x["text"] for x in meanings(w, sid2)] == ["ボタンを押している"]


@pytest.mark.parametrize("data", [
    demo_bytes().replace(b"MOVL C00002 V=30.0", b"MOVL C00002 V=31.0", 1),  # 名前は同じで中身が違う
    OTHER.read_bytes(),                                                     # 別のデータ
])
def test_e2_1_e2_2_different_content_clears_meaning(tmp_path, data):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    sid2 = w.load(data, previous=sid)["session_id"]
    assert meanings(w, sid2) == []


def test_e2_1_new_screen_without_previous_starts_empty(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    assert meanings(w, w.load(demo_bytes())["session_id"]) == []


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeClient()
    ex = cx.Explainer(KEY, MODEL, client=fake, usage_log=cx.UsageLog(tmp_path / "api_usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, fake
    srv.shutdown()
    srv.server_close()


def post(srv, path, obj, headers=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    body = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
    conn.request("POST", path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = json.loads(r.read())
    conn.close()
    return data


def test_e2_1_e2_2_reload_and_sample_over_http(running):
    srv, fake = running
    sid = post(srv, "/api/load", demo_bytes())["session_id"]
    fake.add(answer("同じ動きをくり返しています。" + ASKED, [21]))
    post(srv, "/api/ask", {"session_id": sid, "text": "このロボット、何をしている？"})
    r = post(srv, "/api/ask", {"session_id": sid, "text": "そう、ボタンを押しているんだよ", "source": "voice"})
    assert r["status"] == "ask_which" and r["source"] == "voice"
    r = post(srv, "/api/ask", {"session_id": sid, "choose": "confirm:yes", "token": r["talk"]["pending"]["token"]})
    assert r["taught_note"]
    sid2 = post(srv, "/api/load", demo_bytes().replace(b"\r\n", b"\n"),
                {"X-VT-Previous-Session": sid})["session_id"]
    assert [x["text"] for x in post(srv, "/api/ask", {"session_id": sid2, "sync": True})["talk"]["meanings"]] \
        == ["ボタンを押している"]
    # 「サンプルで試す」は別のサンプル（DEMO_C_PLACE）：消える
    sid3 = post(srv, "/api/sample", {}, {"X-VT-Previous-Session": sid2})["session_id"]
    assert post(srv, "/api/ask", {"session_id": sid3, "sync": True})["talk"]["meanings"] == []


# ======== E2-3・E2-4 変更の候補・承認のあと ========
def _candidate(new_job):
    return SimpleNamespace(kind="shift", new_job=new_job)


def test_e2_3_shift_candidate_keeps_meaning_when_numbers_and_lines_are_same(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    e = w._entries[sid]
    sp = sim.simulate_shift(e.session.original, "C00002", "Z", 5.0)  # 位置の値は変わる・番号と行の並びは同じ
    e.session.candidate = _candidate(jbi.parse_bytes(sp.new_bytes))
    r = w.ask(sid, sync=True)
    assert r["talk"]["notices"] == [talk.DATA_CHANGED]  # 会話を区切った
    assert [x["text"] for x in r["talk"]["meanings"]] == ["ボタンを押している"]
    e.approval = SimpleNamespace(approved_by="テスト")  # 承認した（状態が変わった）：確かめ直して、残る
    assert [x["text"] for x in meanings(w, sid)] == ["ボタンを押している"]


def test_e2_3_candidate_that_breaks_the_block_invalidates_meaning_and_says_so(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    e = w._entries[sid]
    broken = jbi.parse_bytes(demo_bytes().replace(b"MOVL C00002 V=30.0\r\nMOVL C00003 V=150.0\r\nMOVJ",
                                                  b"MOVL C00002 V=31.0\r\nMOVL C00003 V=150.0\r\nMOVJ"))
    e.session.candidate = _candidate(broken)
    r = w.ask(sid, sync=True)
    assert talk.INVALIDATED.format(text="ボタンを押している") in r["talk"]["notices"]
    assert "さきほど教えてもらった意味（ボタンを押している）を確認できなくなりました。もう一度教えてください" in r["talk"]["notices"]
    assert r["talk"]["meanings"] == []
    assert w.ask(sid, sync=True)["talk"]["notices"] == []  # 知らせは1回だけ
    # 次の要求には、無効にした意味を入れない
    e.session.candidate = None
    fake.add(answer("同じ動きをくり返しています。", [21]))
    w.ask(sid, "何をしている？")
    assert "taught_by_human" not in dump(fake.requests[-1]["messages"])


def test_e2_4_repeat_two_to_three_approved_keeps_meaning_on_all_cycles(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    h = repeat_to_three(w, sid)
    assert h["candidate_made"] is True
    m = meanings(w, sid)
    assert [(x["text"], x["lines"][0], x["lines"][-1]) for x in m] == [("ボタンを押している", 21, 29)]
    assert "3回" in m[0]["target"] or m[0]["target"] == "21〜29行目の動き（C00001→C00002→C00003）"
    assert w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))["ok"] is True
    assert [x["lines"][-1] for x in meanings(w, sid)] == [29]
    # 元に戻しても残る（元のデータにも同じ区間がある）
    w.undo(sid)
    assert [x["lines"][-1] for x in meanings(w, sid)] == [26]


# ======== E2-5 意味と安全の分離 ========
@pytest.mark.parametrize("steps", [
    [("hear", "C00001からC00003の作業を2回から3回にして"), ("approve", "テスト")],
    [("select", "C00002"), ("hear", "Zプラス方向に3mm"), ("approve", "テスト")],
    [("select", "C00002"), ("hear", "Zプラス方向に30mm")],
    [("hear", "C00001からC00003の作業を2回から4回にして")],
    [("select", "C00004"), ("hear", "3mm下げて")],
])
def test_e2_5_meaning_does_not_change_hear_results(tmp_path, steps):
    def run(teach: bool):
        w, fake = flow(tmp_path)
        sid = first_turn(w, fake)
        if teach:
            taught_button(w, fake, sid)
            assert meanings(w, sid)
        outs = []
        for kind, arg in steps:
            r = {"hear": lambda a: w.hear(sid, a, "text"), "select": lambda a: w.select(sid, a),
                 "approve": lambda a: w.approve(sid, a, candidate_gen=w.candidate_gen_of(sid))}[kind](arg)
            r.get("state", {}).pop("session_id", None)
            for item in (r.get("state") or {}).get("log", []):
                item.pop("at", None)
            outs.append(r)
        import re
        return re.sub(r"#[0-9a-f]{16}", "#番号", dump(outs))  # d（11/5）：向きの確かめのボタンの番号は毎回違う（比べるときは除く）
    assert run(True) == run(False)


# ======== E2-6 保存しない ========
def test_e2_6_meaning_is_not_written_anywhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert [p.name for p in files] == ["api_usage.jsonl"]
    assert "ボタン" not in files[0].read_text(encoding="utf-8")
    st = w.state(sid)["state"]
    assert "ボタン" not in dump(st)  # 画面の状態（作業ログを含む）にも入れない。/api/ask の答えにだけ出す
    js = (STATIC / "explain.js").read_text(encoding="utf-8")
    assert "localStorage" not in js and "sessionStorage" not in js and "indexedDB" not in js
    # app.js が覚えるのは音声の方式（MODE_KEY）だけ
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    import re
    assert set(re.findall(r"localStorage\.\w+\(([^,)]*)", app)) == {"MODE_KEY"}
    assert "sessionStorage" not in app and "indexedDB" not in app


# ======== E4 3D で自動で光らせる ========
def test_e4_highlight_is_decided_from_line_numbers(tmp_path):
    w, fake = flow(tmp_path, answer("同じ動きをくり返しています。", [22]))
    sid = w.load(demo_bytes())["session_id"]
    r = w.ask(sid, "何をしている？")
    assert r["highlight"] == {"lines": [21, 22, 23, 24, 25, 26], "positions": ["C00001", "C00002", "C00003"], "context_lines": [], "entry_lines": [21]}
    assert r["supplement"] == ["21〜26行目：C00001→C00002→C00003（2回）"]


def test_e4_movj_line_and_lines_not_in_job(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("最後は待機の位置へ戻ります。", [27, 99, 3]))
    r = w.ask(sid, "最後は？")
    assert r["highlight"] == {"lines": [27], "positions": ["C00004"], "context_lines": [], "entry_lines": []}
    assert r["supplement"] == ["27行目：MOVJ C00004 VJ=25.00（C00004 へ動く）"]
    fake.add(answer("ありません。", [99]))
    r = w.ask(sid, "ほかは？")
    assert r["highlight"] == {"lines": [], "positions": [], "context_lines": [], "entry_lines": []} and r["supplement"] == []


def test_e4_highlight_does_not_come_from_claude_text(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("C00004 の動きです。", []))  # 文に位置番号があっても、根拠の行が無ければ光らせない
    assert w.ask(sid, "どこ？")["highlight"] == {"lines": [], "positions": [], "context_lines": [], "entry_lines": []}


def test_e4_static_auto_highlight_and_movj_look():
    js = (STATIC / "explain.js").read_text(encoding="utf-8")
    assert "r.highlight" in js and "vt:highlight" in js
    v3 = (STATIC / "view3d.js").read_text(encoding="utf-8")
    assert "askHighlightLook" in v3
    hl = (STATIC / "highlight.js").read_text(encoding="utf-8")
    assert "export function askHighlightLook" in hl


# ======== E5 読み取り専用の範囲 ========
def test_e5_change_request_with_count_mismatch_is_recorded_and_not_shown(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    # Claude の文に数が無くても（数の検査には通る）、change_request の数が人の文と違えば使わない
    fake.add(answer("この動作の回数を変更しますか？", [21], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 4}))
    r = w.ask(sid, "そう。じゃあ3回にして")
    assert r["reply"] == talk.COUNT_MISMATCH and r["reply_by"] == "app"
    rec = r["change_record"]
    assert rec["program_total"] == 3 and rec["claude_total"] == 4 and rec["mismatch"] is True
    assert rec["note"] == talk.CHANGE_RECORD_NOTE
    assert w._entries[sid].session.candidate is None and w.state(sid)["state"]["candidate"] is None


def test_e5_pending_candidate_explain_only(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    w.select(sid, "C00004")
    assert w.hear(sid, "Zプラス方向に10mm", "text")["candidate_made"]
    before = dump(w.state(sid)["state"])
    n = len(fake.requests)
    for said in ("じゃあ3回にして", "C00002を3mm下げて", "承認して"):
        r = w.ask(sid, said)
        assert r["reply"] == talk.PENDING_EXPLAIN_ONLY and r["status"] == "explain_only"
    assert len(fake.requests) == n  # Claude には送らない
    # 説明の質問は送る。Claude が change_request を返しても受けない
    fake.add(answer("この動作を3回に変更しますか？", [21], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply"] == talk.PENDING_EXPLAIN_ONLY and r["change_request"] is None and r["notes"] == []
    first = fake.requests[-1]["messages"][0]
    facts = json.loads(first["content"][0]["text"])["job_facts"]
    assert facts["change_state"]["pending_candidate"] is True  # 新しい会話の事実に、承認待ちの候補があることを入れる
    assert dump(w.state(sid)["state"]) == before


def test_e5_data_change_separates_conversation(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    assert repeat_to_three(w, sid)["candidate_made"]
    assert w.ask(sid, sync=True)["talk"]["notices"] == [talk.DATA_CHANGED]
    fake.add(answer("同じ動きをくり返しています。", [21]))
    w.ask(sid, "何をしている？")
    assert len(fake.requests[-1]["messages"]) == 1  # 前の会話を入れない（新しい内容で答える）
    w.undo(sid)
    assert w.ask(sid, sync=True)["talk"]["notices"] == [talk.DATA_CHANGED]
    # 読み直し：前の画面は閉じる（唐川さんの決定 Q-7。11/5）。古い番号では会話の知らせも返さず、断る
    sid2 = w.load(demo_bytes(), previous=sid)["session_id"]
    r = w.ask(sid, sync=True)
    assert r["ok"] is False and r["session_closed"] is True and "talk" not in r
    assert w.ask(sid2, sync=True)["ok"] is True


def test_e5_affirmative_in_ask_panel_confirms_nothing(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=OTHER.read_bytes(), lines=(), reply="物を運ぶような動きです。" + ASKED)
    d = w.hear(sid, "置くところを少し手前にして", "text")
    st0 = w.state(sid)["state"]
    assert st0["dialog"]["step"] == "confirm_position"  # 指示の欄では「そう」で位置を選べる段（説明の欄では使わない）
    before = dump(st0)
    fake.add(answer("はい。", []), answer("はい。", []))
    for said in ("そう", "そうそう"):
        w.ask(sid, said, source="voice")
    after = w.state(sid)["state"]
    assert dump(after) == before and after["selected"] == st0["selected"] and after["approval"] is None
    assert d["ok"]


def test_e5_two_blocks_change_request_target_is_not_decided(tmp_path, synthetic_two):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=TWO_BLOCKS, lines=())
    fake.add(answer("どちらの動きを3回に変更しますか？", [], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "全部3回にして")
    rec = r["change_record"]
    assert rec["target"] is None and rec["target_note"] == talk.COUNT_NO_TARGET and rec["program_total"] == 3
    assert w._entries[sid].session.candidate is None


# ======== E6 音声と文字 ========
SAYINGS = ["そう", "うん、そうだね", "そう、ボタンを押しているんだよ", "ボタンを押しているんだよ、たぶん", "ボタンを押してない",
           "3回にしないで", "そう、じゃなくて4回", "ボタンは C00004 だよ", "これはボタン", "フガフガを3mm下げて", "承認"]


@pytest.mark.parametrize("said", SAYINGS)
def test_e6_voice_and_text_go_through_the_same_checks(tmp_path, said):
    def run(source):
        w, fake = flow(tmp_path)
        sid = first_turn(w, fake)
        fake.add(answer("分かりました。", [22]))
        r = w.ask(sid, said, source=source)
        st = w.state(sid)["state"]
        r.pop("source")
        for k in ("session_usage", "total_cost_usd", "usage", "cost_usd"):
            r.pop(k, None)
        return dump(r), meanings(w, sid), st["candidate"], st["approval"], st["can_export"]
    v, t = run("voice"), run("text")
    assert v == t
    assert v[2] is None and v[3] is None and v[4] is False  # 音声だけで確定・承認しない


def test_e6_unknown_source_is_refused(tmp_path):
    w, fake = flow(tmp_path)
    sid = w.load(demo_bytes())["session_id"]
    assert w.ask(sid, "何をしている？", source="robot")["ok"] is False and fake.requests == []


# ======== E7 意地悪な会話 ========
@pytest.mark.parametrize("said", ["そう、じゃなくて4回", "3回にしないで", "たぶん3回"])
def test_e7_negated_or_hesitant_counts_are_not_recorded_and_not_sent(tmp_path, said):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    n = len(fake.requests)
    r = w.ask(sid, said)
    assert r["status"] == "count_refused" and r["change_record"] is None and r["change_request"] is None
    assert "3回に" not in r["reply"] and "4回に" not in r["reply"]
    assert len(fake.requests) == n and meanings(w, sid) == []


@pytest.mark.parametrize("claude_to, mismatch", [(3, True), (2, False), (None, False)])
def test_e7_keep_two_times(tmp_path, claude_to, mismatch):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("2回のままにしますか？", [21], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": claude_to}))
    r = w.ask(sid, "やっぱり2回のまま")
    rec = r["change_record"]
    assert rec["program_keep"] is True and rec["program_total"] == 2 and rec["mismatch"] is mismatch
    assert (r["reply"] == talk.COUNT_MISMATCH) is mismatch
    assert w._entries[sid].session.candidate is None


def test_e7_correction_is_not_a_change_negation_and_all_three_two_blocks(tmp_path, synthetic_two):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, data=TWO_BLOCKS, lines=(22, 23))
    taught_button(w, fake, sid)
    r = w.ask(sid, "ボタンじゃなくてスイッチ")
    assert r["change_record"] is None
    press(w, sid, "replace:yes")
    assert [x["text"] for x in meanings(w, sid)] == ["スイッチを押している"]
    fake.add(answer("どちらの動きを3回にしますか？", [], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "全部3回にして")
    assert r["change_record"]["target"] is None and [x["text"] for x in meanings(w, sid)] == ["スイッチを押している"]


def test_e7_claude_tries_to_set_meaning_on_backchannel(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("ボタンを押す動作ですね。", [22]))
    r = w.ask(sid, "そう")
    assert r["reply_by"] == "claude" and meanings(w, sid) == []  # 文は出しても、意味は登録しない
    # CHECKER_032 K04：人が教えていない目的の言葉（ボタン・押す）は「AI の推定」の印を付ける（事実のように出さない）
    assert r["guesses"] == ["ボタンを押す動作ですね。"]


def test_ai_guess_is_shown_separately_and_not_registered(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("ボタンを押す動きのようです。合っていますか？", [22]))
    r = w.ask(sid, "くわしく")
    assert r["guesses"] == ["ボタンを押す動きのようです。"] and meanings(w, sid) == []
    t = r["talk"]
    assert t["human_mark"] == "ユーザー登録情報（人が教えた・この会話の中だけ）" and t["ai_mark"] == "AI の推定（未確定）"  # 11/4 の決定


def test_reset_clears_meaning(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    taught_button(w, fake, sid)
    assert w.ask(sid, reset=True)["talk"]["meanings"] == []


def test_static_talk_panel_marks_and_thinking():
    js = (STATIC / "explain.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "考えています" in js and "innerHTML" not in js
    assert "human_mark" in js and "ai_mark" in js
    assert ".mark-human" in css and ".mark-ai" in css
    for path in ("/api/approve", "/api/export", "/api/hear", "/api/select", "/api/dialog"):
        assert path not in js


# ======== 台本 Ver.6.1 Scene 3（3-1 読む・3-2 教える・3-3 変える） ========
SCENE_31 = "待機の位置から作業の場所へ動き、下りて戻る動きをくり返してから、待機の位置に戻ります。何をする作業ですか？"


def confirmed_config(tmp_path) -> Path:
    """DEMO_BUTTON_REPEAT の座標系で、上下の向きの仮の設定（テストの一時フォルダだけ。実機で確かめたものではない）。"""
    from voice_teaching import updown
    obj = updown.build_config(jbi.load(DEMO), updown.DEFAULT_ROBOT, "テスト（一時フォルダの仮の設定）",
                              "テスト用の仮の設定。実機で確かめたものではない")
    return Path(updown.write_config(obj, tmp_path / "updown_demo.json", overwrite=True))


def test_scene_3_1_updown_confirmed_allows_down_and_back_and_marks_roles_as_ai_guess(tmp_path):
    w, fake = flow(tmp_path, answer(SCENE_31, [21, 22, 23, 24, 25, 26]), updown_config=confirmed_config(tmp_path))
    sid = w.load(demo_bytes())["session_id"]
    assert w.state(sid)["state"]["updown"]["status"] == "confirmed"
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["reply_by"] == "claude" and r["reply"] == SCENE_31  # Claude の文は書き換えない
    facts = json.loads(fake.requests[0]["messages"][0]["content"][0]["text"])["job_facts"]
    assert facts["updown"]["status"] == "confirmed"
    # 「待機の位置」「作業の場所」は、プログラムが事実として知らない：AI の推定（未確定）の印で示す（登録しない）
    assert r["guesses"] == [SCENE_31.split("。")[0] + "。"] and meanings(w, sid) == []
    assert r["highlight"]["lines"] == [21, 22, 23, 24, 25, 26]  # 下りて戻る区間（2回分）を光らせる
    assert r["supplement"] == ["21〜26行目：C00001→C00002→C00003（2回）"]  # 番号は補足


def test_scene_3_1_updown_unconfirmed_needs_z_words(tmp_path):
    w, fake = flow(tmp_path, answer(SCENE_31, [21]))
    sid = w.load(demo_bytes())["session_id"]
    assert w.state(sid)["state"]["updown"]["status"] != "confirmed"
    assert w.ask(sid, "このロボット、何をしている？")["reply"] == cx.FIXED_UPDOWN
    w2, fake2 = flow(tmp_path, answer("Zのマイナスへ動いて戻る動きをくり返します。何をする作業ですか？", [21]))
    sid2 = w2.load(demo_bytes())["session_id"]
    assert w2.ask(sid2, "このロボット、何をしている？")["reply_by"] == "claude"


def test_scene_3_2_and_3_3_teach_then_three_times(tmp_path):
    w, fake = flow(tmp_path, answer(SCENE_31, [21, 22, 23, 24, 25, 26]), updown_config=confirmed_config(tmp_path))
    sid = w.load(demo_bytes())["session_id"]
    w.ask(sid, "このロボット、何をしている？")
    # 3-2 教える：その区間を光らせて確かめ、［この動きです］を押してから覚える（アプリの文。Claude は呼ばない）
    r = w.ask(sid, "ボタンを押してるんだよ")
    assert r["status"] == "ask_which" and r["highlight"]["lines"] == [21, 22, 23, 24, 25, 26]
    r = press(w, sid)
    assert r["reply_by"] == "app" and r["taught_note"].startswith("分かりました。この動き（21〜26行目のくり返し）")
    m = r["talk"]["meanings"]
    assert [(x["text"], x["mark"], x["positions"]) for x in m] == [
        ("ボタンを押してる", talk.HUMAN_MARK, ["C00001", "C00002", "C00003"])]  # 吹き出しと 3D の印に使う
    assert r["guesses"] == []
    # 3-3 変える（このブランチでは変更エンジンにつながない：記録と固定の文だけ）
    fake.add(answer("ボタンを押す動きを、2回から3回にしますか？", [21, 22, 23, 24, 25, 26], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r = w.ask(sid, "じゃあ、3回にして")
    # CHECKER_033 P1-A（唐川さんの方針② 11/4）：変更の会話はプログラムの定型文（Claude の文は出さない）
    assert r["reply_by"] == "app" and "2回から3回" in r["reply"] and "受け取りました" in r["reply"]
    assert r["notes"] == [cx.READ_ONLY_NOTE] and r["highlight"]["lines"] == [21, 22, 23, 24, 25, 26]
    rec = r["change_record"]
    assert (rec["program_from"], rec["program_total"], rec["claude_from"], rec["claude_total"]) == (2, 3, 2, 3)
    assert rec["block"] == {"positions": ["C00001", "C00002", "C00003"], "first_line": 21, "last_line": 26, "count": 2}
    assert rec["evidence_matches_block"] is True and rec["mismatch"] is False
    st = w.state(sid)["state"]
    assert st["candidate"] is None and st["approval"] is None and w._entries[sid].session.candidate is None


def test_scene_3_3_evidence_not_matching_the_block_is_reported(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    fake.add(answer("この動作の回数を、2回から3回にしますか？", [27], "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    rec = w.ask(sid, "じゃあ、3回にして")["change_record"]
    assert rec["evidence_matches_block"] is False and rec["evidence_lines"] == [27]


def test_scene_3_taught_mark_is_drawn_in_3d():
    js = (STATIC / "explain.js").read_text(encoding="utf-8")
    v3 = (STATIC / "view3d.js").read_text(encoding="utf-8")
    assert "vtTaught" in js and "_taughtMarks" in v3 and "（人が教えた）" in v3



# ======== シクロの本物の通し（2026-11-03）で見つかったこと ========
REAL_1 = ("最初の位置に動いたあと、同じ3か所をめぐる動きをくり返し、最後に別の位置へ移ります。"
          "作業の目的は分からないので、何の作業か教えてもらえますか？")
REAL_1_PLAIN = REAL_1.replace("同じ3か所をめぐる", "同じ場所をめぐる")  # 方針③（11/4）：数を含めない言い方


def test_real_counted_numbers_are_allowed_but_uncounted_are_not(tmp_path):
    w, fake = flow(tmp_path, answer(REAL_1, list(range(20, 28))), updown_config=confirmed_config(tmp_path))
    sid = w.load(demo_bytes())["session_id"]
    r = w.ask(sid, "このロボット、何をしている？")
    # 唐川さんの方針③（11/4）：「3か所」のような数えた数でも、MVP では Claude の自由文に入れない（数はアプリの補足）
    assert r["reply_by"] == "app" and r["reply"] == talk.FIXED_NO_NUMBER
    facts = json.loads(fake.requests[0]["messages"][0]["content"][0]["text"])["job_facts"]
    assert facts["repeats"][0]["points_per_cycle"] == 3  # プログラムが数えて Claude にも渡す
    assert facts["counts"] == {"positions": 5, "instructions": 10, "moves": 8, "repeat_blocks": 1,
                               "moves_by_command": {"MOVJ": 2, "MOVL": 6}}
    fake.add(answer("全部で4秒かかります。", [21]), answer("同じ動きを7回くり返します。", [21]))
    assert w.ask(sid, "時間は？")["reply"] == cx.FIXED_NUMBER  # 数えていない数（時間）は今どおり使わない
    assert w.ask(sid, "回数は？")["reply"] == cx.FIXED_NUMBER


def test_real_three_turns_single_block_confirm_then_three_times(tmp_path):
    w, fake = flow(tmp_path, answer(REAL_1_PLAIN, list(range(20, 28))), updown_config=confirmed_config(tmp_path))
    sid = w.load(demo_bytes())["session_id"]
    r1 = w.ask(sid, "このロボット、何をしている？")
    assert r1["reply_by"] == "claude"
    n = len(fake.requests)
    # 2ターン目：根拠に区間の外の行（20・27）があるが、区間は1つ：その区間を光らせて［この動きです］［ほかの動き］
    r2 = w.ask(sid, "ボタンを押してるんだよ")
    q = "「ボタンを押してる」のは、この光っている動き（21〜26行目のくり返し）のことですか？"
    assert r2["status"] == "ask_which" and r2["reply"] == q and len(fake.requests) == n
    p = r2["talk"]["pending"]
    assert p["question"] == q and [o["label"] for o in p["options"]] == [talk.CONFIRM_YES, talk.CONFIRM_NO]
    assert r2["highlight"] == {"lines": [21, 22, 23, 24, 25, 26], "positions": ["C00001", "C00002", "C00003"], "context_lines": [], "entry_lines": [21]}
    assert meanings(w, sid) == []  # 押すまで登録しない（E2-7 ④）
    # 声・文字の「はい」では選ばない
    fake.add(answer("どの動きか、ボタンで選んでください。", []))
    w.ask(sid, "はい", source="voice")
    assert meanings(w, sid) == []
    p = w.ask(sid, sync=True)["talk"]["pending"]
    r3 = w.ask(sid, choose="confirm:yes", token=p["token"])
    assert r3["status"] == "taught" and r3["reply_by"] == "app"
    assert r3["taught_note"] == "分かりました。この動き（21〜26行目のくり返し）を「ボタンを押してる」として、この会話の中で覚えます"
    assert [(x["text"], x["lines"][0], x["lines"][-1]) for x in meanings(w, sid)] == [("ボタンを押してる", 21, 26)]
    # 3ターン目：Claude に人が教えた意味が渡り、change_record の対象は登録した区間
    fake.add(answer("ボタンを押す動きを、2回から3回に変更しますか？", list(range(21, 27)), "change_request",
                    {"kind": "repeat", "from_count": 2, "to_count": 3}))
    r4 = w.ask(sid, "じゃあ、3回にして")
    sent = json.loads(next(b["text"] for b in fake.requests[-1]["messages"][-1]["content"]
                           if b["type"] == "text" and "taught_by_human" in b["text"]))["taught_by_human"]
    assert [(t["meaning"], t["lines"]) for t in sent] == [("ボタンを押してる", [21, 26])]
    rec = r4["change_record"]
    assert rec["target"] == "21〜26行目：C00001→C00002→C00003（2回）" and rec["program_from"] == 2
    assert rec["program_total"] == 3 and rec["evidence_matches_block"] is True and rec["mismatch"] is False
    assert w._entries[sid].session.candidate is None


def test_real_single_block_other_move_does_not_register(tmp_path):
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake, lines=list(range(20, 28)))
    p = w.ask(sid, "ボタンを押してるんだよ")["talk"]["pending"]
    r = w.ask(sid, choose="confirm:no", token=p["token"])
    assert r["reply"] == talk.NOT_THIS and meanings(w, sid) == [] and r["talk"]["pending"] is None


def test_real_first_turn_highlight_includes_movj_lines_but_they_stay_dotted(tmp_path):
    # 1ターン目の根拠 20〜27：くり返しの区間（21〜26）を強く光らせ、前後の移動（MOVJ の 20・27 行）は控えめに出す
    # （唐川さん 11/3 夜）。線の見た目は区間の種類のまま（MOVJ は点線。highlight.js・e2e で確かめる）
    w, fake = flow(tmp_path, answer(REAL_1, list(range(20, 28))), updown_config=confirmed_config(tmp_path))
    sid = w.load(demo_bytes())["session_id"]
    r = w.ask(sid, "このロボット、何をしている？")
    assert r["highlight"]["lines"] == list(range(21, 27))  # 区間（意味の登録などに使う）
    assert r["highlight"]["context_lines"] == [20, 27]
    assert r["highlight"]["entry_lines"] == [21]  # 1回目に C00000 から入ってくる線は、強調せず控えめに描く
    assert r["highlight"]["positions"] == ["C00001", "C00002", "C00003"]
    segs = {s["line_no"]: s["kind"] for s in __import__("voice_teaching.web.api", fromlist=["x"]).job_view(
        demo_bytes())["segments"]}
    assert segs[27] == "movj" and 20 not in segs  # 20行目は最初の移動（線は無い）。27行目は MOVJ の点線


@pytest.mark.parametrize("said", ["そう", "はい", "そうそう", "うん、そうだね", "この動きです"])
@pytest.mark.parametrize("source", ["voice", "text"])
def test_pending_target_is_not_confirmed_by_words(tmp_path, said, source):
    """「〜のことですか？」は、声・文字の「そう」「はい」（ボタンの文字と同じ言葉でも）では確定しない。ボタンだけ。"""
    w, fake = flow(tmp_path)
    sid = first_turn(w, fake)
    assert w.ask(sid, "ボタンを押してるんだよ")["status"] == "ask_which"
    fake.add(answer("ボタンで選んでください。", []))
    w.ask(sid, said, source=source)
    assert meanings(w, sid) == []
