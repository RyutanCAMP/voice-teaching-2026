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
  ["#5d626b", "fig", "図の面の軸の文字"],
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
