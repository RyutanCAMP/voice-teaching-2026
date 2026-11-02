"""PR #1 修正の QA 3回目の条件（P2-1・P2-3・P3）と、1回の上限 20mm（仕様v2.1）の回帰テスト。

- P2-1：許可リストが先頭の語しか見ていない（END X・NOP X・TIMER T=I000 などがすり抜ける）
- P2-3：Unicode の数字（全角など）を数字として受け付ける
- P3：END が無いジョブが通る
- 位置の変更の上限：1回・累積とも 20mm（仕様v2.1 のデモ案の値。安全の保証ではない）
どれも「分からないものは止める」側に直す。読むこと（表示）は続ける。
"""
from pathlib import Path

import pytest

from voice_teaching import jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def base() -> str:
    return DUMMY.read_bytes().decode("ascii")


def test_base_job_has_no_problems_and_can_be_changed():
    job = jbi.parse(base())
    assert job.problems == []
    assert job.plan_shift("C00004", "Z", 5.0).after_value == 125.0


# ---- P2-1：命令は行全体の形で判定する ----
@pytest.mark.parametrize("old,new", [
    ("\nEND", "\nEND X"),
    ("\nEND", "\nEND 1"),
    ("NOP", "NOP X"),
    ("TIMER T=3.00", "TIMER T=I000"),
    ("TIMER T=3.00", "TIMER T=3.00 X"),
    ("TIMER T=3.00", "TIMER"),
    ("DOUT OT#(1) ON", "DOUT OT#(1) 1"),
    ("DOUT OT#(1) ON", "DOUT OG#(1) ON"),
    ("DOUT OT#(1) ON", "DOUT OT#(1) ON X"),
])
def test_known_command_with_unexpected_arguments_is_a_problem(old, new):
    text = base()
    assert old in text
    job = jbi.parse(text.replace(old, new, 1))
    assert job.problems, f"{new} が problems になりません"
    with pytest.raises(jbi.JbiError, match="読めない所"):
        job.plan_shift("C00004", "Z", 5.0)
    assert len(job.positions) == 6  # 読むのは続ける


def test_instructions_after_end_with_argument_are_problems():
    """END X の後ろに命令があっても、END の後ろとして止める"""
    text = base().replace("\nEND", "\nEND X\nMOVL C00004 V=80.0", 1)
    job = jbi.parse(text)
    assert any("END" in p for p in job.problems)
    assert any("END より後ろ" in p for p in job.problems)


# ---- P2-3：数字は ASCII の 0〜9 だけ ----
@pytest.mark.parametrize("old,new", [
    ("C00004=450.000", "C00004=４５０.000"),                    # 位置の値（全角）
    ("C00004=450.000", "C00004=٤٥٠.000"),                       # 位置の値（アラビア・インド数字）
    ("C00004=450.000", "C０００04=450.000"),                    # 位置の名前
    ("///NPOS 6,0,0,0,0,0", "///NPOS ６,0,0,0,0,0"),            # NPOS
    ("MOVL C00004 V=80.0", "MOVL C00004 V=８0.0"),              # 速度
    ("MOVL C00004 V=80.0", "MOVL C０００04 V=80.0"),            # 命令の位置参照
    ("TIMER T=3.00", "TIMER T=３.00"),                          # 命令の引数
])
def test_non_ascii_digits_are_not_accepted_as_numbers(old, new):
    text = base()
    assert old in text
    job = jbi.parse(text.replace(old, new, 1))
    assert job.problems, f"{new} が problems になりません"
    with pytest.raises(jbi.JbiError):
        job.plan_shift("C00004", "Z", 5.0)


def test_non_ascii_digits_in_comment_are_allowed():
    """コメント（' の行）の中は命令ではないので止めない"""
    text = base().replace("NOP\n", "NOP\n'全角の数字 １２３\n", 1)
    job = jbi.parse(text)
    assert job.problems == []


# ---- P3：END が無いジョブは止める ----
def test_job_without_end_is_a_problem():
    text = base()
    assert text.rstrip("\r\n").endswith("END")
    no_end = text.rstrip("\r\n")[: -len("END")]
    job = jbi.parse(no_end)
    assert any("END" in p for p in job.problems)
    with pytest.raises(jbi.JbiError, match="読めない所"):
        job.plan_shift("C00004", "Z", 5.0)


def test_job_without_inst_section_is_a_problem():
    text = base()
    job = jbi.parse(text[: text.index("//INST")])
    assert any("END" in p for p in job.problems)


# ---- 1回・累積の上限 20mm（仕様v2.1） ----
def test_shift_limit_is_20mm():
    assert jbi.DEFAULT_MAX_SHIFT_MM == 20.0


def test_shift_of_exactly_20mm_is_allowed_and_over_is_rejected():
    job = jbi.parse(base())
    assert job.plan_shift("C00004", "Z", 20.0).after_value == 140.0
    assert job.plan_shift("C00004", "Z", -20.0).after_value == 100.0
    with pytest.raises(jbi.JbiError, match="上限 20mm"):
        job.plan_shift("C00004", "Z", 20.001)
    with pytest.raises(jbi.JbiError, match="上限 20mm"):
        job.plan_shift("C00004", "Z", 21.0)


def test_cumulative_limit_is_20mm():
    first = jbi.parse(base()).plan_shift("C00004", "Z", 15.0)
    with pytest.raises(jbi.JbiError, match="累積"):
        first.new_job.plan_shift("C00004", "Z", 10.0)
    assert first.new_job.plan_shift("C00004", "Z", 5.0).cumulative_delta == pytest.approx(20.0)


def test_larger_limit_argument_cannot_raise_20mm():
    with pytest.raises(jbi.JbiError, match="上限"):
        jbi.parse(base()).plan_shift("C00004", "Z", 25.0, max_abs_delta_mm=100.0)
