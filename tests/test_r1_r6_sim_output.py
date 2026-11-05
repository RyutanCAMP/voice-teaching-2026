"""クロちゃんの独立レビュー CHECKER_019 の R1・R6 の受入テスト。

R1：実機用の検査で止まった（blocked_reason のある）変更候補は、合成ジョブであっても承認も出力もしない。
    sim.approve・sim.export_sim・WebFlow.approve/export・HTTP の /api/approve・/api/export のどれからでも断る。
    対象は samples/dummy のすべての合成ジョブ × すべての位置のうち、実機用の検査で止まるもの全部
    （CHECKER_019 が挙げた合成ジョブ7種類を含む範囲）。正しい変更の承認・出力は今までどおり。
R6：シミュレーション用の出力は、拡張子 .JBI.txt で、シミュレーション専用のフォルダ（sim.DEFAULT_OUT_DIR）だけに書く。
    出力先を呼び出し側から指定する引数は無い。JBI の中身に警告のコメントは足さない。
    これらは実機での誤使用を完全に防ぐものではない。
"""
import http.client
import inspect
import json
import threading
from pathlib import Path

import pytest

from voice_teaching import jbi, sim
from voice_teaching.web import server
from voice_teaching.web.flow import WebFlow

DUMMY = Path(__file__).resolve().parent.parent / "samples" / "dummy"


def _blocked_cases():
    """実機用の検査（jbi.plan_shift）で止まり、シミュレーションの候補は作れる (ファイル, 位置) の組をすべて集める"""
    cases = []
    for path in sorted(DUMMY.glob("*.JBI")):
        try:
            job = jbi.load(path)
        except jbi.JbiError:
            continue  # 読み込めないファイルは候補も作れない
        for name in sorted(job.positions):
            try:
                job.plan_shift(name, "Z", 5.0)
                continue  # 検査を通る
            except jbi.JbiError:
                pass
            try:
                p = sim.simulate_shift(job, name, "Z", 5.0)
            except sim.SimError:
                continue  # シミュレーションの候補も作れない
            assert p.blocked_reason
            cases.append((path.name, name))
    return cases


BLOCKED = _blocked_cases()


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_blocked_cases_cover_many_synthetic_jobs():
    files = {f for f, _ in BLOCKED}
    assert len(files) >= 7, files
    assert ("DEMO_PLACE.JBI", "C00000") in BLOCKED  # 共用の位置


# ---- R1：sim の層 ----
@pytest.mark.parametrize("name,position", BLOCKED)
def test_sim_approve_refuses_blocked_candidate(name, position):
    p = sim.simulate_shift(jbi.load(DUMMY / name), position, "Z", 5.0)
    with pytest.raises(sim.SimError, match="実機用の検査で止まった"):
        sim.approve(p, "テスト担当")


@pytest.mark.parametrize("name,position", BLOCKED)
def test_sim_export_refuses_blocked_candidate_even_with_matching_approval(name, position, tmp_path):
    """承認の記録を直接作っても（ハッシュが合っていても）、止まった候補は出力しない"""
    p = sim.simulate_shift(jbi.load(DUMMY / name), position, "Z", 5.0)
    forged = sim.Approval(
        original_sha256=sim._sha(p.original_bytes), new_sha256=sim._sha(p.new_bytes),
        approved_by="テスト担当", approved_at="2026-11-02T00:00:00+00:00")
    with pytest.raises(sim.SimError, match="実機用の検査で止まった"):
        sim.export_sim(p, forged)
    assert _files(tmp_path) == []


# ---- R1：画面の流れ（WebFlow）の層 ----
@pytest.mark.parametrize("name,position", BLOCKED)
def test_flow_refuses_to_approve_or_export_blocked_candidate(name, position, tmp_path):
    wf = WebFlow()
    sid = wf.load((DUMMY / name).read_bytes())["session_id"]
    r = wf.hear(sid, f"{position}をZプラス方向に5mm移動して")
    st = r["state"]
    assert st["candidate"] is not None and st["blocked_reason"], r
    assert st["can_approve"] is False
    a = wf.approve(sid, "テスト太郎", candidate_gen=wf.candidate_gen_of(sid))
    assert a["ok"] is False and "実機用の検査で止まった" in a["error"]
    assert wf.state(sid)["state"]["approval"] is None
    e = wf.export(sid, candidate_gen=wf.candidate_gen_of(sid))
    assert e["ok"] is False
    assert _files(tmp_path) == []


# ---- R1：HTTP の API を直接呼ぶ ----
@pytest.fixture(scope="module")
def running():
    """サーバーはこのファイルのテストで使い回す（出力先 sim.DEFAULT_OUT_DIR は出力のたびに読むので、テストごとの差し替えが効く）"""
    srv = server.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _post(srv, path, body):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    ctype = "application/octet-stream" if isinstance(body, bytes) else "application/json"
    conn.request("POST", path, body=data, headers={"Content-Type": ctype})
    r = conn.getresponse()
    out = json.loads(r.read())
    conn.close()
    return out


@pytest.mark.parametrize("name,position", BLOCKED)
def test_http_api_refuses_blocked_candidate(running, name, position, tmp_path):
    sid = _post(running, "/api/load", (DUMMY / name).read_bytes())["session_id"]
    h = _post(running, "/api/hear", {"session_id": sid, "text": f"{position}をZプラス方向に5mm移動して", "source": "text"})
    assert h["state"]["blocked_reason"]
    gen = h["state"]["candidate_gen"]
    a = _post(running, "/api/approve", {"session_id": sid, "approver": "テスト太郎", "candidate_gen": gen})
    assert a["ok"] is False
    e = _post(running, "/api/export", {"session_id": sid, "candidate_gen": gen})
    assert e["ok"] is False
    assert _files(tmp_path) == []


# ---- 正しい変更の承認・出力は壊れない ----
@pytest.mark.parametrize("name,position,before,after", [
    ("DEMO_C_PLACE.JBI", "C00005",
     b"C00005=400.000,150.000,200.000,180.00,0.00,0.00", b"C00005=400.000,150.000,210.000,180.00,0.00,0.00"),
    ("DEMO_PLACE.JBI", "C00004",
     b"C00004=450.000,190.000,120.000,180.00,0.00,0.00", b"C00004=450.000,190.000,130.000,180.00,0.00,0.00"),
])
def test_http_api_approves_and_exports_passing_change(running, name, position, before, after, tmp_path,
                                                      make_updown, monkeypatch):
    data = (DUMMY / name).read_bytes()
    # 上下の向き（CHECKER_026 M2）：「上に10mm」を Z+10 と読むには、確認済みの設定が要る。
    # このテストのジョブの、一時フォルダの設定をサーバーに当てる（読み込みの前に。テストの後で元に戻る）
    monkeypatch.setattr(running.flow, "updown_config", make_updown(name, (position,)))
    sid = _post(running, "/api/load", data)["session_id"]
    assert _post(running, "/api/select", {"session_id": sid, "position": position})["ok"] is True
    h = _post(running, "/api/hear", {"session_id": sid, "text": "この位置を上に10mm", "source": "text"})
    assert h["state"]["blocked_reason"] is None and h["state"]["can_approve"] is True, h
    gen = h["state"]["candidate_gen"]
    assert _post(running, "/api/approve", {"session_id": sid, "approver": "テスト太郎", "candidate_gen": gen})["ok"] is True
    e = _post(running, "/api/export", {"session_id": sid, "candidate_gen": gen})
    assert e["ok"] is True, e
    out = Path(e["path"])
    assert out.name == name.replace(".JBI", "_SIM.JBI.txt")
    assert out.parent == sim.DEFAULT_OUT_DIR.resolve()
    new = out.read_bytes()
    diff = [(a, b) for a, b in zip(data.splitlines(), new.splitlines()) if a != b]
    stem = name[:-4].encode()
    assert diff == [(b"//NAME " + stem, b"//NAME " + stem + b"_SIM"), (before, after)]
    assert len(new.splitlines()) == len(data.splitlines())  # 警告のコメントなどの行は足さない
    assert (DUMMY / name).read_bytes() == data  # 元のジョブは変わらない


# ---- R6：出力先は固定・拡張子は .JBI.txt ----
def test_output_location_cannot_be_chosen_by_caller():
    assert "out_dir" not in inspect.signature(sim.export_sim).parameters
    assert "out_dir" not in inspect.signature(WebFlow).parameters
    assert "out_dir" not in inspect.signature(server.make_server).parameters


def test_sim_output_extension_is_jbi_txt():
    assert sim.SIM_EXTENSION == ".JBI.txt"


@pytest.mark.real_out_dir
def test_fixed_output_folder_is_output_sim_in_repo():
    assert sim.DEFAULT_OUT_DIR == sim.REPO_ROOT / "output" / "sim"
