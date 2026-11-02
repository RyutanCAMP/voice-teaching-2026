"""受入テスト（仕様v2.1 11章の1〜4、10の一部）。実装より先に書いた。"""
from pathlib import Path

import pytest

from voice_teaching import jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def load():
    return DUMMY.read_text(encoding="utf-8")


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
    job = jbi.parse(load()).shift_position("C00004", "X", 0.1234)
    line = [l for l in job.render().splitlines() if l.startswith("C00004=")][0]
    assert line.startswith("C00004=450.123,")


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
