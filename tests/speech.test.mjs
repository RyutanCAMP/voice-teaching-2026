// 出どころ：引き継ぎ（音声入力）コミット fd03d84cc862006464cc07275b558e4f8bbfad09 の tests/speech.test.mjs。
// 変えたのは speech.js の import 先（voice_teaching/web/static/）だけ。
import test from 'node:test';
import assert from 'node:assert/strict';
import { SpeechInput } from '../voice_teaching/web/static/speech.js';

function setup(local = true) {
  const log = {states: [], partial: [], final: [], errors: []};
  let engine;
  class FakeRecognition {
    constructor() {engine = this; if(local) this.processLocally = false;}
    start() {this.started = true;}
    stop() {this.stopped = true;}
    abort() {this.aborted = true;}
  }
  const speech = new SpeechInput(FakeRecognition, {
    state: x=>log.states.push(x), partial: x=>log.partial.push(x),
    final: x=>log.final.push(x), error: x=>log.errors.push(x)
  });
  return {speech, log, engine: ()=>engine};
}
function result(text, final) {return {0: {transcript:text}, isFinal:final};}

test('interim results never become an instruction; final text is delivered only on end', ()=>{
  const {speech, log, engine} = setup(); speech.start('browser'); const r=engine();
  assert.equal(r.lang,'ja-JP'); assert.equal(r.continuous,false);
  r.onstart(); r.onresult({results:[result('この位置を',false)]});
  assert.deepEqual(log.final,[]);
  r.onresult({results:[result('この位置を上に10ミリ',true)]});
  assert.deepEqual(log.final,[]); r.onend();
  assert.deepEqual(log.final,['この位置を上に10ミリ']);
});
test('revised interim segments are replaced rather than duplicated',()=>{
  const {speech,log,engine}=setup(); speech.start('browser'); const r=engine(); r.onstart();
  r.onresult({results:[result('この位置を',true),result('右',false)]});
  r.onresult({results:[result('この位置を',true),result('上に10ミリ',true)]}); r.onend();
  assert.deepEqual(log.final,['この位置を上に10ミリ']);
});
for(const code of ['not-allowed','audio-capture','network','no-speech','language-not-supported']) {
  test(`${code} clears results and never reports success`,()=>{
    const {speech,log,engine}=setup(); speech.start('browser'); const r=engine(); r.onstart();
    r.onresult({results:[result('この位置を上に10ミリ',true)]});
    const end=r.onend;r.onerror({error:code});end();
    assert.deepEqual(log.final,[]); assert.equal(log.errors.length,1);
    assert.equal(speech.active,false); assert.ok(r.aborted);
  });
}
test('cancel ignores late callbacks from a previous recording',()=>{
  const {speech,log,engine}=setup(); speech.start('browser'); const old=engine();
  const late=old.onresult, end=old.onend;
  speech.cancel(); speech.start('browser');
  late({results:[result('承認して',true)]}); end();
  assert.deepEqual(log.final,[]); assert.equal(speech.active,true);speech.cancel();
});
test('local-only mode must never fall back to remote processing',()=>{
  const {speech,log,engine}=setup(false);speech.start('local');
  assert.equal(engine().started,undefined);assert.equal(log.errors.length,1);
  assert.equal(speech.active,false);
});
test('local-only sets processLocally before starting',()=>{
  const {speech,engine}=setup();speech.start('local');
  assert.equal(engine().processLocally,true);speech.cancel();
});
test('unselected mode and unsupported recognition never start recording',()=>{
  const {speech,log}=setup();speech.start('');assert.equal(log.errors.length,1);
  const errors=[];new SpeechInput(null,{error:x=>errors.push(x)}).start('browser');
  assert.equal(errors.length,1);
});
test('stop requests a final result, cancellation discards it',()=>{
  const {speech,log,engine}=setup();speech.start('browser');const r=engine();r.onstart();
  speech.stop();assert.ok(r.stopped);r.onresult({results:[result('この位置を下に2mm',true)]});r.onend();
  assert.deepEqual(log.final,['この位置を下に2mm']);
});
test('empty recognition is reported as failure',()=>{
  const {speech,log,engine}=setup();speech.start('browser');engine().onend();
  assert.equal(log.errors.length,1);assert.deepEqual(log.final,[]);
});
