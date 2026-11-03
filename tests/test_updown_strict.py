"""上下の向きの照合と、JOB の範囲の照合を分ける（CHECKER_028 K4・唐川さんの指示 2026-11-03）の受入テスト。実装より先に書いた。

- 上下の向き（updown）：ロボットの設置と座標系の性質。JOB の中身（structure_hash・位置の値）では照合しない。
  照合するのは、ロボット・座標系（POSTYPE ROBOT・RECTAN・USER なし・FRAME・ATTR の RJ）・ツール番号・
  向きの対応・確認した人・日時（未来は不可）・根拠・設定の指紋だけ
  → JOB の中身だけが違うジョブは、上下の向きとしては確認済みになる（設計の変更点）
- 変更の許可の範囲（job_scope）：JOB の指紋と対象の位置で厳密に照合する部品（FRAME v1.1 用。枠そのものはまだ作らない）。
  伏せてよいのは、指定した対象の位置の X・Y・Z だけ。全部の位置・空・ジョブに無い位置・変更できない位置の指定は断る

設定は一時フォルダのものだけ（conftest.make_updown）。実機で上下を確かめた設定ではない。照合に通ることは安全の保証ではない。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from voice_teaching import jbi, job_scope, sim, updown

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy"
DEMO_C = (DUMMY / "DEMO_C_PLACE.JBI").read_bytes()
DEMO = (DUMMY / "DEMO_PLACE.JBI").read_bytes()
RJ = (DUMMY / "DEMO_RJ_XYZ.JBI").read_bytes()
NL = b"\r\n"
FRAME = b"////FRAME ROBOT" + NL


def replace1(data, old, new):
    assert data.count(old) == 1, old
    return data.replace(old, new)


def load_obj(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_obj(path, obj):
    obj["fingerprint"] = updown.content_fingerprint(obj)
    Path(path).write_bytes(updown.dump(obj))


def status(data: bytes, path, robot=updown.DEFAULT_ROBOT):
    return updown.check(jbi.parse_bytes(data), path, robot)


# ======================================================================
# 1. 上下の向き：JOB の中身では照合しない（設計の変更点）
# ======================================================================
def test_config_has_no_job_content(make_updown):
    obj = load_obj(make_updown())
    assert set(obj) == {"schema", "version", "directions", "confirmed_by", "confirmed_at", "evidence", "robot",
                        "coordinate", "fingerprint"}
    assert obj["coordinate"] == {"postype": "ROBOT", "rectan": True, "user": None, "tool": 0, "frame": None, "rj": False}
    rel = load_obj(make_updown("DEMO_RJ_XYZ.JBI", name="rj.json"))
    assert rel["coordinate"] == {"postype": "ROBOT", "rectan": True, "user": None, "tool": 8,
                                 "frame": "ROBOT", "rj": True}


@pytest.mark.parametrize("data", [
    DEMO,  # 別のジョブ（位置・命令が違う。座標系・ツールは同じ）
    replace1(DEMO_C, b"C00004=400.000,150.000,300.000", b"C00004=400.000,150.000,301.000"),  # 位置の値だけ違う
    replace1(DEMO_C, b"C00001=400.000,-150.000,300.000", b"C00001=401.000,-150.000,300.000"),
], ids=["other_job", "c4_z", "c1_x"])
def test_job_content_only_difference_is_confirmed_for_updown(make_updown, data):
    st = status(data, make_updown())
    assert st.confirmed, st


@pytest.mark.parametrize("case,data,robot", [
    ("user", replace1(DEMO_C, b"///TOOL 0" + NL, b"///TOOL 0" + NL + b"///USER 1" + NL), "HC20DT"),
    ("tool", replace1(DEMO_C, b"///TOOL 0" + NL, b"///TOOL 1" + NL), "HC20DT"),
    ("robot", DEMO_C, "HC10DT"),
    ("pulse", (DUMMY / "PAT_POSTYPE_PULSE.JBI").read_bytes(), "HC20DT"),
    ("rj_added", replace1(DEMO_C, b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL + FRAME), "HC20DT"),
    ("rj_without_frame", replace1(DEMO_C, b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL), "HC20DT"),
    ("frame_user1", replace1(DEMO_C, b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL + b"////FRAME USER 1" + NL),
     "HC20DT"),
])
def test_applicability_differences_are_mismatch(make_updown, case, data, robot):
    st = status(data, make_updown(), robot)
    assert st.status == updown.MISMATCH, (case, st)


@pytest.mark.parametrize("case,data", [
    ("plain", replace1(RJ, b"///ATTR SC,RW,RJ" + NL + FRAME, b"///ATTR SC,RW" + NL)),  # ふつうのジョブ
    ("no_frame", replace1(RJ, FRAME, b"")),  # RJ だけ（K3 で止まる）
    ("frame_user1", replace1(RJ, FRAME, b"////FRAME USER 1" + NL)),
    ("frame_base", replace1(RJ, FRAME, b"////FRAME BASE" + NL)),
    ("tool0", replace1(RJ, b"///TOOL 8", b"///TOOL 0")),
])
def test_relative_job_config_does_not_match_other_coordinates(make_updown, case, data):
    path = make_updown("DEMO_RJ_XYZ.JBI")
    assert status(RJ, path).confirmed
    st = status(data, path)
    assert st.status == updown.MISMATCH, (case, st)


def test_relative_job_with_other_values_is_confirmed(make_updown):
    path = make_updown("DEMO_RJ_XYZ.JBI")
    other = replace1(RJ, b"C00003=680.000,140.000,180.000", b"C00003=690.000,150.000,170.000")
    assert status(other, path).confirmed


@pytest.mark.parametrize("change", [
    {"frame": "ROBOT", "rj": False},
    {"frame": None, "rj": True},
    {"frame": "USER 1"},
    {"frame": "BASE"},
    {"rj": "yes"},
    {"rj": 1},
])
def test_config_with_bad_frame_fields_is_not_read(make_updown, change):
    path = make_updown()
    obj = load_obj(path)
    obj["coordinate"].update(change)
    save_obj(path, obj)
    assert status(DEMO_C, path).status == updown.UNCONFIRMED


def test_config_frame_or_rj_changed_to_other_valid_value_is_mismatch(make_updown):
    path = make_updown()
    obj = load_obj(path)
    obj["coordinate"].update({"frame": "ROBOT", "rj": True})
    save_obj(path, obj)
    st = status(DEMO_C, path)
    assert st.status == updown.MISMATCH and "RJ" in st.reason


@pytest.mark.parametrize("change", [
    lambda o: o.__setitem__("job", {"name": "DEMO_C_PLACE", "structure_hash": "0" * 64, "positions": ["C00005"]}),
    lambda o: o["coordinate"].pop("rj"),
    lambda o: o["coordinate"].pop("frame"),
    lambda o: o.__setitem__("evidence", ""),
    lambda o: o.__setitem__("confirmed_at", "2999-01-01T00:00:00+09:00"),
], ids=["old_job_item", "no_rj", "no_frame", "empty_evidence", "future"])
def test_old_or_broken_config_is_not_read(make_updown, change):
    path = make_updown()
    obj = load_obj(path)
    change(obj)
    save_obj(path, obj)
    assert status(DEMO_C, path).status == updown.UNCONFIRMED


def test_future_date_is_compared_with_now():
    obj = updown.build_config(jbi.parse_bytes(DEMO_C), "HC20DT", "テスト", "テスト用",
                              confirmed_at="2026-11-03T14:00:00+09:00")
    data = updown.dump(obj)
    past = datetime(2026, 11, 3, 4, 59, tzinfo=timezone.utc)  # 14:00+09:00 の1分前
    with pytest.raises(updown.UpdownError, match="未来"):
        updown.parse_config(data, now=past)
    updown.parse_config(data, now=past + timedelta(minutes=1))


def test_problem_job_is_mismatch(make_updown):
    bad = replace1(DEMO_C, b"MOVL C00003 V=50.0 PL=0" + NL, b"MOVL C00003 V=50.0 PL=0" + NL + b"WAIT IN#(1)=ON" + NL)
    st = status(bad, make_updown())
    assert st.status == updown.MISMATCH and "読めない所" in st.reason


@pytest.mark.parametrize("name,position", [("DEMO_C_PLACE.JBI", "C00005"), ("DEMO_RJ_XYZ.JBI", "C00003")])
def test_sim_output_stays_confirmed(make_updown, name, position):
    path = make_updown(name)
    job = jbi.load(DUMMY / name)
    p = sim.simulate_shift(job, position, "Z", -3.0)
    out = sim.export_sim(p, sim.approve(p, "テスト"))
    assert updown.check(jbi.load(out), path).confirmed


# ======================================================================
# 2. 変更の許可の範囲（job_scope。FRAME v1.1 用の部品）：JOB の指紋と対象の位置で厳密に照合
# ======================================================================
def test_scope_refuses_masking_all_positions():
    job = jbi.parse_bytes(DEMO_C)
    with pytest.raises(job_scope.ScopeError, match="全部の位置"):
        job_scope.check_mask(job, sorted(job.positions))
    with pytest.raises(job_scope.ScopeError):
        job_scope.structure_hash(job, sorted(job.positions))


@pytest.mark.parametrize("positions,words", [
    ([], "空"),
    (["C00099"], "ありません"),
    (["C00005", "C00005"], "重複"),
    (["c00005"], "名前"),
    ("C00005", "形"),
    (["C00000"], "変更できない位置"),
    (["C00007"], "変更できない位置"),
])
def test_scope_refuses_bad_masks(positions, words):
    with pytest.raises(job_scope.ScopeError, match=words):
        job_scope.structure_hash(jbi.parse_bytes(DEMO_C), positions)


@pytest.mark.parametrize("position", ["C00000", "C00001", "C00005", "C00006"])
def test_scope_refuses_positions_next_to_movj(position):
    with pytest.raises(job_scope.ScopeError, match="変更できない位置"):
        job_scope.structure_hash(jbi.parse_bytes(RJ), [position])


@pytest.mark.parametrize("old,new", [
    (b"C00004=400.000,150.000,300.000", b"C00004=400.000,150.000,301.000"),  # 伏せない位置の Z
    (b"C00001=400.000,-150.000,300.000", b"C00001=401.000,-150.000,300.000"),  # 伏せない位置の X
    (b"C00005=400.000,150.000,200.000,180.00", b"C00005=400.000,150.000,200.000,179.00"),  # 伏せた位置の Rx
])
def test_scope_same_instructions_different_values_do_not_match(old, new):
    h = job_scope.structure_hash(jbi.parse_bytes(DEMO_C), ["C00005"])
    assert job_scope.matches(jbi.parse_bytes(replace1(DEMO_C, old, new)), h, ["C00005"]) is False


@pytest.mark.parametrize("old,new", [
    (b"///ATTR SC,RW,RJ" + NL + FRAME, b"///ATTR SC,RW" + NL),
    (FRAME, b""),
])
def test_scope_frame_only_difference_does_not_match(old, new):
    h = job_scope.structure_hash(jbi.parse_bytes(RJ), ["C00003"])
    other = jbi.parse_bytes(replace1(RJ, old, new))
    assert job_scope.matches(other, h, ["C00003"]) is False


@pytest.mark.parametrize("name,position", [("DEMO_C_PLACE.JBI", "C00005"), ("DEMO_RJ_XYZ.JBI", "C00003")])
def test_scope_sim_output_with_only_masked_position_changed_matches(name, position):
    job = jbi.load(DUMMY / name)
    h = job_scope.structure_hash(job, [position])
    p = sim.simulate_shift(job, position, "Z", -3.0)
    out = jbi.load(sim.export_sim(p, sim.approve(p, "テスト")))
    assert job_scope.matches(out, h, [position]) is True
    # 伏せていない位置を直した _SIM は一致しない
    other = "C00004" if position == "C00005" else "C00002"
    p2 = sim.simulate_shift(job, other, "Z", -3.0)
    assert job_scope.matches(jbi.parse_bytes(p2.new_bytes), h, [position]) is False


def test_scope_matches_refuses_bad_input():
    job = jbi.parse_bytes(DEMO_C)
    h = job_scope.structure_hash(job, ["C00005"])
    assert job_scope.matches(job, h, ["C00005"]) is True
    assert job_scope.matches(job, h, sorted(job.positions)) is False
    assert job_scope.matches(job, None, ["C00005"]) is False


# ======================================================================
# 3. register の CLI：JOB の中身を設定に入れない。相対ジョブはその旨を表示する
# ======================================================================
def _cli(argv):
    lines = []
    code = updown.main(argv, input_fn=lambda prompt: "yes", out=lines.append)
    return code, "\n".join(lines)


def test_cli_register_relative_job_shows_frame(tmp_path):
    cfg = tmp_path / "u.json"
    code, out = _cli(["register", "--job", str(DUMMY / "DEMO_RJ_XYZ.JBI"),
                      "--by", "テスト", "--evidence", "テスト用", "--config", str(cfg)])
    assert code == 0 and cfg.exists(), out
    assert "相対ジョブ" in out and "FRAME ROBOT" in out and "ツール番号 8" in out
    assert "job" not in load_obj(cfg)
    assert updown.check(jbi.load(DUMMY / "DEMO_RJ_XYZ.JBI"), cfg).confirmed
    assert updown.check(jbi.load(DUMMY / "DEMO_C_PLACE.JBI"), cfg).status == updown.MISMATCH


@pytest.mark.parametrize("data", [
    replace1(RJ, FRAME, b""),
    replace1(RJ, FRAME, b"////FRAME USER 1" + NL),
], ids=["rj_without_frame", "frame_user1"])
def test_cli_register_refuses_unclear_frame(tmp_path, data):
    job = tmp_path / "J.JBI"
    job.write_bytes(data)
    cfg = tmp_path / "u.json"
    code, out = _cli(["register", "--job", str(job), "--by", "テスト", "--evidence", "テスト用", "--config", str(cfg)])
    assert code == 2 and "設定を作りません" in out and not cfg.exists()
