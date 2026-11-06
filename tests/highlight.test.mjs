// 読めなかった言葉（facts.unknown_words）を、聞き取った文の中で目立たせるための分け方（CHECKER_023 B1）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { splitUnknown, displayMinus, segmentLook, segmentTitle, trimDecimal, confirmHeading, updownStatus, updownConfirmLine, updownDetail, updownShort, speechExamples }
  from '../voice_teaching/web/static/highlight.js';

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

// ---- CHECKER_026 S-MOVJ：区間の見た目（MOVJ を直線移動のように見せない） ----
test('segmentLook: MOVL is solid and the marker may move along it', () => {
  const l = segmentLook('movl');
  assert.equal(l.kind, 'movl');
  assert.equal(l.dash, null);
  assert.equal(l.animate, true);
});
test('segmentLook: MOVJ is a thin gray dotted line, labelled, and the marker does not move along it', () => {
  const j = segmentLook('movj'), l = segmentLook('movl'), o = segmentLook('other');
  assert.equal(j.kind, 'movj');
  assert.ok(j.dash && j.dash !== o.dash, 'MOVJ の点線は、目安の破線と違う');
  assert.ok(j.width < l.width, 'MOVJ は細い');
  assert.ok(j.color, 'MOVJ は決まった灰色');
  assert.equal(j.animate, false);
  assert.equal(j.label, '関節移動（MOVJ）：実際の通り道は直線ではありません');
});
test('segmentLook: unknown or missing kind is treated as other (never as MOVL)', () => {
  for (const k of ['other', undefined, null, '', 'MOVL', 'movc', 1]) {
    const o = segmentLook(k);
    assert.equal(o.kind, 'other', String(k));
    assert.equal(o.dash, '6 5');
    assert.equal(o.animate, false);
  }
});
test('segmentTitle: line, text, and the label for MOVJ / other', () => {
  assert.equal(segmentTitle({ line_no: 12, text: 'MOVL C00001 V=80.0', kind: 'movl' }), '12行目 MOVL C00001 V=80.0');
  assert.equal(segmentTitle({ line_no: 13, text: 'MOVJ C00006 VJ=25.00', kind: 'movj' }),
    '13行目 MOVJ C00006 VJ=25.00\n関節移動（MOVJ）：実際の通り道は直線ではありません');
  assert.equal(segmentTitle({ line_no: 9, text: 'MOVC C00003 V=250.0', kind: 'other' }), '9行目 MOVC C00003 V=250.0（目安）');
});

// ---- CHECKER_026 S5：確認表示の見出し（数字はサーバーの候補のデータから。計算しない） ----
test('trimDecimal only removes trailing zeros of the given text', () => {
  assert.equal(trimDecimal('-3.000'), '-3');
  assert.equal(trimDecimal('+10.000'), '+10');
  assert.equal(trimDecimal('-0.500'), '-0.5');
  assert.equal(trimDecimal('+12.340'), '+12.34');
  assert.equal(trimDecimal('100'), '100');
  assert.equal(trimDecimal('abc'), 'abc');
});
test('confirmHeading: one line, position, axis and amount', () => {
  const c = { kind: 'shift', target: 'C00005 Y', delta: '-3.000', before_value: '150.000', after_value: '147.000' };
  assert.equal(confirmHeading(c), 'C00005 を Y −3mm 動かします');
  assert.equal(confirmHeading({ ...c, target: 'C00004 Z', delta: '+10.000' }, '（上へ）'), 'C00004 を Z +10mm（上へ）動かします');
  assert.ok(!confirmHeading(c).includes('\n'));
});
test('confirmHeading: a stopped candidate does not say it will move', () => {
  const c = { kind: 'shift', target: 'C00000 Z', delta: '+10.000', blocked_reason: '入る命令が MOVJ' };
  const h = confirmHeading(c);
  assert.equal(h, '参考の表示だけ・変更しません：C00000 を Z +10mm 動かした場合（承認できません）');
  assert.ok(!h.endsWith('動かします'));
});
test('confirmHeading: speed and missing data', () => {
  assert.equal(confirmHeading({ kind: 'speed', target: '12行目', before_value: '80.0', after_value: '88.0' }),
    '12行目の速度を V=80.0 から V=88.0 に変えます');
  assert.equal(confirmHeading(null), '');
  assert.equal(confirmHeading({ kind: 'shift', target: 'C00005' }), '');
});

// ---- 上下の向きの状態（state.updown。無いときは何も出さない） ----
test('updownStatus: nothing when the server does not send it', () => {
  for (const u of [undefined, null, 'confirmed', 3, []]) assert.equal(updownStatus(u), null, String(u));
});
test('updownStatus: four states', () => {
  assert.deepEqual(updownStatus({ status: 'unconfirmed' }), { text: '上下の向き：未確認（Z＋・Z−で指示）', tone: 'unconfirmed', title: '' });
  assert.deepEqual(updownStatus({ status: 'confirmed', confirmed_by: '唐川', confirmed_at: '2026-11-03T14:00:00+09:00', message: 'FRAME の設定で確認' }),
    { text: '上下の向き：確認済み', tone: 'confirmed', title: '確認：唐川・11/3 14:00。FRAME の設定で確認' });
  assert.equal(updownStatus({ status: 'mismatch' }).text, '上下の向き：確認した座標系・ツールと違います');
  assert.equal(updownStatus({ status: 'mismatch' }).tone, 'warn');
  assert.equal(updownStatus({ status: 'changed' }).text, '上下の向き：設定が変わっています');
  assert.equal(updownStatus({ status: 'changed' }).tone, 'warn');
});
test('updownStatus: confirmed with partial data, and unknown status is never shown as confirmed', () => {
  assert.equal(updownStatus({ status: 'confirmed', confirmed_by: '唐川' }).title, '確認：唐川');
  assert.equal(updownStatus({ status: 'confirmed', confirmed_at: '2026-11-03 09:05' }).title, '確認：11/3 09:05');
  assert.equal(updownStatus({ status: 'confirmed', confirmed_at: '昨日' }).title, '確認：昨日');
  assert.equal(updownStatus({ status: 'confirmed' }).text, '上下の向き：確認済み');
  assert.equal(updownStatus({ status: 'confirmed' }).title, '');
  for (const s of ['ok', 'CONFIRMED', undefined, '']) {
    assert.deepEqual(updownStatus({ status: s, confirmed_by: '唐川' }), { text: '上下の向き：未確認（Z＋・Z−で指示）', tone: 'unconfirmed', title: '' }, String(s));
  }
});

// ---- CHECKER_026 再修正（QA P1-2・P2） ----
test('speechExamples: up/down examples only when confirmed', () => {
  const lure = /上に|下に|上へ|下へ|上方向|下方向/;
  for (const u of [undefined, null, {}, { status: 'unconfirmed' }, { status: 'mismatch' }, { status: 'changed' }, { status: 'confirmedx' }, []]) {
    const ex = speechExamples(u);
    assert.equal(ex.placeholder, 'Zプラス方向に10mm', JSON.stringify(u));
    assert.ok(!lure.test(ex.placeholder) && !lure.test(ex.howto) && !ex.help.some((t) => lure.test(t)), JSON.stringify(u));
  }
  const ok = speechExamples({ status: 'confirmed' });
  assert.equal(ok.placeholder, 'この位置を上に10mm');
  assert.ok(ok.howto.includes('「この位置を上に10mm」'));
});

test('updownConfirmLine: one line only when confirmed', () => {
  assert.equal(updownConfirmLine({ status: 'confirmed', confirmed_by: '唐川', confirmed_at: '2026-11-03T14:00:00+09:00' }),
    '上下の向き：唐川さんが確認（11/3 14:00）');
  assert.equal(updownConfirmLine({ status: 'confirmed' }), '上下の向き：確認済み');
  for (const u of [undefined, null, [], { status: 'unconfirmed', confirmed_by: '唐川' }, { status: 'mismatch' }, { status: 'changed' }])
    assert.equal(updownConfirmLine(u), '', JSON.stringify(u));
});

// ---- CHECKER_030 S30-1（唐川さんの決定 2026-11-03 17:14）：ロボット名と確認した人・日時を常に見せる1行 ----
test('updownDetail: confirmed shows robot of the config, who and when', () => {
  const u = { status: 'confirmed', robot: 'HC20DT', app_robot: 'HC20DT', confirmed_by: '唐川', confirmed_at: '2026-11-03T14:00:00+09:00' };
  assert.equal(updownDetail(u), '上下の向き：HC20DT・唐川（11/3 14:00）で確認済み');
  // 確認済みのときは設定の robot を出す（無ければアプリの値）
  assert.equal(updownDetail({ ...u, robot: undefined }), '上下の向き：HC20DT・唐川（11/3 14:00）で確認済み');
  assert.equal(updownDetail({ status: 'confirmed', robot: 'HC10DTP', confirmed_by: '唐川' }), '上下の向き：HC10DTP・唐川で確認済み');
  assert.equal(updownDetail({ status: 'confirmed' }), '上下の向き：確認済み');
});
test('updownDetail: other states show the app robot', () => {
  assert.equal(updownDetail({ status: 'unconfirmed', app_robot: 'HC20DT' }), '上下の向き：未確認（HC20DT）');
  assert.equal(updownDetail({ status: 'mismatch', app_robot: 'HC20DT', robot: 'HC10DTP' }), '上下の向き：確認した設定と違います（HC20DT）');
  assert.equal(updownDetail({ status: 'changed', app_robot: 'HC20DT' }), '上下の向き：設定が変わっています（HC20DT）');
  // 確認済みでないときは、設定の名前・確認した人を出さない（確認済みに見せない）
  for (const s of ['unconfirmed', 'ok', 'CONFIRMED', undefined, '']) {
    const t = updownDetail({ status: s, app_robot: 'HC20DT', robot: 'X', confirmed_by: '唐川' });
    assert.ok(!t.includes('唐川') && !t.includes('確認済み') && !t.includes('X') && t.includes('HC20DT'), String(s));
  }
  assert.equal(updownDetail({ status: 'unconfirmed' }), '上下の向き：未確認');
});
test('updownDetail: nothing when the server does not send it, and names are text only', () => {
  for (const u of [undefined, null, 'confirmed', 3, []]) assert.equal(updownDetail(u), '', String(u));
  assert.equal(updownDetail({ status: 'unconfirmed', app_robot: 3 }), '上下の向き：未確認');
  assert.equal(updownDetail({ status: 'confirmed', robot: ['A'], confirmed_by: {} }), '上下の向き：確認済み');
});

// ---- 「Claude に聞く」の答えで光らせる区間（CHECKER_031 E4） ----
import { askHighlightLook, isAskHighlighted } from '../voice_teaching/web/static/highlight.js';
test('ask highlight keeps the dash of MOVJ and other segments (not shown as a straight path)', () => {
  assert.equal(askHighlightLook('movj').dash, segmentLook('movj').dash);
  assert.equal(askHighlightLook('other').dash, segmentLook('other').dash);
  assert.equal(askHighlightLook('movl').dash, null);
  assert.equal(askHighlightLook('unknown').dash, segmentLook('other').dash);
  assert.ok(askHighlightLook('movj').width < askHighlightLook('movl').width);
});
test('ask highlight uses only the line numbers from the server', () => {
  assert.equal(isAskHighlighted({ line_no: 22 }, [21, 22]), true);
  assert.equal(isAskHighlighted({ line_no: 27 }, [21, 22]), false);
  assert.equal(isAskHighlighted({ line_no: '22' }, [22]), false);
  assert.equal(isAskHighlighted({ line_no: 22 }, undefined), false);
  assert.equal(isAskHighlighted(null, [22]), false);
});

// 前後の移動（highlight.context_lines）は、強調の区間（オレンジ）と見分けられる控えめな見た目（唐川さん 11/3 夜）
import { askContextLook, ASK_CONTEXT_COLOR, ASK_HIGHLIGHT_COLOR } from '../voice_teaching/web/static/highlight.js';
test('前後の移動は控えめ：強調と違う色・細い・薄い。MOVJ の点線はそのまま', () => {
  for (const k of ['movl', 'movj', 'other']) {
    const c = askContextLook(k), h = askHighlightLook(k);
    assert.notEqual(c.color, h.color);
    assert.ok(c.width < h.width && c.opacity < h.opacity);
    assert.equal(c.dash, segmentLook(k).dash);
  }
  assert.notEqual(ASK_CONTEXT_COLOR, ASK_HIGHLIGHT_COLOR);
});

// ---- Issue #8 round3（11/6 唐川さん）：3D の上の上下の向きは短い状態だけをいつも見せ、ロボット名・確認した人・日時・理由は詳しい説明へ ----
test('updownShort: short state for the figure head', () => {
  assert.equal(updownShort({ status: 'unconfirmed', app_robot: 'HC20DT' }), '上下方向 未確認');
  // round4（11/6 唐川さんの決定）：確認済みは対象のロボット名も短い表示に出す（設定の robot、無ければアプリの --robot の値）
  assert.equal(updownShort({ status: 'confirmed', robot: 'HC20DT', confirmed_by: '唐川' }), '上下方向 確認済み（HC20DT）');
  assert.equal(updownShort({ status: 'confirmed', app_robot: 'HC10DTP' }), '上下方向 確認済み（HC10DTP）');
  assert.equal(updownShort({ status: 'confirmed' }), '上下方向 確認済み');
  assert.equal(updownShort({ status: 'mismatch', app_robot: 'HC20DT' }), '上下方向 設定と違います');
  assert.equal(updownShort({ status: 'changed', app_robot: 'HC20DT' }), '上下方向 設定が変わっています');
  assert.equal(updownShort({}), '上下方向 未確認');  // 分からない状態は未確認（確認済みにしない）
  assert.equal(updownShort(null), '');
  assert.equal(updownShort([]), '');
});
