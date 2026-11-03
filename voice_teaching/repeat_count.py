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
DECIMAL_MESSAGE = "回数は整数（1〜5回）で言ってください。小数の回数は扱いません"

# 範囲の確認（唐川さんの決定 2026-11-03）：ボタンだけで確定する。声の「はい」では確定しない
RANGE_YES_LABEL = "この範囲を繰り返す"
RANGE_NO_LABEL = "違う"
RANGE_NO_REPLY = "分かりました。変更はしません"
RANGE_STALE = "この範囲の確認は、もう使えません（新しい指示・位置の選び直し・元に戻すがありました）。もう一度指示してください"

_SPACE = re.compile(r"[\s、,，]+")
_KANJI = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}
_KANA = {"いっかい": "1回", "にかい": "2回", "さんかい": "3回", "よんかい": "4回", "ごかい": "5回"}
_RANGE = re.compile(r"C([0-9]{1,5})(?:から|〜|~)C([0-9]{1,5})(?![0-9])")
# 回数の変更の言葉（これがあれば回数の変更の発話として読む）
_COUNT_WORD = re.compile(
    r"[0-9]+回(?:から|に|へ|追加|ふやし|増やし|足し|たし|繰り返|くりかえ|やって)|もう1回(?:ふやし|増やし|追加|足し)"
    r"|[0-9]+(?:\.[0-9]+)回|C[0-9]{1,5}(?:から|〜|~)C[0-9]{1,5}.*[0-9]+回[。.!！]*$")
_LEAD = r"(?:えーと|ええと|えっと|あのー|あの|それでは|じゃあ|では|まず)*"
_RANGE_PART = r"(?:C(?P<a>[0-9]{1,5})(?:から|〜|~)C(?P<b>[0-9]{1,5})(?:まで)?(?:の(?:作業|区間))?(?:を|は)?)?"
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


def to_digits(text: str) -> str:
    """読み取りの前に、回数の漢数字（一〜五）・かな（さんかい）・「3かい」を数字の「3回」に直す。空白・読点は除く。"""
    t = _SPACE.sub("", ut.normalize(text))
    for k, v in _KANA.items():
        t = t.replace(k, v)
    t = re.sub(r"([0-9])かい", r"\1回", t)
    t = re.sub(r"([一二三四五])回", lambda m: _KANJI[m.group(1)] + "回", t)
    return t


def is_repeat_request(text: object) -> bool:
    """回数の変更の発話か（回数の変更の言葉がある）。"""
    return isinstance(text, str) and bool(_COUNT_WORD.search(to_digits(text)))


def has_decimal_count(text: str) -> bool:
    return bool(re.search(r"[0-9]+\.[0-9]+回", to_digits(text)))


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
            one_more=form is _FORM_ONE_MORE)
    return None


def canonical(start: str, end: str, said: int, total: int) -> str:
    """選択肢を押したときに送る、決まった文（今の回数を入れる。同じ検査を通る）。"""
    return f"{start}から{end}の作業を{said}回から{total}回にして"


def range_message(first: int, last: int, cycle_texts: tuple[str, ...], count: int, block_last: int, total: int) -> str:
    """範囲の確認の文。行番号は 1 から。数・命令の行はすべてプログラムが入れる。"""
    return (f"この範囲（{first}〜{last}行目：{'・'.join(cycle_texts)}）を繰り返しますか？ "
            f"今は{count}回続けて書かれています（{first}〜{block_last}行目）。合計{total}回にします")


def mismatch_message(target: str, count: int) -> str:
    return f"このジョブでは、{target} は今 {count} 回です。何回にしますか？"


def add_message(target: str, count: int, add: int) -> str:
    return (f"「{add}回」が合計の回数か、今の回数に足す回数か分かりません。"
            f"このジョブでは、{target} は今 {count} 回です。どちらですか？")


# 示すために、読める言葉を取り除く順（残ったものが分からない言葉）
_TOKENS = [re.compile(p) for p in (
    r"C[0-9]{1,5}(?:から|〜|~)C[0-9]{1,5}", r"もう1回", r"[0-9]+回(?:から|に|へ)?",
    r"追加|ふやして|増やして|足して|たして|繰り返して|くりかえして|やって",
    r"までの作業|までの区間|の作業|の区間|まで|作業", r"ください|下さい|お願いします|おねがいします",
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
