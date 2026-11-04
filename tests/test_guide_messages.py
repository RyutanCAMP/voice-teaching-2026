"""11/5 UI の「次にすること」（highlight.js の NEXT_STEPS）が合わせる、サーバーの決まった文が、今もその形で出るか。

画面の側はサーバーの文の頭だけを見て、次の行動の1行を添える（サーバーの検査・入力の読み方は変えない）。
サーバーの文が変わったら、このテストが落ちる（画面の表を直す合図）。
"""
import pytest

import test_claude_talk as T
import test_koko_position as K


@pytest.mark.parametrize("select,text,head", [
    (True, "10ミリ動かして", "どの方向"),
    (True, "Zプラス", "何mm動かしますか"),
    (True, "少し下げて", "量がはっきりしません"),
    (False, "Zプラス方向に10mm", "位置が決まっていないため"),
    (False, "C00009を3回にして", "位置 C00009 はこのジョブにありません"),
])
def test_server_message_heads_still_match(tmp_path, select, text, head):
    w, f = T.flow(tmp_path, updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    if select:
        w.select(sid, "C00005")
    msg = w.talk(sid, text)["hear"]["message"]
    assert msg.startswith(head), msg


def test_range_and_decimal_heads_still_match(tmp_path):
    w, f = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    assert w.talk(sid, "C00002からC00003を3回にして")["hear"]["message"].startswith("区間（C00002〜C00003）の中に、区間の外の位置")
    # 小数の回数：くり返しの区間が無いジョブ（DEMO_C）では「回数は整数で…」。区間のあるジョブでは、別の理由で断る
    # （「3.5回」を「5回にはしません」と断る。サーバーの読み方の残件として報告済み。画面の表は変えない）
    w2, f2 = T.flow(tmp_path / "b", T.answer("回数を変えたいのですね。", (21,), "change_request",
                                           {"kind": "repeat", "from_count": 2, "to_count": 3}))
    sid2 = w2.load(K.DEMO_C)["session_id"]
    assert w2.talk(sid2, "3.5回にして")["hear"]["message"].startswith("回数は整数で言ってください")
