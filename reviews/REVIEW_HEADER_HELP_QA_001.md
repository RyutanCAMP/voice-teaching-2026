# REVIEW_HEADER_HELP_QA_001 — ヘッダーの「?」2つの直し（案3）の QA レビュー

- 誰が：QA Reviewer（Claude のサブエージェント。実装担当・Safety レビューと同じ Claude であり、外部の独立レビュー（チャッピー）の代わりにはならない）
- 日付：2026-11-05
- 対象：C:\アプリ\vt-header-help（ブランチ ui/header-help）、基点 5c4108a → 対象 4d5ed11
- 変更ファイル：index.html / style.css / tests/test_header_help.py（新規）/ tools/e2e_browser.py（check_header_help 追加、既存の文言1か所）
- コードは編集していない。

## 判定：PASS（軽微な指摘あり。開発継続に問題なし。実機投入可の判定ではない）

## 実行したコマンドと結果
環境：`PYTHONUTF8=1 PYTHONIOENCODING=utf-8 C:/アプリ/voice-teaching/.venv/Scripts/python.exe`、cwd は C:\アプリ\vt-header-help

1. `git diff 5c4108a 4d5ed11`（全4ファイルを読んだ）。意図しない変更なし。JS（app.js・highlight.js）は変更なし。
2. `python -m pytest -q tests/test_header_help.py tests/test_ui_4steps.py tests/test_ux_polish.py tests/test_single_entry.py` → 44 passed
3. index.html / style.css を読む全テスト15ファイル（test_3d_voice, test_c026_r2, test_c026_ui, test_claude_explain, test_claude_talk, test_dialog, test_dialog_guard, test_header_help, test_review_only, test_single_entry, test_ui_4steps, test_ux_polish, test_voice_stable, test_web_api, test_web_flow）→ 1643 passed in 138.95s
4. `node --test tests/*.test.mjs` → tests 124 / pass 124 / fail 0
5. `python tools/e2e_browser.py` → exit 0、OK 1142件、NG 0、「結果： すべて通りました」。1回で通った（check_demo3d_qa の to_repeat の揺れは今回は出ず、流し直していない）。案3 の確認は 1280×800・1920×1080 で計34件すべて OK。
6. 修正前のコードでテストが落ちるか：`git archive 5c4108a` をスクラッチに展開し、tests/test_header_help.py だけ置いて実行 → 4 failed / 4 passed。落ちたのは振る舞いの4件（?ボタンが無い・使い方が文字ボタン・CSS共有・ⓘ がチップ直後）、通ったのは回帰ガード4件（説明文不変・他の ? 不変・開閉/Esc の JS・断定語なし）。意味のある確認になっている。
7. 自作の Playwright 確認（同梱 Chromium、port=0 の一時サーバー）：360×780・390×844・1280×800・1920×1080 で、読み込み前/サンプル読み込み後/変更候補あり の3状態を測定し、基点 5c4108a でも同じ測定をして比べた。
   - 全幅で横スクロールなし（scrollWidth == innerWidth）。
   - 「使い方」：キーボード Enter で開く・Esc で閉じてフォーカスが戻る・読み込み後も開閉・外側クリックで閉じる → 全幅 OK。
   - ⓘ：クリックで吹き出し（aria-expanded=true、説明文は基点と同一）、Esc で閉じる、フォーカス＋Enter で開く → 全幅 OK。吹き出しは画面内。
   - elementFromPoint で「使い方」の左端は help-button、ⓘ の中心は ⓘ（押せる所の重なりなし）。
   - 写真：C:\Users\<user>\AppData\Local\Temp\claude\902507686a100f0b\6bc1279b-dd30-4df5-bdad-061a4303e89d\scratchpad\qa_shots\（qa_*_before/loaded/tip/candidate.png、base\ は基点）
8. 機密の確認：差分に sk-ant / api_key / password / secret / token / .env / .JBI なし。断定語は test の禁止語リストにだけ出現。
9. 後片付け：output/・records/ は無い（増えていない）。git status は `?? reviews/REVIEW_HEADER_HELP_SAFETY_001.md`（別の Safety レビューが書いたもの）と、このファイルのみ。

## 指摘（重さ順）
- P2（軽微・要求の一部が狭い画面で弱い）：360px・390px では、記録のチップが折り返すと ⓘ が最後のチップの直後ではなく、塊（.status-wrap）の右端に縦中央で置かれ、最後のチップから離れる。
  再現：390×844 でサンプル読み込み → C00005 を選び FAKE_TEXT を送る（変更候補あり）。最後のチップ「出力 まだ」の右端 x=154、ⓘ は x=334〜378。360px では読み込み直後（候補なし）でもチップが2行になり同じ状態（qa_360_loaded.png）。
  基点では ⓘ（旧「?」）がロゴの横の1行目にあり、もっと離れていたので悪化ではない。check_header_help の「すきま 0〜8px」は 1280/1920 だけで確かめている。狭い画面でも「記録の一部」に見せたいなら、ⓘ を最後のチップと同じ行の流れ（inline）に置く等の検討を。
- P3（情報）：360px で状態の1行が2行になり、ヘッダーの高さが 188px→217px（候補あり 234px）。ⓘ が同じ行の 44px を取るため。横はみ出しはなく既存の 360px テストも通る。
- P3（情報）：360px では「使い方」は1行目右、「詳細表示」は2行目左に分かれる。基点の「？」でも同じ配置で、今回の変更による崩れではない。
- 良い点：基点では `.top .status-line[hidden] + .help-wrap` が間の「?」ボタンのせいで効かず、読み込み前の 1280/1920 で「？」「詳細表示」が左寄り（x=412 付近）だった。今回 .status-wrap の margin-left:auto で右の端に直り、e2e でも確かめている。

## 既存テストを弱めていないか
- e2e の既存の変更は check_ux_polish の文言「「？」で」→「「使い方」で」の1か所だけ。判定の条件は同じ。
- 既存の pytest の変更なし。test_ux_polish の「使い方→詳細表示の順」も通る。

## 未確認事項
- 全体の pytest（約16分）は流していない。静的ファイルを読む15ファイル（1643件）だけ流した。Python のサーバーコードは変わっていない。
- 実際のスクリーンリーダー（NVDA など）での読み上げ。aria-label「アプリの記録の説明」と「使い方」のボタン名は DOM で確認しただけ。
- Chrome 本体・Edge・タッチ端末の実機。同梱 Chromium の headless だけ。
- 文字を広げた条件（WIDE_FONT_CSS）での ⓘ の位置は既存 M1 テストの横はみ出し確認だけで、個別には見ていない。
