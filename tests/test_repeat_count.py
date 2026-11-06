"""受入テスト：作業の回数を変える（「C00001からC00003の作業を2回から3回にして」）。

設計は docs/REPEAT_COUNT_DESIGN.md。デモ用ジョブは samples/dummy/DEMO_BUTTON_REPEAT.JBI（架空のジョブ）。
- 区間がジョブの中に続けてそのまま N 回書かれている形だけを数える。JUMP・ラベル・カウンタは扱わない
- 区間の中は「MOVL 位置 V=数値」（PL なし、またはちょうど PL=0。速度 0 は不可）だけ。ほかの行があれば止める
- 今が2回の区間を合計3回にする変更だけ（CURRENT_DECISIONS）。減らす変更は扱わない（唐川さんの決定 2026-11-03）
- 繰り返す範囲は、人がボタン（［この範囲を繰り返す］）で確定してから候補にする。声の「はい」では確定しない
- 承認は画面のボタンだけ。出力は合成ジョブだけ。元のジョブは変えない
- 検査を通ったことは、物理的な安全の保証ではない
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi, sim
from voice_teaching import repeat_count as rc
from voice_teaching import utterance as ut
from voice_teaching.session import Session
from voice_teaching.web import api
from voice_teaching.web import flow as flow_mod
from voice_teaching.web.flow import UPDOWN_CHANGED_APPROVE, WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_PATH = ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"
DEMO = DEMO_PATH.read_bytes()
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
SAY = "C00001からC00003の作業を2回から3回にして"
CYCLE = [b"MOVL C00001 V=150.0\r\n", b"MOVL C00002 V=30.0\r\n", b"MOVL C00003 V=150.0\r\n"]
FIXED_WARNINGS = ("同じ動きを1回ぶん足します。新しい位置・新しい移動の線は作りません",
                  "サイクルの時間が長くなります",
                  "検査を通ったことは、物理的な安全の保証ではありません")
SCOPE = "繰り返すのは移動の行だけです。信号（DOUT）や待ち時間（TIMER）を含む作業は、このプロトタイプでは対象外です"


def lines(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


FIRST = lines(DEMO).index(CYCLE[0])              # 1回目の最初の行（0 から）
END = FIRST + 6                                  # 2回目の次の行（0 から）


def inst_start(data: bytes) -> int:
    return [x.rstrip(b"\r\n") for x in lines(data)].index(b"//INST")


def demo_job() -> jbi.Job:
    return jbi.parse_bytes(DEMO)


def one_cycle() -> bytes:
    """区間が1回だけのジョブ（2回目の3行を消した版。テストの中だけで作る）。"""
    ls = lines(DEMO)
    return b"".join(ls[:FIRST + 3] + ls[END:])


def variant(*pairs: tuple[bytes, bytes], count: int = 1) -> bytes:
    data = DEMO
    for old, new in pairs:
        assert old in data, old
        data = data.replace(old, new, count)
    return data


def insert_after(data: bytes, nth_line: int, text: bytes) -> bytes:
    ls = lines(data)
    ls.insert(nth_line, text)
    return b"".join(ls)


class Counting:
    """解釈の係を呼んだ回数を数える（回数の変更では呼ばない）。"""
    kind, model, locator_label = "mock", None, "モック（テスト用）"

    def __init__(self):
        self.inner = ci.MockInterpreter()
        self.notice = self.inner.notice
        self.decides = 0
        self.locates = 0

    def decide(self, *a, **k):
        self.decides += 1
        return self.inner.decide(*a, **k)

    def locate(self, *a, **k):
        self.locates += 1
        return self.inner.locate(*a, **k)


@pytest.fixture
def counting():
    return Counting()


def start(interp=None, data=DEMO, select="C00001", **kw):
    w = WebFlow(interpreter=interp or ci.MockInterpreter(), **kw)
    r = w.load(data)
    assert r["ok"], r
    sid = r["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid


def cand(r):
    return r["state"]["candidate"]


def rng(r):
    return r["state"]["repeat_range"]


def to_candidate(w, sid, text=SAY):
    """指示 → 範囲の確認 →［この範囲を繰り返す］のボタン。"""
    r = w.hear(sid, text)
    assert r["status"] == "ask" and rng(r) is not None and cand(r) is None, r
    r2 = w.repeat_choose(sid, "yes", rng(r)["turn"])
    assert r2["status"] == "ok" and r2["candidate_made"] is True, r2
    return r2


# ======================================================================
# 1. デモ用ジョブ
# ======================================================================
def test_demo_job_is_synthetic_ascii_crlf_without_dout_or_wait():
    assert DEMO.decode("ascii")
    assert all(x.endswith(b"\r\n") for x in lines(DEMO))
    assert b"SYNTHETIC DEMO JOB - NOT A REAL ROBOT JOB" in DEMO
    assert b"DOUT" not in DEMO and b"WAIT" not in DEMO and b"TIMER" not in DEMO
    job = demo_job()
    assert job.problems == [] and job.name == "DEMO_BUTTON_REPEAT" and job.is_robot_coordinates()
    assert [i.text for i in job.instructions if i.command == "MOVJ"] == ["MOVJ C00000 VJ=25.00", "MOVJ C00004 VJ=25.00"]
    assert sim._is_synthetic(DEMO)


def test_demo_has_the_cycle_written_twice():
    b = demo_job().find_repeat("C00001", "C00003")
    assert b.positions == ("C00001", "C00002", "C00003") and b.count == 2
    assert [x.encode() for x in b.cycle_lines] == CYCLE and b.first_line == FIRST
    assert [x.positions for x in demo_job().find_repeat_blocks()] == [("C00001", "C00002", "C00003")]


def test_docs_explain_the_demo_and_what_is_out_of_scope():
    for f in (ROOT / "samples" / "dummy" / "PATTERNS.md", ROOT / "docs" / "REPEAT_COUNT_DESIGN.md"):
        text = f.read_text(encoding="utf-8")
        assert "DEMO_BUTTON_REPEAT.JBI" in text and SCOPE in text, f


def test_view_shows_the_count_from_load():
    v = api.job_view(DEMO)
    assert v["repeat_blocks"] == [{"target": "C00001〜C00003", "positions": ["C00001", "C00002", "C00003"], "count": 2}]
    assert api.job_view(DEMO_C)["repeat_blocks"] == []


# ======================================================================
# 2. 2回 → 3回 の候補（jbi）
# ======================================================================
def test_two_to_three_adds_exactly_one_cycle_after_the_last():
    job = demo_job()
    p = job.plan_repeat("C00001", "C00003", 3)
    assert p.kind == "repeat" and p.target == "C00001〜C00003" and (p.before_count, p.after_count) == (2, 3)
    old, new = lines(DEMO), lines(p.new_job.render_bytes())
    assert len(new) == len(old) + 3
    assert new[:END] == old[:END] and new[END + 3:] == old[END:] and new[END:END + 3] == CYCLE
    assert list(p.added) == [(END, "MOVL C00001 V=150.0"), (END + 1, "MOVL C00002 V=30.0"), (END + 2, "MOVL C00003 V=150.0")]
    assert p.removed == ()
    k = inst_start(DEMO)
    assert new[:k + 1] == old[:k + 1]
    assert {n: q.values for n, q in p.new_job.positions.items()} == {n: q.values for n, q in job.positions.items()}
    assert p.new_job.problems == [] and p.new_job.find_repeat("C00001", "C00003").count == 3
    assert job.render_bytes() == DEMO and DEMO_PATH.read_bytes() == DEMO
    assert tuple(p.warnings[:3]) == FIXED_WARNINGS and SCOPE in p.warnings


@pytest.mark.parametrize("total,word", [
    (2, "同じ"), (1, "減らす変更は、このプロトタイプでは扱いません"), (0, "減らす変更"), (-1, "減らす変更"),
    (4, "このプロトタイプでは、2回から3回への変更だけを扱います"), (5, "このプロトタイプでは、2回から3回への変更だけを扱います"), (6, "このプロトタイプでは、2回から3回への変更だけを扱います"), (99, "このプロトタイプでは、2回から3回への変更だけを扱います"),
])
def test_bad_totals_are_refused(total, word):
    with pytest.raises(jbi.JbiError) as e:
        demo_job().plan_repeat("C00001", "C00003", total)
    assert word in str(e.value)


def test_three_to_two_is_refused_decrease_is_not_handled():
    three = demo_job().plan_repeat("C00001", "C00003", 3).new_job
    with pytest.raises(jbi.JbiError) as e:
        three.plan_repeat("C00001", "C00003", 2)
    assert "減らす変更は、このプロトタイプでは扱いません" in str(e.value)


@pytest.mark.parametrize("total", [True, 3.0, "3", None, float("nan")])
def test_total_must_be_a_plain_int(total):
    with pytest.raises(jbi.JbiError):
        demo_job().plan_repeat("C00001", "C00003", total)


def test_one_cycle_cannot_become_two():
    one = jbi.parse_bytes(one_cycle())
    assert one.problems == [] and one.find_repeat("C00001", "C00003").count == 1
    with pytest.raises(jbi.JbiError) as e:
        one.plan_repeat("C00001", "C00003", 2)
    assert "2回以上" in str(e.value) and "新しい移動の線" in str(e.value)
    assert one.find_repeat_blocks() == []


@pytest.mark.parametrize("cycles,total", [(3, 4), (4, 5), (3, 5)])
def test_only_two_to_three_is_handled(cycles, total):
    # S-P1-1：今が3回・4回のジョブ（テストの中で作る）で「3回から4回」「4回から5回」は止める
    ls = lines(DEMO)
    job = jbi.parse_bytes(b"".join(ls[:END] + CYCLE * (cycles - 2) + ls[END:]))
    assert job.find_repeat("C00001", "C00003").count == cycles
    with pytest.raises(jbi.JbiError) as e:
        job.plan_repeat("C00001", "C00003", total)
    assert "このプロトタイプでは、2回から3回への変更だけを扱います" in str(e.value)
    w, sid = start(data=job.render_bytes())
    r = w.hear(sid, rc.canonical("C00001", "C00003", cycles, total))
    assert r["status"] == "stop" and r["choices"] == [] and rng(r) is None and "このプロトタイプでは、2回から3回への変更だけを扱います" in r["message"]


# ======================================================================
# 3. 区間の中・区間と区間の間のほかの行 → 止める（理由の文）
# ======================================================================
MOVL2 = b"MOVL C00002 V=30.0\r\n"


@pytest.mark.parametrize("name,data,word", [
    ("DOUT（区間の中）", insert_after(DEMO, lines(DEMO).index(MOVL2), b"DOUT OT#(1) ON\r\n"), "DOUT"),
    ("DOUT（区間と区間の間）", insert_after(DEMO, FIRST + 3, b"DOUT OT#(1) ON\r\n"), "DOUT"),
    ("TIMER", insert_after(DEMO, lines(DEMO).index(MOVL2) + 1, b"TIMER T=0.50\r\n"), "TIMER"),
    ("コメント", insert_after(DEMO, FIRST + 3, b"'PUSH AGAIN\r\n"), "コメント"),
    ("MOVJ", variant((b"MOVL C00002 V=30.0", b"MOVJ C00002 VJ=10.00"), count=2), "MOVJ"),
    ("PL=1", variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=30.0 PL=1"), count=2), "PL=1"),
    ("CR", variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=30.0 CR=5.0"), count=2), "CR"),
    ("速度 0", variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=0.0"), count=2), "速度が 0"),
    ("別の速度", variant((b"MOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=100.0\r\nMOVJ")), "同じではありません"),
    ("PL=0 の有無が違う", variant((b"MOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=150.0 PL=0\r\nMOVJ")),
     "同じではありません"),
    ("区間の外の位置への移動", insert_after(DEMO, FIRST + 3, b"MOVL C00004 V=150.0\r\n"), "C00004"),
])
def test_other_lines_inside_or_between_cycles_stop(name, data, word):
    job = jbi.parse_bytes(data)
    with pytest.raises(jbi.JbiError) as e:
        job.plan_repeat("C00001", "C00003", 3)
    msg = str(e.value)
    assert word in msg, (name, msg)
    assert "複写しません" in msg or "扱いません" in msg or "同じではありません" in msg, (name, msg)


def test_dout_inside_says_so_plainly():
    data = insert_after(DEMO, lines(DEMO).index(MOVL2), b"DOUT OT#(1) ON\r\n")
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)
    assert "区間の中に DOUT があるため、複写しません" in str(e.value)


def test_movj_before_the_block_using_a_block_position_names_the_real_line():
    # QA P2-7：区間の前の MOVJ が区間の位置を使う
    data = variant((b"MOVJ C00000 VJ=25.00", b"MOVJ C00001 VJ=25.00"))
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)
    assert "区間の位置 C00001 が、区間の外の MOVJ でも使われているため" in str(e.value)


def test_wait_stops_because_it_is_not_in_the_allow_list():
    data = insert_after(DEMO, lines(DEMO).index(MOVL2), b"WAIT IN#(1)=ON\r\n")
    job = jbi.parse_bytes(data)
    assert job.problems
    with pytest.raises(jbi.JbiError) as e:
        job.plan_repeat("C00001", "C00003", 3)
    assert "WAIT" in str(e.value)


@pytest.mark.parametrize("data,start,end,word", [
    (DEMO, "C00001", "C00009", "ありません"),
    (DEMO, "C00003", "C00001", "始まり"),
    (DEMO, "C00001", "C00001", "始まり"),
    (DEMO, "C00001", "C00002", "C00003"),
    (DEMO, "C00000", "C00002", "MOVJ"),
    (variant((b"MOVL C00002 V=30.0\r\nMOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=150.0\r\nMOVL C00002 V=30.0\r\nMOVJ")),
     "C00001", "C00003", "並び"),
])
def test_no_matching_block_stops(data, start, end, word):
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat(start, end, 3)
    assert word in str(e.value)


def test_jump_and_label_style_loops_are_not_handled():
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(variant((b"NOP\r\n", b"NOP\r\n*L1\r\n"))).plan_repeat("C00001", "C00003", 3)
    assert "JUMP" in str(e.value)


def test_job_with_problems_is_refused():
    data = variant((b"MOVJ C00004 VJ=25.00", b"ZZFAKE T=1.00\r\nMOVJ C00004 VJ=25.00"))
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)
    assert "読めない所" in str(e.value)


def test_non_robot_position_is_refused():
    data = variant((b"///POSTYPE ROBOT\r\n///RECTAN\r\n", b"///POSTYPE PULSE\r\n///PULSE\r\n"))
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)
    assert "ロボット座標" in str(e.value)


# ======================================================================
# 4. 言葉の読み取り（repeat_count。Claude には頼まない）
# ======================================================================
@pytest.mark.parametrize("text,start,end,said,total", [
    (SAY, "C00001", "C00003", 2, 3),
    ("C00001〜C00003を3回にして", "C00001", "C00003", None, 3),
    ("C00001～C00003の作業を3回にしてください", "C00001", "C00003", None, 3),
    ("えーと、C00001からC00003を2回から3回に変更して", "C00001", "C00003", 2, 3),
    ("ｃ００００１からｃ００００３の作業を２回から３回にして", "C00001", "C00003", 2, 3),
    # Safety P2-3：短い名前（範囲はボタンで人が確定する）
    ("C1からC3を3回", "C00001", "C00003", None, 3), ("C1〜C3を3回にして", "C00001", "C00003", None, 3),
    ("C001からC003を3回にして", "C00001", "C00003", None, 3),
    # QA P1-1・P1-3：範囲を言わない・自然な言い方・漢数字とかな
    ("3回にして", None, None, None, 3), ("作業を3回にして", None, None, None, 3), ("2回から3回", None, None, 2, 3),
    ("2回から3回にして", None, None, 2, 3), ("3回に増やして", None, None, None, 3), ("3回にする", None, None, None, 3),
    ("3回にしたい", None, None, None, 3), ("3回にしてほしい", None, None, None, 3),
    ("C00001からC00003を3回繰り返して", "C00001", "C00003", None, 3), ("C00001からC00003を3回やって", "C00001", "C00003", None, 3),
    ("三回にして", None, None, None, 3), ("二回から三回", None, None, 2, 3), ("さんかいにして", None, None, None, 3),
    ("3かいにして", None, None, None, 3), ("2回から三回にして", None, None, 2, 3),
])
def test_reads_the_fixed_forms(text, start, end, said, total):
    assert rc.is_repeat_request(text), text
    r = rc.read(text)
    assert r is not None, text
    assert (r.start, r.end, r.said_count, r.total, r.add, r.one_more) == (start, end, said, total, None, False)


def test_one_more():
    r = rc.read("もう1回ふやして")
    assert r.one_more and r.total is None


@pytest.mark.parametrize("text", ["C00001からC00002を2回移動させて", "2回移動させて", "下に3mm", "C00001を選択して",
                                  "C00001とC00002を2往復させて", "3回下に3mm"])
def test_other_sentences_are_not_repeat_requests(text):
    assert not rc.is_repeat_request(text)


@pytest.mark.parametrize("text,n", [("C00001からC00003を3回追加して", 3), ("C00001〜C00003を1回ふやして", 1),
                                     ("C00001からC00003の作業を2回増やして", 2), ("3回追加して", 3)])
def test_add_words_are_read_as_ambiguous(text, n):
    r = rc.read(text)
    assert r.add == n and r.total is None


def test_canonical_text_reads_back():
    t = rc.canonical("C00001", "C00003", 2, 3)
    assert t == SAY
    r = rc.read(t)
    assert (r.start, r.end, r.said_count, r.total) == ("C00001", "C00003", 2, 3)


# ======================================================================
# 5. 範囲の確認（ボタンで確定する。唐川さんの決定 2026-11-03）
# ======================================================================
def test_range_is_shown_then_button_makes_the_candidate(counting):
    w, sid = start(counting)
    r = w.hear(sid, SAY, source="voice")
    assert r["status"] == "ask" and cand(r) is None and r["choices"] == [] and counting.decides == 0
    rr = rng(r)
    assert rr["target"] == "C00001〜C00003" and rr["positions"] == ["C00001", "C00002", "C00003"]
    assert rr["line_nos"] == [FIRST + 1, FIRST + 2, FIRST + 3] and rr["block_line_nos"] == list(range(FIRST + 1, END + 1))
    assert rr["count"] == 2 and rr["total"] == 3
    assert (rr["yes_label"], rr["no_label"]) == ("この範囲を繰り返す", "違う") and rr["note"] == SCOPE
    ranges = (f"1回目：{FIRST + 1}〜{FIRST + 3}行目（MOVL C00001 V=150.0・MOVL C00002 V=30.0・MOVL C00003 V=150.0）"
              f"／2回目：{FIRST + 4}〜{END}行目／全体：{FIRST + 1}〜{END}行目（2回）")
    assert [list(x) for x in rr["cycles"]] == [[FIRST + 1, FIRST + 3], [FIRST + 4, END]]
    assert r["message"] == f"この範囲（{ranges}）を繰り返しますか？ 今は2回続けて書かれています。合計3回にします"
    r2 = w.repeat_choose(sid, "yes", rr["turn"])
    assert r2["candidate_made"] and cand(r2)["kind"] == "repeat" and rng(r2) is None and counting.decides == 0
    assert f"変更前の範囲 {ranges}" in r2["state"]["confirmation_text"]  # 確認表示にも、1回目・2回目・全体の範囲


@pytest.mark.parametrize("word", ["はい", "そうそう", "この範囲を繰り返す", "OK", "うん", "繰り返して", "お願いします",
                                  "承認して", "この範囲を繰り返す。"])
@pytest.mark.parametrize("source", ["voice", "text"])
def test_voice_does_not_confirm_the_range_but_keeps_it(word, source, counting):
    # QA P1-A：範囲を決めようとする言葉は、確定せず、範囲の確認を残したまま「ボタンで決めてください」と案内する
    w, sid = start(counting)
    turn = rng(w.hear(sid, SAY))["turn"]
    r2 = w.hear(sid, word, source=source)
    guide = rc.RANGE_APPROVE_GUIDE if "承認" in word else rc.RANGE_BUTTON_ONLY
    assert r2["status"] == "ask" and r2["message"] == guide and r2["choices"] == []
    assert cand(r2) is None and rng(r2) is not None and rng(r2)["turn"] == turn
    assert r2["state"]["approval"] is None and counting.decides == 0
    r3 = w.repeat_choose(sid, "yes", turn)  # ボタンなら決まる
    assert r3["candidate_made"] and cand(r3)["kind"] == "repeat"


@pytest.mark.parametrize("text", ["違う", "やめて", "比較して", "C00001からC00003の作業を2回から3回にしないで", "3回にして"])
def test_other_words_during_the_range_drop_it(text):
    # 打ち消し・別の指示・別の回数の指示は、今どおり範囲の確認を捨てる（新しい回数の指示は、新しい範囲の確認になる）
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    r = w.hear(sid, text)
    assert rng(r) is None or rng(r)["turn"] != turn
    assert w.repeat_choose(sid, "yes", turn)["status"] == "stop"


def test_confirm_like_words_without_a_range_are_handled_as_before():
    w, sid = start()
    r = w.hear(sid, "はい")
    assert r["message"] != rc.RANGE_BUTTON_ONLY and cand(r) is None


def test_no_button_does_nothing():
    w, sid = start()
    r = w.hear(sid, SAY)
    r2 = w.repeat_choose(sid, "no", rng(r)["turn"])
    assert r2["status"] == "ok" and r2["message"] == rc.RANGE_NO_REPLY and cand(r2) is None and rng(r2) is None
    assert w.repeat_choose(sid, "yes", rng(r)["turn"])["status"] == "stop"


@pytest.mark.parametrize("act", ["select", "select_missing", "undo", "hear", "dialog", "compare", "explain"])
def test_range_is_dropped_by_other_actions(act):
    # S-P2-2：新しい操作（比べる・［説明です］・ジョブに無い位置の選択も含む）では、範囲の確認を捨てる
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    if act == "select":
        w.select(sid, "C00002")
    elif act == "select_missing":
        assert w.select(sid, "C00099")["status"] == "stop"
    elif act == "undo":
        w.undo(sid)
    elif act == "hear":
        w.hear(sid, "比較して")
    elif act == "compare":
        w.compare(sid)
    elif act == "explain":
        w.hear(sid, ut.EXPLAIN_CHOICE_TEXT)
    else:
        w.choose(sid, "other", 0)
    assert w.state(sid)["state"]["repeat_range"] is None
    r = w.repeat_choose(sid, "yes", turn)
    assert r["status"] == "stop" and cand(r) is None


@pytest.mark.parametrize("turn,choice", [(None, "yes"), (True, "yes"), ("1", "yes"), (99, "yes"), (1, "maybe"), (1, None)])
def test_bad_button_values_are_stale(turn, choice):
    w, sid = start()
    w.hear(sid, SAY)
    r = w.repeat_choose(sid, choice, turn)
    assert r["status"] == "stop" and cand(r) is None


def test_new_range_ask_gets_a_new_turn():
    w, sid = start()
    t1 = rng(w.hear(sid, SAY))["turn"]
    t2 = rng(w.hear(sid, SAY))["turn"]
    assert t2 != t1
    r = w.repeat_choose(sid, "yes", t1)
    # 古いボタンは断り、範囲の確認も捨てる（「もう使えません」の文と状態を合わせる。QA P2-E）
    assert r["status"] == "stop" and r["message"] == rc.RANGE_STALE and rng(r) is None
    assert w.repeat_choose(sid, "yes", t2)["status"] == "stop"
    t3 = rng(w.hear(sid, SAY))["turn"]
    assert w.repeat_choose(sid, "yes", t3)["candidate_made"]


def test_range_button_on_the_server_route():
    from voice_teaching.web import server
    src = Path(server.__file__).read_text(encoding="utf-8")
    assert '"/api/repeat": lambda r: flow.repeat_choose(' in src


# ======================================================================
# 6. 画面の流れ（WebFlow）：指示 → 範囲の確認 → 候補 → ボタン承認 → 出力
# ======================================================================
def test_end_to_end_two_to_three(counting):
    w, sid = start(counting)
    r = to_candidate(w, sid)
    assert counting.decides == 0 and r["tool_calls"] == []
    c = cand(r)
    assert c["kind"] == "repeat" and c["target"] == "C00001〜C00003"
    assert c["heading"] == "C00001〜C00003 の作業を 2回 → 3回 にします"
    assert (c["before_value"], c["after_value"]) == ("2", "3")
    assert [d["sign"] for d in c["diff"]] == ["+", "+", "+"]
    assert [d["text"] for d in c["diff"]] == ["MOVL C00001 V=150.0", "MOVL C00002 V=30.0", "MOVL C00003 V=150.0"]
    assert c["diff"][0]["line_no"] == END + 1
    for wtxt in FIXED_WARNINGS + (SCOPE,):
        assert wtxt in c["warnings"]
    assert c["heard"] == SAY
    st = r["state"]
    assert st["can_approve"] is True and st["approval"] is None and st["can_export"] is False
    assert "2回 → 3回" in st["confirmation_text"] and "承認は画面のボタン" in st["confirmation_text"]
    a = w.approve(sid, "e2e確認", candidate_gen=w.candidate_gen_of(sid))
    assert a["ok"] is True, a
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is True and x["file_name"] == "DEMO_BUTTON_REPEAT_SIM.JBI.txt", x
    out = lines(Path(x["path"]).read_bytes())
    old = lines(DEMO)
    assert out[1] == b"//NAME DEMO_BUTTON_REPEAT_SIM\r\n"
    assert out[:1] + out[2:] == old[:1] + old[2:END] + CYCLE + old[END:]
    assert DEMO_PATH.read_bytes() == DEMO


def test_range_less_sentence_on_the_demo_goes_to_the_range_button():
    # QA P1-1：範囲を言わなくても、区間が1つだけなら範囲の確認へ
    for text in ("3回にして", "作業を3回にして", "2回から3回", "2回から3回にして", "C1からC3を3回", "C001からC003を3回にして",
                 "C00001からC00003を3回繰り返して", "C00001からC00003を3回やって", "三回にして", "さんかいにして",
                 "3回に増やして", "もう1回ふやして", "C00001からC00003の作業を2回から3回にして？"):
        w, sid = start()
        r = w.hear(sid, text)
        assert r["status"] == "ask" and rng(r) is not None and rng(r)["total"] == 3, (text, r["message"])


def test_range_less_sentence_without_a_block_asks_for_the_range():
    w, sid = start(data=DEMO_C)
    r = w.hear(sid, "3回にして")
    # 区間が0件：範囲を言うよう案内し、対象外の文も添える（QA）
    # DEMO_C_PLACE には DOUT・TIMER があるので、対象外の文も添える（Safety P2-2）
    assert r["status"] == "ask" and r["message"] == f"{rc.NO_BLOCK}。{rc.RANGE_NEEDED}。{SCOPE}"
    assert rng(r) is None and cand(r) is None


def test_range_less_sentence_with_two_blocks_asks_for_the_range():
    # 区間が2つ以上あって決まらない（テストの中で作る：C00001〜C00003 ×2 のあとに、同じ形の C00005〜C00006 ×2）
    pos = b"C00005=500.000,0.000,300.000,180.00,0.00,0.00\r\nC00006=500.000,0.000,250.000,180.00,0.00,0.00\r\n"
    data = variant((b"///NPOS 5,0,0,0,0,0", b"///NPOS 7,0,0,0,0,0"),
                   (b"C00004=350.000,60.000,420.000,180.00,0.00,0.00\r\n",
                    b"C00004=350.000,60.000,420.000,180.00,0.00,0.00\r\n" + pos),
                   (b"MOVJ C00004 VJ=25.00", b"MOVL C00005 V=100.0\r\nMOVL C00006 V=100.0\r\n" * 2 + b"MOVJ C00004 VJ=25.00"))
    job = jbi.parse_bytes(data)
    assert job.problems == [] and len(job.find_repeat_blocks()) == 2
    w, sid = start(data=data)
    r = w.hear(sid, "3回にして")
    assert r["status"] == "ask" and r["message"] == rc.RANGE_NEEDED and rng(r) is None
    r2 = w.hear(sid, "C00005を3回にして")  # 位置を1つ言えば、その位置を含む区間だけ
    assert rng(r2) is not None and rng(r2)["target"] == "C00005〜C00006"


@pytest.mark.parametrize("text", ["3回", "3回お願いします", "C00001を3回", "C1を3回にして"])
def test_short_count_sentences_go_to_the_range_button(text):
    # QA：昔の2択［3回の移動］［3往復］に流さない
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "ask" and rng(r) is not None and rng(r)["total"] == 3, (text, r["message"])


def test_single_position_outside_any_block_asks_for_the_range():
    w, sid = start()
    r = w.hear(sid, "C00004を3回にして")
    assert r["status"] == "ask" and rng(r) is None and rc.RANGE_NEEDED in r["message"]


@pytest.mark.parametrize("text", ["六回にして", "十回にして", "ろっかいにして", "C00001からC00003を六回にして"])
def test_big_kanji_and_kana_counts_are_read_then_stopped(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and "2回から3回への変更だけ" in r["message"], (text, r["message"])
    assert "「から」" not in r["message"]


@pytest.mark.parametrize("text", ["C00001からC00003を−1回にして", "-1回にして", "マイナス1回にして"])
def test_negative_counts_stop(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["message"] == rc.NEGATIVE_MESSAGE, (text, r["message"])


def test_said_three_to_three_has_a_reason_and_one_choice():
    # 「3回から3回」：言った今の回数が違い、目標が3回 → ［合計3回にする］の1択（唐川さんの判断待ち。今のまま）
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を3回から3回にして")
    assert r["status"] == "ask" and r["message"] and [c["label"] for c in r["choices"]] == ["合計3回にする"]


def test_short_names_not_in_the_job_stop():
    w, sid = start()
    r = w.hear(sid, "C1からC9を3回にして")
    assert r["status"] == "stop" and "C00005" in r["message"] and "ありません" in r["message"]


def test_decimal_count_stops():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003を2.5回にして")
    assert r["status"] == "stop" and r["message"] == rc.DECIMAL_MESSAGE


def test_unknown_form_shows_the_example_first():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003のバイオを3回にして")
    assert r["status"] == "ask" and r["message"].startswith(rc.FORM_UNKNOWN) and "「バイオ」" in r["message"]
    assert r["choices"] == [] and rng(r) is None
    assert r["facts"]["unknown_words"] == ["バイオ"]


def test_heard_sentence_does_not_mark_the_fixed_words_as_unknown():
    w, sid = start()
    assert w.hear(sid, SAY)["facts"]["unknown_words"] == []


@pytest.mark.parametrize("text", ["はい", "承認して", "OK", "承認"])
def test_voice_yes_does_not_approve(text):
    w, sid = start()
    to_candidate(w, sid)
    r = w.hear(sid, text, source="voice")
    assert r["state"]["approval"] is None and r["state"]["can_export"] is False
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"] is False


def test_new_instruction_after_approval_cancels_it():
    w, sid = start()
    to_candidate(w, sid)
    assert w.approve(sid, "確認者", candidate_gen=w.candidate_gen_of(sid))["ok"]
    r = w.hear(sid, "比較して")
    assert r["state"]["approval"] is None and w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"] is False


def test_export_needs_approval(tmp_path):
    w, sid = start()
    to_candidate(w, sid)
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is False and "承認" in x["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_approve_needs_a_name():
    w, sid = start()
    to_candidate(w, sid)
    assert w.approve(sid, "  ", candidate_gen=w.candidate_gen_of(sid))["ok"] is False


def test_non_synthetic_job_is_never_exported(tmp_path):
    data = variant((b"//NAME DEMO_BUTTON_REPEAT", b"//NAME SOME_REAL_JOB"))
    assert not sim._is_synthetic(data)
    w, sid = start(data=data)
    to_candidate(w, sid)
    w.approve(sid, "確認者", candidate_gen=w.candidate_gen_of(sid))
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is False and "合成ジョブ" in x["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_undo_discards_and_keeps_the_original():
    w, sid = start()
    to_candidate(w, sid)
    assert cand(w.undo(sid)) is None
    assert w._entries[sid].session.original_bytes == DEMO


def test_pending_candidate_blocks_another_repeat_request(counting):
    w, sid = start(counting)
    first = cand(to_candidate(w, sid))
    assert first is not None
    # Q-3／F-2（11/6 唐川さんの方針）：新しい変更の指示では古い候補・承認を無効にする（前の期待：「承認待ち」で古い候補を残す）
    old_gen = w.candidate_gen_of(sid)
    # 仕様A（11/6 12:29 唐川さんの決定）：完全な言い直し・新しい指示は、古い候補・承認を失効したうえで、新しい未承認の候補を作る（前の期待：候補なし）
    # 回数の頼みは新しい指示としてふつうの道へ通し、いつもの範囲の確かめから始める（候補はまだ作らない。Claude は呼ばない）
    r = w.hear(sid, "C00001からC00003の作業を2回から3回にして")
    assert r["message"].startswith("言い直しを受け取ったため") and "取り消しました" in r["message"]
    assert "を繰り返しますか？" in r["message"] and not r["candidate_made"]
    assert rng(r) is not None and r["state"]["repeat_range"] is not None
    assert cand(r) is None and r["state"]["approval"] is None and counting.decides == 0
    assert r["state"]["can_approve"] is False and r["state"]["can_export"] is False
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"] is False
    assert w.approve(sid, "確認者", candidate_gen=old_gen)["ok"] is False
    assert w.export(sid, candidate_gen=old_gen)["ok"] is False


# ---- P1-5：回数の変更の承認でも、上下の向きの設定の変化・中身の食い違いで断る ----
def test_updown_change_blocks_repeat_approval(make_updown):
    path = make_updown("DEMO_BUTTON_REPEAT.JBI")
    w, sid = start(updown_config=path)
    to_candidate(w, sid)
    assert w._entries[sid].session.candidate_updown.confirmed  # 候補を作ったときは確認済み
    Path(path).write_bytes(Path(path).read_bytes() + b"\n")  # 設定が1バイトでも変われば、照合の結果が変わる
    a = w.approve(sid, "確認者", candidate_gen=w.candidate_gen_of(sid))
    assert a["ok"] is False and a["error"] == UPDOWN_CHANGED_APPROVE
    assert w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"] is False


def test_repeat_approval_refuses_when_the_rebuilt_bytes_differ(monkeypatch):
    w, sid = start()
    to_candidate(w, sid)
    real = sim.simulate_repeat

    def tampered(job, start_, end_, total):
        sp = real(job, start_, end_, total)
        return sim.SimRepeatProposal(**{**sp.__dict__, "new_bytes": sp.new_bytes + b"X"})
    monkeypatch.setattr(flow_mod.sim, "simulate_repeat", tampered)
    a = w.approve(sid, "確認者", candidate_gen=w.candidate_gen_of(sid))
    assert a["ok"] is False and "合わない" in a["error"]


# ---- 聞き返し・止める（P1-2・Safety P2-1・P2-2：押せば必ず止まる・逆向きの選択肢は出さない） ----
def test_said_count_differs_from_the_job(counting):
    # S-P2-1：目標が3回で、今の回数の言い違いだけなら［合計3回にする］の1択
    w, sid = start(counting)
    r = w.hear(sid, "C00001からC00003の作業を1回から3回にして")
    assert r["status"] == "ask" and cand(r) is None and counting.decides == 0
    assert r["message"] == "このジョブでは、C00001〜C00003 は今 2 回です。何回にしますか？"
    assert [(c["label"], c["text"]) for c in r["choices"]] == [("合計3回にする", SAY)]


@pytest.mark.parametrize("text", ["C00001からC00003の作業を1回から2回にして", "C00001からC00003の作業を5回から6回にして",
                                  "C00001からC00003の作業を3回から4回にして"])
def test_said_count_and_target_both_differ_stop_without_choices(text):
    # S-P2-1：言った目標と違う回数は選択肢に出さない。目標が3でなければ止める
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and rng(r) is None and "今 2 回" in r["message"]
    assert "このプロトタイプでは、2回から3回への変更だけを扱います" in r["message"]


@pytest.mark.parametrize("text", ["C00001からC00003の作業を3回から2回にして", "C00001からC00003の作業を2回から1回にして",
                                  "C00001からC00003を1回にして"])
def test_decrease_is_stopped_without_choices(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None and rng(r) is None
    assert "減らす変更は、このプロトタイプでは扱いません" in r["message"]


@pytest.mark.parametrize("text,labels,totals", [
    ("C00001からC00003を3回追加して", ["合計3回にする"], [3]),           # 2+3=5 は ±1 でないので出さない
    ("C00001からC00003を1回増やして", ["1回足して合計3回にする"], [3]),  # 合計1回（減らす）は出さない
    ("3回追加して", ["合計3回にする"], [3]),
])
def test_add_words_ask_only_choices_that_would_work(text, labels, totals):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None and "今 2 回" in r["message"]
    assert [c["label"] for c in r["choices"]] == labels
    assert [c["text"] for c in r["choices"]] == [rc.canonical("C00001", "C00003", 2, n) for n in totals]
    r2 = w.hear(sid, r["choices"][0]["text"])
    assert rng(r2) is not None  # 押すと範囲の確認へ（止まらない）


@pytest.mark.parametrize("text", ["C00001からC00003を4回ふやして", "C00001からC00003を9回追加して"])
def test_add_words_with_no_working_choice_stop(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None


@pytest.mark.parametrize("text,word", [
    ("C00001からC00003の作業を2回から2回にして", "同じ"),
    ("C00001からC00003の作業を2回から6回にして", "このプロトタイプでは、2回から3回への変更だけを扱います"),
    ("C00001からC00003の作業を2回から0回にして", "減らす変更"),
    ("C00001からC00003の作業を2回から4回にして", "このプロトタイプでは、2回から3回への変更だけを扱います"),
    ("C00001〜C00003を2回にして", "同じ"),
    ("C00001〜C00003を6回にして", "このプロトタイプでは、2回から3回への変更だけを扱います"),
    ("C00001〜C00003を4回にして", "このプロトタイプでは、2回から3回への変更だけを扱います"),
    ("C00001からC00009の作業を2回から3回にして", "ありません"),
])
def test_refused_counts_stop_without_choices(text, word, counting):
    w, sid = start(counting)
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None, r
    assert word in r["message"] and counting.decides == 0


@pytest.mark.parametrize("text", ["C00001からC00003の作業を1回から2回にして", "C00001からC00003の作業を1回から1回にして",
                                  "C00001からC00003を2回にして"])
def test_one_cycle_job_stops_without_dead_end_choices(text):
    w, sid = start(data=one_cycle())
    r = w.hear(sid, text)
    assert r["status"] == "stop" and r["choices"] == [] and cand(r) is None and rng(r) is None, r["message"]


def test_said_one_to_two_on_the_demo_stops():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を1回から2回にして")
    assert r["status"] == "stop" and "今 2 回" in r["message"] and r["choices"] == []


def test_job_reason_stop_adds_the_scope_note():
    data = insert_after(DEMO, lines(DEMO).index(MOVL2), b"DOUT OT#(1) ON\r\n")
    w, sid = start(data=data)
    r = w.hear(sid, SAY)
    assert r["status"] == "stop" and "DOUT" in r["message"] and SCOPE in r["message"]


@pytest.mark.parametrize("text", ["C00001からC00003の作業を3回にしないで", "C00001からC00003を3回にしなくていい",
                                  "C00001からC00003の作業を2回から3回にしないで"])
def test_negation_stops(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and cand(r) is None and rng(r) is None and "打ち消し" in r["message"]


def test_question_form_goes_straight_to_the_range_button():
    # QA P2-9：2回聞き返さず、範囲の確認のボタン1回で確かめる（候補はボタンのあと）
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を2回から3回にして？")
    assert r["status"] == "ask" and rng(r) is not None and cand(r) is None


@pytest.mark.parametrize("text", ["C00001からC00003の作業を3回にしてZプラス3mm", "C00001からC00003を3回にして速度を10%上げて",
                                  "C00001からC00003の作業を3回にして下に3mm移動して",
                                  "C00001からC00003の作業を3回にして比較して"])
def test_mixed_with_other_changes_stops(text, counting):
    w, sid = start(counting)
    r = w.hear(sid, text)
    assert r["status"] == "stop" and cand(r) is None and "1つ" in r["message"] and counting.decides == 0


def test_approve_word_in_the_same_sentence_stops():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を3回にして承認して")
    assert r["status"] == "stop" and cand(r) is None and r["message"] == ut.APPROVE_MESSAGE


def test_old_repeat_message_no_longer_contradicts_the_feature():
    assert "動きの追加・繰り返しはできません" not in ut.REPEAT_STOP_MESSAGE
    assert "範囲" in ut.REPEAT_STOP_MESSAGE


def test_no_assertive_words_anywhere():
    msgs = [rc.RANGE_NEEDED, rc.RANGE_STALE, rc.RANGE_NO_REPLY, rc.FORM_UNKNOWN, rc.DECIMAL_MESSAGE, SCOPE,
            ut.REPEAT_STOP_MESSAGE]
    for t in (SAY, "C00001〜C00003を3回にして", "C00001からC00003を3回追加して"):
        w, sid = start()
        r = w.hear(sid, t)
        msgs += [r["message"]] + [c["label"] for c in r["choices"]]
    w, sid = start()
    r = to_candidate(w, sid)
    msgs += cand(r)["warnings"] + [cand(r)["heading"], r["state"]["confirmation_text"]]
    assert not any(ut.contains_forbidden(m) for m in msgs)


# ======================================================================
# 7. 候補を作る経路（Safety P2-5：ソースの形だけでなく、動きで確かめる）
# ======================================================================
def test_repeat_candidate_path_does_not_use_plan_shift_or_the_interpreter(monkeypatch, counting):
    def broken(*a, **k):
        raise AssertionError("回数の変更の経路で plan_shift が呼ばれました")
    calls = []
    real_confirm = Session.confirm_repeat

    def spy(self, turn, choice):
        calls.append((turn, choice))
        return real_confirm(self, turn, choice)
    monkeypatch.setattr(Session, "confirm_repeat", spy)
    w, sid = start(counting, select=None)
    monkeypatch.setattr(jbi.Job, "plan_shift", broken)  # 読み込んだあと（読み込みの表示は位置の検査を試すため）
    r = w.hear(sid, SAY)
    assert calls == [] and cand(r) is None  # 指示だけでは候補を作らない
    r2 = w.repeat_choose(sid, "yes", rng(r)["turn"])
    assert len(calls) == 1 and r2["candidate_made"] and counting.decides == 0
    s = w._entries[sid].session
    assert {n: p.values for n, p in s.candidate.new_job.positions.items()} == \
        {n: p.values for n, p in s.original.positions.items()}  # 位置の値は変わらない


def test_session_candidate_keeps_the_original():
    s = Session(demo_job())
    r = s.hear(SAY)
    assert r.status == "ask" and s.candidate is None and s.repeat_range is not None
    # この発話では、ほかの提案（Claude の道具の呼び出し）を受け付けない
    assert s.propose({"position": "C00001", "axis": "Z", "delta_mm": 1}).status == "stop"
    r2 = s.confirm_repeat(s.repeat_range["turn"], "yes")
    assert r2.candidate_made and s.candidate.kind == "repeat" and s.original_bytes == DEMO
    assert s.compare().comparison.kind == "repeat"


# ======================================================================
# 8. 図のデータ
# ======================================================================
def test_figure_data_counts_and_kinds():
    w, sid = start()
    c = cand(to_candidate(w, sid))
    rep = c["repeat"]
    assert rep["before_count"] == 2 and rep["after_count"] == 3 and rep["positions"] == ["C00001", "C00002", "C00003"]
    before, after = c["before_segments"], c["after_segments"]
    assert [s["cycle"] for s in before] == [1, 1, 1, 2, 2, 2, None]
    assert [s["cycle"] for s in after] == [1, 1, 1, 2, 2, 2, 3, 3, 3, None]
    assert after[-1]["kind"] == "movj" and before[-1]["kind"] == "movj"
    assert all(s["kind"] == "movl" for s in after if s["cycle"])
    assert [p["xyz"] for p in c["after_positions"]] == [p["xyz"] for p in api.job_view(DEMO)["positions"]]
    assert c["moved"] is None
    assert {(s["from"], s["to"]) for s in after} == {(s["from"], s["to"]) for s in before}


# ======================================================================
# 9. つなぎの移動を暗黙に作らない（唐川さんの追加の指示 2026-11-03）
# ======================================================================
def test_two_to_three_has_no_new_segment_and_says_so():
    job = demo_job()
    p = job.plan_repeat("C00001", "C00003", 3)
    assert jbi.new_move_segments(job, p.new_job) == []
    assert len(jbi.move_segments(p.new_job)) == len(jbi.move_segments(job)) + 3
    note = [w for w in p.warnings if w.startswith("新しい移動の区間はありません")]
    assert note and "6 種類" in note[0]


def test_entry_and_exit_moves_are_unchanged():
    p = demo_job().plan_repeat("C00001", "C00003", 3)
    segs = {i: (a, t) for a, t, i in jbi.move_segments(p.new_job)}
    assert segs[p.new_block.first_line] == ("C00000", "MOVL C00001 V=150.0")
    assert segs[p.new_block.end_line] == ("C00003", "MOVJ C00004 VJ=25.00")


def test_one_to_two_would_make_a_new_connector():
    one, two = jbi.parse_bytes(one_cycle()), jbi.parse_bytes(DEMO)
    fresh = jbi.new_move_segments(one, two)
    assert [(a, t) for a, t, _ in fresh] == [("C00003", "MOVL C00001 V=150.0")]
    msg = jbi.new_segment_message(fresh[0])
    assert msg.startswith("C00003 から C00001 への移動") and "今のジョブにありません" in msg and "回数は変えません" in msg


def test_guard_stops_even_if_a_new_segment_slipped_through(monkeypatch):
    real = jbi.move_segments

    def fake(job):
        segs = real(job)
        return segs + [("C00004", "MOVL C00001 V=150.0", 999)] if len(segs) > 8 else segs
    monkeypatch.setattr(jbi, "move_segments", fake)
    with pytest.raises(jbi.JbiError) as e:
        demo_job().plan_repeat("C00001", "C00003", 3)
    assert "C00004 から C00001 への移動" in str(e.value) and "新しい移動を作るため" in str(e.value)


@pytest.mark.parametrize("data", [
    insert_after(DEMO, FIRST + 3, b"MOVL C00004 V=150.0\r\n"),
    variant((b"MOVJ C00004 VJ=25.00", b"MOVL C00001 V=150.0\r\nMOVJ C00004 VJ=25.00")),
])
def test_shapes_without_a_known_connector_are_refused(data):
    with pytest.raises(jbi.JbiError):
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)


def test_flow_shows_the_no_new_segment_fact():
    w, sid = start()
    assert any(x.startswith("新しい移動の区間はありません") for x in cand(to_candidate(w, sid))["warnings"])


# ======================================================================
# 10. 出力の前の差分の確かめ（QA P2-6）
# ======================================================================
def test_repeat_diff_check_accepts_only_one_cycle_inserted():
    new = demo_job().plan_repeat("C00001", "C00003", 3).new_job.render_bytes()
    cyc = tuple(CYCLE)
    assert sim._repeat_diff_ok(DEMO, new, END, cyc, added=True) is True
    assert sim._repeat_diff_ok(DEMO, new.replace(b"V=30.0", b"V=31.0", 1), END, cyc, added=True) is False
    assert sim._repeat_diff_ok(DEMO, new, END + 1, cyc, added=True) is False
    assert sim._repeat_diff_ok(DEMO, new + b"NOP\r\n", END, cyc, added=True) is False
    assert sim._repeat_diff_ok(DEMO.replace(b"V=150.0", b"V=151.0", 1), new, END, cyc, added=True) is False


# ---- S-P2-4：区間のすぐ前後の DOUT・TIMER は繰り返さないことを、その行があるときだけ範囲の確認に書く ----
def test_side_lines_are_named_in_the_range_ask():
    data = insert_after(DEMO, END, b"TIMER T=0.50\r\n")
    data = insert_after(data, FIRST, b"DOUT OT#(1) ON\r\n")
    w, sid = start(data=data)
    r = w.hear(sid, SAY)
    rr = rng(r)
    assert rr is not None, r["message"]
    note = rr["side_note"]
    assert note.startswith("区間のすぐ前・後ろに信号（DOUT）や待ち時間（TIMER）の行があります")
    assert f"{FIRST + 1}行目「DOUT OT#(1) ON」" in note and f"{END + 2}行目「TIMER T=0.50」" in note
    assert note.endswith("それらは繰り返しません") and note in r["message"]
    c = cand(w.repeat_choose(sid, "yes", rr["turn"]))
    assert all("DOUT" not in d["text"] and "TIMER" not in d["text"] for d in c["diff"])


def test_no_side_note_without_such_lines():
    w, sid = start()
    rr = rng(w.hear(sid, SAY))
    assert rr["side_note"] is None and rr["heading"] == "この範囲を繰り返しますか？"


def test_export_runs_the_diff_check(monkeypatch, tmp_path):
    # sim.export_sim（回数の変更）は、書く前に _repeat_diff_ok で差分を確かめる（QA：呼ばれていることのテスト）
    calls = []
    real = sim._repeat_diff_ok

    def spy(*a, **k):
        calls.append(a[2])
        return real(*a, **k)
    monkeypatch.setattr(sim, "_repeat_diff_ok", spy)
    w, sid = start()
    to_candidate(w, sid)
    assert w.approve(sid, "確認者", candidate_gen=w.candidate_gen_of(sid))["ok"]
    calls.clear()
    monkeypatch.setattr(sim, "_repeat_diff_ok", lambda *a, **k: False)
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is False and "ほかにも違い" in x["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []
    monkeypatch.setattr(sim, "_repeat_diff_ok", spy)
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is True and calls == [END]


def test_find_repeat_blocks_is_light_on_a_big_job():
    # 大きなジョブ（MOVL 400行。テストの中で作る）でも、区間の候補の確かめは数えるほどしか呼ばない
    import time
    n = 400
    pos = "".join(f"C{i:05d}=400.000,{i}.000,300.000,180.00,0.00,0.00\r\n" for i in range(n))
    inst = "".join(f"MOVL C{i:05d} V=100.0\r\n" for i in range(n))
    text = ("/JOB\r\n//NAME BIG\r\n//POS\r\n///NPOS " + f"{n},0,0,0,0,0\r\n///TOOL 0\r\n///POSTYPE ROBOT\r\n///RECTAN\r\n"
            "///RCONF 0,0,0,0,0,0,0,0\r\n" + pos + "//INST\r\n///DATE 2026/11/03 20:00\r\n///ATTR SC,RW\r\n"
            "///GROUP1 RB1\r\nNOP\r\n" + inst + "END\r\n")
    job = jbi.parse_bytes(text.encode("ascii"))
    assert job.problems == []
    t0 = time.perf_counter()
    assert job.find_repeat_blocks() == []
    assert time.perf_counter() - t0 < 1.0


# ---- Safety P2-3・P2-4（f7f871f のあと）：一覧から少しずれた言い方・承認の言葉 ----
@pytest.mark.parametrize("word", ["この範囲をくりかえす", "この範囲を繰り返します", "はい、この範囲を繰り返す", "はいそうです",
                                  "はい繰り返して", "うん、それで", "OKです", "それでお願い"])
def test_near_confirm_words_keep_the_range(word):
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    r = w.hear(sid, word, source="voice")
    assert r["status"] == "ask" and r["message"] == rc.RANGE_BUTTON_ONLY, (word, r["message"])
    assert rng(r) is not None and rng(r)["turn"] == turn and cand(r) is None


@pytest.mark.parametrize("word", ["はい、違う", "そうじゃない", "はい3回", "はいC00002を選択して", "はい比較して", "はいZプラス3mm",
                                  "繰り返さないで", "いいえ"])
def test_confirm_like_but_with_other_content_is_a_new_instruction(word):
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    r = w.hear(sid, word)
    assert r["message"] not in (rc.RANGE_BUTTON_ONLY, rc.RANGE_APPROVE_GUIDE), (word, r["message"])
    assert cand(r) is None
    assert w.repeat_choose(sid, "yes", turn)["status"] == "stop"


@pytest.mark.parametrize("word", ["承認", "承認して", "承認します"])
def test_approve_words_during_the_range_explain_the_two_steps(word):
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    r = w.hear(sid, word)
    assert r["message"] == rc.RANGE_APPROVE_GUIDE and rng(r)["turn"] == turn and r["state"]["approval"] is None


def test_range_state_has_the_not_approval_note():
    w, sid = start()
    assert rng(w.hear(sid, SAY))["button_note"] == "範囲を決めるだけです。承認は次の確認表示で行います"


# ---- Safety P2-2・P2-6：区間が見つからない理由・ジョブに無い位置 ----
def test_no_block_without_signals_does_not_mention_dout():
    w, sid = start(data=one_cycle())  # 区間は1回だけ。DOUT・TIMER は無い
    r = w.hear(sid, "3回にして")
    assert r["status"] == "ask" and r["message"] == f"{rc.NO_BLOCK}。{rc.RANGE_NEEDED}" and SCOPE not in r["message"]


def test_position_not_in_the_job_is_named():
    w, sid = start()
    r = w.hear(sid, "C00099を3回")
    assert r["status"] == "stop" and r["message"] == "位置 C00099 はこのジョブにありません" and rng(r) is None


# ---- 家のPCの内部レビュー（9ec263c）P2-1・P3-1：範囲・回数を変えたい言葉、別の指示は、範囲の確認のつもりの言葉にしない ----
@pytest.mark.parametrize("word", ["範囲を変えて", "範囲を広げて", "範囲を短くして", "範囲を選び直す", "別の範囲", "前の範囲",
                                  "はい、四回にして", "はい五回", "はい、十回", "はい出力して", "はい、転送して",
                                  "はい、ゆっくり", "はい、全部", "やっぱりいい", "もういい"])
def test_requests_to_change_range_or_count_are_not_range_confirm_words(word):
    assert not rc.is_range_confirm_word(word), word
    w, sid = start()
    turn = rng(w.hear(sid, SAY))["turn"]
    r = w.hear(sid, word, source="voice")
    assert r["message"] not in (rc.RANGE_BUTTON_ONLY, rc.RANGE_APPROVE_GUIDE), (word, r["message"])
    assert cand(r) is None and r["state"]["approval"] is None
