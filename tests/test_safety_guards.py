"""PR #1 レビュー指摘（1〜6）の回帰テスト。

チャット側シクロの「PR #1 安全ガード修正の展開指示」2-1 のテストをそのまま置いたもの。
PL=0 は、本人（唐川さん）の指示で正本（仕様v2.1 6・7章、CURRENT_DECISIONS 4章）に従い「通す」。
いったん test_pl0_is_rejected にしていたものを、原本どおり test_pl0_is_allowed に戻した（修正ラウンド1）。
"""
from pathlib import Path

import pytest

from voice_teaching import jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def base() -> str:
    return DUMMY.read_bytes().decode("ascii")


def idx_of(job, pos):
    return [i.index for i in job.instructions if i.position == pos][0]


@pytest.fixture
def speed_enabled(monkeypatch):
    """速度の変更は既定で止めている（SPEED_CHANGE_ENABLED = False）。検査の中身を試すテストだけ True にする"""
    monkeypatch.setattr(jbi, "SPEED_CHANGE_ENABLED", True)


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


@pytest.mark.usefixtures("speed_enabled")
def test_pl_speed_change_is_rejected():
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 PL=1"))
    with pytest.raises(jbi.JbiError, match="PL=1"):
        job.plan_speed(idx_of(job, "C00004"), 100.0)


@pytest.mark.usefixtures("speed_enabled")
def test_pl0_is_allowed():
    """原本どおり：ちょうど PL=0 と書かれたMOVLは、位置も速度も変えられる（修正ラウンド1 A-1）"""
    job = jbi.parse(base().replace("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 PL=0"))
    assert job.plan_shift("C00004", "Y", 1.0).after_value == 191.0
    p = job.plan_speed(idx_of(job, "C00004"), 90.0)
    assert p.after_value == 90.0
    assert p.after_line == "MOVL C00004 V=90.0 PL=0"


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
    # CHECKER_032 P1-3：保存の精度で丸まる量は候補にしない（0 になるものも同じ）
    with pytest.raises(jbi.JbiError, match="言った量と違う"):
        jbi.parse(base()).plan_shift("C00004", "X", 0.0004)


def test_rounding_is_reported_with_actual_value():
    # CHECKER_032 P1-3（唐川さん 11/3 夜）：言った量と保存される量が違えば、黙って丸めずに候補を作らない
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse(base()).plan_shift("C00004", "X", 0.1234)
    assert "言った量と違う" in str(e.value) and "+0.123mm" in str(e.value)


@pytest.mark.parametrize("bad", [0, float("nan"), float("inf"), True])
def test_invalid_delta_is_rejected(bad):
    with pytest.raises(jbi.JbiError):
        jbi.parse(base()).plan_shift("C00004", "Y", bad)


def test_shift_over_limit_is_rejected():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError, match="上限"):
        job.plan_shift("C00004", "Y", jbi.DEFAULT_MAX_SHIFT_MM + 0.001)
    assert job.plan_shift("C00004", "Y", -jbi.DEFAULT_MAX_SHIFT_MM).after_value == 190.0 - jbi.DEFAULT_MAX_SHIFT_MM


@pytest.mark.usefixtures("speed_enabled")
def test_same_speed_is_rejected():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), 250.0)


@pytest.mark.parametrize("bad", [0, -10.0, float("nan"), True])
@pytest.mark.usefixtures("speed_enabled")
def test_invalid_speed_is_rejected(bad):
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), bad)


# ---- 変更案の中身（承認画面に出すもの） ----
def test_proposal_lists_paths_in_and_out_and_warnings():
    p = jbi.parse(base()).plan_shift("C00004", "Y", 10.0)
    assert p.before_line == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert p.after_line == "C00004=450.000,200.000,120.000,180.00,0.00,0.00"
    # 修正ラウンド1 C-8：入る区間の始まり（直前の移動命令）も入れる
    assert len(p.affected) == 3
    assert "MOVL C00003" in p.affected[0]
    assert "MOVL C00004" in p.affected[1] and "MOVL C00005" in p.affected[2]
    assert jbi.PATH_WARNING in p.warnings
    assert any("ツール番号 0" in w for w in p.warnings)


@pytest.mark.usefixtures("speed_enabled")
def test_proposal_does_not_modify_original():
    job = jbi.parse(base())
    before = job.render()
    job.plan_shift("C00004", "Y", 10.0)
    job.plan_speed(idx_of(job, "C00003"), 300.0)
    assert job.render() == before


def test_jump_anywhere_in_job_stops_position_edits():
    """修正ラウンド1 D-4：もとは test_jump_in_job_adds_flow_warning（注意文だけ）。
    制御の流れを解析する機能がまだ無いので、ジョブのどこかに JUMP があれば位置の変更を止める"""
    text = base().replace("NOP\n", "NOP\nJUMP *L1\n")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="制御の流れ") as e:
        job.plan_shift("C00004", "Y", 1.0)
    assert "「JUMP *L1」" in str(e.value)
    assert job.render() == text


# ---- 仕様v2.1 6章の追加分（止める条件：出る命令・前後の命令） ----
@pytest.mark.parametrize("out_line", [
    "MOVJ C00005 VJ=50.00",
    "MOVC C00005 V=80.0",
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


def test_shift_rejected_when_no_outgoing_move():
    """修正ラウンド2：もとは test_shift_allowed_when_no_outgoing_move（止めない）。
    次の移動命令が無い（ジョブの最後）位置は、出る区間がジョブの中で決まらないので止める"""
    text = base().replace("MOVL C00005 V=80.0\nMOVL C00000 V=200.0\n", "MOVL C00005 V=80.0\n")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="出る"):
        job.plan_shift("C00005", "Y", 1.0)
    assert job.render() == text


@pytest.mark.parametrize("old,new", [
    ("MOVL C00002 V=80.0", "MOVL C00002 V=80.0 PL=1"),
    ("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 CR=5.0"),
    ("MOVL C00004 V=80.0", "MOVL C00004 V=80.0 pl=3"),
])
@pytest.mark.usefixtures("speed_enabled")
def test_speed_rejected_when_neighbor_move_has_pl_or_cr(old, new):
    """速度を変える命令の直前・直後の移動命令に PL・CR があれば止める（ちょうど PL=0 は通す。修正ラウンド1 A-1）"""
    text = base().replace(old, new)
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"前後.*行目「"):
        job.plan_speed(idx_of(job, "C00003"), 300.0)
    assert job.render() == text


@pytest.mark.usefixtures("speed_enabled")
def test_speed_allowed_when_neighbor_is_movj_without_pl():
    """前後の命令は PL・CR だけを見る（前後が MOVJ でも PL・CR が無ければ止めない）"""
    job = jbi.parse(base().replace("MOVL C00002 V=80.0", "MOVJ C00002 VJ=50.00"))
    assert job.plan_speed(idx_of(job, "C00003"), 300.0).after_value == 300.0


@pytest.mark.usefixtures("speed_enabled")
def test_speed_allowed_when_no_previous_move():
    """前に移動命令が無い（最初の移動）なら、前後の命令では止めない"""
    job = jbi.parse(base())
    first = min(i.index for i in job.instructions if i.position == "C00000")
    assert job.plan_speed(first, 150.0).after_value == 150.0


def test_outgoing_move_with_exact_pl0_is_allowed():
    """出る命令がちょうど PL=0 のMOVLなら止めない（修正ラウンド1 A-1）"""
    job = jbi.parse(base().replace("MOVL C00005 V=80.0", "MOVL C00005 V=80.0 PL=0"))
    assert job.plan_shift("C00004", "Y", 1.0).after_value == 191.0


@pytest.mark.usefixtures("speed_enabled")
def test_speed_allowed_when_neighbor_has_exact_pl0():
    """前後の移動命令がちょうど PL=0 なら速度の変更を止めない（修正ラウンド1 A-1）"""
    job = jbi.parse(base().replace("MOVL C00002 V=80.0", "MOVL C00002 V=80.0 PL=0"))
    assert job.plan_speed(idx_of(job, "C00003"), 300.0).after_value == 300.0


# ---- 修正ラウンド1 ----
# A-1. PL=0 はちょうどその書き方だけを通す。緩い書き方は止めたまま
@pytest.mark.parametrize("suffix", ["PL = 0", "pl=0", "PL=0.0", "PL=00", "PL=0 CR=5.0", "PL=0 PL=0"])
@pytest.mark.usefixtures("speed_enabled")
def test_loose_pl0_forms_are_rejected(suffix):
    text = base().replace("MOVL C00004 V=80.0", f"MOVL C00004 V=80.0 {suffix}")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00004"), 90.0)
    assert job.render() == text


def test_previous_move_with_exact_pl0_is_allowed():
    job = jbi.parse(base().replace("MOVL C00003 V=250.0", "MOVL C00003 V=250.0 PL=0"))
    assert job.plan_shift("C00004", "Y", 1.0).after_value == 191.0


# A-2. ///LVARS は数字をカンマで区切った形だけ通す
def test_lvars_with_numbers_is_allowed():
    job = jbi.parse(base().replace("///GROUP1 RB1\n", "///GROUP1 RB1\n///LVARS 0,0,0,0,0,0,0,0\n"))
    assert job.problems == []
    assert job.plan_shift("C00004", "Y", 1.0).after_value == 191.0


@pytest.mark.parametrize("bad", ["///LVARS", "///LVARS a,0", "///LVARS 0,,0", "///LVARS 0, 0", "///LVARS -1"])
def test_lvars_in_other_forms_is_rejected(bad):
    job = jbi.parse(base().replace("///GROUP1 RB1\n", f"///GROUP1 RB1\n{bad}\n"))
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


# A-3. 全角文字・コメント・Shift_JIS（2バイト目が 0x5C の文字、半角カナ、全角空白、///COMM、CRLF）
def _sjis_text(newline="\n"):
    text = (base()
            .replace("///ATTR SC,RW\n", "///ATTR SC,RW\n///COMM 置き場所の調整用\n")
            .replace("NOP\n", "NOP\n'ソ表能のワーク　取出し\n'ｿﾌﾄ ﾊﾝﾄﾞ\n")
            .replace("TIMER T=3.00\n", "TIMER T=3.00\n'表示　能力\n"))
    return text.replace("\n", newline)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_shift_jis_realistic_roundtrip(newline):
    data = _sjis_text(newline).encode("cp932")
    assert b"\x83\x5c" in data and b"\x95\x5c" in data and b"\x94\x5c" in data  # ソ・表・能
    job = jbi.parse_bytes(data)
    assert job.encoding == "cp932"
    assert job.problems == []
    assert job.render_bytes() == data


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.usefixtures("speed_enabled")
def test_shift_jis_realistic_edit_changes_one_line(newline):
    data = _sjis_text(newline).encode("cp932")
    job = jbi.parse_bytes(data)
    nl = newline.encode("ascii")
    new = job.plan_shift("C00004", "Y", 10.0).new_job.render_bytes()
    changed = [(a, b) for a, b in zip(data.split(nl), new.split(nl)) if a != b]
    assert changed == [(b"C00004=450.000,190.000,120.000,180.00,0.00,0.00",
                        b"C00004=450.000,200.000,120.000,180.00,0.00,0.00")]
    assert len(data.split(nl)) == len(new.split(nl))
    new = job.plan_speed(idx_of(job, "C00003"), 300.0).new_job.render_bytes()
    changed = [(a, b) for a, b in zip(data.split(nl), new.split(nl)) if a != b]
    assert changed == [(b"MOVL C00003 V=250.0", b"MOVL C00003 V=300.0")]


# B-5. INST部の命令は許可リストで判定する。知らない命令・前後の空白・空行は止める
@pytest.mark.usefixtures("speed_enabled")
def test_unknown_instruction_blocks_all_edits():
    """再現：MOVL C00005 を EIMOVL P005 にすると、出る移動を取り違えた変更案ができていた"""
    job = jbi.parse(base().replace("MOVL C00005 V=80.0", "EIMOVL P005 V=80.0"))
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_speed(idx_of(job, "C00003"), 300.0)


@pytest.mark.parametrize("line", ["FOO 1", "SPEED 10", "nop", "*L1 MOVL C00001 V=80.0"])
def test_instructions_outside_allowlist_are_problems(line):
    job = jbi.parse(base().replace("NOP\n", f"NOP\n{line}\n"))
    assert job.problems
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


@pytest.mark.parametrize("old,new", [
    ("///GROUP1 RB1\n", "///GROUP1 RB1\n  ///LVARS 0,0\n"),
    ("NOP\n", "NOP \n"),
    ("NOP\n", " NOP\n"),
    ("NOP\n", "NOP\n\n"),
    ("///RECTAN\n", "///RECTAN \n"),
    ("//INST\n", "//INST \n"),
])
def test_lines_with_surrounding_spaces_or_empty_are_problems(old, new):
    job = jbi.parse(base().replace(old, new))
    assert job.problems
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


def test_label_line_alone_is_known():
    job = jbi.parse(base().replace("NOP\n", "NOP\n*L1\n"))
    assert job.problems == []


# B-6. 巨大な整数は OverflowError でなく JbiError にする
@pytest.mark.usefixtures("speed_enabled")
def test_huge_integer_delta_is_rejected():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 10 ** 400)
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0, max_abs_delta_mm=10 ** 400)
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), 10 ** 400)


# B-7. 速度の書式が壊れるもの（整数部の桁数が元より多くなる）は止める。速度の上限の値は本人判断待ち
@pytest.mark.parametrize("bad", [1e300, 1000.0])
@pytest.mark.usefixtures("speed_enabled")
def test_speed_with_more_integer_digits_is_rejected(bad):
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError, match="桁"):
        job.plan_speed(idx_of(job, "C00003"), bad)


# C-8. 入る区間の始まり（直前の移動命令）の PL・CR も止める
@pytest.mark.parametrize("suffix", ["PL=3", "CR=20.0", "PL=I001", "pl=0"])
def test_shift_rejected_when_previous_move_has_pl_or_cr(suffix):
    text = base().replace("MOVL C00003 V=250.0", f"MOVL C00003 V=250.0 {suffix}")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"入る.*行目「.*C00003.*」"):
        job.plan_shift("C00004", "Y", 10.0)
    assert job.render() == text


# C-9. ロボット座標の位置は値が6個のときだけ変える。引数付きの RECTAN・PULSE などは分からない指示行
@pytest.mark.parametrize("values", [
    "450.000,190.000,120.000,180.00,0.00,0.00,0.00",
    "450.000,190.000,120.000,180.00,0.00",
    "450.000,190.000,120.000",
])
def test_robot_position_without_six_values_is_rejected(values):
    job = jbi.parse(base().replace("C00004=450.000,190.000,120.000,180.00,0.00,0.00", f"C00004={values}"))
    with pytest.raises(jbi.JbiError, match="6"):
        job.plan_shift("C00004", "Y", 1.0)


@pytest.mark.parametrize("old,new", [
    ("///RECTAN\n", "///RECTAN 1\n"),
    ("///TOOL 0\n", "///TOOL A\n"),
    ("///RCONF 0,0,0,0,0,0,0,0\n", "///RCONF x\n"),
])
def test_directive_with_unexpected_argument_is_unknown(old, new):
    job = jbi.parse(base().replace(old, new))
    assert job.positions["C00004"].after_unknown_directive
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Y", 1.0)


def test_pulse_with_argument_is_unknown():
    job = jbi.parse(base().replace("C00005=", "///PULSE 1\nC00005="))
    assert job.positions["C00005"].after_unknown_directive
    assert not job.positions["C00004"].after_unknown_directive


# C-10 と D-4. ジョブの中に JUMP・CALL・RET・ラベル行が1つでもあれば位置の変更を止める
# （入る・出る区間の中だけでなく、区間の外にあっても止める）
@pytest.mark.parametrize("old,new,line", [
    ("TIMER T=3.00\n", "TIMER T=3.00\nJUMP *L1\n", "JUMP *L1"),
    ("TIMER T=3.00\n", "TIMER T=3.00\nCALL JOB:SUB\n", "CALL JOB:SUB"),
    ("TIMER T=3.00\n", "TIMER T=3.00\nRET\n", "RET"),
    ("MOVL C00004 V=80.0\n", "*L1\nMOVL C00004 V=80.0\n", "*L1"),
    ("MOVL C00004 V=80.0\n", "JUMP *L1\nMOVL C00004 V=80.0\n", "JUMP *L1"),
    ("NOP\n", "NOP\nCALL JOB:SUB\n", "CALL JOB:SUB"),
    ("NOP\n", "NOP\n*L1\n", "*L1"),
    ("MOVL C00000 V=200.0\nEND\n", "MOVL C00000 V=200.0\nRET\nEND\n", "RET"),
])
def test_flow_change_anywhere_stops_position_edits(old, new, line):
    job = jbi.parse(base().replace(old, new))
    with pytest.raises(jbi.JbiError, match="制御の流れ") as e:
        job.plan_shift("C00004", "Y", 1.0)
    assert f"「{line}」" in str(e.value)


# ---- 修正ラウンド1 追加（本人の判断 D1〜D5） ----
# D-1. 速度の変更は既定で止める
def test_speed_change_is_disabled_by_default():
    assert jbi.SPEED_CHANGE_ENABLED is False
    text = base()
    job = jbi.parse(text)
    i = idx_of(job, "C00003")
    with pytest.raises(jbi.JbiError, match="速度の変更は、単位・指定形式・適用範囲の確認が済むまで"):
        job.plan_speed(i, 300.0)
    with pytest.raises(jbi.JbiError, match="止めています"):
        job.set_speed(i, 300.0)
    assert job.render() == text


# D-2. 元のジョブ（基準）からの累積で、軸ごとに検査する
# 量は上限（20mm）に対する割合で決める：1回はどちらも上限以内、2回の合計だけが上限を超える
STEP = 12.0  # 上限の 0.6 倍。2回で 24mm（上限を超える）
BACK = 16.0  # 上限の 0.8 倍


def test_cumulative_steps_are_within_one_shot_limit_but_exceed_in_total():
    assert STEP <= jbi.DEFAULT_MAX_SHIFT_MM and BACK <= jbi.DEFAULT_MAX_SHIFT_MM
    assert STEP * 2 > jbi.DEFAULT_MAX_SHIFT_MM


def test_cumulative_shift_over_limit_is_rejected():
    first = jbi.parse(base()).plan_shift("C00004", "Y", STEP)
    assert first.after_value == 202.0
    with pytest.raises(jbi.JbiError, match="累積"):
        first.new_job.plan_shift("C00004", "Y", STEP)


def test_cumulative_shift_back_within_limit_is_allowed():
    first = jbi.parse(base()).plan_shift("C00004", "Y", STEP)
    second = first.new_job.plan_shift("C00004", "Y", -BACK)
    assert second.after_value == 186.0
    assert second.baseline_value == 190.0
    assert abs(second.cumulative_delta - (-4.0)) < 1e-9


def test_cumulative_is_per_axis():
    job = jbi.parse(base()).plan_shift("C00004", "Y", BACK).new_job
    assert job.plan_shift("C00004", "Z", BACK).after_value == 136.0


def test_baseline_is_kept_through_rename():
    job = jbi.parse(base()).plan_shift("C00004", "Y", STEP).new_job.rename("DEMO_PLACE_V1")
    with pytest.raises(jbi.JbiError, match="累積"):
        job.plan_shift("C00004", "Y", STEP)


def test_reparsed_job_has_new_baseline():
    """読み直したジョブは基準が新しくなる（ファイルをまたいだ引き継ぎは別の設計で扱う）"""
    text = jbi.parse(base()).plan_shift("C00004", "Y", STEP).new_job.render()
    assert jbi.parse(text).plan_shift("C00004", "Y", STEP).after_value == 214.0


def test_larger_max_argument_does_not_bypass_cumulative_limit():
    # 修正ラウンド2：1回の上限は引数で上げられなくなったので、2回に分けて累積を確かめる
    job = jbi.parse(base()).plan_shift("C00004", "Y", STEP, max_abs_delta_mm=100.0).new_job
    with pytest.raises(jbi.JbiError, match="累積"):
        job.plan_shift("C00004", "Y", STEP, max_abs_delta_mm=100.0)


# D-3. 極端な値は例外で落とさず JbiError で断る（上限の引数も含む）
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 10 ** 400, True, "50"])
def test_extreme_max_argument_is_rejected(bad):
    with pytest.raises(jbi.JbiError):
        jbi.parse(base()).plan_shift("C00004", "Y", 1.0, max_abs_delta_mm=bad)


@pytest.mark.parametrize("bad", [-float("inf"), -(10 ** 400), 10 ** 400])
def test_extreme_delta_is_rejected(bad):
    with pytest.raises(jbi.JbiError):
        jbi.parse(base()).plan_shift("C00004", "Y", bad)


@pytest.mark.usefixtures("speed_enabled")
@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 10 ** 400, 1e308])
def test_extreme_speed_is_rejected(bad):
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError):
        job.plan_speed(idx_of(job, "C00003"), bad)


# D-5. 知らない命令があっても最後まで読む（表示のため）。変更は problems で止める
def test_unknown_command_is_read_but_blocks_edits():
    text = base().replace("TIMER T=3.00\n", "TIMER T=3.00\nWAIT IN#(1)=ON\n")
    job = jbi.parse(text)
    assert any("WAIT" in p for p in job.problems)
    assert len(job.positions) == 6
    assert [i.text for i in job.instructions][-3:] == ["MOVL C00005 V=80.0", "MOVL C00000 V=200.0", "END"]
    assert any(i.text == "WAIT IN#(1)=ON" for i in job.instructions)
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


# ---- 修正ラウンド2 ----
# 1. 入る区間の始まり（直前の移動命令）も、出る側と同じ許可リストで判定する
@pytest.mark.parametrize("prev_line", [
    "MOVL C00003 V=250.0 UNTIL IN#(1)=ON",
    "MOVJ C00003 VJ=50.00",
    "MOVC C00003 V=250.0",
    "MOVS C00003 V=250.0",
    "MOVL P000 V=250.0",
    "MOVL LP000 V=250.0",
    "MOVL BP000 V=250.0",
    "MOVL C00003 V=250.0 NWAIT",
    "MOVL C00003 V=250.0 ENWAIT",
    "MOVL C00003 V=250.0 SPDL=10",
    "MOVL C00003 V=250.0 ACC=50",
    "MOVL C00003 V=250.0 +MOVJ C00001 VJ=10.00",
    "MOVL C00003 V=250.0 TOOL#(1)",
    "MOVL C00003 V=250.0 XYZ",
])
def test_shift_rejected_when_entry_move_is_not_plain_movl(prev_line):
    text = base().replace("MOVL C00003 V=250.0", prev_line)
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"入る.*行目「"):
        job.plan_shift("C00004", "Y", 1.0)
    assert job.render() == text


# 2. 直前・次の移動命令が無い位置は、入る／出る区間が決まらないので止める
def test_shift_rejected_when_no_entry_move():
    job = jbi.parse(base().replace("NOP\nMOVL C00000 V=200.0\n", "NOP\n"))
    with pytest.raises(jbi.JbiError, match="入る"):
        job.plan_shift("C00001", "Y", 1.0)


# 3. END より後ろの命令は problems（実行されない命令だけが使う位置も、これで止まる）
@pytest.mark.parametrize("after", ["NOP", "MOVL C00005 V=80.0"])
def test_instruction_after_end_is_problem(after):
    text = base().replace("MOVL C00005 V=80.0\n", "") if after.startswith("MOVL") else base()
    job = jbi.parse(text + after + "\n")
    assert any("END" in p for p in job.problems)
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


def test_comment_after_end_is_not_problem():
    job = jbi.parse(base() + "'memo\n")
    assert job.problems == []


# 4. Unicode の空白（全角空白・NBSP）を含む命令行は problems。読むのは最後まで続ける
@pytest.mark.parametrize("line", ["　", "\xa0", "　MOVL C00001 V=80.0", "NOP　", "DOUT\xa0OT#(1) ON"])
def test_unicode_space_in_instruction_line_is_problem(line):
    job = jbi.parse(base().replace("NOP\n", f"NOP\n{line}\n"))
    assert job.problems
    assert job.instructions[-1].text == "END"
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


# 5. max_abs_delta_mm は下げる方向だけ効く
def test_max_argument_cannot_raise_single_limit():
    with pytest.raises(jbi.JbiError, match="上限") as e:
        jbi.parse(base()).plan_shift("C00004", "Y", jbi.DEFAULT_MAX_SHIFT_MM + 1.0, max_abs_delta_mm=100.0)
    assert "累積" not in str(e.value)  # 累積の検査ではなく、1回の上限で断られること


def test_max_argument_can_lower_limit():
    job = jbi.parse(base())
    with pytest.raises(jbi.JbiError, match="上限"):
        job.plan_shift("C00004", "Y", 6.0, max_abs_delta_mm=5.0)
    assert job.plan_shift("C00004", "Y", 5.0, max_abs_delta_mm=5.0).after_value == 195.0


# 6. 位置の値の整数部が7桁以上なら problems（扱える桁の問題。ロボットの範囲を決めるものではない）
@pytest.mark.parametrize("value", ["1234567.000", "-1234567.000", "1" * 400 + ".000"])
def test_position_value_with_too_many_digits_is_problem(value):
    job = jbi.parse(base().replace("C00005=450.000", f"C00005={value}"))
    assert any("桁" in p for p in job.problems)
    with pytest.raises(jbi.JbiError, match="読めない"):
        job.plan_shift("C00004", "Y", 1.0)


def test_position_value_with_six_digits_is_read():
    job = jbi.parse(base().replace("C00005=450.000", "C00005=999999.000"))
    assert job.problems == []
