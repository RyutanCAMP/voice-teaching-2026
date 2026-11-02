"""合成（架空）のテスト用ジョブ samples/dummy/PAT_*.JBI の受入テスト。

一覧と期待結果は samples/dummy/PATTERNS.md。ファイルは tools/make_dummy_patterns.py で作る。
期待結果は仕様v2.1 6・7章と CURRENT_DECISIONS に合わせる。今の jbi.py が正本と違うところは
xfail(strict=True) にしてある（直ると XPASS で失敗するので、そのとき xfail を外す）。
"""
import importlib.util
from pathlib import Path

import pytest

from voice_teaching import jbi

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy"

PL0_FIX = "PL=0 は正本（仕様v2.1 6・7章、CURRENT_DECISIONS 4章）どおり通す予定。jbi.py の修正待ち"
UNKNOWN_FIX = "INST部の知らない命令は problems に入れて止める予定。jbi.py の修正待ち"
LVARS_FIX = "正しい形の ///LVARS（数字をカンマで区切った行）は problems にしない予定。jbi.py の修正待ち"


def _make_module():
    spec = importlib.util.spec_from_file_location("make_dummy_patterns", ROOT / "tools" / "make_dummy_patterns.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name: str) -> jbi.Job:
    return jbi.load(DUMMY / name)


def _line_index(job: jbi.Job, text: str) -> int:
    found = [i for i, line in enumerate(job.lines) if line.rstrip("\r\n") == text]
    assert len(found) == 1, f"{text} が1行だけ見つかりません"
    return found[0]


# 読み込めて、書き戻すと同じバイト列になるファイル
LOADABLE = [
    "PAT_MOVL_OK.JBI", "PAT_PL0.JBI", "PAT_PL_GT0.JBI", "PAT_CR.JBI", "PAT_MOVJ.JBI", "PAT_MOVC.JBI",
    "PAT_POSTYPE_ROBOT.JBI", "PAT_POSTYPE_PULSE.JBI", "PAT_POSTYPE_MIXED.JBI", "PAT_SHARED_POS.JBI",
    "PAT_SFTON.JBI", "PAT_SJIS_COMMENT.JBI", "PAT_SJIS_COMMENT_CRLF.JBI", "PAT_UNKNOWN_INST.JBI",
    "PAT_BAD_POS_NONNUM.JBI", "PAT_BAD_POS_SHORT.JBI", "PAT_BAD_POS_DUP.JBI", "PAT_LVARS.JBI",
    "PAT_LVARS_BAD.JBI",
]
# 読み込みを断るファイル
NOT_LOADABLE = ["PAT_SJIS_NOROUNDTRIP.JBI"]


def _xf(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


# ---- 作り方のスクリプトとファイルが一致する ----
def test_generator_is_reproducible_and_matches_files():
    mod = _make_module()
    first, second = mod.build(), mod.build()
    assert first == second
    on_disk = {p.name for p in DUMMY.glob("PAT_*.JBI")}
    assert on_disk == set(first) == set(LOADABLE + NOT_LOADABLE)
    for name, data in first.items():
        assert (DUMMY / name).read_bytes() == data, f"{name} が tools/make_dummy_patterns.py の出力と違います"


# ---- 読み込み → 書き出しのバイト一致 ----
@pytest.mark.parametrize("name", LOADABLE)
def test_roundtrip_bytes(name):
    data = (DUMMY / name).read_bytes()
    assert jbi.parse_bytes(data).render_bytes() == data


@pytest.mark.parametrize("name", NOT_LOADABLE)
def test_refuses_file_that_changes_when_written_back(name):
    with pytest.raises(jbi.JbiError, match="書き戻すと中身が変わってしまう"):
        _load(name)


# ---- 文字コード・改行 ----
@pytest.mark.parametrize("name,eol", [("PAT_SJIS_COMMENT.JBI", b"\n"), ("PAT_SJIS_COMMENT_CRLF.JBI", b"\r\n")])
def test_sjis_files_are_cp932_with_0x5c_second_bytes(name, eol):
    data = (DUMMY / name).read_bytes()
    for ch in "ソ表能":
        enc = ch.encode("cp932")
        assert enc[1] == 0x5C and enc in data
    assert "ﾊﾝﾄﾞ".encode("cp932") in data  # 半角カナ
    assert "　".encode("cp932") in data  # 全角空白
    assert b"///COMM " + "合成テスト".encode("cp932") in data
    if eol == b"\r\n":
        assert data.count(b"\r\n") == data.count(b"\n")
    else:
        assert b"\r" not in data
    assert _load(name).encoding == "cp932"


@pytest.mark.parametrize("name", ["PAT_SJIS_COMMENT.JBI", "PAT_SJIS_COMMENT_CRLF.JBI"])
def test_sjis_change_keeps_other_bytes(name):
    """コメント・///COMM の日本語は problems にしない。変えるのは1行だけで、他のバイトはそのまま"""
    job = _load(name)
    assert job.problems == []
    new = job.plan_shift("C00004", "Z", 5.0).new_job
    old_lines = [line.encode("cp932") for line in job.lines]
    new_lines = [line.encode("cp932") for line in new.lines]
    diff = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(old_lines) == len(new_lines) and len(diff) == 1
    assert new_lines[diff[0]].endswith(b"\r\n" if "CRLF" in name else b"\n")


# ---- problems の有無 ----
PROBLEMS = [
    ("PAT_MOVL_OK.JBI", False, None),
    ("PAT_SJIS_COMMENT.JBI", False, None),
    ("PAT_SJIS_COMMENT_CRLF.JBI", False, None),
    ("PAT_BAD_POS_NONNUM.JBI", True, None),
    ("PAT_BAD_POS_SHORT.JBI", True, None),
    ("PAT_BAD_POS_DUP.JBI", True, None),
    ("PAT_LVARS_BAD.JBI", True, None),
    ("PAT_UNKNOWN_INST.JBI", True, UNKNOWN_FIX),
    ("PAT_LVARS.JBI", False, LVARS_FIX),
]


@pytest.mark.parametrize("name,has_problems", [
    pytest.param(n, h, marks=_xf(r)) if r else (n, h) for n, h, r in PROBLEMS])
def test_problems(name, has_problems):
    assert bool(_load(name).problems) is has_problems


# ---- 期待結果（plan_shift / plan_speed が通るか止まるか） ----
# (ファイル, 操作, 対象, 期待, 理由に含まれる語, xfail の理由)
#   shift の対象は位置名（Z を +5mm）。speed の対象は命令の行（速度を 100.0 にする）
CASES = [
    ("PAT_MOVL_OK.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_MOVL_OK.JBI", "shift", "C00001", "ok", None, None),
    ("PAT_MOVL_OK.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_PL0.JBI", "shift", "C00004", "ok", None, PL0_FIX),
    ("PAT_PL0.JBI", "shift", "C00003", "ok", None, PL0_FIX),
    ("PAT_PL0.JBI", "speed", "MOVL C00004 V=80.0 PL=0", "ok", None, PL0_FIX),
    ("PAT_PL0.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, PL0_FIX),

    ("PAT_PL_GT0.JBI", "shift", "C00002", "stop", "PL=3", None),
    ("PAT_PL_GT0.JBI", "shift", "C00001", "stop", "PL=3", None),
    ("PAT_PL_GT0.JBI", "shift", "C00003", "ok", None, None),
    ("PAT_PL_GT0.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_PL_GT0.JBI", "speed", "MOVL C00002 V=80.0 PL=3", "stop", "PL", None),
    ("PAT_PL_GT0.JBI", "speed", "MOVL C00001 V=80.0", "stop", "PL", None),
    ("PAT_PL_GT0.JBI", "speed", "MOVL C00003 V=250.0", "stop", "PL", None),
    ("PAT_PL_GT0.JBI", "speed", "MOVL C00004 V=80.0", "ok", None, None),

    ("PAT_CR.JBI", "shift", "C00002", "stop", "CR", None),
    ("PAT_CR.JBI", "shift", "C00001", "stop", "CR", None),
    ("PAT_CR.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_CR.JBI", "speed", "MOVL C00002 V=80.0 CR=10.0", "stop", "CR", None),
    ("PAT_CR.JBI", "speed", "MOVL C00001 V=80.0", "stop", "CR", None),
    ("PAT_CR.JBI", "speed", "MOVL C00004 V=80.0", "ok", None, None),

    ("PAT_MOVJ.JBI", "shift", "C00000", "stop", "MOVL", None),
    ("PAT_MOVJ.JBI", "shift", "C00005", "stop", "出る移動", None),
    ("PAT_MOVJ.JBI", "shift", "C00001", "ok", None, None),
    ("PAT_MOVJ.JBI", "speed", "MOVJ C00000 VJ=25.00", "stop", "MOVL", None),
    ("PAT_MOVJ.JBI", "speed", "MOVL C00001 V=80.0", "ok", None, None),

    ("PAT_MOVC.JBI", "shift", "C00003", "stop", "MOVL", None),
    ("PAT_MOVC.JBI", "shift", "C00001", "stop", "出る移動", None),
    ("PAT_MOVC.JBI", "shift", "C00005", "ok", None, None),
    ("PAT_MOVC.JBI", "speed", "MOVC C00003 V=250.0", "stop", "MOVL", None),

    ("PAT_POSTYPE_ROBOT.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_POSTYPE_ROBOT.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_POSTYPE_PULSE.JBI", "shift", "C00004", "stop", "ロボット座標", None),
    ("PAT_POSTYPE_PULSE.JBI", "shift", "C00001", "stop", "ロボット座標", None),
    ("PAT_POSTYPE_PULSE.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_POSTYPE_MIXED.JBI", "shift", "C00001", "ok", None, None),
    ("PAT_POSTYPE_MIXED.JBI", "shift", "C00003", "stop", "ロボット座標", None),
    ("PAT_POSTYPE_MIXED.JBI", "shift", "C00004", "stop", "ロボット座標", None),
    ("PAT_POSTYPE_MIXED.JBI", "shift", "C00005", "ok", None, None),

    ("PAT_SHARED_POS.JBI", "shift", "C00000", "stop", "共用", None),
    ("PAT_SHARED_POS.JBI", "shift", "C00002", "stop", "共用", None),
    ("PAT_SHARED_POS.JBI", "shift", "C00003", "stop", "共用", None),
    ("PAT_SHARED_POS.JBI", "shift", "C00001", "ok", None, None),
    ("PAT_SHARED_POS.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_SHARED_POS.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_SFTON.JBI", "shift", "C00004", "stop", "SFTON", None),
    ("PAT_SFTON.JBI", "shift", "C00001", "stop", "SFTON", None),
    ("PAT_SFTON.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_SJIS_COMMENT.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_SJIS_COMMENT.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),
    ("PAT_SJIS_COMMENT_CRLF.JBI", "shift", "C00004", "ok", None, None),
    ("PAT_SJIS_COMMENT_CRLF.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, None),

    ("PAT_UNKNOWN_INST.JBI", "shift", "C00004", "stop", None, UNKNOWN_FIX),
    ("PAT_UNKNOWN_INST.JBI", "speed", "MOVL C00003 V=250.0", "stop", None, UNKNOWN_FIX),

    ("PAT_BAD_POS_NONNUM.JBI", "shift", "C00004", "stop", "数値でない", None),
    ("PAT_BAD_POS_NONNUM.JBI", "speed", "MOVL C00003 V=250.0", "stop", "数値でない", None),
    ("PAT_BAD_POS_SHORT.JBI", "shift", "C00004", "stop", "3個未満", None),
    ("PAT_BAD_POS_SHORT.JBI", "speed", "MOVL C00003 V=250.0", "stop", "3個未満", None),
    ("PAT_BAD_POS_DUP.JBI", "shift", "C00004", "stop", "重複", None),
    ("PAT_BAD_POS_DUP.JBI", "speed", "MOVL C00003 V=250.0", "stop", "重複", None),

    ("PAT_LVARS.JBI", "shift", "C00004", "ok", None, LVARS_FIX),
    ("PAT_LVARS.JBI", "speed", "MOVL C00003 V=250.0", "ok", None, LVARS_FIX),
    ("PAT_LVARS_BAD.JBI", "shift", "C00004", "stop", "LVARS", None),
    ("PAT_LVARS_BAD.JBI", "speed", "MOVL C00003 V=250.0", "stop", "LVARS", None),
]


def _case_id(c):
    return f"{c[0][:-4]}-{c[1]}-{c[2].replace(' ', '_')}"


@pytest.mark.parametrize("name,op,target,expected,why", [
    pytest.param(*c[:5], id=_case_id(c), marks=_xf(c[5])) if c[5] else pytest.param(*c[:5], id=_case_id(c))
    for c in CASES])
def test_expected_result(name, op, target, expected, why):
    job = _load(name)
    before = job.render_bytes()

    def run():
        if op == "shift":
            return job.plan_shift(target, "Z", 5.0)
        return job.plan_speed(_line_index(job, target), 100.0)

    if expected == "ok":
        proposal = run()
        old = job.lines
        new = proposal.new_job.lines
        assert len(old) == len(new)
        assert sum(a != b for a, b in zip(old, new)) == 1  # 変わるのは1行だけ
    else:
        with pytest.raises(jbi.JbiError) as e:
            run()
        if why:
            assert why in str(e.value)
    assert job.render_bytes() == before  # 元のジョブは変わらない


def test_tool_number_is_reported_in_warnings():
    """ROBOT 形式でツール番号 1 の指定がある位置は、変更案の注意にツール番号を出す"""
    p = _load("PAT_POSTYPE_ROBOT.JBI").plan_shift("C00004", "Z", 5.0)
    assert any("ツール番号 1" in w for w in p.warnings)


def test_mixed_postype_is_judged_per_position():
    job = _load("PAT_POSTYPE_MIXED.JBI")
    assert not job.is_robot_coordinates()
    robot = {n for n, p in job.positions.items() if p.is_robot_coordinates()}
    assert robot == {"C00000", "C00001", "C00002", "C00005", "C00006"}
