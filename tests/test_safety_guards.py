"""PR #1 レビュー指摘（1〜6）の回帰テスト。

チャット側シクロの「PR #1 安全ガード修正の展開指示」2-1 のテストをそのまま置いたもの。
ただし test_pl0_is_allowed は、チャッピーの暫定判断（PL=0 は拒否を維持）に合わせて
test_pl0_is_rejected に変えている（reviews/REVIEW_PR1_FIX_001.md に矛盾として記録）。
"""
from pathlib import Path

import pytest

from voice_teaching import jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def base() -> str:
    return DUMMY.read_bytes().decode("ascii")


def idx_of(job, pos):
    return [i.index for i in job.instructions if i.position == pos][0]


# ---- 1. POS部の形式を位置ごとに判定する ----
MIXED = (
    "///POSTYPE ROBOT\n///RECTAN\n///RCONF 0,0,0,0,0,0,0,0\n"
    "C00000=450.000,-200.000,350.000,180.00,0.00,0.00\n",
    "///POSTYPE PULSE\n///PULSE\nC00000=1200,-3400,5600,0,-7800,900\n"
    "///POSTYPE ROBOT\n///RECTAN\n///RCONF 0,0,0,0,0,0,0,0\n",
)


def test_mixed_pos_pulse_position_is_rejected():
    job = jbi.parse(base().replace(*MIXED))
    assert not job.positions["C00000"].is_robot_coordinates()
    assert job.positions["C00004"].is_robot_coordinates()
    assert not job.is_robot_coordinates()
    with pytest.raises(jbi.JbiError, match="ロボット座標"):
        job.plan_shift("C00000", "Y", 10.0)


def test_postype_switch_resets_rectan():
    text = base().replace(
        "C00005=450.000,190.000,250.000,180.00,0.00,0.00\n",
        "///POSTYPE USER\nC00005=450.000,190.000,250.000,180.00,0.00,0.00\n")
    assert not jbi.parse(text).positions["C00005"].is_robot_coordinates()


def test_unknown_pos_directive_blocks_following_positions():
    job = jbi.parse(base().replace("C00004=", "///SOMETHING 1\nC00004="))
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)


def test_unreadable_pos_line_blocks_all_edits():
    job = jbi.parse(base().replace("C00003=450.000,190.000", "C00003=450.000,abc"))
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


def test_duplicate_position_blocks_all_edits():
    job = jbi.parse(base().replace("C00005=", "C00004="))
    with pytest.raises(jbi.JbiError, match="重複"):
        job.plan_shift("C00001", "Y", 1.0)


# ---- 2. 共用位置 ----
def test_shared_position_is_rejected():
    job = jbi.parse(base())
    assert len(job.users_of("C00000")) == 2
    with pytest.raises(jbi.JbiError, match="共用"):
        job.plan_shift("C00000", "Y", 10.0)


def test_unused_position_is_rejected():
    job = jbi.parse(base().replace("MOVL C00005 V=80.0\n", "NOP\n"))
    with pytest.raises(jbi.JbiError, match="使われていない"):
        job.plan_shift("C00005", "Y", 1.0)


def test_reference_in_non_motion_instruction_counts_as_user():
    job = jbi.parse(base().replace("TIMER T=3.00", "GETS PX000 C00004"))
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)


def test_bc_variable_is_not_counted_as_c_reference():
    job = jbi.parse(base().replace("TIMER T=3.00", "MOVJ BC00004 VJ=10.00"))
    assert len(job.users_of("C00004")) == 1


# ---- 3. PL・CR付き命令 ----
def test_pl_position_shift_is_rejected():
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 PL=3"))
    with pytest.raises(jbi.JbiError, match="PL=3"):
        job.plan_shift("C00004", "Y", 10.0)


def test_pl_speed_change_is_rejected():
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 PL=1"))
    with pytest.raises(jbi.JbiError, match="PL=1"):
        job.plan_speed(idx_of(job, "C00004"), 100.0)


def test_pl0_is_rejected():
    """原本は test_pl0_is_allowed（PL=0 を許す）。暫定判断で PL=0 も拒否を維持する"""
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 PL=0"))
    with pytest.raises(jbi.JbiError, match="PL=0"):
        job.plan_shift("C00004", "Y", 1.0)
    with pytest.raises(jbi.JbiError, match="PL=0"):
        job.plan_speed(idx_of(job, "C00004"), 100.0)


def test_cr_is_rejected():
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 CR=5.0"))
    with pytest.raises(jbi.JbiError, match="CR="):
        job.plan_shift("C00004", "Y", 1.0)


# ---- 4. MOVL以外の命令・位置の意味を変える命令 ----
@pytest.mark.parametrize("cmd", ["MOVJ C00004 VJ=50.00", "MOVC C00004 V=80.0", "MOVS C00004 V=80.0"])
def test_non_movl_user_is_rejected(cmd):
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", cmd))
    with pytest.raises(jbi.JbiError, match="MOVL"):
        job.plan_shift("C00004", "Y", 10.0)


@pytest.mark.parametrize("cmd", ["SFTON P000", "IMOV P001 V=10.0", "MFRAME UF#(1) C00001 C00002 C00003"])
def test_blocking_commands_stop_position_edits(cmd):
    job = jbi.parse(base().replace("TIMER T=3.00", cmd))
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)


def test_multi_group_job_is_rejected():
    job = jbi.parse(base().replace("///GROUP1 RB1", "///GROUP1 RB1,ST1"))
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)


# ---- 5. 文字コード ----
def test_shift_jis_roundtrip_is_byte_identical():
    data = base().replace("NOP\n", "NOP\n'ワーク取出し\n").replace("\n", "\r\n").encode("cp932")
    job = jbi.parse_bytes(data)
    assert job.encoding == "cp932"
    assert job.render_bytes() == data


def test_shift_jis_edit_keeps_other_bytes():
    data = base().replace("NOP\n", "NOP\n'ワーク取出し\n").encode("cp932")
    new = jbi.parse_bytes(data).plan_shift("C00004", "Y", 10.0).new_job.render_bytes()
    changed = [(a, b) for a, b in zip(data.split(b"\n"), new.split(b"\n")) if a != b]
    assert changed == [(b"C00004=450.000,190.000,120.000,180.00,0.00,0.00",
                        b"C00004=450.000,200.000,120.000,180.00,0.00,0.00")]
    assert "ワーク取出し".encode("cp932") in new


def test_comment_mentioning_position_is_not_a_user():
    job = jbi.parse(base().replace("NOP\n", "NOP\n'C00004 is pick\n"))
    assert len(job.users_of("C00004")) == 1


def test_undecodable_bytes_are_rejected():
    with pytest.raises(jbi.JbiError):
        jbi.parse_bytes(base().encode("ascii") + b"\x81\xff\n")


def test_load_reads_dummy_as_bytes():
    assert jbi.load(DUMMY).render_bytes() == DUMMY.read_bytes()


# ---- 6. 丸め・実際の出力値・異常値 ----
def test_change_that_rounds_to_zero_is_rejected():
    with pytest.raises(jbi.JbiError, match="値が変わりません"):
        jbi.parse(base()).plan_shift("C00004", "X", 0.0004)


def test_rounding_is_reported_with_actual_value():
    p = jbi.parse(base()).plan_shift("C00004", "X", 0.1234)
    assert p.after_value == 450.123
    assert abs(p.actual_delta - 0.123) < 1e-9
    assert any("丸め" in w for w in p.warnings)


@pytest.mark.parametrize("bad", [0, float("nan"), float("inf"), True])
def test_invalid_delta_is_rejected(bad):
    with pytest.raises(jbi.JbiError):
        jbi.parse(base()).plan_shift("C00004", "Y", bad)


def test_shift_over_limit_is_rejected():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError, match="上限"):
        job.plan_shift("C00004", "Y", jbi.DEFAULT_MAX_SHIFT_MM + 0.001)
    assert job.plan_shift("C00004", "Y", -jbi.DEFAULT_MAX_SHIFT_MM).after_value == 140.0


def test_same_speed_is_rejected():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), 250.0)


@pytest.mark.parametrize("bad", [0, -10.0, float("nan"), True])
def test_invalid_speed_is_rejected(bad):
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), bad)


# ---- 変更案の中身（承認画面に出すもの） ----
def test_proposal_lists_paths_in_and_out_and_warnings():
    p = jbi.parse(base()).plan_shift("C00004", "Y", 10.0)
    assert p.before_line == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert p.after_line == "C00004=450.000,200.000,120.000,180.00,0.00,0.00"
    assert len(p.affected) == 2
    assert "MOVL C00004" in p.affected[0] and "MOVL C00005" in p.affected[1]
    assert jbi.PATH_WARNING in p.warnings
    assert any("ツール番号 0" in w for w in p.warnings)


def test_proposal_does_not_modify_original():
    job = jbi.parse(base())
    before = job.render()
    job.plan_shift("C00004", "Y", 10.0)
    job.plan_speed(idx_of(job, "C00003"), 300.0)
    assert job.render() == before


def test_jump_in_job_adds_flow_warning():
    p = jbi.parse(base().replace("TIMER T=3.00", "JUMP *L1")).plan_shift("C00004", "Y", 1.0)
    assert any("JUMP" in w for w in p.warnings)


# ---- 仕様v2.1 6章の追加分（止める条件：出る命令・前後の命令） ----
@pytest.mark.parametrize("out_line", [
    "MOVJ C00005 VJ=50.00",
    "MOVC C00005 V=80.0",
    "MOVL C00005 V=80.0 PL=0",
    "MOVL C00005 V=80.0 PL=2",
    "MOVL C00005 V=80.0 CR=5.0",
    "MOVL C00005 V=80.0 +MOVJ C00001 VJ=50.00",
    "MOVL C00005 V=80.0 ACC=50",
])
def test_shift_rejected_when_outgoing_move_is_not_plain_movl(out_line):
    """変える位置から出る命令が「MOVL 位置 V=数値」でなければ止める"""
    text = base().replace("MOVL C00005 V=80.0", out_line)
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"出る.*行目「.*C00005.*」"):
        job.plan_shift("C00004", "Y", 1.0)
    assert job.render() == text


def test_shift_allowed_when_no_outgoing_move():
    """変える位置の後に移動命令が無い（ジョブの最後）なら、出る命令では止めない"""
    text = base().replace("MOVL C00005 V=80.0\nMOVL C00000 V=200.0\n", "MOVL C00005 V=80.0\n")
    p = jbi.parse(text).plan_shift("C00005", "Y", 1.0)
    assert p.after_value == 191.0


@pytest.mark.parametrize("old,new", [
    ("MOVL C00002 V=80.0", "MOVL C00002 V=80.0 PL=1"),
    ("MOVL C00002 V=80.0", "MOVL C00002 V=80.0 PL=0"),
    ("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 CR=5.0"),
    ("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 pl=3"),
])
def test_speed_rejected_when_neighbor_move_has_pl_or_cr(old, new):
    """速度を変える命令の直前・直後の移動命令に PL・CR があれば止める（PL=0 も今は止める）"""
    text = base().replace(old, new)
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"前後.*行目「"):
        job.plan_speed(idx_of(job, "C00003"), 300.0)
    assert job.render() == text


def test_speed_allowed_when_neighbor_is_movj_without_pl():
    """前後の命令は PL・CR だけを見る（前後が MOVJ でも PL・CR が無ければ止めない）"""
    job = jbi.parse(base().replace("MOVL C00002 V=80.0", "MOVJ C00002 VJ=50.00"))
    assert job.plan_speed(idx_of(job, "C00003"), 300.0).after_value == 300.0


def test_speed_allowed_when_no_previous_move():
    """前に移動命令が無い（最初の移動）なら、前後の命令では止めない"""
    job = jbi.parse(base())
    first = min(i.index for i in job.instructions if i.position == "C00000")
    assert job.plan_speed(first, 150.0).after_value == 150.0
