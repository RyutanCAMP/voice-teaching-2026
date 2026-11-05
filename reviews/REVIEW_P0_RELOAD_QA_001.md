# REVIEW_P0_RELOAD_QA_001 — P0「JOB の読み直し」修正の QA レビュー

- 確認者：qa-reviewer（サブエージェント。Claude Opus 5.5）
- 日付：2026-11-05
- 対象：C:\アプリ\vt-p0-reload、ブランチ fix/p0-reload-close、cf0b3fb → 42843c2（eb4a84e, 42843c2）
- 注意：このサブエージェントはメインや実装担当と**同じ Claude** で、文脈が別なだけ。外部の独立レビュー（チャッピー）の代わりにはならない
- コードは変えていない。コミットしていない。本物の Claude は呼んでいない（モック・保存した応答の再生だけ）。ポート 8805・8809・8811・8813 は使っていない（サーバーはすべて port=0）

## 判定：CONDITIONAL PASS（撮影の準備は進めてよい。FAIL に当たる指摘はなし）

条件（未確認事項）：本物の Claude の解釈（ClaudeInterpreter）を通したときの錠の待ち方、実マイク、Chrome 以外のブラウザー。
実機投入可の判定ではない。

## 実行したコマンドと結果
環境：`PYTHONUTF8=1 PYTHONIOENCODING=utf-8 C:/アプリ/voice-teaching/.venv/Scripts/python.exe`、作業場所 C:\アプリ\vt-p0-reload

| # | コマンド | 結果 |
|---|---|---|
| 1 | `git diff cf0b3fb 42843c2`（全部読んだ） | 変更は flow.py・app.js・explain.js・テスト4つだけ。jbi.py・sim.py・検査・承認/出力の本体は変わっていない |
| 2 | `pytest -q tests/test_p0_reload_close.py` | 91 passed in 18.99s |
| 3 | `pytest -q -p no:cacheprovider --ignore=tests/test_e2e_browser.py`（上限 20分） | 5226 passed, 1 skipped, 22 xfailed in 219.07s |
| 4 | 修正前の確認：`git archive cf0b3fb` を一時フォルダに出し、新しいテスト4ファイルだけ写して実行（対象の作業ツリーは触っていない） | `test_p0_reload_close.py` ＋ acceptance の該当分で **89 failed, 5 passed**。失敗の理由は `assert True is False`（古い番号の操作が ok:True）、`session_closed` が無い、`len(_entries)==8`、古い出力で ok:True など、実際の振る舞いで落ちている（名前の無い定数で落ちたものではない）。xfail を外した3件も修正前は失敗 |
| 5 | `node --test tests/*.test.mjs`、`node --check app.js / explain.js` | 124 pass / 0 fail、構文 OK |
| 6 | `tools/p0_qa/replay_all.py --run qa-p0-reload`（上限 15分） | PASS 121 / FAIL 44 / REVIEW_REQUIRED 4、API calls 0 |
| 7 | 比べるため `replay_all.py --run qa-p0-reload-basecf0 --commit cf0b3fb` | 同じ 121/44/4。169件すべて結果が同じ（差 0）。44件の FAIL は修正前からのもので、この修正の退行ではない |
| 8 | `pytest -q tests/test_e2e_browser.py`（上限 15分） | 1 passed in 577.51s |
| 9 | 自作の確認スクリプト（モック・一時フォルダ）：scratchpad/probe/probe.py | 34項目中 33 OK。NG の1件は下の L1（修正前と同じ振る舞い） |
| 10 | 同梱 Chromium で2つのタブ：scratchpad/probe/tabs.py、stale.py | 13/13 OK |
| 11 | `git diff cf0b3fb 42843c2 \| grep -Ei "sk-ant\|api_key\|password\|secret\|PRIVATE"` ほか | 機密・キー・実ジョブなし（`secrets.token_urlsafe` の文脈行だけ）。出力物（output/）は git の対象外 |

## 確かめたこと（依頼の1〜6）
1. 古い番号の API：approve・export・repeat・dialog・hear・talk（指示/回数）・select・state・undo・compare・ask（文/sync/info/reset/choose）がすべて `ok:false, session_closed:true`、状態を返さない。HTTP の経路（server.py）もすべて `_run` か `_live` を通る。
   - 異常な番号：None・0・-1・NaN・inf・True/False・空・10万文字・list・dict・末尾 CRLF・大文字・前に空白 → すべて例外なく断る。正しい画面は生きたまま
   - 異常な previous（None・0・True・NaN・空・10万文字・list）でも読み込めて、前は閉じる
   - 読み直し失敗（空・CRLFだけ・Shift_JIS の文・NUL・//NAME なし・5MB）：前を閉じ、`session_closed:true` を返す。Shift_JIS＋CRLF のサンプルは読めて、前を閉じる
   - 70回続けて読み込み：生きているのは最後の1つだけ。最初の番号は NO_SESSION の文で断る（_CLOSED_KEEP=64 を超えたため。断ることは同じ）
   - 20スレッドで同時に読み込み：生きている画面はちょうど1つ
   - 錠を待つ間に閉じる・Claude（説明）を待つ間に閉じる：開発者のテストが修正前に落ち、修正後に通ることを確認
   - 承認済み・未出力 → 読み直し → 古い出力は断り、ファイルはできない
2. 新しい画面：候補 → 承認 → 出力 が通る。人が教えた意味の引き継ぎ（同じタブ・同じ中身）は test_taught_meaning_is_still_carried_over_and_old_is_closed ほかで通る
3. 撮影の流れ（モック・一時フォルダ）：DEMO_C_PLACE.JBI → C00005 →「Zプラス方向に30mm」は「仮の上限 20mm を超える」で候補なし →「Zプラス方向に5mm」→ 承認 → 出力 DEMO_C_PLACE_SIM.JBI.txt。読み直したあと、新しい画面でも同じことができる（**出力先が別のフォルダなら**。L1）
4. 画面：タブA（承認済み）→ タブBで読み込み → Aで［出力］→ サーバーが断り、決まった文「この画面の作業は終わりました…」が返事の欄に出て、出力・承認のボタンが押せない。Aで確認表示から［承認する］→ 確認表示の中に「承認できませんでした」と決まった文が出て、ボタンが押せない（スクリーンショットで確認）。Aで読み直しに失敗 →「前の作業画面は終わりました…」が出る。Aで読み直すと closed は戻り、ふつうに使える
5. テストの期待の変更：test_old_sessions_are_dropped は「b が生きている」から「a・b とも閉じ、決まった文」に変わり、Q-7 に合わせて強くなった。acceptance の3件は本文を変えず xfail を外しただけ。test_e5_data_change_separates_conversation は L5 を参照（弱めたとまでは言えない）
6. 承認・出力・安全の判定・JOB の生成：approve()/export() の中身、jbi.py・sim.py・検査は差分なし。変わったのは入口（_run・_live・load）と閉じる処理だけ

## 指摘（重い順）
FAIL に当たるものはなし。

- **L1（低・撮影に影響あり、運用で避けられる）**：同じ出力フォルダで2回目の出力は「DEMO_C_PLACE_SIM.JBI.txt が既にあるため、上書きしません」で断られる。読み直したあとの新しい画面で同じ流れを撮ると、ここで止まる。修正前（cf0b3fb）でも同じで、上書きしない決まりどおり。撮影のテイクごとに出力フォルダを空にするか、別の一時フォルダにすること
  - 再現：probe.py の最初の2回の demo() を同じ DEFAULT_OUT_DIR で実行
- **L2（低・撮影に影響なし）**：指示の解釈（interpreter.decide）は画面の錠を持ったまま動く。その最中に別のタブで読み込むと、読み込みはそれが終わるまで待ち、古い画面には `ok:true, candidate_made:true, can_approve:true` の返事が返る（閉じたあとに届く成功の返事）。続く承認・出力はサーバーが断るので、変更は起きない。本物の Claude の解釈だと、読み込みが Claude の応答まで待つことがある
  - 再現：probe.py の Slow（decide を止める）で hear 中に load(previous=…)
- **L3（低・撮影に影響なし）**：出力の書き込み中（錠を持った後）に読み直すと、その出力は完了してファイル名を取り、新しい画面の出力は上書きしない決まりで断られる。読み直し「前」に始まった要求なので決定の範囲外と判断したが、記録しておく
- **L4（低・撮影に影響なし）**：explain.js の closedCheck は、返事がどの画面の番号のものかを見ずに vt:session-closed を出す（app.js の postJson は番号を見ている）。古い番号の /api/ask（sync・reset・choose）の返事が読み直しのあとに届くと、新しい画面が「終わりました」と表示され、承認・出力を押せなくなりうる（安全側の誤り）。/api/talk は送信中に読み込みを止めている（G2）ので、タブで試した範囲では起きなかった
- **L5（情報）**：test_e5_data_change_separates_conversation は、読み直したあとの新しい画面で会話が新しく始まること（前の会話を送らないこと）を確かめなくなった。新しい画面は新しい会話の記録から始まるので実害は見えないが、確認を1行足すとよい
- **情報**：別のタブで既に閉じられた番号を previous に付けて読み直すと、人が教えた意味は引き継がない（閉じた画面の中身は捨てるため。Q-7 と合う）。1つのタブで使う撮影には関係しない

## 未確認事項
- 本物の Claude（解釈・説明）を通したとき（呼んでいない）
- 実マイクでの音声入力（FAKE_RECOGNITION だけ）
- Chrome 本体・Edge など同梱 Chromium 以外
- H02・H16・世代番号・ホームに戻る（今日はやらない決定）
- replay の 44 件の FAIL の中身（修正前と同じであることだけ確認）

## 使った一時ファイル
- C:\Users\<user>\AppData\Local\Temp\claude\902507686a100f0b\6bc1279b-dd30-4df5-bdad-061a4303e89d\scratchpad\probe\probe.py・probe2.py・tabs.py・stale.py
- スクリーンショット：同 scratchpad\shots\
- replay の結果：C:\アプリ\vt-p0-reload\output\p0-20261105\qa-p0-reload、qa-p0-reload-basecf0（git の対象外）
