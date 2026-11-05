"""Autopilot keeps running for everyone when one creator's data or account is broken."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

from flask import Flask

import routes.polly_autopilot as rpa
import services.polly_gmail as pg
import services.polly_memory as pm

NOW = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)


def test_thread_saves_handle_database_decimals_and_dates():
    wrapped = pm._Json({"brands": [{"fit": Decimal("0.83"), "seen": datetime(2026, 11, 5)}]})
    dumped = json.loads(wrapped.dumps(wrapped.adapted))
    assert dumped == {"brands": [{"fit": 0.83, "seen": "2026-11-05T00:00:00"}]}


def test_one_failing_creator_does_not_stop_the_fill():
    app = Flask(__name__)
    app.secret_key = "t"
    rows = [{"creator_id": 1, "need": 3, "fill_blocked": None}, {"creator_id": 2, "need": 3, "fill_blocked": None}]
    marked = []

    def fill(conn, row, creator, budget):
        if row["creator_id"] == 1:
            raise TypeError("Object of type Decimal is not JSON serializable")
        return ["Glosshouse"], None, 1, False

    with app.app_context(), \
            mock.patch.object(rpa, "fill_candidates", return_value=rows), \
            mock.patch.object(rpa, "_is_pro", return_value=True), \
            mock.patch.object(rpa, "_load_creator", side_effect=lambda c, cid: {"id": cid, "user_id": cid * 10}), \
            mock.patch.object(rpa, "_fill_creator", side_effect=fill), \
            mock.patch.object(rpa, "mark_filled", side_effect=lambda c, cid, at, b: marked.append((cid, b))), \
            mock.patch.object(rpa, "claim_unannounced_drafts", return_value=[]):
        out = rpa.auto_fill(mock.MagicMock(), now=NOW, budget=4, note_fn=lambda *a, **k: None,
                            daily_fn=lambda *a, **k: None)
    assert out["drafted"] == 1
    assert (1, "error") in marked


def test_no_gmail_inbox_marks_the_account_not_each_pitch():
    resp = mock.MagicMock(status_code=400, content=b"x")
    resp.json.return_value = {"error": {"message": "Mail service not enabled"}}
    with mock.patch.object(pg, "load_account", return_value={"email": "rensns@aol.com", "revoked_at": None}), \
            mock.patch.object(pg, "_access_token", return_value="tok"), \
            mock.patch.object(pg, "build_raw", return_value={"raw": "r", "message_id": "m"}), \
            mock.patch.object(pg.requests, "post", return_value=resp), \
            mock.patch.object(pg, "_mark_revoked") as revoke:
        try:
            pg.send_email(mock.MagicMock(), 516, "hi@brand.com", "s", "b")
            raise AssertionError("should raise")
        except pg.GmailError as err:
            assert err.revoked and "no Gmail inbox" in str(err)
    revoke.assert_called_once_with(mock.ANY, 516, pg.NO_INBOX)


def test_reconnect_requeues_account_failures():
    from services import polly_autopilot as pa
    conn = mock.MagicMock()
    with mock.patch.object(pa, "ensure_autopilot_tables"):
        pa.requeue_account_failures(conn, 516)
    sql = conn.cursor.return_value.execute.call_args.args[0]
    assert "status = 'draft'" in sql and "mail service not enabled" in sql.lower()


def test_autopilot_ships_to_the_signup_region_when_no_city():
    loc = rpa.autopilot_location({"regions": '["US"]'}, {}, {})
    assert loc == {"city": "", "country": "United States", "display": "United States", "needs_location": False}
    assert rpa.autopilot_location({"regions": '["North America"]'}, {}, {})["needs_location"] is True
    addr = {"shipping_address": {"city": "Austin", "country": "United States"}}
    assert rpa.autopilot_location(addr, {}, {})["display"].startswith("Austin")


def test_health_copy_for_no_inbox_and_pro_only_misses():
    import inspect
    from services import autopilot_delivery, autopilot_health
    mail = autopilot_health.gmail_email("Renee", {"no_inbox": True, "address": "rensns@aol.com", "waiting": 23})
    assert "rensns@aol.com" in mail["html"] and "no Gmail inbox" in mail["html"]
    note = autopilot_health.chat_note(autopilot_health.GMAIL_KIND, {"no_inbox": True, "waiting": 23})
    assert "no Gmail inbox" in note["text"]
    assert "subscription_tier = 'pro'" in inspect.getsource(autopilot_delivery.record_misses)


def test_pitch_preference_rewrite_handles_decimals():
    from services import polly_pitch_edit as pe
    import services.polly_brain as pb
    sent = {}

    class FakeGemini:
        def call(self, payload):
            sent["payload"] = payload
            raise pb.BrainUnavailable("off")

    with mock.patch.object(pb, "_Gemini", FakeGemini):
        out = pe._llm_apply_prefs({"subject": "s", "body": "b"}, {"tone": "friendly"},
                                  {"engagement_rate": Decimal("4.20"), "since": datetime(2026, 1, 2)})
    assert out == {}
    text = sent["payload"]["contents"][0]["parts"][0]["text"]
    assert json.loads(text)["facts"] == {"engagement_rate": 4.2, "since": "2026-01-02T00:00:00"}


def test_round_robin_gives_newer_creators_a_turn():
    app = Flask(__name__)
    app.secret_key = "t"
    rows = [{"creator_id": 1, "need": 15, "fill_blocked": None}, {"creator_id": 2, "need": 15, "fill_blocked": None}]
    marked = []
    with app.app_context(), \
            mock.patch.object(rpa, "fill_candidates", return_value=rows), \
            mock.patch.object(rpa, "_is_pro", return_value=True), \
            mock.patch.object(rpa, "_load_creator", side_effect=lambda c, cid: {"id": cid, "user_id": cid}), \
            mock.patch.object(rpa, "_fill_creator", return_value=(["A", "B"], None, 2, False)), \
            mock.patch.object(rpa, "mark_filled", side_effect=lambda c, cid, at, b: marked.append(cid)), \
            mock.patch.object(rpa, "claim_unannounced_drafts", return_value=[]):
        rpa.auto_fill(mock.MagicMock(), now=NOW, budget=2, note_fn=lambda *a, **k: None, daily_fn=lambda *a, **k: None)
    assert marked == [1]  # creator 1 drafted 2 of 15 and still goes to the back of the line
