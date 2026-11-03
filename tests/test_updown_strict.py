"""上下の向きの設定の照合を厳しくする（唐川さんの判断③ 2026-11-03）の受入テスト。実装より先に書いた。

判断：ジョブの照合で、すべての座標値を伏せる方式は採らない（違うジョブを同じものとして扱う危険があるため）。
座標系・FRAME・ATTR の RJ・ロボットの名前・ツール番号・位置の値まで含めて照合する。照合できなければ未確認／不一致。
- 伏せる位置（positions）は一部だけ。全部の位置・半分以上の位置・上限（3つ）を超える数・変更できない位置は断る
- 伏せない位置は X・Y・Z も含めてそのまま指紋に入る
- 設定の coordinate に frame（null／"ROBOT"）と relative_job（ATTR の RJ の有無）を持ち、ジョブと照合する
- _SIM の読み直し（伏せた位置だけが変わったもの）は確認済みのまま

設定は一時フォルダのものだけ（conftest.make_updown）。実機で上下を確かめた設定ではない。
"""
import json
from pathlib import Path

import pytest

from voice_teaching import jbi, sim, updown

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy"
DEMO_C = (DUMMY / "DEMO_C_PLACE.JBI").read_bytes()
RJ = (DUMMY / "DEMO_RJ_XYZ.JBI").read_bytes()
NL = b"\r\n"


def load_obj(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_obj(path, obj, refingerprint=True):
    if refingerprint:
        obj["fingerprint"] = updown.content_fingerprint(obj)
    Path(path).write_bytes(updown.dump(obj))


def forced(make_updown, data: bytes, positions, base="DEMO_C_PLACE.JBI", base_positions=("C00005",), name="f.json"):
    """base で作った設定の、伏せる位置と structure_hash を data・positions のものに書き換える（指紋も計算し直す）。

    build_config が断る設定を、手で書いた場合の照合を試すためのもの。
    """
    path = make_updown(base, base_positions, name=name)
    obj = load_obj(path)
    obj["job"]["positions"] = list(positions)
    obj["job"]["structure_hash"] = updown.structure_hash(jbi.parse_bytes(data), list(positions))
    save_obj(path, obj)
    return path


def replace1(data, old, new):
    assert data.count(old) == 1, old
    return data.replace(old, new)


# ======================================================================
# 1. 伏せる位置は一部だけ
# ======================================================================
def test_build_config_refuses_masking_all_positions():
    job = jbi.parse_bytes(DEMO_C)
    with pytest.raises(updown.UpdownError, match="伏せる位置"):
        updown.build_config(job, sorted(job.positions), "HC20DT", "テスト", "テスト用")


def test_build_config_refuses_masking_too_many_positions():
    job = jbi.parse_bytes(DEMO_C)  # 8点のうち、変更できる位置を4つ（半分）伏せる
    with pytest.raises(updown.UpdownError, match="伏せる位置"):
        updown.build_config(job, ["C00002", "C00003", "C00004", "C00005"], "HC20DT", "テスト", "テスト用")


@pytest.mark.parametrize("data,position", [(DEMO_C, "C00000"), (DEMO_C, "C00007"), (RJ, "C00000"), (RJ, "C00001"),
                                           (RJ, "C00005"), (RJ, "C00006")])
def test_build_config_refuses_masking_a_position_that_cannot_change(data, position):
    with pytest.raises(updown.UpdownError, match="変更できない位置"):
        updown.build_config(jbi.parse_bytes(data), [position], "HC20DT", "テスト", "テスト用")


def test_config_masking_all_positions_is_not_confirmed(make_updown):
    job = jbi.parse_bytes(DEMO_C)
    path = forced(make_updown, DEMO_C, sorted(job.positions))
    st = updown.check(job, path)
    assert not st.confirmed and st.status in (updown.UNCONFIRMED, updown.MISMATCH), st


def test_config_masking_more_than_the_limit_is_not_read(make_updown):
    path = forced(make_updown, DEMO_C, ["C00001", "C00002", "C00003", "C00004"])
    st = updown.check(jbi.parse_bytes(DEMO_C), path)
    assert st.status == updown.UNCONFIRMED and "伏せる位置" in st.reason, st


def test_config_masking_half_of_positions_is_not_confirmed(make_updown):
    # 上限の3つ以内でも、ジョブの位置の半分以上を伏せる設定は通さない（RJ は7点。3つ伏せると残りは4点）
    small = replace1(RJ, b"C00006=260.000,310.000,520.000,-179.5000,1.2500,-12.0000" + NL, b"")
    small = replace1(small, b"MOVJ C00006 VJ=25.00" + NL, b"").replace(b"///NPOS 7,", b"///NPOS 6,")
    job = jbi.parse_bytes(small)
    assert job.problems == []
    path = forced(make_updown, small, ["C00002", "C00003", "C00004"], base="DEMO_RJ_XYZ.JBI",
                  base_positions=("C00003",))
    st = updown.check(job, path)
    assert st.status == updown.MISMATCH and "伏せる位置" in st.reason, st


@pytest.mark.parametrize("position", ["C00000", "C00007"])
def test_config_masking_a_position_that_cannot_change_is_not_confirmed(make_updown, position):
    path = forced(make_updown, DEMO_C, [position])
    st = updown.check(jbi.parse_bytes(DEMO_C), path)
    assert st.status == updown.MISMATCH and "変更できない位置" in st.reason, st


def test_three_editable_positions_of_seven_can_be_masked(make_updown):
    path = make_updown("DEMO_RJ_XYZ.JBI", ("C00002", "C00003", "C00004"))
    assert updown.check(jbi.parse_bytes(RJ), path).confirmed


# ======================================================================
# 2. 伏せない位置の X・Y・Z はそのまま照合に入る
# ======================================================================
@pytest.mark.parametrize("old,new", [
    (b"C00004=400.000,150.000,300.000", b"C00004=400.000,150.000,301.000"),  # 伏せない位置の Z
    (b"C00001=400.000,-150.000,300.000", b"C00001=401.000,-150.000,300.000"),  # 伏せない位置の X
    (b"C00005=400.000,150.000,200.000,180.00", b"C00005=400.000,150.000,200.000,179.00"),  # 伏せた位置の Rx
])
def test_same_instructions_with_different_values_is_mismatch(make_updown, old, new):
    data = replace1(DEMO_C, old, new)
    st = updown.check(jbi.parse_bytes(data), make_updown())
    assert st.status == updown.MISMATCH, st


# ======================================================================
# 3. FRAME と ATTR の RJ：設定の上でも区別する
# ======================================================================
def test_config_records_frame_and_relative_job(make_updown):
    plain = load_obj(make_updown())
    assert plain["coordinate"] == {"postype": "ROBOT", "rectan": True, "user": None, "tool": 0,
                                   "frame": None, "relative_job": False}
    rel = load_obj(make_updown("DEMO_RJ_XYZ.JBI", ("C00003",), name="rj.json"))
    assert rel["coordinate"] == {"postype": "ROBOT", "rectan": True, "user": None, "tool": 8,
                                 "frame": "ROBOT", "relative_job": True}


@pytest.mark.parametrize("old,new", [
    (b"///ATTR SC,RW,RJ" + NL + b"////FRAME ROBOT" + NL, b"///ATTR SC,RW" + NL),  # ふつうのジョブ
    (b"////FRAME ROBOT" + NL, b""),  # RJ はあるが FRAME の行が無い
])
def test_jobs_differing_only_in_frame_are_mismatch(make_updown, old, new):
    data = replace1(RJ, old, new)
    path = make_updown("DEMO_RJ_XYZ.JBI", ("C00003",))
    assert updown.check(jbi.parse_bytes(data), path).status == updown.MISMATCH
    # 指紋だけをそのジョブのものにしても、座標系（frame・relative_job）の照合で不一致
    p2 = forced(make_updown, data, ["C00003"], base="DEMO_RJ_XYZ.JBI", base_positions=("C00003",), name="f2.json")
    st = updown.check(jbi.parse_bytes(data), p2)
    assert st.status == updown.MISMATCH and ("FRAME" in st.reason or "RJ" in st.reason), st


def test_plain_job_config_does_not_match_relative_version(make_updown):
    # ふつうのジョブの設定を、相対ジョブの形（RJ＋FRAME ROBOT）のジョブに当てても不一致
    rel = replace1(DEMO_C, b"///ATTR SC,RW" + NL, b"///ATTR SC,RW,RJ" + NL + b"////FRAME ROBOT" + NL)
    job = jbi.parse_bytes(rel)
    assert job.problems == [] and job.relative_frame == "ROBOT"
    assert updown.check(job, make_updown()).status == updown.MISMATCH
    st = updown.check(job, forced(make_updown, rel, ["C00005"]))
    assert st.status == updown.MISMATCH and ("FRAME" in st.reason or "RJ" in st.reason), st


@pytest.mark.parametrize("coord", [
    {"frame": "ROBOT", "relative_job": False},  # FRAME ROBOT は RJ のときだけ
    {"frame": None, "relative_job": True},      # RJ なのに座標系が無い
    {"frame": "USER 1"},
    {"frame": "BASE"},
    {"relative_job": "yes"},
    {"relative_job": 1},
])
def test_config_with_bad_frame_fields_is_not_read(make_updown, coord):
    path = make_updown()
    obj = load_obj(path)
    obj["coordinate"].update(coord)
    save_obj(path, obj)
    st = updown.check(jbi.parse_bytes(DEMO_C), path)
    assert st.status == updown.UNCONFIRMED, st


@pytest.mark.parametrize("key", ["frame", "relative_job"])
def test_old_config_without_frame_fields_is_not_read(make_updown, key):
    path = make_updown()
    obj = load_obj(path)
    del obj["coordinate"][key]
    save_obj(path, obj)
    assert updown.check(jbi.parse_bytes(DEMO_C), path).status == updown.UNCONFIRMED


def test_robot_name_and_tool_still_checked(make_updown):
    path = make_updown("DEMO_RJ_XYZ.JBI", ("C00003",))
    job = jbi.parse_bytes(RJ)
    assert updown.check(job, path, robot="HC10").status == updown.MISMATCH
    obj = load_obj(path)
    obj["coordinate"]["tool"] = 0
    save_obj(path, obj)
    st = updown.check(job, path)
    assert st.status == updown.MISMATCH and "ツール番号" in st.reason


# ======================================================================
# 4. _SIM の読み直し（伏せた位置だけが変わったもの）は確認済みのまま
# ======================================================================
@pytest.mark.parametrize("name,position", [("DEMO_C_PLACE.JBI", "C00005"), ("DEMO_RJ_XYZ.JBI", "C00003")])
def test_sim_output_with_only_masked_position_changed_stays_confirmed(make_updown, name, position):
    path = make_updown(name, (position,))
    job = jbi.load(DUMMY / name)
    p = sim.simulate_shift(job, position, "Z", -3.0)
    assert p.blocked_reason is None
    out = sim.export_sim(p, sim.approve(p, "テスト"))
    st = updown.check(jbi.load(out), path)
    assert st.confirmed, st


# ======================================================================
# 5. register の CLI も、全部の位置・変更できない位置を伏せようとしたら断る
# ======================================================================
def _cli(argv):
    lines = []
    code = updown.main(argv, input_fn=lambda prompt: "yes", out=lines.append)
    return code, "\n".join(lines)


@pytest.mark.parametrize("positions", ["C00000,C00001,C00002,C00003,C00004,C00005,C00006,C00007", "C00000"])
def test_cli_register_refuses_bad_masks(tmp_path, positions):
    cfg = tmp_path / "u.json"
    code, out = _cli(["register", "--job", str(DUMMY / "DEMO_C_PLACE.JBI"), "--positions", positions,
                      "--by", "テスト", "--evidence", "テスト用", "--config", str(cfg)])
    assert code == 2 and "設定を作りません" in out and not cfg.exists()


def test_cli_register_relative_job_shows_frame(tmp_path):
    cfg = tmp_path / "u.json"
    code, out = _cli(["register", "--job", str(DUMMY / "DEMO_RJ_XYZ.JBI"), "--positions", "C00003",
                      "--by", "テスト", "--evidence", "テスト用", "--config", str(cfg)])
    assert code == 0 and cfg.exists(), out
    assert "相対ジョブ" in out and "FRAME ROBOT" in out
    assert updown.check(jbi.load(DUMMY / "DEMO_RJ_XYZ.JBI"), cfg).confirmed
