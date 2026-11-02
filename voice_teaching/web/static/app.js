// ジョブの図と変更案のプレビュー（読むだけ）。
// - JBI の解析はサーバー側（Python の jbi.py）だけで行う。ここでは JBI を解析しない。
// - 変更案は画面に見せるだけ。書き出し・保存・転送はしない。
// - 外部から何も読み込まない。サーバー（同じ 127.0.0.1）にだけ要求を送る。
"use strict";

const state = {
  jobBytes: null,   // 選んだファイルのバイト列（このページのメモリの中だけ）
  view: null,       // /api/parse の結果
  selected: null,   // 選んだ位置の名前
  preview: null,    // /api/preview/* の結果（ok のときだけ）
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

function bytesToBase64(bytes) {
  let s = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(s);
}

async function postJson(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

// ---- 読み込み ----
$("file-input").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  $("file-name").textContent = f.name;
  const bytes = new Uint8Array(await f.arrayBuffer());
  let view;
  try {
    const r = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    view = await r.json();
  } catch (e) {
    view = { ok: false, error: "サーバーに届きませんでした（python -m voice_teaching.web が動いているか確かめてください）" };
  }
  state.jobBytes = null; state.view = null; state.selected = null; state.preview = null;
  if (!view.ok) {
    $("load-error").textContent = "読み込めません：" + (view.error || "理由が分かりません");
    $("load-error").hidden = false;
    $("job-area").hidden = true;
    $("problems-panel").hidden = true;
    return;
  }
  $("load-error").hidden = true;
  state.jobBytes = bytes;
  state.view = view;
  renderAll();
});

// ---- 画面全体 ----
function renderAll() {
  const v = state.view;
  $("job-area").hidden = false;
  $("job-summary").textContent =
    `ジョブ名 ${v.name}・文字コード ${v.encoding}・位置 ${v.positions.length}個・命令 ${v.instructions.length}行`;
  const notes = $("job-notes");
  notes.replaceChildren(...v.notes.map((n) => el("li", {}, n)));

  const pl = $("problems-list");
  pl.replaceChildren(...v.problems.map((p) => el("li", {}, p)));
  $("problems-panel").hidden = v.problems.length === 0;

  $("max-shift-label").textContent = v.max_shift_label;
  $("figure-message").textContent = v.figure_message;
  $("figure-message").hidden = !v.figure_message;

  const sp = $("shift-position");
  sp.replaceChildren(...v.positions.map((p) => el("option", { value: p.name }, p.name)));
  const sl = $("speed-line");
  sl.replaceChildren(...v.instructions.filter((i) => i.is_move)
    .map((i) => el("option", { value: String(i.line_no) }, `${i.line_no}行目 ${i.text}`)));

  clearPreview();
  renderTables();
  renderFigures();
}

function select(name) {
  state.selected = name;
  if (name) $("shift-position").value = name;
  renderTables();
  renderFigures();
}

function usersOf(name) {
  const p = state.view.positions.find((q) => q.name === name);
  return p ? p.users.map((u) => u.line_no) : [];
}

function renderTables() {
  const v = state.view;
  const related = new Set(state.selected ? usersOf(state.selected) : []);
  const hl = new Set(state.preview ? state.preview.highlight_lines : []);

  const pb = $("positions-table").tBodies[0];
  pb.replaceChildren(...v.positions.map((p) => {
    const fmt = `${p.format}${p.rectan ? "（RECTAN）" : ""}${p.robot_coordinates ? "" : "：ロボット座標でない"}` +
      `${p.after_unknown_directive ? "（分からない指示行のあと）" : ""}・TOOL ${p.tool ?? "なし"}` +
      (p.user ? `・USER ${p.user}` : "");
    const users = p.users.length
      ? el("div", {}, ...p.users.map((u) => el("div", {}, el("code", {}, `${u.line_no}行目 ${u.text}`))))
      : "どの命令も使っていません";
    const chk = p.shift_check.ok
      ? el("span", { class: "ok" }, "検査を通る")
      : el("span", { class: "ng" }, "断る：" + p.shift_check.reason);
    const tr = el("tr", { class: "clickable" + (p.name === state.selected ? " selected" : "") },
      el("td", {}, el("strong", {}, p.name)), el("td", {}, p.line_no),
      el("td", {}, el("code", {}, p.line)), el("td", {}, fmt), el("td", {}, users), el("td", {}, chk));
    tr.addEventListener("click", () => select(p.name));
    return tr;
  }));

  const ib = $("instructions-table").tBodies[0];
  ib.replaceChildren(...v.instructions.map((i) => {
    let cls = "";
    if (related.has(i.line_no)) cls = "related";
    if (hl.has(i.line_no)) cls = "highlight";
    let chk = "";
    if (i.speed_check) {
      chk = i.speed_check.ok
        ? el("span", { class: "ok" }, "検査を通る")
        : el("span", { class: "ng" }, "断る：" + i.speed_check.reason);
    }
    const tr = el("tr", { class: (i.position ? "clickable " : "") + cls },
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
  $("legend").hidden = !(state.preview && state.preview.after_positions);
}

function drawFigure(host, axA, axB) {
  const v = state.view;
  const a = AXES[axA], b = AXES[axB];
  const before = v.positions.filter((p) => p.xyz);
  const pv = state.preview && state.preview.after_positions ? state.preview : null;
  const after = pv ? pv.after_positions.filter((p) => p.xyz) : [];
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
      const related = state.selected && (s.to === state.selected || s.from === state.selected);
      const line = svgEl("line", { x1: sx(p.xyz[a]), y1: sy(p.xyz[b]), x2: sx(q.xyz[a]), y2: sy(q.xyz[b]),
        stroke: color, "stroke-width": related ? width + 1.5 : width, "stroke-opacity": opacity });
      if (!s.solid) line.setAttribute("stroke-dasharray", "6 4");
      const t = svgEl("title", {});
      t.textContent = `${s.line_no}行目 ${s.text}${s.solid ? "" : "（目安）"}`;
      line.append(t);
      svg.append(line);
    }
  };

  if (pv) {
    drawSegs(v.segments, before, "#9a9a9a", 2, 0.9);           // 変更前（灰色）
    drawSegs(pv.after_segments, after, "#d0602a", 2, 0.95);    // 変更後（色付き）
  } else {
    drawSegs(v.segments, before, "#4a6fa5", 2, 1);
  }

  // 同じ点に重なる位置は、名前を縦にずらして並べる
  const groups = new Map();
  for (const p of before) {
    const key = `${sx(p.xyz[a]).toFixed(0)},${sy(p.xyz[b]).toFixed(0)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  for (const list of groups.values()) {
    list.forEach((p, idx) => {
      const x = sx(p.xyz[a]), y = sy(p.xyz[b]);
      const moved = pv && pv.moved === p.name;
      const sel = p.name === state.selected;
      const g = svgEl("g", { class: "pt" });
      if (sel) g.append(svgEl("circle", { cx: x, cy: y, r: 11, fill: "none", stroke: "#e0a000", "stroke-width": 3 }));
      g.append(svgEl("circle", { cx: x, cy: y, r: 5, fill: moved ? "#9a9a9a" : (pv ? "#555" : "#d0602a") }));
      const label = svgEl("text", { x: x + 9, y: y - 8 + idx * 14, "font-weight": sel ? "bold" : "normal" });
      label.textContent = p.name + (moved ? "（変更前）" : "");
      g.append(label);
      const t = svgEl("title", {});
      t.textContent = `${p.name}  ${p.line}`;
      g.append(t);
      g.addEventListener("click", () => select(p.name));
      svg.append(g);
    });
  }
  if (pv) {
    const q = after.find((p) => p.name === pv.moved);
    if (q) {
      const x = sx(q.xyz[a]), y = sy(q.xyz[b]);
      svg.append(svgEl("circle", { cx: x, cy: y, r: 6, fill: "#d0602a" }));
      const label = svgEl("text", { x: x + 9, y: y + 16, fill: "#b04010", "font-weight": "bold" });
      label.textContent = `${q.name}（変更後）`;
      svg.append(label);
    }
  }
  const ax1 = svgEl("text", { x: W - 46, y: H - 10 }); ax1.textContent = `${axA} →`;
  const ax2 = svgEl("text", { x: 8, y: 16 }); ax2.textContent = `↑ ${axB}`;
  svg.append(ax1, ax2);
  host.replaceChildren(svg);
}

// ---- 変更案のプレビュー ----
function clearPreview() {
  state.preview = null;
  $("preview-result").replaceChildren();
  $("preview-clear").hidden = true;
}

$("preview-clear").addEventListener("click", () => {
  clearPreview();
  renderTables();
  renderFigures();
});

// 結果の形（api.py）：mode・proposal（あれば）・warnings（注意）・blocked_reason（実機用の検査で止まる理由。無ければ null）。
// いまは断られたときは proposal が無い。後で sim.py が両方を返す形になっても表示できるようにしておく。
function showResult(res) {
  const parts = [];
  const blocked = res.blocked_reason || (!res.ok ? (res.reason || res.error || "理由が分かりません") : null);
  if (blocked) {
    parts.push(el("div", { class: "result refused" },
      el("strong", {}, res.proposal
        ? "実機用の検査では止まります（シミュレーションでの表示だけです）。"
        : "検査に断られました。何も変えていません。"),
      el("p", {}, "理由：" + blocked)));
  }
  if (res.proposal) {
    const p = res.proposal;
    const unit = p.kind === "shift" ? "mm" : "";
    parts.push(el("div", { class: "result proposal" },
      el("strong", {}, `変更案（${p.target}・ファイルの ${p.line_no}行目）。見るだけで、ジョブは変わりません。`),
      el("div", { class: "diff" },
        el("div", { class: "minus" }, "変更前  " + p.before_line),
        el("div", { class: "plus" }, "変更後  " + p.after_line)),
      el("p", {}, `指示の値：${p.requested}${unit}　変更前の値：${p.before_value}　丸めた後の実際の値：${p.after_value}` +
        (p.kind === "shift" ? `　実際の変化量：${fmtNum(p.actual_delta)}mm` : "")),
      el("p", {}, "変わる命令の行："),
      el("ul", {}, ...p.affected.map((a) => el("li", {}, a))),
      el("p", {}, "注意："),
      el("ul", {}, ...(res.warnings || p.warnings).map((w) => el("li", {}, w)))));
    state.preview = res;
    if (p.kind === "shift" && res.moved) state.selected = res.moved;
  } else {
    state.preview = null;
  }
  $("preview-result").replaceChildren(...parts);
  $("preview-clear").hidden = !res.proposal;
  renderTables();
  renderFigures();
}

function fmtNum(x) {
  return Math.round(x * 1e6) / 1e6;
}

function numberOrRaw(text) {
  // 空や数値でない入力は、そのまま文字で送り、検査プログラムに断らせる
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : t;
}

$("shift-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!state.jobBytes) return;
  const res = await postJson("/api/preview/shift", {
    job_base64: bytesToBase64(state.jobBytes),
    position: $("shift-position").value,
    axis: $("shift-axis").value,
    delta_mm: numberOrRaw($("shift-delta").value),
  });
  showResult(res);
});

$("speed-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!state.jobBytes) return;
  const res = await postJson("/api/preview/speed", {
    job_base64: bytesToBase64(state.jobBytes),
    line_no: Number($("speed-line").value),
    speed: numberOrRaw($("speed-value").value),
  });
  showResult(res);
});
