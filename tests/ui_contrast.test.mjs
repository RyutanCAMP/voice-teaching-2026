// UI 修正 11/4（ダークの画面）：文字と地のコントラストが WCAG の 4.5:1 以上か（style.css の色を読んで計算する）。
// 色の意味（オレンジ＝変更後・承認へ進む、青＝範囲を決める、赤＝止まった、緑＝人が教えた、紫＝AI の推定）は変えずに、読める濃さにする
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../voice_teaching/web/static/style.css", import.meta.url), "utf-8");
const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
const vars = Object.fromEntries([...root.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
const color = (c) => (c.startsWith("#") ? c : vars[c]);

function lum(hex) {
  const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
function ratio(a, b) {
  const [x, y] = [lum(color(a)), lum(color(b))].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// [文字, 地, どこ]
const PAIRS = [
  ["text", "bg", "地の上の文字"], ["text", "card", "カードの文字"], ["text", "card2", "ボタン・入力欄の文字"],
  ["sub", "bg", "補足（地）"], ["sub", "card", "補足（カード）"], ["sub", "card2", "補足（ボタン）"],
  ["faint", "bg", "薄い文字（地）"], ["faint", "card", "薄い文字（カード）"],
  ["accent-ink", "accent", "オレンジのボタンの文字（次に押す・承認する）"],
  ["blue-ink", "blue", "青のボタンの文字（この範囲を繰り返す）"],
  ["accent-text", "card", "オレンジの文字（変更後の値）"], ["blue-text", "card", "青の文字（位置・リンク）"],
  ["red", "card", "赤の文字（止めた理由）"], ["red", "red-bg", "赤の帯の文字"],
  ["green", "card", "緑の文字（承認しました）"], ["purple", "card", "紫の文字（AI の推定）"], ["purple", "purple-bg", "紫の印"],
  ["warn-text", "warn-bg", "注意の帯"], ["#ffc2c4", "red-bg", "承認できない帯（確認表示）"],
  ["#b9d5ff", "#15294a", "変更案の確認のみの帯"], ["#a9cbff", "#15294a", "シミュレーションモードの印"],
  ["#ffffff", "#1f4f8f", "人の吹き出し"], ["#c9f2dc", "green-bg", "人が教えた（緑の吹き出し）"],
  // 唐川さん承認のデザイン 11/4：3D の面を暗い地にした（前回 QA P1-2）。軸の文字（格子 100mm）・操作のヒント・窓の見出しは CSS の色
  ["#93a4c3", "fig", "図の面の軸の文字（格子の大きさ）"], ["#8f9fbd", "fig", "3D の操作のヒント"], ["#9fb0cc", "#101c33", "3D の窓の見出し"],
  ["text", "chip", "状態のチップ"], ["accent-text", "chip", "状態のチップ（候補あり）"], ["green", "chip", "状態のチップ（済み）"],
  ["red", "chip", "状態のチップ（承認できない）"], ["blue-text", "chip", "状態のチップ（範囲の確認中・青）"], ["#a9cbff", "#15294a", "シミュレーションの印（3D の下）"],
];

test("style.css の色の変数が読める", () => {
  for (const k of ["bg", "card", "text", "sub", "accent", "accent-ink", "blue", "blue-ink", "fig"]) assert.ok(vars[k], k);
});

for (const [fg, bg, where] of PAIRS) {
  test(`コントラスト 4.5:1 以上：${where}`, () => {
    const r = ratio(fg, bg);
    assert.ok(r >= 4.5, `${where}：${color(fg)} / ${color(bg)} = ${r.toFixed(2)}`);
  });
}

test("決まっている色の意味：範囲を決めるボタンは青、承認へ進むボタンはオレンジ（同じ色にしない）", () => {
  assert.notEqual(vars.blue.toLowerCase(), vars.accent.toLowerCase());
  assert.match(css, /\.choice\.range-go:not\(\[disabled\]\) \{ background: var\(--blue\)/);
  assert.match(css, /button\.primary:not\(\[disabled\]\) \{\s*background: var\(--accent\)/);
  assert.match(css, /button\.approve:not\(\[disabled\]\) \{ background: var\(--accent\)/);
});

test("動きを減らす設定のときは、動きを出さない", () => {
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*animation: none !important/);
});

// ---- view3d.js の色（唐川さん承認：色の値だけを変えた）。暗い図の面（--fig）の上で、文字は 4.5:1 以上、線・点は 3:1 以上 ----
const v3 = readFileSync(new URL("../voice_teaching/web/static/view3d.js", import.meta.url), "utf-8");
const hl = readFileSync(new URL("../voice_teaching/web/static/highlight.js", import.meta.url), "utf-8");
const pick = (re, src = v3) => { const m = src.match(re); assert.ok(m, String(re)); return m.slice(1); };
const [AFTER, AFTER_TEXT] = pick(/const C_AFTER = "(#[0-9a-f]{6})", C_AFTER_TEXT = "(#[0-9a-f]{6})"/);
const [STOPPED] = pick(/const C_STOPPED = "(#[0-9a-f]{6})"/);
const [LABEL_SEL, LABEL] = pick(/fill: sel && !isMoved \? "(#[0-9a-f]{6})" : "(#[0-9a-f]{6})"/);
const [PT_MOVED, PT_SEL, PT] = pick(/fill: isMoved \? "(#[0-9a-f]{6})" : \(sel \? "(#[0-9a-f]{6})" : "(#[0-9a-f]{6})"\)/);
const [LINE_BEFORE, LINE] = pick(/seg\(s, d\.points, c \? "(#[0-9a-f]{6})" : "(#[0-9a-f]{6})", 2\.5, "before"\)/);
const [RANGE] = pick(/line\.setAttribute\("stroke", "(#[0-9a-f]{6})"\); line\.setAttribute\("stroke-width", "5"\)/);
const [CAND] = pick(/r: 19, fill: "[^"]+", stroke: "(#[0-9a-f]{6})"/);
const [AX_X, AX_Y, AX_Z] = pick(/\["X", \[sx \* step, 0, 0\], "(#[0-9a-f]{6})"\], \["Y", \[0, sy \* step, 0\], "(#[0-9a-f]{6})"\], \["Z", \[0, 0, step\], "(#[0-9a-f]{6})"\]/);
const [TAUGHT_BG, TAUGHT_TEXT] = [pick(/rx: 11, fill: "(#[0-9a-f]{6})"/)[0], pick(/"text-anchor": "middle", fill: "(#[0-9a-f]{6})", "font-weight": "700" \}\);\s*tx\.textContent/)[0]];
const [INSET_BG] = pick(/rx: 14, fill: "(#[0-9a-f]{6})"/);
const [WAS] = pick(/fill: "(#[0-9a-f]{6})", "data-fs": 18/);
const [NOW] = pick(/"text-anchor": "middle", fill: "(#[0-9a-f]{6})", "data-fs": 30/);
const [MOVJ] = pick(/movj: \{ kind: "movj", dash: "2 6", width: 0\.72, color: "(#[0-9a-f]{6})"/, hl);
// HC20 を模した静止モデル・台座の目印・正面の矢印（2026-11-05 夜 唐川さんの決定）
const [ROBOT] = pick(/const C_ROBOT = "(#[0-9a-f]{6})"/);
const [MODEL] = pick(/const C_MODEL = "(#[0-9a-f]{6})"/);
const [MODEL_TEXT] = pick(/const C_MODEL_TEXT = "(#[0-9a-f]{6})"/);
const V3_TEXT = [  // [文字, 地, どこ]
  [LABEL, "fig", "3D の点の名前"], [LABEL_SEL, "fig", "3D の選んだ点の名前"], [AFTER_TEXT, "fig", "3D の動かす点の名前（変更量つき）"],
  [STOPPED, "fig", "3D の止まった候補の名前"], [AX_X, "fig", "3D の軸の文字 X"], [AX_Y, "fig", "3D の軸の文字 Y"], [AX_Z, "fig", "3D の軸の文字 Z"],
  [TAUGHT_TEXT, TAUGHT_BG, "3D の人が教えた印"], [AFTER_TEXT, INSET_BG, "3D の回数の窓（×3）・変更の窓の量"], [WAS, INSET_BG, "3D の回数の窓（×2 →）"],
  [NOW, INSET_BG, "3D の回数の窓（読み込んだ直後の ×2）"],
  [ROBOT, "fig", "3D の台座と正面の札"], [MODEL_TEXT, "fig", "3D のモデルの札（HC20 を模した簡易モデル…）"],
];
const V3_LINE = [  // [線・点, 地, どこ]
  [LINE, "fig", "3D の線（MOVL）"], [LINE_BEFORE, "fig", "3D の変更前の線"], [AFTER, "fig", "3D の変更後の線"], [STOPPED, "fig", "3D の止まった候補の点線"],
  [MOVJ, "fig", "3D の MOVJ の点線"], [RANGE, "fig", "3D の範囲の線"], [CAND, "fig", "3D の位置の候補の輪"],
  [PT, "fig", "3D の点"], [PT_SEL, "fig", "3D の選んだ点"], [PT_MOVED, "fig", "3D の動かす点（変更前）"],
  [ROBOT, "fig", "3D の台座の印・正面の矢印"], [MODEL, "fig", "3D のモデルの細い線"],
];
test("view3d.js の色の意味：台座・正面の色は、変更後のオレンジ・軸の X・Y・Z の色と違う", () => {
  for (const c of [AFTER, AFTER_TEXT, AX_X, AX_Y, AX_Z, STOPPED]) assert.notEqual(ROBOT.toLowerCase(), c.toLowerCase());
});
for (const [fg, bg, where] of V3_TEXT) {
  test(`view3d.js の文字 4.5:1 以上：${where}`, () => {
    const r = ratio(fg, bg);
    assert.ok(r >= 4.5, `${where}：${color(fg)} / ${color(bg)} = ${r.toFixed(2)}`);
  });
}
for (const [fg, bg, where] of V3_LINE) {
  test(`view3d.js の線・点 3:1 以上：${where}`, () => {
    const r = ratio(fg, bg);
    assert.ok(r >= 3, `${where}：${color(fg)} / ${color(bg)} = ${r.toFixed(2)}`);
  });
}
test("view3d.js の色の意味：変更後はオレンジ（承認へ進むボタンと同じ系統）、止まった候補はオレンジにしない", () => {
  assert.equal(AFTER.toLowerCase(), "#ff6a1a");
  assert.notEqual(STOPPED.toLowerCase(), AFTER.toLowerCase());
  assert.notEqual(STOPPED.toLowerCase(), AFTER_TEXT.toLowerCase());
});
