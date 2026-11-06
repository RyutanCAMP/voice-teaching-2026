"""文の改善 K2〜K7（2026-11-06 唐川さんの許可：「次にどうすればいいか」が伝わる返答を優先）。

REPORT_KUROKU_NL_QUALITY_1106 の R3〜R7 と、表示されない説明（K7）。安全ガード・承認と出力の条件・座標の解釈の範囲は変えない：
- どの場面も、新しい候補・範囲の確認を作らない／承認を残す・消すの決まりは今のまま（K1 は別のコミット）
- 数はプログラムが入れる（人の言った数をそのまま例に使わない）
偽の Claude・合成ジョブだけ。同じ Claude（クローク）が書いたテスト。
"""
import pytest

import test_claude_talk as T
from voice_teaching import claude_explain, sim

CP = T.OTHER.read_bytes()
BLOCK = (21, 22, 23, 24, 25, 26)
NEXT_Q = ["このあとどうすればいい？", "次は何をすればいい？", "どうやって使うの？", "次どうしたらいい？", "使い方を教えて"]


@pytest.fixture(autouse=True)
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")


def start(tmp_path, data=CP, *responses):
    w, fake = T.flow(tmp_path, *responses)
    return w, fake, w.load(data)["session_id"]


def snap(w, sid):
    st = w.state(sid)["state"]
    return (str(st["candidate"]), st["approval"] is not None, st["hold"], st["repeat_range"], w.candidate_gen_of(sid))


# ---- K2：「このあとどうすればいい？」はアプリの決まった文で、今の状態に合わせて答える（Claude に送らない・状態を変えない） ----
@pytest.mark.parametrize("said", NEXT_Q)
@pytest.mark.parametrize("where", ["start", "selected", "pending", "approved", "hold"])
def test_k2_next_step_question_is_answered_by_the_app(tmp_path, said, where):
    w, fake, sid = start(tmp_path, CP)
    if where != "start":
        w.select(sid, "C00005")
    if where in ("pending", "approved", "hold"):
        w.talk(sid, "Zプラス方向に5ミリ")
    if where == "approved":
        assert w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid))["ok"]
    if where == "hold":
        w.talk(sid, "もうちょっと")
    before = snap(w, sid)
    r = w.talk(sid, said, "voice")
    after = snap(w, sid)
    assert not fake.requests  # Claude に送らない
    msg = (r.get("hear") or {}).get("message") or ""
    if where == "approved" and after != before:
        # 承認のあと、ふつうの質問と読めない文（「次どうしたらいい？」）は、今ある安全の決まりで保留になる（K2 では緩めない）。
        # 保留だけ（候補・世代は同じ）で、返事は保留の文
        assert after[2] is True and (after[0], after[4]) == (before[0], before[4]), (said, after)
        assert "このままでいい" in msg, msg
        return
    assert after == before, (where, said)  # 候補・承認・保留・範囲・世代を変えない
    want = {"start": "位置を選んで", "selected": "C00005", "pending": "承認ボタン", "approved": "シミュレーション用に出力",
            "hold": "このままでいい"}[where]
    assert want in msg, (where, msg)
    assert "表示しません" not in msg


def test_k2_change_with_how_question_is_not_k2(tmp_path):
    """変更の言葉がある文（「Zプラス3ミリにするにはどうすればいい？」）は今までの道。K2 の決まった文にしない。"""
    w, _, sid = start(tmp_path, CP, *[T.answer("この位置へ動く動作です。", BLOCK) for _ in range(3)])
    w.select(sid, "C00005")
    r = w.talk(sid, "Zプラス3ミリにするにはどうすればいい？", "voice")
    assert "位置を選んで" not in ((r.get("hear") or {}).get("message") or "")


# ---- K3：数だけ（「3」）：何を言えばよいかを案内する。候補は作らない・人の数を例に使わない ----
@pytest.mark.parametrize("said", ["3", "３", "3。", "10"])
def test_k3_bare_number_guides(tmp_path, said):
    w, fake, sid = start(tmp_path, CP)
    w.select(sid, "C00005")
    r = w.talk(sid, said, "voice")
    st = r["state"]
    assert st["candidate"] is None and st["repeat_range"] is None and not fake.requests
    msg = (r.get("hear") or {}).get("message") or ""
    assert "軸と向きと量" in msg and "回にして" in msg, msg
    assert "Zプラス3ミリ" not in msg and "Zプラス10ミリ" not in msg


# ---- K4：上限を超えたとき：同じことを2回言わない・次にすること（上限以下で言い直す）を案内する ----
@pytest.mark.parametrize("said", ["Zプラス30ミリ", "Zプラス方向に21ミリ", "Yマイナス50ミリ動かして"])
def test_k4_over_limit_says_next_step_once(tmp_path, said):
    w, _, sid = start(tmp_path, CP)
    w.select(sid, "C00005")
    r = w.talk(sid, said, "voice")
    st = r["state"]
    assert st["candidate"] is None and st["approval"] is None
    msg = (r.get("hear") or {}).get("message") or ""
    assert "変更しません" in msg and msg.count("仮の上限") == 1, msg
    assert "20ミリ以下の量で、もう一度言ってください" in msg, msg


# ---- K5：回数を断るとき：Claude の「受け取りました」を出さず、断りの文に次にすることを足す ----
@pytest.mark.parametrize("said,to", [("4回にして", 4), ("10回繰り返して", 10), ("1回にして", 1)])
def test_k5_count_refusal_is_one_clear_message(tmp_path, said, to):
    w, fake, sid = start(tmp_path, T.demo_bytes(), T.answer("回数のご希望ですね。", BLOCK, "change_request",
                                                           {"kind": "repeat", "from_count": 2, "to_count": to}))
    r = w.talk(sid, said, "voice")
    st = r["state"]
    assert st["candidate"] is None and st["repeat_range"] is None
    ask = r.get("ask") or {}
    shown = " ".join([ask.get("reply") or "", " ".join(ask.get("notes") or []), (r.get("hear") or {}).get("message") or ""])
    assert "受け取りました" not in shown and "範囲と回数は、アプリの表示で確かめてください" not in shown, shown
    assert "範囲のボタン" not in shown, shown
    assert "「3回にして」" in shown, shown


def test_k5_direct_count_refusal_says_next_step(tmp_path):
    w, _, sid = start(tmp_path, T.demo_bytes())
    r = w.hear(sid, "C00001からC00003の作業を2回から4回にして", "text")
    assert not r.get("candidate_made") and not r["state"]["repeat_range"]
    assert "4回にはしません" in r["message"] and "「3回にして」" in r["message"], r["message"]


def test_k5_two_to_three_is_unchanged(tmp_path):
    w, _, sid = start(tmp_path, T.demo_bytes())
    T.repeat_to_three(w, sid)
    assert w.state(sid)["state"]["candidate"] is not None


# ---- K6：「斜め」：斜めには動かせないこと・1つの軸ずつ言うことを案内する（候補は作らない） ----
@pytest.mark.parametrize("said", ["斜め上に3ミリ", "斜め下に3mm", "ななめに3ミリ"])
def test_k6_diagonal(tmp_path, said):
    w, _, sid = start(tmp_path, CP, *[T.answer("この位置へ動く動作です。", BLOCK) for _ in range(3)])
    w.select(sid, "C00005")
    r = w.talk(sid, said, "voice")
    assert r["state"]["candidate"] is None
    msg = (r.get("hear") or {}).get("message") or ""
    assert "斜めには動かせない" in msg and "1つの軸ずつ" in msg and "分かりませんでした" not in msg, msg


# ---- K7：説明が表示されない原因（ジョブの信号の命令を「出力をオン」と言う）を、Claude への頼み方で避ける。表示の決まりは同じ ----
def test_k7_prompt_says_signal_not_output():
    assert "「出力」" in claude_explain.SYSTEM and "信号を出す" in claude_explain.SYSTEM


def test_k7_guard_still_hides_output_word(tmp_path):
    """頼み方を変えても、Claude の答えに「出力」があれば今までどおり表示しない（決まりは緩めない）。"""
    w, fake, sid = start(tmp_path, CP, T.answer("Z方向へ動いて出力をオンにし、待ってから戻ります。", BLOCK))
    r = w.talk(sid, "何をする動き？", "voice")
    ask = r.get("ask") or {}
    assert "出力をオン" not in (ask.get("reply") or "")
