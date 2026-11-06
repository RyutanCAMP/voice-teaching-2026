"""起動: python -m voice_teaching.web [--port 8765] [--updown-config records/updown.json] [--robot HC20DT]
      [--env-file C:/アプリ/voice-teaching/.env] [--explain-model claude-sonnet-5-5]

127.0.0.1 だけで待ち受ける。Chrome で表示された URL を開く。止めるときは Ctrl+C。
シミュレーションモード：元のジョブは変えない。承認ボタンで承認した候補だけを、合成ジョブに限って
output/sim/ に新しいジョブ名で出力する。実機用の出力・転送・ロボットの起動はしない。
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import buildinfo, claude_explain, claude_interpreter, claude_lead, sim, updown
from .server import DEFAULT_PORT, make_server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ボイスティーチング シミュレーションモード（127.0.0.1 だけ）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    # 上下の向きの設定（専門家が python -m voice_teaching.updown register で書く）。無ければ「上・下」は Z にしない
    ap.add_argument("--updown-config", default=str(updown.DEFAULT_PATH),
                    help="上下の向きの設定ファイル（既定 records/updown.json）")
    ap.add_argument("--robot", default=updown.DEFAULT_ROBOT, help="アプリで使うロボットの名前（設定と照合する。既定 HC20DT）")
    ap.add_argument("--env-file", default=str(sim.REPO_ROOT / ".env"),
                    help=".env の場所（既定はリポジトリ直下。worktree からは本体の .env を指定できる）")
    ap.add_argument("--explain-model", default=None,
                    help="「Claude に聞く（読み取りだけ）」のモデル（無ければ ANTHROPIC_MODEL、それも無ければ claude-sonnet-5-5）")
    ap.add_argument("--channel", choices=buildinfo.CHANNELS, default=None,
                    help="画面の版の区別（dev／filming／release。無ければ環境変数 VT_CHANNEL、それも無ければ dev）")
    args = ap.parse_args(argv)
    # 任意：.env から ANTHROPIC_API_KEY・ANTHROPIC_MODEL を読む（環境変数があればそちら）
    if claude_interpreter.load_dotenv(args.env_file) == claude_interpreter.DOTENV_UNREADABLE:
        print(claude_interpreter.DOTENV_UNREADABLE_MESSAGE, flush=True)
    interp = claude_interpreter.from_env()
    explainer = claude_explain.Explainer.from_env(model=args.explain_model)
    try:
        srv = make_server(port=args.port, interpreter=interp, updown_config=args.updown_config, robot=args.robot,
                          explainer=explainer, channel=args.channel)
    except ValueError as e:  # チャンネルが3つ以外（環境変数 VT_CHANNEL）・タグ前の release：起動しない
        ap.error(str(e))
    if getattr(srv, "build", None) is not None:
        print(f"版：{srv.build.detail}", flush=True)
    # Claude の言い添え（指示の道の答えの前の一言）：本物の Claude で、キーがあるときだけ。VOICE_TEACHING_CLAUDE_LEAD=0 で使わない
    srv.flow.claude_lead = claude_lead.enabled(interp, explainer, os.environ)
    print(f"解釈：{'本物の Claude（' + interp.model + '）' if interp.kind == 'claude' else '偽のClaude（モック）'}", flush=True)
    print(f"Claude の言い添え：{'使う' if srv.flow.claude_lead else '使わない'}", flush=True)
    print(f"Claude に聞く（読み取りだけ）：{'キーあり・' + explainer.model if explainer.has_key else 'APIキーが設定されていません'}",
          flush=True)
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
