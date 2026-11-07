"""承認は1回・保存の場所を選ぶ（11/7 FINAL_RC_CHANGE_REQUEST A・B）の画面の確かめ。偽の Claude・合成 JOB・一時フォルダ。

  python tools/check_approve_save.py [--shots <フォルダ>]

- 承認：候補の窓の横で名前を入れて［この変更を承認する］を1回押すと承認になる（確認表示を開かない）。名前が空なら承認しない。
  2回すばやく押しても承認の送信は1回。保留・止まった候補では押せない
- 保存：OS の保存の窓は自動で操作できないので、showSaveFilePicker を偽の窓に差し替えて、①保存できた（読み戻しが同じ）
  ②取りやめた（失敗ではない）③書けなかった の3つを確かめる。ダウンロード（窓が無いとき）は本物のブラウザーのダウンロードで、
  中身がサーバーの SIM の中身と同じことを確かめる
- 実機には何もしない。OS の本物の保存の窓は、人の手で確かめる（報告に手順を書く）
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_teaching import claude_explain as cx  # noqa: E402
from voice_teaching import sim  # noqa: E402
from voice_teaching.claude_interpreter import MockInterpreter  # noqa: E402
from voice_teaching.web import server  # noqa: E402

FAKE_PICKER = """
window.__picks = [];
window.__pickerMode = 'ok';
window.showSaveFilePicker = async (opts) => {
  window.__picks.push(opts.suggestedName);
  if (window.__pickerMode === 'abort') { const e = new Error('cancel'); e.name = 'AbortError'; throw e; }
  let data = new Uint8Array();
  return {
    name: 'chosen_' + opts.suggestedName,
    async createWritable() {
      if (window.__pickerMode === 'fail') throw new Error('書けません（テスト）');
      return { async write(b) { data = new Uint8Array(b); }, async close() { window.__saved = Array.from(data); } };
    },
    async getFile() { return new Blob([window.__pickerMode === 'corrupt' ? new Uint8Array([1, 2, 3]) : data]); },
  };
};
"""

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> None:
    results.append((bool(ok), msg))
    print(("OK  " if ok else "NG  ") + msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="vt-approve-save-"))
    sim.DEFAULT_OUT_DIR = out / "sim"
    ex = cx.Explainer.from_env({})
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    approves: list[int] = []

    def to_candidate(pg):
        pg.goto(url)
        pg.wait_for_timeout(800)
        pg.set_input_files("#file-input", str(ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips .chip", has_text="C00004").first.click()
        if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
            pg.click("#kbd-button")
        pg.fill("#text-input", "Zプラス方向に5ミリ動かして")
        pg.click("#text-send")
        pg.wait_for_function("() => !document.querySelector('#quick-approve').hidden", timeout=20000)

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for (w, h) in [(1920, 1080), (1440, 900), (1280, 720)]:
                ctx = b.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
                pg = ctx.new_page()
                pg.on("request", lambda r: approves.append(1) if r.url.endswith("/api/approve") else None)
                pg.add_init_script(FAKE_PICKER)
                to_candidate(pg)
                # 何を承認するかが、承認のボタンと一緒に見えている（対象・JOB・SIM 専用・変更前→変更後）
                pop = pg.inner_text("#candidate-view")
                note = pg.inner_text("#quick-approve")
                vis = pg.evaluate("""() => { const r = document.querySelector('#quick-approve-button').getBoundingClientRect();
                    return r.top >= 0 && r.bottom <= innerHeight && r.width > 0; }""")
                check("C00004" in pop and "DEMO_RJ_XYZ.JBI" in pop and "シミュレーション用" in pop and "180.000" in pop
                      and "185.000" in pop and "実機への接続・送信はしません" in (pop + note) and vis
                      and "primary" in (pg.get_attribute("#quick-approve-button", "class") or ""),
                      f"承認の前（{w}×{h}）：対象・JOB・SIM 専用・変更前→変更後が見え、［この変更を承認する］が画面の中で目立つ")
                if shots:
                    pg.screenshot(path=str(shots / f"A1_before_approve_{w}x{h}.png"))
                # 名前が空：承認しない
                n0 = len(approves)
                pg.click("#quick-approve-button")
                pg.wait_for_timeout(300)
                check(len(approves) == n0 and pg.is_visible("#quick-approve-error") and pg.is_hidden("#approval-status"),
                      f"名前が空（{w}×{h}）：承認の送信をしない・理由を出す")
                # 1回で承認（確認表示を開かない）。2回すばやく押しても送信は1回
                pg.fill("#quick-approver", "唐川")
                n0 = len(approves)
                pg.click("#quick-approve-button")
                try:
                    pg.click("#quick-approve-button", timeout=500)
                except Exception:  # noqa: BLE001  1回目で押せなくなっていれば、それでよい
                    pass
                pg.wait_for_selector("#approval-status:not([hidden])", timeout=10000)
                pg.wait_for_timeout(300)
                check(len(approves) - n0 == 1 and pg.is_hidden("#confirm-overlay")
                      and "唐川 さんが承認しました" in pg.inner_text("#approval-status") and pg.is_hidden("#quick-approve"),
                      f"承認は1回（{w}×{h}）：［この変更を承認する］1回で承認。確認表示は開かない。送信 {len(approves) - n0}回")
                if w == 1920:
                    # 保存の窓：保存できた（読み戻しで照らし合わせる）
                    pg.evaluate("() => { window.__pickerMode = 'ok'; }")
                    pg.evaluate("() => Object.defineProperty(navigator, 'webdriver', { get: () => false })")
                    with pg.expect_response(lambda r: r.url.endswith("/api/export")) as er:
                        pg.click("#export-button")
                    res = er.value.json()
                    pg.wait_for_function("() => /選んだ場所に保存しました/.test(document.querySelector('#export-result').innerText)",
                                         timeout=10000)
                    saved = bytes(pg.evaluate("() => window.__saved"))
                    server_file = Path(res["path"]).read_bytes()
                    box = pg.inner_text("#export-result")
                    picks = pg.evaluate("() => window.__picks")
                    check(re.fullmatch(r"DEMO_RJ_XYZ_SIM_\d{8}_\d{6}\.JBI\.txt", res["file_name"]) is not None
                          and picks == [res["file_name"]] and saved == server_file
                          and hashlib.sha256(saved).hexdigest() == res["sha256"]
                          and "アプリの記録" in box and "選んだ場所に保存しました" in box,
                          f"保存の窓：日本時間の日時つきの名前を出し、選んだ場所にサーバーと同じ中身を保存。知らせは「アプリの記録」と分ける（{res['file_name']}）")
                    if shots:
                        pg.screenshot(path=str(shots / "B1_saved_to_chosen_place.png"))
                    # 取りやめ：失敗ではない。ボタンでやり直せる
                    pg.evaluate("() => { window.__pickerMode = 'abort'; }")
                    pg.click("#export-result .save-as-pick") if pg.locator("#export-result .save-as-pick").count() else None
                pg.close()
                ctx.close()

            # 取りやめ・書けない・読み戻しが違う・ダウンロード（窓が無い）を、それぞれ新しい画面で
            for mode, want in [("abort", "取りやめました"), ("fail", "保存に失敗しました"), ("corrupt", "保存に失敗しました")]:
                ctx = b.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
                pg = ctx.new_page()
                pg.add_init_script(FAKE_PICKER)
                to_candidate(pg)
                pg.fill("#quick-approver", "唐川")
                pg.click("#quick-approve-button")
                pg.wait_for_selector("#approval-status:not([hidden])")
                pg.evaluate(f"() => {{ window.__pickerMode = '{mode}'; }}")
                pg.evaluate("() => Object.defineProperty(navigator, 'webdriver', { get: () => false })")
                pg.click("#export-button")
                pg.wait_for_function(f"() => /{want}/.test(document.querySelector('#export-result').innerText)", timeout=10000)
                box = pg.inner_text("#export-result")
                check(want in box and "選んだ場所に保存しました" not in box and pg.locator("#export-result .save-as-download").count() == 1,
                      f"保存の窓（{mode}）：「{want}」と出し、保存したとは言わない。ダウンロードのボタンを出す")
                if mode == "abort":
                    with pg.expect_download() as dl:
                        pg.click("#export-result .save-as-download")
                    path = dl.value.path()
                    got = Path(path).read_bytes()
                    srv_files = sorted((out / "sim").iterdir())
                    check(got == srv_files[-1].read_bytes() and dl.value.suggested_filename == srv_files[-1].name
                          and "ダウンロードを始めました" in pg.inner_text("#export-result"),
                          f"ダウンロード：中身はサーバーの SIM と同じ・名前も同じ（{dl.value.suggested_filename}）。「保存しました」とは言わない")
                    if shots:
                        pg.screenshot(path=str(shots / "B2_cancel_then_download.png"))
                pg.close()
                ctx.close()

            # 保存の窓が無いブラウザー：自動で窓を開かず、ダウンロードと設定の案内
            ctx = b.new_context(viewport={"width": 1920, "height": 1080})
            pg = ctx.new_page()
            pg.add_init_script("delete window.showSaveFilePicker;")
            to_candidate(pg)
            pg.fill("#quick-approver", "唐川")
            pg.click("#quick-approve-button")
            pg.wait_for_selector("#approval-status:not([hidden])")
            pg.click("#export-button")
            pg.wait_for_selector("#export-result .save-as-download", timeout=10000)
            box = pg.inner_text("#export-result")
            check(pg.locator("#export-result .save-as-pick").count() == 0 and "ダウンロード前に各ファイルの保存場所を確認する" in box,
                  "保存の窓が無いとき：ダウンロードのボタンと、ブラウザーの設定の案内を出す")
            pg.close()
            ctx.close()
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
    ng = [m for ok, m in results if not ok]
    print(f"結果：OK {len(results) - len(ng)}・NG {len(ng)}")
    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
