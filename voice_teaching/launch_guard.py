"""起動のときの決まり：本物の Claude が使えるか、モックを明示したかを確かめる（11/6 唐川さんの決定）。

- filming / release：本物の Claude が使えない設定（解釈の係がモック、または説明の係 Explainer にキーが無い）なら起動を止める。
  .env を読めない（UTF-8 でない・開けない）ときも止める。モック（--mock・tools/demo_mock.py）と一緒なら止める
- dev：モックは明示したとき（--mock・tools/demo_mock.py）だけ。付けずにキーが無ければ、dev でも止める
- 文にはキーの値を入れない（決まった文だけ）

claude_interpreter.from_env の決まり（キーが無ければ MockInterpreter を返す）は変えない。起動の側で
interp.kind と explainer.has_key を見て判断する。実行中の API のエラーはモックへ切り替えず止める作りのまま。
"""
from __future__ import annotations

from . import buildinfo, claude_interpreter

STRICT_CHANNELS = ("filming", "release")
MOCK_FLAG = "--mock"
MOCK_BANNER = "モックで起動しています（自動テスト・試し用。撮影には使いません）"
NO_REAL_DEV = ("本物の Claude のキーがありません（ANTHROPIC_API_KEY が無いか空、または .env を読めません）。"
               "起動を止めます。モックで試すときは --mock を付けてください")
NO_REAL_STRICT = ("{channel} のチャンネルでは本物の Claude が必要です。本物の Claude のキーがありません"
                  "（ANTHROPIC_API_KEY が無いか空、または .env を読めません）。起動を止めます")
DOTENV_UNREADABLE_STRICT = ("{channel} のチャンネルでは .env を読めないまま起動しません（UTF-8 で保存してください）。"
                            "起動を止めます")
MOCK_WITH_STRICT = "モックは {channel} のチャンネルでは使えません（撮影・公開にモックは使いません）。起動を止めます"
BAD_CHANNEL = "チャンネルは " + " / ".join(buildinfo.CHANNELS) + " のどれかです。起動を止めます"
EXIT_REFUSED = 2


def channel_of(arg: str | None) -> str:
    """起動の引数 → 環境変数 VT_CHANNEL → dev（buildinfo.channel_from と同じ決め方）。"""
    return buildinfo.channel_from(arg)


def refuse_mock(channel: str) -> str | None:
    """モックで起動してよいか。だめなら理由の文（キーは入らない）。"""
    if channel not in buildinfo.CHANNELS:
        return BAD_CHANNEL
    if channel in STRICT_CHANNELS:
        return MOCK_WITH_STRICT.format(channel=channel)
    return None


def refuse_real(channel: str, interp, explainer, dotenv_status: str | None = None) -> str | None:
    """--mock なしの起動で、本物の Claude が使えるか。だめなら理由の文（キーは入らない）。

    解釈の係（interp.kind == "claude"）と説明の係（explainer.has_key）の両方が要る。片方だけでも止める。
    """
    if channel not in buildinfo.CHANNELS:
        return BAD_CHANNEL
    strict = channel in STRICT_CHANNELS
    if strict and dotenv_status == claude_interpreter.DOTENV_UNREADABLE:
        return DOTENV_UNREADABLE_STRICT.format(channel=channel)
    real = getattr(interp, "kind", None) == "claude" and getattr(explainer, "has_key", False) is True
    if real:
        return None
    return NO_REAL_STRICT.format(channel=channel) if strict else NO_REAL_DEV
