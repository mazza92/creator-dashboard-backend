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
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, logo_url text, category text, source text);
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
        cur.execute("""DROP TABLE IF EXISTS creator_goals, creator_deals, creator_pitch_plan, creator_milestones, polly_autopilot,
                       polly_autopilot_queue;
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

    def test_next_offer_balances_the_month(self):
        ct.set_goal(self.conn, 7, 100000, "custom", sync_autopilot=False)   # big goal: ~70%+ paid
        self.assertEqual(ct.next_offer(self.conn, 7), "paid")
        cur = self.conn.cursor()
        for bid in (1, 2, 3):
            cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                               status, pitch_meta) VALUES (7, %s, 'b', 'a@b.co', 's', 'b', 'draft', '{"offer": "paid"}')""",
                        (bid,))
        self.conn.commit()
        self.assertEqual(ct.next_offer(self.conn, 7), "gifted")   # 3 of 4 paid would pass the share

    def test_cards_show_the_offer_and_paused_slots(self):
        cur = self.conn.cursor()
        cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                           status, scheduled_for, pitch_meta)
                       VALUES (7, 1, 'Glow Co', 'a@b.co', 's', 'b', 'approved', %s, '{"offer": "paid"}'),
                              (7, 2, 'Dew Lab', 'a@b.co', 's', 'My rate is $120 per video', 'draft', NULL, NULL)""",
                    (NOW - timedelta(days=5),))
        self.conn.commit()
        out = ct.build_tracker(self.conn, 7, now=NOW)
        lined = {c["brand_id"]: c for c in out["columns"][0]["cards"]}
        self.assertEqual(lined[1]["type"], "paid")
        self.assertEqual(lined[1]["next"]["label"], "Autopilot paused")
        self.assertEqual(lined[2]["type"], "paid")
        self.assertEqual(lined[2]["next"]["label"], "Pitch ready to check")
        self.assertFalse(any(e["kind"] == "sched" and e["brand_id"] == 1 for e in out["calendar"]))
        self.assertIn("paid_share", out["goal"])

    def _brands(self, n, start=100):
        return [{"id": start + i, "name": f"Brand {start + i}"} for i in range(n)]

    def test_goal_becomes_a_week_of_dated_brands(self):
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        target = ct.weekly_target(20000, 5000, ct.own_rates(self.conn, 7))
        self._queue(1, "sent", sent_days_ago=2)                    # already pitched: never planned
        out = ct.plan_week(self.conn, 7, [{"id": 1, "name": "Glow Co"}] + self._brands(target * 6), now=NOW)
        self.assertEqual(out["weeks"], 5)                          # Wed 11 Nov + 28 days: 5 calendar weeks
        self.assertEqual(out["planned"], out["capacity"])
        by_week = {}
        for r in ct.planned_rows(self.conn, 7):
            wk = r["planned_for"].isocalendar()[1]
            by_week[wk] = by_week.get(wk, 0) + 1
        weeks = sorted(by_week)
        self.assertEqual(by_week[weeks[0]], round(target * 3 / 5))   # this week: Wed-Fri only
        self.assertEqual(by_week[weeks[1]], target)                   # full weeks: the goal's weekly number
        planned = out["planned"]
        rows = ct.planned_rows(self.conn, 7)
        self.assertNotIn(1, [r["brand_id"] for r in rows])
        days = {r["planned_for"] for r in rows}
        self.assertTrue(all(d.weekday() < 5 for d in days))
        self.assertGreater(len(days), 1)                           # spread over the week
        offers = [r["offer"] for r in rows]
        self.assertIn("paid", offers)
        self.assertIn("gifted", offers)
        # Shown on the Tracker: lined up with dates, on the calendar, beyond free credits locked.
        tr = ct.build_tracker(self.conn, 7, now=NOW)
        lined = [c for c in tr["columns"][0]["cards"] if c.get("planned")]
        self.assertEqual(len(lined), planned)
        self.assertTrue(all(c["next"]["label"].startswith("Pitch ") for c in lined))
        self.assertEqual(sum(1 for e in tr["calendar"] if e["label"] == "Planned pitch"), planned)
        self.assertEqual(tr["free"]["locked"], planned - 1)        # 1 free credit left in setUp
        self.assertEqual(tr["goal"]["weekly_target"], target)

    def test_replan_keeps_moves_overdue_and_tops_up(self):
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        ct.plan_week(self.conn, 7, self._brands(5), now=NOW - timedelta(days=9))
        before = {r["brand_id"] for r in ct.planned_rows(self.conn, 7)}
        out = ct.plan_week(self.conn, 7, self._brands(200, start=500), now=NOW)
        rows = ct.planned_rows(self.conn, 7)
        self.assertTrue(before <= {r["brand_id"] for r in rows})   # overdue brands moved, not lost
        self.assertTrue(all(r["planned_for"] >= NOW.date() for r in rows))
        self.assertEqual(out["planned"], out["capacity"])

    def test_needs_plan(self):
        self.assertFalse(ct.needs_plan(self.conn, 7))               # no goal yet
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        self.assertTrue(ct.needs_plan(self.conn, 7))
        ct.plan_week(self.conn, 7, self._brands(60))
        self.assertFalse(ct.needs_plan(self.conn, 7))
        self.assertEqual(ct.planned_offer(self.conn, 7, ct.planned_rows(self.conn, 7)[0]["brand_id"]) in
                         ("paid", "gifted"), True)

    def test_plan_message_fits_the_creator(self):
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        ct.plan_week(self.conn, 7, self._brands(60), now=NOW)
        msg = ct.goal_plan_message(self.conn, 7, now=NOW)            # free, 1 credit left (setUp)
        gp = msg["goal_plan"]
        self.assertEqual(msg["kind"], "goal_plan")
        self.assertEqual(gp["mode"], "free")
        self.assertEqual(sum(len(d["brands"]) for d in gp["days"]), gp["planned"])
        self.assertEqual(gp["days"][0]["label"], "Today")
        self.assertEqual(msg["task_chips"][0]["action"], "pitch_batch")
        self.assertEqual(len(msg["task_chips"][0]["brand_ids"]), 1)
        self.assertIn("pitch about", msg["content"])
        self.balance.stop()
        cur = self.conn.cursor()
        cur.execute("UPDATE creators SET subscription_tier = 'pro' WHERE id = 7")
        self.conn.commit()
        off = ct.goal_plan_message(self.conn, 7, now=NOW)
        self.assertEqual(off["goal_plan"]["mode"], "autopilot_off")
        self.assertEqual(off["task_chips"][0]["action"], "autopilot")
        import services.polly_autopilot as pa
        pa.set_enabled(self.conn, 7, True)
        on = ct.goal_plan_message(self.conn, 7, now=NOW)
        self.assertEqual(on["goal_plan"]["mode"], "autopilot_on")
        self.assertEqual([c["id"] for c in on["task_chips"]], ["open_tracker"])
        self.balance.start()

    def test_plan_message_survives_the_thread_sanitizer(self):
        from services.polly_memory import _clean_message
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        ct.plan_week(self.conn, 7, self._brands(10), now=NOW)
        msg = ct.goal_plan_message(self.conn, 7, now=NOW)
        clean = _clean_message({**msg, "at": NOW.isoformat()})
        self.assertEqual(clean["kind"], "goal_plan")
        self.assertEqual(clean["goal_plan"]["planned"], msg["goal_plan"]["planned"])
        self.assertTrue(clean["task_chips"])

    def test_first_run_and_checklist(self):
        self.assertTrue(ct.in_first_run(self.conn, 7, {}))
        self.assertFalse(ct.in_first_run(self.conn, 7, {"first_run_done": "x"}))
        ct.mark_milestone(self.conn, 7, "first_run_done")             # survives a thread save that drops notes
        self.assertFalse(ct.in_first_run(self.conn, 7, {}))
        self.assertIsNotNone(ct.milestone_at(self.conn, 7, "first_run_done"))
        cur = self.conn.cursor()
        cur.execute("DELETE FROM creator_milestones WHERE creator_id = 7")
        self.conn.commit()
        self.assertIsNone(ct.first_run_payload(self.conn, 7, now=NOW))      # nothing planned yet
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        ct.plan_week(self.conn, 7, self._brands(60), now=NOW)
        fr = ct.first_run_payload(self.conn, 7, now=NOW)
        self.assertTrue(fr["goal_set"])
        self.assertEqual(len(fr["brands"]), 6)
        self.assertEqual(fr["free_left"], 1)
        self.assertGreater(fr["this_week"], 0)
        self.assertTrue(all(b["offer"] in ("paid", "gifted") for b in fr["brands"]))
        gs = ct.getting_started(self.conn, 7)
        self.assertEqual((gs["goal"], gs["pitched"], gs["calendar"], gs["show"]), (True, False, False, True))
        self._queue(1, "sent", sent_days_ago=1)
        self.assertFalse(ct.in_first_run(self.conn, 7, {}))                  # a pitch went out
        ct.mark_milestone(self.conn, 7, "calendar_seen")
        gs = ct.getting_started(self.conn, 7)
        self.assertEqual((gs["done"], gs["show"]), (3, False))
        self.assertIn("getting_started", ct.build_tracker(self.conn, 7, now=NOW))

    def test_plan_message_waits_during_first_run(self):
        ct.set_goal(self.conn, 7, 20000, "custom", sync_autopilot=False)
        ct.plan_week(self.conn, 7, self._brands(10), now=NOW)
        with patch("services.polly_memory.load_thread", return_value={"messages": [], "notes": {}}), \
                patch("services.polly_memory.save_thread") as save:
            self.assertFalse(ct.post_goal_plan(self.conn, 7, now=NOW))
            save.assert_not_called()

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


class OfferMix(unittest.TestCase):
    def test_pitch_offer(self):
        self.assertEqual(ct.pitch_offer({"offer": "paid"}), "paid")
        self.assertEqual(ct.pitch_offer({"origin": "paid_gig", "offer": "paid"}), "gig")
        self.assertEqual(ct.pitch_offer({}, "Hi! My rates start at $150 per video."), "paid")
        self.assertEqual(ct.pitch_offer(None, "I'd love to try your serum and share it."), "gifted")

    def test_paid_share_grows_with_goal(self):
        small = ct.paid_share(15000, 500)
        big = ct.paid_share(100000, 500)
        self.assertLess(small, big)
        self.assertGreaterEqual(small, ct.PAID_SHARE_MIN)
        self.assertLessEqual(big, ct.PAID_SHARE_MAX)
        self.assertGreaterEqual(ct.paid_share(15000, 500, "paid"), 0.7)


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
