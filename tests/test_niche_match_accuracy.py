"""Brand matching follows the niches a creator picked (Nov 2026: a wellness/supplements/fitness creator was shown
three skincare brands because wellness expanded to skincare and a 'wellness is not fitness' rule dropped fitness)."""
import unittest

from services.polly import balance_by_niche, flatten_for_you
from services.polly_discovery import categories_for_notes, required_categories_for_match


def _brand(i, cat, **kw):
    return {"id": i, "name": f"B{i}", "slug": f"b{i}", "category": cat, **kw}


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


class FlattenKeepsOnlyNicheBrands(unittest.TestCase):
    def test_skincare_and_uncategorised_brands_are_dropped(self):
        payload = {"matched": [_brand(1, "skincare"), _brand(2, "wellness"), _brand(3, ""), _brand(4, "fitness"),
                               _brand(5, "supplements"), _brand(6, "beauty")]}
        required = categories_for_notes({"niche": ["wellness", "supplements", "fitness"]})
        out = flatten_for_you(payload, required_categories=required, niches=["wellness", "supplements", "fitness"])
        self.assertEqual(sorted(b["category"] for b in out), ["fitness", "supplements", "wellness"])


class FirstMatchesCoverEachNiche(unittest.TestCase):
    def test_one_of_each_niche_first(self):
        rows = [_brand(1, "supplements"), _brand(2, "supplements"), _brand(3, "wellness"), _brand(4, "supplements"),
                _brand(5, "fitness")]
        out = balance_by_niche(rows, ["wellness", "supplements", "fitness"])
        self.assertEqual([b["category"] for b in out[:3]], ["wellness", "supplements", "fitness"])
        self.assertEqual(len(out), 5)

    def test_single_niche_keeps_order(self):
        rows = [_brand(1, "fitness"), _brand(2, "activewear")]
        self.assertEqual(balance_by_niche(rows, ["fitness"]), rows)

    def test_synonym_fills_a_niche_with_no_exact_brand(self):
        rows = [_brand(1, "supplements"), _brand(2, "activewear")]
        out = balance_by_niche(rows, ["wellness", "fitness"])
        self.assertEqual([b["id"] for b in out], [1, 2])


if __name__ == "__main__":
    unittest.main()
