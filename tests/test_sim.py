"""シミュレーションモードの受入テスト（実装より先に書いた）。

合成ジョブ（samples/dummy/）で、変更候補 → 承認 → シミュレーション用の出力 が一通り動くことを確かめる。
出力は一時フォルダ（tmp_path）にだけ書く。
"""
import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from voice_teaching import jbi, sim

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"
FORBIDDEN_WORDS = ("安全です", "当たります", "干渉します")


def raw() -> bytes:
    return DUMMY.read_bytes()


def job():
    return jbi.parse_bytes(raw())


def lines_of(data: bytes) -> list[bytes]:
    return re.findall(rb"[^\n]*\n|[^\n]+\Z", data)


def diff_indexes(a: bytes, b: bytes) -> list[int]:
    la, lb = lines_of(a), lines_of(b)
    assert len(la) == len(lb)
    return [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]


def all_text(p) -> str:
    return " ".join([p.blocked_reason or "", *p.warnings, p.before_line, p.after_line])


# ---- 1. 変更候補 ----
def test_passing_change_uses_plan_shift_result():
    """通る変更（C00004 Y+10）：実機用の検査の結果をそのまま使う"""
    j = job()
    expected = j.plan_shift("C00004", "Y", 10)
    p = sim.simulate_shift(j, "C00004", "Y", 10)
    assert p.mode == "sim"
    assert p.blocked_reason is None
    assert p.position == "C00004" and p.axis == "Y" and p.requested == 10.0
    assert p.before_value == 190.0 and p.after_value == 200.0
    assert p.before_line == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert p.after_line == "C00004=450.000,200.000,120.000,180.00,0.00,0.00"
    assert p.warnings == expected.warnings
    assert p.new_bytes == expected.new_job.render_bytes()
    assert p.original_bytes == raw()
    assert p.line_index == j.positions["C00004"].line_index
    assert diff_indexes(p.original_bytes, p.new_bytes) == [p.line_index]
    assert [u[0] for u in p.users] == [25]  # 26行目（0始まりで25。line_index と同じ数え方）
    assert "MOVL C00004 V=80.0" in p.users[0][1]


def test_shared_position_is_blocked_for_real_but_simulated():
    """共用の C00000：実機用の検査では止まるが、シミュレーションでは候補になる"""
    j = job()
    with pytest.raises(jbi.JbiError):
        j.plan_shift("C00000", "Z", -5)
    p = sim.simulate_shift(j, "C00000", "Z", -5)
    assert p.blocked_reason  # 止まる理由が入る
    assert "共用" in p.blocked_reason
    assert any(w.startswith("実機用の検査では止まります：") for w in p.warnings)
    assert any("この位置を使う命令がすべて変わります" in w for w in p.warnings)
    assert len(p.users) == 2
    assert p.before_value == 350.0 and p.after_value == 345.0
    assert p.after_line == "C00000=450.000,-200.000,345.000,180.00,0.00,0.00"
    assert diff_indexes(p.original_bytes, p.new_bytes) == [p.line_index]
    # 読み直した値
    assert jbi.parse_bytes(p.new_bytes).positions["C00000"].values[2] == 345.0


def test_negative_zero_becomes_zero():
    data = raw().replace(b"C00004=450.000,190.000,", b"C00004=450.000,0.500,")
    p = sim.simulate_shift(jbi.parse_bytes(data), "C00004", "Y", -0.5)
    assert "C00004=450.000,0.000," in p.after_line
    assert "-0.000" not in p.after_line


@pytest.mark.parametrize("position,axis,delta", [
    ("C00099", "Y", 10),          # 無い位置
    ("C00004", "W", 10),          # 軸W
    ("C00004", "Rx", 10),
    ("C00004", "Y", float("nan")),
    ("C00004", "Y", float("inf")),
    ("C00004", "Y", 0),
    ("C00004", "Y", True),
    ("C00004", "Y", "10"),
    ("C00004", "Y", 21),           # 上限（20mm）を超える
    ("C00000", "Z", -51),          # 共用の位置でも上限は守る
    ("C00004", "Y", 0.0001),      # 丸めると変わらない
    ("C00000", "Z", 0.0001),      # 実機用で止まる位置でも、丸めて変わらなければ作らない
])
def test_cannot_simulate(position, axis, delta):
    with pytest.raises(sim.SimError) as e:
        sim.simulate_shift(job(), position, axis, delta)
    assert str(e.value)  # 理由つき


def test_not_robot_coordinates_cannot_simulate():
    data = raw().replace(b"///RECTAN\n", b"///PULSE\n")
    with pytest.raises(sim.SimError):
        sim.simulate_shift(jbi.parse_bytes(data), "C00000", "Z", 5)


def test_original_bytes_unchanged():
    j = job()
    before = j.render_bytes()
    lines_before = list(j.lines)
    sim.simulate_shift(j, "C00004", "Y", 10)
    sim.simulate_shift(j, "C00000", "Z", -5)
    assert j.render_bytes() == before == raw()
    assert j.lines == lines_before


def test_crlf_and_shift_jis_comment_change_only_one_line():
    text = DUMMY.read_text(encoding="utf-8")
    text = text.replace("NOP\n", "NOP\n'ワークを置く（架空のコメント）\n").replace("\n", "\r\n")
    data = text.encode("cp932")
    j = jbi.parse_bytes(data)
    assert j.encoding == "cp932"
    for pos, axis, delta in (("C00004", "Y", 10), ("C00000", "Z", -5)):
        p = sim.simulate_shift(j, pos, axis, delta)
        assert p.original_bytes == data
        idx = diff_indexes(data, p.new_bytes)
        assert idx == [p.line_index]
        assert lines_of(p.new_bytes)[p.line_index].endswith(b"\r\n")
        assert "ワークを置く".encode("cp932") in p.new_bytes
        assert p.new_bytes.count(b"\r\n") == data.count(b"\r\n")
        assert p.new_bytes.count(b"\n") == data.count(b"\n")


def test_messages_do_not_use_forbidden_words():
    for p in (sim.simulate_shift(job(), "C00004", "Y", 10), sim.simulate_shift(job(), "C00000", "Z", -5)):
        for w in FORBIDDEN_WORDS:
            assert w not in all_text(p)
    for args in (("C00099", "Y", 10), ("C00004", "Y", 51)):
        with pytest.raises(sim.SimError) as e:
            sim.simulate_shift(job(), *args)
        for w in FORBIDDEN_WORDS:
            assert w not in str(e.value)


def test_limit_message_says_provisional():
    with pytest.raises(sim.SimError) as e:
        sim.simulate_shift(job(), "C00004", "Y", 51)
    assert "仮の上限（確認待ち）" in str(e.value)


def test_proposal_is_frozen():
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.new_bytes = b""



def written(root):
    """root の下に書かれたファイル（出力先は sim.DEFAULT_OUT_DIR = root/"sim"）"""
    return [p for p in root.rglob("*") if p.is_file()]

# ---- 2. 承認 ----
def test_approve_records_hashes_and_person():
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    a = sim.approve(p, "テスト担当")
    assert a.original_sha256 == hashlib.sha256(p.original_bytes).hexdigest()
    assert a.new_sha256 == hashlib.sha256(p.new_bytes).hexdigest()
    assert a.approved_by == "テスト担当"
    assert a.approved_at
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.approved_by = "別の人"


@pytest.mark.parametrize("who", ["", "   ", None])
def test_approve_needs_a_person(who):
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    with pytest.raises(sim.SimError):
        sim.approve(p, who)


# ---- 3. シミュレーション用の出力 ----
def test_export_writes_sim_job_with_two_line_diff(tmp_path):
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    out = sim.export_sim(p, sim.approve(p, "テスト担当"))
    # 11/7：名前は <ジョブ名>_SIM_YYYYMMDD_HHMMSS.JBI.txt（日本時間）
    assert out.parent == tmp_path / "sim" and re.fullmatch(r"DEMO_PLACE_SIM_\d{8}_\d{6}\.JBI\.txt", out.name)
    written = out.read_bytes()
    j2 = jbi.parse_bytes(written)
    assert j2.name == "DEMO_PLACE_SIM"
    assert j2.positions["C00004"].values[1] == 200.0
    name_line = next(i for i, l in enumerate(lines_of(raw())) if l.startswith(b"//NAME "))
    assert diff_indexes(raw(), written) == sorted([name_line, p.line_index])
    assert lines_of(written)[name_line] == b"//NAME DEMO_PLACE_SIM\n"
    assert DUMMY.read_bytes() == raw()  # 元のファイルはそのまま


def test_blocked_for_real_but_simulated_change_is_not_approved(tmp_path):
    """実機用の検査で止まった候補は、合成ジョブでも承認しない（CHECKER_019 R1。以前は出力できていた）"""
    p = sim.simulate_shift(job(), "C00000", "Z", -5)
    assert p.blocked_reason and "共用" in p.blocked_reason
    with pytest.raises(sim.SimError, match="実機用の検査で止まった"):
        sim.approve(p, "テスト担当")
    assert written(tmp_path) == []


def test_export_rejects_bytes_replaced_after_approval(tmp_path):
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    a = sim.approve(p, "テスト担当")
    other = sim.simulate_shift(job(), "C00004", "Y", 20)
    tampered = dataclasses.replace(p, new_bytes=other.new_bytes)
    with pytest.raises(sim.SimError):
        sim.export_sim(tampered, a)
    assert written(tmp_path) == []


def test_export_rejects_approval_of_other_proposal(tmp_path):
    p1 = sim.simulate_shift(job(), "C00004", "Y", 10)
    p2 = sim.simulate_shift(job(), "C00004", "Y", 20)
    with pytest.raises(sim.SimError):
        sim.export_sim(p2, sim.approve(p1, "テスト担当"))
    assert written(tmp_path) == []


def test_export_rejects_non_synthetic_job(tmp_path):
    """samples/dummy のどれとも一致しないバイト列（実機のジョブかもしれない）は出力しない"""
    data = raw().replace(b"DEMO_PLACE", b"OTHER_JOB")
    p = sim.simulate_shift(jbi.parse_bytes(data), "C00004", "Y", 10)
    with pytest.raises(sim.SimError) as e:
        sim.export_sim(p, sim.approve(p, "テスト担当"))
    assert "シミュレーションモードでは、合成ジョブ以外は出力しません" in str(e.value)
    assert written(tmp_path) == []


def test_export_rejects_crlf_copy_of_dummy(tmp_path):
    """改行だけ変えた写しも、バイト単位で一致しないので出力しない"""
    data = raw().replace(b"\n", b"\r\n")
    p = sim.simulate_shift(jbi.parse_bytes(data), "C00004", "Y", 10)
    with pytest.raises(sim.SimError):
        sim.export_sim(p, sim.approve(p, "テスト担当"))


FIXED = sim.datetime(2026, 11, 7, 12, 15, 30, tzinfo=sim.JST)


def test_export_does_not_overwrite(tmp_path, monkeypatch):
    """N-1（11/7）：同じ名前（同じ秒）のファイルがあっても上書きしない。番号を付けた新しいファイルに書く"""
    monkeypatch.setattr(sim, "clock", lambda: FIXED)
    (tmp_path / "sim").mkdir()
    existing = tmp_path / "sim" / "DEMO_PLACE_SIM_20261107_121530.JBI.txt"
    existing.write_bytes(b"keep me")
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    out = sim.export_sim(p, sim.approve(p, "テスト担当"))
    assert existing.read_bytes() == b"keep me"
    assert out.name == "DEMO_PLACE_SIM_20261107_121530_02.JBI.txt"
    assert b"//NAME DEMO_PLACE_SIM\n" in out.read_bytes() or b"//NAME DEMO_PLACE_SIM\r\n" in out.read_bytes()


def test_export_refuses_when_all_numbers_are_used(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "clock", lambda: FIXED)
    d = tmp_path / "sim"
    d.mkdir()
    for n in range(1, sim.MAX_SAME_NAME + 1):
        sim.output_path_for(d, "DEMO_PLACE_SIM", n, "20261107_121530").write_bytes(b"keep")
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    with pytest.raises(sim.SimError):
        sim.export_sim(p, sim.approve(p, "テスト担当"))
    assert all(f.read_bytes() == b"keep" for f in d.iterdir())


def test_export_rejects_extra_line_change(tmp_path):
    """承認した位置の1行以外も変わっている候補は書かない（ハッシュは合っていても）"""
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    extra = p.new_bytes.replace(b"MOVL C00005 V=80.0", b"MOVL C00005 V=90.0")
    bad = dataclasses.replace(p, new_bytes=extra)
    with pytest.raises(sim.SimError):
        sim.export_sim(bad, sim.approve(bad, "テスト担当"))
    assert written(tmp_path) == []


def test_export_removes_file_when_read_back_differs(tmp_path, monkeypatch):
    p = sim.simulate_shift(job(), "C00004", "Y", 10)
    a = sim.approve(p, "テスト担当")
    monkeypatch.setattr(sim, "_read_back", lambda path: b"broken")
    with pytest.raises(sim.SimError):
        sim.export_sim(p, a)
    assert written(tmp_path) == []


@pytest.mark.real_out_dir
def test_default_out_dir_is_ignored_by_git():
    assert sim.DEFAULT_OUT_DIR.parts[-2:] == ("output", "sim")
