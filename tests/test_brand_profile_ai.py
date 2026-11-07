"""Brand profiler: evidence-grounded category fixes, fill-vs-overwrite rules, no profile without evidence."""

import unittest
from types import SimpleNamespace
from unittest import mock

from services import brand_profile_ai as bp


def _result(**kw):
    base = {"category": "food", "category_confidence": "high", "category_reason": "sells pancake mix",
            "is_consumer_brand": True, "hero_product": "Protein Pancake Mix", "description": "X makes pancake mix.",
            "target_audience": "gym-goers", "tone": "playful", "price_point": 24, "instagram_handle": "@flourish",
            "tiktok_handle": None, "youtube_handle": "null", "min_followers": 200, "collaboration_type": "gifted",
            "seo_title": "T", "seo_description": "D"}
    base.update(kw)
    return base


class ProfileUpdatesTests(unittest.TestCase):
    def test_confident_category_replaces_beauty(self):
        up = bp.profile_updates({"category": "beauty", "description": "old"}, _result())
        self.assertEqual(up["category"], "food")
        self.assertNotIn("description", up)  # fill-only keeps existing text
        self.assertEqual(up["hero_product"], "Protein Pancake Mix")
        self.assertEqual(up["instagram_handle"], "flourish")
        self.assertNotIn("youtube_handle", up)
        self.assertEqual(up["min_followers"], 1000)

    def test_low_confidence_keeps_category(self):
        up = bp.profile_updates({"category": "beauty"}, _result(category_confidence="low"))
        self.assertNotIn("category", up)
        self.assertEqual(up["category_confidence"], "low")

    def test_overwrite_replaces_text_but_not_handles(self):
        up = bp.profile_updates({"category": "food", "description": "old", "instagram_handle": "typed"},
                                _result(), overwrite=True)
        self.assertNotIn("category", up)  # unchanged
        self.assertEqual(up["description"], "X makes pancake mix.")
        self.assertNotIn("instagram_handle", up)

    def test_fragrance_is_stored_as_beauty(self):
        self.assertEqual(bp.store_category(_result(category="fragrance")), "beauty")

    def test_no_result_no_updates(self):
        self.assertEqual(bp.profile_updates({}, None), {})


class ProfileBrandTests(unittest.TestCase):
    def test_no_evidence_skips_the_call(self):
        with mock.patch.object(bp, "_anthropic") as client:
            self.assertIsNone(bp.profile_brand({"brand_name": "X"}, {"website": None}))
        client.assert_not_called()

    def test_structured_output_request(self):
        resp = SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text='{"category": "jewelry"}')])
        client = mock.MagicMock()
        client.messages.create.return_value = resp
        ev = {"website": "https://indigolune.com", "products": [{"title": "Moon Ring", "type": "Rings", "price": 39}]}
        with mock.patch.object(bp, "_anthropic", return_value=client):
            out = bp.profile_brand({"brand_name": "Indigolune", "category": "beauty"}, ev)
        self.assertEqual(out, {"category": "jewelry"})
        kw = client.messages.create.call_args.kwargs
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Moon Ring | Rings | 39", kw["messages"][0]["content"])
        self.assertNotIn("beauty", kw["messages"][0]["content"])  # no anchoring on the old category

    def test_refusal_returns_none(self):
        client = mock.MagicMock()
        client.messages.create.return_value = SimpleNamespace(stop_reason="refusal", content=[])
        with mock.patch.object(bp, "_anthropic", return_value=client):
            self.assertIsNone(bp.profile_brand({"brand_name": "X"}, {"captions": ["gifted by X"]}))


class WriteUpdatesTests(unittest.TestCase):
    def test_sql(self):
        cur = mock.MagicMock()
        bp.write_updates(cur, 7, {"category": "food", "tone": "playful"})
        sql, params = cur.execute.call_args.args
        self.assertIn("category = %s, tone = %s, category_checked_at = NOW()", sql)
        self.assertEqual(params, ["food", "playful", 7])


if __name__ == "__main__":
    unittest.main()


class SchemaShapeTests(unittest.TestCase):
    """The API rejects an enum next to a union type (["string", "null"]): every call 400'd on Nov 6."""

    def walk(self, node):
        if isinstance(node, dict):
            if "enum" in node:
                self.assertIsInstance(node.get("type"), str, node)
                self.assertNotIn(None, node["enum"], node)
            self.assertFalse(isinstance(node.get("type"), list), node)
            for v in node.values():
                self.walk(v)
        elif isinstance(node, list):
            for v in node:
                self.walk(v)

    def test_no_enum_on_union_types(self):
        self.walk(bp.SCHEMA)
        self.assertEqual(set(bp.SCHEMA["required"]), set(bp.SCHEMA["properties"]))

    def test_rejected_request_is_surfaced(self):
        import anthropic
        import httpx
        err = anthropic.BadRequestError("bad schema", response=httpx.Response(
            400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body=None)
        client = mock.MagicMock()
        client.messages.create.side_effect = err
        bp.LAST_ERROR = None
        with mock.patch.object(bp, "_anthropic", return_value=client):
            self.assertIsNone(bp.profile_brand({"brand_name": "X"}, {"captions": ["gifted"]}))
        self.assertIn("400", bp.LAST_ERROR)
