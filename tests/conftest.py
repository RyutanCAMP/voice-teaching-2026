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
