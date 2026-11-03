"""Chrome での通しの確かめ用：本物の Claude を呼ばずに、画面を起動する（台本の答えのモック）。

起動（リポジトリの直下で）:
    .venv の python で  python tools/demo_mock.py  [--port 8765] [--updown-config records/updown.json]

- .env は読まない。ANTHROPIC_API_KEY が環境にあっても使わない（解釈の係も会話の係もモック）。通信しない
- 会話の答えは、利用者の文から決まった台本で作る（本物の Claude ではない）。画面の話し手は「モック（台本の答え…）」と出る
  - 回数の言葉（「3回にして」）→ 回数の変更の希望（数は利用者の文から。画面にはプログラムの定型文が出る）
  - 人が教えた意味があれば → その言葉で説明する（「ボタンを押してる」動きをくり返しています）
  - それ以外 → 動きの説明と「何の作業か、教えてもらえますか？」
- 安全の決まりはアプリのまま（答えの検査・状態の1行・承認はボタンだけ・出力は合成ジョブだけ）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from voice_teaching import claude_explain as cx  # noqa: E402
from voice_teaching import updown  # noqa: E402
from voice_teaching.claude_interpreter import MockInterpreter  # noqa: E402
from voice_teaching.web.server import DEFAULT_PORT, make_server  # noqa: E402

MOCK_MODEL = "claude-mock-script"  # 画面では「モック（台本の答え・本物の Claude ではありません）」と出す（explain.js）
_COUNT = re.compile(r"([0-9０-９]+)\s*回")
_ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def _texts(message: dict) -> list[dict]:
    out = []
    for part in message.get("content", []):
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                out.append(json.loads(part["text"]))
            except (KeyError, ValueError):
                pass
    return out


class ScriptClient:
    """messages.create だけを持つ偽の client。送られた事実（job_facts）と利用者の文から、決まった答えを作る。"""

    def __init__(self):
        self.messages = self
        self.n = 0

    def create(self, **kw):
        msgs = kw.get("messages") or []
        facts, taught, question = {}, [], ""
        for m in msgs:
            for t in _texts(m):
                facts = t.get("job_facts", facts)
                taught = t.get("taught_by_human", taught)
                question = t.get("question", question)
        repeats = facts.get("repeats") or []
        block = list(range(repeats[0]["first_line"], repeats[0]["last_line"] + 1)) if repeats else []
        m = _COUNT.search(question.translate(_ZEN))
        if m and repeats:
            data = {"reply": "回数を変えたいのですね。", "evidence_lines": block, "intent": "change_request",
                    "change_request": {"kind": "repeat", "from_count": repeats[0]["count"], "to_count": int(m.group(1))}}
        elif taught:
            meaning = taught[-1].get("meaning") or taught[-1].get("text") or ""
            data = {"reply": f"教えてもらった「{meaning}」の動きをくり返しています。ほかに知りたいことはありますか？",
                    "evidence_lines": block, "intent": "explain", "change_request": None}
        else:
            data = {"reply": "最初の位置から動いて、同じ場所をめぐる動きをくり返し、最後に別の位置へ移ります。"
                             "何の作業か、教えてもらえますか？",
                    "evidence_lines": block, "intent": "explain", "change_request": None}
        self.n += 1
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(type="tool_use", id=f"toolu_mock{self.n}", name="answer", input=data)],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0, cache_creation=None))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ボイスティーチング（モックの通し・本物の Claude を呼ばない・127.0.0.1 だけ）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--updown-config", default=str(updown.DEFAULT_PATH))
    ap.add_argument("--robot", default=updown.DEFAULT_ROBOT)
    args = ap.parse_args(argv)
    cx.PRICES[MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    explainer = cx.Explainer("mock-no-network", MOCK_MODEL, client=ScriptClient(),
                             usage_log=cx.UsageLog(REPO / "records" / "mock_usage.jsonl"))
    srv = make_server(port=args.port, interpreter=MockInterpreter(), updown_config=args.updown_config,
                      robot=args.robot, explainer=explainer)
    print("モックの通し：解釈の係も会話の係もモック（本物の Claude は呼びません。.env は読みません）", flush=True)
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
