// 作業の回数を変える候補（kind="repeat"）の表示だけの関数（docs/REPEAT_COUNT_DESIGN.md 5章）。数は計算しない（サーバーの文字のまま）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { confirmHeading, repeatCountLabel, cycleStatus } from '../voice_teaching/web/static/highlight.js';

const C = { kind: 'repeat', target: 'C00001〜C00003', before_value: '2', after_value: '3', delta: '+1' };

test('repeat heading is the fixed sentence with the server numbers', () => {
  assert.equal(confirmHeading(C), 'C00001〜C00003 の作業を 2回 → 3回 にします');
  assert.equal(confirmHeading({ ...C, before_value: '3', after_value: '2' }), 'C00001〜C00003 の作業を 3回 → 2回 にします');
});
test('repeat heading needs all the parts', () => {
  for (const k of ['target', 'before_value', 'after_value']) {
    assert.equal(confirmHeading({ ...C, [k]: undefined }), '');
  }
});
test('count label: ×2 → ×3', () => {
  assert.equal(repeatCountLabel(C), '×2 → ×3');
  assert.equal(repeatCountLabel({}), '');
  assert.equal(repeatCountLabel(null), '');
});
test('cycle status counts the turn only for segments inside the block', () => {
  assert.equal(cycleStatus({ cycle: 2 }, 'C00001〜C00003'), '2回目（C00001〜C00003）');
  assert.equal(cycleStatus({ cycle: 3 }, 'C00001〜C00003'), '3回目（C00001〜C00003）');
  for (const s of [{ cycle: null }, {}, null, { cycle: 0 }, { cycle: '2' }]) assert.equal(cycleStatus(s, 'X'), '');
});
