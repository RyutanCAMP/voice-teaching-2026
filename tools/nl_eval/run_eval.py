"""自然言語の評価（本番候補 v1.0）。同じケースを「本物の Claude」か「モック（今までの道だけ）」で流し、別のフォルダに書く。

  python tools/nl_eval/run_eval.py --mode mock
  python tools/nl_eval/run_eval.py --mode live --env-file <.env の場所>   （本物の Claude を呼ぶ。費用がかかる）

- 実機には何もしない。JOB は合成ジョブ（samples/dummy）だけ。SIM の出力は一時フォルダ。
- 記録に残すのは、ケースの ID・発話（テストの作り文）・アプリの返事の種類と文（先頭だけ）・最後の候補の位置・軸・量・
  作業ログの「Claude の読み」の要約だけ。API キー・ヘッダー・座標の値の一覧は残さない（書く前にキーらしい文字列を探して止める）。
- 数えるもの：合格（期待どおり）・誤った候補（候補ができたのに期待と違う／作ってはいけないのに作った）・API の失敗（別に数える）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice_teaching import claude_explain as cx  # noqa: E402
from voice_teaching import claude_interpreter as ci  # noqa: E402
from voice_teaching import sim  # noqa: E402
from voice_teaching.web.flow import WebFlow  # noqa: E402

_SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}|x-api-key|authorization", re.I)


def parse_expect(e: str):
    if e.startswith("cand:"):
        _, pos, axis, mm = e.split(":")
        return ("cand", pos, axis, float(mm))
    if e.startswith("safe:cand:"):
        _, _, pos, axis, mm = e.split(":")
        return ("safe", pos, axis, float(mm))  # その候補か、候補なし（聞き返し）なら合格
    return (e,)


def run_case(w: WebFlow, data: bytes, case: dict, default_select: str | None) -> dict:
    sid = w.load(data)["session_id"]
    sel = case.get("select", default_select)
    if sel:
        w.select(sid, sel)
    replies, api_error = [], False
    last_choices: list = []
    gen_before_last = None
    turns = case["turns"]
    for i, t in enumerate(turns):
        if i == len(turns) - 1:
            gen_before_last = w.candidate_gen_of(sid)
        if t == "@yes":
            ch = [c for c in last_choices if c["label"] == "合っている"]
            r = w.hear(sid, ch[0]["text"]) if ch else {"status": "stop", "message": "（［合っている］のボタンが出ていない）"}
            h = r
        elif t == "@approve":
            r = w.approve(sid, "評価", candidate_gen=w.candidate_gen_of(sid))
            h = {"status": "ok" if r.get("ok") else "stop", "message": r.get("error") or "承認"}
        elif t == "@export":
            r = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
            h = {"status": "ok" if r.get("ok") else "stop", "message": r.get("error") or r.get("file_name") or ""}
        elif t == "@undo":
            r = w.undo(sid)
            h = {"status": r.get("status", "ok"), "message": r.get("message", "")}
        elif t.startswith("@select:"):
            r = w.select(sid, t.split(":", 1)[1])
            h = {"status": r.get("status", "ok"), "message": r.get("message", "")}
        else:
            r = w.talk(sid, t)
            h = r.get("hear") or {}
            if not h and r.get("ask"):
                h = {"status": "explain", "message": (r["ask"].get("reply") or r["ask"].get("error") or "")}
        last_choices = h.get("choices") or []
        msg = h.get("message") or ""
        if any(m.split("。")[0] in msg for m in (ci.AUTH, ci.NETWORK, ci.BUSY, ci.MODEL_NOT_FOUND, ci.FAILED, ci.REJECTED)):
            api_error = True  # 解釈の係・意味理解の接続の失敗（説明の係のキーなしの案内は数えない）
        replies.append({"said": t, "status": h.get("status"), "message": msg[:160],
                        "choices": [c["label"] for c in last_choices]})
    e = w._entries[sid]
    c = e.session.candidate
    cand = None
    if c is not None and c.kind == "shift":
        pos, axis = c.target.split()
        base = c.baseline_value if c.baseline_value is not None else c.before_value
        cand = {"position": pos, "axis": axis, "delta_mm": round(c.after_value - base, 3),
                "approvable": bool(w.state(sid)["state"].get("can_approve")), "approved": e.approval is not None,
                "hold": e.hold}
    reads = [t for k, t in e.session.log if k == "Claude の読み"]
    exp = parse_expect(case["expect"])
    ok, wrong = False, False
    if exp[0] == "cand":
        match = (cand is not None and cand["position"] == exp[1] and cand["axis"] == exp[2]
                 and abs(cand["delta_mm"] - exp[3]) < 1e-6 and not cand["approved"])
        ok = match
        wrong = cand is not None and not match
    elif exp[0] == "safe":
        match = (cand is not None and cand["position"] == exp[1] and cand["axis"] == exp[2]
                 and abs(cand["delta_mm"] - exp[3]) < 1e-6 and not cand["approved"])
        ok = cand is None or match
        wrong = cand is not None and not match
    elif exp[0] == "kept":
        st = w.state(sid)["state"]
        ok = e.approval is not None and not e.hold and bool(st.get("can_export"))
        wrong = False
    elif exp[0] == "nocand":
        ok = cand is None
        wrong = cand is not None
    elif exp[0] == "noappr":
        ok = cand is None or not cand["approvable"]
        wrong = cand is not None and cand["approvable"]
    elif exp[0] == "void":
        old_ok = (gen_before_last is not None and w.approve(sid, "評価", candidate_gen=gen_before_last).get("ok")) if gen_before_last is not None else False
        ok = not old_ok and (e.approval is None or e.hold)
        wrong = bool(old_ok)
    return {"id": case["id"], "cat": case["cat"], "expect": case["expect"], "ok": bool(ok), "wrong_candidate": bool(wrong),
            "api_error": api_error, "candidate": cand, "replies": replies, "claude_reads": reads}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("mock", "live"), required=True)
    ap.add_argument("--env-file")
    ap.add_argument("--cases", default=str(Path(__file__).with_name("cases_v1.json")))
    ap.add_argument("--out")
    ap.add_argument("--only")
    ap.add_argument("--budget-usd", type=float, default=2.0,
                    help="本物の Claude のときの料金の上限（この評価の記録 output/nl-eval/api_usage.jsonl の累計。既定 2ドル）")
    args = ap.parse_args()
    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    data = (ROOT / "samples" / "dummy" / spec["job"]).read_bytes()
    sim.DEFAULT_OUT_DIR = Path(tempfile.mkdtemp(prefix="nl-eval-sim-"))
    if args.mode == "live":
        env: dict = {}
        if not args.env_file:
            print("--env-file が要ります（本物の Claude）"); return 2
        ci.load_dotenv(args.env_file, env)
        interp = ci.from_env(env)
        if interp.kind != "claude":
            print("キーが読めないため止めます（モックにしません）"); return 2
        # 評価の料金も記録し、上限を超えそうなら呼ばない（11/7。API は Max の枠とは別の費用）
        usage = cx.UsageLog(ROOT / "output" / "nl-eval" / "api_usage.jsonl")
        interp.meter = cx.ApiMeter(usage, args.budget_usd)
        print(f"評価の料金：これまで {usage.total():.4f} ドル・上限 {args.budget_usd} ドル")
    else:
        interp = ci.MockInterpreter()
    # 説明の係（読み取りだけ）はキーなし：本物の Claude を呼ばない（評価するのは変更の候補。説明の文は評価しない）
    explainer = cx.Explainer.from_env({})
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    out = Path(args.out) if args.out else ROOT / "output" / "nl-eval" / f"{args.mode}-{sha}{'-dirty' if dirty else ''}"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    t0 = time.time()
    for case in spec["cases"]:
        if args.only and not re.search(args.only, case["id"]):
            continue
        w = WebFlow(interpreter=interp, updown_config=None, explainer=explainer)
        t = time.time()
        row = run_case(w, data, case, spec.get("select"))
        row["seconds"] = round(time.time() - t, 2)
        rows.append(row)
        print(f"{row['id']} {'OK ' if row['ok'] else 'NG '}{'WRONG ' if row['wrong_candidate'] else ''}"
              f"{'APIERR ' if row['api_error'] else ''}{row['seconds']}s {row['candidate']}")
    text = json.dumps({"mode": args.mode, "model": getattr(interp, "model", None), "sha": sha, "dirty": bool(dirty),
                       "cases": rows}, ensure_ascii=False, indent=1)
    if _SECRET.search(text):
        print("記録にキーらしい文字列があるため、書きません"); return 3
    (out / "results.json").write_text(text, encoding="utf-8")
    n = len(rows)
    okn = sum(r["ok"] for r in rows)
    wrong = sum(r["wrong_candidate"] for r in rows)
    api = sum(r["api_error"] for r in rows)
    cats: dict = {}
    for r in rows:
        c = cats.setdefault(r["cat"], [0, 0, 0])
        c[0] += 1; c[1] += r["ok"]; c[2] += r["wrong_candidate"]
    lines = [f"# 自然言語の評価（{args.mode}・{getattr(interp, 'model', None) or 'モック'}・{sha}{'-dirty' if dirty else ''}）", "",
             f"- ケース {n}・合格 {okn}（{okn / n:.0%}）・**誤った候補 {wrong}**・API の失敗 {api}・時間 {time.time() - t0:.0f}秒", "",
             "| 分類 | 件数 | 合格 | 誤った候補 |", "|---|---|---|---|"]
    lines += [f"| {k} | {v[0]} | {v[1]} | {v[2]} |" for k, v in cats.items()]
    lines += ["", "| ID | 期待 | 結果 | 最後の候補 |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['expect']} | {'OK' if r['ok'] else 'NG'}{'・誤った候補' if r['wrong_candidate'] else ''} | "
              f"{'' if not r['candidate'] else r['candidate']['position'] + ' ' + r['candidate']['axis'] + ' ' + format(r['candidate']['delta_mm'], 'g')} |"
              for r in rows]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.mode == "live":
        lines.insert(3, f"- 評価の料金の累計（output/nl-eval/api_usage.jsonl）：{interp.meter.usage_log.total():.4f} ドル")
    print("\n".join(lines[:4]))
    print("書いた場所：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
