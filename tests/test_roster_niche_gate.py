import unittest

import pr_crm_routes as P
import routes.polly as R


class RosterNicheGateTests(unittest.TestCase):
    def test_signup_niche_bounds_live_rosters(self):
        scrape = {"primary_niche": "fitness", "secondary_niches": ["lifestyle"]}
        pool = P._roster_category_pool(scrape, ["fitness"])
        self.assertEqual(pool, {"fitness", "activewear", "athleisure", "sports"})
        self.assertNotIn("beauty", pool)

    def test_scrape_lane_used_without_concrete_signup_niche(self):
        self.assertIn("skincare", P._roster_category_pool({"primary_niche": "beauty"}, []))


class SeededNicheTests(unittest.TestCase):
    CREATOR = {"creator_niches": ["fitness"]}

    def test_seed_uses_signup_pick_over_scrape(self):
        from services.polly_discovery import seed_from_survey
        notes = seed_from_survey({}, {}, {"primary_niche": "lifestyle"}, self.CREATOR)
        self.assertEqual(notes["niche"], ["fitness"])

    def test_seed_falls_back_to_scrape(self):
        from services.polly_discovery import seed_from_survey
        self.assertEqual(seed_from_survey({}, {}, {"primary_niche": "skincare"}, {})["niche"], "skincare")

    def test_heals_old_scrape_seed(self):
        from services.polly_discovery import heal_seeded_niche
        notes = {"niche": "lifestyle", "discovery_source": "survey"}
        self.assertEqual(heal_seeded_niche(notes, {"primary_niche": "lifestyle"}, self.CREATOR)["niche"], ["fitness"])

    def test_never_touches_a_niche_from_chat(self):
        from services.polly_discovery import heal_seeded_niche
        chat = {"niche": "lifestyle", "discovery_source": "chat"}
        self.assertEqual(heal_seeded_niche(chat, {"primary_niche": "lifestyle"}, self.CREATOR)["niche"], "lifestyle")
        other = {"niche": "beauty", "discovery_source": "survey"}
        self.assertEqual(heal_seeded_niche(other, {"primary_niche": "lifestyle"}, self.CREATOR)["niche"], "beauty")


class MatchScrapeTests(unittest.TestCase):
    def test_vague_scrape_yields_to_signup_niche(self):
        out = R._match_scrape({"primary_niche": "lifestyle", "secondary_niches": ["home"]}, {},
                              {"creator_niches": ["Fitness"]})
        self.assertEqual(out["primary_niche"], "fitness")
        self.assertEqual(out["secondary_niches"], ["fitness"])

    def test_concrete_scrape_kept(self):
        out = R._match_scrape({"primary_niche": "skincare"}, {}, {"creator_niches": ["Fitness"]})
        self.assertEqual(out["primary_niche"], "skincare")

    def test_chat_stated_niche_wins(self):
        out = R._match_scrape({"primary_niche": "lifestyle"}, {"niche": "beauty"}, {"creator_niches": ["Fitness"]})
        self.assertNotEqual(out["primary_niche"], "fitness")
