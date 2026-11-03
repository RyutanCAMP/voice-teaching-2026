// 経路の再生の進め方だけ（DOM を使わない。node でも試せる）。描くのは呼ぶ側（onFrame）。
// - 区間（items）を命令の順に1つずつ進める。各区間の長さ（duration）は画面の上の目安で、ロボットの動きの時間ではない。
// - play：はじめから（止まっていれば続きから）／pause：その場で止める／step：止めて1つ進む（今の区間を終わりまで、
//   終わっていれば次の区間を終わりまで）／restart：はじめから／stop：やめて印を消す。
// - onStep(item, i)：区間が今の区間になったとき（画面の「いま：…」を変える）。onFrame(item, f)：f は 0〜1（描き直す）。
"use strict";

export class Player {
  constructor(items, { duration, onFrame, onStep, onEnd, raf, clock } = {}) {
    this.items = Array.isArray(items) ? items : [];
    this.duration = duration || (() => 500);
    this.onFrame = onFrame || (() => {});
    this.onStep = onStep || (() => {});
    this.onEnd = onEnd || (() => {});
    this.raf = raf || ((fn) => requestAnimationFrame(fn));
    this.clock = clock || (() => performance.now());
    this.state = "idle";  // idle・playing・paused・ended・stopped
    this.i = 0; this.f = 0; this.t0 = null; this.announced = -1; this.token = 0;
  }

  play() {
    if (this.state === "playing") return;
    if (this.state !== "paused") { this.i = 0; this.f = 0; this.announced = -1; }
    this._run();
  }

  restart() {
    this.i = 0; this.f = 0; this.announced = -1;
    this.state = "idle";
    this._run();
  }

  pause() {
    if (this.state !== "playing") return;
    this.state = "paused";
    this.token++;
  }

  step() {
    if (this.state === "ended") return;
    if (this.state !== "playing" && this.state !== "paused") { this.i = 0; this.f = 0; this.announced = -1; }
    this.state = "paused";
    this.token++;
    if (this.i >= this.items.length) return this._end();
    if (this.announced !== this.i) {
      this._announce();
    } else if (this.f >= 1) {
      this.i++;
      if (this.i >= this.items.length) return this._end();
      this._announce();
    }
    this.f = 1;
    this.onFrame(this.items[this.i], 1);
  }

  stop() {
    this.token++;
    this.state = "stopped";
    this.onFrame(null, 0);
  }

  _announce() {
    this.announced = this.i;
    this.f = 0;
    this.onStep(this.items[this.i], this.i);
  }

  _end() {
    this.token++;
    this.state = "ended";
    this.onEnd();
  }

  _run() {
    if (this.i >= this.items.length) return this._end();
    this.state = "playing";
    this.t0 = null;
    const token = ++this.token;
    const frame = (now) => {
      if (token !== this.token || this.state !== "playing") return;
      const t = typeof now === "number" ? now : this.clock();
      if (this.announced !== this.i) { this._announce(); this.t0 = t; }
      const it = this.items[this.i];
      const dur = Math.max(1, this.duration(it));
      if (this.t0 === null) this.t0 = t - this.f * dur;  // 止めたところから続ける
      this.f = Math.min(1, (t - this.t0) / dur);
      this.onFrame(it, this.f);
      if (this.f >= 1) {
        this.i++;
        if (this.i >= this.items.length) return this._end();
        this._announce();
        this.t0 = t;
        this.onFrame(this.items[this.i], 0);  // 「いま」の文と図を同じときに変える（前の区間の印を残さない）
      }
      this.raf(frame);
    };
    this.raf(frame);
  }
}
