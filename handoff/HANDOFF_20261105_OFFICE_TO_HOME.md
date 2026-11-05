# 引き継ぎ：11/5 夜 会社PC → 自宅PC（11/6 撮影の準備）

- 書いた人：シクロ（会社PC）。2026-11-05 18時すぎ
- 正本：GitHub https://github.com/RyutanCAMP/voice-teaching
- **撮影用の統合ブランチ：`integrate/filming-1106`**。コードの SHA は **`4fa07c5`**（このメモはそのあとの文書だけのコミット）
- main には merge していない。強制 push もしていない
- **安全の合格ではない**（内部のレビューは同じ Claude。外部の独立レビューは別）
- APIキー・.env は、このメモにもリポジトリにも入れていない

## 1. 全体の進み具合

| 作業 | ブランチ・SHA | 状態 |
|---|---|---|
| クロクラウドの C1・C2・「あと N 回」 | `fix/kurocloud-c1c2-answers` `41ad793` | 完了。QA CONDITIONAL PASS・Safety PASS（クロクラウドの記録） |
| ヘッダーの「?」の整理（ⓘ） | `ui/header-help` `90f269e` | 完了。QA・Safety PASS。Chrome・Edge 224件 OK |
| 朝の UI の6項目 | `fix/ui-final-1105` `63b8806` | 完了。内部チェック PASS |
| デバッグ専用の起動（承認・出力をサーバーで断る） | `integrate/debug-1106` `cf0b3fb` | 完了。**8805 で稼働中（唐川さんの承認まで変えない）** |
| 自然な会話：差し戻しの直し・a〜d | `feat/nl-demo-1106` `c8a031e`（レビュー記録 `e08efc1`） | 実装・テスト完了。**3回目の QA・Safety は会社PCで実行中（結果は未コミット）** |
| 3D：台座の丸・正面（X＋）の矢印（HC20 モデルはやめた） | `ui/3d-robot-base` `2a33510`（記録 `083e1be`） | 実装・テスト完了。Safety CONDITIONAL PASS（重大なし）。**QA は実行中** |
| P0：JOB の読み直しで古い画面を閉じる | `fix/p0-reload-close` `42843c2` | 実装・テスト完了。**QA・Safety とも CONDITIONAL PASS（FAIL なし）** |
| P0：H02（言い直し）・H16（取消＋説明）・世代番号・ホーム | `fix/p0-stale-candidates` `a8d2143`（設計だけ） | **未着手**。クロちゃんのレビュー待ち。撮影では台本で避ける |
| 案1・2・4 の設計書 | `docs/direction-words-design` `59fec81` | 文書だけ |
| コックさんの撮影向け UI | `ui/filming-polish` | **GitHub で見つからない（未確認）** |

## 2. 撮影用の統合（`integrate/filming-1106` `4fa07c5`）の中身
`integrate/debug-1106`（`cf0b3fb`）に、次の3つを merge したもの：
- `feat/nl-demo-1106` `c8a031e`
- `ui/3d-robot-base` `2a33510`
- `fix/p0-reload-close` `42843c2`

### テスト（会社PCで `4fa07c5` を実測）
| テスト | 結果 |
|---|---|
| pytest（`--ignore=tests/test_e2e_browser.py`） | 5616 passed, 1 skipped, 14 xfailed |
| node | 127 / 127 |
| 169件の再生（更新の記録あり） | PASS 123・FAIL 40・REVIEW 4・UPDATED_PENDING_QA 2（D03・D14）。API 0回 |
| 169件の再生（`--no-updates`） | PASS 117・REVIEW 14・FAIL 38 |
| e2e（`tools/e2e_browser.py`） | **「すべて通りました」**（exit 0。`4fa07c5` で会社PCで実測） |

### 本物の Claude での通し（8815、文字の入力、DEMO_C_PLACE、18時すぎ）
| 順 | 操作 | 結果 |
|---|---|---|
| 1 | ［サンプルで試す］（DEMO_C_PLACE） | 読み込み OK。解釈は本物の Claude（claude-sonnet-5-5） |
| 2 | C00005 を選ぶ | OK |
| 3 | 「Zプラス方向に30mm」 | **断る**：「移動量 30mm は仮の上限 20mm（確認待ち）を超えるため、変更しません…」。候補なし |
| 4 | 「Zプラス方向に5mm」 | 候補 C00005 Z 200.000 → 205.000（+5.000） |
| 5 | 承認（名前：唐川） | OK。状態「承認 済み（ボタンで承認）」 |
| 6 | 出力 | `output\sim\DEMO_C_PLACE_SIM.JBI.txt`。2回目は「既にあるため、上書きしません」（決まりどおり）。**確かめに使ったファイルは消してある** |
| 7 | 読み直したあと、古い番号で承認 | **断る**：「この画面の作業は終わりました（新しいジョブが読み込まれたため）…」 |
| 8 | 「このロボット、何をしているの？」 | **Claude の答えが隠れた**：「動く大きさ（大きく・少し など）を言う言い方が入っていたため、表示しません」→ 下の P1-a |

- 実マイク・ブラウザーの画面の操作での通しは、**まだ**（唐川さんが 8815 で確かめる）
- 拡張子が `.JBI.txt` なのは、実機に取り違えて入れないための決まり（R6）。変えていない

## 3. 未解決の問題（優先順）
- **High（会話の3回目の Safety が FAIL・18時すぎ）**：向きの確かめのボタン（d の［合っている］）が、位置の選び直しや［元に戻す］のあとも使える
  - 例：C00003 で「右に10mm」→ C00004 を選ぶ → 前の［合っている］→ C00004 の候補ができる
  - 記録：`feat/nl-demo-1106` の reviews/REVIEW_NL_DEMO_1106_SAFETY_003.md
  - 会社PCで developer が直している最中。push されていれば `feat/nl-demo-1106` の先頭にある。確かめてから統合し直す
  - **撮影の台本（「Zプラス方向に5mm」）はこの道を通らない**。撮影で「右に」「上に」など向きの言葉を使わなければ影響しない
  - 同じレビューの Medium：1つの言葉を確かめると6つの向きが全部使える／「確かめた向きです」が関係ない候補にも付く。Low 5件。撮影のあとに
- **読み直しの P0 の QA（42843c2）：CONDITIONAL PASS**（FAIL なし。reviews/REVIEW_P0_RELOAD_QA_001.md の写し）。2つのタブで、古いタブの承認・出力が断られることを画面で確かめた。修正前のコードでは、新しいテストが 89件落ちる。169件は修正前と同じ
- **読み直しの P0 の Safety（42843c2）：CONDITIONAL PASS**（記録は reviews/REVIEW_P0_RELOAD_SAFETY_001.md の写し）。読み直しが返ったあと、古い番号で承認・出力・候補づくりができる道は見つからなかった
  - **撮影の注意（M-1）**：承認や出力を押したら、返事が出るまで次のジョブを落とさない。遅れて届いた古い返事が、新しい画面に「承認しました」を出すことがある（画面だけ。サーバーは断る。前からの動き）。直すなら app.js の承認・出力の返事で、送ったときの番号と今の番号を比べる
  - M-2：読み直しより先に錠を取った出力は書かれる（R-5）。受け入れるかは外部レビュー（クロちゃん）
  - M-3：sim.export_sim を直接呼ぶ層（取り消しの印 revoked）は後回し（世代番号と一緒に）。画面・HTTP の道ではない
  - L-1：会話の欄の送信中に同じタブで読み直すと、新しい画面が「終わりました」になることがある（止める側）
  - L-2：「この画面の作業は終わりました」は、完了とも読める。言い方は唐川さんの判断
- **会話の3回目の QA（c8a031e）：CONDITIONAL PASS**（FAIL なし。記録は reviews/REVIEW_NL_DEMO_1106_QA_003.md の写し）
  - 撮影の流れはブラウザーでも通った。D03・D14 は PASS
  - 撮影に影響しない中くらいの指摘：
    - M1：速度の問いへの答え「下げて」が、d の確かめを通って位置の Z の候補になる
    - M2：向きの確かめのボタンの古い文を /api/hear に送ると使える（選び直し・元に戻すは 5a1208e で直した見込み。［説明です］と回数の変更の道は未確認）
  - developer に、回数の二択の次に依頼済み
- **撮影に関係する（唐川さんが 8815 で見つけた・18時すぎ）**：「C0001からC0003までの動作を2回続けて下さい」→「2回の移動ですか、2往復ですか？」→「はい」→「この指示は使えませんでした」
  - 原因：
    - utterance.py の古い2択（facts.repeat・11/3）が回数の変更より先に出る。しかも、どちらを押しても止まる行き止まり
    - 声の「はい」は承認の言葉として断られる
    - 4桁の番号・「まで」「動作」「続けて」が c の型に入っていない
  - 直し方：
    - ジョブの区間を決められるときは、区間と回数を示して確かめる
    - 二択に「はい」と答えたら、ボタンを残して選ばせる
    - 行き止まりの2択をやめる
  - 会話の developer に、High-1 の次に依頼済み。push されていれば feat/nl-demo-1106 の先頭にある
  - **撮影では、回数の変更の場面を使わないか、「C00001からC00003を3回にして」（範囲の確認のボタンへ進む形）で撮る**
- **3D の QA（18時すぎ）：CONDITIONAL PASS**（FAIL なし。記録は reviews/REVIEW_3D_ROBOT_BASE_QA_001.md の写し）。撮影に関わる指摘が2つ
  - M1：2D の X・Z と X・Y、確認表示の 3D で、台座の札が自分の印（点線の丸・正面の矢印）に重なる → 撮影では 3D の主画面を写す。2D のタブと確認表示の 3D は、なるべく写さない。直すなら drawBase2d と _robotLabels の、札を避ける範囲に印を入れる
  - M2：原点も図に入れたので、点が約3割小さい → **撮影は 1920×1080 がよい**。1280×720 なら、ホイールで拡大してから撮る
  - 参考：出力のあとの画面のまま「Zプラス方向に30mm」を送ると、「前の承認を取り消しました。変更候補は残っています」になる（前からの動き）→ 30mm の場面は、出力の前（5mm の前）に撮る
- **P1-a（撮影に関係する）**：「何をしているの？」の Claude の説明が、大きさの言葉の網（`_MAGNITUDE`）で隠れることがある
  - 例：「少し待つ」のような、時間の「少し」でも隠れる
  - 安全側（隠すだけ）だが、デモの「Claude の正常な説明を隠さない」に反する
  - 直す場所：`voice_teaching/claude_talk.py` の `_MAGNITUDE`。動く距離の話のときだけ隠すようにする（`長く・短く` と同じ扱い）。テストを足して、QA・Safety を通す
- **P1-b**：3回目のレビュー（会話）、QA（3D）、QA・Safety（読み直し）の結果を確かめる
  - 結果のファイルは、会社PCの各作業フォルダの `reviews/` に未コミットで残る
  - 自宅からは見えないので、会社PCで commit・push するか、もう一度レビューを流す
- **P0（撮影では台本で避ける）**：H02（候補のあとに「やっぱり2回」）、H16（「今の変更はやめて」）。言い直したいときは［元に戻す］を押す
- 唐川さんの確認待ち：
  - 3D の原点の高さ（Z=0 が据え付け面か）、据え付けの形
  - 3D の画面に「台座の印は大きさ・形ではありません」を見える形で出すか
- コックさんの `ui/filming-polish`：GitHub に無い。**基本の動作を確かめてから統合**（唐川さん）
  - ファイルの分け方の案：style.css と新しい表示用のファイルはコックさん。app.js・index.html・view3d.js・explain.js はシクロ側

## 4. 撮影用のアプリの起動（8815）
```
cd <統合ブランチの作業フォルダ>
（.venv の python）-m voice_teaching.web --port 8815 --env-file <本体の .env>
```
- 会社PCの例：`C:\アプリ\vt-film` で `C:\アプリ\voice-teaching\.venv\Scripts\python.exe -m voice_teaching.web --port 8815 --env-file C:/アプリ/voice-teaching/.env`
- 起動の表示に「解釈：本物の Claude（claude-sonnet-5-5）」と出ること（「モック」なら止める）
- 環境変数：`PYTHONUTF8=1`・`PYTHONIOENCODING=utf-8`
- URL：http://127.0.0.1:8815/（Chrome・表示倍率 100%・新しいタブ）
- 8805 はデバッグ専用（`C:\アプリ\vt-debug`・`tools/debug_review_only.py`）。**唐川さんの承認まで変えない**

## 5. 撮影用のジョブと操作の手順
- ジョブ：`samples\dummy\DEMO_C_PLACE.JBI`（［サンプルで試す］と同じ）
- 撮影の前に：`output\sim\` に、残すべきファイルが無いことを確かめてから空にする
- 手順：
  1. ジョブを読み込む
  2. C00005 を押す（または声で「C00005」）
  3. 声で「Zプラス方向に30mm」→ 上限で断られる場面（**必ず5mm の前に撮る**）
  4. 声で「Zプラス方向に5mm」→ 聞き取った文を確かめて［送る］→ 候補（Z 200.000 → 205.000）
  5. 3D で、変更前（灰色）と変更後（オレンジ）、台座の丸、ロボットの正面（X＋）を見せる
  6. ［確認して承認へ］→ 名前 →［承認する］
  7. ［出力］→ `DEMO_C_PLACE_SIM.JBI.txt`（USB には手で写す。実機には入れない）
- 撮影中にしないこと：
  - 承認・出力の返事が出る前に、次のジョブを落とす（読み直しの Safety M-1）
  - 候補のあとの言い直し・取消（P0）
  - 撮影中の［詳細表示］
  - 同じジョブの2回目の出力（上書きしない決まりで止まる）

## 6. 自宅PCで再開する手順
```
git clone https://github.com/RyutanCAMP/voice-teaching.git（既にあれば git fetch origin）
git worktree add <空のフォルダ> origin/integrate/filming-1106
cd <そのフォルダ>
py -3.13 -m venv .venv
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install playwright
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m pytest -q --ignore=tests/test_e2e_browser.py   （約4分。5616 passed が線）
node --test tests\*.test.mjs                                           （127）
.venv\Scripts\python tools\p0_qa\replay_all.py --run home-check         （PASS 123 が線）
.venv\Scripts\python tools\e2e_browser.py                               （約10分。「すべて通りました」）
```
- `.env` はリポジトリの外に置く。キーは自分で入れる（コピーしない）
- 各ブランチの作業は、上の表のブランチから `git worktree add` で別のフォルダに取る

## 7. 明日の撮影の前に必要な残件（優先順）
1. 唐川さんが 8815 で、マイクと画面の操作で手順5を通す（会社PC）。気になった所を記録する
2. 統合版の e2e と、3回目のレビュー（会話・3D・読み直し）の結果を確かめる。High があれば直す
3. P1-a（説明が隠れる）を直す：撮影で「何をしているの？」を使う場合
4. 唐川さんの承認のあと、8805 を統合版に入れ替える（または撮影は 8815 のまま）
5. コックさんの UI は、基本の動作を確かめたあと、ぶつからなければ統合する
