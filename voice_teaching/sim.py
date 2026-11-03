"""シミュレーションモード：合成ジョブで「変更候補 → プレビュー → ボタン承認 → 出力」を一通り動かす。

方針:
- まず実機用の検査（jbi の plan_shift）を呼ぶ。通ればその結果をそのまま使う。
- 実機用の検査で止まったときは、止まった理由（blocked_reason）を付けたうえで、
  最小限の条件を満たす場合だけ、シミュレーション用の候補を作る。満たさなければ SimError。
- 変えるのは、その位置の行の、その軸の数値1つだけ（他のバイトは変えない。改行コード・文字コードも保つ）。
- 1回の移動量は、シミュレーションでも仮の上限（DEFAULT_MAX_SHIFT_MM。確認待ち）を守る。
- 出力は合成ジョブ（samples/dummy/*.JBI とバイト単位で一致するもの）だけ。実機用のジョブは出力しない。
- 承認はボタンから呼ぶ前提。このモジュールは音声や文字の指示を解析しない。
- 実機の制御・転送は扱わない。検査を通ったことは、物理的な安全の保証ではない。

jbi の公開の関数・クラスだけを使う（_ で始まるものには頼らない）。
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import jbi

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples" / "dummy"
# git に入らない場所（.gitignore の output/）
# シミュレーション用の出力先（固定）。呼び出し側からは選べない（USB など任意の場所へ直接は書かない。CHECKER_019 R6）
DEFAULT_OUT_DIR = REPO_ROOT / "output" / "sim"
SIM_SUFFIX = "_SIM"
SIM_EXTENSION = ".JBI.txt"
# 出力の拡張子。.JBI のままにしないことで、取り違えて実機に入れることを減らす（R6）。
# 拡張子・出力先・ジョブ名の _SIM は誤使用を減らすためのもので、完全に防ぐものではない。JBI の中身に警告のコメントは足さない

SIM_NOTICE = (
    "シミュレーション用の候補です。実機用のジョブとしては出力しません。"
    "変更後の経路は、人が確かめてください。"
)

_NUM = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_POS_LINE = re.compile(r"^(C[0-9]{5})=(.*)$")
_LINES = re.compile(rb"[^\n]*\n|[^\n]+\Z")
_NAME_OK = re.compile(r"[A-Za-z0-9_\-]+")


class SimError(Exception):
    """シミュレーションでも候補を作れない・出力できないときのエラー（理由をそのまま見せられる文にする）。"""


@dataclass(frozen=True)
class SimProposal:
    """シミュレーションの変更候補。承認されるまで出力しない。

    line_index と users の行番号は 0 始まり（画面では +1 して見せる）。
    """
    mode: str  # 常に "sim"
    position: str
    axis: str
    requested: float
    before_line: str
    after_line: str
    before_value: float
    after_value: float  # new_bytes を読み直した値
    line_index: int
    blocked_reason: str | None  # 実機用の検査で止まった理由。通ったときは None
    warnings: tuple[str, ...]
    users: tuple[tuple[int, str], ...]  # この位置を使う命令（行番号, 文字）
    original_bytes: bytes
    new_bytes: bytes

    @property
    def actual_delta(self) -> float:
        return self.after_value - self.before_value


@dataclass(frozen=True)
class SimRepeatProposal:
    """回数の変更のシミュレーションの候補（docs/REPEAT_COUNT_DESIGN.md）。承認されるまで出力しない。

    実機用の検査（jbi.plan_repeat）を通ったものだけ。止まったものは参考の候補も作らない（blocked_reason は常に None）。
    first_line は足した（消した）最初の行。足すときは new_bytes の、消すときは original_bytes の行番号（0 から）。
    """
    mode: str  # 常に "sim"
    start: str
    end: str
    before_count: int
    after_count: int
    first_line: int
    cycle_lines: tuple[bytes, ...]  # 足す・消す1回分の行（改行コードを含むバイト列）
    warnings: tuple[str, ...]
    original_bytes: bytes
    new_bytes: bytes
    blocked_reason: None = None


@dataclass(frozen=True)
class Approval:
    """ボタンでの承認の記録。承認したときのバイト列の指紋を持つ。"""
    original_sha256: str
    new_sha256: str
    approved_by: str
    approved_at: str  # ISO 8601（UTC）
    # 上下の向きの設定（CHECKER_026 M2）：承認したときの照合の状態、使った設定の指紋、確認した人（確認済みのときだけ）
    updown_status: str | None = None
    updown_fingerprint: str | None = None
    updown_confirmed_by: str | None = None


# ---- 1. 変更候補 ----
def simulate_shift(job: jbi.Job, position: str, axis: str, delta_mm: float) -> SimProposal:
    original = job.render_bytes()
    users = tuple((ins.index, ins.text) for ins in job.users_of(position))
    try:
        cp = job.plan_shift(position, axis, delta_mm)
    except jbi.JbiError as e:
        return _simulate_blocked(job, original, position, axis, delta_mm, str(e), users)

    new = cp.new_job.render_bytes()
    _check_one_line_changed(original, new, cp.line_index)
    return SimProposal(
        mode="sim", position=position, axis=axis, requested=float(delta_mm),
        before_line=cp.before_line, after_line=cp.after_line,
        before_value=cp.before_value, after_value=cp.after_value,
        line_index=cp.line_index, blocked_reason=None,
        warnings=tuple(cp.warnings), users=users,
        original_bytes=original, new_bytes=new)


def _simulate_blocked(job, original, position, axis, delta_mm, reason, users) -> SimProposal:
    limit = jbi.DEFAULT_MAX_SHIFT_MM
    if position not in job.positions:
        raise SimError(f"位置 {position} はこのジョブにないため、候補を作りません")
    if axis not in jbi.AXES:
        raise SimError(f"軸 {axis} は扱えません（X・Y・Zのみ）")
    pos = job.positions[position]
    if not pos.is_robot_coordinates():
        raise SimError(f"位置 {position} はロボット座標の形式ではないため、候補を作りません")
    if not isinstance(delta_mm, (int, float)) or isinstance(delta_mm, bool):
        raise SimError("移動量が数値ではないため、候補を作りません")
    if not math.isfinite(delta_mm):
        raise SimError("移動量が有限の数値ではないため、候補を作りません")
    if delta_mm == 0:
        raise SimError("移動量が0のため、候補を作りません")
    if abs(delta_mm) > limit:
        raise SimError(
            f"移動量 {delta_mm:g}mm は1回の仮の上限（確認待ち）{limit:g}mm を超えるため、候補を作りません")

    k = jbi.AXES[axis]
    lines = list(job.lines)
    body, ending = _split_ending(lines[pos.line_index])
    m = _POS_LINE.fullmatch(body)
    if m is None:
        raise SimError(f"{pos.line_index + 1}行目の位置の行が読めないため、候補を作りません")
    tokens = m.group(2).split(",")
    if len(tokens) <= k or not all(_NUM.fullmatch(t) for t in tokens):
        raise SimError(f"{pos.line_index + 1}行目の位置の数値が読めないため、候補を作りません")
    old_token = tokens[k]
    before_value = float(old_token)
    tokens[k] = _format_like(old_token, before_value + delta_mm)
    new_body = f"{m.group(1)}={','.join(tokens)}"
    lines[pos.line_index] = new_body + ending
    try:
        new = "".join(lines).encode(job.encoding)
        new_job = jbi.parse_bytes(new)
    except (UnicodeEncodeError, jbi.JbiError):
        raise SimError("変更後のジョブを読み直せなかったため、候補を作りません") from None
    _check_one_line_changed(original, new, pos.line_index)

    new_pos = new_job.positions.get(position)
    if new_pos is None or len(new_pos.values) != len(pos.values):
        raise SimError("変更後のジョブを読み直せなかったため、候補を作りません")
    after_value = new_pos.values[k]
    if after_value == before_value:
        raise SimError(f"位置 {position} の {axis} は、小数点以下の桁数に丸めると値が変わらないため、候補を作りません")
    if any(a != b for j, (a, b) in enumerate(zip(pos.values, new_pos.values)) if j != k):
        raise SimError("変更後のジョブで別の値が変わってしまうため、候補を作りません")
    actual = after_value - before_value
    if abs(actual) > limit + 1e-9:
        raise SimError(f"丸めた後の実際の移動量が1回の仮の上限（確認待ち）{limit:g}mm を超えるため、候補を作りません")

    warnings = [f"実機用の検査では止まります：{reason}", SIM_NOTICE, jbi.PATH_WARNING]
    if len(users) >= 2:
        where = "・".join(f"{i + 1}行目" for i, _ in users)
        warnings.append(f"この位置を使う命令がすべて変わります（{where}）")
    elif not users:
        warnings.append("この位置はどの命令にも使われていません")
    if abs(actual - delta_mm) > 1e-9:
        warnings.append(
            f"指示の {delta_mm:g}mm は小数点以下の桁数に丸めて、実際には {_format_like(old_token, actual)}mm 動かします")

    return SimProposal(
        mode="sim", position=position, axis=axis, requested=float(delta_mm),
        before_line=body, after_line=new_body,
        before_value=before_value, after_value=after_value,
        line_index=pos.line_index, blocked_reason=reason,
        warnings=tuple(warnings), users=users,
        original_bytes=original, new_bytes=new)


def simulate_repeat(job: jbi.Job, start: str, end: str, new_total: int) -> SimRepeatProposal:
    """回数の変更の候補。実機用の検査（jbi.plan_repeat）に断られたら SimError（参考の候補は作らない）。"""
    original = job.render_bytes()
    try:
        cp = job.plan_repeat(start, end, new_total)
    except jbi.JbiError as e:
        raise SimError(str(e)) from None
    new = cp.new_job.render_bytes()
    enc = job.encoding
    cycle = tuple(line.encode(enc) for line in cp.block.cycle_lines)
    if _repeat_diff_ok(original, new, cp.line_index, cycle, added=cp.after_count > cp.before_count) is not True:
        raise SimError("足す（消す）1回分の行のほかにも違いが出たため、候補を作りません")
    return SimRepeatProposal(
        mode="sim", start=start, end=end, before_count=cp.before_count, after_count=cp.after_count,
        first_line=cp.line_index, cycle_lines=cycle, warnings=tuple(cp.warnings),
        original_bytes=original, new_bytes=new)


def _repeat_diff_ok(a: bytes, b: bytes, at: int, cycle: tuple[bytes, ...], added: bool) -> bool:
    """b が、a の at 行目に cycle を足した（added）／a の at 行目から cycle を消したものと、行単位でちょうど同じか。"""
    la, lb = _LINES.findall(a), _LINES.findall(b)
    if b"".join(la) != a or b"".join(lb) != b:
        return False
    n = len(cycle)
    if added:
        return lb[at:at + n] == list(cycle) and lb[:at] + lb[at + n:] == la
    return la[at:at + n] == list(cycle) and la[:at] + la[at + n:] == lb


# ---- 2. 承認（ボタンから呼ぶ。音声の文字からは呼ばない） ----
def approve(proposal: "SimProposal | SimRepeatProposal", approved_by: str, updown=None) -> Approval:
    """updown：承認したときの上下の向きの照合の結果（updown.UpdownStatus）。記録に残すだけで、判定は呼ぶ側（web/flow.py）。"""
    if not isinstance(proposal, (SimProposal, SimRepeatProposal)) or proposal.mode != "sim":
        raise SimError("シミュレーションの候補ではないため、承認できません")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise SimError("承認した人の名前がないため、承認できません")
    if proposal.blocked_reason:
        # 実機用の検査で止まった候補は、合成ジョブでも承認しない（CHECKER_019 R1）
        raise SimError(f"実機用の検査で止まった変更候補は、シミュレーションでも承認できません（理由：{proposal.blocked_reason}）")
    return Approval(
        original_sha256=_sha(proposal.original_bytes),
        new_sha256=_sha(proposal.new_bytes),
        approved_by=approved_by.strip(),
        approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        updown_status=getattr(updown, "status", None),
        updown_fingerprint=getattr(updown, "fingerprint", None),
        updown_confirmed_by=getattr(updown, "confirmed_by", None))


# ---- 3. シミュレーション用の出力 ----
def export_sim(proposal: SimProposal, approval: Approval) -> Path:
    """承認した変更候補を、シミュレーション専用のフォルダ（DEFAULT_OUT_DIR）に <ジョブ名>_SIM.JBI.txt で書く。"""
    if isinstance(proposal, SimRepeatProposal):
        return _export_repeat(proposal, approval)
    if not isinstance(proposal, SimProposal) or proposal.mode != "sim":
        raise SimError("シミュレーションの候補ではないため、出力しません")
    if proposal.blocked_reason:
        # 承認の記録があっても、実機用の検査で止まった候補は出力しない（CHECKER_019 R1）
        raise SimError(f"実機用の検査で止まった変更候補は、シミュレーションでも出力しません（理由：{proposal.blocked_reason}）")
    if not isinstance(approval, Approval):
        raise SimError("承認されていないため、出力しません")
    if (approval.original_sha256 != _sha(proposal.original_bytes)
            or approval.new_sha256 != _sha(proposal.new_bytes)):
        raise SimError("承認の後でデータが変わったため、出力しません")
    if not _is_synthetic(proposal.original_bytes):
        raise SimError("シミュレーションモードでは、合成ジョブ以外は出力しません")

    try:
        changed = jbi.parse_bytes(proposal.new_bytes)
    except jbi.JbiError:
        raise SimError("変更後のジョブを読み直せなかったため、出力しません") from None
    new_pos = changed.positions.get(proposal.position)
    if (proposal.axis not in jbi.AXES or new_pos is None
            or new_pos.line_index != proposal.line_index
            or new_pos.values[jbi.AXES[proposal.axis]] != proposal.after_value):
        raise SimError("承認した変更と、変更後のジョブの中身が合わないため、出力しません")

    new_name = changed.name + SIM_SUFFIX
    if not _NAME_OK.fullmatch(new_name):
        raise SimError("ジョブ名に使えない文字が入っているため、出力しません")
    name_index = _name_line_index(changed.lines)
    lines = list(changed.lines)
    _, ending = _split_ending(lines[name_index])
    lines[name_index] = f"//NAME {new_name}{ending}"
    try:
        data = "".join(lines).encode(changed.encoding)
    except UnicodeEncodeError:
        raise SimError("ジョブ名を変えたジョブを書き出せないため、出力しません") from None

    # 書く前に：元との差が「承認した位置の1行」と「//NAME の1行」だけであること
    diff = _diff_line_indexes(proposal.original_bytes, data)
    if diff is None or diff != sorted({proposal.line_index, name_index}) or len(diff) != 2:
        raise SimError("承認した位置の行とジョブ名の行のほかにも違いがあるため、出力しません")

    out = Path(DEFAULT_OUT_DIR)
    if out.resolve() == SAMPLES_DIR.resolve():
        raise SimError("合成ジョブの置き場所には出力しません")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{new_name}{SIM_EXTENSION}"
    try:
        with open(path, "xb") as f:
            f.write(data)
    except FileExistsError:
        raise SimError(f"{path.name} が既にあるため、上書きしません") from None
    except OSError as e:
        _remove_quietly(path)
        raise SimError(f"書き出しに失敗しました（{e}）") from None

    # 書いた後に：読み戻して一致を確かめる
    try:
        back = _read_back(path)
    except OSError:
        back = None
    if back != data:
        _remove_quietly(path)
        raise SimError("書き出したファイルを読み戻すと中身が違ったため、ファイルを消しました")
    return path


def _export_repeat(proposal: SimRepeatProposal, approval: Approval) -> Path:
    """回数の変更の出力。元との違いが「足した（消した）1回分の行」と「//NAME の1行」だけのときだけ書く。"""
    if proposal.mode != "sim" or proposal.blocked_reason:
        raise SimError("シミュレーションの候補ではないため、出力しません")
    if not isinstance(approval, Approval):
        raise SimError("承認されていないため、出力しません")
    if (approval.original_sha256 != _sha(proposal.original_bytes)
            or approval.new_sha256 != _sha(proposal.new_bytes)):
        raise SimError("承認の後でデータが変わったため、出力しません")
    if not _is_synthetic(proposal.original_bytes):
        raise SimError("シミュレーションモードでは、合成ジョブ以外は出力しません")
    try:
        original = jbi.parse_bytes(proposal.original_bytes)
        again = original.plan_repeat(proposal.start, proposal.end, proposal.after_count)
        changed = jbi.parse_bytes(proposal.new_bytes)
    except jbi.JbiError:
        raise SimError("承認した変更を作り直せなかったため、出力しません") from None
    if again.new_job.render_bytes() != proposal.new_bytes:
        raise SimError("承認した変更と、変更後のジョブの中身が合わないため、出力しません")
    new_name = changed.name + SIM_SUFFIX
    if not _NAME_OK.fullmatch(new_name):
        raise SimError("ジョブ名に使えない文字が入っているため、出力しません")
    name_index = _name_line_index(changed.lines)
    if name_index >= proposal.first_line:
        raise SimError("ジョブ名の行の場所が読めないため、出力しません")
    lines = list(changed.lines)
    _, ending = _split_ending(lines[name_index])
    lines[name_index] = f"//NAME {new_name}{ending}"
    try:
        data = "".join(lines).encode(changed.encoding)
    except UnicodeEncodeError:
        raise SimError("ジョブ名を変えたジョブを書き出せないため、出力しません") from None
    # 書く前に：元との差が「足した（消した）1回分の行」と「//NAME の1行」だけであること
    named = _LINES.findall(proposal.original_bytes)
    named[name_index] = lines[name_index].encode(changed.encoding)
    if not _repeat_diff_ok(b"".join(named), data, proposal.first_line, proposal.cycle_lines,
                           added=proposal.after_count > proposal.before_count):
        raise SimError("承認した回数の変更の行とジョブ名の行のほかにも違いがあるため、出力しません")
    out = Path(DEFAULT_OUT_DIR)
    if out.resolve() == SAMPLES_DIR.resolve():
        raise SimError("合成ジョブの置き場所には出力しません")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{new_name}{SIM_EXTENSION}"
    try:
        with open(path, "xb") as f:
            f.write(data)
    except FileExistsError:
        raise SimError(f"{path.name} が既にあるため、上書きしません") from None
    except OSError as e:
        _remove_quietly(path)
        raise SimError(f"書き出しに失敗しました（{e}）") from None
    try:
        back = _read_back(path)
    except OSError:
        back = None
    if back != data:
        _remove_quietly(path)
        raise SimError("書き出したファイルを読み戻すと中身が違ったため、ファイルを消しました")
    return path


# ---- 内部 ----
def _read_back(path: Path) -> bytes:
    return Path(path).read_bytes()


def _remove_quietly(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_synthetic(data: bytes) -> bool:
    if not SAMPLES_DIR.is_dir():
        return False
    return any(p.is_file() and p.read_bytes() == data
               for p in SAMPLES_DIR.iterdir() if p.suffix.upper() == ".JBI")


def _name_line_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.startswith("//NAME "):
            return i
    raise SimError("ジョブ名の行（//NAME）が見つからないため、出力しません")


def _diff_line_indexes(a: bytes, b: bytes) -> list[int] | None:
    """行単位（改行コードを含めてバイトで）の違い。行の数が違えば None。"""
    la, lb = _LINES.findall(a), _LINES.findall(b)
    if len(la) != len(lb) or b"".join(la) != a or b"".join(lb) != b:
        return None
    return [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]


def _check_one_line_changed(original: bytes, new: bytes, line_index: int) -> None:
    if _diff_line_indexes(original, new) != [line_index]:
        raise SimError("位置の行のほかにも違いが出たため、候補を作りません")


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _format_like(original: str, value: float) -> str:
    """元の数値と同じ小数点以下の桁数で書く。-0 は 0 にする。"""
    decimals = len(original.split(".", 1)[1]) if "." in original else 0
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return text
