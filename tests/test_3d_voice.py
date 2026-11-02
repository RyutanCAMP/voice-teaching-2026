"""feat/3d-voice の受入テスト（画面のファイルとサーバーの状態を読んで確かめる）。

唐川さんの最終指示（2026-11-02 夜）：「JOBを読み込む。3Dでポイントを見る。声で動かす。確認する。」
- U1（CHECKER_020・P1）：最終承認の確認表示に、候補の注意（warnings）をすべてと、図の注意を出す
- 3D：JOB の XYZ をそのまま使う。ドラッグで回転・ホイールで拡大縮小・点を押して選ぶ・視点を戻す・名前を出す・
  変更前後の色分け・命令の順の再生。重なった点も選べる。ロボット本体・関節は描かない（実機の動作・干渉は保証しないと明示）
- 音声：Web会議のように、マイクを一度 ON にすると続けて話せる。ON/OFF が一目で分かる。認識文は入力欄に入るだけ
- 解釈：モックと本物の Claude を画面で区別する
ブラウザーでの動き（回転・拡大・選択・再生・マイクの ON/OFF）は tools/e2e_browser.py で確かめる。
"""
import re
from pathlib import Path

import pytest

from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web import api
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"
DEMO_C = ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI"


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def overlay():
    return re.search(r'<div id="confirm-overlay".*?<!-- /confirm-overlay -->', text("index.html"), re.S).group(0)


# ---- U1：最終承認の確認表示に、必要な注意をすべて出す ----
def test_u1_overlay_has_places_for_all_warnings_and_figure_note():
    o = overlay()
    assert 'id="confirm-warnings"' in o
    assert api.FIGURE_NOTE in o  # 図の注意（右のカードの下と同じ文）
    assert "ロボット本体の動きではありません" in o
    assert "実機用の出力・転送・ロボットの起動はしません" in o


def test_u1_open_confirm_lists_every_candidate_warning():
    j = text("app.js")
    body = j[j.index("function openConfirm()"):j.index("function closeConfirm()")]
    assert '$("confirm-warnings")' in body
    assert "st.candidate.warnings" in body
    assert ".slice(" not in body and ".filter(" not in body  # 一部だけにしない


def test_u1_candidate_has_warnings_to_show():
    wf = WebFlow(interpreter=MockInterpreter())
    sid = wf.load(DEMO_C.read_bytes())["session_id"]
    wf.select(sid, "C00005")
    st = wf.hear(sid, "この位置を上に10mm", "text")["state"]
    ws = st["candidate"]["warnings"]
    assert any("物理的な安全の保証ではありません" in w for w in ws)
    assert any("経路" in w for w in ws)


# ---- 3D 表示 ----
def test_view3d_module_is_local_and_has_required_operations():
    j3 = text("view3d.js")
    assert "http://" not in j3.replace("http://www.w3.org/2000/svg", "") and "https://" not in j3
    for w in ['"pointerdown"', '"wheel"', "reset(", "play(", "stop("]:
        assert w in j3, w
    # 重なった点を選ぶための選び分け
    assert "chooser" in j3
    # 座標はジョブの値（xyz）をそのまま使う。作った座標を足さない
    assert "Math.random" not in j3


def test_3d_is_a_view_and_the_first_one():
    h = text("index.html")
    tabs = re.findall(r'class="view-tab" data-view="([a-z0-9]+)"', h)
    assert tabs[0] == "3d" and set(tabs) == {"3d", "yz", "xz", "xy"}
    assert 'id="view-reset"' in h
    assert 'from "/static/view3d.js"' in text("app.js")


def test_3d_states_it_is_not_a_robot_simulator():
    h = text("index.html")
    note = re.search(r'id="view3d-note"[^>]*>(.*?)</', h, re.S)
    assert note
    assert "ロボット本体" in note.group(1) and "保証" in note.group(1)


# ---- 音声：Web会議のようなマイクの ON/OFF ----
def test_live_mic_toggle_in_voice_section():
    j = text("app.js")
    v = j[j.index("// ---- 音声入力（speech.js）"):]
    assert "state.live" in v
    assert "function resumeLive()" in v
    # 音声の部分は、今まで通り送信・承認・出力を呼ばない
    for w in ["/api/", "postJson", "fetch(", "onApproveClick", "sendInstruction", ".click()", "requestSubmit"]:
        assert w not in v, w
    h = text("index.html")
    assert 'id="mic-label"' in h


# ---- 解釈：モックと本物の Claude を区別 ----
def test_state_tells_mock_or_claude():
    wf = WebFlow(interpreter=MockInterpreter())
    st = wf.load(DEMO_C.read_bytes())["state"]
    assert st["interpreter"]["kind"] == "mock"
    h = text("index.html")
    assert 'id="interp-badge"' in h
    head = h[:h.index("</header>")]
    assert 'id="interp-badge"' in head  # いつも見える上の帯


def test_server_serves_view3d_js():
    from voice_teaching.web import server
    assert "/static/view3d.js" in server.STATIC_FILES if hasattr(server, "STATIC_FILES") else \
        "/static/view3d.js" in (ROOT / "voice_teaching" / "web" / "server.py").read_text(encoding="utf-8")


# ---- 内部 Safety レビュー #1〜#3（確認表示に、描けない点の注記・3D の限界・変わる命令の行も出す） ----
def test_confirm_shows_unplottable_note_3d_limit_and_affected_lines():
    j = text("app.js")
    body = j[j.index("function openConfirm()"):j.index("function closeConfirm()")]
    assert "state.view.figure_message" in body
    assert '$("confirm-affected")' in body and "st.candidate.affected" in body
    o = overlay()
    assert "ロボット本体・関節は描いていません" in o and "保証" in o
    view = api.job_view((ROOT / "samples" / "dummy" / "PAT_POSTYPE_MIXED.JBI").read_bytes())
    assert view["figure_message"]  # 描けない点があるジョブでは、注記の文がある


def test_live_mic_turns_off_after_a_while():
    v = text("app.js")
    assert "LIVE_MAX_MS" in v and "state.live = false" in v
