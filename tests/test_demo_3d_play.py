"""再生の表示（feat/demo-3d）：図の区間の線に、何回目の区間か（cycle）を付けるのはサーバー（プログラム）だけ。

- 読み込んだジョブの区間が数えられるもの（続けてそのまま書かれた区間）がちょうど1つのときだけ、view の線に cycle を付ける。
  区間の外の線は None。区間が無い・2つ以上のときは cycle を付けない（画面は「何回目」を出さない）。
- 回数の変更の候補では、変更前は2回目まで・変更後は3回目まで（足した回）を、候補の線（before_segments・after_segments）で数える。
- 画面の文字（いま：24行目 …）は highlight.js の nowPlaying が、この線のデータだけから作る（tests/repeat.test.mjs）。
- 検査を通ったことは、物理的な安全の保証ではない。再生は点を命令の順に結んだ目安で、ロボットの動きではない。
"""
from pathlib import Path

from voice_teaching import claude_interpreter as ci
from voice_teaching.web import api
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO = (ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI").read_bytes()
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SAY = "C00001からC00003の作業を2回から3回にして"


def test_view_segments_have_cycle_when_one_block():
    v = api.job_view(DEMO)
    assert len(v["repeat_blocks"]) == 1
    got = [(s["line_no"], s["to"], s["cycle"]) for s in v["segments"]]
    # 20行目 MOVJ C00000 は始点なので線にならない。21〜23行目が1回目、24〜26行目が2回目、27行目 MOVJ は区間の外
    assert got == [(21, "C00001", 1), (22, "C00002", 1), (23, "C00003", 1),
                   (24, "C00001", 2), (25, "C00002", 2), (26, "C00003", 2), (27, "C00004", None)]


def test_view_segments_without_block_have_no_cycle():
    v = api.job_view(DEMO_C)
    assert v["repeat_blocks"] == []
    assert v["segments"] and all("cycle" not in s for s in v["segments"])


def test_candidate_segments_count_two_before_and_three_after():
    f = WebFlow(interpreter=ci.MockInterpreter())
    sid = f.load(DEMO)["session_id"]
    assert f.select(sid, "C00001")["status"] == "ok"
    r = f.hear(sid, SAY)
    turn = r["state"]["repeat_range"]["turn"]
    r = f.repeat_choose(sid, "yes", turn)
    c = r["state"]["candidate"]
    assert c["kind"] == "repeat" and not c["blocked_reason"]
    before = [s["cycle"] for s in c["before_segments"] if s["cycle"] is not None]
    after = [s["cycle"] for s in c["after_segments"] if s["cycle"] is not None]
    assert max(before) == 2 and before.count(1) == 3 and before.count(2) == 3
    assert max(after) == 3 and after.count(3) == 3
    # 足した回（3回目）の線は、元からある線と同じ（同じ始点・終点・命令の行）。新しい線は無い
    old = {(s["from"], s["to"], s["text"]) for s in c["before_segments"]}
    assert all((s["from"], s["to"], s["text"]) in old for s in c["after_segments"] if s["cycle"] == 3)


def test_server_serves_player_js():
    from voice_teaching.web import server
    src = (ROOT / "voice_teaching" / "web" / "server.py").read_text(encoding="utf-8")
    assert '"/static/player.js": ("player.js"' in src
    assert (ROOT / "voice_teaching" / "web" / "static" / "player.js").is_file()
    assert server is not None
