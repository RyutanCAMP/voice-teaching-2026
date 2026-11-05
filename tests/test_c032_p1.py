"""CHECKER_032（クロ・独立レビュー）の P1-2・P1-3 の受入テスト（唐川さんの決定 2026-11-03 夜）。

P1-2：変更できない参考の表示は、文の最初に「参考の表示だけ・変更しません」と出す（「動かします」で始めない）
P1-3：言った量と、JBI に保存できる量（その値の小数点以下の桁数）が違うときは、黙って丸めて候補を作らない。
      浮動小数点の誤差（0.1＋0.2 など）と、保存の精度による丸めは分けて考える（言った量を10進数のまま比べる）。
      小数の桁数は3けたと決めつけず、その値が JBI に書かれている桁数を基準にする。
"""
import re
from pathlib import Path

import pytest

from voice_teaching import jbi, sim
from voice_teaching.claude_interpreter import MockInterpreter
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()


def with_c00005(decimals: int) -> bytes:
    """DEMO_C_PLACE の C00005 の X・Y・Z だけを、小数 decimals けたの書き方にした合成（メモリの中だけ）。"""
    text = DEMO_C.decode("ascii")

    def fix(m):
        vals = m.group(2).split(",")
        out = [f"{float(v):.{decimals}f}" if i < 3 else v for i, v in enumerate(vals)]
        return m.group(1) + ",".join(out)
    return re.sub(r"(C00005=)([^\r\n]*)", fix, text, count=1).encode("ascii")


def hear(data: bytes, text: str, select: str = "C00005"):
    w = WebFlow(interpreter=MockInterpreter())
    sid = w.load(data)["session_id"]
    if select:
        w.select(sid, select)
    return w.hear(sid, text, source="voice")


# ---- P1-3：保存の精度で丸まる量は候補にしない ----
@pytest.mark.parametrize("decimals,delta", [
    (3, -19.9999), (3, -0.0006), (3, 0.0004),
    (0, -0.6), (0, 2.5), (0, -3.5), (0, 19.6),
    (1, 0.25), (2, 1.005),
])
def test_amount_that_cannot_be_stored_is_not_a_candidate(decimals, delta):
    job = jbi.parse_bytes(with_c00005(decimals))
    with pytest.raises(jbi.JbiError) as e:
        job.plan_shift("C00005", "Z", delta)
    msg = str(e.value)
    assert "候補を作りません" in msg and "言った量と違う" in msg
    with pytest.raises(sim.SimError):  # 参考の表示（シミュレーション）の道でも作らない
        sim.simulate_shift(job, "C00005", "Z", delta)


@pytest.mark.parametrize("decimals,delta", [
    (3, -19.999), (3, 0.3), (3, 0.1 + 0.2),  # 0.1+0.2 は浮動小数点で 0.30000000000000004（誤差。丸めではない）
    (0, -3.0), (0, 20.0), (1, 0.5), (2, 1.25),
])
def test_amount_that_can_be_stored_is_a_candidate(decimals, delta):
    job = jbi.parse_bytes(with_c00005(decimals))
    cp = job.plan_shift("C00005", "Z", delta)
    assert abs((cp.after_value - cp.before_value) - delta) < 1e-6


def test_float_noise_is_not_treated_as_rounding():
    job = jbi.parse_bytes(with_c00005(3))
    assert jbi.precision_problem("C00005", "Z", "200.000", 0.1 + 0.2) is None
    assert jbi.precision_problem("C00005", "Z", "200.000", 0.30000000000000004) is None
    assert jbi.precision_problem("C00005", "Z", "200", 3.0) is None
    assert jbi.precision_problem("C00005", "Z", "200", 0.6) is not None
    assert job.plan_shift("C00005", "Z", 0.3)  # 候補になる


@pytest.mark.parametrize("decimals,said", [(0, "Zマイナス0.6ミリ"), (0, "Zプラス2.5ミリ"), (3, "Zマイナス19.9999ミリ")])
def test_flow_does_not_make_a_candidate_for_unstorable_amounts(decimals, said):
    r = hear(with_c00005(decimals), said)
    assert r["state"]["candidate"] is None and r["status"] in ("stop", "ask")
    assert "言った量と違う" in r["message"]


def test_flow_makes_candidate_for_storable_amount_on_integer_position():
    r = hear(with_c00005(0), "Zマイナス3ミリ")
    c = r["state"]["candidate"]
    assert c is not None and c["delta"] == "-3"


def test_reference_path_also_refuses_unstorable_amounts():
    # 変更できない位置（C00000：MOVJ の隣など）の参考の表示でも、言った量と違う量では作らない
    data = DEMO_C.decode("ascii")
    r = hear(DEMO_C, "C00000のZプラス0.0004ミリ", select=None)
    assert r["state"]["candidate"] is None


# ---- P1-2：参考の表示だけの候補は、最初に「参考の表示だけ・変更しません」 ----
def test_blocked_candidate_text_starts_with_reference_only():
    r = hear(DEMO_C, "C00000のZプラス3ミリ", select=None)
    st = r["state"]
    assert st["candidate"] is not None and st["blocked_reason"]
    text = r["message"]
    assert text.startswith("【参考の表示だけ・変更しません】"), text[:40]
    first = text.split("。")[0]
    assert "動かします" not in first and "変えます" not in first
    assert "動かします" not in text  # 止まった候補の文に「動かします」は使わない
    assert not st["can_approve"] and not st["can_export"]


def test_ok_candidate_text_is_unchanged():
    r = hear(DEMO_C, "Zマイナス3ミリ")
    assert r["message"].startswith("位置 C00005 を Z方向に -3.000mm 動かします")


# ---- P1-1：言い終わりの「〜ます」は、既定の確認ルール（11/3 17:14）どおり2択で確かめる（候補にしない） ----
from voice_teaching import utterance as ut


@pytest.mark.parametrize("said,restated", [
    ("3mm下げます", "3mm下げて"), ("3mm上げます", "3mm上げて"),
    ("3mm下げますね", "3mm下げて"), ("3mm下げますよ", "3mm下げて"), ("3ミリ下げておきます", "3ミリ下げて"),
    ("Zマイナスに3mmずらします", None), ("Zマイナス3ミリ動かしますね", None),
    ("Zマイナス3ミリにします", "Zマイナス3ミリ"), ("Zマイナス3ミリにしますね", "Zマイナス3ミリ"),
])
def test_masu_endings_are_confirmed_with_two_choices(said, restated):
    f = ut.read_utterance(said)
    assert f.shimasu_text is not None, said
    if restated is not None:
        assert f.shimasu_text == restated
    r = hear(DEMO_C, said)
    assert r["state"]["candidate"] is None and r["status"] == "ask"
    assert ("ご希望ですか" in r["message"] or "上下の向き" in r["message"]
            or ut.is_updown_unset_ask(r["message"])), (said, r["message"])


@pytest.mark.parametrize("said", ["3mm下げません", "下げます3mm", "3mm下げましたね", "3mm下げましょう"])
def test_other_masu_forms_are_not_turned_into_requests(said):
    # 打ち消し・文の途中の「ます」・過去・誘いは、指示として読まない（今まで通り：候補にしない）
    assert hear(DEMO_C, said)["state"]["candidate"] is None


# ---- 内部 Safety（ad77d67）の P2・P3 ----
@pytest.mark.parametrize("said", ["Zマイナス19.99999999999999ミリ", "Zマイナス3.0000000000004ミリ", "Zマイナス1.1234567ミリ"])
def test_amounts_with_too_many_decimals_are_asked_back(said):
    # 細かすぎる小数（float の誤差と区別できない）は、黙って丸めず聞き返す（候補にしない）
    r = hear(DEMO_C, said)
    assert r["state"]["candidate"] is None and r["status"] == "ask"


def test_six_decimals_are_still_read():
    f = ut.read_utterance("Zマイナス0.123456ミリ")
    assert f.amounts and not f.unclear_amounts


@pytest.mark.parametrize("said", ["Zマイナス3ミリずらしておきます", "Zマイナス3ミリ動かしておきます", "Zマイナス3ミリにしておきます"])
def test_te_oku_masu_variants_are_confirmed(said):
    f = ut.read_utterance(said)
    assert f.shimasu_text is not None, said
    assert hear(DEMO_C, said)["state"]["candidate"] is None
