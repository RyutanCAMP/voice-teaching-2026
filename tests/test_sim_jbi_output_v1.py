"""SIM の出力は .JBI（11/7 唐川さんの決定）。名前は <JOB名>_SIM_YYMMDDHHMM.JBI（日本時間・分まで）、同じ分は _02…。
中身の形（JOB として読み直せる・//NAME・文字コード・改行）と、元の JOB が変わらないことを確かめる。偽の時計・一時フォルダだけ。"""
import hashlib
import re
from pathlib import Path

import pytest

from voice_teaching import jbi, sim

ROOT = Path(__file__).resolve().parent.parent
FIXED = sim.datetime(2026, 11, 7, 14, 10, 59, tzinfo=sim.JST)


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "sim"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    monkeypatch.setattr(sim, "clock", lambda: FIXED)
    return d


def shift_export(name, pos, axis, mm):
    path = ROOT / "samples" / "dummy" / name
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = path.read_bytes()
    p = sim.simulate_shift(jbi.parse_bytes(data), pos, axis, mm)
    out = sim.export_sim(p, sim.approve(p, "テスト"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # 元の JOB は変わらない
    return data, out


def test_name_extension_and_stamp(out):
    data, f = shift_export("DEMO_RJ_XYZ.JBI", "C00004", "Z", 5)
    assert f.name == "DEMO_RJ_XYZ_SIM_2611071410.JBI" and f.suffix == ".JBI"
    assert not any(p.name.endswith(".txt") for p in out.iterdir())


def test_inside_is_a_jbi_with_the_sim_name_same_encoding_and_line_endings(out):
    data, f = shift_export("DEMO_RJ_XYZ.JBI", "C00004", "Z", 5)
    got = f.read_bytes()
    job = jbi.parse_bytes(got)  # JOB として読み直せる
    assert job.name == "DEMO_RJ_XYZ_SIM"  # //NAME は <JOB名>_SIM（日時はファイル名だけ）
    assert job.positions["C00004"].values[2] == pytest.approx(jbi.parse_bytes(data).positions["C00004"].values[2] + 5)
    # 改行の種類と行の数は元と同じ。違う行は //NAME と変えた位置の2行だけ
    assert got.count(b"\r\n") == data.count(b"\r\n") and got.count(b"\n") == data.count(b"\n")
    a, b = data.splitlines(keepends=True), got.splitlines(keepends=True)
    assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 2
    assert got.decode("ascii")  # 合成 JOB は ASCII のまま（元と同じ文字コード）


def test_same_minute_gets_a_number_and_nothing_is_overwritten(out):
    _, f1 = shift_export("DEMO_RJ_XYZ.JBI", "C00004", "Z", 5)
    keep = f1.read_bytes()
    _, f2 = shift_export("DEMO_RJ_XYZ.JBI", "C00004", "Z", 3)
    assert f2.name == "DEMO_RJ_XYZ_SIM_2611071410_02.JBI"
    assert f1.read_bytes() == keep


def test_repeat_output_is_also_jbi(out):
    path = ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"
    data = path.read_bytes()
    p = sim.simulate_repeat(jbi.parse_bytes(data), "C00001", "C00003", 3)
    f = sim.export_sim(p, sim.approve(p, "テスト"))
    assert re.fullmatch(r"DEMO_BUTTON_REPEAT_SIM_2611071410\.JBI", f.name)
    job = jbi.parse_bytes(f.read_bytes())
    assert job.name == "DEMO_BUTTON_REPEAT_SIM"
    assert f.read_bytes().count(b"MOVL C00002 V=30.0") == data.count(b"MOVL C00002 V=30.0") + 1
