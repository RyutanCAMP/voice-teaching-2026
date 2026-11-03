"""作業の回数の変更の発話（「C00001からC00003の作業を2回から3回にして」）を読む。プログラムだけで読む（Claude に頼まない）。

設計は docs/REPEAT_COUNT_DESIGN.md 3章。
- 対象の発話：位置の範囲（C00001からC00003・C00001〜C00003）と、回数の変更の言葉（N回から・N回に・N回追加 など）の両方がある発話。
  これに当たらない「2回移動させて」などは、今どおり utterance.pre_check が扱う（繰り返し・往復の2択で聞いて止める）。
- 決まった形だけを読む。形に合わない言葉は、捨てずに示して聞き返す（B1 と同じ）。
- 打ち消し・承認の言葉・疑問の形・ほかの変更との混ざりは、Session が utterance.read_utterance の結果で先に調べる。
- 数（今の回数・変更後の回数）はプログラムがジョブから数えて文に入れる。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import utterance as ut

EXAMPLE = "C00001からC00003の作業を2回から3回にして"
MIXED_MESSAGE = ("1回の指示では、1つの変更だけを行います。回数の変更と、位置の移動・速度・比較などは、"
                 "別々に指示してください")
FORM_UNKNOWN = f"回数の変更の言い方が分かりませんでした。「{EXAMPLE}」のように言ってください"

_SPACE = re.compile(r"[\s、,，]+")
_RANGE = re.compile(r"C\s?([0-9]{5})\s?(?:から|〜|~)\s?C\s?([0-9]{5})")
_COUNT_WORD = re.compile(r"[0-9]+\s?回\s?(?:から|に|へ|追加|ふやし|増やし|足し|たし)")
_LEAD = r"(?:えーと|ええと|えっと|あのー|あの|それでは|じゃあ|では|まず)*"
_FORM = re.compile(
    _LEAD
    + r"C(?P<a>[0-9]{5})(?:から|〜|~)C(?P<b>[0-9]{5})(?:まで)?(?:の(?:作業|区間))?(?:を|は)"
    + r"(?:(?P<said>[0-9]+)回から)?"
    + r"(?:(?P<total>[0-9]+)回(?:に|へ)(?:して|変更して|変えて)?"
    + r"|(?P<add>[0-9]+)回(?:追加して|追加|ふやして|増やして|足して|たして))"
    + r"(?:ください|下さい|お願いします|おねがいします)?[。.!！]*")
# 示すために、読める言葉を取り除く順（残ったものが分からない言葉）
_TOKENS = [re.compile(p) for p in (
    r"C[0-9]{5}(?:から|〜|~)C[0-9]{5}", r"[0-9]+回(?:から|に|へ)", r"[0-9]+回(?:追加|ふやし|増やし|足し|たし)",
    r"までの作業|までの区間|の作業|の区間|まで", r"ください|下さい|お願いします|おねがいします",
    r"変更して|変えて|して|て", r"えーと|ええと|えっと|あのー|あの|それでは|じゃあ|では|まず",
    r"を|は|の", r"[。.!！]",
)]


@dataclass(frozen=True)
class RepeatRequest:
    start: str
    end: str
    said_count: int | None  # 言った今の回数（「2回から」）。言わなければ None
    total: int | None       # 変更後の合計の回数（「3回に」）
    add: int | None         # 「3回追加」「3回ふやして」の数（合計か追加かが分からない）


def _compact(text: str) -> str:
    return _SPACE.sub("", ut.normalize(text))


def is_repeat_request(text: object) -> bool:
    """回数の変更の発話か（位置の範囲と、回数の変更の言葉の両方がある）。"""
    if not isinstance(text, str):
        return False
    t = ut.normalize(text)
    return bool(_RANGE.search(t) and _COUNT_WORD.search(t))


def read(text: str) -> RepeatRequest | None:
    """決まった形の文だけを読む。合わなければ None（leftovers で分からない言葉を示す）。"""
    m = _FORM.fullmatch(_compact(text))
    if m is None:
        return None
    return RepeatRequest(
        start=f"C{m.group('a')}", end=f"C{m.group('b')}",
        said_count=int(m.group("said")) if m.group("said") else None,
        total=int(m.group("total")) if m.group("total") else None,
        add=int(m.group("add")) if m.group("add") else None)


def leftovers(text: str) -> tuple[str, ...]:
    """決まった形の言葉を取り除いて残った言葉（分からない言葉として示す）。"""
    t = _compact(text)
    for pat in _TOKENS:
        t = pat.sub(" ", t)
    return tuple(dict.fromkeys(w for w in t.split() if w))


def canonical(start: str, end: str, said: int, total: int) -> str:
    """選択肢を押したときに送る、決まった文（今の回数を入れる。同じ検査を通る）。"""
    return f"{start}から{end}の作業を{said}回から{total}回にして"


def unknown_message(words: tuple[str, ...]) -> str:
    if not words:
        return FORM_UNKNOWN
    return f"{''.join(f'「{w}」' for w in words)}が分かりませんでした。回数の変更は「{EXAMPLE}」のように言ってください"


def mismatch_message(target: str, count: int) -> str:
    return f"このジョブでは、{target} は今 {count} 回です。何回にしますか？"


def confirm_message(target: str, count: int, total: int) -> str:
    return f"このジョブでは、{target} は今 {count} 回です。{count}回 → {total}回 にしますか？"


def add_message(target: str, count: int, add: int) -> str:
    return (f"「{add}回」が合計の回数か、今の回数に足す回数か分かりません。"
            f"このジョブでは、{target} は今 {count} 回です。どちらですか？")
