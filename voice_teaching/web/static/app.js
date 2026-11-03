// ボイスティーチング（シミュレーションモード）の画面。4ステップ：読込 → 位置を選ぶ → 音声で指示 → 確かめて承認。
// - JBI の解析・変更候補・承認・出力はサーバー側で行う。ここでは JBI を解析しない。
// - 指示は文字入力の欄から /api/hear へ送る。音声（speech.js）は認識した文を入力欄に入れるだけで、自動では送らない。
// - 録音中は、取消と終了のほかの操作（読込・位置選択・指示を送る・承認・出力）を使えなくする。
// - 承認は確認表示の承認ボタンからだけ /api/approve を呼ぶ。指示の経路からは呼ばない。
// - 出力ボタンは承認した後だけ使える。失敗を成功と表示しない。
// - 図と再生は、命令の順に位置の点を直線で結んだ「経路の目安」。ロボット本体の動きではない。
//   区間の種類（サーバーの kind）で描き分ける。MOVJ（関節移動）は直線移動のように見せず、再生の印も線の上を動かさない。
// - 外部から何も読み込まない。サーバー（同じ 127.0.0.1）にだけ要求を送る。
// - 画面は「説明が要らない」ことを目指す：右側には今のステップのカードだけ、目立たせる操作は1つだけ。
"use strict";
import { SpeechInput } from "/static/speech.js";
import { View3D } from "/static/view3d.js";
import { splitUnknown, displayMinus, segmentLook, segmentTitle, confirmHeading, updownStatus, updownConfirmLine, speechExamples,
  repeatCountLabel, cycleStatus } from "/static/highlight.js";

const state = {
  sessionId: null,  // サーバーのメモリにある画面の状態の番号
  view: null,       // 読み込んだときのジョブの内容（/api/load の view）
  st: null,         // サーバーの画面の状態（state）
  figView: "yz",    // 大きく出す図の向き（xy / xz / yz）
  playToken: 0,     // 再生を止めるための番号（描き直すと増やす）
  pickAgain: false, // 位置を選び直している（図で重なった点も、位置のボタンで選べるように）
  voiceState: "idle",   // 録音の状態（starting / listening / stopping / idle）
  voiceToken: null,     // 録音を始めたときのジョブ（session）と位置。違えば認識文を使わない
  voiceFinal: null,     // 最後に入力欄へ入れた認識文（直さずに送れば source=voice として記録）
  live: false,          // マイク ON（Web会議のように、一度 ON にすると続けて話せる）
  liveTimer: null,
  liveAt: 0,            // マイクを ON にした・最後に話した時刻（長く放っておいたら OFF）
  unknownWords: [],     // 最後の指示で、プログラムが読めなかった言葉（facts.unknown_words。表示で目立たせるだけ）
  needChrome: false,    // Chrome 以外で声を文字にできなかった（アドレスのコピーを目立たせる）
  talkFirst: false,     // 位置を選ばずに話す（「置くところを…」から Claude との会話を始める）
  dialogNotice: false,  // 最後の結果が会話の層の答え（会話が終わっても、止めた・聞き返した理由を会話のカードに残す）
};
const SAMPLE_NAME = "DEMO_C_PLACE.JBI";  // 「サンプルで試す」で読む架空のジョブ（サーバーが決まった1つを読む）

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
function resetForLoad() {
  voice.cancel();
  state.live = false;  // ジョブが変わったらマイクは OFF に戻す
  state.voiceFinal = null;
  state.unknownWords = [];
  // Chrome 以外の案内は、ブラウザーの確かめが通るとき（Chrome・Edge）だけ消す。Chrome 以外では残す
  if (BROWSER !== "other") { state.needChrome = false; document.body.classList.remove("need-chrome"); }
  $("voice-hint").hidden = true;
  $("voice-consent").hidden = true;
  $("text-input").value = "";
}

async function loadFile(f) {
  if (!f || recording()) return;
  resetForLoad();
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
  applyLoad(res, f.name);
}

// 読み込みの結果を画面に出す（ファイルから読んでも、サンプルでも同じ）
function applyLoad(res, fileName) {
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
  $("file-name").textContent = fileName;
  state.sessionId = res.session_id;
  state.view = res.view;
  state.st = res.state;
  state.figView = res.view.positions.some((p) => p.xyz) ? "3d" : bestView(res.view.positions);
  state.pickAgain = false;
  state.talkFirst = false;
  state.dialogNotice = false;
  $("heard-text").textContent = "（まだありません）";
  $("facts-view").textContent = "（まだありません）";
  $("reply-message").hidden = true;
  $("export-result").hidden = true;
  document.body.classList.add("loaded");
  renderAll();
  updateLocks();
}

$("file-input").addEventListener("change", (ev) => loadFile(ev.target.files[0]));

// 「サンプルで試す」：サーバーが決まった1つの架空のジョブ（samples/dummy/DEMO_C_PLACE.JBI）を読む。名前も場所も渡さない
async function loadSample() {
  if (recording()) return;
  resetForLoad();
  const res = await postJson("/api/sample", {});
  applyLoad(res, SAMPLE_NAME);
}
$("sample-button").addEventListener("click", loadSample);

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

// 上下の向きの状態（サーバーの state.updown）。無いときは何も出さない。文は highlight.js の updownStatus が作る
function renderUpdown(u) {
  const box = $("updown-status");
  const v = updownStatus(u);
  box.textContent = v ? v.text : "";
  box.title = v ? v.title : "";
  box.dataset.tone = v ? v.tone : "";
  box.hidden = !v;
  // 話し方の例：確認済みのときだけ上下の例。ほかは軸と符号の例（そのまま送れば候補になる文）
  const ex = speechExamples(u);
  $("text-input").placeholder = ex.placeholder;
  $("howto-examples").textContent = ex.howto;
  $("help-examples").replaceChildren(...ex.help.map((t) => el("li", {}, t)));
}

function renderState() {
  const st = state.st;
  if (!st) return;
  $("mock-notice").textContent = st.mock_notice;
  // 解釈のしかた（モックか本物の Claude か）を、いつも見える上の帯で区別する
  const claude = st.interpreter && st.interpreter.kind === "claude";
  $("interp-badge").textContent = claude ? `解釈：Claude（${st.interpreter.model}）` : "解釈：モック";
  $("interp-badge").classList.toggle("claude", claude);
  $("interp-badge").title = st.mock_notice;
  $("interp-badge").hidden = false;
  $("voice-position").replaceChildren(el("strong", {}, st.selected || "位置は未選択"), el("span", {}, st.selected ? "変える" : "選ぶ"));
  renderUpdown(st.updown);
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
  const ds = dialogStep();
  if (ds === "pick" || state.pickAgain) return 2;  // 会話の途中でも、位置は人が選ぶ
  if (ds === "confirm_position" || ds === "ask_move") return 3;
  if (!st.selected && !state.talkFirst) return 2;
  return 3;
}

// 会話の今の段（サーバーの state.dialog.step）。無ければ null
function dialogStep() {
  const d = state.st && state.st.dialog;
  return d && d.step ? d.step : null;
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
  renderDialog();
  // 次に押すものを1つだけ目立たせる
  for (const id of ["drop-zone", "mic-button", "voice-stop", "consent-yes", "text-send", "open-confirm", "undo-button", "export-button", "copy-url"]) {
    $(id).classList.remove("primary");
  }
  const st = state.st;
  let text = "", primary = null;
  if (step === 1) {
    text = "JOBファイルをドロップしてください";
    primary = "drop-zone";
  } else if (step === 2) {
    text = dialogStep() === "pick" ? "位置を選んでください" : "点か番号を押してください";
  } else if (step === 3 && dialogStep()) {
    // Claude との会話の途中：選択肢はどれも同じ重さ（目立たせない）。声でも答えられる
    const go = st.dialog.choices.length === 1 && st.dialog.choices[0].id === "go";
    text = dialogStep() === "confirm_position" ? "この位置で合っていますか？" : go ? "この内容で動かしますか？" : "方向と量を教えてください";
    if (recording()) primary = "voice-stop";
    else if ($("text-input").value.trim()) primary = "text-send";
    else if (!st.dialog.choices.length) primary = $("voice-controls").hidden ? "text-send" : "mic-button";
  } else if (step === 3) {
    // 聞き返しの間は、見出しも聞き返しに合わせる（選択肢があれば「どちらですか？」）
    const reply = $("reply-message");
    const asking = !reply.hidden && (reply.classList.contains("ask") || (state.dialogNotice && reply.classList.contains("stop")));
    const askText = asking && reply.querySelector("button.choice:not([disabled])") && !state.unknownWords.length
      ? "どちらですか？" : "もう一度、言い直してください";
    if (recording()) {
      text = asking ? askText : "どうぞ、話してください";
      primary = "voice-stop";
    } else if ($("text-input").value.trim()) {
      text = "文を確かめて「次へ」";
      primary = "text-send";
    } else if (asking) {  // 聞き返しの見出しは、Chrome の案内より先に出す
      text = askText;
      // 選択肢があるときは、どれも同じ重さ（目立たせるボタンを作らない）
      primary = askText === "どちらですか？" ? null
        : state.needChrome ? "copy-url" : $("voice-controls").hidden ? "text-send" : "mic-button";
    } else if (state.needChrome) {
      text = "この画面では音声を使えません";
      primary = "copy-url";
    } else if (!$("voice-consent").hidden) {
      text = "音声入力を使いますか？";
      primary = "consent-yes";
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
  renderHeard();
  resumeLive();  // マイクが ON なら、入力欄が空のステップ3で、また聞き始める
}

$("text-input").addEventListener("input", renderSteps);

// 声から入った文は、大きく・枠を付けて「聞き取った文（直せます）」と出す（聞き違いに気づけるように）
function renderHeard() {
  const heard = state.voiceFinal !== null && !!$("text-input").value.trim();
  $("heard-label").hidden = !heard;
  $("text-input").classList.toggle("heard", heard);
}

// 聞き取った文を、読めなかった言葉だけ目立たせて出す（テキストのノードで作る。文は変えない）
function heardNodes(text) {
  return splitUnknown(text, state.unknownWords).map((part) =>
    part.unknown ? el("mark", { class: "unknown", title: "この言葉は読めませんでした" }, part.text) : document.createTextNode(part.text));
}

// ---- 使い方の小窓（？）。Esc か外を押すと閉じる ----
function setHelp(open) {
  $("help-pop").hidden = !open;
  $("help-button").setAttribute("aria-expanded", String(open));
}
$("help-button").addEventListener("click", (ev) => {
  ev.stopPropagation();
  setHelp($("help-pop").hidden);
});
document.addEventListener("click", (ev) => {
  if (!$("help-pop").hidden && !ev.target.closest(".help-wrap")) setHelp(false);
});
$("voice-position").addEventListener("click", () => {
  if (recording()) return;
  state.pickAgain = true;
  renderSteps();
});
// 位置を選ばずに話す（「置くところを少し手前にして」から、Claude との会話を始める）
$("talk-first").addEventListener("click", () => {
  if (recording()) return;
  state.talkFirst = true;
  state.pickAgain = false;
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
  state.dialogNotice = !!res.dialog;
  const talking = !!(state.st && state.st.dialog && state.st.dialog.step);
  if (res.dialog && res.ok !== false && !(res.choices && res.choices.length)
      && ((talking && res.status !== "stop") || res.candidate_made)) {
    // 会話の途中の問いかけ：文は会話のカード（吹き出し）に出す。下の返事の欄には重ねて出さない。
    // 止めた理由・会話が終わったときの聞き返しは、返事の欄にも出す（見出しも「もう一度、言い直してください」）
    $("reply-message").hidden = true;
    $("reply-message").replaceChildren();
  } else if (res.ok === false) {
    showReply("stop", res.error || "理由が分かりません");
  } else if (res.status && res.status !== "ok") {
    showReply(res.status, res.message, res.choices);
  } else if (res.message && (showOk || res.dialog || res.no_change)) {
    // 会話が終わったとき（「やめて」など）・［説明です］（何も変えない。CHECKER_026 M3）も、返事の欄に出す
    showReply("ok", res.message);
  }
  renderState();
  renderRepeatRange();
}

// 回数の変更の範囲の確認（サーバーの state.repeat_range）：［この範囲を繰り返す］［違う］のボタン。声では確定しない。
// 押すと /api/repeat に、今の確認の番号（turn）と一緒に送る。番号が古ければサーバーが断る
function renderRepeatRange() {
  const rr = state.st && state.st.repeat_range;
  const box = $("reply-message");
  for (const x of box.querySelectorAll(".range-choices, .range-note")) x.remove();
  if (!rr || box.hidden) return;
  const row = el("div", { class: "choices range-choices" });
  const send = async (choice) => {
    if (recording()) return;
    for (const x of row.querySelectorAll("button")) x.disabled = true;  // 2回押しても1回だけ送る
    const res = await postJson("/api/repeat", { session_id: state.sessionId, choice: choice, turn: rr.turn });
    applyResult(res);
    if (res.candidate_made && state.st && state.st.candidate) startPlayback();
    keepHeadingInView();
  };
  const yes = el("button", { type: "button", class: "choice", id: "range-yes" }, rr.yes_label);
  const no = el("button", { type: "button", class: "choice", id: "range-no" }, rr.no_label);
  yes.addEventListener("click", () => send("yes"));
  no.addEventListener("click", () => send("no"));
  row.append(yes, no);
  box.append(el("p", { class: "fine range-note" }, rr.note), row);
  renderSteps();  // 見出し（どちらですか？）を、ボタンに合わせる
}

function showReply(status, message, choices) {
  const box = $("reply-message");
  box.className = "reply " + status;
  if (status === "ok") box.replaceChildren(el("p", {}, message));
  else if (status === "ask") {
    box.replaceChildren(friendly("ask", message));
    // 読めなかった言葉があれば、聞き取った文の中で目立たせる（言い直すときの手がかり）
    if (state.unknownWords.length && state.lastHeard) {
      box.append(el("p", { class: "heard-line" }, "聞き取った文：「", ...heardNodes(state.lastHeard), "」"));
    }
    // 短い選択肢：押すと、その文を指示として送る（同じ検査を通る。承認・出力はしない）
    if (choices && choices.length) {
      const row = el("div", { class: "choices" });
      for (const c of choices) {
        const b = el("button", { type: "button", class: "choice" }, c.label);
        b.addEventListener("click", () => {
          if (recording()) return;
          for (const x of row.querySelectorAll("button")) x.disabled = true;  // 2回押しても1回だけ送る
          sendInstruction(c.text, "text");
        });
        row.append(b);
      }
      box.append(row);
    }
  }
  else if (/上限[^。]*を超え/.test(message || "")) box.replaceChildren(friendly("limit", message));  // 移動量の上限を超えたときだけ
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
  $("voice-hint").hidden = true;
  $("text-input").value = "";
  await sendInstruction(text, source);
});

async function sendInstruction(text, source) {
  if (!state.sessionId) return;
  $("reply-message").hidden = true;  // 前の聞き返し（と選択肢）は消す。古い選択肢を後から押せないように
  $("reply-message").replaceChildren();
  const res = await postJson("/api/hear", { session_id: state.sessionId, text: text, source: source });
  $("heard-text").textContent = res.heard !== undefined ? res.heard : text;
  // 別のブランチで facts.unknown_words（読めなかった言葉）が足される。無ければ空のまま
  const uw = res.facts && res.facts.unknown_words;
  state.unknownWords = Array.isArray(uw) ? uw.filter((w) => typeof w === "string" && w) : [];
  state.lastHeard = res.heard !== undefined ? res.heard : text;
  renderFacts(res.facts);
  applyResult(res, false);
  // 候補ができたら、まず図で変更前後の経路を再生して見せる（承認は確認表示のボタンだけ）
  if (res.candidate_made && state.st && state.st.candidate) startPlayback();
  keepHeadingInView();
}

// 小さな画面：送ったあと、見出し（次にすること）が上の帯の下に隠れないようにする
function keepHeadingInView() {
  if (window.innerWidth > 600) return;
  const top = $("next-action").getBoundingClientRect().top;
  const bar = document.querySelector(".top").getBoundingClientRect().bottom;
  if (top < bar + 4) $("next-action").scrollIntoView({ block: "start" });
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

// ---- Claude との会話（吹き出し・位置の候補の根拠・選択肢）。文はすべてテキストのノードで作る（HTML の文字列は使わない） ----
// 位置の候補・根拠の行・選択肢・決まった文は、サーバーのプログラムが作ったもの。Claude の文はそのまま出さない。
// 選択肢を押すと /api/dialog に id と今の番号（turn）を送るだけ。承認・出力は呼ばない（承認は確認表示のボタンだけ）。
function estimatedLine(est) {
  return el("p", { class: "cand-est" }, `推定で選んだ位置：${est.position}（「${est.word}」・${est.label}）`);
}

function renderDialog() {
  const st = state.st;
  const d = st && st.dialog;
  const panel = $("dialog-panel");
  const step = currentStep();
  // 会話の途中は、会話のカードを見出しのすぐ下に出し、マイクの欄を小さくする（表示だけ）
  document.body.classList.toggle("in-dialog", !!(d && d.step) && step !== 4 && step !== 1);
  $("talk-first").hidden = !!(d && d.step);  // 会話の途中（手で選ぶ段など）は出さない
  // 会話の途中（ステップ2〜3）と、会話のあとに候補ができたとき（ステップ4）だけ出す
  if (!d || !d.turns.length || step === 1 || (!d.step && step !== 4 && !state.dialogNotice)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("done", !d.step);
  // 会話が終わって候補ができたあとは、最後のやり取りだけを小さく残す
  const turns = d.step ? d.turns.slice(-6) : d.turns.slice(-2);
  const mock = d.locator.kind !== "claude";
  $("dialog-engine").textContent = mock ? "モック（テスト用）・本物の Claude ではありません" : d.locator.label;
  $("dialog-engine").classList.toggle("mock", mock);
  $("dialog-turns").replaceChildren(...turns.map((t) => {
    const cls = t.who === "人" ? "human" : t.who === "アプリ" ? "app" : "ai";
    return el("li", { class: "bubble " + cls + (t.kind === "stop" ? " stop" : "") },
      el("span", { class: "who" }, t.who), el("span", { class: "said" }, t.text));
  }));
  const list = $("dialog-turns");
  list.scrollTop = list.scrollHeight;
  // 位置の候補の根拠（プログラムが命令の行から作った文）
  $("dialog-evidence").replaceChildren(...(d.step === "confirm_position" ? d.candidates : []).map((c) => {
    const tag = c.confirmed_by_frame ? "FRAME の名前（確定）" : mock ? "モックの規則による推定・未確定" : "Claude の推定・未確定";
    return el("div", { class: "ev-card" },
      el("p", { class: "ev-head" }, `「${d.word}」の候補（${tag}）`),
      el("p", { class: "ev-pos" }, c.position),
      el("ul", { class: "ev-lines" }, ...c.evidence.map((x) =>
        el("li", {}, el("code", {}, `${x.line_no}行目 ${x.text}`), x.note ? el("span", { class: "ev-note" }, `（${x.note}）`) : null))));
  }));
  $("dialog-estimated").hidden = !d.estimated || !d.step;
  $("dialog-estimated").textContent = d.estimated
    ? `推定で選んだ位置：${d.estimated.position}（「${d.estimated.word}」・${d.estimated.label}）` : "";
  // 選択肢（プログラムが作ったもの。どれも同じ重さ）
  $("dialog-choices").replaceChildren(...d.choices.map((c) => {
    const b = el("button", { type: "button", class: "choice dlg", "data-id": c.id }, c.label);
    b.disabled = recording();
    b.addEventListener("click", () => chooseDialog(c.id, d.turn));
    return b;
  }));
  $("dialog-choices").hidden = !d.choices.length;
  // 役割の注意。モックのときは、モックであることを書く
  $("dialog-note").textContent = mock
    ? "モック（テスト用）は、決まった規則で候補を出し、聞き返すだけです（本物の Claude ではありません）。座標の変更・検査・数字はプログラムが行い、承認は確認表示のボタンだけです。"
    : "Claude は意味を読んで候補を出し、聞き返すだけです。座標の変更・検査・数字はプログラムが行い、承認は確認表示のボタンだけです。";
  $("dialog-note").hidden = !d.step;
}

async function chooseDialog(id, turn) {
  if (!state.sessionId || recording()) return;
  for (const x of $("dialog-choices").querySelectorAll("button")) x.disabled = true;  // 2回押しても1回だけ送る
  const res = await postJson("/api/dialog", { session_id: state.sessionId, choice: id, turn: turn });
  applyResult(res, false);
  if (res.candidate_made && state.st && state.st.candidate) startPlayback();
  keepHeadingInView();
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
  if (c.kind === "repeat") return renderRepeatCandidate(c, host);
  const [pos, axis] = c.target.split(" ");
  const parts = [];
  if (c.blocked_reason) parts.push(friendly("blocked", c.blocked_reason));
  parts.push(el("p", { class: "cand-pos" }, pos));
  if (c.estimated) parts.push(estimatedLine(c.estimated));
  parts.push(el("p", { class: "cand-big" }, displayMinus(`${axis}方向に ${c.delta}mm`) + dirName(axis, c.delta, c.direction_names)));
  parts.push(changeLine(axis, c));
  if (c.heard) parts.push(el("p", { class: "cand-heard" }, "指示：「", ...heardNodes(c.heard), "」"));
  if (c.before_xyz) parts.push(xyzView(c));
  if (c.warnings.length) parts.push(el("ul", { class: "cand-warn" }, ...c.warnings.map((w) => el("li", {}, w))));
  host.replaceChildren(...parts);
  $("candidate-detail").replaceChildren(
    el("p", {}, `変更候補（${c.target}・ファイルの ${c.line_no}行目）。承認するまでジョブは変わりません。`),
    diffView(c),
    el("p", {}, "変わる命令の行："),
    el("ul", {}, ...c.affected.map((a) => el("li", {}, a))));
}

// 回数の変更の候補（docs/REPEAT_COUNT_DESIGN.md）：区間・「×2 → ×3」・足す行。数はサーバーの文字のまま
function renderRepeatCandidate(c, host) {
  const parts = [el("p", { class: "cand-pos" }, c.target), el("p", { class: "cand-big repeat-count" }, repeatCountLabel(c)),
    el("p", { class: "cand-change" }, `合計 ${c.before_value}回`, el("span", { class: "arrow" }, "→"), el("span", { class: "to" }, `${c.after_value}回`))];
  if (c.heard) parts.push(el("p", { class: "cand-heard" }, "指示：「", ...heardNodes(c.heard), "」"));
  if (c.warnings.length) parts.push(el("ul", { class: "cand-warn" }, ...c.warnings.map((w) => el("li", {}, w))));
  host.replaceChildren(...parts);
  $("candidate-detail").replaceChildren(
    el("p", {}, `変更候補（${c.target} の回数）。承認するまでジョブは変わりません。`),
    diffView(c),
    el("p", {}, "変わる命令の行："),
    el("ul", {}, ...c.affected.map((a) => el("li", {}, a))));
}

// 変わる値を大きく：「Y 150.000 → 147.000」（サーバーの before_value・after_value をそのまま。表示でマイナスだけ − に）
function changeLine(axis, c) {
  return el("p", { class: "cand-change" + (c.blocked_reason ? " stopped" : "") }, `${axis} ${displayMinus(c.before_value)}`,
    el("span", { class: "arrow" }, "→"), el("span", { class: "to" }, displayMinus(c.after_value)));
}

// 確認済みの方向の名前（今は Z の 上・下 だけ）。未確認の軸には名前を付けない。
// 候補の向きの名前は、候補を作ったときの照合の結果（サーバーの candidate.direction_names）から付ける（QA P2）
function dirName(axis, delta, names) {
  names = names || {};
  const n = names[axis] && names[axis][String(delta).trim().startsWith("-") ? "-" : "+"];
  return n ? `（${n}へ）` : "";
}

// 変更前後の X・Y・Z（サーバーが読んだ値）。変わる軸を強調し、確認済みの向きの名前だけを添える
function xyzView(c) {
  const axis = c.target.split(" ")[1];
  const names = c.direction_names || {};  // 候補を作ったときの照合の結果から（見出しと同じ）
  const head = (a) => {
    const n = names[a];
    return el("th", { class: a === axis ? "chg" : "" }, a, n ? el("span", { class: "dir" }, `＋${n["+"]}／−${n["-"]}`) : null);
  };
  const row = (label, xyz) => el("tr", {}, el("th", {}, label),
    ...["X", "Y", "Z"].map((a, i) => el("td", { class: a === axis ? "chg" : "" }, xyz[i])));
  const known = ["X", "Y", "Z"].filter((a) => names[a]);
  const unknown = ["X", "Y", "Z"].filter((a) => !names[a]);
  return el("div", { class: "xyz" },
    el("table", {}, el("tr", {}, el("th", {}, ""), ...["X", "Y", "Z"].map(head)),
      row("変更前", c.before_xyz), row("変更後", c.after_xyz)),
    el("p", { class: "fine" }, (known.length ? `向きの名前は確認済みの ${known.join("・")} だけ。` : "")
      + (unknown.length ? `${unknown.join("・")} の向きの名前（右・前など）は未確認のため出していません` : "")));
}

function diffView(c) {
  if (c.kind === "repeat") {  // 足す行（+）・消す行（−）だけ
    return el("div", { class: "diff" }, ...(c.diff || []).map((d) =>
      el("div", { class: d.sign === "+" ? "plus" : "minus" }, `${d.sign === "+" ? "+" : "-"} ${d.line_no}行目  ${d.text}`)));
  }
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
  const cc = st.candidate, [, ax] = cc.target.split(" ");
  // 見出しは1行（CHECKER_026 S5）：「C00005 を Y −3mm 動かします」。数はサーバーの候補のデータのまま。長い説明は下の小さな文
  $("confirm-title").textContent = confirmHeading(cc, cc.kind === "shift" ? dirName(ax, cc.delta, cc.direction_names) : "") || "確認";
  const upLine = updownConfirmLine(cc.updown);
  $("confirm-updown").textContent = upLine;
  $("confirm-updown").hidden = !upLine;
  $("confirm-text").textContent = st.confirmation_text || "";
  // 止まった理由は確認の文に入っている。入っていないときだけ、ここに添える（理由は省略しない）
  const reason = st.blocked_reason && !(st.confirmation_text || "").includes(st.blocked_reason)
    ? "（実機用の検査で止まりました：" + st.blocked_reason + "）" : "";
  $("confirm-blocked").textContent = st.blocked_reason
    ? "この変更は承認できません。閉じて「元に戻す」を押し、別の位置を選んでください" + reason : "";
  $("confirm-blocked").hidden = !st.blocked_reason;
  $("confirm-stopped-legend").hidden = !st.blocked_reason;
  // U1：承認ボタンを押す画面に、候補の注意をすべて出す（右のカードは確認表示に隠れるため）
  const notes = [...st.candidate.warnings];  // 候補の注意は全部（一部だけにしない）
  if (state.view.figure_message) notes.push(state.view.figure_message);  // 図に描けない点がある
  $("confirm-warnings").replaceChildren(...notes.map((w) => el("li", {}, w)));
  $("confirm-warnings").hidden = notes.length === 0;
  $("confirm-affected").replaceChildren(...st.candidate.affected.map((a) => el("li", {}, a)));
  $("confirm-diff").replaceChildren(diffView(st.candidate));
  $("confirm-xyz").replaceChildren(  // 位置・軸・量は見出しに出したので、ここでは変わる値から
    cc.kind === "repeat"
      ? el("p", { class: "cand-change repeat-count" }, `区間を通る回数 ×${cc.before_value}`, el("span", { class: "arrow" }, "→"),
        el("span", { class: "to" }, `×${cc.after_value}`))
      : changeLine(ax, cc),
    cc.heard ? el("p", { class: "cand-heard" }, "指示：「", ...heardNodes(cc.heard), "」",
      cc.estimated ? el("span", { class: "cand-est inline" }, `推定で選んだ位置（「${cc.estimated.word}」・${cc.estimated.label}）`) : null) : "",
    cc.before_xyz ? xyzView(cc) : "");
  $("confirm-movj-legend").hidden = !hasKind("movj");
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
  // 止まった候補の赤い帯がいちばん上に見えるように、上から出す（ボタンへ移るときにスクロールさせない）
  document.querySelector("#confirm-overlay .confirm-box").scrollTop = 0;
  if (st.can_approve) $("approver-name").focus({ preventScroll: true }); else $("confirm-close").focus({ preventScroll: true });
}

function closeConfirm() {
  const was = !$("confirm-overlay").hidden;
  $("confirm-overlay").hidden = true;
  if (was && !$("open-confirm").hidden && !$("open-confirm").disabled) $("open-confirm").focus();
}

$("open-confirm").addEventListener("click", openConfirm);
$("confirm-close").addEventListener("click", closeConfirm);
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  if (!$("help-pop").hidden) { setHelp(false); $("help-button").focus(); return; }
  if (!$("confirm-overlay").hidden) closeConfirm();
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
const C_STOPPED = "#8e8e93";  // 止まった候補（承認できない）の変更後

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
}, onView: () => { $("view-reset").hidden = $("fig-3d").hidden || !view3d.changed(); } });
const confirm3d = new View3D($("confirm-figure"), { width: 640, height: 440, hint: false });  // 確認表示の中では選び直さない

function data3d() {
  const v = state.view;
  const c = state.st && state.st.candidate && state.st.candidate.after_positions ? state.st.candidate : null;
  const rep = c && c.kind === "repeat" ? c : null;
  const rr = state.st && state.st.repeat_range;
  const blocks = v.repeat_blocks || [];
  // 回数の印：候補があれば ×2 → ×3。無ければ、読み込んだ直後から今の回数（区間が1つのときだけ）
  const repeat = rep ? { target: rep.target, positions: rep.repeat.positions, label: repeatCountLabel(rep),
    before: rep.repeat.before_count, after: rep.repeat.after_count }
    : blocks.length === 1 ? { target: blocks[0].target, positions: blocks[0].positions, label: `×${blocks[0].count}`,
      before: blocks[0].count, after: null } : null;
  return {
    points: v.positions.filter((p) => p.xyz),
    segments: rep ? rep.before_segments : v.segments,  // 回数の変更：区間の線に回の番号（cycle）が付いたもの
    repeat,
    // 範囲を確かめている間：範囲の線（区間に入る最初の線を除く）を光らせる
    rangeLines: rr ? rr.block_line_nos.slice(1) : [],
    selected: state.st ? state.st.selected : null,
    after: c ? { points: c.after_positions.filter((p) => p.xyz), segments: c.after_segments, moved: c.moved } : null,
    delta_label: c && !rep ? `${c.target.split(" ")[1]} ${c.delta}mm` : "",
    stopped: !!(c && c.blocked_reason),  // 止まった候補：変更後をオレンジにしない
    blocked: Object.fromEntries(v.positions.filter((p) => !p.shift_check.ok).map((p) => [p.name, p.shift_check.reason])),
    // Claude（モック）が出した位置の候補（確かめている間だけ光らせる。選んではいない）
    candidates: dialogStep() === "confirm_position" ? state.st.dialog.candidates.map((x) => x.position)
      : rr ? rr.positions : [],  // 回数の変更の範囲の点も、同じ輪で光らせる（選んではいない）
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
  $("view-reset").hidden = !is3d || !view3d.changed();  // 回した・拡大したときだけ出す
  if (is3d) view3d.setData(data3d());
  else drawFigure($("fig-main"), ...VIEWS[state.figView === "3d" ? bestView(state.view.positions) : state.figView], true);
  drawFigure($("fig-xy"), "X", "Y", false);
  drawFigure($("fig-xz"), "X", "Z", false);
  drawFigure($("fig-yz"), "Y", "Z", false);
  const c = state.st && state.st.candidate;
  $("legend").hidden = !(c && c.after_positions);
  // 止まった候補：変更後の線は灰色の点線。凡例もそれに合わせる
  $("legend-after").hidden = !!(c && c.blocked_reason);
  $("legend-stopped").hidden = !(c && c.blocked_reason);
  // 区間の種類の凡例（MOVJ・目安の区間は、そのジョブにあるときだけ）
  $("legend-kinds").hidden = !hasKind("movl") && !hasKind("movj") && !hasKind("other");
  $("legend-movj").hidden = !hasKind("movj");
  $("legend-other").hidden = !hasKind("other");
  $("play-button").disabled = !state.view || state.view.segments.length === 0 || recording();
}

// 今の図（変更前と、変更候補の変更後）に、その種類の区間があるか。種類が分からない区間は "other" として数える
function hasKind(kind) {
  const c = state.st && state.st.candidate;
  const segs = (state.view ? state.view.segments : []).concat(c && c.after_segments ? c.after_segments : []);
  return segs.some((s) => segmentLook(s.kind).kind === kind);
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
      // 区間の種類で描き分ける（MOVJ は細い灰色の点線。変更後の線は変更後の色で、点線の形だけ残す）
      const look = segmentLook(s.kind);
      const lw = width * look.width;
      const line = svgEl("line", { x1: sx(p.xyz[a]), y1: sy(p.xyz[b]), x2: sx(q.xyz[a]), y2: sy(q.xyz[b]),
        stroke: look.color && key === "before" ? look.color : color, "stroke-width": lw, "stroke-linecap": "round",
        class: `seg ${key} seg-${look.kind}`, "data-kind": look.kind });
      if (look.dash) line.setAttribute("stroke-dasharray", look.dash);
      const t = svgEl("title", {});
      t.textContent = segmentTitle(s);
      line.append(t);
      svg.append(line);
      segLines[key].push({ seg: s, look, line: line, width: lw, from: [sx(p.xyz[a]), sy(p.xyz[b])], to: [sx(q.xyz[a]), sy(q.xyz[b])] });
    }
  };

  const w = big ? 2.5 : 2;
  const stopped = !!(c && c.blocked_reason);
  const cAfter = stopped ? C_STOPPED : C_AFTER;
  if (c) {
    drawSegs(c.before_segments || v.segments, before, C_BEFORE, w, "before");        // 変更前（灰色）。回数の変更は回の番号つき
    // 変更後は、動かす点に触れる区間だけを見せる（オレンジ。止まった候補は灰色の点線）。
    // ほかの区間は変更前と同じ線なので見せない（再生の印が通れるように、線は透明で置いておく）
    drawSegs(c.after_segments, after, cAfter, w + 0.5, "after");
    for (const x of segLines.after) {
      if (x.seg.from !== c.moved && x.seg.to !== c.moved) x.line.setAttribute("stroke", "transparent");
      else if (stopped) { x.line.setAttribute("stroke-dasharray", "0.5 7"); x.line.setAttribute("stroke-width", "4"); }  // 目安の破線と見分ける
    }
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
    const g = svgEl("g", { class: "pt" + (sel ? " sel" : ""), "data-name": p.name });
    g.append(svgEl("circle", { cx: x, cy: y, r: big ? 18 : 8, fill: "transparent" }));  // 押しやすい大きさ
    if (sel) g.append(svgEl("circle", { cx: x, cy: y, r: big ? 14 : 7, fill: "rgba(10,132,255,0.15)", stroke: C_SEL, "stroke-width": 2 }));
    g.append(svgEl("circle", { cx: x, cy: y, r: big ? 6 : 4, fill: moved ? C_BEFORE : (sel ? C_SEL : C_POINT) }));
    if (!moved) {  // 動かす点の名前は、変更後の点の横に1つだけ出す
      const label = svgEl("text", { x: x + 12, y: placeLabel(x, y, p.name.length * 8),
        fill: sel ? C_SEL : "#6e6e73", "font-weight": sel ? "600" : "400" });
      label.textContent = p.name;
      g.append(label);
    }
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
      svg.append(svgEl("circle", { cx: x, cy: y, r: big ? 7 : 4, fill: cAfter }));
      const text = `${q.name} ${displayMinus(`${c.target.split(" ")[1]} ${c.delta}mm`)}`;
      const label = svgEl("text", { x: x + 12, y: placeLabel(x, y, text.length * 8), fill: stopped ? C_STOPPED : "#c4470a", "font-weight": "600" });
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
  const marker = svgEl("circle", { r: 9, fill: runs[0].color, stroke: "#fff", "stroke-width": 3, class: "marker" });
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
      s.line.setAttribute("stroke-width", String(s.look.animate ? 5 : Math.max(s.width + 1.5, 3)));
      $("play-status").textContent = playStatus(run.name, si + 1, run.list.length, s.look, s.seg);
      $("play-status").title = segmentTitle(s.seg);
      marker.setAttribute("fill", run.color);
    }
    const len = Math.hypot(s.to[0] - s.from[0], s.to[1] - s.from[1]);
    const dur = s.look.animate ? Math.max(350, (len / SPEED) * 1000) : HOLD_MS;
    const f = Math.min(1, (now - t0) / dur);
    if (s.look.animate) {
      marker.removeAttribute("visibility");
      marker.setAttribute("cx", s.from[0] + (s.to[0] - s.from[0]) * f);
      marker.setAttribute("cy", s.from[1] + (s.to[1] - s.from[1]) * f);
    } else {
      // MOVJ・目安の区間：印を線の上で動かさない。区間の間は印を消し、終わったら終点に置く
      marker.setAttribute("visibility", "hidden");
      if (f >= 1) { marker.setAttribute("cx", s.to[0]); marker.setAttribute("cy", s.to[1]); }
    }
    if (f >= 1) {
      s.line.setAttribute("stroke-width", String(s.width));
      si++; t0 = null;
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// 再生の状態の文。MOVJ・目安の区間では、印が線の上を動かないことを添える
const HOLD_MS = 700;  // MOVJ・目安の区間で、印を消しておく時間（画面の上だけ。実際の動きとは関係ない）
function playStatus(name, i, n, look, seg) {
  // 回数の変更の候補では、区間の線ごとに「2回目」と数える（サーバーの seg.cycle）
  const c = state.st && state.st.candidate;
  const cyc = c && c.kind === "repeat" ? cycleStatus(seg, c.target) : "";
  const head = `${name}を再生中 ${i}/${n}` + (cyc ? `　${cyc}` : "");
  if (look.kind === "movj") return `${head}　MOVJ：関節移動（点線は順番だけ。実際の通り道ではありません）`;
  if (look.kind === "other") return `${head}　直線とは限らない区間（印は終点へ移ります）`;
  return head;
}

// 3D の再生：命令の順（変更前 → 変更後）に、MOVL の区間だけ線の上を印が動く（ロボットの動きではない）
function startPlayback3d() {
  const d = data3d();
  const runs = [{ name: d.after ? "変更前" : "命令の順", color: d.after ? "#6e6e73" : C_POINT, segments: d.segments, points: d.points }];
  if (d.after) runs.push({ name: "変更後", color: C_AFTER, segments: d.after.segments, points: d.after.points });
  $("play-button").classList.add("playing");
  view3d.play(runs,
    (run, s) => {
      $("play-status").textContent = playStatus(run.name, run.segments.indexOf(s) + 1, run.segments.length, segmentLook(s.kind), s);
      $("play-status").title = segmentTitle(s);  // 命令の行は、マウスを重ねたときだけ
    },
    () => { $("play-status").textContent = "再生が終わりました"; $("play-button").classList.remove("playing"); });
}

$("play-button").addEventListener("click", startPlayback);

// ---- 音声入力（speech.js）。認識した文は入力欄に入れるだけ。承認・出力・送信は呼ばない ----
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

// ブラウザーの種類。音声を文字にする処理は、Google Chrome は Google の、Edge は Microsoft のサーバーで行う。
// アプリの中のブラウザー（ChatGPT のアプリの中など）は、その処理を使えず「通信に失敗」になることがある（2026-11-03 に調べた）
const BRANDS = ((navigator.userAgentData && navigator.userAgentData.brands) || []).map((b) => b.brand);
const BROWSER = BRANDS.includes("Google Chrome") ? "chrome" : BRANDS.includes("Microsoft Edge") ? "edge" : "other";
const PROVIDER = { chrome: "Chrome が音声を Google のサーバーへ送って", edge: "Edge が音声を Microsoft のサーバーへ送って",
  other: "ブラウザーが音声を提供元のサーバーへ送って" }[BROWSER];
const MODE_KEY = "vt-voice-mode";  // 同意した方式（このブラウザーにだけ覚える。音声そのものは保存しない）

// speech.js の失敗の文（固定）から、失敗の種類を知る（speech.js は変えない）
const FAILS = [
  ["network", "音声認識の通信に失敗"],
  ["no-speech", "声を聞き取れませんでした"],
  ["aborted", "音声入力が中断されました"],
  ["not-allowed", "マイクの利用が許可されていません"],
  ["audio-capture", "マイクを利用できません"],
  ["language", "日本語の音声認識を利用できません"],
];
const failKind = (message) => (FAILS.find(([, head]) => (message || "").startsWith(head)) || ["other"])[0];

// 失敗したときに出す、次にすることの短い文
function failText(kind, message) {
  if (kind === "network") {
    return BROWSER === "other"
      ? "このブラウザーでは、声を文字にできませんでした。Google Chrome で開いてください（下のボタンでアドレスをコピー）"
      : "声を文字にする通信がつながりません。インターネットの接続を確かめて、もう一度 🎤";
  }
  if (kind === "not-allowed") {
    return BROWSER === "other"
      ? "このブラウザーではマイクを使えません。Google Chrome で開いてください"
      : "マイクが許可されていません。アドレス欄の左のマークから、マイクを「許可」にして、もう一度 🎤";
  }
  if (kind === "audio-capture") return "マイクが見つかりません。接続を確かめるか、Teams・Zoom を閉じて、もう一度 🎤";
  if (kind === "language") return "端末内のみでは日本語を使えません。詳細表示で「ブラウザー標準」にするか、文字で入力してください";
  return message;
}

// Web会議のように、マイクを一度 ON（state.live）にすると、続けて話せる：
// 話し終わると認識文が入力欄に入り、録音は一時停止する（確かめて直してから「次へ」を押す）。
// 結果が出て入力欄が空に戻り、ステップ3（音声で指示）にいれば、また聞き始める。
// 声が無いまま止まったときは、マイク ON のまま聞き直す。通信の失敗は1回だけ聞き直す。
// それ以外の失敗は、マイクを OFF にして、次にすることと文字の入力へ案内する。承認・出力・送信はここから呼ばない。
state.netRetry = 0;
state.quiet = 0;       // 声が無いまま止まって、続けて聞き直した回数
const QUIET_MAX = 20;  // これを超えたら OFF にする（すぐに止まる失敗が続いても、聞き直しを繰り返さない）
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
    state.netRetry = 0;
    state.quiet = 0;
    state.needChrome = false;  // 声を文字にできた：Chrome の案内は要らない
    document.body.classList.remove("need-chrome");
    renderSteps();
    renderMic();
    renderSignHint();
  },
  error: (message) => {
    state.voiceToken = null;
    const kind = failKind(message);
    // 声が無いまま止まった：マイク ON のまま、また聞く（長く使わなければ resumeLive が OFF にする）
    const empty = !$("text-input").value.trim();
    if (state.live && (kind === "no-speech" || kind === "aborted") && state.quiet < QUIET_MAX) {
      state.quiet += 1;
      state.resumeArmed = empty;  // 入力欄に文があれば一時停止のまま（「次へ」のあとに聞く）
      resumeLive();
      return;
    }
    // 通信の失敗：Chrome・Edge なら1回だけ聞き直す
    if (state.live && kind === "network" && BROWSER !== "other" && state.netRetry < 1) {
      state.netRetry += 1;
      state.resumeArmed = empty;
      if (empty) $("voice-status").textContent = "つながりにくいので、もう一度聞いています…";
      resumeLive();
      return;
    }
    state.quiet = 0;
    state.live = false;  // それ以外は OFF にして、次にすることと文字の入力へ案内する
    state.netRetry = 0;
    showVoiceError(failText(kind, message));  // 固定の文。入力欄は変えない
    $("browser-note").hidden = BROWSER !== "other";
    // Chrome 以外で声を文字にできない：案内の文は1回だけ（上の文）にして、「アドレスをコピー」だけを目立たせる
    state.needChrome = BROWSER === "other" && (kind === "network" || kind === "not-allowed");
    document.body.classList.toggle("need-chrome", state.needChrome);
    renderMic();
    renderSteps();
    offerLocalInstall();
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

// 認識で「マイナス」が落ちることがある（実マイクで「Xマイナス方向に5ミリ」→「X 方向に 5mm」）。
// 声から入った文に、軸があって向き（＋・−）が無ければ、送る前に知らせる（向きの無い軸は＋として読まれるため）
function lacksSign(text) {
  const t = text.normalize("NFKC").toUpperCase()
    .replace(/(?<![ァ-ヺー])(エックス|ワイ|ゼット|ズィー)(?=\s?(の|プラス|マイナス|PLUS|MINUS|[+\-]|方向|軸))/g, (m) => ({ "エックス": "X", "ワイ": "Y" }[m] || "Z"));
  return /(^|[^A-Z])[XYZ]([^A-Z]|$)/.test(t) && /\d/.test(t) && !/[+\-−]|プラス|マイナス|PLUS|MINUS|上げ|下げ|上に|下に|上へ|下へ|上方向|下方向|増や|減ら|大きく|小さく/.test(t);
}

function renderSignHint() {
  const t = $("text-input").value;
  $("voice-hint").hidden = !(state.voiceFinal !== null && t && lacksSign(t));
}

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
  if (rec) $("mic-label").replaceChildren(el("span", { class: "dot", "aria-hidden": "true" }, "●"), "聞いています");
  else $("mic-label").textContent = state.live ? "一時停止中" : "マイク OFF";
  // 長い説明は、小さな文字の行に分ける
  $("mic-hint").textContent = !rec && state.live ? "マイクは ON のまま。「次へ」で送ると、また聞きます" : "";
  $("mic-hint").hidden = !$("mic-hint").textContent;
}

function startListening() {
  const mode = voiceMode();
  if (!state.sessionId || !mode || recording()) return;
  // 新しい録音：前の認識文・エラーは使わない。直さずに残っている前の認識文は入力欄から消す
  if (state.voiceFinal !== null && $("text-input").value === state.voiceFinal) $("text-input").value = "";
  state.voiceFinal = null;
  $("voice-error").hidden = true;
  $("voice-hint").hidden = true;
  $("voice-partial").textContent = "";
  if (BROWSER !== "other") { state.needChrome = false; document.body.classList.remove("need-chrome"); }
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
    if ($("text-input").value.trim()) {  // 入力欄に文がある：聞き直す約束を取り消す（消しても勝手に聞き始めない）
      state.resumeArmed = false;
      return;
    }
    if (!state.live || !state.resumeArmed || recording() || !Recognition || !voiceMode() || currentStep() !== 3
        || !$("confirm-overlay").hidden || $("instruction-panel").hidden) return;
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
  $("voice-mode-reset").disabled = rec;
  $("voice-position").disabled = rec;
  $("mic-button").disabled = rec || !loaded || !Recognition;
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
// 前に同意した方式があれば、それを使う（無ければ、初めて 🎤 を押したときに聞く）
try {
  const saved = localStorage.getItem(MODE_KEY);
  if (saved === "browser" || saved === "local") $("voice-mode-" + saved).checked = true;
} catch (_) { /* 覚えられないブラウザーでは、毎回聞く */ }
for (const id of ["voice-mode-browser", "voice-mode-local"]) {
  $(id).addEventListener("change", () => {
    try { localStorage.setItem(MODE_KEY, voiceMode()); } catch (_) { /* 覚えられなくても使える */ }
    state.live = false;
    $("voice-error").hidden = true;
    updateLocks();
    offerLocalInstall();
  });
}
$("text-input").addEventListener("input", () => { renderMic(); renderSignHint(); });
$("browser-note").hidden = BROWSER !== "other" || !Recognition;
$("browser-info").textContent = "このブラウザー：" + (BRANDS.join("・") || "不明") + "（" + navigator.userAgent + "）";
$("consent-text").textContent = "声を文字にするため、" + PROVIDER + "文字にします。このアプリは音声を保存しません。あとで詳細表示から変えられます。";

function micOn() {
  state.live = true;
  state.liveAt = Date.now();
  state.netRetry = 0;
  state.quiet = 0;
  startListening();
  renderMic();
}

$("mic-button").addEventListener("click", () => {
  if (!state.sessionId || recording()) return;
  if (state.live) {  // 一時停止中に押すと OFF
    state.live = false;
    updateLocks();
    return;
  }
  if (!voiceMode()) {  // 初めて：音声をどこで文字にするかを伝えて、同意をもらう（勝手に送らない）
    $("voice-consent").hidden = false;
    renderSteps();
    $("consent-yes").focus();
    return;
  }
  micOn();
});
$("consent-yes").addEventListener("click", () => {
  $("voice-mode-browser").checked = true;
  try { localStorage.setItem(MODE_KEY, "browser"); } catch (_) { /* 覚えられなくても使える */ }
  $("voice-consent").hidden = true;
  micOn();
});
$("voice-mode-reset").addEventListener("click", () => {
  if (recording()) return;
  state.live = false;
  for (const id of ["voice-mode-browser", "voice-mode-local"]) $(id).checked = false;
  try { localStorage.removeItem(MODE_KEY); } catch (_) { /* 覚えていなければ何もしない */ }
  $("voice-status").textContent = "方式を選び直します。次に 🎤 を押したときに聞きます";
  updateLocks();
});
$("consent-no").addEventListener("click", () => {
  $("voice-consent").hidden = true;
  renderSteps();
  $("text-input").focus();
});
$("copy-url").addEventListener("click", async () => {
  const url = location.origin + "/";
  try {
    await navigator.clipboard.writeText(url);
    $("voice-status").textContent = "コピーしました。Chrome のアドレス欄に貼り付けてください";
  } catch (_) {
    $("voice-status").textContent = "Chrome で " + url + " を開いてください";
  }
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
