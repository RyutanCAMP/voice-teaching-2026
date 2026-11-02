"""シミュレーション表示（読むだけ＋変更案のプレビュー）の受入テスト。実装より先に書いた。

- JSON を作る関数（voice_teaching.web.api）はサーバーを立てずに呼ぶ。
- HTTP のテストは 127.0.0.1 のポート0（空いているポート）で立てる。
- ダミーは samples/dummy/DEMO_PLACE.JBI（架空のジョブ）。
"""
import base64
import http.client
import json
import os
import threading
from pathlib import Path

import pytest

from voice_teaching import jbi
from voice_teaching.web import api, server

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI"
STATIC = ROOT / "voice_teaching" / "web" / "static"
ASSERTIVE = ["安全です", "当たります", "干渉します", "当たりません", "干渉しません", "安全な"]


def demo() -> bytes:
    return DUMMY.read_bytes()


def pos(view, name):
    return next(p for p in view["positions"] if p["name"] == name)


# ---- 1. 解析結果（読むだけ） ----
def test_view_lists_positions_with_file_line_format_and_users():
    view = api.job_view(demo())
    assert view["ok"] is True
    assert view["name"] == "DEMO_PLACE"
    assert view["problems"] == []
    assert [p["name"] for p in view["positions"]] == [f"C0000{i}" for i in range(6)]
    c4 = pos(view, "C00004")
    # ファイルの行そのまま・1から数えた行番号
    assert c4["line"] == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert c4["line_no"] == 13
    assert c4["format"] == "ROBOT"
    assert c4["robot_coordinates"] is True
    assert c4["xyz"] == [450.0, 190.0, 120.0]
    assert [u["line_no"] for u in c4["users"]] == [26]
    assert c4["users"][0]["text"] == "MOVL C00004 V=80.0"
    assert c4["shift_check"]["ok"] is True
    # 共用の位置は、検査の理由をそのまま出す
    c0 = pos(view, "C00000")
    assert [u["line_no"] for u in c0["users"]] == [20, 31]
    assert c0["shift_check"]["ok"] is False
    assert "共用" in c0["shift_check"]["reason"]


def test_view_lists_instructions_with_speed_check():
    view = api.job_view(demo())
    ins = {i["line_no"]: i for i in view["instructions"]}
    assert ins[26]["text"] == "MOVL C00004 V=80.0"
    assert ins[26]["position"] == "C00004"
    assert ins[26]["is_move"] is True
    assert ins[26]["speed_raw"] == "80.0"
    assert ins[26]["speed_check"]["ok"] is True
    assert ins[19]["text"] == "NOP"
    assert ins[19]["is_move"] is False
    assert ins[19]["speed_check"] is None


def test_view_shows_problems_and_still_lists_positions():
    data = demo().replace(b"///GROUP1 RB1", b"///GROUP1 RB1,ST1")
    view = api.job_view(data)
    assert view["ok"] is True
    assert view["problems"] and "GROUP" in view["problems"][0]
    assert len(view["positions"]) == 6
    assert all(p["shift_check"]["ok"] is False for p in view["positions"])


def test_view_refuses_unreadable_bytes_with_reason():
    view = api.job_view(b"\xff\xfe\x80\x81")
    assert view["ok"] is False
    assert view["error"]


def test_view_of_pulse_positions_has_no_coordinates_and_no_segments():
    data = demo().replace(b"///POSTYPE ROBOT\n///RECTAN\n", b"///POSTYPE PULSE\n///PULSE\n")
    view = api.job_view(data)
    assert view["ok"] is True
    c4 = pos(view, "C00004")
    assert c4["format"] == "PULSE"
    assert c4["robot_coordinates"] is False
    assert c4["xyz"] is None  # 無理に変換しない
    assert view["segments"] == []
    assert "ロボット座標でないため描けません" in view["figure_message"]


# ---- 2. 図の線（命令の順に結ぶ。MOVL（PL・CRなし）だけ実線） ----
def test_segments_follow_instruction_order_and_mark_plain_movl_solid():
    view = api.job_view(demo())
    segs = view["segments"]
    assert [(s["from"], s["to"]) for s in segs] == [
        ("C00000", "C00001"), ("C00001", "C00002"), ("C00002", "C00003"),
        ("C00003", "C00004"), ("C00004", "C00005"), ("C00005", "C00000")]
    assert all(s["solid"] for s in segs)
    assert segs[3]["line_no"] == 26  # その区間へ入る命令の行


def test_segments_with_pl_or_other_moves_are_dashed():
    data = (demo().replace(b"MOVL C00001 V=80.0", b"MOVL C00001 V=80.0 PL=0")
            .replace(b"MOVL C00003 V=250.0", b"MOVJ C00003 VJ=50.00"))
    segs = {s["to"]: s for s in api.job_view(data)["segments"]}
    assert segs["C00001"]["solid"] is False
    assert segs["C00003"]["solid"] is False
    assert segs["C00004"]["solid"] is True


def test_segments_break_at_moves_without_drawable_position():
    data = demo().replace(b"MOVL C00003 V=250.0", b"MOVL P001 V=250.0")
    segs = [(s["from"], s["to"]) for s in api.job_view(data)["segments"]]
    assert ("C00002", "C00004") not in segs  # 分からない移動を飛ばして結ばない
    assert ("C00002", "C00003") not in segs


# ---- 3. 変更のプレビュー（plan_shift / plan_speed） ----
def test_preview_shift_returns_proposal_and_after_coordinates():
    res = api.preview_shift(demo(), "C00004", "Z", 10)
    assert res["ok"] is True
    p = res["proposal"]
    assert p["kind"] == "shift"
    assert p["before_line"] == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert p["after_line"] == "C00004=450.000,190.000,130.000,180.00,0.00,0.00"
    assert p["before_value"] == 120.0 and p["after_value"] == 130.0
    assert p["actual_delta"] == pytest.approx(10.0)
    assert p["line_no"] == 13
    assert len(p["affected"]) == 2
    assert jbi.PATH_WARNING in p["warnings"]
    after = {q["name"]: q["xyz"] for q in res["after_positions"]}
    assert after["C00004"] == [450.0, 190.0, 130.0]
    assert after["C00003"] == [450.0, 190.0, 250.0]
    assert 26 in res["highlight_lines"] and 30 in res["highlight_lines"]


def test_preview_does_not_return_the_new_job():
    res = api.preview_shift(demo(), "C00004", "Z", 10)
    text = json.dumps(res, ensure_ascii=False)
    assert "new_job" not in text
    assert "//INST" not in text  # ジョブ全体の文字列は返さない（書き出さない）


def test_preview_shift_rounding_is_reported():
    res = api.preview_shift(demo(), "C00004", "Z", 0.0004)
    assert res["ok"] is False  # 丸めると変わらない
    res = api.preview_shift(demo(), "C00004", "Z", 1.2345)
    assert res["ok"] is True
    assert res["proposal"]["after_value"] == pytest.approx(121.235) or \
        res["proposal"]["after_value"] == pytest.approx(121.234)
    assert any("丸め" in w for w in res["proposal"]["warnings"])


@pytest.mark.parametrize("position,axis,delta,word", [
    ("C00000", "X", 5, "共用"),
    ("C00004", "Z", 60, "上限"),
    ("C00004", "Z", "10", "数値"),
    ("C00004", "Z", True, "数値"),
    ("C00004", "Z", None, "数値"),
    ("C00004", "Rx", 5, "軸"),
    ("C00099", "X", 5, "C00099"),
])
def test_preview_shift_refused_returns_reason_and_no_proposal(position, axis, delta, word):
    res = api.preview_shift(demo(), position, axis, delta)
    assert res["ok"] is False
    assert word in res["reason"]
    assert "proposal" not in res and "after_positions" not in res


def test_preview_shift_refused_when_job_has_problems():
    data = demo().replace(b"///GROUP1 RB1", b"///GROUP1 RB1,ST1")
    res = api.preview_shift(data, "C00004", "Z", 5)
    assert res["ok"] is False
    assert "読めない所" in res["reason"]


def test_preview_speed_returns_proposal():
    res = api.preview_speed(demo(), 26, 100)
    assert res["ok"] is True
    p = res["proposal"]
    assert p["kind"] == "speed"
    assert p["before_line"] == "MOVL C00004 V=80.0"
    assert p["after_line"] == "MOVL C00004 V=100.0"
    assert p["after_value"] == 100.0
    assert jbi.SPEED_UNIT_WARNING in p["warnings"]
    assert res["highlight_lines"] == [26]


@pytest.mark.parametrize("line_no,speed,word", [
    (19, 100, "MOVL"),        # NOP
    (26, 0, "正の"),
    (26, "100", "数値"),
    (26, True, "数値"),
    (999, 100, "命令"),
    ("26", 100, "行"),
])
def test_preview_speed_refused_returns_reason(line_no, speed, word):
    res = api.preview_speed(demo(), line_no, speed)
    assert res["ok"] is False
    assert word in res["reason"]
    assert "proposal" not in res


def test_preview_of_unreadable_bytes_is_refused():
    res = api.preview_shift(b"\xff\xfe\x80", "C00004", "Z", 5)
    assert res["ok"] is False and res["reason"]


def test_view_states_provisional_limit_only_as_unconfirmed():
    view = api.job_view(demo())
    assert view["max_shift_mm"] == jbi.DEFAULT_MAX_SHIFT_MM
    assert "仮の上限（確認待ち）" in view["max_shift_label"]


# ---- 4. 画面の文言（断定しない・読むだけ・外から読まない） ----
def static_text():
    return {p.name: p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.is_file()}


def test_static_files_have_required_notices_and_no_assertive_words():
    files = static_text()
    assert {"index.html", "app.js", "style.css"} <= set(files)
    html = files["index.html"]
    # MVP：元のジョブは変えず、承認した候補だけをシミュレーション用に出力する（実機用の出力・転送・起動は無い）
    assert "元のジョブは変えません" in html
    assert "実機用の出力・転送・ロボットの起動はしません" in html
    assert ("この図で離れて見えても、当たらないとは限りません。腕やツールの根元は描いていません。"
            "参考表示であり、実際のロボットの動きではありません。") in html
    api_text = json.dumps(api.job_view(demo()), ensure_ascii=False) + api.FIGURE_NOTE
    for text in list(files.values()) + [api_text]:
        for w in ASSERTIVE:
            assert w not in text


def test_static_files_load_nothing_from_outside():
    for name, text in static_text().items():
        text = text.replace("http://www.w3.org/2000/svg", "")  # SVG の名前空間（通信ではない）
        assert "http://" not in text and "https://" not in text, name
        assert "//cdn" not in text, name


def test_approve_and_export_buttons_start_disabled():
    """承認・出力のボタンは、最初は使えない（候補ができて承認するまで）。保存・転送・ダウンロードのボタンは無い。"""
    import re
    html = static_text()["index.html"]
    js = static_text()["app.js"]
    buttons = re.findall(r"<button([^>]*)>(.*?)</button>", html, re.S)
    for attrs, text in buttons:
        if any(w in text for w in ["書き出", "出力", "保存", "転送", "承認する", "ダウンロード", "送信"]):
            assert "disabled" in attrs, text
        assert not any(w in text for w in ["保存", "転送", "ダウンロード"]), text
    assert re.search(r'<button[^>]*id="approve-button"[^>]*disabled', html)
    assert re.search(r'<button[^>]*id="export-button"[^>]*disabled', html)
    assert "download" not in js.lower()


def test_simulation_mode_is_shown():
    html = static_text()["index.html"]
    assert 'id="mode-banner"' in html
    assert "シミュレーションモード" in html
    assert api.job_view(demo())["mode"] == "simulation"


def test_preview_json_separates_warnings_and_blocked_reason():
    ok = api.preview_shift(demo(), "C00004", "Z", 10)
    assert ok["mode"] == "simulation"
    assert ok["blocked_reason"] is None
    assert ok["warnings"] == ok["proposal"]["warnings"]
    ng = api.preview_shift(demo(), "C00000", "X", 5)
    assert ng["mode"] == "simulation"
    assert "共用" in ng["blocked_reason"]
    assert ng["blocked_reason"] == ng["reason"]
    assert ng["warnings"] == []
    sp = api.preview_speed(demo(), 19, 100)
    assert sp["blocked_reason"] and sp["warnings"] == []
    assert api.preview_speed(demo(), 26, 100)["blocked_reason"] is None


def test_voice_panel_placeholder_exists():
    html = static_text()["index.html"]
    assert 'id="instruction-panel"' in html
    assert 'id="instruction-slot"' in html


# ---- 5. サーバー（127.0.0.1 だけ・ディスクに保存しない） ----
@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    srv = server.make_server(port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def test_server_listens_only_on_loopback(running_server):
    assert running_server.server_address[0] == "127.0.0.1"
    with pytest.raises(ValueError):
        server.make_server(host="0.0.0.0", port=0)


def test_server_serves_page_and_parses_without_writing_files(running_server, tmp_path):
    r, body = request(running_server, "GET", "/")
    assert r.status == 200
    assert "元のジョブは変えません" in body.decode("utf-8")
    assert "default-src 'self'" in r.getheader("Content-Security-Policy")

    r, body = request(running_server, "POST", "/api/parse", body=demo(),
                      headers={"Content-Type": "application/octet-stream"})
    assert r.status == 200
    view = json.loads(body)
    assert view["ok"] is True and view["name"] == "DEMO_PLACE"

    payload = json.dumps({"job_base64": base64.b64encode(demo()).decode("ascii"),
                          "position": "C00004", "axis": "Z", "delta_mm": 10})
    r, body = request(running_server, "POST", "/api/preview/shift", body=payload,
                      headers={"Content-Type": "application/json"})
    assert r.status == 200
    assert json.loads(body)["proposal"]["after_value"] == 130.0

    payload = json.dumps({"job_base64": base64.b64encode(demo()).decode("ascii"),
                          "line_no": 26, "speed": 100})
    r, body = request(running_server, "POST", "/api/preview/speed", body=payload,
                      headers={"Content-Type": "application/json"})
    assert json.loads(body)["proposal"]["after_value"] == 100.0

    assert os.listdir(tmp_path) == []  # 受け取ったジョブをディスクに置かない


def test_server_rejects_foreign_host_and_origin(running_server):
    r, _ = request(running_server, "GET", "/", headers={"Host": "evil.example:80"})
    assert r.status == 403
    r, _ = request(running_server, "POST", "/api/parse", body=demo(),
                   headers={"Origin": "http://evil.example"})
    assert r.status == 403


def test_server_has_no_write_or_transfer_routes(running_server):
    for path in ["/api/save", "/api/transfer", "/api/write", "/api/start", "/api/send"]:
        r, _ = request(running_server, "POST", path, body=b"{}")
        assert r.status == 404
    # /api/export はあるが、読み込み・承認の無い要求は断る（何も書かない）
    r, body = request(running_server, "POST", "/api/export", body=b"{}")
    assert r.status == 200 and json.loads(body)["ok"] is False
    r, _ = request(running_server, "GET", "/static/../../jbi.py")
    assert r.status == 404


def test_server_rejects_too_large_body(running_server):
    # 本文を読む前に Content-Length で断る（大きな本文は実際には送らない）
    r, _ = request(running_server, "POST", "/api/parse", body=b"x",
                   headers={"Content-Length": str(server.MAX_BODY + 1)})
    assert r.status == 413
