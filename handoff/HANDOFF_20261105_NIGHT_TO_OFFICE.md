# 引き継ぎ：11/5 朝 自宅PC（夜間の品質改善）→ 会社PC

- 書いた人：シクロ（自宅PC）。2026-11-05 朝
- GitHub：https://github.com/RyutanCAMP/voice-teaching のブランチ **`night/qa-1105`**（このメモを入れたコミットが先頭）
- コードの最後のコミットは **20c8464**（クロクラウドの再評価はこの SHA で）。そのあとは記録の文書だけ
- `main`（eca4339）・`work/ui-voice-first-home`（57f379f）・`fix/ui-voice-first`（9417ac8）には merge していない。強制 push もしていない
- **安全の合格ではない**（Safety・QA の独立レビューは別）。本物の Claude・実マイクでの通しは**未検証**
- 夜間の記録：`handoff/NIGHT_QA_LOG_20261105.md`／発表の手順：`handoff/HANDOFF_20261105_COMPANY.md`（3・3b 章）

## 1. 会社PCで取る（既存の作業フォルダは変えない）
PowerShell で。既存の `C:\アプリ\vt-ui2`（fix/ui-voice-first）・`C:\アプリ\vt-home` などには触らず、**新しいフォルダ**に取る。
```
cd C:\アプリ\voice-teaching
git fetch origin
git worktree add -b night/qa-1105 C:\アプリ\vt-night origin/night/qa-1105
cd C:\アプリ\vt-night
git status                                   （何も出ないこと）
git rev-parse HEAD                           （次の行と同じ SHA であること）
git rev-parse origin/night/qa-1105
```
- もし `night/qa-1105` という名前のローカルのブランチが既にあると言われたら、`-b night/qa-1105` を `-b night/qa-1105-office` に替える
- worktree を使わない場合は、別のフォルダに新しく clone：`git clone -b night/qa-1105 https://github.com/RyutanCAMP/voice-teaching.git C:\アプリ\vt-night`

## 2. 環境（自宅PCで使った版）
| もの | 自宅PCの版 | 会社PCで |
|---|---|---|
| Python | 3.13 | 本体の venv `C:\アプリ\voice-teaching\.venv` をそのまま使う |
| pytest | 9.2 | `requirements.txt` に入っている |
| anthropic（SDK） | 2.3 | `requirements.txt` に入っている（本物の Claude を使うときだけ要る） |
| Playwright（Python） | 1.64 | e2e で使う |
| Node | 26 | `node --test` で使う |

venv が無い・足りないときだけ：
```
cd C:\アプリ\voice-teaching
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r C:\アプリ\vt-night\requirements.txt
.venv\Scripts\python -m pip install playwright
.venv\Scripts\python -m playwright install chromium
```

## 3. テスト（会社PCで最初に流す）
```
cd C:\アプリ\vt-night
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
C:\アプリ\voice-teaching\.venv\Scripts\python -m pytest -q                 （約10分。ブラウザーの通しも含む）
node --test (Get-ChildItem tests\*.test.mjs).FullName                       （1秒ほど）
C:\アプリ\voice-teaching\.venv\Scripts\python tools\e2e_browser.py          （約7分）
```
自宅PCで 20c8464 を流した結果（合格の線）：

| テスト | 結果 |
|---|---|
| pytest | 4988 passed, 1 skipped |
| Node | 124 / 124 |
| e2e | OK 1108・NG 0・「すべて通りました」 |

- 夜間に1回だけ、e2e の既存の確かめ（check_demo3d_qa の to_repeat）が待ち切れずに止まった。流し直しで通った（試験の側の通信の横取りの問題とみている。アプリの競合は 51 回試して見つからない）。止まったら1回流し直す
- テストは `output\sim\` に書かない（一時フォルダに書く）。終わったあとに `C:\アプリ\vt-night\output` ができていないことを確かめる

## 4. 起動
### モック（料金なし・本物の Claude を呼ばない・.env を読まない）
```
cd C:\アプリ\vt-night
C:\アプリ\voice-teaching\.venv\Scripts\python tools\demo_mock.py --port 8805
```
→ Chrome で http://127.0.0.1:8805/ 。画面の話し手が「モック（台本の答え・本物の Claude ではありません）」

### 本物の Claude（唐川さんが操作するときだけ・料金がかかる）
- APIキーは**本体の `.env`**（`C:\アプリ\voice-teaching\.env`）に置く。worktree には `.env` を作らない・コピーしない。`.env` は `.gitignore` で除外済み
  ```
  ANTHROPIC_API_KEY=（本人のキー）
  ANTHROPIC_MODEL=claude-sonnet-5-5        （任意。書き方は claude- で始まる英小文字・数字・点・ハイフン）
  ```
  または、その PowerShell の中だけで：`$env:ANTHROPIC_API_KEY = "（本人のキー）"`（終わったらウィンドウごと閉じる）
- 自己点検（通信しない）：
  `C:\アプリ\voice-teaching\.venv\Scripts\python -m voice_teaching.claude_check --env C:/アプリ/voice-teaching/.env`
- 起動：
  `C:\アプリ\voice-teaching\.venv\Scripts\python -m voice_teaching.web --env-file C:/アプリ/voice-teaching/.env --port 8805`
  → PowerShell に「解釈：本物の Claude（モデル名）」と出ること（「モック」なら止める）
- 通しの手順と、本物の Claude を呼ぶ回数（3回）：`handoff/HANDOFF_20261105_COMPANY.md` の3章。気をつけること：同じメモの 3b 章
- 環境変数のまとめ：`ANTHROPIC_API_KEY`（本物の Claude のときだけ）・`ANTHROPIC_MODEL`（任意）・`PYTHONUTF8=1` と `PYTHONIOENCODING=utf-8`（テストの文字化け防止）

## 5. 撮影の前に
- `C:\アプリ\vt-night\output\sim\` に**残すべきファイルが無いことを確かめてから**空にする（同じ名前のファイルがあると、出力は上書きせず「既にあるため、上書きしません」で止まる。1つの JOB は1回だけ出力）
- Chrome・表示倍率 100%・新しいタブで開く。撮影中は［詳細表示］を開かない

## 6. クロクラウドの再評価（12項目）
- 道具：claude-hackathon-idea-lab の `docs/usability/tools/recheck_top5.js`。自宅PCでは、ブラウザーの起動の1行だけ `chromium.launch({ channel: 'chrome' })` に替えて流した
- 20c8464 の結果：**12項目中 PASS 6・FAIL 6**（57f379f は PASS 2）。PASS：T1-1・T1-2・T1-3・T2・T5・R1。FAIL：T1-4（出力の失敗のとき「次にすること」が無い）・T3-1（承認の「？」の説明が［確認して承認へ］を隠す）・T3-2（名前なしの注意が帯の下）・T4（上限のあと［会話をやり直す］が「詳しく」の中）。T3 は 1280・1920 の両方。残りは画面だけで直せる見込み（唐川さんの指示待ち）

## 7. 残件
- **P0（コックさんの調査待ち・シクロは直さない）**：Claude の正常な説明が「表示しません」に置き換わる（夜間の G1）／範囲の確認の途中で質問すると範囲が消える／あいまいな回数の指定が確定の候補に進む／取り消した候補が残る
- P1：クロクラウドの T1-4・T3・T4（画面）／回数が食い違うときの矛盾した文（サーバー）
- 未検証：本物の Claude・実マイク・Safety と QA の独立レビュー
- サーバーの Python・安全の判定・承認の条件・JOB の生成は、夜間に変えていない
