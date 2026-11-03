"""発話（音声認識の文字・文字入力）の読み取りと、Claude の提案との照らし合わせ。Claude に頼らない、プログラムだけの部分。

方針（docs/VOICE_DESIGN.md、仕様v2.1 5-3・6章）:
- 発話からは、はっきり書かれた事実だけを取り出す。あいまいな言葉を推測で数字や軸にしない。
- 量として読むのは「算用数字（全角・半角）＋ mm／ミリ／%」だけ。漢数字・「半分」・単位の無い数字は、量が分からないものとして聞き返す。
- 「右」「左」は枠の設定の軸で決めるもの。枠がまだ無いので、軸を決めずに聞き返す。
- Claude（またはモック）の提案は、取り出した事実と一致したときだけ通す。食い違えば聞き返す。
- 承認・書き出し・転送・ロボットの起動は、声や文字では行わない。
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

# 画面にも文章にも出さない、安全や距離を断定する言葉（CLAUDE.md・受入テスト18）
FORBIDDEN_WORDS = (
    "安全です", "安全な", "安全に", "安全でした",
    "当たります", "当たりません", "当たらない位置", "当たらないので",
    "干渉します", "干渉しません", "干渉しない",
    "ぶつかります", "ぶつかりません", "ぶつからない",
    "問題ありません", "大丈夫です",
)

OPS = ("move", "speed", "compare", "undo")  # 1つの発話で1つだけ
KIND_ORDER = ("select", "move", "speed", "compare", "undo", "approve", "start")

APPROVE_MESSAGE = "承認・書き出し・転送は、画面のボタンで行います。声や文字の指示では行いません"
START_MESSAGE = "このアプリからはロボットを動かしません。ロボットはペンダントで選んで、人がスタートします"
SIDE_MESSAGE = ("「右」「左」は、専門家が枠の設定で軸を決めるまで使えません（軸が決まっていません）。"
                "X・Y・Z の方向、または「上げて」「下げて」で指示してください")
FRONT_BACK_MESSAGE = "前・後ろの向きは、このアプリでは扱いません"


def contains_forbidden(text: str) -> bool:
    return any(w in text for w in FORBIDDEN_WORDS)


# ---- 正規化 ----
_MINUS = str.maketrans({"−": "-", "‐": "-", "‑": "-", "–": "-"})


def normalize(text: str) -> str:
    """全角英数字を半角に（NFKC）、英字を大文字にする。"""
    # 音声認識が返すマイナス記号（−・‐ など）も「-」として読む
    return unicodedata.normalize("NFKC", text).upper().translate(_MINUS)


def normalize_position(text: str) -> str | None:
    """'c00005'・'Ｃ００００５' などを 'C00005' にする。形が違えば None。"""
    t = normalize(text).strip()
    m = re.fullmatch(r"C\s?([0-9]{5})", t)
    return f"C{m.group(1)}" if m else None


# ---- 取り出した事実 ----
@dataclass(frozen=True)
class Amount:
    value: float          # 符号を除いた量
    unit: str             # "mm" または "%"
    sign: int | None      # 数字の前の +／-／プラス／マイナス。無ければ None
    text: str             # 発話の中の書き方


@dataclass(frozen=True)
class UtteranceFacts:
    raw: str
    text: str
    kinds: tuple[str, ...] = ()
    positions: tuple[str, ...] = ()
    bad_positions: tuple[str, ...] = ()
    amounts: tuple[Amount, ...] = ()
    unclear_amounts: tuple[str, ...] = ()
    axes: tuple[str, ...] = ()
    axis_signs: tuple[int, ...] = ()       # 方向の言葉の向き（X-方向、上げて など）
    sides: tuple[str, ...] = ()            # 右・左（軸は枠で決める）
    speed_signs: tuple[int, ...] = ()      # 速度を上げる +1 ／下げる -1
    refers_to_selection: bool = False      # 「この位置」「ここ」など
    stray_signs: int = 0                   # 量にも軸にも付かなかった符号（＋・−・プラス・マイナス）の数
    vertical: bool = False                 # 「上げ・下げ・上に・下に」を Z（上下）として読んだ
    stop_reasons: tuple[str, ...] = ()
    # 読み取りで使わなかった言葉（つなぎの言葉の一覧 FILLER_WORDS にも無いもの）。
    # 移動・速度の指示で1つでもあれば聞き返す（「バイオ 3ミリ 下げて」を Z −3 にしない。docs/UNKNOWN_WORDS_PLAN.md）
    unknown_words: tuple[str, ...] = ()
    # 打ち消し・取り消し・言い直しの言葉（「下げないで」「駄目」「じゃなくて」）。移動・速度の指示にあれば止める（CHECKER_023A B6・B7）
    negations: tuple[str, ...] = ()
    # 元に戻す言葉そのもの（「やめて」「取り消して」）の打ち消し。「やめて」だけの元に戻す指示（_plain_undo）のときは空。
    # 空でなければ、どの指示でも止める（「キャンセルはやめて」「C00004を選ぶのはやめて」）
    undo_negations: tuple[str, ...] = ()
    # 2026-11-03 夜の決定（docs/UNKNOWN_WORDS_PLAN.md 7章）。対話の機能・画面から使う
    question: bool = False                 # 疑問の形（文の終わりの ？・?・ですか など）。候補にせず、確かめる
    question_text: str | None = None       # ？・か を外した同じ指示（確かめる選択肢の文）。答えと合わせたときは None
    repeat: tuple[int | None, str] | None = None  # 繰り返し・往復 (回数, "ambiguous"／"times"／"round_trip")。候補にしない
    place_ref: str | None = None           # 「置くところ」などの場所の言葉（どの位置かはプログラムが決めない）
    relative_dir: tuple[str, ...] = ()     # 手前・奥・前・後ろ・右・左（軸には当てない。止める・聞き返す）

    @property
    def position(self) -> str | None:
        return self.positions[0] if len(self.positions) == 1 else None

    @property
    def amount(self) -> Amount | None:
        return self.amounts[0] if len(self.amounts) == 1 and not self.unclear_amounts else None

    @property
    def axis(self) -> str | None:
        return self.axes[0] if len(self.axes) == 1 else None

    @property
    def op(self) -> str | None:
        ops = [k for k in self.kinds if k in OPS]
        return ops[0] if len(ops) == 1 else None

    def requested_value(self) -> float | None:
        """利用者が言った、符号つきの量（mm または %）。決まらなければ None。"""
        a = self.amount
        if a is None:
            return None
        if self.op == "speed":
            signs = [s for s in (a.sign, *self.speed_signs) if s is not None]
        else:
            signs = [s for s in (a.sign, *self.axis_signs) if s is not None]
        if len(signs) > 1:
            return None  # 向きの指定が2つ（あいまい）
        if not signs:
            return None  # 符号が分からない（「Y方向に3mm」）。＋を補わない（CHECKER_021 D2・2026-11-03 本人の方針）
        return signs[0] * a.value

    def sign_count(self) -> int:
        """量と方向の言葉についた向き（＋・−）の数。"""
        a = self.amount
        own = [a.sign] if a is not None and a.sign is not None else []
        return len(own) + len(self.speed_signs if self.op == "speed" else self.axis_signs)


# 数字は半角の 0〜9 だけを読む（NFKC のあとも残る ٣ などの数字は、知らない言葉として聞き返す）
_POS_RE = re.compile(r"(?<![A-Z0-9])C\s?([0-9]+)(?![0-9.])")
_NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_SIGN_BEFORE = re.compile(r"(マイナス|プラス|[+\-])\s?$")
_UNIT_AFTER = re.compile(r"^\s?(MM|ミリ|%)")
_OTHER_UNIT_AFTER = re.compile(r"^\s?(センチ|CM|M(?![A-Z])|メートル|パーセント|割|度|倍)")
_KANJI_AMOUNT = re.compile(r"[〇零一二三四五六七八九十百千万]+\s?(ミリ|MM|センチ|CM|%|パーセント|割)")
_VAGUE = re.compile(r"そこそこ|半分|少し|すこし|ちょっと|ちょい|わずか|僅か|倍|もう少し|適当")
_AXIS_RE = re.compile(r"(?<![A-Z])([XYZ])\s?(マイナス|プラス|[+\-])?\s?(方向|軸|に|へ)")
# 「Xマイナス5ミリ」「Y +3 mm」「X の -5ミリ」：軸のすぐ後（「の」をはさんでもよい）に符号と数字。符号は量の側（_SIGN_BEFORE）で読むので、ここでは軸だけ
_AXIS_SIGNED_NUM = re.compile(r"(?<![A-Z])([XYZ])\s?(?:の\s?)?(?=(?:マイナス|プラス|[+\-])\s?[0-9])")
# 単独の X・Y・Z（「Yを」「Yの」も、軸を言ったものとして読む。CHECKER_021 D1）
_AXIS_WORD = re.compile(r"(?<![A-Z])([XYZ]{1,3})(?![A-Z])")
# 座標の値の上げ下げ（「Y座標を3mm下げて」「Yの値を3mm減らして」）：その軸の値の増減として読む（2026-11-03 本人の判断）
_COORD_VALUE = re.compile(r"(?<![A-Z])([XYZ])\s?(?:の\s?)?(?:座標|値)")
_VALUE_UP = re.compile(r"上げ|増や|大きく")
_VALUE_DOWN = re.compile(r"下げ|減ら|小さく")
_TOWARD_UP = re.compile(r"(?<!以)(上に|上へ|上方向)")      # 座標の値の話のときも、これは「上へ」（Z）
_TOWARD_DOWN = re.compile(r"(?<!以)(下に|下へ|下方向)")
_UP = re.compile(r"(?<!以)(上げ|上に|上へ|上方向)")
_DOWN = re.compile(r"(?<!以)(下げ|下に|下へ|下方向)")
_SIDE = re.compile(r"(右|左)")
_FRONT_BACK = re.compile(r"(?<!変更)(前|後ろ|手前|奥)(に|へ|方向)(?!戻)")
_SELECT = re.compile(r"選択|選ん|選ぶ|選べ")
_MOVE = re.compile(r"移動|動か|ずら|寄せ|動いて")
_SPEED = re.compile(r"移動速度|速度|速さ|スピード|速く|早く|遅く")
_FASTER = re.compile(r"速く|早く")
_SLOWER = re.compile(r"遅く")
_COMPARE = re.compile(r"比較|比べ|差分|違い")
_UNDO = re.compile(r"元に戻|もとに戻|元へ戻|戻して|取り消|取消|キャンセル|やめて")
_APPROVE = re.compile(
    r"承認|(?<![A-Z])OK(?![A-Z])|オーケー|これでいい|それでいい|決定|確定|書き出|書出|転送|送って|保存"
    r"|(^|[、。,\s])はい([、。,!！\s]|$)")
_START = re.compile(r"起動|スタート|運転開始|動作開始|実行して|再生して")
_THIS = re.compile(r"この位置|この点|この命令|ここ|その位置|その点|そこ")

_SIGN = {"+": 1, "プラス": 1, "-": -1, "マイナス": -1}
_SIGN_TOKEN = re.compile(r"マイナス|プラス|[+\-]")


# 音声認識が返す、軸と符号の別の書き方（実マイクで「ワイプラス 3mm」「Y の Plus 3ミリ」と返った）。
# 言った言葉の書き方を揃えるだけで、符号や軸を推測で足さない。カタカナの軸名は、すぐ後に符号・方向・軸・「の」があり、
# すぐ前がカタカナでないときだけ（「ガゼット」「ハワイ」を軸にしない）
_SPOKEN_AXIS = re.compile(r"(?<![ァ-ヺー])(エックス|ワイ|ゼット|ズィー)(?![ァ-ヴー])|(?<![ァ-ヺー])(エックス|ワイ|ゼット|ズィー)(?=プラス|マイナス)")
_SPOKEN_SIGN = re.compile(r"(?<![A-Z])(PLUS|MINUS)(?![A-Z])")


def _spoken(text: str) -> str:
    text = _SPOKEN_AXIS.sub(lambda m: {"エックス": "X", "ワイ": "Y", "ゼット": "Z", "ズィー": "Z"}[m.group(1) or m.group(2)], text)
    return _SPOKEN_SIGN.sub(lambda m: {"PLUS": "プラス", "MINUS": "マイナス"}[m.group(1)], text)


# ---- 読み取りで使わなかった言葉（知らない言葉）----
# 読み取りで使った部分を取り除いたあとに残ってよい、つなぎの言葉（これだけ。短く保つ）。
# 残ってよいのは、決まった場所に、決まった形で1つずつ（下の _run_is_allowed）。同じ言葉を並べた「もも」「でも」は通さない。
# ここに無いもの（カタカナ・漢字・英字・この一覧に無いひらがな）は「知らない言葉」として、移動・速度の指示では聞き返す。
# 迷う言葉は入れない（入れなければ聞き返しになるだけ）。聞き違いを軸に読み替える一覧（「バイオ」→ Y など）は作らない。
# 打ち消しの言葉（NEGATION_WORDS）は、どの一覧にも入れない（tests/test_unknown_words.py で確かめる）。
PARTICLES = ("を", "に", "へ", "の", "で", "は", "が", "と", "も")          # 読んだ言葉のすぐ後に1つだけ
TE_FORMS = ("して", "て")                                                   # 動きの言葉のすぐ後だけ（下げて・移動して）
REQUESTS = ("ください", "お願いします", "おねがいします")                    # 読んだ言葉・助詞・て の後
POLITE_AFTER_TE = ("下さい",)                                               # 「下げて下さい」：て のすぐ後だけ（下（Z）とは読まない）
FINALS = ("ね", "よ")                                                       # 文の終わりだけ。助詞の後はだめ（「はね」）
HESITATIONS = ("えーと", "ええと", "えっと", "あのー", "あの")               # 1つだけで区切られていれば、どこでも
OPENERS = ("それでは", "じゃあ", "では", "まず")                             # 文の初め（読んだ言葉より前）に1つだけで
FILLER_WORDS = PARTICLES + TE_FORMS + REQUESTS + POLITE_AFTER_TE + FINALS + HESITATIONS + OPENERS
# 読み取りで使った動きの言葉のすぐ後の言い終わり（「下げる」「下げます」「ずらす」「移動させて」）。2026-11-03 シクロの判断
VERB_ENDINGS = ("させて", "させ", "します", "ます", "です", "る", "す")  # 「動かします」「移動します」も（2026-11-03 夜）
# 読んだ指示の後の、文の終わりの「です」（「下に3ミリです」「Xのマイナス5ミリです」。2026-11-03 夜）。文の途中・初めでは使わない
SENTENCE_END_COPULA = ("です",)
# 量のすぐ前の「あと」「もう」（「あと3mm下げて」。CHECKER_023A）。「もう少し」はあいまいな量として聞き返す
AMOUNT_PREFIXES = ("あと", "もう")

# 打ち消し・取り消し・言い直しの言葉。移動・速度の指示にあれば、知らない言葉より先に止める（CHECKER_023A B6・B7）
NEGATION_WORDS = ("じゃなく", "じゃない", "ではなく", "でなく", "たくない", "なかっ", "ないで", "なくて", "ない", "なく",
                  "駄目", "だめ", "ダメ", "やめ", "禁止", "違う", "ちがう", "取り消し",
                  "ません", "ずに", "ぬ", "なし", "不要", "ストップ", "中止", "いや", "嫌", "なよ", "んな", "いらない",
                  "いいえ", "止めて", "待って", "結構です", "無し", "無理")
# 一覧のほかに、決まった形で見つける打ち消し：文の終わりの「下げるな」「動かすな」、文の終わりの「下げず」、英字の「NG」
NEGATION_MESSAGE = "{word}などの打ち消しの言葉があるため、変更しません。してほしいことだけを言ってください"


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _fold_kana(text: str) -> str:
    """カタカナをひらがなにする（打ち消しを、書き方によらず見つけるため。「やメて」「ダメ」「ストップ」）。字の数は変えない。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


_UNDO_FOLDED = re.compile(_fold_kana(_UNDO.pattern))


_NEGATION_RE = re.compile(
    f"(?:{_alt(dict.fromkeys(_fold_kana(w) for w in NEGATION_WORDS))})"
    r"|(?<=[るすく])な(?=[\s、。,.!！?？]*$)"   # 文の終わりの「下げるな」「動かすな」
    r"|(?<=[ぁ-ゖ])ず(?=[\s、。,.!！?？]*$)"    # 文の終わりの「下げず」「動かさず」
    r"|(?:(?<![A-Z])|(?<=MM))NG(?![A-Z])")  # 「3mmNG」は大文字にすると「3MMNG」
_P, _TE, _REQ, _FIN = _alt(PARTICLES), _alt(TE_FORMS), _alt(REQUESTS), _alt(FINALS)
# 読んだ言葉の後の1つのひと続き（文の終わりでないとき／文の終わりのとき）
_TAIL_MID = re.compile(f"(?:{_P})|(?:{_P})?(?:{_REQ})|(?:{_TE})(?:{_REQ}|{_alt(POLITE_AFTER_TE)})?")
_TAIL_END = re.compile(f"{_TAIL_MID.pattern}|(?:(?:{_TE})(?:{_REQ}|{_alt(POLITE_AFTER_TE)})?|(?:{_REQ}))?(?:{_FIN})"
                       f"|{_alt(SENTENCE_END_COPULA)}")
_LEAD_STRIP = re.compile(f"{_P}|{_alt(HESITATIONS)}|{_alt(OPENERS)}")
_TAIL_STRIP = re.compile(f"(?:{_P})?(?:{_REQ})?")  # 示すときに後ろから外す助詞など（「バイオを」→「バイオ」）
_ANY_FILLER_SEQ = re.compile(f"(?:{_alt(FILLER_WORDS)})+")
_HESITATION_RE = re.compile(_alt(HESITATIONS))
_OPENER_RE = re.compile(_alt(OPENERS))
# て・して が付いてよい、読んだ動きの言葉の最後の字（下げ・動か・増や・ずら・寄せ・遅く・移動、元に戻・取り消・比べ・比較・選択）
_VERB_END_CHARS = "げかやらせく動戻消べ較択"
# 言葉の区切り（空白・句読点など）。読み取りで使った部分も空白にしてから区切る
_WORD_SEP = re.compile(r"[\s、。,，.．!！?？・／/「」『』（）()…~〜]+")
_WORD = re.compile(r"[^\s、。,，.．!！?？・／/「」『』（）()…~〜]+")
# 「Yのマイナス方向」「Y軸」「X軸方向」：軸のあとの「方向」「軸」（軸を言っていない「方向」「軸」は、知らない言葉として残す）
_AXIS_DIRECTION_WORD = re.compile(r"(?<![A-Z])[XYZ]\s?(?:の\s?)?(?:(?:マイナス|プラス|[+\-])\s?)?(?:軸方向|方向|軸)")
# 「Y座標」「Yの値」「Y座標値」：座標の値の言葉（_COORD_VALUE で読んだときだけ取り除く）
_COORD_VALUE_WORD = re.compile(r"(?<![A-Z])[XYZ]\s?(?:の\s?)?(?:座標値|座標|値)")
_CONNECTOR = re.compile(r"そして|それから")  # 2つの変更の区切り（_SPLIT_MOVES）
_VERB_TAIL = re.compile(f"(?<=上げ|下げ|増や|減ら|動か|ずら|寄せ|移動)(?:{_alt(VERB_ENDINGS)})")
_AMOUNT_PREFIX = re.compile(f"(?:{_alt(AMOUNT_PREFIXES)})" + r"(?=\s?[0-9]+(?:\.[0-9]+)?\s?(?:MM|ミリ|%))")
# 位置番号のすぐ後の「位置」（「C00005の位置を下に3mm」）。「この位置」「その位置」は _THIS で読む
_POSITION_WORD = re.compile(r"^\s?(?:の\s?)?位置")
# 「ここここ」「そこそこ」：同じ指す言葉の繰り返しは、指す言葉として読まない（そこそこ は、あいまいな量として読む）
_THIS_REPEAT = re.compile(r"(?:この位置|この点|この命令|ここ|その位置|その点|そこ){2,}")


def _negation_words(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """打ち消しの言葉を、前の動きの言葉ごと取り出す（「下げないで」「駄目」）。取り除くことはしない。

    返す値：(打ち消し, 元に戻す言葉そのものの打ち消し)。「やめて」「取り消して」は元に戻す指示の言葉なので、
    ほかの打ち消しと分ける（移動・速度が無ければ、元に戻す指示として使う）。「元に戻さないで」の「ないで」は打ち消し。
    """
    folded = _fold_kana(text)
    undo = [m.span() for m in _UNDO_FOLDED.finditer(folded)]
    out: list[str] = []
    of_undo: list[str] = []
    for m in _NEGATION_RE.finditer(folded):
        if _inside_katakana_word(text, m.start(), m.end()):
            continue  # 「ワイヤー」の「イヤ」、「ダメージ」の「ダメ」は打ち消しではない
        start = m.start()
        while start > 0 and m.start() - start < 4 and ("ぁ" <= text[start - 1] <= "ゖ" or _script(text[start - 1]) == "kanji") \
                and text[start - 1] not in PARTICLES and text[start - 2:start] != "とか":
            start -= 1
        word = text[start:m.end()]  # 示すのは元の書き方
        target = of_undo if any(a < m.end() and m.start() < b for a, b in undo) else out
        if word not in target:
            target.append(word)
    return tuple(out), tuple(of_undo)


def _inside_katakana_word(text: str, start: int, end: int) -> bool:
    """見つけた所が全部カタカナで、前か後ろもカタカナ（長いカタカナの言葉の一部）か。"""
    def kata(i: int) -> bool:
        return 0 <= i < len(text) and _script(text[i]) == "kata"
    return all(kata(i) for i in range(start, end)) and (kata(start - 1) or kata(end))


def _plain_undo(text: str, kinds: set[str]) -> bool:
    """「やめて」「元に戻して」「取り消して」「キャンセル」だけの、元に戻す指示か。

    元に戻す言葉がちょうど1つ、ほかの操作（選択・比較・移動・速度）が無く、「〜のはやめて」「〜はやめて」の形でないとき。
    「キャンセルはやめて」「元に戻すのはやめて」「C00004を選ぶのはやめて」は、元に戻す指示として使わない（止める）。
    """
    folded = _fold_kana(text)
    found = list(_UNDO_FOLDED.finditer(folded))
    if kinds != {"undo"} or len(found) != 1:
        return False
    return not folded[: found[0].start()].rstrip().endswith("は")


def _script(ch: str) -> str:
    if "ぁ" <= ch <= "ゖ":
        return "hira"
    if "ァ" <= ch <= "ヺ" or ch in "ーヽヾ":
        return "kata"
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return "latin"
    if "0" <= ch <= "9":
        return "digit"
    if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or ch in "々〆":
        return "kanji"
    return "other"


_INVISIBLE = ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp", "Mn", "Me")


def _show(word: str) -> str:
    """知らない言葉を、画面で読める形にする。見えない文字（ゼロ幅・制御文字など）だけなら「（見えない文字）」。"""
    visible = "".join(c for c in word if unicodedata.category(c) not in _INVISIBLE)
    return visible if visible.strip() else "（見えない文字）"


def _run_is_allowed(run: str, used_before: bool, used_after: bool, prev_char: str) -> bool:
    """読み取りで使わなかった1つのひと続きが、決まった場所の、決まったつなぎの言葉か。"""
    if _HESITATION_RE.fullmatch(run):
        return True
    if not used_before:  # 読んだ言葉より前（文の初め）
        return bool(_OPENER_RE.fullmatch(run))
    tail = _TAIL_MID if used_after else _TAIL_END
    m = tail.fullmatch(run)
    if not m:
        return False
    if run.startswith(TE_FORMS) and prev_char not in _VERB_END_CHARS:
        return False  # て・して は動きの言葉のすぐ後だけ
    return True


def _unknown_words(residual: str, used_mask: Sequence[bool], source: str) -> tuple[str, ...]:
    """読み取りで使った部分を空白にした文から、知らない言葉を取り出す。

    residual：読み取りで使った部分を空白にした文。used_mask：その字を読み取りで使ったか。source：空白にする前の文。
    区切りと使った部分で分けたひと続きごとに、決まった場所のつなぎの言葉（_run_is_allowed）でなければ知らない言葉にする。
    """
    out: list[str] = []
    for m in _WORD.finditer(residual):
        run = m.group(0)
        before = any(used_mask[: m.start()])
        after = any(used_mask[m.end():])
        prev = source[m.start() - 1] if m.start() > 0 else ""
        if _run_is_allowed(run, before, after, prev):
            continue
        # 示すときは、後ろの助詞などを外す（「バイオを」→「バイオ」）。外すと何も残らないときは、そのまま示す
        # 前の助詞・つなぎの言葉も外す（「をバイオ」→「バイオ」。ひらがなの言葉の途中では分けない）。
        # つなぎの言葉を並べただけのもの（「えーとえーと」「あのあの」）は、分けずにそのまま示す
        word = run if _ANY_FILLER_SEQ.fullmatch(run) else next(
            (run[:k] for k in range(1, len(run)) if _TAIL_STRIP.fullmatch(run[k:])
             and not _ANY_FILLER_SEQ.fullmatch(run[:k])), run)
        word = next((word[k:] for k in range(1, len(word)) if _LEAD_STRIP.fullmatch(word[:k])
                     and _script(word[k]) != "hira" and not _ANY_FILLER_SEQ.fullmatch(word[k:])), word)
        word = _show(word)
        if word not in out:
            out.append(word)
    return tuple(out)


# ひらがなの「さげ」「あげ」は「下げ」「上げ」と同じ動きの言葉として読む（向きは言葉だけから。軸は足さない）。
# 前がひらがなのときは、助詞のあとだけ（「ささげて」を読まない）
_HIRA_VERB = re.compile(r"(?:(?<![ぁ-ゖ])|(?<=[をにへのではがとも]))(さげ|あげ)")
# 疑問の形：文の終わりの ？・?、ひらがなのすぐ後の「か」（「下げますか」「3mmですか」）
_QUESTION_END = re.compile(r"(?:(?<=[ぁ-ゖ])か)?[?？]*[\s　。．.!！]*$")
# 繰り返し・往復（2026-11-03 夜）。「2往復」「2回の移動」ははっきりしたもの。「2回」「行き来」「繰り返し」はどちらか分からない
_ROUND_TRIP = re.compile(r"(?:([0-9]+)\s?)?往復")
_REPEAT_TIMES = re.compile(r"([0-9]+)\s?回の移動|繰り返しの移動")
_REPEAT_AMBIGUOUS = re.compile(r"([0-9]+)\s?回|行き来|繰り返し|繰り返|くりかえし|くりかえ")
REPEAT_STOP_MESSAGE = "このアプリでは、動きの追加・繰り返しはできません。位置の修正だけです"
# 場所の言葉（2026-11-03 夜）。どの位置かは決めない
PLACE_WORDS = ("置くところ", "置き場所", "置く位置", "取るところ", "つかむところ", "離すところ")
_PLACE = re.compile(_alt(PLACE_WORDS))
# 向きの言葉（軸には当てない。手前・前・後ろ・奥は止め、右・左は聞き返す）
_RELATIVE_DIR = re.compile(r"手前|後ろ|奥|(?<!変更)(?<!以)(?<!名)前|右|左")


def _repeat(text: str) -> tuple[tuple[int | None, str] | None, list[tuple[int, int]]]:
    """繰り返し・往復の言葉を読む。(回数, 種類) と、読んだ部分の位置。"""
    for pat, kind in ((_ROUND_TRIP, "round_trip"), (_REPEAT_TIMES, "times"), (_REPEAT_AMBIGUOUS, "ambiguous")):
        found = list(pat.finditer(text))
        if found:
            n = next((int(m.group(1)) for m in found if m.group(1)), None)
            spans = [m.span() for p2 in (_ROUND_TRIP, _REPEAT_TIMES, _REPEAT_AMBIGUOUS) for m in p2.finditer(text)]
            return (n, kind), spans
    return None, []


def read_utterance(raw: str) -> UtteranceFacts:
    q = _QUESTION_END.search(raw)
    if q is not None and ("か" in q.group(0) or "?" in q.group(0) or "？" in q.group(0)):
        # 疑問の形：？・か を外した文を読み、確かめるために印を付ける（候補にはしない。pre_check が確かめる）
        plain = raw[: q.start()].rstrip()
        f = read_utterance(plain)
        return replace(f, raw=raw, question=True, question_text=plain if plain and not f.question else None)
    text = _HIRA_VERB.sub(lambda m: {"さげ": "下げ", "あげ": "上げ"}[m.group(1)], _spoken(normalize(raw)))
    # 位置番号（数字を量と読まないように、先に取り除く）
    positions: list[str] = []
    bad_positions: list[str] = []
    masked = list(text)
    for m in _POS_RE.finditer(text):
        digits = m.group(1)
        if len(digits) == 5:
            name = f"C{digits}"
            if name not in positions:
                positions.append(name)
        else:
            bad_positions.append(m.group(0))
        for i in range(m.start(), m.end()):
            masked[i] = " "
    rest = "".join(masked)

    # 量
    amounts: list[Amount] = []
    unclear: list[str] = []
    for m in _NUM_RE.finditer(rest):
        before = rest[: m.start()]
        after = rest[m.end():]
        if before.endswith((".", ",")) or (before and "0" <= before[-1] <= "9") or after.startswith((",", ".")):
            unclear.append(f"「{m.group(0)}」（区切りのある数字）")
            continue
        unit = _UNIT_AFTER.match(after)
        if unit is None:
            other = _OTHER_UNIT_AFTER.match(after)
            word = other.group(1) if other else ""
            unclear.append(f"「{m.group(0)}{word}」（単位が mm・ミリ・% ではありません）")
            continue
        sm = _SIGN_BEFORE.search(before)
        amounts.append(Amount(
            value=float(m.group(1)),
            unit="%" if unit.group(1) == "%" else "mm",
            sign=_SIGN[sm.group(1)] if sm else None,
            text=(sm.group(0) if sm else "") + m.group(0) + unit.group(0),
        ))
    for m in _KANJI_AMOUNT.finditer(rest):
        unclear.append(f"「{m.group(0)}」（漢数字）")
    for m in _VAGUE.finditer(rest):
        unclear.append(f"「{m.group(0)}」（量がはっきりしません）")

    is_speed = bool(_SPEED.search(rest))
    rest_no_speed = _SPEED.sub(" ", rest)

    # 方向
    axes: list[str] = []
    axis_signs: list[int] = []
    placed_axis_signs = 0  # 軸に付いた符号の数（「X-方向」の -）
    for m in _AXIS_RE.finditer(rest_no_speed):
        if m.group(1) not in axes:
            axes.append(m.group(1))
        if m.group(2):
            axis_signs.append(_SIGN[m.group(2)])
            placed_axis_signs += 1
    for m in _AXIS_SIGNED_NUM.finditer(rest_no_speed):
        if m.group(1) not in axes:
            axes.append(m.group(1))
    speed_signs: list[int] = []
    up, down = bool(_UP.search(rest)), bool(_DOWN.search(rest))
    coord = [] if is_speed else list(dict.fromkeys(m.group(1) for m in _COORD_VALUE.finditer(rest_no_speed)))
    vertical = False  # 「上げ・下げ・上に・下に」を Z（上下）として読んだ
    if is_speed:
        speed_signs += [1] * up + [-1] * down
        speed_signs += [1] * bool(_FASTER.search(rest)) + [-1] * bool(_SLOWER.search(rest))
    elif coord:
        # 座標の値の増減：その軸に、増やす＋・減らす−。「上に・下に」は別に Z（上下）として読む（軸が2つなら聞き返す）
        for a in coord:
            if a not in axes:
                axes.append(a)
        axis_signs += [1] * bool(_VALUE_UP.search(rest_no_speed)) + [-1] * bool(_VALUE_DOWN.search(rest_no_speed))
        t_up, t_down = bool(_TOWARD_UP.search(rest)), bool(_TOWARD_DOWN.search(rest))
        if t_up or t_down:
            vertical = True
            if "Z" not in axes:
                axes.append("Z")
            axis_signs += [1] * t_up + [-1] * t_down
    elif up or down:
        vertical = True
        if "Z" not in axes:
            axes.append("Z")
        axis_signs += [1] * up + [-1] * down
    # 単独の X・Y・Z も軸として足す（「Yを3mm下げて」は Y と 下げ（Z）の2つ → 聞き返す）
    if not is_speed:
        for m in _AXIS_WORD.finditer(rest_no_speed):
            for a in m.group(1):  # 「XY」は X と Y
                if a not in axes:
                    axes.append(a)
    sides = tuple(dict.fromkeys(_SIDE.findall(rest)))

    kinds: set[str] = set()
    if _SELECT.search(rest):
        kinds.add("select")
    if is_speed:
        kinds.add("speed")
    if _MOVE.search(rest_no_speed) or axes or sides:
        kinds.add("move")
    if _COMPARE.search(rest):
        kinds.add("compare")
    if _UNDO.search(rest):
        kinds.add("undo")
    stops: list[str] = []
    if _APPROVE.search(rest):
        kinds.add("approve")
        stops.append(APPROVE_MESSAGE)
    if _START.search(rest):
        kinds.add("start")
        stops.append(START_MESSAGE)
    if _FRONT_BACK.search(rest_no_speed):
        kinds.add("move")
        stops.append(FRONT_BACK_MESSAGE)

    # 読み取りで使った部分を空白にして、残った言葉を調べる（その文の読み方で使ったものだけを取り除く）
    used = list(rest)

    def blank(start: int, end: int) -> None:
        for i in range(start, end):
            used[i] = " "

    for m in _NUM_RE.finditer(rest):
        end = m.end()
        unit = _UNIT_AFTER.match(rest[end:]) or _OTHER_UNIT_AFTER.match(rest[end:])
        blank(m.start(), end + (unit.end() if unit else 0))
    # 「ここここ」のような繰り返しでない「この位置」「ここ」だけを、指す言葉として取り除く
    repeats = [m.span() for m in _THIS_REPEAT.finditer(rest)]
    for m in _THIS.finditer(rest):
        if not any(a <= m.start() < b for a, b in repeats):
            blank(m.start(), m.end())
    used_patterns = [_SIGN_TOKEN, _KANJI_AMOUNT, _VAGUE, _UP, _DOWN, _SIDE, _FRONT_BACK, _SELECT, _MOVE,
                     _COMPARE, _UNDO, _APPROVE, _START, _CONNECTOR, _AMOUNT_PREFIX]
    if is_speed:
        used_patterns += [_SPEED]
    else:
        # 速度の指示では、軸の言葉は読まない（使わない言葉として残す）
        used_patterns += [_AXIS_RE, _AXIS_SIGNED_NUM, _AXIS_WORD, _AXIS_DIRECTION_WORD, _TOWARD_UP, _TOWARD_DOWN]
        if coord:
            used_patterns += [_COORD_VALUE_WORD, _VALUE_UP, _VALUE_DOWN]
    for pat in used_patterns:
        for m in pat.finditer(rest):
            blank(m.start(), m.end())
    for m in _VERB_TAIL.finditer(rest):
        if used[m.start() - 1] == " ":  # 前の動きの言葉を読んだときだけ
            blank(m.start(), m.end())
    for m in _POS_RE.finditer(text):
        if len(m.group(1)) == 5:
            w = _POSITION_WORD.match(text[m.end():])
            if w:
                blank(m.end(), m.end() + w.end())
    repeat, repeat_spans = _repeat(rest)
    for a, b in repeat_spans:
        blank(a, b)
    place = _PLACE.search(rest)
    for m in _PLACE.finditer(rest):
        blank(m.start(), m.end())
    negations, undo_negations = _negation_words(text)
    if undo_negations and _plain_undo(text, kinds):
        undo_negations = ()  # 元に戻す指示の言葉そのもの（「やめて」「取り消して」）は打ち消しにしない
    used_mask = [used[i] == " " and not text[i].isspace() for i in range(len(text))]
    unknown = _unknown_words("".join(used), used_mask, text)

    return UtteranceFacts(
        raw=raw, text=text,
        kinds=tuple(k for k in KIND_ORDER if k in kinds),
        positions=tuple(positions), bad_positions=tuple(bad_positions),
        amounts=tuple(amounts), unclear_amounts=tuple(unclear),
        axes=tuple(axes), axis_signs=tuple(axis_signs), sides=sides,
        speed_signs=tuple(speed_signs),
        refers_to_selection=bool(_THIS.search(rest)),
        stray_signs=len(_SIGN_TOKEN.findall(rest)) - sum(a.sign is not None for a in amounts) - placed_axis_signs,
        vertical=vertical,
        stop_reasons=tuple(stops),
        unknown_words=unknown,
        negations=negations, undo_negations=undo_negations,
        repeat=repeat, place_ref=place.group(0) if place else None,
        relative_dir=tuple(dict.fromkeys(m.group(0) for m in _RELATIVE_DIR.finditer(rest))),
    )


def _merged_extras(previous: UtteranceFacts, answer: UtteranceFacts) -> dict:
    """答えと合わせるときの、疑問・繰り返し・場所・向きの欄。どちらかにあれば残す。
    疑問の形が混ざったときは、確かめる選択肢の文（question_text）を作らない（言い直してもらう）。"""
    return dict(
        question=previous.question or answer.question, question_text=None,
        repeat=answer.repeat or previous.repeat,
        place_ref=answer.place_ref or previous.place_ref,
        relative_dir=tuple(dict.fromkeys(previous.relative_dir + answer.relative_dir)),
    )


def combine_axes(previous: UtteranceFacts, answer: UtteranceFacts) -> UtteranceFacts:
    """量を聞き返した後に、上げ・下げだけの答え（「3mm下げて」）が来たとき：前の軸と合わせる（推測で Z だけにしない）。"""
    return replace(answer, raw=f"{previous.raw} ／ {answer.raw}", text=f"{previous.text} ／ {answer.text}",
                   axes=tuple(dict.fromkeys(previous.axes + answer.axes)),
                   positions=answer.positions or previous.positions,
                   refers_to_selection=previous.refers_to_selection or answer.refers_to_selection,
                   unknown_words=tuple(dict.fromkeys(previous.unknown_words + answer.unknown_words)),
                   negations=tuple(dict.fromkeys(previous.negations + answer.negations)),
                   undo_negations=tuple(dict.fromkeys(previous.undo_negations + answer.undo_negations)),
                   **_merged_extras(previous, answer))


def merge_answer(previous: UtteranceFacts, answer: UtteranceFacts) -> UtteranceFacts:
    """聞き返しへの答え（操作の言葉が無い1文）を、前の発話で分かっていたことに足す。"""
    has_amount_info = bool(answer.amounts or answer.unclear_amounts)
    loose = _SIGN_TOKEN.findall(answer.text)
    if (not has_amount_info and not answer.axes and len(loose) == 1 and answer.stray_signs == 1
            and previous.axes and not previous.axis_signs and previous.amount is not None
            and previous.amount.sign is None):
        # 「プラスですか、マイナスですか？」への答え「マイナス」：前の発話の軸の向きにする
        answer = replace(answer, axes=previous.axes, axis_signs=(_SIGN[loose[0]],), stray_signs=0)
    return UtteranceFacts(
        raw=f"{previous.raw} ／ {answer.raw}",
        text=f"{previous.text} ／ {answer.text}",
        kinds=previous.kinds,
        positions=answer.positions or previous.positions,
        bad_positions=answer.bad_positions if answer.positions or answer.bad_positions else previous.bad_positions,
        amounts=answer.amounts if has_amount_info else previous.amounts,
        unclear_amounts=answer.unclear_amounts if has_amount_info else previous.unclear_amounts,
        axes=answer.axes or previous.axes,
        axis_signs=answer.axis_signs if answer.axes else previous.axis_signs,
        sides=answer.sides or previous.sides,
        speed_signs=answer.speed_signs or previous.speed_signs,
        refers_to_selection=previous.refers_to_selection or answer.refers_to_selection,
        stray_signs=answer.stray_signs if (has_amount_info or answer.axes) else previous.stray_signs,
        stop_reasons=tuple(dict.fromkeys(previous.stop_reasons + answer.stop_reasons)),
        # 答えの中の知らない言葉も、前の発話の知らない言葉も捨てない（どちらかにあれば聞き返す）
        unknown_words=tuple(dict.fromkeys(previous.unknown_words + answer.unknown_words)),
        negations=tuple(dict.fromkeys(previous.negations + answer.negations)),
        undo_negations=tuple(dict.fromkeys(previous.undo_negations + answer.undo_negations)),
        **_merged_extras(previous, answer),
    )


# ---- 判定 ----
@dataclass(frozen=True)
class Verdict:
    status: str   # "ok" / "ask"（聞き返す） / "stop"（止める）
    message: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # 聞き返しの短い選択肢（見せる文, 指示の文）。押すと、その文を指示として送る


# 「上・下 ＝ Z±」を設定済みとみなすか（CHECKER_021 10章の未決A。本人に確認中）。
# 今は FRAME v1.1 6章（枠が無いときは今どおり Z±）に従って True。False にすると、上・下を Z として読まず
# 「上下の設定を確認してください」と聞き返し、聞き返しの選択肢にも［下方向へ …］を出さない
UPDOWN_IS_Z = True
UPDOWN_UNSET_MESSAGE = "上・下が X・Y・Z のどれに当たるかが設定されていません。上下の設定を確認してください"

# 確認済みの方向の名前（軸 → 向き → 名前）。
# 右・左（実機の確認まで未定）、前・後（止める）は未確認なので入れない（推測で足さない）
DIRECTION_NAMES = {"Z": {"+": "上", "-": "下"}} if UPDOWN_IS_Z else {}
DIRECTION_NAMES_SOURCE = "FRAME v1.1 6章（枠が無いときは 上・下 ＝ Z±）" if UPDOWN_IS_Z else "確認済みの方向の名前はありません"


OK = Verdict("ok")


def _ask(msg: str, choices: tuple[tuple[str, str], ...] = ()) -> Verdict:
    return Verdict("ask", msg, choices)


def _num(x: float) -> str:
    return f"{x:g}"


def _where(facts: UtteranceFacts) -> str:
    return f"{facts.position}を" if facts.position else ""


def _stop(msg: str) -> Verdict:
    return Verdict("stop", msg)


def unknown_words_message(words: Sequence[str], op: str) -> str:
    quoted = "".join(f"「{w}」" for w in words)
    if op == "speed":
        return f"{quoted}が分かりませんでした。速度の向きと量を、もう一度言ってください（例：速度を10%下げて）"
    return (f"{quoted}が分かりませんでした。軸（X・Y・Z）と向きと量を、もう一度言ってください"
            "（例：Y座標を3ミリ下げて／下に3ミリ）")


QUESTION_RESTATE_MESSAGE = "質問の形の指示では変更しません。してほしいことを、もう一度言ってください"


def pre_check(facts: UtteranceFacts) -> Verdict:
    """Claude に頼らず、発話だけで止める・聞き返すことが決まるかを調べる。"""
    v = _pre_check(facts)
    if v.status != "ok" or not facts.question:
        return v
    # 疑問の形（「3mm下げて？」）：候補にせず、？を外した同じ指示でよいかを確かめる。選択肢はその文の1つだけ。
    # 知らない言葉・打ち消しがあれば選択肢を出さない（打ち消しは _pre_check で止まっている）
    if facts.unknown_words:
        return _ask(f"{''.join(f'「{w}」' for w in facts.unknown_words)}が分かりませんでした。もう一度指示してください")
    t = facts.question_text
    if not t:
        return _ask(QUESTION_RESTATE_MESSAGE)
    return _ask(f"「{t}」でよいですか？", ((t, t),))


def _pre_check(facts: UtteranceFacts) -> Verdict:
    found = facts.negations + facts.undo_negations
    if found:
        # 打ち消しの言葉を捨てて、言ったことと逆の操作にしない。選択・比較・元に戻すも含めて、いちばん先に止める（選択肢も出さない）。
        # 「やめて」「元に戻して」「取り消して」「キャンセル」だけの指示は、元に戻す指示として使う（undo_negations が空）
        return _stop(NEGATION_MESSAGE.format(word="".join(f"「{w}」" for w in found)))
    if facts.repeat is not None:
        # 繰り返し・往復：候補にしない。「2回」「行き来」はどちらの意味かを、プログラムが作る2つの選択肢で聞く。
        # どちらを選んでも、最後は止める（動きの追加・繰り返しはしない）
        n, kind = facts.repeat
        if kind != "ambiguous":
            return _stop(REPEAT_STOP_MESSAGE)
        if n is None:
            return _ask("繰り返しの移動ですか、往復ですか？", (("繰り返しの移動", "繰り返しの移動"), ("往復", "往復")))
        return _ask(f"{n}回の移動ですか、{n}往復ですか？", ((f"{n}回の移動", f"{n}回の移動"), (f"{n}往復", f"{n}往復")))
    if facts.stop_reasons:
        return _stop("。".join(facts.stop_reasons))
    if not facts.kinds:
        if facts.unknown_words:
            return _ask(f"{''.join(f'「{w}」' for w in facts.unknown_words)}が分かりませんでした。何をするか、もう一度指示してください")
        return _ask("何をするか分かりませんでした。もう一度指示してください")
    ops = [k for k in facts.kinds if k in OPS]
    if len(ops) > 1:
        return _ask("1回の指示では、1つのことだけを行います。1つずつ指示してください")
    if facts.bad_positions:
        return _ask(f"位置番号 {'・'.join(facts.bad_positions)} の形が分かりません。C と5桁の数字で言ってください")
    if len(facts.positions) > 1:
        return _ask(f"位置が {len(facts.positions)}つ（{'・'.join(facts.positions)}）あります。"
                    "1回の指示で変えるのは1つの位置だけです。1つずつ指示してください")
    op = facts.op
    if op in ("move", "speed") and facts.unknown_words:
        # 知らない言葉を捨てて、残りの言葉だけで候補にしない。選択肢も出さない（どれを出しても推測か、言葉を捨てることになる）
        return _ask(unknown_words_message(facts.unknown_words, op))
    if op in ("move", "speed") and facts.place_ref and not facts.positions and not facts.sides:  # 右・左は下の SIDE_MESSAGE
        # 「置くところ」がどの位置かは、プログラムも Claude も決めない（対話の機能が来るまで、聞き返すだけ）
        return _ask(f"「{facts.place_ref}」がどの位置か決まっていません。位置を選んでください")
    if op in ("move", "speed") and facts.stray_signs > 0:
        return _ask("プラス・マイナスがどこに付くか分かりません。「Xのマイナス5ミリ」のように、軸のすぐ後に言ってください")
    if op == "move":
        if len(facts.amounts) > 1 or len(facts.axes) > 1:
            moves = _split_moves(facts.raw)
            if moves:  # 2つ以上の変更：各軸の量を、プログラムが読んだとおりに示して、1つずつ指示してもらう
                listed = "、".join(f"{a}方向 {_fmt(v)}mm" for a, v in moves)
                return _ask(f"変更が{len(moves)}つ入っています（{listed}）。"
                            "1回に動かすのは1つの方向だけです。1つずつ指示してください")
        if facts.sides:
            return _ask(SIDE_MESSAGE)
        if facts.vertical and len(facts.axes) > 1:
            other = "・".join(a for a in facts.axes if a != "Z")
            choices: tuple[tuple[str, str], ...] = ()
            a = facts.amount
            if (len(facts.axes) == 2 and len(facts.axis_signs) == 1 and a is not None and a.sign is None
                    and a.unit == "mm" and not facts.unclear_amounts):
                sign, v, w = facts.axis_signs[0], _num(a.value), _where(facts)
                up = sign > 0
                choices = ((f"{other}座標を {'+' if up else '−'}{v}mm", f"{w}{other}座標を{v}mm{'上げて' if up else '下げて'}"),)
                if UPDOWN_IS_Z:
                    choices += ((f"{'上' if up else '下'}方向へ {v}mm", f"{w}{'上' if up else '下'}に{v}mm"),)
            if not UPDOWN_IS_Z:
                return _ask(f"{other} の値を変えるなら「{other}座標を…上げて／下げて」と言ってください。" + UPDOWN_UNSET_MESSAGE, choices)
            return _ask(f"{other} と、上げ・下げ（上下＝Z）の2つの読み方があります。"
                        f"{other} の値なら「{other}座標を…上げて／下げて」、上下なら「上に…／下に…」のように言ってください",
                        choices)
        if facts.vertical and not UPDOWN_IS_Z:
            return _ask(UPDOWN_UNSET_MESSAGE)
        v = _amount_check(facts, "mm", "何mm動かしますか？")
        if v is not OK:
            return v
        if not facts.axes:
            return _ask("どの方向（X・Y・Z、または上げる・下げる）に動かしますか？")
        if len(facts.axes) > 1:
            return _ask("方向が2つ以上あります。1回の指示で動かすのは1つの方向だけです")
        if facts.sign_count() == 0:
            a, ax, w = facts.amount, facts.axis, _where(facts)
            choices = ()
            if a is not None and a.unit == "mm":
                v = _num(a.value)
                choices = ((f"{ax}のプラス {v}mm", f"{w}{ax}プラスに{v}mm"), (f"{ax}のマイナス {v}mm", f"{w}{ax}マイナスに{v}mm"))
            return _ask(f"{ax}方向のプラスですか、マイナスですか？（「{ax}のマイナス」のように向きも言ってください）", choices)
        if facts.requested_value() is None:
            return _ask("向きの指定が2つあります。どちらの向きか、もう一度指示してください")
    elif op == "speed":
        v = _amount_check(facts, "%", "今の速度に対して、何%変えますか？")
        if v is not OK:
            return v
        if facts.requested_value() is None:
            return _ask("速度を上げますか、下げますか？（向きが分からないか、2つあります）")
    elif "select" in facts.kinds and not facts.positions and not facts.refers_to_selection:
        return _ask("どの位置ですか？ 位置番号（C と5桁の数字）で言ってください")
    return OK


_SPLIT_MOVES = re.compile(r"[、,，。]|そして|それから")


def _split_moves(raw: str) -> list[tuple[str, float]] | None:
    """「上に2mm、Yプラスに3mm」を、区切りごとの（軸, 符号つきの量）に分ける。1つでも読めなければ None。

    1つの変更にまとめることはしない（各軸の量を示して、1つずつ指示してもらうためだけに使う）。
    """
    parts = [p for p in _SPLIT_MOVES.split(raw) if p.strip()]
    if len(parts) < 2:
        return None
    out = []
    for part in parts:
        f = read_utterance(part)
        if (f.op != "move" or f.sides or f.unclear_amounts or len(f.axes) != 1 or len(f.amounts) != 1
                or f.amounts[0].unit != "mm" or f.positions):
            return None
        v = f.requested_value()
        if v is None:
            return None
        out.append((f.axis, v))
    return out


def _amount_check(facts: UtteranceFacts, unit: str, missing_question: str) -> Verdict:
    if facts.unclear_amounts:
        return _ask(f"量がはっきりしません（{'・'.join(facts.unclear_amounts)}）。"
                    f"算用数字と {'mm（ミリ）' if unit == 'mm' else '%'} で量を言ってください")
    if not facts.amounts:
        return _ask(missing_question)
    if len(facts.amounts) > 1:
        return _ask("量が2つ以上あります。1つの量で指示してください")
    if facts.amounts[0].unit != unit:
        word = "移動" if unit == "mm" else "速度"
        return _ask(f"{word}の量は {'mm（ミリ）' if unit == 'mm' else '%'} で言ってください")
    return OK


# ---- Claude（またはモック）の提案 ----
_ALLOWED_KEYS = {"kind", "position", "axis", "side", "delta_mm", "percent"}


def _real(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class ProposedChange:
    """道具 propose_change の引数。ジョブの文字列や説明文は持たない。"""
    kind: str                      # "shift" または "speed"
    position: str
    axis: str | None = None        # "X" / "Y" / "Z"
    side: str | None = None        # "右" / "左"（軸は枠で決める）
    delta_mm: float | None = None  # 符号つき
    percent: float | None = None   # 今の速度に対する割合（符号つき）

    @classmethod
    def from_tool_args(cls, args: Mapping) -> "ProposedChange":
        if not isinstance(args, Mapping):
            raise ValueError("提案の形が読めません")
        extra = set(args) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"提案に使えない項目（{'・'.join(sorted(map(str, extra)))}）が入っています")
        kind = args.get("kind")
        position = args.get("position")
        if kind not in ("shift", "speed"):
            raise ValueError("提案の種類は、位置の移動か速度の変更だけです")
        if not isinstance(position, str) or normalize_position(position) is None:
            raise ValueError("提案の位置番号が読めません")
        axis, side = args.get("axis"), args.get("side")
        delta, percent = args.get("delta_mm"), args.get("percent")
        if kind == "shift":
            if (axis is None) == (side is None):
                raise ValueError("位置の移動の提案には、軸か左右のどちらか1つが要ります")
            if axis is not None and axis not in ("X", "Y", "Z"):
                raise ValueError("提案の軸が読めません")
            if side is not None and side not in ("右", "左"):
                raise ValueError("提案の左右が読めません")
            if not _real(delta) or percent is not None:
                raise ValueError("提案の移動量が数値ではありません")
        else:
            if not _real(percent) or delta is not None or axis is not None or side is not None:
                raise ValueError("提案の速度の割合が数値ではありません")
        return cls(kind=kind, position=normalize_position(position), axis=axis, side=side,
                   delta_mm=float(delta) if delta is not None else None,
                   percent=float(percent) if percent is not None else None)


def _fmt(x: float) -> str:
    return f"{x:+g}"


def check_proposal(facts: UtteranceFacts, proposals: "ProposedChange | Sequence[ProposedChange]",
                   selected: str | None = None) -> Verdict:
    """提案を、発話から取り出した事実と照らし合わせる（受入テスト5・7・11・16）。"""
    v = pre_check(facts)
    if v.status != "ok":
        return v
    plist = [proposals] if isinstance(proposals, ProposedChange) else list(proposals)
    if len(plist) != 1:
        return _ask("1回の提案で変えるのは1つの位置、または1つの命令だけです。1つずつ指示してください")
    p = plist[0]
    if p.side is not None:
        return _stop(SIDE_MESSAGE)
    op = facts.op
    if op not in ("move", "speed"):
        return _stop("この指示は変更を求めていないため、変更の提案は使いません")
    if (op == "move") != (p.kind == "shift"):
        said = "位置の移動" if op == "move" else "速度の変更"
        return _ask(f"指示は{said}ですが、提案の種類が違います。もう一度指示してください")
    expected = facts.position or selected
    if expected is None:
        return _ask("どの位置ですか？ 位置を選ぶか、位置番号で言ってください")
    if p.position != expected:
        return _ask(f"指示の位置（{expected}）と提案の位置（{p.position}）が違います。もう一度指示してください")
    said = facts.requested_value()
    if p.kind == "shift":
        if p.axis != facts.axis:
            return _ask(f"指示の方向（{facts.axis}）と提案の方向（{p.axis}）が違います。もう一度指示してください")
        if not math.isclose(p.delta_mm, said, abs_tol=1e-9):
            return _ask(f"指示の量（{_fmt(said)}mm）と提案の量（{_fmt(p.delta_mm)}mm）が違います。もう一度、量を指示してください")
    else:
        if not math.isclose(p.percent, said, abs_tol=1e-9):
            return _ask(f"指示の量（{_fmt(said)}%）と提案の量（{_fmt(p.percent)}%）が違います。もう一度、量を指示してください")
    return OK


# ---- Claude の文：数字は空欄にプログラムが入れる（受入テスト14） ----
_BLANK = re.compile(r"\{([^{}]+)\}")
_DIGIT = re.compile(r"[0-9０-９〇零一二三四五六七八九十百千万]")


def fill_blanks(template: str, values: Mapping[str, str], fallback: str) -> str:
    """Claude が書いた空欄つきの文に、プログラムが値を入れる。

    空欄以外に数字がある・断定の言葉がある・知らない空欄がある・空欄が無い値がある 文は使わず、
    固定文（fallback。こちらも空欄に値を入れる）にする。
    """
    def fill(t: str) -> str:
        return _BLANK.sub(lambda m: str(values[m.group(1)]), t)

    names = _BLANK.findall(template)
    outside = _BLANK.sub("", template)
    usable = (
        all(n in values for n in names)
        and not _DIGIT.search(normalize(outside))
        and not contains_forbidden(template)
        and "{" not in outside and "}" not in outside
    )
    return fill(template) if usable else fill(fallback)
