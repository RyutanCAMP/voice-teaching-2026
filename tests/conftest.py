import pytest


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """テストでは本物の Claude を呼ばない（このPCの環境変数にキーがあっても使わない）。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
