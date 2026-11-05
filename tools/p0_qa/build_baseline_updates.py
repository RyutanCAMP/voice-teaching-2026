"""意図して変わった件だけの、比べる元の更新（overlay）を作る。元の録音・case_verdicts.json は書き換えない。
使い方：python build_updates.py <run名> <commit>（cwd は vt-nl-demo）"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

run, commit = sys.argv[1], sys.argv[2]
B = Path("output/p0-20261105")
HERE = Path("tools/p0_qa")
spec = importlib.util.spec_from_file_location("sem", HERE / "portable/semantics.py")
sem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sem)
base = json.loads((HERE / "reports/20261105/case_verdicts.json").read_text(encoding="utf-8"))
ref = {c["id"]: c for c in base["cases"]}


def recs(name):
    out = {}
    for tag in ["base", "extra", "context", "fraction", "recaptured-context"]:
        p = B / f"{name}-{tag}" / "results.json"
        if p.exists():
            for r in json.loads(p.read_text(encoding="utf-8")).get("cases", []):
                out[r["id"]] = r
    return out


now = recs(run)
REASON_SHOWN = ("動きの説明の「〜て終わります」を、状態の言い切りと見なして隠していた（0673f15・e356855）。"
                "動く大きさの言葉の無い、動きの順番の説明として表示するようにした")
CASES = {
    "A02": REASON_SHOWN, "A08": REASON_SHOWN, "A13": REASON_SHOWN, "A14": REASON_SHOWN, "A38": REASON_SHOWN,
    "A48": REASON_SHOWN, "H03": REASON_SHOWN,
    "C12": "打ち消しの言葉を、言葉の切れ端（「り変更しないで」）ではなく、まとまり全体（「やっぱり変更しないで」）で引用する"
           "（cd064d1・a78d6cf）。止めること・候補の扱いは変わらない",
    "D14": "上下の向きが未確認のときの会話の聞き返しを、意図を受け止めてから分からないことだけを聞く文にした"
           "（a08c740。唐川さんの決定 11/5）。状態の表示の文・判定・候補なしは変わらない",
}


NOTES = {
    "A13": "約束は「最後の命令先 C00004 を説明し、C00000 と区別する」。新しい文は位置番号を言わない（Claude の文に数を入れない決まり）。約束を満たすかは QA が見る",
    "H03": "約束は「変更前後の比較を表示できる状態へ進む」。新しい文は元のジョブの説明だけで、状態は変わらない。約束を満たすかは QA が見る",
    "A08": "前の FAIL の理由は「汎用補足だけ」。新しい文は Claude の説明（Z方向のマイナスへゆっくり動き、Zのプラスへ戻ります）。中身が合うかは QA が見る",
    "A02": "前は決まった文で PASS。新しい文は Claude の説明（ゆっくり動く場面もあります）",
}


def shown_text(r):
    p = r["response"]
    a, h = p.get("ask") or {}, p.get("hear") or {}
    return a.get("reply") or h.get("message") or ""


def old_text(layers):
    return (layers.get("ask") or {}).get("reply") or (layers.get("hear") or {}).get("message") or ""


updates = []
for cid, why in CASES.items():
    r = now[cid]
    layers = sem.projection(r)
    prev = ref[cid]
    assert layers != prev["layers"], cid
    updates.append(dict(
        id=cid, input=r["text"], contract=prev["contract"],
        previous_reviewed=prev["combined_after"],
        previous_layers_sha256=hashlib.sha256(json.dumps(prev["layers"], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        before_text=old_text(prev["layers"]), after_text=shown_text(r), reason=why,
        combined_after=None,  # QA が内容を確かめて決める（developer は決めない）
        qa_confirmed=False, qa_confirmed_by=None,
        developer_note=NOTES.get(cid, ""),
        layers=layers))
doc = dict(
    rule=("意図して変わった件だけ、比べる元を更新する（唐川さんの決定 2026-11-05）。元の録音（保存した答え）と "
          "reports/20261105/case_verdicts.json は書き換えない。replay_all.py は、ここの layers と一致した件を、"
          "qa_confirmed が true なら combined_after（QA が決めて書く）、false なら UPDATED_PENDING_QA として数える。"
          "一致しなければ、元の比べる元で今どおり判定する"),
    approval="唐川さん 2026-11-05（QA の内容確認が条件。確認したら combined_after に PASS か FAIL を書き、qa_confirmed を true、qa_confirmed_by に誰が・いつを書く）",
    source_run=run, source_commit=commit, base_reviewed="reports/20261105/case_verdicts.json（ce7c981 で確認した比べる元）",
    updates=updates)
out = HERE / "reports/20261106_nl_demo/baseline_updates.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
for u in updates:
    print(u["id"], u["previous_reviewed"], "|", u["input"], "|", u["before_text"][:60], "=>", u["after_text"][:80])
