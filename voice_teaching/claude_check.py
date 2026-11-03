"""本物の Claude を使う前の自己点検。起動: python -m voice_teaching.claude_check [--call]

- ふだん（--call なし）は通信しない。SDK のクライアントも作らない。
  .env とキーが「あり／なし」か、使うモデル、SDK（anthropic）を読み込めるか、例の指示で送る内容（キーは伏せる）を表示する。
  キーの中身・長さ・一部は表示しない。
- --call は、本物の Claude に1回送る（料金がかかる。混雑・接続失敗のときは SDK が最大1回やり直す）。
  キーが無い・使えない文字・モデル名が正しくない・.env が読めないときは実行しない。
  料金の注意を出し、yes と入力したときだけ送る（入力を省く方法は作らない）。架空のジョブ（samples/dummy/DEMO_PLACE.JBI）を
  画面と同じ流れ（WebFlow）に通し、結果は照らし合わせ（check_proposal）を通った候補か、止めた理由だけを表示する。
  承認・出力・転送・ロボットの起動はしない。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Callable, Mapping, TextIO

from . import claude_interpreter as ci
from . import jbi, sim

REDACTED = "（伏せています）"
SAMPLE_JOB = sim.SAMPLES_DIR / "DEMO_PLACE.JBI"
# 上下の向きの設定が無くても届くよう、軸と符号を明示した指示にする（CHECKER_026 M2：未確認なら「上に」は Claude に送る前に聞き返す）
SAMPLE_UTTERANCE = "この位置をZのプラス10mm移動して"
SAMPLE_SELECTED = "C00004"
COST_WARNING = ("注意：--call は本物の Claude API に1回送ります（混雑・接続失敗のときは SDK が最大1回やり直します）。"
                "料金がかかります（キーの持ち主の請求になります）。送るのは上の「送る内容」と同じものです")


def _sdk_status() -> tuple[bool, str]:
    try:
        mod = importlib.import_module("anthropic")
    except Exception:
        return False, "読み込めません（pip install -r requirements.txt を確かめてください）"
    return True, f"読み込めます（anthropic {getattr(mod, '__version__', '?')}）"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, out: TextIO | None = None,
         input_fn: Callable[[str], str] = input,
         make_interpreter: Callable[[str, str], object] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本物の Claude を使う前の自己点検（ふだんは通信しない）")
    ap.add_argument("--env", default=str(sim.REPO_ROOT / ".env"), help=".env の場所（既定はリポジトリ直下）")
    ap.add_argument("--call", action="store_true", help="本物の Claude に1回だけ送る（料金がかかる）")
    args = ap.parse_args(argv)
    out = out or sys.stdout

    def say(text: str = "") -> None:
        print(text, file=out, flush=True)

    env = dict(os.environ if env is None else env)
    from_env_var = bool((env.get("ANTHROPIC_API_KEY") or "").strip())
    dotenv_exists = os.path.isfile(args.env)
    dotenv_status = ci.load_dotenv(args.env, env)
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    model = (env.get("ANTHROPIC_MODEL") or "").strip() or ci.DEFAULT_MODEL
    model_src = "ANTHROPIC_MODEL" if (env.get("ANTHROPIC_MODEL") or "").strip() else "既定"
    model_ok = ci.model_is_valid(model)
    usable = ci.key_is_usable(key)
    sdk_ok, sdk_text = _sdk_status()

    say("Claude の自己点検（この点検では通信はしていません）")
    say(f".env ファイル：{'あり' if dotenv_exists else 'なし'}")
    if dotenv_status == ci.DOTENV_UNREADABLE:
        say("  " + ci.DOTENV_UNREADABLE_MESSAGE)
    if key:
        src = "環境変数" if from_env_var else ".env"
        say(f"ANTHROPIC_API_KEY：あり（{src}）")
        if not usable:
            say("  キーに使えない文字（全角・空白など）が含まれています。.env.example の見本の文のままかもしれません。"
                "この状態では送りません")
    else:
        say("ANTHROPIC_API_KEY：なし")
    if model_ok:
        say(f"モデル：{model}（{model_src}）")
    else:
        say("モデル：ANTHROPIC_MODEL の書き方が正しくありません（値は表示しません。docs/CLAUDE_API.md を見てください）。"
            "この状態では送りません")
    say(f"SDK：{sdk_text}")
    if key:
        say("画面での解釈：本物の Claude（失敗してもモックへは切り替えず、止めて理由を出します）")
    else:
        say("画面での解釈：偽のClaude（モック）。本物の Claude は呼びません")

    positions = list(jbi.load(SAMPLE_JOB).positions)
    shown = {
        "client": {"api_key": REDACTED, "timeout": ci.TIMEOUT_SECONDS, "max_retries": ci.MAX_RETRIES},
        "request": ci.build_request(model if model_ok else REDACTED, SAMPLE_UTTERANCE, SAMPLE_SELECTED, positions),
    }
    say(f"送る内容（例：架空のジョブ {SAMPLE_JOB.name}、{SAMPLE_SELECTED} を選んで「{SAMPLE_UTTERANCE}」）：")
    say(json.dumps(shown, ensure_ascii=False, indent=2))
    say("送らないもの：ジョブの中身（命令の行・座標値・ジョブ名）、作業ログ。APIキーは認証のためだけに使います（ここでは伏せています）")

    if not args.call:
        return 0

    say()
    if dotenv_status == ci.DOTENV_UNREADABLE and not key:
        say(".env を読めないので、--call は実行しません")
        return 2
    if not key:
        say("APIキーが無いので、--call は実行しません")
        return 2
    if not usable:
        say("APIキーに使えない文字があるので、--call は実行しません")
        return 2
    if not model_ok:
        say("モデル名（ANTHROPIC_MODEL）の書き方が正しくないので、--call は実行しません")
        return 2
    if make_interpreter is None and not sdk_ok:
        say("SDK（anthropic）を読み込めないので、--call は実行しません")
        return 2
    say(COST_WARNING)
    try:  # 料金の確かめは、いつも入力してもらう（省く方法は作らない）
        answer = (input_fn("続けるなら yes と入力してください：") or "").strip().lower()
    except (EOFError, KeyboardInterrupt):  # 入力が無い（パイプ・自動実行）ときは送らない
        answer = ""
    if answer != "yes":
        say("送りませんでした")
        return 3

    from .web.flow import WebFlow  # noqa: PLC0415（--call のときだけ読む）
    interp = (make_interpreter or ci.ClaudeInterpreter)(key, model)
    w = WebFlow(interpreter=interp)
    sid = w.load(SAMPLE_JOB.read_bytes())["session_id"]
    w.select(sid, SAMPLE_SELECTED)
    r = w.hear(sid, SAMPLE_UTTERANCE, source="text")
    say(f"結果：{r['status']}")
    if r.get("message"):
        say(f"  {r['message']}")
    cand = r["state"]["candidate"]
    if cand:
        say(f"  変更候補（照らし合わせを通ったもの。承認・出力はしていません）：{cand['target']} {cand['delta']}")
    return 0 if r["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
