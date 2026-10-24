from datetime import datetime, timedelta, timezone

import pytest

from services import roster_delivery as rd
from tests.test_roster_delivery import FakeConn, FakeCursor, _pick_row


@pytest.mark.parametrize(
    "age,last,expected",
    [
        (2, 0, None),
        (5.1, 0, 1),
        (5.1, 1, None),
        (rd.CONTENT_DUE_DAYS, 1, 2),
        (rd.CONTENT_OVERDUE_DAYS + 1, 0, 3),
        (rd.CONTENT_OVERDUE_DAYS + 1, 3, None),
    ],
)
def test_content_reminder_due_sends_latest_unsent(age, last, expected):
    assert rd.content_reminder_due(age, last) == expected


def test_content_due_at_is_ten_days_after_shipping():
    shipped = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert rd.content_due_at(shipped) == shipped + timedelta(days=rd.CONTENT_DUE_DAYS)
    assert rd.content_due_at(None) is None


def test_reminder_emails_escalate_to_paused_applications():
    row = _pick_row(shipped_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    first = rd.build_content_reminder_email(row, 1)
    assert first["subject"] == "Your ReGlow content is due Oct 11"
    assert "content_due_1" in first["url"]
    overdue = rd.build_content_reminder_email(row, 3)
    assert "paused" in overdue["subject"]
    assert "can't apply for new gifted PR" in overdue["html"]
    assert "\u2014" not in overdue["html"]
    assert "paused" in rd.content_reminder_note(row, 3)


def test_send_content_reminders_logs_then_emails_and_notes(monkeypatch):
    monkeypatch.setattr(rd, "brief_select_sql", lambda cursor, alias="camp": "NULL::jsonb AS brief")
    shipped = datetime.now(timezone.utc) - timedelta(days=6)
    rows = [
        _pick_row(id=1, creator_id=7, brand_id=9, shipped_at=shipped, age_days=6.0, last_sent=None),
        _pick_row(id=2, creator_id=8, brand_id=9, shipped_at=shipped, age_days=6.0, last_sent=1),
    ]
    cur = FakeCursor([rows, None])
    sent, notes = [], []

    def send(to, subject, html, kind):
        sent.append(kind)
        return {"success": True}

    stats = rd.send_content_reminders(
        cur, FakeConn(), send_fn=send, note_fn=lambda conn, cid, text, chips: notes.append(cid)
    )
    assert stats == {"sent": 1, "failed": 0, "preview": []}
    assert sent == ["roster_content_due_1"]
    assert notes == [7]
    logged = [p for s, p in cur.executed if "INSERT INTO brand_pr_events" in s]
    assert logged and logged[0][2] == rd.CONTENT_REMINDER_EVENT


def test_overdue_delivery_returns_oldest_late_pick(monkeypatch):
    monkeypatch.setattr(rd, "public_column_exists", lambda *a: True)
    cur = FakeCursor([{"application_id": 4, "brand_name": "ReGlow"}])
    assert rd.overdue_delivery(cur, 7) == {"application_id": 4, "brand_name": "ReGlow"}
    assert cur.executed[0][1] == (7, rd.CONTENT_OVERDUE_DAYS)


def test_card_shows_due_date_only_while_waiting_on_content():
    from brand_pr_roster_routes import _content_due_fields

    shipped = datetime.now(timezone.utc) - timedelta(days=rd.CONTENT_DUE_DAYS + 1)
    late = _content_due_fields({"shipped_at": shipped}, "ships")
    assert late["content_overdue"] is True
    assert late["content_due_at"].startswith((shipped + timedelta(days=rd.CONTENT_DUE_DAYS)).date().isoformat())
    assert _content_due_fields({"shipped_at": shipped}, "posted") == {"content_due_at": None, "content_overdue": False}


def test_paywall_hit_is_logged_before_402():
    from flask import Flask
    from brand_pr_roster_routes import _paywall_response

    cur, conn = FakeCursor(), FakeConn()
    conn.close = lambda: None
    with Flask(__name__).test_request_context():
        body, status = _paywall_response(cur, conn, {"id": 3, "brand_id": 9}, "pick_limit", 44)
    assert status == 402
    assert body.get_json()["reason"] == "pick_limit"
    sql, params = cur.executed[0]
    assert "INSERT INTO brand_pr_events" in sql
    assert params[2] == "roster_paywall"
    assert params[4].adapted == {"campaign_id": 3, "reason": "pick_limit", "application_id": 44}
    assert conn.commits == 1


class TestInstantPublish:
    def test_inbound_submissions_stay_pending(self):
        import opportunities_routes as routes

        assert not routes.can_publish_instantly(None, {"brand_name": "Newbrand", "brand_website": "newbrand.co"})
        assert not routes.can_publish_instantly(None, {"brand_name": "Glossier", "brand_website": "glossier.com"})
        assert not routes.can_publish_instantly(None, {"brand_name": "X", "brand_website": "amazon.com/dp/1"})
