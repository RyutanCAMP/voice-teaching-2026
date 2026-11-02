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
from dataclasses import dataclass
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
def normalize(text: str) -> str:
    """全角英数字を半角に（NFKC）、英字を大文字にする。"""
    return unicodedata.normalize("NFKC", text).upper()


def normalize_position(text: str) -> str | None:
    """'c00005'・'Ｃ００００５' などを 'C00005' にする。形が違えば None。"""
    t = normalize(text).strip()
    m = re.fullmatch(r"C\s?(\d{5})", t)
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
    stop_reasons: tuple[str, ...] = ()

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
            if self.op == "speed":
                return None  # 上げるか下げるか分からない
            signs = [1]  # 「X方向に10mm」は + として読む（確認表示に符号を出す）
        return signs[0] * a.value


_POS_RE = re.compile(r"(?<![A-Z0-9])C\s?(\d+)(?![\d.])")
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
_SIGN_BEFORE = re.compile(r"(マイナス|プラス|[+\-])\s?$")
_UNIT_AFTER = re.compile(r"^\s?(MM|ミリ|%)")
_OTHER_UNIT_AFTER = re.compile(r"^\s?(センチ|CM|M(?![A-Z])|メートル|パーセント|割|度|倍)")
_KANJI_AMOUNT = re.compile(r"[〇零一二三四五六七八九十百千万]+\s?(ミリ|MM|センチ|CM|%|パーセント|割)")
_VAGUE = re.compile(r"半分|少し|すこし|ちょっと|ちょい|わずか|僅か|倍|もう少し|適当")
_AXIS_RE = re.compile(r"(?<![A-Z])([XYZ])\s?(マイナス|プラス|[+\-])?\s?(方向|軸|に|へ)")
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


def read_utterance(raw: str) -> UtteranceFacts:
    text = normalize(raw)
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
        if before.endswith((".", ",")) or (before and before[-1].isdigit()) or after.startswith((",", ".")):
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
    for m in _AXIS_RE.finditer(rest_no_speed):
        if m.group(1) not in axes:
            axes.append(m.group(1))
        if m.group(2):
            axis_signs.append(_SIGN[m.group(2)])
    speed_signs: list[int] = []
    up, down = bool(_UP.search(rest)), bool(_DOWN.search(rest))
    if is_speed:
        speed_signs += [1] * up + [-1] * down
        speed_signs += [1] * bool(_FASTER.search(rest)) + [-1] * bool(_SLOWER.search(rest))
    elif up or down:
        if "Z" not in axes:
            axes.append("Z")
        axis_signs += [1] * up + [-1] * down
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

    return UtteranceFacts(
        raw=raw, text=text,
        kinds=tuple(k for k in KIND_ORDER if k in kinds),
        positions=tuple(positions), bad_positions=tuple(bad_positions),
        amounts=tuple(amounts), unclear_amounts=tuple(unclear),
        axes=tuple(axes), axis_signs=tuple(axis_signs), sides=sides,
        speed_signs=tuple(speed_signs),
        refers_to_selection=bool(_THIS.search(rest)),
        stop_reasons=tuple(stops),
    )


def merge_answer(previous: UtteranceFacts, answer: UtteranceFacts) -> UtteranceFacts:
    """聞き返しへの答え（操作の言葉が無い1文）を、前の発話で分かっていたことに足す。"""
    has_amount_info = bool(answer.amounts or answer.unclear_amounts)
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
        stop_reasons=tuple(dict.fromkeys(previous.stop_reasons + answer.stop_reasons)),
    )


# ---- 判定 ----
@dataclass(frozen=True)
class Verdict:
    status: str   # "ok" / "ask"（聞き返す） / "stop"（止める）
    message: str = ""


OK = Verdict("ok")


def _ask(msg: str) -> Verdict:
    return Verdict("ask", msg)


def _stop(msg: str) -> Verdict:
    return Verdict("stop", msg)


def pre_check(facts: UtteranceFacts) -> Verdict:
    """Claude に頼らず、発話だけで止める・聞き返すことが決まるかを調べる。"""
    if facts.stop_reasons:
        return _stop("。".join(facts.stop_reasons))
    if not facts.kinds:
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
    if op == "move":
        if len(facts.amounts) > 1 or len(facts.axes) > 1:
            moves = _split_moves(facts.raw)
            if moves:  # 2つ以上の変更：各軸の量を、プログラムが読んだとおりに示して、1つずつ指示してもらう
                listed = "、".join(f"{a}方向 {_fmt(v)}mm" for a, v in moves)
                return _ask(f"変更が{len(moves)}つ入っています（{listed}）。"
                            "1回に動かすのは1つの方向だけです。1つずつ指示してください")
        if facts.sides:
            return _ask(SIDE_MESSAGE)
        v = _amount_check(facts, "mm", "何mm動かしますか？")
        if v is not OK:
            return v
        if not facts.axes:
            return _ask("どの方向（X・Y・Z、または上げる・下げる）に動かしますか？")
        if len(facts.axes) > 1:
            return _ask("方向が2つ以上あります。1回の指示で動かすのは1つの方向だけです")
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
