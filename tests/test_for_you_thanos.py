"""For You and the cold-spend alternatives read their brands from Thanos."""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from flask import Flask

import pr_crm_routes as R
from services.thanos.engine import Ranking
from services.thanos.profile import CreatorProfile


def _card(i, category="skincare", **extra):
    return {"id": i, "slug": f"b{i}", "name": f"Brand {i}", "brand_name": f"Brand {i}", "logo": None,
            "category": category, "match_score": 70, "fit_tier": "good_match", "reply_band": "medium",
            "reply_chance": 0.2, "roster_is_open": 0, "roster_spotlighted": 0, "source": "thanos", **extra}


class _Cursor:
    """Answers the two id lookups For You makes; everything else returns nothing."""

    def __init__(self):
        self.last = ""
        self.connection = MagicMock()

    def execute(self, sql, params=None):
        self.last = sql

    def fetchall(self):
        if "creator_pipeline" in self.last:
            return [{"brand_id": 13}]
        if "added_at" in self.last:
            return [{"id": 14, "added_at": datetime(2026, 1, 1)}, {"id": 15, "added_at": datetime(2026, 11, 1)}]
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class ForYouTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.prof = CreatorProfile(creator_id=7, followers=5000, interests=["skincare"], roster_categories={"skincare"})
        self.conn = MagicMock()
        self.conn.cursor.return_value = _Cursor()

    def _call(self, query=""):
        calls = []

        def fake_rank(conn, creator_id, surface="polly", n=6, **kw):
            calls.append({"surface": surface, "n": n, **kw})
            if surface == "autopilot":
                return Ranking(brands=[_card(90)], level=1, note="close to your niche")
            if kw.get("categories"):
                return Ranking(brands=[_card(20, "beauty")])
            if n == R.FOR_YOU_MATCHED:
                return Ranking(brands=[_card(1), _card(2, roster_is_open=1)], level=1, note="close to your niche")
            return Ranking(brands=[_card(11), _card(12), _card(13), _card(14), _card(15)])

        with self.app.test_request_context(f"/api/pr-crm/for-you{query}"), \
                patch.object(R, "get_creator_id_from_session", return_value=7), \
                patch.object(R, "get_db_connection", return_value=self.conn), \
                patch.object(R, "get_subscription_status", return_value="free"), \
                patch("services.roster_demand.ensure_campaign_spotlight_column"), \
                patch("services.roster_demand.fetch_spotlighted_brand_rows", return_value=[]), \
                patch("services.brand_reply_signal.load_stats", return_value={}), \
                patch("services.thanos.load_profile", return_value=self.prof), \
                patch("services.thanos.rank", side_effect=fake_rank):
            body = R.get_for_you().get_json()
        return body, calls

    def test_matched_is_the_thanos_ranking(self):
        body, calls = self._call()
        self.assertTrue(body["success"])
        self.assertEqual([b["id"] for b in body["matched"]], [1, 2])
        self.assertEqual(body["note"], "close to your niche")
        self.assertEqual(calls[0]["surface"], "for_you")
        self.assertEqual(calls[0]["n"], 8)
        # The browse pool never repeats matched brands.
        self.assertEqual(calls[1]["exclude_ids"], [1, 2])
        self.assertEqual(body["profile"], {"niches": ["skincare"], "followers": 5000})
        self.assertTrue(body["has_profile"])

    def test_sections_are_views_over_thanos(self):
        body, calls = self._call()
        # Hot: the recently pitched brand first, then Thanos order.
        self.assertEqual([b["id"] for b in body["hot"]][0], 13)
        # Seasonal: Thanos restricted to this month's categories.
        seasonal_call = next(c for c in calls if c.get("categories"))
        self.assertEqual(seasonal_call["categories"], R.FOR_YOU_SEASONAL_CATEGORIES[datetime.now().month])
        self.assertEqual([b["id"] for b in body["seasonal"]], [20])
        # Open lists come from the Thanos brands with an open roster.
        self.assertEqual([b["id"] for b in body["open_lists"]], [2])
        for key in ("hot", "matched", "open_lists", "recruiting", "seasonal", "newest", "seasonal_reason",
                    "seasonal_month", "is_pro", "has_profile", "profile"):
            self.assertIn(key, body)

    def test_autopilot_pool_uses_autopilot_surface(self):
        body, calls = self._call("?pool=autopilot&exclude=4,5,x")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["surface"], "autopilot")
        self.assertEqual(calls[0]["exclude_ids"], [4, 5])
        self.assertEqual([b["id"] for b in body["matched"]], [90])


class ColdSpendAlternativesTests(unittest.TestCase):
    def test_alternatives_come_from_thanos(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"category": "skincare"}
        conn = MagicMock()
        conn.cursor.return_value = cursor
        ranking = Ranking(brands=[_card(5), _card(6)])
        with patch("services.brand_reply_signal.brand_signal", return_value={"tier": "cold"}), \
                patch.object(R, "get_creator_unlock_balance", return_value={"remaining": 2}), \
                patch.object(R, "brand_already_delivered", return_value=False), \
                patch("services.thanos.rank", return_value=ranking) as rank:
            out = R.cold_spend_warning(7, 99, conn=conn)
        self.assertTrue(out["warn"])
        self.assertEqual([b["id"] for b in out["alternatives"]], [5, 6])
        args, kwargs = rank.call_args
        self.assertEqual(kwargs["surface"], "alternatives")
        self.assertEqual(kwargs["categories"], ["skincare"])
        self.assertEqual(kwargs["exclude_ids"], [99])


if __name__ == "__main__":
    unittest.main()
