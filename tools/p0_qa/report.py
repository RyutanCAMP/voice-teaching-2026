"""Publish reviewed P0 evidence without changing the preceding independent review."""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / 'voice-teaching-cock'
BASE = ROOT / 'output/p0-20261105'
OUT = ROOT / 'tools/p0_qa/reports/20261105'
OUT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('review_helper', ROOT / 'tools/p0_qa/review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

before, after = review.read_run('before'), review.read_run('delivery')
old = review.old
improved = set('A02 A04 A07 A17 A20 A25 A27 A31 C03 C14 E02 E03 E04 F04 F07 F08 F15 F16 H04 H11 SUP-E03-taught SUP-E03-candidate SUP-F07-candidate NUM-FRACTION-23'.split())
rows = []
for id, r in after.items():
    status = old[id]['status']
    if r['status'] == 'ERROR':
        status = 'REVIEW_REQUIRED'
        reason = '旧保存応答に、新しく到達した方向補完のSDK応答が無いため再生実行エラー。合格に加えない。'
    elif id in improved:
        status = 'PASS'
        reason = ('元の問い・原応答・アプリの補足・候補/範囲/承認状態を個別再確認。整数/曖昧さの質問、取消、実装範囲、目的質問の該当契約を満たす。'
                  '既存候補のある曖昧回数は既存候補を保持し、新規候補・承認を作らない。')
    elif review.prior.fingerprint(r) == old[id]['fingerprint']:
        reason = '修正前と意味に関わる投影が完全一致。前回の個別判定を継承。'
    else:
        reason = ('原要求の意味契約で再確認。旧PASSの取消6件と古い範囲ボタン拒否は維持。'
                  '汎用補足だけのA08/A19、回数の言い換え、H02の言い直し等は未解決としてFAILを維持。E06も保留を維持。')
    rows.append(dict(id=id, before=old[id]['status'], after=status, reason=reason,
                     contract=old[id]['contract'], input=r['text'], replay_execution=r['status'],
                     layers=review.prior.projection(r) if 'response' in r else None))
recaptured = read(BASE / 'delivery-context-recaptured/results.json')['cases'][0]
assert recaptured['status'] == 'PASS'
combined = [dict(r, combined_after='PASS' if r['id'] == 'SUP-D05-dialog' else r['after']) for r in rows]
live = [read(BASE / n / 'results.json') for n in ['final-live-focused', 'final-live-context']]
calls = [c for run in live for c in run['api_calls']]
usage = {k: sum(c.get('usage', {}).get(k, 0) for c in calls) for k in
         ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']}
assert len(calls) == 5 and all(c['status'] == 'success' for c in calls)
assert len(rows) == 169
assert all(not r['response']['state']['approval'] and not r['response']['state']['exported'] for r in after.values() if 'response' in r)
risks = [id for id in ['H02', 'H16'] if after[id]['response']['state']['candidate'] and after[id]['response']['state']['can_approve']]
summary = dict(target_sha='ce7c981a917e9137f2f2e1b584fc39d579892ed9', base_sha=read(BASE / 'before/manifest.json')['commit'],
    before=dict(Counter(r['before'] for r in rows)), replay=dict(Counter(r['after'] for r in rows)),
    replay_executed=168, replay_execution_errors=['SUP-D05-dialog'],
    combined=dict(Counter(r['combined_after'] for r in combined)),
    combined_method='168 original-response replays + 1 replay of a newly captured real response. Not 169 new live trials.',
    improved=sorted(improved | {'SUP-D05-dialog'}), regressions=[],
    real_api=dict(calls=len(calls), successful=5, usage=usage, estimated_usd=round(sum(c['estimated_usd'] for c in calls), 8),
                  median_seconds=statistics.median(c['elapsed_seconds'] for c in calls), model='claude-sonnet-5-5',
                  source_sha=live[0]['commit'], cases=7, direct_target_api_cases=4),
    observed_hard_boundary_violations=dict(unrequested_approval=0, output_from_language=0, original_job_modified=0, robot_transfer=0),
    unresolved_approvable_candidate_risks=risks, microphone='NOT_RUN', filming='FAIL')
write(OUT / 'summary.json', summary)
write(OUT / 'case_verdicts.json', dict(cases=combined, context_recaptured=review.prior.projection(recaptured)))
assets = [QA / 'tools/live_claude_qa' / n for n in ['natural_cases_v1.json', 'natural_extra_v1.json', 'context_supplement_v1.json', 'fraction_boundary_v1.json']]
provenance = dict(target_sha=summary['target_sha'], prior_qa_commit='da9ee7b',
    test_asset_sha256={p.name: sha(p) for p in assets},
    job_sha256={p.name: sha(p) for p in (ROOT / 'samples/dummy').glob('DEMO*JBI')},
    prior_review_sha256=sha(QA / 'tools/live_claude_qa/reviews/20261104-supplement/semantic_review.json'),
    condition=dict(updown='unconfirmed', real_microphone=False, API_retries=0, synthetic_only=True, dedicated_port='ephemeral, never 8765'),
    baseline_semantic_projection_mismatch=[], app_files_changed=subprocess.check_output(['git','diff','--name-only','20c8464','HEAD','--','voice_teaching'],cwd=ROOT).decode().splitlines())
write(OUT / 'provenance.json', provenance)

report = f'''# コックさん P0原因調査・最小修正（2026-11-05）

撮影利用の総合判定：**FAIL**。重点P0の代表再現は改善したが、自由会話全体の不合格46件、保留2件、既存E2Eの実行エラーが残る。人間の最終判断・シクロ側での統合確認が必要。

- 環境：独立clone由来の専用worktree `C:/Users/User/voice-teaching-cock-p0`、`fix/cock-p0-conversation`。
- 基準：`night/qa-1105` / `{summary['base_sha']}`（調査開始時に共有環境から取得した確定コミット）。GitHub取得も独立clone内のみ。共有環境・UIファイル・ポート8765には変更なし。
- 検証した本体SHA：`{summary['target_sha']}`。最初の修正 `0ec499a`、追加修正 `35c537f`、`e446563`、`38c2507`、`db1c8c5`、最終互換修正 `ce7c981`。
- 既存169件・独立原本は改変していない。初期化前の実測判定も保存。安全判定・承認条件・JOB生成・出力関数は不変（独立AST/ファイル照合済み）。

| 条件 | PASS | FAIL | 保留 | 実行エラー |
|---|---:|---:|---:|---:|
| 修正前・旧実応答169件再生 | 96 | 71 | 2 | 0 |
| 修正後・同じ旧実応答 | 120 | 46 | 3 | 1（保留に含む） |
| 文脈補完1件を新しい保存実応答で補完 | 121 | 46 | 2 | 0 |

25件改善、既存PASS96件の退行0。最後の行は168件の旧応答再生＋1件の新応答再生であり、169件の新規実API実行ではない。旧応答だけで再生できないケースを合格へ書き換えていない。

重点再現・既存関連615テストはPASS。全体pytestはブラウザーを分離して5030 PASS / 1 SKIP、Node 17 PASS。SKIPはtest_c028_decisionsの上下未確認で会話が始まらない既存ケース。既存E2Eは1実行エラー（`Browser.new_page: Response has been disposed`、`tools/e2e_browser.py:1875`の応答フック付近）で未合格。共用サーバーは停止せず、停止したのは自分の待ち続けたpytestプロセスだけ。

## 4層で確認した根本原因

| P0 | 元文・実Claude応答 | アプリ側の根拠と修正 | 表示・内部状態の再確認 |
|---|---|---|---|
| 1 小数 | `2.3回にして`。実応答は整数指定を求め、to_count=null | `claude_talk.read_count`の整数正規表現が末尾3回を拾い、`_repeat_canonical`で小数部分が消失。数値トークン全体を先に検査 | 整数の再指定。API前停止、範囲/候補なし。0.3/3.5/全角/負号/科学表記/数字列も確認 |
| 2 曖昧 | 保存実ログは`3回くらいにして`、モデルはto_count=3へ確定 | くらい等がためらい検査に無く、修飾語を落としてcanonical化。約/程度/だいたい等も確認へ | `3回でよいですか？`。明示的な言い直しまで新しい範囲/候補なし。「はい」だけでは進まない。末尾なし`3回くらい`はプログラム/UI再生で確認 |
| 3 取消 | `今の提案は取り消して`。旧版ではAPI未呼出し | `_plain_undo`が接頭辞末尾「は」を否定と扱い候補を残した。完全一致の取消文を既存undoへ接続。迷い/否定/複合は実行しない | 候補破棄、can_approve=false。承認・出力・古い範囲ボタンの呼出しが拒否される。取消後のJOB出力なし |
| 4 文脈 | `C00002を10mm動かして`→`Zプラス`。旧版は両方API未呼出し | Sessionが操作語なしの回答だけを結合。軸文字はmoveと判定され、量と対象が捨てられた。明示対象と量のある指示への軸/符号だけの返事に限定して既存mergeを使用 | 実Claude1回でC00002 Z +10.000mm候補、未承認。選び直し/新指示/否定/過大/精度不足で以前の量を流用しない |
| 5 質問 | E02実応答「『いい感じ』とは、どう変えたいということですか？」、intent=ask_user | パースは成功。`reply_mvp_problem`が「変え」を状態断定語として非表示化。検査は緩和せず、アプリが変更内容の具体化質問、説明時はJOB由来の目的質問を補足 | E02/C03/A04の代表質問が届く。初回説明には既知の反復と目的確認を補足。**汎用文だけで質問全般を解決したとは判定しない** |
| 6 未実装 | `実機にジョブを送って`。API未呼出し | 共通APPROVE_MESSAGEが存在しない転送ボタンを案内。文言だけを修正 | 承認/合成シミュレーション出力はボタン、実機転送/実機用出力/直接操作は未対応、と区別 |

さらに、Claudeがask_user/explain・回数不一致・APIエラーの場合にも範囲へ進んだ入口の停止漏れと、`99回を3回`の現在回数の取りこぼしを修正。元JOB・利用者の対象/数値を優先し、不一致を隠さず停止する。意味が合わないまま先へ進む期待値は、停止を検査する方向に強化した。

4層の全データは `tools/p0_qa/reports/20261105/case_verdicts.json`。入力、元SDK tool内容、アプリ解釈/返信/補足、候補・承認等の内部状態を保存。UIはE02/A02/小数/曖昧の4件を実ブラウザーで確認、アニメーション完了後の画像も保存した。DOMの文字存在だけの検査は視認確認と区別した。

## 実Claudeと再生の区別

追加有料呼出しは **5回すべて成功**、7シナリオを確認（対象発話が実APIへ届いたものは4件、取消ケースの1回は正常候補を準備する発話）。小数・曖昧・実機転送・取消自体はAPI前の決定論処理。モデル `claude-sonnet-5-5`、実測中央値 **{summary['real_api']['median_seconds']:.3f}秒**、概算 **${summary['real_api']['estimated_usd']:.7f}**。請求額未照合。トークン内訳はsummary.json。

料金は公式 https://platform.claude.com/docs/en/models/sonnet-5-5/overview の入力$2/出力$10/5分cache書込$2.5/cache読込$0.10（100万tokens）で再確認。APIキーは元.envを子プロセスメモリで読むだけ。値の表示/複製/保存なし。プロンプトを変更しておらず、最後の局所変更は保存応答を再生して検証した。

## 残件と撮影判定

- **P0残件 H02**：`さっきの3回じゃなくて2回`は打消しとして拒否し、旧2→3候補は残る。明示し直された希望に対する候補の無効化方針が必要。
- **P0残件 H16**：取消＋説明の複合要求を解決せず、旧候補が残る。取り消すか確認する状態と、候補を承認可能なまま保つかを独立レビュー付きで決める必要がある。
- 上記2件では旧候補のcan_approveがtrue。**無断承認・音声出力・実機転送の観測は0だが、安全上の問題0とは報告しない。** 承認条件の変更は本担当では行っていない。
- P0-5は代表ケースを改善。A08/A19等の全体の流れを聞く問いへ汎用反復案内だけでは十分でない。正常な追加回数の言い換え（B04/B10/B11/C09/G06等）もFAILを維持。
- 保留A18（未知初期姿勢の距離）、E06（候補なしで曖昧な戻し対象）を成功に含めない。全46件のFAIL一覧はcase_verdicts.json。
- 独立レビューはce7c981局所240 PASS、35c537f時点981 PASS。いずれもAPI0。変更範囲の保留解除であり、P0全体・安全合格の承認ではない。
- 実マイク36件は未実施。音声→文字と文字→意味を別欄で記録する。まず本修正をシクロ側で確認し、合成JOBの固定台本のみで人が判断する。今回の総合判定は**FAIL**、撮影本番への無条件投入は推奨しない。

## シクロへの引き継ぎ・実行方法

UI静的ファイルは無変更。局所差分を専用ブランチから確認し、現在の会話入口・意味登録・候補状態との統合を別途行う。main/シクロのブランチへのmergeはしていない。CHECKER_032/033の原本は無変更。231件の旧独立ランナーは今回再実行していない（今回は169件と既存安全pytestを優先）。

```powershell
# 専用worktreeで実行。既存QA clone（da9ee7bと夜間保存応答）を同階層に置く。
C:/Users/User/voice-teaching-cock/output/qa9417/venv/Scripts/python.exe -m pytest tests/test_cock_p0_conversation.py -q
C:/Users/User/voice-teaching-cock/output/qa9417/venv/Scripts/python.exe tools/p0_qa/checks.py core --label final
# 新規run名が必要。既存結果は上書きしない。
C:/Users/User/voice-teaching-cock/output/qa9417/venv/Scripts/python.exe tools/p0_qa/run.py --variant delivery --run new-replay --recorded baseline-live
```

再生は追加API0。`--mode live`は有料試験なので明示して区別する。保存済み応答のない新経路は実行エラー/保留として残す。全体の実行エラーや中間の失敗も evidence.tar.gz 内に保存し、最終PASSと混ぜていない。

出力先スナップショットが無い別環境では、最初に `tools/p0_qa/run.py --snapshot delivery --commit ce7c981` で確定版を展開する。169件の資産・録音済み応答は上記の既存QA cloneが前提。付属evidence.tar.gzには再生前後のSDK tool原応答も含む。テスト資産を新しい169発話で作り直してはいない。

テスト基盤自身の中間エラーも区別した。初回Git commitは作者設定不足、snapshot1回は準備完了前の起動、UI probe1回はPlaywright引数の書き方の誤りだった。いずれも本体不具合/合格には数えず、API追加0。個別の停止・失敗ログと最終結果を区別している。
'''
(ROOT / 'docs/COCK_P0_20261105.md').write_text(report, encoding='utf-8')
members = [p for p in BASE.glob('*.txt')] + [p for p in BASE.glob('*.json')] + [p for p in BASE.glob('*.log')]
for n in ['before-base','before-extra','before-context','before-fraction','delivery-base','delivery-extra','delivery-context','delivery-fraction',
          'delivery-context-recaptured','final-live-focused','final-live-context']:
    members.append(BASE / n / 'results.json')
for n in ['before','delivery']:
    members.append(BASE / n / 'manifest.json')
members += list((BASE / 'ui').glob('*.json')) + list((BASE / 'ui').glob('*.png'))
with tarfile.open(OUT / 'evidence.tar.gz', 'w:gz') as tar:
    for p in sorted(set(members)):
        if p.exists(): tar.add(p, arcname=p.relative_to(BASE), recursive=False)
write(OUT / 'checksums.json', {p.name: sha(p) for p in OUT.iterdir() if p.is_file() and p.name != 'checksums.json'})
print(json.dumps(summary, ensure_ascii=False))
raise SystemExit(1 if summary['combined'].get('FAIL') else 2 if summary['combined'].get('REVIEW_REQUIRED') else 0)
