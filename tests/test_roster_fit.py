import unittest

from services import roster_fit as rf


def _card(app_id, *, status="review", country="US", posts=3, followers=4000, engagement=3.5, **kw):
    card = {
        "application_id": app_id, "status": status, "skipped": status == "declined",
        "selected": False, "country": {"US": "United States", "GB": "United Kingdom"}.get(country, country),
        "country_code": country, "followers": followers, "followers_label": f"{followers // 1000}K",
        "engagement": engagement, "engagement_label": f"{engagement}%",
        "posts": [{"thumbnail_url": f"https://x/{i}.jpg"} for i in range(posts)], "niche": "skincare",
    }
    card.update(kw)
    return card


class TestTargets(unittest.TestCase):
    def test_brief_and_category_map_to_groups(self):
        t = rf.brand_targets(category="Skin Care", brief_niches=["supplements"], brief_regions=["US", "UK"])
        self.assertEqual(t["niches"], ["wellness", "skincare"])
        self.assertEqual(t["countries"], {"US", "GB", "UK"})
        self.assertEqual(t["regions_label"], ["the US", "the UK"])

    def test_worldwide_means_no_country_filter(self):
        self.assertIsNone(rf.brand_targets(regions=["Worldwide", "US"])["countries"])
        self.assertIsNone(rf.brand_targets()["countries"])

    def test_postgres_array_text_parses(self):
        self.assertEqual(rf._as_list('{"1K-10K","10K-50K"}'), ["1K-10K", "10K-50K"])
        t = rf.brand_targets(follower_ranges='{"1K-10K"}')
        self.assertEqual(t["ranges"], [(1_000, 10_000)])


class TestFitScore(unittest.TestCase):
    targets = rf.brand_targets(category="wellness", brief_regions=["US"])

    def test_primary_niche_and_country_lead(self):
        score, reasons, ok = rf.fit_score(["Wellness", "beauty"], _card(1), self.targets)
        self.assertTrue(ok)
        self.assertIn("Wellness is their main niche", reasons)
        self.assertIn("Based in United States", reasons)
        off, _r, off_ok = rf.fit_score(["makeup", "fashion"], _card(2), self.targets)
        self.assertLess(off, score)

    def test_wrong_country_never_shortlisted(self):
        _s, _r, ok = rf.fit_score(["wellness"], _card(1, country="GB"), self.targets)
        self.assertFalse(ok)

    def test_no_samples_never_shortlisted(self):
        _s, _r, ok = rf.fit_score(["wellness"], _card(1, posts=0), self.targets)
        self.assertFalse(ok)

    def test_tick_every_box_creators_rank_lower(self):
        focused, _r, _ = rf.fit_score(["wellness", "fitness"], _card(1), self.targets)
        spray = ["wellness"] + ["beauty", "food", "home", "pet", "tech", "travel", "luxury", "baby"]
        broad, _r, _ = rf.fit_score(spray, _card(2), self.targets)
        self.assertGreater(focused, broad)


class TestRankAndVisibility(unittest.TestCase):
    def test_top_picks_are_best_eligible_review_cards(self):
        targets = rf.brand_targets(category="skincare", brief_regions=["US"])
        pairs = [(["skincare"], _card(i)) for i in range(1, 8)]
        pairs.append((["skincare"], _card(8, country="GB")))
        pairs.append((["skincare"], _card(9, status="declined")))
        ranked = rf.rank_cards(pairs, targets)
        top = [c["application_id"] for c in ranked if c["top_pick"]]
        self.assertEqual(len(top), rf.FREE_PICK_LIMIT)
        self.assertNotIn(8, top)
        self.assertNotIn(9, top)
        self.assertTrue(all(c["fit_reasons"] for c in ranked if c["top_pick"]))

    def test_free_brands_see_ten_plus_their_picks(self):
        cards = [_card(i) for i in range(1, 16)]
        cards[13]["selected"] = True
        cards[14]["status"] = "ships"
        shown, hidden = rf.split_visible(cards, subscribed=False)
        ids = {c["application_id"] for c in shown}
        self.assertEqual(len(shown), 12)
        self.assertIn(14, ids)
        self.assertIn(15, ids)
        self.assertEqual(len(hidden), 3)
        teaser = rf.locked_teasers(hidden)
        self.assertEqual(teaser["count"], 3)
        self.assertNotIn("application_id", teaser["teasers"][0])

    def test_subscribers_see_everyone_and_pick_ten(self):
        cards = [_card(i) for i in range(1, 16)]
        shown, hidden = rf.split_visible(cards, subscribed=True)
        self.assertEqual((len(shown), len(hidden)), (15, 0))
        self.assertEqual(rf.pick_limit_for(True), 10)
        self.assertEqual(rf.pick_limit_for(False), 5)


if __name__ == "__main__":
    unittest.main()
