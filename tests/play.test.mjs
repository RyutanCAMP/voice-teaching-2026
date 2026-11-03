// 再生の表示（feat/demo-3d）：「いま：24行目 MOVL C00001 V=150.0（2回目）」の文と、再生の止める・1つ進む・はじめから。
// 文はサーバーの区間の線のデータ（line_no・text・kind・cycle）だけから作る。数は計算しない。時間・関節の通り道は出さない。
import test from 'node:test';
import assert from 'node:assert/strict';
import { nowPlaying, nowText } from '../voice_teaching/web/static/highlight.js';
import { Player } from '../voice_teaching/web/static/player.js';

const L = (line_no, text, cycle, kind = 'movl') => ({ line_no, text, kind, cycle, from: 'A', to: 'B' });

test('now: line number, command line and cycle', () => {
  const n = nowPlaying(L(24, 'MOVL C00001 V=150.0', 2), { run: '変更前', count: 2 });
  assert.equal(n.line, 'いま：24行目 MOVL C00001 V=150.0');
  assert.equal(n.cycle, '2回目');
  assert.equal(n.run, '変更前');
  assert.equal(n.count, '×2');
  assert.equal(n.note, '');
  assert.equal(n.added, false);
  assert.equal(nowText(n), '変更前 ×2　いま：24行目 MOVL C00001 V=150.0（2回目）');
});

test('now: the added cycle is marked only when it equals the added number', () => {
  const n = nowPlaying(L(27, 'MOVL C00001 V=150.0', 3), { run: '変更後', count: 3, added: 3 });
  assert.equal(n.cycle, '3回目（足した分）');
  assert.equal(n.added, true);
  const m = nowPlaying(L(24, 'MOVL C00001 V=150.0', 2), { run: '変更後', count: 3, added: 3 });
  assert.equal(m.cycle, '2回目');
  assert.equal(m.added, false);
});

test('now: MOVJ says the path is not shown, other kinds are a guide', () => {
  const j = nowPlaying(L(27, 'MOVJ C00004 VJ=25.00', null, 'movj'), {});
  assert.equal(j.line, 'いま：27行目 MOVJ C00004 VJ=25.00');
  assert.equal(j.note, '関節移動：通り道は表示していません');
  assert.equal(j.cycle, '');
  const o = nowPlaying(L(30, 'MOVC C00009 V=10.0', null, 'movc'), {});
  assert.equal(o.note, '直線とは限らない区間：印は終点へ移ります');
  assert.equal(nowText(j), 'いま：27行目 MOVJ C00004 VJ=25.00　関節移動：通り道は表示していません');
});

test('now: no cycle text without a valid cycle number; nothing for broken data', () => {
  for (const c of [null, undefined, 0, '2', 1.5]) assert.equal(nowPlaying(L(5, 'MOVL C1 V=1', c), {}).cycle, '');
  for (const s of [null, {}, { line_no: 0, text: 'x' }, { line_no: '3', text: 'x' }, { line_no: 3 }]) {
    assert.equal(nowPlaying(s, {}), null);
  }
  assert.equal(nowText(null), '');
});

test('now: never shows time or safety words', () => {
  const all = [nowPlaying(L(24, 'MOVL C00001 V=150.0', 2), { run: '変更後', count: 3, added: 3 }),
    nowPlaying(L(27, 'MOVJ C00004 VJ=25.00', null, 'movj'), {})].map(nowText).join(' ');
  for (const w of ['秒', '時間', '安全です', '当たります', '干渉します', '当たりません']) assert.ok(!all.includes(w), w);
});

// ---- Player：時計と描き直しを外から渡して試す ----
function rig(items, dur = 100) {
  let now = 0;
  const q = [];
  const log = [];
  const p = new Player(items, {
    duration: () => dur,
    onFrame: (it, f) => log.push(['frame', it && it.id, Math.round(f * 100) / 100]),
    onStep: (it, i) => log.push(['step', it.id, i]),
    onEnd: () => log.push(['end']),
    raf: (fn) => q.push(fn),
    clock: () => now,
  });
  const tick = (ms) => { now += ms; const fns = q.splice(0); for (const fn of fns) fn(now); };
  return { p, log, tick };
}
const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

test('player plays all items in order and ends', () => {
  const { p, log, tick } = rig(items);
  p.play();
  for (let k = 0; k < 10; k++) tick(60);
  assert.deepEqual(log.filter((x) => x[0] !== 'frame'), [['step', 'a', 0], ['step', 'b', 1], ['step', 'c', 2], ['end']]);
  assert.equal(p.state, 'ended');
});

test('player pause keeps the place, resume continues', () => {
  const { p, log, tick } = rig(items);
  p.play(); tick(0); tick(50);
  p.pause();
  const n = log.length;
  tick(500); tick(500);
  assert.equal(log.length, n, 'no frames while paused');
  assert.equal(p.state, 'paused');
  p.play(); tick(0); tick(60);
  assert.deepEqual(log.filter((x) => x[0] === 'step').map((x) => x[1]), ['a', 'b'], 'continues from a, not from the start');
});

test('player step: finishes the current item, then the next one, one press at a time', () => {
  const { p, log, tick } = rig(items);
  p.step();
  assert.equal(p.state, 'paused');
  assert.deepEqual(log, [['step', 'a', 0], ['frame', 'a', 1]]);
  p.step();
  assert.deepEqual(log.slice(2), [['step', 'b', 1], ['frame', 'b', 1]]);
  tick(1000);
  assert.equal(log.length, 4, 'stepping does not start playing');
  p.step(); p.step();
  assert.deepEqual(log.slice(-1), [['end']]);
  assert.equal(p.state, 'ended');
  p.step();
  assert.deepEqual(log.slice(-1), [['end']], 'nothing after the end');
});

test('player step while playing pauses and finishes the current item', () => {
  const { p, log, tick } = rig(items);
  p.play(); tick(0); tick(30);
  p.step();
  assert.equal(p.state, 'paused');
  assert.deepEqual(log.slice(-1), [['frame', 'a', 1]]);
});

test('player restart goes back to the first item; stop clears', () => {
  const { p, log, tick } = rig(items);
  p.play(); tick(0); tick(150);
  p.restart(); tick(0);
  assert.deepEqual(log.filter((x) => x[0] === 'step').map((x) => x[1]), ['a', 'b', 'a']);
  p.stop();
  assert.equal(p.state, 'stopped');
  assert.deepEqual(log.slice(-1), [['frame', null, 0]]);
  const n = log.length;
  tick(1000);
  assert.equal(log.length, n);
});

test('player with no items ends at once', () => {
  const { p, log } = rig([]);
  p.play();
  assert.deepEqual(log, [['end']]);
});

// ---- 動きの並びのカード（台本 Ver.6.1 Scene 3-3）。サーバーの candidate.sequence のまま ----
import { sequenceChips, tallyParts } from '../voice_teaching/web/static/highlight.js';
const SEQ = {
  target: 'C00001〜C00003', name: 'C00001〜C00003', added: [3], summary: '足した行：3行（直線移動だけ）。新しい位置はありません',
  before: { cycles: [1, 2], entry: { position: 'C00000', line_no: 20, text: 'MOVJ C00000 VJ=25.00' },
    exit: { position: 'C00004', line_no: 27, text: 'MOVJ C00004 VJ=25.00' } },
  after: { cycles: [1, 2, 3], entry: { position: 'C00000', line_no: 20, text: 'MOVJ C00000 VJ=25.00' },
    exit: { position: 'C00004', line_no: 30, text: 'MOVJ C00004 VJ=25.00' } },
};

test('sequence: after = entry → ① ② ③(added) → exit, words do not guess the meaning', () => {
  const c = sequenceChips(SEQ, 'after');
  assert.deepEqual(c.map((x) => x.kind), ['entry', 'arrow', 'name', 'cycle', 'cycle', 'cycle', 'arrow', 'exit']);
  assert.equal(c[0].text, 'C00000（区間の前）');
  assert.equal(c[0].title, '20行目 MOVJ C00000 VJ=25.00');
  assert.equal(c[2].text, 'C00001〜C00003');
  assert.deepEqual(c.filter((x) => x.kind === 'cycle').map((x) => [x.text, x.added]), [['①', false], ['②', false], ['③ 追加', true]]);
  assert.equal(c[7].text, 'C00004（区間の後）');
  assert.equal(c[7].title, '30行目 MOVJ C00004 VJ=25.00');
  assert.ok(!JSON.stringify(c).includes('待機'));
});

test('sequence: before has only ① ②; a given name is used for the block', () => {
  const c = sequenceChips(SEQ, 'before');
  assert.deepEqual(c.filter((x) => x.kind === 'cycle').map((x) => x.text), ['①', '②']);
  assert.ok(c.every((x) => !x.added));
  const n = sequenceChips({ ...SEQ, name: 'ボタンを押す動き' }, 'after');
  assert.equal(n.find((x) => x.kind === 'name').text, 'ボタンを押す動き');
});

test('sequence: missing entry/exit are left out, broken data gives nothing', () => {
  const c = sequenceChips({ ...SEQ, after: { cycles: [1, 2, 3], entry: null, exit: null } }, 'after');
  assert.deepEqual(c.map((x) => x.kind), ['name', 'cycle', 'cycle', 'cycle']);
  for (const q of [null, {}, { ...SEQ, after: null }, { ...SEQ, after: { cycles: 'x' } }]) assert.deepEqual(sequenceChips(q, 'after'), []);
});

test('tally: 1・2 before, 1・2・3 after with the added number marked', () => {
  assert.deepEqual(tallyParts(2, []), [{ text: '1', added: false }, { text: '2', added: false }]);
  assert.deepEqual(tallyParts(3, [3]).map((x) => [x.text, x.added]), [['1', false], ['2', false], ['3', true]]);
  assert.deepEqual(tallyParts(0, [3]), []);
  assert.deepEqual(tallyParts('2', [3]), []);
});
