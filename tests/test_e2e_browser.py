"""ブラウザーでの通し確認（tools/e2e_browser.py）。playwright か Chromium が無ければ skip。"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

_spec = importlib.util.spec_from_file_location(
    "e2e_browser", Path(__file__).resolve().parent.parent / "tools" / "e2e_browser.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


def test_voice_to_export_in_browser(tmp_path):
    from playwright.sync_api import Error as PlaywrightError
    try:
        fails = e2e.run(tmp_path, log=lambda *_: None)
    except PlaywrightError as ex:
        if "Executable doesn't exist" in str(ex):
            pytest.skip("Playwright の Chromium が入っていません")
        raise
    assert fails == []
