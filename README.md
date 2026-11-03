# ボイスティーチング（Voice Teaching）

**専門家が決めた枠の中で、現場の人がロボットに声で直しを頼める。**

既存の安川ロボット（今回の実証：HC20＋YRC1000）のジョブを、普段の言葉で直すためのアプリです。
「置く位置を右に10ミリ」と話しかけると、Claudeがジョブを読んで対象を特定し、専門家が決めた範囲の中かどうかをプログラムが検査し、人が画面で確認・承認してから、新しいジョブとしてロボットへ送ります。

> このリポジトリは **Built with Claude: Japan 2026** の提出作品です。
> **2026年11月2日のキックオフ以降に、新しく作り始めました。** 開発の経過はコミット履歴で確認できます。

## 状態
開発中です（2026-11-02）。合成ジョブとシミュレーションで、読込 → 位置選択 → 文字/音声の指示 → 変更候補 → 比較 → ボタン承認 → シミュレーション用の出力 が一通り動きます。実機用の出力はまだありません。操作は `docs/MVP_HOWTO.md` を参照。

## 大事な注意
- アプリは**ロボットを起動しません**。動かすのは人が、ロボット側のスタートボタンで行います。
- 画面の図は変更前後を見比べるためのもので、**干渉や安全を保証するものではありません**。実機では必ず低速で動作を確認してください。
- ロボット・ペンダント・ジョブの転送機能は、安川電機の既存製品です。このリポジトリで作っているのは、それらとつなぐアプリの部分です。

## 開発を始める・別のPCで続きをする（2026-11-02 時点）

### いまの作業ブランチ
| ブランチ | 中身 | 状態 |
|---|---|---|
| `feat/mvp` | **動く最小構成**（JBI読込 → 位置選択 → 文字/音声の指示 → 変更候補 → 変更前後の図と差分 → ボタン承認 → シミュレーション用の出力）。合成ジョブだけ出力できる | 開発の中心。音声と本物の Claude の接続は統合済み。実マイク・実 API・Chrome での目視は未確認（`docs/MVP_HOWTO.md` の「未完了」を参照） |
| `fix/safety-guards` | 実機用モードの安全ガード（jbi.py）とレビュー記録 `reviews/` | レビュー中（Safety 3回目は CONDITIONAL PASS） |
| `feat/readonly-inspect` | 実機のジョブを読むだけで確かめる道具と手順書 `docs/REAL_MACHINE_CHECK.md` | レビュー中（Safety 3回目は CONDITIONAL PASS） |
| `feat/preview-workflow` | 別チャットで作った音声入力の元の成果物（引き継ぎ資料 `docs/VOICE_INPUT_HANDOFF.md`） | 参照用。`feat/mvp` へ取り込み中 |
| `docs/export-design`・`docs/speed-spec`・`docs/demo-plan`・`chore/quality` | 書き出しの設計、速度の調査、デモの構成案、CI と品質レポート | 文書・道具。レビュー待ち |

`main` には、レビューと唐川さんの承認が済むまで統合しません。

### 必要なもの
| もの | 版 | 用途 |
|---|---|---|
| Git | 何でもよい | GitHub から取得 |
| Python | **3.11 以上**（3.13.1 で確認） | アプリ本体とテスト |
| pip のパッケージ | `requirements.txt`（pytest、anthropic） | テスト、本物の Claude への接続 |
| Chrome | 最新 | 画面と音声入力（音声は Chrome の音声認識を使う） |
| Node.js（任意） | 18 以上（22.14.0 で確認） | 音声モジュールのテスト `node --test tests/speech.test.mjs` |
| Playwright（任意） | `pip install playwright` → `python -m playwright install chromium` | ブラウザの自動操作で通しを確かめる |

VS Code は不要です。PowerShell（またはコマンドプロンプト）と Chrome だけで動きます。

### 最初に実行するコマンド（PowerShell）
```powershell
git clone https://github.com/RyutanCAMP/voice-teaching.git
cd voice-teaching
git checkout feat/mvp
python --version                      # 3.11 以上か確かめる
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # 実行できないときは: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements.txt
python -m pytest -q                   # すべて通ることを確かめる
python -m voice_teaching.web          # 表示された http://127.0.0.1:8765/ を Chrome で開く
```
画面で `samples/dummy/DEMO_PLACE.JBI` を選び、位置 C00004 を選んで「この位置をZのプラス10mm」と入力すると（「上に10mm」は、下の「上下の向きの設定」が無ければ聞き返しになります）、確認表示 → 承認 → シミュレーション用の出力（`output/sim/` に `.JBI.txt` で書きます。実機には入れません）まで進みます。止めるときは Ctrl+C。

### 上下の向きの設定（CHECKER_026 M2。設定が無ければ「上・下」は使えません）
- 「上に」「下げて」などを Z＋・Z− として読むのは、専門家が上下の向きを確かめて登録した設定（既定 `records/updown.json`。`records/` は git に入りません）の照合に通ったときだけです。床置き・架台固定の情報だけでは決めません。
- 設定が無い・形が正しくない・確認のあとで変わった・別のジョブ（`///USER` 付き・ツール番号違いを含む）・別のロボットのときは、「上・下」を軸にせず、「Zプラス・Zマイナスのように、軸と向きで言ってください」と聞き返します。「Zマイナス3ミリ」「Z座標を3mm下げて」は設定が無くても使えます。
- 設定を書くのは、確かめた本人が次のコマンドで（内容を表示し、`yes` と入力したときだけ書きます。AI や画面からは書きません）：
  ```
  python -m voice_teaching.updown register --job samples/dummy/DEMO_C_PLACE.JBI --positions C00005 --robot HC20DT --by （名前） --evidence （何で確かめたか）
  python -m voice_teaching.updown check --job samples/dummy/DEMO_C_PLACE.JBI
  ```
- サーバーは `python -m voice_teaching.web --updown-config records/updown.json --robot HC20DT`（どちらも既定値）。
- この設定は「誰が何を根拠に確かめたか」の記録で、本人確認（署名）でも、安全の保証でもありません。

### Claude API の設定（秘密の情報はリポジトリに入れない）
- APIキーが無いときは、偽の Claude（モック）で動きます（画面と作業ログに「モック」と出ます）。
- 本物の Claude を使うときは、リポジトリ直下に `.env` を作り（`.env.example` をコピー）、自分のキーを書きます。`.env` は `.gitignore` で除外されていて、GitHub には上がりません。
  ```
  ANTHROPIC_API_KEY=（自分のキー）
  ANTHROPIC_MODEL=claude-opus-5-5
  ```
  または、その PowerShell の中だけで設定します：`$env:ANTHROPIC_API_KEY = "（自分のキー）"`
- キーをコード・コミット・チャット・スクリーンショットに書かないでください。

### Claude Code で続きを始めるとき
リポジトリのフォルダで Claude Code を起動し、次のように伝えると続きから始められます：
「`feat/mvp` の `docs/MVP_HOWTO.md` の『未完了』と、`fix/safety-guards` の `reviews/REVIEW_PR1_FIX_001.md` の『未解決事項』を読んで、続きから進めて」

### 守ること
- 実機のジョブ・会社のデータ・APIキーをリポジトリに入れない（`.gitignore` が `*.JBI` を除外。許可されているのは `samples/dummy/` の合成ジョブだけ）
- 開発ルールは `CLAUDE.md` を参照

## ライセンス
[MIT License](LICENSE)
