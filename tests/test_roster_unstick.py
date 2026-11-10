"""Locked rosters don't stay stuck: a longer ship cycle with one-click answers, and creators can say their box came."""

from datetime import datetime, timedelta, timezone
from unittest import mock

from services import roster_delivery as rd

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def _row(age, sent=(), last_days_ago=None, snoozed=None):
    return {"age_days": age, "sent_triggers": list(sent),
            "last_at": NOW - timedelta(days=last_days_ago) if last_days_ago is not None else None,
            "snoozed_until": snoozed}


def test_long_cycle_and_quiet_rosters_catch_up_to_the_latest_step():
    assert rd.next_ship_reminder(_row(1), now=NOW) is None
    assert rd.next_ship_reminder(_row(2.5), now=NOW) == 1
    assert rd.next_ship_reminder(_row(9.5, [21, 22], 4), now=NOW) == 3
    assert rd.next_ship_reminder(_row(9.5, [21, 22], 1), now=NOW) is None  # 2-day gap
    # Nuvaskin: locked 34 days ago, last reminder (#2) 23 days ago -> the final one, not #3 and #4 first
    assert rd.next_ship_reminder(_row(34, [21, 22], 23), now=NOW) == 5
    assert rd.next_ship_reminder(_row(40, [21, 22, 25], 6), now=NOW) is None  # final already sent


def test_snooze_holds_reminders():
    assert rd.next_ship_reminder(_row(10, [21, 22], 5, snoozed=NOW + timedelta(days=3)), now=NOW) is None
    assert rd.next_ship_reminder(_row(10, [21, 22], 5, snoozed=NOW - timedelta(days=1)), now=NOW) == 3


def test_late_emails_offer_one_click_answers():
    row = {"brand_name": "Nail Pop", "pick_count": 5, "token": "tok", "locked_at": NOW, "contact_name": "Ana"}
    for n in (3, 4, 5):
        mail = rd.build_ship_reminder_email(row, n)
        assert "confirm=all" in mail["url"] and "step=ship" in mail["url"]
        assert "/api/brand-pr/r/tok/snooze-ship?days=7" in mail["html"]
        assert "I&#39;ve shipped them" in mail["html"] or "I've shipped them" in mail["html"]
        assert mail["trigger_number"] == rd.SHIP_REMINDERS[n - 1][1]
    assert "last reminder" in rd.build_ship_reminder_email(row, 5)["html"]


def test_arrival_token_round_trip_and_tamper():
    tok = rd.arrival_token(42, 7)
    assert rd.read_arrival_token(tok) == (42, 7)
    body, sig = tok.split(".")
    assert rd.read_arrival_token(body + "." + "0" * 32) is None
    assert rd.read_arrival_token("garbage") is None


def test_arrival_email_links_to_the_confirm_page():
    mail = rd.build_arrival_check_email({"id": 42, "creator_id": 7, "brand_name": "ReGlow", "first_name": "maya"}, 1)
    assert mail["subject"] == "Did your ReGlow box arrive?"
    assert "/api/brand-pr/arrived?t=" in mail["url"]


def test_confirm_arrival_marks_shipped_and_closes_roster_when_everyone_has_it():
    cur = mock.MagicMock()
    cur.fetchone.side_effect = [
        {"id": 42, "brand_id": 3, "campaign_id": 118, "shipped_at": None, "brand_name": "ReGlow",
         "campaign_status": "locked"},
        {"n": 0},
    ]
    res = rd.confirm_arrival(cur, 42, 7)
    assert res == {"ok": True, "already": False, "brand_name": "ReGlow", "all_shipped": True}
    sqls = " ".join(c.args[0] for c in cur.execute.call_args_list)
    assert "SET shipped_at = NOW()" in sqls and "status = 'shipped'" in sqls


def test_confirm_arrival_is_idempotent():
    cur = mock.MagicMock()
    cur.fetchone.return_value = {"id": 42, "brand_id": 3, "campaign_id": 118, "shipped_at": NOW,
                                 "brand_name": "ReGlow", "campaign_status": "locked"}
    assert rd.confirm_arrival(cur, 42, 7)["already"] is True
    assert cur.execute.call_count == 1
