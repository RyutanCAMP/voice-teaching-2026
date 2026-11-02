// 音声認識だけを担当する。Claude呼出し・変更・承認は行わない。
const ERRORS = {
  'not-allowed': 'マイクの利用が許可されていません。ブラウザーのマイク設定を確認してください。',
  'service-not-allowed': 'この音声認識サービスは利用できません。ブラウザーの設定を確認してください。',
  'audio-capture': 'マイクを利用できません。接続と、ほかのアプリによる使用を確認してください。',
  'network': '音声認識の通信に失敗しました。接続を確認して、録音し直してください。',
  'no-speech': '声を聞き取れませんでした。もう一度録音するか、文字で入力してください。',
  'language-not-supported': '日本語の音声認識を利用できません。端末内のみの場合は、日本語の言語パックと対応状況を確認してください。',
  'aborted': '音声入力が中断されました。認識文は採用していません。'
};

export class SpeechInput {
  constructor(Recognition, callbacks = {}) {
    this.Recognition = Recognition;
    this.callbacks = callbacks;
    this.active = false;
    this.generation = 0;
    this.recognition = null;
    this.timer = null;
  }
  emit(name, value) {this.callbacks[name]?.(value);}
  setTimer(fn, delay) {clearTimeout(this.timer);this.timer=setTimeout(fn,delay);}
  detach() {
    clearTimeout(this.timer); this.timer=null;
    const r=this.recognition;this.recognition=null;this.active=false;this.generation++;
    if(r) {
      r.onstart=r.onresult=r.onerror=r.onend=r.onnomatch=null;
      try {r.abort();} catch(_) { /* 既に終了していれば何もしない */ }
    }
  }
  fail(message) {this.detach();this.emit('partial','');this.emit('state','idle');this.emit('error',message);}
  start(mode) {
    if(this.active) return;
    if(!this.Recognition) return this.fail('このブラウザーは音声入力に対応していません。対応するChromeなどで開くか、文字で入力してください。');
    if(!['browser','local'].includes(mode)) return this.fail('音声の処理方法を選んでください。');
    let r;
    try {r=new this.Recognition();}
    catch(_) {return this.fail('音声認識を初期化できません。文字入力をご利用ください。');}
    if(mode==='local' && !('processLocally' in r)) return this.fail('このブラウザーは「端末内のみ」に対応していません。外部処理へは切り替えません。');
    this.recognition=r;this.active=true;
    const generation=++this.generation, live=()=>this.active && this.generation===generation;
    let finalText='';
    try {
      r.lang='ja-JP';r.continuous=false;r.interimResults=true;r.maxAlternatives=1;
      if('processLocally' in r) r.processLocally=mode==='local';
      r.onstart=()=>{
        if(!live())return;
        this.emit('state','listening');
        this.setTimer(()=>this.stop(),30000);
      };
      r.onresult=event=>{
        if(!live())return;
        let complete='',interim='';
        for(const item of Array.from(event.results)) {
          const text=item[0]?.transcript || '';
          if(item.isFinal)complete+=text;else interim+=text;
        }
        finalText=complete.trim();
        this.emit('partial',(complete+interim).trim());
      };
      r.onerror=event=>{if(live())this.fail(ERRORS[event.error] || '音声認識に失敗しました。録音し直すか、文字で入力してください。');};
      r.onnomatch=()=>{if(live())this.fail(ERRORS['no-speech']);};
      r.onend=()=>{
        if(!live())return;
        const text=finalText;
        this.detach();this.emit('partial','');this.emit('state','idle');
        if(text)this.emit('final',text);else this.emit('error',ERRORS['no-speech']);
      };
      this.emit('state','starting');
      this.setTimer(()=>this.fail('音声入力を開始できませんでした。マイクの許可を確認してください。'),12000);
      r.start();
    } catch(_) {this.fail('音声入力を開始できません。ブラウザーのマイク設定を確認してください。');}
  }
  stop() {
    if(!this.active)return;
    this.emit('state','stopping');
    this.setTimer(()=>this.fail('音声認識を終了できませんでした。認識文は採用していません。'),5000);
    try {this.recognition.stop();} catch(_) {this.fail('音声入力を終了できませんでした。録音し直してください。');}
  }
  cancel() {this.detach();this.emit('partial','');this.emit('state','idle');}
}
