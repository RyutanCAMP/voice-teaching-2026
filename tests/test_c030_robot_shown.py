"""受入テスト：CHECKER_030 S30-1（唐川さんの決定 2026-11-03 17:14）。実装より先に書いた。

上下の向きの表示に、ロボット名と確認した人・日時を、マウスを重ねなくても常に見せる。
- サーバーの state.updown に、確認済みのときは設定の robot（"robot"）、どの状態でもアプリの --robot の値（"app_robot"）を入れる
- 画面の文は highlight.js の updownDetail（tests/highlight.test.mjs）。置き場所は図の欄（音声のカードの 80文字の決まりの外。e2e）
- 設定があるときに --robot を必須にするかは、新しい安全の判断なので実装しない（報告に選択肢を書く）
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import updown
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
DEMO = (ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI").read_bytes()


def state(cfg, robot=updown.DEFAULT_ROBOT, data=DEMO_C):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg, robot=robot)
    return w.load(data)["state"]["updown"]


def test_confirmed_shows_robot_of_the_config_and_who(make_updown):
    u = state(make_updown())
    assert u["status"] == "confirmed" and u["robot"] == updown.DEFAULT_ROBOT and u["app_robot"] == updown.DEFAULT_ROBOT
    assert u["confirmed_by"] and u["confirmed_at"]


def test_confirmed_with_other_robot_name(make_updown):
    u = state(make_updown(robot="HC10DTP"), robot="HC10DTP")
    assert u["status"] == "confirmed" and u["robot"] == "HC10DTP" and u["app_robot"] == "HC10DTP"


@pytest.mark.parametrize("robot", ["HC20DT", "HC10DTP"])
def test_unconfirmed_shows_the_app_robot(robot, tmp_path):
    u = state(tmp_path / "none.json", robot=robot)
    assert u["status"] == "unconfirmed" and u["robot"] is None and u["app_robot"] == robot


def test_not_configured_shows_the_app_robot():
    u = state(None, robot="HC20DT")
    assert u["status"] == "unconfirmed" and u["app_robot"] == "HC20DT"


def test_mismatch_robot_shows_the_app_robot_not_the_config(make_updown):
    u = state(make_updown(robot="HC10DTP"), robot="HC20DT")
    assert u["status"] == "mismatch" and u["robot"] is None and u["app_robot"] == "HC20DT"
    assert u["confirmed_by"] is None


def test_mismatch_frame_shows_the_app_robot(make_updown):
    # DEMO_C_PLACE の座標系の設定で、座標系の違う別のジョブ（相対ジョブ）を読む
    rj = (ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI").read_bytes()
    u = state(make_updown(), data=rj)
    assert u["status"] == "mismatch" and u["app_robot"] == updown.DEFAULT_ROBOT and u["robot"] is None


def test_changed_shows_the_app_robot(make_updown):
    p = make_updown()
    p.write_bytes(p.read_bytes().replace(b'"evidence": "', b'"evidence": "x', 1))
    u = state(p)
    assert u["status"] == "changed" and u["app_robot"] == updown.DEFAULT_ROBOT and u["robot"] is None


def test_robot_is_not_part_of_the_token(make_updown):
    # 表示のための値を足しただけ。承認・出力で比べる値（token）は今どおり
    from voice_teaching import jbi
    st = updown.check(jbi.parse_bytes(DEMO_C), make_updown())
    assert st.token == (st.status, st.fingerprint, st.file_sha256) and st.robot == updown.DEFAULT_ROBOT
