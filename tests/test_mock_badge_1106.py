"""上の帯の MOCK の印（Issue #8 round4・11/6 唐川さんの決定）。実装より先に書いた。

- 解釈の係か説明の係のどちらかがモック（キーが無くて黙ってモックになった場合も）なら、上の帯に「MOCK」を出す
- 本物の Claude（解釈の係も説明の係も本物）のときは要素ごと出さない
- 判定はサーバーの状態から（画面で推測しない）。state.uses_mock と、配信する index.html の両方
- API は呼ばない（偽の client を渡すだけで、何も送らない）
"""
import threading
import urllib.request

from voice_teaching import claude_explain as cx
from voice_teaching import claude_interpreter as ci
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web import server
from voice_teaching.web.flow import WebFlow

DEMO = (__import__("pathlib").Path(__file__).resolve().parent.parent / "samples/dummy/DEMO_C_PLACE.JBI").read_bytes()


class NoCall:
    """messages.create を呼ばれたら失敗する偽の client（API を呼んでいないことの確かめ）"""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        raise AssertionError("API を呼んではいけない")


def real_pair():
    return (ci.ClaudeInterpreter("test-key-not-real", "claude-sonnet-5-5", client=NoCall()),
            cx.Explainer("test-key-not-real", "claude-sonnet-5-5", client=NoCall()))


def page(srv) -> str:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/").read().decode("utf-8")
    finally:
        srv.shutdown()
        srv.server_close()


def test_state_uses_mock_with_mock_interpreter():
    w = WebFlow(interpreter=MockInterpreter(), updown_config=None, explainer=real_pair()[1])
    sid = w.load(DEMO)["session_id"]
    assert w.state(sid)["state"]["uses_mock"] is True


def test_state_uses_mock_when_explainer_has_no_key():
    interp, _ = real_pair()
    w = WebFlow(interpreter=interp, updown_config=None, explainer=cx.Explainer("", "claude-sonnet-5-5"))
    sid = w.load(DEMO)["session_id"]
    assert w.state(sid)["state"]["uses_mock"] is True


def test_state_uses_mock_with_scripted_explainer():
    interp, _ = real_pair()
    w = WebFlow(interpreter=interp, updown_config=None, explainer=cx.Explainer("mock-no-network", "claude-mock-script", client=NoCall()))
    sid = w.load(DEMO)["session_id"]
    assert w.state(sid)["state"]["uses_mock"] is True


def test_state_not_mock_with_real_pair():
    interp, ex = real_pair()
    w = WebFlow(interpreter=interp, updown_config=None, explainer=ex)
    sid = w.load(DEMO)["session_id"]
    assert w.state(sid)["state"]["uses_mock"] is False


def test_no_key_env_falls_back_to_mock_and_shows_badge():
    # キーが無いと、解釈の係は黙ってモックになる（claude_interpreter.from_env）。そのときも MOCK を出す
    interp = ci.from_env({})
    ex = cx.Explainer.from_env({})
    assert interp.kind == "mock"
    html = page(server.make_server(port=0, interpreter=interp, updown_config=None, explainer=ex))
    assert 'id="mock-banner"' in html and ">MOCK<" in html


def test_page_shows_mock_badge_for_mock():
    html = page(server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=real_pair()[1]))
    assert 'id="mock-banner"' in html and ">MOCK<" in html
    assert "__VT_MOCK_BANNER__" not in html


def test_page_has_no_mock_badge_for_real():
    interp, ex = real_pair()
    html = page(server.make_server(port=0, interpreter=interp, updown_config=None, explainer=ex))
    assert 'id="mock-banner"' not in html and ">MOCK<" not in html
    assert "__VT_MOCK_BANNER__" not in html
    assert 'id="mode-banner"' in html  # SIM は残す
