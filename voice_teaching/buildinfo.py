"""画面に出す版の文字（番号・チャンネル・Git の SHA）。チャンネルと SHA の決まりはこのコメントの1か所（11/6 唐川さんの決定）。

- 番号：voice_teaching/__init__.py の __version__ の1か所だけ（ここでは読むだけ。今は 1.00-dev）
- 表示：__version__ から番号（"1.00"）と「-dev」の印を分けて読み、チャンネルで末尾だけを決める（CHANNEL_SUFFIX）
    dev     → v1.00-dev
    filming → v1.00-TEST（画面に「撮影用」の日本語は出さない。正式リリースの前なので撮影でも TEST を消さない）
    release → v1.00（__version__ に「-dev」が付いている間は使えない。起動のときに ValueError で断る。dev に倒さない）
  詳しい版（SHA つき）は「v1.00-TEST（filming・a1b2c3d）」の形
- チャンネル：dev / filming / release の3つだけ。起動の引数 --channel、無ければ環境変数 VT_CHANNEL、無ければ dev。
  3つ以外の値は ValueError（起動を止める。似た値を dev に倒すと、撮影用・リリース版と思って開発版を使うおそれがあるため）
- SHA：起動のときに git rev-parse --short HEAD。追跡しているファイルに未コミットの変更があれば「-dirty」。
  git が無い・失敗したら「unknown」（起動は止めない）。ふだんは出さず、版の文字の小さな説明とアプリ情報（ⓘ）・GET /api/version
表示だけ。承認・出力などの判定には使わない。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import voice_teaching

CHANNELS = ("dev", "filming", "release")
DEFAULT_CHANNEL = "dev"
ENV_NAME = "VT_CHANNEL"
DEV_SUFFIX = "-dev"
CHANNEL_SUFFIX = {"dev": "-dev", "filming": "-TEST", "release": ""}  # 画面の版の末尾（内部のチャンネル名は変えない）
UNKNOWN_SHA = "unknown"
_REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BuildInfo:
    version: str   # __version__ のまま（例 1.00-dev）
    number: str    # 「-dev」を外した番号（例 1.00）
    channel: str   # dev / filming / release
    sha: str       # 例 d6f8b2c・d6f8b2c-dirty・unknown

    @property
    def label(self) -> str:
        """画面の小さな版の文字：v＜番号＞＋チャンネルの末尾（v1.00-dev・v1.00-TEST・v1.00）。"""
        return f"v{self.number}{CHANNEL_SUFFIX[self.channel]}"

    @property
    def detail(self) -> str:
        """詳しい版（アプリ情報・小さな説明）：v1.00-dev（dev・a1b2c3d）"""
        return f"{self.label}（{self.channel}・{self.sha}）"

    def as_dict(self) -> dict:
        return {"version": self.version, "channel": self.channel, "sha": self.sha, "label": self.label, "detail": self.detail}


def channel_from(arg: str | None) -> str:
    """決め方は1か所：引数 → 環境変数 VT_CHANNEL → dev。値の確かめは resolve で行う。"""
    if arg is not None:
        return arg
    env = os.environ.get(ENV_NAME)
    return env if env is not None else DEFAULT_CHANNEL


def git_sha(repo: Path = _REPO) -> str:
    """短い SHA。未コミットの変更があれば -dirty。失敗したら unknown。"""
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5)
        if head.returncode != 0 or not head.stdout.strip():
            return UNKNOWN_SHA
        sha = head.stdout.strip()
        if not all(c in "0123456789abcdef" for c in sha):
            return UNKNOWN_SHA
        st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True, text=True, timeout=5)
        if st.returncode != 0:
            return sha
        return sha + ("-dirty" if st.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return UNKNOWN_SHA


def resolve(channel: str, sha: str | None = None) -> BuildInfo:
    """チャンネルを確かめて、版の情報を作る。3つ以外・タグ前の release は ValueError。"""
    if channel not in CHANNELS:
        raise ValueError(f"チャンネルは {' / '.join(CHANNELS)} のどれかです（受け取った値：{channel!r}）")
    version = voice_teaching.__version__
    is_dev = version.endswith(DEV_SUFFIX)
    if channel == "release" and is_dev:
        raise ValueError(f"版が {version}（タグ前）の間は release で起動できません。dev か filming を使ってください")
    number = version[: -len(DEV_SUFFIX)] if is_dev else version
    return BuildInfo(version=version, number=number, channel=channel, sha=git_sha() if sha is None else sha)
