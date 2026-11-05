# REVIEW_HEADER_HELP_SAFETY_001 — ヘッダーの「?」2つの整理（案3）の Safety & Spec レビュー

- 確認した者：safety-spec-reviewer（サブエージェント。メインと同じ Claude。外部の独立レビュー（チャッピー）の代わりにはならない）
- 日付：2026-11-05
- 対象：C:\アプリ\vt-header-help（ブランチ ui/header-help）、基点 5c4108a → 対象 4d5ed11
- コードは直していない。コミットしていない。

## 確認したこと・コマンド

| 何を | どう確かめたか |
|---|---|
| 変更したファイルの範囲 | `git -C C:/アプリ/vt-header-help diff --stat 5c4108a 4d5ed11` → tests/test_header_help.py（新規）、tools/e2e_browser.py、voice_teaching/web/static/index.html、voice_teaching/web/static/style.css の4つだけ |
| Python（サーバー・判定・承認・出力）に触れていないか | `git diff --name-only 5c4108a 4d5ed11 -- voice_teaching/*.py voice_teaching/**/*.py` → 0件。app.js・highlight.js も差分なし |
| 差分の中身 | `git diff 5c4108a 4d5ed11` を全部読んだ |
| 断定する言葉 | 追加行を「安全・当たり・干渉・大丈夫・問題ありません・保証」で検索 → テストの禁止語の一覧（ASSERTIVE）の1行だけ。画面の文言・aria-label には無し |
| 説明（TIPS.status）の中身 | highlight.js は差分なし。test_status_tip_text_unchanged が元の文を完全一致で確かめている |
| 選択子の参照切れ | app.js を `help-btn` `status-line` `help-button` `nextElementSibling` で検索。app.js は id（#status-line・#help-button）だけで参照、消した .help-btn と、消した `.status-line[hidden] + .help-wrap` を参照する JS は無し |
| 受入テスト | `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 C:/アプリ/voice-teaching/.venv/Scripts/python.exe -m pytest -q tests/test_header_help.py`（C:\アプリ\vt-header-help で）→ 8 passed |
| 全体のテスト | 同じ python で `-m pytest -q -p no:cacheprovider --ignore=tests/test_header_help.py` → 4988 passed, 1 skipped（15分22秒、終了コード 0。報告のあとに終わった） |
| 画面写真 | scratchpad\shots の header_1280x800_start / _loaded、page_1280x800_loaded、header_1920x1080_status_tip を目で見た |

## 結果

1. 画面だけの変更：はい。HTML 2か所（状態の1行を .status-wrap で包み、説明ボタンを ⓘ に。使い方のボタンを文字ボタンに）と CSS だけ。承認・出力・検査・転送の経路（Python・app.js の承認と出力の処理）には差分が無い。ロボットの起動・転送につながる追加も無い。
2. 状態の1行（アプリの記録：変更の候補／承認／出力）：#status-line の id・class・aria-live="polite"・hidden の扱いはそのまま。中身はサーバーの state.status_line を app.js がそのまま出す仕組みで、変わっていない。写真（1280×800・1920×1080）でチップ3つが前と同じ位置（右寄せ）で読め、ⓘ は最後のチップの直後にある。記録が出ていないとき（読み込む前）は ⓘ も隠れ、「使い方」「詳細表示」が右の端に残る。
3. 承認ボタン・［確認して承認へ］・出力の導線：ヘッダーの外にあり、HTML・CSS・JS とも差分なし。ヘッダーの高さも写真で前と同じ1行。使い方の小窓の中の「承認は確認表示のボタンだけです」の文も変えていない。
4. 新しい文言：「使い方」「アプリの記録の説明」「i」だけ。断定の言葉は無い。
5. 説明（TIPS.status）：変わっていない（吹き出しの写真でも「アプリが記録している今の状態です（候補・承認・出力）。会話の答えでは変わりません。」）。
6. CLAUDE.md の安全の線引き：反するものは見つからなかった。

## 指摘

- [低] ⓘ の押せる所（44×44）は、負の左の余白（-11px）で最後のチップ「出力 まだ」の右の端に約11px重なる。チップは押す物ではないので、誤って承認や出力を押すことにはならない。ただ、チップの右端を指でなぞると説明の吹き出しが出る、くらいの影響。直すかどうかは UX 側の判断でよい。
- [低] ⓘ の見た目は丸 16px の中の斜体の「i」で、ほかの「?」（22px）より小さい。画面でははっきり見えるが、離れた所からは「i」が読みにくいかもしれない。中身は aria-label「アプリの記録の説明」で読み上げられる。
- [情報] 状態の吹き出しの位置（1920×1080 の写真）はヘッダーの下、右のパネルの上に重なるが、これは前からの吹き出しの動き（変更なし）。マウスを離すと閉じる。

FAIL に当たる指摘は無し。

## 未確認事項

- 390px など狭い画面（@media max-width: 600px の .status-wrap の行）の見た目は、写真が無く確かめていない（CSS は読んだ：折り返して1行を全部使い、ⓘ の右の余白を0にして画面の内側に収める作り）。
- tools/e2e_browser.py の check_header_help は流していない（別の担当が流す）。
- キーボードだけ・読み上げソフトでの実際の操作は確かめていない。
- このレビューは画面の表示の確認で、実機のリスクアセスメントや唐川さんの安全確認の代わりにならない。実機投入の可否は判定しない。

## 判定

**PASS**（Safety & Spec。画面だけの変更であることを差分で確かめた。上の未確認事項は残る）
