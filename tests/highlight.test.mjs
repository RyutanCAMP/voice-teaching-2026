// 読めなかった言葉（facts.unknown_words）を、聞き取った文の中で目立たせるための分け方（CHECKER_023 B1）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { splitUnknown, displayMinus } from '../voice_teaching/web/static/highlight.js';

test('unknown words are split out in order', () => {
  assert.deepEqual(splitUnknown('バイオ 3ミリ 下げて', ['バイオ']), [
    { text: 'バイオ', unknown: true }, { text: ' 3ミリ 下げて', unknown: false }]);
});
test('several words and repeats', () => {
  assert.deepEqual(splitUnknown('あバイオいバイオう', ['バイオ', 'う']), [
    { text: 'あ', unknown: false }, { text: 'バイオ', unknown: true }, { text: 'い', unknown: false },
    { text: 'バイオ', unknown: true }, { text: 'う', unknown: true }]);
});
test('longer word wins when words overlap', () => {
  assert.deepEqual(splitUnknown('バイオリン', ['バイ', 'バイオリン']), [{ text: 'バイオリン', unknown: true }]);
});
test('missing or bad field leaves the text as is', () => {
  for (const w of [undefined, null, [], 'バイオ', [1, null, '']]) {
    assert.deepEqual(splitUnknown('Y座標を3ミリ下げて', w), [{ text: 'Y座標を3ミリ下げて', unknown: false }]);
  }
  assert.deepEqual(splitUnknown('', ['x']), []);
});
test('word not in text: nothing highlighted', () => {
  assert.deepEqual(splitUnknown('上に10ミリ', ['バイオ']), [{ text: '上に10ミリ', unknown: false }]);
});
test('regex characters in words are treated literally', () => {
  assert.deepEqual(splitUnknown('a.b(c', ['.b(']), [
    { text: 'a', unknown: false }, { text: '.b(', unknown: true }, { text: 'c', unknown: false }]);
});
test('displayMinus only swaps ASCII hyphen for U+2212', () => {
  assert.equal(displayMinus('Y -3.000mm'), 'Y −3.000mm');
  assert.equal(displayMinus('+10.000'), '+10.000');
});
