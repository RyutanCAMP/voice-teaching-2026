"""起動: python -m voice_teaching.web [--port 8765]

127.0.0.1 だけで待ち受ける。Chrome で表示された URL を開く。止めるときは Ctrl+C。
シミュレーションモード：元のジョブは変えない。承認ボタンで承認した候補だけを、合成ジョブに限って
output/sim/ に新しいジョブ名で出力する。実機用の出力・転送・ロボットの起動はしない。
"""
from __future__ import annotations

import argparse
import sys

from .. import claude_interpreter, sim
from .server import DEFAULT_PORT, make_server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ボイスティーチング シミュレーションモード（127.0.0.1 だけ）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    # 任意：リポジトリ直下の .env から ANTHROPIC_API_KEY・ANTHROPIC_MODEL を読む（環境変数があればそちら）
    claude_interpreter.load_dotenv(sim.REPO_ROOT / ".env")
    interp = claude_interpreter.from_env()
    srv = make_server(port=args.port, interpreter=interp)
    print(f"解釈：{'本物の Claude（' + interp.model + '）' if interp.kind == 'claude' else '偽のClaude（モック）'}", flush=True)
    print(f"Chrome で開いてください: http://127.0.0.1:{srv.server_address[1]}/  （止めるときは Ctrl+C）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
