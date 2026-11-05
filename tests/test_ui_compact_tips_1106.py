"""11/6 唐川さん：本番UIでは説明の吹き出しを小さく・短くする（ふだんは出さず、? に合わせた・押したときだけ）。実装より先に書いた。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "voice_teaching" / "web" / "static"


def tips_block() -> str:
    hl = (STATIC / "highlight.js").read_text(encoding="utf-8")
    start = hl.index("export const TIPS")
    return hl[start:hl.index("};", start)]


def entry(key: str) -> str:
    m = re.search(rf"^\s*{key}: \{{.*\}},?$", tips_block(), re.M)
    assert m, key
    return m.group(0)


def test_count_tip_is_short():
    e = entry("count")
    assert 'head: "回数"' in e
    assert "同じ動作を繰り返す回数を指定します" in e
    assert 'ex: "3回にして"' in e
    assert "範囲は画面のボタン" not in e  # 長い説明はやめた


def test_small_tips_are_marked_and_styled():
    for key in ("count", "movl", "movj", "other"):
        assert "small: true" in entry(key), key
    hl = (STATIC / "highlight.js").read_text(encoding="utf-8")
    assert 'classList.toggle("tip-small"' in hl
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".tip-pop.tip-small" in css


def test_tips_open_only_on_hover_focus_or_press():
    """ふだんは出さない：吹き出しは最初から隠れていて、開くのは ? に合わせた・フォーカス・押したときだけ（今の決まりのまま）"""
    hl = (STATIC / "highlight.js").read_text(encoding="utf-8")
    assert "pop.hidden = true;" in hl
    for ev in ('"mouseover"', '"focusin"', '"click"'):
        assert ev in hl


def test_count_help_is_in_usage_not_under_3d():
    """11/6 唐川さん：「回数？」は 3D の再生の横から外し、回数の説明は上の「使い方」にまとめる"""
    h = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-tip="count"' not in h
    help_pop = h[h.index('id="help-pop"'):h.index("<!-- /help-pop -->")]
    assert "同じ動作を繰り返す回数" in help_pop and "3回にして" in help_pop
    assert 'data-tip="play"' in h  # 3D の再生の説明は再生の操作の横に残す


def test_count_replies_get_next_step_in_conversation():
    """回数の発話への決まった返事（整数で・1以上の整数で）には、会話欄に「次にすること」を添える（表示だけ）"""
    hl = (STATIC / "highlight.js").read_text(encoding="utf-8")
    steps = hl[hl.index("export const NEXT_STEPS"):hl.index("];", hl.index("export const NEXT_STEPS"))]
    m = re.search(r"\{ re: /(\^回数は[^/]*)/, say: \"回数を、整数で指定してください\", ex: \"3回にして\" \}", steps)
    assert m, steps
    pat = re.compile(m.group(1).replace("\\", "\\"))
    assert pat.search("回数は整数で言ってください。小数の回数は扱いません")
    assert pat.search("回数は1以上の整数で言ってください。マイナスの回数は扱いません")
