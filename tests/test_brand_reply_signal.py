import unittest

from services import brand_reply_signal as rs
from services.polly import cold_brand_chips
from services.polly_persona import persona_cold_brand_warning


class BuildStatsTests(unittest.TestCase):
    def test_merges_sources_per_brand(self):
        stats = rs.build_stats(
            [{"brand_id": 1, "pitched": 20, "replied": 1}, {"brand_id": "x", "pitched": 5, "replied": 0}],
            [{"brand_id": 1, "replied": 3}, {"brand_id": 2, "replied": 1}],
            [{"brand_id": 3, "selected": 2}],
        )
        self.assertEqual(stats[1], {"pitched": 20, "replied": 3, "selected": 0})
        self.assertEqual(stats[2], {"pitched": 0, "replied": 1, "selected": 0})
        self.assertEqual(stats[3], {"pitched": 0, "replied": 0, "selected": 2})
        self.assertNotIn("x", stats)

    def test_tuple_rows(self):
        stats = rs.build_stats([(7, 10, 2)], [], [(7, 1)])
        self.assertEqual(stats[7], {"pitched": 10, "replied": 2, "selected": 1})


class ClassifyTests(unittest.TestCase):
    def test_selected_beats_everything(self):
        self.assertEqual(rs.classify({"pitched": 200, "replied": 0, "selected": 1}), "replies")

    def test_reply_rate_threshold(self):
        self.assertEqual(rs.classify({"pitched": 20, "replied": 1}), "replies")
        self.assertIsNone(rs.classify({"pitched": 25, "replied": 1}))
        self.assertEqual(rs.classify({"pitched": 40, "replied": 1}), "cold")

    def test_cold_needs_volume(self):
        self.assertEqual(rs.classify({"pitched": 159, "replied": 0}), "cold")
        self.assertEqual(rs.classify({"pitched": 148, "replied": 2}), "cold")
        self.assertIsNone(rs.classify({"pitched": 14, "replied": 0}))

    def test_empty(self):
        self.assertIsNone(rs.classify(None))
        self.assertIsNone(rs.classify({}))


class SignalForTests(unittest.TestCase):
    def test_replies_detail_prefers_selection(self):
        sig = rs.signal_for({"pitched": 3, "replied": 1, "selected": 1})
        self.assertEqual(sig["label"], "Replies to creators")
        self.assertEqual(sig["detail"], "Picked 1 Newcollab creator for gifted PR")

    def test_replies_detail_from_replies(self):
        sig = rs.signal_for({"pitched": 10, "replied": 4, "selected": 0})
        self.assertEqual(sig["detail"], "4 creators heard back after pitching")

    def test_cold_detail(self):
        sig = rs.signal_for({"pitched": 159, "replied": 0, "selected": 0})
        self.assertEqual(sig["tier"], "cold")
        self.assertEqual(sig["label"], "Rarely replies")
        self.assertEqual(sig["detail"], "159 creators pitched on Newcollab, none heard back")

    def test_no_signal(self):
        self.assertIsNone(rs.signal_for({"pitched": 2, "replied": 0}))


class OrderingTests(unittest.TestCase):
    STATS = {
        1: {"pitched": 100, "replied": 0, "selected": 0},
        2: {"pitched": 10, "replied": 3, "selected": 0},
    }

    def test_annotate_sets_and_clears(self):
        rows = rs.annotate([{"id": 1}, {"brand_id": 2}, {"id": 3, "reply_signal": {"tier": "cold"}}], self.STATS)
        self.assertEqual(rows[0]["reply_signal"]["tier"], "cold")
        self.assertEqual(rows[1]["reply_signal"]["tier"], "replies")
        self.assertNotIn("reply_signal", rows[2])

    def test_sort_is_stable(self):
        rows = rs.annotate([{"id": 1}, {"id": 4}, {"id": 2}, {"id": 5}], self.STATS)
        self.assertEqual([b["id"] for b in rs.sort_by_signal(rows)], [2, 4, 5, 1])

    def test_rerank_blends_fit(self):
        rows = rs.annotate([
            {"id": 1, "match_score": 95},
            {"id": 4, "match_score": 80},
            {"id": 2, "match_score": 70},
        ], self.STATS)
        self.assertEqual([b["id"] for b in rs.rerank_by_fit(rows)], [2, 4, 1])

    def test_strong_unknown_fit_can_beat_replies(self):
        rows = rs.annotate([{"id": 2, "match_score": 60}, {"id": 4, "match_score": 90}], self.STATS)
        self.assertEqual([b["id"] for b in rs.rerank_by_fit(rows)], [4, 2])


class ColdBrandChipsTests(unittest.TestCase):
    def test_alternatives_then_anyway(self):
        chips = cold_brand_chips(
            {"id": 9, "name": "Aveeno"},
            [{"id": 1, "name": "Glow Co"}, {"id": 2, "name": "Dew"}, {"id": 3, "name": "Extra"}],
        )
        self.assertEqual([c["label"] for c in chips], ["Pitch Glow Co instead", "Pitch Dew instead", "Pitch Aveeno anyway"])
        self.assertTrue(chips[-1]["confirm_cold"])
        self.assertEqual(chips[-1]["brand_id"], 9)
        self.assertNotIn("confirm_cold", chips[0])

    def test_no_alternatives_offers_line_up(self):
        chips = cold_brand_chips({"brand_id": 9, "brand_name": "Fenty"}, [])
        self.assertEqual(chips[0]["action"], "suggest_brands")
        self.assertEqual(chips[-1]["label"], "Pitch Fenty anyway")


class ColdWarningCopyTests(unittest.TestCase):
    def test_mentions_numbers_and_alternatives(self):
        say = persona_cold_brand_warning(
            "Aveeno", {"pitched": 159, "replied": 0}, [{"name": "Glow Co"}], remaining=2,
        )
        self.assertIn("**Aveeno** rarely answers creators", say)
        self.assertIn("**159** creators", say)
        self.assertIn("none of them heard back", say)
        self.assertIn("**2** free credits left", say)
        self.assertIn("**Glow Co**", say)

    def test_without_alternatives(self):
        say = persona_cold_brand_warning("Rhode", {"pitched": 148, "replied": 2}, [], remaining=1)
        self.assertIn("only 2 heard back", say)
        self.assertIn("**1** free credit left", say)
        self.assertIn("pitch Rhode anyway", say)


if __name__ == "__main__":
    unittest.main()
