"""3D・2D の図の、ロボットの台座の目印と正面の矢印（唐川さんの決定 2026-11-05 夜。docs/DECISION_3D_ROBOT_BASE_20261105.md）。
1回目の決定（HC20 を模した静止モデルを出す）は、プレビューを見て2回目の決定で取りやめた：ロボットの形は描かない。
画面のファイルの中身を確かめる受入テスト。動きの確かめ（印が出る・原点と X＋ の向き・重ならない・視点を戻す・2D の印）は
tools/e2e_browser.py の check_3d_robot_base。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"
FILES = ["view3d.js", "app.js", "index.html", "style.css", "highlight.js", "explain.js"]


def text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_base_and_front_labels_are_in_view3d():
    v3 = text("view3d.js")
    assert 'const BASE_LABEL = "ロボット（台座の中心）";' in v3
    assert 'const FRONT_LABEL = "ロボットの正面（X＋）";' in v3
    assert "export function drawBase2d" in v3


def test_no_robot_model_anywhere():
    # 2回目の決定：HC20 の腕・関節・胴体のモデルは出さない。モデル用のコード・文・ボタンを残さない
    for name in FILES:
        t = text(name)
        for w in ["HC20 を模した", "HC20_DIMS", "HC20_POSE", "hc20Parts", "robot-model", "model-toggle", "メーカーの形状データ",
                  "目安の静止モデル", "ロボットの姿勢は、実機・ジョブの動きと関係ありません"]:
            assert w not in t, (name, w)


def test_robot_labels_have_no_direction_or_safety_words():
    v3 = text("view3d.js")
    labels = re.findall(r'"(ロボット[^"]*)"', v3) + re.findall(r"`\$\{FRONT_LABEL\}([^`]*)`", v3)
    assert labels
    for s in labels:
        # 「上」「右」「手前」などは、画面ごとに向きを確かめる仕組みができるまで札に入れない
        for w in ["上", "下", "右", "左", "手前", "奥", "安全", "当た", "干渉", "届き", "届か", "離れて"]:
            assert w not in s, (s, w)


def test_base_marks_do_not_take_clicks():
    v3 = text("view3d.js")
    body = v3[v3.index("  _baseMarks(svg) {"):]
    body = body[:body.index("\n  }\n")]
    assert 'class: "robot-base", "pointer-events": "none"' in body
    assert "robot-origin" in body and "robot-front" in body
    # 再生（marker・player）に合わせて動かさない
    assert "marker" not in body and "player" not in body
    lab = v3[v3.index("function labelEl("):]
    assert '"pointer-events": "none"' in lab[:lab.index("\n}\n")]


def test_stage_head_buttons_are_as_before():
    h = text("index.html")
    head = h[h.index('<div class="stage-head">'):h.index('<p id="figure-message"')]
    buttons = re.findall(r'<button[^>]*id="([a-z0-9-]+)"', head)
    assert buttons == ["view-reset"]
    tabs = re.findall(r'class="view-tab" data-view="([a-z0-9]+)"', head)
    assert tabs == ["3d", "yz", "xz", "xy"]


def test_notes_keep_the_no_guarantee_sentences_and_add_base_note():
    h = text("index.html")
    m = re.search(r'<p class="fine figure-notes">(.*?)</p>', h, re.S).group(1)
    assert ("3D はジョブの X・Y・Z をそのまま点で描いたもので、ロボット本体・関節は描いていません。"
            "動作や干渉を保証するシミュレーターではありません。") in m
    assert ("この図で離れて見えても、当たらないとは限りません。腕やツールの根元は描いていません。"
            "参考表示であり、実際のロボットの動きではありません。") in m
    # 台座の印の説明：図の下の注意書きには足さない（1440×900 で 3D が縮むため）。3D の印の title と確認表示の注意書きに置く
    assert 'id="base-note"' not in h
    assert 'const BASE_NOTE = "台座の印は、ロボット座標の原点（0,0,0）の位置です。ロボットの大きさ・形ではありません。";' in text("view3d.js")
    ov = h[h.index('id="confirm-overlay"'):]
    assert "ロボット本体・関節は描いていません" in ov and "動作や干渉を保証するシミュレーターではありません" in ov
    assert "台座の印は、ロボット座標の原点（0,0,0）の位置です。ロボットの大きさ・形ではありません。" in ov
    assert "当たらないとは限りません" in ov and "参考表示であり" in ov


def test_2d_marks_come_from_view3d():
    app = text("app.js")
    assert 'import { View3D, drawBase2d } from "/static/view3d.js";' in app
    body = app[app.index("function drawFigure("):]
    body = body[:body.index("\n}\n")]
    assert "drawBase2d(" in body
    assert ".concat(0)" in body  # 原点も図の広さに入れる


def test_decision_record_has_both_decisions():
    d = (ROOT / "docs" / "DECISION_3D_ROBOT_BASE_20261105.md").read_text(encoding="utf-8")
    for w in ["1回目の決定", "2回目の決定", "HC20", "シンプルな表示に戻す", "ロボット（台座の中心）", "ロボットの正面（X＋）",
              "原点", "格子", "残るリスク"]:
        assert w in d, w
    assert not (ROOT / "docs" / "DECISION_3D_HC20_MODEL_20261105.md").exists()
