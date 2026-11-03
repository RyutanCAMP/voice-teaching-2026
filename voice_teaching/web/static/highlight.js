// 画面の表示だけに使う小さな関数（DOM を使わない。node でも試せる）。
// - splitUnknown：聞き取った文を、プログラムが読めなかった言葉（facts.unknown_words）とそれ以外に分ける。
//   目立たせるのは表示だけ。文を変えない・読み方に使わない。画面は分けた文字をテキストのノードとして入れる。
// - displayMinus：表示のときだけ、ASCII の "-" を数学のマイナス "−"（U+2212）に置き換える（数は計算しない）。
"use strict";

export function splitUnknown(text, words) {
  const s = String(text ?? "");
  if (!s) return [];
  const list = Array.isArray(words)
    ? [...new Set(words.filter((w) => typeof w === "string" && w.length > 0))].sort((a, b) => b.length - a.length)
    : [];
  const out = [];
  const push = (t, unknown) => {
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.unknown === unknown && !unknown) last.text += t; else out.push({ text: t, unknown });
  };
  let i = 0, plain = "";
  while (i < s.length) {
    const w = list.find((x) => s.startsWith(x, i));  // 長い言葉を先に見る
    if (w) {
      push(plain, false); plain = "";
      push(w, true);
      i += w.length;
    } else {
      plain += s[i];
      i += 1;
    }
  }
  push(plain, false);
  return out;
}

export function displayMinus(s) {
  return String(s ?? "").replace(/-/g, "−");
}
