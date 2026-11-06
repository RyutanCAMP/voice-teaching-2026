"""本番候補 v1.0 の画面の流れ（本物の Claude・1920×1080）を撮る。サーバーは別に起動しておく（例 8901・--channel filming）。

  python tools/nl_eval/shots_rc.py --url http://127.0.0.1:8901/ --out docs/v1.0-rc/screens

JOB は合成ジョブ DEMO_RJ_XYZ.JBI。承認は画面のボタン（名前を入れて［承認する］）。出力は SIM 用（サーバーの output/sim）。
実機には何もしない。各場面で、画面に「v1.00-TEST」「filming」「SHA」が見えていないことも確かめる。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8901/")
    ap.add_argument("--out", default=str(ROOT / "docs" / "v1.0-rc" / "screens"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    bad = re.compile(r"v1\.00-TEST|filming|[0-9a-f]{7}(?:-dirty)?（|DEBUG")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        def shot(name: str, note: str) -> None:
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(out / f"{name}.png"))
            visible = pg.evaluate("() => document.body.innerText")
            leak = bad.findall(visible)
            reply = pg.evaluate("() => (document.querySelector('#reply-message') || {}).innerText || ''")
            icon = pg.get_attribute("#approval-icon", "aria-label") if pg.locator("#approval-icon").count() else None
            log.append({"shot": name, "note": note, "reply": reply[:300], "approval_icon": icon, "dev_info_visible": leak})
            print(name, "|", note, "|", reply[:90].replace("\n", " "), "| leak:", leak)

        def say(text: str) -> None:
            if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
                pg.click("#kbd-button")
            pg.wait_for_function("() => !document.querySelector('#text-send').disabled", timeout=60000)
            pg.fill("#text-input", text)
            n0 = pg.evaluate("() => document.querySelectorAll('#ask-turns > li').length")
            pg.click("#text-send")
            pg.wait_for_function(
                "(n) => document.querySelectorAll('#ask-turns > li').length > n && !document.querySelector('#ask-turns .thinking')",
                arg=n0, timeout=90000)
            pg.wait_for_timeout(800)

        pg.goto(args.url)
        pg.wait_for_timeout(2500)
        for sel in ("#opening-skip", "button:has-text('スキップ')"):
            if pg.locator(sel).count() and pg.locator(sel).first.is_visible():
                pg.locator(sel).first.click()
                break
        shot("00_start", "最初の画面")
        pg.set_input_files("#file-input", str(ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        shot("01_loaded", "JOB を読み込んだ（3D）")
        pg.locator("#position-chips .chip", has_text="C00004").first.click()
        shot("02_selected", "C00004 を選んだ")
        say("Zプラスに3センチ動かして")
        shot("03_over_limit", "3センチ（30mm）→ 20mm の上限で止まる")
        say("もう少し上に寄せたいな")
        shot("04_ask_amount", "あいまいな量 → 何ミリか聞き返す")
        say("5ミリ")
        shot("05_confirm_direction", "上の向き（未確認）をボタンで確かめる")
        pg.locator("#reply-message .choices button", has_text="合っている").first.click()
        pg.wait_for_function("() => document.querySelector('#open-confirm') && !document.querySelector('#open-confirm').disabled",
                             timeout=90000)
        shot("06_candidate", "変更候補（3D・差分）")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        shot("07_confirm", "確認表示（変更前→変更後・承認する人）")
        pg.fill("#approver-name", "唐川")
        pg.click("#approve-button")
        pg.wait_for_timeout(1500)
        shot("08_approved", "画面のボタンで承認")
        pg.wait_for_function("() => !document.querySelector('#export-button').disabled", timeout=30000)
        pg.click("#export-button")
        pg.wait_for_selector("#export-result:not([hidden])", timeout=30000)
        shot("09_exported", "SIM 用の JOB を出力")
        export_text = pg.inner_text("#export-result")
        can_again = pg.is_enabled("#export-button")
        pg.locator("details.app-info > summary").click()
        shot("10_app_info", "アプリ情報（版・SHA を出さない）")
        pg.locator("details.app-info > summary").click()

        # 回数の変更（提出版は 2回 → 3回 だけ）：範囲はボタンで確かめる → 確認表示 → ボタンで承認 → SIM 出力
        count = {}
        pg.set_input_files("#file-input", str(ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.wait_for_timeout(1200)
        shot("11_repeat_loaded", "回数の JOB を読み込んだ")
        say("ボタンを押す動作を2回から3回にして")
        pg.wait_for_selector("#range-yes", timeout=90000)
        shot("12_repeat_range", "範囲をボタンで確かめる（声では決めない）")
        pg.click("#range-yes")
        pg.wait_for_function("() => document.querySelector('#open-confirm') && !document.querySelector('#open-confirm').disabled",
                             timeout=60000)
        shot("13_repeat_candidate", "回数の変更候補（2回 → 3回）")
        pg.click("#open-confirm")
        pg.wait_for_selector("#confirm-overlay:not([hidden])")
        pg.fill("#approver-name", "唐川")
        pg.click("#approve-button")
        pg.wait_for_function("() => !document.querySelector('#export-button').disabled", timeout=30000)
        pg.click("#export-button")
        pg.wait_for_selector("#export-result:not([hidden])", timeout=30000)
        shot("14_repeat_exported", "回数の変更を SIM 用に出力")
        count["export_result"] = pg.inner_text("#export-result")[:300]
        say("4回にして")
        shot("15_repeat_4_refused", "4回は提出版では断る（2→3回だけ）")
        b.close()
    summary = {"url": args.url, "shots": log, "page_errors": errs, "export_result": export_text[:300],
               "export_button_enabled_after_export": can_again, "repeat": count}
    (out / "flow.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("export:", export_text[:120].replace("\n", " "), "| 2回目のボタン:", can_again, "| page errors:", errs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
