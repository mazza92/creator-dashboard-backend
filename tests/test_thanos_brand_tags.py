"""Gemini niche tags for brands (services.thanos.brand_tags)."""
import unittest
from unittest.mock import MagicMock

from services.thanos import brand_tags as T


class CleanTags(unittest.TestCase):
    def test_keeps_category_first_and_only_canonical_niches(self):
        self.assertEqual(T.clean_tags(["Travel", "fashion", "luggage", "travel"], "fashion"), ["fashion", "travel"])
        self.assertEqual(T.clean_tags(["skincare", "beauty", "wellness", "lifestyle", "home"], "skincare"),
                         ["skincare", "beauty", "wellness", "lifestyle"])
        self.assertEqual(T.clean_tags("travel", "other"), [])
        self.assertEqual(T.clean_tags(None, "travel"), ["travel"])


class TagRows(unittest.TestCase):
    def test_maps_answers_to_brands_and_skips_unknown_ids(self):
        rows = [{"id": 1, "brand_name": "Béis", "category": "fashion", "description": "Luggage and travel bags"},
                {"id": 2, "brand_name": "Glow", "category": "skincare"}]
        seen = {}

        def ask(system, user):
            seen["user"] = user
            return {"brands": [{"id": 1, "niches": ["travel", "fashion"]}, {"id": 99, "niches": ["pet"]},
                               {"id": "x"}]}

        out = T.tag_rows(rows, ask=ask)
        self.assertEqual(out, {1: ["fashion", "travel"]})
        self.assertIn("Luggage and travel bags", seen["user"])

    def test_prompt_is_strict(self):
        self.assertIn("Be strict", T.SYSTEM)
        self.assertIn("sunscreen is not travel", T.SYSTEM)


class RunBatch(unittest.TestCase):
    def test_tags_answered_brands_and_retries_the_rest_tomorrow(self):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = [{"id": 1, "brand_name": "Béis", "category": "fashion"},
                                     {"id": 2, "brand_name": "Mystery", "category": "other"}]
        cur.fetchone.return_value = {"n": 10}
        out = T.run_batch(conn, ask=lambda s, u: {"brands": [{"id": 1, "niches": ["travel"]}]})
        self.assertEqual(out, {"tagged": 1, "failed": 1, "left": 10})
        sqls = [c[0][0] for c in cur.execute.call_args_list]
        self.assertTrue(any("SET match_niches = %s" in q for q in sqls))
        self.assertTrue(any(q.startswith("UPDATE pr_brands SET match_niches_at = NOW()") for q in sqls))


if __name__ == "__main__":
    unittest.main()
