"""版・チャンネル・Git の SHA の表示（11/6 唐川さん・Issue #8 と一緒に）。実装より先に書いた。

決まり（CHANGELOG.md「版の決まり」）：
- 番号は voice_teaching/__init__.py の __version__ の1か所だけ。画面の版の文字はこの値から作る
- チャンネルは dev / filming / release の3つだけ。決め方は1か所（起動の引数 --channel、無ければ環境変数 VT_CHANNEL、無ければ dev）。
  3つ以外の値は起動を止める（ValueError・起動の道具では終了）
- 表示：dev は「v1.00-dev」、filming は「v1.00 撮影用」、release は「v1.00」。__version__ の「-dev」は番号から外して使い、
  __version__ が「-dev」のまま（タグ前）の間は release を断る
- SHA：git rev-parse --short HEAD。未コミットの変更があれば「-dirty」。取れなければ「unknown」（起動は止めない）
"""
import subprocess
import urllib.request

import pytest

import voice_teaching
from voice_teaching import buildinfo
from voice_teaching.web import server


def test_number_comes_only_from_dunder_version(monkeypatch):
    monkeypatch.setattr(voice_teaching, "__version__", "3.21-dev")
    b = buildinfo.resolve("dev", sha="abc1234")
    assert b.version == "3.21-dev" and b.number == "3.21"
    assert b.label == "v3.21-dev"
    assert buildinfo.resolve("filming", sha="abc1234").label == "v3.21 撮影用"


def test_labels_for_the_three_channels(monkeypatch):
    monkeypatch.setattr(voice_teaching, "__version__", "1.00")
    assert buildinfo.resolve("dev", sha="x").label == "v1.00-dev"
    assert buildinfo.resolve("filming", sha="x").label == "v1.00 撮影用"
    assert buildinfo.resolve("release", sha="x").label == "v1.00"
    assert buildinfo.resolve("dev", sha="a1b2c3d").detail == "v1.00-dev（dev・a1b2c3d）"


def test_release_is_refused_while_version_is_dev(monkeypatch):
    monkeypatch.setattr(voice_teaching, "__version__", "1.00-dev")
    with pytest.raises(ValueError):
        buildinfo.resolve("release", sha="x")


@pytest.mark.parametrize("bad", ["", "Dev", "prod", "release ", "撮影", "filming;rm"])
def test_unknown_channel_stops(bad):
    with pytest.raises(ValueError):
        buildinfo.resolve(bad, sha="x")


def test_channel_from_env_and_default(monkeypatch):
    monkeypatch.delenv("VT_CHANNEL", raising=False)
    assert buildinfo.channel_from(None) == "dev"
    monkeypatch.setenv("VT_CHANNEL", "filming")
    assert buildinfo.channel_from(None) == "filming"
    assert buildinfo.channel_from("dev") == "dev"  # 引数が先
    monkeypatch.setenv("VT_CHANNEL", "bogus")
    with pytest.raises(ValueError):
        buildinfo.resolve(buildinfo.channel_from(None), sha="x")


def test_sha_unknown_when_git_fails(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", boom)
    assert buildinfo.git_sha() == "unknown"


def test_sha_dirty_mark(monkeypatch):
    class R:
        def __init__(self, out, code=0):
            self.stdout, self.returncode = out, code
    calls = []

    def fake(cmd, **k):
        calls.append(cmd)
        return R("d6f8b2c\n") if "rev-parse" in cmd else R(" M voice_teaching/web/flow.py\n")
    monkeypatch.setattr(subprocess, "run", fake)
    assert buildinfo.git_sha() == "d6f8b2c-dirty"

    def clean(cmd, **k):
        return R("d6f8b2c\n") if "rev-parse" in cmd else R("")
    monkeypatch.setattr(subprocess, "run", clean)
    assert buildinfo.git_sha() == "d6f8b2c"

    def bad(cmd, **k):
        return R("", 128)
    monkeypatch.setattr(subprocess, "run", bad)
    assert buildinfo.git_sha() == "unknown"


def test_server_serves_label_and_api(monkeypatch):
    monkeypatch.setattr(buildinfo, "git_sha", lambda: "abc1234")
    srv = server.make_server(port=0, channel="filming")
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        n = voice_teaching.__version__.removesuffix("-dev")
        assert f"v{n} 撮影用" in page and f"v{n} 撮影用（filming・abc1234）" in page
        assert "__VT_BUILD_LABEL__" not in page and "__VT_BUILD_DETAIL__" not in page
        assert f"Ver.{voice_teaching.__version__}" in page  # アプリ情報の今の版の文字は今どおり
        import json
        v = json.loads(urllib.request.urlopen(base + "/api/version").read().decode("utf-8"))
        assert v == {"ok": True, "version": voice_teaching.__version__, "channel": "filming", "sha": "abc1234",
                     "label": f"v{n} 撮影用", "detail": f"v{n} 撮影用（filming・abc1234）"}
    finally:
        srv.shutdown()
        srv.server_close()


def test_make_server_default_channel_is_dev(monkeypatch):
    monkeypatch.delenv("VT_CHANNEL", raising=False)
    monkeypatch.setattr(buildinfo, "git_sha", lambda: "unknown")
    srv = server.make_server(port=0)
    try:
        assert srv.build.channel == "dev" and srv.build.sha == "unknown"
    finally:
        srv.server_close()


def test_make_server_refuses_bad_channel():
    with pytest.raises(ValueError):
        server.make_server(port=0, channel="prod")


@pytest.mark.parametrize("mod", ["voice_teaching.web.__main__", "demo_mock", "debug_review_only"])
def test_launchers_accept_only_three_channels(mod, monkeypatch):
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    m = importlib.import_module(mod)
    with pytest.raises(SystemExit):
        m.main(["--channel", "prod", "--port", "0"])
