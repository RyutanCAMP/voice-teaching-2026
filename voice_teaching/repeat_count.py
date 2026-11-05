"""作業の回数の変更の発話（「C00001からC00003の作業を2回から3回にして」）を読む。プログラムだけで読む（Claude に頼まない）。

設計は docs/REPEAT_COUNT_DESIGN.md 3章。
- 対象の発話：回数の変更の言葉（N回から・N回に・N回追加・N回繰り返して・N回やって・文の終わりの N回 など）がある発話。
  位置の範囲（C00001からC00003・C1〜C3）は言わなくてもよい（ジョブに数えられる区間が1つだけのとき、その区間を示して確かめる）。
  「2回移動させて」など、回数の変更の言葉が無い発話は、今どおり utterance.pre_check が扱う。
- 漢数字の一〜五・かなの回数（「三回」「さんかい」「3かい」）は、読む前に数字に直す（数はプログラムが決める）。
- 決まった形だけを読む。形に合わなければ、まず言い方の例を出して聞き返す（言葉を捨てて読まない）。
- 短い位置の名前（C1・C3）も読む（C1 → C00001）。どの行の範囲かは、範囲の確認のボタンで人が確かめて確定する
  （唐川さんの決定 2026-11-03。CHECKER_029B 2-2）。ジョブに無い位置なら止める。
- 打ち消し・承認の言葉・ほかの変更との混ざりは、Session が utterance.read_utterance の結果で先に調べる。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import utterance as ut

EXAMPLE = "C00001からC00003の作業を2回から3回にして"
MIXED_MESSAGE = ("1回の指示では、1つの変更だけを行います。回数の変更と、位置の移動・速度・比較などは、"
                 "別々に指示してください")
FORM_UNKNOWN = f"回数の変更は「{EXAMPLE}」のように言ってください"
RANGE_NEEDED = f"回数を変えるときは「{EXAMPLE}」のように、範囲（どの位置からどの位置まで）を言ってください"
DECIMAL_MESSAGE = "回数は整数で言ってください。小数の回数は扱いません"
NEGATIVE_MESSAGE = "回数は1以上の整数で言ってください。マイナスの回数は扱いません"
# 範囲の確認を待っている間に、声・文字で範囲を決めようとしたとき（QA P1-A）。確定はせず、範囲の確認は残す
RANGE_BUTTON_ONLY = "範囲は［この範囲を繰り返す］のボタンで決めてください（声や文字では決めません）"
RANGE_APPROVE_GUIDE = "範囲を決めるのは［この範囲を繰り返す］のボタンです。承認は、そのあとの確認表示の承認ボタンで行います"
RANGE_BUTTON_NOTE = "範囲を決めるだけです。承認は次の確認表示で行います"
NO_BLOCK = "このジョブには、続けて2回以上書かれた移動の区間（1回分は2〜10行）が見つかりません"
_CONFIRM_HINT = re.compile(r"繰り?返|くりかえ|はい|そう|うん|OK|オーケー|おけ|お願い|おねがい|範囲|確定|決定|承認|いい|大丈夫|合って|それで")
# 範囲・回数を変えたい言葉、別の指示（出力・転送・速さ・全部）、取りやめ（やっぱり・もう）は、範囲の確認のつもりの言葉にしない
# （家のPCの内部レビュー 9ec263c P2-1・P3-1）。漢数字の回数も数として扱う
_CONFIRM_BAD = re.compile(r"[0-9]|C[0-9]|ない|違|ちが|やめ|止め|戻|消|取り消|キャンセル|しないで|変更|比較|比べ|移動|速度|ミリ|MM|%"
                          r"|変え|広げ|広く|狭|短|長く|選び|別|前の|次の|ほか|他の|[一二三四五六七八九十百]+回|回数"
                          r"|出力|転送|書き出|送っ|送る|ゆっくり|速く|早く|遅く|全部|やっぱり|もう")
_CONFIRM_LIKE = frozenset({
    "この範囲を繰り返す", "この範囲を繰り返して", "この範囲で", "この範囲でお願いします", "繰り返す", "繰り返して",
    "くりかえす", "くりかえして", "繰り返してください", "はい", "はいはい", "そう", "そうそう", "そうです", "うん", "OK",
    "オーケー", "おけ", "お願いします", "おねがいします", "それで", "それでお願いします", "いいよ", "いいです", "いいですよ",
    "大丈夫", "確定", "確定して", "決定", "承認", "承認して", "それでいい", "それでいいです", "合ってる", "合っています",
})

# 範囲の確認（唐川さんの決定 2026-11-03）：ボタンだけで確定する。声の「はい」では確定しない
RANGE_HEADING = "この範囲を繰り返しますか？"
RANGE_YES_LABEL = "この範囲を繰り返す"
RANGE_NO_LABEL = "違う"
RANGE_NO_REPLY = "分かりました。変更はしません"
RANGE_STALE = "この範囲の確認は、もう使えません（新しい指示・位置の選び直し・元に戻すがありました）。もう一度指示してください"

_SPACE = re.compile(r"[\s、,，]+")
_KANJI = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
_KANA = {"いっかい": "1回", "にかい": "2回", "さんかい": "3回", "よんかい": "4回", "ごかい": "5回", "ろっかい": "6回",
         "ななかい": "7回", "はちかい": "8回", "はっかい": "8回", "きゅうかい": "9回", "じゅっかい": "10回", "じっかい": "10回"}
_RANGE = re.compile(r"C([0-9]{1,5})(?:から|〜|~)C([0-9]{1,5})(?![0-9])")
# 回数の変更の言葉（これがあれば回数の変更の発話として読む）
_COUNT_WORD = re.compile(
    r"[0-9]+回(?:から|に|へ|追加|ふやし|増やし|足し|たし|繰り返|くりかえ|やって)|もう1回(?:ふやし|増やし|追加|足し)"
    r"|[0-9]+(?:\.[0-9]+)回|[0-9]+回(?:お願い|おねがい)|[0-9]+回[。.!！]*$")
_LEAD = r"(?:えーと|ええと|えっと|あのー|あの|それでは|じゃあ|では|まず)*"
# 「AとBの動きを」（唐川さんの実機の試し 11/5）も、2つの位置として読む。区間はプログラムがジョブのくり返しから決め、
# 言った位置が区間と合わなければ、合う区間を示して確かめる（Session）。確定は今までどおり［この範囲を繰り返す］のボタンだけ
_RANGE_PART = (r"(?:C(?P<a>[0-9]{1,5})(?:から|〜|~|と)C(?P<b>[0-9]{1,5})(?:まで)?(?:の(?:作業|区間|動き))?(?:を|は)?"
               r"|C(?P<p>[0-9]{1,5})(?:の(?:作業|区間|動き))?(?:を|は))?")  # 位置1つ（「C00001を3回」）：その位置を含む区間だけ
_VERB = r"(?:して|する|したい|してほしい|変更して|変えて|増やして|ふやして|繰り返して|くりかえして|やって)?"
_TAIL = r"(?:ください|下さい|お願いします|おねがいします)?[。.!！]*"
_FORM_TOTAL = re.compile(
    _LEAD + _RANGE_PART + r"(?:作業を)?(?:(?P<said>[0-9]+)回から)?(?P<total>[0-9]+)回(?:に|へ)?" + _VERB + _TAIL)
_FORM_ADD = re.compile(
    _LEAD + _RANGE_PART + r"(?:作業を)?(?P<add>[0-9]+)回(?:追加して|追加|ふやして|増やして|足して|たして)" + _TAIL)
_FORM_ONE_MORE = re.compile(
    _LEAD + _RANGE_PART + r"(?:作業を)?もう1回(?:追加して|追加|ふやして|増やして|足して|たして)" + _TAIL)


@dataclass(frozen=True)
class RepeatRequest:
    start: str | None       # 範囲を言わなければ None（ジョブの区間を示して確かめる）
    end: str | None
    said_count: int | None  # 言った今の回数（「2回から」）。言わなければ None
    total: int | None       # 変更後の合計の回数（「3回に」）
    add: int | None         # 「3回追加」「3回ふやして」の数（合計か追加かが分からない）
    one_more: bool = False  # 「もう1回ふやして」（今の回数 +1）
    position: str | None = None  # 位置を1つだけ言った（「C00001を3回」）：その位置を含む区間だけを示す


def to_digits(text: str) -> str:
    """読み取りの前に、回数の漢数字（一〜五）・かな（さんかい）・「3かい」を数字の「3回」に直す。空白・読点は除く。"""
    t = _SPACE.sub("", ut.normalize(text))
    for k, v in _KANA.items():
        t = t.replace(k, v)
    t = re.sub(r"([0-9])かい", r"\1回", t)
    t = re.sub(r"([一二三四五六七八九十])回", lambda m: _KANJI[m.group(1)] + "回", t)
    return t


def is_repeat_request(text: object) -> bool:
    """回数の変更の発話か（回数の変更の言葉がある）。"""
    return isinstance(text, str) and bool(_COUNT_WORD.search(to_digits(text)))


def has_decimal_count(text: str) -> bool:
    return bool(re.search(r"[0-9]+\.[0-9]+回", to_digits(text)))


def has_negative_count(text: str) -> bool:
    return bool(re.search(r"(?:-|マイナス)[0-9]+回", to_digits(text)))


def is_range_confirm_word(text: object) -> bool:
    """範囲を確定しようとする短い言葉（「この範囲を繰り返す」「はい」「そうそう」など）か。"""
    if not isinstance(text, str):
        return False
    t = re.sub(r"[。．.!！?？、,\s　]+", "", ut.normalize(text))
    if t in {ut.normalize(w) for w in _CONFIRM_LIKE}:
        return True
    # 一覧から少しずれた言い方（「この範囲をくりかえす」「はいそうです」「はい繰り返して」）も、範囲を決めようとする短い文として扱う
    # （Safety P2-3）。打ち消し・別の指示・数・位置を含む文は扱わない（今どおり新しい指示として読む）。確定はしない
    if not t or len(t) > 20 or not _CONFIRM_HINT.search(t) or _CONFIRM_BAD.search(t):
        return False
    f = ut.read_utterance(text)
    return not (f.negations or f.undo_negations or f.amounts or f.positions or f.bad_positions
                or set(f.kinds) - {"approve"} or f.axes or f.place_ref)


def is_approve_word(text: str) -> bool:
    return "承認" in ut.normalize(text)


def _pos(digits: str | None) -> str | None:
    return None if digits is None else f"C{int(digits):05d}"  # C1 → C00001


def read(text: str) -> RepeatRequest | None:
    """決まった形の文だけを読む。合わなければ None。"""
    t = to_digits(text)
    for form in (_FORM_ONE_MORE, _FORM_ADD, _FORM_TOTAL):
        m = form.fullmatch(t)
        if m is None:
            continue
        g = m.groupdict()
        if (g.get("a") is None) != (g.get("b") is None):
            return None
        return RepeatRequest(
            start=_pos(g.get("a")), end=_pos(g.get("b")),
            said_count=int(g["said"]) if g.get("said") else None,
            total=int(g["total"]) if g.get("total") else None,
            add=int(g["add"]) if g.get("add") else None,
            one_more=form is _FORM_ONE_MORE, position=_pos(g.get("p")))
    return None


def canonical(start: str, end: str, said: int, total: int) -> str:
    """選択肢を押したときに送る、決まった文（今の回数を入れる。同じ検査を通る）。"""
    return f"{start}から{end}の作業を{said}回から{total}回にして"


def ranges_text(cycles: list[tuple[int, int]], cycle_texts: tuple[str, ...]) -> str:
    """「1回目：21〜23行目（…）／2回目：24〜26行目／全体：21〜26行目（2回）」。行番号は 1 から。プログラムが入れる。"""
    parts = [f"{k}回目：{a}〜{b}行目" + (f"（{'・'.join(cycle_texts)}）" if k == 1 else "")
             for k, (a, b) in enumerate(cycles, start=1)]
    parts.append(f"全体：{cycles[0][0]}〜{cycles[-1][1]}行目（{len(cycles)}回）")
    return "／".join(parts)


def range_message(cycles: list[tuple[int, int]], cycle_texts: tuple[str, ...], total: int) -> str:
    """範囲の確認の文。数・行番号・命令の行はすべてプログラムが入れる。"""
    return (f"この範囲（{ranges_text(cycles, cycle_texts)}）を繰り返しますか？ "
            f"今は{len(cycles)}回続けて書かれています。合計{total}回にします")


def block_confirm_message(start: str, end: str, total: int) -> str:
    """言った位置がジョブのくり返しの区間と合わないとき：合う区間を示して確かめる。位置・回数はプログラムが入れる。"""
    return f"くり返している区間は {start}〜{end} です。この区間を{total}回にしますか？"


def mismatch_message(target: str, count: int) -> str:
    return f"このジョブでは、{target} は今 {count} 回です。何回にしますか？"


def add_message(target: str, count: int, add: int) -> str:
    return (f"「{add}回」が合計の回数か、今の回数に足す回数か分かりません。"
            f"このジョブでは、{target} は今 {count} 回です。どちらですか？")


# 示すために、読める言葉を取り除く順（残ったものが分からない言葉）
_TOKENS = [re.compile(p) for p in (
    r"C[0-9]{1,5}(?:から|〜|~|と)C[0-9]{1,5}", r"C[0-9]{1,5}", r"もう1回", r"[0-9]+回(?:から|に|へ)?",
    r"追加|ふやして|増やして|足して|たして|繰り返して|くりかえして|やって",
    r"までの作業|までの区間|の作業|の区間|の動き|まで|作業", r"ください|下さい|お願いします|おねがいします|お願い|おねがい",
    r"してほしい|したい|変更して|変えて|して|する|て", r"えーと|ええと|えっと|あのー|あの|それでは|じゃあ|では|まず",
    r"を|は|の", r"[。.!！]",
)]


def leftovers(text: str) -> tuple[str, ...]:
    """決まった形の言葉を取り除いて残った言葉（例のあとに、分からなかった言葉として示す）。"""
    t = to_digits(text)
    for pat in _TOKENS:
        t = pat.sub(" ", t)
    return tuple(dict.fromkeys(w for w in t.split() if w))


def unknown_message(words: tuple[str, ...]) -> str:
    """読めなかったとき：まず言い方の例。分からなかった言葉は、そのあとに示す。"""
    if not words:
        return FORM_UNKNOWN
    return f"{FORM_UNKNOWN}（分からなかった言葉：{''.join(f'「{w}」' for w in words)}）"
