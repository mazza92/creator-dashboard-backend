"""Brands For you feed: real posts first (one per brand), directory brands fill in, targets saved."""

import os
import unittest

import psycopg2

from services import brand_feed as feed

SCHEMA = "brand_feed_t"
DDL = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
SET search_path TO {SCHEMA};
CREATE TABLE creators (id int PRIMARY KEY, niche text, creator_niches text[]);
CREATE TABLE pr_brands (id int PRIMARY KEY, slug text, brand_name text, logo_url text, category text, regions text,
                        min_followers int, avg_product_value numeric, hero_product text, website text,
                        tiktok_handle text, contact_email text, status text DEFAULT 'published',
                        is_featured boolean DEFAULT false, gifting_signal text);
CREATE TABLE discovered_brands (id int PRIMARY KEY, brand_id int);
CREATE TABLE tiktok_videos (tiktok_id text PRIMARY KEY, url text, caption text, cover_url text,
                            creator_handle text, creator_followers int, created_at timestamptz);
CREATE TABLE brand_signals (discovered_brand_id int, tiktok_id text, signal_type text, niche text);
"""


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class BrandFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(DDL)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        try:
            c = _conn()
            c.autocommit = True
            c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            c.close()
        except Exception:
            pass

    def setUp(self):
        feed._TARGETS_READY = False
        feed._SEEN_READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS creator_brand_targets;
            DROP TABLE IF EXISTS creator_feed_seen;
            TRUNCATE creators, pr_brands, discovered_brands, tiktok_videos, brand_signals;
            INSERT INTO creators VALUES (1, 'Beauty & Skincare', NULL);
            INSERT INTO pr_brands (id, slug, brand_name, category, contact_email, gifting_signal, status) VALUES
              (1, 'glow', 'Glow', 'skincare', 'a@glow.co', 'tiktok', 'published'),
              (2, 'byoma', 'Byoma', 'skincare', 'b@byoma.co', NULL, 'published'),
              (3, 'drafty', 'Drafty', 'skincare', 'c@d.co', NULL, 'draft'),
              (4, 'pup', 'Pup', 'pet', 'd@pup.co', NULL, 'published'),
              (5, 'calm', 'Calm Skin', 'beauty', 'e@calm.co', 'brand_list', 'published'),
              (6, 'noemail', 'No Email', 'skincare', NULL, NULL, 'published');
            INSERT INTO discovered_brands VALUES (10, 1), (11, 2), (12, 3), (13, 4);
            INSERT INTO tiktok_videos VALUES
              ('v1', 'https://tt/v1', 'PR from @glow', 'https://x/storage/v1/object/public/c1.jpg', 'maya', 6800, NOW() - INTERVAL '3 days'),
              ('v2', 'https://tt/v2', 'glow again', 'https://x/storage/v1/object/public/c2.jpg', 'ana', 3100, NOW() - INTERVAL '1 days'),
              ('v3', 'https://tt/v3', 'byoma ad', 'https://tiktokcdn/c3.jpg', 'lee', 12000, NOW() - INTERVAL '2 days'),
              ('v4', 'https://tt/v4', 'draft brand', 'https://x/storage/v1/object/public/c4.jpg', 'kim', 2000, NOW()),
              ('v5', 'https://tt/v5', 'dog box', 'https://x/storage/v1/object/public/c5.jpg', 'jo', 4000, NOW()),
              ('v6', 'https://tt/v6', 'old', 'https://x/storage/v1/object/public/c6.jpg', 'old', 4000, NOW() - INTERVAL '90 days');
            INSERT INTO brand_signals VALUES (10, 'v1', 'gifted', 'skincare'), (10, 'v2', 'gifted', 'skincare'),
              (11, 'v3', 'paid', 'skincare'), (12, 'v4', 'gifted', 'skincare'), (13, 'v5', 'gifted', 'pet'),
              (10, 'v6', 'gifted', 'skincare');
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_creator_niche_defaults_from_profile(self):
        data = feed.build(self.conn, 1)
        self.assertEqual(data["niche"], "skincare")

    def test_one_post_per_published_brand_newest_with_stored_cover_first(self):
        data = feed.build(self.conn, 1, "skincare")
        posts = [i for i in data["items"] if i["type"] == "post"]
        self.assertEqual([p["brand"]["name"] for p in posts][:2], ["Glow", "Byoma"])
        glow = posts[0]
        self.assertEqual(glow["post"]["creator_handle"], "ana")
        self.assertEqual(glow["brand"]["creators_30d"], 2)
        self.assertTrue(glow["brand"]["gifting_confirmed"])
        names = {i["brand"]["name"] for i in data["items"]}
        self.assertNotIn("Drafty", names)

    def test_directory_brands_fill_without_repeating_and_need_a_way_to_pitch(self):
        data = feed.build(self.conn, 1, "skincare")
        dir_names = [i["brand"]["name"] for i in data["items"] if i["type"] == "brand"]
        self.assertEqual(dir_names[0], "Calm Skin")
        self.assertNotIn("Glow", dir_names)
        self.assertNotIn("No Email", dir_names)

    def test_niche_sorts_first_but_every_niche_stays(self):
        data = feed.build(self.conn, 1, "pet")
        names = [i["brand"]["name"] for i in data["items"] if i["type"] == "post"]
        self.assertEqual(names[0], "Pup")
        self.assertEqual(sorted(names[1:]), ["Byoma", "Glow"])
        skin = [i["brand"]["name"] for i in feed.build(self.conn, 1, "skincare")["items"] if i["type"] == "post"]
        self.assertEqual(skin[-1], "Pup")

    def test_targets_toggle_and_show_on_cards(self):
        self.assertEqual(feed.set_target(self.conn, 1, 2, True), 1)
        self.assertEqual(feed.set_target(self.conn, 1, 2, True), 1)
        data = feed.build(self.conn, 1, "skincare")
        byoma = next(i for i in data["items"] if i["brand"]["id"] == 2)
        self.assertTrue(byoma["brand"]["targeted"])
        self.assertEqual(data["targets"], 1)
        self.assertEqual([t["name"] for t in feed.target_brands(self.conn, 1)], ["Byoma"])
        self.assertEqual(feed.set_target(self.conn, 1, 2, False), 0)


    # ----- fresh, endless feed -----
    def _keys(self, page):
        return [i["key"] for i in page["items"]]

    def test_pages_never_repeat_and_use_every_post_not_one_per_brand(self):
        import random
        first = feed.next_page(self.conn, 1, "skincare", size=4, rng=random.Random(1))
        keys = self._keys(first)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("post-v4", keys)  # draft brand
        self.assertNotIn("post-v6", keys)  # outside the window
        posts_in_page = [i for i in first["items"] if i["type"] == "post"]
        self.assertEqual(len({p["brand"]["id"] for p in posts_in_page}), len(posts_in_page))
        seen_all, page, n = set(keys), first, 0
        while page["has_more"] and n < 10:
            page = feed.next_page(self.conn, 1, "skincare", exclude=list(seen_all), size=4, rng=random.Random(n))
            new = self._keys(page)
            self.assertFalse(set(new) & seen_all)
            seen_all |= set(new)
            n += 1
        self.assertTrue({"post-v1", "post-v2", "post-v3", "post-v5"} <= seen_all)  # both Glow posts show up

    def test_seen_posts_go_last_and_new_ones_lead(self):
        import random
        feed.mark_seen(self.conn, 1, ["post-v2", "post-v3", "post-v5"])
        page = feed.next_page(self.conn, 1, "skincare", size=12, rng=random.Random(3))
        posts = [i["key"] for i in page["items"] if i["type"] == "post"]
        self.assertEqual(posts[0], "post-v1")  # the only unseen post leads
        self.assertTrue(all(k not in posts for k in ("post-v2", "post-v3", "post-v5"))
                        or posts.index("post-v1") < min(posts.index(k) for k in posts if k != "post-v1"))

    def test_order_changes_between_visits(self):
        import random
        orders = {tuple(self._keys(feed.next_page(self.conn, 1, "all", size=12, rng=random.Random(s))))
                  for s in range(8)}
        self.assertGreater(len(orders), 1)

    def test_mark_seen_upserts(self):
        self.assertEqual(feed.mark_seen(self.conn, 1, ["post-v1", "post-v1", 5, None]), 1)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM creator_feed_seen WHERE creator_id = 1")
        self.assertEqual(cur.fetchone()[0], 1)


class InterleaveTests(unittest.TestCase):
    def test_two_posts_then_a_brand(self):
        p = [{"type": "post", "i": n} for n in range(4)]
        b = [{"type": "brand", "i": n} for n in range(3)]
        kinds = [x["type"] for x in feed.interleave(p, b)]
        self.assertEqual(kinds, ["post", "post", "brand", "post", "post", "brand", "brand"])


if __name__ == "__main__":
    unittest.main()


class AutopilotTargetsFirstTests(unittest.TestCase):
    def test_targets_lead_and_are_not_repeated(self):
        from unittest import mock
        from routes import polly_autopilot as ap
        matched = [{"id": 5, "name": "Pick"}, {"id": 7, "name": "Saved too"}]
        targets = [{"id": 7, "name": "Saved too"}, {"id": 9, "name": "Saved, no email"},
                   {"id": 3, "name": "Already pitched"}]
        with mock.patch("services.brand_feed.target_brands", return_value=targets), \
                mock.patch.object(ap, "_brands_with_email", return_value={7: {}, 3: {}}):
            out = ap._targets_first(object(), 1, matched, [3], {})
        self.assertEqual([b["id"] for b in out], [7, 5])
        self.assertTrue(out[0]["target"])


class PitchTargetsTurnTests(unittest.TestCase):
    def _state(self, balance):
        from types import SimpleNamespace
        chips = []
        st = SimpleNamespace(balance=balance, notes={}, creator_id=1, final_say="", db=lambda: None,
                             add_chips=chips.extend)
        return st, chips

    def _run(self, balance, targets):
        from unittest import mock
        import routes.polly as rp
        st, chips = self._state(balance)
        prior = []
        with mock.patch("services.brand_feed.target_brands", return_value=targets), \
                mock.patch.object(rp, "_pipeline_pitched_ids", return_value=[]):
            call = rp._pitch_targets_call(st, prior)
        return st, chips, call

    T = [{"id": i, "name": f"Brand {i}"} for i in range(1, 6)]

    def test_no_targets_says_where_to_add_them(self):
        st, chips, call = self._run({"remaining": 3}, [])
        self.assertIsNone(call)
        self.assertIn("+ Save", st.final_say)

    def test_free_pitches_left_write_that_many(self):
        st, chips, call = self._run({"remaining": 2}, self.T)
        self.assertEqual(call, ("draft_pitch_batch", {"brand_ids": [1, 2]}))
        self.assertEqual(st._targets_rest, 0)

    def test_pro_writes_three_and_autopilot_takes_the_rest(self):
        st, chips, call = self._run({"is_unlimited": True}, self.T)
        self.assertEqual(call[1]["brand_ids"], [1, 2, 3])
        self.assertEqual(st._targets_rest, 2)

    def test_out_of_free_pitches_shows_saved_brand_cards_and_pro(self):
        st, chips, call = self._run({"remaining": 0}, self.T)
        self.assertIsNone(call)
        self.assertIn("**5 saved brands**", st.final_say)
        self.assertEqual([b["id"] for b in st.brands], [1, 2, 3, 4, 5])
        self.assertEqual(chips[0]["label"], "Pitch my 5 saved brands · $19/mo")
        self.assertEqual(chips[0]["action"], "unlock_pro")
