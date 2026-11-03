"""受入テスト（仕様v2.1 11章の1〜4、10の一部）。実装より先に書いた。"""
from pathlib import Path

import pytest

from voice_teaching import jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def load():
    return DUMMY.read_text(encoding="utf-8")


@pytest.fixture
def speed_enabled(monkeypatch):
    """速度の変更は既定で止めている（SPEED_CHANGE_ENABLED = False）。検査の中身を試すテストだけ True にする"""
    monkeypatch.setattr(jbi, "SPEED_CHANGE_ENABLED", True)


def test_roundtrip_unchanged():
    """1. 無変更で読込→書出しが元と完全に一致"""
    text = load()
    job = jbi.parse(text)
    assert job.render() == text


def test_roundtrip_keeps_crlf():
    text = load().replace("\n", "\r\n")
    assert jbi.parse(text).render() == text


def test_positions_and_instructions_are_read():
    job = jbi.parse(load())
    assert job.name == "DEMO_PLACE"
    assert job.is_robot_coordinates()
    assert job.positions["C00004"].xyz == (450.0, 190.0, 120.0)
    moves = [i for i in job.instructions if i.command == "MOVL"]
    assert len(moves) == 7
    assert moves[3].position == "C00003"
    assert moves[3].speed == 250.0


def test_shift_changes_only_one_axis_of_one_position():
    """2. 指定した位置の指定した軸だけが変わり、他は不変"""
    text = load()
    job = jbi.parse(text)
    new_text = job.shift_position("C00004", "Y", 10.0).render()
    old_lines, new_lines = text.splitlines(), new_text.splitlines()
    changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert changed == [
        ("C00004=450.000,190.000,120.000,180.00,0.00,0.00",
         "C00004=450.000,200.000,120.000,180.00,0.00,0.00"),
    ]


def test_shift_z_works_with_same_mechanism():
    """3. 上下（Z）も同じ仕組みで変わる"""
    job = jbi.parse(load()).shift_position("C00004", "Z", -5.5)
    assert job.positions["C00004"].xyz == (450.0, 190.0, 114.5)


def test_shift_keeps_decimal_places():
    job = jbi.parse(load()).shift_position("C00004", "X", 0.123)  # 保存できる量（小数3けた）
    line = [l for l in job.render().splitlines() if l.startswith("C00004=")][0]
    assert line.startswith("C00004=450.123,")


@pytest.mark.usefixtures("speed_enabled")
def test_speed_changes_only_that_instruction():
    """4. 指定したMOVLのV=だけが変わる"""
    text = load()
    job = jbi.parse(text)
    idx = [i.index for i in job.instructions if i.position == "C00003"][0]
    new_text = job.set_speed(idx, 300.0).render()
    changed = [(a, b) for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b]
    assert changed == [("MOVL C00003 V=250.0", "MOVL C00003 V=300.0")]


def test_original_is_not_modified():
    """元のジョブは残る（変更は新しいオブジェクトで返す）"""
    job = jbi.parse(load())
    job.shift_position("C00004", "Y", 10.0)
    assert job.positions["C00004"].xyz == (450.0, 190.0, 120.0)


def test_rename_changes_only_name_line():
    text = load()
    new_text = jbi.parse(text).rename("DEMO_PLACE_V1").render()
    changed = [(a, b) for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b]
    assert changed == [("//NAME DEMO_PLACE", "//NAME DEMO_PLACE_V1")]


def test_unknown_position_is_rejected():
    with pytest.raises(jbi.JbiError):
        jbi.parse(load()).shift_position("C00099", "Y", 1.0)


def test_pulse_job_is_not_robot_coordinates():
    text = load().replace("///POSTYPE ROBOT\n", "///POSTYPE PULSE\n").replace("///RECTAN\n", "")
    job = jbi.parse(text)
    assert not job.is_robot_coordinates()
    with pytest.raises(jbi.JbiError):
        job.shift_position("C00004", "Y", 1.0)


@pytest.mark.usefixtures("speed_enabled")
def test_pl_and_speed_variable_are_detected():
    text = load().replace("MOVL C00003 V=250.0", "MOVL C00003 V=250.0 PL=3").replace(
        "MOVL C00004 V=80.0", "MOVL C00004 V=I000")
    job = jbi.parse(text)
    i3 = [i for i in job.instructions if i.position == "C00003"][0]
    i4 = [i for i in job.instructions if i.position == "C00004"][0]
    assert i3.pl == 3
    assert i4.speed is None
    with pytest.raises(jbi.JbiError):
        job.set_speed(i4.index, 100.0)


def test_normalized_hash_ignores_name_and_date():
    text = load()
    h1 = jbi.parse(text).normalized_hash()
    h2 = jbi.parse(text.replace("///DATE 2026/11/02 14:40", "///DATE 2026/11/03 09:00")).rename("OTHER").normalized_hash()
    h3 = jbi.parse(text).shift_position("C00004", "Y", 1.0).normalized_hash()
    assert h1 == h2
    assert h1 != h3


# ---- 安全の止め（PR #1 のレビュー指摘 P1 ×2 と、内部レビューの指摘への対応）。実装より先に書いた ----
# 分からないものは全部止める：読み取れない書き方は「無い」とみなさず、変更を断る

def _index_of(job, position):
    return [i.index for i in job.instructions if i.position == position][0]


@pytest.mark.parametrize("suffix", [
    "PL=3", "PL=I001", "PL = 0", "pl=0", "PL=0.0", "PL = 3", "pl=3", "PL=3.5", "CR=10.0",
])
@pytest.mark.usefixtures("speed_enabled")
def test_speed_on_move_with_pl_or_cr_is_rejected(suffix):
    """PL・CRが付いたMOVLは（値や書き方にかかわらず）速度を変えない。理由を返し、何も変えない"""
    text = load().replace("MOVL C00003 V=250.0", f"MOVL C00003 V=250.0 {suffix}")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="変更しません"):
        job.set_speed(_index_of(job, "C00003"), 300.0)
    assert job.render() == text


@pytest.mark.parametrize("line", [
    "MOVJ C00004 VJ=50.00",
    "MOVC C00004 V=80.0",
    "MOVS C00004 V=80.0",
    "movj C00004 VJ=50.00",
    "SMOVL C00004 V=80.0",
    "IMOV C00004 V=80.0",
    "*L1 MOVL C00004 V=80.0",
    "MOVL C00004 V=80.0 PL=2",
    "MOVL C00004 V=80.0 PL=I001",
    "MOVL C00004 V=80.0 CR=10.0",
])
def test_shift_used_by_unsupported_line_is_rejected(line):
    """位置を使う行が、PL・CRの無いMOVL以外なら、その位置は変えない"""
    # 普通のMOVLを残したまま、止める対象の行を足す（「使っていない」で断られる道を通さない）
    text = load().replace("MOVL C00004 V=80.0\n", f"MOVL C00004 V=80.0\n{line}\n")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match=r"「.*C00004.*」は"):
        job.shift_position("C00004", "Y", 10.0)
    assert job.render() == text


def test_shift_rejected_when_other_command_mentions_position():
    """移動命令以外（GETS など）が位置を使うときも変えない"""
    text = load().replace("NOP\n", "NOP\nGETS PX000 C00004\n")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="GETS"):
        job.shift_position("C00004", "Y", 10.0)
    assert job.render() == text


def test_shift_rejected_when_position_is_second_target():
    """同時移動（+MOVJ）の相手になっている位置は変えない"""
    text = load().replace("MOVL C00005 V=80.0", "MOVL C00005 V=80.0 +MOVJ C00004 VJ=50.00")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="組になっている"):
        job.shift_position("C00004", "Y", 10.0)
    assert job.render() == text


def test_shift_checks_every_user_of_position():
    """同じ位置を複数の命令が使うとき、1つでも止める対象なら変えない"""
    # C00000 は最初と最後のMOVLで使われている。最後の1つだけをMOVJにする
    lines = load().splitlines(keepends=True)
    last = max(i for i, l in enumerate(lines) if l.startswith("MOVL C00000 "))
    lines[last] = lines[last].replace("MOVL C00000 V=200.0", "MOVJ C00000 VJ=50.00")
    text = "".join(lines)
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="MOVJ"):
        job.shift_position("C00000", "Z", 10.0)
    assert job.render() == text


def test_shift_of_unused_position_is_rejected():
    """どの命令も使っていない位置は、何のための位置か分からないので変えない"""
    text = load().replace("MOVL C00005 V=80.0\n", "")
    job = jbi.parse(text)
    assert "C00005" in job.positions
    with pytest.raises(jbi.JbiError, match="使っていない"):
        job.shift_position("C00005", "Y", 10.0)
    assert job.render() == text


def test_shift_of_shared_position_is_rejected():
    """C00000 は2つのMOVL（PL・CRなし）が共用しているので変更しない
    （PR #1 レビュー指摘：原本の test_shared_position_is_rejected に合わせて、許可から拒否に変えた）"""
    text = load()
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="共用"):
        job.shift_position("C00000", "Z", 10.0)
    assert job.render() == text


@pytest.mark.parametrize("suffix", ["APL=3", "ACC=50", "'memo"])
@pytest.mark.usefixtures("speed_enabled")
def test_move_with_unknown_words_is_rejected(suffix):
    """MOVL Cxxxxx V=数値 の形に無い語が付いた行は、意味が分からないので変えない"""
    text = load().replace("MOVL C00004 V=80.0", f"MOVL C00004 V=80.0 {suffix}")
    job = jbi.parse(text)
    with pytest.raises(jbi.JbiError, match="扱える形"):
        job.set_speed(_index_of(job, "C00004"), 100.0)
    with pytest.raises(jbi.JbiError, match="扱える形"):
        job.shift_position("C00004", "Y", 10.0)
    assert job.render() == text
