// 言い換えの例のチップ（唐川さん承認のデザイン 11/4・Safety P3-4）：白リストにある例だけをチップにする。
// サーバーの文の引用に別の量・別の向き・上下の言葉が入っていても、チップにしない。押しても入力欄に入るだけ（ここでは送らない）
import { test } from "node:test";
import assert from "node:assert/strict";
import { rephraseExamples, REPHRASE_OK } from "../voice_teaching/web/static/highlight.js";

test("サーバーの決まった文の例（「」と（例：…））から、白リストの例だけを取る", () => {
  assert.deepEqual(rephraseExamples("プラス・マイナスがどこに付くか分かりません。「Xのマイナス5ミリ」のように、軸のすぐ後に言ってください"),
    ["Xのマイナス5ミリ"]);
  assert.deepEqual(rephraseExamples("右に、「右」は使えません。「Zプラス3ミリ」「Zマイナス3ミリ」のように、軸と向きで指示してください"),
    ["Zプラス3ミリ", "Zマイナス3ミリ"]);
  assert.deepEqual(rephraseExamples("軸と向きで指示してください（例：Zマイナス3ミリ／Yのプラス3ミリ）"), ["Zマイナス3ミリ", "Yのプラス3ミリ"]);
});

test("白リストにない例はチップにしない（別の量・上下の言葉・回数・数の無い例・上限の文）", () => {
  for (const m of ["「Zプラス100ミリ」のように", "「Zマイナス30ミリ」のように", "（例：下に3ミリ）", "「上に10mm」",
    "「C00001からC00003の作業を2回から3回にして」のように", "「Z座標を…上げて／下げて」", "「1回の変更上限 20mm」", "「Xのマイナス」のように",
    "「Xのマイナス5ミリです」", "「 Xのマイナス50ミリ」"]) {
    assert.deepEqual(rephraseExamples(m), [], m);
  }
});

test("聞き取った文と同じ例は出さない。同じ例は1回だけ。形が違うものは何も出さない", () => {
  assert.deepEqual(rephraseExamples("「Zマイナス3ミリ」「Zマイナス3ミリ」", "Zマイナス3ミリ"), []);
  assert.deepEqual(rephraseExamples("「Zマイナス3ミリ」「Zマイナス3ミリ」"), ["Zマイナス3ミリ"]);
  for (const bad of [null, undefined, 3, {}, []]) assert.deepEqual(rephraseExamples(bad), []);
});

test("白リストの例は、どれも軸と符号と量（20mm 以下）がそろっている", () => {
  for (const t of REPHRASE_OK) {
    assert.match(t, /^[XYZ](の)?(プラス|マイナス)\d+ミリ$/, t);
    assert.ok(Number(t.match(/\d+/)[0]) <= 20, t);
  }
});
