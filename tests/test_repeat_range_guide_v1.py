"""回数の変更の範囲の案内（11/7 FINAL_RC_REPEAT_RANGE_GUIDANCE）。偽の Claude・合成 JOB だけ。
決まった文の言い直しを頼まず、ジョブの解析で確かめた範囲（今2回 → 3回）だけを案内する。人が言った違う範囲・回数に置き換えない。
選ぶボタンは、今までの範囲の確かめへ進むだけ（承認ではない）。"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import sim
from voice_teaching import utterance as ut
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
RP = (ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI").read_bytes()
RJ = (ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI").read_bytes()


@pytest.fixture(autouse=True)
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "sim")
    return tmp_path / "sim"


def talk(job, text):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(job)["session_id"]
    return w, sid, w.talk(sid, text)


@pytest.mark.parametrize("said", ["C1C4を2回繰り返したい", "Ｃ１Ｃ４を２回繰り返したい", "繰り返したい", "2回繰り返したい",
                                  "もう1回繰り返して", "3回にして", "C00001からC00004を3回にして"])
def test_guide_shows_the_supported_range_and_never_makes_a_candidate(said):
    w, sid, r = talk(RP, said)
    h = r["hear"]
    assert "このJOBで変えられる範囲は C00001〜C00003 です" in h["message"]
    assert "のように言ってください" not in h["message"]  # 決まった文の言い直しを頼まない
    assert [c["label"] for c in h["choices"]] == ["C00001〜C00003 を3回にする（範囲を確かめる）", "今回は変更しない"]
    st = r["state"]
    assert st["candidate"] is None and st["repeat_range"] is None and st["approval"] is None


def test_different_range_is_explained_not_replaced():
    w, sid, r = talk(RP, "C1C4を2回繰り返したい")
    assert "ご指定の C00001〜C00004 とは範囲が違います" in r["hear"]["message"]
    assert r["state"]["candidate"] is None


@pytest.mark.parametrize("said,ambiguous", [("2回繰り返したい", True), ("もう1回繰り返して", False), ("3回にして", False)])
def test_ambiguous_count_is_not_read_as_two_to_three(said, ambiguous):
    w, sid, r = talk(RP, said)
    assert ("プログラムは決めません" in r["hear"]["message"]) is ambiguous
    assert r["state"]["candidate"] is None and r["state"]["repeat_range"] is None


def test_choice_goes_to_the_existing_range_confirmation_not_approval():
    w, sid, r = talk(RP, "C1C4を2回繰り返したい")
    pick = r["hear"]["choices"][0]["text"]
    r2 = w.hear(sid, pick)
    st = r2["state"]
    assert st["repeat_range"] is not None and st["candidate"] is None and st["approval"] is None
    assert (st["repeat_range"]["count"], st["repeat_range"]["total"]) == (2, 3)


def test_no_change_choice_does_nothing():
    w, sid, r = talk(RP, "繰り返したい")
    r2 = w.hear(sid, r["hear"]["choices"][1]["text"])
    assert r2["message"] == ut.EXPLAIN_REPLY and r2["state"]["candidate"] is None


@pytest.mark.parametrize("said", ["繰り返したい", "2回繰り返したい", "3回にして"])
def test_job_without_a_supported_range_says_so(said):
    w, sid, r = talk(RJ, said)
    h = r["hear"]
    assert "繰り返しの回数を変えられる範囲を確認できません" in h["message"] and not h["choices"]
    assert r["state"]["candidate"] is None


def test_move_instructions_and_existing_flows_are_unchanged():
    w, sid, r = talk(RP, "C00001からC00003を3回にして")
    assert r["state"]["repeat_range"] is not None and "repeat_guide" not in (r["hear"] or {})
    w, sid, r = talk(RJ, "Zプラス5ミリ")
    assert "repeat_guide" not in (r["hear"] or {})


def test_guide_is_not_shown_with_a_candidate_and_stale_choices_do_nothing_after_reload():
    w, sid, r = talk(RP, "C1C4を2回繰り返したい")
    pick = r["hear"]["choices"][0]["text"]
    sid2 = w.load(RJ)["session_id"]  # 別の JOB を読んだ：前の画面は閉じる
    r2 = w.hear(sid, pick)
    assert r2.get("session_closed") or r2.get("ok") is False or r2["state"]["repeat_range"] is None
    r3 = w.hear(sid2, pick)  # 新しい JOB（繰り返し無し）にその文を送っても範囲の確かめにならない
    assert r3["state"]["repeat_range"] is None and r3["state"]["candidate"] is None
