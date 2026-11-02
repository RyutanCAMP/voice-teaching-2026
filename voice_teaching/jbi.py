"""安川ロボットのジョブ（JBI）の読み込み・変更・書き出し。

方針:
- 分からない行は一文字も変えずに残す（改行コード・文字コードもそのまま）。
- 変えるのは、検査を通った位置の数値1つ、ジョブ名だけ（速度の変更は、確認が済むまで止めている）。
- 変更は元のオブジェクトを書き換えず、変更案（ChangeProposal）の中に新しい Job を入れて返す（元のジョブは必ず残る）。
- 分からない書き方は「無い」とみなさず、変更を断る（理由を文で返す）。読むこと（表示）は最後まで続ける。
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

# 位置の変更量の上限（mm）。仕様v2.1 では「1回の変更上限 20mm」（最終の値は本人が実機を見て決める。安全の保証ではない）。
# 【暫定】元のジョブ（基準）からの累積にも同じ値を使っている。仕様では累積は専門家の枠（FRAME）で基準位置から判定する。
# 枠ができるまでの止める側の暫定措置で、FRAME を実装するときに枠の判定と整合させる（docs/JBI_FORMAT_NOTES.md）
DEFAULT_MAX_SHIFT_MM = 20.0

# 速度の変更は、単位・指定形式・適用範囲の確認が済むまで止める（本人の判断）。検査の中身は将来のために残す
SPEED_CHANGE_ENABLED = False
SPEED_DISABLED_MESSAGE = "速度の変更は、単位・指定形式・適用範囲の確認が済むまで、このアプリでは止めています"

PATH_WARNING = (
    "この位置を変えると、この位置へ入る移動と、この位置から出る移動の経路が変わります。"
    "検査を通ったことは、物理的な安全の保証ではありません。"
)
SPEED_UNIT_WARNING = (
    "V= の単位は、ロボットの表示単位の設定で変わることがあります（確認待ち）。"
    "速度を変えると、この移動の動き方と時間が変わります。"
)

_NUM = r"[+-]?[0-9]+(?:\.[0-9]+)?"
_POS_NAME_LINE = re.compile(r"^(C[0-9]{5})=(.*)$")
_MOVE = re.compile(r"^(MOVL|MOVJ|MOVC|MOVS)\s+(C[0-9]{5})\b(.*)$")
# 行の中の位置参照。BC00004 のような別の変数は数えない。大文字小文字は区別しない
_REF = re.compile(r"(?<![A-Z0-9#])C[0-9]{5}\b", re.IGNORECASE)
_SPEED_NUM = re.compile(r"(?<![A-Z])V=([0-9]+(?:\.[0-9]+)?)(?![\w.])")
_SPEED_ANY = re.compile(r"(?<![A-Z])V=(\S+)")
_PL = re.compile(r"\bPL=([0-9]+)(?![\w.])")
_CR = re.compile(r"\bCR=")
# 変更を断るための広い検出（書き方の揺れも拾う）。読み取れない書き方を「無い」とみなさない
_PL_LOOSE = re.compile(r"\bPL\b", re.IGNORECASE)
_CR_LOOSE = re.compile(r"\bCR\b", re.IGNORECASE)
_SECOND_MOVE = re.compile(r"\+\s*\w*MOV", re.IGNORECASE)
# 変更してよい行の形（許可リスト）。「MOVL 位置 V=数値」と、ちょうど「 PL=0」が付いた形だけを通す
# （PL=0 は本人の指示で正本＝仕様v2.1 6・7章に従う）。これ以外の語が付いた行は止める
_EDITABLE_MOVL = re.compile(r"MOVL C[0-9]{5} V=[0-9]+(?:\.[0-9]+)?(?: PL=0)?")
# 前後の命令の PL を見るときに通す形：空白で区切られた、ちょうど「PL=0」1つだけ
_EXACT_PL0 = re.compile(r"(?<= )PL=0(?= |$)")
# 位置の意味を変える命令。ジョブに1つでもあれば位置は変えない
_BLOCKING = re.compile(r"(?<![A-Z0-9_])(SFTON|SFTOF|MSHIFT|MFRAME|IMOV|SMOV[LJCS])(?![A-Z0-9_])", re.IGNORECASE)
# 移動命令（入る・出る移動を探すため。止める側に広めに拾う）
_ANY_MOVE = re.compile(r"(?<![A-Z0-9_])(MOV[LJCS]|SMOV[LJCS]|IMOV)(?![A-Z0-9_])", re.IGNORECASE)
# 制御の流れを変える命令。解析する機能がまだ無いので、1つでもあれば位置は変えない
_FLOW = re.compile(r"(?<![A-Z0-9_])(JUMP|CALL|RET)(?![A-Z0-9_])", re.IGNORECASE)

_KNOWN_INST_DIRECTIVES = {"DATE", "ATTR", "COMM"}
# INST部の命令の許可リスト（仮。今のコード・ダミー・仕様に出てくるものだけ。実機サンプルで本人と確定する）
KNOWN_INST_COMMANDS = frozenset({
    "NOP", "END", "MOVL", "MOVJ", "MOVC", "MOVS", "DOUT", "TIMER", "JUMP", "CALL", "RET",
    # 止めるために知っている命令（ジョブにあれば位置の変更を止める。_BLOCKING）
    "SFTON", "SFTOF", "MSHIFT", "MFRAME", "IMOV", "SMOVL", "SMOVJ", "SMOVC", "SMOVS",
    # GETS は止める命令ではない。位置を参照する命令として「使う行」に数えられ、共用・MOVL でない で止まる
    "GETS",
})
# 引数の形まで決まっている命令は、行全体がこの形のときだけ認める（先頭の語だけでは決めない。END X・NOP X などを止める）
_EXACT_INST_FORMS = {
    "NOP": re.compile(r"NOP"),
    "END": re.compile(r"END"),
    "TIMER": re.compile(r"TIMER T=[0-9]+(?:\.[0-9]+)?"),
    "DOUT": re.compile(r"DOUT OT#\([0-9]+\) (?:ON|OFF)"),
}
# 命令・位置の行に入ってよい文字（ASCII の表示できる文字と空白）。全角の数字などは数字として扱わず止める
_ASCII_LINE = re.compile(r"[ -~\t]*")
_LABEL_LINE = re.compile(r"\*[A-Za-z0-9_]+")
_DIGIT_LIST = r"[0-9]+(?:,[0-9]+)*"
# POS部の指示行と、その引数の形（None は引数なし）。形が違えば「分からない指示行」とする
_POS_DIRECTIVE_ARGS = {
    "NPOS": _DIGIT_LIST, "TOOL": r"[0-9]+", "POSTYPE": r"[A-Z]+", "RECTAN": None, "PULSE": None,
    "RCONF": _DIGIT_LIST, "USER": r"[0-9]+",
}
# 1行の中に入っていると行の区切りがあいまいになる文字（タブ以外の制御文字など）
_BAD_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f\x85  ]")
_ROBOT_VALUES = 6  # HC20 は6軸。ロボット座標の位置は値が6個のときだけ変える
# 位置の値の整数部の桁の上限（このアプリで扱える桁。ロボットの動作範囲を決めるものではない）
_MAX_INT_DIGITS = 6


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
    command: str  # MOVL など。移動命令以外は先頭の語（ラベル行は「*L1」など）
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
    baseline_value: float | None = None  # 位置の変更のとき、元のジョブ（基準）の値

    @property
    def actual_delta(self) -> float:
        return self.after_value - self.before_value

    @property
    def cumulative_delta(self) -> float | None:
        return None if self.baseline_value is None else self.after_value - self.baseline_value


@dataclass
class Job:
    lines: list[str]  # 改行コードを含んだままの行
    name: str = ""
    positions: dict[str, Position] = field(default_factory=dict)
    instructions: list[Instruction] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # 1件でもあれば変更はすべて断る
    encoding: str = "utf-8"
    # 累積の検査の基準：最初に読み込んだ元のジョブの位置の値。変更後の Job に引き継ぐ
    baseline: dict[str, tuple[float, ...]] = field(default_factory=dict)

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
        limit = _as_float(max_abs_delta_mm, "移動量の上限の指定")
        if limit <= 0:
            raise JbiError("移動量の上限の指定が正しくないため、変更しません")
        limit = min(limit, DEFAULT_MAX_SHIFT_MM)  # 引数は上限を下げる方向にだけ効く
        delta = _as_float(delta_mm, "移動量")
        if delta == 0:
            raise JbiError("移動量が0のため、変更しません")
        if abs(delta) > limit:
            raise JbiError(f"移動量 {delta:g}mm は仮の上限 {limit:g}mm（確認待ち）を超えるため、変更しません")
        if position not in self.positions:
            raise JbiError(f"位置 {position} はこのジョブにありません")
        if axis not in AXES:
            raise JbiError(f"軸 {axis} は扱えません（X・Y・Zのみ）")
        pos = self.positions[position]
        if pos.after_unknown_directive:
            raise JbiError(f"位置 {position} の前に分からない指示行があり、ロボット座標かどうか分からないため、変更しません")
        if not pos.is_robot_coordinates():
            raise JbiError(f"位置 {position} はロボット座標の形式ではないため、変更しません")
        if len(pos.values) != _ROBOT_VALUES:
            raise JbiError(
                f"位置 {position} の値が{len(pos.values)}個で、6個（6軸のロボット座標）ではないため、変更しません")

        for ins in self.instructions:
            if _BLOCKING.search(ins.text):
                raise JbiError(
                    f"{ins.index + 1}行目「{ins.text}」は位置の意味を変える命令のため、"
                    "このジョブの位置は、このアプリでは変更しません")
        for ins in self.instructions:
            if _FLOW.search(ins.text) or _LABEL_LINE.match(ins.text):
                raise JbiError(
                    f"{ins.index + 1}行目「{ins.text}」があります。制御の流れ（JUMP・CALL など）を解析する機能が"
                    "まだ無いため、この位置へ入る移動・出る移動を確かめられず、このジョブの位置は変更しません")

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
        # 入る区間の始まり（直前の移動命令）と出る命令（次の移動命令）は、どちらも
        # 「MOVL 位置 V=数値」（ちょうど PL=0 は可）でなければ止める（仕様v2.1 6章。MOVJ を始まりに認めるかは本人判断待ち）
        # 前後の移動命令が無い位置は、入る／出る区間がジョブの中で決まらないので止める
        prev = self._prev_move(target.index)
        if prev is None:
            raise JbiError(
                f"位置 {position} へ入る区間の始まり（直前の移動命令）がジョブの中に無く、入る移動が決まらないため、変更しません")
        try:
            _check_editable_move(prev)
        except JbiError as e:
            raise JbiError(f"位置 {position} へ入る移動の始まりの命令が、このアプリで変更できる形ではありません。{e}") from None
        nxt = self._next_move(target.index)
        if nxt is None:
            raise JbiError(
                f"位置 {position} から出る移動の命令（次の移動命令）がジョブの中に無く、出る移動が決まらないため、変更しません")
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
        new_token = _format_like(old_token, before_value + delta)
        tokens[k] = new_token
        new_body = f"{m.group(1)}={','.join(tokens)}"
        new_job = self._with_line(pos.line_index, new_body + ending)

        new_pos = new_job.positions.get(position)
        if new_job.problems or new_pos is None or len(new_pos.values) != len(pos.values):
            raise JbiError("変更後のジョブを読み直せなかったため、変更しません")
        after_value = new_pos.values[k]
        if after_value == before_value:
            raise JbiError(f"位置 {position} の {axis} は、小数点以下の桁数に丸めると値が変わりませんので、変更しません")
        if any(a != b for j, (a, b) in enumerate(zip(pos.values, new_pos.values)) if j != k):
            raise JbiError("変更後のジョブで別の値が変わってしまうため、変更しません")
        actual = after_value - before_value
        if abs(actual) > limit + 1e-9:
            raise JbiError(f"丸めた後の実際の移動量が仮の上限 {limit:g}mm（確認待ち）を超えるため、変更しません")
        # 仕様v2.1 6章：元のジョブ（基準）からの累積で軸ごとに検査する
        # 【暫定】今は1回の上限と同じ値で止める。FRAME（専門家の枠）を実装するときに枠の判定と整合させる
        base_values = self.baseline.get(position)
        if base_values is None or len(base_values) != len(pos.values):
            raise JbiError(f"位置 {position} の基準（元のジョブの値）が分からないため、変更しません")
        baseline_value = base_values[k]
        cumulative = after_value - baseline_value
        if abs(cumulative) > DEFAULT_MAX_SHIFT_MM + 1e-9:
            raise JbiError(
                f"位置 {position} の {axis} は、元のジョブ（基準）からの累積が {_format_like(old_token, cumulative)}mm に"
                f"なり、累積の仮の上限 {DEFAULT_MAX_SHIFT_MM:g}mm（確認待ち）を超えるため、変更しません")

        warnings = [PATH_WARNING]
        if pos.tool is not None:
            warnings.append(f"この位置はツール番号 {pos.tool} で書かれています")
        else:
            warnings.append("この位置のツール番号の指定が見つかりません")
        if pos.user is not None:
            warnings.append(f"この位置にはユーザ座標 {pos.user} の指定があります")
        if abs(actual - delta) > 1e-9:
            warnings.append(
                f"指示の {delta:g}mm は小数点以下の桁数に丸めて、実際には {_format_like(old_token, actual)}mm 動かします")
        if baseline_value != before_value:
            warnings.append(
                f"元のジョブ（基準）からの {axis} の累積の変更量は {_format_like(old_token, cumulative)}mm です")

        note = "この位置から出る移動" + ("。PL=0 付き" if _PL_LOOSE.search(nxt.text) else "")
        affected = [
            f"{prev.index + 1}行目「{prev.text}」（この位置へ入る区間の始まり）",
            f"{target.index + 1}行目「{target.text}」（この位置へ入る移動）",
            f"{nxt.index + 1}行目「{nxt.text}」（{note}）",
        ]

        return ChangeProposal(
            kind="shift", target=f"{position} {axis}", line_index=pos.line_index,
            before_line=body, after_line=new_body, requested=delta,
            before_value=before_value, after_value=after_value,
            affected=tuple(affected), warnings=tuple(warnings), new_job=new_job,
            baseline_value=baseline_value)

    def plan_speed(self, index: int, new_speed: float) -> ChangeProposal:
        if not SPEED_CHANGE_ENABLED:
            raise JbiError(SPEED_DISABLED_MESSAGE)
        self._check_no_problems()
        ins = self.instruction_at(index)
        if ins.command != "MOVL":
            raise JbiError(f"{index + 1}行目は直線移動（MOVL）ではないため、速度を変更できません")
        if ins.speed is None:
            raise JbiError(f"{index + 1}行目の速度は数値で書かれていないため、変更できません")
        _check_editable_move(ins)
        # 仕様v2.1 6章：前後の移動命令に PL・CR があれば止める（ちょうど PL=0 は通す）
        for nb in (self._prev_move(index), self._next_move(index)):
            if nb is None:
                continue
            what = _pl_or_cr(nb.text)
            if what:
                raise JbiError(
                    f"{index + 1}行目の前後の移動命令 {nb.index + 1}行目「{nb.text}」に {what} が付いていて、"
                    "通り道が変わるため、このアプリでは速度を変更しません")
        speed = _as_float(new_speed, "速度")
        if speed <= 0:
            raise JbiError("速度は正の数値でなければならないため、変更しません")

        body, ending = _split_ending(self.lines[index])
        m = _SPEED_NUM.search(body)
        if m is None:
            raise JbiError(f"{index + 1}行目の速度が読めないため、変更しません")
        old_token = m.group(1)
        new_token = _format_like(old_token, speed)
        # 速度の上限の値は本人の判断待ち。書式が壊れるもの（整数部の桁が元より多い）だけを止める
        if _int_digits(new_token) > _int_digits(old_token):
            raise JbiError(
                f"新しい速度は、元の値 {old_token} より整数部の桁が多く、このアプリで扱える桁を超えるため、変更しません")
        new_body = body[: m.start(1)] + new_token + body[m.end(1):]
        new_job = self._with_line(index, new_body + ending)
        new_ins = next((i for i in new_job.instructions if i.index == index), None)
        if new_job.problems or new_ins is None or new_ins.speed is None:
            raise JbiError("変更後のジョブを読み直せなかったため、変更しません")
        before_value, after_value = float(old_token), new_ins.speed
        if after_value == before_value:
            raise JbiError(f"{index + 1}行目の速度は、小数点以下の桁数に丸めると値が変わりませんので、変更しません")
        if after_value <= 0:
            raise JbiError("丸めると速度が0になるため、変更しません")

        warnings = [SPEED_UNIT_WARNING]
        if abs(after_value - speed) > 1e-9:
            warnings.append(f"指示の {speed:g} は小数点以下の桁数に丸めて、実際には {new_token} にします")
        return ChangeProposal(
            kind="speed", target=f"{index + 1}行目", line_index=index,
            before_line=body, after_line=new_body, requested=speed,
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
        """1行だけ差し替えた新しい Job。累積の基準は引き継ぐ。"""
        new_lines = list(self.lines)
        new_lines[index] = new_line
        new_job = parse("".join(new_lines), encoding=self.encoding)
        new_job.baseline = dict(self.baseline)
        return new_job


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
    """ジョブを読む。分からない所は problems に記録し、読むこと自体は最後まで続ける（表示のため）。

    読み直したジョブは、累積の検査の基準が新しくなる（ファイルをまたいだ引き継ぎは別の設計で扱う）。
    """
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
    ended = False

    def problem(i: int, body: str, why: str) -> None:
        job.problems.append(f"{i + 1}行目「{body}」は{why}")

    for i, line in enumerate(lines):
        body, _ = _split_ending(line)
        if _BAD_CHARS.search(body):
            problem(i, body.encode("unicode_escape").decode("ascii"), "行の中に読めない文字があります")
            continue
        if body != body.strip(" \t"):
            problem(i, body, "前後に空白があります")
            body = body.strip(" \t")
        if not body:
            problem(i, body, "空の行です")
            continue
        # 全角空白・NBSP などの空白文字は、コメント（' の行・///COMM）以外では止める。読むのは続ける
        if not (body.startswith("'") or body.startswith("///COMM ")) and any(
                c.isspace() and c not in " \t" for c in body):
            problem(i, body, "全角空白などの空白文字を含みます")

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
                word, sep, arg = body[3:].partition(" ")
                if word not in _POS_DIRECTIVE_ARGS:
                    unknown = True  # 以降の位置は変更しない
                    continue
                pat = _POS_DIRECTIVE_ARGS[word]
                ok = (sep == "") if pat is None else (sep == " " and re.fullmatch(pat, arg) is not None)
                if word == "NPOS":
                    if not ok or npos_count is not None:
                        problem(i, body, "NPOS が読めません")
                    else:
                        npos_count = int(arg.split(",")[0])
                elif not ok:
                    unknown = True  # 想定外の引数が付いた指示行は「分からない指示行」
                elif word == "TOOL":
                    tool = arg
                elif word == "POSTYPE":
                    postype = arg
                    rectan = False
                elif word == "RECTAN":
                    rectan = True
                elif word == "PULSE":
                    rectan = False
                elif word == "RCONF":
                    rconf = arg
                elif word == "USER":
                    user = arg
                continue
            m = _POS_NAME_LINE.fullmatch(body)
            if not m:
                problem(i, body, "読めない行です")
                continue
            c_lines += 1
            if not _ASCII_LINE.fullmatch(body):
                problem(i, body, "ASCII 以外の文字（全角の数字など）を含みます")
                continue
            name = m.group(1)
            raw = m.group(2).split(",")
            if not all(re.fullmatch(_NUM, v) for v in raw):
                problem(i, body, "数値でない値があります")
                continue
            if any(_int_digits(v) > _MAX_INT_DIGITS for v in raw):
                problem(i, body, "整数部が7桁以上の値があり、このアプリで扱える桁を超えます")
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
            word, sep, arg = body[3:].partition(" ")
            if word.startswith("GROUP"):
                if body != "///GROUP1 RB1":
                    problem(i, body, "ロボット1台（GROUP1 RB1）以外の軸グループです")
            elif word == "LVARS":
                if not (sep == " " and re.fullmatch(_DIGIT_LIST, arg)):
                    problem(i, body, "数字をカンマで区切った形ではない LVARS です")
            elif word not in _KNOWN_INST_DIRECTIVES:
                problem(i, body, "知らない指示行です")
            continue
        if body.startswith("'"):
            continue  # コメント行は命令として扱わない
        words = body.split()
        if not words:
            continue  # 空白文字だけの行（problems には上で記録済み）
        if not _ASCII_LINE.fullmatch(body):
            problem(i, body, "ASCII 以外の文字（全角の数字など）を含みます")
        # 命令を「認識できる」かの判定（許可リスト）。知らない命令も読み続け、problems に記録する
        if not (_LABEL_LINE.fullmatch(body) or words[0] in KNOWN_INST_COMMANDS):
            problem(i, body, "このアプリの命令の許可リストに無い命令です")
        elif words[0] in _EXACT_INST_FORMS and not _EXACT_INST_FORMS[words[0]].fullmatch(body):
            problem(i, body, f"{words[0]} の書き方が、このアプリで扱える形ではありません")
        # END より後ろの命令は実行されない。その命令だけが使う位置などを取り違えないよう、ジョブごと止める
        if ended:
            problem(i, body, "END より後ろにある命令です（実行されません）")
        if words[0] == "END":
            ended = True  # 引数の付いた END（END X）も、ここで終わりとみなして後ろを止める
        job.instructions.append(_parse_instruction(i, body))

    if "POS" in seen_headers:
        if npos_count is None:
            job.problems.append("POS部に NPOS の行がありません")
        elif npos_count != c_lines:
            job.problems.append(f"NPOS の位置の数（{npos_count}）と位置の行の数（{c_lines}）が合いません")
    if not ended:
        # END の無いジョブは、途中で切れたファイルかもしれない。命令の終わりが確かめられないので止める
        job.problems.append("END の命令がありません（ファイルが途中で切れているかもしれません）")
    job.baseline = {name: p.values for name, p in job.positions.items()}
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

    「MOVL 位置 V=数値」と、ちょうど「 PL=0」が付いた形だけを通す（許可リスト）。
    PL（PL=0 以外や緩い書き方）・CR・同時移動・知らない語は、理由を分けて返す。
    """
    where = f"{ins.index + 1}行目「{ins.text}」"
    if ins.command != "MOVL":
        raise JbiError(f"{where}は直線移動（MOVL）の命令ではないため、このアプリでは変更しません")
    if _EDITABLE_MOVL.fullmatch(ins.text):
        return
    if _SECOND_MOVE.search(ins.text):
        raise JbiError(f"{where}は別の移動と組になっているため、このアプリでは変更しません")
    if _CR_LOOSE.search(ins.text):
        raise JbiError(f"{where}は CR（CR=）が付いているため、このアプリでは変更しません")
    if _PL_LOOSE.search(ins.text):
        m = _PL.search(ins.text)
        if m is None:
            raise JbiError(f"{where}は PL（書き方を読み取れない形）が付いているため、このアプリでは変更しません")
        if m.group(1) != "0":
            raise JbiError(f"{where}は PL={m.group(1)} が付いているため、このアプリでは変更しません")
    raise JbiError(f"{where}は、このアプリで扱える形（MOVL 位置 V=数値、または末尾に PL=0）ではないため、変更しません")


def _pl_or_cr(text: str) -> str | None:
    """前後の命令に付いた PL・CR を調べる。ちょうど「PL=0」1つだけなら通す。"""
    if _CR_LOOSE.search(text):
        return "CR"
    n = len(_PL_LOOSE.findall(text))
    if n == 0 or (n == 1 and _EXACT_PL0.search(text)):
        return None
    return "PL"


def _as_float(x: object, what: str) -> float:
    """移動量・速度・上限の引数を数値として受け取る。極端な値は例外で落とさず JbiError で断る。"""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise JbiError(f"{what}が数値ではないため、変更しません")
    try:
        f = float(x)
    except (OverflowError, ValueError):
        raise JbiError(f"{what}が大きすぎて扱えないため、変更しません") from None
    if not math.isfinite(f):
        raise JbiError(f"{what}が有限の数値ではないため、変更しません")
    return f


def _split_lines(text: str) -> list[str]:
    """\\n だけで行を分ける（str.splitlines は \\x0c などでも分けてしまうため使わない）。"""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _int_digits(token: str) -> int:
    return len(token.lstrip("+-").split(".", 1)[0])


def _format_like(original: str, value: float) -> str:
    """元の数値と同じ小数点以下の桁数で書く。"""
    original = original.strip()
    decimals = len(original.split(".", 1)[1]) if "." in original else 0
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # -0.000 を 0.000 にする
    return text
