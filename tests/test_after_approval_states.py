"""受入テスト：承認のあとに3つの状態を並べて出す（台本 Ver.6.1 Scene 3-3・チャッピーの修正③。シクロの指示 2026-11-03）。
実装より先に書いた。表示だけで、安全の動き（承認・出力の条件）は変えない。

| 元の動作データ | 変わっていません |（サーバーが、元のバイト列が読み込んだときと同じかを確かめたときだけ。違えば行を出さずに理由）
| 変更候補 | 人が承認しました（シミュレーション用） |
| ロボットへの送信 | していません |
出力したあとは「シミュレーション用に出力しました（<ファイル名>。実機には入れないでください）」を足す。
文はプログラムの決まった文。「変更できた」とは言わない。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy"
DEMO_C = (DUMMY / "DEMO_C_PLACE.JBI").read_bytes()
NOT_SYNTHETIC = DEMO_C.replace(b"//NAME DEMO_C_PLACE", b"//NAME DEMO_C_PLACX", 1)
ROWS = [
    {"label": "元の動作データ", "value": "変わっていません"},
    {"label": "変更候補", "value": "人が承認しました（シミュレーション用）"},
    {"label": "ロボットへの送信", "value": "していません"},
]


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    from voice_teaching import sim
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "sim")
    return tmp_path / "sim"


def to_candidate(data=DEMO_C):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(data)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "Zマイナス3ミリ")
    assert r["state"]["candidate"] is not None and r["state"]["can_approve"]
    return w, sid, r["state"]


def test_not_shown_before_approval(out_dir):
    w, sid, st = to_candidate()
    assert st["after_approval"] is None
    assert w.state(sid)["state"]["after_approval"] is None


@pytest.mark.parametrize("data,exportable", [(DEMO_C, True), (NOT_SYNTHETIC, False)])
def test_three_rows_after_approval(data, exportable, out_dir):
    w, sid, _ = to_candidate(data)
    st = w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))["state"]
    assert st["approval"] is not None and st["exportable"] is exportable
    a = st["after_approval"]
    assert a["rows"] == ROWS and a["note"] is None and a["exported"] is None


def test_exported_line_after_export(out_dir):
    w, sid, _ = to_candidate()
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    r = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert r["ok"] is True
    a = w.state(sid)["state"]["after_approval"]
    assert a["rows"] == ROWS
    assert a["exported"] == f"シミュレーション用に出力しました（{r['file_name']}。実機には入れないでください）"


def test_real_job_cannot_export_so_no_exported_line(out_dir):
    w, sid, _ = to_candidate(NOT_SYNTHETIC)
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid)).get("ok") is not True
    a = w.state(sid)["state"]["after_approval"]
    assert a["rows"] == ROWS and a["exported"] is None


@pytest.mark.parametrize("cancel", ["undo", "new_instruction", "select"])
def test_gone_when_approval_is_cancelled(cancel, out_dir):
    w, sid, _ = to_candidate()
    assert w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))["state"]["after_approval"] is not None
    if cancel == "undo":
        st = w.undo(sid)["state"]
    elif cancel == "new_instruction":
        st = w.hear(sid, "Zマイナス2ミリ")["state"]
    else:
        st = w.select(sid, "C00004")["state"]
    assert st["approval"] is None and st["after_approval"] is None


def test_original_row_is_not_shown_when_it_cannot_be_confirmed(out_dir):
    # 元のバイト列が読み込んだときと同じだと確かめられなければ、「変わっていません」を出さずに理由を出す
    w, sid, _ = to_candidate()
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    e = w._entries[sid]
    e.session._job = jbi.parse_bytes(DEMO_C.replace(b"C00005=", b"C00005=1", 1))  # 元のジョブが変わった場合の代わり
    a = w.state(sid)["state"]["after_approval"]
    assert [r["label"] for r in a["rows"]] == ["変更候補", "ロボットへの送信"] and a["note"] == flow_mod.ORIGINAL_UNKNOWN_NOTE


def test_original_row_uses_loaded_bytes(out_dir, monkeypatch):
    w, sid, _ = to_candidate()
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    w._entries[sid].loaded_sha256 = "0" * 64  # 読み込んだときの値と合わない
    a = w.state(sid)["state"]["after_approval"]
    assert [r["label"] for r in a["rows"]] == ["変更候補", "ロボットへの送信"]
    assert a["note"] == flow_mod.ORIGINAL_UNKNOWN_NOTE


def test_every_dummy_sample_renders_back_to_the_loaded_bytes():
    # 「変わっていません」は、読み込んだバイト列と、元のジョブを書き戻したバイト列が同じときだけ。合成ジョブで同じになること
    for p in DUMMY.glob("*.JBI"):
        try:
            job = jbi.parse_bytes(p.read_bytes())
        except jbi.JbiError:
            continue  # 読み込まないジョブ（書き戻すと変わるもの）
        if not job.problems:
            assert job.render_bytes() == p.read_bytes(), p.name


def test_fixed_texts_do_not_claim_change_or_safety(out_dir):
    w, sid, _ = to_candidate()
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    a = w.state(sid)["state"]["after_approval"]
    text = "".join(r["label"] + r["value"] for r in a["rows"]) + (a["exported"] or "")
    for word in ("変更できました", "変更しました", "安全", "当たりません", "干渉しません", "送信しました", "転送しました"):
        assert word not in text, word
