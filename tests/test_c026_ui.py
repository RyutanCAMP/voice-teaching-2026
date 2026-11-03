"""CHECKER_026 の M1・S-MOVJ・S5 と、上下の向きの状態の表示（画面側）の受入テスト。実装より先に書いた。

- S-MOVJ：図のデータの区間に種類（kind）を入れる。MOVL（PL・CR なし、またはちょうど PL=0）は "movl"、
  MOVJ は "movj"、それ以外は "other"。関節移動の実際の通り道はジョブの点だけでは分からないので、
  画面では MOVJ の区間を直線移動のように見せない（見た目を分ける・再生の印を直線の上で動かさない）。
- 画面の動き（見た目・再生・はみ出し・見出し）は tools/e2e_browser.py の check_c026 で確かめる。
- 文を作る関数（区間の見た目・確認表示の見出し・上下の向きの状態）は tests/highlight.test.mjs で確かめる。
- ダミーは samples/dummy の架空のジョブ（PAT_MOVJ・PAT_PL0・DEMO_PLACE）。
"""
import re
from pathlib import Path

import pytest

from voice_teaching import jbi
from voice_teaching.web import api

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ROOT / "samples" / "dummy"
STATIC = ROOT / "voice_teaching" / "web" / "static"
ASSERTIVE = ["安全です", "当たります", "当たりません", "干渉します", "干渉しません", "問題ありません", "大丈夫です", "安全な"]


def read(name: str) -> bytes:
    return (DUMMY / name).read_bytes()


def text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def segs_by_to(data: bytes) -> dict:
    view = api.job_view(data)
    assert view["ok"] is True, view
    return {s["to"]: s for s in view["segments"]}


# ---- S-MOVJ：区間の種類 ----
def test_movj_segment_is_kind_movj_and_not_solid():
    segs = segs_by_to(read("PAT_MOVJ.JBI"))
    j = segs["C00006"]  # MOVJ C00006 VJ=25.00（C00005 から）
    assert j["kind"] == "movj"
    assert j["solid"] is False
    assert j["from"] == "C00005" and j["text"] == "MOVJ C00006 VJ=25.00"
    for name in ["C00001", "C00002", "C00003", "C00004", "C00005"]:
        assert segs[name]["kind"] == "movl", name
        assert segs[name]["solid"] is True, name


def test_pl0_movl_is_straight_kind_movl_and_solid():
    segs = segs_by_to(read("PAT_PL0.JBI"))
    for name in ["C00004", "C00005"]:  # MOVL … V=80.0 PL=0
        assert segs[name]["text"].endswith(" PL=0")
        assert segs[name]["kind"] == "movl", name
        assert segs[name]["solid"] is True, name


@pytest.mark.parametrize("old,new", [
    (b"MOVL C00003 V=250.0", b"MOVL C00003 V=250.0 PL=1"),     # PL>0：角を内回りする
    (b"MOVL C00003 V=250.0", b"MOVL C00003 V=250.0 PL=00"),    # 緩い書き方の PL
    (b"MOVL C00003 V=250.0", b"MOVL C00003 V=250.0 CR=10.0"),  # CR
    (b"MOVL C00003 V=250.0", b"MOVC C00003 V=250.0"),          # 円弧
    (b"MOVL C00003 V=250.0", b"MOVS C00003 V=250.0"),          # スプライン
    (b"MOVL C00003 V=250.0", b"MOVL C00003 V=250.0 ACC=50"),   # 知らない語
])
def test_other_moves_are_kind_other_and_dashed(old, new):
    data = read("DEMO_PLACE.JBI")
    assert old in data
    s = segs_by_to(data.replace(old, new))["C00003"]
    assert s["kind"] == "other"
    assert s["solid"] is False


def test_movj_with_extra_words_is_still_movj():
    # MOVJ に PL などが付いていても、関節移動であることは変わらない（直線に見せない）
    data = read("PAT_MOVJ.JBI").replace(b"MOVJ C00006 VJ=25.00", b"MOVJ C00006 VJ=25.00 PL=3")
    assert segs_by_to(data)["C00006"]["kind"] == "movj"


def test_lowercase_or_lookalike_is_not_movl():
    # 許可リストと同じく、大文字小文字やよく似た命令は MOVL として扱わない
    data = read("DEMO_PLACE.JBI").replace(b"MOVL C00003 V=250.0", b"SMOVL C00003 V=250.0")
    segs = {(s["from"], s["to"]): s for s in api.job_view(data)["segments"]}
    for s in segs.values():
        if s["to"] == "C00003":
            assert s["kind"] != "movl"


def test_every_segment_has_one_of_three_kinds():
    for f in sorted(DUMMY.glob("*.JBI")):
        view = api.job_view(f.read_bytes())
        if not view["ok"]:
            continue
        for s in view["segments"]:
            assert s["kind"] in ("movl", "movj", "other"), (f.name, s)
            assert s["solid"] is (s["kind"] == "movl"), (f.name, s)


def test_figure_data_and_after_segments_carry_kind():
    job = jbi.parse_bytes(read("PAT_MOVJ.JBI"))
    fig = api.figure_data(job)
    assert {s["to"]: s["kind"] for s in fig["segments"]}["C00006"] == "movj"
    # 変更のプレビュー（変更後の区間）にも種類が入る。C00003 は前後が MOVL で、今の検査で変更できる位置
    res = api.preview_shift(read("PAT_MOVJ.JBI"), "C00003", "Z", 5)
    assert res["ok"] is True, res
    kinds = {s["to"]: s["kind"] for s in res["after_segments"]}
    assert kinds["C00006"] == "movj" and kinds["C00003"] == "movl"


def test_movj_job_is_not_a_real_job():
    # 図の確認に使う MOVJ のジョブは架空のもの（samples/dummy）
    assert b"SYNTHETIC TEST DATA - NOT A REAL ROBOT JOB" in read("PAT_MOVJ.JBI")


# ---- S-MOVJ：画面のファイル ----
def test_screen_uses_segment_kind_not_only_solid():
    app, v3 = text("app.js"), text("view3d.js")
    for js in (app, v3):
        assert "segmentLook" in js            # 見た目は1か所（highlight.js）で決める
        assert "s.solid" not in js            # 実線かどうかだけで描き分けない
    hl = text("highlight.js")
    assert "関節移動（MOVJ）：実際の通り道は直線ではありません" in hl


def test_legend_names_movl_and_movj():
    h = text("index.html")
    assert "MOVL：直線移動" in h
    assert "MOVJ：関節移動（点線は順番だけ。実際の通り道ではありません）" in h  # QA P2：点線が引かれていることと矛盾しない言い方


def test_engineer_note_does_not_say_only_plain_movl_is_solid():
    h = text("index.html")
    assert "実線は MOVL（PL・CRなし）の区間" not in h  # PL=0 の MOVL も実線になった
    assert "MOVJ" in h[h.index("<h3>図と再生</h3>"):h.index('<div class="figs-small">')]


# ---- M1：上の帯の CSS ----
def test_brand_can_wrap():
    css = text("style.css")
    m = re.findall(r"\.brand\s*\{([^}]*)\}", css)
    joined = " ".join(m)
    assert "min-width: 0" in joined and "flex-wrap: wrap" in joined


# ---- 上下の向きの状態（M2 の画面側） ----
def test_updown_status_slot_is_near_position_and_quiet():
    h = text("index.html")
    assert 'id="updown-status"' in h
    # 位置の欄（音声で指示のカードの位置の札）の近くに置く
    i, j = h.index('id="voice-position"'), h.index('id="updown-status"')
    assert 0 < j - i < 400
    assert 'id="updown-status" class="updown fine" hidden' in h  # 最初は出さない（サーバーが返したときだけ）


def test_no_assertive_words_in_new_texts():
    for name in ["app.js", "view3d.js", "highlight.js", "index.html", "style.css"]:
        t = text(name)
        for w in ASSERTIVE:
            assert w not in t, (name, w)
