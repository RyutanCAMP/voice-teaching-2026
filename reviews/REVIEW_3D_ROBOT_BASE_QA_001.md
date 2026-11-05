# REVIEW_3D_ROBOT_BASE_QA_001（QA レビュー）

- 誰が：qa-reviewer（サブエージェント。メインと同じ Claude。外部の独立レビュー（チャッピー）の代わりにはならない）
- 何を：C:\アプリ\vt-3d-base ブランチ ui/3d-robot-base、基点 cf0b3fb → 対象 2a33510（基点との差で確認）
- いつ：2026-11-05
- コードは編集していない。コミットしていない。

## 判定：CONDITIONAL PASS（開発継続・撮影準備に使ってよい。実機投入可の判定ではない）

FAIL に当たる問題はなかった。撮影の流れは 1280×720・1920×1080 の両方で通った。見た目の指摘（中2件）は撮影の画面に写るので、撮る前に直すか、写さない画面を選ぶこと。

## 要求との照合
| 唐川さんの決定 | 結果 |
|---|---|
| 3D に黄色の丸「ロボット（台座の中心）」と「ロボットの正面（X＋）」の矢印 | OK（写真と DOM で確認。矢印は床の X 軸の＋向き、e2e で cos>0.97） |
| HC20 のモデルと表示・非表示のボタンを出さない | OK（static 全ファイルに HC20 のモデル用コード・文・ボタンなし。stage-head のボタンは view-reset のみ） |
| JOB の点・経路・位置番号・XYZ の軸・格子は今までどおり | OK（格子の Z が Z=0 になり、床の余白 pad が 0.15→0.06 に変わった。点・経路・番号は同じ） |
| 最初の視点と［視点を戻す］で台座と点が両方見える | OK（e2e の check_3d_robot_base と自分の撮影） |
| 2D にも原点と正面の目印 | OK（Y・Z は「この図の面に垂直」の文、X・Z／X・Y は矢印） |
| モデル用の注意書きを消し、参考表示・干渉を保証しない注意書きは残す | OK |
| 決定の記録 docs/DECISION_3D_ROBOT_BASE_20261105.md | OK（1回目・2回目の決定と残るリスクあり） |

## 指摘（重さ順）

### M1（中・撮影に影響あり：2D の図と確認表示を写す場合）札が自分の印（原点の丸・正面の矢印）に重なる
- 2D の X・Z／X・Y：「ロボットの正面（X＋）」の文字に原点の点線の丸が重なり、「ロボット（台座の中心）」の文字が矢印の根元・十字に重なる。2D の札は地の四角が無く、印と同じ黄色なので読みにくい。
  - DOM 計測（1920×1080・X・Z）：「ロボットの正面（X＋）」の四角が circle.base2d・line・line.front2d と重なる。「ロボット（台座の中心）」が circle.base2d・line・path と重なる。1280×720 でも同じ。
  - 写真：qa3d\zoom_1920_2d_xz.png
- 確認表示の 3D（#confirm-figure）：「ロボット（台座の中心）」の札が正面の矢印（line.robot-front）の上に乗り、矢印がほぼ隠れて矢じりだけが見える。正面の向きが確認表示では読み取りにくい。
  - 写真：qa3d\zoom_1280_confirm_base.png、qa3d\1280x720_C00005_confirm.png
- メインの 3D でも「ロボットの正面（X＋）」の札が矢じり（path.robot-front-head）に少しかかる（読める程度）。
- 原因（読んだ範囲）：drawBase2d の boxes、View3D._robotLabels の taken に、台座の印・矢印そのものが入っていない。e2e の重なりの確かめも、点・名前・軸の文字との重なりだけで、自分の印との重なりを見ていない。
- 再現：DEMO_C_PLACE.JBI を読み込む → C00005 → 「Zプラス方向に5mm」→ X・Z のタブ／［確認して承認へ］。

### M2（中・撮影に影響あり：1280×720）原点を図に入れたため、JOB の点が約3割小さくなった
- 同じ流れ（C00005 Z+5mm の候補）で、点の広がり（画面 px、幅×高さ）：
  - 1280×720：基点 cf0b3fb 181×211 → 対象 123×155（約 68〜73%）
  - 1920×1080：基点 350×407 → 対象 237×298（約 70〜73%）
- 写真での判断：1920×1080 は点・名前・変更の札「C00005 Z +5.000mm」とも十分読める。1280×720 は点の名前（12px を縮めた文字）が小さく、図の左下に空きが多い。読めなくはないが、撮影では小さく見える。変更の大きさは右上の拡大の窓で見えるので、内容は伝わる。
- 撮影の提案：1920×1080 で撮る。1280×720 で撮るならホイールで拡大してから撮る（［視点を戻す］で元に戻る）。
- 写真：qa3d\1280x720_C00005_Zplus5_candidate.png、qa3d\1920x1080_C00005_Zplus5_candidate.png、比較用 qa3d\base_cf0b3fb\1280x720_C00005_Zplus5_candidate.png

### L1（低・撮影に影響なし）原点に JOB の点があると、台座の札が点に重なる
- C00000 を (0,0,0) にした架空のジョブ（scratchpad の qa3d\edge_jobs\QA_ORIGINPT.JBI）で、「ロボット（台座の中心）」が点 C00000 と重なった。findSpot は空いた所が無いと「枠の中の重なる所」を返すため。決定の記録の「無ければ出さない」と食い違う。点は押して選べた。

### L2（低・撮影に影響なし）原点から遠く、広がりが小さいジョブでは点が一か所に固まる
- X を +1200、Y・Z を 1/10 にした架空のジョブ（QA_FAR.JBI）で、点が数十 px に固まり、押すと「重なっている位置」の一覧が出た（一覧は図の下で切れて見える）。押せる範囲の最小が 7px になった変更も影響。決定の記録の「残るリスク」に書かれている範囲。

### 参考（今回の変更ではない・基点でも同じ）撮影の台本の「出力のあと Zプラス方向に30mm」
- 出力のあとで同じ画面のまま「Zプラス方向に30mm」を送ると、「新しい指示を受け取ったため、前の承認を取り消しました。変更候補は残っています」が出て、+5mm のカードが残る（基点 cf0b3fb でも同じ動き）。読み込み直してから C00005 →「Zプラス方向に30mm」なら「1回に動かせる量を超えています」で断られ、［確認して承認へ］は押せない（両方の大きさで確認）。撮影の前に、台本の順番どおりに一度通しておくこと。

## テストを弱めていないか
- 基点との差で、テストの削除・条件の緩和はなし（tests/ui_contrast.test.mjs は追加のみ、tools/e2e_browser.py は check_3d_robot_base の追加と呼び出しのみ、tests/test_3d_robot_base.py は新規）。
- 新しい受入テストを基点 cf0b3fb のコード（git archive で scratchpad に展開）で実行：6 failed, 2 passed。通った2件は「モデルが無い」「ボタンが元のまま」で、基点でも満たす内容なので妥当。
- サーバー・安全の判定・承認・出力・samples の変更はなし（git diff --name-status で確認）。

## 実行したコマンドと結果（C:\アプリ\vt-3d-base、PYTHONUTF8=1 PYTHONIOENCODING=utf-8、C:/アプリ/voice-teaching/.venv/Scripts/python.exe）
- `git diff --stat cf0b3fb 2a33510` / `git diff cf0b3fb 2a33510`：7ファイル（docs・tests 2・tools/e2e_browser.py・app.js・index.html・view3d.js）
- `python -m pytest -q tests/test_3d_robot_base.py`：8 passed
- `python -m pytest -q --ignore=tests/test_e2e_browser.py`：5140 passed, 1 skipped, 25 xfailed（2分47秒）
- `node --test tests/*.test.mjs`：tests 127 / pass 127 / fail 0
- `python tools/e2e_browser.py --shots <scratchpad>\qa3d\e2e_shots`：「結果： すべて通りました」、終了コード 0、OK 1499 件（うち「台座」138 件）
- 自作の確認（scratchpad の qa3d_flow.py・qa3d_z30.py・qa3d_2d.py・qa3d_edge.py。サーバーは port=0、MockInterpreter、本物の Claude は呼ばない、出力先は scratchpad）：
  - DEMO_C_PLACE → 3D の点 C00005 を押して選べた → 「Zプラス方向に5mm」で候補 → 札の重なり・はみ出し・隠れた点なし（ROBOT3D_JS）→ 再生の印が動いた（座標が変わる）→ 2D の3つの図に原点と正面の印 → 確認表示に台座の注意書き → 承認 → 出力 data-ok=true「シミュレーション用に出力しました」。1280×720・1920×1080 の両方。ページのエラーなし。
  - 新しく読み込んで C00005「Zプラス方向に30mm」→ 断られる・［確認して承認へ］押せない（両方の大きさ）
  - 同じ流れを基点 cf0b3fb でも実行して、点の大きさを比べた
  - 異常・境界：X 負（QA_NEGX）・Z 負（QA_NEGZ）・原点に点（QA_ORIGINPT）・遠くに固まる（QA_FAR）・X=4,000,000（QA_HUGE：読み込みで「桁を超えます」と止まり、図を描かない）。ページのエラーなし。
- 機密の確認：`git diff cf0b3fb 2a33510 | grep -niE "sk-ant|api[_-]?key|password|secret|token|BEGIN (RSA|PRIVATE)"`：該当なし。samples の変更なし。

## 写真（C:\Users\<user>\AppData\Local\Temp\claude\902507686a100f0b\6bc1279b-dd30-4df5-bdad-061a4303e89d\scratchpad\qa3d\）
- 1280x720_C00005_Zplus5_candidate.png / 1920x1080_C00005_Zplus5_candidate.png（C00005 Z+5mm の候補）
- *_playing.png・*_confirm.png・*_exported.png・*_2d_{yz,xz,xy}.png・*_Zplus30_fresh.png
- zoom_*（拡大）、base_cf0b3fb\（基点の同じ画面）、edge_1280_*（異常・境界）、e2e_shots\（e2e の写真）

## 未確認事項
- 実際の撮影の機材（画面の拡大率・ブラウザーの拡大・録画の解像度）での見え方。今回は同梱の Chromium・倍率 1 で撮った
- Shift_JIS・CRLF のジョブでの表示（今回の変更は画面の描画だけで、読み込みは変えていないため、試していない）
- DEMO_BUTTON_REPEAT の回数の変更（focus の図）での台座の札の出方は e2e の読み込み直後の確認だけで、候補の状態は自分では撮っていない
- 「X＋ がロボットの正面」が実機の据え付けと合うか（唐川さんの確認）
- 本レビューは同じ Claude のサブエージェントによる。外部の独立レビュー（チャッピー）の代わりにはならない
