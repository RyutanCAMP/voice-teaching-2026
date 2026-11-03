"""変更の許可の範囲（どの JOB のどの位置を変えてよいか）を照合するための部品（FRAME v1.1 2章。CHECKER_028 K4）。

上下の向きの照合（voice_teaching/updown.py）とは別のもの。上下の向きはロボットの設置と座標系の性質なので、
JOB の中身では照合しない。JOB の中身の照合は、専門家の枠（FRAME v1.1）の役目で、枠そのものはまだ作っていない（保留）。
ここでは、そのときに使う JOB の指紋（structure_hash）の計算だけを用意する。

structure_hash の決まり（唐川さんの判断③・K4）:
- //NAME・///DATE の行を除き、改行を \\n にそろえる（jbi.Job.normalized_hash と同じ正規化）
- 伏せてよいのは、指定した対象の位置の X・Y・Z（先頭の3つ）だけ。回転などの残りの値と、ほかの位置の値はそのまま入る
- 全部の位置を伏せる指定・空の指定・同じ位置の重複・ジョブに無い位置・変更できない位置（plan_shift が通らない位置）は断る
  （全座標を伏せる方式は禁止。違うジョブを同じものとして扱う危険があるため）

照合に通ったことは、物理的な安全の保証ではない。
"""
from __future__ import annotations

import hashlib
import re
from typing import Sequence

from . import jbi

_POS_NAME = re.compile(r"C[0-9]{5}")


class ScopeError(Exception):
    """指紋を計算しない理由（1行の日本語）。"""


def can_change(job: jbi.Job, name: str) -> bool:
    """plan_shift（実機用の検査）が通る位置か。ジョブは変えない（変更案を作って捨てるだけ）。"""
    for delta in (1.0, -1.0):
        try:
            job.plan_shift(name, "Z", delta)
            return True
        except jbi.JbiError:
            continue
    return False


def check_mask(job: jbi.Job, positions: Sequence[str]) -> list[str]:
    """伏せる位置の指定を確かめて、名前の一覧を返す。正しくなければ ScopeError。"""
    if isinstance(positions, (str, bytes)) or not isinstance(positions, Sequence):
        raise ScopeError("伏せる位置の一覧の形が正しくありません")
    names = list(positions)
    if not names:
        raise ScopeError("伏せる位置の指定が空です")
    if not all(isinstance(n, str) and _POS_NAME.fullmatch(n) for n in names):
        raise ScopeError("伏せる位置の名前が正しくありません")
    if len(set(names)) != len(names):
        raise ScopeError("伏せる位置が重複しています")
    for n in names:
        if n not in job.positions:
            raise ScopeError(f"位置 {n} がこのジョブにありません")
    if set(names) >= set(job.positions):
        raise ScopeError("ジョブの全部の位置を伏せる指定は受け付けません")
    for n in names:
        if not can_change(job, n):
            raise ScopeError(f"位置 {n} はこのアプリで変更できない位置のため、伏せる位置にできません")
    return names


def structure_hash(job: jbi.Job, positions: Sequence[str]) -> str:
    """指定した対象の位置の X・Y・Z だけを * に置き換えた、ジョブ全体の sha256（上の決まり）。"""
    names = check_mask(job, positions)
    masked: dict[int, str] = {}
    for name in names:
        pos = job.positions[name]
        body = job.lines[pos.line_index].rstrip("\r\n")
        head, sep, rest = body.partition("=")
        values = rest.split(",")
        if head != name or not sep or len(values) < 3:
            raise ScopeError(f"位置 {name} の行が読めません")
        masked[pos.line_index] = f"{name}=" + ",".join(["*", "*", "*"] + values[3:])
    h = hashlib.sha256()
    for i, line in enumerate(job.lines):
        body = line.rstrip("\r\n")
        if body.startswith("//NAME") or body.startswith("///DATE"):
            continue
        h.update(masked.get(i, body).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def matches(job: jbi.Job, expected_hash: str, positions: Sequence[str]) -> bool:
    """ジョブが、指紋と対象の位置で記録したジョブと同じか（伏せた位置の X・Y・Z だけが違ってよい）。断るものは False。"""
    if not isinstance(expected_hash, str):
        return False
    try:
        return structure_hash(job, positions) == expected_hash
    except ScopeError:
        return False
