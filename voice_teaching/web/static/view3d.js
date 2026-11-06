// 3D の点と経路の表示（依存なし。SVG に投影して描く）。
// - 座標はジョブの位置の値（X・Y・Z、mm）をそのまま使う。作った座標・ロボット本体・関節は描かない。
// - ロボットの台座の目印（2026-11-05 夜 唐川さんの決定。docs/DECISION_3D_ROBOT_BASE_20261105.md）：ロボット座標の原点 (0,0,0)
//   に黄色の丸と「ロボット（台座の中心）」、原点から X＋ へ「ロボットの正面（X＋）」の矢印。点・経路の下に描き、押しても何も起きない。
//   印の大きさ・矢印の長さは見やすさの目安で、ロボットの大きさ・形ではない
// - ドラッグで回転、ホイールで拡大・縮小、点を押して選ぶ、視点を戻す。重なって見える点は、押すと選び分けの一覧を出す。
// - 経路は JOB の命令の順に点を直線で結んだ目安。再生は線の上を印が動くだけで、ロボットの動きではない。
// - 区間の種類（kind）で描き分ける（highlight.js の segmentLook）。MOVJ（関節移動）は細い灰色の点線で、
//   再生の印は MOVL の区間だけ線の上を動く。MOVJ・目安の区間では印を消し、終わったら終点に置く。
// - 画面の上下は Z、奥行きは X・Y を回して見せる（表示の向きだけ。指示の方向の解釈には使わない）。
// - 変更候補があるときは、動かす点に触れる区間（入る・出る）だけを変更後の色で描く。止まった候補は灰色の点線。
// - 回数の変更の候補（data.repeat）：区間の近くに「×2 → ×3」の印。足した回の線（元からある線と同じ線）を変更後の色で重ねる。
//   再生では、区間の線（サーバーの seg.cycle）ごとに「2回目」を印の横に出す。新しい点・新しい線は描かない
// - 再生（player.js）：止める・続ける・1つ進む・はじめから。再生中は、その区間の行き先の点の上に小さな印（▼）を出す
//   （MOVJ でも点の上だけ。線の上は動かさない）。回数の変更の比較では、変更前の再生の間は足した回の線を重ねない
"use strict";
import { displayMinus, segmentLook, segmentTitle, askHighlightLook, askContextLook, isAskHighlighted } from "/static/highlight.js";
import { Player } from "/static/player.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const DEFAULT_YAW = -0.75;    // 最初の向き（ラジアン）。読み込んだときと、確認表示の図（reset）
const DEFAULT_PITCH = 0.42;
// ［正面に戻す］の向き（Issue #8 round3・11/6 唐川さん）：ロボットの正面（X＋）の側から、水平に（仰ぎも俯きも無く）見る。
// project() は「画面の右＝x1＝x·cos(yaw) − y·sin(yaw)・奥＝y1・上＝z」なので、yaw = −π/2 で 右＝Y＋・奥＝X−（手前＝X＋）、pitch = 0 で水平・上＝Z＋。
// Y・Z の 2D の図（横＝Y、上＝Z）と左右・上下が同じ。カメラの向きだけで、点の座標・描く点や線は変えない
const FRONT_YAW = -Math.PI / 2;
const FRONT_PITCH = 0;
const OVERLAP_PX = 6;         // この距離より近く見える点は、まとめて選び分ける
const C_AFTER = "#ff6a1a", C_AFTER_TEXT = "#ffa266";
const C_STOPPED = "#9aa3b5";  // 止まった候補（承認できない）の変更後：オレンジにしない
const HOLD_MS = 700;          // MOVJ・目安の区間で、印を消しておく時間（画面の上だけ。実際の動きとは関係ない）

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
  // 文字の大きさ：画面の CSS（svg text { font-size: 12px }）より強く、CSP（style 属性は使えない）でも効くように CSSOM で入れる
  if (attrs && attrs["data-fs"]) e.style.fontSize = `${attrs["data-fs"]}px`;
  return e;
}

// 文字の幅の目安（px・12px の文字）。名前の重なりを避けるためだけに使う
function textWidth(s) {
  let w = 0;
  for (const ch of String(s)) w += ch.charCodeAt(0) < 0x2000 ? 7 : 12;
  return w;
}

function overlaps(a, b) {
  return a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
}

function niceStep(span) {
  const raw = span / 8;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (raw <= m * p) return m * p;
  return 10 * p;
}


// ---- 台座の目印・正面の矢印（2026-11-05 夜 唐川さんの決定。docs/DECISION_3D_ROBOT_BASE_20261105.md） ----
// ジョブはロボット座標なので、原点 (0,0,0) がロボットの台座の中心。安川のロボット座標では X＋ がロボットの正面。
// 印の大きさ・矢印の長さは見やすさのための目安で、ロボットの大きさ・形ではない。ロボット本体・腕・関節は描かない
const C_ROBOT = "#f2c94c";  // 台座の目印・正面の矢印と札（軸の X・Y・Z の色、変更後のオレンジと見分ける。形と札でも見分ける）
const BASE_LABEL = "ロボット（台座の中心）";
const FRONT_LABEL = "ロボットの正面（X＋）";
// 台座の印の説明（印にマウスを重ねたときの文。確認表示の注意書きにも同じ文がある）。図の下の注意書きには足さない（1440×900 で 3D が縮むため）
const BASE_NOTE = "台座の印は、ロボット座標の原点（0,0,0）の位置です。ロボットの大きさ・形ではありません。";

const ptsAttr = (ps) => ps.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(" ");

// 札の置き場所：目印（ax, ay）のまわりを近い順に探す。枠の中で、boxes に重ならない所（無ければ枠の中の所、それも無ければ null）
function findSpot(ax, ay, w, boxes, W, H, h = 16, M = 5) {
  let inside = null;
  for (const r of [10, 18, 28, 40, 54, 70, 88, 110, 134, 160]) {
    for (let i = 0; i < 16; i++) {
      const t = (i / 16) * Math.PI * 2 + Math.PI / 4;
      const cx = ax + Math.cos(t) * (r + w / 2), cy = ay + Math.sin(t) * (r + h / 2);
      const b = { x0: cx - w / 2 - 2, x1: cx + w / 2 + 2, y0: cy - h / 2 - 1, y1: cy + h / 2 + 1 };
      if (b.x0 < M || b.x1 > W - M || b.y0 < M || b.y1 > H - M) continue;
      if (!inside) inside = [cx, cy, b];
      if (!boxes.some((q) => overlaps(b, q))) return [cx, cy, b];
    }
  }
  return inside;
}

// 札（暗い地の四角の上に文字）。text-anchor は真ん中。押しても何も起きない（下の点を押せるように）
function labelEl(cx, cy, text, color, cls) {
  const g = svgEl("g", { "pointer-events": "none" });
  const w = textWidth(text);
  g.append(svgEl("rect", { x: cx - w / 2 - 3, y: cy - 9, width: w + 6, height: 18, rx: 5, fill: "#08101f", "fill-opacity": 0.78 }));
  const t = svgEl("text", { x: cx, y: cy + 4, "text-anchor": "middle", fill: color, "font-weight": "700", class: cls });
  t.textContent = text;
  g.append(t);
  return g;
}

// 2D の図（app.js の drawFigure）の、原点（台座の中心）の印と X＋（正面）の向きの印。
// sx・sy：その図の座標 → 画面。placed：点の名前の札（{x, y, w}）。pts：点の画面の位置
export function drawBase2d(svg, { axA, axB, sx, sy, W, H, big, placed = [], pts = [] }) {
  const ox = sx(0), oy = sy(0);
  const g = svgEl("g", { class: "base2d-g", "pointer-events": "none" });
  const r = big ? 9 : 5;
  g.append(svgEl("circle", { cx: ox, cy: oy, r, fill: "none", stroke: C_ROBOT, "stroke-width": 2, "stroke-dasharray": "3 2", class: "base2d" }));
  g.append(svgEl("line", { x1: ox - r * 0.6, y1: oy, x2: ox + r * 0.6, y2: oy, stroke: C_ROBOT, "stroke-width": 1.5 }));
  g.append(svgEl("line", { x1: ox, y1: oy - r * 0.6, x2: ox, y2: oy + r * 0.6, stroke: C_ROBOT, "stroke-width": 1.5 }));
  let tip = null;
  if (axA === "X") {  // 横が X の図（X・Y、X・Z）：X＋ は図の X の → の向き
    const L = big ? 60 : 24;
    tip = [ox + r + L, oy];
    g.append(svgEl("line", { x1: ox, y1: oy, x2: tip[0], y2: oy, stroke: C_ROBOT, "stroke-width": big ? 4 : 2.5,
      "stroke-linecap": "round", class: "front2d" }));
    const hd = big ? 9 : 5;
    g.append(svgEl("path", { d: `M ${tip[0] + hd} ${oy} L ${tip[0] - hd * 0.4} ${oy - hd} L ${tip[0] - hd * 0.4} ${oy + hd} Z`, fill: C_ROBOT }));
  }
  svg.insertBefore(g, svg.firstChild);  // 線・点の下（点を押すのを妨げない）
  const boxes = placed.map((q) => ({ x0: q.x, x1: q.x + q.w, y0: q.y - 12, y1: q.y + 3 }))
    .concat(pts.map(([x, y]) => ({ x0: x - 10, x1: x + 10, y0: y - 10, y1: y + 10 })))
    .concat([{ x0: W - 44, x1: W, y0: H - 26, y1: H }, { x0: 0, x1: 44, y0: 0, y1: 28 }]);  // 軸の文字（X →・↑ Z）
  const labs = [[BASE_LABEL, ox, oy]];
  labs.push(tip ? [FRONT_LABEL, tip[0], tip[1]] : [`${FRONT_LABEL}：この図の面に垂直`, ox, oy]);
  for (const [text, ax, ay] of labs) {
    const w = textWidth(text);
    const spot = findSpot(ax, ay, w, boxes, W, H);
    if (!spot) continue;
    const t = svgEl("text", { x: spot[0], y: spot[1] + 4, "text-anchor": "middle", fill: C_ROBOT, "font-weight": "600", class: "base2d-label" });
    t.textContent = text;
    svg.append(t);
    boxes.push(spot[2]);
  }
}

export class View3D {
  // host：描く場所（div）。opts.onPick(name)：点を選んだとき（無ければ選べない）
  constructor(host, opts = {}) {
    this.host = host;
    this.onPick = opts.onPick || null;
    this.onView = opts.onView || null;  // 回した・拡大した・戻したとき（「視点を戻す」を出すかどうか）
    this.hint = opts.hint !== false;    // 「ドラッグで回す・ホイールで拡大」を出す（確認表示の中では出さない）
    this.interacted = false;            // 一度でも回した・拡大したら、操作のヒントは出さない
    this.W = opts.width || 720;
    this.H = opts.height || 480;
    this.data = null;
    this.yaw = DEFAULT_YAW; this.pitch = DEFAULT_PITCH; this.zoom = 1;
    this.marker = null;      // 再生の印（世界座標）
    this.player = null;      // 再生の進め方（player.js）
    this.chooser = null;
    host.classList.add("view3d");
    this._bind();
  }

  // data: { points: [{name, xyz, line}], segments: [{from, to, line_no, text, kind, solid}],
  //         after: null | { points, segments, moved }, selected }
  setData(data) {
    this.data = data;
    const all = data.points.concat(data.after ? data.after.points : []).map((p) => p.xyz);
    const bounds = (vs) => [[0, 1, 2].map((k) => Math.min(...vs.map((v) => v[k]))), [0, 1, 2].map((k) => Math.max(...vs.map((v) => v[k])))];
    const [plo, phi] = bounds(all);
    this.spanPts = Math.max(Math.hypot(phi[0] - plo[0], phi[1] - plo[1], phi[2] - plo[2]), 1);
    // 図に入れるもの：ジョブの点と、ロボット座標の原点（台座の中心）。最初の視点で、台座と点の両方が入るように
    const [lo, hi] = bounds(all.concat([[0, 0, 0]]));
    this.center = [0, 1, 2].map((k) => (lo[k] + hi[k]) / 2);
    this.lo = lo; this.hi = hi;
    this.span = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]), 1);
    // 床の格子：点がどれも Z≥0 なら Z=0（台座の面）に置く。Z<0 の点があれば、いちばん下の点の少し下
    this.z0 = plo[2] >= 0 ? 0 : plo[2] - this.spanPts * 0.08;
    // data.focus（位置の名前）：その点を図の中央に大きく見せる（回数の変更の比較で、区間の3点を見分けやすく）。
    // 表示の向き・大きさだけ。座標は変えない。区間の外の点は図の外に出ることがある（動きの並びのカードに番号を出す）
    const fp = (data.focus || []).map((n) => data.points.find((p) => p.name === n)).filter((p) => p && p.xyz);
    if (fp.length >= 2) {
      const flo = [0, 1, 2].map((k) => Math.min(...fp.map((p) => p.xyz[k])));
      const fhi = [0, 1, 2].map((k) => Math.max(...fp.map((p) => p.xyz[k])));
      this.center = [0, 1, 2].map((k) => (flo[k] + fhi[k]) / 2);
      const fspan = Math.hypot(fhi[0] - flo[0], fhi[1] - flo[1], fhi[2] - flo[2]);
      this.span = Math.max(fspan * 1.8, this.spanPts * 0.5, 1);
    }
    // 枠に収める割合とずれは、JOB の点（と focus）が変わったときだけ決め直す。選ぶ・候補・元に戻す・再生では図を動かさない
    const fitKey = JSON.stringify([data.points.map((p) => [p.name, p.xyz]), fp.map((p) => p.name)]);
    this.fitFocus = fp.length >= 2;
    if (fitKey !== this.fitKey) { this.fitKey = fitKey; this._fit(this.fitFocus, this.fitView); }
    this.render();
  }

  // 最初の向き（DEFAULT_YAW・DEFAULT_PITCH・拡大なし）で、点・点の名前・床・軸と軸の文字が枠（W×H）に収まるように、
  // 縮める・広げる割合（fitK・0.3〜2.5）と、真ん中に寄せるずれ（fitX・fitY、画面の px）を決める（11/5 夜 F7）。
  // 回す・拡大するときは、この割合とずれのまま（回している間に大きさは変えない）。focus のとき（区間を大きく見せる）は今のまま。
  // JOB の点だけで決める（変更後の点・右上の窓は、出る前から場所を空けておく。候補が出ても図が動かないように）
  // fitView：収める向き（無ければ最初の向き。［正面に戻す］のときは正面の向き [FRONT_YAW, FRONT_PITCH]）
  _fit(focus, fitView = null) {
    this.fitK = 1; this.fitX = 0; this.fitY = 0;
    this.fitView = fitView;
    if (focus) return;
    const view = [this.yaw, this.pitch, this.zoom];
    [this.yaw, this.pitch] = fitView || [DEFAULT_YAW, DEFAULT_PITCH];
    this.zoom = 1;
    const d = this.data, M = 10;
    // 世界座標で決まる所（縮めると近づく）と、そのまわりの文字の余白（px のまま）
    const marks = [];
    const pts = d.points;
    for (const p of pts) {  // 同じ所の点は名前を「 / 」でつないで1つに出す
      const label = pts.filter((q) => q.xyz.every((v, i) => v === p.xyz[i])).map((q) => q.name).join(" / ");
      marks.push([p.xyz, -20, 14 + textWidth(label), -24, 20]);
    }
    const fl = this._floorFrame();
    for (const c of fl.corners) marks.push([c, 0, 0, 0, 0]);
    for (const dv of fl.axes) marks.push([[fl.o[0] + dv[0] * 1.25, fl.o[1] + dv[1] * 1.25, fl.o[2] + dv[2] * 1.25], -28, 28, -18, 18]);
    marks.push([fl.o, -28, 28, -18, 18]);
    const at = Object.fromEntries(d.points.map((p) => [p.name, p.xyz]));
    for (const t of d.taught || []) {  // 人が教えた意味の札（点の上に出す）
      for (const n of t.positions || []) if (at[n] && typeof t.text === "string") {
        const w = textWidth(`✓ ${t.text}（人が教えた）`) + 16;
        marks.push([at[n], -w / 2, w / 2, -44, 0]);
      }
    }
    marks.push([[0, 0, 0], -24, 24, -14, 30]);  // 原点の印（札は描くときにまわりの空いた所を探す）
    marks.push([this._frontTip(), -16, 16 + textWidth(FRONT_LABEL) / 2, -24, 24]);  // 正面の矢印の先と札
    const base = marks.map(([v, ...pad]) => [this.project(v), pad]);
    // 縮める割合 k のときの、文字の余白を含めた広がり（文字の余白は縮まない）
    const box = (k) => {
      const xs = base.map(([q]) => this.W / 2 + (q[0] - this.W / 2) * k), ys = base.map(([q]) => this.H / 2 + (q[1] - this.H / 2) * k);
      return [Math.min(...base.map(([, p], j) => xs[j] + p[0])), Math.max(...base.map(([, p], j) => xs[j] + p[1])),
        Math.min(...base.map(([, p], j) => ys[j] + p[2])), Math.max(...base.map(([, p], j) => ys[j] + p[3]))];
    };
    // 右上の窓（回数の窓・変更の拡大の窓）の下には、点・名前を入れない
    const inset = { x0: this.W - 156, y1: 148 };  // 回数の窓（〜y 104）と変更の拡大の窓（〜y 144）の両方
    // 割合 k で枠に収まり、右上の窓に重ならない、真ん中に最も近いずれ（無ければ null）
    const place = (k) => {
      const b = box(k);
      const ax = [M - b[0], this.W - M - b[1]], ay = [M - b[2], this.H - M - b[3]];
      if (ax[0] > ax[1] || ay[0] > ay[1]) return null;
      const cx = this.W / 2 - (b[0] + b[1]) / 2, cy = this.H / 2 - (b[2] + b[3]) / 2;
      const rects = base.map(([q, p]) => [this.W / 2 + (q[0] - this.W / 2) * k + p[1], this.H / 2 + (q[1] - this.H / 2) * k + p[2]]);
      const free = (ox, oy) => rects.every(([x1, y0]) => x1 + ox <= inset.x0 || y0 + oy >= inset.y1);
      let best = null, bestD = Infinity;
      const N = 24;
      for (let i = 0; i <= N; i++) for (let j = 0; j <= N; j++) {
        const ox = ax[0] + (ax[1] - ax[0]) * i / N, oy = ay[0] + (ay[1] - ay[0]) * j / N;
        const dd = Math.hypot(ox - cx, oy - cy);
        if (dd < bestD && free(ox, oy)) { best = [ox, oy]; bestD = dd; }
      }
      if (best && free(cx, cy)) best = [cx, cy];
      return best;
    };
    let k = 1, off = place(1);
    if (off) {  // 枠に余りがあれば大きくする（最大 2.5 倍。原点を入れると点が小さくなるため。2026-11-05 夜）
      let lo = 1, hi = 2.5;
      if (place(hi)) lo = hi;
      else for (let i = 0; i < 14; i++) { const mid = (lo + hi) / 2; if (place(mid)) lo = mid; else hi = mid; }
      k = lo; off = place(lo);
    } else {  // 収まる最も大きい割合（0.3 より小さくはしない）
      let lo = 0.3, hi = 1;
      for (let i = 0; i < 16; i++) { const mid = (lo + hi) / 2; if (place(mid)) lo = mid; else hi = mid; }
      k = lo; off = place(lo) || [this.W / 2 - (box(lo)[0] + box(lo)[1]) / 2, this.H / 2 - (box(lo)[2] + box(lo)[3]) / 2];
    }
    this.fitK = k;
    [this.fitX, this.fitY] = off;
    [this.yaw, this.pitch, this.zoom] = view;
  }

  // 最初の向き・大きさに戻す（確認表示の図で使う。初期の視点は今のまま）
  reset() {
    this.yaw = DEFAULT_YAW; this.pitch = DEFAULT_PITCH; this.zoom = 1;
    if (this.data && this.fitView) this._fit(this.fitFocus, null);
    this.closeChooser();
    this.render();
    this.onView && this.onView();
  }

  // ［正面に戻す］：X＋ の側から水平に見る（右＝Y＋・上＝Z＋・手前＝X＋）。拡大なし。正面の向きで枠に収め直す
  front() {
    this.yaw = FRONT_YAW; this.pitch = FRONT_PITCH; this.zoom = 1;
    if (this.data) this._fit(this.fitFocus, [FRONT_YAW, FRONT_PITCH]);
    this.closeChooser();
    this.render();
    this.onView && this.onView();
  }

  // 今のカメラの向き（確かめ用。表示だけ）
  camera() {
    return { yaw: this.yaw, pitch: this.pitch, zoom: this.zoom };
  }

  // 最初の向き・大きさから変えたか
  changed() {
    return this.yaw !== DEFAULT_YAW || this.pitch !== DEFAULT_PITCH || this.zoom !== 1;
  }

  // 世界座標 → 画面（x, y, 奥行き）
  project(v) {
    const x = v[0] - this.center[0], y = v[1] - this.center[1], z = v[2] - this.center[2];
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const x1 = x * cy - y * sy, y1 = x * sy + y * cy;
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const up = z * cp + y1 * sp, depth = y1 * cp - z * sp;
    const D = this.span * 2.6;
    const f = D / (D + depth);
    const k = (Math.min(this.W, this.H) * 0.82 / this.span) * (this.fitK || 1);
    // 拡大は図の真ん中を中心に（ずれ fitX・fitY も一緒に大きくする）
    return [this.W / 2 + (x1 * k * f + (this.fitX || 0)) * this.zoom, this.H / 2 + (-up * k * f + (this.fitY || 0)) * this.zoom, depth];
  }

  render() {
    if (!this.data) return;
    const d = this.data;
    const svg = svgEl("svg", { viewBox: `0 0 ${this.W} ${this.H}`, width: this.W, height: this.H,
      role: "img", "aria-label": "3D の点と経路（ドラッグで回す・ホイールで拡大縮小）" });
    this._floor(svg);
    this._baseMarks(svg);  // 台座の目印・正面の矢印（点・経路の下）
    const c = d.after;
    const stopped = !!(c && d.stopped);
    const cAfter = stopped ? C_STOPPED : C_AFTER, cAfterText = stopped ? C_STOPPED : C_AFTER_TEXT;
    // 経路（命令の順に結んだ直線）
    const lines = { before: [], after: [] };
    const seg = (s, pts, color, w, key) => {
      const at = Object.fromEntries(pts.map((p) => [p.name, p.xyz]));
      const a = at[s.from], b = at[s.to];
      if (!a || !b) return;
      const p = this.project(a), q = this.project(b);
      const look = segmentLook(s.kind);
      const playing = this.marker && this.marker.seg === s;  // 再生中の区間は少し太く
      const line = svgEl("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1],
        stroke: look.color && key === "before" ? look.color : color, "stroke-width": w * look.width + (playing ? 1.5 : 0),
        "stroke-linecap": "round", class: `seg ${key} seg-${look.kind}`, "data-kind": look.kind, "data-from": s.from, "data-to": s.to });
      if (look.dash) line.setAttribute("stroke-dasharray", look.dash);
      // 回数の変更の範囲を確かめている間：その範囲の線を光らせる（会話の位置の候補と同じ色）
      if (key === "before" && (d.rangeLines || []).includes(s.line_no)) {
        line.setAttribute("stroke", "#a99cff"); line.setAttribute("stroke-width", "5"); line.classList.add("range");
      }
      // 止まった候補は、目安の区間（6 5 の破線）・MOVJ の点線と見分けられる細かい点線
      if (key === "after" && stopped) { line.setAttribute("stroke-dasharray", "0.5 7"); line.setAttribute("stroke-width", "4"); }
      const t = svgEl("title", {}); t.textContent = segmentTitle(s);
      line.append(t);
      svg.append(line);
      lines[key].push(line);
    };
    // 「Claude に聞く」の答えの根拠の区間（サーバーが行番号から決めた highlight.lines）を、下に太い色の帯で光らせる。
    // MOVJ・目安の区間は点線・破線のまま（直線の通り道に見せない）
    // 前後の移動（強調の区間の外の根拠の行）は、控えめな色で先に描く（強調の区間の下になる）
    for (const s of d.segments) {
      if (!isAskHighlighted(s, d.askContext) || isAskHighlighted(s, d.askLines)) continue;
      const at = Object.fromEntries(d.points.map((p) => [p.name, p.xyz]));
      if (!at[s.from] || !at[s.to]) continue;
      const p = this.project(at[s.from]), q = this.project(at[s.to]);
      const look = askContextLook(s.kind);
      const ctx = svgEl("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1], stroke: look.color, "stroke-width": look.width,
        "stroke-opacity": look.opacity, "stroke-linecap": "round", class: `ask-hl-context seg-${look.kind}`, "data-line": s.line_no });
      if (look.dash) ctx.setAttribute("stroke-dasharray", look.dash);
      svg.append(ctx);
    }
    for (const s of d.segments) {
      if (!isAskHighlighted(s, d.askLines)) continue;
      const at = Object.fromEntries(d.points.map((p) => [p.name, p.xyz]));
      if (!at[s.from] || !at[s.to]) continue;
      const p = this.project(at[s.from]), q = this.project(at[s.to]);
      const look = askHighlightLook(s.kind);
      const glow = svgEl("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1], stroke: look.color, "stroke-width": look.width,
        "stroke-opacity": look.opacity, "stroke-linecap": "round", class: `ask-hl seg-${look.kind}`, "data-line": s.line_no });
      if (look.dash) glow.setAttribute("stroke-dasharray", look.dash);
      svg.append(glow);
    }
    for (const s of d.segments) seg(s, d.points, c ? "#7f8ba3" : "#c9d6ec", 2.5, "before");
    // 変更後は、動かす点に触れる区間だけ（ほかの区間は変更前と同じ線なので描き直さない）
    if (c && !d.repeat) for (const s of c.segments) if (s.from === c.moved || s.to === c.moved) seg(s, c.points, cAfter, 3, "after");
    // 回数の変更：足した回の線だけを変更後の色で重ねる（元からある線と同じ線。新しい線は無い）
    // 再生で変更前を見せている間は重ねない（変更前は2回目まで）。変更後の再生で今の線は、光の輪を付けて太く
    if (c && d.repeat && d.repeat.after > d.repeat.before && !(this.marker && this.marker.runKey === "before")) {
      for (const s of c.segments) {
        if (s.cycle !== d.repeat.after) continue;
        if (this.marker && this.marker.seg === s) this._glow(svg, s, c.points, cAfter);
        seg(s, c.points, cAfter, 3, "after");
      }
    }
    this.lines = lines;

    // 点（奥から手前の順に描く）。近く見える点は1つの印にまとめ、押すと選び分ける
    const items = d.points.map((p) => ({ p, s: this.project(p.xyz) }));
    items.sort((a, b) => b.s[2] - a.s[2]);
    const groups = [];
    for (const it of items) {
      const g = groups.find((q) => Math.hypot(q.s[0] - it.s[0], q.s[1] - it.s[1]) < OVERLAP_PX);
      if (g) g.names.push(it.p.name); else groups.push({ s: it.s, names: [it.p.name], p: it.p });
    }
    const moved = c ? c.moved : null;
    const labelBoxes = [];  // 置いた名前の四角（変更後の名前を重ならない所に置くため）
    for (const g of groups) {
      const [x, y] = g.s;
      const sel = g.names.includes(d.selected);
      const isMoved = g.names.includes(moved);
      const el = svgEl("g", { class: "pt3" + (sel ? " sel" : ""), "data-names": g.names.join(" ") });
      // 床への縦の補助線（奥行きを分かりやすくする）
      const floor = this.project([g.p.xyz[0], g.p.xyz[1], this.z0]);
      svg.insertBefore(svgEl("line", { x1: x, y1: y, x2: floor[0], y2: floor[1], stroke: "#3a4d72",
        "stroke-width": 1, "stroke-dasharray": "2 3" }), svg.firstChild.nextSibling);
      // 押しやすい大きさ。近くにほかの点があるときは、その点の真ん中を覆わない大きさ（原点も図に入れると点が近く見えるため）
      const near = Math.min(Infinity, ...groups.filter((q) => q !== g).map((q) => Math.hypot(q.s[0] - x, q.s[1] - y)));
      el.append(svgEl("circle", { cx: x, cy: y, r: Math.max(7, Math.min(16, near / 2 - 0.5)), fill: "transparent" }));
      if (sel) el.append(svgEl("circle", { cx: x, cy: y, r: 12, fill: "rgba(77,163,255,0.18)", stroke: "#4da3ff", "stroke-width": 2 }));
      // 会話で Claude（モック）が出した位置の候補：選んではいないので、選んだ点（青）と違う色の点線の輪で示す
      if (g.names.some((n) => (d.candidates || []).includes(n))) {
        el.classList.add("cand");
        el.append(svgEl("circle", { cx: x, cy: y, r: 19, fill: "rgba(169,156,255,0.12)", stroke: "#a99cff",
          "stroke-width": 2.5, "stroke-dasharray": "5 4", class: "cand-ring" }));
      }
      el.append(svgEl("circle", { cx: x, cy: y, r: 6, fill: isMoved ? "#7f8ba3" : (sel ? "#4da3ff" : "#e3ebf8") }));
      // 動かす点の名前は、変更後の点の横に1つだけ出す（変更前の名前と重ならないように）
      const shownNames = isMoved && c ? g.names.filter((n) => n !== moved) : g.names;
      if (shownNames.length) {
        const label = svgEl("text", { x: x + 12, y: y - 8, fill: sel && !isMoved ? "#7cbcff" : "#b3c0d8",
          "font-weight": sel && !isMoved ? "600" : "400" });
        label.textContent = shownNames.join(" / ");
        // 名前の置き場所：右上（今まで）。先に置いた名前・ほかの点に重なるときは、右下・左上・左下・もう少し上・下の順に
        // 空いている所（枠の中）。原点も図に入れると点が近く見えるため（2026-11-05 夜）
        const tw = textWidth(label.textContent);
        const others = groups.filter((q) => q !== g).map((q) => ({ x0: q.s[0] - 9, x1: q.s[0] + 9, y0: q.s[1] - 9, y1: q.s[1] + 9 }));
        const cands = [[x + 12, y - 8, "start"], [x + 12, y + 16, "start"], [x - 12, y - 8, "end"], [x - 12, y + 16, "end"],
          [x + 12, y - 22, "start"], [x + 12, y + 30, "start"]];
        const boxOf = ([lx, ly, an]) => (an === "start" ? { x0: lx, x1: lx + tw, y0: ly - 12, y1: ly + 4 } : { x0: lx - tw, x1: lx, y0: ly - 12, y1: ly + 4 });
        const inFrame = (b) => b.x0 >= 4 && b.x1 <= this.W - 4 && b.y0 >= 4 && b.y1 <= this.H - 4;
        const spot = cands.find((cd) => inFrame(boxOf(cd)) && !labelBoxes.concat(others).some((q) => overlaps(boxOf(cd), q))) || cands[0];
        label.setAttribute("x", spot[0]); label.setAttribute("y", spot[1]);
        if (spot[2] === "end") label.setAttribute("text-anchor", "end");
        const lb = boxOf(spot);
        // 名前がほかの点の上にかかるときは、名前を押してもその点を押せるように（名前は押しても選ばない）
        if (others.some((q) => overlaps(lb, q))) label.setAttribute("pointer-events", "none");
        el.append(label);
        labelBoxes.push(lb);
      }
      labelBoxes.push({ x0: x - 8, x1: x + 8, y0: y - 8, y1: y + 8 });  // 点そのもの
      const t = svgEl("title", {});
      t.textContent = g.names.map((n) => {
        const p = d.points.find((q) => q.name === n);
        return `${n}  ${p.line}`;
      }).join("\n");
      el.append(t);
      svg.append(el);
    }
    // 変更後の点と、変更前からの矢印（小さな変更でも見えるように）
    if (c) {
      const q = c.points.find((p) => p.name === moved);
      const p0 = d.points.find((p) => p.name === moved);
      if (q && p0) {
        const a = this.project(p0.xyz), b = this.project(q.xyz);
        svg.append(svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: cAfter, "stroke-width": 2 }));
        svg.append(svgEl("circle", { cx: b[0], cy: b[1], r: 7, fill: cAfter, class: "after-pt" }));
        svg.append(svgEl("circle", { cx: b[0], cy: b[1], r: 14, fill: "none", stroke: cAfter, "stroke-width": 1.5, "stroke-opacity": 0.5 }));
        if (stopped) {
          // 止まった候補（変更できない案）：変更後の点に ✕ を重ね、名前の横に「変更できません」と書く（11/5 残件3。色は灰色のまま）
          for (const [dx, dy] of [[1, 1], [1, -1]]) {
            svg.append(svgEl("line", { x1: b[0] - 9 * dx, y1: b[1] - 9 * dy, x2: b[0] + 9 * dx, y2: b[1] + 9 * dy,
              stroke: C_STOPPED, "stroke-width": 3, "stroke-linecap": "round", class: "stopped-x" }));
          }
        }
        const text = stopped ? `✕ ${moved} 変更できません` : `${moved} ${displayMinus(d.delta_label || "")}`.trim();
        const tw = textWidth(text);
        // 同じ所にほかの位置（共用の座標など）の名前があっても重ならないように、右下・左・右上・下の順に空いている所を探す
        const spots = [[b[0] + 14, b[1] + 18, "start"], [b[0] - 20, b[1] + 4, "end"], [b[0] + 14, b[1] - 22, "start"],
          [b[0] - 20, b[1] + 22, "end"], [b[0] + 14, b[1] + 36, "start"], [b[0] - 20, b[1] - 18, "end"]];
        const box = ([x, y, anchor]) => (anchor === "start"
          ? { x0: x, x1: x + tw, y0: y - 13, y1: y + 3 } : { x0: x - tw, x1: x, y0: y - 13, y1: y + 3 });
        const spot = spots.find((s) => !labelBoxes.some((q) => overlaps(box(s), q))) || spots[0];
        const lab = svgEl("text", { x: spot[0], y: spot[1], "text-anchor": spot[2], fill: cAfterText, "font-weight": "600", class: "moved-label" });
        lab.textContent = text;
        labelBoxes.push(box(spot));  // 回数の札（_repeatMark）が、動かす点の名前と重ならないように
        svg.append(lab);
        this._inset(svg, p0.xyz, q.xyz, displayMinus(d.delta_label || ""), cAfter, cAfterText);
      }
    }
    if (d.repeat) this._repeatMark(svg, d, labelBoxes, cAfterText);
    if (this.marker && this.marker.dest) this._destMark(svg, this.marker.dest, this.marker.color);
    if ((d.taught || []).length) this._taughtMarks(svg, d, labelBoxes);
    if (this.marker && this.marker.xyz) {  // MOVJ・目安の区間の間は xyz が無い（印を出さない）
      const m = this.project(this.marker.xyz);
      svg.append(svgEl("circle", { cx: m[0], cy: m[1], r: 9, fill: this.marker.color, stroke: "#fff", "stroke-width": 3, class: "marker" }));
      // 何回目かは図の上の帯（いま：…）と動きの並びに出す（印の横には出さない。重複を減らす）
    }
    this._robotLabels(svg, labelBoxes);  // 台座と正面の札（ほかの札の置き場所が決まってから、空いた所に）
    this.host.querySelector("svg")?.remove();
    this.host.prepend(svg);
    this.svg = svg;
  }

  // 正面の矢印の先（世界座標。X＋ の向き。長さは表示の目安で、ロボットの大きさではない）
  _frontTip() {
    return [this.span * 0.22, 0, 0];
  }

  // 見る位置より手前に出る所（投影が崩れる所）か。区間を大きく見せる図（focus）で、遠くの原点の印を描かないため
  _behind(q) {
    return q[2] <= -0.8 * this.span * 2.6;
  }

  // 台座の目印（原点の印と、床の面 Z=0 の点線の輪）と、正面の矢印（原点から X＋ へ。太い線と矢じり）
  _baseMarks(svg) {
    this.baseAt = null;
    const o = this.project([0, 0, 0]), t = this.project(this._frontTip());
    if (this._behind(o) || this._behind(t)) return;
    const g = svgEl("g", { class: "robot-base", "pointer-events": "none" });
    const R = this.span * 0.07;  // 輪の大きさは目安（ロボットの大きさではない）
    const ring = Array.from({ length: 36 }, (_, i) => { const a = (i / 36) * Math.PI * 2; return this.project([R * Math.cos(a), R * Math.sin(a), 0]); });
    if (!ring.some((q) => this._behind(q))) {
      g.append(svgEl("polygon", { points: ptsAttr(ring), fill: "none", stroke: C_ROBOT, "stroke-width": 1.5, "stroke-dasharray": "6 4" }));
    }
    if (this.z0 < 0) {  // 床が台座の面より下のとき：床までの縦の補助線（点と同じ）
      const f = this.project([0, 0, this.z0]);
      g.append(svgEl("line", { x1: o[0], y1: o[1], x2: f[0], y2: f[1], stroke: C_ROBOT, "stroke-width": 1, "stroke-dasharray": "2 3" }));
    }
    g.append(svgEl("line", { x1: o[0], y1: o[1], x2: t[0], y2: t[1], stroke: C_ROBOT, "stroke-width": 4, "stroke-linecap": "round", class: "robot-front" }));
    const len = Math.hypot(t[0] - o[0], t[1] - o[1]) || 1, ux = (t[0] - o[0]) / len, uy = (t[1] - o[1]) / len;
    g.append(svgEl("path", { d: `M ${t[0] + ux * 12} ${t[1] + uy * 12} L ${t[0] - ux * 4 - uy * 8} ${t[1] - uy * 4 + ux * 8} `
      + `L ${t[0] - ux * 4 + uy * 8} ${t[1] - uy * 4 - ux * 8} Z`, fill: C_ROBOT, class: "robot-front-head" }));
    g.append(svgEl("circle", { cx: o[0], cy: o[1], r: 5, fill: C_ROBOT, stroke: "#08101f", "stroke-width": 1.5, class: "robot-origin" }));
    const tt = svgEl("title", {}); tt.textContent = BASE_NOTE;
    g.append(tt);
    svg.append(g);
    this.baseAt = { o, t };
  }

  // 札：「ロボット（台座の中心）」・「ロボットの正面（X＋）」。
  // 点・点の名前・変更の札・回数の札・人が教えた札（boxes）・右上の窓・軸の文字に重ならず、枠の中に収まる所に置く
  _robotLabels(svg, boxes) {
    const d = this.data;
    if (!this.baseAt) return;
    const taken = boxes.concat(this._axisBoxes || [],
      d.points.map((p) => this.project(p.xyz)).map(([x, y]) => ({ x0: x - 12, x1: x + 12, y0: y - 12, y1: y + 12 })));
    if (d.after || d.repeat) taken.push({ x0: this.W - 156, x1: this.W, y0: 0, y1: 148 });  // 右上の窓
    taken.push({ x0: 0, x1: 210, y0: this.H - 46, y1: this.H });  // 左下の「格子 …mm」と操作のヒント
    if (d.after) {
      const q = d.after.points.find((p) => p.name === d.after.moved);
      if (q) { const [x, y] = this.project(q.xyz); taken.push({ x0: x - 16, x1: x + 16, y0: y - 16, y1: y + 16 }); }
    }
    const { o, t } = this.baseAt;
    for (const [text, at] of [[BASE_LABEL, o], [FRONT_LABEL, t]]) {
      const spot = findSpot(at[0], at[1], textWidth(text), taken, this.W, this.H);
      if (!spot) continue;  // 枠の中に置ける所が無い（区間を大きく見せる図など）：札は出さない（切れた文字を出さない）
      svg.append(labelEl(spot[0], spot[1], text, C_ROBOT, "robot-label"));
      taken.push(spot[2]);
      boxes.push(spot[2]);
    }
  }

  // 人が教えた意味（「Claude に聞く」の欄。サーバーのメモリの中だけ）：対象の点の上に、緑の印と文字で出す。
  // AI の推定とは別の印（緑・実線・✓）。位置はジョブの点の座標の真ん中（描くだけ。変更の範囲には使わない）
  // 札は、点（候補の輪の大きさ）・点の名前・動かす点の名前・回数の札（boxes）・右上の窓に重ならず、図の中に収まる所に置く
  // （まず点の上の真ん中。重なるなら上・横に少しずつずらし、それでも無ければ点の下。11/5 夜 G6）。点と図は動かさない
  _taughtMarks(svg, d, boxes = []) {
    const at = Object.fromEntries(d.points.map((p) => [p.name, p.xyz]));
    const inset = { x0: this.W - 156, x1: this.W, y0: 0, y1: 148 };  // 回数の窓・変更の拡大の窓
    const dots = d.points.map((p) => this.project(p.xyz)).map(([px, py]) => ({ x0: px - 20, x1: px + 20, y0: py - 20, y1: py + 20 }));
    for (const t of d.taught) {
      const pts = (t.positions || []).map((n) => at[n]).filter(Boolean);
      if (!pts.length || typeof t.text !== "string") continue;
      const s = pts.map((v) => this.project(v));
      const label = `✓ ${t.text}（人が教えた）`;
      const w = textWidth(label) + 16;
      const cx = s.reduce((a, q) => a + q[0], 0) / s.length;
      const top = Math.min(...s.map((q) => q[1])), bottom = Math.max(...s.map((q) => q[1]));
      const box = ([x, y]) => ({ x0: x - w / 2 - 3, x1: x + w / 2 + 3, y0: y - 18, y1: y + 10 });  // 札（3px の間をあける）
      const inside = (q) => q.x0 >= 5 && q.x1 <= this.W - 5 && q.y0 >= 5 && q.y1 <= this.H - 5;
      const free = (sp) => inside(box(sp)) && !boxes.concat(dots).some((q) => overlaps(box(sp), q)) && !overlaps(box(sp), inset);
      const spots = [];
      for (const base of [top - 26, bottom + 44]) {
        for (let dy = 0; dy <= 72; dy += 6) for (let dx = 0; dx <= w / 2 + 60; dx += 12) {
          const y = base < top ? base - dy : base + dy;
          spots.push([cx - dx, y, Math.hypot(dx, dy) + (base < top ? 0 : 1000)], [cx + dx, y, Math.hypot(dx, dy) + (base < top ? 0 : 1000) + 0.5]);
        }
      }
      spots.sort((a, b) => a[2] - b[2]);
      const [x, y] = spots.find(free) || spots.find((sp) => inside(box(sp))) || [cx, top - 26];
      const g = svgEl("g", { class: "taught-mark" });
      g.append(svgEl("rect", { x: x - w / 2, y: y - 15, width: w, height: 22, rx: 11, fill: "#12301f", stroke: "#5fd19a", "stroke-width": 1.5 }));
      const tx = svgEl("text", { x, y: y + 1, "text-anchor": "middle", fill: "#c9f2dc", "font-weight": "700" });
      tx.textContent = label;
      g.append(tx);
      svg.append(g);
      boxes.push(box([x, y]));  // 札が2つあっても重ならないように
    }
  }

  // 回数の変更の印：区間の点の近くに「C00001〜C00003 ×2 → ×3」（読み込んだ直後は「×2」）、右上の窓に回数。
  // 数はサーバーの文字のまま。文字は図の中に収まる所に置く（はみ出すなら別の所。QA P1-4）
  _repeatMark(svg, d, boxes, textColor) {
    const r = d.repeat;
    const pts = d.points.filter((p) => r.positions.includes(p.name)).map((p) => this.project(p.xyz));
    if (pts.length) {
      const text = `${r.target} ${r.label}`, tw = Math.ceil(textWidth(text) * 1.25);
      const right = Math.max(...pts.map((q) => q[0])), left = Math.min(...pts.map((q) => q[0]));
      const top = Math.min(...pts.map((q) => q[1])), bottom = Math.max(...pts.map((q) => q[1]));
      const box = ([x, y]) => ({ x0: x, x1: x + tw, y0: y - 14, y1: y + 4 });
      const inside = (q) => q.x0 >= 8 && q.x1 <= this.W - 8 && q.y0 >= 8 && q.y1 <= this.H - 8;
      // 点・点の名前・右上の窓に重ならず、図の中に収まる所を探す（右・右下・左・下・上の順）
      const inset = { x0: this.W - 156, x1: this.W, y0: 0, y1: 110 };
      const spots = [[right + 90, (top + bottom) / 2], [right + 24, bottom + 24], [left - tw - 24, (top + bottom) / 2],
        [left - tw / 2, bottom + 40], [left - tw / 2, top - 30], [left - tw - 24, bottom + 24], [12, this.H - 44]];
      const spot = spots.find((s) => inside(box(s)) && !boxes.some((q) => overlaps(box(s), q)) && !overlaps(box(s), inset))
        || spots.find((s) => inside(box(s))) || [12, this.H - 44];
      const lab = svgEl("text", { x: spot[0], y: spot[1], fill: textColor, "font-weight": "700", "data-fs": 13, class: "repeat-label" });
      lab.textContent = text;
      svg.append(lab);
      boxes.push(box(spot));
    }
    // 右上の窓：変更後の回数（×3）を大きく、変更前（×2）を灰色で小さく。候補が無ければ今の回数だけ
    const S = 140, x0 = this.W - S - 12, y0 = 12;
    const g = svgEl("g", { class: "inset repeat-inset" });
    g.append(svgEl("rect", { x: x0, y: y0, width: S, height: 92, rx: 14, fill: "#101c33", stroke: "#2b3f66" }));
    const head = svgEl("text", { x: x0 + 12, y: y0 + 22, class: "inset-head", "data-fs": 12 });
    head.textContent = "区間を通る回数";
    g.append(head);
    if (r.after != null) {
      const was = svgEl("text", { x: x0 + 14, y: y0 + 70, fill: "#a3afc6", "data-fs": 18, "font-weight": "600", class: "repeat-was" });
      was.textContent = `×${r.before} →`;
      const now = svgEl("text", { x: x0 + S - 14, y: y0 + 74, "text-anchor": "end", fill: textColor, "data-fs": 34,
        "font-weight": "800", class: "repeat-now" });
      now.textContent = `×${r.after}`;
      g.append(was, now);
    } else {
      const now = svgEl("text", { x: x0 + S / 2, y: y0 + 72, "text-anchor": "middle", fill: "#d5deed", "data-fs": 30,
        "font-weight": "700", class: "repeat-now" });
      now.textContent = `×${r.before}`;
      g.append(now);
    }
    svg.append(g);
  }

  // 再生中の区間の行き先の点の上の小さな印（▼）。点の上だけに出す（線の上は動かさない。MOVJ でも同じ）
  _destMark(svg, xyz, color) {
    const [x, y] = this.project(xyz);
    svg.append(svgEl("path", { d: `M ${x - 7} ${y - 25} L ${x + 7} ${y - 25} L ${x} ${y - 14} Z`, fill: color,
      stroke: "#fff", "stroke-width": 1.5, class: "dest-mark" }));
  }

  // 変更後の再生で、足した回の今の線に付ける光の輪（線の下に太く薄く）
  _glow(svg, s, pts, color) {
    const at = Object.fromEntries(pts.map((p) => [p.name, p.xyz]));
    if (!at[s.from] || !at[s.to]) return;
    const p = this.project(at[s.from]), q = this.project(at[s.to]);
    svg.append(svgEl("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1], stroke: color, "stroke-width": 14,
      "stroke-opacity": 0.28, "stroke-linecap": "round", class: "seg-glow" }));
  }

  // 拡大の窓：変更前（灰）と変更後（オレンジ）を大きくして並べる。小さな変更も見えるように
  _inset(svg, a, b, label, color = C_AFTER, textColor = C_AFTER_TEXT) {
    const S = 132, x0 = this.W - S - 12, y0 = 12;
    const pa = this.project(a), pb = this.project(b);
    const dx = pb[0] - pa[0], dy = pb[1] - pa[1];
    const len = Math.hypot(dx, dy);
    const g = svgEl("g", { class: "inset" });
    g.append(svgEl("rect", { x: x0, y: y0, width: S, height: S, rx: 14, fill: "#101c33", stroke: "#2b3f66" }));
    const cx = x0 + S / 2, cy = y0 + S / 2 + 12;
    let ux = 0, uy = -1;
    if (len > 0.01) { ux = dx / len; uy = dy / len; }
    const L = 24;  // 窓の中では、向きはそのまま・長さは決まった大きさで見せる
    const ax = cx - ux * L, ay = cy - uy * L, bx = cx + ux * L, by = cy + uy * L;
    g.append(svgEl("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: color, "stroke-width": 3, "stroke-linecap": "round" }));
    g.append(svgEl("circle", { cx: ax, cy: ay, r: 7, fill: "#7f8ba3" }));
    g.append(svgEl("circle", { cx: bx, cy: by, r: 8, fill: color }));
    const head = svgEl("text", { x: x0 + 12, y: y0 + 20, class: "inset-head" });
    head.textContent = "変更（拡大）";
    const sub = svgEl("text", { x: x0 + 12, y: y0 + 34, class: "inset-head" });
    sub.textContent = "長さは実寸ではありません";
    g.append(sub);
    const amt = svgEl("text", { x: x0 + S / 2, y: y0 + S - 12, "text-anchor": "middle", fill: textColor, "font-weight": "700", class: "inset-amt" });
    amt.textContent = label;
    g.append(head, amt);
    svg.append(g);
  }

  // 床の格子と、X・Y・Z の軸（向きを知るため）
  // 床の四隅・軸の根元（点から遠く見える角）・軸の向き。描く（_floor）のと、枠に収める（_fit）のに使う
  _floorFrame() {
    const z0 = this.z0;
    const pad = this.span * 0.06;
    const x0 = this.lo[0] - pad, x1 = this.hi[0] + pad, y0 = this.lo[1] - pad, y1 = this.hi[1] + pad;
    const step = niceStep(Math.max(x1 - x0, y1 - y0));
    const pts = this.data.points.map((p) => this.project(p.xyz));
    const mx = pts.reduce((a, q) => a + q[0], 0) / pts.length, my = pts.reduce((a, q) => a + q[1], 0) / pts.length;
    const corners = [[x0, y0, z0], [x1, y0, z0], [x0, y1, z0], [x1, y1, z0]];
    const far = (c) => { const q = this.project(c); return Math.hypot(q[0] - mx, q[1] - my); };
    const o = corners.reduce((a, c) => (far(c) > far(a) ? c : a));
    const sx = o[0] === x0 ? 1 : -1, sy = o[1] === y0 ? 1 : -1;  // 角から床の内側へ向けて描く（＋の向きは矢印の先の文字で示す）
    const axes = [[sx * step, 0, 0], [0, sy * step, 0], [0, 0, step]];
    return { z0, x0, x1, y0, y1, step, pts, corners, o, sx, sy, axes };
  }

  _floor(svg) {
    const { z0, x0, x1, y0, y1, step, pts, o, sx, sy } = this._floorFrame();
    const g = svgEl("g", { class: "floor" });
    const line = (a, b, attrs) => {
      const p = this.project(a), q = this.project(b);
      g.append(svgEl("line", Object.assign({ x1: p[0], y1: p[1], x2: q[0], y2: q[1] }, attrs)));
    };
    for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) line([x, y0, z0], [x, y1, z0], { stroke: "#1b2a47", "stroke-width": 1 });
    for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) line([x0, y, z0], [x1, y, z0], { stroke: "#1b2a47", "stroke-width": 1 });
    // 軸（床の角から、格子1目盛りの長さ）
    const axes = [["X", [sx * step, 0, 0], "#ff7b7f"], ["Y", [0, sy * step, 0], "#4fd08f"], ["Z", [0, 0, step], "#5aa9ff"]];
    // 軸の文字は、点と点の名前に重ならない所に置く（軸の線の上を、先の方から根元の方へずらして探す）
    const taken = pts.flatMap((q) => [
      { x0: q[0] - 9, x1: q[0] + 9, y0: q[1] - 9, y1: q[1] + 9 },          // 点
      { x0: q[0] + 10, x1: q[0] + 110, y0: q[1] - 22, y1: q[1] - 2 }]);    // 点の名前（右上）
    const hit = (r) => taken.some((b) => r.x0 < b.x1 && r.x1 > b.x0 && r.y0 < b.y1 && r.y1 > b.y0);
    this._axisBoxes = [];
    for (const [name, dv, color] of axes) {
      const end = [o[0] + dv[0], o[1] + dv[1], o[2] + dv[2]];
      line(o, end, { stroke: color, "stroke-width": 2, class: "axis-line", "data-axis": name,
        "data-sign": name === "X" ? sx : name === "Y" ? sy : 1 });
      let at = null;
      for (const f of [1, 0.8, 0.6, 0.4, 1.25]) {
        const p = this.project([o[0] + dv[0] * f, o[1] + dv[1] * f, o[2] + dv[2] * f]);
        for (const [ox, oy] of [[4, -4], [-26, -4], [4, 14], [-26, 14]]) {
          const r = { x0: p[0] + ox, x1: p[0] + ox + 22, y0: p[1] + oy - 12, y1: p[1] + oy + 2 };
          if (!hit(r)) { at = [p[0] + ox, p[1] + oy]; break; }
        }
        if (at) break;
      }
      if (!at) { const p = this.project(end); at = [p[0] + 4, p[1] - 4]; }
      const t = svgEl("text", { x: at[0], y: at[1], fill: color, class: "axis3", "font-weight": "600" });
      t.textContent = name + (name === "X" ? (sx > 0 ? "+" : "−") : name === "Y" ? (sy > 0 ? "+" : "−") : "+");
      taken.push({ x0: at[0], x1: at[0] + 22, y0: at[1] - 12, y1: at[1] + 2 });
      this._axisBoxes.push({ x0: at[0], x1: at[0] + 22, y0: at[1] - 12, y1: at[1] + 2 });
      g.append(t);
    }
    const s = svgEl("text", { x: 12, y: this.H - 12, class: "axis" });
    s.textContent = `格子 ${step}mm`;
    g.append(s);
    if (this.hint && !this.interacted) {
      const hint = svgEl("text", { x: 12, y: this.H - 28, class: "hint3" });
      hint.textContent = "ドラッグで回す・ホイールで拡大";
      g.append(hint);
    }
    svg.append(g);
  }

  _pick(names, ev) {
    if (this.dragged || !this.onPick) return;
    ev.stopPropagation();
    if (names.length === 1) { this.closeChooser(); this.onPick(names[0]); return; }
    this.openChooser(names, ev);
  }

  // 重なって見える点の選び分け
  openChooser(names, ev) {
    this.closeChooser();
    const box = document.createElement("div");
    box.className = "chooser";
    const r = this.host.getBoundingClientRect();
    box.style.left = `${ev.clientX - r.left + 10}px`;
    box.style.top = `${ev.clientY - r.top + 10}px`;
    const head = document.createElement("p");
    head.textContent = "重なっている位置";
    const x = document.createElement("button");
    x.type = "button"; x.className = "close"; x.textContent = "×"; x.setAttribute("aria-label", "閉じる");
    x.addEventListener("click", (e) => { e.stopPropagation(); this.closeChooser(); });
    head.append(x);
    box.append(head);
    const blocked = (this.data && this.data.blocked) || {};
    for (const n of names) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = n + (blocked[n] ? "（変更できません）" : "");
      b.dataset.name = n;
      if (blocked[n]) { b.classList.add("blocked"); b.title = "この位置は変更できません：" + blocked[n]; }
      b.addEventListener("click", (e) => { e.stopPropagation(); this.closeChooser(); this.onPick(n); });
      box.append(b);
    }
    this.host.append(box);
    this.chooser = box;
  }

  closeChooser() {
    if (this.chooser) this.chooser.remove();
    this.chooser = null;
  }

  _bind() {
    const h = this.host;
    let start = null;
    h.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".chooser")) return;
      start = { x: ev.clientX, y: ev.clientY, yaw: this.yaw, pitch: this.pitch };
      this.dragged = false;
      const pt = ev.target.closest && ev.target.closest(".pt3");
      this.downNames = pt ? pt.dataset.names.split(" ") : null;  // 再生中に作り直されても選べるように
    });
    h.addEventListener("pointermove", (ev) => {
      if (!start || !(ev.buttons & 1)) return;
      const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
      if (!this.dragged && Math.hypot(dx, dy) < 4) return;
      if (!this.dragged) { this.dragged = true; this.interacted = true; this.closeChooser(); h.setPointerCapture?.(ev.pointerId); }
      this.yaw = start.yaw + dx * 0.006;
      this.pitch = Math.max(0.05, Math.min(1.3, start.pitch + dy * 0.006));  // 床が裏返らない範囲
      this.render();
      this.onView && this.onView();
    });
    const end = () => { start = null; this.downNames = null; setTimeout(() => { this.dragged = false; }, 0); };
    h.addEventListener("pointerup", (ev) => {
      if (start && !this.dragged && this.downNames) {
        this.justPicked = true;
        this._pick(this.downNames, ev);
      }
      end();
    });
    h.addEventListener("pointercancel", end);
    h.addEventListener("wheel", (ev) => {
      if (!this.data) return;
      ev.preventDefault();
      this.interacted = true;
      this.closeChooser();
      this.zoom = Math.max(0.6, Math.min(5, this.zoom * Math.exp(-ev.deltaY * 0.0012)));
      this.render();
      this.onView && this.onView();
    }, { passive: false });
    h.addEventListener("click", (ev) => {
      if (this.justPicked) { this.justPicked = false; return; }  // 点を押した直後の click では閉じない
      if (!ev.target.closest(".chooser")) this.closeChooser();
    });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") this.closeChooser(); });
  }

  // ---- 経路の再生：runs = [{name, key, color, segments, points}]。onStep(run, seg) で今の命令の行を知らせる ----
  // play は Player を返す（止める・続ける・1つ進む・はじめからは、呼ぶ側が Player に頼む）。start=false なら作るだけ
  stop() {
    if (this.player) this.player.stop();
    this.player = null;
    this.marker = null;
    this.render();
  }

  play(runs, onStep, onEnd, start = true) {
    this.stop();
    const SPEED = this.span * 0.9;  // 世界座標の速さ（mm/秒の目安。実際のロボットの速度とは関係ない）
    const list = [];
    for (const run of runs) {
      const at = Object.fromEntries(run.points.map((p) => [p.name, p.xyz]));
      for (const s of run.segments) if (at[s.from] && at[s.to]) list.push({ run, s, a: at[s.from], b: at[s.to] });
    }
    const player = new Player(list, {
      duration: (it) => {
        const len = Math.hypot(it.b[0] - it.a[0], it.b[1] - it.a[1], it.b[2] - it.a[2]);
        return segmentLook(it.s.kind).animate ? Math.max(350, (len / SPEED) * 1000) : HOLD_MS;
      },
      onStep: (it) => onStep && onStep(it.run, it.s),
      // MOVL の区間だけ線の上を動かす。MOVJ・目安の区間は、直線の上に印を置かない（区間の間は消す）
      onFrame: (it, f) => {
        if (this.player !== player) return;
        this.marker = it ? { color: it.run.color, seg: it.s, runKey: it.run.key || "", dest: it.b,
          xyz: segmentLook(it.s.kind).animate ? [0, 1, 2].map((k) => it.a[k] + (it.b[k] - it.a[k]) * f) : null } : null;
        this.render();
      },
      onEnd: () => {
        if (this.player !== player) return;
        this.marker = null; this.render(); onEnd && onEnd();
      },
    });
    this.player = player;
    if (start) player.play();
    return player;
  }
}
