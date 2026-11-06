"""起動: python -m voice_teaching.web [--port 8765] [--updown-config records/updown.json] [--robot HC20DT]
      [--env-file C:/アプリ/voice-teaching/.env] [--explain-model claude-sonnet-5-5] [--channel dev|filming|release]
      [--mock]

本物の Claude のキー（ANTHROPIC_API_KEY）が無ければ起動を止める（dev でも。11/6 唐川さんの決定）。
モックで試すときだけ --mock を付ける（.env を読まず、解釈の係も説明の係もモック。filming・release とは一緒に使えない）。

127.0.0.1 だけで待ち受ける。Chrome で表示された URL を開く。止めるときは Ctrl+C。
シミュレーションモード：元のジョブは変えない。承認ボタンで承認した候補だけを、合成ジョブに限って
output/sim/ に新しいジョブ名で出力する。実機用の出力・転送・ロボットの起動はしない。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

from .. import buildinfo, claude_explain, claude_interpreter, claude_lead, launch_guard, sim, updown
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
    ap.add_argument(launch_guard.MOCK_FLAG, dest="mock", action="store_true",
                    help="モックで起動する（自動テスト・試し用。本物の Claude を呼ばない。.env を読まない。dev だけ）")
    args = ap.parse_args(argv)
    channel = launch_guard.channel_of(args.channel)
    if args.mock:
        reason = launch_guard.refuse_mock(channel)
        if reason is not None:
            print(reason, file=sys.stderr, flush=True)
            return launch_guard.EXIT_REFUSED
        interp = claude_interpreter.MockInterpreter()
        explainer = claude_explain.Explainer.from_env(env={}, model=args.explain_model)  # キーなし（通信しない）
    else:
        # .env から ANTHROPIC_API_KEY・ANTHROPIC_MODEL を読む（環境変数があればそちら）
        dotenv_status = claude_interpreter.load_dotenv(args.env_file)
        if dotenv_status == claude_interpreter.DOTENV_UNREADABLE:
            print(claude_interpreter.DOTENV_UNREADABLE_MESSAGE, flush=True)
        interp = claude_interpreter.from_env()
        explainer = claude_explain.Explainer.from_env(model=args.explain_model)
        # 本物の Claude（解釈の係と説明の係の両方）が使えなければ止める。黙ってモックで動かさない
        reason = launch_guard.refuse_real(channel, interp, explainer, dotenv_status)
        if reason is not None:
            print(reason, file=sys.stderr, flush=True)
            return launch_guard.EXIT_REFUSED
    try:
        srv = make_server(port=args.port, interpreter=interp, updown_config=args.updown_config, robot=args.robot,
                          explainer=explainer, channel=args.channel)
    except ValueError as e:  # チャンネルが3つ以外（環境変数 VT_CHANNEL）・タグ前の release：起動しない
        ap.error(str(e))
    if getattr(srv, "build", None) is not None:
        print(f"版：{srv.build.detail}", flush=True)
    # Claude の言い添え（指示の道の答えの前の一言）：本物の Claude で、キーがあるときだけ。VOICE_TEACHING_CLAUDE_LEAD=0 で使わない
    srv.flow.claude_lead = claude_lead.enabled(interp, explainer, os.environ)
    if args.mock:
        print(launch_guard.MOCK_BANNER, flush=True)
    print(f"解釈：{'本物の Claude（' + interp.model + '）' if interp.kind == 'claude' else '偽のClaude（モック）'}", flush=True)
    print(f"Claude の言い添え：{'使う' if srv.flow.claude_lead else '使わない'}", flush=True)
    ask = ('キーあり・' + explainer.model if explainer.has_key
           else 'モック（キーを使いません）' if args.mock else 'APIキーが設定されていません')
    print(f"Claude に聞く（読み取りだけ）：{ask}", flush=True)
    if interp.kind == "claude" and callable(getattr(interp, "understand", None)):
        # 意味理解の最初の1回は、形（JSON Schema）の準備で遅い（実測 約38秒）。起動のときに1回だけ、作り文で先に呼んでおく。
        # 画面の Session・候補には何もしない。結果は使わない（失敗しても起動は止めない。画面で話したときに理由を出す）
        def _warm() -> None:
            from .. import nl_understand as nu  # noqa: PLC0415
            try:
                interp.understand(nu.build_state(["C00001"], "C00001", None, None, []), "Zプラス5ミリ")
                print("意味理解（Claude）：準備ができました", flush=True)
            except Exception as ex:  # noqa: BLE001
                print(f"意味理解（Claude）：準備の呼び出しが使えませんでした（{ex}）", flush=True)
        threading.Thread(target=_warm, daemon=True).start()
        print("意味理解（Claude）：準備中（最初の1回だけ時間がかかります）", flush=True)
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
