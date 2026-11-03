"""C変数（ジョブの中の位置 C00000 など）だけを使う独立したデモ JOB の受入テスト。実装（デモ JOB の作成）より先に書いた。

実機では P変数の使用枠が上限に達しているため、デモは C変数方式で行う（唐川さん、2026-11-02）。
アプリは P変数への新規割り当てや既存 P変数の変更を行わない。このテストでそれを確かめる。
"""
import re
from pathlib import Path

import pytest

from voice_teaching import jbi, sim
from voice_teaching.web.flow import WebFlow

DEMO = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
P_VAR = re.compile(r"(?<![A-Za-z0-9#])L?P\d{3}\b|\bP\[|\bLP\b")  # P000・LP000 などの位置変数の参照


def lines(b: bytes) -> list[bytes]:
    return b.split(b"\r\n")


def test_demo_job_uses_only_c_positions_and_no_p_variables():
    data = DEMO.read_bytes()
    job = jbi.parse_bytes(data)
    assert job.problems == []
    assert job.render_bytes() == data  # 読込→書出しがバイト一致
    assert job.is_robot_coordinates()
    assert sorted(job.positions) == [f"C0000{i}" for i in range(8)]
    for ins in job.instructions:
        assert not P_VAR.search(ins.text), ins.text  # P変数を使う命令が無い
    assert b"\r\n" in data and data.count(b"\r\n") == data.count(b"\n")  # 改行は CRLF（実機の書き方を想定）


def test_place_point_is_used_by_one_movl_entered_and_left_by_movl():
    job = jbi.parse_bytes(DEMO.read_bytes())
    users = job.users_of("C00005")
    assert len(users) == 1 and users[0].command == "MOVL"
    moves = [i for i in job.instructions if i.position]
    k = [i.position for i in moves].index("C00005")
    assert moves[k - 1].position == "C00004" and moves[k + 1].position == "C00006"  # 真上から下りて、真上へ上がる


# 上下の向き（CHECKER_026 M2）：「上・下」を Z として読むことを前提にしたテスト。一時フォルダの確認済みの設定を当てる
@pytest.mark.usefixtures("updown_demo_c")
def test_full_simulation_flow_changes_only_the_place_point(tmp_path):
    """読込 → 位置選択 → 指示 → 変更候補 → 比較 → 承認 → シミュレーション用の出力"""
    data = DEMO.read_bytes()
    wf = WebFlow()
    r = wf.load(data)
    assert r["ok"] is True, r
    sid = r["session_id"]
    assert wf.select(sid, "C00005")["status"] == "ok"
    h = wf.hear(sid, "この位置を上に10mm", source="text")
    assert h["status"] == "ok", h
    cand = h["state"]["candidate"]
    assert cand["before_line"] == "C00005=400.000,150.000,200.000,180.00,0.00,0.00"
    assert cand["after_line"] == "C00005=400.000,150.000,210.000,180.00,0.00,0.00"
    assert wf.compare(sid)["status"] == "ok"
    assert wf.approve(sid, "テスト")["ok"] is True
    e = wf.export(sid)
    assert e["ok"] is True, e
    out = Path(e["path"])
    assert out.name == "DEMO_C_PLACE_SIM.JBI.txt"
    new = out.read_bytes()
    diff = [(a, b) for a, b in zip(lines(data), lines(new)) if a != b]
    assert diff == [
        (b"//NAME DEMO_C_PLACE", b"//NAME DEMO_C_PLACE_SIM"),
        (b"C00005=400.000,150.000,200.000,180.00,0.00,0.00", b"C00005=400.000,150.000,210.000,180.00,0.00,0.00"),
    ]
    assert len(lines(new)) == len(lines(data))
    assert DEMO.read_bytes() == data  # 元のジョブは変わらない


def test_p_variable_lines_are_never_changed_by_a_proposal():
    """ジョブに P変数を使う命令があっても、変更候補は C の位置の1行だけを変え、P変数の行は1文字も変えない"""
    data = DEMO.read_bytes().replace(b"MOVL C00007 V=200.0\r\n", b"MOVL C00007 V=200.0\r\nMOVJ P001 VJ=10.00\r\n")
    job = jbi.parse_bytes(data)
    p = sim.simulate_shift(job, "C00005", "Z", 10.0)
    changed = [(a, b) for a, b in zip(lines(p.original_bytes), lines(p.new_bytes)) if a != b]
    assert len(changed) == 1 and changed[0][0].startswith(b"C00005=")
    assert b"MOVJ P001 VJ=10.00" in p.new_bytes


@pytest.mark.parametrize("position", ["P001", "P000", "LP000"])
def test_p_variable_cannot_be_selected_as_target(position):
    job = jbi.parse_bytes(DEMO.read_bytes())
    with pytest.raises((jbi.JbiError, sim.SimError)):
        sim.simulate_shift(job, position, "Z", 10.0)
