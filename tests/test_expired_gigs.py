"""Briefs past closes_at: the daily job closes them, and the creator gig feed and gig autopilot ignore them."""

import inspect
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2

from services import gig_autopilot as ga
from services.gig_freshness import close_expired_opportunities

SCHEMA = "expired_gigs_t"

DDL = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
SET search_path TO {SCHEMA};
CREATE TABLE opportunities (
    id int PRIMARY KEY, status text, closes_at timestamptz, pr_brand_id int,
    brand_name text, brand_email text, brand_website text, brand_category text, creator_niches text[],
    follower_ranges text[], campaign_description text, pr_value_usd numeric, listing_brief jsonb,
    additional_notes text, published_at timestamptz, created_at timestamptz DEFAULT NOW(), product_name text);
CREATE TABLE opportunity_applications (id serial PRIMARY KEY, opportunity_id int, creator_id int,
                                       applied_at timestamptz, status text);
CREATE TABLE polly_autopilot_queue (id serial PRIMARY KEY, creator_id int, pitch_meta jsonb);
"""

SCANNER = "[scanner:craigslist] | contact=site_email"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class ExpiredGigsDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:  # no local database
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(DDL)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def setUp(self):
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("TRUNCATE opportunities, opportunity_applications, polly_autopilot_queue")
        now = datetime.now(timezone.utc)
        rows = [
            # Like the GLO brief: closed 2026-09-13, still 'live'.
            (1081, "live", datetime(2026, 9, 13, tzinfo=timezone.utc), "GLO"),
            (1082, "live", now - timedelta(minutes=5), "JustClosed"),
            (1083, "live", now + timedelta(days=5), "Open"),
            (1084, "live", None, "NoDeadline"),
            (1085, "paused", now - timedelta(days=3), "Paused"),
        ]
        for oid, status, closes, name in rows:
            cur.execute(
                """INSERT INTO opportunities (id, status, closes_at, brand_name, brand_email, brand_category,
                                              creator_niches, additional_notes, published_at)
                   VALUES (%s, %s, %s, %s, %s, 'Fitness', ARRAY['Fitness'], %s, %s)""",
                (oid, status, closes, name, f"hi@{name.lower()}.com", SCANNER, now - timedelta(days=1)))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _status(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, status FROM opportunities ORDER BY id")
        return dict(cur.fetchall())

    def test_job_closes_live_rows_past_closes_at(self):
        self.assertEqual(close_expired_opportunities(self.conn), [1081, 1082])
        self.assertEqual(self._status(), {1081: "closed", 1082: "closed", 1083: "live", 1084: "live",
                                          1085: "paused"})
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'live' AND closes_at <= NOW()")
        self.assertEqual(cur.fetchone()[0], 0)
        self.assertEqual(close_expired_opportunities(self.conn), [])  # idempotent

    def test_dry_run_changes_nothing(self):
        self.assertEqual(close_expired_opportunities(self.conn, dry_run=True), [1081, 1082])
        self.assertEqual(self._status()[1081], "live")

    def test_autopilot_never_picks_an_expired_brief(self):
        # Before the job has run, the expired rows are still 'live': the query itself must skip them.
        ids = {o["id"] for o in ga.candidates(self.conn, creator_id=7)}
        self.assertEqual(ids, {1083, 1084})

    def test_queued_pitch_for_an_expired_brief_is_not_sent(self):
        self.assertTrue(ga.gig_closed(self.conn, {"origin": ga.GIG_ORIGIN, "gig_id": 1081}))
        self.assertTrue(ga.gig_closed(self.conn, {"origin": ga.GIG_ORIGIN, "gig_id": 1085}))
        self.assertTrue(ga.gig_closed(self.conn, {"origin": ga.GIG_ORIGIN, "gig_id": 9999}))
        self.assertFalse(ga.gig_closed(self.conn, {"origin": ga.GIG_ORIGIN, "gig_id": 1083}))
        self.assertFalse(ga.gig_closed(self.conn, {"origin": "brand_pitch"}))
        self.assertFalse(ga.gig_closed(self.conn, None))


class FeedFiltersTests(unittest.TestCase):
    def test_creator_feed_query_filters_closes_at(self):
        """The paid gigs board (_paid_board) and the Polly gig cards both read fetch_live_opportunity_cards."""
        import opportunities_routes as orr
        sql = inspect.getsource(orr.fetch_live_opportunity_cards)
        self.assertIn("(closes_at IS NULL OR closes_at > NOW())", sql)
        self.assertIn("fetch_live_opportunity_cards(creator_id)", inspect.getsource(orr._paid_board))

    def test_apply_rejects_an_expired_brief(self):
        import opportunities_routes as orr
        src = inspect.getsource(orr.apply_opportunity)
        self.assertIn("(closes_at IS NOT NULL AND closes_at <= NOW()) AS expired", src)
        self.assertIn("opp.get('expired')", src)


class CronRouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from routes.polly import polly_bp
        app = Flask(__name__)
        app.register_blueprint(polly_bp)
        self.client = app.test_client()

    def test_needs_the_secret(self):
        with mock.patch("services.admin_auth.cron_request_ok", return_value=False):
            self.assertEqual(self.client.get("/api/polly/cron/close-expired-gigs").status_code, 401)

    def test_runs_the_job(self):
        conn = mock.MagicMock()
        with mock.patch("services.admin_auth.cron_request_ok", return_value=True), \
                mock.patch("pr_crm_routes.get_db_connection", return_value=conn), \
                mock.patch("services.gig_freshness.close_expired_opportunities", return_value=[1081]) as job:
            r = self.client.get("/api/polly/cron/close-expired-gigs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["closed"], 1)
        job.assert_called_once_with(conn, dry_run=False)
        conn.close.assert_called_once()

    def test_wired_daily(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        crons = json.load(open(os.path.join(root, "vercel.json")))["crons"]
        self.assertIn({"path": "/api/polly/cron/close-expired-gigs", "schedule": "15 3 * * *"}, crons)


if __name__ == "__main__":
    unittest.main()
