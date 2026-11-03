# クロちゃんへ：CHECKER_024（B1・B6 の再レビュー）のお願い

- 対象ブランチ：**`fix/unknown-words`**、コミット **ec04caa**（`feat/voice-stable` 682d1ab から。ほかのブランチは混ぜていない）
- 直したもの：CHECKER_023 §3 **B1**（知らない言葉を捨てて Z の候補にする）、CHECKER_023A §1 **B6・B7**（打ち消しを無視して逆の候補／否定した位置に候補）、B8・B9・B10（聞き返しになる）
- 変えたファイル：`voice_teaching/utterance.py`、`voice_teaching/session.py`（1行）、`voice_teaching/web/flow.py`（facts に unknown_words・negations・undo_negations）、`tests/test_unknown_words.py`、`tests/test_checker022_acceptance.py`（期待1件）、`docs/UNKNOWN_WORDS_PLAN.md`。承認・出力・jbi・sim は変えていない
- 決まりの説明：`docs/UNKNOWN_WORDS_PLAN.md` 6章。内部レビューの記録：`reviews/REVIEW_B1_B6_002.md`

## こちらで流した結果（会社の PC、Windows、Python 3.13.1、PYTHONUTF8=1）
- `python -m pytest -q` → 1479 passed
- `python tools/e2e_browser.py` → OK 91・NG 0
- `node --test tests/speech.test.mjs` → 13/13
- `VT_REPO=<repo> python -I -X utf8 reviews/CHECKER_023_tools/b1_rereview.py` → **MUST 118/118・SHOULD 26/26**
- probe.py（cases.txt・cases_neg.txt・cases_b1.txt）、multi.py、safety.py、anom.py 54/0、items16.py 18/18、limit.py、chain.py → 退行なし

## 特に見てほしいこと
1. 打ち消しの見つけ方（`NEGATION_WORDS`・文の終わりの「な」「ず」・カタカナ）に抜けがないか。止めすぎ（「すみません」「そんなに」）は許容してよいか
2. 「使ってよい言葉」の決まり（`_run_is_allowed`）で、聞き違えた軸の言葉が隠れる形がないか
3. 知らない言葉の聞き返しで**選択肢を出さない**判断（CHECKER_023 の［下方向へ 3mm］案と違う）
4. 期待を変えたテスト1件（「ワイざひょう」：2択 → 選択肢なし）
5. 「3mm下げて？」が候補になること（唐川さんの判断待ち）

## 別のブランチ（このレビューの対象ではない。参考）
- `feat/ux-polish`（ec0a453）：画面の改善、Ver.1.00-dev、？ヘルプ、サンプルで試す、知らない言葉の色付け
- `feat/claude-ready`（ba46170）：本物の Claude API の準備（キーなし・未接続）
- `integrate/day-20261103`：上の3つをまとめて試すためのブランチ（安定ブランチではない）
