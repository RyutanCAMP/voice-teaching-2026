"""起動: python -m voice_teaching.web [--port 8765]

127.0.0.1 だけで待ち受ける。Chrome で表示された URL を開く。止めるときは Ctrl+C。
シミュレーションモード：元のジョブは変えない。承認ボタンで承認した候補だけを、合成ジョブに限って
output/sim/ に新しいジョブ名で出力する。実機用の出力・転送・ロボットの起動はしない。
"""
from __future__ import annotations

import argparse
import sys

from .server import DEFAULT_PORT, make_server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ボイスティーチング シミュレーションモード（127.0.0.1 だけ）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    srv = make_server(port=args.port)
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
