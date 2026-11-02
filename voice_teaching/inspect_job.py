"""読むだけの確認用の道具：ジョブの形式・位置・使っている命令・検査の結果を表示し、2Dの図をHTMLで出す。

使い方（実機のジョブは PC の中だけで扱う。リポジトリに入れない・Claude に送らない）:
    python -m voice_teaching.inspect_job ジョブ.JBI [--out 出力フォルダ]

- ジョブは書き換えない。JBI は書き出さない。出すのは HTML（既定は git に入らない output/）だけ。
- 「検査」の欄は、試しに X を +1mm／速度を +1 した場合に検査プログラムが通すかどうか。変更案は作らない。
- 図は位置の確認用。干渉・到達性・安全を保証しない。
"""
from __future__ import annotations

import argparse
import html as _html
import sys
from pathlib import Path

from . import jbi

FIGURE_NOTE = (
    "この図は位置と順番を確かめるためのものです。この図で離れて見えても、当たらないとは限りません。"
    "腕やツールの根元は描いていません。図の向きが実機の向きと合っているかは確認していません。"
)


def _line(job: jbi.Job, index: int) -> str:
    return job.lines[index].rstrip("\r\n")


def _shift_verdict(job: jbi.Job, name: str) -> str:
    try:
        job.plan_shift(name, "X", 1.0)
    except jbi.JbiError as e:
        return f"断る：{e}"
    return "変更の検査を通る（試しに X を +1mm）"


def _speed_verdict(job: jbi.Job, ins: jbi.Instruction) -> str:
    if ins.command != "MOVL":
        return "対象外（MOVLでない）"
    if ins.speed is None:
        return "断る：速度が数値で書かれていない"
    try:
        job.plan_speed(ins.index, ins.speed + 1)
    except jbi.JbiError as e:
        return f"断る：{e}"
    return "変更の検査を通る（試しに +1）"


def _line_ending(job: jbi.Job) -> str:
    text = job.render()
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "CRLF" if crlf and not lf else "LF" if lf and not crlf else f"混在（CRLF {crlf}・LF {lf}）"


def _moves(job: jbi.Job) -> list[jbi.Instruction]:
    return [i for i in job.instructions if i.position is not None]


def report(job: jbi.Job) -> str:
    out = [
        f"ジョブ名: {job.name}",
        f"文字コード: {job.encoding}　改行: {_line_ending(job)}",
        f"位置の数: {len(job.positions)}　命令の行数: {len(job.instructions)}",
        f"すべての位置がロボット座標: {'はい' if job.is_robot_coordinates() else 'いいえ'}",
        "",
        f"■ 確認できない点（1件でもあれば変更はすべて断る）: {len(job.problems)}件",
    ]
    out += [f"  - {p}" for p in job.problems] or ["  なし"]
    out += ["", "■ 位置"]
    for name, pos in job.positions.items():
        users = job.users_of(name)
        out.append(f"{pos.line_index + 1}行目 {_line(job, pos.line_index)}")
        out.append(f"    形式: POSTYPE={pos.postype} RECTAN={'あり' if pos.rectan else 'なし'} "
                   f"TOOL={pos.tool} USER={pos.user} RCONF={pos.rconf}"
                   f"{'（分からない指示行のあと）' if pos.after_unknown_directive else ''}"
                   f" → {'ロボット座標' if pos.is_robot_coordinates() else 'ロボット座標でない'}")
        out.append(f"    使っている行: {len(users)}件" + ("（共用）" if len(users) > 1 else ""))
        out += [f"      {u.index + 1}行目 {u.text}" for u in users]
        out.append(f"    検査: {_shift_verdict(job, name)}")
    out += ["", "■ 移動命令"]
    for ins in _moves(job):
        out.append(f"{ins.index + 1}行目 {ins.text}")
        out.append(f"    速度: {ins.speed_raw}　PL: {ins.pl}　CR: {'あり' if ins.has_cr else 'なし'}"
                   f"　検査: {_speed_verdict(job, ins)}")
    return "\n".join(out)


# ---- 2Dの図 ----
def _svg(job: jbi.Job, ax: int, ay: int, title: str) -> str:
    pts = {n: p for n, p in job.positions.items() if p.is_robot_coordinates()}
    if not pts:
        return "<p>ロボット座標でない位置だけのため、図は描けません。</p>"
    xs = [p.values[ax] for p in pts.values()]
    ys = [p.values[ay] for p in pts.values()]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    w = h = 420
    pad = 50
    k = (w - 2 * pad) / span
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    def sx(v: float) -> float:
        return w / 2 + (v - cx) * k

    def sy(v: float) -> float:
        return h / 2 - (v - cy) * k  # 画面は下向きが正なので反転

    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{_html.escape(title)}">',
             f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fafafa" stroke="#ccc"/>']
    prev = None
    for ins in _moves(job):
        p = pts.get(ins.position)
        if p is None:
            prev = None
            continue
        if prev is not None:
            plain = ins.command == "MOVL" and ins.pl is None and not ins.has_cr
            dash = "" if plain else ' stroke-dasharray="6 4"'
            parts.append(f'<line x1="{sx(prev.values[ax]):.1f}" y1="{sy(prev.values[ay]):.1f}" '
                         f'x2="{sx(p.values[ax]):.1f}" y2="{sy(p.values[ay]):.1f}" stroke="#4a6fa5" stroke-width="2"{dash}/>')
        prev = p
    for n, p in pts.items():
        x, y = sx(p.values[ax]), sy(p.values[ay])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#d0602a"/>')
        parts.append(f'<text x="{x + 8:.1f}" y="{y - 8:.1f}" font-size="12">{_html.escape(n)}</text>')
    names = "XYZ"
    parts.append(f'<text x="{w - 40}" y="{h - 10}" font-size="12">{names[ax]} →</text>')
    parts.append(f'<text x="8" y="16" font-size="12">↑ {names[ay]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def html(job: jbi.Job, source_name: str) -> str:
    e = _html.escape
    rows = []
    for name, pos in job.positions.items():
        users = job.users_of(name)
        fmt = "ロボット座標" if pos.is_robot_coordinates() else "ロボット座標でない"
        rows.append(
            f"<tr><td>{e(name)}</td><td><code>{e(_line(job, pos.line_index))}</code></td><td>{fmt}"
            f"（TOOL {e(str(pos.tool))}）</td><td>{'<br>'.join(f'{u.index + 1}行目 {e(u.text)}' for u in users) or 'なし'}"
            f"</td><td>{e(_shift_verdict(job, name))}</td></tr>")
    problems = "".join(f"<li>{e(p)}</li>" for p in job.problems) or "<li>なし</li>"
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{e(source_name)} の確認</title>
<style>body{{font-family:sans-serif;margin:20px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px;font-size:13px;vertical-align:top}}
.note{{background:#fff4e0;padding:8px;border:1px solid #e0b060}}.figs{{display:flex;gap:16px;flex-wrap:wrap}}</style></head><body>
<h1>{e(source_name)}（ジョブ名 {e(job.name)}）</h1>
<p class="note">{e(FIGURE_NOTE)} 実線は MOVL（PL・CRなし）の区間、点線はそれ以外（目安）。このページは読むだけで、ジョブは書き出しません。</p>
<p>文字コード {e(job.encoding)}・改行 {e(_line_ending(job))}・すべての位置がロボット座標：{'はい' if job.is_robot_coordinates() else 'いいえ'}</p>
<h2>確認できない点（1件でもあれば変更はすべて断る）</h2><ul>{problems}</ul>
<h2>図</h2><div class="figs"><div><h3>上から（X・Y）</h3>{_svg(job, 0, 1, "上から")}</div>
<div><h3>横から（X・Z）</h3>{_svg(job, 0, 2, "横から")}</div></div>
<h2>位置</h2><table><tr><th>位置</th><th>ファイルの行</th><th>形式</th><th>使っている行</th><th>検査（試しに X +1mm）</th></tr>
{''.join(rows)}</table>
<h2>文字での一覧</h2><pre>{e(report(job))}</pre>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ジョブを読むだけで確かめる（書き換えない）")
    ap.add_argument("path")
    ap.add_argument("--out", default="output", help="HTMLを置くフォルダ（既定は git に入らない output/）")
    args = ap.parse_args(argv)
    src = Path(args.path)
    try:
        job = jbi.load(src)
    except (jbi.JbiError, OSError) as e:
        print(f"{src} は読めません：{e}")
        return 1
    print(report(job))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{src.stem}.html"
    dest.write_text(html(job, src.name), encoding="utf-8")
    print(f"\n図と一覧: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
