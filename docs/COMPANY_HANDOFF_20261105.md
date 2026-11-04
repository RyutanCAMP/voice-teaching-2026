# 会社PCへの引き継ぎ（2026-11-05）

**再開用ブランチ：`fix/cock-p0-conversation`。main・シクロのブランチへのmergeはしない。**

本体の検証済みSHAは `ce7c981`。その後の変更はテスト資産の同梱・実行パス・本手順で、本体機能は変更していない。撮影利用の総合判定は **FAIL**。H02（3回ではなく2回への言い直し）、H16（取消＋説明）で旧候補が承認可能なまま残る。承認条件の変更は行っていない。

## 1. まず新規cloneとオフライン確認

シクロのフォルダとは別の空の場所で、PowerShellから実行する。Python 3.13とGitを使用する。Nodeは音声UIの単体テスト時だけ必要。

```powershell
git clone --branch fix/cock-p0-conversation --single-branch https://github.com/RyutanCAMP/voice-teaching.git voice-teaching-cock-company
Set-Location voice-teaching-cock-company
git status --short --branch
git rev-parse HEAD
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools/p0_qa/requirements-company.txt
.\.venv\Scripts\python.exe tools/p0_qa/company_key.py
```

最後はキー未設定を表示して終了するのが新規cloneの正常状態。キーの値は表示しない。この時点で実APIは0回。Pythonの呼出しに使うのは必ずこのcloneの `.venv` とする。自宅の絶対パス・隣の旧QA clone・自宅の `.env` は不要。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cock_p0_conversation.py -q
.\.venv\Scripts\python.exe tools/p0_qa/replay_all.py --run company-first
```

169件は既存資産128＋32＋8＋1をそのまま再利用。現在の期待再現値は **121 PASS / 46 FAIL / 2 REVIEW_REQUIRED**。終了コード1は46件の既知FAILを反映したものであり、環境構築失敗とは別。`output/p0-20261105/company-first/semantic_results.json` の `EXECUTION_ERROR`・`NOT_RUN` が0か確認する。コード2や新たな保留は、実行できない項目・出力が変わった項目として調べる。保留を成功へ変更しない。

元の文脈ケース1件には新しい経路の保存応答が無かった。ランナーは旧録音の実行エラーを残した上で、別途実APIで保存した応答を再生する。新たに169回APIを呼ぶ試験ではない。**このコマンドのAPI呼出し・キー読取りは0回**。

出力名は毎回変える。既存結果は上書きしない。新しい確定コミットを検証するときは `--commit <SHA> --run company-<別名>` を指定する。前回の意味投影と異なる結果は、キーワードで自動合格にせずREVIEW_REQUIREDとなる。

## 2. 会社PCで安全にキーを設定する

会社で利用を認められたClaude APIアカウントのキーを本人が取得する。自宅のキーや `.env` をコピーしない。Claude Consoleで利用可能クレジット・課金先・利用上限を本人が確認する。Consoleの情報はキー設定チェックだけでは確認できない。

```powershell
.\.venv\Scripts\python.exe tools/p0_qa/company_key.py --configure
.\.venv\Scripts\python.exe tools/p0_qa/company_key.py
git check-ignore .env
git status --short
```

キーは非表示のプロンプトに貼り付ける。コマンド引数・チャット・画面共有・スクリーンショットに貼らない。作成先はこのclone内のGit除外済み `.env` のみ。既存 `.env` は上書きしない。確認出力はキーの有無・形式・モデル・接続先だけ。認証情報をGit設定やremote URLへ入れない。`.env` や `output/` を強制addしない。

モデルは `claude-sonnet-5-5`、接続先は `https://api.anthropic.com/v1/messages`。専用ランナーは再試行0、1回の予約額$0.20以下・1実行$5以下。UI側は既存の累計上限設定も適用する。上限・承認・安全検査を撮影のために緩めない。

## 3. 最小の実Claude接続確認（本人が実行する有料操作）

オフライン169件を先に確認する。次は合成JOBの正常な「3回にして」1件のみ。キーを設定しただけでは接続を合格にしない。

```powershell
.\.venv\Scripts\python.exe tools/p0_qa/run.py --restore
.\.venv\Scripts\python.exe tools/p0_qa/run.py --snapshot company-connect-source --commit HEAD
.\.venv\Scripts\python.exe tools/p0_qa/run.py --mode live --variant company-connect-source --run company-connect-1 --ids B01
```

この1件は通常API1回。`output/p0-20261105/company-connect-1/results.json` で `real_api_calls`、通信status、使用モデル、`elapsed_seconds`、usage、`estimated_usd`、最終の候補/範囲/未承認を確認する。認証・課金・ネットワークエラーをモックへ置き換えない。エラー時は新規名で原因を解決してから再試験し、無意味に連続実行しない。

この入口の機械判定だけを169件の意味評価の代わりにしない。実Claude結果と保存応答の再生結果を別に記録する。本引き継ぎ作業中の追加有料APIは0回。

## 4. 会社PCでUIと実マイクを確認する

会社PC専用cloneで次を起動し、表示されたURLを **Google Chrome** で開く。8765やシクロのサーバーは使用・停止しない。8877が使用中なら別の空きポートを選ぶ。

```powershell
.\.venv\Scripts\python.exe -m voice_teaching.web --port 8877 --env-file .env --explain-model claude-sonnet-5-5
```

起動表示と画面で「本物のClaude」「claude-sonnet-5-5」を確認する。キー未設定のモックを実Claude成功と数えない。マイクの許可・音声認識の外部送信への同意は本人が内容を読んで行う。上下方向の設定は自宅からコピーしない。未確認ならX/Y/Zの軸と符号で試す。

1. `samples/dummy/DEMO_BUTTON_REPEAT.JBI` を読み込む。
2. 本人がマイクを押して「何してる？」。認識文字を確認し、必要なら直してから［送る］。説明が出たことを確認。
3. 「ボタンを押してるんだよ」を送る。
4. **左の会話欄の意味確認吹き出し内にある、上側の［この動きです］を押す。タイミングは「ボタンを押してるんだよ」の送信後、C00001〜C00003のハイライト確認後、「じゃあ3回にして」を話す前。** 声で「この動きです」と言う操作ではない。
5. 「じゃあ3回にして」。現在2回→合計3回、対象範囲を本人が確認する。正しい場合だけ［この範囲を繰り返す］を押す。このボタンは承認ではない。
6. 3Dの変更前後と確認画面を本人が見る。今回のP0再現中は曖昧さ・数・対象が解決するまで承認しない。出力・実機転送・ロボット起動は行わない。

**36件の元手順と記録表：**

- [MIC_PRIORITY_20261105.md](../tools/p0_qa/portable/MIC_PRIORITY_20261105.md)
- [microphone_matrix.json](../tools/p0_qa/portable/microphone_matrix.json)

元の計画は53fe778時点の記録なので書き換えていない。実行時は各ケースの `actual_sha` に会社で試すSHAを記入する。表は最初にGit除外済み `output/microphone/` へコピーして使用する。

```powershell
New-Item -ItemType Directory -Force output/microphone | Out-Null
Copy-Item tools/p0_qa/portable/microphone_matrix.json output/microphone/company-36.json
```

発話→Chrome認識文字と、実際に送った文字→Claude/プログラムの意味を別欄に記録する。認識文字を直して送った場合もASR成功とは扱わない。候補画面でマイクが隠れる場合はUI_BLOCKEDとし、HTTPやDOM操作で迂回して実マイク成功としない。36件は引き継ぎ時点ですべてNOT_RUN。

P0優先：2.3回／3回くらい／今の提案は取り消して／C00002を10mm動かして→Zプラス／いい感じにして／実機にジョブを送って。H02/H16は未解決であり、本番で通る操作として案内しない。

## 5. テスト再開と成果物

```powershell
.\.venv\Scripts\python.exe tools/p0_qa/checks.py core --label final
# ブラウザー試験は任意。Chromium導入後に実行する。
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tools/p0_qa/run.py --restore
.\.venv\Scripts\python.exe tools/p0_qa/ui_probe.py
.\.venv\Scripts\python.exe tools/p0_qa/checks.py browser
# Nodeが導入済みなら:
node --test tests/speech.test.mjs tests/repeat.test.mjs
```

引き継ぎ元の実測：本体pytest5030 PASS/1 SKIP、P0重点43 PASS、Node17 PASS、UI重点4 PASS。既存E2Eは`Response has been disposed`で実行エラー、未合格。新しいcloneでの確認結果は下の追記欄/検証記録に保存する。ブラウザーが無い場合のskipをPASSに数えない。

- [根本原因・修正案・未解決P0](COCK_P0_20261105.md)
- [169件の入力・原応答・アプリ加工・内部状態・意味判定](../tools/p0_qa/reports/20261105/case_verdicts.json)
- [件数・実API5回の使用量と費用](../tools/p0_qa/reports/20261105/summary.json)
- [P0再現記録・前後ログ・UI画像](../tools/p0_qa/reports/20261105/evidence.tar.gz)
- [独立技術レビューの範囲](../tools/p0_qa/reports/20261105/independent_review.md)
- [同梱資産の版とハッシュ](../tools/p0_qa/portable/manifest.json)

`replay_all.py` が会社PCでの意味評価の正式な再開入口。`report.py`・`package_handoff.py`は過去レポートの作成/梱包用であり、会社で実行する必要はない。以前のレポート内にある自宅PCの絶対パスのコマンドより、本手順を優先する。

今回の追加物のセキュリティ検査はGit履歴内の変更blobと圧縮証跡の中身を対象に実施する。実キーとの一致・認証情報パターン・個人連絡先候補・秘密ファイルを調べ、値自体は記録しない。これはあらゆる個人情報が存在しないことを数学的に保証するものではない。既存プロジェクトの著者名等を一括変更せず、実マイクの録音/個人名/端末情報はローカルoutputに留める。

## 新規cloneでの確認完了（11/5 7:04 JST）

GitHubの専用ブランチを別の空フォルダへ新規cloneし、そのclone専用venvへ上のrequirementsを公式PyPIからインストールした。自宅の `.env`・出力・旧QA cloneのデータはコピーしていない。

- 新規clone開始SHA：`c81a207`。ハッシュ整備後の `42d816b` も取得して照合済み。本体ツリーは検証済み `ce7c981` と同一。
- 新規cloneのP0重点：**43 PASS**。
- 同cloneの保存応答169件：**121 PASS / 46 FAIL / 2 REVIEW_REQUIRED**、実API0。終了コード1は既知の意味不合格46件を正しく反映。
- 旧録音の文脈1件の実行エラーと、別途保存した実応答での補完を分離して記録。未実行を成功には数えていない。
- 4テスト資産＋マイク手順/36件表、および公開証跡のハッシュは全件一致。Windowsの自動改行変換でも一致するようQA資産の改行を固定した。
- `.env`なし、Git追跡ファイルの変更なし。キー設定チェックは「未設定」を正しく表示。本人のキー入力・会社回線での実Claude接続・実マイク36件は会社での実施待ち。
- `42d816b`までの変更履歴10コミット/56固有blob/圧縮内部を含む133件を秘密情報検査。実キーとの一致・認証情報パターン・個人連絡先候補・追跡秘密ファイルの検出0。

詳細な検証記録は [fresh_clone.json](../tools/p0_qa/handoff_validation/fresh_clone.json)、[security.json](../tools/p0_qa/handoff_validation/security.json)。**H02/H16・説明/聞き返しの残件・既存E2E未合格は持ち越し**。撮影利用の判定FAILは変えていない。
