"""受入テスト：作業の回数を変える（「C00001からC00003の作業を2回から3回にして」）。実装より先に書いた。

設計は docs/REPEAT_COUNT_DESIGN.md。デモ用ジョブは samples/dummy/DEMO_BUTTON_REPEAT.JBI（架空のジョブ）。
- 区間がジョブの中に続けてそのまま N 回書かれている形だけを数える。JUMP・ラベル・カウンタは扱わない
- 区間の中は「MOVL 位置 V=数値」（PL なし、またはちょうど PL=0）だけ。ほかの行があれば止める
- 今が2回以上・±1・合計1〜5回だけ。足すのは最後の回の直後に1回分（同じバイト列）だけ
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
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_PATH = ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"
DEMO = DEMO_PATH.read_bytes()
SAY = "C00001からC00003の作業を2回から3回にして"
CYCLE = [b"MOVL C00001 V=150.0\r\n", b"MOVL C00002 V=30.0\r\n", b"MOVL C00003 V=150.0\r\n"]
FIXED_WARNINGS = ("同じ動きを1回ぶん足します。新しい位置・新しい移動の線は作りません",
                  "サイクルの時間が長くなります",
                  "検査を通ったことは、物理的な安全の保証ではありません")


def lines(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


def inst_start(data: bytes) -> int:
    return [x.rstrip(b"\r\n") for x in lines(data)].index(b"//INST")


def demo_job() -> jbi.Job:
    return jbi.parse_bytes(DEMO)


def variant(*pairs: tuple[bytes, bytes], count: int = 1) -> bytes:
    """デモ用ジョブの行を書き換えた版（テストの中だけで作る。ファイルにはしない）。"""
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


def start(interp=None, data=DEMO, select="C00001"):
    w = WebFlow(interpreter=interp or ci.MockInterpreter())
    r = w.load(data)
    assert r["ok"], r
    sid = r["session_id"]
    if select:
        assert w.select(sid, select)["status"] == "ok"
    return w, sid


def cand(r):
    return r["state"]["candidate"]


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
    assert b.positions == ("C00001", "C00002", "C00003")
    assert b.count == 2
    assert [x.encode() for x in b.cycle_lines] == CYCLE
    assert b.first_line == lines(DEMO).index(CYCLE[0])


def test_patterns_md_explains_the_demo_job():
    text = (ROOT / "samples" / "dummy" / "PATTERNS.md").read_text(encoding="utf-8")
    assert "DEMO_BUTTON_REPEAT.JBI" in text


# ======================================================================
# 2. 2回 → 3回 の候補（jbi）
# ======================================================================
def test_two_to_three_adds_exactly_one_cycle_after_the_last():
    job = demo_job()
    p = job.plan_repeat("C00001", "C00003", 3)
    assert p.kind == "repeat" and p.target == "C00001〜C00003"
    assert (p.before_count, p.after_count) == (2, 3)
    old, new = lines(DEMO), lines(p.new_job.render_bytes())
    assert len(new) == len(old) + 3
    at = job.find_repeat("C00001", "C00003").first_line + 2 * 3  # 最後の回の直後
    assert new[:at] == old[:at] and new[at + 3:] == old[at:]
    assert new[at:at + 3] == CYCLE
    assert [(i, t) for i, t in p.added] == [(at, "MOVL C00001 V=150.0"), (at + 1, "MOVL C00002 V=30.0"),
                                           (at + 2, "MOVL C00003 V=150.0")]
    assert p.removed == ()
    # 位置・ヘッダーは同じ（//INST より前はバイト列ごと同じ）
    k = inst_start(DEMO)
    assert new[:k + 1] == old[:k + 1]
    assert {n: q.values for n, q in p.new_job.positions.items()} == {n: q.values for n, q in job.positions.items()}
    assert p.new_job.problems == [] and p.new_job.name == "DEMO_BUTTON_REPEAT"
    assert p.new_job.find_repeat("C00001", "C00003").count == 3
    # 元のジョブは変わらない
    assert job.render_bytes() == DEMO and DEMO_PATH.read_bytes() == DEMO
    assert tuple(p.warnings[:3]) == FIXED_WARNINGS


def test_three_to_two_removes_the_last_cycle_and_gives_back_the_demo():
    three = demo_job().plan_repeat("C00001", "C00003", 3).new_job
    p = three.plan_repeat("C00001", "C00003", 2)
    assert (p.before_count, p.after_count) == (3, 2)
    assert p.new_job.render_bytes() == DEMO
    assert len(p.removed) == 3 and p.added == ()
    assert "サイクルの時間が短くなります" in p.warnings
    assert "検査を通ったことは、物理的な安全の保証ではありません" in p.warnings


def test_two_to_one_only_removes_lines():
    p = demo_job().plan_repeat("C00001", "C00003", 1)
    old, new = lines(DEMO), lines(p.new_job.render_bytes())
    assert len(new) == len(old) - 3 and p.new_job.find_repeat("C00001", "C00003").count == 1


@pytest.mark.parametrize("total,word", [
    (2, "同じ"), (4, "1回ずつ"), (6, "1〜5"), (0, "1〜5"), (-1, "1〜5"), (99, "1〜5"),
])
def test_bad_totals_are_refused(total, word):
    with pytest.raises(jbi.JbiError) as e:
        demo_job().plan_repeat("C00001", "C00003", total)
    assert word in str(e.value)


@pytest.mark.parametrize("total", [True, 3.0, "3", None, float("nan")])
def test_total_must_be_a_plain_int(total):
    with pytest.raises(jbi.JbiError):
        demo_job().plan_repeat("C00001", "C00003", total)


def test_one_cycle_cannot_become_two():
    # 1回だけのジョブ：区間と区間のつなぎの移動が無く、新しい移動の線ができるので止める
    one = demo_job().plan_repeat("C00001", "C00003", 1).new_job
    assert one.find_repeat("C00001", "C00003").count == 1
    with pytest.raises(jbi.JbiError) as e:
        one.plan_repeat("C00001", "C00003", 2)
    assert "2回以上" in str(e.value) and "新しい移動の線" in str(e.value)


def test_four_to_five_and_five_is_the_top():
    job = demo_job()
    for n in (3, 4, 5):
        job = job.plan_repeat("C00001", "C00003", n).new_job
    assert job.find_repeat("C00001", "C00003").count == 5
    with pytest.raises(jbi.JbiError) as e:
        job.plan_repeat("C00001", "C00003", 6)
    assert "1〜5" in str(e.value)


# ======================================================================
# 3. 区間の中・区間と区間の間のほかの行 → 止める（理由の文）
# ======================================================================
MOVL2 = b"MOVL C00002 V=30.0\r\n"


@pytest.mark.parametrize("name,data,word", [
    ("DOUT（区間の中）", insert_after(DEMO, lines(DEMO).index(MOVL2), b"DOUT OT#(1) ON\r\n"), "DOUT"),
    ("DOUT（区間と区間の間）", insert_after(DEMO, lines(DEMO).index(CYCLE[2]) + 1, b"DOUT OT#(1) ON\r\n"), "DOUT"),
    ("TIMER", insert_after(DEMO, lines(DEMO).index(MOVL2) + 1, b"TIMER T=0.50\r\n"), "TIMER"),
    ("コメント", insert_after(DEMO, lines(DEMO).index(CYCLE[2]) + 1, b"'PUSH AGAIN\r\n"), "コメント"),
    ("MOVJ", variant((b"MOVL C00002 V=30.0", b"MOVJ C00002 VJ=10.00"), count=2), "MOVJ"),
    ("PL=1", variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=30.0 PL=1"), count=2), "PL=1"),
    ("CR", variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=30.0 CR=5.0"), count=2), "CR"),
    ("別の速度", variant((b"MOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=100.0\r\nMOVJ")), "同じではありません"),
    ("PL=0 の有無が違う", variant((b"MOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=150.0 PL=0\r\nMOVJ")),
     "同じではありません"),
    ("区間の外の位置への移動", insert_after(DEMO, lines(DEMO).index(CYCLE[2]) + 1, b"MOVL C00004 V=150.0\r\n"), "C00004"),
])
def test_other_lines_inside_or_between_cycles_stop(name, data, word):
    job = jbi.parse_bytes(data)
    if b"MOVL C00004 V=150.0" not in data:
        assert job.problems == [], (name, job.problems)
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


def test_wait_stops_because_it_is_not_in_the_allow_list():
    data = insert_after(DEMO, lines(DEMO).index(MOVL2), b"WAIT IN#(1)=ON\r\n")
    job = jbi.parse_bytes(data)
    assert job.problems  # WAIT は許可リストに無い（推測で増やさない）
    with pytest.raises(jbi.JbiError) as e:
        job.plan_repeat("C00001", "C00003", 3)
    assert "WAIT" in str(e.value)


@pytest.mark.parametrize("data,start,end,word", [
    (DEMO, "C00001", "C00009", "ありません"),            # 位置がジョブに無い
    (DEMO, "C00003", "C00001", "始まり"),                 # 始まりが終わりより後ろ
    (DEMO, "C00001", "C00001", "始まり"),
    (DEMO, "C00001", "C00002", "C00003"),                 # 並びの間にほかの位置への移動がある
    (DEMO, "C00000", "C00002", "MOVJ"),                   # 区間の位置へ入る命令が MOVJ
    # 並びが違う（2回目だけ C00002 と C00003 が入れ替わっている）
    (variant((b"MOVL C00002 V=30.0\r\nMOVL C00003 V=150.0\r\nMOVJ", b"MOVL C00003 V=150.0\r\nMOVL C00002 V=30.0\r\nMOVJ")),
     "C00001", "C00003", "並び"),
])
def test_no_matching_block_stops(data, start, end, word):
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat(start, end, 3)
    assert word in str(e.value)


def test_jump_and_label_style_loops_are_not_handled():
    data = variant((b"NOP\r\n", b"NOP\r\n*L1\r\n"))
    with pytest.raises(jbi.JbiError) as e:
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)
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
    ("C00001からC00003までの作業を3回から2回にして", "C00001", "C00003", 3, 2),
    ("ｃ００００１からｃ００００３の作業を２回から３回にして", "C00001", "C00003", 2, 3),
])
def test_reads_the_fixed_forms(text, start, end, said, total):
    assert rc.is_repeat_request(text)
    r = rc.read(text)
    assert (r.start, r.end, r.said_count, r.total, r.add) == (start, end, said, total, None)


@pytest.mark.parametrize("text", ["C00001からC00002を2回移動させて", "2回移動させて", "下に3mm", "C00001を選択して",
                                  "3回にして", "C00001とC00002を2往復させて"])
def test_other_sentences_are_not_repeat_requests(text):
    assert not rc.is_repeat_request(text)


@pytest.mark.parametrize("text,n", [("C00001からC00003を3回追加して", 3), ("C00001〜C00003を1回ふやして", 1),
                                     ("C00001からC00003の作業を2回増やして", 2), ("C00001からC00003を1回足して", 1)])
def test_add_words_are_read_as_ambiguous(text, n):
    r = rc.read(text)
    assert r.add == n and r.total is None


def test_canonical_text_reads_back():
    t = rc.canonical("C00001", "C00003", 2, 3)
    assert t == SAY
    r = rc.read(t)
    assert (r.start, r.end, r.said_count, r.total) == ("C00001", "C00003", 2, 3)


# ======================================================================
# 5. 画面の流れ（WebFlow）：指示 → 候補 → ボタン承認 → 出力
# ======================================================================
def test_end_to_end_two_to_three(counting, tmp_path):
    w, sid = start(counting)
    r = w.hear(sid, SAY, source="voice")
    assert r["ok"] and r["status"] == "ok" and r["candidate_made"] is True, r
    assert counting.decides == 0 and r["tool_calls"] == []  # 解釈の係は呼ばない
    c = cand(r)
    assert c["kind"] == "repeat" and c["target"] == "C00001〜C00003"
    assert c["heading"] == "C00001〜C00003 の作業を 2回 → 3回 にします"
    assert (c["before_value"], c["after_value"]) == ("2", "3")
    assert [d["sign"] for d in c["diff"]] == ["+", "+", "+"]
    assert [d["text"] for d in c["diff"]] == ["MOVL C00001 V=150.0", "MOVL C00002 V=30.0", "MOVL C00003 V=150.0"]
    assert c["diff"][0]["line_no"] == lines(DEMO).index(CYCLE[2], lines(DEMO).index(CYCLE[2]) + 1) + 2
    for wtxt in FIXED_WARNINGS:
        assert wtxt in c["warnings"]
    assert c["heard"] == SAY
    st = r["state"]
    assert st["can_approve"] is True and st["approval"] is None and st["can_export"] is False
    assert "2回 → 3回" in st["confirmation_text"] and "承認は画面のボタン" in st["confirmation_text"]

    a = w.approve(sid, "e2e確認")
    assert a["ok"] is True, a
    assert a["state"]["approval"]["approved_by"] == "e2e確認" and a["state"]["can_export"] is True
    x = w.export(sid)
    assert x["ok"] is True, x
    assert x["file_name"] == "DEMO_BUTTON_REPEAT_SIM.JBI.txt"
    out = Path(x["path"]).read_bytes()
    old, new = lines(DEMO), lines(out)
    assert len(new) == len(old) + 3
    diff_name = [i for i, (p, q) in enumerate(zip(old, new)) if p != q]
    assert new[1] == b"//NAME DEMO_BUTTON_REPEAT_SIM\r\n" and diff_name[0] == 1
    assert b"".join(new[:1] + new[2:]) == b"".join(
        old[:1] + old[2:lines(DEMO).index(CYCLE[2], lines(DEMO).index(CYCLE[2]) + 1) + 1] + CYCLE
        + old[lines(DEMO).index(CYCLE[2], lines(DEMO).index(CYCLE[2]) + 1) + 1:])
    assert DEMO_PATH.read_bytes() == DEMO  # 元のジョブは変わらない
    assert jbi.parse_bytes(out).find_repeat("C00001", "C00003").count == 3


def test_three_to_two_via_flow_is_refused_on_the_demo_because_it_has_two():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を3回から2回にして")
    assert r["status"] == "ask" and cand(r) is None
    assert "このジョブでは、C00001〜C00003 は今 2 回です。何回にしますか？" in r["message"]
    assert [c["text"] for c in r["choices"]] == [rc.canonical("C00001", "C00003", 2, 1),
                                                 rc.canonical("C00001", "C00003", 2, 3)]


@pytest.mark.parametrize("text", ["はい", "承認して", "OK", "承認"])
def test_voice_yes_does_not_approve(text):
    w, sid = start()
    assert w.hear(sid, SAY)["candidate_made"]
    r = w.hear(sid, text, source="voice")
    assert r["state"]["approval"] is None and r["state"]["can_export"] is False
    x = w.export(sid)
    assert x["ok"] is False


def test_new_instruction_after_approval_cancels_it():
    w, sid = start()
    w.hear(sid, SAY)
    assert w.approve(sid, "確認者")["ok"]
    r = w.hear(sid, "比較して")
    assert r["state"]["approval"] is None and r["state"]["can_export"] is False
    assert w.export(sid)["ok"] is False


def test_export_needs_approval(tmp_path):
    w, sid = start()
    w.hear(sid, SAY)
    x = w.export(sid)
    assert x["ok"] is False and "承認" in x["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_approve_needs_a_name():
    w, sid = start()
    w.hear(sid, SAY)
    assert w.approve(sid, "  ")["ok"] is False


def test_non_synthetic_job_is_never_exported(tmp_path):
    # 合成ジョブ（samples/dummy とバイト単位で一致）でない、同じ形のジョブ：候補・承認はできても、出力しない
    data = variant((b"//NAME DEMO_BUTTON_REPEAT", b"//NAME SOME_REAL_JOB"))
    assert not sim._is_synthetic(data)
    w, sid = start(data=data)
    assert w.hear(sid, SAY)["candidate_made"]
    w.approve(sid, "確認者")
    x = w.export(sid)
    assert x["ok"] is False and "合成ジョブ" in x["error"]
    assert not (tmp_path / "sim").exists() or list((tmp_path / "sim").iterdir()) == []


def test_undo_discards_and_keeps_the_original():
    w, sid = start()
    w.hear(sid, SAY)
    r = w.undo(sid)
    assert cand(r) is None
    assert w._entries[sid].session.original_bytes == DEMO


def test_pending_candidate_blocks_another_repeat_request(counting):
    w, sid = start(counting)
    first = cand(w.hear(sid, SAY))
    r = w.hear(sid, "C00001からC00003の作業を3回から4回にして")
    assert r["status"] == "stop" and "承認待ち" in r["message"]
    assert cand(r)["diff"] == first["diff"] and counting.decides == 0


# ---- 聞き返し ----
def test_said_count_differs_from_the_job(counting):
    w, sid = start(counting)
    r = w.hear(sid, "C00001からC00003の作業を3回から4回にして")
    assert r["status"] == "ask" and cand(r) is None and counting.decides == 0
    assert r["message"].startswith("このジョブでは、C00001〜C00003 は今 2 回です。何回にしますか？")


def test_count_not_said_is_confirmed_first():
    w, sid = start()
    r = w.hear(sid, "C00001〜C00003を3回にして")
    assert r["status"] == "ask" and cand(r) is None
    assert r["message"] == "このジョブでは、C00001〜C00003 は今 2 回です。2回 → 3回 にしますか？"
    assert [c["text"] for c in r["choices"]] == [SAY]
    r2 = w.hear(sid, r["choices"][0]["text"])
    assert r2["candidate_made"] and cand(r2)["after_value"] == "3"


@pytest.mark.parametrize("text,labels,totals", [
    ("C00001からC00003を3回追加して", ["合計3回にする", "3回足して合計5回にする"], [3, 5]),
    ("C00001からC00003を4回ふやして", ["合計4回にする"], [4]),        # 2+4=6 は範囲の外：出さない
    ("C00001からC00003を1回増やして", ["合計1回にする", "1回足して合計3回にする"], [1, 3]),
])
def test_add_words_ask_total_or_added(text, labels, totals):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None
    assert "今 2 回" in r["message"]
    assert [c["label"] for c in r["choices"]] == labels
    assert [c["text"] for c in r["choices"]] == [rc.canonical("C00001", "C00003", 2, n) for n in totals]


def test_add_choice_beyond_plus_one_then_stops():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003を3回追加して")
    r2 = w.hear(sid, r["choices"][1]["text"])  # 合計5回：±1 ではないので止める
    assert r2["status"] == "stop" and cand(r2) is None and "1回ずつ" in r2["message"]


# ---- 止める ----
@pytest.mark.parametrize("text,word", [
    ("C00001からC00003の作業を2回から2回にして", "同じ"),
    ("C00001からC00003の作業を2回から6回にして", "1〜5"),
    ("C00001からC00003の作業を2回から0回にして", "1〜5"),
    ("C00001からC00003の作業を2回から4回にして", "1回ずつ"),
    ("C00001〜C00003を2回にして", "同じ"),
    ("C00001〜C00003を6回にして", "1〜5"),
    ("C00001からC00009の作業を2回から3回にして", "ありません"),
])
def test_refused_counts_stop_without_candidate(text, word, counting):
    w, sid = start(counting)
    r = w.hear(sid, text)
    assert r["status"] == "stop" and cand(r) is None, r
    assert word in r["message"] and counting.decides == 0


def test_one_to_two_on_a_one_cycle_job_stops(tmp_path):
    one = demo_job().plan_repeat("C00001", "C00003", 1).new_job.render_bytes()
    w, sid = start(data=one)
    r = w.hear(sid, "C00001からC00003の作業を1回から2回にして")
    assert r["status"] == "stop" and cand(r) is None and "2回以上" in r["message"]


def test_said_one_to_two_on_the_demo_asks():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を1回から2回にして")
    assert r["status"] == "ask" and "今 2 回" in r["message"] and cand(r) is None


@pytest.mark.parametrize("text", ["C00001からC00003の作業を3回にしないで", "C00001からC00003を3回にしなくていい",
                                  "C00001からC00003の作業を2回から3回にしないで"])
def test_negation_stops(text):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "stop" and cand(r) is None and "打ち消し" in r["message"]


@pytest.mark.parametrize("text,word", [("C00001からC00003のバイオを3回にして", "バイオ"),
                                        ("C00001からC00003の作業をなんか3回にして", "なんか")])
def test_unknown_words_ask(text, word):
    w, sid = start()
    r = w.hear(sid, text)
    assert r["status"] == "ask" and cand(r) is None and f"「{word}」" in r["message"]
    assert r["choices"] == []


def test_question_form_is_confirmed_not_made():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を2回から3回にして？")
    assert r["status"] == "ask" and cand(r) is None
    assert [c["text"] for c in r["choices"]] == [SAY]


@pytest.mark.parametrize("text", ["C00001からC00003の作業を3回にしてZプラス3mm", "C00001からC00003を3回にして速度を10%上げて",
                                  "C00001からC00003の作業を3回にして下に3mm移動して",
                                  "C00001からC00003の作業を3回にして比較して"])
def test_mixed_with_other_changes_stops(text, counting):
    w, sid = start(counting)
    r = w.hear(sid, text)
    assert r["status"] == "stop" and cand(r) is None and "1つ" in r["message"]
    assert counting.decides == 0


def test_approve_word_in_the_same_sentence_stops():
    w, sid = start()
    r = w.hear(sid, "C00001からC00003の作業を3回にして承認して")
    assert r["status"] == "stop" and cand(r) is None and r["message"] == ut.APPROVE_MESSAGE


@pytest.mark.parametrize("data,word", [
    (insert_after(DEMO, lines(DEMO).index(MOVL2), b"DOUT OT#(1) ON\r\n"), "DOUT"),
    (variant((b"MOVL C00002 V=30.0", b"MOVL C00002 V=30.0 PL=1"), count=2), "PL=1"),
])
def test_flow_stops_with_the_jbi_reason(data, word):
    w, sid = start(data=data)
    r = w.hear(sid, SAY)
    assert r["status"] == "stop" and cand(r) is None and word in r["message"]


def test_no_assertive_words_anywhere(counting):
    w, sid = start(counting)
    msgs = []
    for t in (SAY, "C00001〜C00003を3回にして", "C00001からC00003を3回追加して"):
        w2, sid2 = start()
        r = w2.hear(sid2, t)
        msgs += [r["message"]] + [c["label"] for c in r["choices"]]
        if cand(r):
            msgs += cand(r)["warnings"] + [cand(r)["heading"], r["state"]["confirmation_text"]]
    assert msgs and not any(ut.contains_forbidden(m) for m in msgs)


# ======================================================================
# 6. Session（流れの下の層）
# ======================================================================
def test_session_candidate_keeps_the_original():
    s = Session(demo_job())
    r = s.hear(SAY)
    assert r.status == "ok" and r.candidate_made
    assert s.candidate.kind == "repeat" and s.original_bytes == DEMO and s.original.render_bytes() == DEMO
    # この発話では、ほかの提案（Claude の道具の呼び出し）を受け付けない
    assert s.propose({"position": "C00001", "axis": "Z", "delta_mm": 1}).status == "stop"
    assert s.compare().comparison.kind == "repeat"


# ======================================================================
# 7. 図のデータ：変更前後の回数・区間の線ごとの回・MOVJ の kind
# ======================================================================
def test_figure_data_counts_and_kinds():
    w, sid = start()
    c = cand(w.hear(sid, SAY))
    rep = c["repeat"]
    assert rep["before_count"] == 2 and rep["after_count"] == 3
    assert rep["positions"] == ["C00001", "C00002", "C00003"]
    before, after = c["before_segments"], c["after_segments"]
    # C00000→C00001（1回目に入る）… C00003→C00001（2回目に入る、つなぎの移動）… C00003→C00004（MOVJ・回の外）
    assert [s["cycle"] for s in before] == [1, 1, 1, 2, 2, 2, None]
    assert [s["cycle"] for s in after] == [1, 1, 1, 2, 2, 2, 3, 3, 3, None]
    assert after[-1]["kind"] == "movj" and before[-1]["kind"] == "movj"  # MOVJ は直線（movl）にしない
    assert all(s["kind"] == "movl" for s in after if s["cycle"])
    # 新しい位置は作らない（変更後の位置は変更前と同じ）
    view = api.job_view(DEMO)
    assert [p["xyz"] for p in c["after_positions"]] == [p["xyz"] for p in view["positions"]]
    assert c["moved"] is None
    # 足した回の線は、元からある線（同じ位置の組）だけ
    old_pairs = {(s["from"], s["to"]) for s in before}
    assert {(s["from"], s["to"]) for s in after} == old_pairs


# ======================================================================
# 8. つなぎの移動を暗黙に作らない（唐川さんの追加の指示 2026-11-03）
#    変更後の移動の区間（直前の移動の行き先 → 命令の行）が、すべて変更前のジョブにあること
# ======================================================================
def test_two_to_three_has_no_new_segment_and_says_so():
    job = demo_job()
    p = job.plan_repeat("C00001", "C00003", 3)
    assert jbi.new_move_segments(job, p.new_job) == []
    assert len(jbi.move_segments(p.new_job)) == len(jbi.move_segments(job)) + 3  # 区間の数は増えてよい
    note = [w for w in p.warnings if w.startswith("新しい移動の区間はありません")]
    assert note and "6 種類" in note[0]  # 初めの MOVJ C00000・C00000→C00001・C00001→C00002・C00002→C00003・C00003→C00001・C00003→C00004


def test_entry_and_exit_moves_are_unchanged():
    job = demo_job()
    p = job.plan_repeat("C00001", "C00003", 3)
    segs = {i: (a, t) for a, t, i in jbi.move_segments(p.new_job)}
    assert segs[p.new_block.first_line] == ("C00000", "MOVL C00001 V=150.0")
    assert segs[p.new_block.end_line] == ("C00003", "MOVJ C00004 VJ=25.00")


def test_one_to_two_would_make_a_new_connector():
    # 1回のジョブを手で2回にした形：C00003 → C00001 のつなぎの移動が、今のジョブに無い
    one = demo_job().plan_repeat("C00001", "C00003", 1).new_job
    two = jbi.parse_bytes(DEMO)
    fresh = jbi.new_move_segments(one, two)
    assert [(a, t) for a, t, _ in fresh] == [("C00003", "MOVL C00001 V=150.0")]
    msg = jbi.new_segment_message(fresh[0])
    assert msg.startswith("C00003 から C00001 への移動") and "今のジョブにありません" in msg and "回数は変えません" in msg
    with pytest.raises(jbi.JbiError):
        one.plan_repeat("C00001", "C00003", 2)


def test_guard_stops_even_if_a_new_segment_slipped_through(monkeypatch):
    # 区間の数え方を通っても、変更後に新しい区間があれば候補を作らない（独立の検査）
    real = jbi.move_segments
    def fake(job):
        segs = real(job)
        if len(segs) > 8:  # 変更後のジョブだけ
            segs = segs + [("C00004", "MOVL C00001 V=150.0", 999)]
        return segs
    monkeypatch.setattr(jbi, "move_segments", fake)
    with pytest.raises(jbi.JbiError) as e:
        demo_job().plan_repeat("C00001", "C00003", 3)
    assert "C00004 から C00001 への移動" in str(e.value) and "新しい移動を作るため" in str(e.value)


@pytest.mark.parametrize("data", [
    insert_after(DEMO, lines(DEMO).index(CYCLE[2]) + 1, b"MOVL C00004 V=150.0\r\n"),   # 区間の間に別の移動
    # 最後の回のあとが区間の始点へ戻る（C00001 の行がもう1つ。区間の行の数が合わない）
    variant((b"MOVJ C00004 VJ=25.00", b"MOVL C00001 V=150.0\r\nMOVJ C00004 VJ=25.00")),
])
def test_shapes_without_a_known_connector_are_refused(data):
    with pytest.raises(jbi.JbiError):
        jbi.parse_bytes(data).plan_repeat("C00001", "C00003", 3)


def test_flow_shows_the_no_new_segment_fact():
    w, sid = start()
    c = cand(w.hear(sid, SAY))
    assert any(x.startswith("新しい移動の区間はありません") for x in c["warnings"])


def test_heard_sentence_does_not_mark_the_fixed_words_as_unknown():
    # 「から」「作業」「にして」を、読めなかった言葉として画面で目立たせない（回数の変更の決まった形で読めた言葉）
    w, sid = start()
    r = w.hear(sid, SAY)
    assert r["facts"]["unknown_words"] == []
    w2, sid2 = start()
    r2 = w2.hear(sid2, "C00001からC00003のバイオを3回にして")
    assert r2["facts"]["unknown_words"] == ["バイオ"]
