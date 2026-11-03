// 画面の表示だけに使う小さな関数（DOM を使わない。node でも試せる）。
// - splitUnknown：聞き取った文を、プログラムが読めなかった言葉（facts.unknown_words）とそれ以外に分ける。
//   目立たせるのは表示だけ。文を変えない・読み方に使わない。画面は分けた文字をテキストのノードとして入れる。
// - displayMinus：表示のときだけ、ASCII の "-" を数学のマイナス "−"（U+2212）に置き換える（数は計算しない）。
// - segmentLook・segmentTitle：図の区間の見た目と、マウスを重ねたときの文（CHECKER_026 S-MOVJ）。
// - trimDecimal・confirmHeading：確認表示の見出し（CHECKER_026 S5）。数はサーバーの候補の文字のまま（末尾の0を削るだけ）。
// - updownStatus：上下の向きの状態（サーバーの state.updown）。無いときは何も出さない。
// - updownDetail：上下の向きの、ロボット名と確認した人・日時の1行（CHECKER_030 S30-1）。
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

// ---- 図の区間の見た目（CHECKER_026 S-MOVJ） ----
// サーバーの区間の種類（kind）だけで決める。知らない種類・無いときは "other"（直線とは見せない側）
// - movl：直線移動。実線。再生の印は線の上を動く
// - movj：関節移動。実際の通り道はジョブの点だけでは分からない。細い灰色の点線で、再生の印は線の上を動かさない（終点へ移るだけ）
// - other：MOVC・PL>0 など。直線とは限らないので破線の「目安」。再生の印は線の上を動かさない
export const MOVJ_LABEL = "関節移動（MOVJ）：実際の通り道は直線ではありません";
const LOOKS = {
  movl: { kind: "movl", dash: null, width: 1, color: null, animate: true, label: "MOVL：直線移動" },
  movj: { kind: "movj", dash: "2 6", width: 0.72, color: "#8e8e96", animate: false, label: MOVJ_LABEL },
  other: { kind: "other", dash: "6 5", width: 1, color: null, animate: false, label: "直線とは限りません（目安）" },
};

export function segmentLook(kind) {
  const k = kind === "movl" || kind === "movj" ? kind : "other";
  return { ...LOOKS[k] };
}

export function segmentTitle(seg) {
  const s = seg || {};
  const head = `${s.line_no}行目 ${s.text ?? ""}`;
  const look = segmentLook(s.kind);
  if (look.kind === "movl") return head;
  if (look.kind === "movj") return `${head}\n${look.label}`;
  return `${head}（目安）`;
}

// ---- 確認表示の見出し（CHECKER_026 S5）：「C00005 を Y −3mm 動かします」の1行 ----
// 数は計算しない。サーバーの文字（例 "-3.000"）の、小数点より後ろの末尾の 0 を削るだけ
export function trimDecimal(s) {
  const t = String(s ?? "");
  if (!/^[+-]?\d+\.\d+$/.test(t)) return t;
  return t.replace(/0+$/, "").replace(/\.$/, "");
}

export function confirmHeading(c, dir = "") {
  if (!c || typeof c !== "object") return "";
  if (c.kind === "speed") {
    if (!c.target || c.before_value == null || c.after_value == null) return "";
    return `${c.target}の速度を V=${c.before_value} から V=${c.after_value} に変えます`;
  }
  const [pos, axis] = String(c.target ?? "").split(" ");
  if (!pos || !axis || c.delta == null || c.delta === "") return "";
  const amount = `${pos} を ${axis} ${displayMinus(trimDecimal(c.delta))}mm${dir || ""}`;
  // 止まった候補は「動かします」と言わない（承認も出力もできない）
  const sp = dir ? "" : " ";  // 「（上へ）」のあとは空けない
  // 止まった候補の見出しも「参考の表示だけ・変更しません」から始める（CHECKER_032 P1-2・内部 Safety P3）
  return c.blocked_reason ? `参考の表示だけ・変更しません：${amount}${sp}動かした場合（承認できません）` : `${amount}${sp}動かします`;
}

// ---- 上下の向きの状態（サーバーの state.updown）。無いときは null（何も出さない） ----
// 知らない status は「未確認」として出す（確認済みとは出さない）
function shortTime(s) {
  const m = /^\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(s));
  return m ? `${Number(m[1])}/${Number(m[2])} ${m[3]}:${m[4]}` : String(s);
}

export function updownStatus(u) {
  if (!u || typeof u !== "object" || Array.isArray(u)) return null;
  const title = typeof u.message === "string" ? u.message : "";
  if (u.status === "confirmed") {
    const who = [u.confirmed_by, u.confirmed_at ? shortTime(u.confirmed_at) : ""]
      .filter((x) => typeof x === "string" && x.length > 0);
    // 画面は短く（音声のカードの文字数の決まり）。確認した人・日時は、マウスを重ねたときの説明に入れる
    const by = who.length ? `確認：${who.join("・")}` : "";
    return { text: "上下の向き：確認済み", tone: "confirmed", title: [by, title].filter((x) => x).join("。") };
  }
  if (u.status === "mismatch") return { text: "上下の向き：確認した座標系・ツールと違います", tone: "warn", title };
  if (u.status === "changed") return { text: "上下の向き：設定が変わっています", tone: "warn", title };
  return { text: "上下の向き：未確認（Z＋・Z−で指示）", tone: "unconfirmed", title: u.status === "unconfirmed" ? title : "" };
}

// 上下の向きの、ロボット名と確認した人・日時の1行（CHECKER_030 S30-1。唐川さんの決定 2026-11-03 17:14）。
// マウスを重ねなくても常に見せる（図の欄の上。音声のカードの文字数の決まりの外）。サーバーが返さないときは ""。
// 確認済み：設定の robot（無ければアプリの --robot の値）と、確認した人・日時。ほか：アプリの --robot の値だけ（確認した人は出さない）
export function updownDetail(u) {
  if (!u || typeof u !== "object" || Array.isArray(u)) return "";
  const str = (x) => (typeof x === "string" && x.length > 0 ? x : "");
  const app = str(u.app_robot);
  if (u.status === "confirmed") {
    const names = [str(u.robot) || app, str(u.confirmed_by)].filter((x) => x).join("・");
    const at = str(u.confirmed_at) ? `（${shortTime(u.confirmed_at)}）` : "";
    return names ? `上下の向き：${names}${at}で確認済み` : `上下の向き：確認済み${at}`;
  }
  const tail = app ? `（${app}）` : "";
  if (u.status === "mismatch") return `上下の向き：確認した設定と違います${tail}`;
  if (u.status === "changed") return `上下の向き：設定が変わっています${tail}`;
  return `上下の向き：未確認${tail}`;
}

// 確認表示に出す、上下の向きを確認した人と日時の1行（確認済みのときだけ。ほかは ""）。u は候補を作ったときの照合の結果
export function updownConfirmLine(u) {
  if (!u || typeof u !== "object" || Array.isArray(u) || u.status !== "confirmed") return "";
  const by = typeof u.confirmed_by === "string" && u.confirmed_by ? `${u.confirmed_by}さんが確認` : "確認済み";
  const at = typeof u.confirmed_at === "string" && u.confirmed_at ? `（${shortTime(u.confirmed_at)}）` : "";
  return `上下の向き：${by}${at}`;
}

// 話し方の例（入力欄の例文・使い方・？の小窓）。上下の向きが確認済みのときだけ「上に」「下に」を例に出す。
// それ以外（未確認・不一致・変更済み・状態が無い）は、そのまま送れば候補になる軸と符号の例（CHECKER_026 M2、QA P1-2）
export function speechExamples(u) {
  const ok = !!u && typeof u === "object" && !Array.isArray(u) && u.status === "confirmed";
  if (ok) {
    return {
      placeholder: "この位置を上に10mm",
      howto: "話し方の例：「この位置を上に10mm」「この位置を下に5mm」「C00005をZプラス方向に10mm移動して」「C00004を選択して」「比較して」「元に戻して」",
      help: ["「Y座標を3ミリ下げて」", "「Yのマイナス3ミリ」", "「この位置を上に10ミリ」"],
    };
  }
  return {
    placeholder: "Zプラス方向に10mm",
    howto: "話し方の例：「この位置をZプラス方向に10mm」「Zのマイナス5ミリ」「C00005をZプラス方向に10mm移動して」「C00004を選択して」「比較して」「元に戻して」",
    help: ["「Y座標を3ミリ下げて」", "「Yのマイナス3ミリ」", "「Zプラス方向に10ミリ」"],
  };
}
