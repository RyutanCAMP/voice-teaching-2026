# 本番用UIの引き継ぎ（2026-11-05 夜・コックさん）

## 引き渡す版

- 専用clone: `voice-teaching-cock-filming`。シクロのcheckout・未コミット変更は使用していません。
- 専用ブランチ: `ui/cock-filming-main`
- UIコードSHA: `7bc484aa2a9c95af513d4d0814719fada5543663`
- 基準: `integrate/filming-1106` / `63577a6ba29f8d8fd48c75a9fd2522cf795a0828`
- PR #3: `ui/filming-polish` / `eb8aeda101671ae5ccc678b743f3a52be30b9312`。確認時はDraft・open・未merge。
- PR #3の表示実装 `0625d68` を、この専用ブランチの `442af8b` としてcherry-pick。その上のUI変更は `f535699`、短文の整理と診断ツール更新は `7bc484a`。
- 最新の自然言語ブランチ `feat/nl-demo-1106` / `9dab764` は基準版にまだ含まれていません。シクロによる統合後の確認が必要です。
- 会社の最終引き継ぎは [元のUI資料](https://github.com/RyutanCAMP/voice-teaching/blob/eb8aeda101671ae5ccc678b743f3a52be30b9312/handoff/UI_FILMING_POLISH_20261105.md) と [会社→自宅資料](HANDOFF_20261105_OFFICE_TO_HOME.md) を参照しました。

## 変更内容

1. 名前と短い案内を中心にしたヘッダー。モデルとアプリの詳細な記録は［ⓘ アプリ情報］へ移しました。モデルの正確な表示は会話の［詳しく］にも残しています。
2. 初期マイクは1920幅で176px。会話後も84pxで中央に置き、［押して話す］を表示。既存の同意・録音状態・送信操作は維持しています。
3. キーボードボタンに［文字で入力］の文字を追加。会話本文を読みやすい大きさと行間に整理しました。
4. 3Dは画面右の約7割を使用。変更前後の座標・強調色、再生・視点操作、位置番号の選択は既存機能を使います。
5. 図の技術補足はPR #3の［ⓘ 図の見方］へ。実際の警告、停止理由、未確定の向き・位置の確認、座標差分、承認者・承認ボタンは折り畳んでいません。
6. 小さい画面でフッターがマイク・送信ボタンに重なる箇所を修正。1920×1080を主対象に、1280×720と390×844も確認しました。

実行時差分は `index.html`、`style.css`、PR #3の `highlight.js`、`explain.js` の話者表示1式だけです。`explain.js` は実Claudeの吹き出し名を `Claude` にする表示変更のみで、モック表示は維持します。変更前にPR #3でシクロへ連絡しています。

Python全20ファイルと `app.js` / `view3d.js` / `player.js` / `speech.js` は基準版と同一です（改行正規化比較）。API、会話解析、状態機械、安全ガード、承認条件、JOB生成・出力処理は変更していません。HTMLの既存ID保持・重複なし、explain.jsの差分限定も自動確認しています。

## 実測結果

| 確認 | 結果 | 条件・限界 |
|---|---|---|
| 最終Chrome UI回帰 | **99 PASS / 0 FAIL**、Python終了コード0 | Chrome 156.0.8078.80。3解像度。認識・Claudeとも既存モック。実API・実マイク0 |
| pytest（E2E以外） | **5616 PASS / 1 skip / 14 XFAIL**、終了コード0 | 最終の表示名整理前。skip/XFAILは合格に含めない |
| 表示名整理後の関連pytest | **153 PASS**、終了コード0 | UI4steps / C026 UI / Claude説明 / Web API |
| Node | **127 PASS / 0 FAIL**、終了コード0 | 音声・再生・表示・コントラスト等の既存テスト |
| PR #3の既存Chromeチェック | **44 PASS / 0 FAIL** | 追加デザイン前後に確認。今回の最終版の主証拠は99件 |
| 再生の単独比較・Chrome | 基準2/2、新UI2/2で22行目へ進む | 合成DEMO_BUTTON_REPEAT、1280×900 |
| 再生の単独比較・同梱Chromium | 基準2/2、新UI2/2で22行目へ進む | 同じ手順。詳細はJSON |
| 既存E2E全体 | **未完了・終了コード1** | 下記。全体PASSとは扱わない |

最終UIの99件には、マイク同意→録音モック→認識文を入力欄へ反映（自動送信なし）、会話表示、向き未確認の聞き返し（候補なし）、正確なXYZ差分、再生/コマ送り/視点復帰/2D・3D切替、承認表示、変更不可の候補の停止理由と承認無効を含みます。

UIチェック自体は承認・出力HTTP要求を送っていません。既存E2Eの途中では人のクリックを模した承認と、一時フォルダへの合成JOBのシミュレーション出力を確認しました。実機出力・転送はありません。実マイクの精度・実Claudeの会話品質は未検証です。

### 既存E2Eの停止位置と未適合

`tools/e2e_browser.py` は原本を変更していません。

- 最初は、再生の22行目を待つ `check_demo3d_qa` でタイムアウト。単独追試ではChrome/Chromium、基準版/新UIとも各2回成功しました。この一度のタイムアウトの根本原因は未確定です。
- 再実行では再生確認を通過し、**206件OK・5件NGを記録した後**、`check_header_help` の「status-lineを常時ヘッダーに表示する」待ちで停止しました。今回の［アプリ情報］へ移す設計と旧テストの前提が異なります。
- 5件のNGは、初期文字数168/160、指示周辺81/80、モデル名の常時ヘッダー表示、技術補足2つの常時表示、再生補足の常時表示です。文字数2件はその後の最終短文化より前の結果です。残る3件の旧配置期待は新UIに未対応です。
- 警告・停止理由・差分・承認を隠して合格にはしていません。新UIでの警告・聞き返し・拒否・承認表示は99件で確認済みですが、未実行になった旧E2Eの後半項目を合格へ読み替えません。
- 撮影前の残件: 新UIでの情報欄と常時警告を区別したE2Eの期待整理、およびシクロの自然言語統合後の全体E2E完走。

## 証拠と実行方法

- [最終判定・JOB SHA256・4表示資産SHA256](../docs/ui-filming-main/results.json)
- [マイク](../docs/ui-filming-main/1920-microphone.png)、[会話](../docs/ui-filming-main/1920-conversation.png)、[方向の確認](../docs/ui-filming-main/1920-question.png)
- [差分](../docs/ui-filming-main/1920-difference.png)、[承認](../docs/ui-filming-main/1920-review.png)、[拒否](../docs/ui-filming-main/1920-refusal.png)
- [1280の会話](../docs/ui-filming-main/1280-conversation.png)、[1280の承認](../docs/ui-filming-main/1280-review.png)、[390の承認](../docs/ui-filming-main/390-review.png)
- [Chrome再生比較](../docs/ui-filming-main/playback-chrome.json)、[Chromium再生比較](../docs/ui-filming-main/playback-chromium.json)
- 同じフォルダの `node.txt` / `pytest.txt` / `pytest-ui-final.txt` / `e2e.txt` / `e2e-final.txt` は実行記録です。E2EのNG・例外も保存しています。

必要なものはPython・requirements.txt・Playwright・Google Chrome・Nodeです。UIチェックはAPIキーを必要とせず、`.env`を読み込まずに専用モックサーバーを空きポートで起動・終了します。

```powershell
python -m pip install -r requirements.txt playwright
python tools/check_filming_main.py --out output/filming-main/check
node --test tests/*.test.mjs
python -m pytest -q --ignore=tests/test_e2e_browser.py
python tools/probe_filming_playback.py --out output/filming-main/playback
```

モック画面を人が見る場合:

```powershell
python tools/check_filming_main.py --serve
```

表示された `127.0.0.1` のURLをChromeで開いてください。これはモック確認用です。本番のClaude接続とは区別してください。

## シクロへの統合依頼

1. API・自然言語側の確定SHAと独立レビュー結果を先に確定してください。このUIの合格は既知High/P0の解消を意味しません。
2. 本ブランチはPR #3の表示実装を含む後続です。PR #3と重ねて適用する場合は二重取り込みと共通HTML/説明UIの差分を確認してください。独立レビュー原本は変更していません。
3. 実行時4ファイルの差分を確認し、シクロの新しい確定統合版で99件を再実行してください。特にHTML ID、モデル詳細、警告、範囲確認のボタン、候補→承認、JOB再読込の旧画面無効化を確認してください。
4. 全体E2Eと撮影台本の実Claude・実マイク確認を行い、実際に配信したSHAを記録してください。撮影は1920×1080、警告・差分が読めるChromeの表示倍率で本人が最終確認してください。

この作業ではmain/シクロのブランチへのmerge、既存8765/8805/8815へのアクセス・変更、キーのコピー、実Claude呼出しは行っていません。UIは統合レビューに渡せますが、アプリ全体の撮影可否は **条件付き・統合後確認待ち** です。
