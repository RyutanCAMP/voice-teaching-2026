"""受入テスト：CHECKER_028（クロの準備。1章・2章 K1・K2）と唐川さんの指示（A〜D）。実装より先に書いた。

A：取り消し・元に戻す・位置の選び直し・キャンセル・新しいジョブ・承認の取り消しでは、未確定の会話の状態を必ず捨てる
   （確認済みの設定で「下げて」→ C00004 を選ぶ →「3ミリ」が C00004 Z−3 になっていた）
B：上下の言葉から来た Z の出どころ（vertical・explicit_axes・value_axes）は、候補を作る直前の1か所
   （Session.propose → check_proposal の始めの ut.axis_source_gate）で確かめる。全経路がそこを通ることを固定する
C：「Zを3mm下げて」（座標・値が無い）は、確認済みでも未確認でも D1 と同じ形で聞き返す。「Z座標を3mm下げて」は値を減らす
   指示として許可し、確認文は「Z の値を 3mm 減らします」と書く（上下が未確認のときは「下へ」と書かない）
D：上下の案内のあとの「3ミリ」には、次にどう言えばよいかの案内を返す（前の発話とはつながない）

クロの26の流れ（CHECKER_028_tools/p0_split.py）を、設定なしと確認済みの設定（一時フォルダの仮の設定）の両方で動かす。
期待の印は p0_split.py の [Z]/[noZ]/[any] と CHECKER_028 3章 1（設定があるときは、上下の言葉からの Z も候補になってよい）。
"""
import inspect
import re
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import session as session_mod
from voice_teaching import updown
from voice_teaching import utterance as ut
from voice_teaching.session import Session
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C_PATH = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
DEMO_C = DEMO_C_PATH.read_bytes()
SOURCES = ["voice", "text"]


def start(config, select="C00005", interp=None):
    w = WebFlow(interpreter=interp or ci.MockInterpreter(), updown_config=config)
    sid = w.load(DEMO_C)["session_id"]
    if select:
        w.select(sid, select)
    return w, sid


def turn(w, sid):
    return w.state(sid)["state"]["dialog"]["turn"]


def run(w, sid, steps, source="voice"):
    last = None
    for s in steps:
        if s.startswith("#"):
            last = w.choose(sid, s[1:], turn(w, sid))
        elif s.startswith("@"):
            last = w.select(sid, s[1:])
        elif s == "!undo":
            last = w.undo(sid)
        else:
            last = w.hear(sid, s, source)
    return last


def cand(r):
    c = r["state"]["candidate"]
    return None if c is None else (c["target"], c["delta"])


@pytest.fixture
def confirmed(make_updown):
    return make_updown()


# (名前, 手順, 設定なしの期待, 確認済みの期待)。期待：None＝Z の候補なし（承認・出力も不可）／タプル＝その候補／"any"＝記録だけ
Z5M, Z5P = ("C00005 Z", "-3.000"), ("C00005 Z", "+3.000")
CASES = [
    ("P0 下げて→3ミリ", ["下げて", "3ミリ"], None, Z5M),
    ("下に→3ミリ", ["下に", "3ミリ"], None, Z5M),
    ("上げて→3ミリ", ["上げて", "3ミリ"], None, Z5P),
    ("3ミリ→下げて", ["3ミリ", "下げて"], None, "any"),
    ("下げて→3", ["下げて", "3"], None, "any"),
    ("下げて→3mm（全角）", ["下げて", "３ｍｍ"], None, Z5M),
    ("下げて→少し→3ミリ", ["下げて", "少し", "3ミリ"], None, "any"),
    ("下げて→えーと→3ミリ", ["下げて", "えーと", "3ミリ"], None, "any"),
    # A：選び直し・元に戻す・キャンセルのあとは、確認済みでも前の発話とつながらない
    ("下げて→位置を選び直す→3ミリ", ["下げて", "@C00004", "3ミリ"], None, None),
    ("下げて→元に戻す→3ミリ", ["下げて", "!undo", "3ミリ"], None, None),
    ("下げて→キャンセル→3ミリ", ["下げて", "キャンセル", "3ミリ"], None, None),
    ("下げて→そうそう→3ミリ", ["下げて", "そうそう", "3ミリ"], None, "any"),
    ("さげて→3ミリ", ["さげて", "3ミリ"], None, Z5M),
    ("置くところを下げて→そうそう→3ミリ", ["置くところを下げて", "そうそう", "3ミリ"], None, Z5M),
    ("置くところを3ミリ下げて→そうそう", ["置くところを3ミリ下げて", "そうそう"], None, Z5M),
    ("置くところを少し上に→C00005ボタン→3ミリ", ["置くところを少し上に", "#pos:C00005", "3ミリ"], None, Z5P),
    ("手前に→3ミリ→Zマイナスボタン", ["置くところを少し手前に", "#pos:C00005", "3ミリ", "#dir:Z-"], Z5M, Z5M),
    ("下げて→Zマイナス3ミリ（明示）", ["下げて", "Zマイナス3ミリ"], Z5M, Z5M),
    ("Zマイナス3ミリ（明示）", ["Zマイナス3ミリ"], Z5M, Z5M),
    ("Z座標を3mm下げて（決定1）", ["Z座標を3mm下げて"], Z5M, Z5M),
    # C：「Zを3mm下げて」は確認済みでも聞き返す
    ("Zを3mm下げて（D1：聞き返し）", ["Zを3mm下げて"], None, None),
    ("ゼット座標を3ミリ下げて", ["ゼット座標を3ミリ下げて"], Z5M, Z5M),
    ("全座標を3ミリ下げて（聞き違い）", ["全座標を3ミリ下げて"], None, None),
    ("Z座標を下げて→3ミリ", ["Z座標を下げて", "3ミリ"], Z5M, Z5M),
    ("下げて→Y座標→3ミリ", ["下げて", "Y座標", "3ミリ"], None, None),
    ("Y座標を3mm下げて", ["Y座標を3mm下げて"], "any", "any"),
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("mode", ["none", "confirmed"])
@pytest.mark.parametrize("name,steps,unconf,conf", CASES, ids=[c[0] for c in CASES])
def test_c028_26_flows(name, steps, unconf, conf, mode, source, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    last = run(w, sid, steps, source)
    st = w.state(sid)["state"]
    c = st["candidate"]
    got = None if c is None else (c["target"], c["delta"])
    want = conf if mode == "confirmed" else unconf
    if want == "any":
        return
    if want is None:
        assert got is None or not got[0].endswith("Z"), (name, mode, got, last.get("message"))
        assert w.approve(sid, "唐川")["ok"] is False or not (got and got[0].endswith("Z"))
        assert w.export(sid)["ok"] is False
    else:
        assert got == want, (name, mode, got, last.get("message"))


# ======================================================================
# A：未確定の状態を捨てる
# ======================================================================
@pytest.mark.parametrize("first", ["下げて", "Z座標を下げて", "Zマイナス方向", "Yのマイナス方向に"])
@pytest.mark.parametrize("between", ["@C00004", "@C00005", "!undo", "キャンセル", "元に戻して"])
@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_a_reset_actions_drop_the_pending_question(first, between, mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, first)
    assert r["status"] == "ask"
    run(w, sid, [between])
    assert w._entries[sid].session._awaiting_answer is None
    r = w.hear(sid, "3ミリ")
    assert cand(r) is None and r["status"] == "ask", (first, between, r["message"])


@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_a_session_entry_points_drop_the_pending_question(mode, updown_ok):
    chk = (lambda job: updown_ok) if mode == "confirmed" else None
    for reset in (lambda s: s.select("C00004"), lambda s: s.select("C00005"), lambda s: s.undo(),
                  lambda s: s.explain_only("説明です")):
        s = Session.from_file(DEMO_C_PATH, updown_check=chk)
        s.select("C00005")
        assert s.hear("Z座標を下げて").status == "ask" and s._awaiting_answer is not None
        reset(s)
        assert s._awaiting_answer is None
        assert s.hear("3ミリ").status == "ask" and s.candidate is None


def test_a_new_job_starts_clean(make_updown):
    w, sid = start(make_updown())
    w.hear(sid, "下げて")
    sid2 = w.load(DEMO_C)["session_id"]
    w.select(sid2, "C00005")
    r = w.hear(sid2, "3ミリ")
    assert cand(r) is None and r["status"] == "ask"


@pytest.mark.parametrize("step_texts", [
    ["置くところを少し上に", "そうそう"],              # 方向（Z＋）を持って、量を聞いている
    ["置くところを3ミリ", "そうそう"],                 # 量だけ持っている
])
def test_a_reselect_during_dialogue_drops_held_amount_and_direction(step_texts, make_updown):
    # 会話で量・方向を持っている段で、位置を選び直した：会話をやめる（持っていた量・方向を別の位置に当てない）
    w, sid = start(make_updown(), select="C00001")
    r = run(w, sid, step_texts)
    assert r["state"]["dialog"]["step"] == "ask_move"
    r = w.select(sid, "C00004")
    d = r["state"]["dialog"]
    assert d["step"] is None and d["amount_mm"] is None and d["direction"] is None
    assert r["state"]["selected"] == "C00004" and r["state"]["candidate"] is None
    r = w.hear(sid, "3ミリ")
    assert cand(r) is None
    r = w.hear(sid, "Zマイナス")
    assert cand(r) is None


def test_a_pick_step_still_uses_the_selection(make_updown):
    # 「ほかの位置」のあとの手で選ぶ段は、選んだ位置で会話を続ける（今どおり）
    w, sid = start(make_updown(), select="C00001")
    r = w.hear(sid, "置くところを少し手前にして")
    r = w.choose(sid, "other", r["state"]["dialog"]["turn"])
    assert r["state"]["dialog"]["step"] == "pick"
    r = w.select(sid, "C00004")
    assert r["state"]["dialog"]["step"] == "ask_move" and r["state"]["selected"] == "C00004"


def test_a_cancelled_approval_drops_the_pending_question(make_updown):
    w, sid = start(make_updown())
    assert cand(w.hear(sid, "Zマイナス3ミリ")) == ("C00005 Z", "-3.000")
    assert w.approve(sid, "唐川")["ok"] is True
    w.undo(sid)  # 承認も取り消す
    s = w._entries[sid].session
    assert s._awaiting_answer is None
    assert w._entries[sid].dialog.step is None


# ======================================================================
# B：最後の関門は1か所（ut.axis_source_gate。Session.propose → check_proposal の始め）
# ======================================================================
def test_b_only_session_propose_creates_candidates():
    src = inspect.getsource(session_mod.Session)
    # 候補を入れるのは _accept だけ。_accept を呼ぶのは propose と _blocked だけ。_blocked を呼ぶのは propose だけ
    assert len(re.findall(r"self\.candidate = change", src)) == 1
    accept_calls = [m.start() for m in re.finditer(r"self\._accept\(", src)]
    blocked_calls = [m.start() for m in re.finditer(r"self\._blocked\(", src)]
    def owner(pos):
        return re.findall(r"\n    def (\w+)\(", src[:pos])[-1]
    assert {owner(p) for p in accept_calls} == {"propose", "_blocked"}
    assert {owner(p) for p in blocked_calls} == {"propose"}
    # check_proposal は始めに axis_source_gate を呼ぶ
    cp = inspect.getsource(ut.check_proposal)
    body = cp.split('"""')[2]
    assert "axis_source_gate(" in body and body.index("axis_source_gate(") < body.index("pre_check(")
    # check_proposal が OK を返すのは1か所だけで、関門の結果を見たあと
    assert body.count("return OK") == 1 and body.index("for gate in gates") < body.index("return OK")
    # propose の中で、check_proposal は _plan（plan_shift）の前
    pr = inspect.getsource(Session.propose)
    assert pr.index("ut.check_proposal(") < pr.index("self._plan(")
    # ほかのモジュールは Session の候補を直接入れない
    for mod in ("web/flow.py", "dialog.py", "claude_interpreter.py", "mock_claude.py"):
        text = (ROOT / "voice_teaching" / mod).read_text(encoding="utf-8")
        assert ".candidate = " not in text and "._accept(" not in text, mod


def _paths(confirmed_cfg):
    """候補を作るすべての経路（文字・音声・会話のボタン・合わせた答え・Claude の答え・M3 の［変更したい］・疑問の選択肢）。"""
    def plain(w, sid):
        return w.hear(sid, "Zマイナス3ミリ", "text")

    def voice(w, sid):
        return w.hear(sid, "Zマイナス3ミリ", "voice")

    def merged(w, sid):
        w.hear(sid, "Zマイナス方向")
        return w.hear(sid, "3ミリ")

    def updown_merged(w, sid):
        w.hear(sid, "下げて")
        return w.hear(sid, "3ミリ")

    def dialog_button(w, sid):
        w.select(sid, "C00001")
        return run(w, sid, ["置くところを少し手前に", "#pos:C00005", "3ミリ", "#dir:Z-"])

    def dialog_voice(w, sid):
        w.select(sid, "C00001")
        return run(w, sid, ["置くところ", "そうそう", "下に3ミリ"])

    def want_change(w, sid):
        r = w.hear(sid, "Zマイナス3ミリずらします")
        return w.hear(sid, r["choices"][0]["text"])

    def question(w, sid):
        r = w.hear(sid, "Zマイナス3ミリ？")
        return w.hear(sid, r["choices"][0]["text"])

    def d1_choice(w, sid):
        r = w.hear(sid, "Zを3mm下げて")
        return w.hear(sid, r["choices"][0]["text"])

    return [plain, voice, merged, updown_merged, dialog_button, dialog_voice, want_change, question, d1_choice]


def test_b_every_path_goes_through_the_gate(make_updown, monkeypatch):
    cfg = make_updown()
    calls = []
    real = ut.axis_source_gate

    def spy(facts, p, updown=None):
        calls.append((facts.raw, p.axis, p.delta_mm))
        return real(facts, p, updown)
    monkeypatch.setattr(ut, "axis_source_gate", spy)
    for path in _paths(cfg):
        w, sid = start(cfg)
        calls.clear()
        r = path(w, sid)
        assert cand(r) is not None and cand(r)[0].endswith("Z"), (path.__name__, r["message"])
        assert calls and calls[-1][1] == "Z", path.__name__


def test_b_mutation_broken_gate_stops_every_path(make_updown, monkeypatch):
    # 簡単なミューテーション：関門を「いつも断る」に壊すと、どの経路でも候補ができない（＝全経路がここを通る）
    cfg = make_updown()
    monkeypatch.setattr(ut, "axis_source_gate", lambda facts, p, updown=None: ut.Verdict("ask", "関門で止めた"))
    for path in _paths(cfg):
        w, sid = start(cfg)
        r = path(w, sid)
        assert cand(r) is None, path.__name__


@pytest.mark.parametrize("raw,st_kind,ok", [
    ("3ミリ下げて", "none", False), ("3ミリ下げて", "confirmed", True),
    ("Zマイナス3ミリ", "none", True), ("Z座標を3mm下げて", "none", True),
    ("Zを3mm下げて", "none", False), ("Zを3mm下げて", "confirmed", False),
    ("Z座標を下に3mm", "none", False), ("Z座標を下に3mm", "confirmed", True),
])
def test_b_gate_reads_the_axis_source(raw, st_kind, ok, updown_ok):
    st = updown_ok if st_kind == "confirmed" else None
    f = ut.read_utterance(raw)
    p = ut.ProposedChange(kind="shift", position="C00005", axis="Z", delta_mm=-3.0)
    assert (ut.axis_source_gate(f, p, st).status == "ok") is ok, raw


# ======================================================================
# C：「Zを3mm下げて」は聞き返す。値の増減と上下の指示の区別
# ======================================================================
@pytest.mark.parametrize("text", ["Zを3mm下げて", "Zを3ミリ下げて", "ゼットを3ミリ下げて", "zを3mm下げて"])
@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_c_bare_z_with_up_down_word_is_asked_like_d1(text, mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None, r["message"]
    labels = [c["label"] for c in r["choices"]]
    if mode == "confirmed":
        assert labels == ["Z座標を −3mm", "下方向へ 3mm"]
    else:
        assert labels == ["Z座標を −3mm"]
        assert updown.MSG_UNCONFIRMED in r["message"]
    r2 = w.hear(sid, r["choices"][0]["text"])
    assert cand(r2) == ("C00005 Z", "-3.000")


@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_c_bare_z_up_and_split(mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, "ゼットを3ミリ上げて")
    assert r["status"] == "ask" and cand(r) is None and r["choices"][0]["label"] == "Z座標を +3mm"
    w2, sid2 = start(cfg)
    w2.hear(sid2, "Zを下げて")
    r = w2.hear(sid2, "3ミリ")
    assert r["status"] == "ask" and cand(r) is None, r["message"]


@pytest.mark.parametrize("text", ["C座標を3ミリ下げて", "シー座標を3ミリ下げて", "ジー座標を3ミリ下げて", "全座標を3ミリ下げて"])
@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_c_misheard_axes_stop_as_unknown_words(text, mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None and "分かりませんでした" in r["message"], r["message"]
    assert r["facts"]["unknown_words"], r["facts"]


@pytest.mark.parametrize("mode", ["none", "confirmed"])
def test_c_zetto_coordinate_is_z(mode, make_updown, tmp_path):
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, "ゼット座標を3ミリ下げて")
    assert cand(r) == ("C00005 Z", "-3.000")


@pytest.mark.parametrize("mode", ["none", "confirmed"])
@pytest.mark.parametrize("text,word", [("Z座標を3mm下げて", "減らします"), ("Zの値を3mm減らして", "減らします"),
                                        ("Z座標を3mm上げて", "増やします")])
def test_c_value_change_confirmation_text(text, word, mode, make_updown, tmp_path):
    # K2 の1：値の増減の指示の確認文は「Z の値を 3mm 減らします」。物理の「下へ」「上へ」は書かない
    cfg = make_updown() if mode == "confirmed" else tmp_path / "none.json"
    w, sid = start(cfg)
    r = w.hear(sid, text)
    conf = r["state"]["confirmation_text"]
    assert f"Z の値を 3mm {word}" in conf, conf
    assert "下へ" not in conf and "上へ" not in conf
    if mode == "none":
        assert r["state"]["candidate"]["direction_names"] == {}


def test_c_unconfirmed_candidate_has_no_physical_direction_names(tmp_path):
    w, sid = start(tmp_path / "none.json")
    r = w.hear(sid, "Zマイナス3ミリ")
    c = r["state"]["candidate"]
    assert c["direction_names"] == {} and (c["updown"] or {}).get("status") != "confirmed"
    assert "下へ" not in r["state"]["confirmation_text"] and r["state"]["direction_names"] == {}


# ======================================================================
# D：上下の案内のあとの「3ミリ」に、次の言い方を案内する
# ======================================================================
@pytest.mark.parametrize("kind,head", [("missing", "上下の向きが確認されていないため"),
                                       ("changed", "上下の向きの設定が、確認したあとで変わっているため"),
                                       ("mismatch", "このジョブは上下の向きを確認したジョブと違うため")])
@pytest.mark.parametrize("first,word", [("下げて", "下げて"), ("上に", "上に"), ("さげて", "下げて")])
def test_d_followup_after_updown_ask(kind, head, first, word, make_updown, tmp_path):
    import json
    if kind == "missing":
        cfg, data = tmp_path / "none.json", DEMO_C
    else:
        cfg = make_updown()
        data = DEMO_C
        if kind == "changed":
            obj = json.loads(Path(cfg).read_text(encoding="utf-8"))
            obj["evidence"] += "。"
            Path(cfg).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        else:
            nl = b"\r\n" if b"\r\n" in DEMO_C else b"\n"
            data = DEMO_C.replace(b"///TOOL 0" + nl, b"///TOOL 0" + nl + b"///USER 1" + nl)
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg)
    sid = w.load(data)["session_id"]
    w.select(sid, "C00005")
    w.hear(sid, first)
    r = w.hear(sid, "3ミリ")
    assert r["status"] == "ask" and cand(r) is None
    assert r["message"].startswith(head) and f"「{word}」は使えません" in r["message"], r["message"]
    assert "軸と向きと量を一緒に言ってください" in r["message"]
    assert not ut.contains_forbidden(r["message"])
    # 案内は1回だけ。次の発話はふつうに読む
    r = w.hear(sid, "3ミリ")
    assert r["message"] == "何をするか分かりませんでした。もう一度指示してください"
