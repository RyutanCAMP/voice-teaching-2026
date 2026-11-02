"""音声入力の安定化（2026-11-03 朝の指示）の受入テスト。

唐川さんの朝の確認で「音声認識の通信に失敗しました」が出た。調べた結果（docs/VOICE_DIAGNOSIS.md）：
- 朝の画面は Google Chrome ではなく、ChatGPT（Codex）のアプリの中のブラウザーで開かれていた（そのブラウザーの履歴に 05:35 の訪問）
- この PC の Google Chrome 156・実マイクでは、ブラウザー標準で「この位置を上に2 mm」と文字になった
- 「端末内のみ」は、この PC の Chrome 156 で、話し終わるとタブが落ちた（3回とも）

ここでは、画面の決まりとプログラムの読み方を確かめる（実マイクの確認は docs/VOICE_DIAGNOSIS.md）。
"""
import re
from pathlib import Path

import pytest

from voice_teaching.utterance import pre_check, read_utterance, OK
from voice_teaching.web.flow import WebFlow

STATIC = Path(__file__).resolve().parent.parent / "voice_teaching" / "web" / "static"
DEMO_C = (Path(__file__).resolve().parent.parent / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def js():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def voice(j=None):
    j = j or js()
    return j[j.index("// ---- 音声入力（speech.js）"):]


def html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


def card(h):
    return re.search(r'<section id="instruction-panel".*?</section>', h, re.S).group(0)


def engineer(h):
    i = h.index('id="engineer-view"')
    return h[i:]


# ---- 1. Chrome が返す形のまま、軸と符号つきの量を読む ----
@pytest.mark.parametrize("text,target,delta", [
    ("Xマイナス5ミリ", "C00005 X", "-5.000"),     # 唐川さんの確認の文（「に」なし）
    ("Yプラス3ミリ", "C00005 Y", "+3.000"),
    ("Yプラスに3ミリ", "C00005 Y", "+3.000"),
    ("Y +3 mm", "C00005 Y", "+3.000"),            # Chrome が返した形（「プラス」を + に）
    ("X -5 mm", "C00005 X", "-5.000"),
    ("x -5 mm", "C00005 X", "-5.000"),
    ("X − 5 mm", "C00005 X", "-5.000"),           # 数学のマイナス記号
    ("この位置を上に2 mm", "C00005 Z", "+2.000"),  # 実マイクで Chrome が返した文
])
def test_axis_with_sign_before_number(text, target, delta):
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, text, source="voice")
    c = r["state"]["candidate"]
    assert r["status"] == "ok" and c["target"] == target and c["delta"] == delta, (text, r.get("message"))


def test_sign_is_read_once_not_twice():
    # 「Xマイナス5」：符号は量の側で1回だけ読む（2回読むと「向きの指定が2つ」になる）
    f = read_utterance("Xマイナス5ミリ")
    assert f.axes == ("X",) and f.axis_signs == () and f.requested_value() == -5


def test_lost_sign_is_not_guessed():
    # 認識で「マイナス」が落ちた「x5ミリ」：軸と向きを推測しない（聞き返す）
    f = read_utterance("x5ミリ")
    assert f.axes == () and pre_check(f) is not OK


def test_misheard_large_amount_is_stopped_by_the_existing_limit():
    # 実マイクで「Yプラスに3ミリ」が「Y +23 mm」になった。上限（20mm）の検査で止まる（上限は変えていない）
    w = WebFlow()
    sid = w.load(DEMO_C)["session_id"]
    w.select(sid, "C00005")
    r = w.hear(sid, "Y +23 mm", source="voice")
    assert r["status"] == "stop" and r["state"]["candidate"] is None


# ---- 2. 外部へ送る前に同意を聞く。勝手に方式を切り替えない ----
def test_method_choice_is_not_on_the_main_card():
    h = html()
    assert 'name="voice-mode"' not in card(h)
    assert 'id="voice-mode-set"' in engineer(h) and 'id="local-install"' in engineer(h)
    assert "試験的" in engineer(h)  # 端末内のみはタブが落ちたので試験的
    assert not re.search(r'name="voice-mode"[^>]*checked', h)  # 既定は未選択（同意するまで送らない）


def test_consent_before_audio_is_sent():
    v = voice()
    click = v[v.index('$("mic-button").addEventListener("click"'):v.index('$("consent-yes").addEventListener')]
    # 方式が無ければ、同意の欄を出して戻る（聞き始めない）
    assert re.search(r'if \(!voiceMode\(\)\) \{[^}]*\$\("voice-consent"\)\.hidden = false;[^}]*return;', click, re.S)
    # ブラウザー標準にするのは「使う」の処理と、前に同意したものを読むところだけ
    assert v.count('$("voice-mode-browser").checked = true') == 1
    yes = v[v.index('$("consent-yes").addEventListener'):v.index('$("consent-no")')]
    assert '$("voice-mode-browser").checked = true' in yes
    h = html()
    assert 'id="voice-consent"' in card(h) and 'id="consent-yes"' in card(h) and 'id="consent-no"' in card(h)
    assert "保存しません" in v  # 同意の文：アプリは音声を保存しない


def test_failure_never_switches_method():
    # 失敗の処理では、方式（ブラウザー標準・端末内のみ）を変えない・覚えない
    v = voice()
    err = v[v.index("error: (message) =>"):v.index("// 「端末内のみ」で日本語")]
    assert ".checked" not in err and "localStorage" not in err


# ---- 3. Web会議のように：声が無ければ聞き直す、通信の失敗は1回だけ、Chrome 以外は Chrome へ ----
def test_quiet_and_network_retries_are_bounded():
    v = voice()
    err = v[v.index("error: (message) =>"):v.index("// 「端末内のみ」で日本語")]
    assert 'kind === "no-speech"' in err and "state.quiet < QUIET_MAX" in err
    assert re.search(r"const QUIET_MAX = \d+;", v)
    assert 'kind === "network" && BROWSER !== "other" && state.netRetry < 1' in err
    assert "state.live = false;" in err  # それ以外は OFF


def test_failure_kinds_match_speech_js_messages():
    # 失敗の種類は speech.js の固定の文の頭で見分ける（speech.js は変えない）
    sp = (STATIC / "speech.js").read_text(encoding="utf-8")
    v = voice()
    heads = re.findall(r'\["([a-z-]+)", "([^"]+)"\]', v[v.index("const FAILS"):v.index("const failKind")])
    assert len(heads) == 6
    for kind, head in heads:
        assert re.search(r"'[a-z-]+': '" + re.escape(head), sp), (kind, head)


def test_non_chrome_browser_gets_open_in_chrome_guide():
    v, h = voice(), html()
    assert 'BRANDS.includes("Google Chrome")' in v
    assert 'id="browser-note"' in h and "Google Chrome で開いてください" in h and 'id="copy-url"' in h
    assert "このブラウザーでは、声を文字にできません。Google Chrome で開いてください" in v


def test_voice_section_still_never_sends_or_approves():
    v = voice()
    for w in ["/api/", "postJson", "fetch(", "onApproveClick", "sendInstruction", ".click()", "requestSubmit"]:
        assert w not in v, w


def test_voice_text_without_sign_is_flagged_before_sending():
    # 実マイクで「Xマイナス方向に5ミリ」が「X 方向に 5mm」になった（マイナスが落ちた）。向きの無い軸は＋として読むので、
    # 声から入った文に軸があって向きが無ければ、送る前に知らせる（サーバーの読み方は変えない：音声と文字で結果は同じ）
    h, v = html(), voice()
    assert 'id="voice-hint"' in card(h) and "＋として扱います" in h
    assert "function lacksSign(" in v and "state.voiceFinal !== null" in v[v.index("function renderSignHint"):]
    f = read_utterance("X 方向に 5mm")
    assert f.requested_value() == 5  # 今の決まり（向きの無い軸は＋）。変えるかは唐川さんの判断


@pytest.mark.parametrize("text,axis,value", [
    ("ワイプラス 3mm", "Y", 3),        # 実マイクで Chrome が返した文
    ("Y の Plus 3ミリ", "Y", 3),       # 同上
    ("X の -5ミリ", "X", -5),          # 同上（「Xのマイナス5ミリ」）
    ("エックスマイナス5ミリ", "X", -5),
    ("X minus 5 mm", "X", -5),
])
def test_spoken_axis_and_sign_spellings(text, axis, value):
    f = read_utterance(text)
    assert f.axes == (axis,) and f.requested_value() == value and pre_check(f) is OK


@pytest.mark.parametrize("text", ["ワイヤーを3mm動かして", "ワイ3mm", "ワイの3mm"])
def test_katakana_alone_is_not_taken_as_axis(text):
    f = read_utterance(text)
    assert f.axes == () and pre_check(f) is not OK
