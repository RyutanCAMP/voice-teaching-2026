"""読むだけの確認用の道具（実機のジョブの形式を確かめる・2Dの図を見る）の受入テスト。実装より先に書いた。"""
from pathlib import Path

import pytest

from voice_teaching import inspect_job, jbi

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_PLACE.JBI"


def test_report_lists_positions_users_and_verdicts():
    text = inspect_job.report(jbi.load(DUMMY))
    assert "DEMO_PLACE" in text
    assert "C00004=450.000,190.000,120.000" in text  # 位置はファイルの文字のまま出す
    # 位置ごとの形式・使っている行・試しの検査結果が出る
    assert "ROBOT" in text
    assert "共用" in text  # C00000 は2つのMOVLが使う
    assert "変更の検査を通る" in text  # C00004 は通る


def test_report_shows_problems_and_still_lists_positions():
    data = DUMMY.read_bytes().replace(b"///GROUP1 RB1", b"///GROUP1 RB1,ST1")
    text = inspect_job.report(jbi.parse_bytes(data))
    assert "GROUP" in text
    assert "C00004" in text


def test_html_has_labels_and_disclaimer_and_no_assertive_words():
    html = inspect_job.html(jbi.load(DUMMY), source_name="DEMO_PLACE.JBI")
    for name in ["C00000", "C00004", "C00005"]:
        assert name in html
    assert "<svg" in html
    assert "当たらないとは限りません" in html
    for word in ["安全です", "当たります", "干渉します"]:
        assert word not in html


def test_html_handles_non_robot_positions():
    text = DUMMY.read_text(encoding="ascii").replace("///POSTYPE ROBOT\n", "///POSTYPE PULSE\n").replace("///RECTAN\n", "")
    html = inspect_job.html(jbi.parse(text), source_name="x.JBI")
    assert "ロボット座標でない" in html


def test_main_is_read_only_and_writes_html_outside_repo_tracking(tmp_path):
    src = tmp_path / "SAMPLE.JBI"
    src.write_bytes(DUMMY.read_bytes())
    out = tmp_path / "out"
    rc = inspect_job.main([str(src), "--out", str(out)])
    assert rc == 0
    assert src.read_bytes() == DUMMY.read_bytes()  # 元のジョブは変えない
    files = sorted(p.name for p in out.iterdir())
    assert files == ["SAMPLE.html"]  # JBIは書き出さない


def test_main_reports_unreadable_file(tmp_path, capsys):
    src = tmp_path / "BAD.JBI"
    src.write_bytes(b"/JOB\n\x81\xff\n")
    assert inspect_job.main([str(src), "--out", str(tmp_path / "out")]) == 1
    assert "読めません" in capsys.readouterr().out
