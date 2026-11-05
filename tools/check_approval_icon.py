"""上の帯の承認の状態のアイコン（11/6）を、モックのサーバーと偽の音声認識で全部の状態にして確かめ、1920×1080 の画面を撮る（API・実マイクは使わない）。

    python tools/check_approval_icon.py <出力のフォルダ>

読み込む前（点線の丸）→ 読み込み（未承認）→ 候補（未承認）→ 承認（緑のチェック）→「違くない？」で保留（警告）→
［取り消す］で取り消し（警告）→ 別のタブで別の JOB を読み込んで画面が終わる（警告）→ 読み込み直し（未承認）。
出力はシミュレーション用の一時フォルダだけ。
"""
import sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools"))
import shoot_filming as S, e2e_browser as E
from playwright.sync_api import sync_playwright
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
srv, url = S.serve(Path(tempfile.mkdtemp(prefix="vt-appr-")))
res = []
def icon(pg):
    return pg.get_attribute("#approval-icon", "data-appr"), pg.get_attribute("#approval-icon", "aria-label"), pg.get_attribute("#approval-icon", "title")
def rec(pg, name, want):
    pg.wait_for_timeout(500)
    k, label, title = icon(pg)
    ok = k == want and label == title and label.startswith("承認の状態：")
    res.append((name, want, k, label, ok))
    pg.screenshot(path=str(out / f"appr_{name}_header.png"), clip={"x": 1400, "y": 0, "width": 520, "height": 56})
    pg.screenshot(path=str(out / f"appr_{name}_1920.png"))
def send(pg, text):
    E.fill_text(pg, text); pg.click("#text-send")
    E.until(pg, lambda: pg.is_enabled("#text-send") or not pg.is_visible("#text-send"), 20000); pg.wait_for_timeout(600)
with sync_playwright() as p:
    br = p.chromium.launch(channel="chrome", headless=True)
    pg = br.new_page(viewport={"width": 1920, "height": 1080})
    pg.add_init_script(E.FAKE_RECOGNITION)
    pg.goto(url); pg.wait_for_timeout(400)
    if pg.is_visible("#op-skip"): pg.click("#op-skip")
    rec(pg, "1_none", "none")
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI")); pg.wait_for_selector("#job-area:not([hidden])")
    rec(pg, "2_pending_loaded", "pending")
    pg.locator("#position-chips button", has_text="C00005").first.click()
    send(pg, "Zプラス方向に5ミリ")
    rec(pg, "3_pending_candidate", "pending")
    pg.click("#open-confirm"); pg.wait_for_selector("#confirm-overlay:not([hidden])")
    pg.fill("#approver-name", "自動確認（シクロ）"); pg.click("#approve-button"); pg.wait_for_timeout(800)
    rec(pg, "4_ok_approved", "ok")
    send(pg, "違くない？")
    st = pg.text_content("#status-line")
    rec(pg, "5_warn_hold", "warn")
    res.append(("5_server_hold", "保留", "保留" in st, st, "保留" in st))
    pg.locator("#reply-message button", has_text="取り消す").first.click(); pg.wait_for_timeout(800)
    st = pg.text_content("#status-line")
    rec(pg, "6_warn_voided", "warn")
    res.append(("6_server_voided", "取り消しました", "取り消しました" in st, st, "取り消しました" in st))
    # 閉じた画面：別のタブで別の JOB を読み込むと、この画面はサーバーが閉じる。押すと断られて閉じた印
    pg2 = br.new_page(viewport={"width": 1280, "height": 720}); pg2.goto(url); pg2.wait_for_timeout(300)
    if pg2.is_visible("#op-skip"): pg2.click("#op-skip")
    pg2.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_BUTTON_REPEAT.JBI")); pg2.wait_for_selector("#job-area:not([hidden])")
    E.fill_text(pg, "Zプラス方向に3ミリ"); pg.click("#text-send"); pg.wait_for_timeout(1500)
    rec(pg, "7_warn_closed", "warn")
    # 読み込み直すと、前の印は残らない
    pg.set_input_files("#file-input", str(REPO / "samples/dummy/DEMO_C_PLACE.JBI")); pg.wait_for_selector("#job-area:not([hidden])")
    rec(pg, "8_pending_reloaded", "pending")
    br.close()
srv.shutdown()
fails = [r for r in res if not r[4]]
for r in res: print("OK " if r[4] else "NG ", r[0], "| want", r[1], "| got", r[2], "|", r[3])

print("結果：", "すべて通りました" if not fails else f"{len(fails)}件 通りませんでした")
sys.exit(1 if fails else 0)
