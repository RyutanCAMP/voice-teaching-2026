"""CHECKER_026 M2：上下の向きの設定（11章。唐川さんの決定 2026-11-03 13:28 A案）の受入テスト。

11-3章「再レビューで試すこと」の1〜8を、そのまま受入テストにする。
- 設定は一時フォルダのものだけを使う（conftest.make_updown。実機で確かめた設定ではない）
- 照合に通ったときだけ「上・下・上げ・下げ」を Z± にする。通らなければ状態ごとの案内で聞き返す
- 軸と符号を明示した指示（「Zマイナス3ミリ」）は、どの状態でも候補にしてよい
"""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import dialog as dg
from voice_teaching import jbi, job_scope, sim, updown
from voice_teaching import utterance as ut
from voice_teaching.session import Session
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C_PATH = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
DEMO_C = DEMO_C_PATH.read_bytes()
DEMO = (ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()
NL = b"\r\n" if b"\r\n" in DEMO_C else b"\n"  # samples は改行を変えずに持つ（.gitattributes -text）
WITH_USER = DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 0" + NL + b"///USER 1" + NL)
TOOL_1 = DEMO_C.replace(b"///TOOL 0" + NL, b"///TOOL 1" + NL)
assert WITH_USER != DEMO_C and TOOL_1 != DEMO_C

UNCONF = updown.MSG_UNCONFIRMED
MISMATCH = updown.MSG_MISMATCH
CHANGED = updown.MSG_CHANGED


def start(config, data=DEMO_C, select="C00005", robot=updown.DEFAULT_ROBOT, interp=None):
    w = WebFlow(interpreter=interp or ci.MockInterpreter(), updown_config=config, robot=robot)
    r = w.load(data)
    assert r["ok"], r
    sid = r["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid


def cand(r):
    c = r["state"]["candidate"]
    return None if c is None else (c["target"], c["delta"])


def labels(r):
    return [c["label"] for c in r["state"]["dialog"]["choices"]]


def rewrite(path, fn):
    """設定ファイルの中身を書き換える（fn は辞書を受け取って変える）。指紋は計算し直さない。"""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    fn(obj)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def refingerprint(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    obj["fingerprint"] = updown.content_fingerprint(obj)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ======================================================================
# 0. structure_hash（FRAME v1.1 2章。CHECKER_028 K4 で上下の照合から外し、job_scope の部品にした）と設定の指紋
# ======================================================================
def test_structure_hash_masks_xyz_of_listed_positions_only():
    job = jbi.parse_bytes(DEMO_C)
    h = job_scope.structure_hash(job, ["C00005"])
    # 手で同じ計算：//NAME・///DATE を除き、C00005 の X,Y,Z を * にして、改行を \n にそろえる
    lines = []
    for line in DEMO_C.decode("ascii").splitlines():
        if line.startswith("//NAME") or line.startswith("///DATE"):
            continue
        if line.startswith("C00005="):
            line = "C00005=*,*,*,180.00,0.00,0.00"
        lines.append(line + "\n")
    assert h == hashlib.sha256("".join(lines).encode()).hexdigest()
    # 名前・日付・C00005 の X,Y,Z が違っても同じ。ほかの行が1文字でも違えば違う
    other = jbi.parse_bytes(DEMO_C.replace(b"//NAME DEMO_C_PLACE", b"//NAME DEMO_C_PLACE_SIM")
                            .replace(b"///DATE 2026/11/02 18:30", b"///DATE 2026/11/04 09:00")
                            .replace(b"C00005=400.000,150.000,200.000", b"C00005=401.000,149.000,197.000"))
    assert job_scope.structure_hash(other, ["C00005"]) == h
    rx = jbi.parse_bytes(DEMO_C.replace(b"C00005=400.000,150.000,200.000,180.00", b"C00005=400.000,150.000,200.000,179.00"))
    assert job_scope.structure_hash(rx, ["C00005"]) != h  # Rx 以降は伏せない
    c4 = jbi.parse_bytes(DEMO_C.replace(b"C00004=400.000,150.000,300.000", b"C00004=400.000,150.000,301.000"))
    assert job_scope.structure_hash(c4, ["C00005"]) != h
    assert job_scope.structure_hash(job, ["C00004", "C00005"]) != h
    with pytest.raises(job_scope.ScopeError):
        job_scope.structure_hash(job, ["C00099"])
    # 改行を CRLF・LF のどちらにしても同じ（改行を \n にそろえる）
    lf = DEMO_C.replace(b"\r\n", b"\n")
    assert job_scope.structure_hash(jbi.parse_bytes(lf), ["C00005"]) == h
    assert job_scope.structure_hash(jbi.parse_bytes(lf.replace(b"\n", b"\r\n")), ["C00005"]) == h


def test_fingerprint_ignores_order_and_spaces_but_not_values(make_updown):
    path = make_updown()
    obj = json.loads(path.read_text(encoding="utf-8"))
    fp = obj["fingerprint"]
    assert fp == updown.content_fingerprint(obj)
    reordered = dict(reversed(list(obj.items())))
    assert updown.content_fingerprint(reordered) == fp
    obj["evidence"] += "。"
    assert updown.content_fingerprint(obj) != fp


def test_config_has_the_items_of_11_1(make_updown):
    obj = json.loads(make_updown().read_text(encoding="utf-8"))
    assert obj["directions"] == {"上": "Z+", "下": "Z-"}
    assert obj["confirmed_by"] and obj["confirmed_at"] and obj["evidence"]
    assert obj["robot"] == "HC20DT"
    assert "job" not in obj  # CHECKER_028 K4：上下の向きの設定は JOB の中身（structure_hash・positions）を持たない
    assert obj["coordinate"] == {"postype": "ROBOT", "rectan": True, "user": None, "tool": 0,
                                 "frame": None, "rj": False}  # FRAME・ATTR の RJ も照合する
    assert obj["fingerprint"] == updown.content_fingerprint(obj)


# ======================================================================
# 1. 設定なし → 「3ミリ下げて」は案内、「Zマイナス3ミリ」は候補
# ======================================================================
@pytest.mark.parametrize("config", ["missing", None])
def test_1_no_config_asks_for_updown_and_accepts_explicit_axis(config, tmp_path):
    cfg = tmp_path / "none.json" if config == "missing" else None
    w, sid = start(cfg)
    for text in ("3ミリ下げて", "3mm下げて", "下に3ミリ", "上に3mm", "3ミリ上げて", "C00005を下に3mm"):
        r = w.hear(sid, text)
        assert r["status"] == "ask" and ut.is_updown_unset_ask(r["message"]), text
        assert cand(r) is None and all(ut.read_facing_choice(c["text"]) for c in r["choices"]) and r["tool_calls"] == []  # d（11/5 唐川さんの決定）：未確認の向きの言葉は、向きの確かめのボタン（番号つきの文）だけ。Z を決める選択肢は出さない
    st = r["state"]
    assert st["updown"]["status"] == "unconfirmed" and st["updown"]["message"] == UNCONF
    assert st["updown"]["confirmed_by"] is None and st["updown"]["fingerprint"] is None
    assert st["direction_names"] == {}
    r = w.hear(sid, "Zマイナス3ミリ")
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "-3.000")
    w.undo(sid)
    r = w.hear(sid, "Zのプラス3ミリ")
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "+3.000")


def test_1_default_webflow_has_no_config():
    w = WebFlow(interpreter=ci.MockInterpreter())
    assert w.updown_config is None
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "3ミリ下げて")
    assert r["status"] == "ask" and ut.is_updown_unset_ask(r["message"]) and cand(r) is None


@pytest.mark.parametrize("text,target,delta", [
    ("Z座標を3mm下げて", "C00005 Z", "-3.000"),   # 軸の文字＋値の上げ下げ＝値の増減（上下の向きに依存しない）
    ("Y座標を3mm下げて", "C00005 Y", "-3.000"),
    ("Z座標を3mm上げて", "C00005 Z", "+3.000"),
    ("Z方向に-4mm", "C00005 Z", "-4.000"),
])
def test_1_axis_value_changes_do_not_need_updown(text, target, delta, tmp_path):
    w, sid = start(tmp_path / "none.json")
    r = w.hear(sid, text)
    assert r["status"] == "ok" and cand(r) == (target, delta), r["message"]


@pytest.mark.parametrize("text", ["Z座標を下に3mm", "Yを3mm下げて", "Y座標を下に3mm", "3mm下げて？"])
def test_1_up_down_words_with_axis_letters_are_still_asked(text, tmp_path):
    w, sid = start(tmp_path / "none.json")
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None
    # 「Yを3mm下げて」は「Y の値を変えるなら…」に状態の文を足した文、「3mm下げて？」などは会話の聞き返しの文（11/5）
    assert ut.is_updown_unset_ask(r["message"]) or UNCONF in r["message"]
    assert not any("上方向" in c["label"] or "下方向" in c["label"] for c in r["choices"])


def test_1_d1_choice_keeps_only_the_value_reading(tmp_path):
    # 「Yを3mm下げて」：未確認では［下方向へ…］を出さず、Y の値の読み方だけを出す
    w, sid = start(tmp_path / "none.json")
    r = w.hear(sid, "Yを3mm下げて")
    assert [c["label"] for c in r["choices"]] == ["Y座標を −3mm"]


def test_1_pre_check_without_updown_is_unconfirmed(updown_ok):
    f = ut.read_utterance("C00005を3ミリ下げて")
    v = ut.pre_check(f)
    assert v.status == "ask" and ut.is_updown_unset_ask(v.message) and v.choices == ()
    assert ut.pre_check(f, None, updown_ok).status == "ok"
    p = ut.ProposedChange(kind="shift", position="C00005", axis="Z", delta_mm=-3.0)
    assert ut.check_proposal(f, p).status == "ask"  # Claude が Z−3 を出しても、未確認なら通さない
    assert ut.check_proposal(f, p, updown=updown_ok).status == "ok"
    assert ut.direction_names() == {} and ut.direction_names(updown_ok) == {"Z": {"+": "上", "-": "下"}}


def test_1_session_without_checker_is_unconfirmed():
    s = Session(jbi.parse_bytes(DEMO_C))
    s.select("C00005")
    r = s.hear("3ミリ下げて")
    assert r.status == "ask" and ut.is_updown_unset_ask(r.message)
    assert s.propose({"kind": "shift", "position": "C00005", "axis": "Z", "delta_mm": -3.0}).status == "stop"


def test_1_session_checker_error_is_unconfirmed():
    def broken(job):
        raise RuntimeError("x")
    s = Session(jbi.parse_bytes(DEMO_C), updown_check=broken)
    assert s.updown_status().status == "unconfirmed"


def test_1_claude_is_not_called_for_up_down_when_unconfirmed(tmp_path):
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            block = NS(type="tool_use", name="propose_position", id="t",
                       input={"position": "C00005", "direction": "下", "amount_mm": 3})
            return NS(stop_reason="tool_use", content=[block])
    w, sid = start(tmp_path / "none.json", interp=ci.ClaudeInterpreter("sk-ant-TEST-0123456789",
                                                                       client=NS(messages=FakeMessages())))
    r = w.hear(sid, "3ミリ下げて")
    assert r["status"] == "ask" and ut.is_updown_unset_ask(r["message"]) and calls == [] and cand(r) is None
    # 軸と符号を明示した指示は Claude に送り、Claude の「下」も照らし合わせ（Z−3）を通る
    r = w.hear(sid, "Zマイナス3ミリ")
    assert len(calls) == 1 and cand(r) == ("C00005 Z", "-3.000")


# ======================================================================
# 2. 正しい設定 ＋ DEMO_C_PLACE → 「3ミリ下げて」は Z−3、ボタンに「（下）」
# ======================================================================
def test_2_confirmed_config_reads_down_as_z_minus(make_updown):
    w, sid = start(make_updown())
    r = w.hear(sid, "3ミリ下げて")
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "-3.000")
    st = r["state"]["updown"]
    assert st["status"] == "confirmed"
    assert st["confirmed_by"] and st["confirmed_at"] == "2026-11-03T14:00:00+09:00" and len(st["fingerprint"]) == 64
    assert st["message"] == updown.MSG_CONFIRMED.format(by=st["confirmed_by"], at=st["confirmed_at"])
    assert r["state"]["direction_names"] == {"Z": {"+": "上", "-": "下"}}
    assert not ut.contains_forbidden(st["message"])


def test_2_confirmed_dialog_buttons_have_up_down_names(make_updown, tmp_path):
    w, sid = start(make_updown(), select="C00001")
    r = w.hear(sid, "置くところを3ミリ動かして")
    r = w.hear(sid, "そうそう")
    assert "Z−（下）3mm" in labels(r) and "Z＋（上）3mm" in labels(r)
    # 設定なしでは同じ会話でも「（上）」「（下）」を付けない
    w2, sid2 = start(tmp_path / "none.json", select="C00001")
    w2.hear(sid2, "置くところを3ミリ動かして")
    r2 = w2.hear(sid2, "そうそう")
    assert "Z− 3mm" in labels(r2) and "Z＋ 3mm" in labels(r2)
    assert not any("（上）" in x or "（下）" in x for x in labels(r2))


def test_direction_label_needs_names():
    assert dg.direction_label("Z-") == "Z−"
    assert dg.direction_label("Z-", {"Z": {"+": "上", "-": "下"}}) == "Z−（下）"
    assert dg.direction_label("Y+", {"Z": {"+": "上", "-": "下"}}) == "Y＋"


# ======================================================================
# 3. 設定のあとで中身を1文字変える → 変更済みの案内
# ======================================================================
@pytest.mark.parametrize("change", [
    lambda o: o.__setitem__("evidence", o["evidence"] + "。"),
    lambda o: o.__setitem__("confirmed_by", o["confirmed_by"] + "x"),
    lambda o: o.__setitem__("robot", "HC20DU"),
    lambda o: o["coordinate"].__setitem__("tool", 1),  # K4：structure_hash の項目は無い。座標系の値を変える
    lambda o: o.__setitem__("fingerprint", ("0" if o["fingerprint"][0] != "0" else "1") + o["fingerprint"][1:]),
])
def test_3_changed_config_gives_changed_message(change, make_updown):
    path = make_updown()
    rewrite(path, change)
    w, sid = start(path)
    r = w.hear(sid, "3ミリ下げて")
    assert r["status"] == "ask" and r["message"] == CHANGED and cand(r) is None and r["choices"] == []
    assert r["state"]["updown"]["status"] == "changed" and r["state"]["direction_names"] == {}
    assert r["state"]["updown"]["confirmed_by"] is None
    r = w.hear(sid, "Zマイナス3ミリ")
    assert cand(r) == ("C00005 Z", "-3.000")


def test_3_one_byte_edit_in_file_text(make_updown):
    path = make_updown()
    text = path.read_text(encoding="utf-8")
    assert "テスト用の仮の設定" in text
    path.write_text(text.replace("テスト用の仮の設定", "テスト用の仮の設置", 1), encoding="utf-8")
    assert updown.check(jbi.parse_bytes(DEMO_C), path).status == "changed"


# ======================================================================
# 4. 別の JOB／///USER 付き／ツール番号違い（／ロボット違い） → 不一致の案内
# ======================================================================
def test_4_other_job_with_same_coordinate_is_confirmed(make_updown):
    # CHECKER_028 K4（唐川さんの指示）：上下の向きはロボットの設置と座標系の性質。JOB の中身（位置・命令）だけが
    # 違うジョブは、上下の向きとしては確認済みになる（前は「別の JOB」で不一致だった。設計の変更）
    w, sid = start(make_updown(), data=DEMO, select="C00004")
    r = w.hear(sid, "3ミリ下げて")
    assert r["state"]["updown"]["status"] == "confirmed"
    assert cand(r) == ("C00004 Z", "-3.000")


@pytest.mark.parametrize("data", [WITH_USER, TOOL_1], ids=["user", "tool"])
def test_4_user_or_tool_gives_mismatch(data, make_updown):
    w, sid = start(make_updown(), data=data, select=None)
    pos = "C00005"
    w.select(sid, pos)
    for text in ("3ミリ下げて", "置くところを3ミリ上げて"):
        r = w.hear(sid, text)
        assert r["status"] == "ask" and r["message"] == MISMATCH, (text, r["message"])
        assert cand(r) is None
    assert r["state"]["updown"]["status"] == "mismatch" and r["state"]["direction_names"] == {}
    r = w.hear(sid, "Zマイナス3ミリ")
    assert r["status"] in ("ok", "stop") and r["message"] != MISMATCH  # 明示した軸は上下の照合と無関係（ジョブの検査は別）


def test_4_user_job_registered_by_hand_still_mismatches_on_coordinate(make_updown):
    # ///USER 付きのジョブの指紋で設定を書いても（register は断る）、座標系の照合で不一致
    with pytest.raises(updown.UpdownError):
        updown.build_config(jbi.parse_bytes(WITH_USER), "HC20DT", "x", "y")
    path = make_updown()
    job = jbi.parse_bytes(WITH_USER)
    st = updown.check(job, path)
    assert st.status == "mismatch" and "USER" in st.reason


def test_4_tool_number_in_config_must_match_job(make_updown):
    path = make_updown()
    rewrite(path, lambda o: o["coordinate"].__setitem__("tool", 1))
    refingerprint(path)
    st = updown.check(jbi.parse_bytes(DEMO_C), path)
    assert st.status == "mismatch" and "ツール番号" in st.reason


def test_4_robot_name_must_match(make_updown):
    w, sid = start(make_updown(), robot="HC10")
    r = w.hear(sid, "3ミリ下げて")
    assert r["message"] == MISMATCH and r["state"]["updown"]["status"] == "mismatch"
    assert updown.check(jbi.parse_bytes(DEMO_C), make_updown(robot="HC10DT"), "HC20DT").status == "mismatch"


def test_4_pulse_job_mismatches():
    data = (ROOT / "samples" / "dummy" / "PAT_POSTYPE_PULSE.JBI").read_bytes()
    with pytest.raises(updown.UpdownError):
        updown.build_config(jbi.parse_bytes(data), "HC20DT", "x", "y")


# ======================================================================
# 5. アプリで C00005 を直した _SIM を読み直す → 確認済みのまま
# ======================================================================
def test_5_reloaded_sim_output_stays_confirmed(make_updown):
    path = make_updown()
    w, sid = start(path)
    assert cand(w.hear(sid, "3ミリ下げて")) == ("C00005 Z", "-3.000")
    assert w.approve(sid, "唐川")["ok"]
    out = w.export(sid)
    assert out["ok"], out
    data = Path(out["path"]).read_bytes()
    assert b"//NAME DEMO_C_PLACE_SIM" in data and b"C00005=400.000,150.000,197.000" in data
    w2, sid2 = start(path, data=data)
    assert w2.state(sid2)["state"]["updown"]["status"] == "confirmed"
    r = w2.hear(sid2, "2ミリ下げて")
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "-2.000")
    # CHECKER_028 K4：C00005 以外を直したジョブも、座標系が同じなら上下の向きとしては確認済み（前は不一致）。
    # どの位置を変えてよいかは job_scope（FRAME v1.1）で別に照合する
    other = DEMO_C.replace(b"C00004=400.000,150.000,300.000", b"C00004=400.000,150.000,297.000")
    assert updown.check(jbi.parse_bytes(other), path).status == "confirmed"
    h = job_scope.structure_hash(jbi.parse_bytes(DEMO_C), ["C00005"])
    assert job_scope.matches(jbi.parse_bytes(data), h, ["C00005"]) is True
    assert job_scope.matches(jbi.parse_bytes(other), h, ["C00005"]) is False


# ======================================================================
# 6. 根拠が空・下: Z（符号なし）・下: Y-・知らない項目 → 読み込まない（未確認と同じ動き）
# ======================================================================
BROKEN = {
    "empty_evidence": lambda o: o.__setitem__("evidence", ""),
    "blank_evidence": lambda o: o.__setitem__("evidence", "   "),
    "down_Z_no_sign": lambda o: o["directions"].__setitem__("下", "Z"),
    "down_Y_minus": lambda o: o["directions"].__setitem__("下", "Y-"),
    "up_Z_minus": lambda o: o["directions"].__setitem__("上", "Z-"),
    "unknown_item": lambda o: o.__setitem__("note", "x"),
    "unknown_direction": lambda o: o["directions"].__setitem__("右", "Y+"),
    "old_job_item": lambda o: o.__setitem__("job", {"name": "DEMO_C_PLACE", "structure_hash": "0" * 64,
                                                    "positions": ["C00005"]}),  # 前の形（K4 で外した）
    "missing_item": lambda o: o.pop("confirmed_by"),
    "bool_tool": lambda o: o["coordinate"].__setitem__("tool", False),
    "float_tool": lambda o: o["coordinate"].__setitem__("tool", 0.0),
    "rectan_1": lambda o: o["coordinate"].__setitem__("rectan", 1),
    "user_set": lambda o: o["coordinate"].__setitem__("user", 1),
    "postype_pulse": lambda o: o["coordinate"].__setitem__("postype", "PULSE"),
    "bool_version": lambda o: o.__setitem__("version", True),
    "no_timezone": lambda o: o.__setitem__("confirmed_at", "2026-11-03T14:00:00"),
    "bad_date": lambda o: o.__setitem__("confirmed_at", "きのう"),
    "future_date": lambda o: o.__setitem__("confirmed_at", "2999-01-01T00:00:00+09:00"),
    "frame_user": lambda o: o["coordinate"].__setitem__("frame", "USER 1"),
    "frame_robot_without_rj": lambda o: o["coordinate"].__setitem__("frame", "ROBOT"),
    "rj_without_frame": lambda o: o["coordinate"].__setitem__("rj", True),
    "rj_not_bool": lambda o: o["coordinate"].__setitem__("rj", 0),
    "no_rj": lambda o: o["coordinate"].pop("rj"),
    "no_frame": lambda o: o["coordinate"].pop("frame"),
    "schema": lambda o: o.__setitem__("schema", "voice-teaching/frame"),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_6_broken_config_is_not_read(name, make_updown):
    path = make_updown()
    rewrite(path, BROKEN[name])
    refingerprint(path)  # 指紋を合わせても、形が正しくなければ読まない
    with pytest.raises(updown.UpdownError):
        updown.parse_config(path.read_bytes())
    w, sid = start(path)
    r = w.hear(sid, "3ミリ下げて")
    assert r["status"] == "ask" and ut.is_updown_unset_ask(r["message"]) and cand(r) is None
    assert r["state"]["updown"]["status"] == "unconfirmed"
    assert cand(w.hear(sid, "Zマイナス3ミリ")) == ("C00005 Z", "-3.000")


@pytest.mark.parametrize("raw", [
    b'{"version": NaN}',
    b"not json",
    b"[]",
    "﻿{}".encode("utf-8"),
    b"\xff\xfe",
])
def test_6_unreadable_bytes_are_not_read(raw, tmp_path):
    p = tmp_path / "u.json"
    p.write_bytes(raw)
    assert updown.check(jbi.parse_bytes(DEMO_C), p).status == "unconfirmed"


def test_6_nan_and_duplicate_keys_are_rejected(make_updown):
    path = make_updown()
    text = path.read_text(encoding="utf-8")
    nan = text.replace('"tool": 0', '"tool": NaN')
    assert nan != text
    with pytest.raises(updown.UpdownError):
        updown.parse_config(nan.encode("utf-8"))
    dup = text.replace('"robot": "HC20DT"', '"robot": "HC20DT", "robot": "HC20DT"')
    with pytest.raises(updown.UpdownError):
        updown.parse_config(dup.encode("utf-8"))


# ======================================================================
# 7. 承認のあとで設定を変える → 承認が消える／出力しない
# ======================================================================
def test_7_config_change_after_approval_cancels_and_refuses_export(make_updown):
    path = make_updown()
    w, sid = start(path)
    w.hear(sid, "3ミリ下げて")
    a = w.approve(sid, "唐川")
    assert a["ok"] and a["state"]["approval"]["updown_status"] == "confirmed"
    assert a["state"]["approval"]["updown_fingerprint"] == a["state"]["updown"]["fingerprint"]
    assert a["state"]["approval"]["updown_confirmed_by"] == a["state"]["updown"]["confirmed_by"]
    rewrite(path, BROKEN["empty_evidence"])
    out = w.export(sid)
    assert not out["ok"] and out["error"] == flow_mod.UPDOWN_CHANGED_EXPORT
    st = out["state"]
    assert st["approval"] is None and st["can_export"] is False and st["exported"] is None
    out_dir = Path(sim.DEFAULT_OUT_DIR)
    assert not (out_dir.exists() and any(out_dir.iterdir()))  # 何も書いていない
    assert any("承認を取り消しました" in x["text"] for x in st["log"])
    # 候補は残るが、設定が候補を作ったときと違うので承認できない
    assert st["can_approve"] is False
    r = w.approve(sid, "唐川")
    assert not r["ok"] and r["error"] == flow_mod.UPDOWN_CHANGED_APPROVE


def test_7_state_request_after_change_removes_approval(make_updown):
    path = make_updown()
    w, sid = start(path)
    w.hear(sid, "Zマイナス3ミリ")
    assert w.approve(sid, "唐川")["ok"]
    path.write_bytes(path.read_bytes() + b"\n")  # 1バイト（中身の意味は同じ）でも変われば承認を消す
    st = w.state(sid)["state"]
    assert st["approval"] is None and st["can_export"] is False
    assert not w.export(sid)["ok"]


@pytest.mark.parametrize("action", ["add", "remove"])
def test_7_config_added_or_removed_between_candidate_and_approval(action, make_updown, tmp_path):
    path = tmp_path / "updown" / "later.json"
    if action == "remove":
        path = make_updown(name="later.json")
    w, sid = start(path)
    r = w.hear(sid, "Zマイナス3ミリ")
    assert cand(r) == ("C00005 Z", "-3.000") and r["state"]["can_approve"]
    if action == "add":
        make_updown(name="later.json")
    else:
        path.unlink()
    st = w.state(sid)["state"]
    assert st["can_approve"] is False
    r = w.approve(sid, "唐川")
    assert not r["ok"] and r["error"] == flow_mod.UPDOWN_CHANGED_APPROVE


def test_7_approval_record_and_log_keep_fingerprint_and_person(make_updown, tmp_path):
    path = make_updown()
    w, sid = start(path)
    w.hear(sid, "3ミリ下げて")
    a = w.approve(sid, "唐川")
    fp = a["state"]["updown"]["fingerprint"]
    logs = [x["text"] for x in a["state"]["log"] if x["kind"] == "承認"]
    assert any(fp in t and "テスト（一時フォルダの仮の設定）" in t for t in logs)
    out = w.export(sid)
    assert out["ok"]
    assert any(fp in x["text"] for x in out["state"]["log"] if x["kind"] == "出力")
    # 設定なしで明示の軸の候補を承認した記録は「未確認」
    w2, sid2 = start(tmp_path / "none.json")
    w2.hear(sid2, "Zマイナス3ミリ")
    a2 = w2.approve(sid2, "唐川")
    assert a2["state"]["approval"]["updown_status"] == "unconfirmed"
    assert a2["state"]["approval"]["updown_fingerprint"] is None
    assert any("未確認" in x["text"] for x in a2["state"]["log"] if x["kind"] == "承認")


# ======================================================================
# 8. 会話の流れ（置くところ → そうそう → 「少し上に3ミリ」）でも、1〜4と同じ結果
# ======================================================================
def _state_config(kind, make_updown, tmp_path):
    if kind == "none":
        return tmp_path / "none.json", DEMO_C, UNCONF
    path = make_updown()
    if kind == "changed":
        rewrite(path, lambda o: o.__setitem__("evidence", o["evidence"] + "。"))
        return path, DEMO_C, CHANGED
    if kind == "mismatch_user":
        return path, WITH_USER, MISMATCH
    if kind == "mismatch_tool":
        return path, TOOL_1, MISMATCH
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["none", "changed", "mismatch_user", "mismatch_tool"])
@pytest.mark.parametrize("answer", ["少し上に3ミリ", "上に3ミリ", "3ミリ下げて"])
def test_8_dialog_follows_the_same_updown_rule(kind, answer, make_updown, tmp_path):
    path, data, msg = _state_config(kind, make_updown, tmp_path)
    w, sid = start(path, data=data, select="C00001")
    r = w.hear(sid, "置くところ")
    if r["state"]["dialog"]["step"] == "confirm_position":
        r = w.hear(sid, "そうそう")
    else:  # 架空のサンプルでないジョブ（///USER 付きなど）：人が位置を選ぶ
        r = w.select(sid, "C00005")
    assert r["state"]["dialog"]["step"] == "ask_move"
    assert not any("（上）" in x or "（下）" in x for x in labels(r))
    r = w.hear(sid, answer)
    assert r["status"] == "ask" and (ut.is_updown_unset_ask(r["message"]) if msg == UNCONF else r["message"] == msg) and cand(r) is None, r["message"]
    assert not any("（上）" in x or "（下）" in x for x in labels(r))
    # 同じ会話の続きで、軸と符号を明示すれば候補（方向のボタンでも同じ）
    r = w.hear(sid, "Zのマイナス3ミリ")
    assert cand(r) == ("C00005 Z", "-3.000"), r["message"]


@pytest.mark.parametrize("kind", ["none", "changed", "mismatch_user", "mismatch_tool"])
@pytest.mark.parametrize("opening", ["置くところを少し上に", "置くところを3ミリ上げて", "置くところを下に3mm"])
def test_8_dialog_opening_with_up_down_is_asked(kind, opening, make_updown, tmp_path):
    path, data, msg = _state_config(kind, make_updown, tmp_path)
    w, sid = start(path, data=data, select="C00001")
    r = w.hear(sid, opening)
    assert r["status"] == "ask" and (ut.is_updown_unset_ask(r["message"]) if msg == UNCONF else r["message"] == msg) and cand(r) is None
    assert r["state"]["dialog"]["step"] is None
    assert r["state"]["dialog"]["direction"] is None


def test_8_dialog_with_confirmed_config(make_updown):
    w, sid = start(make_updown(), select="C00001")
    r = w.hear(sid, "置くところ")
    r = w.hear(sid, "そうそう")
    assert "Z＋（上）" in labels(r)
    r = w.hear(sid, "上に3ミリ")
    assert r["status"] == "ok" and cand(r) == ("C00005 Z", "+3.000")
    # 「少し上に3ミリ」は、確認済みでも今どおり量（「少し」）を聞き返す（量を推測しない）
    w2, sid2 = start(make_updown(name="b.json"), select="C00001")
    w2.hear(sid2, "置くところ")
    w2.hear(sid2, "そうそう")
    r2 = w2.hear(sid2, "少し上に3ミリ")
    assert r2["status"] == "ask" and r2["message"].startswith("量がはっきりしません") and cand(r2) is None
    # 始めの発話の「上に」も、確認済みなら会話で Z＋ として持つ
    w3, sid3 = start(make_updown(name="c.json"), select="C00001")
    r3 = w3.hear(sid3, "置くところを少し上に")
    assert r3["state"]["dialog"]["step"] == "confirm_position"
    r3 = w3.hear(sid3, "そうそう")
    assert r3["state"]["dialog"]["direction"] == "Z+" and "Z＋（上）" in r3["message"]


def test_8_read_slots_does_not_turn_up_down_into_z_without_confirmation(updown_ok):
    f = ut.read_utterance("上に3ミリ")
    s = dg.read_slots(f)
    assert s.problem == UNCONF and s.axis is None and s.sign is None
    s = dg.read_slots(f, updown=updown_ok)
    assert (s.axis, s.sign, s.amount) == ("Z", 1, 3.0)
    assert dg.handled_by_dialogue(ut.read_utterance("この位置を上に移動")) is False
    assert dg.handled_by_dialogue(ut.read_utterance("この位置を上に移動"), updown_ok) is True


# ======================================================================
# 照合は毎回、設定を読み直す
# ======================================================================
def test_config_is_reread_on_every_instruction(make_updown, tmp_path):
    path = tmp_path / "updown" / "live.json"
    w, sid = start(path)
    assert ut.is_updown_unset_ask(w.hear(sid, "3ミリ下げて")["message"])
    make_updown(name="live.json")
    r = w.hear(sid, "3ミリ下げて")
    assert cand(r) == ("C00005 Z", "-3.000")
    w.undo(sid)
    path.unlink()
    assert ut.is_updown_unset_ask(w.hear(sid, "3ミリ下げて")["message"])


def test_messages_have_no_forbidden_words():
    for m in (UNCONF, MISMATCH, CHANGED, updown.MSG_CONFIRMED, flow_mod.UPDOWN_CHANGED_APPROVE,
              flow_mod.UPDOWN_CHANGED_EXPORT):
        assert not ut.contains_forbidden(m)


# ======================================================================
# CLI（専門家が使う。テストでは一時フォルダだけ）
# ======================================================================
def run_cli(argv, answer="yes"):
    lines = []
    code = updown.main(argv, input_fn=lambda prompt: answer, out=lines.append)
    return code, "\n".join(lines)


def test_cli_register_needs_yes_and_writes_config(tmp_path):
    cfg = tmp_path / "records" / "updown.json"
    base = ["register", "--job", str(DEMO_C_PATH), "--robot", "HC20DT",
            "--by", "テスト", "--evidence", "テストの根拠", "--config", str(cfg)]
    code, out = run_cli(base, answer="no")
    assert code == 3 and not cfg.exists() and "書きませんでした" in out
    code, out = run_cli(base, answer="yes")
    assert code == 0 and cfg.exists() and "本人確認・安全の保証ではありません" in out
    obj = json.loads(cfg.read_text(encoding="utf-8"))
    assert obj["confirmed_by"] == "テスト" and obj["fingerprint"] == updown.content_fingerprint(obj)
    assert updown.datetime.fromisoformat(obj["confirmed_at"]).tzinfo is not None
    code, out = run_cli(base, answer="yes")
    assert code == 2 and "既にある" in out  # 上書きしない
    code, out = run_cli(["check", "--job", str(DEMO_C_PATH), "--config", str(cfg)])
    assert code == 0 and "confirmed" in out
    # K4：座標系が同じ別のジョブ（DEMO_PLACE）は確認済み。ツール番号が違うジョブ（PAT_POSTYPE_ROBOT は TOOL 1）は不一致
    code, out = run_cli(["check", "--job", str(ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI"), "--config", str(cfg)])
    assert code == 0 and "confirmed" in out
    code, out = run_cli(["check", "--job", str(ROOT / "samples" / "dummy" / "PAT_POSTYPE_ROBOT.JBI"), "--config", str(cfg)])
    assert code == 1 and "mismatch" in out and MISMATCH in out


def test_cli_register_refuses_eof_and_bad_jobs(tmp_path):
    cfg = tmp_path / "u.json"

    def eof(_p):
        raise EOFError
    lines = []
    code = updown.main(["register", "--job", str(DEMO_C_PATH), "--by", "a",
                        "--evidence", "b", "--config", str(cfg)], input_fn=eof, out=lines.append)
    assert code == 3 and not cfg.exists()
    pulse = ROOT / "samples" / "dummy" / "PAT_POSTYPE_PULSE.JBI"
    code, out = run_cli(["register", "--job", str(pulse), "--by", "a",
                         "--evidence", "b", "--config", str(cfg)])
    assert code == 2 and not cfg.exists()
    # K4：伏せる位置の指定（--positions）は無くなった。渡すと引数の誤りで止まり、何も書かない
    with pytest.raises(SystemExit):
        run_cli(["register", "--job", str(DEMO_C_PATH), "--positions", "C00005", "--by", "a",
                 "--evidence", "b", "--config", str(cfg)])
    assert not cfg.exists()
    code, out = run_cli(["register", "--job", str(DEMO_C_PATH), "--by", "a",
                         "--evidence", " ", "--config", str(cfg)])
    assert code == 2 and not cfg.exists()


def test_server_option_defaults():
    from voice_teaching.web import __main__ as web_main
    import inspect
    src = inspect.getsource(web_main.main)
    assert "--updown-config" in src and "--robot" in src
    assert updown.DEFAULT_PATH == ROOT / "records" / "updown.json" and updown.DEFAULT_ROBOT == "HC20DT"
    assert "records/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_make_server_passes_config(make_updown):
    from voice_teaching.web import server
    path = make_updown()
    srv = server.make_server(port=0, interpreter=ci.MockInterpreter(), updown_config=path, robot="HC20DT")
    try:
        assert srv.flow.updown_config == path and srv.flow.robot == "HC20DT"
    finally:
        srv.server_close()
    srv = server.make_server(port=0, interpreter=ci.MockInterpreter())
    try:
        assert srv.flow.updown_config is None  # 既定は設定なし（未確認）
    finally:
        srv.server_close()
