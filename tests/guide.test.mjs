// 11/5 UI の仕上げ：次にすること（nextStep）と個別ヘルプ（TIPS）の中身。
// 文はサーバーの決まった文（tests/test_guide_messages.py が、サーバーの文が今もこの形かを確かめる）
import { test } from "node:test";
import assert from "node:assert/strict";
import { nextStep, NEXT_STEPS, TIPS } from "../voice_teaching/web/static/highlight.js";

test("方向・量・位置・回数・範囲・無い位置：次の行動と、決まった例を返す", () => {
  assert.deepEqual(nextStep("どの方向（X・Y・Z、または上げる・下げる）に動かしますか？"),
    { say: "移動する方向を指定してください", ex: "Zプラス方向に10mm" });
  assert.equal(nextStep("何mm動かしますか？").ex, "Zプラス方向に10mm");
  assert.equal(nextStep("量がはっきりしません（「少し」（量がはっきりしません））。算用数字と mm（ミリ） で量を言ってください").say,
    "移動量を、数と mm で指定してください");
  assert.equal(nextStep("位置が決まっていないため、変更の提案をしていません。位置を選ぶか、位置番号（C と5桁の数字）で言ってください").ex, null);
  assert.deepEqual(nextStep("回数は整数で言ってください。小数の回数は扱いません"), { say: "回数を、整数で指定してください", ex: "3回にして" });
  assert.deepEqual(nextStep("区間（C00002〜C00003）の中に、区間の外の位置（C00001）への移動があるため、複写しません（24行目「MOVL C00001 V=150.0」）"),
    { say: "指定された範囲では変更できません。対象の位置を確認してください", ex: null });
  assert.equal(nextStep("位置 C00009 はこのジョブにありません").ex, null);
});

test("範囲が合わないときは、正しい範囲を推測して例に出さない（P1-C を壊さない）", () => {
  const n = nextStep("区間（C00002〜C00003）の中に、区間の外の位置（C00001）への移動があるため、複写しません");
  assert.equal(n.ex, null);
  assert.ok(!/C0000[0-9]/.test(n.say));
});

test("例をすでに含む文・できない操作の文・安全の理由の文には、足さない", () => {
  for (const m of ["「バイオ」が分かりませんでした。軸（X・Y・Z）と向きと量を、もう一度言ってください（例：Y座標を3ミリ下げて／下に3ミリ）",
    "前・後ろの向きは、このアプリでは扱いません",
    "区間の中に DOUT があるため、複写しません（26行目「DOUT OT#(1) ON」）",
    "移動量 100mm は仮の上限 20mm（確認待ち）を超えるため、変更しません",
    "Z方向のプラスですか、マイナスですか？（「Zのマイナス」のように向きも言ってください）",
    "", null, undefined]) {
    assert.equal(nextStep(m), null, String(m));
  }
});

test("次にすることの文：承認・出力・安全の言葉を含めない。例はアプリが今扱える形だけ", () => {
  const ok = new Set(["Zプラス方向に10mm", "3回にして", null]);
  for (const s of NEXT_STEPS) {
    assert.ok(!/承認|出力|転送|実行|安全|大丈夫|問題ありません/.test(s.say), s.say);
    assert.ok(ok.has(s.ex), String(s.ex));
  }
});

test("個別ヘルプ：短い説明（80字以内）と、できることの具体例。安全を言い切らない・声で承認できるとは書かない", () => {
  const keys = ["mic", "input", "shift", "count", "range", "position", "updown", "play", "approve", "status", "movl", "movj", "other"];  // 11/6：図の下の線の種類（凡例の ?）を足した
  assert.deepEqual(Object.keys(TIPS).sort(), [...keys].sort());
  for (const [k, t] of Object.entries(TIPS)) {
    assert.ok(t.head && t.body, k);
    assert.ok(t.body.length <= 80, `${k}：${t.body.length}字`);
    assert.ok(!/安全です|大丈夫|問題ありません|ぶつかりません|実機に送|転送して/.test(t.body), k);
  }
  assert.equal(TIPS.shift.ex, "Zプラス方向に10mm");
  assert.equal(TIPS.count.ex, "3回にして");
  assert.ok(TIPS.approve.body.includes("声では承認しません"));
});
