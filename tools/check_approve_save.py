"""［承認して保存］（11/7 唐川さんの決定）の画面の確かめ。偽の Claude・合成 JOB・一時フォルダ。本物の Chromium。

  python tools/check_approve_save.py [--shots <フォルダ>]

- 1回の操作：候補の窓の横で名前を入れて［承認して保存］を1回押すと、サーバーが承認を確かめ、通ったら SIM（.JBI）を書き、
  保存の場所を選ぶ窓を開き、選んだ場所に書いて読み戻しが同じなら「保存しました」。最後の［シミュレーション用に出力］は無い
- 名前が空なら承認も保存もしない。2回すばやく押しても、承認の送信1回・書き出し1回
- 保存の窓を閉じた・書けない・読み戻しが違う：保存したと言わない。もう一度承認せずに［保存先を選んで保存］で保存し直せる
- 保存の窓が無いブラウザー：ダウンロード（中身と名前はサーバーと同じ）
- OS の本物の保存の窓は自動で操作できないので、showSaveFilePicker を偽の窓に差し替える（本物の窓は人の手で確かめる）
"""
from __future__ import annotations

import argparse
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
window.__pickerMode = 'MODE';
Object.defineProperty(navigator, 'webdriver', { get: () => false });  // 人の操作と同じく、承認のあと窓を自動で開かせる
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
NAME_RE = r"DEMO_RJ_XYZ_SIM_\d{10}(?:_\d{2})?\.JBI"
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
    server.AUTOSAVE_DEFAULT = True  # 本番と同じ［承認して保存］
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=cx.Explainer.from_env({}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    calls = {"approve": 0, "export": 0}

    def count(r):
        for k in calls:
            if r.url.endswith("/api/" + k):
                calls[k] += 1

    def page(b, w, h, picker=True, mode="ok"):
        ctx = b.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
        pg = ctx.new_page()
        pg.on("request", count)
        if picker:
            pg.add_init_script(FAKE_PICKER.replace("MODE", mode))
        else:
            pg.add_init_script("delete window.showSaveFilePicker;")
        pg.goto(url)
        pg.wait_for_timeout(700)
        pg.set_input_files("#file-input", str(ROOT / "samples" / "dummy" / "DEMO_RJ_XYZ.JBI"))
        pg.wait_for_selector("#job-area:not([hidden])")
        pg.locator("#position-chips .chip", has_text="C00004").first.click()
        if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
            pg.click("#kbd-button")
        pg.fill("#text-input", "Zプラス方向に5ミリ動かして")
        pg.click("#text-send")
        pg.wait_for_function("() => !document.querySelector('#quick-approve').hidden", timeout=20000)
        return ctx, pg

    def text(pg):
        return pg.inner_text("#export-result") if pg.is_visible("#export-result") else ""

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for (w, h) in [(1920, 1080), (1440, 900), (1280, 720)]:
                ctx, pg = page(b, w, h)
                pop = pg.inner_text("#candidate-view")
                vis = pg.evaluate("""() => { const r = document.querySelector('#quick-approve-button').getBoundingClientRect();
                    return r.top >= 0 && r.bottom <= innerHeight && r.width > 0; }""")
                check("C00004" in pop and "DEMO_RJ_XYZ.JBI" in pop and "実機への接続・送信はしません" in pop and "180.000" in pop
                      and "185.000" in pop and vis and pg.inner_text("#quick-approve-button") == "承認して保存"
                      and pg.is_hidden("#export-button")
                      and "primary" in (pg.get_attribute("#quick-approve-button", "class") or ""),
                      f"押す前（{w}×{h}）：対象・JOB・SIM 専用・変更前→変更後が見え、［承認して保存］だけが目立つ。最後の出力ボタンは無い")
                if shots:
                    pg.screenshot(path=str(shots / f"A1_before_approve_save_{w}x{h}.png"))
                a0, e0 = calls["approve"], calls["export"]
                pg.click("#quick-approve-button")
                pg.wait_for_timeout(300)
                check(calls["approve"] == a0 and calls["export"] == e0 and pg.is_visible("#quick-approve-error"),
                      f"名前が空（{w}×{h}）：承認も保存もしない・理由を出す")
                pg.fill("#quick-approver", "唐川")
                pg.click("#quick-approve-button")
                try:
                    pg.click("#quick-approve-button", timeout=400)  # 連打
                except Exception:  # noqa: BLE001  1回目で押せなくなっていれば、それでよい
                    pass
                pg.wait_for_function("() => /選んだ場所に保存しました/.test(document.querySelector('#export-result').innerText)",
                                     timeout=15000)
                saved = bytes(pg.evaluate("() => window.__saved"))
                picks = pg.evaluate("() => window.__picks")
                box = text(pg)
                check(calls["approve"] - a0 == 1 and calls["export"] - e0 == 1 and bool(picks)
                      and re.fullmatch(NAME_RE, picks[-1]) is not None
                      and saved == (out / "sim" / picks[-1]).read_bytes() and "アプリの記録" in box
                      and "選んだ場所に保存しました" in box and pg.is_hidden("#export-button")
                      and "唐川 さんが承認しました" in pg.inner_text("#approval-status"),
                      f"［承認して保存］1回（{w}×{h}）：承認 {calls['approve'] - a0}回・書き出し {calls['export'] - e0}回・"
                      f"保存の窓の名前 {picks[-1] if picks else None}・選んだ場所とアプリの記録の中身が同じ")
                # UI-1700-2：見出しは確かめたあとだけ「保存しました」。✓ は1つ（選んだ場所）。アプリの記録は控えとして別の行
                check(pg.inner_text("#next-action") == "保存しました" and box.count("✓") == 1
                      and "アプリの記録（控え）に出力しました" in box,
                      f"保存の言い分け（{w}×{h}）：見出し「{pg.inner_text('#next-action')}」・✓ {box.count('✓')}個")
                # UI-1700-1・XC-26：承認のあと、候補の窓が 3D の変更の点（C00003〜C00005）を隠さない
                hidden_pts = pg.evaluate("""() => { const p = document.querySelector('.cand-pop').getBoundingClientRect();
                    return [...document.querySelectorAll('#fig-3d svg circle')].filter((c) => { const r = c.getBoundingClientRect();
                      const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
                      return r.width > 0 && x >= p.left && x <= p.right && y >= p.top && y <= p.bottom; }).length; }""")
                check(hidden_pts == 0, f"承認のあと（{w}×{h}）：候補の窓の下に隠れた 3D の点 {hidden_pts}個")
                if shots:
                    pg.screenshot(path=str(shots / f"A2_after_approve_save_{w}x{h}.png"))
                ctx.close()

            for mode, want in [("abort", "取りやめました"), ("fail", "保存に失敗しました"), ("corrupt", "保存に失敗しました")]:
                ctx, pg = page(b, 1920, 1080, mode=mode)
                pg.fill("#quick-approver", "唐川")
                a0, e0 = calls["approve"], calls["export"]
                pg.click("#quick-approve-button")
                pg.wait_for_function(f"() => /{want}/.test(document.querySelector('#export-result').innerText)", timeout=15000)
                box = text(pg)
                head = pg.inner_text("#next-action")
                check(want in box and "選んだ場所に保存しました" not in box and pg.locator("#export-result .save-as-pick").count() == 1
                      and "✓" not in box and "保存しました" not in head.replace("保存していません", "") and "まだ" in box,
                      f"保存の窓（{mode}）：「{want}」と出し、保存したとは言わない（見出し「{head}」・✓ なし）。［保存先を選んで保存］を出す")
                if shots:
                    pg.screenshot(path=str(shots / f"B0_{mode}_not_saved.png"))
                pg.evaluate("() => { window.__pickerMode = 'ok'; }")
                pg.click("#export-result .save-as-pick")
                pg.wait_for_function("() => /選んだ場所に保存しました/.test(document.querySelector('#export-result').innerText)",
                                     timeout=15000)
                check(calls["approve"] - a0 == 1 and calls["export"] - e0 == 1 and pg.is_visible("#approval-status"),
                      f"保存し直し（{mode} のあと）：もう一度承認しない・書き出しも1回のまま・選んだ場所に保存できた")
                if shots and mode == "abort":
                    pg.screenshot(path=str(shots / "B1_cancel_then_resave.png"))
                ctx.close()

            # XC-28：保存のあとに話しかけて保留になったら、見出しのとおり保存もできない（ボタンは押せない・わけを書く）。
            # 取り消したら結果の欄ごと消える。保存のボタンが保存するのは、承認して出力した中身だけ（承認していない中身は保存できない）
            ctx, pg = page(b, 1920, 1080, mode="abort")
            pg.fill("#quick-approver", "唐川")
            pg.click("#quick-approve-button")
            pg.wait_for_function("() => /取りやめました/.test(document.querySelector('#export-result').innerText)", timeout=15000)
            pg.fill("#text-input", "どこが変わったの？")
            pg.click("#text-send")
            pg.wait_for_function("() => /承認・出力はできません/.test(document.querySelector('#next-action').innerText)", timeout=15000)
            pg.wait_for_timeout(300)
            st = pg.evaluate("""() => ({ btn: [...document.querySelectorAll('#save-as-area button')].map((b) => b.disabled),
                note: (n => n && !n.hidden ? n.innerText : '')(document.querySelector('#save-held-note')) })""")
            n0, e0 = len(pg.evaluate("() => window.__picks")), calls["export"]
            pg.evaluate("() => document.querySelectorAll('#save-as-area button').forEach((x) => x.click())")  # 押せないボタンを JS から押しても
            pg.wait_for_timeout(500)
            check(len(st["btn"]) == 2 and all(st["btn"]) and "保留中は保存できません" in st["note"]
                  and len(pg.evaluate("() => window.__picks")) == n0 and calls["export"] == e0,
                  f"保留中（XC-28）：保存のボタン {len(st['btn'])}つとも押せない・わけを出す・押しても保存の窓を開かない")
            if shots:
                pg.screenshot(path=str(shots / "C0_hold_save_disabled.png"))
            pg.locator("#reply-message button, #dialog-choices button", has_text="取り消す").first.click()
            pg.wait_for_function("() => document.querySelector('#export-result').hidden", timeout=15000)
            check(pg.locator("#save-as-area button:visible").count() == 0,
                  "保留から［取り消す］（XC-28）：結果の欄と保存のボタンは消える（取り消した中身は保存できない）")
            ctx.close()

            ctx, pg = page(b, 1920, 1080, picker=False)
            pg.fill("#quick-approver", "唐川")
            pg.click("#quick-approve-button")
            pg.wait_for_selector("#export-result .save-as-download", timeout=15000)
            with pg.expect_download() as dl:
                pg.click("#export-result .save-as-download")
            got = Path(dl.value.path()).read_bytes()
            name = dl.value.suggested_filename
            check(re.fullmatch(NAME_RE, name) is not None and got == (out / "sim" / name).read_bytes()
                  and pg.locator("#export-result .save-as-pick").count() == 0
                  and "ダウンロードを始めました" in text(pg) and "選んだ場所に保存しました" not in text(pg)
                  and "✓" not in text(pg) and pg.inner_text("#next-action") != "保存しました",
                  f"保存の窓が無いとき：ダウンロード（{name}）の中身はサーバーと同じ。保存したとは言わない")
            ctx.close()
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
    names = sorted(f.name for f in (out / "sim").glob("*"))
    check(bool(names) and all(re.fullmatch(NAME_RE, n) for n in names) and len(names) == len(set(names)),
          f"出力の名前はどれも <JOB名>_SIM_YYMMDDHHMM(_NN).JBI・重なりなし（{len(names)}個：{names[:3]}…）")
    repeat_check(shots)
    ng = [m for ok, m in results if not ok]
    print(f"結果：OK {len(results) - len(ng)}・NG {len(ng)}")
    return 1 if ng else 0


def repeat_check(shots: Path | None) -> None:
    """UX-2100-P1-1：回数の変更（2→3回）を［承認して保存］したあと、候補の窓が 3D の「×2 → ×3」と変更の点を隠さない（3つの大きさ）"""
    sys.path.insert(0, str(ROOT / "tools"))
    import demo_mock as dm  # noqa: PLC0415  偽の Claude の台本（本物の Claude は呼ばない）
    out = Path(tempfile.mkdtemp(prefix="vt-approve-save-repeat-"))
    sim.DEFAULT_OUT_DIR = out / "sim"
    cx.PRICES[dm.MOCK_MODEL] = cx.Price(input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0)
    ex = cx.Explainer("mock-no-network", dm.MOCK_MODEL, client=dm.ScriptClient(), usage_log=cx.UsageLog(out / "usage.jsonl"))
    srv = server.make_server(port=0, interpreter=MockInterpreter(), updown_config=None, explainer=ex)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    def say(pg, t, wait):
        if not pg.is_visible("#text-input") and pg.is_visible("#kbd-button"):
            pg.click("#kbd-button")
        pg.fill("#text-input", t)
        pg.click("#text-send")
        pg.wait_for_function(wait, timeout=20000)

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for (w, h) in [(1920, 1080), (1440, 900), (1280, 720)]:
                ctx = b.new_context(viewport={"width": w, "height": h})
                pg = ctx.new_page()
                pg.add_init_script(FAKE_PICKER.replace("MODE", "ok"))
                pg.goto(url)
                pg.wait_for_timeout(700)
                pg.set_input_files("#file-input", str(ROOT / "samples" / "dummy" / "DEMO_BUTTON_REPEAT.JBI"))
                pg.wait_for_selector("#job-area:not([hidden])")
                say(pg, "このロボット、何をしている？", "() => document.querySelector('#ask-turns .bubble.claude:not(.thinking)')")
                say(pg, "ボタンを押してるんだよ", "() => document.querySelector('#ask-turns .talk-choice')")
                pg.locator("#ask-turns .talk-choice", has_text="この動きです").click()
                pg.wait_for_selector("#ask-turns .bubble.taught")
                say(pg, "じゃあ、3回にして", "() => document.querySelector('#range-yes')")
                pg.click("#range-yes")
                pg.wait_for_function("() => !document.querySelector('#quick-approve').hidden", timeout=15000)
                pg.fill("#quick-approver", "唐川")
                pg.click("#quick-approve-button")
                pg.wait_for_function("() => /選んだ場所に保存しました/.test(document.querySelector('#export-result').innerText)",
                                     timeout=15000)
                pg.wait_for_timeout(500)
                # 数えるのは 3D の表示の中（.stage の四角の中）にあるものだけ。回数の変更の向きでは、台座の印（robot-origin）が 3D の表示の
                # 左の外（会話の欄の位置）に来て、候補の窓があってもなくても見えないため
                r = pg.evaluate("""() => { const p = document.querySelector('.cand-pop').getBoundingClientRect();
                    const sg = document.querySelector('.stage').getBoundingClientRect();
                    const under = (e) => { const r = e.getBoundingClientRect(); const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
                      return r.width > 0 && x >= sg.left && x <= sg.right && y >= sg.top && y <= sg.bottom
                        && x >= p.left && x <= p.right && y >= p.top && y <= p.bottom; };
                    const x3 = [...document.querySelectorAll('#fig-3d *')].filter((e) => !e.children.length && /×\s*3/.test(e.textContent));
                    return { pts: [...document.querySelectorAll('#fig-3d svg circle')].filter(under).length,
                             x3: x3.length, x3under: x3.filter(under).map((e) => e.textContent.trim()) }; }""")
                check(r["pts"] == 0 and r["x3"] > 0 and not r["x3under"],
                      f"回数の変更を承認して保存（{w}×{h}）：候補の窓の下に 3D の点 {r['pts']}個・「×3」{r['x3']}か所のうち隠れたもの {r['x3under']}")
                if shots:
                    pg.screenshot(path=str(shots / f"D0_repeat_after_save_{w}x{h}.png"))
                ctx.close()
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
