// ボイスティーチング（シミュレーションモード）の画面。4ステップ：読込 → 位置を選ぶ → 音声で指示 → 確かめて承認。
// - JBI の解析・変更候補・承認・出力はサーバー側で行う。ここでは JBI を解析しない。
// - 指示は文字入力の欄（入口は1つ）から /api/talk へ送る。音声（speech.js）は認識した文を入力欄に入れるだけで、自動では送らない。
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
import { Player } from "/static/player.js";
import { splitUnknown, displayMinus, segmentLook, segmentTitle, confirmHeading, updownStatus, updownConfirmLine, updownDetail,
  speechExamples, repeatCountLabel, cycleStatus, nowPlaying, nowText, sequenceChips, tallyParts, rephraseExamples,
  nextStep, attachTips } from "/static/highlight.js";

const state = {
  sessionId: null,  // サーバーのメモリにある画面の状態の番号
  view: null,       // 読み込んだときのジョブの内容（/api/load の view）
  st: null,         // サーバーの画面の状態（state）
  figView: "yz",    // 大きく出す図の向き（xy / xz / yz）
  playToken: 0,     // 再生を止めるための番号（描き直すと増やす）
  player: null,     // 今の再生（player.js。3D は view3d.player、2D は player2d）
  player2d: null,
  playWhich: "both",  // 再生しているもの（both：変更前 → 変更後・before・after）
  tally: { run: "", max: 0 },  // 比較の再生で、今の再生が通った区間の回（「1・2・3」）
  pickAgain: false, // 位置を選び直している（図で重なった点も、位置のボタンで選べるように）
  voiceState: "idle",   // 録音の状態（starting / listening / stopping / idle）
  voiceToken: null,     // 録音を始めたときのジョブ（session）と位置。違えば認識文を使わない
  voiceFinal: null,     // 最後に入力欄へ入れた認識文（直さずに送れば source=voice として記録）
  live: false,          // マイク ON（1回の発話の間だけ。話し終わると OFF）
  unknownWords: [],     // 最後の指示で、プログラムが読めなかった言葉（facts.unknown_words。表示で目立たせるだけ）
  needChrome: false,    // Chrome 以外で声を文字にできなかった（アドレスのコピーを目立たせる）
  talkFirst: false,     // 位置を選ばずに話す（「置くところを…」から Claude との会話を始める）
  dialogNotice: false,  // 最後の結果が会話の層の答え（会話が終わっても、止めた・聞き返した理由を会話のカードに残す）
  convo: false,         // 会話を始めた（最初に送った）。false の間は ① 初期の画面（大きなマイク）。見せ方だけに使う
  sending: false,       // 答えを待っている（「考えています」）。その間は送らず、書いた文は入力欄に残す（11/5 F3）
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
      // 前の画面の番号を見出しで渡す。サーバーが読むのは読込（サンプル）のときだけ（同じ中身なら人が教えた意味を引き継ぐ）
      headers: previousHeader({ "Content-Type": "application/json" }),
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

// 別の JOB を読み込めない間：録音中と、答えを待っている間（「考えています」）。待っている間に読み込むと、
// 前の JOB の答え（補足・3D の光）が新しい JOB の会話に出てしまう（11/5 G2）。答えが来れば、また読み込める
function loadLocked() {
  return recording() || state.sending;
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

// 前に見ていた画面の番号（同じ中身を読み直したとき、サーバーが「人が教えた意味」を引き継ぐか決める。中身で決める）
function previousHeader(h) {
  return state.sessionId ? { ...h, "X-VT-Previous-Session": state.sessionId } : h;
}

async function loadFile(f) {
  if (!f || loadLocked()) return;
  resetForLoad();
  const bytes = new Uint8Array(await f.arrayBuffer());
  let res;
  try {
    const r = await fetch("/api/load", {
      method: "POST",
      headers: previousHeader({ "Content-Type": "application/octet-stream" }),
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
  announceSession();
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
  state.convo = false;  // 別のデータを読んだ：① 初期の画面に戻る（会話の履歴は explain.js が消す）
  setKeyboard(!Recognition);  // 音声を使えないブラウザーでは、はじめから入力欄を出す
  $("heard-text").textContent = "（まだありません）";
  $("facts-view").textContent = "（まだありません）";
  $("reply-message").hidden = true;
  $("export-result").hidden = true;
  document.body.classList.add("loaded");
  renderAll();
  updateLocks();
  announceSession();
}

// 「Claude に聞く（読み取りだけ）」の欄（explain.js）に、今の画面の番号を知らせる。欄は答えを表示するだけ
function announceSession() {
  window.dispatchEvent(new CustomEvent("vt:session", { detail: { sessionId: state.sessionId } }));
}
// 会話をやり直した（explain.js）：候補・範囲の確認・会話の途中が無ければ ① 初期の画面に戻る（見せ方だけ）
window.addEventListener("vt:talk-reset", () => {
  state.convo = false;
  const st = state.st;
  if (st && !st.candidate && !st.repeat_range && !dialogStep()) clearReply();  // 範囲の確認・候補の途中は消さない（そのまま ② に残る）
  if (st) renderSteps();
});
// 根拠の行を押したとき：その位置を図で光らせる（選択ではない）
window.addEventListener("vt:highlight", () => { if (state.view) renderFigures(); });

// 選んだファイルを受け取ったら欄を空にする。欄に前のファイルが残ると、同じファイルを選び直しても change が出ない（11/5 G5）
$("file-input").addEventListener("change", (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";
  loadFile(f);
});

// 「サンプルで試す」：サーバーが決まった1つの架空のジョブ（samples/dummy/DEMO_C_PLACE.JBI）を読む。名前も場所も渡さない
async function loadSample() {
  if (loadLocked()) return;
  resetForLoad();
  const res = await postJson("/api/sample", {});
  applyLoad(res, SAMPLE_NAME);
}
$("sample-button").addEventListener("click", loadSample);

const drop = $("drop-zone");
drop.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  if (!loadLocked()) drop.classList.add("dragover");
});
drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
drop.addEventListener("drop", (ev) => {
  ev.preventDefault();
  drop.classList.remove("dragover");
  const f = ev.dataTransfer && ev.dataTransfer.files[0];
  if (f) loadFile(f);
});
drop.addEventListener("click", (ev) => {
  if (ev.target.closest("label, input") || loadLocked()) return;  // ラベルは自分でファイル選択を開く
  $("file-input").click();
});
drop.addEventListener("keydown", (ev) => {
  if ((ev.key === "Enter" || ev.key === " ") && !loadLocked()) { ev.preventDefault(); $("file-input").click(); }
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

// 実機の JOB の承認のあとの案内（CHECKER_030 S30-3。唐川さんの決定 2026-11-03 17:14）
const UNDO_HINT = "もう一度直すときは［元に戻す］を押してください";

// 上下の向きの状態（サーバーの state.updown）。無いときは何も出さない。文は highlight.js の updownStatus が作る
function renderUpdown(u) {
  const box = $("updown-status");
  const v = updownStatus(u);
  box.textContent = v ? v.text : "";
  box.title = v ? v.title : "";
  box.dataset.tone = v ? v.tone : "";
  box.hidden = !v;
  // ロボット名と確認した人・日時の1行（CHECKER_030 S30-1）。図の欄の上に、マウスを重ねなくても見えるように出す
  const detail = $("updown-detail");
  detail.textContent = updownDetail(u);
  detail.dataset.tone = v ? v.tone : "";
  detail.hidden = !detail.textContent;
  // 話し方の例：確認済みのときだけ上下の例。ほかは軸と符号の例（そのまま送れば候補になる文）
  const ex = speechExamples(u);
  $("text-input").placeholder = ex.placeholder;
  $("howto-examples").textContent = ex.howto;
  $("help-examples").replaceChildren(...ex.help.map((t) => el("li", {}, t)));
  // 入力欄の横の ⓘ の例（同じ例）。押すと欄に入るだけ（送るのは人が［送る］で）
  $("input-examples").replaceChildren(...ex.help.map((t) => {
    const b = el("button", { type: "button", class: "example-fill" }, t);
    b.addEventListener("click", () => { fillInput(t.replace(/^「|」$/g, "")); setExamples(false); });
    return el("li", {}, b);
  }));
}

// 例・言い換えの例を、入力欄に入れるだけ（自動では送らない。声から入った文ではないので source は text）
function fillInput(text) {
  if (recording() || $("text-input").disabled) return;
  state.voiceFinal = null;
  $("text-input").value = text;
  setKeyboard(true);
  $("voice-hint").hidden = true;
  renderSteps();
  renderMic();
  $("text-input").focus({ preventScroll: true });
}

function renderState() {
  const st = state.st;
  if (!st) return;
  // 状態の1行（CHECKER_033 P1-A・唐川さんの方針①）：サーバーがプログラムの確定情報だけから作った文をそのまま出す
  $("status-line").hidden = !st.status_line;
  renderStatusChips(st.status_line || "");
  // 「Claude に聞く」の欄に、データ（候補・承認）が変わったかもしれないことを知らせる（欄がサーバーに確かめ直す）
  const c = st.candidate;
  window.dispatchEvent(new CustomEvent("vt:state", { detail: { sessionId: state.sessionId,
    key: JSON.stringify([c ? c.kind : null, c ? c.target : null, c ? c.after_value : null, st.approval ? st.approval.approved_at : null]) } }));
  $("mock-notice").textContent = st.mock_notice;
  // 解釈のしかた（モックか本物の Claude か）を、いつも見える上の帯で区別する
  const claude = st.interpreter && st.interpreter.kind === "claude";
  $("interp-badge").textContent = claude ? `解釈：Claude（${st.interpreter.model}）` : "解釈：モック";
  $("interp-badge").classList.toggle("claude", claude);
  $("interp-badge").title = st.mock_notice;
  $("interp-badge").hidden = false;
  $("voice-position").replaceChildren(el("strong", {}, st.selected || "位置は未選択"), el("span", {}, st.selected ? "変える" : "選ぶ"));
  renderSelLine(st.selected);
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

// 状態の1行を、上の帯の小さなチップにする（唐川さん承認のデザイン 11/4）。文はサーバーの文のまま（区切りの「／」も文字として残す）。
// 形が違う（「：」「／」で分けられない）ときは、そのまま1行で出す。色はチップの文字（済み・あり・参考の表示だけ）から付けるだけ
function renderStatusChips(text) {
  const box = $("status-line");
  const at = text.indexOf("：");
  const parts = at >= 0 ? text.slice(at + 1).split("／") : [];
  if (at < 0 || parts.length < 2 || parts.some((p) => !p)) { box.textContent = text; return; }
  const tone = (p) => (/参考の表示だけ|承認できません/.test(p) ? " stop" : /済み/.test(p) ? " done" : /確認中/.test(p) ? " range" : /あり/.test(p) ? " on" : "");
  const nodes = [el("span", { class: "st-head" }, text.slice(0, at + 1))];
  parts.forEach((p, i) => {
    if (i) nodes.push(el("span", { class: "st-sep", "aria-hidden": "true" }, "／"));
    nodes.push(el("span", { class: "st-chip" + tone(p) }, p));
  });
  box.replaceChildren(...nodes);
}

// 3D の下の「選択中 C00004　X 450.000　Y 200.000　Z 300.000 mm」。位置を選んでいるときだけ。
// 値はサーバーが読んだファイルの行（p.line の「=」のあとの最初の3つ）のまま。ロボット座標で描ける位置（p.xyz がある）だけ数を出す
function renderSelLine(name) {
  const box = $("sel-pos");
  const p = name && state.view ? state.view.positions.find((q) => q.name === name) : null;
  if (!p) { box.hidden = true; box.replaceChildren(); return; }
  const vals = p.xyz && typeof p.line === "string" && p.line.includes("=") ? p.line.slice(p.line.indexOf("=") + 1).split(",").slice(0, 3).map((s) => s.trim()) : [];
  // 候補・承認のあとも、ここの値はファイルの行（変更前）のまま。変更後と読み違えないよう「変更前」と付ける（Safety P2-2。計算は足さない）
  const before = !!(state.st && state.st.candidate);
  const nodes = [el("span", { class: "sel-head" }, "選択中"), el("span", { class: "sel-name" }, p.name)];
  if (before) nodes.push(el("span", { class: "sel-before" }, "変更前"));
  if (vals.length === 3 && vals.every((v) => /^[+-]?\d+(\.\d+)?$/.test(v))) {
    ["X", "Y", "Z"].forEach((a, i) => nodes.push(el("span", { class: "sel-ax" }, a), displayMinus(vals[i])));
    nodes.push(el("span", { class: "sel-ax" }, "mm"));
  }
  box.replaceChildren(...nodes);
  box.title = p.line || "";
  box.hidden = false;
}

// ---- 今のステップと「次にすること」 ----
function currentStep() {
  const st = state.st;
  if (!state.sessionId || !st) return 1;
  if (st.candidate) return 4;
  if (st.repeat_range) return 3;  // 範囲の確認の途中（会話の欄から来ても）：範囲のボタンを出す（Step F）
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
  $("opening-replay").hidden = !state.sessionId;  // オープニングの出し直しは、JOB を読み込んだあと（最初の画面のボタンを増やさない）
  const step = currentStep();
  for (let i = 1; i <= 4; i++) {
    const li = $("step-" + i);
    li.classList.toggle("done", i < step);
    li.classList.toggle("current", i === step);
    if (i === step) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  }
  // 右側には今のステップのカードを出す。入口（指示の欄）は1つなので、JOB を読み込んだらいつも出す（11/4 唐川さんの指示③）：
  // 位置を選ぶ前の「何をしている？」も同じ欄から送る（振り分けはサーバー）。ステップ4（候補・承認）では今までどおり出さない
  // （録音しながら承認・出力できないように）
  const cards = { 2: "select-panel", 3: "instruction-panel", 4: "candidate-panel" };
  for (const [n, id] of Object.entries(cards)) {
    // ステップ4は、承認のあとだけ入口（文字の欄だけ）を出す（承認のあとの説明の質問のため）
    const approvedNow = !!(state.st && state.st.approval);
    $(id).hidden = id === "instruction-panel" ? !(step === 2 || step === 3 || (step === 4 && approvedNow)) : Number(n) !== step;
  }
  // 置く場所だけ（UI 修正 11/4）：指示の結果の欄は中の段に、候補のボタンは下の帯に移した。
  // 出す・隠すは元のカードと同じにする（指示の結果＝指示の欄と同じ、候補のボタン＝「確かめて承認」のカードと同じ）
  $("reply-panel").hidden = $("instruction-panel").hidden;
  $("dock-actions").hidden = $("candidate-panel").hidden;
  // ステップ4では、マイク（録音）を出さない・押せない（録音と承認・出力が重ならない。今までの決まり）。承認のあとは文字の欄だけ
  $("voice-input-slot").hidden = step === 4;
  if (step === 4) $("mic-button").disabled = true;
  renderDialog();
  // 次に押すものを1つだけ目立たせる
  for (const id of ["drop-zone", "mic-button", "voice-stop", "consent-yes", "text-send", "open-confirm", "undo-button", "export-button", "copy-url"]) {
    $(id).classList.remove("primary");
  }
  const st = state.st;
  let text = "", primary = null, hint = "";
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
  } else if (st.exportable === false) {
    // 実機の JOB（合成ジョブでない）：承認しても出力しない。出力を促さない（内部 QA の再レビュー P1-1）
    text = "承認しました（変更案の確認のみ。実機用JOBは出力できません）";
    primary = null;  // 出力も、捨てる操作も勧めない（内部 QA N-1）
    hint = UNDO_HINT;  // 次の操作の案内を1行（CHECKER_030 S30-3）。「元に戻す」は目立たせない（primary にしない）
  } else if (!st.exported) {
    text = "承認しました。出力してください";
    primary = "export-button";
  } else {
    text = "出力しました";
  }
  if (step === 3 && st && st.repeat_range && !recording() && !dialogStep()) {
    // 回数の変更の範囲の確認：画面の上の目立たせるボタンは作らない。範囲の欄の中だけ、［この範囲を繰り返す］を
    // 目立たせる（唐川さんの決定 2026-11-03 夜の例外。承認ボタンとは色・文字・注記で見分ける。style.css の .range-go）
    text = st.repeat_range.heading;
    primary = null;
  }
  // ① 初期（会話の前）：ふだんの案内（点か番号を押す・🎤 を押す・書いて送る）の代わりに、大きな見出しを1つだけ出す。
  // 同意・録音中・聞き取った文の確かめ・Chrome の案内などは、そのまま出す（目立たせるボタンの決まりは変えない）
  const hero = isHero();
  if (hero && HERO_REPLACES.includes(text)) text = HERO_TITLE;
  $("next-action").textContent = text;
  $("next-hint").textContent = hint;
  $("next-hint").hidden = !hint;
  if (primary) $(primary).classList.add("primary");
  renderHeard();
  syncRangeDock();
  renderHero(hero);
  requestAnimationFrame(renderChipsMore);
}

$("text-input").addEventListener("input", renderSteps);

// ---- ① 初期（大きなマイク）と ② 会話の切り替え（唐川さん承認のデザイン 11/4。見せ方だけ）----
// ① は、読み込んだあと・会話の前（まだ送っていない・候補も範囲の確認も会話の途中も無い）。最初の送信で ② になり、
// 大きなマイクが小さくなりながら左下の入力欄の横へ移る（FLIP・約0.4秒）。空いた場所に会話の履歴がふわっと出る。
// 戻り方：別のデータを読んだ → ① に戻る（会話の履歴は無くなる）。「会話をやり直す」→ 候補・範囲の確認・会話の途中が無ければ ① に戻る
//（小さなマイクが真ん中へ大きくなって戻る）。動きを減らす設定のときは、動かさずにすぐ切り替える
// 唐川さんの確定文（11/5 UI 最終仕様）。ロボットを動かす命令ではなく、動きについて話す（Safety P2-4 の「直接命令に読める」を避ける）
const HERO_TITLE = "ロボットの動きについて話しかけてください";
const HERO_REPLACES = ["点か番号を押してください", "🎤 を押して話してください", "動かし方を書いて「次へ」"];

function isHero() {
  const st = state.st;
  if (!state.sessionId || !st || state.convo) return false;
  const step = currentStep();
  if (step !== 2 && step !== 3) return false;
  if (st.candidate || st.repeat_range || dialogStep()) return false;
  if (!$("reply-message").hidden || $("ask-turns").children.length) return false;
  return true;
}

const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function renderHero(want = isHero()) {
  const was = document.body.classList.contains("hero");
  if (want === was) return;
  const mic = $("mic-button");
  const first = mic.getBoundingClientRect();
  document.body.classList.toggle("hero", want);
  if (!want) setExamples(false);
  if (reduceMotion() || !first.width || mic.offsetParent === null || typeof mic.animate !== "function") return;
  const last = mic.getBoundingClientRect();
  if (!last.width) return;
  // FLIP：前の場所・大きさから、今の場所へ（transform だけ。押せる場所・大きさは今のまま）
  const dx = first.left - last.left, dy = first.top - last.top, s = first.width / last.width;
  mic.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${s})`, transformOrigin: "0 0" },
    { transform: "none", transformOrigin: "0 0" }], { duration: 420, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
  if (!want) {
    $("side-scroll").animate([{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "none" }],
      { duration: 380, delay: 140, easing: "ease-out", fill: "backwards" });
    document.querySelector(".side-head").animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 300, easing: "ease-out" });
  }
}

// 会話の欄に何か出たら（アプリの知らせなど）、① の画面を ② に切り替える（見せ方だけ）
new MutationObserver(() => { if (document.body.classList.contains("hero") && !isHero()) renderSteps(); })
  .observe($("ask-turns"), { childList: true });

// キーボード：① では押すと入力欄が出る（もう一度押すまで出したまま）。② では入力欄へ移る
function setKeyboard(open) {
  document.body.classList.toggle("kbd-open", open);
  $("kbd-button").setAttribute("aria-expanded", String(open));
}
$("kbd-button").addEventListener("click", () => {
  setKeyboard(true);
  if (!$("text-input").disabled) $("text-input").focus({ preventScroll: true });
});
// 指示の例（ⓘ）：ふだんは出さない。押すと小さな窓。Esc か外を押すと閉じる
function setExamples(open) {
  $("examples-pop").hidden = !open;
  $("examples-button").setAttribute("aria-expanded", String(open));
}
$("examples-button").addEventListener("click", (ev) => {
  ev.stopPropagation();
  setExamples($("examples-pop").hidden);
});
document.addEventListener("click", (ev) => {
  if (!$("examples-pop").hidden && !ev.target.closest("#examples-pop, #examples-button")) setExamples(false);
});

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
  requestAnimationFrame(renderChipsMore);
}

// ② の番号の1行（横に送る）：はみ出した番号の数を「ほか3 ›」の札で見せる。右に残りが無く左にあれば「‹ 戻る」（11/5 残件1）
function chipsHidden() {
  const c = $("position-chips"), box = c.getBoundingClientRect();
  const kids = [...c.children].map((b) => b.getBoundingClientRect());
  return { right: kids.filter((r) => r.right > box.right + 1).length, left: kids.filter((r) => r.left < box.left - 1).length };
}

function renderChipsMore() {
  const c = $("position-chips"), more = $("chips-more");
  const on = !document.body.classList.contains("hero") && !$("select-panel").hidden;
  const h = on ? chipsHidden() : { right: 0, left: 0 };
  c.classList.toggle("more-right", h.right > 0);
  c.classList.toggle("more-left", h.left > 0);
  more.hidden = !(h.right || h.left);
  more.textContent = h.right ? `ほか${h.right} ›` : "‹ 戻る";
  more.setAttribute("aria-label", h.right ? `ほかに ${h.right} つの番号があります。押すと横に送ります` : "はじめの番号に戻る");
}

$("chips-more").addEventListener("click", () => {
  const c = $("position-chips");
  if (chipsHidden().right) c.scrollBy({ left: Math.max(80, c.clientWidth * 0.8), behavior: reduceMotion() ? "auto" : "smooth" });
  else c.scrollTo({ left: 0, behavior: reduceMotion() ? "auto" : "smooth" });
});
$("position-chips").addEventListener("scroll", renderChipsMore, { passive: true });
$("position-chips").addEventListener("wheel", (ev) => {
  // マウスのホイール（縦）でも横に送る（はみ出しているときだけ）
  const c = ev.currentTarget;
  if (c.scrollWidth <= c.clientWidth || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) return;
  c.scrollLeft += ev.deltaY;
  ev.preventDefault();
}, { passive: false });
window.addEventListener("resize", () => requestAnimationFrame(renderChipsMore));

function applyResult(res, showOk = true) {
  if (res.state) state.st = res.state;
  state.dialogNotice = !!res.dialog;
  const talking = !!(state.st && state.st.dialog && state.st.dialog.step);
  if (res.dialog && res.ok !== false && !(res.choices && res.choices.length)
      && ((talking && res.status !== "stop") || res.candidate_made)) {
    // 会話の途中の問いかけ：文は会話のカード（吹き出し）に出す。下の返事の欄には重ねて出さない。
    // 止めた理由・会話が終わったときの聞き返しは、返事の欄にも出す（見出しも「もう一度、言い直してください」）
    clearReply();
  } else if (res.ok === false) {
    showReply("stop", res.error || "理由が分かりません");
  } else if (res.status && res.status !== "ok") {
    showReply(res.status, res.message, res.choices);
  } else if (res.message && (showOk || res.dialog || res.no_change)) {
    // 会話が終わったとき（「やめて」など）・［説明です］（何も変えない。CHECKER_026 M3）も、返事の欄に出す
    showReply("ok", res.message);
  }
  renderState();
  // 範囲の確認の最中に声・文字で決めようとした（「はい」など）：案内の文を、範囲の確認の上に添える（QA P1-A）
  renderRepeatRange(res && res.status === "ask" && res.message && !res.message.startsWith("この範囲（") ? res.message : "");
}

// 回数の変更の範囲の確認（サーバーの state.repeat_range）：［この範囲を繰り返す］［違う］のボタン。声では確定しない。
// 押すと /api/repeat に、今の確認の番号（turn）と一緒に送る。番号が古ければサーバーが断る
function renderRepeatRange(notice = "") {
  const rr = state.st && state.st.repeat_range;
  const box = $("reply-message");
  // 範囲の確認の間は、マイクの下の例文と「1回に動かせる量」の行を目立たせない（隠す）
  const limit = $("max-shift-label").closest("p");
  if (limit) limit.hidden = !!rr;
  if (rr) $("text-input").placeholder = "";
  if (!rr) {
    // 範囲の確認が無くなった：この関数が作った部品だけを消す（Safety S2-P2-1）。下の操作の欄のボタンも消す
    if (box.classList.contains("range-ask")) { box.replaceChildren(); box.hidden = true; box.classList.remove("range-ask"); }
    syncRangeDock();
    return;
  }
  if (box.hidden && !notice) { syncRangeDock(); return; }
  const row = el("div", { class: "choices range-choices" });
  const send = async (choice) => {
    if (recording()) return;
    for (const x of row.querySelectorAll("button")) x.disabled = true;  // 2回押しても1回だけ送る
    const res = await postJson("/api/repeat", { session_id: state.sessionId, choice: choice, turn: rr.turn });
    applyResult(res);
    if (res.candidate_made && state.st && state.st.candidate) startPlayback();
    keepHeadingInView();
  };
  // ［この範囲を繰り返す］を目立たせ（青。承認のオレンジとは別）、［違う］は控えめに（唐川さんの決定 2026-11-03 夜）
  const yes = el("button", { type: "button", class: "choice range-go", id: "range-yes" }, rr.yes_label);
  const no = el("button", { type: "button", class: "choice quiet", id: "range-no" }, rr.no_label);
  yes.addEventListener("click", () => send("yes"));
  no.addEventListener("click", () => send("no"));
  row.append(yes, no);
  // デモで目に留まるように：見出し「この範囲を繰り返しますか？」、範囲（行番号と命令の行）を大きく、対象外の文は下に小さく。
  // 文字・数はすべてサーバーの state.repeat_range のまま（ここでは作らない）。CSP のため大きさは CSSOM で入れる
  const big = (node, px, weight) => { node.style.fontSize = `${px}px`; if (weight) node.style.fontWeight = weight; return node; };
  const first = rr.line_nos[0], last = rr.line_nos[rr.line_nos.length - 1];
  const blockLast = rr.block_line_nos[rr.block_line_nos.length - 1];
  // 見出し（この範囲を繰り返しますか？）は画面の上の「次にすること」に1回だけ出す（ここでは出さない）
  // 行の一覧は「詳しく」（会話の欄に入るときは開いたまま。入らないときは畳み、要約（1回目・2回目・全体）とボタンを同時に見せる）
  // 1回目の行（命令の行）は畳まずに出す（Safety P2-3：範囲が別のブロックに替わったことを、人が押さずに見抜けるように）
  const lines = el("div", { class: "diff range-lines" }, ...rr.cycle_lines.map((t, i) => big(el("div", {}, `${first + i}行目  ${t}`), 14)));
  const parts = [
    ...(notice ? [big(el("p", { class: "range-guide" }, notice), 15, "700")] : []),
    big(el("p", { class: "range-where" }, `${rr.target}　1回目：${first}〜${last}行目`), 18, "700"),
    // 2回目以降の範囲と、全体の範囲（数・行番号はサーバーのもの）
    ...rr.cycles.slice(1).map(([a, b], k) => big(el("p", { class: "range-cycle" }, `${k + 2}回目：${a}〜${b}行目`), 16, "600")),
    big(el("p", { class: "range-all" }, `全体：${first}〜${blockLast}行目（${rr.count}回）`), 16, "600"),
    big(el("p", { class: "range-count" }, `今は${rr.count}回続けて書かれています。合計${rr.total}回にします`), 15),
  ];
  if (rr.side_note) parts.push(el("p", { class: "range-side" }, rr.side_note));
  parts.push(lines, el("p", { class: "fine range-note" }, rr.note));
  box.className = "reply ask range-ask";
  box.replaceChildren(...parts);
  box.hidden = false;
  // ボタンと注記（範囲を決めるだけ。承認は次の確認表示）は、下の操作の欄（いつも見える。前の答えのボタンは残さない）
  $("dock-range").replaceChildren(row, el("p", { class: "range-button-note" }, rr.button_note,
    el("button", { type: "button", class: "tip", "data-tip": "range" }, "?")));
  $("dock-range").dataset.turn = String(rr.turn);
  renderSteps();  // 見出しを、ボタンに合わせる
  // 狭い画面：範囲の確認の中身とボタンが見えるところまで送る
  if (window.innerWidth < 700) box.scrollIntoView({ block: "start", behavior: "auto" });
}

// 範囲の確認のボタン（下の操作の欄）は、範囲の確認のカードが出ている間だけ。カードが消えた・別の答えに替わったら、すぐ消す
function syncRangeDock() {
  const box = $("reply-message"), dock = $("dock-range");
  const rr = state.st && state.st.repeat_range;
  const live = !!rr && !box.hidden && box.classList.contains("range-ask") && !$("reply-panel").hidden
    && dock.dataset.turn === String(rr.turn);
  if (!live && dock.childElementCount) { dock.replaceChildren(); delete dock.dataset.turn; }
  dock.hidden = !live;
}

// 前の答え（聞き返し・選択肢・範囲の確認）を消す。範囲のボタンも一緒に消す（古い選択肢を後から押せないように）
function clearReply() {
  $("reply-message").hidden = true;
  $("reply-message").replaceChildren();
  syncRangeDock();
}

// 言い換えの例：サーバーの返事の文の中の例のうち、白リスト（highlight.js の REPHRASE_OK）にあるものだけ。
// 押すと入力欄に入るだけ（送るのは人）。サーバーの文は変えない。取れないときは出さない
function rephraseRow(message) {
  const ex = rephraseExamples(message, state.lastHeard);
  if (!ex.length) return null;
  const row = el("div", { class: "rephrase" }, el("span", { class: "rephrase-head" }, "言い換えの例（押すと欄に入ります）"));
  for (const t of ex) {
    const b = el("button", { type: "button", class: "rephrase-chip" }, t);
    b.disabled = recording();
    b.addEventListener("click", () => fillInput(t));
    row.append(b);
  }
  return row;
}

// 次にすること（11/5）：サーバーの返事の文（今の検査の結果）に合わせた1行。理由の文はそのまま上に出す。
// 例は決まった文だけで、押しても欄に入れない（人の言葉から正しい指示を推測して入れない）
function nextStepLine(message) {
  const n = nextStep(message);
  if (!n) return null;
  const line = el("p", { class: "next-step" }, el("span", { class: "next-step-head" }, "次にすること"), n.say);
  if (n.ex) line.append(el("span", { class: "next-step-ex" }, `例：「${n.ex}」`));
  return line;
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
          answerChoice(c.text);
        });
        row.append(b);
      }
      box.append(row);
    }
  }
  else if (/上限[^。]*を超え/.test(message || "")) box.replaceChildren(friendly("limit", message));  // 移動量の上限を超えたときだけ
  else box.replaceChildren(friendly("stop", message));
  if (status !== "ok") { const r = rephraseRow(message); if (r) box.append(r); }
  if (status !== "ok") { const n = nextStepLine(message); if (n) box.append(n); }
  box.hidden = false;
  $("voice-status").textContent = "";
  syncRangeDock();
}

// ---- 3. 指示（音声または文字）。どちらも同じ1つの入口（/api/talk）へ ----
$("text-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (recording() || state.sending) return;  // 答えを待っている間は送らない（文は入力欄に残る。11/5 F3）
  const text = $("text-input").value.trim();
  if (!text) return;
  // 認識文を直さずに送ったときだけ source=voice（記録だけ。結果は変わらない）
  const source = state.voiceFinal !== null && text === state.voiceFinal ? "voice" : "text";
  state.voiceFinal = null;
  $("voice-hint").hidden = true;
  $("text-input").value = "";
  startConversation();
  state.sending = true;
  updateLocks();
  let t = null;
  try { t = await sendInstruction(text, source); } finally { state.sending = false; updateLocks(); }
  // 送れなかった（会話の欄が別の答えを待っていた など）：書いた文を黙って消さない
  if (!t && !$("text-input").value) $("text-input").value = text;
});

// 最初の送信で ② 会話の画面へ（見せ方だけ。送る内容・順番は変えない）
function startConversation() {
  if (state.convo) return;
  state.convo = true;
  renderSteps();
}

// Step F：会話の欄（1つの入口 /api/talk）の答えのうち、指示の道の結果（範囲の確認・候補・止めた理由）を、
// いつもの指示の結果と同じように描く。状態はサーバーの state のまま。承認・出力はここから呼ばない
window.addEventListener("vt:talk-result", (ev) => {
  const t = ev.detail || {};
  if (t.hear && t.hear.status) {
    // 読めなかった言葉・聞き取った文は、結果を描く前に入れる（見出しと聞き取った文の目立たせに使う）
    const uw = t.hear.facts && t.hear.facts.unknown_words;
    state.unknownWords = Array.isArray(uw) ? uw.filter((w) => typeof w === "string" && w) : [];
    state.lastHeard = t.hear.heard !== undefined ? t.hear.heard : t.heard;
    renderFacts(t.hear.facts);
    applyResult({ ...t.hear, state: t.state || t.hear.state }, false);
    if (t.hear.candidate_made && state.st && state.st.candidate) startPlayback();
  } else if (t.state) {
    state.st = t.state;
    renderState();
  }
});

async function sendInstruction(text, source) {
  if (!state.sessionId) return;
  clearReply();  // 前の聞き返し（と選択肢・範囲のボタン）は消す。古い選択肢を後から押せないように
  followCardAt = -1e9;  // 人が新しく送った：その答えの吹き出しは見せる（直前に見せた候補・承認のカードの 1.5 秒の待ちで止めない。11/5 F1）
  // 入口は1つ（11/4 唐川さんの指示③）：/api/talk（explain.js の vtTalk）。結果は vt:talk-result で applyResult に渡る。
  // 指示の欄から /api/hear を直接呼ばない（振り分けをしないと、ボタンだけの言葉や相づちで承認が取り消されるため）
  const t = await window.vtTalk(text, source);
  if (!t) return null;
  const res = t.hear || {};
  $("heard-text").textContent = res.heard !== undefined ? res.heard : text;
  keepHeadingInView();
  return t;
}

// 指示の道が出した選択肢（［変更したい］［説明です］［2回の移動］など）への答え：プログラムが作った文なので、
// 振り分けをせずに指示の道（/api/hear）へ返す（Claude には送らない）。自由な文・声は、いつも入口（/api/talk）から
async function answerChoice(text) {
  if (!state.sessionId) return;
  startConversation();
  clearReply();
  const res = await postJson("/api/hear", { session_id: state.sessionId, text: text, source: "text" });
  $("heard-text").textContent = res.heard !== undefined ? res.heard : text;
  const uw = res.facts && res.facts.unknown_words;
  state.unknownWords = Array.isArray(uw) ? uw.filter((w) => typeof w === "string" && w) : [];
  state.lastHeard = res.heard !== undefined ? res.heard : text;
  renderFacts(res.facts);
  applyResult(res, false);
  if (res.candidate_made && state.st && state.st.candidate) startPlayback();
  keepHeadingInView();
}

// 小さな画面：送ったあと、見出し（次にすること）が上の帯の下に隠れないようにする
function keepHeadingInView() {
  if (window.innerWidth > 600) return;
  // 範囲の確認の最中は、範囲の中身と［この範囲を繰り返す］が見えるほうを優先する（会話の流れが見出しとの間に入るため）
  const box = $("reply-message");
  if (!box.hidden && box.classList.contains("range-ask")) return;
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
  startConversation();
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
  if (c.blocked_reason) {
    // 赤い帯（ここ）と 3D の灰色の点線・✕ が同じもの（変更できない案）だと分かるように書き添える（11/5 残件3）
    const band = friendly("blocked", c.blocked_reason);
    band.append(el("p", { class: "fine stopped-link" }, "3D では、灰色の点線と ✕ で示しています（参考の表示だけ）"));
    parts.push(band);
  }
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
  $("confirm-review-only").hidden = st.exportable !== false;  // 実機の JOB（合成ジョブでない）は出力しない
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
  if (!$("examples-pop").hidden) { setExamples(false); $("examples-button").focus(); return; }
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

// 承認のあとの3つの状態（元の動作データ・変更候補・ロボットへの送信）と、出力したあとの1行。
// 文はサーバーの決まった文をそのまま出す（ここでは作らない）。承認していなければ何も出さない
function renderAfterApproval(aa) {
  const ok = !!aa && typeof aa === "object" && Array.isArray(aa.rows);
  const rows = ok ? aa.rows.filter((r) => r && typeof r.label === "string" && typeof r.value === "string") : [];
  $("approval-states").replaceChildren(...rows.map((r) => el("div", { class: "state-row" }, el("dt", {}, r.label), el("dd", {}, r.value))));
  $("approval-states").hidden = rows.length === 0;
  const note = ok && typeof aa.note === "string" ? aa.note : "";
  $("approval-note").textContent = note;
  $("approval-note").hidden = !note;
  const exported = ok && typeof aa.exported === "string" ? aa.exported : "";
  $("approval-exported").textContent = exported;
  $("approval-exported").hidden = !exported;
}

function renderApproval() {
  const st = state.st;
  const a = st.approval;
  $("approval-status").textContent = a ? `✓ ${a.approved_by} さんが承認しました` : "";
  $("approval-status").hidden = !a;
  $("open-confirm").hidden = !!a;
  const reviewOnly = st.exportable === false;  // 実機の JOB：承認しても出力しない
  $("export-button").hidden = !a || reviewOnly;
  $("export-note").hidden = !a || reviewOnly;
  $("export-review-only").hidden = !a || !reviewOnly;
  renderAfterApproval(a ? st.after_approval : null);
  // 出力に成功したら（今の承認の出力が済み）・出力を送っている間は押せない。2回目が「上書きしません」の赤い失敗になって、
  // 「出力しました」と食い違うため（11/5 夜 H1）。上書きしない決まりはサーバーのまま。1回目の失敗のあとは押せる
  $("export-button").disabled = !st.can_export || !!st.exported || state.exporting || recording();
  $("approve-button").disabled = !st.can_approve || recording();
  if (!a && !$("export-result").hidden && $("export-result").dataset.ok === "true") {
    $("export-result").hidden = true;
  }
}

// ---- シミュレーション用の出力 ----
$("export-button").addEventListener("click", async () => {
  if (recording() || state.exporting || (state.st && state.st.exported)) return;  // ダブルクリックでも送るのは1回（11/5 夜 H1）
  state.exporting = true;
  $("export-button").disabled = true;
  let res;
  try {
    res = await postJson("/api/export", { session_id: state.sessionId });
  } finally {
    state.exporting = false;
  }
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
// 色は暗い図の面（style.css の --fig）で読める色（唐川さん承認のデザイン 11/4。意味は今までどおり：灰＝変更前・オレンジ＝変更後・青＝選んだ位置）
const C_LINE = "#c9d6ec", C_BEFORE = "#7f8ba3", C_AFTER = "#ff6a1a", C_POINT = "#e3ebf8", C_SEL = "#4da3ff";
const C_STOPPED = "#9aa3b5";  // 止まった候補（承認できない）の変更後

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
      : rr ? rr.positions : askHighlight().positions,  // 回数の変更の範囲の点・「Claude に聞く」の根拠の行の位置（光らせるだけ。選択ではない）
    // 「Claude に聞く」の答えの根拠の区間（サーバーが行番号から決めた行。光らせるだけ）。位置の確認中は出さない
    askLines: dialogStep() === "confirm_position" ? [] : askHighlight().lines,
    askContext: dialogStep() === "confirm_position" ? [] : askHighlight().context_lines,  // 前後の移動（控えめ）
    // 人が教えた意味（「Claude に聞く」の欄が置く。サーバーのメモリの中だけのもの）。図に印を出すだけ
    taught: Array.isArray(window.vtTaught) ? window.vtTaught : [],
  };
}

// explain.js が置く、光らせる範囲（{positions, lines}。サーバーが根拠の行番号から決めたもの）。形が違えば何も光らせない
function askHighlight() {
  const h = window.vtAskHighlight;
  if (Array.isArray(h)) return { positions: h, lines: [] };  // 根拠の行を押したとき（その行の位置だけ）
  if (h && Array.isArray(h.positions) && Array.isArray(h.lines)) {
    const entry = Array.isArray(h.entry_lines) ? h.entry_lines : [];
    const context = (Array.isArray(h.context_lines) ? h.context_lines : []).concat(entry);
    // 区間に外から入ってくる線（entry_lines）は強調しない（前後の移動と同じく控えめ）
    return { positions: h.positions, lines: h.lines.filter((n) => !entry.includes(n)), context_lines: context };
  }
  return { positions: [], lines: [], context_lines: [] };
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
  // 回数の変更の比較（動きの並びのカードを出すとき）は、区間の点に寄せて大きく見せる（表示の向き・大きさだけ）
  const rc = repeatCompare();
  if (is3d) view3d.setData({ ...data3d(), focus: rc && rc.sequence ? rc.repeat.positions : null });
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
  renderPlayButtons();
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
    if (sel) g.append(svgEl("circle", { cx: x, cy: y, r: big ? 14 : 7, fill: "rgba(77,163,255,0.18)", stroke: C_SEL, "stroke-width": 2 }));
    g.append(svgEl("circle", { cx: x, cy: y, r: big ? 6 : 4, fill: moved ? C_BEFORE : (sel ? C_SEL : C_POINT) }));
    if (!moved) {  // 動かす点の名前は、変更後の点の横に1つだけ出す
      const label = svgEl("text", { x: x + 12, y: placeLabel(x, y, p.name.length * 8),
        fill: sel ? "#7cbcff" : "#b3c0d8", "font-weight": sel ? "600" : "400" });
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
      const label = svgEl("text", { x: x + 12, y: placeLabel(x, y, text.length * 8), fill: stopped ? C_STOPPED : "#ffa266", "font-weight": "600" });
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
// 進め方は player.js（止める・続ける・1つ進む・はじめから）。図の上に「いま：24行目 MOVL C00001 V=150.0（2回目）」を出す。
// 回数の変更の候補があるときだけ［変更前を再生］［変更後を再生］を出す（変更前は2回目まで、変更後は3回目＝足した分まで）。
// 表示はサーバーの区間の線（line_no・text・kind・cycle）と候補の回数だけから作る。時間・関節の通り道は出さない
function stopPlayback() {
  state.playToken++;
  if (state.player2d) { const p = state.player2d; state.player2d = null; p.stop(); }
  state.player = null;
  $("play-status").textContent = "";
  $("play-button").classList.remove("playing");
  view3d.stop();
  renderNow(null);
  state.tally = { run: "", max: 0 };
  state.statusBase = "";
  renderSequence("after", null);
  renderPlayButtons();
}

// 動きの並びのカード（回数の変更の候補があるときだけ）。side：before・after。active：今の区間の回（無ければ null）
function renderSequence(side, active, activeSide = "") {
  const rep = repeatCompare();
  const box = $("play-seq");
  const seq = rep && rep.sequence;
  const chips = seq ? sequenceChips(seq, side) : [];
  if (!chips.length) { box.hidden = true; return; }
  $("seq-side").textContent = side === "before" ? `変更前 ×${rep.repeat.before_count}` : `変更後 ×${rep.repeat.after_count}`;
  $("seq-side").classList.toggle("after", side !== "before");
  $("seq-chips").replaceChildren(...chips.map((c) => {
    const cls = ["seq-chip", `seq-${c.kind}`];
    if (c.added) cls.push("added");
    if (c.kind === "cycle" && c.n === active) cls.push("now");
    if ((c.kind === "entry" || c.kind === "exit") && c.kind === activeSide) cls.push("now");
    const e = el("span", { class: cls.join(" ") }, c.text);
    if (c.title) e.title = c.title;
    return e;
  }));
  const t = state.tally;
  const parts = t.run === side ? tallyParts(t.max, side === "after" ? seq.added : []) : [];
  const tally = [];
  parts.forEach((x, k) => {
    if (k) tally.push(el("span", { class: "tally-dot" }, "・"));
    tally.push(el("span", { class: "tally-n" + (x.added ? " added" : "") }, x.text));
  });
  $("seq-tally").replaceChildren(...tally);
  $("seq-summary").textContent = seq.summary || "";
  box.hidden = false;
}

// 回数の変更の候補（承認できるもの）があるか。比較の再生はこのときだけ出す
function repeatCompare() {
  const c = state.st && state.st.candidate;
  return c && c.kind === "repeat" && !c.blocked_reason && c.repeat && Array.isArray(c.after_segments) ? c : null;
}

// 再生する runs の名前・回数（変更前 ×2・変更後 ×3）。which：both（変更前 → 変更後）・before・after
function playRuns(which, segsBefore, segsAfter) {
  const c = state.st && state.st.candidate;
  const rep = repeatCompare();
  // 変更後の線が無い（候補が無い。3D では変更後の点も無い候補）ときは、今までどおり「命令の順」だけ
  if (!c || segsAfter == null) return [{ name: "命令の順", key: "", color: C_POINT, segments: segsBefore, count: null, added: null }];
  const added = rep && rep.repeat.after_count > rep.repeat.before_count ? rep.repeat.after_count : null;
  const w = rep ? which : "both";  // 比較の再生は回数の変更の候補のときだけ
  const runs = [];
  if (w !== "after") runs.push({ name: "変更前", key: "before", color: "#6e6e73", segments: segsBefore,
    count: rep ? rep.repeat.before_count : null, added: null });
  if (w !== "before") runs.push({ name: "変更後", key: "after", color: C_AFTER, segments: segsAfter,
    count: rep ? rep.repeat.after_count : null, added });
  return runs;
}

// 一時停止中・1つ進むで止めているときは「を再生中」を「（一時停止中）」にする
function showStatus() {
  const base = state.statusBase || "";
  const p = state.player;
  $("play-status").textContent = p && p.state === "paused" ? base.replace("を再生中", "（一時停止中）") : base;
}

function onPlayStep(run, seg, i, n) {
  state.statusBase = playStatus(run.name, i, n, segmentLook(seg.kind), seg);
  showStatus();
  $("play-status").title = segmentTitle(seg);  // 命令の行は、マウスを重ねたときだけ
  renderNow(nowPlaying(seg, { run: run.name === "命令の順" ? "" : run.name, count: run.count, added: run.added }), run.color);
  // 動きの並び：今の区間の回を光らせ、通った回を「1・2・3」と数える（サーバーの seg.cycle のまま）
  if (run.key === "before" || run.key === "after") {
    if (state.tally.run !== run.key) state.tally = { run: run.key, max: 0 };
    const n = Number.isInteger(seg.cycle) ? seg.cycle : null;
    if (n && n > state.tally.max) state.tally.max = n;
    const seq = repeatCompare() && repeatCompare().sequence;
    const side = seq && seq[run.key];
    const at = n ? "" : side && side.exit && seg.to === side.exit.position && state.tally.max > 0 ? "exit"
      : side && side.entry && seg.to === side.entry.position ? "entry" : "";
    renderSequence(run.key, n, at);
  }
}

function onPlayEnd() {
  state.statusBase = "再生が終わりました";
  $("play-status").textContent = "再生が終わりました";
  if (state.tally.run) renderSequence(state.tally.run, null);  // 数えた「1・2・3」は残す（次の再生・止めるで消す）
  $("play-button").classList.remove("playing");
  renderNow(null);
  renderPlayButtons();
}

// 図の上の「いま」の表示。文字はテキストのノードとして入れる（CSP のため大きさは CSS のクラス）
function renderNow(n, color) {
  const box = $("play-now");
  if (!n) { box.hidden = true; box.replaceChildren(); box.title = ""; return; }
  const head = [];
  if (n.run) head.push(el("span", { class: "now-run" }, n.run));
  if (n.count) head.push(el("span", { class: "now-count" + (n.run === "変更後" ? " after" : "") }, n.count));
  const parts = [];
  if (head.length) parts.push(el("div", { class: "now-head" }, ...head));
  const line = el("div", { class: "now-line" }, n.line);
  if (n.cycle) line.append(el("span", { class: "now-cycle" + (n.added ? " added" : "") }, n.cycle));
  parts.push(line);
  if (n.note) parts.push(el("div", { class: "now-note" }, n.note));
  box.replaceChildren(...parts);
  box.title = nowText(n);
  box.classList.toggle("after", color === C_AFTER);
  box.hidden = false;
}

// 再生のボタンの状態（使える・押せない・比較のボタンを出すか）
function renderPlayButtons() {
  const rec = recording();
  const none = !state.view || state.view.segments.length === 0 || rec;
  const p = state.player;
  const live = !!p && (p.state === "playing" || p.state === "paused");
  $("play-button").disabled = none;
  $("play-pause").disabled = none || !live;
  $("play-pause").textContent = p && p.state === "paused" ? "続ける" : "一時停止";
  $("play-step").disabled = none;  // 終わったあとは、はじめから1区間ずつ
  $("play-stop").disabled = none || !live;
  const rep = repeatCompare();
  $("play-compare").hidden = !rep;
  $("play-before").disabled = none;
  $("play-after").disabled = none;
  for (const [id, w] of [["play-before", "before"], ["play-after", "after"]]) {
    const on = live && state.playWhich === w;
    $(id).classList.toggle("on", on);
    $(id).setAttribute("aria-pressed", String(on));
  }
}

// 再生を作る（start=false なら作るだけ。「1つ進む」から始めるとき）
function startPlayback(which = "both", start = true) {
  if (!state.view || recording()) return null;
  stopPlayback();
  state.playWhich = typeof which === "string" ? which : "both";
  const p = !$("fig-3d").hidden ? startPlayback3d(state.playWhich, start) : startPlayback2d(state.playWhich, start);
  state.player = p;
  if (p && p.items.length === 0) {
    // 図に描ける区間が1つも無い：すぐ終わる（再生中の見た目を残さない）
    p.stop();
    state.player = null;
    $("play-button").classList.remove("playing");
    $("play-status").textContent = "図に描ける区間が無いため、再生できません";
    renderPlayButtons();
    return null;
  }
  if (p && start) $("play-button").classList.add("playing");
  renderPlayButtons();
  return p;
}

function startPlayback2d(which, start) {
  const fig = drawFigure($("fig-main"), ...VIEWS[state.figView === "3d" ? bestView(state.view.positions) : state.figView], true);
  if (!fig) return null;
  const c = state.st && state.st.candidate;
  const runs = playRuns(which, fig.segLines.before, c ? fig.segLines.after : null);
  const marker = svgEl("circle", { r: 9, fill: runs[0].color, stroke: "#fff", "stroke-width": 3, class: "marker", visibility: "hidden" });
  const dest = svgEl("path", { d: "M -7 -25 L 7 -25 L 0 -14 Z", stroke: "#fff", "stroke-width": 1.5, class: "dest-mark", visibility: "hidden" });
  fig.svg.append(dest, marker);
  const items = runs.flatMap((run) => run.segments.map((s, k) => ({ run, s, k })));
  const SPEED = 260;  // 画面の上の速さ（px/秒）。実際のロボットの速度とは関係ない
  let cur = null;
  const p = new Player(items, {
    duration: (it) => {
      const s = it.s;
      return s.look.animate ? Math.max(350, (Math.hypot(s.to[0] - s.from[0], s.to[1] - s.from[1]) / SPEED) * 1000) : HOLD_MS;
    },
    onStep: (it) => {
      if (cur) {
        cur.s.line.setAttribute("stroke-width", String(cur.s.width));
        if (cur.restore) cur.s.line.setAttribute("stroke", cur.restore);
      }
      cur = it;
      const s = it.s;
      // 変更後の線のうち、変更前と同じで透明にしてある線（回数の変更の1回目・2回目など）は、再生している間だけ灰色で見せる
      if (s.line.getAttribute("stroke") === "transparent" && !(it.run.added && s.seg.cycle === it.run.added)) {
        it.restore = "transparent";
        s.line.setAttribute("stroke", "#6e6e73");
      }
      s.line.setAttribute("stroke-width", String(s.look.animate ? 5 : Math.max(s.width + 1.5, 3)));
      marker.setAttribute("fill", it.run.color);
      dest.setAttribute("fill", it.run.color);
      dest.setAttribute("transform", `translate(${s.to[0]} ${s.to[1]})`);
      dest.removeAttribute("visibility");
      // 回数の変更の比較：変更後の再生で、足した回の線は変更後の色で光らせる
      if (it.run.added && s.seg.cycle === it.run.added) s.line.setAttribute("stroke", C_AFTER);
      onPlayStep(it.run, s.seg, it.k + 1, it.run.segments.length);
    },
    onFrame: (it, f) => {
      if (!it) { marker.remove(); dest.remove(); return; }
      const s = it.s;
      if (s.look.animate) {
        marker.removeAttribute("visibility");
        marker.setAttribute("cx", s.from[0] + (s.to[0] - s.from[0]) * f);
        marker.setAttribute("cy", s.from[1] + (s.to[1] - s.from[1]) * f);
      } else {
        // MOVJ・目安の区間：印を線の上で動かさない。区間の間は印を消し、終わったら終点に置く
        marker.setAttribute("visibility", "hidden");
        if (f >= 1) { marker.setAttribute("cx", s.to[0]); marker.setAttribute("cy", s.to[1]); }
      }
    },
    onEnd: () => {
      if (cur) {
        cur.s.line.setAttribute("stroke-width", String(cur.s.width));
        if (cur.restore) cur.s.line.setAttribute("stroke", cur.restore);
      }
      marker.remove(); dest.remove();
      if (state.player2d === p) onPlayEnd();
    },
  });
  state.player2d = p;
  if (start) p.play();
  return p;
}

// 再生の状態の文。MOVJ・目安の区間では、印が線の上を動かないことを添える
const HOLD_MS = 700;  // MOVJ・目安の区間で、印を消しておく時間（画面の上だけ。実際の動きとは関係ない）
function playStatus(name, i, n, look, seg) {
  // 回数の変更の候補では「変更後：3回目を再生中」（サーバーの seg.cycle）。区間の番号（7/10）は回数と紛れるので出さない
  const c = state.st && state.st.candidate;
  const rep = c && c.kind === "repeat";
  const cyc = rep ? cycleStatus(seg, c.target) : "";
  const head = rep ? (cyc ? `${name}：${cyc.replace(/（.*$/, "")}を再生中` : `${name}を再生中`) : `${name}を再生中 ${i}/${n}`;
  if (look.kind === "movj") return `${head}　MOVJ：関節移動（点線は順番だけ。実際の通り道ではありません）`;
  if (look.kind === "other") return `${head}　直線とは限らない区間（印は終点へ移ります）`;
  return head;
}

// 3D の再生：命令の順（変更前 → 変更後）に、MOVL の区間だけ線の上を印が動く（ロボットの動きではない）
function startPlayback3d(which, start) {
  const d = data3d();
  const runs = playRuns(which, d.segments, d.after ? d.after.segments : null)
    .map((r) => ({ ...r, points: r.key === "after" ? d.after.points : d.points }));
  return view3d.play(runs,
    (run, s) => onPlayStep(run, s, run.segments.indexOf(s) + 1, run.segments.length),
    () => { if (state.player && view3d.player === state.player) onPlayEnd(); }, start);
}

$("play-button").addEventListener("click", () => startPlayback("both"));
$("play-before").addEventListener("click", () => startPlayback("before"));
$("play-after").addEventListener("click", () => startPlayback("after"));
$("play-pause").addEventListener("click", () => {
  const p = state.player;
  if (!p) return;
  if (p.state === "playing") { p.pause(); $("play-button").classList.remove("playing"); }
  else if (p.state === "paused") { p.play(); $("play-button").classList.add("playing"); }
  showStatus();
  renderPlayButtons();
});
$("play-step").addEventListener("click", () => {
  let p = state.player;
  if (!p || p.state === "stopped" || p.state === "ended") p = startPlayback(state.playWhich || "both", false);
  if (!p) return;
  p.step();
  $("play-button").classList.remove("playing");
  showStatus();
  renderPlayButtons();
});
$("play-stop").addEventListener("click", () => {
  stopPlayback();
  $("play-status").textContent = "再生を止めました";
});

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
  if (kind === "no-speech" || kind === "aborted") return "声を聞き取れませんでした。もう一度 🎤 を押して話してください";
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

// マイクは1回の発話だけ（唐川さんの決定 11/3 夜）：🎤 を押す → 話す → 認識文が入力欄に入り、マイクは OFF。
// 聞き取れなかった・通信の失敗のときも OFF にして、次にすることを出す。自動では、もう一度聞き始めない。
// 次に話すときは、人がもう一度 🎤 を押す。承認・出力・送信はここから呼ばない。
const voice = new SpeechInput(Recognition, {
  state: (value) => {
    state.voiceState = value;
    document.body.classList.toggle("listening", value === "listening");  // 波形は「聞いている」間だけ動く（声の大きさは使わない）
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
      state.live = false;  // 使わなかったときも、1回の発話で OFF
      showVoiceError("話している間に位置が変わったので、使いませんでした。もう一度どうぞ");
      renderMic();
      return;
    }
    $("text-input").value = text;  // 入力欄に入れるだけ。自動では送らない
    state.voiceFinal = text;
    setKeyboard(true);  // ① 初期の画面でも、聞き取った文を確かめられるように入力欄を出す
    state.live = false;  // 1回の発話で OFF（次は人が 🎤 を押す）
    state.needChrome = false;  // 声を文字にできた：Chrome の案内は要らない
    document.body.classList.remove("need-chrome");
    renderSteps();
    renderMic();
    renderSignHint();
  },
  error: (message) => {
    state.voiceToken = null;
    const kind = failKind(message);
    state.live = false;  // どの失敗でも OFF（聞き直さない）。次にすることと文字の入力へ案内する
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
  setKeyboard(true);  // ① 初期の画面でも「下の欄」を出す（11/5 夜 G7）。出すだけ。入れない・送らない・移らない
}

// マイクの ON／OFF を一目で分かるように（OFF・聞いています）
function renderMic() {
  const rec = recording();
  const on = state.live || rec;
  document.body.classList.toggle("live", on);
  $("mic-button").setAttribute("aria-pressed", String(on));
  $("mic-button").setAttribute("aria-label", on ? "マイクをOFFにする" : "マイクをONにする");
  if (rec) $("mic-label").replaceChildren(el("span", { class: "dot", "aria-hidden": "true" }, "●"), "聞いています");
  else $("mic-label").textContent = "マイク OFF";
  // 長い説明は、小さな文字の行に分ける
  $("mic-hint").textContent = "";
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
  voice.start(mode);
}

function updateLocks() {
  const rec = recording();
  const loaded = !!state.sessionId;
  $("file-input").disabled = rec || state.sending;  // 答えを待っている間は、別の JOB を読み込まない（11/5 G2）
  $("sample-button").disabled = rec || state.sending;
  $("text-input").disabled = rec || !loaded;
  $("text-send").disabled = rec || !loaded || state.sending;  // 答えを待っている間は押せない（11/5 F3）
  $("voice-mode-browser").disabled = rec;
  $("voice-mode-local").disabled = rec;
  $("voice-mode-reset").disabled = rec;
  $("voice-position").disabled = rec;
  $("mic-button").disabled = rec || !loaded || !Recognition || currentStep() === 4;  // ステップ4では録音しない
  $("voice-stop").disabled = !rec;
  $("voice-cancel").disabled = !rec;
  document.body.classList.toggle("recording", rec);
  if (state.st && state.view) {
    renderChips();
    renderCandidate();
    renderApproval();
    renderPlayButtons();
  }
  renderSteps();
  renderMic();
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
  state.live = true;  // この1回の発話の間だけ ON
  startListening();
  renderMic();
}

$("mic-button").addEventListener("click", () => {
  if (!state.sessionId || recording()) return;
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
// ---- 左の列の中の段のスクロール（表示だけ。UI 修正 11/4・唐川さん承認のデザイン 11/4）----
// PC 版では左の列の中の段（会話・指示の結果・候補）だけがスクロールする。ふだんは下に寄せて見せる（style.css の column-reverse。自動のスクロールに頼らない）。
// 新しく出たものが下に寄せただけでは見えないとき（上の端が切れるときなど）だけ、見えるところへ送る。新しく出たもの・変わったものが見えるところへ送る。
// - 指示の結果（範囲の確認など）・候補のカードの中身が変わった：そのカードを見せる（収まれば下の端まで。範囲のボタン・承認の1行が見える。
//   収まらなければ候補は上の端から（止まった理由・変わる値）、範囲の確認は下の端（ボタン）を見せる）
// - それ以外（会話の吹き出しが増えた）：いちばん新しい吹き出しを見せる
// 文・ボタン・出す隠すの決まりは変えない。狭い画面（縦に並べる）では何もしない
const sideScroll = $("side-scroll");
const followText = { reply: "", cand: "", talk: "" };
let followWant = null;
let followCardAt = -1e9;  // 候補・範囲のカードを見せた時刻。直後に届く会話の知らせ（同期の答え）でカードから離れない

function zoneText(id) {
  const e = $(id);
  return e.hidden ? "" : e.innerText;
}

// 会話の欄は下に寄せて見せる（column-reverse。scrollTop は 0 が下の端・上へ戻すと負）。どちらの向きでも動くよう、今の位置からの差で送る
function revealIn(box, target, align) {
  const br = box.getBoundingClientRect(), r = target.getBoundingClientRect();
  if (r.top >= br.top - 0.5 && r.bottom <= br.bottom + 0.5) return;  // もう全部見えている：動かさない
  const fits = r.height <= box.clientHeight - 8;
  const down = fits ? r.bottom > br.bottom : align === "bottom";
  box.scrollTop += down ? r.bottom - br.bottom + 8 : r.top - br.top - 8;
}

function followLatest() {
  followWant = null;
  if (getComputedStyle(sideScroll).overflowY !== "auto" || sideScroll.scrollHeight <= sideScroll.clientHeight) return;
  const reply = zoneText("reply-panel"), cand = zoneText("candidate-panel");
  const replyNew = reply !== followText.reply && reply !== "", candNew = cand !== followText.cand && cand !== "";
  const talk = zoneText("ask-panel") + "|" + zoneText("dialog-panel");
  const talkNew = talk !== followText.talk;
  followText.reply = reply;
  followText.cand = cand;
  followText.talk = talk;
  if (candNew || replyNew) followCardAt = performance.now();
  if (candNew) {
    // 承認のあと：「〇〇さんが承認しました」と3つの状態（出力の結果は下の操作の欄）。承認の前：候補のカードを上の端から（止まった理由・変わる値）
    if (!$("approval-status").hidden) return revealIn(sideScroll, $("approval-panel"), "top");
    return revealIn(sideScroll, $("candidate-panel"), "top");
  }
  if (replyNew) return revealIn(sideScroll, $("reply-message"), "top");  // 範囲のボタンは下の操作の欄にある：カードは上の端から
  if (!talkNew) return;  // 何も新しくない（同じ中身の描き直し）：今の位置のまま
  if (performance.now() - followCardAt < 1500) return;
  const bubbles = [...sideScroll.querySelectorAll("#ask-turns > li, #dialog-turns > li")].filter((e) => e.offsetParent !== null);
  const panel = document.body.classList.contains("in-dialog") && !$("dialog-panel").hidden ? $("dialog-panel") : null;
  if (panel) return revealIn(sideScroll, panel, "bottom");  // 会話の途中（位置の確認など）は、選択肢まで見せる
  const last = bubbles.filter((e) => e.closest("#ask-turns")).pop() || bubbles.pop();
  if (last) revealIn(sideScroll, last, "top");
}

new MutationObserver(() => {
  if (!followWant) followWant = requestAnimationFrame(followLatest);
}).observe(sideScroll, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });

$("voice-stop").addEventListener("click", () => voice.stop());  // 話し終わった（認識文が入ると、マイクは OFF）
$("voice-cancel").addEventListener("click", () => {
  state.live = false;
  state.voiceToken = null;
  voice.cancel();  // 認識文は使わない。入力欄は変えない
  $("voice-status").textContent = "マイクを OFF にしました";
  updateLocks();
});
updateLocks();

// 個別ヘルプ（11/5）：画面の「？」に、短い説明と例の小窓をつなぐ（見せ方だけ）
attachTips(document);

// ---- オープニング（11/5 UI 最終仕様）：起動のときだけの短い演出。表示だけ（読み込み・状態・検査には触らない） ----
// - 約3秒。押す・Esc・Enter・Space・［SKIP］で飛ばす。下の画面はそのまま読み込みを続ける
// - 動きを減らす設定：止めた絵を少しだけ出す（動かさない）
// - 画面を開いたとき（起動のとき）に出す。ブラウザーには何も覚えさせない（E2-6）。自動のときは無音（ブラウザーの決まり）。下の［▶ オープニング］で出し直すと、
//   押したあとなので短い電子音も鳴らす（ピピッ・カチカチ・シュン）。?opening=1 で必ず出す・?opening=0 で出さない
// - 自動の試験（navigator.webdriver）では自動で出さない（ほかの確かめの邪魔をしない）。出し直しの確かめはボタンで行う
// - ROBOT・TOOL・JOB・READY は、確かめていない接続・安全を言わない（ROBOT：実機に接続しない／TOOL：確認していない／
//   JOB：読み込んだかどうかだけ／READY：シミュレーションの操作を始められるだけ）
const opening = (() => {
  const box = $("opening");
  const STEPS = [[0, "s0"], [380, "s1"], [1080, "s2"], [1330, "s3"], [1580, "s4"], [2080, "s5"]];
  const END = 3300, FADE = 450;
  let timers = [], audio = null, active = false, leaving = false, still = false, back = null;

  const tone = (freq, t0, dur, type = "square", vol = 0.05) => {
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t0);
    g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(vol, t0 + 0.004); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g).connect(audio.destination); o.start(t0); o.stop(t0 + dur + 0.02);
  };
  const whoosh = (t0) => {
    const n = Math.floor(audio.sampleRate * 0.4), buf = audio.createBuffer(1, n, audio.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    const src = audio.createBufferSource(), f = audio.createBiquadFilter(), g = audio.createGain();
    src.buffer = buf; f.type = "bandpass"; f.Q.value = 2.5;
    f.frequency.setValueAtTime(300, t0); f.frequency.exponentialRampToValueAtTime(5200, t0 + 0.32);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.16, t0 + 0.08); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.38);
    src.connect(f).connect(g).connect(audio.destination); src.start(t0); src.stop(t0 + 0.4);
  };
  const sound = (step) => {
    if (!audio) return;
    try {
      const t = audio.currentTime + 0.01;
      if (step === "s1") { tone(1760, t, 0.05); tone(2349, t + 0.09, 0.07); }           // ピピッ
      else if (step === "s2" || step === "s3" || step === "s4") { tone(3400, t, 0.012, "square", 0.04); tone(1700, t + 0.05, 0.01, "square", 0.03); }  // カチカチ
      else if (step === "s5") whoosh(t);                                                  // シュン
    } catch { /* 音が出せなくても演出は続ける */ }
  };
  const jobLine = () => {
    const name = ($("file-name").textContent || "").trim();
    $("op-job").textContent = name ? "LOADED" : "NOT LOADED";
    $("op-job-note").textContent = name ? `${name}（読み込みました）` : "まだ読み込んでいません";
  };
  function finish() {
    if (!active || leaving) return;  // 消えかけの間の2回目の押す・キーは、消える時間を延ばさない（受け止めるだけ。11/5 夜 H3）
    leaving = true;
    for (const t of timers) clearTimeout(t);
    timers = [];
    box.classList.add("out");
    const done = () => { box.hidden = true; box.className = "opening"; active = false; leaving = false; if (back && back.focus) back.focus(); back = null; };
    if (still) done(); else timers.push(setTimeout(done, FADE));
  }
  function start(withSound = false) {
    if (active) return;
    active = true;
    still = reduceMotion();
    box.className = "opening";
    jobLine();
    if (withSound) {
      try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); if (audio.state === "suspended") audio.resume(); }
      catch { audio = null; }
    }
    const loud = withSound ? audio : null;
    box.hidden = false;
    back = document.activeElement;
    $("op-skip").focus({ preventScroll: true });
    if (still) {
      box.classList.add("still", ...STEPS.map(([, c]) => c));
      timers.push(setTimeout(finish, 1400));
      return;
    }
    for (const [t, c] of STEPS) timers.push(setTimeout(() => { box.classList.add(c); if (loud) sound(c); }, t));
    timers.push(setTimeout(finish, END));
  }
  box.addEventListener("click", finish);
  document.addEventListener("keydown", (ev) => {
    if (!active || !["Escape", "Enter", " ", "Spacebar"].includes(ev.key)) return;
    ev.preventDefault();
    ev.stopPropagation();
    finish();
  }, true);
  $("opening-replay").addEventListener("click", () => start(true));
  return { start, finish, get active() { return active; } };
})();

{
  const q = new URLSearchParams(location.search).get("opening");
  if (q !== "0" && (q === "1" || !navigator.webdriver)) opening.start(false);
}
