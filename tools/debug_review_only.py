"""デバッグ専用の起動（2026-11-05 唐川さんの指示）：承認と出力を、サーバー側で必ず断る。

P0（H02・H16・ジョブの読み直しのあとに古い候補が残る）が未解決の間、最新版の会話を試すためだけに使う。
- 候補を作る・3D で変更前後を比べる・会話（本物の Claude）は、ふつうの起動と同じ
- /api/approve と /api/export は、どのセッション・どの候補でも断る（画面のボタンを隠すだけではない）
- 状態の can_approve・can_export は常に False（画面の承認・出力のボタンは押せない）
- 製品のコード（voice_teaching/）は変えない。このファイルの起動のときだけ、WebFlow の関数を差し替える

起動（リポジトリの直下で）:
    python tools/debug_review_only.py --port 8805 --env-file C:/アプリ/voice-teaching/.env
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from voice_teaching import claude_explain, claude_interpreter, sim, updown  # noqa: E402
from voice_teaching.web.flow import WebFlow  # noqa: E402
from voice_teaching.web.server import DEFAULT_PORT, make_server  # noqa: E402

REFUSED = ("デバッグ専用の起動のため、承認と出力はできません（未解決の P0 があるため、サーバーで断っています）。"
           "変更候補と 3D の変更前後は確かめられます")


def lock_down(flow: WebFlow) -> WebFlow:
    """この flow の承認・出力を断る形に差し替える（そのインスタンスだけ）。"""
    original_state = flow._state

    def approve(sid=None, approver=None, *args, **kwargs) -> dict:
        return {"ok": False, "error": REFUSED, "debug_review_only": True}

    def export(sid=None, *args, **kwargs) -> dict:
        return {"ok": False, "error": REFUSED, "debug_review_only": True}

    def _approve_repeat(*args, **kwargs) -> dict:
        return {"ok": False, "error": REFUSED, "debug_review_only": True}

    def _state(sid, e) -> dict:
        st = original_state(sid, e)
        st["can_approve"] = False
        st["can_export"] = False
        st["debug_review_only"] = True
        return st

    flow.approve = approve
    flow.export = export
    flow._approve_repeat = _approve_repeat
    flow._state = _state
    flow.debug_review_only = True
    return flow


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ボイスティーチング：デバッグ専用（承認・出力をサーバーで断る。127.0.0.1 だけ）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--updown-config", default=str(updown.DEFAULT_PATH))
    ap.add_argument("--robot", default=updown.DEFAULT_ROBOT)
    ap.add_argument("--env-file", default=str(sim.REPO_ROOT / ".env"))
    ap.add_argument("--explain-model", default=None)
    args = ap.parse_args(argv)
    if claude_interpreter.load_dotenv(args.env_file) == claude_interpreter.DOTENV_UNREADABLE:
        print(claude_interpreter.DOTENV_UNREADABLE_MESSAGE, flush=True)
    interp = claude_interpreter.from_env()
    explainer = claude_explain.Explainer.from_env(model=args.explain_model)
    srv = make_server(port=args.port, interpreter=interp, updown_config=args.updown_config, robot=args.robot,
                      explainer=explainer)
    lock_down(srv.flow)
    print("デバッグ専用：承認と出力はサーバーで断ります（候補・3D・会話は使えます）", flush=True)
    print(f"解釈：{'本物の Claude（' + interp.model + '）' if interp.kind == 'claude' else '偽のClaude（モック）'}", flush=True)
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
