from services import brand_creator_picks as picks

POOL = [
    {"id": 1, "username": "glowwithsam", "kit_slug": None, "niche": '["skincare", "beauty"]', "social_platform": "instagram", "followers": 21356},
    {"id": 2, "username": "fitbyleo", "kit_slug": "leo-fit", "niche": "fitness", "social_platform": "tiktok", "followers": 4100},
    {"id": 3, "username": "dewydays", "kit_slug": None, "niche": "makeup", "social_platform": "tiktok", "followers": 980},
    {"id": 4, "username": "homeandhue", "kit_slug": None, "niche": '["home", "lifestyle"]', "social_platform": "instagram", "followers": 12000},
    {"id": 5, "username": "beautybyana", "kit_slug": None, "niche": "beauty", "social_platform": "instagram", "followers": 7600},
    {"id": 6, "username": "randomother", "kit_slug": None, "niche": "other", "social_platform": "instagram", "followers": 7600},
]


def test_parse_niches_handles_json_arrays_and_plain_strings():
    assert picks.parse_niches('["Skincare", "beauty"]') == ["skincare", "beauty"]
    assert picks.parse_niches("fitness") == ["fitness"]
    assert picks.parse_niches("other") == [] and picks.parse_niches(None) == []


def test_skincare_brand_gets_three_beauty_creators_with_kit_links():
    got = picks.pick_creators(None, 42, "skincare", pool=POOL)
    assert {c["creator_id"] for c in got} == {1, 3, 5}
    assert got[0]["creator_id"] == 1  # matches the brand's first niche (skincare)
    assert all(c["url"].startswith("https://newcollab.co/kit/") for c in got)


def test_kit_slug_wins_over_username_and_unmatched_niches_are_left_out():
    got = picks.pick_creators(None, 7, "fitness", pool=POOL)
    assert got[0]["url"] == "https://newcollab.co/kit/leo-fit"
    assert 6 not in {c["creator_id"] for c in got}


def test_order_is_stable_per_brand_but_varies_across_brands():
    a = [c["creator_id"] for c in picks.pick_creators(None, 1, "beauty", pool=POOL, n=2)]
    assert a == [c["creator_id"] for c in picks.pick_creators(None, 1, "beauty", pool=POOL, n=2)]
    seen = {tuple(c["creator_id"] for c in picks.pick_creators(None, b, "beauty", pool=POOL, n=2)) for b in range(30)}
    assert len(seen) > 1


def test_creator_line():
    c = picks.pick_creators(None, 42, "skincare", pool=POOL)[0]
    assert picks.creator_line(c) == ("• @glowwithsam: skincare & beauty, 21K followers on Instagram. "
                                     "[Portfolio](https://newcollab.co/kit/glowwithsam)")
    assert picks.followers_label(4100) == "4.1K" and picks.followers_label(980) == "980"
