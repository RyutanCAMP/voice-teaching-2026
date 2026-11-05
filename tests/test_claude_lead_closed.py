"""統合（fix/p0-reload-close × Claude の言い添え）：読み直しで閉じた画面には、一言を足さない・返さない。"""
from __future__ import annotations

from test_claude_lead import OK_LEAD, LeadClient, ROOT, make_flow, start


class ReloadDuring(LeadClient):
    """Claude を待つ間に、別の画面でジョブを読み直す（古い画面は閉じられる）。"""

    def __init__(self, flow_box):
        super().__init__(OK_LEAD)
        self.flow_box = flow_box

    def create(self, **kw):
        self.flow_box[0].load((ROOT / "samples" / "dummy" / "DEMO_C_PLACE.JBI").read_bytes())
        return super().create(**kw)


def test_reload_while_waiting_for_lead(tmp_path):
    box = []
    c = ReloadDuring(box)
    f = make_flow(tmp_path, c)
    box.append(f)
    sid = start(f)
    out = f.talk(sid, "Yに10mm")
    assert c.sent  # 呼んだ
    assert "hear" not in out or not (out.get("hear") or {}).get("claude_lead")
    assert "終わりました" in str(out)


def test_closed_screen_does_not_call(tmp_path):
    c = LeadClient(OK_LEAD)
    f = make_flow(tmp_path, c)
    sid = start(f)
    start(f)  # 新しいジョブ：前の画面は閉じる
    out = f.talk(sid, "Yに10mm")
    assert c.sent == []
    assert "終わりました" in str(out)
