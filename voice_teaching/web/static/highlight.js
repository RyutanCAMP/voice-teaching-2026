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

// 「Claude に聞く」の答えで光らせる区間（CHECKER_031 E4）。光らせる行はサーバーが行番号から決める（ここでは選ばない）。
// 見た目は区間の種類のまま：MOVJ・目安の区間は、光らせても点線・破線のまま（直線の通り道に見せない）。太さも種類の割合のまま
export const ASK_HIGHLIGHT_COLOR = "#ff9f0a";
export function askHighlightLook(kind) {
  const look = segmentLook(kind);
  return { kind: look.kind, color: ASK_HIGHLIGHT_COLOR, dash: look.dash, width: 5 * look.width, opacity: look.kind === "movl" ? 0.85 : 0.8 };  // 変える対象の区間は、はっきり（唐川さん 11/3 夜）
}

// 前後の移動（サーバーの highlight.context_lines）：強調の区間と見分けられるよう、控えめな色・細さ・薄さで出す
export const ASK_CONTEXT_COLOR = "#8fa3bf";
export function askContextLook(kind) {
  const look = segmentLook(kind);
  return { kind: look.kind, color: ASK_CONTEXT_COLOR, dash: look.dash, width: 3 * look.width, opacity: 0.35 };
}

// 光らせる行（サーバーの highlight.lines）に入る区間か。行番号が数でない・無いときは光らせない
export function isAskHighlighted(seg, lines) {
  return Array.isArray(lines) && !!seg && Number.isInteger(seg.line_no) && lines.includes(seg.line_no);
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
  if (c.kind === "repeat") {
    // 回数の変更（docs/REPEAT_COUNT_DESIGN.md）：「C00001〜C00003 の作業を 2回 → 3回 にします」。数はサーバーの文字のまま
    if (!c.target || c.before_value == null || c.after_value == null) return "";
    return `${c.target} の作業を ${c.before_value}回 → ${c.after_value}回 にします`;
  }
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

// ---- 回数の変更の印（docs/REPEAT_COUNT_DESIGN.md 5章）。数はサーバーの文字のまま ----
export function repeatCountLabel(c) {
  if (!c || typeof c !== "object" || c.before_value == null || c.after_value == null) return "";
  return `×${c.before_value} → ×${c.after_value}`;
}

// 再生中の区間の線が、何回目の区間か（サーバーの seg.cycle。区間の外・分からないときは ""）
export function cycleStatus(seg, target) {
  const n = seg && seg.cycle;
  if (!Number.isInteger(n) || n < 1) return "";
  return `${n}回目（${target}）`;
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

// 3D の上にいつも見せる、上下の向きの短い状態（Issue #8 round3・11/6 唐川さん）。ロボット名・確認した人・日時・理由は
// updownDetail と u.message を詳しい説明（小さな説明・押したときの小窓）に出す。状態の出どころは state.updown のまま。
// 確認済みと言うのは status が confirmed のときだけ（分からない状態は未確認）
export function updownShort(u) {
  if (!u || typeof u !== "object" || Array.isArray(u)) return "";
  if (u.status === "confirmed") {
    // round4（11/6 唐川さんの決定）：確認済みは対象のロボット名も出す（updownDetail と同じ：設定の robot、無ければアプリの --robot の値）
    const robot = [u.robot, u.app_robot].find((x) => typeof x === "string" && x.length > 0);
    return robot ? `上下方向 確認済み（${robot}）` : "上下方向 確認済み";
  }
  if (u.status === "mismatch") return "上下方向 設定と違います";
  if (u.status === "changed") return "上下方向 設定が変わっています";
  return "上下方向 未確認";
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

// ---- 再生の「いま」の表示（feat/demo-3d）：「いま：24行目 MOVL C00001 V=150.0（2回目）」 ----
// サーバーの区間の線（line_no・text・kind・cycle）の文字のまま。数は計算しない。時間・関節の通り道は出さない。
// opts.run：「変更前」「変更後」など。opts.count：その再生の回数（×2 など。回数の変更のときだけ）。
// opts.added：足した回の番号（変更後の再生の 3 など）。その回の線だけ「（足した分）」と出す
export const NOW_MOVJ_NOTE = "関節移動：通り道は表示していません";
export const NOW_OTHER_NOTE = "直線とは限らない区間：印は終点へ移ります";
export function nowPlaying(seg, opts = {}) {
  if (!seg || typeof seg !== "object" || !Number.isInteger(seg.line_no) || seg.line_no < 1
    || typeof seg.text !== "string") return null;
  const o = opts || {};
  const n = seg.cycle;
  const ok = Number.isInteger(n) && n >= 1;
  const added = ok && Number.isInteger(o.added) && n === o.added;
  const kind = segmentLook(seg.kind).kind;
  return {
    run: typeof o.run === "string" ? o.run : "",
    count: o.count != null && o.count !== "" ? `×${o.count}` : "",
    line: `いま：${seg.line_no}行目 ${seg.text}`,
    cycle: ok ? `${n}回目${added ? "（足した分）" : ""}` : "",
    added,
    note: kind === "movj" ? NOW_MOVJ_NOTE : kind === "other" ? NOW_OTHER_NOTE : "",
  };
}

// 読み上げ・title 用の1行（画面は nowPlaying の部品を分けて大きさを変えて出す）
export function nowText(n) {
  if (!n) return "";
  const head = [n.run, n.count].filter((x) => x).join(" ");
  const cyc = n.cycle ? (n.cycle.endsWith("）") ? `　${n.cycle}` : `（${n.cycle}）`) : "";
  return [head, n.line + cyc, n.note].filter((x) => x).join("　");
}

// ---- 動きの並びのカード（台本 Ver.6.1 Scene 3-3）：［C00000（区間の前）］→［名前］①②③ 追加 →［C00004（区間の後）］ ----
// サーバーの candidate.sequence（変更前後のジョブと区間から作ったもの）のまま。位置の意味（待機など）は決めつけない。
// seq.name は区間の名前（今は C00001〜C00003。人が教えた意味の名前を、あとで差し込める）
function circled(n) {
  return n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : `(${n})`;
}
function sideChip(kind, m, word) {
  if (!m || typeof m !== "object" || typeof m.position !== "string") return null;
  return { kind, text: `${m.position}（${word}）`, title: Number.isInteger(m.line_no) ? `${m.line_no}行目 ${m.text ?? ""}` : "" };
}
export function sequenceChips(seq, side) {
  if (!seq || typeof seq !== "object") return [];
  const s = seq[side];
  if (!s || typeof s !== "object" || !Array.isArray(s.cycles) || !s.cycles.every((n) => Number.isInteger(n) && n >= 1)) return [];
  const added = Array.isArray(seq.added) ? seq.added : [];
  const out = [];
  const entry = sideChip("entry", s.entry, "区間の前");
  if (entry) out.push(entry, { kind: "arrow", text: "→" });
  out.push({ kind: "name", text: String(seq.name || seq.target || "") });
  for (const n of s.cycles) {
    const a = added.includes(n);
    out.push({ kind: "cycle", n, text: circled(n) + (a ? " 追加" : ""), added: a });
  }
  const exit = sideChip("exit", s.exit, "区間の後");
  if (exit) out.push({ kind: "arrow", text: "→" }, exit);
  return out;
}

// 比較の再生で、区間を通った回を大きく数える（変更前「1・2」、変更後「1・2・3」）。足した回の番号は印を付ける
export function tallyParts(max, added) {
  if (!Number.isInteger(max) || max < 1) return [];
  const a = Array.isArray(added) ? added : [];
  return Array.from({ length: max }, (_, k) => ({ text: String(k + 1), added: a.includes(k + 1) }));
}

// ---- 言い換えの例（唐川さん承認のデザイン 11/4）：サーバーの返事の文の中の例を、押すと入力欄に入るチップにする ----
// 出すのは、サーバーの決まった文にある例のうち、下の白リストにあるものだけ（軸と符号と量がそろった、小さな量の例）。
// 引用の中に別の量・別の向き・上下の言葉（上下の向きが確認済みのときしか使えない）が入っていても、チップにしない。
// チップは入力欄に入れるだけで、送るのは人（［送る］）。サーバーの文は変えない
export const REPHRASE_OK = ["Xのマイナス5ミリ", "Zプラス3ミリ", "Zマイナス3ミリ", "Yのプラス3ミリ"];
export function rephraseExamples(message, heard) {
  const text = typeof message === "string" ? message : "";
  const found = [];
  for (const m of text.matchAll(/「([^「」]{1,40})」/g)) found.push(m[1].trim());
  for (const m of text.matchAll(/（例：([^）]{1,80})）/g)) found.push(...m[1].split("／").map((s) => s.trim()));
  const out = [];
  for (const t of found) {
    if (REPHRASE_OK.includes(t) && t !== heard && !out.includes(t)) out.push(t);
  }
  return out.slice(0, 3);
}

// 11/5 UI の仕上げ（唐川さんの方針）：見せ方だけ。サーバーの検査・入力の読み方・承認は変えない。
// 1. 個別ヘルプ（TIPS・attachTips）：操作の横の小さな「？」。マウスを合わせる・押す・キーボード（Tab で合わせる・Enter・Esc）で開く。
//    説明は短く、具体例を中心に。できない操作をできるように書かない
// 2. 次にすること（nextStep）：サーバーの返事の文（今の検査の結果・決まった文）に合わせて、次の行動を1行で添える。
//    サーバーの理由はそのまま出す（隠さない）。例は決まった文だけ（人の言葉から正しい指示を推測しない・押しても欄に入れない）

export const TIPS = {
  mic: { head: "声で指示する", body: "押して1回話すと、自動で OFF になります。話した文は欄に入るので、確かめて［送る］。", ex: "Zプラス方向に10mm" },
  input: { head: "文字で指示する", body: "声と同じ欄です。位置を選んでから、軸・向き・量を書きます。", ex: "Zマイナス3ミリ" },
  shift: { head: "移動量", body: "軸（X・Y・Z）と向き（プラス・マイナス）と量（mm）を一緒に言います。上限を超える量は変更しません。", ex: "Zプラス方向に10mm" },
  count: { head: "回数", body: "同じ動作を繰り返す回数を指定します", ex: "3回にして", small: true },
  range: { head: "変更範囲", body: "どの位置からどの位置まで変更するか確認します。違うときは［違う］を押します。", ex: null },
  position: { head: "位置を選ぶ", body: "右の図の点か、番号を押します。薄い番号の位置は変更できません。", ex: "C00005 を押してから「Zプラス方向に10mm」" },
  updown: { head: "上下の向き", body: "「上げる・下げる」が Z のどちら向きかを、設定で確かめたかどうかです。未確認のときは Zプラス・Zマイナスで言います。", ex: "Zマイナス方向に3mm" },
  play: { head: "3D の再生", body: "▶ で命令の順に動きをたどります。候補があるときは［変更前を再生］［変更後を再生］で比べます。ドラッグで回す・ホイールで拡大。", ex: null },
  approve: { head: "承認", body: "［確認して承認へ］を押し、名前を入れて［承認する］。声では承認しません。", ex: null },
  status: { head: "アプリの記録", body: "アプリが記録している今の状態です（候補・承認・出力）。会話の答えでは変わりません。", ex: null },
  // 図の下の線の種類（11/6：凡例は短いラベルだけ。違いはここに出す）
  movl: { head: "MOVL", body: "直線で移動（実線）。", ex: null, small: true },
  movj: { head: "MOVJ", body: "関節を使って移動（細い点線）。点線は順番だけで、実際の通り道ではありません。", ex: null, small: true },
  other: { head: "目安の線", body: "破線は、直線とは限りません（目安）。", ex: null, small: true },
};

// サーバーの返事の文（決まった文）→ 次にすること。上から順に、最初に合ったものだけ。合わなければ出さない
// （例をすでに含む文・できない操作の文には足さない）
export const NEXT_STEPS = [
  { re: /^どの方向/, say: "移動する方向を指定してください", ex: "Zプラス方向に10mm" },
  { re: /^何mm動かしますか|^量がはっきりしません/, say: "移動量を、数と mm で指定してください", ex: "Zプラス方向に10mm" },
  { re: /^位置が決まっていないため/, say: "先に、右の図の点か下の番号で位置を選んでください", ex: null },
  { re: /^回数は(?:1以上の)?整数で言ってください/, say: "回数を、整数で指定してください", ex: "3回にして" },
  { re: /^区間（[^）]+）の中に、区間の外の位置/, say: "指定された範囲では変更できません。対象の位置を確認してください", ex: null },
  { re: /^位置 C[0-9]{5} はこのジョブにありません/, say: "対象の位置を確認してください。位置の番号は右の図と下の番号で確かめられます", ex: null },
];

export function nextStep(message) {
  const text = typeof message === "string" ? message.trim() : "";
  if (!text) return null;
  const hit = NEXT_STEPS.find((s) => s.re.test(text));
  return hit ? { say: hit.say, ex: hit.ex } : null;
}

// ---- 個別ヘルプの小窓（1つを使い回す）----
// 目印：<button type="button" class="tip" data-tip="mic">?</button>。開く：マウスを合わせる・押す・Tab で合わせる。
// 閉じる：マウスを外す（押して開いたときは、もう一度押す・×・Esc・外を押す）・Tab で外す
export function attachTips(doc = document) {
  // 図の注意（#play-note・#view3d-note）は畳まない（安全の表示。ミニマル確定案「目につかないヘルプ内へ移してはいけない」）
  const pop = doc.createElement("div");
  pop.id = "tip-pop";
  pop.className = "tip-pop";
  pop.setAttribute("role", "tooltip");
  pop.hidden = true;
  doc.body.append(pop);
  let owner = null, pinned = false, returning = false;  // returning：閉じて「？」へ戻すフォーカスでは、開き直さない

  const fill = (key) => {
    const t = TIPS[key];
    const head = doc.createElement("p"); head.className = "tip-head"; head.textContent = t.head;
    const body = doc.createElement("p"); body.className = "tip-body"; body.textContent = t.body;
    const parts = [head, body];
    if (t.ex) { const ex = doc.createElement("p"); ex.className = "tip-ex"; ex.textContent = `例：「${t.ex}」`; parts.push(ex); }
    const x = doc.createElement("button");
    x.type = "button"; x.className = "tip-close"; x.textContent = "×"; x.setAttribute("aria-label", "閉じる");
    x.addEventListener("click", () => close(true));
    pop.replaceChildren(x, ...parts);
    pop.classList.toggle("tip-small", !!t.small);  // 11/6：回数・線の種類は、? の近くに小さく出す
  };
  const place = (btn) => {
    const r = btn.getBoundingClientRect(), vw = doc.documentElement.clientWidth, vh = doc.documentElement.clientHeight;
    pop.style.left = "0px"; pop.style.top = "0px";
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, vw - w - 8));
    const below = r.bottom + 8 + h <= vh - 8;
    let top = below ? r.bottom + 8 : r.top - 8 - h;
    // 操作の大きいボタン（［確認して承認へ］など）は隠さない：重なるときは、開く向きのまま、そのボタンの外まで動かす
    const keep = [...doc.querySelectorAll("button.wide, button.approve")].filter((e) => e.getClientRects().length).map((e) => e.getBoundingClientRect());
    for (let i = 0; i < keep.length; i++) {
      const hit = keep.filter((k) => left < k.right && left + w > k.left && top < k.bottom && top + h > k.top);
      if (!hit.length) break;
      top = below ? Math.max(...hit.map((k) => k.bottom)) + 8 : Math.min(...hit.map((k) => k.top)) - 8 - h;
    }
    if (top < 8 || top + h > vh - 8) top = below ? r.bottom + 8 : Math.max(8, r.top - 8 - h);  // 画面に入らないときは今までの位置
    pop.style.left = `${Math.round(left)}px`; pop.style.top = `${Math.round(top)}px`;
  };
  const open = (btn, pin) => {
    if (owner && owner !== btn) owner.setAttribute("aria-expanded", "false");
    owner = btn; pinned = pin;
    fill(btn.dataset.tip);
    pop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    btn.setAttribute("aria-describedby", "tip-pop");
    place(btn);
  };
  const close = (focusBack = false) => {
    if (!owner) return;
    const b = owner;
    pop.hidden = true; owner.setAttribute("aria-expanded", "false"); owner.removeAttribute("aria-describedby");
    owner = null; pinned = false;
    if (focusBack) { returning = true; b.focus(); returning = false; }
  };

  // あとから足した「？」（範囲の確認の欄など）にも効くよう、文書全体で受ける（委譲）
  const tipOf = (t) => {
    const b = t && t.closest ? t.closest("button.tip[data-tip]") : null;
    if (b && TIPS[b.dataset.tip] && !b.hasAttribute("aria-expanded")) {
      b.setAttribute("aria-expanded", "false");
      if (!b.getAttribute("aria-label")) b.setAttribute("aria-label", `${TIPS[b.dataset.tip].head}の説明`);
    }
    return b && TIPS[b.dataset.tip] ? b : null;
  };
  for (const b of doc.querySelectorAll("button.tip[data-tip]")) tipOf(b);
  doc.addEventListener("mouseover", (ev) => { const b = tipOf(ev.target); if (b && !pinned && owner !== b) open(b, false); });
  doc.addEventListener("mouseout", (ev) => {
    const b = tipOf(ev.target);
    if (b && !pinned && owner === b && !b.contains(ev.relatedTarget)) close();
  });
  doc.addEventListener("focusin", (ev) => { const b = tipOf(ev.target); if (b && !pinned && !returning) open(b, false); });
  doc.addEventListener("focusout", (ev) => {
    const b = tipOf(ev.target);
    if (b && !pinned && owner === b && !pop.contains(ev.relatedTarget)) close();
  });
  doc.addEventListener("click", (ev) => {
    const b = tipOf(ev.target);
    if (b) {
      ev.preventDefault();
      if (owner === b && pinned) close(); else open(b, true);  // 押す・タップ：開いたまま（もう一度・×・Esc・外を押すと閉じる）
      return;
    }
    if (owner && pinned && !pop.contains(ev.target)) close();
  }, true);
  doc.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && owner) close(true); });
  doc.addEventListener("scroll", () => { if (owner) place(owner); }, true);  // 欄が動いても、目印のそばに付いていく
  doc.defaultView.addEventListener("resize", () => { if (owner) place(owner); });
  return { open, close, pop };
}
