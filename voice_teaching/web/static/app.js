// ボイスティーチング（シミュレーションモード）の画面。4ステップ：読込 → 位置を選ぶ → 音声で指示 → 確かめて承認。
// - JBI の解析・変更候補・承認・出力はサーバー側で行う。ここでは JBI を解析しない。
// - 指示は文字入力の欄から /api/hear へ送る。音声（speech.js）は認識した文を入力欄に入れるだけで、自動では送らない。
// - 録音中は、取消と終了のほかの操作（読込・位置選択・指示を送る・承認・出力）を使えなくする。
// - 承認は確認表示の承認ボタンからだけ /api/approve を呼ぶ。指示の経路からは呼ばない。
// - 出力ボタンは承認した後だけ使える。失敗を成功と表示しない。
// - 図と再生は、命令の順に位置の点を直線で結んだ「経路の目安」。ロボット本体の動きではない。
// - 外部から何も読み込まない。サーバー（同じ 127.0.0.1）にだけ要求を送る。
// - 画面は「説明が要らない」ことを目指す：右側には今のステップのカードだけ、目立たせる操作は1つだけ。
"use strict";
import { SpeechInput } from "/static/speech.js";
import { View3D } from "/static/view3d.js";

const state = {
  sessionId: null,  // サーバーのメモリにある画面の状態の番号
  view: null,       // 読み込んだときのジョブの内容（/api/load の view）
  st: null,         // サーバーの画面の状態（state）
  figView: "yz",    // 大きく出す図の向き（xy / xz / yz）
  playToken: 0,     // 再生を止めるための番号（描き直すと増やす）
  modeOpen: false,  // マイクの方式を選び直す欄を開いているか
  pickAgain: false, // 位置を選び直している（図で重なった点も、位置のボタンで選べるように）
  voiceState: "idle",   // 録音の状態（starting / listening / stopping / idle）
  voiceToken: null,     // 録音を始めたときのジョブ（session）と位置。違えば認識文を使わない
  voiceFinal: null,     // 最後に入力欄へ入れた認識文（直さずに送れば source=voice として記録）
  live: false,          // マイク ON（Web会議のように、一度 ON にすると続けて話せる）
  liveTimer: null,
  liveAt: 0,            // マイクを ON にした・最後に話した時刻（長く放っておいたら OFF）
};

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const NO_SERVER = "アプリにつながりません。アプリを起動し直して、もう一度どうぞ";

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

// エラーは専門用語でなく「次に何をすればよいか」を大きく出し、プログラムの理由は小さく添える（理由は省略しない）
const HEADS = {
  load: "読み込めませんでした。.JBI のファイルを選んで、もう一度どうぞ",
  stop: "この指示は使えませんでした。言い方を変えて、もう一度どうぞ",
  ask: "もう少し教えてください",
  blocked: "この位置は、ここでは変更できません。「元に戻す」を押して、別の位置を選んでください",
  limit: "1回に動かせる量を超えています。小さい量で、もう一度どうぞ",
  approve: "承認できませんでした",
  export: "出力していません",
};

function friendly(kind, reason) {
  const box = el("div", { class: "friendly " + kind });
  box.append(el("strong", {}, HEADS[kind] || ""));
  if (reason) box.append(el("p", { class: "reason" }, reason));
  return box;
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
    return { ok: false, error: NO_SERVER };
  }
}

function recording() {
  return state.voiceState !== "idle";
}

// ---- 1. 読み込み（ドラッグ＆ドロップ、またはファイルを選ぶ。どちらも loadFile を通る） ----
async function loadFile(f) {
  if (!f || recording()) return;
  voice.cancel();
  state.live = false;  // ジョブが変わったらマイクは OFF に戻す
  state.voiceFinal = null;
  $("text-input").value = "";
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
    res = { ok: false, error: NO_SERVER };
  }
  state.sessionId = null; state.view = null; state.st = null;
  closeConfirm();
  stopPlayback();
  if (!res.ok) {
    $("load-error").replaceChildren(friendly("load", res.error || "理由が分かりません"));
    $("load-error").hidden = false;
    $("job-area").hidden = true;
    $("problems-panel").hidden = true;
    $("file-name").textContent = "";
    document.body.classList.remove("loaded");
    renderSteps();
    updateLocks();
    return;
  }
  $("load-error").hidden = true;
  $("file-name").textContent = f.name;
  state.sessionId = res.session_id;
  state.view = res.view;
  state.st = res.state;
  state.figView = res.view.positions.some((p) => p.xyz) ? "3d" : bestView(res.view.positions);
  state.modeOpen = false;
  state.pickAgain = false;
  $("heard-text").textContent = "（まだありません）";
  $("facts-view").textContent = "（まだありません）";
  $("reply-message").hidden = true;
  $("export-result").hidden = true;
  document.body.classList.add("loaded");
  renderAll();
  updateLocks();
}

$("file-input").addEventListener("change", (ev) => loadFile(ev.target.files[0]));

const drop = $("drop-zone");
drop.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  if (!recording()) drop.classList.add("dragover");
});
drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
drop.addEventListener("drop", (ev) => {
  ev.preventDefault();
  drop.classList.remove("dragover");
  const f = ev.dataTransfer && ev.dataTransfer.files[0];
  if (f) loadFile(f);
});
drop.addEventListener("click", (ev) => {
  if (ev.target.closest("label, input") || recording()) return;  // ラベルは自分でファイル選択を開く
  $("file-input").click();
});
drop.addEventListener("keydown", (ev) => {
  if ((ev.key === "Enter" || ev.key === " ") && !recording()) { ev.preventDefault(); $("file-input").click(); }
});
// 枠の外に落としたファイルも読み込む（ブラウザーがファイルを開いてしまわないように）
window.addEventListener("dragover", (ev) => ev.preventDefault());
window.addEventListener("drop", (ev) => {
  ev.preventDefault();
  const f = ev.dataTransfer && ev.dataTransfer.files[0];
  if (f && !drop.contains(ev.target)) loadFile(f);
});

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
  // 解釈のしかた（モックか本物の Claude か）を、いつも見える上の帯で区別する
  const claude = st.interpreter && st.interpreter.kind === "claude";
  $("interp-badge").textContent = claude ? "解釈：Claude" : "解釈：モック";
  $("interp-badge").classList.toggle("claude", claude);
  $("interp-badge").title = st.mock_notice;
  $("interp-badge").hidden = false;
  $("voice-position").replaceChildren(el("strong", {}, st.selected || ""), el("span", {}, "変える"));
  $("out-dir").textContent = st.out_dir;
  renderChips();
  renderCandidate();
  renderApproval();
  renderLog();
  renderTables();
  renderFigures();
  renderSteps();
}

// ---- 今のステップと「次にすること」 ----
function currentStep() {
  const st = state.st;
  if (!state.sessionId || !st) return 1;
  if (st.candidate) return 4;
  if (!st.selected || state.pickAgain) return 2;
  return 3;
}

function voiceMode() {
  const checked = document.querySelector('input[name="voice-mode"]:checked');
  return checked ? checked.value : null;
}

function renderSteps() {
  const step = currentStep();
  for (let i = 1; i <= 4; i++) {
    const li = $("step-" + i);
    li.classList.toggle("done", i < step);
    li.classList.toggle("current", i === step);
    if (i === step) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  }
  // 右側には今のステップのカードだけを出す
  const cards = { 2: "select-panel", 3: "instruction-panel", 4: "candidate-panel" };
  for (const [n, id] of Object.entries(cards)) {
    $(id).hidden = Number(n) !== step;
  }
  // マイクの方式は、選ぶまでだけ出す（選んだ後は「マイクの方式を変える」で開く）
  const chosen = !!voiceMode();
  $("voice-mode-set").hidden = chosen && !state.modeOpen;
  $("voice-mode-change").hidden = !chosen || state.modeOpen || recording();
  // 次に押すものを1つだけ目立たせる
  for (const id of ["drop-zone", "mic-button", "voice-stop", "text-send", "open-confirm", "undo-button", "export-button"]) {
    $(id).classList.remove("primary");
  }
  const st = state.st;
  let text = "", primary = null;
  if (step === 1) {
    text = "JOBファイルをドロップしてください";
    primary = "drop-zone";
  } else if (step === 2) {
    text = "図の点か、下の番号を押してください";
  } else if (step === 3) {
    if (recording()) {
      text = "どうぞ、話してください";
      primary = "voice-stop";
    } else if ($("text-input").value.trim()) {
      text = "文を確かめて「次へ」";
      primary = "text-send";
    } else if (!$("voice-controls").hidden && !chosen) {
      text = "マイクの方式を選んでください";
    } else if (!$("voice-controls").hidden) {
      text = "🎤 を押して話してください";
      primary = "mic-button";
    } else {
      text = "動かし方を書いて「次へ」";
      primary = "text-send";
    }
  } else if (st.blocked_reason) {
    text = "この変更はできません";
    primary = "undo-button";
  } else if (!st.approval) {
    text = "動きを確かめてください";
    primary = "open-confirm";
  } else if (!st.exported) {
    text = "承認しました。出力してください";
    primary = "export-button";
  } else {
    text = "出力しました";
  }
  $("next-action").textContent = text;
  if (primary) $(primary).classList.add("primary");
  resumeLive();  // マイクが ON なら、入力欄が空のステップ3で、また聞き始める
}

$("text-input").addEventListener("input", renderSteps);
for (const id of ["voice-mode-browser", "voice-mode-local"]) {
  $(id).addEventListener("change", () => { state.modeOpen = false; renderSteps(); });
}
$("voice-mode-change").addEventListener("click", () => { state.modeOpen = true; renderSteps(); });
$("voice-position").addEventListener("click", () => {
  if (recording()) return;
  state.pickAgain = true;
  renderSteps();
});

// ---- 詳細表示（同じ画面の中で開く・閉じる） ----
$("engineer-toggle").addEventListener("click", () => {
  const open = $("engineer-view").hidden;
  $("engineer-view").hidden = !open;
  $("engineer-toggle").setAttribute("aria-expanded", String(open));
  $("engineer-toggle").textContent = open ? "詳細表示を閉じる" : "詳細表示";
  if (open) $("engineer-view").scrollIntoView({ behavior: "smooth", block: "start" });
});

// ---- 2. 位置の選択 ----
async function select(name) {
  if (!state.sessionId || recording()) return;
  const res = await postJson("/api/select", { session_id: state.sessionId, position: name });
  state.pickAgain = false;
  $("reply-message").hidden = true;
  applyResult(res, false);
}

function renderChips() {
  const selected = state.st ? state.st.selected : null;
  $("chips-note").hidden = state.view.positions.every((p) => p.shift_check.ok);
  $("position-chips").replaceChildren(...state.view.positions.map((p) => {
    const b = el("button", {
      type: "button",
      class: "chip" + (p.name === selected ? " selected" : "") + (p.shift_check.ok ? "" : " blocked"),
      title: p.shift_check.ok ? p.line : "この位置は変更できません：" + p.shift_check.reason,
    }, p.name);
    b.disabled = recording();
    b.addEventListener("click", () => select(p.name));
    return b;
  }));
}

function applyResult(res, showOk = true) {
  if (res.state) state.st = res.state;
  state.resumeArmed = true;  // 結果が出た：マイクが ON なら、また聞き始めてよい
  if (res.ok === false) {
    showReply("stop", res.error || "理由が分かりません");
  } else if (res.status && res.status !== "ok") {
    showReply(res.status, res.message);
  } else if (res.message && showOk) {
    showReply("ok", res.message);
  }
  renderState();
}

function showReply(status, message) {
  const box = $("reply-message");
  box.className = "reply " + status;
  if (status === "ok") box.replaceChildren(el("p", {}, message));
  else if (status === "ask") box.replaceChildren(friendly("ask", message));
  else if (/まで(です)?|上限/.test(message || "")) box.replaceChildren(friendly("limit", message));
  else box.replaceChildren(friendly("stop", message));
  box.hidden = false;
  $("voice-status").textContent = "";
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
  $("text-input").value = "";
  await sendInstruction(text, source);
});

async function sendInstruction(text, source) {
  if (!state.sessionId) return;
  const res = await postJson("/api/hear", { session_id: state.sessionId, text: text, source: source });
  $("heard-text").textContent = res.heard !== undefined ? res.heard : text;
  renderFacts(res.facts);
  applyResult(res, false);
  // 候補ができたら、まず図で変更前後の経路を再生して見せる（承認は確認表示のボタンだけ）
  if (res.candidate_made && state.st && state.st.candidate) startPlayback();
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

// ---- 4. 変更候補（要点だけをカードに。詳しくは詳細表示） ----
function renderCandidate() {
  const st = state.st;
  const c = st.candidate;
  const host = $("candidate-view");
  $("open-confirm").disabled = !c || recording();
  $("compare-button").disabled = !c || recording();
  $("undo-button").disabled = !c || recording();
  if (!c) {
    host.replaceChildren();
    $("candidate-detail").textContent = "（まだありません）";
    return;
  }
  const [pos, axis] = c.target.split(" ");
  const parts = [];
  if (c.blocked_reason) parts.push(friendly("blocked", c.blocked_reason));
  parts.push(el("p", { class: "cand-pos" }, pos));
  parts.push(el("p", { class: "cand-big" }, `${axis}方向に ${c.delta}mm`));
  parts.push(el("p", { class: "cand-values" }, `${c.before_value} → ${c.after_value}`));
  if (c.warnings.length) parts.push(el("ul", { class: "cand-warn" }, ...c.warnings.map((w) => el("li", {}, w))));
  host.replaceChildren(...parts);
  $("candidate-detail").replaceChildren(
    el("p", {}, `変更候補（${c.target}・ファイルの ${c.line_no}行目）。承認するまでジョブは変わりません。`),
    diffView(c),
    el("p", {}, "変わる命令の行："),
    el("ul", {}, ...c.affected.map((a) => el("li", {}, a))));
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
  stopPlayback();
  applyResult(res);
});

// ---- 大きな確認表示（文はサーバーのプログラムが作ったもの） ----
function openConfirm() {
  const st = state.st;
  if (!st || !st.candidate) return;
  stopPlayback();
  $("confirm-text").textContent = st.confirmation_text || "";
  // 止まった理由は確認の文に入っている。入っていないときだけ、ここに添える（理由は省略しない）
  const reason = st.blocked_reason && !(st.confirmation_text || "").includes(st.blocked_reason)
    ? "（実機用の検査で止まりました：" + st.blocked_reason + "）" : "";
  $("confirm-blocked").textContent = st.blocked_reason
    ? "この変更は承認できません。閉じて「元に戻す」を押し、別の位置を選んでください" + reason : "";
  $("confirm-blocked").hidden = !st.blocked_reason;
  // U1：承認ボタンを押す画面に、候補の注意をすべて出す（右のカードは確認表示に隠れるため）
  const notes = [...st.candidate.warnings];  // 候補の注意は全部（一部だけにしない）
  if (state.view.figure_message) notes.push(state.view.figure_message);  // 図に描けない点がある
  $("confirm-warnings").replaceChildren(...notes.map((w) => el("li", {}, w)));
  $("confirm-warnings").hidden = notes.length === 0;
  $("confirm-affected").replaceChildren(...st.candidate.affected.map((a) => el("li", {}, a)));
  $("confirm-diff").replaceChildren(diffView(st.candidate));
  if (state.view.positions.some((p) => p.xyz)) {
    confirm3d.reset();
    confirm3d.setData(data3d());
  } else {
    drawFigure($("confirm-figure"), "X", "Z", true);  // ロボット座標でないときは「描けません」と出す
  }
  $("confirm-error").hidden = true;
  $("approve-button").disabled = !st.can_approve || recording();
  $("approver-name").disabled = !st.can_approve;
  $("confirm-overlay").hidden = false;
  if (st.can_approve) $("approver-name").focus(); else $("confirm-close").focus();
}

function closeConfirm() {
  const was = !$("confirm-overlay").hidden;
  $("confirm-overlay").hidden = true;
  if (was && !$("open-confirm").hidden && !$("open-confirm").disabled) $("open-confirm").focus();
}

$("open-confirm").addEventListener("click", openConfirm);
$("confirm-close").addEventListener("click", closeConfirm);
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("confirm-overlay").hidden) closeConfirm();
});

// ---- 承認（このボタンからだけ） ----
async function onApproveClick() {
  if (recording()) return;
  const name = $("approver-name").value.trim();
  if (!name) {
    $("confirm-error").textContent = "名前を入れてから、承認してください";
    $("confirm-error").hidden = false;
    $("approver-name").focus();
    return;
  }
  const res = await postJson("/api/approve", { session_id: state.sessionId, approver: name });
  if (res.state) state.st = res.state;
  if (res.ok !== true) {
    $("confirm-error").replaceChildren(friendly("approve", res.error || "理由が分かりません"));
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
  $("approval-status").textContent = a ? `✓ ${a.approved_by} さんが承認しました` : "";
  $("approval-status").hidden = !a;
  $("open-confirm").hidden = !!a;
  $("export-button").hidden = !a;
  $("export-note").hidden = !a;
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
    box.className = "result done";
    box.dataset.ok = "true";
    box.replaceChildren(el("strong", {}, "✓ シミュレーション用に出力しました（実機には入れないでください）"),
      el("p", { class: "reason" }, el("code", {}, res.path)));
  } else {
    box.className = "result";
    box.dataset.ok = "false";
    box.replaceChildren(friendly("export", res.error || "理由が分かりません"));
  }
  box.hidden = false;
  renderState();
});

// ---- 作業ログ（詳細表示） ----
function renderLog() {
  const ol = $("work-log");
  ol.replaceChildren(...state.st.log.map((x) =>
    el("li", { class: "log-" + (x.kind === "止めた理由" ? "stop" : x.kind === "聞き返し" ? "ask" : "info") },
      el("strong", {}, x.kind + "："), x.text)));
  ol.scrollTop = ol.scrollHeight;
}

// ---- 一覧（詳細表示） ----
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

// ---- 図（大きな図1つ。3つの向きを切り替える。小さな図は詳細表示） ----
const AXES = { X: 0, Y: 1, Z: 2 };
const VIEWS = { xy: ["X", "Y"], xz: ["X", "Z"], yz: ["Y", "Z"] };
const C_LINE = "#c7cbd1", C_BEFORE = "#a1a6ad", C_AFTER = "#ff6a1a", C_POINT = "#1d1d1f", C_SEL = "#0a84ff";

// 位置の広がりがいちばん大きい2つの軸の図を、最初に大きく出す
function bestView(positions) {
  const pts = positions.filter((p) => p.xyz);
  if (!pts.length) return "yz";
  const spread = [0, 1, 2].map((k) => {
    const v = pts.map((p) => p.xyz[k]);
    return Math.max(...v) - Math.min(...v);
  });
  const order = [0, 1, 2].sort((i, j) => spread[j] - spread[i]).slice(0, 2).sort();
  return { "0,1": "xy", "0,2": "xz", "1,2": "yz" }[order.join(",")];
}

function setView(name) {
  state.figView = name;
  stopPlayback();
  renderFigures();
}

for (const b of document.querySelectorAll(".view-tab")) b.addEventListener("click", () => setView(b.dataset.view));
for (const f of document.querySelectorAll(".figs-small figure")) f.addEventListener("click", () => setView(f.dataset.view));

// ---- 3D（ジョブの X・Y・Z をそのまま点で描く。ロボット本体は描かない） ----
const view3d = new View3D($("fig-3d"), { onPick: (name) => {
  if (state.st && state.st.candidate) {
    $("play-status").textContent = "変更を確かめている間は、位置を選び直せません（「元に戻す」で選び直せます）";
    return;
  }
  select(name);
} });
const confirm3d = new View3D($("confirm-figure"), { width: 640, height: 440 });  // 確認表示の中では選び直さない

function data3d() {
  const v = state.view;
  const c = state.st && state.st.candidate && state.st.candidate.after_positions ? state.st.candidate : null;
  return {
    points: v.positions.filter((p) => p.xyz),
    segments: v.segments,
    selected: state.st ? state.st.selected : null,
    after: c ? { points: c.after_positions.filter((p) => p.xyz), segments: c.after_segments, moved: c.moved } : null,
    delta_label: c ? `${c.target.split(" ")[1]} ${c.delta}mm` : "",
    blocked: Object.fromEntries(v.positions.filter((p) => !p.shift_check.ok).map((p) => [p.name, p.shift_check.reason])),
  };
}

$("view-reset").addEventListener("click", () => view3d.reset());

function renderFigures() {
  stopPlayback();
  for (const b of document.querySelectorAll(".view-tab")) {
    b.setAttribute("aria-selected", String(b.dataset.view === state.figView));
  }
  const is3d = state.figView === "3d" && state.view.positions.some((p) => p.xyz);
  $("fig-3d").hidden = !is3d;
  $("fig-main").hidden = is3d;
  $("view-reset").hidden = !is3d;
  if (is3d) view3d.setData(data3d());
  else drawFigure($("fig-main"), ...VIEWS[state.figView === "3d" ? bestView(state.view.positions) : state.figView], true);
  drawFigure($("fig-xy"), "X", "Y", false);
  drawFigure($("fig-xz"), "X", "Z", false);
  drawFigure($("fig-yz"), "Y", "Z", false);
  const c = state.st && state.st.candidate;
  $("legend").hidden = !(c && c.after_positions);
  $("play-button").disabled = !state.view || state.view.segments.length === 0 || recording();
}

// 図を描き、再生のために座標の変換を返す
function drawFigure(host, axA, axB, big) {
  const v = state.view;
  const a = AXES[axA], b = AXES[axB];
  const selected = state.st ? state.st.selected : null;
  const before = v.positions.filter((p) => p.xyz);
  const c = state.st && state.st.candidate && state.st.candidate.after_positions ? state.st.candidate : null;
  const after = c ? c.after_positions.filter((p) => p.xyz) : [];
  if (before.length === 0) {
    host.replaceChildren(el("p", { class: "warn" }, "ロボット座標でないため描けません"));
    return null;
  }
  const all = before.concat(after);
  const xs = all.map((p) => p.xyz[a]), ys = all.map((p) => p.xyz[b]);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1);
  const W = big ? 720 : 300, H = big ? 480 : 220, PAD = big ? 70 : 24;
  const k = Math.min((W - 2 * PAD) / span, (H - 2 * PAD) / span);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const sx = (val) => W / 2 + (val - cx) * k;
  const sy = (val) => H / 2 - (val - cy) * k; // 画面は下向きが正なので反転

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": `${axA}・${axB} の図` });

  const segLines = { before: [], after: [] };
  const drawSegs = (segs, posList, color, width, key) => {
    const at = Object.fromEntries(posList.map((p) => [p.name, p]));
    for (const s of segs) {
      const p = at[s.from], q = at[s.to];
      if (!p || !q) continue;
      const line = svgEl("line", { x1: sx(p.xyz[a]), y1: sy(p.xyz[b]), x2: sx(q.xyz[a]), y2: sy(q.xyz[b]),
        stroke: color, "stroke-width": width, "stroke-linecap": "round" });
      if (!s.solid) line.setAttribute("stroke-dasharray", "6 5");
      const t = svgEl("title", {});
      t.textContent = `${s.line_no}行目 ${s.text}${s.solid ? "" : "（目安）"}`;
      line.append(t);
      svg.append(line);
      segLines[key].push({ seg: s, line: line, width: width, from: [sx(p.xyz[a]), sy(p.xyz[b])], to: [sx(q.xyz[a]), sy(q.xyz[b])] });
    }
  };

  const w = big ? 2.5 : 2;
  if (c) {
    drawSegs(v.segments, before, C_BEFORE, w, "before");        // 変更前（灰色）
    drawSegs(c.after_segments, after, C_AFTER, w + 0.5, "after"); // 変更後（オレンジ）
  } else {
    drawSegs(v.segments, before, C_LINE, w, "before");
  }

  // 名前のラベルは、近くのラベルと重ならないように下へずらして置く
  const placed = [];
  const placeLabel = (x, y, wd) => {
    let ly = y - 10;
    while (placed.some((q) => Math.abs(q.x - (x + 12)) < Math.max(q.w, wd) && Math.abs(q.y - ly) < 14)) ly += 15;
    placed.push({ x: x + 12, y: ly, w: wd });
    return ly;
  };
  const sorted = before.slice().sort((p, q) => sy(p.xyz[b]) - sy(q.xyz[b]) || sx(p.xyz[a]) - sx(q.xyz[a]));
  for (const p of sorted) {
    const x = sx(p.xyz[a]), y = sy(p.xyz[b]);
    const moved = c && c.moved === p.name;
    const sel = p.name === selected;
    const text = p.name + (moved ? "（変更前）" : "");
    const g = svgEl("g", { class: "pt" + (sel ? " sel" : ""), "data-name": p.name });
    g.append(svgEl("circle", { cx: x, cy: y, r: big ? 18 : 8, fill: "transparent" }));  // 押しやすい大きさ
    if (sel) g.append(svgEl("circle", { cx: x, cy: y, r: big ? 14 : 7, fill: "rgba(10,132,255,0.15)", stroke: C_SEL, "stroke-width": 2 }));
    g.append(svgEl("circle", { cx: x, cy: y, r: big ? 6 : 4, fill: moved ? C_BEFORE : (sel ? C_SEL : C_POINT) }));
    const label = svgEl("text", { x: x + 12, y: placeLabel(x, moved ? y + 24 : y, text.length * 8),
      fill: sel ? C_SEL : "#6e6e73", "font-weight": sel ? "600" : "400" });
    label.textContent = text;
    g.append(label);
    const t = svgEl("title", {});
    t.textContent = `${p.name}  ${p.line}`;
    g.append(t);
    if (big) g.addEventListener("click", () => select(p.name));
    svg.append(g);
  }
  if (c) {
    const q = after.find((p) => p.name === c.moved);
    if (q) {
      const x = sx(q.xyz[a]), y = sy(q.xyz[b]);
      svg.append(svgEl("circle", { cx: x, cy: y, r: big ? 7 : 4, fill: C_AFTER }));
      const text = `${q.name}（変更後）`;
      const label = svgEl("text", { x: x + 12, y: placeLabel(x, y, text.length * 8), fill: "#c4470a", "font-weight": "600" });
      label.textContent = text;
      svg.append(label);
    }
  }
  const ax1 = svgEl("text", { x: W - 40, y: H - 12, class: "axis" }); ax1.textContent = `${axA} →`;
  const ax2 = svgEl("text", { x: 12, y: 20, class: "axis" }); ax2.textContent = `↑ ${axB}`;
  svg.append(ax1, ax2);
  host.replaceChildren(svg);
  return { svg, segLines, big };
}

// ---- 経路の再生：JOB の命令の順（view.segments、変更後は after_segments）に、点を直線で動かす目安 ----
function stopPlayback() {
  state.playToken++;
  $("play-status").textContent = "";
  $("play-button").classList.remove("playing");
  view3d.stop();
}

function startPlayback() {
  if (!state.view || recording()) return;
  stopPlayback();
  if (!$("fig-3d").hidden) return startPlayback3d();
  const token = state.playToken;
  const fig = drawFigure($("fig-main"), ...VIEWS[state.figView === "3d" ? bestView(state.view.positions) : state.figView], true);
  if (!fig) return;
  $("play-button").classList.add("playing");
  const c = state.st && state.st.candidate;
  const runs = [{ name: c ? "変更前" : "命令の順", list: fig.segLines.before, color: c ? "#6e6e73" : C_POINT }];
  if (c) runs.push({ name: "変更後", list: fig.segLines.after, color: C_AFTER });
  const marker = svgEl("circle", { r: 9, fill: runs[0].color, stroke: "#fff", "stroke-width": 3 });
  fig.svg.append(marker);
  const SPEED = 260;  // 画面の上の速さ（px/秒）。実際のロボットの速度とは関係ない
  let ri = 0, si = 0, t0 = null;
  const step = (now) => {
    if (token !== state.playToken) { marker.remove(); return; }
    const run = runs[ri];
    if (!run || si >= run.list.length) {
      ri++; si = 0; t0 = null;
      if (ri >= runs.length) {
        $("play-status").textContent = "再生が終わりました";
        $("play-button").classList.remove("playing");
        marker.remove();
        return;
      }
      requestAnimationFrame(step);
      return;
    }
    const s = run.list[si];
    if (t0 === null) {
      t0 = now;
      s.line.setAttribute("stroke-width", "5");
      $("play-status").textContent = `${run.name}を再生中 ${si + 1}/${run.list.length}`;
      $("play-status").title = `${s.seg.line_no}行目 ${s.seg.text}`;
      marker.setAttribute("fill", run.color);
    }
    const len = Math.hypot(s.to[0] - s.from[0], s.to[1] - s.from[1]);
    const dur = Math.max(350, (len / SPEED) * 1000);
    const f = Math.min(1, (now - t0) / dur);
    marker.setAttribute("cx", s.from[0] + (s.to[0] - s.from[0]) * f);
    marker.setAttribute("cy", s.from[1] + (s.to[1] - s.from[1]) * f);
    if (f >= 1) {
      s.line.setAttribute("stroke-width", String(s.width));
      si++; t0 = null;
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// 3D の再生：命令の順（変更前 → 変更後）に、線の上を印が動く（ロボットの動きではない）
function startPlayback3d() {
  const d = data3d();
  const runs = [{ name: d.after ? "変更前" : "命令の順", color: d.after ? "#6e6e73" : C_POINT, segments: d.segments, points: d.points }];
  if (d.after) runs.push({ name: "変更後", color: C_AFTER, segments: d.after.segments, points: d.after.points });
  $("play-button").classList.add("playing");
  view3d.play(runs,
    (run, s) => {
      $("play-status").textContent = `${run.name}を再生中 ${run.segments.indexOf(s) + 1}/${run.segments.length}`;
      $("play-status").title = `${s.line_no}行目 ${s.text}`;  // 命令の行は、マウスを重ねたときだけ
    },
    () => { $("play-status").textContent = "再生が終わりました"; $("play-button").classList.remove("playing"); });
}

$("play-button").addEventListener("click", startPlayback);

// ---- 音声入力（speech.js）。認識した文は入力欄に入れるだけ。承認・出力・送信は呼ばない ----
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

// Web会議のように、マイクを一度 ON（state.live）にすると、続けて話せる：
// 話し終わると認識文が入力欄に入り、録音は一時停止する（確かめて直してから「次へ」を押す）。
// 結果が出て入力欄が空に戻り、ステップ3（音声で指示）にいれば、また聞き始める。
// 認識に失敗したら、マイクを OFF にして文字の入力へ案内する。承認・出力・送信はここから呼ばない。
const voice = new SpeechInput(Recognition, {
  state: (value) => {
    state.voiceState = value;
    $("voice-status").textContent = { starting: "準備しています…", listening: "",
      stopping: "まとめています…", idle: "" }[value] || "";
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
      showVoiceError("話している間に位置が変わったので、使いませんでした。もう一度どうぞ");
      return;
    }
    $("text-input").value = text;  // 入力欄に入れるだけ。自動では送らない
    state.voiceFinal = text;
    state.liveAt = Date.now();
    renderSteps();
    renderMic();
  },
  error: (message) => {
    state.voiceToken = null;
    state.live = false;  // 失敗したら OFF にして、文字の入力へ案内する
    showVoiceError(message);  // 固定の文。入力欄は変えない
    renderMic();
    offerLocalInstall();
    setTimeout(() => { if (!recording() && !$("text-input").disabled) $("text-input").focus(); }, 0);
  },
});

// 「端末内のみ」で日本語の音声データ（Chrome の言語パック）が無いときだけ、入れるボタンを出す（押したときだけ入れる）
async function offerLocalInstall() {
  $("local-install").hidden = true;
  if (voiceMode() !== "local" || !Recognition || typeof Recognition.available !== "function") return;
  try {
    const a = await Recognition.available({ langs: ["ja-JP"], processLocally: true });
    // 返る値は available・unavailable・取り込み中・取り込める の4つ。取り込める（まだ入っていない）ときだけ出す
    $("local-install").hidden = ["available", "unavailable"].includes(a) || /ing$/.test(a);
  } catch (_) { /* 調べられないときは出さない */ }
}

$("local-install").addEventListener("click", async () => {
  $("local-install").hidden = true;
  $("voice-status").textContent = "日本語の音声データを入れています…";
  let ok = false;
  try { ok = await Recognition.install({ langs: ["ja-JP"], processLocally: true }); } catch (_) { ok = false; }
  $("voice-status").textContent = ok ? "入れました。もう一度 🎤 を押してください" : "入れられませんでした。「ブラウザー標準」か、文字で入力してください";
  if (ok) $("voice-error").hidden = true;
});

function showVoiceError(message) {
  $("voice-error").replaceChildren(el("span", {}, message), el("span", { class: "reason" }, "下の欄に文字で入力することもできます"));
  $("voice-error").hidden = false;
}

// マイクの ON／OFF を一目で分かるように（OFF・聞いています・一時停止）
function renderMic() {
  const rec = recording();
  const on = state.live || rec;
  document.body.classList.toggle("live", on);
  $("mic-button").setAttribute("aria-pressed", String(on));
  $("mic-button").setAttribute("aria-label", on ? "マイクをOFFにする" : "マイクをONにする");
  $("mic-label").textContent = rec ? "マイク ON・聞いています"
    : state.live ? "マイク ON（押すと OFF）"
    : "マイク OFF";
}

function startListening() {
  const mode = voiceMode();
  if (!state.sessionId || !mode || recording()) return;
  // 新しい録音：前の認識文・エラーは使わない。直さずに残っている前の認識文は入力欄から消す
  if (state.voiceFinal !== null && $("text-input").value === state.voiceFinal) $("text-input").value = "";
  state.voiceFinal = null;
  $("voice-error").hidden = true;
  $("voice-partial").textContent = "";
  closeConfirm();
  state.voiceToken = { sessionId: state.sessionId, selected: state.st ? state.st.selected : null };
  state.resumeArmed = false;
  voice.start(mode);
}

// マイクが ON のままなら、指示の結果が出て入力欄が空のときに、また聞き始める
const LIVE_MAX_MS = 3 * 60 * 1000;  // マイク ON のまま、指示が無くても聞き続ける長さ

function resumeLive() {
  clearTimeout(state.liveTimer);
  state.liveTimer = setTimeout(() => {
    if (state.live && Date.now() - state.liveAt > LIVE_MAX_MS) {  // しばらく使っていなければ OFF にする
      state.live = false;
      $("voice-status").textContent = "しばらく使わなかったので、マイクを OFF にしました";
      renderMic();
      return;
    }
    if (!state.live || !state.resumeArmed || recording() || !Recognition || !voiceMode() || currentStep() !== 3
        || $("text-input").value.trim() || !$("confirm-overlay").hidden || $("instruction-panel").hidden) return;
    startListening();
  }, 500);
}

function updateLocks() {
  const rec = recording();
  const loaded = !!state.sessionId;
  $("file-input").disabled = rec;
  $("text-input").disabled = rec || !loaded;
  $("text-send").disabled = rec || !loaded;
  $("voice-mode-browser").disabled = rec;
  $("voice-mode-local").disabled = rec;
  $("voice-position").disabled = rec;
  $("mic-button").disabled = rec || !loaded || !voiceMode() || !Recognition;
  $("voice-stop").disabled = !rec;
  $("voice-cancel").disabled = !rec && !state.live;
  document.body.classList.toggle("recording", rec);
  if (state.st && state.view) {
    renderChips();
    renderCandidate();
    renderApproval();
    $("play-button").disabled = state.view.segments.length === 0 || rec;
  }
  renderSteps();
  renderMic();
  if (!rec) resumeLive();
}

if (!Recognition) {
  $("voice-controls").hidden = true;
  $("voice-unsupported").hidden = false;
}
for (const id of ["voice-mode-browser", "voice-mode-local"]) $(id).addEventListener("change", updateLocks);
$("text-input").addEventListener("input", renderMic);

$("mic-button").addEventListener("click", () => {
  if (!state.sessionId || !voiceMode() || recording()) return;
  if (state.live) {  // 一時停止中に押すと OFF
    state.live = false;
    updateLocks();
    return;
  }
  state.live = true;
  state.liveAt = Date.now();
  startListening();
  renderMic();
});
$("voice-stop").addEventListener("click", () => voice.stop());  // 話し終わった（マイクは ON のまま）
$("voice-cancel").addEventListener("click", () => {
  state.live = false;
  state.voiceToken = null;
  voice.cancel();  // 認識文は使わない。入力欄は変えない
  $("voice-status").textContent = "マイクを OFF にしました";
  updateLocks();
});
updateLocks();
