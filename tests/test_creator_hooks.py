import unittest


class CreatorNicheTests(unittest.TestCase):
    def test_scan_targets_the_creators_own_niches(self):
        from services.brand_insights import creator_question, niche_label
        from services.creator_hooks import creator_niche

        creator = {"creator_niches": ["travel", "fashion", "wellness"], "niche": '["travel", "fashion", "wellness"]'}
        key = creator_niche(creator)
        self.assertEqual(key, "travel+fashion+wellness")
        self.assertEqual(niche_label(key), "travel, fashion and wellness")
        q = creator_question(key, "5K to 20K")
        self.assertIn("travel, fashion and wellness creators", q)
        self.assertNotIn("home", q)

    def test_travel_is_never_mapped_to_home(self):
        from services.creator_hooks import creator_niche

        self.assertEqual(creator_niche({"creator_niches": ["travel"], "niche": '["travel"]'}), "travel")

    def test_fallback_searches_cover_every_niche(self):
        from services.brand_insights import _fallback_searches

        qs = " ".join(_fallback_searches("creator_hooks", "travel+fashion+wellness"))
        for word in ("travel", "fashion", "wellness"):
            self.assertIn(word, qs)


if __name__ == "__main__":
    unittest.main()
