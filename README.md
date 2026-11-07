# ボイスティーチング（Voice Teaching）v1.0

**ロボットの教示データ（JOB）を、普段の言葉で直す相談ができるアプリ。決めるのは、いつも人です。**

「もう少し上に寄せたいな」と話しかけると、Claude が意味を読み取って「C00004 を上にですね。何ミリ動かしますか？」と聞き返します。
「5ミリ」と答えると、プログラムが上限や JOB の形を検査し、3D で変更前と変更後を見せます。人が画面のボタンで承認したときだけ、
**シミュレーション用の JOB** を新しいファイルに書き出します。元の JOB は変えません。実機への転送・ロボットの起動はしません。

> このリポジトリは **Built with Claude: Japan 2026** の提出作品です。
> **2026年11月2日のキックオフ以降に、新しく作り始めました。** 開発の経過はコミット履歴で確認できます。

## 状態（2026-11-07）
- 提出用の本番候補：ブランチ **`release/v1.0-rc`**（報告：`handoff/REPORT_V1_RC_20261107.md`）
- 撮影に使った版：タグ **`filming-60f3d24`**
- 実機の JOB・実機への出力は扱いません。動かせるのは合成 JOB（`samples/dummy/`）のシミュレーションだけです
- 独立した安全レビューと、最終の承認は、提出の前に別に行います（このリポジトリのテストは内部の確かめです）

## しくみ（Claude は読む、プログラムは確かめる、人が決める）
| 役 | すること | しないこと |
|---|---|---|
| **Claude**（claude-sonnet-5-5・構造化出力） | 話し言葉から、意図・対象の位置・方向・量・足りない項目を読む。足りなければ聞き返す。会話の続き（「5ミリ」）をつなぐ | 数の計算・単位の換算・安全の判定・承認・出力 |
| **プログラム**（Python） | Claude の読みを独立に確かめる（根拠の言葉が本当に発話にあるか・量は自分で読み直して mm に換算・位置は JOB の一覧）。1回 20mm・累計の上限、座標系、共用の位置、JOB の形、差分を検査する | 推測で量や軸を決めること |
| **人** | 3D と変更前→変更後を見て、画面のボタンで承認する。上・下・右・左が軸のどれかは、ボタンで確かめる | 声だけの承認（できません） |

- 「少し」「くらい」など、数のない量や「だいたい」の量では候補を作りません（聞き返します）
- 候補や承認のあとに言い直し・否定・ためらいがあれば、古い候補と承認は使えなくなります（取り消し、または保留）
- Claude につながらないときは、黙ってモックにはなりません（理由を出して止めます）

## 使い方（Windows・PowerShell）
### 必要なもの
| もの | 版 |
|---|---|
| Python | 3.11 以上（3.13 で確認） |
| Chrome | 最新（音声入力は Chrome の音声認識） |
| Claude API のキー | 本物の Claude で動かすとき |
| Node.js・Playwright | テストだけ（任意） |

### 起動
```powershell
git clone https://github.com/RyutanCAMP/voice-teaching.git
cd voice-teaching
git checkout release/v1.0-rc
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env          # .env に自分のキーを書く（下の「API キー」）
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
python -m voice_teaching.web --port 8901 --channel filming
```
- Chrome で **http://127.0.0.1:8901/** を開きます（アプリの中で開いた画面では、音声が使えないことがあります）
- 起動の窓に「解釈：本物の Claude」「意味理解（Claude）：準備ができました」と出れば準備完了です（最初の1回だけ 30〜40 秒）
- キーが無い・`.env` が読めないと、`--channel filming` では起動しません
- キーなしで試すときは `python -m voice_teaching.web --mock`（モック。Claude の意味理解は使わず、決まった読み方だけ）

### 画面の流れ
1. JOB を読み込む（例：`samples/dummy/DEMO_RJ_XYZ.JBI`）。3D に位置が出ます
2. 位置（例：C00004）を選ぶ
3. マイクか文字で話す：「Zプラスに3センチ動かして」→ 上限 20mm で止まる／「もう少し上に寄せたいな」→「何ミリ？」→「5ミリ」
4. 向きの確かめ（上・下・右・左）はボタンで答える
5. 候補の窓（位置・JOB・軸と向き・量・変更前→変更後・注意）と 3D の差分を見て、名前を入れて **［承認して保存］を1回**押す
   （くわしく見るときは［詳しく見る］。声では承認しません）
   - サーバーが承認を確かめ、通ったら SIM 用の JOB を書きます。アプリの記録：`output\sim\<JOB名>_SIM_YYMMDDHHMM.JBI`
     （日本時間・分まで。同じ分なら `_02`・`_03`。上書きしません）。中のジョブ名（`//NAME`）は `<JOB名>_SIM`
   - 続けて保存の場所を選ぶ窓が開きます（Chrome・Edge）。選んだ場所に同じ中身を保存し、読み戻して同じと確かめてから「保存しました」と出します
   - 窓を閉じた・保存できなかったときは失敗にせず、もう一度承認しないで［保存先を選んで保存］で保存し直せます。
     窓が使えないブラウザーでは［ダウンロードとして保存］（保存先はブラウザーの設定。Chrome の「ダウンロード前に各ファイルの保存場所を確認する」をオンにすると場所を選べます）
6. 書いたファイルはシミュレーション用です。拡張子は `.JBI` ですが、実機には入れないでください（名前と中のジョブ名に `_SIM` が付きます）

## 大事な注意
- アプリは**ロボットを起動しません**。実機への転送・実機用の出力の機能はありません
- 図は目安です。**干渉や安全を保証するものではありません**。ロボット本体・関節は描いていません
- 上限（1回 20mm・累計 20mm）は仮の値です（確認待ち）。専門家が位置ごとに決める枠（FRAME）は、まだ作っていません
- 書き出したファイルはシミュレーション用です。実機には入れないでください

## 上下の向きの設定
- 「上に」「下げて」を Z＋・Z− として読むのは、専門家が確かめて登録した設定（既定 `records/updown.json`。git に入りません）に合うときだけです。無ければ、そのたびにボタンで確かめます
- 登録は確かめた本人が次のコマンドで行います（AI や画面からは書きません）
  ```
  python -m voice_teaching.updown register --job samples/dummy/DEMO_C_PLACE.JBI --robot HC20DT --by （名前） --evidence （何で確かめたか）
  ```

## API キーと料金
- `.env`（`.gitignore` で除外。GitHub に上がりません）に書きます
  ```
  ANTHROPIC_API_KEY=（自分のキー）
  ANTHROPIC_MODEL=claude-sonnet-5-5
  ```
- 料金の上限：`VOICE_TEACHING_API_BUDGET_USD`（既定 5ドル）。説明・意味理解・解釈のすべての呼び出しを `records/api_usage.jsonl` に記録し（送った中身・キーは書きません）、累計が上限を超えそうなら呼びません
- キーをコード・コミット・チャット・スクリーンショットに書かないでください

## テスト
```powershell
python -m pytest -q --ignore=tests/test_e2e_browser.py   # 全部（偽の Claude）
node --test tests/*.mjs                                  # 画面の部品
python tools/e2e_browser.py                              # ブラウザーの通し（Playwright）
python tools/nl_eval/run_eval.py --mode mock --cases tools/nl_eval/cases_v2.json            # 自然言語の評価（モック）
python tools/nl_eval/run_eval.py --mode live --env-file .env --cases tools/nl_eval/cases_v2.json  # 本物の Claude（費用がかかる）
```
結果と画面：`docs/v1.0-rc/`

## 守ること
- 実機の JOB・会社のデータ・API キーをリポジトリに入れない（`*.JBI` は `.gitignore` で除外。入れてよいのは `samples/dummy/` の合成 JOB だけ）
- 開発の決まりは `CLAUDE.md`

## ライセンス
[MIT License](LICENSE)
