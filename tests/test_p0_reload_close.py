"""P0：JOB を読み直したあと、前の画面の番号（session_id）を使えないこと（唐川さんの決定 Q-5・Q-7。2026-11-05）。

- Q-7：有効な作業画面は1つだけ。読み込んだら（X-VT-Previous-Session の有無に関係なく）ほかのすべての画面を閉じる
- Q-5：読み直しに失敗しても（読めないファイル）、前の画面を閉じる
- 閉じた画面への要求は、どの API でも状態を変えずに断る（session_closed: True）。サーバー側で断る
- 本物の Claude は呼ばない（偽の Claude・合成ジョブだけ）。出力先は一時フォルダ
- 同じ Claude が書いたテスト。外部の独立レビュー（チャッピー）の代わりにはならない
"""
import json
import threading
import time
from pathlib import Path

import pytest

import test_claude_talk as T
from test_claude_talk import running, post  # noqa: F401（HTTP のテストで使う fixture）
from voice_teaching import sim

CLOSED = ("この画面の作業は終わりました（新しいジョブが読み込まれたため）。変更候補・承認は使えません。"
          "新しい画面で続けてください")
BAD = b"\xff\xfe not a job"


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "out"
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", d)
    return d


def files(d: Path) -> list:
    return sorted(d.iterdir()) if d.exists() else []


def refused(r: dict) -> None:
    assert r.get("ok") is False, r
    assert r.get("session_closed") is True, r
    assert r.get("error") == CLOSED, r
    assert "state" not in r or r["state"] is None, r  # 閉じた画面の状態（候補・承認）は返さない


def dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def all_ops(w, sid, turn=1):
    """古い番号で、画面から呼べるすべての操作。"""
    return {
        "approve": lambda: w.approve(sid, "QA", candidate_gen=w.candidate_gen_of(sid)),
        "export": lambda: w.export(sid, candidate_gen=w.candidate_gen_of(sid)),
        "repeat": lambda: w.repeat_choose(sid, "yes", turn),
        "dialog": lambda: w.choose(sid, "pos:C00003", turn),
        "hear": lambda: w.hear(sid, "C00001からC00003の作業を2回から3回にして"),
        "talk": lambda: w.talk(sid, "C00002を選んで"),
        "talk_count": lambda: w.talk(sid, "3回にして"),
        "select": lambda: w.select(sid, "C00002"),
        "state": lambda: w.state(sid),
        "undo": lambda: w.undo(sid),
        "compare": lambda: w.compare(sid),
        "ask": lambda: w.ask(sid, "何をしている？"),
        "ask_sync": lambda: w.ask(sid, sync=True),
        "ask_info": lambda: w.ask(sid, info=True),
        "ask_reset": lambda: w.ask(sid, reset=True),
        "ask_choose": lambda: w.ask(sid, choose="confirm:yes", token="x"),
    }


def pending_shift(w):
    """位置の移動の候補（DEMO_C_PLACE）が承認待ち。"""
    sid = w.load(T.OTHER.read_bytes())["session_id"]
    w.select(sid, "C00003")
    r = w.hear(sid, "Y-方向に10mm")
    assert r.get("candidate_made") and r["state"]["can_approve"], r
    return sid


# ---- 1. 読み直し（previous あり・なし × 同じジョブ・別のジョブ）のあと、古い番号のすべての操作を断る ----
@pytest.mark.parametrize("with_previous", [True, False])
@pytest.mark.parametrize("same_job", [True, False])
@pytest.mark.parametrize("op", list(all_ops(None, None)))
def test_every_api_refuses_the_old_session_after_reload(tmp_path, out, with_previous, same_job, op):
    w, fake = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    assert T.repeat_to_three(w, old)["state"]["can_approve"]
    old_entry = w._entries[old]
    data = T.demo_bytes() if same_job else T.OTHER.read_bytes()
    new = (w.load(data, previous=old) if with_previous else w.load(data))["session_id"]
    assert new != old
    before = dump(w.state(new)["state"])
    n = len(fake.requests)
    refused(all_ops(w, old)[op]())
    assert files(out) == []                         # 出力のファイルができない
    assert len(fake.requests) == n                  # Claude を呼ばない
    assert dump(w.state(new)["state"]) == before    # 新しい画面の状態も変わらない
    assert list(w._entries) == [new]                # 有効な画面は1つだけ
    # 閉じた画面の候補・承認・範囲の確認は捨ててある
    assert old_entry.session.candidate is None and old_entry.approval is None
    assert old_entry.session.repeat_range is None and old_entry.dialog.step is None


def test_old_session_stays_refused_after_many_reloads(tmp_path, out):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    sid = old
    for _ in range(3):
        sid = w.load(T.demo_bytes(), previous=sid)["session_id"]
    refused(w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old)))
    refused(w.export(old, candidate_gen=w.candidate_gen_of(old)))
    assert files(out) == []


# ---- 2. 範囲の確認の最中に読み直し → 古い turn で［この範囲を繰り返す］ ----
@pytest.mark.parametrize("with_previous", [True, False])
def test_old_range_button_after_reload_makes_no_candidate(tmp_path, out, with_previous):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    h = w.hear(old, "C00001からC00003の作業を2回から3回にして", "text")
    turn = h["state"]["repeat_range"]["turn"]
    new = (w.load(T.demo_bytes(), previous=old) if with_previous else w.load(T.demo_bytes()))["session_id"]
    refused(w.repeat_choose(old, "yes", turn))
    refused(w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old)))
    st = w.state(new)["state"]
    assert st["candidate"] is None and st["repeat_range"] is None and not st["can_approve"]
    assert files(out) == []


# ---- 3. 会話の選択肢の途中で読み直し → 古い turn のボタン ----
def test_old_dialog_choice_after_reload_is_refused(tmp_path, out):
    w, _ = T.flow(tmp_path)
    other = T.OTHER.read_bytes()
    old = w.load(other)["session_id"]
    d = w.hear(old, "置くところを少し手前にして")["state"]["dialog"]
    assert d["step"] and d["choices"], d
    new = w.load(other, previous=old)["session_id"]
    before = dump(w.state(new)["state"])
    refused(w.choose(old, d["choices"][0]["id"], d["turn"]))
    assert dump(w.state(new)["state"]) == before


# ---- 4. 位置の移動の候補（別のジョブ）でも同じ ----
@pytest.mark.parametrize("with_previous", [True, False])
def test_old_shift_candidate_cannot_be_approved_after_reload(tmp_path, out, with_previous):
    w, _ = T.flow(tmp_path)
    old = pending_shift(w)
    w.load(T.demo_bytes(), previous=old) if with_previous else w.load(T.demo_bytes())
    refused(w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old)))
    refused(w.export(old, candidate_gen=w.candidate_gen_of(old)))
    assert files(out) == []


# ---- 5. 承認済み・未出力で読み直し → 古い番号の出力を断る ----
@pytest.mark.parametrize("with_previous", [True, False])
@pytest.mark.parametrize("same_job", [True, False])
def test_approved_not_exported_then_reload_refuses_old_export(tmp_path, out, with_previous, same_job):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    assert w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old))["ok"]
    data = T.demo_bytes() if same_job else T.OTHER.read_bytes()
    w.load(data, previous=old) if with_previous else w.load(data)
    refused(w.export(old, candidate_gen=w.candidate_gen_of(old)))
    assert files(out) == []


def test_stale_export_does_not_take_the_new_file_name(tmp_path, out):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    assert w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old))["ok"]
    new = w.load(T.demo_bytes(), previous=old)["session_id"]
    T.repeat_to_three(w, new)
    assert w.approve(new, "QA", candidate_gen=w.candidate_gen_of(new))["ok"]
    refused(w.export(old, candidate_gen=w.candidate_gen_of(old)))
    r = w.export(new, candidate_gen=w.candidate_gen_of(new))
    assert r["ok"], r
    assert [p.name for p in files(out)] == [r["file_name"]]


# ---- 6. 読み込みに失敗しても（Q-5）、古い番号を断る ----
@pytest.mark.parametrize("with_previous", [True, False])
@pytest.mark.parametrize("bad", [BAD, b"/JOB\r\n//POS\r\n"])
def test_failed_reload_still_closes_the_old_session(tmp_path, out, with_previous, bad):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    assert w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old))["ok"]
    r = w.load(bad, previous=old) if with_previous else w.load(bad)
    assert r["ok"] is False and r["error"]
    assert r.get("session_closed") is True  # 画面が「前の作業画面は終わりました」を出すための印
    for name, fn in all_ops(w, old).items():
        refused(fn())
    assert files(out) == [] and list(w._entries) == []


def test_failed_first_load_has_no_closed_mark(tmp_path):
    w, _ = T.flow(tmp_path)
    r = w.load(BAD)
    assert r["ok"] is False and not r.get("session_closed")


# ---- 7. 新しい番号は、ふつうに使える（候補 → 承認 → 出力） ----
@pytest.mark.parametrize("with_previous", [True, False])
def test_new_session_works_after_reload(tmp_path, out, with_previous):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    new = (w.load(T.demo_bytes(), previous=old) if with_previous else w.load(T.demo_bytes()))["session_id"]
    assert w.state(new)["ok"] is True
    assert T.repeat_to_three(w, new)["state"]["can_approve"]
    assert w.approve(new, "QA", candidate_gen=w.candidate_gen_of(new))["ok"]
    r = w.export(new, candidate_gen=w.candidate_gen_of(new))
    assert r["ok"], r
    assert [p.name for p in files(out)] == [r["file_name"]]


def test_new_session_works_after_failed_reload(tmp_path, out):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    w.load(BAD, previous=old)
    new = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, new)
    assert w.approve(new, "QA", candidate_gen=w.candidate_gen_of(new))["ok"] and w.export(new, candidate_gen=w.candidate_gen_of(new))["ok"]


# ---- 8. 人が教えた意味の引き継ぎ（同じ中身の読み直し・E2-1）は今までどおり ----
def test_taught_meaning_is_still_carried_over_and_old_is_closed(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = T.first_turn(w, fake)
    T.taught_button(w, fake, sid)
    sid2 = w.load(T.demo_bytes(), previous=sid)["session_id"]
    assert [x["text"] for x in T.meanings(w, sid2)] == ["ボタンを押している"]
    refused(w.ask(sid, sync=True))


def test_taught_meaning_is_not_carried_over_to_other_content(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = T.first_turn(w, fake)
    T.taught_button(w, fake, sid)
    sid2 = w.load(T.OTHER.read_bytes(), previous=sid)["session_id"]
    assert T.meanings(w, sid2) == []


# ---- 9. 同時の要求：古い番号の承認を錠の前で止めておいて読み直し → 承認は断られる ----
class GateLock:
    """錠を取ろうとしたことを知らせる錠（テストで順番を強制する）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, *a, **k):
        if threading.current_thread().name == "stale-approve":
            self.waiting.set()
        return self._lock.acquire(*a, **k)

    def release(self):
        self._lock.release()

    def locked(self):
        return self._lock.locked()

    __enter__ = acquire

    def __exit__(self, *a):
        self.release()


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("what", ["approve", "export"])
def test_concurrent_stale_request_waiting_for_the_lock_is_refused(tmp_path, out, what):
    w, _ = T.flow(tmp_path)
    old = w.load(T.demo_bytes())["session_id"]
    T.repeat_to_three(w, old)
    if what == "export":
        assert w.approve(old, "QA", candidate_gen=w.candidate_gen_of(old))["ok"]
    gen = w.candidate_gen_of(old)  # 世代は錠を取る前に先に取る（QA M-3：錠を待つのは approve・export 自身）
    e = w._entries[old]
    e.lock = GateLock()
    result, loaded = {}, {}
    e.lock.acquire()  # テストが錠を持つ：古い要求は錠の前で止まる
    try:
        call = (lambda: w.approve(old, "QA", candidate_gen=gen)) if what == "approve" else (lambda: w.export(old, candidate_gen=gen))
        a = threading.Thread(target=lambda: result.update(r=call()), name="stale-approve")
        a.start()
        assert e.lock.waiting.wait(5)  # 古い要求がエントリを見つけ、錠を待っている
        b = threading.Thread(target=lambda: loaded.update(r=w.load(T.demo_bytes(), previous=old)), name="reload")
        b.start()
        # 読み直しが前の画面を閉じるまで（直す前は、読み直しが錠を待たずに終わる）
        assert wait_until(lambda: old not in w._entries or not b.is_alive())
        assert wait_until(lambda: old not in w._entries)
    finally:
        e.lock.release()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    refused(result["r"])
    assert loaded["r"]["ok"] is True
    assert files(out) == []
    assert e.approval is None and e.session.candidate is None


# ---- 10. Claude に聞いている間に閉じられたら、答えを書き戻さない ----
def test_answer_is_not_written_back_when_closed_while_asking(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    e = w._entries[sid]
    fake.add(T.answer("同じ動きをくり返しています。", [21, 22]))
    real = w.explainer.ask
    seen = {}

    def ask_and_reload(*a, **k):
        seen["new"] = w.load(T.demo_bytes(), previous=sid)["session_id"]  # Claude の答えを待つ間に読み直す
        return real(*a, **k)

    w.explainer.ask = ask_and_reload
    r = w.ask(sid, "何をしている？")
    refused(r)
    assert "reply" not in r and "evidence" not in r
    assert e.talk is None or e.talk.last_evidence == ()
    assert seen["new"] in w._entries and list(w._entries) == [seen["new"]]


def test_talk_closed_while_asking_is_refused(tmp_path):
    w, fake = T.flow(tmp_path)
    sid = w.load(T.demo_bytes())["session_id"]
    fake.add(T.answer("同じ動きをくり返しています。", [21]))
    real = w.explainer.ask

    def ask_and_reload(*a, **k):
        w.load(T.demo_bytes())
        return real(*a, **k)

    w.explainer.ask = ask_and_reload
    refused(w.talk(sid, "このロボット、何をしている？"))


# ---- 11. HTTP でも、サーバー側で断る ----
def test_http_old_session_is_refused_after_reload(running, tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "out")
    srv, fake = running
    old = post(srv, "/api/load", T.demo_bytes())["session_id"]
    h = post(srv, "/api/hear", {"session_id": old, "text": "C00001からC00003の作業を2回から3回にして"})
    rp = post(srv, "/api/repeat", {"session_id": old, "choice": "yes", "turn": h["state"]["repeat_range"]["turn"]})
    gen = rp["state"]["candidate_gen"]
    assert post(srv, "/api/approve", {"session_id": old, "approver": "QA", "candidate_gen": gen})["ok"]
    new = post(srv, "/api/load", T.demo_bytes(), headers={"X-VT-Previous-Session": old})["session_id"]
    for path, body in [("/api/approve", {"approver": "QA", "candidate_gen": gen}), ("/api/export", {"candidate_gen": gen}),
                       ("/api/state", {}),
                       ("/api/hear", {"text": "3回にして"}), ("/api/talk", {"text": "3回にして"}),
                       ("/api/select", {"position": "C00002"}), ("/api/undo", {}), ("/api/compare", {}),
                       ("/api/dialog", {"choice": "x", "turn": 1}), ("/api/repeat", {"choice": "yes", "turn": 1}),
                       ("/api/ask", {"sync": True})]:
        refused(post(srv, path, {"session_id": old, **body}))
    assert files(tmp_path / "out") == []
    assert post(srv, "/api/state", {"session_id": new})["ok"] is True
    r = post(srv, "/api/load", BAD, headers={"X-VT-Previous-Session": new})
    assert r["ok"] is False and r["session_closed"] is True
    refused(post(srv, "/api/state", {"session_id": new}))


# ---- 12. 同じ時間に2つ読み込んでも、有効な画面は1つだけ ----
def test_two_concurrent_loads_leave_one_live_session(tmp_path):
    w, _ = T.flow(tmp_path)
    got = []
    ts = [threading.Thread(target=lambda: got.append(w.load(T.demo_bytes())["session_id"])) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(got) == 8 and len(w._entries) == 1
    live = [sid for sid in got if w.state(sid)["ok"]]
    assert live == list(w._entries)
    for sid in got:
        if sid not in live:
            refused(w.state(sid))
