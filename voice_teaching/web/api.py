"""シミュレーション表示の画面に返す JSON を作る関数（読むだけ＋変更案のプレビュー）。

- ジョブの解析は voice_teaching.jbi だけで行う（ここでは JBI を独自に解析しない）。
- 変更案は jbi の plan_shift / plan_speed が作る。検査に断られたら理由をそのまま返し、何も変えない。
- 変更後のジョブ（new_job）の中身は返さない。書き出し・保存・転送はしない。
- ここで足しているのは、表示のための「読むだけ」の処理（図の線の実線・点線の区別など）だけ。
- 図は参考表示。干渉・到達性・安全を保証しない。
"""
from __future__ import annotations

import math
import re

from .. import jbi

FIGURE_NOTE = (
    "この図で離れて見えても、当たらないとは限りません。腕やツールの根元は描いていません。"
    "参考表示であり、実際のロボットの動きではありません。"
)
NOT_DRAWABLE = "ロボット座標でないため描けません"
# いまはシミュレーションモードだけ。実機用モードは後で作る
MODE = "simulation"

# 図で実線にしてよい命令の形（これだけ。ほかは点線で「目安」）。jbi の許可リストと同じ形を、表示用に別に持つ
_PLAIN_MOVL = re.compile(r"MOVL C\d{5} V=\d+(?:\.\d+)?")
# 移動命令（線をつなぐ・切るため。分からない移動は広めに拾って線を切る側にする）
_ANY_MOVE = re.compile(r"(?<![A-Z0-9_])(MOV[LJCS]|SMOV[LJCS]|IMOV)(?![A-Z0-9_])", re.IGNORECASE)
_FLOW = re.compile(r"(?<![A-Z0-9_])(JUMP|CALL|RET)(?![A-Z0-9_])", re.IGNORECASE)


def _max_shift_label() -> str:
    return f"仮の上限（確認待ち） {jbi.DEFAULT_MAX_SHIFT_MM:g}mm"


def _line(job: jbi.Job, index: int) -> str:
    return job.lines[index].rstrip("\r\n")


def _is_move(ins: jbi.Instruction) -> bool:
    return bool(_ANY_MOVE.search(ins.text))


def _drawable(job: jbi.Job, name: str | None) -> jbi.Position | None:
    if name is None:
        return None
    p = job.positions.get(name)
    return p if p is not None and p.is_robot_coordinates() else None


def _xyz(p: jbi.Position | None) -> list[float] | None:
    return [p.values[0], p.values[1], p.values[2]] if p is not None else None


def _check(fn) -> dict:
    try:
        fn()
    except jbi.JbiError as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "reason": ""}


def _segments(job: jbi.Job) -> list[dict]:
    """命令の順に、移動先の位置を線で結ぶ。描けない移動があれば線を切る（飛ばして結ばない）。"""
    out = []
    prev: jbi.Position | None = None
    for ins in job.instructions:
        if not _is_move(ins):
            continue
        p = _drawable(job, ins.position)
        if p is None:
            prev = None
            continue
        if prev is not None:
            out.append({
                "from": prev.name, "to": p.name, "line_no": ins.index + 1, "text": ins.text,
                "solid": bool(_PLAIN_MOVL.fullmatch(ins.text)),
            })
        prev = p
    return out


def _positions(job: jbi.Job, with_checks: bool = True) -> list[dict]:
    out = []
    for name, p in job.positions.items():
        robot = p.is_robot_coordinates()
        item = {
            "name": name,
            "line_no": p.line_index + 1,
            "line": _line(job, p.line_index),
            "format": p.postype or "不明",
            "rectan": p.rectan,
            "robot_coordinates": robot,
            "after_unknown_directive": p.after_unknown_directive,
            "tool": p.tool, "user": p.user, "rconf": p.rconf,
            "xyz": _xyz(p) if robot else None,
        }
        if with_checks:
            item["users"] = [{"line_no": u.index + 1, "text": u.text} for u in job.users_of(name)]
            # 試しに X を +1mm した場合に検査が通すか（変更案は使わない）
            item["shift_check"] = _check(lambda n=name: job.plan_shift(n, "X", 1.0))
        out.append(item)
    return out


def _speed_check(job: jbi.Job, ins: jbi.Instruction) -> dict | None:
    if not _is_move(ins):
        return None
    if ins.command != "MOVL":
        return {"ok": False, "reason": "直線移動（MOVL）ではないため、速度は変更の対象外です"}
    if ins.speed is None:
        return {"ok": False, "reason": "速度が数値で書かれていないため、変更の対象外です"}
    return _check(lambda: job.plan_speed(ins.index, ins.speed + 1))


def job_view(data: bytes) -> dict:
    """JBI のバイト列を jbi.parse_bytes で読み、画面に出す内容を返す。"""
    try:
        job = jbi.parse_bytes(data)
    except jbi.JbiError as e:
        return {"ok": False, "error": str(e)}
    positions = _positions(job)
    segments = _segments(job)
    drawable = [p for p in positions if p["xyz"] is not None]
    if not drawable:
        figure_message = f"どの位置も{NOT_DRAWABLE}。"
    elif len(drawable) < len(positions):
        names = "・".join(p["name"] for p in positions if p["xyz"] is None)
        figure_message = f"{names} は{NOT_DRAWABLE}（図に入れていません）。"
    else:
        figure_message = ""
    flow = sorted({m.group(1).upper() for i in job.instructions for m in _FLOW.finditer(i.text)})
    notes = []
    if flow:
        notes.append(f"このジョブには {'・'.join(flow)} があるため、図の線は行の並びから推定したものです。")
    return {
        "ok": True,
        "mode": MODE,
        "name": job.name,
        "encoding": job.encoding,
        "problems": list(job.problems),
        "positions": positions,
        "instructions": [{
            "line_no": i.index + 1, "text": i.text, "command": i.command, "position": i.position,
            "refs": list(i.refs), "is_move": _is_move(i), "speed": i.speed, "speed_raw": i.speed_raw,
            "pl": i.pl, "has_cr": i.has_cr, "speed_check": _speed_check(job, i),
        } for i in job.instructions],
        "segments": segments,
        "figure_message": figure_message,
        "figure_note": FIGURE_NOTE,
        "notes": notes,
        "max_shift_mm": jbi.DEFAULT_MAX_SHIFT_MM,
        "max_shift_label": _max_shift_label(),
    }


def figure_data(job: jbi.Job) -> dict:
    """図を描くための位置と線だけ（検査の試しは含めない）。変更後のジョブの図に使う。"""
    return {"positions": _positions(job, with_checks=False), "segments": _segments(job)}


def _proposal(p: jbi.ChangeProposal) -> dict:
    return {
        "kind": p.kind, "target": p.target, "line_no": p.line_index + 1,
        "before_line": p.before_line, "after_line": p.after_line,
        "requested": p.requested, "before_value": p.before_value, "after_value": p.after_value,
        "actual_delta": p.actual_delta,
        "affected": list(p.affected), "warnings": list(p.warnings),
    }


def _refused(reason: str) -> dict:
    """検査に断られたとき。blocked_reason は「実機用の検査で止まる理由」の欄。

    いまは jbi の plan_shift / plan_speed が断った理由をそのまま入れ、変更案は出さない。
    後で voice_teaching/sim.py が、断られてもシミュレーションのプレビューを出す形にするときは、
    proposal と blocked_reason を同時に入れる（warnings は注意、blocked_reason は止まる理由で、別々に持つ）。
    reason は以前の形との互換のため同じ文を入れる。
    """
    return {"ok": False, "mode": MODE, "reason": reason, "blocked_reason": reason, "warnings": []}


def _accepted(prop: jbi.ChangeProposal, **extra) -> dict:
    return {"ok": True, "mode": MODE, "proposal": _proposal(prop),
            "warnings": list(prop.warnings), "blocked_reason": None, **extra}


def preview_shift(data: bytes, position: object, axis: object, delta_mm: object) -> dict:
    """位置の変更案を作って返す（ジョブは変えない・書き出さない）。断られたら理由だけを返す。"""
    try:
        job = jbi.parse_bytes(data)
    except jbi.JbiError as e:
        return _refused(str(e))
    if not isinstance(position, str) or not isinstance(axis, str):
        return _refused("位置または軸の指定が読めないため、変更しません")
    try:
        prop = job.plan_shift(position, axis, delta_mm)  # 型の検査も jbi に任せる
    except jbi.JbiError as e:
        return _refused(str(e))
    new = prop.new_job
    highlight = [u.index + 1 for u in job.users_of(position)]
    nxt = next((i for i in job.instructions
                if i.index > max(u.index for u in job.users_of(position)) and _is_move(i)), None)
    if nxt is not None:
        highlight.append(nxt.index + 1)
    return _accepted(
        prop,
        after_positions=_positions(new, with_checks=False),
        after_segments=_segments(new),
        highlight_lines=highlight,
        moved=position,
    )


def preview_speed(data: bytes, line_no: object, new_speed: object) -> dict:
    """速度の変更案を作って返す（ジョブは変えない・書き出さない）。断られたら理由だけを返す。"""
    try:
        job = jbi.parse_bytes(data)
    except jbi.JbiError as e:
        return _refused(str(e))
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 1:
        return _refused("行番号が正しく指定されていないため、変更しません")
    if isinstance(new_speed, float) and not math.isfinite(new_speed):
        return _refused("速度は正の有限の数値でなければならないため、変更しません")
    try:
        prop = job.plan_speed(line_no - 1, new_speed)
    except jbi.JbiError as e:
        return _refused(str(e))
    return _accepted(prop, highlight_lines=[line_no])
