"""4ステップの画面（feat/ui-4steps）の受入テスト。画面のファイル（HTML・JS・CSS）を読んで確かめる。

方針（唐川さん 2026-11-02）：誰が見ても5秒で使い方が分かる。操作は4ステップ
  1. JOB をドラッグ＆ドロップ  2. ロボットの位置を選ぶ  3. 音声で変更を指示  4. 変更前後の動作を確かめて承認
- 最初の画面は JOB の読込が中心。次に押すボタンを目立たせる。マイクボタンは大きく。ロボットの図を画面の中心に
- 技術的なログ・表は「詳細表示」（同じ画面の中の切り替え）にまとめる
- 図は X-Y・X-Z に加えて Y-Z。JOB の命令の順に、変更前後の経路を再生する（直線で結んだ目安。ロボット本体の動きではない）
- 最終の承認は全画面の確認表示のまま。安全に関わる注意は省略しない

追加の方針（唐川さん 2026-11-02 夜・Apple の製品のように）：「説明を増やす」のではなく「説明が要らない画面」にする
- 余白を広く、文字とボタンを減らす。画面ごとに目立たせる操作は1つだけ
- 細かい説明・音声の方式の説明・通信の仕組み・ログは詳細表示へ
- 音声の画面は「位置・マイク・入力欄・次へ」が中心
- エラーは専門用語でなく、次に何をすればよいかを短く
- 危険な変更の警告、シミュレーション限定の明示、最終承認は残す
合格基準：初めて使う人が説明なしで操作できること。
ブラウザーでの動き（見える文字の量・目立つボタンが1つだけ・クリック・再生）は tools/e2e_browser.py で確かめる。
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "voice_teaching" / "web" / "static"


def files():
    return {p.name: p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.is_file()}


def html():
    return files()["index.html"]


def js():
    return files()["app.js"]


def engineer_part(h):
    m = re.search(r'<section id="engineer-view".*?<!-- /engineer-view -->', h, re.S)
    assert m, "engineer-view が無い"
    return m.group(0)


def folded_part(h):
    """詳細表示と <details> の中（最初は見えない所）"""
    return engineer_part(h) + "".join(re.findall(r"<details.*?</details>", h, re.S))


def outside_detail(h):
    """詳細表示と <details> を除いた所（いつも見える所）"""
    rest = h.replace(engineer_part(h), "")
    return re.sub(r"<details.*?</details>", "", rest, flags=re.S)


# ---- 4つのステップと「次にすること」 ----
def test_four_steps_are_shown_in_order():
    h = html()
    labels = ["JOBを読み込む", "位置を選ぶ", "音声で指示", "確かめて承認"]
    pos = []
    for i, label in enumerate(labels, start=1):
        m = re.search(rf'<li[^>]*id="step-{i}"[^>]*>.*?{label}.*?</li>', h, re.S)
        assert m, f"step-{i}（{label}）"
        pos.append(m.start())
    assert pos == sorted(pos)


def test_next_action_area_exists_and_js_marks_current_step():
    h, j = html(), js()
    assert 'id="next-action"' in h
    assert "function currentStep()" in j
    assert "aria-current" in j  # 今のステップを支援技術にも伝える
    assert "primary" in j  # 次に押すボタンを目立たせる印


def test_only_the_current_step_card_is_shown():
    """右側には、今のステップのカードだけを出す（前後のカードは隠す）。
    入口は1つ（11/4 唐川さんの指示③）：指示の欄（会話の入口）だけはステップ2・3と、承認のあとのステップ4で出す（承認の前は出さない）"""
    j = js()
    assert re.search(r'\$\(id\)\.hidden\s*=\s*id === "instruction-panel" \? !\(step === 2 \|\| step === 3 \|\| \(step === 4 && approvedNow\)\) : Number\(n\)\s*!==\s*step', j)


# ---- 1. 読込はドラッグ＆ドロップが中心 ----
def test_drop_zone_is_the_first_screen_and_uses_the_same_loader():
    h, j = html(), js()
    assert re.search(r'id="drop-zone"[^>]*>', h)
    assert "ドロップ" in h
    assert 'id="file-input"' in h  # クリックで選ぶこともできる
    assert 'addEventListener("drop"' in j
    # ドロップでもファイル選択でも、同じ読み込みの関数を通る（/api/load は1か所だけ）
    assert j.count('"/api/load"') == 1
    assert "loadFile(" in j


# ---- 3. 音声の画面：位置・マイク・入力欄・次へ ----
def test_mic_button_is_big_and_starts_disabled():
    h, css = html(), files()["style.css"]
    assert re.search(r'<button[^>]*id="mic-button"[^>]*class="[^"]*mic-big', h) or \
        re.search(r'<button[^>]*class="[^"]*mic-big[^"]*"[^>]*id="mic-button"', h)
    assert re.search(r'<button[^>]*id="mic-button"[^>]*disabled', h)
    m = re.search(r"\.mic-big\s*\{([^}]*)\}", css)
    assert m and re.search(r"(width|min-width|font-size)\s*:\s*\d{2,}", m.group(1))
    assert not re.search(r'name="voice-mode"[^>]*checked', h)


def test_voice_card_is_position_mic_input_next():
    h = html()
    card = re.search(r'<section id="instruction-panel".*?</section>', h, re.S).group(0)
    for i in ["voice-position", "mic-button", "text-input", "text-send"]:
        assert f'id="{i}"' in card, i
    # 入口は1つ（11/4 唐川さんの指示③）：説明の質問も変更の指示も同じ欄から送るので「送る」
    assert re.search(r'<button[^>]*id="text-send"[^>]*>送る</button>', card)
    # 聞き取った文字・解釈・ログはカードに置かない（詳細表示へ）
    eng = engineer_part(h)
    for i in ["heard-text", "facts-view", "work-log"]:
        assert f'id="{i}"' in eng, i


# ---- 説明・音声の方式・通信の仕組み・ログは詳細表示へ ----
def test_details_are_moved_out_of_the_main_screen():
    h = html()
    folded, shown = folded_part(h), outside_detail(h)
    for w in ["ブラウザー提供元のサーバーで処理",  # 音声の方式の説明
              "127.0.0.1",                       # 通信の仕組み
              "話し方の例"]:
        assert w in folded, w
        assert w not in shown, w


# ---- エラーは次に何をすればよいかを短く ----
def test_errors_say_what_to_do_next():
    j = js()
    assert "function friendly(" in j
    assert "python" not in j.split("function friendly(")[0].lower()  # 画面に出す文に起動のコマンドを書かない
    for w in ["もう一度", "別の位置"]:
        assert w in j, w


# ---- 図：3つの向きと、経路の再生（ロボット本体の動きではないことを明記） ----
def test_three_views_including_yz():
    h, j = html(), js()
    for v in ["xy", "xz", "yz"]:
        assert f'data-view="{v}"' in h, v
    assert 'id="fig-main"' in h
    assert '"Y", "Z"' in j


def test_path_playback_exists_and_states_its_limits():
    h, j = html(), js()
    assert re.search(r'<button[^>]*id="play-button"[^>]*disabled', h)
    note = re.search(r'id="play-note"[^>]*>(.*?)</', h, re.S)
    assert note, "再生の限界の説明が無い"
    text = note.group(1)
    assert "ロボット本体の動きではありません" in text
    assert "直線" in text
    assert "命令の順" in j  # 再生は JOB の命令の順（view.segments / after_segments）に沿う
    assert "after_segments" in j and "segments" in j


# ---- 詳細表示（同じ画面の中の切り替え） ----
def test_engineer_view_is_a_toggle_in_the_same_page():
    h, j = html(), js()
    assert re.search(r'<button[^>]*id="engineer-toggle"', h)
    body = engineer_part(h)
    for i in ["work-log", "positions-table", "instructions-table", "facts-view"]:
        assert f'id="{i}"' in body, i
    assert 'aria-expanded' in j
    assert "<a " not in h  # 別のページへ移らない


# ---- 承認は全画面の確認表示のまま・安全の注意は省略しない ----
def test_final_approval_stays_in_full_screen_overlay():
    h = html()
    overlay = re.search(r'<div id="confirm-overlay".*?<!-- /confirm-overlay -->', h, re.S).group(0)
    assert 'id="approve-button"' in overlay and 'id="approver-name"' in overlay
    assert 'id="confirm-blocked"' in overlay


def test_safety_notices_are_visible_not_folded():
    """安全に関わる注意は、詳細表示にも <details>（折りたたみ）にも入れない"""
    h = html()
    shown = outside_detail(h)
    for must in ["シミュレーションモード", "実機用の出力・転送・ロボットの起動はしません",
                 "この図で離れて見えても、当たらないとは限りません", "ロボット本体の動きではありません",
                 "実機での取り違えを完全に防ぐものではありません",
                 # 11/7：［承認して保存］。最後の出力のボタンは［保存をやり直す（…合成ジョブだけ・実機に入れない）］になり、ふだんは出さない。
                 # 承認のボタンの横に「シミュレーション用の変更です（実機への接続・送信はしません）」を出す
                 "合成ジョブだけ・実機に入れない", "シミュレーション用の変更です（実機への接続・送信はしません）"]:
        assert must in shown, must
    for i in ["confirm-blocked", "problems-panel", "mode-banner", "candidate-view", "reply-message"]:
        assert f'id="{i}"' in shown, i
