import unittest

from services.pro_roster_placement import (
    complete_address,
    creator_niches,
    niche_fits,
    plan_placements,
    ships_to,
)


def _creator(cid, niches='["beauty", "skincare"]', country="US"):
    return {"creator_id": cid, "niche": niches, "creator_niches": [], "country": country}


def _roster(bid, category="Skincare", regions=None, name="Glow Co"):
    return {
        "brand_id": bid,
        "campaign_id": bid * 10,
        "brand_name": name,
        "category": category,
        "brand_niches": [],
        "regions": regions or [],
    }


class TestShipsTo(unittest.TestCase):
    def test_unknown_passes(self):
        self.assertTrue(ships_to([], "US"))
        self.assertTrue(ships_to(["US"], None))

    def test_codes_and_names(self):
        self.assertTrue(ships_to(["US", "CA"], "United States"))
        self.assertTrue(ships_to('["UK"]', "GB"))
        self.assertFalse(ships_to(["US"], "GB"))

    def test_global_and_eu(self):
        self.assertTrue(ships_to(["Global"], "AU"))
        self.assertTrue(ships_to(["EU"], "FR"))
        self.assertFalse(ships_to(["EU"], "US"))


class TestNicheFit(unittest.TestCase):
    def test_beauty_creator_fits_skincare_brand(self):
        self.assertTrue(niche_fits(["beauty"], {"category": "Skincare"}))

    def test_beauty_creator_not_fitness_brand(self):
        self.assertFalse(niche_fits(["beauty"], {"category": "Fitness"}))

    def test_unknown_lane_is_not_a_fit(self):
        self.assertFalse(niche_fits([], {"category": "Skincare"}))
        self.assertFalse(niche_fits(["beauty"], {"category": ""}))

    def test_creator_niches_merges_sources(self):
        row = {"creator_niches": ["Beauty"], "niche": '["beauty", "Fashion"]'}
        self.assertEqual(creator_niches(row), ["Beauty", "Fashion"])


class TestAddress(unittest.TestCase):
    def test_complete(self):
        self.assertTrue(complete_address({
            "full_name": "Ana B", "address_line1": "1 Main", "city": "NYC", "country": "US",
        }))
        self.assertFalse(complete_address({"full_name": "Ana", "city": "NYC"}))


class TestPlan(unittest.TestCase):
    def test_daily_cap_and_skip_applied(self):
        rosters = [_roster(i) for i in range(1, 6)]
        plan = plan_placements([_creator(7)], rosters, {(7, 1)}, {}, {7: 1}, daily_cap=3)
        self.assertEqual([r["brand_id"] for _, r in plan], [2, 3])

    def test_per_roster_cap_spreads_creators(self):
        creators = [_creator(1), _creator(2), _creator(3)]
        rosters = [_roster(10), _roster(11)]
        plan = plan_placements(creators, rosters, set(), {10: 1}, {}, daily_cap=1, per_roster=2)
        self.assertEqual([(c["creator_id"], r["brand_id"]) for c, r in plan], [(1, 10), (2, 11), (3, 11)])

    def test_skips_off_niche_and_wrong_region(self):
        rosters = [_roster(1, category="Fitness"), _roster(2, regions=["GB"]), _roster(3)]
        plan = plan_placements([_creator(5)], rosters, set(), {}, {})
        self.assertEqual([r["brand_id"] for _, r in plan], [3])


if __name__ == "__main__":
    unittest.main()
