"""クロークの照合の報告（reviews/REPORT_KUROKU_NL_CROSSCHECK_1106.md・XC-）の直し（本番候補 v1.0・11/7・シクロ）。
偽の Claude・合成 JOB だけ。実 API は呼ばない。同じ Claude が直して書いたテストなので、独立のレビューの代わりにはならない。"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import sim
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
RJ = (ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI").read_bytes()
CP = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
NEGATIONS = ["だめ", "ダメ", "ノー", "No", "出さないで", "きゃんせる", "あかん", "いいえ", "違う", "それじゃない", "いや",
             "だめです。", "ノー！"]


@pytest.fixture(autouse=True)
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "sim")
    return tmp_path / "sim"


def made(approve: bool):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(RJ)["session_id"]
    w.select(sid, "C00004")
    w.talk(sid, "Zプラス方向に5ミリ動かして")
    g = w.candidate_gen_of(sid)
    if approve:
        assert w.approve(sid, "QA", candidate_gen=g)["ok"]
    return w, sid, g


@pytest.mark.parametrize("said", NEGATIONS)
def test_xc02_negation_makes_unapproved_candidate_unapprovable(said):
    w, sid, g = made(False)
    r = w.talk(sid, said)
    assert r["state"]["hold"] is True
    assert not w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    assert not w.export(sid, candidate_gen=w.candidate_gen_of(sid))["ok"]


@pytest.mark.parametrize("said", NEGATIONS)
def test_xc08_negation_after_approval_revokes_the_approval(said):
    w, sid, g = made(True)
    r = w.talk(sid, said)
    assert r["state"]["approval"] is None and r["state"]["hold"] is True and r["state"]["can_export"] is False
    assert not w.export(sid, candidate_gen=g)["ok"]


@pytest.mark.parametrize("said", ["どうしよう", "どうしようかな", "うーん、どうかな", "どうかな", "どうだろう", "どうだろ"])
def test_xc03_hesitation_after_approval_holds(said):
    w, sid, g = made(True)
    r = w.talk(sid, said)
    assert r["state"]["hold"] is True and r["state"]["can_export"] is False
    assert not w.export(sid, candidate_gen=g)["ok"]


@pytest.mark.parametrize("said", ["はい", "OK", "このロボット何してるの？"])
def test_acks_and_plain_questions_keep_the_approval(said):
    w, sid, g = made(True)
    r = w.talk(sid, said)
    assert r["state"]["approval"] is not None and r["state"]["hold"] is False and r["state"]["can_export"] is True


@pytest.mark.parametrize("said", ["大丈夫？", "合ってる？", "本当に？"])
def test_xc07_confirm_question_is_answered_and_held(said):
    w, sid, g = made(True)
    r = w.talk(sid, said)
    assert r["hear"]["message"].startswith("確かめたいのですね。今の変更候補は C00004 の Z を 180.0 → 185.0 です")
    assert "保証ではありません" in r["hear"]["message"]
    assert r["state"]["hold"] is True and r["state"]["can_export"] is False


def facing_case():
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=None)
    sid = w.load(CP)["session_id"]
    w.select(sid, "C00003")
    return w, sid


def press(w, sid, r, label):
    ch = [c for c in (r.get("hear") or r).get("choices") or [] if c["label"] == label]
    assert ch, r
    return w.hear(sid, ch[0]["text"])


def test_xc04_r10_followup_then_button_makes_the_candidate():
    w, sid = facing_case()
    w.talk(sid, "もう少し右に寄せて")
    r = w.talk(sid, "5ミリ")
    r = press(w, sid, r, "合っている")
    c = w._entries[sid].session.candidate
    assert r["status"] == "ok" and c is not None and c.target == "C00003 Y"
    assert c.after_value - c.before_value == pytest.approx(5.0)


def test_xc05_after_no_side_words_get_a_clear_next_step():
    """［違う］のあとは同じ確かめを繰り返さない（11/5 の決定のまま）。右・左を言ったら、理由と次にすることを出す
    （前の「専門家が枠の設定で軸を決めるまで使えません」は、この場面の理由として分かりにくかった：XC-05）。"""
    w, sid = facing_case()
    r = w.talk(sid, "右に5ミリ")
    press(w, sid, r, "違う")
    r = w.talk(sid, "右に5ミリ")
    msg = r["hear"]["message"]
    assert w._entries[sid].session.candidate is None and not r["hear"]["choices"]
    assert "［違う］を選んだ" in msg and "Yプラス5ミリ" in msg and "専門家が枠" not in msg
    r = w.talk(sid, "Yプラス5ミリ")  # 案内のとおりに言えば候補になる
    assert w._entries[sid].session.candidate is not None


def test_xc01_confirmation_is_forgotten_when_the_position_changes():
    w, sid = facing_case()
    r = w.talk(sid, "右に5ミリ")
    press(w, sid, r, "合っている")
    w.undo(sid)
    w.select(sid, "C00004")
    r = w.talk(sid, "右に3ミリ")
    assert w._entries[sid].session.candidate is None
    assert [c["label"] for c in r["hear"]["choices"]] == ["合っている", "違う"]


@pytest.mark.parametrize("said,remade", [("3ミリに訂正して", None), ("3ミリに修正して", None), ("Zプラス3ミリに訂正して", 3.0)])
def test_xc19_teisei_is_a_correction_with_consistent_reply(said, remade):
    """XC-19（11/7）：「〜に訂正して」も言い直しの印（前に新しい内容）。古い候補・承認を取り消し、全部そろっていれば新しい
    未承認の候補。返事に「変更候補は残しています…承認してください」と「分かりませんでした」を並べない。"""
    w, sid, g = made(True)
    r = w.talk(sid, said)
    msg = r["hear"]["message"]
    assert "取り消しました" in msg and "分かりませんでした" not in msg and "変更候補は残しています" not in msg
    assert not w.approve(sid, "QA", candidate_gen=g)["ok"] and r["state"]["approval"] is None
    c = w._entries[sid].session.candidate
    if remade is None:
        assert c is None
    else:
        assert c is not None and c.after_value - c.before_value == pytest.approx(remade)
