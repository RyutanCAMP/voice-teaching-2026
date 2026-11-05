"""入口は1つ（11/4 唐川さんの指示③）：声も文字も、指示の欄1つから /api/talk へ。

- 旧「Claude に聞く」の入力欄（#ask-input・#ask-send）と「🎤 の文をこの欄に入れる」（#voice-to-ask）は無い
- 指示の欄の送信は /api/talk（explain.js の vtTalk）だけ。/api/hear を直接呼ぶのは、指示の道が出した選択肢への答え（answerChoice）だけ
  （振り分けをせずに /api/hear へ送ると、相づち・ボタンだけの言葉で承認が取り消されることがあったため）
- 状態の1行（#status-line）は、サーバーの state.status_line をそのまま出す（Claude の文では変わらない）
- ステップ4（候補・承認）では、今までどおり指示の欄（マイク）を出さない（録音しながら承認・出力できない）
"""
import re
from pathlib import Path

import test_claude_talk as T
import test_koko_position as K

STATIC = Path(__file__).resolve().parent.parent / "voice_teaching" / "web" / "static"


def test_old_ask_input_is_gone_and_one_entry_remains():
    h = (STATIC / "index.html").read_text(encoding="utf-8")
    for gone in ('id="ask-input"', 'id="ask-send"', 'id="ask-form"', 'id="voice-to-ask"'):
        assert gone not in h, gone
    assert h.count('id="text-input"') == 1 and h.count('id="text-send"') == 1 and h.count('id="mic-button"') == 1
    assert 'id="status-line"' in h


def test_free_text_and_voice_go_only_through_talk():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r'postJson\("/api/hear"', js)]
    assert len(calls) == 1  # 選択肢への答えだけ
    i = js.index("async function answerChoice(")
    assert i < calls[0] < js.index("\n}\n", i)
    send = js[js.index("async function sendInstruction("):]
    send = send[:send.index("\n}\n")]
    assert "window.vtTalk(" in send and 'postJson("/api/hear"' not in send
    ex = (STATIC / "explain.js").read_text(encoding="utf-8")
    assert "window.vtTalk = talkSend" in ex and '"/api/talk"' in ex


def test_status_line_is_drawn_from_the_server_state_only():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # 唐川さん承認のデザイン 11/4：状態の1行は上の帯の小さなチップ。文はサーバーの state.status_line だけから作り、
    # 区切り（「：」「／」）で分けるだけ（文字は足さない・変えない。分けられないときはそのまま1行）
    assert 'renderStatusChips(st.status_line || "");' in js
    body = js[js.index("function renderStatusChips(text)"):js.index("function renderSelLine(")]
    assert "box.textContent = text;" in body and "text.slice(0, at + 1)" in body and '"／")' in body
    assert "st." not in body and "state." not in body  # チップの文は引数（サーバーの文）だけから
    ex = (STATIC / "explain.js").read_text(encoding="utf-8")
    assert "status-line" not in ex  # 会話（Claude の答え）の側からは書かない


def test_entry_card_at_step_4_only_after_approval():
    # 承認の前のステップ4では出さない（確認して承認へ、だけを目立たせる）。承認のあとは説明の質問のために出す
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id === "instruction-panel" ? !(step === 2 || step === 3 || (step === 4 && approvedNow))' in js
    # ステップ4では、マイクを出さない・押せない（承認のあとも文字の欄だけ）
    assert '$("voice-input-slot").hidden = step === 4;' in js and "step === 4;" in js[js.index("function micLocked(step)"):]
    assert '$("mic-button").disabled = micLocked(step);' in js and '$("mic-button").disabled = micLocked(currentStep());' in js
    # 録音中は、確認表示・承認・出力のボタンを押せない
    for b in ('$("open-confirm").disabled = !c || recording();', '$("approve-button").disabled = !st.can_approve || recording();',
              '$("export-button").disabled = !st.can_export || !!st.exported || state.exporting || recording();'):
        assert b in js, b


def test_backchannel_question_and_button_words_after_approval_keep_it(tmp_path):
    # 旧入力欄（/api/hear 直接）で承認が消えた問題：入口（/api/talk）からなら、変更の無い言葉では承認を取り消さない
    w, f = T.flow(tmp_path, T.answer("同じ動きをくり返しています。", (21,)), updown_config=K.config_c(tmp_path))
    sid = w.load(K.DEMO_C)["session_id"]
    w.select(sid, "C00005")
    w.talk(sid, "Zマイナス3ミリ")
    w.approve(sid, "テスト", candidate_gen=w.candidate_gen_of(sid))
    approval = w.state(sid)["state"]["approval"]
    for text in ["はい", "OK", "保存して", "出力して", "このロボット、何をしている？", "3回にして、あと3ミリ下げて"]:
        r = w.talk(sid, text)
        assert r["state"]["approval"] == approval, text
        assert "承認 済み" in r["state"]["status_line"], text
