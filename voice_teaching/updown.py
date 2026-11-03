"""上下の向きの設定（CHECKER_026 M2・11章、唐川さんの決定 2026-11-03 13:28 A案）。

専門家が設定ファイル（JSON）に「上＝Z＋、下＝Z−」を登録し、アプリは使うたびに照合する。
照合に通ったとき（confirmed）だけ、自然言語の「上」「下」「上げ」「下げ」を Z± に変える。
床置き・架台固定の情報だけでは決めない。照合に通らなければ、どの状態でも「上・下」を軸に変えず、
「Zプラス・Zマイナス」のように軸と向きで言ってもらう（軸と符号を明示した指示は今どおり候補にしてよい）。

照合（11-2章。すべてを満たすときだけ confirmed）:
1. 設定ファイルがあり、形が正しい（知らない項目・空の根拠・符号の無い値・Z 以外・NaN・bool の取り違え → 読まない＝未確認）
2. 設定の指紋（fingerprint）が中身と一致する（合わなければ「変更済み」）
3. 読み込んだ JOB の structure_hash が設定の対象と一致する（FRAME v1.1 2章。アプリで位置だけ直した _SIM は一致してよい）
   伏せる位置（positions）は一部だけ（唐川さんの判断③ 2026-11-03：全部の座標値を伏せる方式は採らない）。
   上限 MAX_MASKED_POSITIONS（3つ）以内で、ジョブの位置の半分より少なく、どれも変更できる位置（plan_shift が通る位置）。
   伏せない位置は X・Y・Z も含めてそのまま指紋に入る
4. JOB の座標系が設定の対象と同じ（///POSTYPE ROBOT・///RECTAN があり、///USER が無く、ツール番号が同じ。
   相対ジョブかどうか（///ATTR の RJ）と FRAME（jbi が認めた「////FRAME ROBOT」だけ）も同じ）
5. 対象のロボットの名前が、アプリで使うロボットの設定（起動オプション。既定 HC20DT）と同じ（文字列を比べるだけ）

限界（正直に書く）：
- 設定は「誰が・何を根拠に確認したか」の記録で、本人であることの証明（電子署名）ではない。JSON を書ける人は設定も書ける。
- 照合に通ったことは、物理的な安全の保証ではない。向きの対応が正しいかは、確認した人の判断による。
- このモジュールは設定を読むだけ。設定を書くのは CLI の register だけで、人が内容を見て yes と入力したときだけ書く。
  アプリの画面・API・声・Claude からは書けない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from . import jbi

REPO_ROOT = Path(__file__).resolve().parent.parent
# 既定の置き場所（records/ は .gitignore 済み。リポジトリに入れない）
DEFAULT_PATH = REPO_ROOT / "records" / "updown.json"
DEFAULT_ROBOT = "HC20DT"

SCHEMA = "voice-teaching/updown"
VERSION = 1
# 受け付ける向きの対応はこの1組だけ（Z 以外・符号なし・逆向きは読まない。逆向きの設置は、決まってから足す）
DIRECTIONS = {"上": "Z+", "下": "Z-"}
DIRECTION_NAMES = {"Z": {"+": "上", "-": "下"}}  # 確認済みのときだけ使う方向の名前
MAX_BYTES = 64 * 1024
# 伏せる位置の数の上限（判断③）。伏せた位置の X・Y・Z は照合に入らないので、伏せる数が多いほど
# 違うジョブを同じものとして扱いやすくなる。シミュレーションで直す位置（1回に1点）だけを伏せる使い方を想定し、
# 3つまでとする。あわせて、ジョブの位置の半分より少ないこと（伏せない位置が多い方）を照合のたびに確かめる
MAX_MASKED_POSITIONS = 3

_TOP_KEYS = {"schema", "version", "directions", "confirmed_by", "confirmed_at", "evidence", "robot", "job",
             "coordinate", "fingerprint"}
_JOB_KEYS = {"name", "structure_hash", "positions"}
_COORD_KEYS = {"postype", "rectan", "user", "tool", "frame", "relative_job"}
_FRAMES = {None, "ROBOT"}  # 設定の frame に書ける値（jbi が認める相対ジョブの座標系は ROBOT だけ）
_HEX64 = re.compile(r"[0-9a-f]{64}")
_POS_NAME = re.compile(r"C[0-9]{5}")
_MAX_TEXT = {"confirmed_by": 40, "robot": 40, "evidence": 500, "name": 40}

UNCONFIRMED = "unconfirmed"
CONFIRMED = "confirmed"
MISMATCH = "mismatch"
CHANGED = "changed"

# 案内の文（CHECKER_026 11-2章の表のまま。プログラムが出す）
MSG_UNCONFIRMED = "上下の向きがまだ確認されていません。Zプラス・Zマイナスのように、軸と向きで言ってください"
MSG_MISMATCH = "このジョブは、上下の向きを確認したジョブと違います。Zプラス・Zマイナスで言ってください"
MSG_CHANGED = "上下の向きの設定が、確認したあとで変わっています。Zプラス・Zマイナスで言ってください"
MSG_CONFIRMED = "上下の向き：{by} さんが確認した設定（{at}）で、上＝Z＋・下＝Z−として読みます"

SOURCE_CONFIRMED = "上下の向きの設定（{by} さんが確認・{at}）"
SOURCE_NONE = "確認済みの方向の名前はありません"


class UpdownError(Exception):
    """設定ファイルを読まない・書かない理由（1行の日本語）。"""


@dataclass(frozen=True)
class UpdownConfig:
    confirmed_by: str
    confirmed_at: str
    evidence: str
    robot: str
    job_name: str
    structure_hash: str
    positions: tuple[str, ...]
    tool: int
    frame: str | None         # 相対ジョブの座標系（"ROBOT"）。ふつうのジョブは None
    relative_job: bool        # ///ATTR に RJ があるジョブか
    fingerprint: str          # ファイルに書かれた指紋
    computed_fingerprint: str  # 中身から計算した指紋


@dataclass(frozen=True)
class UpdownStatus:
    """照合の結果。confirmed のときだけ、上・下を Z± に変えてよい。"""
    status: str
    message: str
    reason: str = ""                  # 詳しい理由（ログ・CLI 用。案内の文とは別）
    confirmed_by: str | None = None   # confirmed のときだけ
    confirmed_at: str | None = None   # confirmed のときだけ
    fingerprint: str | None = None    # confirmed のときだけ（使った設定の指紋）
    file_sha256: str | None = None    # 設定ファイルのバイト列の sha256（あれば。変更の検出に使う）

    @property
    def confirmed(self) -> bool:
        return self.status == CONFIRMED

    @property
    def direction_names(self) -> dict:
        return {k: dict(v) for k, v in DIRECTION_NAMES.items()} if self.confirmed else {}

    @property
    def direction_names_source(self) -> str:
        if self.confirmed:
            return SOURCE_CONFIRMED.format(by=self.confirmed_by, at=self.confirmed_at)
        return SOURCE_NONE

    @property
    def token(self) -> tuple:
        """承認・出力のときに比べる値。設定ファイルが1バイトでも変われば変わる。"""
        return (self.status, self.fingerprint, self.file_sha256)

    def as_state(self) -> dict:
        return {"status": self.status, "message": self.message, "confirmed_by": self.confirmed_by,
                "confirmed_at": self.confirmed_at, "fingerprint": self.fingerprint}


def unconfirmed(reason: str = "設定ファイルがありません", file_sha256: str | None = None) -> UpdownStatus:
    return UpdownStatus(UNCONFIRMED, MSG_UNCONFIRMED, reason=reason, file_sha256=file_sha256)


NOT_CONFIGURED = unconfirmed("上下の向きの設定ファイルの場所が決まっていません")


# ---- 指紋 ----
def structure_hash(job: jbi.Job, positions: Sequence[str]) -> str:
    """FRAME v1.1 2章：normalized_hash と同じ正規化（//NAME・///DATE の行を除き、改行を \\n にそろえる）をして、
    positions の位置の行の値の先頭3つ（X,Y,Z）を * に置き換え、全体の sha256 を取る。

    位置がジョブに無い・位置の行が読めないときは UpdownError（照合では「不一致」）。
    """
    if isinstance(positions, str):
        raise UpdownError("位置の一覧の形が正しくありません")
    masked: dict[int, str] = {}
    for name in positions:
        pos = job.positions.get(name) if isinstance(name, str) else None
        if pos is None:
            raise UpdownError(f"位置 {name} がこのジョブにありません")
        body = job.lines[pos.line_index].rstrip("\r\n")
        head, sep, rest = body.partition("=")
        values = rest.split(",")
        if head != name or not sep or len(values) < 3:
            raise UpdownError(f"位置 {name} の行が読めません")
        masked[pos.line_index] = f"{name}=" + ",".join(["*", "*", "*"] + values[3:])
    h = hashlib.sha256()
    for i, line in enumerate(job.lines):
        body = line.rstrip("\r\n")
        if body.startswith("//NAME") or body.startswith("///DATE"):
            continue
        h.update(masked.get(i, body).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def content_fingerprint(obj: dict) -> str:
    """設定の指紋：fingerprint の項目を除いた中身を、項目の順と空白に左右されない形にした UTF-8 の sha256。"""
    body = {k: v for k, v in obj.items() if k != "fingerprint"}
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- 読み込みと形の検査 ----
def _reject_constant(name: str):
    raise UpdownError(f"数値でない値（{name}）があります")


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise UpdownError(f"項目「{k}」が2回あります")
        out[k] = v
    return out


def _text(obj: dict, key: str, limit: int) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise UpdownError(f"「{key}」が空か、文字ではありません")
    if len(v) > limit or any(not c.isprintable() for c in v):
        raise UpdownError(f"「{key}」の文字が長すぎるか、読めない文字があります")
    return v


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_config(data: bytes) -> UpdownConfig:
    """設定ファイルのバイト列を読む。形が正しくなければ UpdownError（照合では「未確認」と同じ）。"""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
        raise UpdownError("設定ファイルが大きすぎます")
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UpdownError("設定ファイルが UTF-8 で読めません") from None
    try:
        obj = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)
    except ValueError:
        raise UpdownError("設定ファイルが JSON として読めません") from None
    if not isinstance(obj, dict):
        raise UpdownError("設定ファイルの形が正しくありません")
    keys = set(obj)
    if keys != _TOP_KEYS:
        extra, missing = sorted(keys - _TOP_KEYS), sorted(_TOP_KEYS - keys)
        raise UpdownError("知らない項目" + "・".join(extra) + "があります" if extra
                          else "必要な項目" + "・".join(missing) + "がありません")
    if obj["schema"] != SCHEMA:
        raise UpdownError("schema が違います")
    if not _int(obj["version"]) or obj["version"] != VERSION:
        raise UpdownError("version が違います")

    d = obj["directions"]
    if not isinstance(d, dict) or set(d) != set(DIRECTIONS):
        raise UpdownError("向きの対応（directions）は「上」「下」の2つだけです")
    for word, want in DIRECTIONS.items():
        v = d[word]
        if not isinstance(v, str):
            raise UpdownError(f"「{word}」の向きが文字ではありません")
        if not v.startswith("Z"):
            raise UpdownError(f"「{word}」の向きが Z ではありません")
        if v != want:
            raise UpdownError(f"「{word}」の向きは {want} だけを受け付けます（符号が無い・違う）")

    by = _text(obj, "confirmed_by", _MAX_TEXT["confirmed_by"])
    at = obj["confirmed_at"]
    if not isinstance(at, str):
        raise UpdownError("確認した日時（confirmed_at）が文字ではありません")
    try:
        when = datetime.fromisoformat(at)
    except ValueError:
        raise UpdownError("確認した日時（confirmed_at）が ISO 8601 の形ではありません") from None
    if when.tzinfo is None:
        raise UpdownError("確認した日時（confirmed_at）に時差（+09:00 など）がありません")
    evidence = _text(obj, "evidence", _MAX_TEXT["evidence"])
    robot = _text(obj, "robot", _MAX_TEXT["robot"])

    job = obj["job"]
    if not isinstance(job, dict) or set(job) != _JOB_KEYS:
        raise UpdownError("対象の JOB（job）の項目が正しくありません")
    job_name = _text(job, "name", _MAX_TEXT["name"])
    sh = job["structure_hash"]
    if not isinstance(sh, str) or not _HEX64.fullmatch(sh):
        raise UpdownError("対象の JOB の指紋（structure_hash）が64桁の16進ではありません")
    positions = job["positions"]
    if (not isinstance(positions, list) or not positions
            or not all(isinstance(p, str) and _POS_NAME.fullmatch(p) for p in positions)
            or len(set(positions)) != len(positions)):
        raise UpdownError("値を伏せる位置の一覧（positions）が正しくありません")
    if len(positions) > MAX_MASKED_POSITIONS:
        raise UpdownError(f"伏せる位置が{len(positions)}つあり、上限の{MAX_MASKED_POSITIONS}つを超えています")

    c = obj["coordinate"]
    if not isinstance(c, dict) or set(c) != _COORD_KEYS:
        raise UpdownError("対象の座標系（coordinate）の項目が正しくありません")
    if c["postype"] != "ROBOT":
        raise UpdownError("座標系は POSTYPE ROBOT だけです")
    if c["rectan"] is not True:
        raise UpdownError("座標系は RECTAN（直交）だけです")
    if c["user"] is not None:
        raise UpdownError("ユーザ座標（USER）のある座標系は受け付けません")
    tool = c["tool"]
    if not _int(tool) or tool < 0:
        raise UpdownError("ツール番号（tool）が0以上の整数ではありません")
    frame, relative = c["frame"], c["relative_job"]
    if not isinstance(relative, bool):
        raise UpdownError("相対ジョブかどうか（relative_job）が true・false ではありません")
    if not (frame is None or isinstance(frame, str)) or frame not in _FRAMES:
        raise UpdownError("FRAME（frame）は null か \"ROBOT\" だけです")
    if (frame == "ROBOT") != relative:
        raise UpdownError("FRAME ROBOT は相対ジョブ（relative_job: true）のときだけで、相対ジョブには FRAME ROBOT が要ります")

    fp = obj["fingerprint"]
    if not isinstance(fp, str) or not _HEX64.fullmatch(fp):
        raise UpdownError("設定の指紋（fingerprint）が64桁の16進ではありません")
    return UpdownConfig(confirmed_by=by, confirmed_at=at, evidence=evidence, robot=robot, job_name=job_name,
                        structure_hash=sh, positions=tuple(positions), tool=tool, frame=frame,
                        relative_job=relative, fingerprint=fp,
                        computed_fingerprint=content_fingerprint(obj))


# ---- 照合 ----
def job_relative(job: jbi.Job) -> bool:
    """///ATTR に RJ（相対ジョブ）があるか（並びの1つとして）。"""
    for line in job.lines:
        body = line.rstrip("\r\n")
        if body.startswith("///ATTR ") and "RJ" in body[len("///ATTR "):].split(","):
            return True
    return False


def _frame_problem(job: jbi.Job) -> str | None:
    """FRAME の行と ATTR の RJ が、jbi の決定どおりの形（ふつうのジョブ、または RJ＋「////FRAME ROBOT」）でなければ理由。"""
    has_frame_line = any(jbi.is_frame_line(line.rstrip("\r\n")) for line in job.lines)
    if has_frame_line and job.relative_frame != "ROBOT":
        return "ジョブの FRAME の行が、ROBOT の座標系として扱える形ではありません"
    if job_relative(job) and job.relative_frame != "ROBOT":
        return "相対ジョブ（///ATTR の RJ）ですが、FRAME の座標系（////FRAME ROBOT）が分かりません"
    return None


def _coordinate_problem(job: jbi.Job, tool: int) -> str | None:
    """JOB の座標系が、設定の対象（ROBOT・RECTAN・USER なし・ツール番号・FRAME）と同じでなければ、その理由。"""
    if not job.positions:
        return "ジョブに位置がありません"
    frame = _frame_problem(job)
    if frame:
        return frame
    if any(line.rstrip("\r\n").startswith("///USER") for line in job.lines):
        return "ジョブにユーザ座標（///USER）があります"
    for p in job.positions.values():
        if p.after_unknown_directive or p.postype != "ROBOT" or not p.rectan:
            return f"位置 {p.name} が POSTYPE ROBOT・RECTAN ではありません"
        if p.user is not None:
            return f"位置 {p.name} にユーザ座標があります"
        if p.tool is None or not p.tool.isdigit() or int(p.tool) != tool:
            return f"位置 {p.name} のツール番号が設定（{tool}）と違います"
    return None


def _mask_problem(job: jbi.Job, positions: Sequence[str]) -> str | None:
    """伏せる位置が、ジョブの位置の一部だけで、どれも変更できる位置か（判断③）。合わなければ理由。"""
    names = list(positions)
    if not names or len(set(names)) != len(names):
        return "伏せる位置の一覧が正しくありません"
    if len(names) > MAX_MASKED_POSITIONS:
        return f"伏せる位置が{len(names)}つあり、上限の{MAX_MASKED_POSITIONS}つを超えています"
    if len(names) * 2 >= len(job.positions):
        return (f"伏せる位置が{len(names)}つで、ジョブの位置（{len(job.positions)}つ）の半分以上です。"
                "伏せない位置の方が多くなるようにしてください")
    for name in names:
        if name not in job.positions:
            return f"位置 {name} がこのジョブにありません"
        if not _can_change(job, name):
            return f"位置 {name} はこのアプリで変更できない位置のため、伏せる位置にできません"
    return None


def _can_change(job: jbi.Job, name: str) -> bool:
    """plan_shift（実機用の検査）が通る位置か。ジョブは変えない（変更案を作って捨てるだけ）。"""
    for delta in (1.0, -1.0):
        try:
            job.plan_shift(name, "Z", delta)
            return True
        except jbi.JbiError:
            continue
    return False


def check(job: jbi.Job, path: str | Path | None, robot: str = DEFAULT_ROBOT) -> UpdownStatus:
    """設定ファイルを読み直して、ジョブと照らし合わせる（11-2章の1〜5）。例外は出さない。"""
    if path is None:
        return NOT_CONFIGURED
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return unconfirmed("設定ファイルがありません")
    except OSError:
        return unconfirmed("設定ファイルを読めません")
    sha = hashlib.sha256(data).hexdigest()
    try:
        cfg = parse_config(data)
    except UpdownError as e:
        return unconfirmed(f"設定ファイルを読みませんでした：{e}", sha)
    if cfg.computed_fingerprint != cfg.fingerprint:
        return UpdownStatus(CHANGED, MSG_CHANGED, reason="設定の指紋が中身と合いません", file_sha256=sha)
    try:
        h = structure_hash(job, cfg.positions)
    except UpdownError as e:
        return UpdownStatus(MISMATCH, MSG_MISMATCH, reason=str(e), file_sha256=sha)
    if h != cfg.structure_hash:
        return UpdownStatus(MISMATCH, MSG_MISMATCH, reason="JOB の指紋（structure_hash）が違います", file_sha256=sha)
    problem = _coordinate_problem(job, cfg.tool)
    if problem is None and (job.relative_frame != cfg.frame or job_relative(job) != cfg.relative_job):
        problem = (f"ジョブの相対ジョブ・FRAME（RJ {'あり' if job_relative(job) else 'なし'}・FRAME {job.relative_frame}）が"
                   f"設定（RJ {'あり' if cfg.relative_job else 'なし'}・FRAME {cfg.frame}）と違います")
    if problem is None:
        problem = _mask_problem(job, cfg.positions)
    if problem:
        return UpdownStatus(MISMATCH, MSG_MISMATCH, reason=problem, file_sha256=sha)
    if not isinstance(robot, str) or robot != cfg.robot:
        return UpdownStatus(MISMATCH, MSG_MISMATCH, reason=f"ロボットの名前が違います（設定：{cfg.robot}）",
                            file_sha256=sha)
    return UpdownStatus(CONFIRMED, MSG_CONFIRMED.format(by=cfg.confirmed_by, at=cfg.confirmed_at),
                        reason="照合に通りました", confirmed_by=cfg.confirmed_by, confirmed_at=cfg.confirmed_at,
                        fingerprint=cfg.fingerprint, file_sha256=sha)


def checker(path: str | Path | None, robot: str = DEFAULT_ROBOT) -> Callable[[jbi.Job], UpdownStatus]:
    """Session に渡す照合（呼ぶたびにファイルを読み直す）。"""
    return lambda job: check(job, path, robot)


# ---- 設定を作る（専門家が CLI で。人が内容を見て yes と入力したときだけ書く） ----
def build_config(job: jbi.Job, positions: Sequence[str], robot: str, by: str, evidence: str,
                 confirmed_at: str | None = None) -> dict:
    """設定の中身を作る（書かない）。ジョブの座標系が ROBOT・RECTAN・USER なし・ツール番号が1つでなければ断る。"""
    if job.problems:
        raise UpdownError("このジョブには読めない所があるため、設定を作りません")
    tools = {p.tool for p in job.positions.values()}
    if len(tools) != 1 or next(iter(tools)) is None or not str(next(iter(tools))).isdigit():
        raise UpdownError("ジョブのツール番号が1つに決まらないため、設定を作りません")
    tool = int(next(iter(tools)))
    problem = _coordinate_problem(job, tool)
    if problem:
        raise UpdownError(f"{problem}。設定を作りません")
    positions = list(positions)
    problem = _mask_problem(job, positions)
    if problem:
        raise UpdownError(f"{problem}。設定を作りません")
    at = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    obj = {
        "schema": SCHEMA, "version": VERSION,
        "directions": dict(DIRECTIONS),
        "confirmed_by": by, "confirmed_at": at, "evidence": evidence,
        "robot": robot,
        "job": {"name": job.name, "structure_hash": structure_hash(job, positions), "positions": positions},
        "coordinate": {"postype": "ROBOT", "rectan": True, "user": None, "tool": tool,
                       "frame": job.relative_frame, "relative_job": job_relative(job)},
    }
    obj["fingerprint"] = content_fingerprint(obj)
    parse_config(dump(obj))  # 書く前に、読む側と同じ検査を通す
    return obj


def dump(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_config(obj: dict, path: str | Path, overwrite: bool = False) -> Path:
    p = Path(path)
    data = dump(obj)
    parse_config(data)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb" if overwrite else "xb") as f:
        f.write(data)
    return p


def _summary(obj: dict) -> list[str]:
    return [
        f"  向きの対応     ：上＝{obj['directions']['上']}、下＝{obj['directions']['下']}",
        f"  確認した人     ：{obj['confirmed_by']}",
        f"  確認した日時   ：{obj['confirmed_at']}",
        f"  根拠           ：{obj['evidence']}",
        f"  対象のロボット ：{obj['robot']}",
        f"  対象の JOB     ：{obj['job']['name']}（structure_hash {obj['job']['structure_hash'][:16]}…、"
        f"値を伏せる位置 {'・'.join(obj['job']['positions'])}）",
        f"  座標系         ：POSTYPE ROBOT・RECTAN・USER なし・ツール番号 {obj['coordinate']['tool']}",
        ("  相対ジョブ     ：あり（///ATTR の RJ・////FRAME ROBOT。行き先はツール番号の設定にも左右されます）"
         if obj['coordinate']['relative_job'] else "  相対ジョブ     ：なし（FRAME の行なし）"),
        f"  設定の指紋     ：{obj['fingerprint']}",
    ]


def main(argv: list[str] | None = None, input_fn: Callable[[str], str] = input,
         out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(prog="python -m voice_teaching.updown",
                                 description="上下の向きの設定（専門家が確認して登録する。記録であり、本人確認ではありません）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="設定ファイルを書く（内容を見て yes と入力したときだけ）")
    r.add_argument("--job", required=True)
    r.add_argument("--positions", required=True, help="値を伏せる位置（例 C00005。複数は , で区切る）")
    r.add_argument("--robot", default=DEFAULT_ROBOT)
    r.add_argument("--by", required=True, help="確認した人")
    r.add_argument("--evidence", required=True, help="確認の根拠（ペンダントで Z+ にジョグして真上へ動いた など）")
    r.add_argument("--config", default=str(DEFAULT_PATH))
    r.add_argument("--overwrite", action="store_true", help="既にある設定ファイルを置き換える")
    c = sub.add_parser("check", help="照合の結果を表示する")
    c.add_argument("--job", required=True)
    c.add_argument("--config", default=str(DEFAULT_PATH))
    c.add_argument("--robot", default=DEFAULT_ROBOT)
    args = ap.parse_args(argv)
    try:
        job = jbi.load(args.job)
    except (OSError, jbi.JbiError) as e:
        out(f"ジョブを読めません：{e}")
        return 2
    if args.cmd == "check":
        st = check(job, args.config, args.robot)
        out(f"状態：{st.status}")
        out(f"案内：{st.message}")
        out(f"理由：{st.reason}")
        if st.confirmed:
            out(f"確認した人：{st.confirmed_by}（{st.confirmed_at}）")
            out(f"設定の指紋：{st.fingerprint}")
        return 0 if st.confirmed else 1
    positions = [p.strip() for p in args.positions.split(",") if p.strip()]
    try:
        obj = build_config(job, positions, args.robot, args.by, args.evidence)
    except UpdownError as e:
        out(f"設定を作りません：{e}")
        return 2
    out("次の内容で、上下の向きの設定を書きます（確認の記録です。本人確認・安全の保証ではありません）：")
    for line in _summary(obj):
        out(line)
    try:  # 人が見て入力する。省く方法は作らない
        answer = (input_fn("この内容を、自分で確かめたなら yes と入力してください：") or "").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer != "yes":
        out("書きませんでした")
        return 3
    try:
        path = write_config(obj, args.config, overwrite=args.overwrite)
    except FileExistsError:
        out(f"{args.config} が既にあるため、書きませんでした（置き換えるときは --overwrite）")
        return 2
    except (OSError, UpdownError) as e:
        out(f"書けませんでした：{e}")
        return 2
    out(f"書きました：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
