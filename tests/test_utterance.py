"""受入テスト：発話の読み取りと、Claude（モック）の提案との照らし合わせ。

仕様v2.1 11章の 5・7・11・14・16・18 に対応。実装より先に書いた。
"""
import pytest

from voice_teaching import utterance as u
from voice_teaching.utterance import ProposedChange, check_proposal, pre_check, read_utterance


def shift(position="C00005", axis="X", delta_mm=10):
    return ProposedChange(kind="shift", position=position, axis=axis, delta_mm=delta_mm)


# ---- 想定する発話 ----
def test_select_utterance():
    f = read_utterance("C00005を選択して")
    assert f.kinds == ("select",)
    assert f.position == "C00005"
    assert pre_check(f).status == "ok"


def test_move_this_position_x_10mm():
    f = read_utterance("この位置をXプラス方向に10mm移動して")
    assert f.kinds == ("move",)
    assert f.position is None
    assert f.refers_to_selection
    assert f.axis == "X"
    assert f.amount.value == 10.0 and f.amount.unit == "mm"
    assert f.requested_value() == 10.0
    assert pre_check(f).status == "ok"


def test_compare_utterance_is_not_front_back():
    f = read_utterance("変更前と比較して")
    assert f.kinds == ("compare",)
    assert f.stop_reasons == ()
    assert pre_check(f).status == "ok"


def test_undo_utterance():
    f = read_utterance("元に戻して")
    assert f.kinds == ("undo",)
    assert pre_check(f).status == "ok"


def test_right_left_is_not_turned_into_an_axis():
    """7. 軸が設定されていない枠では、左右の依頼は進まない（シクロの指示：聞き返し）"""
    for text in ("置く位置を右に10ミリ", "C00005を左に10mm"):
        f = read_utterance(text)
        assert f.sides
        assert f.axes == ()
        v = pre_check(f)
        assert v.status == "ask"
        assert "軸" in v.message


# ---- 正規化 ----
def test_fullwidth_is_normalized():
    f = read_utterance("Ｃ００００５をＸプラス方向に１０ｍｍ移動して")
    assert f.position == "C00005"
    assert f.axis == "X"
    assert f.amount.value == 10.0
    assert pre_check(f).status == "ok"


def test_fullwidth_milli_and_lowercase():
    f = read_utterance("c00005をxプラス方向に１０ミリ動かして")
    assert f.position == "C00005"
    assert f.axis == "X"
    assert f.requested_value() == 10.0


def test_decimal_amount():
    f = read_utterance("C00005をZプラス方向に0.5mm移動して")
    assert f.requested_value() == 0.5


# ---- 5. 量が分からない指示は聞き返す ----
@pytest.mark.parametrize("text", [
    "C00005をXプラス方向に移動して",          # 量が無い
    "C00005をXプラス方向に十ミリ移動して",     # 漢数字
    "C00005をX方向に半分だけ移動して",   # 半分
    "C00005をX方向に少し移動して",       # 少し
    "C00005をXプラス方向に10移動して",         # 単位が無い数字
    "C00005をXプラス方向に1センチ移動して",    # mm以外の長さの単位
    "C00005をXプラス方向に1,000mm移動して",    # 区切りのある数字（1 や 0 と読まない）
])
def test_unclear_amount_asks(text):
    f = read_utterance(text)
    assert f.amount is None
    v = pre_check(f)
    assert v.status == "ask"


def test_no_amount_question_text():
    v = pre_check(read_utterance("C00005をXプラス方向に移動して"))
    assert "何mm" in v.message


def test_kanji_amount_is_not_read_as_number():
    f = read_utterance("C00005をXプラス方向に十ミリ移動して")
    assert f.amounts == ()
    assert f.unclear_amounts


# ---- 軸と向き ----
def test_up_down_is_z():
    up = read_utterance("C00005を10mm上げて")
    assert up.kinds == ("move",)
    assert up.axis == "Z" and up.requested_value() == 10.0
    down = read_utterance("C00005を10mm下げて")
    assert down.axis == "Z" and down.requested_value() == -10.0


def test_negative_direction():
    assert read_utterance("C00005をX方向に-10mm移動して").requested_value() == -10.0
    assert read_utterance("C00005をXマイナス方向に10mm移動して").requested_value() == -10.0


def test_two_sign_specifications_ask():
    assert pre_check(read_utterance("C00005をX-方向に-10mm移動して")).status == "ask"


def test_no_direction_asks():
    assert pre_check(read_utterance("C00005を10mm移動して")).status == "ask"


def test_two_axes_ask():
    assert pre_check(read_utterance("C00005をX方向とYプラス方向に10mm移動して")).status == "ask"


def test_front_back_stops():
    assert pre_check(read_utterance("C00005を前に10mm移動して")).status == "stop"


# ---- 速度 ----
def test_speed_up_percent():
    f = read_utterance("C00004の速度を20%上げて")
    assert f.kinds == ("speed",)
    assert f.axes == ()
    assert f.amount.unit == "%"
    assert f.requested_value() == 20.0
    assert pre_check(f).status == "ok"


def test_speed_down_percent():
    assert read_utterance("C00004の速度を20%下げて").requested_value() == -20.0


def test_speed_without_direction_asks():
    assert pre_check(read_utterance("C00004の速度を20%にして")).status == "ask"


def test_speed_absolute_value_is_not_an_amount():
    """仕様5-3：量は 算用数字＋mm／ミリ／% だけ"""
    assert pre_check(read_utterance("C00004の速度を100にして")).status == "ask"


# ---- 11. 2つ以上を変える依頼は聞き返す ----
def test_two_positions_ask():
    assert pre_check(read_utterance("C00004とC00005をXプラス方向に10mm移動して")).status == "ask"


def test_two_operations_ask():
    assert pre_check(read_utterance("Xプラス方向に10mm移動して元に戻して")).status == "ask"


def test_bad_position_number_asks():
    f = read_utterance("C0005をXプラス方向に10mm移動して")
    assert f.position is None
    assert pre_check(f).status == "ask"


def test_nothing_understood_asks():
    assert pre_check(read_utterance("こんにちは")).status == "ask"


# ---- 承認・起動は声や文字では行わない ----
@pytest.mark.parametrize("text", ["はい", "OK", "承認して", "これでいいです", "書き出して", "ロボットに転送して"])
def test_voice_approval_stops(text):
    v = pre_check(read_utterance(text))
    assert v.status == "stop"
    assert "ボタン" in v.message


def test_robot_start_stops():
    v = pre_check(read_utterance("ロボットを起動して"))
    assert v.status == "stop"


# ---- 16. 提案と発話の照らし合わせ ----
def test_matching_proposal_ok():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    assert check_proposal(f, shift()).status == "ok"


def test_amount_mismatch_asks():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    v = check_proposal(f, shift(delta_mm=15))
    assert v.status == "ask"
    assert "10" in v.message and "15" in v.message


def test_sign_mismatch_asks():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    assert check_proposal(f, shift(delta_mm=-10)).status == "ask"


def test_axis_mismatch_asks():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    assert check_proposal(f, shift(axis="Y")).status == "ask"


def test_position_mismatch_asks():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    assert check_proposal(f, shift(position="C00004")).status == "ask"


def test_this_position_uses_selection():
    f = read_utterance("この位置をXプラス方向に10mm移動して")
    assert check_proposal(f, shift(), selected="C00005").status == "ok"
    assert check_proposal(f, shift(position="C00004"), selected="C00005").status == "ask"
    assert check_proposal(f, shift(), selected=None).status == "ask"


def test_two_proposals_ask():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    assert check_proposal(f, [shift(), shift(position="C00004")]).status == "ask"
    assert check_proposal(f, []).status == "ask"


def test_unclear_amount_rejects_any_proposal():
    """5. 漢数字・「半分」では、Claudeが数字を出しても使わない"""
    for text in ("C00005をXプラス方向に十ミリ移動して", "C00005をX方向に半分移動して"):
        assert check_proposal(read_utterance(text), shift(delta_mm=5)).status == "ask"


def test_side_proposal_stops():
    f = read_utterance("C00005をXプラス方向に10mm移動して")
    p = ProposedChange(kind="shift", position="C00005", side="右", delta_mm=10)
    assert check_proposal(f, p).status == "stop"


def test_kind_mismatch_asks():
    f = read_utterance("C00004の速度を20%上げて")
    assert check_proposal(f, shift(position="C00004")).status == "ask"


def test_percent_mismatch_asks():
    f = read_utterance("C00004の速度を20%上げて")
    ok = ProposedChange(kind="speed", position="C00004", percent=20)
    bad = ProposedChange(kind="speed", position="C00004", percent=30)
    assert check_proposal(f, ok).status == "ok"
    assert check_proposal(f, bad).status == "ask"


def test_proposal_for_non_change_utterance_stops():
    f = read_utterance("変更前と比較して")
    assert check_proposal(f, shift(), selected="C00005").status == "stop"


# ---- 道具の引数：ジョブの文字列や文を受け取らない ----
def test_tool_args_accepts_only_known_keys():
    p = ProposedChange.from_tool_args({"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": 10})
    assert p == shift()
    for bad in (
        {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": 10, "after_line": "C00005=460.000,..."},
        {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": 10, "message": "動かします"},
        {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": True},
        {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": "10"},
        {"kind": "shift", "position": "C00005", "axis": "X", "delta_mm": float("nan")},
        {"kind": "rename", "position": "C00005"},
    ):
        with pytest.raises(ValueError):
            ProposedChange.from_tool_args(bad)


# ---- 14. 数字は空欄にプログラムが入れる ----
FALLBACK = "専門家が決めた範囲（メモ：{メモ}）を超えます。範囲まであと{範囲まで}mmです"
VALUES = {"メモ": "右に障害物", "範囲まで": "12"}


def test_fill_blanks_uses_program_values():
    t = "{範囲まで}mmまでなら、専門家が決めた範囲（メモ：{メモ}）の中です"
    assert u.fill_blanks(t, VALUES, FALLBACK) == "12mmまでなら、専門家が決めた範囲（メモ：右に障害物）の中です"


@pytest.mark.parametrize("template", [
    "範囲まであと15mmです",                  # 空欄以外に数字
    "範囲まであと十五mmです",                # 漢数字
    "{範囲まで}mm動かしても安全です",        # 断定の言葉
    "あと{のこり}mmです",                    # 知らない空欄
])
def test_fill_blanks_falls_back_to_fixed_sentence(template):
    out = u.fill_blanks(template, VALUES, FALLBACK)
    assert out == "専門家が決めた範囲（メモ：右に障害物）を超えます。範囲まであと12mmです"


def test_contains_forbidden():
    for w in ("安全です", "当たります", "干渉します"):
        assert u.contains_forbidden(f"これは{w}")
    assert not u.contains_forbidden("専門家が決めた範囲の中です")
