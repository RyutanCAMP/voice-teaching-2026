"""マイクは1回話し終わったら必ず OFF（唐川さんの決定 2026-11-03 夜）。

- 1回の発話が終わったら（認識できた・聞き取れなかった・通信の失敗・どれでも）マイクは OFF
- 自動では、もう一度聞き始めない（結果のあと・Claude の答えのあと・失敗のあと、どれも）
- 次に話すときは、人がもう一度 🎤 を押す
- 声で送信・承認・出力はしない（今まで通り）
これまでの「Web会議のように ON のまま続けて聞く」「声が無ければ20回まで聞き直す」「通信の失敗は1回聞き直す」は、
この決定で取りやめた（tests/test_3d_voice.py・tests/test_voice_stable.py の該当テストを置き換えた）。
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "voice_teaching" / "web" / "static"


def app():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def voice():
    j = app()
    return j[j.index("// ---- 音声入力（speech.js）"):]


def handler(v, start, end):
    i = v.index(start)
    return v[i:v.index(end, i)]


def test_no_automatic_restart_machinery():
    j = app()
    for w in ["resumeLive", "resumeArmed", "LIVE_MAX_MS", "QUIET_MAX", "netRetry", "state.quiet", "liveTimer"]:
        assert w not in j, w


def test_listening_starts_only_from_a_person_pressing_the_mic():
    j = app()
    # 録音を始めるのは startListening だけ。startListening を呼ぶのは micOn だけ
    assert j.count("voice.start(") == 1 and "voice.start(mode)" in handler(j, "function startListening()", "function updateLocks()")
    assert len(re.findall(r"\bstartListening\(", j)) == 2  # 定義と micOn の中だけ
    assert "startListening();" in handler(j, "function micOn()", '$("mic-button").addEventListener')
    # micOn を呼ぶのは、🎤 のクリックと、同意の「使う」のクリックだけ（どちらも人の操作）
    assert len(re.findall(r"\bmicOn\(", j)) == 3  # 定義・🎤 のクリック・「使う」のクリック
    assert "micOn();" in handler(j, '$("mic-button").addEventListener("click"', '$("consent-yes").addEventListener')
    assert "micOn();" in handler(j, '$("consent-yes").addEventListener("click"', '$("voice-mode-reset")')


def test_every_end_of_an_utterance_turns_the_mic_off():
    v = voice()
    final = handler(v, "final: (text) =>", "error: (message) =>")
    # 認識できた：入力欄（または Claude に聞く欄）に入れるだけで、どの道でもマイクは OFF
    assert final.count("state.live = false;") >= 2
    err = handler(v, "error: (message) =>", "// 「端末内のみ」で日本語")
    # 失敗：種類にかかわらず OFF。聞き直しの分かれ道（return で先に抜ける道）は無い
    assert "state.live = false;" in err
    before_off = err[:err.index("state.live = false;")]
    assert "return" not in before_off


def test_failure_messages_say_press_the_mic_again():
    v = voice()
    assert "もう一度 🎤" in v[v.index("function failText("):v.index("function failText(") + 2000]
    # 聞き取れなかったときの文（no-speech・aborted）
    assert re.search(r'kind === "no-speech" \|\| kind === "aborted"', v)


def test_mic_label_has_no_paused_state():
    v = voice()
    mic = handler(v, "function renderMic()", "function startListening()")
    assert "一時停止" not in mic and "また聞きます" not in mic


def test_voice_still_never_sends_or_approves():
    v = voice()
    for w in ["/api/", "postJson", "fetch(", "onApproveClick", "sendInstruction", ".click()", "requestSubmit"]:
        assert w not in v, w
