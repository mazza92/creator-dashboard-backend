import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.kit_view_tracking import (
    generate_kit_token,
    public_kit_url,
    record_brand_profile_view,
    resolve_brand_from_kit_ref,
)


class ScriptedCursor:
    def __init__(self, fetchone=None, fetchall=None):
        self.sqls = []
        self.params = []
        self._fetchone = list(fetchone or [])
        self._fetchall = list(fetchall or [])

    def execute(self, sql, params=None):
        self.sqls.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchone(self):
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchall(self):
        return self._fetchall.pop(0) if self._fetchall else []


class TestGenerateKitToken(unittest.TestCase):
    def test_deterministic_for_pair(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret"}):
            a = generate_kit_token(10, 20)
            b = generate_kit_token(10, 20)
            c = generate_kit_token(10, 21)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 12)
        self.assertNotEqual(a, c)

    def test_public_kit_url_includes_ref(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret", "PUBLIC_KIT_BASE_URL": "https://newcollab.co"}):
            url = public_kit_url("smartstylenatasharosemills", 10, 20)
            token = generate_kit_token(10, 20)
        self.assertEqual(
            url,
            f"https://newcollab.co/kit/smartstylenatasharosemills?ref={token}",
        )

    def test_public_kit_url_blank_without_slug(self):
        self.assertEqual(public_kit_url("", 10, 20), "")
        self.assertEqual(public_kit_url("natasha", None, 20), "")


class TestResolveBrandFromKitRef(unittest.TestCase):
    def test_pipeline_hit(self):
        row = {
            "pipeline_id": 5,
            "creator_id": 10,
            "brand_id": 20,
            "brand_name": "Glow Co",
            "brand_category": "skincare",
        }
        cursor = ScriptedCursor(fetchone=[row])
        out = resolve_brand_from_kit_ref(cursor, "abc123", creator_id=10)
        self.assertEqual(out["brand_name"], "Glow Co")
        self.assertEqual(out["pipeline_id"], 5)

    def test_pipeline_creator_mismatch_is_ignored(self):
        row = {
            "pipeline_id": 5,
            "creator_id": 99,
            "brand_id": 20,
            "brand_name": "Glow Co",
            "brand_category": "skincare",
        }
        cursor = ScriptedCursor(fetchone=[row])
        self.assertIsNone(resolve_brand_from_kit_ref(cursor, "abc123", creator_id=10))

    def test_falls_back_to_application_token(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret"}):
            token = generate_kit_token(10, 20)
            cursor = ScriptedCursor(
                fetchone=[None],
                fetchall=[[
                    {
                        "creator_id": 10,
                        "brand_id": 20,
                        "brand_name": "Glow Co",
                        "brand_category": "skincare",
                    }
                ]],
            )
            out = resolve_brand_from_kit_ref(cursor, token, creator_id=10)
        self.assertEqual(out["brand_id"], 20)
        self.assertIsNone(out["pipeline_id"])

    def test_falls_back_to_pipeline_brands_without_stored_token(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret"}):
            token = generate_kit_token(10, 77)
            cursor = ScriptedCursor(
                fetchone=[None],
                fetchall=[
                    [],
                    [{
                        "pipeline_id": 3,
                        "brand_id": 77,
                        "brand_name": "IONIQ Skincare US",
                        "brand_category": "skincare",
                    }],
                ],
            )
            out = resolve_brand_from_kit_ref(cursor, token, creator_id=10)
        self.assertEqual(out["brand_id"], 77)
        self.assertEqual(out["brand_name"], "IONIQ Skincare US")


class TestRecordBrandProfileView(unittest.TestCase):
    def test_inserts_when_no_recent_view(self):
        cursor = ScriptedCursor(fetchone=[None])
        result = record_brand_profile_view(
            cursor,
            creator_id=10,
            brand_id=20,
            brand_name="Glow Co",
            brand_category="skincare",
            notify=False,
        )
        self.assertTrue(result["recorded"])
        self.assertFalse(result["emailed"])
        self.assertTrue(any("INSERT INTO kit_views" in sql for sql in cursor.sqls))

    def test_increments_instead_of_inserting_duplicate(self):
        cursor = ScriptedCursor(fetchone=[{"id": 44}])
        result = record_brand_profile_view(
            cursor,
            creator_id=10,
            brand_id=20,
            brand_name="Glow Co",
            notify=False,
        )
        self.assertFalse(result["recorded"])
        self.assertTrue(any("UPDATE kit_views" in sql for sql in cursor.sqls))
        self.assertFalse(any("INSERT INTO kit_views" in sql for sql in cursor.sqls))
