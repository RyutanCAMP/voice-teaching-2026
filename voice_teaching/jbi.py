"""安川ロボットのジョブ（JBI）の読み込み・変更・書き出し。

方針:
- 分からない行は一文字も変えずに残す（改行コード・文字コードもそのまま）。
- 変えるのは、検査を通った位置の数値1つ、または速度の数値1つ、ジョブ名だけ。
- 変更は元のオブジェクトを書き換えず、変更案（ChangeProposal）の中に新しい Job を入れて返す（元のジョブは必ず残る）。
- 分からない書き方は「無い」とみなさず、変更を断る（理由を文で返す）。
- 検査を通ったことは、物理的な安全の保証ではない。

書き方の前提は docs/JBI_FORMAT_NOTES.md を参照（実機のサンプルで確認待ちの点がある）。
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

AXES = {"X": 0, "Y": 1, "Z": 2}

# 1回の位置変更で動かしてよい量の上限（mm）。唐川さんの確認待ちの仮の値
DEFAULT_MAX_SHIFT_MM = 50.0

PATH_WARNING = (
    "この位置を変えると、この位置へ入る移動と、この位置から出る移動の経路が変わります。"
    "検査を通ったことは、物理的な安全の保証ではありません。"
)
SPEED_UNIT_WARNING = (
    "V= の単位は、ロボットの表示単位の設定で変わることがあります（確認待ち）。"
    "速度を変えると、この移動の動き方と時間が変わります。"
)

_NUM = r"[+-]?\d+(?:\.\d+)?"
_NUM_RE = re.compile(_NUM)
_POS_NAME_LINE = re.compile(r"^(C\d{5})=(.*)$")
_MOVE = re.compile(r"^(MOVL|MOVJ|MOVC|MOVS)\s+(C\d{5})\b(.*)$")
# 行の中の位置参照。BC00004 のような別の変数は数えない。大文字小文字は区別しない
_REF = re.compile(r"(?<![A-Z0-9#])C\d{5}\b", re.IGNORECASE)
_SPEED_NUM = re.compile(r"(?<![A-Z])V=(\d+(?:\.\d+)?)(?![\w.])")
_SPEED_ANY = re.compile(r"(?<![A-Z])V=(\S+)")
_PL = re.compile(r"\bPL=(\d+)(?![\w.])")
_CR = re.compile(r"\bCR=")
# 変更を断るための広い検出（書き方の揺れも拾う）。読み取れない書き方を「無い」とみなさない
_PL_LOOSE = re.compile(r"\bPL\b", re.IGNORECASE)
_CR_LOOSE = re.compile(r"\bCR\b", re.IGNORECASE)
_SECOND_MOVE = re.compile(r"\+\s*\w*MOV", re.IGNORECASE)
# 変更してよい行の形（許可リスト）。これ以外の語が付いた行は止める
_EDITABLE_MOVL = re.compile(r"MOVL C\d{5} V=\d+(?:\.\d+)?")
# 位置の意味を変える命令。ジョブに1つでもあれば位置は変えない
_BLOCKING = re.compile(r"(?<![A-Z0-9_])(SFTON|SFTOF|MSHIFT|MFRAME|IMOV|SMOV[LJCS])(?![A-Z0-9_])", re.IGNORECASE)
# 移動命令（出る移動を探すため。止める側に広めに拾う）
_ANY_MOVE = re.compile(r"(?<![A-Z0-9_])(MOV[LJCS]|SMOV[LJCS]|IMOV)(?![A-Z0-9_])", re.IGNORECASE)
_FLOW = re.compile(r"(?<![A-Z0-9_])(JUMP|CALL|RET)(?![A-Z0-9_])", re.IGNORECASE)

_KNOWN_POS_DIRECTIVES = {"NPOS", "TOOL", "POSTYPE", "RECTAN", "PULSE", "RCONF", "USER"}
_KNOWN_INST_DIRECTIVES = {"DATE", "ATTR", "COMM"}
# 1行の中に入っていると行の区切りがあいまいになる文字（タブ以外の制御文字など）
_BAD_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f\x85  ]")


class JbiError(Exception):
    """ジョブを変更できないときのエラー（理由をそのまま利用者に見せられる文にする）。"""


@dataclass(frozen=True)
class Position:
    name: str
    values: tuple[float, ...]
    line_index: int
    postype: str | None = None
    rectan: bool = False
    tool: str | None = None
    user: str | None = None
    rconf: str | None = None
    after_unknown_directive: bool = False

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.values[0], self.values[1], self.values[2])

    def is_robot_coordinates(self) -> bool:
        return self.postype == "ROBOT" and self.rectan and not self.after_unknown_directive


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
    refs: tuple[str, ...] = ()  # 行の中の位置参照（すべて）


@dataclass(frozen=True)
class ChangeProposal:
    """承認画面に出す変更案。new_job は承認されるまで書き出さない。"""
    kind: str  # "shift" または "speed"
    target: str
    line_index: int
    before_line: str
    after_line: str
    requested: float
    before_value: float
    after_value: float  # new_job から読み直した実際の値（丸め後）
    affected: tuple[str, ...]
    warnings: tuple[str, ...]
    new_job: "Job"

    @property
    def actual_delta(self) -> float:
        return self.after_value - self.before_value


@dataclass
class Job:
    lines: list[str]  # 改行コードを含んだままの行
    name: str = ""
    positions: dict[str, Position] = field(default_factory=dict)
    instructions: list[Instruction] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # 1件でもあれば変更はすべて断る
    encoding: str = "utf-8"

    # ---- 書き出し・情報 ----
    def render(self) -> str:
        return "".join(self.lines)

    def render_bytes(self) -> bytes:
        return self.render().encode(self.encoding)

    def is_robot_coordinates(self) -> bool:
        return bool(self.positions) and all(p.is_robot_coordinates() for p in self.positions.values())

    def instruction_at(self, index: int) -> Instruction:
        for ins in self.instructions:
            if ins.index == index:
                return ins
        raise JbiError(f"{index + 1}行目は命令ではありません")

    def users_of(self, position: str) -> list[Instruction]:
        return [i for i in self.instructions if position in i.refs]

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

    # ---- 変更案（新しい Job を中に入れて返す。元の Job は変えない） ----
    def plan_shift(self, position: str, axis: str, delta_mm: float,
                   max_abs_delta_mm: float = DEFAULT_MAX_SHIFT_MM) -> ChangeProposal:
        self._check_no_problems()
        if not _is_real_number(max_abs_delta_mm) or not math.isfinite(max_abs_delta_mm) or max_abs_delta_mm <= 0:
            raise JbiError("移動量の上限の指定が正しくないため、変更しません")
        if not _is_real_number(delta_mm):
            raise JbiError("移動量が数値ではないため、変更しません")
        if not math.isfinite(delta_mm):
            raise JbiError("移動量が有限の数値ではないため、変更しません")
        if delta_mm == 0:
            raise JbiError("移動量が0のため、変更しません")
        if abs(delta_mm) > max_abs_delta_mm:
            raise JbiError(f"移動量 {delta_mm:g}mm は上限 {max_abs_delta_mm:g}mm を超えるため、変更しません")
        if position not in self.positions:
            raise JbiError(f"位置 {position} はこのジョブにありません")
        if axis not in AXES:
            raise JbiError(f"軸 {axis} は扱えません（X・Y・Zのみ）")
        pos = self.positions[position]
        if pos.after_unknown_directive:
            raise JbiError(f"位置 {position} の前に分からない指示行があり、ロボット座標かどうか分からないため、変更しません")
        if not pos.is_robot_coordinates():
            raise JbiError(f"位置 {position} はロボット座標の形式ではないため、変更しません")

        for ins in self.instructions:
            if _BLOCKING.search(ins.text):
                raise JbiError(
                    f"{ins.index + 1}行目「{ins.text}」は位置の意味を変える命令のため、"
                    "このジョブの位置は、このアプリでは変更しません")

        users = self.users_of(position)
        if not users:
            raise JbiError(
                f"位置 {position} はどの命令にも使われていない位置です。どの命令も使っていない位置は、"
                "何のための位置か分からないため、このアプリでは変更しません")
        # 位置を使う行を1つずつ確かめる（1つでも止める対象なら変えない）
        for ins in users:
            _check_editable_move(ins)
        if len(users) >= 2:
            where = "・".join(f"{i.index + 1}行目" for i in users)
            raise JbiError(
                f"位置 {position} は {where} の{len(users)}つの命令が共用しているため、"
                "1か所だけを直すことができず、このアプリでは変更しません")
        target = users[0]
        if target.command != "MOVL" or target.position != position:
            raise JbiError(
                f"{target.index + 1}行目「{target.text}」は位置 {position} を直線移動（MOVL）の行き先として"
                "使っていないため、変更しません")
        # 仕様v2.1 6章：入る命令と出る命令のどちらか一方でも「MOVL 位置 V=数値」でなければ止める
        nxt = self._next_move(target.index)
        if nxt is not None:
            try:
                _check_editable_move(nxt)
            except JbiError as e:
                raise JbiError(f"位置 {position} から出る移動の命令が、このアプリで変更できる形ではありません。{e}") from None

        line = self.lines[pos.line_index]
        body, ending = _split_ending(line)
        m = _POS_NAME_LINE.fullmatch(body)
        if m is None:
            raise JbiError(f"{pos.line_index + 1}行目の位置の行が読めないため、変更しません")
        tokens = m.group(2).split(",")
        k = AXES[axis]
        old_token = tokens[k]
        if not re.fullmatch(_NUM, old_token):
            raise JbiError(f"{pos.line_index + 1}行目の位置の数値が読めないため、変更しません")
        before_value = float(old_token)
        new_token = _format_like(old_token, before_value + delta_mm)
        tokens[k] = new_token
        new_body = f"{m.group(1)}={','.join(tokens)}"
        new_job = self._with_line(pos.line_index, new_body + ending)

        new_pos = new_job.positions.get(position)
        if new_pos is None or len(new_pos.values) != len(pos.values):
            raise JbiError("変更後のジョブを読み直せなかったため、変更しません")
        after_value = new_pos.values[k]
        if after_value == before_value:
            raise JbiError(f"位置 {position} の {axis} は、小数点以下の桁数に丸めると値が変わりませんので、変更しません")
        if any(a != b for j, (a, b) in enumerate(zip(pos.values, new_pos.values)) if j != k):
            raise JbiError("変更後のジョブで別の値が変わってしまうため、変更しません")
        actual = after_value - before_value
        if abs(actual) > max_abs_delta_mm + 1e-9:
            raise JbiError(f"丸めた後の実際の移動量が上限 {max_abs_delta_mm:g}mm を超えるため、変更しません")

        warnings = [PATH_WARNING]
        if pos.tool is not None:
            warnings.append(f"この位置はツール番号 {pos.tool} で書かれています")
        else:
            warnings.append("この位置のツール番号の指定が見つかりません")
        if pos.user is not None:
            warnings.append(f"この位置にはユーザ座標 {pos.user} の指定があります")
        if abs(actual - delta_mm) > 1e-9:
            warnings.append(
                f"指示の {delta_mm:g}mm は小数点以下の桁数に丸めて、実際には {_fmt_delta(old_token, actual)}mm 動かします")
        flow = sorted({mm.group(1).upper() for i in self.instructions for mm in _FLOW.finditer(i.text)})
        if flow:
            warnings.append(
                f"このジョブには {'・'.join(flow)} があるため、前後の移動は行の並びから推定したものです")

        affected = [f"{target.index + 1}行目「{target.text}」（この位置へ入る移動）"]
        if nxt is None:
            affected.append("（この後の移動命令は見つかりません）")
        else:
            note = "この位置から出る移動"
            if _PL_LOOSE.search(nxt.text):
                note += "。PL付き"
            if _CR_LOOSE.search(nxt.text):
                note += "。CR付き"
            affected.append(f"{nxt.index + 1}行目「{nxt.text}」（{note}）")

        return ChangeProposal(
            kind="shift", target=f"{position} {axis}", line_index=pos.line_index,
            before_line=body, after_line=new_body, requested=float(delta_mm),
            before_value=before_value, after_value=after_value,
            affected=tuple(affected), warnings=tuple(warnings), new_job=new_job)

    def plan_speed(self, index: int, new_speed: float) -> ChangeProposal:
        self._check_no_problems()
        ins = self.instruction_at(index)
        if ins.command != "MOVL":
            raise JbiError(f"{index + 1}行目は直線移動（MOVL）ではないため、速度を変更できません")
        if ins.speed is None:
            raise JbiError(f"{index + 1}行目の速度は数値で書かれていないため、変更できません")
        _check_editable_move(ins)
        # 仕様v2.1 6章：前後の移動命令に PL・CR があれば止める（PL=0 も今は止める扱い）
        for nb in (self._prev_move(index), self._next_move(index)):
            if nb is None:
                continue
            what = "PL" if _PL_LOOSE.search(nb.text) else "CR" if _CR_LOOSE.search(nb.text) else None
            if what:
                raise JbiError(
                    f"{index + 1}行目の前後の移動命令 {nb.index + 1}行目「{nb.text}」に {what} が付いているため、"
                    "通り道が変わるので、このアプリでは速度を変更しません")
        if not _is_real_number(new_speed):
            raise JbiError("速度が数値ではないため、変更しません")
        if not math.isfinite(new_speed) or new_speed <= 0:
            raise JbiError("速度は正の有限の数値でなければならないため、変更しません")

        body, ending = _split_ending(self.lines[index])
        m = _SPEED_NUM.search(body)
        if m is None:
            raise JbiError(f"{index + 1}行目の速度が読めないため、変更しません")
        old_token = m.group(1)
        new_token = _format_like(old_token, new_speed)
        new_body = body[: m.start(1)] + new_token + body[m.end(1):]
        new_job = self._with_line(index, new_body + ending)
        new_ins = next((i for i in new_job.instructions if i.index == index), None)
        if new_ins is None or new_ins.speed is None:
            raise JbiError("変更後のジョブを読み直せなかったため、変更しません")
        before_value, after_value = float(old_token), new_ins.speed
        if after_value == before_value:
            raise JbiError(f"{index + 1}行目の速度は、小数点以下の桁数に丸めると値が変わりませんので、変更しません")
        if after_value <= 0:
            raise JbiError("丸めると速度が0になるため、変更しません")

        warnings = [SPEED_UNIT_WARNING]
        if abs(after_value - new_speed) > 1e-9:
            warnings.append(f"指示の {new_speed:g} は小数点以下の桁数に丸めて、実際には {new_token} にします")
        return ChangeProposal(
            kind="speed", target=f"{index + 1}行目", line_index=index,
            before_line=body, after_line=new_body, requested=float(new_speed),
            before_value=before_value, after_value=after_value,
            affected=(f"{index + 1}行目「{ins.text}」",), warnings=tuple(warnings), new_job=new_job)

    # ---- 互換（変更案の new_job を返す） ----
    def shift_position(self, position: str, axis: str, delta_mm: float) -> "Job":
        return self.plan_shift(position, axis, delta_mm).new_job

    def set_speed(self, index: int, new_speed: float) -> "Job":
        return self.plan_speed(index, new_speed).new_job

    def rename(self, new_name: str) -> "Job":
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", new_name):
            raise JbiError("ジョブ名に使えない文字が入っています")
        for i, line in enumerate(self.lines):
            body, ending = _split_ending(line)
            if body.startswith("//NAME "):
                return self._with_line(i, f"//NAME {new_name}{ending}")
        raise JbiError("ジョブ名の行（//NAME）が見つかりません")

    # ---- 内部 ----
    def _check_no_problems(self) -> None:
        if self.problems:
            raise JbiError(
                "このジョブには読めない所があるため、変更しません（" + "／".join(self.problems) + "）")

    def _next_move(self, index: int) -> Instruction | None:
        return next((i for i in self.instructions if i.index > index and _ANY_MOVE.search(i.text)), None)

    def _prev_move(self, index: int) -> Instruction | None:
        return next((i for i in reversed(self.instructions) if i.index < index and _ANY_MOVE.search(i.text)), None)

    def _with_line(self, index: int, new_line: str) -> "Job":
        new_lines = list(self.lines)
        new_lines[index] = new_line
        return parse("".join(new_lines), encoding=self.encoding)


# ---- 読み込み ----
def load(path: str | Path) -> Job:
    return parse_bytes(Path(path).read_bytes())


def parse_bytes(data: bytes) -> Job:
    """ascii → utf-8 → cp932 の順に読む。書き戻して元のバイト列と一致しなければ断る。"""
    for enc in ("ascii", "utf-8", "cp932"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        job = parse(text, encoding=enc)
        try:
            same = job.render_bytes() == data
        except UnicodeEncodeError:
            same = False
        if not same:
            raise JbiError("このファイルは読み込んで書き戻すと中身が変わってしまうため、扱いません")
        return job
    raise JbiError("このファイルの文字コードが読めないため、扱いません")


def parse(text: str, encoding: str = "utf-8") -> Job:
    lines = _split_lines(text)
    job = Job(lines=lines, encoding=encoding)
    section = None
    seen_headers: set[str] = set()
    # POS部の指示行の状態（後ろの位置に効く）
    postype = None
    rectan = False
    tool = user = rconf = None
    unknown = False
    npos_count = None
    c_lines = 0

    def problem(i: int, body: str, why: str) -> None:
        job.problems.append(f"{i + 1}行目「{body}」: {why}")

    for i, line in enumerate(lines):
        body, _ = _split_ending(line)
        if _BAD_CHARS.search(body):
            problem(i, body.encode("unicode_escape").decode("ascii"), "行の中に読めない文字があります")
            continue
        stripped = body.strip()

        # 見出し（//NAME・//POS・//INST だけを知っている）
        if body.startswith("//") and not body.startswith("///"):
            if body.startswith("//NAME ") and "NAME" not in seen_headers:
                job.name = body[len("//NAME "):].strip()
                seen_headers.add("NAME")
                section = None
            elif body in ("//POS", "//INST") and body[2:] not in seen_headers:
                section = body[2:]
                seen_headers.add(section)
            else:
                problem(i, body, "知らない見出し、または重複した見出しです")
                section = "UNKNOWN"
            continue

        if section is None:
            if i == 0 and body == "/JOB":
                continue
            problem(i, body, "読めない行です")
            continue

        if section == "UNKNOWN":
            continue

        if section == "POS":
            if body.startswith("///"):
                word, _, arg = body[3:].partition(" ")
                arg = arg.strip()
                if word not in _KNOWN_POS_DIRECTIVES:
                    unknown = True  # 以降の位置は変更しない
                elif word == "NPOS":
                    first = arg.split(",")[0]
                    if npos_count is not None or not re.fullmatch(r"\d+", first):
                        problem(i, body, "NPOS が読めません")
                    else:
                        npos_count = int(first)
                elif word == "TOOL":
                    tool = arg or None
                elif word == "POSTYPE":
                    postype = arg or None
                    rectan = False
                elif word == "RECTAN":
                    rectan = True
                elif word == "PULSE":
                    rectan = False
                elif word == "RCONF":
                    rconf = arg or None
                elif word == "USER":
                    user = arg or None
                continue
            m = _POS_NAME_LINE.fullmatch(body)
            if not m:
                problem(i, body, "読めない行です")
                continue
            c_lines += 1
            name = m.group(1)
            raw = m.group(2).split(",")
            if not all(re.fullmatch(_NUM, v) for v in raw):
                problem(i, body, "数値でない値があります")
                continue
            if len(raw) < 3:
                problem(i, body, "値が3個未満です")
                continue
            if name in job.positions:
                problem(i, body, f"位置 {name} が重複しています")
                continue
            job.positions[name] = Position(
                name, tuple(float(v) for v in raw), i, postype=postype, rectan=rectan,
                tool=tool, user=user, rconf=rconf, after_unknown_directive=unknown)
            continue

        # INST部
        if body.startswith("///"):
            word = body[3:].split(" ", 1)[0]
            if word.startswith("GROUP"):
                if body != "///GROUP1 RB1":
                    problem(i, body, "ロボット1台（GROUP1 RB1）以外の軸グループがあります")
            elif word not in _KNOWN_INST_DIRECTIVES:
                problem(i, body, "知らない指示行です")
            continue
        if not stripped or stripped.startswith("'"):
            continue  # 空行とコメント行は命令として扱わない
        job.instructions.append(_parse_instruction(i, stripped))

    if "POS" in seen_headers:
        if npos_count is None:
            job.problems.append("POS部に NPOS の行がありません")
        elif npos_count != c_lines:
            job.problems.append(f"NPOS の位置の数（{npos_count}）と位置の行の数（{c_lines}）が合いません")
    return job


def _parse_instruction(index: int, text: str) -> Instruction:
    refs = tuple(r.upper() for r in _REF.findall(text))
    m = _MOVE.match(text)
    if not m:
        return Instruction(index=index, text=text, command=text.split()[0], refs=refs)
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
        refs=refs,
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
        m = _PL.search(ins.text)
        what = f"PL={m.group(1)}" if m else "PL（書き方を読み取れない形）"
        raise JbiError(f"{where}は {what} が付いているため、このアプリでは変更しません")
    if _CR_LOOSE.search(ins.text):
        raise JbiError(f"{where}は CR（CR=）が付いているため、このアプリでは変更しません")
    if not _EDITABLE_MOVL.fullmatch(ins.text):
        raise JbiError(f"{where}は、このアプリで扱える形（MOVL 位置 V=数値）ではないため、変更しません")


def _is_real_number(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _split_lines(text: str) -> list[str]:
    """\\n だけで行を分ける（str.splitlines は \\x0c などでも分けてしまうため使わない）。"""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _decimals(original: str) -> int:
    original = original.strip()
    return len(original.split(".", 1)[1]) if "." in original else 0


def _format_like(original: str, value: float) -> str:
    """元の数値と同じ小数点以下の桁数で書く。"""
    text = f"{value:.{_decimals(original)}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # -0.000 を 0.000 にする
    return text


def _fmt_delta(original: str, delta: float) -> str:
    return _format_like(original, delta)
