"""Brand matching follows the niches a creator picked (Nov 2026: a wellness/supplements/fitness creator was shown
three skincare brands because wellness expanded to skincare and a 'wellness is not fitness' rule dropped fitness)."""
import unittest

from services.polly_discovery import categories_for_notes, required_categories_for_match


class PickedNichesAreTheBrief(unittest.TestCase):
    def test_wellness_supplements_fitness(self):
        cats = categories_for_notes({"niche": ["wellness", "supplements", "fitness"]})
        self.assertEqual(set(cats), {"wellness", "supplements", "fitness", "activewear"})
        self.assertNotIn("skincare", cats)

    def test_explicit_fitness_survives_beauty_pick(self):
        cats = categories_for_notes({"niche": ["skincare", "fitness"]})
        self.assertIn("fitness", cats)
        self.assertIn("skincare", cats)

    def test_explicit_picks_beat_scrape(self):
        cats = required_categories_for_match({"niche": ["fitness"]}, {"primary_niche": "skincare"})
        self.assertEqual(set(cats), {"fitness", "activewear"})

    def test_scrape_only_still_keeps_wellness_apart_from_gym(self):
        cats = required_categories_for_match({}, {"primary_niche": "skincare", "secondary_niches": ["fitness"]})
        self.assertNotIn("fitness", cats)


if __name__ == "__main__":
    unittest.main()
