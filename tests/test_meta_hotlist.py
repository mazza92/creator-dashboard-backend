"""Meta-ads signal, brand quality gate, hotlist and the daily hot brand."""

import json
import os
import unittest
from datetime import date, datetime, timedelta, timezone

import psycopg2

from services import brand_quality_gate as gate
from services import hot_brands as hot
from services import meta_ads_signal as sig

SCHEMA = "meta_hotlist_t"
TODAY = datetime.now(timezone.utc).date()


# ----- pure: ad signal -------------------------------------------------------------------------------------------

class AdSignalParsing(unittest.TestCase):
    def test_started_running_both_formats(self):
        self.assertEqual(sig.parse_started("Active\nStarted running on Oct 28, 2025\nPlatforms"), date(2025, 10, 28))
        self.assertEqual(sig.parse_started("Started running on 3 Nov 2026"), date(2026, 11, 3))
        self.assertIsNone(sig.parse_started("no date here"))

    def test_cards_dedupe_unwrap_and_summarize(self):
        raw = [
            {"library_id": "111111", "text": "Active\nLibrary ID: 111111\nStarted running on Oct 28, 2026\nGlowCo\n"
                                             "Sponsored\nI tried this serum for 2 weeks and my skin is glowing",
             "landing_url": "https://l.facebook.com/l.php?u=https%3A%2F%2Fshop.glowco.com%2Fp", "has_video": True,
             "advertiser_page": "https://www.facebook.com/123456789/", "advertiser_name": "GlowCo"},
            {"library_id": "111111", "text": "dup"},
            {"library_id": "222222", "text": "Started running on Nov 2, 2026\n2 ads use this creative and text\n"
                                             "20% off everything", "landing_url": "https://glowco.com/"},
            {"library_id": "333333", "text": "Started running on Nov 1, 2026", "landing_url": "https://other.com"},
        ]
        cards = sig.normalize_cards(raw)
        self.assertEqual(len(cards), 3)
        mine = sig.cards_for_domain(cards, "www.glowco.com")
        self.assertEqual([c["library_id"] for c in mine], ["111111", "222222"])
        out = sig.summarize(mine, checked_on=TODAY)
        self.assertEqual(out["active_ad_count"], 3)  # 1 + "2 ads use this creative"
        self.assertEqual(out["ads_first_seen"], date(2026, 10, 28))
        self.assertEqual(out["ads_last_seen"], TODAY)
        self.assertEqual(out["page_id"], "123456789")
        self.assertIn("view_all_page_id=123456789", out["ad_library_url"])
        self.assertTrue(out["ad_samples"][1].startswith("I tried this serum"))
        self.assertNotIn("Sponsored", " ".join(out["ad_samples"]))

    def test_ugc_heuristic(self):
        self.assertTrue(sig.ugc_heuristic(["I tried this for 30 days and honestly my skin changed. #ad"])[0])
        self.assertTrue(sig.ugc_heuristic(["I'm a mum of two and I love my mornings now"], {"video": 2})[0])
        self.assertFalse(sig.ugc_heuristic(["30% off sitewide. Free shipping on all orders", "Shop now: new arrivals"])[0])
        self.assertIsNone(sig.ugc_heuristic(["Meet the serum everyone is talking about"])[0])

    def test_llm_only_when_inconclusive_and_cached_by_samples(self):
        calls = []

        def llm(system, user, max_tokens=0):
            calls.append(user)
            return {"ugc": True, "reason": "creator talking to camera"}

        self.assertEqual(sig.classify_ugc(["I tried it and my skin loves it. #ad"], llm=llm)[2], "heuristic")
        self.assertEqual(calls, [])
        ugc, reason, source = sig.classify_ugc(["Meet the serum everyone is talking about"], llm=llm)
        self.assertEqual((ugc, source), (True, "llm"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sig.samples_hash(["A b"]), sig.samples_hash([" a b "]))

    def test_sample_from_notes(self):
        note = "[META_ADS_OUTREACH] Shopify DTC lead. Batch 2026-10-01. Ad sample: Active Library ID: 1 I tried it"
        self.assertTrue(sig.sample_from_notes(note).endswith("I tried it"))
        self.assertIsNone(sig.sample_from_notes("no sample"))


# ----- pure: gate --------------------------------------------------------------------------------------------------

GOOD_BRAND = {"id": 1, "brand_name": "GlowCo", "website": "https://glowco.com", "category": "skincare",
              "description": "GlowCo makes vitamin C serums.", "hero_product": "C Serum", "contact_email": "pr@glowco.com",
              "email_status": "valid", "category_confidence": "high", "category_checked_at": "2026-10-01",
              "email_bounce_count": 0}
GOOD_SIG = {"active_ad_count": 5, "ads_first_seen": date(2026, 10, 1), "ads_last_seen": TODAY, "ugc_style": True,
            "ugc_style_reason": "first-person", "ad_samples": ["I tried it"]}


class GateChecks(unittest.TestCase):
    def check(self, brand=None, s=None, override=None):
        return gate.evaluate_row({**GOOD_BRAND, **(brand or {})}, {**GOOD_SIG, **(s or {})}, override=override)

    def test_good_brand_passes(self):
        out = self.check()
        self.assertTrue(out["pass"], out["reasons"])
        self.assertEqual(out["reasons"], [])
        self.assertEqual(set(out["checks"]), set(gate.CHECK_ORDER))

    def test_each_check_fails_on_its_own(self):
        cases = {
            "active_ads": [{}, {"active_ad_count": 1}],
            "active_ads ": [{}, {"ads_last_seen": TODAY - timedelta(days=31)}],
            "active_ads  ": [{}, {"active_ad_count": None}],
            "category": [{"category": "other"}, {}],
            "category ": [{"category_checked_at": None}, {}],
            "category  ": [{"category_confidence": "low"}, {}],
            "inbox": [{"email_status": "invalid"}, {}],
            "inbox ": [{"email_status": None}, {}],
            "inbox  ": [{"email_bounce_count": 1}, {}],
            "real_store": [{"website": "https://www.amazon.com/stores/GlowCo"}, {}],
            "real_store ": [{"website": "https://glowco.myshopify.com"}, {}],
            "real_store  ": [{"website": "https://linktr.ee/glowco"}, {}],
            "real_store   ": [{"website": ""}, {}],
        }
        for key, (brand, s) in cases.items():
            out = self.check(brand, s)
            self.assertFalse(out["pass"], key)
            failing = [k for k in gate.REQUIRED if not out["checks"][k]["ok"]]
            self.assertEqual(failing, [key.strip()], (key, out["reasons"]))

    def test_creator_style_ranks_but_is_not_required(self):
        out = self.check({}, {"ugc_style": False})
        self.assertTrue(out["pass"])
        self.assertFalse(out["checks"]["ugc_style"]["ok"])

    def test_meta_discovery_counts_as_live_ads_until_backfill(self):
        fresh = self.check({"source": "meta_ads_library", "created_at": TODAY - timedelta(days=20)},
                           {"active_ad_count": None})
        self.assertTrue(fresh["pass"], fresh["reasons"])
        self.assertIn("found on", fresh["checks"]["active_ads"]["detail"])
        old = self.check({"source": "meta_ads_library", "created_at": TODAY - timedelta(days=90)},
                         {"active_ad_count": None})
        self.assertFalse(old["pass"])
        other = self.check({"source": "manual", "created_at": TODAY}, {"active_ad_count": None})
        self.assertFalse(other["pass"])

    def test_manual_override_wins_both_ways(self):
        self.assertTrue(self.check({"category": "other"}, override="approved")["pass"])
        self.assertFalse(self.check(override="rejected")["pass"])
        self.assertEqual(self.check(override="rejected")["reasons"][0], "manual reject")


class SafetyFilter(unittest.TestCase):
    def test_blocks_unsafe_brands(self):
        unsafe = [
            ("Calm Leaf", "Full spectrum CBD gummies", None),
            ("Green", "Delta-8 THC vapes", None),
            ("Spin", "Online casino bonus", None),
            ("Coin", "Buy crypto with zero fees", None),
            ("Cloud", "Disposable vapes and e-liquids", None),
            ("Slim", "Semaglutide weight loss injections delivered", None),
            ("Lust", "Sex toys for couples", None),
            ("Fans", None, "https://myonlyfans-tips.com"),
            ("Leaf", "Gummies", "https://bestcbdshop.com"),
        ]
        for name, desc, site in unsafe:
            self.assertTrue(gate.safety_flags(name, desc, website=site), name)

    def test_does_not_flag_normal_beauty_copy(self):
        safe = [
            ("Healthcare Glow", "Serum for adult acne", "https://healthcareglow.com"),
            ("Smooth", "Botox-like results without injections", None),
            ("Calm Skin", "Steroid-free eczema cream", None),
            ("Noir", "Tobacco vanilla eau de parfum", None),
            ("Bet", "Alphabet letter necklaces", "https://alphabetjewels.com"),
        ]
        for name, desc, site in safe:
            self.assertEqual(gate.safety_flags(name, desc, website=site), [], name)

    def test_unsafe_brand_fails_gate(self):
        out = gate.evaluate_row({**GOOD_BRAND, "description": "CBD face oil"}, GOOD_SIG)
        self.assertFalse(out["pass"])
        self.assertIn("safety", out["reasons"][0])


# ----- database ----------------------------------------------------------------------------------------------------

def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class HotlistDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        sig._READY = gate._READY = hot._READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS pr_brands, creators, users, polly_threads, polly_autopilot_queue, creator_pipeline,
                polly_autopilot, polly_gmail_accounts, brand_ad_signals, brand_quality_checks, creator_hot_brands;
            CREATE TABLE pr_brands (id serial PRIMARY KEY, brand_name text, website text, logo_url text, category text,
                description text, hero_product text, contact_email text, email_status text, status text, source text,
                notes text, category_confidence text, category_checked_at timestamptz, email_bounce_count int DEFAULT 0,
                created_at timestamptz DEFAULT NOW());
            CREATE TABLE users (id int PRIMARY KEY, last_login timestamp);
            CREATE TABLE creators (id int PRIMARY KEY, user_id int, creator_niches text[], niche text,
                followers_count int, unlocks_tier text, subscription_tier text);
            CREATE TABLE polly_threads (creator_id int PRIMARY KEY, notes jsonb DEFAULT '{}', updated_at timestamptz);
            CREATE TABLE polly_autopilot_queue (id serial PRIMARY KEY, creator_id int, brand_id int, brand_name text,
                status text, scheduled_for timestamptz, sent_at timestamptz, pitch_meta jsonb,
                UNIQUE (creator_id, brand_id));
            CREATE TABLE creator_pipeline (creator_id int, brand_id int, pitched_at timestamptz, send_confirmed bool);
            CREATE TABLE polly_autopilot (creator_id int PRIMARY KEY, enabled bool DEFAULT true, send_via text);
            CREATE TABLE polly_gmail_accounts (creator_id int PRIMARY KEY, revoked_at timestamptz);
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def brand(self, name, category, ads=4, ugc=True, email_status="valid", **extra):
        cur = self.conn.cursor()
        row = {"brand_name": name, "website": f"https://{name.lower().replace(' ', '')}.com", "category": category,
               "description": f"{name} products", "hero_product": "Hero", "contact_email": f"pr@{name.lower()}.com",
               "email_status": email_status, "status": "published", "source": "meta_ads_library",
               "category_confidence": "high", "category_checked_at": datetime.now(timezone.utc),
               "logo_url": f"https://logo/{name}.png", **extra}
        cur.execute(f"INSERT INTO pr_brands ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
                    list(row.values()))
        bid = cur.fetchone()[0]
        self.conn.commit()
        if ads is not None:
            sig.ensure_table(self.conn)
            cur.execute("""INSERT INTO brand_ad_signals (brand_id, active_ad_count, ads_first_seen, ads_last_seen,
                               ugc_style, ugc_style_reason, ad_samples) VALUES (%s, %s, %s, %s, %s, 'x', '["I tried"]')""",
                        (bid, ads, date(2026, 10, 28), TODAY, ugc))
            self.conn.commit()
        return bid

    def creator(self, cid, niches, pro=False, active=True, followers=5000, notes=None):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO users VALUES (%s, %s)", (cid, datetime.utcnow() - timedelta(days=2 if active else 90)))
        cur.execute("INSERT INTO creators VALUES (%s, %s, %s, NULL, %s, %s, NULL)",
                    (cid, cid, niches, followers, "pro" if pro else None))
        if notes is not None:
            cur.execute("INSERT INTO polly_threads VALUES (%s, %s, NOW() - INTERVAL '60 days')", (cid, json.dumps(notes)))
        self.conn.commit()

    # --- signals ---

    def test_scrape_hook_seed_and_ugc_cache(self):
        from services.brand_db_writer import _save_ad_signal

        bid = self.brand("Glowco", "skincare", ads=None)
        record = {"website": "https://glowco.com", "brand_name": "Glowco",
                  "ad_cards": [{"library_id": "9" * 8, "text": "Started running on Oct 30, 2026\nI tried it. #ad",
                                "landing_url": "https://glowco.com/x"},
                               {"library_id": "8" * 8, "text": "Started running on Nov 2, 2026\nmy skin, honestly",
                                "landing_url": "https://glowco.com/y", "has_video": True}]}
        cur = self.conn.cursor()
        sig.ensure_table(self.conn)
        _save_ad_signal(cur, self.conn, bid, record)
        self.conn.commit()
        row = sig.load(self.conn, bid)
        self.assertEqual((row["active_ad_count"], row["ugc_style"], row["signal_source"]), (2, True, "keyword_search"))
        self.assertEqual(row["ads_first_seen"], date(2026, 10, 30))

        calls = []
        llm = lambda *a, **k: calls.append(1) or {"ugc": False, "reason": "studio shots"}
        other = self.brand("Plain", "skincare", ads=None,
                           notes="[META_ADS_OUTREACH] x. Ad sample: Meet the serum everyone is talking about")
        self.assertEqual(sig.seed_from_notes(self.conn), 1)
        seeded = sig.load(self.conn, other)
        self.assertEqual((seeded["active_ad_count"], seeded["ugc_style"], seeded["ad_checked_at"]), (None, None, None))
        self.assertEqual(sig.classify_pending(self.conn, llm=llm), 1)
        self.assertIs(sig.load(self.conn, other)["ugc_style"], False)
        sig.save(self.conn, other, {"active_ad_count": 3, "ads_last_seen": TODAY, "signal_source": "page"},
                 use_llm=True, llm=llm)
        self.assertEqual(len(calls), 1)  # same samples: cached verdict, no second call
        self.assertEqual(sig.load(self.conn, other)["active_ad_count"], 3)

    # --- gate ---

    def test_run_gate_persists_and_excludes_failures(self):
        good = self.brand("Glow", "skincare")
        few = self.brand("Few", "skincare", ads=1)
        nosig = self.brand("Unchecked", "skincare", ads=None,
                           created_at=datetime.now(timezone.utc) - timedelta(days=90))  # found too long ago
        draft = self.brand("Drafty", "skincare", status="draft")
        out = gate.run_gate(self.conn)
        self.assertEqual((out["checked"], out["passed"], out["failed"]), (3, 1, 2))
        cur = self.conn.cursor()
        cur.execute("SELECT brand_id, passed, reasons FROM brand_quality_checks ORDER BY brand_id")
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        self.assertTrue(rows[good][0])
        self.assertFalse(rows[few][0])
        self.assertIn("active_ads", rows[few][1][0])
        self.assertIn("not checked", rows[nosig][1][0])
        self.assertNotIn(draft, rows)
        self.assertEqual([b["id"] for b in hot.passing_pool(self.conn)], [good])

    def test_override_and_single_evaluate(self):
        bid = self.brand("Meh", "skincare", email_status="invalid")
        self.assertFalse(gate.evaluate(self.conn, bid)["pass"])
        self.assertTrue(gate.set_override(self.conn, bid, "approved", note="looks great")["pass"])
        gate.run_gate(self.conn)  # the nightly re-run keeps the manual decision
        cur = self.conn.cursor()
        cur.execute("SELECT passed, override, auto_passed FROM brand_quality_checks WHERE brand_id = %s", (bid,))
        self.assertEqual(cur.fetchone(), (True, "approved", False))
        self.assertFalse(gate.set_override(self.conn, bid, None)["pass"])

    # --- hotlist ---

    def test_niche_strictness(self):
        skin = self.brand("Skin", "skincare")
        supp = self.brand("Supp", "supplements")
        home = self.brand("Homey", "home")
        life = self.brand("Life", "lifestyle")
        self.brand("Fit", "fitness")
        gate.run_gate(self.conn)
        self.creator(1, ["wellness"])
        self.creator(2, ["lifestyle"])
        self.creator(3, ["home"])
        self.creator(4, ["beauty"])
        self.creator(5, [])
        ids = lambda cid: {b["brand_id"] for b in hot.hotlist_for_creator(self.conn, cid)}
        self.assertEqual(ids(1), {supp})            # wellness <-> supplements, never skincare
        self.assertEqual(ids(2), {life, home})      # lifestyle -> home
        self.assertEqual(ids(3), {home})            # but home does not widen to lifestyle
        self.assertEqual(ids(4), {skin})            # beauty <-> skincare
        self.assertEqual(ids(5), set())             # no recorded niche, nothing

    def test_hotlist_excludes_queued_pitched_avoided_and_has_reason(self):
        a = self.brand("Alpha", "skincare", ads=9)
        b = self.brand("Beta", "skincare")
        c = self.brand("Gamma", "skincare")
        d = self.brand("Delta", "skincare")
        gate.run_gate(self.conn)
        self.creator(1, ["skincare"], notes={"pitched_brand_ids": [d]})
        cur = self.conn.cursor()
        cur.execute("INSERT INTO polly_autopilot_queue (creator_id, brand_id, status) VALUES (1, %s, 'skipped')", (b,))
        cur.execute("INSERT INTO creator_pipeline VALUES (1, %s, NOW(), NULL)", (c,))
        self.conn.commit()
        out = hot.hotlist_for_creator(self.conn, 1)
        self.assertEqual([r["brand_id"] for r in out], [a])
        self.assertEqual(out[0]["reason"], f"Running 9 creator-style ads since Oct 28{'' if TODAY.year == 2026 else ', 2026'}")

    def test_reason_strings(self):
        today = date(2026, 11, 11)
        self.assertEqual(hot.reason_for({"active_ad_count": 9, "ugc_style": True, "ads_first_seen": date(2026, 10, 28)}, 0, today),
                         "Running 9 creator-style ads since Oct 28")
        self.assertEqual(hot.reason_for({"active_ad_count": 2, "ads_first_seen": date(2025, 12, 1)}, 3, today),
                         "Running 2 ads since Dec 1, 2025 · Gifted 3 creators your size")
        self.assertEqual(hot.reason_for({"found_on": date(2026, 11, 2)}, 0, today),
                         "Running Meta ads (spotted Nov 2)")
        self.assertEqual(hot.reason_for({"fallback": True, "category": "skincare"}, 0, today),
                         "Fresh skincare pick · verified inbox")
        self.assertEqual(hot.your_size(5000, [1000, 2000, 15000, 16000, 400000]), 2)  # 1.7k-15k

    # --- daily pick ---

    def test_one_per_day_idempotent_no_padding_no_repeat(self):
        a = self.brand("Alpha", "skincare", ads=9)
        b = self.brand("Beta", "skincare", ads=3)
        self.brand("Bad", "fashion", email_status="invalid")
        gate.run_gate(self.conn)
        self.creator(1, ["skincare"])
        self.creator(2, ["fashion"])            # only a failing brand in the niche -> no pick
        self.creator(3, ["skincare"], active=False)  # inactive, free -> not a candidate
        self.creator(4, ["skincare"], active=False, pro=True)  # inactive but Pro -> candidate
        out = hot.assign_hot_brands(self.conn, day=TODAY, draft=False)
        self.assertEqual((out["assigned"], out["none"]), (2, 1))
        again = hot.assign_hot_brands(self.conn, day=TODAY, draft=False)
        self.assertEqual((again["assigned"], again["already"]), (0, 2))
        cur = self.conn.cursor()
        cur.execute("SELECT creator_id, brand_id FROM creator_hot_brands WHERE day = %s ORDER BY creator_id", (TODAY,))
        self.assertEqual(cur.fetchall(), [(1, a), (4, a)])
        hot.assign_hot_brands(self.conn, day=TODAY + timedelta(days=1), creator_ids=[1], draft=False)
        hot.assign_hot_brands(self.conn, day=TODAY + timedelta(days=2), creator_ids=[1], draft=False)
        cur.execute("SELECT day, brand_id FROM creator_hot_brands WHERE creator_id = 1 ORDER BY day")
        self.assertEqual([r[1] for r in cur.fetchall()], [a, b])  # day 2 gets Beta, day 3 has nothing left: no row

    def test_hot_brand_today(self):
        a = self.brand("Alpha", "skincare", ads=9)
        gate.run_gate(self.conn)
        self.creator(1, ["skincare"])
        self.assertIsNone(hot.hot_brand_today(self.conn, 1))
        hot.assign_hot_brands(self.conn, day=TODAY, draft=False)
        got = hot.hot_brand_today(self.conn, 1)
        self.assertEqual(set(got), {"brand_id", "brand_name", "logo", "reason", "queued_item_id"})
        self.assertEqual((got["brand_id"], got["brand_name"], got["logo"], got["queued_item_id"]),
                         (a, "Alpha", "https://logo/Alpha.png", None))
        self.assertTrue(got["reason"].startswith("Running 9 creator-style ads"))
        self.assertIsNone(hot.hot_brand_today(self.conn, 1, day=TODAY - timedelta(days=1)))
        gate.set_override(self.conn, a, "rejected")
        self.assertIsNone(hot.hot_brand_today(self.conn, 1))

    def test_fallback_keeps_one_hot_brand_a_day(self):
        self.brand("Bad", "fashion", email_status="invalid")             # gate fails: never comes back
        self.brand("Loom", "fashion", ads=None, source="manual")          # verified, not from Meta
        self.brand("Shady", "fashion", ads=None, source="manual", email_status="unverified")
        gate.run_gate(self.conn)
        self.creator(2, ["fashion"])
        hot.assign_hot_brands(self.conn, draft=False)
        got = hot.hot_brand_today(self.conn, 2)
        self.assertEqual(got["brand_name"], "Loom")
        self.assertEqual(got["reason"], "Fresh fashion pick · verified inbox")

    def test_pro_autopilot_pitch_is_queued_free_is_not(self):
        a = self.brand("Alpha", "skincare", ads=9)
        gate.run_gate(self.conn)
        self.creator(1, ["skincare"], pro=True)
        self.creator(2, ["skincare"])                 # free
        self.creator(3, ["skincare"], pro=True)       # Pro, Autopilot on but no sender
        cur = self.conn.cursor()
        cur.execute("INSERT INTO polly_autopilot (creator_id) VALUES (1), (3)")
        cur.execute("INSERT INTO polly_gmail_accounts (creator_id) VALUES (1)")
        self.conn.commit()
        calls = []

        def fake_draft(conn, creator_id, brand_id, reason):
            calls.append((creator_id, brand_id, reason))
            c = conn.cursor()
            c.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, status, pitch_meta)
                         VALUES (%s, %s, 'draft', '{"writer": "v2"}') RETURNING id""", (creator_id, brand_id))
            item = c.fetchone()[0]
            conn.commit()
            hot.tag_hot(conn, item, reason)
            return item, None

        out = hot.assign_hot_brands(self.conn, day=TODAY, draft_fn=fake_draft)
        self.assertEqual((out["assigned"], out["drafted"]), (3, 1))
        self.assertEqual([c[:2] for c in calls], [(1, a)])
        got = hot.hot_brand_today(self.conn, 1)
        self.assertIsNotNone(got["queued_item_id"])
        self.assertIsNone(hot.hot_brand_today(self.conn, 2)["queued_item_id"])
        cur.execute("SELECT pitch_meta FROM polly_autopilot_queue WHERE id = %s", (got["queued_item_id"],))
        meta = cur.fetchone()[0]
        self.assertEqual((meta["origin"], meta["hot"], meta["writer"]), ("hot_brand", True, "v2"))
        self.assertEqual(hot.draft_pending(self.conn, TODAY, draft_fn=fake_draft)["drafted"], 0)  # one attempt only

    def test_jump_queue_swaps_slots_keeping_daily_counts(self):
        now = datetime.now(timezone.utc)
        cur = self.conn.cursor()
        slots = [now + timedelta(hours=1), now + timedelta(days=1), now + timedelta(days=2)]
        ids = []
        for i, slot in enumerate(slots):
            cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, status, scheduled_for)
                           VALUES (1, %s, 'approved', %s) RETURNING id""", (100 + i, slot))
            ids.append(cur.fetchone()[0])
        self.conn.commit()
        self.assertTrue(hot.jump_queue(self.conn, 1, ids[2], now=now))
        cur.execute("SELECT id, scheduled_for FROM polly_autopilot_queue ORDER BY scheduled_for")
        order = [r[0] for r in cur.fetchall()]
        self.assertEqual(order, [ids[2], ids[1], ids[0]])


class Routes(unittest.TestCase):
    def test_cron_and_admin_need_auth(self):
        os.environ.pop("CRON_SECRET", None)
        from app import app as flask_app

        client = flask_app.test_client()
        self.assertEqual(client.get("/api/hotlist/cron/daily").status_code, 401)
        self.assertEqual(client.get("/api/admin/brand-quality").status_code, 401)
        self.assertEqual(client.post("/api/admin/brand-quality/1/approve").status_code, 401)
        self.assertEqual(client.get("/api/hotlist/today").status_code, 401)

    def test_cron_runs_every_step_and_isolates_failures(self):
        from routes import hotlist
        from unittest import mock

        with mock.patch("services.brand_autopublish.run", return_value={"published_count": 4}), \
                mock.patch("services.meta_ads_signal.seed_from_notes", return_value=3), \
                mock.patch("services.meta_ads_signal.classify_pending", side_effect=RuntimeError("llm down")), \
                mock.patch("services.brand_quality_gate.run_gate", return_value={"checked": 2}), \
                mock.patch("services.hot_brands.assign_hot_brands", return_value={"assigned": 1}) as assign:
            conn = mock.MagicMock()
            out = hotlist.run_daily(conn, budget=5)
        self.assertEqual(out["autopublish"], {"published_count": 4})
        self.assertEqual(out["seeded"], 3)
        self.assertIn("llm down", out["classified"]["error"])
        self.assertEqual((out["gate"], out["hot"]), ({"checked": 2}, {"assigned": 1}))
        self.assertEqual(assign.call_args.kwargs["time_budget"], 5)


if __name__ == "__main__":
    unittest.main()
