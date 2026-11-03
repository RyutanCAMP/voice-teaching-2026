"""feat/ux-polish の受入テスト（UX 監査の直し・2026-11-03）。画面のファイルとサーバーを読んで確かめる。

ブラウザーでの動き（聞き返しの見出し・Chrome 以外の案内が1回・版の表示・使い方の小窓・サンプルで試す・
390px で横にはみ出さない・止まった候補の帯が見える・変わる区間だけオレンジ）は tools/e2e_browser.py で確かめる。
"""
import http.client
import json
import re
import threading
from pathlib import Path

import pytest

import voice_teaching
from voice_teaching.web import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"
SAMPLE = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
ASSERTIVE = ["安全です", "当たります", "当たりません", "干渉します", "干渉しません", "問題ありません", "大丈夫です", "安全な"]


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def js_part(start, end):
    j = text("app.js")
    return j[j.index(start):j.index(end)]


# ---- 版（1か所だけで決める） ----
def test_version_single_source():
    assert voice_teaching.__version__ == "1.00-dev"
    h = text("index.html")
    assert 'id="app-version"' in h and "1.00-dev" not in h  # 画面のファイルには書かない（サーバーが入れる）
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "1.00-dev（未確定）" in log and "外部" in log


# ---- 画面の文言 ----
def test_no_assertive_words_in_static_files():
    for p in STATIC.iterdir():
        if p.is_file():
            t = p.read_text(encoding="utf-8")
            for w in ASSERTIVE:
                assert w not in t, (p.name, w)


def test_figure_notes_are_one_paragraph_with_same_ids_and_text():
    h = text("index.html")
    m = re.search(r'<p class="fine figure-notes">(.*?)</p>', h, re.S)
    assert m
    inner = m.group(1)
    for i in ["play-note", "view3d-note", "figure-note"]:
        assert re.search(rf'<span id="{i}">', inner), i
    assert "点を命令の順に直線で結んだ目安です。ロボット本体の動きではありません。" in inner
    assert ("3D はジョブの X・Y・Z をそのまま点で描いたもので、ロボット本体・関節は描いていません。"
            "動作や干渉を保証するシミュレーターではありません。") in inner
    assert ("この図で離れて見えても、当たらないとは限りません。腕やツールの根元は描いていません。"
            "参考表示であり、実際のロボットの動きではありません。") in inner


def test_help_popover_content():
    h = text("index.html")
    head = h[:h.index("</header>")]
    assert head.index('id="help-button"') < head.index('id="engineer-toggle"')
    pop = re.search(r'<div id="help-pop".*?<!-- /help-pop -->', h, re.S).group(0)
    # 既定（上下の向きが未確認）の例は軸と符号（fix/c026-r2 QA P1-2）。確認済みの「この位置を上に10ミリ」は app.js が入れ直す
    for w in ["Y座標を3ミリ下げて", "Yのマイナス3ミリ", "Zプラス方向に10ミリ",
              "一文字の『Yを…』は聞き違えやすいので、『Y座標を…』と言ってください",
              "話した文は欄に入るだけ。『次へ』で送ります",
              "聞き取った文がちがうときは、直してから送ってください",
              "承認は確認表示のボタンだけです"]:
        assert w in pop, w
    assert "approve" not in pop  # 小窓から承認しない
    j = text("app.js")
    assert '"Escape"' in j and "help-pop" in j


# ---- 承認はボタンだけ（選択肢・サンプルのボタンは承認しない） ----
def test_choices_never_primary_and_only_send_text():
    reply = js_part("function showReply(", "// ---- 3. 指示")
    assert "primary" not in reply
    assert "/api/approve" not in reply and "onApproveClick" not in reply
    # 選択肢は、その文を指示の道へ返すだけ（11/4：入口を1つにしたあとも、選択肢の答えは振り分けずに /api/hear へ）
    assert "answerChoice(c.text)" in reply


def test_sample_button_only_calls_sample_route():
    j = text("app.js")
    assert j.count('"/api/sample"') == 1
    body = js_part("async function loadSample(", "// ---- 画面全体")
    assert "/api/approve" not in body and "/api/export" not in body
    assert re.search(r'postJson\("/api/sample", \{\}\)', body)  # 何も渡さない


def test_confirm_dialog_scrolls_to_top_and_does_not_scroll_on_focus():
    body = js_part("function openConfirm()", "function closeConfirm()")
    assert "scrollTop = 0" in body
    assert "preventScroll: true" in body


def test_numbers_on_screen_come_from_server():
    # 候補のカードの「Y 150.000 → 147.000」はサーバーの before_value・after_value をそのまま出す（計算しない）
    body = js_part("function renderCandidate()", "// 確認済みの方向の名前")
    assert "c.before_value" in body and "c.after_value" in body
    assert "parseFloat" not in body and "Number(" not in body


def test_unknown_words_highlight_uses_text_nodes():
    hl = text("highlight.js")
    assert "innerHTML" not in hl
    j = text("app.js")
    assert "innerHTML" not in j
    assert "unknown_words" in j


# ---- サンプルの読込：決まった1つのファイルだけ。要求の中身は使わない ----
@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    srv = server.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=b"", headers=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def test_sample_route_loads_the_fixed_demo(running):
    r, data = call(running, "POST", "/api/sample", b"{}", {"Content-Type": "application/json"})
    res = json.loads(data)
    assert r.status == 200 and res["ok"] is True and res["view"]["name"] == "DEMO_C_PLACE"
    assert res["session_id"]


@pytest.mark.parametrize("path,body", [
    ("/api/sample?path=samples/dummy/PAT_MOVJ.JBI", b"{}"),
    ("/api/sample?name=../../voice_teaching/jbi.py", b'{"path": "samples/dummy/PAT_MOVJ.JBI"}'),
    ("/api/sample", b'{"name": "PAT_SHARED_POS.JBI", "file": "../README.md"}'),
    ("/api/sample", b"not json at all"),
])
def test_sample_route_ignores_query_and_body(running, path, body):
    r, data = call(running, "POST", path, body)
    res = json.loads(data)
    assert res["ok"] is True and res["view"]["name"] == "DEMO_C_PLACE"


def test_sample_route_passes_exact_file_bytes_to_flow_load(running, monkeypatch):
    seen = []
    real = running.flow.load
    monkeypatch.setattr(running.flow, "load", lambda b: (seen.append(b), real(b))[1])
    call(running, "POST", "/api/sample?x=1", b'{"job_base64": "QUJD"}')
    assert seen == [SAMPLE.read_bytes()]


def test_sample_route_is_post_only_and_checks_origin(running):
    r, _ = call(running, "GET", "/api/sample")
    assert r.status == 404
    r, _ = call(running, "POST", "/api/sample", b"{}", {"Origin": "http://evil.example"})
    assert r.status == 403


def test_sample_path_is_fixed_in_code():
    src = (ROOT / "voice_teaching" / "web" / "server.py").read_text(encoding="utf-8")
    m = re.search(r'if path == "/api/sample":(.*?)return True', src, re.S)
    assert m
    part = m.group(1)
    assert "req" not in part and "self.path" not in part and "headers" not in part
    assert server.SAMPLE_JOB.resolve() == SAMPLE.resolve()


def test_index_served_with_version(running):
    r, data = call(running, "GET", "/")
    page = data.decode("utf-8")
    assert "Ver.1.00-dev" in page and "__VT_VERSION__" not in page
