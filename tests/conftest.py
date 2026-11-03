import pytest


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """テストでは本物の Claude を呼ばない（このPCの環境変数にキーがあっても使わない）。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)


@pytest.fixture
def speed_enabled(monkeypatch):
    """速度の変更は既定で止めている（jbi.SPEED_CHANGE_ENABLED = False）。検査の中身を試すテストだけ True にする。

    既定で止まることは、各層の test_*speed*_disabled_by_default で別に確かめる。
    """
    from voice_teaching import jbi
    monkeypatch.setattr(jbi, "SPEED_CHANGE_ENABLED", True)


@pytest.fixture(autouse=True)
def _sim_out_dir(request, monkeypatch, tmp_path):
    """シミュレーション用の出力先（sim.DEFAULT_OUT_DIR。呼び出し側からは選べない）を、テストでは一時フォルダにする。

    リポジトリの output/sim にテストのファイルを書かないため。@pytest.mark.real_out_dir のテストだけ本物の値を見る。
    """
    if request.node.get_closest_marker("real_out_dir"):
        return
    from voice_teaching import sim
    monkeypatch.setattr(sim, "DEFAULT_OUT_DIR", tmp_path / "sim")


# ---- 上下の向きの設定（CHECKER_026 M2）。テストは一時フォルダの設定だけを使う ----
# ここで作る設定は「テストのための仮の記録」で、実機で上下の向きを確かめたものではない。records/ には書かない
TEST_CONFIRMED_BY = "テスト（一時フォルダの仮の設定）"
TEST_EVIDENCE = "テスト用の仮の設定。実機・ペンダントで確かめたものではない"
TEST_CONFIRMED_AT = "2026-11-03T14:00:00+09:00"


@pytest.fixture
def make_updown(tmp_path):
    """一時フォルダに、上下の向きの設定ファイルを作る（updown.build_config と write_config を使う。中身は本物と同じ形）。"""
    from pathlib import Path

    from voice_teaching import jbi, sim, updown

    def make(job="DEMO_C_PLACE.JBI", positions=("C00005",), robot=updown.DEFAULT_ROBOT, name="updown.json"):
        data = job if isinstance(job, (bytes, bytearray)) else (Path(sim.SAMPLES_DIR) / job).read_bytes()
        obj = updown.build_config(jbi.parse_bytes(bytes(data)), list(positions), robot, TEST_CONFIRMED_BY,
                                  TEST_EVIDENCE, confirmed_at=TEST_CONFIRMED_AT)
        return updown.write_config(obj, tmp_path / "updown" / name, overwrite=True)
    return make


@pytest.fixture
def use_updown(make_updown, monkeypatch):
    """WebFlow の既定の上下の向きの設定を、一時フォルダの確認済みの設定にする（上・下を Z± として読むテスト用）。"""
    from voice_teaching.web import flow

    def use(job="DEMO_C_PLACE.JBI", positions=("C00005",), **kw):
        path = make_updown(job, positions, **kw)
        monkeypatch.setattr(flow, "DEFAULT_UPDOWN_CONFIG", path)
        return path
    return use


@pytest.fixture
def updown_demo_c(use_updown):
    """DEMO_C_PLACE（C00005 の値を伏せる）の確認済みの設定を、WebFlow の既定にする。"""
    return use_updown("DEMO_C_PLACE.JBI", ("C00005",))


@pytest.fixture
def updown_demo_place(use_updown):
    """DEMO_PLACE（C00004 の値を伏せる）の確認済みの設定を、WebFlow の既定にする。"""
    return use_updown("DEMO_PLACE.JBI", ("C00004",))


@pytest.fixture
def updown_ok(make_updown):
    """DEMO_C_PLACE と照合した、確認済みの照合の結果（utterance.pre_check に渡す。一時フォルダの設定から作る）。"""
    from voice_teaching import jbi, sim, updown
    path = make_updown("DEMO_C_PLACE.JBI", ("C00005",), name="updown_ok.json")
    st = updown.check(jbi.load(sim.SAMPLES_DIR / "DEMO_C_PLACE.JBI"), path)
    assert st.confirmed, st
    return st
