"""相対ジョブの FRAME 行「////FRAME ROBOT」の受入テスト（CHECKER_027 X2。唐川さんの決定 2026-11-03）。実装より先に書いた。

次のすべてを満たすときだけ、「////FRAME ROBOT」の行を「知らない指示行」にしない：
1. FRAME の行がちょうど「////FRAME ROBOT」（斜線4本・値 ROBOT・後ろに何も付かない）
2. 「///POSTYPE ROBOT」と「///RECTAN」がある
3. 「///ATTR」に RJ がある（並びの1つとして）
4. 「///USER」の指定が無い
5. FRAME の行は1回だけで、INST 部の指示行の並び（///ATTR の後、命令より前）にある
満たさないものは今どおり problems に入れて止める。FRAME の行を認めても、ほかの検査は省略しない。

使うのは samples/dummy/DEMO_RJ_XYZ.JBI（このリポジトリで新しく作った架空のジョブ。実機のジョブではない）。
検査を通ったことは、物理的な安全の保証ではない。
"""
import hashlib
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi, sim, updown
from voice_teaching.web import api
from voice_teaching.web.flow import WebFlow

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy"
PATH = DUMMY / "DEMO_RJ_XYZ.JBI"
DATA = PATH.read_bytes()
NL = b"\r\n"
FRAME = b"////FRAME ROBOT" + NL

EDITABLE = ("C00002", "C00003", "C00004")
BLOCKED = {
    "C00000": "MOVL）の命令ではない",        # MOVJ の行き先
    "C00006": "MOVL）の命令ではない",        # MOVJ の行き先
    "C00001": "入る移動の始まりの命令",     # 入る区間の始まりが MOVJ
    "C00005": "出る移動の命令",             # 出る移動が MOVJ
}


def lines(b: bytes) -> list[bytes]:
    return b.split(NL)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def variant(old: bytes, new: bytes, data: bytes = DATA) -> bytes:
    assert data.count(old) == 1, old
    out = data.replace(old, new)
    assert out != data
    return out


# ======================================================================
# 0. 合成ジョブの形（構成だけ相対ジョブと同じ。数値・名前は架空）
# ======================================================================
def test_synthetic_job_has_relative_job_structure():
    assert DATA.count(NL) == DATA.count(b"\n")  # 改行は CRLF
    DATA.decode("ascii")  # ASCII
    ls = lines(DATA)
    assert b"///TOOL 8" in ls and b"///POSTYPE ROBOT" in ls and b"///RECTAN" in ls
    rconf = next(x for x in ls if x.startswith(b"///RCONF "))
    assert len(rconf[len(b"///RCONF "):].split(b",")) == 24
    assert b"///ATTR SC,RW,RJ" in ls and b"////FRAME ROBOT" in ls
    assert ls.index(b"////FRAME ROBOT") == ls.index(b"///ATTR SC,RW,RJ") + 1
    assert not any(x.startswith(b"///USER") for x in ls)
    moves = [x.split()[0] for x in ls if x.startswith(b"MOV")]
    assert moves == [b"MOVJ", b"MOVL", b"MOVL", b"MOVL", b"MOVL", b"MOVL", b"MOVJ"]
    assert b"'SYNTHETIC DEMO JOB - NOT A REAL ROBOT JOB" in ls


# ======================================================================
# 1. 合成ジョブ：problems 0件、変更できる位置・止まる位置
# ======================================================================
def test_frame_robot_job_has_no_problems_and_is_marked_relative_robot():
    job = jbi.parse_bytes(DATA)
    assert job.problems == []
    assert job.render_bytes() == DATA
    assert job.relative_frame == "ROBOT"  # 相対ジョブ（ROBOT 座標）であることは problems ではない情報として持つ
    assert sorted(job.positions) == [f"C0000{i}" for i in range(7)]
    assert all(p.tool == "8" for p in job.positions.values())
    assert job.is_robot_coordinates()


def test_jobs_without_frame_line_are_not_marked_relative():
    job = jbi.parse_bytes((DUMMY / "DEMO_C_PLACE.JBI").read_bytes())
    assert job.problems == [] and job.relative_frame is None


@pytest.mark.parametrize("position", EDITABLE)
def test_positions_between_movl_only_can_change(position):
    job = jbi.parse_bytes(DATA)
    cp = job.plan_shift(position, "Z", -3.0)
    assert cp.after_value == pytest.approx(cp.before_value - 3.0)
    assert "この位置はツール番号 8 で書かれています" in cp.warnings  # 相対ジョブは行き先がツールの設定に左右される


@pytest.mark.parametrize("position,why", sorted(BLOCKED.items()))
def test_positions_next_to_movj_are_still_blocked_with_reason(position, why):
    job = jbi.parse_bytes(DATA)
    with pytest.raises(jbi.JbiError) as e:
        job.plan_shift(position, "Z", -3.0)
    assert why in str(e.value) and "MOVJ" in str(e.value)


def test_other_checks_are_not_skipped_for_frame_robot_jobs():
    # FRAME の行を認めても、PL>0・共用・許可リストに無い命令・PULSE の検査はそのまま効く
    pl = variant(b"MOVL C00003 V=60.0" + NL, b"MOVL C00003 V=60.0 PL=3" + NL)
    with pytest.raises(jbi.JbiError, match="PL=3"):
        jbi.parse_bytes(pl).plan_shift("C00003", "Z", -3.0)
    shared = variant(b"MOVL C00004 V=60.0" + NL, b"MOVL C00004 V=60.0" + NL + b"MOVL C00003 V=60.0" + NL)
    with pytest.raises(jbi.JbiError, match="共用"):
        jbi.parse_bytes(shared).plan_shift("C00003", "Z", -3.0)
    unknown = variant(b"MOVL C00003 V=60.0" + NL, b"MOVL C00003 V=60.0" + NL + b"WAIT IN#(1)=ON" + NL)
    job = jbi.parse_bytes(unknown)
    assert any("許可リストに無い命令" in p for p in job.problems)
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00003", "Z", -3.0)
    over = jbi.parse_bytes(DATA)
    with pytest.raises(jbi.JbiError, match="上限"):
        over.plan_shift("C00003", "Z", -21.0)


def test_reading_does_not_change_the_file(tmp_path):
    before = sha(PATH.read_bytes())
    job = jbi.load(PATH)
    job.plan_shift("C00003", "Z", -3.0)
    sim.simulate_shift(job, "C00002", "Z", -3.0)
    api.job_view(PATH.read_bytes())
    assert sha(PATH.read_bytes()) == before


def test_figure_data_shows_all_seven_points_and_movj_kind():
    job = jbi.parse_bytes(DATA)
    fig = api.figure_data(job)
    assert sorted(p["name"] for p in fig["positions"]) == [f"C0000{i}" for i in range(7)]
    assert all(p["xyz"] is not None for p in fig["positions"])
    kinds = {s["to"]: s["kind"] for s in fig["segments"]}
    assert kinds["C00006"] == "movj"
    assert kinds["C00002"] == kinds["C00003"] == kinds["C00004"] == kinds["C00005"] == "movl"
    view = api.job_view(DATA)
    assert view["ok"] is True and view["problems"] == []


# ======================================================================
# 2. 「Zマイナス3ミリ」→ 候補 → 承認 → シミュレーションの出力（合成ジョブなので出せる）
# ======================================================================
def test_voice_flow_candidate_approve_export_keeps_header_bytes(tmp_path):
    w = WebFlow(interpreter=ci.MockInterpreter())
    r = w.load(DATA)
    assert r["ok"], r
    sid = r["session_id"]
    assert w.select(sid, "C00002")["status"] == "ok"
    h = w.hear(sid, "Zマイナス3ミリ")
    assert h["status"] == "ok", h
    c = h["state"]["candidate"]
    assert (c["target"], c["delta"]) == ("C00002 Z", "-3.000")
    assert c["blocked_reason"] is None
    assert any("ツール番号 8" in x for x in c["warnings"])
    assert w.compare(sid)["status"] == "ok"
    assert w.approve(sid, "テスト")["ok"] is True
    e = w.export(sid)
    assert e["ok"] is True, e
    new = Path(e["path"]).read_bytes()
    a, b = lines(DATA), lines(new)
    assert len(a) == len(b)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert diff == [
        (b"//NAME DEMO_RJ_XYZ", b"//NAME DEMO_RJ_XYZ_SIM"),
        (b"C00002=520.000,140.000,180.000,-179.5000,1.2500,25.0000",
         b"C00002=520.000,140.000,177.000,-179.5000,1.2500,25.0000"),
    ]
    # FRAME・ATTR・RCONF・TOOL などの行は元のバイト列のまま
    for keep in (b"////FRAME ROBOT", b"///ATTR SC,RW,RJ", b"///TOOL 8", b"///POSTYPE ROBOT", b"///RECTAN",
                 next(x for x in a if x.startswith(b"///RCONF "))):
        assert keep + NL in new
    assert sha(PATH.read_bytes()) == sha(DATA)


def test_sim_output_of_a_non_sample_frame_robot_job_is_refused():
    # samples に無い（バイト列が違う）相対ジョブは、承認しても出力しない（CHECKER_027 R-3 と同じ形）
    data = variant(b"///DATE 2026/11/03 16:00", b"///DATE 2026/11/03 16:01")
    job = jbi.parse_bytes(data)
    assert job.problems == []
    p = sim.simulate_shift(job, "C00002", "Z", -3.0)
    ap = sim.approve(p, "テスト")
    with pytest.raises(sim.SimError, match="合成ジョブ以外は出力しません"):
        sim.export_sim(p, ap)


# ======================================================================
# 3. 止める形（1つずつ）。problems に入り、どの位置も変わらない
# ======================================================================
STOP_CASES = {
    "frame_user1": (FRAME, b"////FRAME USER 1" + NL),
    "frame_base": (FRAME, b"////FRAME BASE" + NL),
    "frame_bf": (FRAME, b"////FRAME BF" + NL),
    "three_slashes": (FRAME, b"///FRAME ROBOT" + NL),
    "five_slashes": (FRAME, b"/////FRAME ROBOT" + NL),
    "trailing_word": (FRAME, b"////FRAME ROBOT X" + NL),
    "trailing_space": (FRAME, b"////FRAME ROBOT " + NL),
    "tab": (FRAME, b"////FRAME\tROBOT" + NL),
    "two_spaces": (FRAME, b"////FRAME  ROBOT" + NL),
    "lowercase": (FRAME, b"////frame robot" + NL),
    "lowercase_value": (FRAME, b"////FRAME robot" + NL),
    "no_value": (FRAME, b"////FRAME" + NL),
    "twice": (FRAME, FRAME + FRAME),
    "robot_and_user": (FRAME, FRAME + b"////FRAME USER 1" + NL),
    "no_rj": (b"///ATTR SC,RW,RJ", b"///ATTR SC,RW"),
    "rj_lookalike": (b"///ATTR SC,RW,RJ", b"///ATTR SC,RW,RJX"),
    "rj_lowercase": (b"///ATTR SC,RW,RJ", b"///ATTR SC,RW,rj"),
    "postype_user": (b"///POSTYPE ROBOT", b"///POSTYPE USER"),
    "postype_pulse": (b"///POSTYPE ROBOT" + NL + b"///RECTAN" + NL, b"///POSTYPE PULSE" + NL + b"///PULSE" + NL),
    "no_rectan": (b"///RECTAN" + NL, b""),
    "with_user": (b"///TOOL 8" + NL, b"///TOOL 8" + NL + b"///USER 1" + NL),
    "postype_user_and_user": (b"///TOOL 8" + NL + b"///POSTYPE ROBOT",
                              b"///TOOL 8" + NL + b"///USER 1" + NL + b"///POSTYPE USER"),
    "before_attr": (b"///ATTR SC,RW,RJ" + NL + FRAME, FRAME + b"///ATTR SC,RW,RJ" + NL),
    "full_width": (FRAME, "////FRAME ＲＯＢＯＴ".encode("utf-8") + NL),
    "no_frame_with_rj": (FRAME, b""),  # CHECKER_028 K3・F4：相対ジョブなのに座標系が分からない
}


def _moved_after_nop(data: bytes = DATA) -> bytes:
    return variant(b"NOP" + NL, b"NOP" + NL + FRAME, data.replace(FRAME, b"", 1))


def _moved_after_end(data: bytes = DATA) -> bytes:
    out = data.replace(FRAME, b"", 1)
    assert out.endswith(b"END" + NL)
    return out + FRAME


def _moved_to_pos(data: bytes = DATA) -> bytes:
    return variant(b"///RECTAN" + NL, b"///RECTAN" + NL + FRAME, data.replace(FRAME, b"", 1))


def _moved_after_last_position(data: bytes = DATA) -> bytes:
    return variant(b"//INST" + NL, FRAME + b"//INST" + NL, data.replace(FRAME, b"", 1))


def _no_attr(data: bytes = DATA) -> bytes:
    return variant(b"///ATTR SC,RW,RJ" + NL, b"")


def _two_attr(data: bytes = DATA) -> bytes:
    return variant(b"///ATTR SC,RW,RJ" + NL, b"///ATTR SC,RW,RJ" + NL + b"///ATTR SC,RW" + NL)


MOVED = {"after_nop": _moved_after_nop, "after_end": _moved_after_end, "in_pos": _moved_to_pos,
         "after_last_position": _moved_after_last_position, "no_attr": _no_attr, "two_attr": _two_attr}


def _stop_data(case: str) -> bytes:
    if case in MOVED:
        return MOVED[case]()
    old, new = STOP_CASES[case]
    return variant(old, new)


@pytest.mark.parametrize("case", sorted(STOP_CASES) + sorted(MOVED))
def test_frame_forms_outside_the_decision_are_stopped(case):
    data = _stop_data(case)
    job = jbi.parse_bytes(data)
    assert job.problems, case
    assert any("FRAME" in p for p in job.problems), (case, job.problems)
    assert job.relative_frame is None
    for name in job.positions:
        with pytest.raises(jbi.JbiError):
            job.plan_shift(name, "Z", -3.0)
    assert api.job_view(data)["problems"]


@pytest.mark.parametrize("case,words", [
    ("frame_user1", "////FRAME ROBOT"),
    ("three_slashes", "////FRAME ROBOT"),
    ("lowercase", "////FRAME ROBOT"),
    ("twice", "2回以上"),
    ("no_rj", "RJ"),
    ("no_attr", "RJ"),
    ("postype_user", "POSTYPE ROBOT"),
    ("no_rectan", "RECTAN"),
    ("with_user", "///USER"),
    ("after_nop", "命令より前"),
    ("before_attr", "///ATTR の後"),
    ("no_frame_with_rj", "相対ジョブ（RJ）ですが、座標系（FRAME）の行がありません"),
    ("in_pos", "INST"),
])
def test_stop_reason_names_the_condition(case, words):
    job = jbi.parse_bytes(_stop_data(case))
    frame_problems = [p for p in job.problems if "FRAME" in p]
    assert frame_problems and any(words in p for p in frame_problems), (case, job.problems)


# ======================================================================
# 4. CHECKER_028 frame_cases.py の17通り（F1〜F10）を、合成ジョブを書き換えて試す（実機の JOB の数値は使わない）
#    OK の形は C00002〜C00004 だけが変更できる。ほかはすべて止まる
# ======================================================================
FR = FRAME


def _r(a: bytes, b: bytes) -> bytes:
    return DATA.replace(a, b)


FRAME_CASES = [
    ("OK", DATA, "ok"),
    ("F1", _r(FR, b"////FRAME USER 1" + NL).replace(b"///POSTYPE ROBOT", b"///POSTYPE USER")
     .replace(b"///TOOL 8", b"///USER 1" + NL + b"///TOOL 8"), "stop"),
    ("F2", _r(FR, b"////FRAME BASE" + NL), "stop"),
    ("F3", _r(b"SC,RW,RJ", b"SC,RW"), "stop"),
    ("F4", _r(FR, b""), "stop"),
    ("F5a", _r(FR, FR + FR), "stop"),
    ("F5b", _r(FR, FR + b"////FRAME USER 1" + NL), "stop"),
    ("F6a", _r(FR, b"////FRAME ROBOT X" + NL), "stop"),
    ("F6b", _r(FR, b"////FRAME  ROBOT" + NL), "stop"),
    ("F6c", _r(FR, b"////frame robot" + NL), "stop"),
    ("F6d", _r(FR, b"///FRAME ROBOT" + NL), "stop"),
    ("F6e", _r(FR, b"/////FRAME ROBOT" + NL), "stop"),
    ("F6f", _r(FR, "////FRAME ＲＯＢＯＴ".encode("utf-8") + NL), "stop"),
    ("F7", _r(b"///POSTYPE ROBOT", b"///POSTYPE PULSE"), "stop"),
    ("F8", _r(b"///TOOL 8", b"///USER 1" + NL + b"///TOOL 8"), "stop"),
    ("F9", _r(b"///RECTAN" + NL, b""), "stop"),
    ("F10", _r(FR, b"").replace(b"///RECTAN" + NL, b"///RECTAN" + NL + FR), "stop"),
]


def test_frame_cases_are_seventeen_and_all_differ():
    assert len(FRAME_CASES) == 17
    assert len({d for _, d, _ in FRAME_CASES}) == 17


@pytest.mark.parametrize("name,data,expected", FRAME_CASES, ids=[c[0] for c in FRAME_CASES])
def test_checker028_frame_cases(name, data, expected):
    job = jbi.parse_bytes(data)
    changeable = []
    for n in sorted(job.positions):
        try:
            job.plan_shift(n, "Z", 1.0)
            changeable.append(n)
        except jbi.JbiError:
            pass
    if expected == "ok":
        assert job.problems == [] and changeable == list(EDITABLE)
        assert job.relative_frame == "ROBOT"
    else:
        assert changeable == [], (name, changeable)
        assert job.relative_frame is None
        assert job.problems or not any(p.is_robot_coordinates() for p in job.positions.values()), name


def test_f4_reason_is_short_and_on_the_attr_line():
    job = jbi.parse_bytes(_r(FR, b""))
    assert job.problems == ["18行目「///ATTR SC,RW,RJ」は扱えません：相対ジョブ（RJ）ですが、座標系（FRAME）の行がありません"]


# ======================================================================
# 5. 上下の向きの照合（M2・CHECKER_028 K4）：この形は ROBOT・RJ の座標系として照合できる。決めた形でない FRAME は不一致
# ======================================================================
def test_updown_config_can_be_made_and_matches_frame_robot_job(make_updown):
    path = make_updown("DEMO_RJ_XYZ.JBI")
    st = updown.check(jbi.parse_bytes(DATA), path)
    assert st.confirmed, st


@pytest.mark.parametrize("case", ["frame_user1", "frame_base", "three_slashes", "no_rj", "twice", "after_nop",
                                  "no_frame_with_rj"])
def test_updown_mismatch_for_frame_forms_outside_the_decision(make_updown, case):
    data = _stop_data(case)
    st = updown.check(jbi.parse_bytes(data), make_updown("DEMO_RJ_XYZ.JBI"))
    assert st.status == updown.MISMATCH, (case, st)
    assert "FRAME" in st.reason or "RJ" in st.reason or "読めない所" in st.reason, (case, st.reason)


def test_updown_build_config_refuses_frame_user1_job():
    job = jbi.parse_bytes(_stop_data("frame_user1"))
    with pytest.raises(updown.UpdownError):
        updown.build_config(job, updown.DEFAULT_ROBOT, "テスト", "テスト用")
