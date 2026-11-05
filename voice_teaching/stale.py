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
    r"(?:(?:やっぱり|やはり|じゃあ|では|もう)[\s]*)?"
    r"(?:(?:(?:今の|この|さっきの|前の)?(?:提案|変更候補|変更|候補)(?:は|を)?)|さっきのは|さっきのを|今のは|今の|さっきの)?"
    r"(?:取り消(?:して|します|し)|取消(?:して|し)?|キャンセル(?:して|します)?|元に戻(?:して|します)|もとに戻(?:して|します)"
    r"|戻して|やめて|やめます|やめる|やめよう|なしにして|なしで|なし)"
    r"(?:ください|下さい|ほしい|欲しい)?(?:よ|ね)?[\s。.!！]*")
# 取消の動詞（節のどこかにある）
_CANCEL_VERB = re.compile(r"取り消|取消|キャンセル|元に戻|もとに戻|戻して|やめ")
# 取消の打ち消し・候補を残す言葉
_N = re.compile(r"取り消さな|取り消さず|取消さな|キャンセルしな|キャンセルせず|戻さな|戻さず|やめないで|やめずに"
                r"|(?:キャンセル|取り消し|取消|取り消すの|取消すの|元に戻すの|戻すの)(?:は|を)やめ")
# ほかの操作の打ち消し（「C00004を選ぶのはやめて」「比較はやめて」「それはやめて」）：候補の取消ではない。今の道（止める）
_OTHER_STOP = re.compile(r"^(?!.*(?:提案|変更|候補))(?:.+?)(?:の)?(?:は|を)やめ(?:て|ます)(?:ください)?[\s。.!！]*$")
# 取消の明示の文を、疑問の形にしただけ（「元に戻しますか」「取り消して？」）：今の道が［「元に戻します」でよいですか？］の
# ボタンで確かめる（押すと取消）。確かめるまでは保留にする
_QUESTION_TAIL = re.compile(r"(?:か|[?？])[\s。.!！?？]*$")
_KEEP_WORDS = re.compile(r"^(?:今の|この|その)?(?:候補|提案|変更)?(?:は|を)?"
                         r"(?:このまま|そのまま)(?:で)?(?:いい|よい|良い|大丈夫|お願いします|お願い)?(?:です)?[\s。.!！]*$"
                         r"|この候補を残(?:す|して)(?:ください)?[\s。.!！]*$")
# 言い直しの印。後ろに新しい内容が来る形（「3回じゃなくて4回」）
_M_BEFORE = re.compile(r"じゃなくて|じゃなく|ではなくて|ではなく|でなくて|でなく|じゃない|違います|違う|ちがう|いや(?=[\s、,]|$)"
                       r"|やっぱり|やはり|間違い|間違え|まちがい|訂正|言い直し")
# 言い直しの印。前に新しい内容が来る形（「4回に変えて」）
_M_AFTER = re.compile(r"に変えて|に変更して|に直して|へ変えて|へ変更して")
# 新しい値（回数・量・軸と符号・位置番号）。「もう一回」「もう一度」は値にしない（副詞）
_ADVERB_ONCE = re.compile(r"もう(?:一|1)回|もう一度|もういちど|一回(?:見せ|比べ|説明)|1回(?:見せ|比べ|説明)")
_V = re.compile(r"[0-9]+(?:\.[0-9]+)?回|[0-9]+(?:\.[0-9]+)?(?:mm|MM|ミリ|みり|%|パーセント)|[XYZxyz][+\-]|"
                r"[XYZxyz]?(?:プラス|マイナス)|C[0-9]{5}|[0-9]+(?:\.[0-9]+)?(?:往復)")
# ためらい・推量・条件・疑問
_H = re.compile(r"かも|かな|迷|たぶん|多分|おそらく|と思う|気がする|ほうがいい|方がいい|べき|もし|なら|たら|れば|場合|とか"
                r"|かどうか|[?？]|(?:ですか|ますか|か|の)[\s。.!！]*$")
_H_HESITATE = re.compile(r"かも|かな|迷|たぶん|多分|おそらく|と思う|気がする|ほうがいい|方がいい|べき")
# 取消のしかた・結果を聞く問い
_HQ = re.compile(r"どうやって|どうやる|どうなる|どうすれば|方法|やり方|とは|って何|ってなに|し方|方を")
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


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


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
        return Verdict(OTHER, rule="3b other stop")
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
            vals = _values(after)
        else:
            vals = []
        if not vals and m_after:
            vals = _values(t[:m_after.start()])
        if vals:
            return Verdict(CORRECTION, value=vals[-1], rule="6 correction")
        # 7：印のほかに中身が無い（「違う」「やっぱり」「今の提案は違う」）
        if _empty_rest(t):
            return Verdict(UNSURE, rule="7 bare marker")
        return Verdict(OTHER, rule="8 marker+other")  # 「ボタンじゃなくてスイッチを押している」：今の道
    if cancel_verb and not _E.fullmatch(t):
        # 取消の動詞があるが、明示の形ではない（「3回はやめて」「やめとこうかな」など）：消さずに確かめる
        return Verdict(UNSURE, rule="cancel verb not explicit")
    return Verdict(OTHER, rule="11 other")
