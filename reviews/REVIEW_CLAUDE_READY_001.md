# REVIEW_CLAUDE_READY_001：本物の Claude API の準備（feat/claude-ready）

- 対象：`feat/claude-ready` 80b38a8（実装）→ 41e7894（指摘の修正）。基準は `feat/voice-stable` 682d1ab
- 実装：developer（サブエージェント）。レビュー：qa-reviewer・safety-spec-reviewer（サブエージェント、それぞれ別に）。統合判定：シクロ
- **全員が同じ Claude。外部の独立レビュー（クロちゃん）の代わりにはならない**
- 本物の API は呼んでいない（キーなし・有料）。`claude_check --call` も実行していない

## 1. 1回目のレビュー（80b38a8）
| 担当 | 確かめたこと（コマンド） | 結果 | 判定 |
|---|---|---|---|
| QA | `pytest -q` 1237 passed、`node --test tests/speech.test.mjs` 13/13、`tools/e2e_browser.py` OK 91・NG 0、`claude_check`（通信なし）、変異テスト 26件（すべてテストが失敗＝検出できた）、機密情報の grep | P1 なし。P2：anthropic のライセンス表記が MIT（正しくは Apache-2.0）、巨大な整数の量・数の content で例外が素通り。P3：.env の文字コード、承認待ちでも Claude を呼ぶ、モデル名を確かめずに表示、テストが本物の .env を読む、403 の案内 | CONDITIONAL PASS |
| Safety | `pytest -q` 1237 passed、e2e すべて通過、新しい文の禁止語の検査（すべてなし）、コードの読み合わせ | P1 なし。P2：CLAUDE_API.md 6章（「置く位置を少し手前に」）の設計が、送る内容の表・仕様v2.1 と食い違う。P3：`--yes` で料金の確認を飛ばせる、「1回だけ送る」が再試行で最大2回、403 の案内、帯の表示はブラウザーで未確認 | CONDITIONAL PASS |

安全の線（必ず check_proposal を通る、モックへ黙って切り替えない、キーとジョブの中身を送らない・出さない、承認・出力・起動の道を増やさない、断定の言葉を使わない）は守られている、が両者の結論。

## 2. 修正（41e7894）
| 指摘 | 直したこと |
|---|---|
| QA P2-1 | requirements.txt・CLAUDE_API.md・MVP_HOWTO.md を Apache License 2.0 に |
| QA P2-2 | `_convert` の思わぬ例外はすべて FAILED（元の例外は残さない）。±10**400・content=5 などのテスト |
| Safety P3-1 | `--yes` を削除（付けると引数エラー）。料金の確認は必ず `yes` の入力 |
| Safety P3-2 | 「1回送ります（混雑・接続失敗のときは SDK が最大1回やり直します）」「料金は最大2回分」 |
| Safety P2-1 | 6章を「未決（唐川さんの判断が必要）」に書き直し。案A（命令の行を送る。仕様v2.1 の形）／案B（FRAME の確定済みラベルから候補を作り Claude は選ぶだけ）。どちらも仕様v2.1 第19項（推定した名前は専門家の確定まで使わない）と「手前は FRAME の向きの表まで止める」を守る |
| QA P3-3 | .env を utf-8-sig で読む。読めなければキーなし扱い＋「.env を読めません（UTF-8 で保存してください）」 |
| QA P3-5 | モデル名は `claude-[a-z0-9.-]{1,60}` だけ。違えば値を出さずに止める |
| QA P3-6 | claude_check のテストは一時ファイルの .env だけを読む |
| QA P3-4 | **未対応（記録だけ）**：承認待ちの候補があっても、移動の指示を Claude に送る（session.py は別のブランチで変更中のため）。候補は作られないが、本物の Claude では1回分の料金がかかる |

## 3. 修正後の確認（シクロ、2026-11-03、会社の PC）
- `git diff --stat 80b38a8..41e7894`：7ファイル、+236/−46。session.py・utterance.py・jbi.py・sim.py・承認の流れは変わっていない
- `PYTHONUTF8=1 python -m pytest -q` → **1266 passed**
- `python tools/e2e_browser.py` → **OK 91・NG 0**
- `grep -- --yes voice_teaching docs` → なし。`python -m voice_teaching.claude_check --env <無いファイル>` → 通信なし・キーなし・api_key は「（伏せています）」
- 修正後のサブエージェントによる再レビューはしていない（指摘の項目ごとのテストと、上の再実行で確かめた）

## 4. 判定：CONDITIONAL PASS（開発の継続のため。実機投入の判定ではない）
未確認事項：本物の API での疎通・応答時間・`max_tokens` 8000 で足りるか、本物の 400/404 と SDK の例外の対応、refusal の fallbacks（意図して未使用）、帯「解釈：Claude（モデル名）」のブラウザーでの表示。
唐川さんへ：①「置く位置を少し手前に」は案A・案Bのどちらか ②キーを入れたら `python -m voice_teaching.claude_check --call` を1回（料金がかかる。yes の入力が必要）
