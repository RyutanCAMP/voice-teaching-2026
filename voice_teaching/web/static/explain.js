// 「Claude に聞く（読み取りだけ）」の小さな欄。/api/ask にだけ送る。
// - Claude の答えで、ジョブ・変更候補・承認は変わらない（サーバーもこの経路では変えない）。
// - 文はすべてテキストのノードで作る（Claude の文を HTML として扱わない）。
// - 根拠の行を押すと、その行の位置を 3D の図で光らせる（app.js の vt:highlight）。選択ではない。
// - キーは画面に来ない。キーが無いときは「APIキーが設定されていません」と出し、モックでは答えない。
"use strict";

const $ = (id) => document.getElementById(id);
const ask = { sessionId: null, busy: false, hasKey: false };

function el(tag, attrs, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  for (const c of children) n.append(c);
  return n;
}

const money = (x) => (typeof x === "number" && isFinite(x) ? "$" + x.toFixed(4) : "料金不明");

async function post(body) {
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    return await r.json();
  } catch (_) {
    return { ok: false, error: "アプリにつながりません。アプリを起動し直して、もう一度どうぞ" };
  }
}

function highlight(positions) {
  window.vtAskHighlight = positions;
  window.dispatchEvent(new CustomEvent("vt:highlight"));
}

function addTurn(who, text, kind) {
  const li = el("li", { class: "bubble " + kind }, el("span", { class: "who" }, who), el("span", { class: "said" }, text));
  $("ask-turns").append(li);
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
  const on = !!ask.sessionId && ask.hasKey && !ask.busy;
  $("ask-input").disabled = !ask.sessionId || ask.busy;
  $("ask-send").disabled = !on;
  $("ask-reset").disabled = !ask.sessionId || ask.busy;
}

async function refreshInfo() {
  if (!ask.sessionId) return;
  const r = await post({ session_id: ask.sessionId, info: true });
  ask.hasKey = !!(r && r.ok && r.has_key);
  $("ask-model").textContent = r && r.ok ? (r.has_key ? "Claude（" + r.model + "）" : "") : "";
  $("ask-nokey").textContent = r && r.ok && !r.has_key ? r.notice : (r && !r.ok ? r.error : "");
  $("ask-nokey").hidden = !$("ask-nokey").textContent;
  showUsage(r);
  renderLock();
}

window.addEventListener("vt:session", (ev) => {
  ask.sessionId = (ev.detail && ev.detail.sessionId) || null;
  $("ask-turns").replaceChildren();
  $("ask-usage").textContent = "";
  highlight([]);
  $("ask-panel").hidden = !ask.sessionId;
  renderLock();
  refreshInfo();
});

$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("ask-input").value.trim();
  if (!text || !ask.sessionId || ask.busy || !ask.hasKey) return;
  ask.busy = true;
  renderLock();
  addTurn("人", text, "human");
  const wait = addTurn("アプリ", "Claude に聞いています…", "app");
  const r = await post({ session_id: ask.sessionId, text });
  wait.remove();
  ask.busy = false;
  if (r.ok) {
    $("ask-input").value = "";
    const who = r.reply_by === "claude" ? "Claude（" + r.model + "）" : "アプリ";
    const li = addTurn(who, r.reply, r.reply_by === "claude" ? "claude" : "app");
    for (const n of r.notes || []) addTurn("アプリ", n, "app");
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
    if (r.change_request) {
      const c = r.change_request;
      const what = c.kind === "repeat" ? `回数の変更（${c.from_count ?? "?"}回 → ${c.to_count ?? "?"}回）` : "位置の移動";
      addTurn("アプリ", `Claude が読んだ変更の希望：${what}。記録して表示しただけで、何も変えていません`, "app");
    }
  } else {
    addTurn("アプリ", r.error || "理由が分かりません", "app");
  }
  if (r.conversation_notice) addTurn("アプリ", r.conversation_notice, "app");
  showUsage(r);
  renderLock();
  window.dispatchEvent(new CustomEvent("vt:ask-done"));
});

$("ask-reset").addEventListener("click", async () => {
  if (!ask.sessionId || ask.busy) return;
  const r = await post({ session_id: ask.sessionId, reset: true });
  $("ask-turns").replaceChildren();
  highlight([]);
  if (!r.ok) addTurn("アプリ", r.error || "理由が分かりません", "app");
  showUsage(r);
  renderLock();
});

renderLock();
