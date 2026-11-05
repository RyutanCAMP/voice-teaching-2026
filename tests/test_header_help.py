"""ヘッダーの「?」2つの直し（案3）の受入テスト。実装より先に書いた。

- 「アプリの記録：」のチップ（#status-line）の説明ボタン（data-tip="status"）は、チップの直後にくっついた小さな「ⓘ」（情報）の見た目。
  中身・動き（data-tip="status" の吹き出し）は変えない。aria-label は「アプリの記録の説明」。
- 使い方の小窓を開く #help-button は、丸の「？」ではなく、隣の #engineer-toggle（「詳細表示」の文字リンク、class="link"）と
  同じ見た目の文字ボタン「使い方」。id・aria-controls・aria-expanded・開閉・Esc で閉じてフォーカスを戻す動きは保つ。
- ヘッダー以外の .tip の「?」（範囲・位置・マイクなど）は今回は変えない。
- 画面の動き（360×800・390×844・1150×800・1280×720・1280×800・1920×1080 での ⓘ の位置・重なり・44×44、PC の幅で折り返さない・開閉・Esc・吹き出し）は
  tools/e2e_browser.py の check_header_help で確かめる。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "voice_teaching" / "web" / "static"
ASSERTIVE = ["安全です", "当たります", "当たりません", "干渉します", "干渉しません", "問題ありません", "大丈夫です", "安全な"]


def text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def header() -> str:
    h = text("index.html")
    start = h.index("<header")
    return h[start:h.index("</header>", start)]


def buttons(html: str) -> list[tuple[str, str]]:
    """(開きタグ, 中の文字) の並び"""
    return [(m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
            for m in re.finditer(r"(<button\b[^>]*>)(.*?)</button>", html, re.S)]


def attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\s{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def test_header_has_no_question_mark_buttons():
    for tag, inner in buttons(header()):
        assert inner not in ("?", "？"), tag
        assert "?" not in inner and "？" not in inner, (tag, inner)
    assert "help-btn" not in header()  # 丸の「？」のボタンの見た目は使わない


def test_help_button_is_text_link_like_engineer_toggle():
    bs = {attr(t, "id"): (t, i) for t, i in buttons(header())}
    tag, inner = bs["help-button"]
    # 本番UI（ミニマル確定案 11/5 夜）：使い方・詳細表示は同じ見た目のアイコンのボタン（名前は aria-label と title）
    assert attr(tag, "aria-label") == "使い方" and attr(bs["engineer-toggle"][0], "aria-label") == "詳細表示"
    assert attr(tag, "class") == "icon-btn" == attr(bs["engineer-toggle"][0], "class")
    assert attr(tag, "type") == "button"
    assert attr(tag, "aria-controls") == "help-pop"
    assert attr(tag, "aria-expanded") == "false"
    # 「使い方」→「詳細表示」の順（test_ux_polish と同じ）
    h = header()
    assert h.index('id="help-button"') < h.index('id="engineer-toggle"')


def test_help_button_and_engineer_toggle_share_css():
    css = text("style.css")
    assert re.search(r"#engineer-toggle\s*,\s*#help-button\s*\{[^}]*font-size:\s*14px[^}]*white-space:\s*nowrap", css) \
        or re.search(r"#help-button\s*,\s*#engineer-toggle\s*\{[^}]*font-size:\s*14px[^}]*white-space:\s*nowrap", css)
    assert not re.search(r"\.help-btn\s*\{", css)  # 丸の「？」の規則は消した


def test_status_tip_is_info_mark_attached_to_status_chip():
    h = header()
    m = re.search(r'<div class="status-wrap">\s*<p id="status-line"[^>]*></p>(<button\b[^>]*>)(.*?)</button>\s*</div>', h, re.S)
    assert m, "状態の1行と説明ボタンを1つの塊（.status-wrap）にし、ボタンはチップの直後に置く"
    tag, inner = m.group(1), m.group(2)
    assert attr(tag, "data-tip") == "status"
    assert "tip" in attr(tag, "class").split() and "tip-info" in attr(tag, "class").split()
    assert attr(tag, "aria-label") == "アプリの記録の説明"
    assert inner == "i"  # 見た目は CSS の丸の中の「i」（ⓘ）。「?」ではない
    css = text("style.css")
    assert re.search(r"button\.tip\.tip-info\s*\{", css)
    assert re.search(r"button\.tip\.tip-info::before\s*\{", css)
    assert re.search(r"\.status-line\[hidden\]\s*\+\s*button\.tip\s*\{\s*display:\s*none", css)  # 記録が出ていないときは出さない
    # 記録が出ていなくても、塊が右へ寄せる（使い方・詳細表示を右の端に置く）
    assert re.search(r"\.status-wrap\s*\{[^}]*margin-left:\s*auto", css)


def test_status_tip_text_unchanged():
    hl = text("highlight.js")
    assert 'status: { head: "アプリの記録", body: "アプリが記録している今の状態です（候補・承認・出力）。会話の答えでは変わりません。", ex: null }' in hl


def test_other_tips_keep_question_mark():
    h = text("index.html")
    rest = h[h.index("</header>"):]
    tips = {attr(t, "data-tip"): i for t, i in buttons(rest) if attr(t, "data-tip")}
    for k in ["position", "approve", "updown", "mic", "shift", "play"]:
        assert tips[k] == "?", k
    assert "count" not in tips  # 11/6 唐川さん：回数は再生の操作ではないので、3D の下から外した（説明は「使い方」へ）
    assert 'el("button", { type: "button", class: "tip", "data-tip": "range" }, "?")' in text("app.js")


def test_help_toggle_and_escape_kept():
    j = text("app.js")
    assert '$("help-button").setAttribute("aria-expanded", String(open));' in j
    assert '$("help-button").addEventListener("click"' in j
    assert 'if (!$("help-pop").hidden) { setHelp(false); $("help-button").focus(); return; }' in j


def test_header_has_no_assertive_words():
    h = header()
    for w in ASSERTIVE:
        assert w not in h, w


def test_status_tip_follows_last_chip_on_same_line():
    """案3の追加（内部レビュー QA P2・Safety 低2件）：チップが2行に折り返しても ⓘ は最後のチップの直後（同じ行）。
    #status-line を inline にして、終わりの右の余白で ⓘ の場所を取っておき、ⓘ は同じ幅だけ左へ戻す。
    ⓘ の丸はほかの「?」と同じ 22px。画面での位置・重なり・44×44 は tools/e2e_browser.py の check_header_help で確かめる。"""
    css = text("style.css")
    m = re.search(r"\n\.status-line\s*\{([^}]*)\}", css)
    assert m and re.search(r"display:\s*inline\s*;", m.group(1)), "記録の1行は inline（ⓘ と同じ文の流れ）"
    reserve = re.search(r"margin:\s*0\s+(\d+)px\s+0\s+0\s*;", m.group(1))
    assert reserve, "記録の1行の終わりに、ⓘ の場所（右の余白）を取っておく"
    info = re.search(r"button\.tip\.tip-info\s*\{([^}]*)\}", css).group(1)
    back = re.search(r"margin:\s*-12px\s+0\s+-12px\s+-(\d+)px\s*;", info)
    assert back and back.group(1) == reserve.group(1) == "44", "ⓘ は取っておいた幅（ⓘ の押せる所と同じ 44px）だけ左へ戻す"
    before = re.search(r"button\.tip\.tip-info::before\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"width:\s*22px", before) and re.search(r"height:\s*22px", before), "ⓘ の丸はほかの「?」と同じ 22px"
