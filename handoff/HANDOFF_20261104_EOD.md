# 引き継ぎメモ：11/4 の終わり（会社PC）→ 11/5 の再開

- 書いた人：シクロ（会社PC）。2026-11-04 夜
- 作業フォルダ：`C:\アプリ\vt-ui2`　ブランチ：`fix/ui-voice-first`（ローカルだけ。push・merge・rebase はしていない）
- このメモを入れるコミットの1つ前が、UI の最終コミット **20608df**

## 1. ブランチとコミット
| コミット | 中身 |
|---|---|
| 1fe6463 | 土台（fix/ui-above-fold の先頭。変えていない） |
| 7912613 | 途中保存（16時の打合せのため） |
| 4a9ff2a | 声を先にした新しい画面の仕上げ |
| f9f88cb | 差し戻しの修正（画面） |
| 20608df | e2e の追加（位置を選ばない流れ・Chrome 以外の帯・長い出力先・再生中の上下の向き） |

- 変えたファイル（1fe6463 から）：voice_teaching/web/static/ の app.js・index.html・style.css・view3d.js（色の値だけ）・explain.js・highlight.js、tools/e2e_browser.py、tests/ui_contrast.test.mjs・tests/test_single_entry.py・tests/rephrase.test.mjs、handoff/
- サーバー側の Python（voice_teaching/*.py・voice_teaching/web/*.py）の差分は 0。P1-C の修正とは別
- fix/ui-above-fold（C:\アプリ\vt-ui、1fe6463）・vt-company（434165d）・8775・8785 は触っていない

## 2. テスト（実装担当のテストと、レビューの判定は別）
| テスト | 結果 | 誰が・どの版で |
|---|---|---|
| pytest | 4931 passed, 1 skipped | 実装担当（f9f88cb〜20608df の作業中）。**最終コミット 20608df での再実行は未実施** |
| Node | 119 / 119 | シクロが 20608df で再実行して確認 |
| e2e（S1〜S7 だけ） | OK 502, NG 0 | 実装担当。**20608df での全体の再実行は未実施** |

## 3. レビュー
| レビュー | 4a9ff2a | f9f88cb・20608df（再レビュー） |
|---|---|---|
| Safety | CONDITIONAL PASS（P0・P1 なし、P2 4件） | **レビュー中・未判定**（終業のため途中で止めた） |
| QA | FAIL（P1 1件・P2 1件） | **レビュー中・未判定**（同上） |

- 4a9ff2a の指摘のうち、直したと実装担当が言うもの（再レビューで未確認）
  - QA P1-1 位置を選ばずに質問すると 1280×720 で答えが入らない → ② で位置のカードを番号の1行に縮めた
  - QA P2-1 長い出力先で左の列がはみ出す → 折り返す
  - Safety P2-1 再生中に上下の向きが消える → ファイル名だけ隠す
  - Safety P2-2 「選択中」の値を変更後と読み違える → 候補があるとき「変更前」の札
  - Safety P2-3 範囲の行の一覧が畳まれる → 1回目の行は畳まない
  - P3：Chrome 以外の帯で見出しが縮む、やり直しで ① に戻らない、範囲のチップの色を青に、言い換えの例のチップを白リストに、S7↑ の e2e
- 止める直前の QA のメモ：② の番号の1行に「ほかにも番号がある」ことを示す手がかりが画面に無い（横に送れることが分からない）→ P2 候補
- 残る P2（唐川さんの判断待ち）：Safety P2-4 初期画面の見出し「ロボットに、やりたい動きを話してください」が、ロボットに直接声で命令するように読める（案「動作データの直し方を話してください」）
- 唐川さんへの確認事項：止まった候補の色（UI は赤・3D は灰色の点線のまま）、② にキーボードのボタンを出さない、② で［位置を選ばずに話す］を出さない、見出しの文言（P2-4）

## 4. 再開の手順（11/5）
```
cd C:\アプリ\vt-ui2
git log --oneline -3          （先頭がこのメモのコミット、その下が 20608df）
git status                    （何も出ないこと）
```
1. 再レビュー（Safety・QA）を 20608df でやり直す（差分は `git diff 4a9ff2a 20608df`）
2. 20608df で pytest（約6〜11分）と e2e の全体を流し直す
   `$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"; C:\アプリ\voice-teaching\.venv\Scripts\python -m pytest -q`
3. 新しい版を目で見るとき：`C:\アプリ\voice-teaching\.venv\Scripts\python tools\demo_mock.py --port 8795`（8775・8785 以外）→ http://127.0.0.1:8795/
4. 比較の画像（scratchpad の ui_v2/compare・states・flip）は 4a9ff2a の版。直したあとの画像は ui_v2/fold3/。scratchpad は一時フォルダなので、残っていなければ撮り直す
5. 正式な統合・push は唐川さんの確認のあと

## 5. 11/5 の優先（唐川さんの指示）
1. P1-C の安全修正と独立レビューの状況を確かめる（自宅のシクロ。会社PCでは直さない。範囲を指定した変更は合格にしない）
2. 本物の Claude API での会話のテストの結果を確かめる
3. Chrome で実際のマイクを使った音声入力
4. 音声 → Claude → 既存の JOB の変更候補 → 検査 → 3D シミュレーションの通し
5. 金曜（11/6）の撮影に使う画面と動きを決める（新しい UI を使うかどうかは、再レビューの結果と唐川さんの判断で）

- 実機を使う場合は、アプリのシミュレーションと実機の試運転を分ける。アプリから実機への転送・起動はしない

## 6. 自宅PCへの引き継ぎ（push は唐川さんの指示のあと）
- GitHub に無いコミット：7f390fe・1fe6463（fix/ui-above-fold）と、7912613〜このメモのコミット（fix/ui-voice-first）。origin に fix/ui-* のブランチはまだ無い
- 土台の 434165d は origin/wip/scene3-connect にある
- push が要るのは **fix/ui-voice-first だけ**（1fe6463 までのコミットも一緒に上がる）。fix/ui-above-fold は、ブランチの名前も残したいときだけ追加で push する
- 推奨の手順（唐川さんの指示が出たら）
  1. 会社PC：`git -C C:\アプリ\vt-ui2 push -u origin fix/ui-voice-first`
  2. 自宅PC：`git fetch origin` → 別の worktree で `git worktree add <新しいフォルダ> fix/ui-voice-first`（wip/scene3-connect の作業フォルダとは分ける）
  3. 自宅PC：このメモの 4章の 1〜2（再レビュー・pytest と e2e の再実行）から再開
- push の前に確かめたこと（11/4 夜）：434165d..HEAD の差分に APIキー・パスワード・トークン・個人のメールアドレス・PC のユーザー名・.env・.JBI・画像・records/・output/ は無い。records/ と __pycache__ は .gitignore で外れている

## 7. 動いているもの・止めたもの
- 動かしたまま：8775（vt-company 434165d）・8785（vt-ui 1fe6463）のモック
- 止めた：実装担当、Safety・QA の再レビュー。それらが立てたモック・ブラウザーは残っていない
