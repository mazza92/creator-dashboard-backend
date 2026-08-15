import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.profile_quality import (
    MAX_LAST_POST_DAYS,
    MIN_FOLLOWERS,
    MIN_POSTS,
    ProfileQualityError,
    assert_onboarding_quality,
    assess_visual_clarity,
    fails_follower_floor,
    fails_post_floor,
    fails_recency,
    interpret_visual_review,
    raw_follower_count,
    visible_post_count,
)


def _ok_profile(**overrides):
    profile = {
        "followersCount": 800,
        "postsCount": 20,
        "latest_post_days_ago": 5,
        "recent_post_thumbnails": ["https://cdn.example/1.jpg"] * 4,
        "recent_posts": [{"id": i} for i in range(12)],
    }
    profile.update(overrides)
    return profile


class TestFollowerFloor(unittest.TestCase):
    def test_floor_is_500(self):
        self.assertEqual(MIN_FOLLOWERS, 500)
        self.assertEqual(MIN_POSTS, 12)
        self.assertEqual(MAX_LAST_POST_DAYS, 30)

    def test_unknown_count_is_not_a_reject(self):
        self.assertFalse(fails_follower_floor(0))
        self.assertFalse(fails_follower_floor(None))

    def test_rejects_below_floor(self):
        self.assertTrue(fails_follower_floor(20))
        self.assertTrue(fails_follower_floor(499))

    def test_passes_at_or_above_floor(self):
        self.assertFalse(fails_follower_floor(500))


class TestPostAndRecency(unittest.TestCase):
    def test_post_floor(self):
        self.assertTrue(fails_post_floor(0))
        self.assertTrue(fails_post_floor(11))
        self.assertFalse(fails_post_floor(12))

    def test_visible_uses_grid_when_count_missing(self):
        self.assertEqual(visible_post_count({"latestPosts": [{}] * 8}), 8)
        self.assertEqual(visible_post_count({"postsCount": 40, "latestPosts": [{}] * 8}), 40)

    def test_recency(self):
        self.assertTrue(fails_recency(None))
        self.assertTrue(fails_recency(31))
        self.assertFalse(fails_recency(0))
        self.assertFalse(fails_recency(30))


class TestAssertQuality(unittest.TestCase):
    def test_follower_reject(self):
        with self.assertRaises(ProfileQualityError) as ctx:
            assert_onboarding_quality(_ok_profile(followersCount=20), "4lla.pam")
        self.assertEqual(ctx.exception.code, "below_follower_min")

    def test_post_reject(self):
        with self.assertRaises(ProfileQualityError) as ctx:
            assert_onboarding_quality(
                _ok_profile(postsCount=4, recent_posts=[{}] * 4),
                "thin",
            )
        self.assertEqual(ctx.exception.code, "below_post_min")
        self.assertEqual(ctx.exception.post_count, 4)

    def test_inactive_reject(self):
        with self.assertRaises(ProfileQualityError) as ctx:
            assert_onboarding_quality(_ok_profile(latest_post_days_ago=80), "old")
        self.assertEqual(ctx.exception.code, "inactive")

    def test_unknown_recency_rejects(self):
        with self.assertRaises(ProfileQualityError) as ctx:
            assert_onboarding_quality(_ok_profile(latest_post_days_ago=999), "undated")
        self.assertEqual(ctx.exception.code, "inactive")

    def test_visual_not_enough_media(self):
        with patch("services.profile_quality.download_thumbnails", return_value=[]):
            assert_onboarding_quality(
                _ok_profile(recent_post_thumbnails=["https://cdn.example/1.jpg"]),
                "blurry",
                check_visual=True,
            )

    def test_visual_low_score_does_not_block(self):
        with patch(
            "services.profile_quality.assess_visual_clarity",
            return_value={"ok": False, "score": 20, "reason": "low_quality"},
        ):
            assert_onboarding_quality(_ok_profile(), "messy", check_visual=True)

    def test_passes_full_bar_without_visual(self):
        assert_onboarding_quality(_ok_profile(), "ok")

    def test_raw_count_reads_ig_tiktok_and_youtube_keys(self):
        self.assertEqual(raw_follower_count({"followersCount": 20}), 20)
        self.assertEqual(raw_follower_count({"followerCount": 800}), 800)
        self.assertEqual(raw_follower_count({"subscriberCount": 1200}), 1200)


class TestVisualAssess(unittest.TestCase):
    def test_not_enough_downloads(self):
        with patch("services.profile_quality.download_thumbnails", return_value=[]):
            result = assess_visual_clarity(["https://cdn.example/1.jpg"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "skipped")

    def test_personal_filtered_no_product_fails(self):
        review = interpret_visual_review({
            "score": 72,
            "clear_and_clean": True,
            "ugc_style": False,
            "heavy_filters": True,
            "personal_only": True,
            "has_product_or_ugc_focus": False,
        })
        self.assertFalse(review["ok"])
        self.assertEqual(review["reason"], "no_brand_focus")

    def test_beauty_ugc_passes_even_if_gemini_flags_filters(self):
        review = interpret_visual_review({
            "score": 74,
            "clear_and_clean": True,
            "ugc_style": True,
            "heavy_filters": True,
            "personal_only": True,
            "has_product_or_ugc_focus": True,
        })
        self.assertTrue(review["ok"])

    def test_face_forward_grwm_passes_without_visible_product(self):
        review = interpret_visual_review({
            "score": 55,
            "clear_and_clean": True,
            "ugc_style": True,
            "heavy_filters": False,
            "personal_only": False,
            "has_product_or_ugc_focus": False,
        })
        self.assertTrue(review["ok"])

    def test_unboxing_passes(self):
        review = interpret_visual_review({
            "score": 68,
            "clear_and_clean": True,
            "ugc_style": True,
            "heavy_filters": False,
            "personal_only": False,
            "has_product_or_ugc_focus": True,
        })
        self.assertTrue(review["ok"])

    def test_personal_only_no_product_fails_even_if_sharp(self):
        review = interpret_visual_review({
            "score": 80,
            "clear_and_clean": True,
            "ugc_style": False,
            "heavy_filters": False,
            "personal_only": True,
            "has_product_or_ugc_focus": False,
        })
        self.assertFalse(review["ok"])
        self.assertEqual(review["reason"], "no_brand_focus")

    def test_ugc_with_product_passes(self):
        review = interpret_visual_review({
            "score": 78,
            "clear_and_clean": True,
            "heavy_filters": False,
            "personal_only": False,
            "has_product_or_ugc_focus": True,
        })
        self.assertTrue(review["ok"])
        self.assertEqual(review["reason"], "ok")

    def test_legacy_score_only_still_works(self):
        review = interpret_visual_review({"score": 80, "clear_and_clean": True})
        self.assertTrue(review["ok"])


if __name__ == "__main__":
    unittest.main()
