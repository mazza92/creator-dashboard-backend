"""Thanos: the one creator-brand matching engine."""
import unittest
from unittest.mock import patch

from services.thanos import engine as E
from services.thanos import model as M
from services.thanos import signals as S
from services.thanos.health import assess
from services.thanos.profile import CreatorProfile


class ModelTests(unittest.TestCase):
    def test_thin_brand_stays_near_its_group(self):
        g = M.Evidence(sends=5000, positive=150)
        one_lucky = M.Evidence(sends=2, positive=1)
        rate, conf = M.base_rate(g, None, one_lucky, None)
        self.assertLess(rate, 0.08)          # 1 reply from 2 pitches is not 50%
        self.assertEqual(conf, 2)

    def test_proven_brand_moves_toward_its_rate(self):
        g = M.Evidence(sends=5000, positive=150)
        proven = M.Evidence(sends=60, positive=12)
        rate, _ = M.base_rate(g, None, proven, None)
        self.assertGreater(rate, 0.1)

    def test_kit_opens_lift_a_brand_without_replies(self):
        g = M.Evidence(sends=5000, positive=150)
        quiet = M.Evidence(sends=10)
        opener = M.Evidence(sends=10, kit_opens=6)
        self.assertGreater(M.base_rate(g, None, opener, None)[0], M.base_rate(g, None, quiet, None)[0])

    def test_size_band_evidence_counts(self):
        g = M.Evidence(sends=5000, positive=150)
        brand = M.Evidence(sends=30, positive=1)
        answers_micros = M.Evidence(sends=10, positive=4)
        self.assertGreater(M.base_rate(g, None, brand, answers_micros)[0], M.base_rate(g, None, brand, None)[0])

    def test_cold(self):
        self.assertTrue(M.is_cold(M.Evidence(sends=20)))
        self.assertFalse(M.is_cold(M.Evidence(sends=20, selected=1)))
        self.assertFalse(M.is_cold(M.Evidence(sends=10)))
        self.assertFalse(M.is_cold(M.Evidence(sends=20, positive=2)))

    def test_reach_and_inbox(self):
        self.assertEqual(M.reach_tier(2000, 1600), 0)
        self.assertEqual(M.reach_tier(7000, 1600), 1)
        self.assertEqual(M.reach_tier(50000, 1600), 2)
        self.assertEqual(M.inbox_rank("pr@glow.com"), 0)
        self.assertEqual(M.inbox_rank("info@glow.com"), 2)
        self.assertEqual(M.inbox_rank("jane@glow.com"), 1)

    def test_multipliers_order_brands_sensibly(self):
        g = M.Evidence(sends=5000, positive=150)
        base = dict(global_ev=g, category_band=None, brand=None, brand_band=None)
        good = M.score(M.Facts(email="pr@a.com", recruiting=True, gifting_proof=True, fit=70), 3000, **base)
        meh = M.score(M.Facts(email="info@b.com", min_followers=100000, fit=40), 3000, **base)
        self.assertGreater(good.p, meh.p * 5)

    def test_fatigue(self):
        g = M.Evidence(sends=5000, positive=150)
        fresh = M.score(M.Facts(), 3000, g, None, M.Evidence(sends=20, positive=2), None)
        swamped = M.score(M.Facts(), 3000, g, None, M.Evidence(sends=20, positive=2, recent_load=16), None)
        self.assertLess(swamped.p, fresh.p / 2)

    def test_thompson_is_stable_per_seed_and_explores(self):
        a = M.thompson(0.03, 0, "7:2026-11-25:1")
        self.assertEqual(a, M.thompson(0.03, 0, "7:2026-11-25:1"))
        draws = {round(M.thompson(0.03, 0, f"s{i}"), 4) for i in range(20)}
        self.assertGreater(len(draws), 10)

    def test_bands_are_relative_to_the_platform(self):
        self.assertEqual(M.band_for(0.10, 20, baseline=0.04), "high")
        self.assertEqual(M.band_for(0.04, 20, baseline=0.04), "medium")
        self.assertEqual(M.band_for(0.01, 20, baseline=0.04), "low")
        self.assertEqual(M.band_for(0.04, 0, baseline=0.04), "new")


class SignalsTests(unittest.TestCase):
    def test_build_rolls_up_bands_and_categories(self):
        rows = [
            {"brand_id": 1, "category": "skincare", "band": "micro", "sends": 10, "positive": 2, "rejected": 0,
             "selected": 0, "kit_opens": 1, "bounces": 0, "recent_load": 3},
            {"brand_id": 1, "category": "skincare", "band": "mid", "sends": 5, "positive": 0, "rejected": 1,
             "selected": 0, "kit_opens": 0, "bounces": 0, "recent_load": 0},
            (2, "beauty", "micro", 4, 0, 0, 1, 0, 0, 1),
        ]
        st = S.build(rows)
        self.assertEqual(st.for_brand(1).sends, 15)
        self.assertEqual(st.for_brand_band(1, "micro").positive, 2)
        self.assertEqual(st.for_category_band("skincare", "mid").rejected, 1)
        self.assertEqual(st.for_brand(2).selected, 1)
        self.assertEqual(st.total.sends, 19)

    def test_load_never_raises(self):
        class Broken:
            def cursor(self):
                raise RuntimeError("db down")

            def rollback(self):
                pass

        S.invalidate()
        st = S.load(Broken(), force=True)
        self.assertIsInstance(st, S.Stats)


def _brand(i, cat="skincare", **kw):
    row = {"id": i, "brand_name": f"Brand {i}", "category": cat, "contact_email": f"pr@b{i}.com",
           "min_followers": None, "slug": f"b{i}"}
    row.update(kw)
    return row


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.prof = CreatorProfile(creator_id=7, followers=3000, interests=["skincare"], categories=["skincare"])
        self.stats = S.Stats(total=M.Evidence(sends=5000, positive=150))
        patches = [
            patch.object(E, "load_profile", return_value=self.prof),
            patch.object(E.S, "load", return_value=self.stats),
            patch.object(E, "_fit", return_value={"overall_score": 60}),
            patch.object(E, "_blocked", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, by_level, **kw):
        calls = []

        def fetch(conn, sql, params):
            level = len(calls)
            calls.append(params)
            return by_level.get(level, [])

        with patch.object(E, "_fetch", side_effect=fetch):
            return E.rank(None, 7, surface="polly", **kw), calls

    def test_fills_from_their_niche_first(self):
        r, calls = self._run({0: [_brand(i) for i in range(1, 10)]}, n=6)
        self.assertEqual(len(r.brands), 6)
        self.assertEqual(r.level, 0)
        self.assertEqual(len(calls), 1)
        card = r.brands[0]
        for key in ("reply_chance", "reply_band", "reply_reason", "match_score", "thanos_level"):
            self.assertIn(key, card)

    def test_ladder_widens_until_full_and_says_so(self):
        self.stats.brand[50] = M.Evidence(sends=8, positive=2)
        r, calls = self._run({0: [_brand(1)], 1: [_brand(2, "beauty")], 2: [], 3: [_brand(50, "home")],
                              4: [_brand(60, "pet")]}, n=4)
        self.assertEqual(r.ids(), sorted(r.ids(), key=lambda i: r.ids().index(i)))
        self.assertEqual(set(r.ids()), {1, 2, 50, 60})
        self.assertEqual(r.level, 4)
        self.assertEqual(r.note, "new this week")

    def test_never_suggests_pitched_or_cold(self):
        self.prof.pitched_ids = {1}
        self.stats.brand[2] = M.Evidence(sends=30)  # cold
        r, calls = self._run({0: [_brand(2), _brand(3)]}, n=6, max_level=0)
        self.assertIn(1, calls[0]["skip"])
        self.assertEqual(r.ids(), [3])

    def test_low_fit_dropped_on_first_levels(self):
        with patch.object(E, "_fit", return_value={"overall_score": 20}):
            r, _ = self._run({0: [_brand(1)]}, n=3, max_level=0)
        self.assertEqual(r.brands, [])

    def test_repitch_is_the_last_resort(self):
        self.prof.repitch_ids = {99}
        with patch.object(E, "_fetch", side_effect=lambda c, sql, p: [_brand(99)] if 99 in p["ids"] else []):
            r = E.rank(None, 7, surface="polly", n=2)
        self.assertEqual(r.ids(), [99])
        self.assertEqual(r.level, 5)
        self.assertEqual(r.brands[0]["thanos_lane"], "followup")

    def test_recruiting_and_proof_rank_first(self):
        rows = [_brand(1, contact_email="info@x.com"), _brand(2, roster_is_open=1, gifting_confirmed_at="2026-11-01")]
        r, _ = self._run({0: rows}, n=2, max_level=0, explore=False)
        self.assertEqual(r.ids()[0], 2)
        self.assertEqual(r.brands[0]["reply_reason"], "Recruiting creators right now")

    def test_scoring_named_brands_ignores_history(self):
        self.prof.pitched_ids = {5}
        with patch.object(E, "_fetch", return_value=[_brand(5)]) as f:
            res = E.score_brands(None, 7, [5])
        self.assertEqual(res.ids(), [5])
        self.assertNotIn(5, f.call_args[0][2]["skip"])

    def test_mix_spreads_categories(self):
        cards = [{"id": i, "category": "skincare"} for i in range(6)] + [{"id": 10, "category": "beauty"}]
        mixed = E._mix(cards)
        self.assertLess([c["id"] for c in mixed].index(10), 5)

    def test_profile_failure_still_ranks(self):
        with patch.object(E, "load_profile", side_effect=RuntimeError("boom")):
            r, _ = self._run({0: []}, n=3)
        self.assertEqual(r.brands, [])


class SqlTests(unittest.TestCase):
    def test_email_surfaces_require_a_deliverable_inbox(self):
        sql = E.candidates_sql("email", categories=True, reach_cap=True, only_ids=False, fresh=False)
        self.assertIn("contact_email", sql)
        self.assertIn("email_bounce_count", sql)
        self.assertIn("accepting_pr", sql)

    def test_browse_surfaces_accept_forms_and_rosters(self):
        sql = E.candidates_sql("contact", categories=False, reach_cap=False, only_ids=False, fresh=True)
        self.assertIn("application_form_url", sql)
        self.assertIn("discovered_at", sql)


class HealthTests(unittest.TestCase):
    def test_flags_thin_categories(self):
        rows = [{"category": "travel", "brands": 8, "brands_pitched_30d": 5, "creators_30d": 6, "pitches_30d": 30},
                {"category": "skincare", "brands": 1093, "brands_pitched_30d": 400, "creators_30d": 10,
                 "pitches_30d": 900}]
        out = assess(rows)
        self.assertEqual(out[0]["category"], "travel")
        self.assertEqual(out[0]["status"], "low")
        self.assertEqual(out[1]["status"], "ok")


if __name__ == "__main__":
    unittest.main()


class NicheRankingTests(unittest.TestCase):
    def test_public_niche_ranking_has_no_creator(self):
        with patch.object(E.S, "load", return_value=S.Stats()), \
                patch.object(E, "_blocked", return_value=False), \
                patch.object(E, "_fetch", return_value=[_brand(1), _brand(2)]) as f:
            r = E.rank_for_niche(None, "skincare", n=2, country="us")
        self.assertEqual(set(r.ids()), {1, 2})
        self.assertEqual(r.profile.creator_id, 0)
        self.assertEqual(f.call_args_list[0][0][2]["cats"], ["skincare"])
        self.assertNotIn("contact_email), '') IS NOT NULL", f.call_args_list[0][0][1])
