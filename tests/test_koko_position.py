"""「ここの位置を3ミリ下げて」（唐川さんの実マイク 11/3。11/4 の指示）：3D で選んだ位置を指し、上下の設定と変えてよいかを確かめる。

- 「ここの位置」「ここの点」「そこの位置」「そこの点」は、選んでいる位置を指す言葉（知らない言葉にしない）
- 上下の向きが未確認なら、「下げて」では候補にしない（Zプラス・Zマイナスで言ってもらう）。安全の決まりは緩めない
- 位置を選んでいなければ、どの位置かを聞き返す。変えられない位置（2つの命令が使う位置など）は、候補にせず理由を出す
- 「〜てくれる」「〜てもらえる」は、お願いとも質問とも読めるので、「〜ます」と同じく2択で確かめる（候補にしない）
- 「3mm下げる」は明確な指示として受け付け、「c00004」は C00004 として扱う（唐川さんの決定 11/4）
- どの道でも、承認と出力はボタンだけ（声・文字では行わない）
"""
from pathlib import Path

import pytest

import test_claude_talk as T
from voice_teaching import jbi, updown
from voice_teaching import utterance as ut

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy"
DEMO_C = (DUMMY / "DEMO_C_PLACE.JBI").read_bytes()


def config_c(tmp_path) -> Path:
    """DEMO_C_PLACE の座標系で、上下の向きの仮の設定（テストの一時フォルダだけ。実機で確かめたものではない）。"""
    obj = updown.build_config(jbi.load(DUMMY / "DEMO_C_PLACE.JBI"), updown.DEFAULT_ROBOT, "テスト（一時フォルダの仮の設定）",
                              "テスト用の仮の設定。実機で確かめたものではない")
    return Path(updown.write_config(obj, tmp_path / "updown_c.json", overwrite=True))


def start(tmp_path, data=DEMO_C, confirmed=True):
    w, f = T.flow(tmp_path, updown_config=config_c(tmp_path) if confirmed else None)
    return w, f, w.load(data)["session_id"]


@pytest.mark.parametrize("text", ["ここの位置を3ミリ下げて", "ここの点を3ミリ下げて", "そこの位置を3ミリ下げて", "この位置を3ミリ下げて",
                                  "ここを3mm下げる"])
def test_koko_refers_to_the_selected_position(tmp_path, text):
    w, f, sid = start(tmp_path)
    w.select(sid, "C00005")
    r = w.talk(sid, text, "voice")
    c = r["state"]["candidate"]
    assert r["via"] == ["hear"] and c is not None and "C00005" in str(c)
    assert r["state"]["approval"] is None and not r["state"]["exported"]  # 候補だけ。承認はボタン
    assert len(f.requests) == 0  # Claude を呼ばない（プログラムが読める指示）


def test_koko_is_not_an_unknown_word():
    f = ut.read_utterance("ここの位置を3ミリ下げて")
    assert f.refers_to_selection and f.unknown_words == ()


def test_koko_without_updown_confirmation_asks_for_z(tmp_path):
    w, f, sid = start(tmp_path, confirmed=False)
    w.select(sid, "C00005")
    r = w.talk(sid, "ここの位置を3ミリ下げて", "voice")
    assert r["state"]["candidate"] is None and "上下の向きがまだ確認されていません" in str(r["hear"])


def test_koko_without_selection_asks_which_position(tmp_path):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, "ここの位置を3ミリ下げて", "voice")
    assert r["state"]["candidate"] is None


def test_koko_on_a_shared_position_is_reference_only_and_cannot_be_approved(tmp_path):
    # DEMO_BUTTON_REPEAT の C00001 は 21・24行目の2つの命令が共用：参考の表示だけ（P1-2）。承認できない
    w, f = T.flow(tmp_path, updown_config=T.confirmed_config(tmp_path))
    sid = w.load(T.demo_bytes())["session_id"]
    w.select(sid, "C00001")
    r = w.talk(sid, "ここの位置を3ミリ下げて", "voice")
    c = r["state"]["candidate"]
    assert c is not None and c["blocked_reason"] and "共用" in c["blocked_reason"]
    assert r["state"]["approval"] is None and not r["state"]["can_export"]
    a = w.approve(sid, "テスト")
    assert not a.get("ok", False) or a["state"]["approval"] is None
    assert w.state(sid)["state"]["approval"] is None and not w.state(sid)["state"]["exported"]


@pytest.mark.parametrize("text", ["ここの位置を3ミリ下げてくれる", "ここの位置を3ミリ下げてもらえる", "ここの位置を3ミリ下げてくれますね"])
def test_kureru_is_confirmed_by_two_choices(tmp_path, text):
    w, f, sid = start(tmp_path)
    w.select(sid, "C00005")
    r = w.talk(sid, text, "voice")
    assert r["state"]["candidate"] is None  # 2択で確かめる（候補にしない）


@pytest.mark.parametrize("text", ["ここの位置を3ミリ下げてくれない", "ここの位置を3ミリ下げてくれる？", "ここの位置ここの位置を3ミリ下げて"])
def test_kureru_negation_question_and_repeats_make_no_candidate(tmp_path, text):
    w, f, sid = start(tmp_path)
    w.select(sid, "C00005")
    assert w.talk(sid, text, "voice")["state"]["candidate"] is None


def test_lowercase_position_number_is_the_same_position(tmp_path):
    w, f, sid = start(tmp_path)
    r = w.talk(sid, "c00005をZマイナス3ミリ", "voice")
    assert r["state"]["candidate"] is not None and "C00005" in str(r["state"]["candidate"])
