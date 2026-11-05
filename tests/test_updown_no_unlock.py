"""唐川さんの重視点（2026-11-03）：上下の向きの設定だけで、変更・承認・出力の安全の制限を解除しない。

上下の向きの設定が変えるのは「上・下」の言葉を Z± と読むかどうかだけ。変更できる位置（jbi の検査）・1回の上限（20mm）・
承認・出力（合成ジョブだけ）の制限は、設定があってもなくても同じであることを固定する。
設定は一時フォルダの仮の設定だけを使う（実機で確かめたものではない）。
"""
from pathlib import Path

import pytest

from voice_teaching import claude_interpreter as ci
from voice_teaching import jbi, updown
from voice_teaching.session import Session
from voice_teaching.web.flow import WebFlow

ROOT = Path(__file__).resolve().parent.parent
DEMO_C = (ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes()
# 合成ジョブ（samples/dummy）と1バイトでも違うジョブ＝実機のジョブと同じ扱い（シミュレーション用にも出力しない）
NOT_SYNTHETIC = DEMO_C.replace(b"///DATE 2026/11/02 18:30", b"///DATE 2026/11/02 18:31")
assert NOT_SYNTHETIC != DEMO_C


def start(cfg, data=DEMO_C, select="C00005"):
    w = WebFlow(interpreter=ci.MockInterpreter(), updown_config=cfg)
    sid = w.load(data)["session_id"]
    w.select(sid, select)
    return w, sid


def outcome(w, sid):
    st = w.state(sid)["state"]
    c = st["candidate"]
    a = w.approve(sid, "唐川", candidate_gen=w.candidate_gen_of(sid))
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    return (None if c is None else (c["target"], c["delta"], bool(c["blocked_reason"])),
            st["can_approve"], bool(a.get("ok")), bool(x.get("ok")))


@pytest.mark.parametrize("text", ["21ミリ下げて", "Zマイナス21ミリ", "下に25mm", "Z座標を21mm下げて"])
def test_limit_20mm_stays_with_confirmed_updown(text, make_updown):
    w, sid = start(make_updown())
    assert w.state(sid)["state"]["updown"]["status"] == "confirmed"
    w.hear(sid, text)
    cand, can_approve, approved, exported = outcome(w, sid)
    assert cand is None or cand[2] is True, cand  # 候補があっても参考の表示だけ（実機用の検査で止まった）
    assert can_approve is False and approved is False and exported is False


@pytest.mark.parametrize("position", ["C00000", "C00007"])
@pytest.mark.parametrize("text", ["上に3ミリ", "Zプラス3ミリ"])
def test_positions_refused_by_jbi_stay_refused_with_confirmed_updown(position, text, make_updown, tmp_path):
    # 変更できない位置（MOVJ の隣など。jbi の検査）は、上下の設定があっても変えられない。設定なしと同じ結果
    with pytest.raises(jbi.JbiError):
        jbi.parse_bytes(DEMO_C).plan_shift(position, "Z", 3.0)
    got = {}
    for mode, cfg in (("confirmed", make_updown()), ("none", tmp_path / "none.json")):
        w, sid = start(cfg, select=position)
        w.hear(sid, text if mode == "confirmed" else "Zプラス3ミリ")
        got[mode] = outcome(w, sid)
        cand, can_approve, approved, exported = got[mode]
        assert cand is None or cand[2] is True
        assert can_approve is False and approved is False and exported is False
    assert got["confirmed"] == got["none"]


def test_not_synthetic_job_is_not_exported_with_confirmed_updown(make_updown):
    # 上下の設定を、そのジョブ（合成ジョブではない）で作って確認済みにしても、シミュレーション用にも出力しない
    cfg = make_updown(job=NOT_SYNTHETIC, name="not_synthetic.json")
    w, sid = start(cfg, data=NOT_SYNTHETIC)
    assert w.state(sid)["state"]["updown"]["status"] == "confirmed"
    r = w.hear(sid, "3ミリ下げて")
    assert r["state"]["candidate"]["target"] == "C00005 Z"  # 上・下を Z として読むところだけが設定で変わる
    assert w.approve(sid, "唐川", candidate_gen=w.candidate_gen_of(sid))["ok"] is True  # 承認はボタンだけ（ここでは API を直接呼ぶ）
    x = w.export(sid, candidate_gen=w.candidate_gen_of(sid))
    assert x["ok"] is False and "合成ジョブ以外は出力しません" in x["error"]


@pytest.mark.parametrize("position", ["C00000", "C00001", "C00002", "C00003", "C00004", "C00005", "C00006", "C00007"])
@pytest.mark.parametrize("delta", [-25.0, -21.0, -20.0, -3.0, 0.0, 3.0, 20.5])
def test_session_checks_are_the_same_with_or_without_updown(position, delta, updown_ok):
    # 軸の文字で言った指示（上下の設定に関係しない）では、設定があってもなくても、候補・止める理由が同じ
    def try_it(check):
        s = Session.from_file(ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI", updown_check=check)
        s.select(position)
        text = f"Z{'プラス' if delta >= 0 else 'マイナス'}{abs(delta):g}ミリ"
        r = s.hear(text)
        if r.status == "ok":
            r = s.propose({"kind": "shift", "position": position, "axis": "Z", "delta_mm": delta})
        return r.status, r.message, r.candidate_made, r.blocked_reason
    assert try_it(lambda job: updown_ok) == try_it(None)
