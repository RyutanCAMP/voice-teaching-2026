# シクロへ：撮影前のUI仕上げ

ブランチ：`ui/filming-polish`。起点：GitHub最新 `integrate/debug-1106` / `cf0b3fb`（作業前・共有前にfetchして一致確認）。mainへのmerge、8805・8813・8815の操作は行っていません。

表示の実装・検証コミット：`0625d68620043c1217c3cb48337af6eef91c436f`。後続は検証ログのUTF-8化と引継ぎ記録のみ。

## 変更範囲と競合確認

| 担当ブランチ | 変更を確認した主なファイル | 今回の扱い |
|---|---|---|
| feat/nl-demo-1106 | app.js、Pythonの自然言語・会話処理、テスト | 編集なし |
| fix/p0-reload-close | app.js、explain.js、flow.py | 編集なし |
| ui/3d-robot-base | app.js、index.html、view3d.js | 編集なし |
| 今回 | **style.css、highlight.jsの表示専用部品のみ** | 上記とファイル重複なし |

最新統合版のworktreeに未コミット変更がないことも読み取りで確認。自分専用worktree内だけで実装しています。既存HTMLへのscript追加・新規API/静的配信ルート追加は不要です。既存の`attachTips`起動時に表示部品を取り付けます。

## 画面の変更

- PCの左カラムを少し絞り、3Dに約70%の幅を確保。余白・角丸・背景・区切りを揃えました。
- 変更前後の座標を等幅の数字で大きく表示し、変更される軸をオレンジで強調。拒否された候補の表は灰色にして、承認できる案と区別します。
- 3Dの文字に細い背景色の縁取りを付け、経路と重なる位置番号を読みやすくしました。点・線・破線・座標・投影・フォントサイズの計算は変更していません。
- 図の技術的な補足2文だけを、クリック/キーボードで開閉できる **ⓘ 図の見方** にまとめました。元のDOMノード・ID・hidden属性を保ち、2D/3D切替の既存処理を維持します。
- 「この図で離れて見えても、当たらないとは限りません」等の限界の説明は常時表示。重大な警告、停止理由、承認の注意・名前欄・ボタンを折りたたんだり隠したりしていません。
- 既存の短いボタン名は維持。承認・範囲確認・拒否の色の意味、操作段階ごとの表示条件も維持しています。

## 変更ファイル

実行時に変わるのは次の2ファイルだけです。

1. `voice_teaching/web/static/style.css`：末尾の「Filming polish」ブロック。
2. `voice_teaching/web/static/highlight.js`：`attachFigureDetails`と、`attachTips`からの呼出し1行。既存の区間描画・表示値計算の関数は無変更。

追加物：`tools/preview_filming_polish.py`（独立ブラウザー検証）、本引継ぎ、`docs/ui-filming-polish/`のスクリーンショットと検証結果。

## 検証結果

| 検証 | 結果 |
|---|---|
| Chrome実画面：1920×1080 / 1280×720 / 390×844 | **44 / 44 PASS**、スクリプトエラー0 |
| 既存Node全体（色のコントラスト・音声・表示等） | **124 PASS** |
| 既存pytest：UI・ヘルプ・3D・承認後状態・review-only・debug-only | **92 PASS** |
| 修正前の表示部品で同じ操作を再実行 | 35 / 35 PASS |
| 3画面幅のSVG線分座標・破線属性 | 修正前後で**完全一致**、各9線分 |
| 保護対象13ファイル | 基準版と正規化した内容・SHA256が一致 |
| JOBの値 | C00002のX=400.000、Y=-150.000、Z=200.000→203.000をDOMで照合 |
| 警告・拒否 | 物理的安全の保証ではない旨が表示。C00000拒否時は赤い説明と承認ボタンdisabledを確認 |

テストは公開済みの合成`DEMO_C_PLACE.JBI`とモックだけです。実Claude・実マイク・実機は未使用。承認/出力のHTTPリクエスト0。自動採番の専用ポートを使い、共有3ポートへは接続していません。スクリーンショットは通常のモックサーバーで撮ったものです。シクロのdebug-only起動での承認禁止は既存サーバーの決定のままです。

途中の検証用スクリプトではCSP下の待機方法、初期のキーボード操作、基準CSSの再生用ルート引数を修正しました。これらはテスト準備の失敗であり、アプリの変更や合格として数えていません。上表は修正後に完了した検証です。

### スクリーンショット

通常画面・差分：

![通常画面](../docs/ui-filming-polish/1920-loaded.png)
![変更差分](../docs/ui-filming-polish/1920-difference.png)

確認と拒否：

![確認](../docs/ui-filming-polish/1920-review.png)
![拒否](../docs/ui-filming-polish/1920-blocked-review.png)

[1280×720](../docs/ui-filming-polish/1280-difference.png)・[小画面の確認](../docs/ui-filming-polish/390-review.png)も保存しています。小画面の確認内容は確認ボックス内でスクロールして読みます。

## シクロへの統合依頼

この専用ブランチの表示差分をレビューして、統合側で取り込んでください。こちらではmain・統合ブランチへのmergeと共有サーバーの再起動はしていません。今回の実画面確認は`cf0b3fb`に対するもので、別developerの最新変更との統合後は、図の補足開閉・候補の座標表・拒否時の承認禁止を再確認してください。

再実行：

```powershell
python tools/preview_filming_polish.py --out output/filming/recheck
node --test tests/*.test.mjs
python -m pytest tests/test_header_help.py tests/test_ui_4steps.py tests/test_ux_polish.py tests/test_3d_voice.py tests/test_c026_ui.py tests/test_after_approval_states.py tests/test_review_only.py tests/test_debug_review_only.py -q -p no:cacheprovider --basetemp=output/filming/recheck-temp
```

【開発報告】対象：撮影用UI仕上げ／実装：Codex／検証：上記／QA：上記の実測範囲でPASS／Safety：ロジック変更なし、実機安全判定ではない／未確認：他developer変更との統合後・実マイク・実機／残存リスク：基準版に残る会話P0は本UIでは解消しない／ブランチ：ui/filming-polish／統合：シクロ待ち、mainへのmergeなし。
