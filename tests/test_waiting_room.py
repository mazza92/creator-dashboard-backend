"""Out-of-free-pitches waiting room: momentum from real events, follow-up opens on day 4 only, drafts."""

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import psycopg2

from services import waiting_room as wr

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
SCHEMA = "waiting_room_t"


def _row(bid, days_ago, followed=False, opened=False, replied=False, name=None):
    return {"brand_id": bid, "brand_name": name or f"Brand {bid}", "sent_at": NOW - timedelta(days=days_ago),
            "followed_up": followed, "opened": opened, "replied": replied}


class FollowUp(unittest.TestCase):
    def test_not_due_before_day_4(self):
        f = wr.followup([_row(1, 2), _row(2, 1)], NOW)
        self.assertFalse(f["due"])
        self.assertEqual(f["brand_id"], 1)
        self.assertEqual(f["day"], 2)
        self.assertEqual(f["opens_on"], "Nov 23")

    def test_due_from_day_4(self):
        f = wr.followup([_row(1, 5, opened=True), _row(2, 1)], NOW)
        self.assertTrue(f["due"])
        self.assertEqual(f["brand_id"], 1)
        self.assertTrue(f["opened_kit"])

    def test_exactly_day_4_is_due(self):
        self.assertTrue(wr.followup([_row(1, 4)], NOW)["due"])

    def test_replied_or_followed_up_pitches_are_skipped(self):
        self.assertIsNone(wr.followup([_row(1, 6, replied=True), _row(2, 6, followed=True)], NOW))
        f = wr.followup([_row(1, 6, replied=True), _row(2, 3)], NOW)
        self.assertEqual(f["brand_id"], 2)
        self.assertFalse(f["due"])


class Db(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                    user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, hero_product text, source text,
                description text, website text, category text, logo_url text, contact_email text DEFAULT 'pr@x.co',
                email_status text, email_bounce_count int DEFAULT 0);
            CREATE TABLE polly_timeline_events (id serial PRIMARY KEY, creator_id int, brand_id int, event_type text,
                event_data jsonb, occurred_at timestamptz);
            INSERT INTO pr_brands (id, brand_name, hero_product, source, description, website, category)
                VALUES (1, 'Glowly', 'Dew Serum', 'meta_ads_library', '', '', 'Skincare'),
                (2, 'Kinfit', 'Mystery Bar', 'ai', 'Protein snacks', '', 'Fitness'),
                (3, 'Oldie', NULL, 'ai', '', '', 'Home');
            INSERT INTO pr_brands (id, brand_name, category, email_status) VALUES (4, 'Bounced', 'Fitness', 'bounced');
            INSERT INTO pr_brands (id, brand_name, category, contact_email) VALUES (5, 'NoMail', 'Fitness', '');
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = cls._conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    @staticmethod
    def _conn():
        return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                                options=f"-c search_path={SCHEMA}")

    def test_pitched_brands_counts_opens_replies(self):
        c = self._conn()
        cur = c.cursor()
        cur.execute("DELETE FROM polly_timeline_events")
        ev = [(1, "pitch_sent", 5), (1, "pitch_sent", 5), (1, "portfolio_viewed", 3), (2, "pitch_sent", 2),
              (2, "brand_replied_interested", 1), (3, "pitch_sent", 80), (3, "portfolio_viewed", 1)]
        for bid, kind, ago in ev:
            cur.execute("INSERT INTO polly_timeline_events (creator_id, brand_id, event_type, event_data, occurred_at) "
                        "VALUES (7, %s, %s, '{}', %s)", (bid, kind, NOW - timedelta(days=ago)))
        c.commit()
        rows = wr.pitched_brands(c, 7, NOW)
        # Brand 3 was pitched outside the 45-day window: a kit view alone doesn't make it a pitch.
        self.assertEqual([r["brand_id"] for r in rows], [1, 2])
        self.assertTrue(rows[0]["opened"])
        self.assertTrue(rows[1]["replied"])
        self.assertFalse(rows[0]["followed_up"])  # the duplicate send log on day 5 isn't a follow-up
        f = wr.followup(rows, NOW)
        self.assertEqual((f["brand_name"], f["due"]), ("Glowly", True))
        c.close()

    def test_drafts_never_name_an_unverified_product(self):
        c = self._conn()
        c.cursor().execute("DELETE FROM polly_timeline_events")
        c.commit()
        plan = [{"brand_id": 1, "brand_name": "Glowly", "planned_for": date(2026, 11, 24), "offer": "paid",
                 "meta_ads": True},
                {"brand_id": 2, "brand_name": "Kinfit", "planned_for": date(2026, 11, 25), "offer": "gifted",
                 "meta_ads": False},
                {"brand_id": 3, "brand_name": "Old", "planned_for": date(2020, 1, 1), "offer": "gifted",
                 "meta_ads": False}]
        plan += [{"brand_id": i, "brand_name": "X", "planned_for": date(2026, 11, 26), "offer": "gifted",
                  "meta_ads": False} for i in (4, 5)]
        with mock.patch("services.creator_tracker.planned_rows", return_value=plan), \
             mock.patch("services.pitch_writer.creator_intro_facts", return_value={"niche": "skincare"}), \
             mock.patch("services.waiting_room.datetime") as dt:
            dt.now.return_value = NOW
            dt.combine = datetime.combine
            dt.min = datetime.min
            out = wr.drafts(c, 7, "Ana")
        # Past days, bounced and email-less brands are left out: Autopilot couldn't send to them.
        self.assertEqual(out["plan_count"], 2)
        glow, kin = out["items"]
        self.assertTrue(glow["product_verified"])
        self.assertIn("Dew Serum", glow["subject"] + glow["opener"])
        self.assertFalse(kin["product_verified"])
        self.assertNotIn("Mystery Bar", kin["subject"] + kin["opener"])
        self.assertIn("your fitness range", kin["opener"])
        self.assertEqual(glow["planned_for"], "Nov 24")
        c.close()


if __name__ == "__main__":
    unittest.main()


class EventRoute(unittest.TestCase):
    def test_logs_known_actions_only(self):
        import routes.polly as R
        from app import app
        logged = []
        conn = mock.MagicMock()
        with mock.patch.object(R, "_creator_auth", return_value=(7, conn, {})), \
             mock.patch.object(R, "_log_polly_event", side_effect=lambda *a: logged.append(a)):
            client = app.test_client()
            ok = client.post("/api/polly/waiting-room/event",
                             json={"action": "checklist", "item": "rates", "source": "pill", "followup_due": False})
            bad = client.post("/api/polly/waiting-room/event", json={"action": "drop table"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(logged, [(7, "waiting_room", {"action": "checklist", "source": "pill", "item": "rates",
                                                        "followup_due": False})])


class AutoFollowUp(unittest.TestCase):
    def test_gmail_sent_pitch_says_polly_follows_up(self):
        auto = {"followup_status": "scheduled", "followup_due_at": NOW + timedelta(days=2), "followups_sent": 0}
        r = _row(1, 2, name="Elite Eleven")
        r["auto"] = auto
        f = wr.followup([r], NOW)
        self.assertTrue(f["auto"])
        self.assertFalse(f["due"])
        self.assertEqual(f["opens_on"], "Nov 23")

    def test_manual_pitch_wins_over_auto(self):
        a = _row(1, 5)
        a["auto"] = {"followup_status": "scheduled", "followup_due_at": NOW, "followups_sent": 0}
        f = wr.followup([a, _row(2, 5)], NOW)
        self.assertEqual((f["brand_id"], f["auto"], f["due"]), (2, False, True))

    def test_auto_already_sent_shows_nothing(self):
        a = _row(1, 5)
        a["auto"] = {"followup_status": "scheduled", "followup_due_at": NOW + timedelta(days=5), "followups_sent": 1}
        self.assertIsNone(wr.followup([a], NOW))
