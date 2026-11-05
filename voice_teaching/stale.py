"""取消・言い直しと、ふつうの質問の見分け（P0 H02・H16。docs/DESIGN_P0_STALE_CANDIDATES_20261105.md 3章）。

- 見分けるのはプログラムの決まった規則（正規表現と言葉の一覧）。Claude には判定させない（K8）
- 使うのは、変更候補があるとき（未承認・承認済み・出力済み）だけ。呼ぶ側（web/flow.py）が決める
- 結果で新しくできることは、候補を無効にすること・保留にすること（止める側）と、決まった文を返すことだけ。
  候補・範囲・承認を新しく作る道は無い（K4）
- 迷う形（ためらい・推量・条件・疑問・読めない残り）は、無効にしないで確かめる（K3）。そのときは承認を保留にする
- 設計の規則 9（同じ種類の値だけ「4回にして」）は今回入れない（今の道のまま。唐川さんの Q-3 の判断を待つ）
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import repeat_count as rc

CANCEL = "cancel"                    # 取消だけ
CANCEL_EXPLAIN = "cancel_explain"    # 取消＋説明の頼み（H16）
CORRECTION = "correction"            # 言い直し（H02）
UNSURE = "unsure"                    # 取消・言い直しかもしれないが、はっきりしない → 保留にして確かめる
QUESTION_ABOUT_CANCEL = "how_cancel"  # 取消のしかた・結果を聞く問い → 何も変えない
KEEP = "keep"                        # 取消の打ち消し（「取り消さないで」「このままでいい」）
OTHER = "other"                      # 今の道のまま
INVALIDATING = (CANCEL, CANCEL_EXPLAIN, CORRECTION)

# 承認・出力など、ボタンだけで行う操作の言葉（web/flow.py の _BUTTON_ONLY_WORDS と同じ）
_BUTTON = re.compile(r"承認|出力|書き出|転送|送信|保存|起動|実行|スタート|エクスポート|ダウンロード|登録|上書き|確定")
# 取消の明示の節（節の全体が一致）。今の utterance.is_explicit_cancellation に、前置き（「変更は」「今の候補を」など）を足した形
_X = re.compile(
    r"(?:(?:やっぱり|やっぱ|やはり|じゃあ|では|もう)[\s]*)?"
    r"(?:(?:(?:今の|この|さっきの|前の)?(?:提案|変更候補|変更|候補)(?:は|を)?)|さっきのは|さっきのを|今のは|今の|さっきの)?"
    r"(?:取り消(?:して|します|し|す)|取消(?:して|し)?|キャンセル(?:して|します)?|元に戻(?:して|します|す)|もとに戻(?:して|します|す)"
    r"|戻して|戻す|やめて|やめます|やめる|やめた|やめよう|なしにして|なしで|なし(?:なし)?|無しにして|無しで|無し|いらない|要らない|撤回(?:して|します)?"
    r"|なかったことに(?:して)?|消して|消しといて|戻しといて|止めて|中止(?:して)?|破棄(?:して)?|取り下げ(?:て|ます)?|リセット(?:して)?"
    r"|アンドゥ|undo|UNDO|cancel|CANCEL)"
    r"(?:ください|下さい|ほしい|欲しい)?(?:よ|ね)?[\s。.!！]*")
# 取消の動詞（節のどこかにある）
_CANCEL_VERB = re.compile(r"取り消|取消|キャンセル|元に戻|もとに戻|戻して|戻す|戻しと|やめ|撤回|なかったことに|消して|消す|消しと|破棄|取り下げ"
                          r"|リセット|止めて|中止|アンドゥ|undo|cancel", re.I)
# 取消・止めたいのかもしれない言葉（はっきりしない）：候補があれば保留にする（今の道の文のまま。承認・出力はできない）
_HOLD_WORDS = re.compile(r"ストップ|すとっぷ|(?:^|[、,]|ちょっと)(?:待って|まって|待った|まった)|ちょいまち|いいや(?:$|[、,。!！]|め)"
                         r"|もういい|やっぱいい"
                         # 向きが逆だと言う文（F-1：「符号が逆」「逆だった」）。どちらへ直すかが分からないので保留にして確かめる
                         r"|逆|反対|ぎゃく|はんたい")
# 取消の打ち消し・候補を残す言葉
_N = re.compile(r"取り消さな|取り消さず|取消さな|キャンセルしな|キャンセルせず|戻さな|戻さず|やめないで|やめずに"
                r"|(?:キャンセル|取り消し|取消|取り消すの|取消すの|元に戻すの|戻すの)(?:は|を)やめ")
# ほかの操作の打ち消し（「C00004を選ぶのはやめて」「比較はやめて」「それはやめて」）：候補の取消ではない。今の道（止める）
_OTHER_STOP = re.compile(r"^(?!.*(?:提案|変更|候補))(?:.+?)(?:の)?(?:は|を)やめ(?:て|ます)(?:ください)?[\s。.!！]*$")
_POINTER = re.compile(r"^(?:それ|これ|その|この|今の|さっきの|さっき|前の|あれ)")
# 取消の明示の文を、疑問の形にしただけ（「元に戻しますか」「取り消して？」）：今の道が［「元に戻します」でよいですか？］の
# ボタンで確かめる（押すと取消）。確かめるまでは保留にする
_QUESTION_TAIL = re.compile(r"(?:か|[?？])[\s。.!！?？]*$")
_KEEP_WORDS = re.compile(r"^(?:今の|この|その)?(?:候補|提案|変更)?(?:は|を)?"
                         r"(?:このまま|そのまま)(?:で)?(?:いい|よい|良い|大丈夫|お願いします|お願い)?(?:です)?[\s。.!！]*$"
                         r"|この候補を残(?:す|して)(?:ください)?[\s。.!！]*$")
# 「いや」「いえ」のすぐあとに来たら言い直しの印にする、新しい内容の始まり（F-1・11/6 クローク Safety 最終レビュー：
# 「いやYプラス3ミリ」「いやワイプラス」「いや上に」は区切りが無く、印にならずに古い候補が残っていた）
_CORR_HEAD = (r"[\s、,0-9]|いや|$|[XYZCxyzc]|プラス|マイナス|ぷらす|まいなす|ワイ|わい|ゼット|ぜっと|エックス|えっくす"
              r"|上|下|右|左|前|後|うえ|した|みぎ|ひだり|まえ|うしろ|逆|反対")
# 言い直しの印。後ろに新しい内容が来る形（「3回じゃなくて4回」）
_M_BEFORE = re.compile(r"じゃなくて|じゃなく|ではなくて|ではなく|でなくて|でなく|やなくて|やなく|じゃない|違います|違う|ちがう"
                       rf"|いや(?={_CORR_HEAD})|いえ(?={_CORR_HEAD})|ごめん|すみません|ちゃう|違く|言い直す"
                       r"|やっぱり|やっぱ|やはり|間違い|間違え|間違って|まちがい|訂正|言い直し")
_BARE_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
# 言い直しの印。前に新しい内容が来る形（「4回に変えて」）。「〜だった」「〜やった」で終わる文も、前が新しい内容（F-1：
# 「Yプラス10ミリだった」「プラスだった」）
_M_AFTER = re.compile(r"に変えて|に変更して|に直して|へ変えて|へ変更して|(?:だった|やった)(?:よ|わ|ね)?[\s。.!！]*$")
# 新しい値（回数・量・軸と符号・位置番号）。「もう一回」「もう一度」は値にしない（副詞）
_ADVERB_ONCE = re.compile(r"もう(?:一|1)回|もう一度|もういちど|一回(?:見せ|比べ|説明)|1回(?:見せ|比べ|説明)")
_V = re.compile(r"[0-9]+(?:\.[0-9]+)?回|[0-9]+(?:\.[0-9]+)?(?:mm|MM|ミリ|みり|%|パーセント)|[XYZxyz][+\-]|"
                r"[XYZxyz]?(?:プラス|マイナス)|C[0-9]{5}|[0-9]+(?:\.[0-9]+)?(?:往復)")
# ためらい・推量・条件・疑問
_H = re.compile(r"かも|かな|迷|たぶん|多分|おそらく|と思う|気がする|ほうがいい|方がいい|べき|もし|なら|たら|れば|場合|とか"
                r"|かどうか|[?？]|(?:ですか|ますか|か|の)[\s。.!！]*$")
_H_HESITATE = re.compile(r"かも|かな|迷|たぶん|多分|おそらく|と思う|気がする|ほうがいい|方がいい|べき")
# 取消のしかた・結果を聞く問い
_HQ = re.compile(r"どうやって|どうやる|どうなる|どうすれば|方法|やり方|とは|って何|ってなに|し方|方を|どこ|ボタン")
# ふつうの質問（QA M-1）：言い直しの印（「違う」「じゃない」「間違い」）があっても、取消の言葉・新しい値が無く、印のほかに
# 中身がある疑問の文（「何が違う？」「3回と4回ではどう違う？」「これは溶接じゃないの？」「間違いないですか？」）は質問。何も変えない
_ASKING = re.compile(r"[?？][\s。.!！?？]*$|(?:ですか|ますか|の|か)[\s。.!！]*$|何|なに|どう|どこ|なぜ|どれ|どっち|どちら|合って")
# 説明の頼みの節（白い一覧。節の全体が一致）。回数・量・軸・位置番号・承認の言葉が入る節は説明にしない
_E = re.compile(
    r"(?:(?:今の|この|元の|読み込んだ)?(?:ジョブ|動作データ|ロボット|動き|作業)?(?:は|が|で)?)?"
    r"(?:何(?:を)?(?:して(?:い)?る|する)(?:の|のか|か)?(?:を)?)?"
    r"(?:説明して|教えて|説明|説明だけ|聞きたい|知りたい)(?:ください|下さい|ほしい|欲しい)?[\s。.!！]*"
    r"|何(?:を)?(?:して(?:い)?る|する)(?:の)?[?？]?"
    r"|(?:どういう|どんな)(?:動き|作業)(?:か|なのか)?(?:を)?(?:教えて|説明して)(?:ください)?[\s。.!！]*")
# 指す言葉・つなぎ（「中身が無い」の判定に使う）
_R = re.compile(r"今の|さっきの|さっき|この|その|それ|提案|変更候補|変更|候補|範囲|ほう|方|は|が|の|を|です|だよ|だ|よ|ね|って"
                r"|やつ|あと|でした|[\s、,。.!！]")
# 節に分ける：読点・句点・感嘆と、「てから」「それから」「そして」。取消の動詞のて形のあとに続く文も分ける
_SPLIT = re.compile(r"[、,。.！!]+|(?<=て)から|それから|そして")
_AFTER_CANCEL_TE = re.compile(r"((?:取り消|取消|キャンセル|元に戻|もとに戻|戻|やめ)(?:し)?て)(?!から)(?=[^\s、,。.！!?？くほ])")
# 値の節の、値のほかに許す言葉（「4回にして」「2回のままでいい」）。ほかの言葉（「理由を説明して」）が残れば値の節にしない
_VALUE_FILLER = re.compile(r"にして|にする|にします|に|で|のまま|まま|いい|よい|お願いします|お願い|します|ください|して|だけ")


@dataclass(frozen=True)
class Verdict:
    kind: str
    asked_form: bool = False     # UNSURE のうち、取消の明示の文を疑問の形にしただけ（今の道のボタンで確かめる）
    explain: str | None = None   # CANCEL_EXPLAIN のとき、Claude に送る説明の節だけ（取消の節は送らない）
    value: str | None = None     # CORRECTION のとき、新しい値の部分（プログラムが読んだ字のまま。案内の文に使うだけ）
    rule: str = ""               # どの規則で決めたか（テスト・作業ログ用）


# 取消・言い直しの言葉のカタカナ書き（声の認識・打ち間違いで出る）。言葉ごとにひらがなに寄せる
# （「キャンセル」などのカタカナの言葉は崩さない）
_KATA = {"マチガエタ": "間違えた", "マチガエ": "間違え", "チガウ": "違う", "トリケシテ": "取り消して", "モドシテ": "戻して", "ヤメテ": "やめて", "ヤメ": "やめ", "ヤッパ": "やっぱ", "ヤッパリ": "やっぱり", "ヤハリ": "やはり", "イヤ": "いや", "チガウ": "ちがう", "チガイマス": "違います",
         "ヤメテ": "やめて", "ヤメル": "やめる", "トリケシ": "取り消し", "モドシテ": "戻して", "マチガイ": "まちがい",
         "ジャナクテ": "じゃなくて", "ジャナク": "じゃなく", "デハナク": "ではなく", "ナシ": "なし"}
_KATA_RE = re.compile("|".join(sorted(_KATA, key=len, reverse=True)))


# かな書きの取消の言葉を、漢字の書き方にそろえる（声の認識はかなで返すことがある）
_KANA_WORDS = (("とりけ", "取り消"), ("取りけ", "取り消"), ("とり消", "取り消"), ("元にもど", "元に戻"), ("もとにもど", "元に戻"),
               ("もどして", "戻して"), ("もどしといて", "戻しといて"), ("もどす", "戻す"), ("てっかい", "撤回"),
               ("けしといて", "消しといて"), ("とりさげ", "取り下げ"), ("取りさげ", "取り下げ"))
# 文の途中で別の言葉に当たる置き換え（「けして（決して）」「はき（では＋き）」「まちが」）はしない（Safety 再検証 M-1）
_KANJI_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_AMOUNT = re.compile(r"([〇零一二三四五六七八九十]+)(?=(?:ミリ|みり|mm|MM|ミリメートル|回|かい|%|パーセント))")


def _kanji_number(k: str) -> str:
    """「五」「十五」「二十」を数字にする（量・回数の前だけ。99 まで）。"""
    if "十" in k:
        a, _, b = k.partition("十")
        return str((_KANJI_DIGIT.get(a, 1) if a else 1) * 10 + (_KANJI_DIGIT.get(b, 0) if b else 0))
    return "".join(str(_KANJI_DIGIT[c]) for c in k)


def _norm(text: str) -> str:
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))  # 「や っぱり」など、声の認識の空白は見分けに使わない
    t = _KATA_RE.sub(lambda m: _KATA[m.group(0)], t)
    for a, b in _KANA_WORDS:
        t = t.replace(a, b)
    return _KANJI_AMOUNT.sub(lambda m: _kanji_number(m.group(1)), t)


def _clauses(t: str) -> list[str]:
    t = _AFTER_CANCEL_TE.sub(r"\1、", t)
    return [c.strip() for c in _SPLIT.split(t) if c and c.strip()]


def _values(t: str) -> list[str]:
    d = rc.to_digits(_ADVERB_ONCE.sub("", t))
    return [m.group(0) for m in _V.finditer(d)]


def _value_clause(c: str) -> bool:
    """値だけの節か（「4回にして」「4回」「2回のままでいい」）。"""
    d = rc.to_digits(_ADVERB_ONCE.sub("", c))
    if not _V.search(d):
        return False
    return not _R.sub("", _VALUE_FILLER.sub("", _V.sub("", d))).strip()


def _empty_rest(t: str) -> bool:
    rest = _M_AFTER.sub("", _M_BEFORE.sub("", t))
    return not _R.sub("", rest).strip()


_ACK = re.compile(r"(?:はい|うん|ok|okay|オーケー|おっけー|了解|りょうかい|わかった|分かった|わかりました|分かりました|ありがとう(?:ございます)?"
                  r"|お願いします|それでお願いします|よし|いいね)[!！。.、]*", re.I)  # 「いいです」「大丈夫です」は断りにもなるので入れない
# 前の候補を否定している・止めたいかもしれない言葉（質問の形でも、承認を残す理由にしない）
_DOUBT = re.compile(r"違|ちが|ちゃう|ない|無い|おかし|なし|無し|ナシ|いら|要ら|間違|まちが|やめ|止め|とめ|戻|もど|消|けし|待|まっ|ストップ"
                    r"|キャンセル|取り消|取消|とりけ|撤回|中止|リセット|ダメ|だめ|嫌|いや|うそ|嘘|でも|けど|早かっ|よかった|まだ"
                    r"|^(?:え|は|あれ|ん+|ほんと|本当|まじ|マジ|そうなの|そう)(?:に|っ)?[?？!！。]*$")


# 承認を残してよいボタンの頼み（許す形の一覧。文の全体が一致するときだけ。Safety 再検証4回目：否定の一覧で除く形は漏れる）
_PLAIN_BUTTON = re.compile(r"(?:では|じゃあ|それでは)?(?:シミュレーション用に)?(?:出力|承認|書き出し)(?:を)?"
                           r"(?:して|します|お願いします|おねがいします)(?:ください|下さい)?[。.!！]*")
# 承認を残してよい質問：疑問の言葉があり、承認した中身（向き・正しさ・危険）を疑う言葉が無いもの（許す形）
_INTERROGATIVE = re.compile(r"何|なに|なん|どこ|なぜ|どうして|どう|いつ|どれ|どの|どんな|いくつ|どのくらい")
_CONTENT_DOUBT = re.compile(r"逆|反対|ぎゃく|はんたい|プラス|マイナス|ぷらす|まいなす|上|下|うえ|した|誤|あやま|変|へん|危|あぶ|ぶつか|当た|あた"
                            r"|大丈夫|だいじょうぶ|本当|ほんと|いいの|よいの|良いの|合って|正し|おかし|安全|問題"
                            # 驚き・理由を問う疑い（Safety 再検証5回目 M-1）：承認した中身への疑いになりやすい
                            r"|なんで|何で|どうして|何これ|なにこれ|なんだこれ|どういうこと|どうなって|そっち|あっち|右|左|前|後ろ|うしろ"
                            r"|こんなに|そんなに|も[?？]|回も|高い|低い|多い|少な|大き|小さ|遠|近")


def _hira(t: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)


def is_plain_button_request(text: object) -> bool:
    """ボタンで行う操作の頼みそのもの（「出力して」「承認して」）。ボタンで行う案内を返すだけで、承認は残す。"""
    return isinstance(text, str) and bool(_PLAIN_BUTTON.fullmatch(_norm(text)))


def is_ack(text: object) -> bool:
    """はっきりした相づちだけか（「はい」「OK」「ありがとう」）。承認のあとでも承認を残してよい文。"""
    return isinstance(text, str) and bool(_ACK.fullmatch(_norm(text)))


def is_plain_question(text: object) -> bool:
    """中身を疑わない、ふつうの質問か（「何をしているの？」「どこへ動くの？」）。承認のあとでも承認を残してよい文。
    疑問の言葉が要る（「これ逆？」「マイナス？」は保留）。否定・取消・向き・正しさ・危険の言葉があれば保留"""
    if not isinstance(text, str):
        return False
    t = _norm(text)
    h = _hira(t)
    return (bool(_ASKING.search(t)) and bool(_INTERROGATIVE.search(t)) and not _DOUBT.search(t) and not _DOUBT.search(h)
            and not _CONTENT_DOUBT.search(t) and not _CONTENT_DOUBT.search(h) and not _M_BEFORE.search(t) and not _M_AFTER.search(t))


def is_keep(text: object) -> bool:
    """保留を解く言葉か（「このままでいい」「この候補を残す」「取り消さないで」）。承認ではない。"""
    if not isinstance(text, str):
        return False
    t = _norm(text)
    return bool(_KEEP_WORDS.search(t)) or (bool(_N.search(t)) and not _BUTTON.search(t) and not _values(t))


def classify(text: object) -> Verdict:
    """変更候補があるときの発話を見分ける（決定論）。候補が無いときは呼ばない（呼ぶ側が決める）。"""
    if not isinstance(text, str) or not text.strip():
        return Verdict(OTHER, rule="empty")
    t = _norm(text)
    clauses = _clauses(t)
    x_clauses = [c for c in clauses if _X.fullmatch(c)]
    m_before = _M_BEFORE.search(t)
    m_after = _M_AFTER.search(t)
    has_m = bool(m_before or m_after)
    cancel_verb = bool(_CANCEL_VERB.search(t))
    # 1・2：承認・出力などの言葉
    if _BUTTON.search(t):
        if x_clauses or has_m or cancel_verb:
            return Verdict(UNSURE, rule="1 button+cancel")  # 「取り消して承認して」：矛盾する頼み。何も変えずに確かめる
        return Verdict(OTHER, rule="2 button")
    # 3：取消の打ち消し・候補を残す言葉
    if _N.search(t) or _KEEP_WORDS.search(t):
        return Verdict(KEEP, rule="3 keep")
    if _OTHER_STOP.match(t) and not [v for v in _values(t) if not v.startswith("C")]:  # 「3回はやめて」は値の打ち消し：下の規則で確かめる
        if _POINTER.match(t):
            return Verdict(UNSURE, asked_form=True, rule="3b pointer stop")  # 「それはやめて」：候補のことかもしれない。保留（今の文のまま）
        return Verdict(OTHER, rule="3b other stop")
    # 3c：ふつうの質問（取消の言葉が無い・印のあとに新しい値が無い・印のほかに中身がある疑問の文）
    if (has_m and not cancel_verb and not x_clauses and _ASKING.search(t)
            and not (m_before and (_values(t[m_before.end():]) or _BARE_NUMBER.search(rc.to_digits(t[m_before.end():]))))
            and not _empty_rest(_QUESTION_TAIL.sub("", t)) and not _H_HESITATE.search(t)
            and not re.search(r"んじゃない|じゃないか|てたよね|てたっけ|違くない|ちがくない|ちゃう", t)):
        return Verdict(OTHER, rule="3c question")
    # 4：取消・言い直しの節に、ためらい・推量・条件・疑問（取消のしかたを聞く問いは何も変えない）
    for c in clauses:
        if _CANCEL_VERB.search(c) and _HQ.search(c):
            return Verdict(QUESTION_ABOUT_CANCEL, rule="4 how to cancel")
    for c in clauses:
        if (_CANCEL_VERB.search(c) or _M_BEFORE.search(c) or _M_AFTER.search(c)) and _H.search(c):
            if _HQ.search(c):
                return Verdict(QUESTION_ABOUT_CANCEL, rule="4 how to cancel")
            plain = _QUESTION_TAIL.sub("", c)
            asked = len(clauses) == 1 and bool(_X.fullmatch(plain) or _X.fullmatch(plain + "て")
                                               or _X.fullmatch(re.sub(r"ます$", "して", plain)))
            return Verdict(UNSURE, asked_form=asked, rule="4 hesitate")
    # 4a：止めたいのかもしれない言葉（「ストップ」「待って」「やっぱりいいや」）：保留（今の道の文のまま）
    if _HOLD_WORDS.search(t) and not x_clauses:
        return Verdict(UNSURE, asked_form=True, rule="4a hold word")
    # 4b：新しい値に、ためらいの言葉（「4回のほうがいいかな」）
    if _values(t) and _H_HESITATE.search(t):
        return Verdict(UNSURE, rule="4b value+hesitate")
    # 5：取消の明示の節
    if x_clauses:
        others = [c for c in clauses if c not in x_clauses and _R.sub("", c).strip()]
        if not others:
            return Verdict(CANCEL, rule="5 cancel")
        if all(_E.fullmatch(c) for c in others):
            return Verdict(CANCEL_EXPLAIN, explain="、".join(others), rule="5 cancel+explain")
        vals = [v for c in others for v in _values(c)]
        if vals and all(_value_clause(c) or _E.fullmatch(c) for c in others):
            return Verdict(CORRECTION, value=vals[-1], rule="5 cancel+value")
        return Verdict(UNSURE, rule="5 cancel+rest")
    if has_m:
        # 6：印より後ろ（「に変えて」の形は前）に新しい値
        if m_before:
            after = t[m_before.end():]
            vals = _values(after) or _BARE_NUMBER.findall(rc.to_digits(after))  # 「10じゃなくて5」：単位が無くても言い直し
        else:
            vals = []
        if not vals and m_after:
            vals = _values(t[:m_after.start()])
        if vals:
            return Verdict(CORRECTION, value=vals[-1], rule="6 correction")
        # 7：印のほかに中身が無い（「違う」「やっぱり」「今の提案は違う」）
        if _empty_rest(t):
            return Verdict(UNSURE, rule="7 bare marker")
        # 「やっぱりやめた」「さっきの間違ってた」など：取消の言葉があれば取消、無ければ保留（今の道の文のまま）
        if cancel_verb:
            return Verdict(UNSURE, rule="8 marker+cancel verb")
        return Verdict(UNSURE, asked_form=True, rule="8 marker+other")
    if cancel_verb and not _E.fullmatch(t):
        # 取消の動詞があるが、明示の形ではない（「3回はやめて」「やめとこうかな」など）：消さずに確かめる
        return Verdict(UNSURE, asked_form=True, rule="cancel verb not explicit")  # 保留にして、返事は今の道の文のまま
    return Verdict(OTHER, rule="11 other")
