import unittest

from services import brand_reply_signal as rs
from services.polly import cold_brand_chips
from services.polly_persona import persona_cold_brand_warning


class ThanosEvidenceTests(unittest.TestCase):
    def test_load_stats_reads_thanos_signals(self):
        from unittest import mock
        from services.thanos.model import Evidence
        from services.thanos.signals import Stats

        stats = Stats(brand={1: Evidence(sends=20, positive=2, rejected=1, selected=0),
                             3: Evidence(selected=2)})
        cursor = mock.MagicMock()
        with mock.patch("services.thanos.signals.load", return_value=stats) as load:
            out = rs.load_stats(cursor)
        self.assertIs(load.call_args.args[0], cursor.connection)
        self.assertEqual(out[1], {"pitched": 20, "replied": 3, "positive": 2, "selected": 0})
        self.assertEqual(out[3], {"pitched": 0, "replied": 0, "positive": 0, "selected": 2})

    def test_cold_is_thanos_cold(self):
        from services.thanos.model import Evidence, is_cold

        for stat in ({"pitched": 40, "replied": 1}, {"pitched": 14, "replied": 0}, {"pitched": 100, "replied": 0,
                                                                                    "selected": 1}):
            ev = Evidence(sends=stat["pitched"], positive=stat["replied"], selected=stat.get("selected", 0))
            self.assertEqual(rs.classify(stat) == "cold", is_cold(ev))


class PublicDirectoryEvidenceTests(unittest.TestCase):
    def test_public_pages_read_thanos_signals(self):
        from unittest import mock
        from public_routes import reply_evidence
        from services.thanos.model import Evidence
        from services.thanos.signals import Stats

        stats = Stats(brand={1: Evidence(sends=30), 2: Evidence(sends=5, positive=1), 3: Evidence(sends=4)})
        with mock.patch("services.thanos.signals.load", return_value=stats) as load:
            badge_stats, tiers = reply_evidence("conn")
        load.assert_called_once_with("conn")
        self.assertEqual(tiers, {"replies": [2], "cold": [1]})
        self.assertEqual(rs.signal_for(badge_stats[1])["tier"], "cold")


class ClassifyTests(unittest.TestCase):
    def test_selected_beats_everything(self):
        self.assertEqual(rs.classify({"pitched": 200, "replied": 0, "selected": 1}), "replies")

    def test_replies_means_heard_back_unless_cold(self):
        self.assertEqual(rs.classify({"pitched": 20, "replied": 1}), "replies")
        self.assertEqual(rs.classify({"pitched": 25, "replied": 1}), "replies")
        self.assertEqual(rs.classify({"pitched": 40, "replied": 1}), "cold")

    def test_a_no_thanks_is_not_replies(self):
        self.assertIsNone(rs.classify({"pitched": 10, "replied": 1, "positive": 0}))

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
        self.assertEqual(sig["detail"], "Picked a Newcollab creator for gifted PR")

    def test_replies_detail_from_replies(self):
        sig = rs.signal_for({"pitched": 10, "replied": 4, "selected": 0})
        self.assertEqual(sig["detail"], "Answered 4 Newcollab creators who pitched them")

    def test_single_reply_has_no_count(self):
        sig = rs.signal_for({"pitched": 5, "replied": 1, "selected": 0})
        self.assertEqual(sig["detail"], "Has answered Newcollab creators who pitched them")

    def test_cold_detail(self):
        sig = rs.signal_for({"pitched": 159, "replied": 0, "selected": 0})
        self.assertEqual(sig["tier"], "cold")
        self.assertEqual(sig["label"], "Long shot")
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
