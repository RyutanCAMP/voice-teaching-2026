"""安川ロボットのジョブ（JBI）の読み込み・変更・書き出し。

方針:
- 分からない行は一文字も変えずに残す（改行コードもそのまま）。
- 変えるのは、指定した位置の数値1つ、指定した命令の速度1つ、ジョブ名だけ。
- 変更は元のオブジェクトを書き換えず、新しい Job を返す（元のジョブは必ず残る）。

書き方の前提は docs/JBI_FORMAT_NOTES.md を参照（実機のサンプルで確認待ちの点がある）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

AXES = {"X": 0, "Y": 1, "Z": 2}

_POS_LINE = re.compile(r"^(C\d{5})=(.*)$")
_MOVE = re.compile(r"^(MOVL|MOVJ|MOVC|MOVS)\s+(C\d{5})\b(.*)$")
_SPEED_NUM = re.compile(r"(?<![A-Z])V=(\d+(?:\.\d+)?)(?![\w.])")
_SPEED_ANY = re.compile(r"(?<![A-Z])V=(\S+)")
_PL = re.compile(r"\bPL=(\d+)")
_CR = re.compile(r"\bCR=")
# 変更を断るための広い検出（書き方の揺れも拾う）。読み取れない書き方を「無い」とみなさない
_PL_LOOSE = re.compile(r"\bPL\b", re.IGNORECASE)
_CR_LOOSE = re.compile(r"\bCR\b", re.IGNORECASE)
_SECOND_MOVE = re.compile(r"\+\s*\w*MOV", re.IGNORECASE)
# 変更してよい行の形（許可リスト）。これ以外の語が付いた行は止める
_EDITABLE_MOVL = re.compile(r"MOVL C\d{5} V=\d+(?:\.\d+)?")


class JbiError(Exception):
    """ジョブを変更できないときのエラー（理由をそのまま利用者に見せられる文にする）。"""


@dataclass(frozen=True)
class Position:
    name: str
    values: tuple[float, ...]
    line_index: int

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.values[0], self.values[1], self.values[2])


@dataclass(frozen=True)
class Instruction:
    index: int  # ジョブ全体の中の行番号（0から）
    text: str
    command: str  # MOVL など。移動命令以外は先頭の語
    position: str | None = None
    speed: float | None = None  # 数値で書かれていないときは None
    speed_raw: str | None = None
    pl: int | None = None
    has_cr: bool = False


@dataclass
class Job:
    lines: list[str]  # 改行コードを含んだままの行
    name: str = ""
    postype: str | None = None
    rectan: bool = False
    positions: dict[str, Position] = field(default_factory=dict)
    instructions: list[Instruction] = field(default_factory=list)

    # ---- 書き出し・情報 ----
    def render(self) -> str:
        return "".join(self.lines)

    def is_robot_coordinates(self) -> bool:
        return self.postype == "ROBOT" and self.rectan

    def instruction_at(self, index: int) -> Instruction:
        for ins in self.instructions:
            if ins.index == index:
                return ins
        raise JbiError(f"{index + 1}行目は命令ではありません")

    def users_of(self, position: str) -> list[Instruction]:
        return [i for i in self.instructions if i.position == position]

    def normalized_hash(self) -> str:
        """ジョブ名と日付の行を除いた中身の指紋（引き継ぎの照合に使う）。"""
        h = hashlib.sha256()
        for line in self.lines:
            body = line.rstrip("\r\n")
            if body.startswith("//NAME") or body.startswith("///DATE"):
                continue
            h.update(body.encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()

    # ---- 変更（新しい Job を返す） ----
    def shift_position(self, position: str, axis: str, delta_mm: float) -> "Job":
        if not self.is_robot_coordinates():
            raise JbiError("このジョブの位置はロボット座標の形式ではないため、変更できません")
        if position not in self.positions:
            raise JbiError(f"位置 {position} はこのジョブにありません")
        if axis not in AXES:
            raise JbiError(f"軸 {axis} は扱えません（X・Y・Zのみ）")
        # 位置を変えると、それを使うすべての行の動きが変わる。位置の名前が出てくる行は
        # 書き方にかかわらず全部「使う行」とみなし、1つでも止める対象なら変えない
        mention = re.compile(rf"(?<![A-Za-z0-9]){position}(?!\d)", re.IGNORECASE)
        mentioned = [i for i in self.instructions if mention.search(i.text)]
        if not mentioned:
            raise JbiError(f"位置 {position} はどの命令も使っていないため、このアプリでは変更しません")
        for ins in mentioned:
            _check_editable_move(ins)
        pos = self.positions[position]
        line = self.lines[pos.line_index]
        body, ending = _split_ending(line)
        m = _POS_LINE.match(body)
        assert m is not None
        tokens = m.group(2).split(",")
        k = AXES[axis]
        tokens[k] = _format_like(tokens[k], float(tokens[k]) + delta_mm)
        new_lines = list(self.lines)
        new_lines[pos.line_index] = f"{m.group(1)}={','.join(tokens)}{ending}"
        return parse("".join(new_lines))

    def set_speed(self, index: int, new_speed: float) -> "Job":
        ins = self.instruction_at(index)
        if ins.command != "MOVL":
            raise JbiError(f"{index + 1}行目は直線移動（MOVL）ではないため、速度を変更できません")
        _check_editable_move(ins)
        if ins.speed is None:
            raise JbiError(f"{index + 1}行目の速度は数値で書かれていないため、変更できません")
        body, ending = _split_ending(self.lines[index])
        m = _SPEED_NUM.search(body)
        assert m is not None
        new_body = body[: m.start(1)] + _format_like(m.group(1), new_speed) + body[m.end(1):]
        new_lines = list(self.lines)
        new_lines[index] = new_body + ending
        return parse("".join(new_lines))

    def rename(self, new_name: str) -> "Job":
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", new_name):
            raise JbiError("ジョブ名に使えない文字が入っています")
        new_lines = list(self.lines)
        for i, line in enumerate(new_lines):
            body, ending = _split_ending(line)
            if body.startswith("//NAME "):
                new_lines[i] = f"//NAME {new_name}{ending}"
                return parse("".join(new_lines))
        raise JbiError("ジョブ名の行（//NAME）が見つかりません")


def parse(text: str) -> Job:
    lines = text.splitlines(keepends=True)
    job = Job(lines=lines)
    section = None
    for i, line in enumerate(lines):
        body, _ = _split_ending(line)
        stripped = body.strip()
        if stripped.startswith("//NAME "):
            job.name = stripped[len("//NAME "):].strip()
            continue
        if stripped == "//POS":
            section = "POS"
            continue
        if stripped == "//INST":
            section = "INST"
            continue
        if stripped.startswith("//") and not stripped.startswith("///"):
            section = None
            continue
        if section == "POS":
            if stripped.startswith("///POSTYPE"):
                job.postype = stripped.split(None, 1)[1].strip() if " " in stripped else None
            elif stripped == "///RECTAN":
                job.rectan = True
            else:
                m = _POS_LINE.match(stripped)
                if m:
                    try:
                        values = tuple(float(v) for v in m.group(2).split(","))
                    except ValueError:
                        continue  # 数値でない位置は扱わない（変更対象にもならない）
                    if len(values) >= 3:
                        job.positions[m.group(1)] = Position(m.group(1), values, i)
        elif section == "INST" and stripped and not stripped.startswith("///"):
            job.instructions.append(_parse_instruction(i, stripped))
    return job


def _parse_instruction(index: int, text: str) -> Instruction:
    m = _MOVE.match(text)
    if not m:
        return Instruction(index=index, text=text, command=text.split()[0])
    rest = m.group(3)
    num = _SPEED_NUM.search(rest)
    anyspeed = _SPEED_ANY.search(rest)
    pl = _PL.search(rest)
    return Instruction(
        index=index,
        text=text,
        command=m.group(1),
        position=m.group(2),
        speed=float(num.group(1)) if num else None,
        speed_raw=anyspeed.group(1) if anyspeed else None,
        pl=int(pl.group(1)) if pl else None,
        has_cr=bool(_CR.search(rest)),
    )


def _check_editable_move(ins: Instruction) -> None:
    """このアプリで変えてよい移動命令か確かめる（docs/JBI_FORMAT_NOTES.md）。

    「MOVL 位置 V=数値」だけの行を通す（許可リスト）。PL・CR・同時移動は理由を分けて返す。PLは値にかかわらず止める
    （PL=0 の扱いは確認待ち。決まるまでは厳しい方に合わせる）。
    """
    where = f"{ins.index + 1}行目「{ins.text}」"
    if ins.command != "MOVL":
        raise JbiError(f"{where}は直線移動（MOVL）の命令ではないため、このアプリでは変更しません")
    if _SECOND_MOVE.search(ins.text):
        raise JbiError(f"{where}は別の移動と組になっているため、このアプリでは変更しません")
    if _PL_LOOSE.search(ins.text):
        raise JbiError(f"{where}は PL が付いているため、このアプリでは変更しません")
    if _CR_LOOSE.search(ins.text):
        raise JbiError(f"{where}は CR が付いているため、このアプリでは変更しません")
    if not _EDITABLE_MOVL.fullmatch(ins.text):
        raise JbiError(f"{where}は、このアプリで扱える形（MOVL 位置 V=数値）ではないため、変更しません")


def _split_ending(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _format_like(original: str, value: float) -> str:
    """元の数値と同じ小数点以下の桁数で書く。"""
    original = original.strip()
    decimals = len(original.split(".", 1)[1]) if "." in original else 0
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # -0.000 を 0.000 にする
    return text
