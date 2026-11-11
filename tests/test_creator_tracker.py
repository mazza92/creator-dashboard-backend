"""Goal + Tracker: goal suggestion and pitch math, deals from 'Brand replied', the board, calendar and free locking."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg2

from services import creator_tracker as ct

SCHEMA = "creator_tracker_t"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class PureMath(unittest.TestCase):
    def test_rate_bands(self):
        self.assertEqual(ct.rate_band(800), (8000, 15000))
        self.assertEqual(ct.rate_band(5000), (12000, 25000))
        self.assertEqual(ct.rate_band(20000), (20000, 40000))
        self.assertEqual(ct.rate_band(900000), (35000, 70000))

    def test_suggestion_has_three_rising_options(self):
        out = ct.suggest_goal({"followers": 5000, "niches": ["skincare"]})
        amounts = [o["amount_cents"] for o in out["options"]]
        self.assertEqual([o["id"] for o in out["options"]], ["starter", "suggested", "stretch"])
        self.assertEqual(amounts, sorted(amounts))
        self.assertEqual(out["recommended"], "suggested")
        self.assertEqual(out["basis"]["followers"], 5000)
        self.assertTrue(all(a % 5000 == 0 for a in amounts))

    def test_high_engagement_lifts_suggested_goal(self):
        base = ct.suggest_goal({"followers": 5000})["options"][1]["amount_cents"]
        boosted = ct.suggest_goal({"followers": 5000, "engagement_rate": 0.06})["options"][1]["amount_cents"]
        self.assertGreater(boosted, base)

    def test_pitch_math_default_rates(self):
        m = ct.pitch_math(60000, 5000, {"reply_rate": 0.08, "deal_rate": 0.30, "source": "default"})
        self.assertEqual(m["avg_deal_cents"], 18500)
        self.assertEqual(m["deals"], 4)
        self.assertEqual(m["pitches"], 167)  # 4 deals / (8% x 30%)

    def test_dollars_parse(self):
        self.assertEqual(ct._dollars_to_cents("$1,250"), 125000)
        self.assertEqual(ct._dollars_to_cents("300.5"), 30050)
        self.assertIsNone(ct._dollars_to_cents("lots"))
        self.assertIsNone(ct._dollars_to_cents(""))

    def test_deal_next_steps(self):
        self.assertTrue(ct._deal_next({"stage": "offered", "deal_type": "gifted"}, NOW)["your_move"])
        self.assertEqual(ct._deal_next({"stage": "negotiating", "deal_type": "paid"}, NOW)["label"],
                         "Answer with your rate")
        late = ct._deal_next({"stage": "posted", "deal_type": "paid", "payment_due": NOW.date() - timedelta(days=1)}, NOW)
        self.assertTrue(late["your_move"])
        ad = ct._deal_next({"stage": "posted", "deal_type": "gifted", "posted_at": NOW - timedelta(days=4)}, NOW)
        self.assertEqual(ad["action"], "ad_usage")


class TrackerDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE creators (id int PRIMARY KEY, user_id int, followers_count int, social_follower_count int,
                creator_niches jsonb, niche text, subscription_tier text, unlocks_tier text,
                pitches_sent_this_week int, last_pitch_reset date);
            CREATE TABLE creator_profile_data (user_id int, engagement_rate numeric);
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, logo_url text, category text);
            CREATE TABLE creator_pipeline (id serial PRIMARY KEY, creator_id int, brand_id int, pitched_at timestamptz,
                created_at timestamptz DEFAULT NOW(), send_confirmed boolean, follow_up_count int, followup_count int,
                expected_delivery date, content_deadline date, package_value numeric, pr_package_value numeric);
            CREATE TABLE polly_timeline_events (id serial PRIMARY KEY, creator_id int, brand_id int, task_id int,
                event_type text, event_label text, event_icon text, event_data jsonb, polly_notes text,
                occurred_at timestamptz DEFAULT NOW());
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        ct._READY = False
        import services.polly_autopilot as pa
        pa._TABLES_READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("""DROP TABLE IF EXISTS creator_goals, creator_deals, polly_autopilot, polly_autopilot_queue;
                       TRUNCATE creators, creator_profile_data, pr_brands, creator_pipeline, polly_timeline_events""")
        cur.execute("""INSERT INTO creators (id, user_id, followers_count, social_follower_count, creator_niches,
                           subscription_tier) VALUES (7, 70, 4000, 5000, '["skincare"]', 'free')""")
        cur.execute("""INSERT INTO pr_brands (id, brand_name, category) VALUES
                       (1, 'Glow Co', 'skincare'), (2, 'Dew Lab', 'skincare'), (3, 'Peak', 'skincare'),
                       (4, 'Halo', 'skincare'), (5, 'Moss', 'skincare'), (6, 'Rise', 'skincare')""")
        self.conn.commit()
        pa.ensure_autopilot_tables(self.conn)
        # The column check isn't schema-scoped, so another test schema can hide missing columns: add them here.
        for table, column, ddl in pa._ADDED_COLUMNS:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        self.conn.commit()
        self.balance = patch("pr_crm_routes.get_creator_unlock_balance",
                             return_value={"is_unlimited": False, "remaining": 1, "limit": 3})
        self.hot = patch.object(ct, "_hot_today", return_value=None)
        self.balance.start()
        self.hot.start()

    def tearDown(self):
        self.balance.stop()
        self.hot.stop()
        self.conn.close()

    def _queue(self, brand_id, status, sent_days_ago=None, scheduled_in=None, followups=0):
        cur = self.conn.cursor()
        sent = NOW - timedelta(days=sent_days_ago) if sent_days_ago is not None else None
        sched = NOW + timedelta(days=scheduled_in) if scheduled_in is not None else None
        cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                           status, sent_at, scheduled_for, followups_sent, followup_status, followup_due_at)
                       VALUES (7, %s, (SELECT brand_name FROM pr_brands WHERE id = %s), 'a@b.co', 's', 'b', %s, %s, %s,
                               %s, %s, %s)""",
                    (brand_id, brand_id, status, sent, sched, followups, "scheduled" if sent else None,
                     NOW + timedelta(days=2) if sent else None))
        self.conn.commit()

    def test_board_columns_from_queue_replies_and_deals(self):
        self._queue(1, "sent", sent_days_ago=3)
        self._queue(2, "sent", sent_days_ago=6, followups=1)
        self._queue(3, "approved", scheduled_in=1)
        self._queue(4, "approved", scheduled_in=2)
        self._queue(5, "draft")
        cur = self.conn.cursor()
        cur.execute("""INSERT INTO polly_timeline_events (creator_id, brand_id, event_type, occurred_at)
                       VALUES (7, 6, 'brand_replied_question', %s)""", (NOW - timedelta(days=1),))
        self.conn.commit()
        ct.record_deal(self.conn, 7, 2, "paid_offer", amount="250")

        out = ct.build_tracker(self.conn, 7, now=NOW)
        cols = {c["id"]: [card["brand_id"] for card in c["cards"]] for c in out["columns"]}
        self.assertEqual(cols["pitched"], [1])
        self.assertEqual(cols["negotiating"], [2])  # the deal wins over "followed up"
        self.assertEqual(cols["replied"], [6])
        self.assertEqual(sorted(cols["lined_up"]), [3, 4, 5])
        # Free with 1 credit left: one lined-up brand sends, the others show "Sends on Pro".
        locked = [c for c in out["columns"][0]["cards"] if c["locked"]]
        self.assertEqual(len(locked), 2)
        self.assertEqual(out["free"], {"pitches_left": 1, "limit": 3, "locked": 2})
        self.assertEqual(out["funnel"]["negotiating_cents"], 25000)
        self.assertEqual(out["goal"]["pipeline_cents"], 25000)
        kinds = {e["kind"] for e in out["calendar"]}
        self.assertTrue({"sent", "sched", "fu"} <= kinds)
        self.assertGreaterEqual(out["your_move_count"], 2)  # the question + answering the paid offer

    def test_goal_progress_counts_paid_and_gifted_this_month(self):
        ct.record_deal(self.conn, 7, 1, "paid_offer", amount=300)
        ct.record_deal(self.conn, 7, 1, "booked")
        ct.record_deal(self.conn, 7, 1, "posted")
        ct.record_deal(self.conn, 7, 1, "paid")
        ct.record_deal(self.conn, 7, 2, "gifted_offer", amount=60)
        ct.record_deal(self.conn, 7, 2, "arrived")
        ct.set_goal(self.conn, 7, 50000, "suggested")
        out = ct.build_tracker(self.conn, 7)
        g = out["goal"]
        self.assertTrue(g["set"])
        self.assertEqual((g["paid_cents"], g["gifted_cents"], g["total_cents"]), (30000, 6000, 36000))
        done = next(c for c in out["columns"] if c["id"] == "done")["cards"]
        booked = next(c for c in out["columns"] if c["id"] == "booked")["cards"]
        self.assertEqual([c["brand_id"] for c in done], [1])
        self.assertEqual([c["brand_id"] for c in booked], [2])  # box arrived: film and post
        self.assertEqual(booked[0]["next"]["action"], "post")

    def test_deal_updates_in_place_and_logs_events(self):
        d1 = ct.record_deal(self.conn, 7, 3, "gifted_offer")
        self.assertEqual(d1["value_cents"], ct.GIFTED_AVG_CENTS)
        d2 = ct.record_deal(self.conn, 7, 3, "paid_offer", amount="$400")
        self.assertEqual(d1["id"], d2["id"])
        self.assertEqual((d2["deal_type"], d2["stage"], d2["value_cents"]), ("paid", "negotiating", 40000))
        d3 = ct.record_deal(self.conn, 7, 3, "closed", reason="Budget gone")
        self.assertIsNotNone(d3["closed_at"])
        cur = self.conn.cursor()
        cur.execute("SELECT event_type FROM polly_timeline_events WHERE brand_id = 3 ORDER BY id")
        self.assertEqual([r[0] for r in cur.fetchall()],
                         ["brand_replied_interested", "brand_replied_interested", "brand_replied_rejected"])
        out = ct.build_tracker(self.conn, 7)
        self.assertEqual([c["brand_id"] for c in out["closed"]], [3])

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            ct.record_deal(self.conn, 7, 3, "won_lottery")

    def test_goal_syncs_autopilot_target_unless_manual(self):
        import services.polly_autopilot as pa
        pa.set_enabled(self.conn, 7, True)
        ct.set_goal(self.conn, 7, 60000, "suggested")
        s = pa.settings(self.conn, 7)
        self.assertEqual(s["monthly_target"], pa.clamp_target(167))
        pa.update_settings(self.conn, 7, {"monthly_target": 40})
        ct.set_goal(self.conn, 7, 200000, "custom")
        self.assertEqual(pa.settings(self.conn, 7)["monthly_target"], pa.clamp_target(40))

    def test_goal_carries_into_next_month(self):
        ct.set_goal(self.conn, 7, 45000, "custom", month="2026-10")
        g = ct.get_goal(self.conn, 7, month="2026-11")
        self.assertEqual((g["amount_cents"], g["source"]), (45000, "custom"))

    def test_goal_clamped(self):
        self.assertEqual(ct.set_goal(self.conn, 7, 100, sync_autopilot=False)["amount_cents"], ct.GOAL_MIN_CENTS)

    def test_pro_has_no_free_block(self):
        self.balance.stop()
        cur = self.conn.cursor()
        cur.execute("UPDATE creators SET subscription_tier = 'pro' WHERE id = 7")
        self.conn.commit()
        self._queue(3, "approved", scheduled_in=1)
        out = ct.build_tracker(self.conn, 7)
        self.assertIsNone(out["free"])
        self.assertTrue(out["is_pro"])
        self.assertFalse(any(c["locked"] for col in out["columns"] for c in col["cards"]))
        self.balance.start()

    def test_hot_brand_pinned_on_board_and_calendar(self):
        hot = {"brand_id": 6, "brand_name": "Rise", "reason": "Launched Meta ads this week"}
        with patch.object(ct, "_hot_today", return_value=hot):
            out = ct.build_tracker(self.conn, 7, now=NOW)
        lined = out["columns"][0]["cards"]
        self.assertEqual(lined[0]["brand_id"], 6)
        self.assertTrue(lined[0]["hot"])
        self.assertEqual(lined[0]["hot_reason"], "Launched Meta ads this week")
        self.assertTrue(any(e["kind"] == "hot" for e in out["calendar"]))


class ReplyChips(unittest.TestCase):
    def test_brand_replied_offers_gifted_and_paid(self):
        from services.polly_pain import reply_kind_chips
        ids = [c["id"] for c in reply_kind_chips({"id": 4, "name": "Halo"}, task_id=9)]
        self.assertEqual(ids, ["checkin_gifted", "checkin_paid", "checkin_question", "checkin_passed"])

    def test_one_tap_links_accept_new_chips(self):
        from services.checkin_links import CHECKIN_BUTTONS, ONE_TAP_NEXT
        self.assertIn("checkin_gifted", ONE_TAP_NEXT)
        self.assertIn("checkin_paid", ONE_TAP_NEXT)
        self.assertIn("checkin_interested", ONE_TAP_NEXT)  # old emails keep working
        self.assertEqual([b[0] for b in CHECKIN_BUTTONS][:2], ["checkin_gifted", "checkin_paid"])

    def test_paid_checkin_opens_tracker_amount(self):
        from services import polly_tracker as pt
        with patch.object(pt, "apply_lifecycle_intent", return_value={}), \
                patch("services.creator_tracker.record_deal") as rec:
            out = pt.apply_task_chip(MagicMock(), 7, "checkin_paid", brand_id=4, brand_name="Halo")
        rec.assert_called_once()
        self.assertEqual(rec.call_args.args[3], "paid_offer")
        self.assertEqual(out["chips"][0]["id"], "open_tracker_deal")
        self.assertIn("/creator/dashboard/tracker?brand=4", out["chips"][0]["href"])


if __name__ == "__main__":
    unittest.main()
