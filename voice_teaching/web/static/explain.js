// 「Claude に聞く（読み取りだけ）」の欄。/api/ask にだけ送る（CHECKER_031 E1〜E7）。
// - Claude の答えで、ジョブ・変更候補・承認は変わらない（サーバーもこの経路では変えない）。
// - 文はすべてテキストのノードで作る（Claude の文を HTML として扱わない）。
// - 答えが出たら、サーバーが根拠の行番号から決めた範囲（r.highlight）を 3D の図で自動で光らせる（選択ではない）。
//   光らせる範囲は Claude の文からは決めない。根拠の行を押すと、その行の位置だけを光らせる。
// - 補足（位置番号・行番号・命令）は、サーバーが根拠の行から作った文（r.supplement）をそのまま出す。
// - 人が教えた意味は、サーバーのメモリの中だけ（この画面の会話の中だけ）。ここでは保存しない（ブラウザーの保存の仕組みに書かない）。
//   「人が教えた（この会話の中だけ）」と「AI の推定（未確定）」は、別の印（色と文字）で出す。
// - 「どの動きのことですか？」はボタンで選ぶ（声・文字では選ばない）。承認・出力はここからは呼ばない。
// - キーは画面に来ない。キーが無いときは「APIキーが設定されていません」と出し、モックでは答えない。
"use strict";

const $ = (id) => document.getElementById(id);
const ask = { sessionId: null, busy: false, hasKey: false, voiceText: null, marks: { human: "", ai: "" } };

function el(tag, attrs, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  for (const c of children) if (c !== null && c !== undefined) n.append(c);
  return n;
}

const money = (x) => (typeof x === "number" && isFinite(x) ? "$" + x.toFixed(4) : "料金不明");

// Step F：会話の欄の文は1つの入口（/api/talk）へ送る。説明・教える・回数の変更・位置の変更の振り分けはサーバーのプログラム
async function postTalk(body) {
  try {
    const r = await fetch("/api/talk", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    return await r.json();
  } catch (_) {
    return { ok: false, error: "アプリにつながりません。アプリを起動し直して、もう一度どうぞ" };
  }
}

async function post(body) {
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    return await r.json();
  } catch (_) {
    return { ok: false, error: "アプリにつながりません。アプリを起動し直して、もう一度どうぞ" };
  }
}

// 光らせる（選択ではない）。h は {positions, lines}（サーバーが決めたもの）か、位置の配列（根拠の行を押したとき）
function highlight(h) {
  window.vtAskHighlight = h;
  window.dispatchEvent(new CustomEvent("vt:highlight"));
}

function scrollDown() {
  const box = $("ask-turns");
  box.scrollTop = box.scrollHeight;
}

function addTurn(who, text, kind) {
  const li = el("li", { class: "bubble " + kind }, el("span", { class: "who" }, who), el("span", { class: "said" }, text));
  $("ask-turns").append(li);
  scrollDown();
  return li;
}

// 印（色と文字の両方）。human＝人が教えた（この会話の中だけ）、ai＝AI の推定（未確定）
function mark(kind, text) {
  return el("span", { class: "mark mark-" + kind }, kind === "human" ? "✓ " + text : "? " + text);
}

function thinking() {
  const li = el("li", { class: "bubble claude thinking", "aria-label": "考えています" },
    el("span", { class: "who" }, "Claude"),
    el("span", { class: "said" }, "考えています", el("span", { class: "dots", "aria-hidden": "true" },
      el("i", {}), el("i", {}), el("i", {}))));
  $("ask-turns").append(li);
  scrollDown();
  return li;
}

function showUsage(r) {
  if (r && r.session_usage) {
    const u = r.session_usage;
    $("ask-usage").textContent = `このセッション：入力 ${u.input_tokens}・出力 ${u.output_tokens} トークン（キャッシュ読み ${u.cache_read_input_tokens}）`
      + `、推定 ${money(u.cost_usd)}／累計 ${money(r.total_cost_usd)}（上限 ${money(r.budget_usd)}）`;
  } else if (r && "budget_usd" in r && !("session_usage" in r)) {
    $("ask-usage").textContent = `累計 ${money(r.total_cost_usd)}（上限 ${money(r.budget_usd)}）`;
  }
}

function renderLock() {
  const on = !!ask.sessionId && !ask.busy;  // キーが無くても送れる（プログラムの道。説明は Claude が要る）
  $("ask-input").disabled = !ask.sessionId || ask.busy;
  $("ask-send").disabled = !on;
  $("ask-reset").disabled = !ask.sessionId || ask.busy;
  for (const b of document.querySelectorAll("#ask-turns .talk-choice")) b.disabled = ask.busy;
}

// 人が教えた意味の一覧（サーバーの talk.meanings）と、知らせ（talk.notices）
function renderTalk(t) {
  if (!t) return;
  ask.marks = { human: t.human_mark || "", ai: t.ai_mark || "" };
  const list = $("ask-memory");
  const items = (t.meanings || []).map((m) => el("li", { class: "memory-item" },
    mark("human", ask.marks.human), el("span", { class: "memory-text" }, `「${m.text}」`),
    el("span", { class: "memory-target" }, m.target)));
  list.replaceChildren(...items);
  $("ask-memory-box").hidden = items.length === 0;
  // 3D の図にも「人が教えた」の印を出す（描くだけ）
  // 変わったときだけ描き直す（描き直すと再生が止まるため）
  const taught = (t.meanings || []).map((m) => ({ text: m.text, positions: m.positions || [] }));
  if (JSON.stringify(taught) !== JSON.stringify(window.vtTaught || [])) {
    window.vtTaught = taught;
    window.dispatchEvent(new CustomEvent("vt:highlight"));
  }
  for (const n of t.notices || []) addTurn("アプリ", n, "app notice");
}

function renderPending(p) {
  if (!p) return;
  const box = el("div", { class: "talk-choices" });
  for (const o of p.options || []) {
    const b = el("button", { type: "button", class: "choice talk-choice" }, o.label);
    b.addEventListener("click", () => choose(o.id, p.token, o.label));
    box.append(b);
  }
  const li = addTurn("アプリ", p.question, "app");
  li.append(box);
}

async function choose(id, token, label) {
  if (!ask.sessionId || ask.busy) return;
  ask.busy = true;
  renderLock();
  for (const b of document.querySelectorAll("#ask-turns .talk-choices")) b.remove();  // 選択肢は1回だけ
  addTurn("人", label, "human");
  const r = await post({ session_id: ask.sessionId, choose: id, token });
  ask.busy = false;
  showReply(r);
  renderLock();
}

async function refreshInfo() {
  if (!ask.sessionId) return;
  const r = await post({ session_id: ask.sessionId, info: true });
  ask.hasKey = !!(r && r.ok && r.has_key);
  $("ask-model").textContent = r && r.ok ? (r.has_key ? "Claude（" + r.model + "）" : "") : "";
  $("ask-nokey").textContent = r && r.ok && !r.has_key ? r.notice : (r && !r.ok ? r.error : "");
  $("ask-nokey").hidden = !$("ask-nokey").textContent;
  if (r && r.ok) renderTalk(r.talk);
  showUsage(r);
  renderLock();
}

function showReply(r) {
  if (r.conversation_notice) addTurn("アプリ", r.conversation_notice, "app notice");
  if (!r.ok) {
    addTurn("アプリ", r.error || "理由が分かりません", "app");
    if (r.talk) renderTalk(r.talk);
    return;
  }
  if (r.taught_note) {
    const li = addTurn("アプリ", r.taught_note, "app taught");
    li.prepend(mark("human", ask.marks.human || "人が教えた（この会話の中だけ）"));
  }
  // 「どの動きのことですか？」：問いはボタンと一緒に1つの吹き出しで出す（下の renderPending）
  const asking = r.status === "ask_which" && r.talk && r.talk.pending;
  if (r.reply && !asking) {
    const isClaude = r.reply_by === "claude";
    const li = addTurn(isClaude ? "Claude（" + r.model + "）" : "アプリ", r.reply, isClaude ? "claude" : "app");
    if ((r.guesses || []).length) {  // AI の推定（未確定）：登録はしない。表示だけ
      const g = el("div", { class: "guesses" });
      for (const s of r.guesses) g.append(el("p", { class: "guess" }, mark("ai", ask.marks.ai || "AI の推定（未確定）"), s));
      li.append(g);
    }
    if ((r.supplement || []).length) {  // 補足：位置番号・行番号・命令（サーバーが根拠の行から作った文）
      li.append(el("p", { class: "supplement" }, el("span", { class: "sup-head" }, "補足 "), r.supplement.join("　")));
    }
    if ((r.evidence || []).length) {
      const box = el("div", { class: "evidence" });
      for (const e of r.evidence) {
        const b = el("button", { type: "button", class: "link small", title: "図でこの行の位置を光らせる（選択ではありません）" },
          `${e.line}行目 ${e.text}${e.note ? "（" + e.note + "）" : ""}`);
        b.addEventListener("click", () => highlight(e.positions || []));
        box.append(b);
      }
      li.append(box);
    }
  }
  for (const n of r.notes || []) addTurn("アプリ", n, "app");
  const c = r.change_record;
  if (c) {
    const prog = c.program_total != null ? `${c.program_total}回${c.program_keep ? "のまま" : ""}` : `読みません（${c.program_reason}）`;
    const cl = c.claude_total != null ? `${c.claude_total}回` : "なし";
    const li = addTurn("アプリ", `変更の希望の記録：あなたの文から ${prog}／Claude ${cl}。対象 ${c.target || "決めていません"}`
      + `${c.target ? "" : (c.target_note ? "（" + c.target_note + "）" : "")}。${c.note}`, "app record");
    if (c.mismatch) li.prepend(el("span", { class: "mark mark-mismatch" }, "! 食い違い"));
  } else if (r.change_request) {
    const what = r.change_request.kind === "repeat" ? "回数の変更" : "位置の移動";
    addTurn("アプリ", `Claude が読んだ変更の希望：${what}。記録して表示しただけで、何も変えていません`, "app");
  }
  if (r.talk) {
    renderTalk(r.talk);
    renderPending(r.talk.pending);
  }
  if (r.highlight) highlight(r.highlight);  // 返事のたびに自動で光らせる（無ければ消す）
}

window.addEventListener("vt:session", (ev) => {
  ask.sessionId = (ev.detail && ev.detail.sessionId) || null;
  ask.voiceText = null;
  $("ask-turns").replaceChildren();
  $("ask-usage").textContent = "";
  $("ask-memory").replaceChildren();
  $("ask-memory-box").hidden = true;
  window.vtTaught = [];
  highlight({ positions: [], lines: [] });
  $("ask-panel").hidden = !ask.sessionId;
  renderLock();
  refreshInfo();
});

// 画面の状態が変わった（候補・承認・元に戻す）：サーバーに確かめ直してもらう（意味の対応・会話の区切り）
let syncing = false, lastKey = null;
window.addEventListener("vt:state", async (ev) => {
  const sid = ev.detail && ev.detail.sessionId;
  const key = sid + "|" + (ev.detail && ev.detail.key);
  if (!sid || sid !== ask.sessionId || syncing || key === lastKey) return;
  lastKey = key;
  syncing = true;
  const r = await post({ session_id: sid, sync: true });
  syncing = false;
  if (r && r.ok && sid === ask.sessionId) {
    if ((r.talk.notices || []).length) highlight({ positions: [], lines: [] });
    renderTalk(r.talk);
  }
});

// 🎤 で聞き取った文がこの欄に入った（app.js）：直さずに送れば source=voice として送る（同じ検査を通る）
window.addEventListener("vt:ask-voice", (ev) => { ask.voiceText = ev.detail && ev.detail.text; });

$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("ask-input").value.trim();
  if (!text || !ask.sessionId || ask.busy) return;
  const source = ask.voiceText !== null && text === ask.voiceText.trim() ? "voice" : "text";
  ask.voiceText = null;
  ask.busy = true;
  renderLock();
  for (const b of document.querySelectorAll("#ask-turns .talk-choices")) b.remove();  // 古い選択肢は使わない
  addTurn("人", text, "human");
  const wait = thinking();
  const t = await postTalk({ session_id: ask.sessionId, text, source });
  wait.remove();
  ask.busy = false;
  if (t.ok) $("ask-input").value = "";
  if (!t.ok) {
    showReply(t);
  } else {
    if (t.ask) {
      showReply(t.ask);
      showUsage(t.ask);
    }
    if (t.hear && t.hear.message) {
      // 指示の道の答え（範囲の確認・候補・止めた理由など）も、会話の流れに出す（文はサーバーのもの）
      addTurn("アプリ", t.hear.message, "app");
      scrollDown();
    }
    // 画面の状態（範囲の確認のボタン・候補・3D）は app.js が描く
    window.dispatchEvent(new CustomEvent("vt:talk-result", { detail: t }));
  }
  renderLock();
  window.dispatchEvent(new CustomEvent("vt:ask-done"));
});

$("ask-reset").addEventListener("click", async () => {
  if (!ask.sessionId || ask.busy) return;
  const r = await post({ session_id: ask.sessionId, reset: true });
  $("ask-turns").replaceChildren();
  highlight({ positions: [], lines: [] });
  if (!r.ok) addTurn("アプリ", r.error || "理由が分かりません", "app");
  else renderTalk(r.talk);
  showUsage(r);
  renderLock();
});

renderLock();
