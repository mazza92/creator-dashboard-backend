"""Daily brief + email budget: skip empty days, Monday report replaces the brief, Pro 1/day 6/week and free 3/week,
kit views untouched and outside the budget, preferences, one brief per creator per local day."""

import inspect
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2

from services import daily_brief as db
from services import email_policy as ep

SCHEMA = "daily_brief_t"
# 07:05 in New York (EST, UTC-5).
MON = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)
TUE = datetime(2026, 11, 10, 12, 5, tzinfo=timezone.utc)
NY = {"mode": "daily", "tz": "America/New_York"}


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


def card(bid, name, column="replied", your_move=True, label="Answer their question", action="answer", **kw):
    c = {"brand_id": bid, "brand_name": name, "column": column, "type": "gifted", "value_cents": None,
         "next": {"label": label, "your_move": your_move, "action": action}, "hot": False, "locked": False}
    c.update(kw)
    return c


def tracker(cards=(), hot=None, is_pro=True, free=None, goal_cents=120000, total=35000):
    return {"is_pro": is_pro, "days_left": 19, "hot": hot, "free": free,
            "goal": {"set": True, "amount_cents": goal_cents, "total_cents": total, "pipeline_cents": 0},
            "columns": [{"id": "x", "cards": list(cards)}]}


def local(now=TUE):
    return db.local_time(now, "America/New_York")



_ROLLOUT = None


def setUpModule():
    global _ROLLOUT
    import os
    _ROLLOUT = os.environ.get("DAILY_BRIEF_ROLLOUT")
    os.environ["DAILY_BRIEF_ROLLOUT"] = "all"


def tearDownModule():
    import os
    if _ROLLOUT is None:
        os.environ.pop("DAILY_BRIEF_ROLLOUT", None)
    else:
        os.environ["DAILY_BRIEF_ROLLOUT"] = _ROLLOUT


class RolloutSwitch(unittest.TestCase):
    def test_switch_values(self):
        import os
        from unittest.mock import patch as _patch
        from services.email_policy import brief_rollout
        with _patch.dict(os.environ, {"DAILY_BRIEF_ROLLOUT": "204, 4248"}):
            self.assertTrue(brief_rollout(204))
            self.assertTrue(brief_rollout("4248"))
            self.assertFalse(brief_rollout(7))
        with _patch.dict(os.environ, {"DAILY_BRIEF_ROLLOUT": ""}):
            self.assertFalse(brief_rollout(204))
        with _patch.dict(os.environ, {"DAILY_BRIEF_ROLLOUT": "all"}):
            self.assertTrue(brief_rollout(7))

    def test_outside_rollout_keeps_old_emails(self):
        import os
        from unittest.mock import MagicMock, patch as _patch
        from services import email_policy as _ep
        with _patch.dict(os.environ, {"DAILY_BRIEF_ROLLOUT": "204"}), \
                _patch.object(_ep, "_recent", return_value=[]):
            self.assertEqual(_ep.decide(MagicMock(), 7, _ep.BRIEF_KIND, True), (False, "brief_rollout"))
            self.assertEqual(_ep.decide(MagicMock(), 7, "ugc_roundup", True), (True, "ok"))

class ContentTests(unittest.TestCase):
    def test_quiet_day_is_skipped(self):
        quiet = db.assemble(tracker([card(1, "Glow", column="pitched", your_move=False, label="Waiting for a reply")]),
                            local=local())
        self.assertEqual(db.worth_sending(quiet), (False, "nothing_new"))

    def test_goal_and_locked_brands_alone_are_not_news(self):
        free = tracker([card(2, "Dew", column="lined_up", your_move=False, locked=True)], is_pro=False,
                       free={"pitches_left": 0, "limit": 3, "locked": 4})
        self.assertFalse(db.worth_sending(db.assemble(free, local=local()))[0])

    def test_already_pitched_hot_brand_is_not_news(self):
        hot = {"brand_id": 3, "brand_name": "Peak", "reason": "Gifted 9 creators this week"}
        t = tracker([card(3, "Peak", column="pitched", your_move=False, hot=True)], hot=hot)
        self.assertFalse(db.worth_sending(db.assemble(t, local=local()))[0])
        t2 = tracker([card(3, "Peak", column="lined_up", your_move=False, hot=True)], hot=hot)
        self.assertEqual(db.worth_sending(db.assemble(t2, local=local())), (True, "hot"))

    def test_each_kind_of_news_is_worth_a_brief(self):
        for key, val in (("today", [{"brand_id": 1, "brand_name": "Glow", "what": "send"}]),
                         ("today", [{"brand_id": 1, "brand_name": "Glow", "what": "followup"}]),
                         ("gigs_applied", [{"brand_name": "Loop", "pay": "$300"}]),
                         ("drafts", [{"brand_id": 4, "brand_name": "Halo"}])):
            self.assertTrue(db.worth_sending(db.assemble(tracker(), local=local(), **{key: val}))[0], key)

    def test_your_move_max_three_paid_first_with_deep_links(self):
        cards = [card(i, f"Brand {i}") for i in range(1, 6)]
        cards.append(card(9, "Loop", column="negotiating", type="paid", stage="negotiating", value_cents=40000,
                          label="Answer with your rate"))
        content = db.assemble(tracker(cards), local=local())
        self.assertEqual(len(content["moves"]), 3)
        self.assertEqual(content["moves"][0]["brand_id"], 9)
        self.assertEqual(content["moves_more"], 3)
        mail = db.brief_email("ana", content)
        self.assertEqual(mail["subject"], "Loop offered you $400: your move")
        self.assertIn("/creator/dashboard/tracker?brand=9&amp;", mail["html"])
        self.assertIn("And 3 more", mail["html"])
        self.assertIn("Your Tuesday brief", mail["html"])
        self.assertIn('name="viewport"', mail["html"])

    def test_hot_brand_line_and_free_upgrade(self):
        hot = {"brand_id": 3, "brand_name": "Peak", "reason": "Gifted 9 creators in skincare this week"}
        t = tracker([card(3, "Peak", column="lined_up", your_move=False, hot=True, locked=True)], hot=hot,
                    is_pro=False, free={"pitches_left": 0, "limit": 3, "locked": 5})
        mail = db.brief_email("ana", db.assemble(t, local=local()))
        self.assertEqual(mail["subject"], "🔥 Peak is hot today")
        self.assertIn("🔥 Hot today", mail["html"])
        self.assertIn("Gifted 9 creators", mail["html"])
        self.assertIn("Send it with Pro", mail["html"])
        self.assertIn("Pro ($19/mo)", mail["html"])

    def test_missing_hot_brands_module_degrades(self):
        content = db.assemble(tracker([card(1, "Glow")], hot=None), local=local())
        self.assertIsNone(content["hot"])
        self.assertNotIn("Hot today", db.brief_email("", content)["html"])

    def test_autopilot_and_gigs_lines(self):
        content = db.assemble(
            tracker(), local=local(),
            today=[{"brand_id": 1, "brand_name": "Glow", "what": "send"},
                   {"brand_id": 2, "brand_name": "Loop", "what": "send", "pitch_meta": {"origin": "paid_gig"}},
                   {"brand_id": 3, "brand_name": "Dew", "what": "followup"}],
            gigs_applied=[{"brand_name": "Fizz", "pay": "$250"}])
        html = db.brief_email("", content)["html"]
        self.assertIn("Today I pitch <strong>Glow</strong>", html)
        self.assertIn("I apply to <strong>Loop</strong>", html)
        self.assertIn("Follow-ups today: <strong>Dew</strong>", html)
        self.assertIn("Dew replied", html)
        self.assertIn("I applied to 1 paid UGC gig for you: <strong>Fizz</strong> ($250)", html)

    def test_clock(self):
        self.assertEqual(db.due_today(local(TUE)), (True, "due"))
        self.assertEqual(db.due_today(local(MON)), (False, "monday_report"))
        self.assertEqual(db.due_today(local(TUE - timedelta(hours=2))), (False, "not_7am"))
        self.assertEqual(db.due_today(local(TUE + timedelta(hours=2, minutes=50))), (True, "due"))
        self.assertEqual(db.due_today(local(TUE + timedelta(days=4))), (False, "weekend"))

    def test_timezone_precedence(self):
        self.assertEqual(db.creator_timezone({}), "America/New_York")
        self.assertEqual(db.creator_timezone({"country": "GB", "timezone": "America/New_York"}), "Europe/London")
        self.assertEqual(db.creator_timezone({"country": "FR"}), "Europe/Paris")
        self.assertEqual(db.creator_timezone({"country": "GB", "timezone": "Asia/Tokyo"}), "Asia/Tokyo")
        self.assertEqual(db.creator_timezone({"country": "GB", "brief_timezone": "America/Denver"}), "America/Denver")
        self.assertEqual(db.creator_timezone({"brief_timezone": "Mars/Base"}), "America/New_York")

    def test_default_modes(self):
        self.assertEqual(db.resolve_mode(None, True), "daily")
        self.assertEqual(db.resolve_mode(None, False), "weekly")
        self.assertEqual(db.resolve_mode("off", True), "off")
        self.assertEqual(db.resolve_mode("hourly", False), "weekly")


class BudgetTests(unittest.TestCase):
    """Pro: 1 routine a day, 6 a week. Free: 3 a week. Transactional always; kit views outside the budget."""

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
            DROP SCHEMA IF EXISTS {SCHEMA}_b CASCADE; CREATE SCHEMA {SCHEMA}_b; SET search_path TO {SCHEMA}_b;
            CREATE TABLE polly_email_log (creator_id int, kind text, ok boolean DEFAULT true, sent_at timestamptz);
            CREATE TABLE lifecycle_email_sends (creator_id int, template_slug text, status text DEFAULT 'sent',
                                                sent_at timestamptz);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                             user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA}_b CASCADE")
        c.close()

    def setUp(self):
        self.conn = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                     dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                     password=os.getenv("DB_PASSWORD") or None, options=f"-c search_path={SCHEMA}_b")
        self.conn.cursor().execute("TRUNCATE polly_email_log, lifecycle_email_sends")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def sent(self, kind, hours_ago, now=TUE, lifecycle=False):
        cur = self.conn.cursor()
        if lifecycle:
            cur.execute("INSERT INTO lifecycle_email_sends (creator_id, template_slug, sent_at) VALUES (1, %s, %s)",
                        (kind, now - timedelta(hours=hours_ago)))
        else:
            cur.execute("INSERT INTO polly_email_log (creator_id, kind, sent_at) VALUES (1, %s, %s)",
                        (kind, now - timedelta(hours=hours_ago)))
        self.conn.commit()

    def check(self, kind, pro=True, brief=None, now=TUE):
        return ep.decide(self.conn, 1, kind, pro, now=now, brief=brief or NY)

    def test_pro_one_routine_a_day(self):
        self.assertEqual(self.check(ep.BRIEF_KIND), (True, "ok"))
        self.sent(ep.BRIEF_KIND, 1)
        self.assertEqual(self.check("niche_gifting"), (False, "daily_cap"))
        self.assertEqual(self.check(ep.BRIEF_KIND), (False, "daily_cap"))

    def test_yesterday_afternoon_email_does_not_eat_the_brief(self):
        self.sent("month_report", 18)  # 2pm yesterday in New York
        self.assertEqual(self.check("saved_brands", pro=False), (False, "daily_cap"))  # rolling 20h for the rest
        self.assertEqual(self.check(ep.BRIEF_KIND, pro=False), (True, "ok"))

    def test_early_morning_email_takes_the_day(self):
        self.sent("niche_gifting", 6)  # 1am today in New York
        self.assertEqual(self.check(ep.BRIEF_KIND), (False, "daily_cap"))

    def test_events_dont_take_the_daily_slot(self):
        self.sent("reply_win", 1)
        self.sent("new_brand_push", 2)
        self.assertEqual(self.check(ep.BRIEF_KIND), (True, "ok"))

    def test_pro_six_a_week(self):
        for day in range(1, 6):
            self.sent("niche_gifting", 24 * day)
        self.assertEqual(self.check(ep.BRIEF_KIND), (True, "ok"))
        self.sent("monday_board", 24 * 6, lifecycle=False)
        self.assertEqual(self.check(ep.BRIEF_KIND), (False, "weekly_cap"))
        self.assertEqual(self.check(ep.BRIEF_KIND, now=TUE + timedelta(hours=24)), (True, "ok"))

    def test_free_three_a_week(self):
        free_daily = {"mode": "daily", "tz": "America/New_York"}
        self.sent("polly_intro", 48)
        self.sent("weekly_digest", 24, lifecycle=True)
        self.assertEqual(self.check(ep.BRIEF_KIND, pro=False, brief=free_daily), (True, "ok"))
        self.sent("saved_brands", 72)
        self.assertEqual(self.check(ep.BRIEF_KIND, pro=False, brief=free_daily), (False, "weekly_cap"))
        self.assertEqual(self.check("new_brand_push", pro=False, brief=free_daily), (False, "weekly_cap"))

    def test_transactional_always_allowed(self):
        for h in (1, 20, 40, 60, 80, 100, 120):
            self.sent("niche_gifting", h)
        for kind in ("trial_ending", "checkout_recovery", "renewal_note", "autopilot_paused", "founder_note"):
            self.assertEqual(self.check(kind), (True, "transactional"))
            self.assertEqual(self.check(kind, pro=False), (True, "transactional"))

    def test_kit_view_keeps_its_pre_brief_rules(self):
        # Founder: don't change kit-view emails. Still an event email: no daily slot needed...
        self.sent("niche_gifting", 1)
        self.assertEqual(self.check("kit_view"), (True, "ok"))
        self.assertEqual(ep.BUDGET_EXEMPT, set())
        self.assertIn("kit_view", ep.EVENT)

    def test_kit_views_count_toward_the_week_as_before(self):
        for h in (10, 40, 70):
            self.sent("kit_view", h)
        self.assertEqual(self.check("niche_gifting", pro=False), (False, "weekly_cap"))

    def test_routine_alerts_fold_into_the_daily_brief(self):
        daily, weekly = NY, {"mode": "weekly", "tz": "America/New_York"}
        self.assertEqual(self.check("ugc_roundup", brief=daily), (False, "in_daily_brief"))
        self.assertEqual(self.check("ugc_roundup", brief=weekly), (True, "ok"))
        self.assertEqual(self.check("polly_brief", pro=False, brief=daily), (False, "in_daily_brief"))
        self.assertEqual(self.check("polly_brief", pro=False, brief=weekly), (True, "ok"))

    def test_brief_modes(self):
        self.assertEqual(self.check(ep.BRIEF_KIND, brief={"mode": "weekly", "tz": "UTC"}), (False, "brief_weekly"))
        self.assertEqual(self.check(ep.BRIEF_KIND, brief={"mode": "off", "tz": "UTC"}), (False, "brief_off"))
        self.assertEqual(self.check("monday_board", brief={"mode": "off", "tz": "UTC"}, now=MON), (False, "brief_off"))
        self.assertEqual(self.check("monday_board", brief={"mode": "daily", "tz": "UTC"}, now=MON), (True, "ok"))
        self.assertEqual(self.check("weekly_digest", pro=False, brief={"mode": "weekly", "tz": "UTC"}, now=MON),
                         (True, "ok"))
        self.assertEqual(self.check("weekly_digest", pro=False, brief={"mode": "off", "tz": "UTC"}, now=MON),
                         (False, "brief_off"))

    def test_decide_without_brief_info_falls_back_to_tier_defaults(self):
        # No creators / email_preferences tables here: settings read as unset, never raise.
        self.assertEqual(ep.decide(self.conn, 1, "ugc_roundup", True, now=TUE), (False, "in_daily_brief"))
        self.assertEqual(ep.decide(self.conn, 1, "polly_brief", False, now=TUE), (True, "ok"))
        self.assertEqual(ep.decide(self.conn, 1, ep.BRIEF_KIND, False, now=TUE), (False, "brief_weekly"))


class KitViewUntouchedTests(unittest.TestCase):
    def test_kit_view_send_path_is_as_before(self):
        from services import polly_alerts as pa
        src = inspect.getsource(pa.email_kit_views)
        self.assertIn('_recipient(conn, row["creator_id"], kind="kit_view")', src)
        self.assertIn('_already_emailed_ref(conn, "kit_view", row["id"])', src)
        self.assertEqual(pa.KIT_VIEW_THROTTLE, timedelta(hours=6))
        self.assertEqual(pa.KIT_VIEW_EMAIL_WINDOW, timedelta(hours=48))
        self.assertIn("kit_view", ep.EVENT)
        self.assertNotIn("kit_view", ep.BUDGET_EXEMPT)  # same budget rules as before the brief
        self.assertNotIn("kit_view", ep.FOLDED_INTO_BRIEF | ep.WEEKLY_REPORT | ep.RETIRED)
        mail = pa.kit_view_email("Ana", "Glow Co", 1, True)
        self.assertEqual(mail["subject"], "Glow Co just opened your kit")
        self.assertNotIn("daily_brief", inspect.getsource(pa.kit_view_email))


class BriefDbTests(unittest.TestCase):
    """The send path against a real schema: claim, preferences, skip-empty, Monday, idempotency."""

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
            CREATE TABLE users (id int PRIMARY KEY, email text, first_name text, unsubscribed_at timestamptz);
            CREATE TABLE creators (id int PRIMARY KEY, user_id int, subscription_tier text, unlocks_tier text,
                                   approval_status text, country text);
            CREATE TABLE lifecycle_email_sends (creator_id int, template_slug text, status text DEFAULT 'sent',
                                                sent_at timestamptz);
            CREATE TABLE polly_autopilot (creator_id int PRIMARY KEY, enabled boolean DEFAULT true);
            CREATE TABLE polly_autopilot_queue (id serial PRIMARY KEY, creator_id int, brand_id int, brand_name text,
                status text, scheduled_for timestamptz, sent_at timestamptz, followup_status text,
                followup_due_at timestamptz, pitch_meta jsonb);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        from services import polly_alerts as pa
        pa._ALERT_TABLES_READY = False
        db._READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS polly_email_log, polly_drafts, email_preferences")
        cur.execute("TRUNCATE users, creators, lifecycle_email_sends, polly_autopilot, polly_autopilot_queue")
        cur.execute("""INSERT INTO users VALUES (10, 'pro@x.co', 'maya', NULL), (20, 'free@x.co', 'leo', NULL),
                                                (30, 'uk@x.co', 'isla', NULL)""")
        cur.execute("""INSERT INTO creators VALUES (1, 10, 'pro', NULL, 'approved', 'US'),
                                                   (2, 20, 'free', NULL, 'approved', 'US'),
                                                   (3, 30, 'pro', NULL, 'approved', 'GB')""")
        self.conn.commit()
        db.ensure_tables(self.conn)
        # ensure_index looks names up in public; make sure this schema has its own copy.
        cur.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_polly_email_log_daily_brief
                        ON polly_email_log (creator_id, ref_id) WHERE kind = '{db.KIND}'""")
        self.conn.commit()
        self.sent = []
        self.content = db.assemble(tracker([card(5, "Glow Co")]), local=local())
        self.gather = mock.patch.object(db, "gather", side_effect=lambda *a, **k: self.content)
        self.gather.start()

    def tearDown(self):
        self.gather.stop()
        self.conn.close()

    def send_fn(self, to, subject, html, unsub, kind):
        self.sent.append((to, subject, kind))
        return {"success": True, "message_id": f"m{len(self.sent)}"}

    def run_cron(self, now, **kw):
        return db.send_due_briefs(self.conn, now=now, send_fn=self.send_fn, **kw)

    def log(self):
        cur = self.conn.cursor()
        cur.execute("SELECT creator_id, ok, error, ref_id FROM polly_email_log WHERE kind = 'daily_brief' ORDER BY id")
        return cur.fetchall()

    def test_pro_gets_one_brief_at_7am_and_free_default_is_weekly(self):
        out = self.run_cron(TUE)
        self.assertEqual([s[0] for s in self.sent], ["pro@x.co"])
        self.assertEqual(self.sent[0][1], "Glow Co is waiting on you")
        self.assertEqual(out["counts"], {"sent": 1})
        self.assertEqual(self.log(), [(1, True, None, 20261110)])

    def test_idempotent_per_creator_per_day(self):
        self.run_cron(TUE)
        self.run_cron(TUE + timedelta(minutes=20))
        self.run_cron(TUE + timedelta(hours=1))
        self.assertEqual(len(self.sent), 1)
        res = db.send_brief(self.conn, 1, now=TUE + timedelta(hours=1), send_fn=self.send_fn)
        self.assertEqual((res["status"], res["reason"]), ("skipped", "already_done"))
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(db.claim(self.conn, 1, 20261110))
        # Next weekday: a new brief.
        self.run_cron(TUE + timedelta(days=1))
        self.assertEqual(len(self.sent), 2)

    def test_each_creator_at_their_own_7am(self):
        london_7am = datetime(2026, 11, 10, 7, 10, tzinfo=timezone.utc)
        self.run_cron(london_7am)
        self.assertEqual([s[0] for s in self.sent], ["uk@x.co"])
        self.run_cron(TUE)
        self.assertEqual([s[0] for s in self.sent], ["uk@x.co", "pro@x.co"])

    def test_skip_empty_day(self):
        self.content = db.assemble(tracker([card(5, "Glow Co", column="pitched", your_move=False)]), local=local())
        out = self.run_cron(TUE)
        self.assertEqual(self.sent, [])
        self.assertEqual(out["counts"], {"skipped": 1})
        self.assertEqual(self.log(), [(1, False, "skip:nothing_new", 20261110)])
        self.run_cron(TUE + timedelta(hours=1))  # decided once a day, not re-asked every hour
        self.assertEqual(len(self.log()), 1)

    def test_monday_report_replaces_the_brief(self):
        out = self.run_cron(MON)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.log(), [])
        res = db.send_brief(self.conn, 1, now=MON, send_fn=self.send_fn)
        self.assertEqual((res["status"], res["reason"]), ("not_due", "monday_report"))
        self.assertEqual(out["counts"], {})

    def test_monday_report_earlier_today_blocks_the_brief(self):
        # e.g. a Monday board that went out late on UTC Monday lands on the creator's Tuesday morning.
        cur = self.conn.cursor()
        cur.execute("INSERT INTO polly_email_log (creator_id, kind, ref_id, ok, sent_at) VALUES (1, 'monday_board', 1, TRUE, %s)",
                    (TUE - timedelta(hours=2),))
        self.conn.commit()
        self.run_cron(TUE)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.log(), [(1, False, "skip:daily_cap", 20261110)])

    def test_weekend_no_brief(self):
        self.run_cron(TUE + timedelta(days=4))
        self.assertEqual(self.sent, [])

    def test_preferences(self):
        self.assertEqual(db.get_preferences(self.conn, 1, True)["daily_brief"], "daily")
        prefs = db.get_preferences(self.conn, 2, False)
        self.assertEqual((prefs["daily_brief"], prefs["is_default"]), ("weekly", True))
        db.set_preferences(self.conn, 2, mode="daily", tz_name="America/Chicago")
        prefs = db.get_preferences(self.conn, 2, False)
        self.assertEqual((prefs["daily_brief"], prefs["is_default"], prefs["timezone"]),
                         ("daily", False, "America/Chicago"))
        db.set_preferences(self.conn, 1, mode="off")
        with self.assertRaises(ValueError):
            db.set_preferences(self.conn, 1, mode="hourly")
        with self.assertRaises(ValueError):
            db.set_preferences(self.conn, 1, tz_name="Mars/Base")
        # Pro turned it off; the free creator opted in and gets theirs at 7am Chicago.
        self.run_cron(TUE)
        self.assertEqual(self.sent, [])
        self.run_cron(TUE + timedelta(hours=1))
        self.assertEqual([s[0] for s in self.sent], ["free@x.co"])
        self.assertEqual(ep.decide(self.conn, 1, "monday_board", True, now=MON), (False, "brief_off"))
        self.assertEqual(ep.decide(self.conn, 2, "polly_brief", False, now=MON), (False, "in_daily_brief"))

    def test_budget_blocks_and_is_logged(self):
        cur = self.conn.cursor()
        for d in range(1, 7):
            cur.execute("INSERT INTO polly_email_log (creator_id, kind, ok, sent_at) VALUES (1, 'niche_gifting', TRUE, %s)",
                        (TUE - timedelta(hours=20 * d),))
        self.conn.commit()
        self.run_cron(TUE)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.log(), [(1, False, "skip:weekly_cap", 20261110)])

    def test_unsubscribed_and_waitlisted_get_nothing(self):
        cur = self.conn.cursor()
        cur.execute("UPDATE users SET unsubscribed_at = NOW() WHERE id = 10")
        cur.execute("UPDATE creators SET approval_status = 'pending' WHERE id = 3")
        self.conn.commit()
        self.run_cron(TUE)
        self.run_cron(datetime(2026, 11, 10, 7, 10, tzinfo=timezone.utc))
        self.assertEqual(self.sent, [])

    def test_dry_run_sends_and_logs_nothing(self):
        out = self.run_cron(TUE, dry_run=True)
        self.assertEqual(out["counts"], {"would_send": 1})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.log(), [])

    def test_autopilot_today_uses_the_local_day(self):
        cur = self.conn.cursor()
        rows = [
            (5, "Glow Co", "approved", TUE + timedelta(hours=3), None, None, None),   # 10am NY today
            (6, "Dew Lab", "approved", TUE + timedelta(hours=15), None, None, None),  # 10pm NY today (UTC tomorrow)
            (7, "Halo", "approved", TUE + timedelta(hours=20), None, None, None),     # tomorrow NY
            (8, "Moss", "sent", None, TUE - timedelta(days=4), "scheduled", TUE + timedelta(hours=2)),
        ]
        for bid, name, status, sched, sent, fs, fdue in rows:
            cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, status, scheduled_for,
                           sent_at, followup_status, followup_due_at) VALUES (1, %s, %s, %s, %s, %s, %s, %s)""",
                        (bid, name, status, sched, sent, fs, fdue))
        self.conn.commit()
        loc = local()
        start = loc.replace(hour=0, minute=0, second=0, microsecond=0)
        got = db.autopilot_today(self.conn, 1, start.astimezone(timezone.utc),
                                 (start + timedelta(days=1)).astimezone(timezone.utc))
        self.assertEqual([(r["brand_name"], r["what"]) for r in got],
                         [("Moss", "followup"), ("Glow Co", "send"), ("Dew Lab", "send")])
        cur.execute("INSERT INTO polly_autopilot (creator_id, enabled) VALUES (1, FALSE)")
        self.conn.commit()
        got = db.autopilot_today(self.conn, 1, start.astimezone(timezone.utc),
                                 (start + timedelta(days=1)).astimezone(timezone.utc))
        self.assertEqual(got, [])


class RouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from routes.daily_brief import brief_bp
        app = Flask(__name__)
        app.register_blueprint(brief_bp)
        self.client = app.test_client()
        self.conn = mock.MagicMock()

    def test_preferences_need_a_creator(self):
        with mock.patch("routes.daily_brief._auth", return_value=(None, None, None)):
            self.assertEqual(self.client.get("/api/brief/preferences").status_code, 401)
            self.assertEqual(self.client.post("/api/brief/preferences", json={}).status_code, 401)

    def test_get_and_post(self):
        with mock.patch("routes.daily_brief._auth", return_value=(7, self.conn, {})), \
                mock.patch("routes.daily_brief._is_pro", return_value=False), \
                mock.patch("routes.daily_brief.get_preferences", return_value={"daily_brief": "weekly"}), \
                mock.patch("routes.daily_brief.set_preferences") as setp:
            r = self.client.get("/api/brief/preferences")
            self.assertEqual(r.get_json(), {"success": True, "daily_brief": "weekly"})
            r = self.client.post("/api/brief/preferences", json={"daily_brief": "daily", "timezone": "Europe/London"})
            self.assertEqual(r.status_code, 200)
            setp.assert_called_once_with(self.conn, 7, mode="daily", tz_name="Europe/London")
            setp.side_effect = ValueError("daily_brief must be daily, weekly or off")
            r = self.client.post("/api/brief/preferences", json={"daily_brief": "hourly"})
            self.assertEqual(r.status_code, 400)

    def test_cron_needs_the_secret(self):
        with mock.patch("services.admin_auth.cron_request_ok", return_value=False):
            self.assertEqual(self.client.get("/api/brief/cron/send").status_code, 401)

    def test_cron_wired_hourly(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        crons = json.load(open(os.path.join(root, "vercel.json")))["crons"]
        self.assertIn({"path": "/api/brief/cron/send", "schedule": "2 * * * *"}, crons)


if __name__ == "__main__":
    unittest.main()
