"""受入テスト：画面の一連の流れ（シミュレーションモード）。実装より先に書いた。

読込 → 位置選択 → 音声/文字の指示 → 変更候補 → プレビュー → ボタン承認 → シミュレーション用の出力。
- Claude は呼ばない。偽のClaude（voice_teaching.mock_claude）が、発話から取り出した事実だけで道具の引数を作る。
- 出力先は tmp_path に向ける（output/ を汚さない）。
- ダミーは samples/dummy/DEMO_PLACE.JBI（架空のジョブ）。
"""
import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from voice_teaching import jbi, mock_claude, sim
from voice_teaching import utterance as u
from voice_teaching.web import flow as flow_mod
from voice_teaching.web import server
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy" / "DEMO_PLACE.JBI"
STATIC = ROOT / "voice_teaching" / "web" / "static"
ORIGINAL = DUMMY.read_bytes()
EXPORT_LABEL = "シミュレーション用に出力（合成ジョブだけ・実機に入れない）"
ASSERTIVE = ["安全です", "当たります", "干渉します", "当たりません", "干渉しません", "安全な"]


@pytest.fixture
def wf(tmp_path):
    return WebFlow()


def load(wf, data=ORIGINAL):
    r = wf.load(data)
    assert r["ok"] is True, r
    return r["session_id"]


def lines(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


def candidate_ready(wf):
    sid = load(wf)
    assert wf.select(sid, "C00004")["status"] == "ok"
    r = wf.hear(sid, "この位置をYプラス方向に10mm移動して", source="voice")
    assert r["status"] == "ok", r
    return sid, r


@pytest.fixture
def no_approve_from_words(monkeypatch):
    """指示の経路で sim.approve が呼ばれたら失敗にする。"""
    def boom(*a, **k):
        raise AssertionError("指示の経路から承認が呼ばれました")
    monkeypatch.setattr(flow_mod.sim, "approve", boom)


# ---- 1. 一通りの流れ ----
def test_full_flow_exports_two_line_diff(wf, tmp_path):
    sid, r = candidate_ready(wf)
    st = r["state"]
    cand = st["candidate"]
    assert cand["target"] == "C00004 Y"
    assert cand["before_line"] == "C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert cand["after_line"] == "C00004=450.000,200.000,120.000,180.00,0.00,0.00"
    assert cand["delta"] == "+10.000"
    assert "C00004" in st["confirmation_text"] and "+10.000mm" in st["confirmation_text"]
    assert st["blocked_reason"] is None
    # 図のための変更後の座標
    moved = next(p for p in cand["after_positions"] if p["name"] == "C00004")
    assert moved["xyz"] == [450.0, 200.0, 120.0]
    assert st["can_approve"] is True and st["can_export"] is False

    # 比較（プレビュー）
    c = wf.compare(sid)
    assert c["status"] == "ok" and c["state"]["candidate"]["line_no"] == 13

    a = wf.approve(sid, "テスト太郎", candidate_gen=wf.candidate_gen_of(sid))
    assert a["ok"] is True, a
    assert a["state"]["approval"]["approved_by"] == "テスト太郎"
    assert a["state"]["can_export"] is True

    e = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert e["ok"] is True, e
    out = Path(e["path"])
    assert out.parent == tmp_path / "sim"
    assert out.name == "DEMO_PLACE_SIM.JBI.txt"
    new = out.read_bytes()
    a_lines, b_lines = lines(ORIGINAL), lines(new)
    assert len(a_lines) == len(b_lines)
    diff = [(x, y) for x, y in zip(a_lines, b_lines) if x != y]
    assert len(diff) == 2
    assert diff[0][0].rstrip(b"\r\n") == b"//NAME DEMO_PLACE"
    assert diff[0][1].rstrip(b"\r\n") == b"//NAME DEMO_PLACE_SIM"
    assert diff[1][0].rstrip(b"\r\n") == b"C00004=450.000,190.000,120.000,180.00,0.00,0.00"
    assert diff[1][1].rstrip(b"\r\n") == b"C00004=450.000,200.000,120.000,180.00,0.00,0.00"
    # 元のジョブは変わらない
    assert DUMMY.read_bytes() == ORIGINAL
    assert any(k == "出力" for k, _ in ((x["kind"], x["text"]) for x in e["state"]["log"]))


def test_mock_claude_is_shown_in_log(wf):
    sid, r = candidate_ready(wf)
    log = r["state"]["log"]
    assert any("モック" in x["text"] or "モック" in x["kind"] for x in log)
    assert r["tool_calls"] == [{"tool": "propose_change", "args": {
        "kind": "shift", "position": "C00004", "axis": "Y", "delta_mm": 10.0}}]
    assert "モック" in r["state"]["mock_notice"]


def test_heard_text_and_facts_are_returned_separately(wf):
    sid = load(wf)
    r = wf.hear(sid, "C00004をYプラス方向に10mm移動して", source="text")
    assert r["heard"] == "C00004をYプラス方向に10mm移動して"
    f = r["facts"]
    assert f["positions"] == ["C00004"] and f["axes"] == ["Y"]
    assert f["requested_value"] == 10.0
    assert r["status"] == "ok"


def test_voice_and_text_give_same_result(tmp_path):
    results = []
    for source in ("voice", "text"):
        w = WebFlow()
        sid = load(w)
        r = w.hear(sid, "C00004をZ方向に-5mm移動して", source=source)
        results.append((r["status"], r["state"]["candidate"]["after_line"]))
    assert results[0] == results[1]


def test_without_selection_this_position_is_not_proposed(wf):
    sid = load(wf)
    r = wf.hear(sid, "この位置をYプラス方向に10mm移動して", source="text")
    assert r["status"] == "ask"
    assert r["tool_calls"] == []
    assert r["state"]["candidate"] is None


def test_unclear_amount_asks_and_mock_proposes_nothing(wf):
    sid = load(wf)
    wf.select(sid, "C00004")
    r = wf.hear(sid, "この位置をY方向に少し移動して", source="voice")
    assert r["status"] == "ask"
    assert r["tool_calls"] == []
    assert r["state"]["candidate"] is None


# ---- 2. 声や文字では承認しない ----
@pytest.mark.parametrize("words", ["承認して", "OK", "はい", "これでいいです", "書き出して"])
def test_words_never_approve(wf, no_approve_from_words, words):
    sid, _ = candidate_ready(wf)
    r = wf.hear(sid, words, source="voice")
    assert r["status"] == "stop"
    assert "ボタン" in r["message"]
    assert r["state"]["approval"] is None
    assert r["state"]["can_export"] is False
    assert wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))["ok"] is False


def test_start_words_are_stopped(wf, no_approve_from_words):
    sid, _ = candidate_ready(wf)
    r = wf.hear(sid, "スタートして", source="voice")
    assert r["status"] == "stop" and "動かしません" in r["message"]


# ---- 3. 承認の前は出力しない ----
def test_export_before_approve_is_refused(wf, tmp_path):
    sid, _ = candidate_ready(wf)
    e = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert e["ok"] is False and "承認" in e["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_approve_needs_name(wf):
    sid, _ = candidate_ready(wf)
    for who in ["", "   ", None]:
        a = wf.approve(sid, who, candidate_gen=wf.candidate_gen_of(sid))
        assert a["ok"] is False and "名前" in a["error"]
        assert a["state"]["approval"] is None


def test_approve_without_candidate_is_refused(wf):
    sid = load(wf)
    a = wf.approve(sid, "テスト太郎", candidate_gen=wf.candidate_gen_of(sid))
    assert a["ok"] is False and "候補" in a["error"]


# ---- 4. 承認の後で候補が変わったら、承認を取り消す ----
def approved(wf):
    sid, _ = candidate_ready(wf)
    assert wf.approve(sid, "テスト太郎", candidate_gen=wf.candidate_gen_of(sid))["ok"] is True
    return sid


def test_new_instruction_after_approve_cancels_approval(wf, tmp_path):
    sid = approved(wf)
    r = wf.hear(sid, "C00005をXプラス方向に5mm移動して", source="text")
    assert r["state"]["approval"] is None
    assert r["state"]["can_export"] is False
    assert any("承認を取り消" in x["text"] for x in r["state"]["log"])
    assert wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))["ok"] is False
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_undo_after_approve_cancels_approval(wf):
    sid = approved(wf)
    r = wf.undo(sid)
    assert r["state"]["approval"] is None and r["state"]["candidate"] is None
    assert wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))["ok"] is False


def test_select_after_approve_cancels_approval(wf):
    sid = approved(wf)
    r = wf.select(sid, "C00005")
    assert r["state"]["approval"] is None
    assert wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))["ok"] is False


def test_compare_after_approve_keeps_approval(wf):
    sid = approved(wf)
    assert wf.compare(sid)["state"]["approval"] is not None


# ---- 5. 合成ジョブでないものは出力しない ----
def test_non_synthetic_bytes_are_refused_at_export(wf, tmp_path):
    data = ORIGINAL.replace(b"///DATE 2026/11/02 14:40", b"///DATE 2026/11/03 09:00")
    assert data != ORIGINAL
    sid = load(wf, data)
    wf.select(sid, "C00004")
    assert wf.hear(sid, "この位置をYプラス方向に10mm移動して")["status"] == "ok"
    assert wf.approve(sid, "テスト太郎", candidate_gen=wf.candidate_gen_of(sid))["ok"] is True
    e = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert e["ok"] is False
    assert "合成ジョブ" in e["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_second_export_does_not_overwrite(wf, tmp_path):
    """同じ承認の2回目の出力は断る（N-1・11/7：断る理由は「もう出力しました」。ファイルは上書きもしないし、増やしもしない）。
    出力のボタンも押せなくする（can_export）"""
    sid = approved(wf)
    first = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert first["ok"] is True
    before = sorted((p.name, p.read_bytes()) for p in (tmp_path / "sim").iterdir())
    e = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert e["ok"] is False and "もう出力しました" in e["error"] and first["file_name"] in e["error"]
    assert sorted((p.name, p.read_bytes()) for p in (tmp_path / "sim").iterdir()) == before
    st = wf.state(sid)["state"]
    assert st["exported"] and st["exported"].endswith(first["file_name"])  # 画面は exported で出力のボタンを押せなくする


# ---- 6. シミュレーションだけの候補（実機用の検査で止まる） ----
def test_blocked_candidate_shows_reason(wf):
    sid = load(wf)
    r = wf.hear(sid, "C00000をXプラス方向に10mm移動して")
    assert r["status"] == "ok"
    st = r["state"]
    assert st["blocked_reason"] and "共用" in st["blocked_reason"]
    assert "シミュレーション" in st["confirmation_text"]
    # 止まった候補は承認・出力できない（CHECKER_019 R1）。承認を促す文は出さない
    assert "承認も出力もできません" in st["confirmation_text"]
    assert "これでいいですか" not in st["confirmation_text"]
    assert st["can_approve"] is False


def test_speed_is_disabled_by_default(wf):
    assert jbi.SPEED_CHANGE_ENABLED is False
    sid = load(wf)
    r = wf.hear(sid, "C00004の速度を10%上げて")
    assert r["status"] == "stop"
    assert r["state"]["candidate"] is None
    assert r["state"]["can_approve"] is False and r["state"]["can_export"] is False


@pytest.mark.usefixtures("speed_enabled")
def test_speed_is_not_changed_without_limit(wf):
    sid = load(wf)
    r = wf.hear(sid, "C00004の速度を10%上げて")
    assert r["status"] == "stop"
    assert r["state"]["candidate"] is None


def test_unknown_session_is_refused(wf):
    assert wf.hear("nope", "C00004をYプラス方向に10mm移動して")["ok"] is False
    assert wf.approve("nope", "テスト太郎", candidate_gen=wf.candidate_gen_of("nope"))["ok"] is False
    assert wf.export("nope", candidate_gen=wf.candidate_gen_of("nope"))["ok"] is False


def test_old_sessions_are_dropped(tmp_path):
    # 有効な画面は1つだけ（唐川さんの決定 Q-7。11/5）：読み込むたびに、前の画面はすべて閉じる（max_sessions に関係なく）
    w = WebFlow(max_sessions=2)
    a, b, c = load(w), load(w), load(w)
    for old in (a, b):
        r = w.state(old)
        assert r["ok"] is False and r["session_closed"] is True and r["error"] == flow_mod.SESSION_CLOSED
    assert w.state(c)["ok"] is True


def test_unreadable_job_is_refused(wf):
    r = wf.load(b"\xff\xfe not a job")
    assert r["ok"] is False and r["error"]


def test_sessions_are_not_saved_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    w = WebFlow()
    sid = load(w)
    w.select(sid, "C00004")
    w.hear(sid, "この位置をYプラス方向に10mm移動して")
    w.approve(sid, "テスト太郎", candidate_gen=w.candidate_gen_of(sid))
    assert list(tmp_path.iterdir()) == []


# ---- 7. 偽のClaude（モック）：事実だけから道具の引数を作る ----
def facts(text):
    return u.read_utterance(text)


def test_mock_uses_only_facts():
    calls = mock_claude.decide(facts("C00005をX方向に-3mm移動して"), selected=None)
    assert calls == [{"tool": "propose_change", "args": {
        "kind": "shift", "position": "C00005", "axis": "X", "delta_mm": -3.0}}]
    for c in calls:
        assert set(c["args"]) <= {"kind", "position", "axis", "side", "delta_mm", "percent"}


@pytest.mark.parametrize("text", [
    "C00005を少し動かして", "C00005を動かして", "右に10ミリ", "C00005をX方向に動かして",
    "Yプラス方向に10mm移動して",  # 位置が分からない（選択も無い）
])
def test_mock_proposes_nothing_without_facts(text):
    calls = mock_claude.decide(facts(text), selected=None)
    assert all(c["tool"] != "propose_change" for c in calls)


def test_mock_select_compare_undo():
    assert mock_claude.decide(facts("C00003を選択して"), None) == [
        {"tool": "select", "args": {"position": "C00003"}}]
    assert mock_claude.decide(facts("比較して"), "C00003") == [{"tool": "compare", "args": {}}]
    assert mock_claude.decide(facts("元に戻して"), "C00003") == [{"tool": "undo", "args": {}}]


def test_mock_never_calls_approve_or_start():
    for text in ["承認して", "OK", "スタートして", "書き出して", "転送して"]:
        for c in mock_claude.decide(facts(text), "C00004"):
            assert c["tool"] in mock_claude.TOOLS
    assert "approve" not in mock_claude.TOOLS and "export" not in mock_claude.TOOLS


# ---- 8. HTTP（127.0.0.1 のポート0） ----
@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    srv = server.make_server(port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def post(srv, path, obj=None, raw=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    if raw is not None:
        conn.request("POST", path, body=raw, headers={"Content-Type": "application/octet-stream"})
    else:
        conn.request("POST", path, body=json.dumps(obj).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, json.loads(body)


def test_http_full_flow(running, tmp_path):
    st, r = post(running, "/api/load", raw=ORIGINAL)
    assert st == 200 and r["ok"] is True
    sid = r["session_id"]
    assert r["view"]["name"] == "DEMO_PLACE"
    assert post(running, "/api/select", {"session_id": sid, "position": "C00004"})[1]["status"] == "ok"
    _, r = post(running, "/api/hear", {"session_id": sid, "text": "この位置をYプラス方向に10mm移動して", "source": "voice"})
    assert r["status"] == "ok" and r["state"]["can_approve"] is True
    gen = r["state"]["candidate_gen"]  # 承認・出力のボタンは、表示中の候補の世代を送る（P0 H02・H16）
    _, r = post(running, "/api/compare", {"session_id": sid})
    assert r["status"] == "ok"
    _, r = post(running, "/api/export", {"session_id": sid, "candidate_gen": gen})
    assert r["ok"] is False
    _, r = post(running, "/api/hear", {"session_id": sid, "text": "承認して", "source": "voice"})
    assert r["status"] == "stop" and r["state"]["approval"] is None
    _, r = post(running, "/api/approve", {"session_id": sid, "approver": ""})
    assert r["ok"] is False
    _, r = post(running, "/api/approve", {"session_id": sid, "approver": "テスト太郎"})
    assert r["ok"] is False  # 候補の世代が無い要求は断る
    _, r = post(running, "/api/approve", {"session_id": sid, "approver": "テスト太郎", "candidate_gen": gen})
    assert r["ok"] is True
    _, r = post(running, "/api/export", {"session_id": sid})
    assert r["ok"] is False  # 候補の世代が無い要求は断る
    _, r = post(running, "/api/export", {"session_id": sid, "candidate_gen": gen})
    assert r["ok"] is True
    assert Path(r["path"]).read_bytes() != ORIGINAL
    assert sorted(p.name for p in (tmp_path / "sim").iterdir()) == ["DEMO_PLACE_SIM.JBI.txt"]
    # 作業フォルダ（tmp_path）には出力先のほか何も作らない
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sim"]


def test_http_undo_and_bad_requests(running):
    _, r = post(running, "/api/load", raw=ORIGINAL)
    sid = r["session_id"]
    _, r = post(running, "/api/undo", {"session_id": sid})
    assert r["status"] == "ok"
    st, r = post(running, "/api/hear", {"session_id": sid, "text": 5})
    assert r["ok"] is False
    st, r = post(running, "/api/hear", {"session_id": sid, "text": "C00004をYプラス方向に10mm移動して", "source": "robot"})
    assert r["ok"] is False
    st, r = post(running, "/api/export", {})
    assert r["ok"] is False


# ---- 9. 画面（HTML・JS） ----
def static_text():
    return {p.name: p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.is_file()}


def test_ids_used_by_js_exist_in_html():
    files = static_text()
    html, js = files["index.html"], files["app.js"]
    used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js))
    defined = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    assert used, "JS が id を使っていない"
    assert used <= defined, used - defined


def test_no_inline_script_or_handlers():
    html = static_text()["index.html"]
    assert "<script>" not in html
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S):
        assert "src=" in m.group(1) and m.group(2).strip() == ""
    assert not re.search(r"\son[a-z]+\s*=", html)


def test_screen_has_text_input_and_voice_controls():
    html = static_text()["index.html"]
    for i in ["text-input", "heard-text", "facts-view", "reply-message",
              "confirm-overlay", "confirm-text", "confirm-blocked", "approver-name",
              "approve-button", "export-button", "work-log",
              "voice-input-slot", "voice-mode-browser", "voice-mode-local", "mic-button",
              "voice-stop", "voice-cancel", "voice-partial", "voice-unsupported"]:
        assert f'id="{i}"' in html, i
    # 処理方法は既定で未選択（選ぶまで録音できない）
    assert not re.search(r'name="voice-mode"[^>]*checked', html)
    assert re.search(r'<button[^>]*id="mic-button"[^>]*disabled', html)
    # 送信先の注意（マイクの近く）と、使えないブラウザーの文
    assert "ブラウザー提供元のサーバーで処理" in html
    assert "このブラウザでは音声入力を使えません。文字で入力してください" in html
    # app.js はモジュールとして読み、speech.js を同じサーバーから読む
    assert '<script type="module" src="/static/app.js"></script>' in html
    assert 'from "/static/speech.js"' in static_text()["app.js"]


def test_speech_js_is_the_handoff_file_unchanged():
    import hashlib
    data = (STATIC / "speech.js").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == "4ad08bae0c175247362bd5e000d4a38c167704f181074c3ea60a8dca962c36d1"


def test_speech_js_calls_no_api():
    js = static_text()["speech.js"]
    assert "/api/" not in js and "fetch(" not in js and "XMLHttpRequest" not in js


def voice_section(js):
    return js[js.index("// ---- 音声入力（speech.js）"):]


def test_voice_only_fills_the_input_and_never_sends_or_approves():
    js = static_text()["app.js"]
    v = voice_section(js)
    # 音声の部分からは、サーバーへの要求も承認・出力も呼ばない
    for w in ["/api/", "postJson", "fetch(", "onApproveClick", "sendInstruction", ".click()", "requestSubmit"]:
        assert w not in v, w
    # final は入力欄に代入するだけ
    final = v[v.index("final: (text) =>"):v.index("error: (message) =>")]
    assert '$("text-input").value = text;' in final
    # 承認と出力は、ボタンのクリックの処理の中だけ
    assert js.count('"/api/approve"') == 1 and js.count('"/api/export"') == 1
    assert '$("export-button").addEventListener("click"' in js


def test_recording_locks_other_operations():
    v = voice_section(static_text()["app.js"])
    locks = v[v.index("function updateLocks()"):v.index("if (!Recognition)")]
    for i in ["file-input", "text-input", "text-send"]:
        assert f'$("{i}").disabled = rec' in locks, i
    # マイクは1か所（micLocked）で決める：録音中・JOB が無い・認識が使えない・ステップ4（11/5 夜 実マイクの不具合の直し）
    assert '$("mic-button").disabled = micLocked(currentStep());' in locks
    v_all = static_text()["app.js"]
    ml = v_all[v_all.index("function micLocked(step)"):v_all.index("function updateLocks()")]
    assert "return recording() || !state.sessionId || !Recognition || step === 4;" in ml
    assert '$("voice-stop").disabled = !rec' in locks
    assert '$("voice-cancel").disabled = !rec' in locks
    js = static_text()["app.js"]
    # 出力のあと・出力中も押せない（11/5 H1）
    assert '$("export-button").disabled = !st.can_export || !!st.exported || state.exporting || recording()' in js
    assert '$("approve-button").disabled = !st.can_approve || recording()' in js
    # 承認を送っている間も押せない（ダブルクリックで2回送らない。11/5 夜 N2）
    assert '$("approve-button").disabled = !st.can_approve || state.approving || recording()' in js


def test_approve_button_is_inside_overlay_and_export_label():
    html = static_text()["index.html"]
    overlay = re.search(r'<div id="confirm-overlay".*?<!-- /confirm-overlay -->', html, re.S).group(0)
    assert 'id="approve-button"' in overlay
    assert re.search(r'<button[^>]*id="export-button"[^>]*disabled[^>]*>' + re.escape(EXPORT_LABEL) + "</button>", html)


def test_js_calls_approve_only_from_button():
    js = static_text()["app.js"]
    assert js.count('"/api/approve"') == 1
    # /api/approve を呼ぶ関数は、承認ボタンのクリックからだけ使う
    m = re.search(r'\$\("approve-button"\)\.addEventListener\("click",\s*(\w+)\)', js)
    assert m, "承認ボタンのクリックの処理が無い"
    fn = m.group(1)
    assert len(re.findall(r"\b" + fn + r"\b", js)) == 2  # 定義とクリックの登録だけ


def test_static_words_are_not_assertive():
    for text in static_text().values():
        for w in ASSERTIVE:
            assert w not in text


def test_mock_and_flow_texts_are_not_assertive(wf):
    sid, r = candidate_ready(wf)
    text = json.dumps(r, ensure_ascii=False) + mock_claude.MOCK_NOTICE
    for w in ASSERTIVE:
        assert w not in text


def test_log_does_not_repeat_candidate_line(wf):
    sid, r = candidate_ready(wf)
    texts = [x["text"] for x in r["state"]["log"]]
    assert texts.count(r["state"]["confirmation_text"]) == 1
