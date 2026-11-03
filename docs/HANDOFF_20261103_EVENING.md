# 引き継ぎ（2026-11-03 夕方）：会社のPC → 家のPC

書いた人：シクロ（会社のPCの Claude Code）。読む人：家のPCのシクロと唐川さん。
朝の引き継ぎ（`docs/HANDOFF_20261103.md`、feat/voice-stable）の続き。**安定ブランチ（feat/mvp・main）へのマージは一つもしていない**（独立レビューの合格と唐川さんの承認のあと）。

## 1. ブランチ（すべて GitHub に push 済み）
| ブランチ | 先頭 | 中身 | 状態 |
|---|---|---|---|
| **`fix/checker026`** | **370b6c8**（コード 7e6f4e8） | 安全修正：M1〜M3・MOVJ の表示・CHECKER_028 の P0・`////FRAME ROBOT`・上下の照合（K4）・実機 JOB は確認のみ・ATTR と FRAME の並び | 内部レビュー Safety・QA とも **CONDITIONAL PASS（P1 なし）**（c41ebb7 まで。7e6f4e8 は内部レビュー前）。**クロちゃんの独立レビュー待ち**（依頼文 `docs/REVIEW_REQUEST_CHECKER029.md`） |
| `fix/c026-r2` | 370b6c8 | fix/checker026 と同じ（作業用） | 同上 |
| **`feat/repeat-count`** | **f11279b** | 2回→3回（範囲をボタンで確定・2回→3回だけ・1回目／2回目／全体の範囲・DOUT/TIMER は対象外の表示） | 内部 Safety（d3f366b）CONDITIONAL PASS → S-P1-1 を f11279b で直した。**QA の再レビュー（d3f366b）は結果待ちのまま**。f11279b の内部レビューはまだ |
| **`feat/claude-explain`** | **2c921e6** | Claude に聞く（読み取りだけ）。CLI と画面の欄 `/api/ask`。使用量・推定料金・累計・上限 | **本物の Claude で接続を確かめた**（下の 4章）。内部レビューはまだ |
| `feat/ui-refresh` | 2994ad7 | 画面の改善：3D を主役・ダーク/ライト・読み込みの演出・効果音（既定 OFF）・3D の文字の重なり | 内部レビューはまだ。スクリーンショットは会社の PC の scratchpad にだけある（下の 6章） |
| `docs/frame-prep` | e883dde | 設計メモ：FRAME v1.1 の準備・位置の特定・バックアップ解析・JOB の解説・MOVJ の目標位置・次の対話（視点つきの左右） | 文書だけ |
| fix/c026-m2・m3・ui、fix/frame-robot | | 安全修正の途中の分け（fix/checker026 に全部入っている） | 使わない |

3つの feat ブランチと fix/checker026 は、どれも `flow.py`・`app.js`・`index.html`・`e2e_browser.py` を触っている。**合わせるのは fix/checker026 の合格のあと**（土台：feat/repeat-count・feat/ui-refresh は f783b2f、feat/claude-explain は 9104aa8）。

## 2. テスト（会社のPC、最後のコミットで）
| ブランチ | pytest | node | e2e | クロのスクリプト |
|---|---|---|---|---|
| fix/checker026 7e6f4e8 | 3989 passed, 1 skipped | 33/33 | NG 0 | p0_split 0/26・frame_cases 0/17・b1_rereview（確認済みの上下の設定）MUST 118/118 |
| feat/repeat-count f11279b | 3981 passed, 1 skipped | 37/37 | NG 0 | |
| feat/claude-explain 2c921e6 | 3992 passed, 1 skipped | 37/37 | | |
| feat/ui-refresh 2994ad7 | 3796 passed, 1 skipped | 46/46 | NG 0 | |
- skip の1件は前からある（上下が未確認では会話が始まらない場合）
- b1_rereview は、上下の設定なしだと MUST 106/118（上下の言葉の12件が聞き返しになる＝M2 の決定どおり）

## 3. 今日決まったこと（唐川さん）
- **上下の向き（M2・A案・K4）**：専門家が設定（`python -m voice_teaching.updown register`、`records/updown.json`）に登録。照合はロボット名・座標系・ツール番号・FRAME・RJ・向きの対応・確認した人・日時・根拠・指紋。**JOB の中身では照合しない**。未確認なら「上・下」を Z± にしない。上下の設定だけで変更・承認・出力の制限は外れない
- 「Z座標を3mm下げて」は Z の値を減らす指示（確認文は「Z の値を 3mm 減らします」）。「Zを3mm下げて」は聞き返す
- 「〜動かします」は候補にしない（2択または言い直し）。「〜ます」（「3ミリ下げます」）は受け付ける（上下の確認・承認は今どおり）
- 承認の取り消しの文：候補が残る→「新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残しています。内容を確認して、もう一度承認してください。」／候補も取り消した→「前の承認と変更候補を取り消しました。元のJOBは変更されていません。」
- 位置の選び直し：「位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。」
- `////FRAME ROBOT`：POSTYPE ROBOT・RECTAN・ATTR がちょうど SC,RW,RJ・USER なし・並びは DATE→ATTR→FRAME→GROUP1 のときだけ。RJ あり・FRAME なし、ATTR の無いジョブ、ほかの形は止める。///TOOL は 0〜63
- 実機の JOB：「変更案の確認のみ。実機用JOBは出力できません」。出力は合成ジョブだけ（変えていない）
- **MOVJ の目標位置の変更**：設計済み（`docs/frame-prep` の docs/MOVJ_TARGET_DESIGN.md 6章）。**実装はまだ**。隣の MOVL の点は止めたまま。CLAUDE.md の書き換えは MOVJ の実装と同じブランチで
- **2回→3回**：範囲は人がボタンで確定。2回→3回だけ。つなぎの移動と DOUT・TIMER は今の厳しい制限。プロトタイプ（実機用の出力は禁止）。決定事項はクロちゃんの CURRENT_DECISIONS に反映を依頼済み（唐川さん）
- 「横に3mm」→ 視点と左右を確かめる会話は、次の対話機能の改善（docs/DIALOG_NEXT.md）

## 4. 本物の Claude（11/3 17:09、Sonnet 5.5、読み取りだけ）
```
python -m voice_teaching.claude_explain --job samples/dummy/DEMO_BUTTON_REPEAT.JBI \
  --say "このロボット、何をしている？" --say "そう。じゃあ3回にして" \
  --env-file <リポジトリ>/.env --model claude-sonnet-5-5 --call   （yes と入力）
```
- ①「C00001〜C00003の直線移動を2回繰り返し、最後にC00004へ関節移動で動くジョブです。繰り返しの中ではZのマイナス方向に動いてからZのプラス方向へ戻ります。目的は不明ですが、作業内容をご存じですか？」（根拠 20〜27行目）
- ②「C00001〜C00003の繰り返しを、2回から3回に変更しますか？」＋アプリの固定文（読み取りだけ）。change_request = repeat 2→3（記録だけ）
- 料金：①$0.0091 ②$0.0031、累計 $0.0122（`records/api_usage.jsonl`。キー・会話の中身は書かない）。エラーなし
- **家のPCでは、キーを家のPCの `.env` に唐川さんが入れる**（キーはリポジトリ・チャットに入れない）

## 5. 未解決・次にやること（優先順）
1. **クロちゃんの独立レビュー**（fix/checker026）。指摘を直す → 合格と唐川さんの承認のあと、安定ブランチへの統合の案を出す
2. feat/repeat-count：QA の再レビュー（d3f366b）の結果を確かめ、f11279b の内部レビュー（Safety・QA）
3. feat/claude-explain：内部レビュー。音声入力 → Claude の欄を実際のマイクで。台本に合わせて指示の文を調整（「ボタンを押す動作ですか？」と聞かせるか）。そのあと、レビュー済みの変更エンジン（範囲の確定ボタン）につなぐ
4. feat/ui-refresh：内部レビュー。範囲の確認・2回→3回の見せ方を一目で分かるように
5. 3つの feat ブランチを fix/checker026 の上に合わせる（`flow.py`・`app.js` の競合あり）
6. MOVJ の目標位置の変更（設計済み）、JOB の解説（設計済み）
- 唐川さんの確認待ち：上下の設定がジョブの中身に関係なく効く形（K4 の残るリスク）、「Zマイナス3ミリ下げます」は聞き返しのままでよいか、相対ジョブでないジョブには並びを求めない扱いでよいか、ジョブに無い位置を選んだときも範囲の確認を捨てる扱いでよいか

## 6. 会社のPCにだけあるもの（リポジトリに入れていない）
- 実機の参考 JOB（VT_TEST_XYZ.JBI）は、クロちゃんの Private リポジトリ（claude-hackathon-idea-lab の reviews/CHECKER_027_tools/real_jobs/）にある。voice-teaching には入れない
- スクリーンショット（会社の PC の Claude Code の scratchpad）。必要なら唐川さんがコピーする
- 会社の PC の癖：pip・テストは `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`、本物の Chrome の自動操作は落ちる（Playwright の Chromium を使う）、gh は無い
