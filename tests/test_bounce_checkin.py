"""Email bounce one-tap: Polly opens on the bounce outcome (new inbox / other routes), not a generic brand list."""

from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from services import checkin_links, polly_tracker


def test_bounce_link_opens_the_result_not_line_up():
    assert checkin_links.ONE_TAP_NEXT["checkin_bounced"] == "checkin_result"
    info = {"chip_id": "checkin_bounced", "brand_id": 7, "brand_name": "NordVPN"}
    q = parse_qs(urlparse(checkin_links.next_polly_url("https://app.example", info, "monday_checkin")).query)
    assert q["chip"] == ["checkin_result"] and q["brand_name"] == ["NordVPN"]


def _state(notes):
    chips = []
    return SimpleNamespace(notes=notes, add_chips=lambda c: chips.extend(c), chips=chips, user_text="", messages=[],
                           creator_id=1, balance={}, brands=[], final_say=None, task_chips=[], first="Ana",
                           creator={}, pitch_batch=None, scrape=None, db=lambda: None)


def test_run_chip_replays_the_stored_result_once():
    from routes import polly as rp
    stored = {"chip_id": "checkin_bounced", "brand_name": "NordVPN",
              "say": "**NordVPN**'s old inbox bounced. I found a verified one: pr@nordvpn.com.",
              "chips": [{"id": "resend_new_inbox", "label": "Resend to pr@nordvpn.com", "action": "generate_pitch"}]}
    st = _state({"checkin_result": stored})
    prior = rp._run_chip(st, "checkin_result", "checkin_result", {"brand_name": "NordVPN"})
    assert any(p.get("result", {}).get("said") == stored["say"] for p in prior)
    assert st.chips[0]["id"] == "resend_new_inbox"
    assert "checkin_result" not in st.notes
    # Landing again (or the result is gone): a plain acknowledgement, never a brand pitch.
    st2 = _state({})
    prior2 = rp._run_chip(st2, "checkin_result", "checkin_result", {"brand_name": "NordVPN"})
    assert "bounced" in prior2[0]["result"]["said"]
    assert st2.chips[0]["id"] == "line_up"


def test_bounce_reply_points_to_other_routes_when_no_new_inbox():
    cur = mock.MagicMock()
    cur.fetchone.return_value = ("https://nordvpn.com/creators", "nordvpn", None)
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    routes = polly_tracker._other_contact_routes(conn, 7)
    assert "PR form (https://nordvpn.com/creators)" in routes and "@nordvpn on Instagram" in routes
    cur.fetchone.return_value = (None, None, None)
    assert polly_tracker._other_contact_routes(conn, 7) == ""
