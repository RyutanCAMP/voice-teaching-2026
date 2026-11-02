// ボイスティーチング（シミュレーションモード）の画面。
// - JBI の解析・変更候補・承認・出力はサーバー側（Python）で行う。ここでは JBI を解析しない。
// - 指示は文字入力の欄から /api/hear へ送る。音声（speech.js）は認識した文を入力欄に入れるだけで、自動では送らない。
// - 録音中は、取消と終了のほかの操作（読込・位置選択・指示を送る・承認・出力）を使えなくする。
// - 承認は確認表示の承認ボタンからだけ /api/approve を呼ぶ。指示の経路からは呼ばない。
// - 出力ボタンは承認した後だけ使える。失敗を成功と表示しない。
// - 外部から何も読み込まない。サーバー（同じ 127.0.0.1）にだけ要求を送る。
"use strict";
import { SpeechInput } from "/static/speech.js";

const state = {
  sessionId: null,  // サーバーのメモリにある画面の状態の番号
  view: null,       // 読み込んだときのジョブの内容（/api/load の view）
  st: null,         // サーバーの画面の状態（state）
  voiceState: "idle",   // 録音の状態（starting / listening / stopping / idle）
  voiceToken: null,     // 録音を始めたときのジョブ（session）と位置。違えば認識文を使わない
  voiceFinal: null,     // 最後に入力欄へ入れた認識文（直さずに送れば source=voice として記録）
};

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    e.append(c instanceof Node ? c : String(c));
  }
  return e;
}

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
  return e;
}

async function postJson(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch (e) {
    return { ok: false, error: "サーバーに届きませんでした（python -m voice_teaching.web が動いているか確かめてください）" };
  }
}

// ---- 1. 読み込み ----
$("file-input").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f || recording()) return;
  voice.cancel();
  state.voiceFinal = null;
  $("text-input").value = "";
  $("file-name").textContent = f.name;
  const bytes = new Uint8Array(await f.arrayBuffer());
  let res;
  try {
    const r = await fetch("/api/load", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    res = await r.json();
  } catch (e) {
    res = { ok: false, error: "サーバーに届きませんでした（python -m voice_teaching.web が動いているか確かめてください）" };
  }
  state.sessionId = null; state.view = null; state.st = null;
  closeConfirm();
  if (!res.ok) {
    $("load-error").textContent = "読み込めません：" + (res.error || "理由が分かりません");
    $("load-error").hidden = false;
    $("job-area").hidden = true;
    $("problems-panel").hidden = true;
    setInputEnabled(false);
    return;
  }
  $("load-error").hidden = true;
  state.sessionId = res.session_id;
  state.view = res.view;
  state.st = res.state;
  $("heard-text").textContent = "（まだありません）";
  $("facts-view").textContent = "（まだありません）";
  $("reply-message").hidden = true;
  $("export-result").hidden = true;
  setInputEnabled(true);
  renderAll();
  updateLocks();
});

function setInputEnabled(on) {
  $("text-input").disabled = !on;
  $("text-send").disabled = !on;
}

function recording() {
  return state.voiceState !== "idle";
}

// ---- 画面全体 ----
function renderAll() {
  const v = state.view;
  $("job-area").hidden = false;
  $("job-summary").textContent =
    `ジョブ名 ${v.name}・文字コード ${v.encoding}・位置 ${v.positions.length}個・命令 ${v.instructions.length}行`;
  $("job-notes").replaceChildren(...v.notes.map((n) => el("li", {}, n)));
  $("problems-list").replaceChildren(...v.problems.map((p) => el("li", {}, p)));
  $("problems-panel").hidden = v.problems.length === 0;
  $("max-shift-label").textContent = v.max_shift_label;
  $("figure-message").textContent = v.figure_message;
  $("figure-message").hidden = !v.figure_message;
  renderState();
}

function renderState() {
  const st = state.st;
  if (!st) return;
  $("mock-notice").textContent = st.mock_notice;
  $("selected-name").textContent = st.selected || "なし";
  $("out-dir").textContent = st.out_dir;
  renderCandidate();
  renderApproval();
  renderLog();
  renderTables();
  renderFigures();
}

// ---- 位置の選択 ----
async function select(name) {
  if (!state.sessionId || recording()) return;
  const res = await postJson("/api/select", { session_id: state.sessionId, position: name });
  applyResult(res);
}

function applyResult(res) {
  if (res.state) state.st = res.state;
  if (res.ok === false) {
    showReply("stop", res.error || "理由が分かりません");
  } else if (res.status && res.status !== "ok") {
    showReply(res.status, res.message);
  } else if (res.message) {
    showReply("ok", res.message);
  }
  renderState();
}

function showReply(status, message) {
  const box = $("reply-message");
  const head = { ok: "結果：", ask: "聞き返し：", stop: "止めました（何も変えていません）：" }[status] || "";
  box.className = "reply " + status;
  box.replaceChildren(el("strong", {}, head), message);
  box.hidden = false;
}

// ---- 3. 指示（音声または文字）。どちらも同じ /api/hear へ ----
$("text-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (recording()) return;
  const text = $("text-input").value.trim();
  if (!text) return;
  // 認識文を直さずに送ったときだけ source=voice（記録だけ。結果は変わらない）
  const source = state.voiceFinal !== null && text === state.voiceFinal ? "voice" : "text";
  state.voiceFinal = null;
  await sendInstruction(text, source);
  $("text-input").value = "";
});

async function sendInstruction(text, source) {
  if (!state.sessionId) return;
  const res = await postJson("/api/hear", { session_id: state.sessionId, text: text, source: source });
  $("heard-text").textContent = res.heard !== undefined ? res.heard : text;
  renderFacts(res.facts);
  applyResult(res);
  if (res.candidate_made && state.st && state.st.candidate) openConfirm();
}

function renderFacts(f) {
  const box = $("facts-view");
  if (!f) { box.textContent = "（取り出せたことはありません）"; return; }
  const kindNames = { select: "選ぶ", move: "移動", speed: "速度", compare: "比べる", undo: "元に戻す",
    approve: "承認・書き出しを求める言葉", start: "起動を求める言葉" };
  const rows = [
    ["操作の種類", f.kinds.map((k) => kindNames[k] || k).join("・") || "分かりません"],
    ["位置番号", f.positions.join("・") || (f.refers_to_selection ? "（選んでいる位置）" : "なし")],
    ["量", f.amounts.join("・") || "なし"],
    ["方向（軸）", f.axes.join("・") || "なし"],
  ];
  if (f.requested_value !== null) rows.push(["読み取った符号つきの量", String(f.requested_value)]);
  if (f.unclear_amounts.length) rows.push(["はっきりしない量", f.unclear_amounts.join("・")]);
  if (f.bad_positions.length) rows.push(["形が違う位置番号", f.bad_positions.join("・")]);
  if (f.sides.length) rows.push(["左右", f.sides.join("・")]);
  box.replaceChildren(el("dl", { class: "facts" },
    ...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])));
}

// ---- 4. 変更候補のプレビュー ----
function renderCandidate() {
  const st = state.st;
  const c = st.candidate;
  const host = $("candidate-view");
  $("open-confirm").disabled = !c || recording();
  $("compare-button").disabled = !c || recording();
  $("undo-button").disabled = !c || recording();
  if (!c) {
    host.replaceChildren(el("p", { class: "muted" }, "変更候補はまだありません。"));
    return;
  }
  const parts = [];
  if (c.blocked_reason) {
    parts.push(el("div", { class: "result refused" },
      el("strong", {}, "参考の表示だけの候補です。実機用の検査で止まったため、承認も出力もできません。"),
      el("p", {}, "理由：" + c.blocked_reason)));
  }
  parts.push(el("div", { class: "result proposal" },
    el("strong", {}, `変更候補（${c.target}・ファイルの ${c.line_no}行目）。承認するまでジョブは変わりません。`),
    diffView(c),
    el("p", {}, `変更前の値：${c.before_value}　変更後の値：${c.after_value}　変化量：${c.delta}`),
    el("p", {}, "変わる命令の行："),
    el("ul", {}, ...c.affected.map((a) => el("li", {}, a))),
    el("p", {}, "注意："),
    el("ul", {}, ...c.warnings.map((w) => el("li", {}, w)))));
  host.replaceChildren(...parts);
}

function diffView(c) {
  return el("div", { class: "diff" },
    el("div", { class: "minus" }, `- ${c.line_no}行目  ${c.before_line}`),
    el("div", { class: "plus" }, `+ ${c.line_no}行目  ${c.after_line}`));
}

$("compare-button").addEventListener("click", async () => {
  const res = await postJson("/api/compare", { session_id: state.sessionId });
  applyResult(res);
});

$("undo-button").addEventListener("click", async () => {
  const res = await postJson("/api/undo", { session_id: state.sessionId });
  closeConfirm();
  applyResult(res);
});

// ---- 大きな確認表示（文はサーバーのプログラムが作ったもの） ----
function openConfirm() {
  const st = state.st;
  if (!st || !st.candidate) return;
  $("confirm-text").textContent = st.confirmation_text || "";
  $("confirm-blocked").textContent = st.blocked_reason ? "実機用の検査では止まりました：" + st.blocked_reason : "";
  $("confirm-blocked").hidden = !st.blocked_reason;
  $("confirm-diff").replaceChildren(diffView(st.candidate));
  $("confirm-error").hidden = true;
  $("approve-button").disabled = !st.can_approve || recording();
  $("confirm-overlay").hidden = false;
}

function closeConfirm() {
  $("confirm-overlay").hidden = true;
}

$("open-confirm").addEventListener("click", openConfirm);
$("confirm-close").addEventListener("click", closeConfirm);

// ---- 5. 承認（このボタンからだけ） ----
async function onApproveClick() {
  if (recording()) return;
  const name = $("approver-name").value.trim();
  if (!name) {
    $("confirm-error").textContent = "承認する人の名前を入れてください";
    $("confirm-error").hidden = false;
    return;
  }
  const res = await postJson("/api/approve", { session_id: state.sessionId, approver: name });
  if (res.state) state.st = res.state;
  if (res.ok !== true) {
    $("confirm-error").textContent = "承認できませんでした：" + (res.error || "理由が分かりません");
    $("confirm-error").hidden = false;
    renderState();
    return;
  }
  closeConfirm();
  $("export-result").hidden = true;
  renderState();
}
$("approve-button").addEventListener("click", onApproveClick);

function renderApproval() {
  const st = state.st;
  const a = st.approval;
  $("approval-status").textContent = a
    ? `${a.approved_by} さんが承認ボタンで承認しました（${a.approved_at}）。出力ボタンが使えます。`
    : "まだ承認していません。承認は確認表示の承認ボタンだけで行います（指示の文字では承認しません）。";
  $("export-button").disabled = !st.can_export || recording();
  $("approve-button").disabled = !st.can_approve || recording();
  if (!a && !$("export-result").hidden && $("export-result").dataset.ok === "true") {
    $("export-result").hidden = true;
  }
}

// ---- シミュレーション用の出力 ----
$("export-button").addEventListener("click", async () => {
  if (recording()) return;
  const res = await postJson("/api/export", { session_id: state.sessionId });
  if (res.state) state.st = res.state;
  const box = $("export-result");
  if (res.ok === true) {
    box.className = "result proposal";
    box.dataset.ok = "true";
    box.replaceChildren(el("strong", {}, "シミュレーション用のジョブを出力しました（実機には入れないでください）："),
      el("div", {}, el("code", {}, res.path)));
  } else {
    box.className = "result refused";
    box.dataset.ok = "false";
    box.replaceChildren(el("strong", {}, "出力していません。"), el("p", {}, res.error || "理由が分かりません"));
  }
  box.hidden = false;
  renderState();
});

// ---- 作業ログ ----
function renderLog() {
  const ol = $("work-log");
  ol.replaceChildren(...state.st.log.map((x) =>
    el("li", { class: "log-" + (x.kind === "止めた理由" ? "stop" : x.kind === "聞き返し" ? "ask" : "info") },
      el("strong", {}, x.kind + "："), x.text)));
  ol.scrollTop = ol.scrollHeight;
}

// ---- 一覧 ----
function usersOf(name) {
  const p = state.view.positions.find((q) => q.name === name);
  return p ? p.users.map((u) => u.line_no) : [];
}

function renderTables() {
  const v = state.view;
  const selected = state.st ? state.st.selected : null;
  const related = new Set(selected ? usersOf(selected) : []);

  const pb = $("positions-table").tBodies[0];
  pb.replaceChildren(...v.positions.map((p) => {
    const fmt = `${p.format}${p.rectan ? "（RECTAN）" : ""}${p.robot_coordinates ? "" : "：ロボット座標でない"}` +
      `${p.after_unknown_directive ? "（分からない指示行のあと）" : ""}・TOOL ${p.tool ?? "なし"}` +
      (p.user ? `・USER ${p.user}` : "");
    const users = p.users.length
      ? el("div", {}, ...p.users.map((u) => el("div", {}, el("code", {}, `${u.line_no}行目 ${u.text}`))))
      : "どの命令も使っていません";
    const chk = p.shift_check.ok
      ? el("span", { class: "ok" }, "形式の検査は通る（枠の検査はまだ無い）")
      : el("span", { class: "ng" }, "断る：" + p.shift_check.reason);
    const tr = el("tr", { class: "clickable" + (p.name === selected ? " selected" : "") },
      el("td", {}, el("strong", {}, p.name)), el("td", {}, p.line_no),
      el("td", {}, el("code", {}, p.line)), el("td", {}, fmt), el("td", {}, users), el("td", {}, chk));
    tr.addEventListener("click", () => select(p.name));
    return tr;
  }));

  const ib = $("instructions-table").tBodies[0];
  ib.replaceChildren(...v.instructions.map((i) => {
    let chk = "";
    if (i.speed_check) {
      chk = i.speed_check.ok
        ? el("span", { class: "ok" }, "形式の検査は通る（枠の検査はまだ無い）")
        : el("span", { class: "ng" }, "断る：" + i.speed_check.reason);
    }
    const tr = el("tr", { class: (i.position ? "clickable " : "") + (related.has(i.line_no) ? "related" : "") },
      el("td", {}, i.line_no), el("td", {}, el("code", {}, i.text)), el("td", {}, chk));
    if (i.position) tr.addEventListener("click", () => select(i.position));
    return tr;
  }));
}

// ---- 図 ----
const AXES = { X: 0, Y: 1, Z: 2 };

function renderFigures() {
  drawFigure($("fig-xy"), "X", "Y");
  drawFigure($("fig-xz"), "X", "Z");
  const c = state.st && state.st.candidate;
  $("legend").hidden = !(c && c.after_positions);
}

function drawFigure(host, axA, axB) {
  const v = state.view;
  const a = AXES[axA], b = AXES[axB];
  const selected = state.st ? state.st.selected : null;
  const before = v.positions.filter((p) => p.xyz);
  const c = state.st && state.st.candidate && state.st.candidate.after_positions ? state.st.candidate : null;
  const after = c ? c.after_positions.filter((p) => p.xyz) : [];
  if (before.length === 0) {
    host.replaceChildren(el("p", { class: "warn" }, "ロボット座標でないため描けません"));
    return;
  }
  const all = before.concat(after);
  const xs = all.map((p) => p.xyz[a]), ys = all.map((p) => p.xyz[b]);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1);
  const W = 460, H = 460, PAD = 60, k = (W - 2 * PAD) / span;
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const sx = (val) => W / 2 + (val - cx) * k;
  const sy = (val) => H / 2 - (val - cy) * k; // 画面は下向きが正なので反転

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": `${axA}・${axB} の図` });

  const drawSegs = (segs, posList, color, width, opacity) => {
    const at = Object.fromEntries(posList.map((p) => [p.name, p]));
    for (const s of segs) {
      const p = at[s.from], q = at[s.to];
      if (!p || !q) continue;
      const related = selected && (s.to === selected || s.from === selected);
      const line = svgEl("line", { x1: sx(p.xyz[a]), y1: sy(p.xyz[b]), x2: sx(q.xyz[a]), y2: sy(q.xyz[b]),
        stroke: color, "stroke-width": related ? width + 1.5 : width, "stroke-opacity": opacity });
      if (!s.solid) line.setAttribute("stroke-dasharray", "6 4");
      const t = svgEl("title", {});
      t.textContent = `${s.line_no}行目 ${s.text}${s.solid ? "" : "（目安）"}`;
      line.append(t);
      svg.append(line);
    }
  };

  if (c) {
    drawSegs(v.segments, before, "#9a9a9a", 2, 0.9);          // 変更前（灰色）
    drawSegs(c.after_segments, after, "#d0602a", 2, 0.95);    // 変更後（色付き）
  } else {
    drawSegs(v.segments, before, "#4a6fa5", 2, 1);
  }

  // 名前のラベルは、近くのラベルと重ならないように下へずらして置く
  const placed = [];
  const placeLabel = (x, y, w) => {
    let ly = y - 8;
    while (placed.some((b) => Math.abs(b.x - (x + 9)) < Math.max(b.w, w) && Math.abs(b.y - ly) < 13)) ly += 14;
    placed.push({ x: x + 9, y: ly, w: w });
    return ly;
  };
  const sorted = before.slice().sort((p, q) => sy(p.xyz[b]) - sy(q.xyz[b]) || sx(p.xyz[a]) - sx(q.xyz[a]));
  for (const p of sorted) {
    const x = sx(p.xyz[a]), y = sy(p.xyz[b]);
    const moved = c && c.moved === p.name;
    const sel = p.name === selected;
    const text = p.name + (moved ? "（変更前）" : "");
    const g = svgEl("g", { class: "pt" });
    if (sel) g.append(svgEl("circle", { cx: x, cy: y, r: 11, fill: "none", stroke: "#e0a000", "stroke-width": 3 }));
    g.append(svgEl("circle", { cx: x, cy: y, r: 5, fill: moved ? "#9a9a9a" : (c ? "#555" : "#d0602a") }));
    // 動かす位置の「変更前」は点の下に置く（「変更後」は点の上）
    const label = svgEl("text", { x: x + 9, y: placeLabel(x, moved ? y + 24 : y, text.length * 8), "font-weight": sel ? "bold" : "normal" });
    label.textContent = text;
    g.append(label);
    const t = svgEl("title", {});
    t.textContent = `${p.name}  ${p.line}`;
    g.append(t);
    g.addEventListener("click", () => select(p.name));
    svg.append(g);
  }
  if (c) {
    const q = after.find((p) => p.name === c.moved);
    if (q) {
      const x = sx(q.xyz[a]), y = sy(q.xyz[b]);
      svg.append(svgEl("circle", { cx: x, cy: y, r: 6, fill: "#d0602a" }));
      const text = `${q.name}（変更後）`;
      const label = svgEl("text", { x: x + 9, y: placeLabel(x, y, text.length * 8), fill: "#b04010", "font-weight": "bold" });
      label.textContent = text;
      svg.append(label);
    }
  }
  const ax1 = svgEl("text", { x: W - 46, y: H - 10 }); ax1.textContent = `${axA} →`;
  const ax2 = svgEl("text", { x: 8, y: 16 }); ax2.textContent = `↑ ${axB}`;
  svg.append(ax1, ax2);
  host.replaceChildren(svg);
}

// ---- 音声入力（speech.js）。認識した文は入力欄に入れるだけ。承認・出力・送信は呼ばない ----
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const voice = new SpeechInput(Recognition, {
  state: (value) => {
    state.voiceState = value;
    $("voice-status").textContent = { starting: "開始しています…", listening: "聞いています（終了で確定）",
      stopping: "終了しています…", idle: "" }[value] || "";
    updateLocks();
  },
  partial: (text) => {
    $("voice-partial").textContent = text;  // 表示だけ。解析に使わない
  },
  final: (text) => {
    const t = state.voiceToken;
    state.voiceToken = null;
    // 録音を始めたときとジョブ・位置が違えば使わない
    if (!t || t.sessionId !== state.sessionId || t.selected !== (state.st ? state.st.selected : null)) {
      showVoiceError("録音の間にジョブか位置が変わったため、認識文は使いません。");
      return;
    }
    $("text-input").value = text;  // 入力欄に入れるだけ。自動では送らない
    state.voiceFinal = text;
    $("voice-status").textContent = "認識文を入力欄に入れました。確かめて、直してから「指示を送る」を押してください";
  },
  error: (message) => {
    state.voiceToken = null;
    showVoiceError(message);  // 固定の文。入力欄は変えない
  },
});

function showVoiceError(message) {
  $("voice-error").textContent = message;
  $("voice-error").hidden = false;
}

function voiceMode() {
  const checked = document.querySelector('input[name="voice-mode"]:checked');
  return checked ? checked.value : null;
}

function updateLocks() {
  const rec = recording();
  const loaded = !!state.sessionId;
  $("file-input").disabled = rec;
  $("text-input").disabled = rec || !loaded;
  $("text-send").disabled = rec || !loaded;
  $("voice-mode-browser").disabled = rec;
  $("voice-mode-local").disabled = rec;
  $("mic-button").disabled = rec || !loaded || !voiceMode() || !Recognition;
  $("voice-stop").disabled = !rec;
  $("voice-cancel").disabled = !rec;
  document.body.classList.toggle("recording", rec);
  if (state.st && state.view) {
    renderCandidate();
    renderApproval();
  }
}

if (!Recognition) {
  $("voice-controls").hidden = true;
  $("voice-unsupported").hidden = false;
}
for (const id of ["voice-mode-browser", "voice-mode-local"]) $(id).addEventListener("change", updateLocks);

$("mic-button").addEventListener("click", () => {
  const mode = voiceMode();
  if (!state.sessionId || !mode || recording()) return;
  // 新しい録音：前の認識文・エラーは使わない。直さずに残っている前の認識文は入力欄から消す
  if (state.voiceFinal !== null && $("text-input").value === state.voiceFinal) $("text-input").value = "";
  state.voiceFinal = null;
  $("voice-error").hidden = true;
  $("voice-partial").textContent = "";
  closeConfirm();
  state.voiceToken = { sessionId: state.sessionId, selected: state.st ? state.st.selected : null };
  voice.start(mode);
});
$("voice-stop").addEventListener("click", () => voice.stop());
$("voice-cancel").addEventListener("click", () => {
  state.voiceToken = null;
  voice.cancel();  // 認識文は使わない。入力欄は変えない
  $("voice-status").textContent = "取り消しました。認識文は使っていません";
});
updateLocks();
