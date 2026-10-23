from datetime import datetime, timezone

from services import founder_wins as fw
from services import polly_alerts as pa
from services import polly_tracker as pt
from services.checkin_links import ONE_TAP_NEXT, read_token


def test_interested_reply_task_gets_its_own_nudge():
    task = {"type": "reply_needed", "metadata": {"after": "interested"}}
    assert pt._nudge_key(task) == "reply_interested"
    assert pt._nudge_key({"type": "reply_needed", "metadata": {}}) == "reply_needed"
    assert "{brand}" in pt.NUDGE_COPY["reply_interested"]
    assert [c["id"] for c in pt.NUDGE_CHIPS["reply_interested"]] == ["paid_reply", "reply_sent"]


def test_interested_creates_24h_reply_check_once(monkeypatch):
    created = []
    monkeypatch.setattr(pt, "close_tasks", lambda *a, **k: 0)
    monkeypatch.setattr(pt, "add_event", lambda *a, **k: 1)
    monkeypatch.setattr(pt, "create_task", lambda conn, cid, kind, **k: created.append((kind, k)) or len(created))
    monkeypatch.setattr(pt, "_open_tasks", lambda *a, **k: [])
    monkeypatch.setattr(pt, "contact_logged", lambda *a, **k: True)
    pt.apply_lifecycle_intent(None, 7, {"intent": "brand_replied_interested", "brand_id": 3, "brand": "Glow"})
    kinds = [k for k, _ in created]
    assert kinds == ["pr_shipped", "reply_needed"]
    assert created[1][1]["metadata"] == {"brand_name": "Glow", "after": "interested"}

    created.clear()
    monkeypatch.setattr(pt, "_open_tasks", lambda *a, **k: [{"id": 1}])
    pt.apply_lifecycle_intent(None, 7, {"intent": "brand_replied_interested", "brand_id": 3, "brand": "Glow"})
    assert [k for k, _ in created] == ["pr_shipped"]


def test_reply_sent_closes_the_check(monkeypatch):
    closed, events = [], []
    monkeypatch.setattr(pt, "close_tasks", lambda conn, cid, types, bid, **k: closed.append(types) or 1)
    monkeypatch.setattr(pt, "add_event", lambda conn, cid, kind, *a, **k: events.append(kind) or 1)
    res = pt.apply_task_chip(_Conn(), 7, "reply_sent", brand_id=3, brand_name="Glow")
    assert closed == [["reply_needed"]]
    assert events == ["creator_replied"]
    assert "Glow" in res["say"]


def test_interested_email_has_one_tap_buttons_and_pro_line_for_free(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    nudge = {"key": "reply_interested", "brand_name": "Glow", "brand_id": 3, "task_id": 11,
             "message": pt.NUDGE_COPY["reply_interested"].format(brand="Glow")}
    free = pa.nudge_email("Ana", nudge, creator_id=7, is_pro=False)
    assert free["subject"] == "Glow said yes. Did you reply?"
    assert "Write my reply" in free["html"] and "I already replied" in free["html"]
    assert "Get Pro" in free["html"]
    assert "Get Pro" not in pa.nudge_email("Ana", nudge, creator_id=7, is_pro=True)["html"]
    assert ONE_TAP_NEXT["reply_help"] == "paid_reply"


def test_wins_email_flags_free_creators_and_reply_status():
    rows = [
        {"creator_id": 5, "brand_name": "Glow", "first_name": "Ana Lee", "is_pro": False, "replied": False},
        {"creator_id": 6, "brand_name": "Bloom", "first_name": "", "is_pro": True, "replied": True},
    ]
    mail = fw.wins_email(rows, month=14)
    assert mail["subject"] == "2 brands said yes to a creator in the last 24h"
    assert "<strong>14</strong>" in mail["html"]
    assert "#5 Ana" in mail["html"] and "no reply logged yet" in mail["html"]
    assert "warmest Pro leads" in mail["html"]
    assert "@" not in mail["html"].split("Unsubscribe")[0].replace("&#x27;", "")


class _Cur:
    def __init__(self, row=None):
        self.row = row

    def execute(self, *a):
        pass

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row=None):
        self.row = row

    def cursor(self, **k):
        return _Cur(self.row)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_founder_wins_sends_once_a_day_only_with_wins(monkeypatch):
    monkeypatch.setattr(pa, "ensure_alert_tables", lambda conn: None)
    monkeypatch.setattr(fw, "month_count", lambda conn, now: 3)
    noon = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    early = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(fw, "load_wins", lambda conn, now: [])
    assert fw.email_founder_wins(_Conn(), dry_run=True, now=noon) == []
    monkeypatch.setattr(fw, "load_wins", lambda conn, now: [{"creator_id": 5, "brand_name": "Glow"}])
    assert fw.email_founder_wins(_Conn(), dry_run=True, now=early) == []
    assert fw.email_founder_wins(_Conn(row=(1,)), dry_run=True, now=noon) == []
    out = fw.email_founder_wins(_Conn(), dry_run=True, now=noon, test_email="t@x.co")
    assert out[0]["wins"] == 1 and out[0]["to"] == "t@x.co"
