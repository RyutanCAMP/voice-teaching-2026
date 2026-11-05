"""3D の HC20 を模した静止モデル・台座の目印・正面の矢印（唐川さんの決定 2026-11-05 夜。
docs/DECISION_3D_HC20_MODEL_20261105.md）。画面のファイルの中身を確かめる受入テスト。
動きの確かめ（モデルが出る・原点と X＋ の向き・重ならない・視点を戻す）は tools/e2e_browser.py の check_3d_robot_model。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"


def text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


NEW_NOTE = ("ロボットの形は、HC20 を模した目安の静止モデルです（メーカーの形状データではありません）。"
            "実機の姿勢・寸法・可動範囲・干渉を再現したものではありません。")
OLD_NOTE = "ロボット本体・関節は描いていません"


def test_dims_are_in_one_constant_marked_as_estimate():
    v3 = text("view3d.js")
    m = re.search(r"//[^\n]*目安。唐川さんの確認待ち[^\n]*\n(?://[^\n]*\n)*export const HC20_DIMS = Object\.freeze\(\{", v3)
    assert m, "寸法は1か所の定数（HC20_DIMS）にまとめ、「目安。唐川さんの確認待ち」と注釈を付ける"
    assert v3.count("HC20_DIMS = ") == 1
    # 届く範囲の目安（約 1700mm）に合わせた値であることを書いておく
    assert "1700" in v3[m.start():m.end() + 800]


def test_model_labels_and_marks_texts():
    v3 = text("view3d.js")
    for w in ["HC20 を模した簡易モデル（メーカーの形状データではありません）",
              "ロボットの姿勢は、実機・ジョブの動きと関係ありません（目安の静止モデル）",
              "ロボット（台座の中心）", "ロボットの正面（X＋）"]:
        assert w in v3, w


def test_model_uses_no_outside_data():
    v3 = text("view3d.js")
    # 形のデータは自分で作る（CAD・画像・ロゴを取ってこない）。外の URL・画像・three を使わない
    assert "https://" not in v3 and "http://" not in v3.replace("http://www.w3.org/2000/svg", "")
    for w in ["three", ".stl", ".png", ".jpg", ".svg\"", "<image", "\"image\"", "fetch("]:
        assert w not in v3, w


def test_robot_labels_have_no_direction_or_safety_words():
    v3 = text("view3d.js")
    labels = re.findall(r'"((?:ロボット|HC20)[^"]*)"', v3)
    assert labels
    for s in labels:
        for w in ["上", "下", "右", "左", "手前", "奥", "安全", "当た", "干渉", "届き", "届か", "離れて"]:
            assert w not in s, (s, w)


def test_model_is_static_and_not_clickable():
    v3 = text("view3d.js")
    body = v3[v3.index("  _robotModel(svg) {"):]
    body = body[:body.index("\n  }\n")]
    # 再生の印（marker）に合わせて動かさない。押しても選ばれない（点を隠さない）
    assert "marker" not in body and "player" not in body
    assert '"pointer-events": "none"' in body


def test_model_toggle_button_in_stage_head_keeps_order():
    h = text("index.html")
    head = h[h.index('<div class="stage-head">'):h.index('<p id="figure-message"')]
    assert 'id="model-toggle"' in head
    i_reset, i_toggle, i_tabs = head.index('id="view-reset"'), head.index('id="model-toggle"'), head.index('class="view-tabs"')
    assert i_reset < i_toggle < i_tabs  # 視点を戻す・（モデル）・3D・Y・Z・X・Z・X・Y の並び
    tabs = re.findall(r'class="view-tab" data-view="([a-z0-9]+)"', head)
    assert tabs == ["3d", "yz", "xz", "xy"]
    btn = re.search(r'<button[^>]*id="model-toggle"[^>]*>', head).group(0)
    assert 'aria-pressed="true"' in btn and "hidden" in btn  # 既定は表示。3D を出すまで隠す


def test_notes_rewritten_for_the_model():
    h = text("index.html")
    note = re.search(r'<span id="view3d-note">(.*?)</span>', h, re.S).group(1)
    assert NEW_NOTE in note
    assert "動作や干渉を保証するシミュレーターではありません。" in note
    assert OLD_NOTE not in h
    fig = re.search(r'<span id="figure-note">(.*?)</span>', h, re.S).group(1)
    assert "当たらないとは限りません" in fig and "参考表示であり、実際のロボットの動きではありません。" in fig
    base = re.search(r'<span id="base-note">(.*?)</span>', h, re.S).group(1)
    assert "ロボット座標の原点（0,0,0）" in base and "ロボットの姿勢は、実機・ジョブの動きと関係ありません" in base
    # 確認表示の中の注意も同じ
    ov = h[h.index('id="confirm-overlay"'):]
    assert NEW_NOTE in ov and "動作や干渉を保証するシミュレーターではありません" in ov
    assert "当たらないとは限りません" in ov and "参考表示であり" in ov


def test_2d_marks_come_from_view3d():
    app, v3 = text("app.js"), text("view3d.js")
    assert "export function drawBase2d" in v3
    body = app[app.index("function drawFigure("):]
    body = body[:body.index("\n}\n")]
    assert "drawBase2d(" in body


def test_decision_record_exists():
    d = (ROOT / "docs" / "DECISION_3D_HC20_MODEL_20261105.md").read_text(encoding="utf-8")
    for w in ["HC20", "静止", "原点", "X＋", "Z＋", "メーカーの形状データではありません", "残るリスク", "唐川さんの確認待ち"]:
        assert w in d, w
