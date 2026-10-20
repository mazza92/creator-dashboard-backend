import unittest
from unittest.mock import patch

import brand_apply_routes
from app import app
from services.polly import apply_work_samples_to_pitch, clean_sample_posts

POSTS = [
    {"post_url": "https://www.instagram.com/p/a1/"},
    {"post_url": "https://www.instagram.com/p/b2/"},
    {"post_url": "https://www.tiktok.com/@me/video/123"},
]
SHIP = {
    "full_name": "Maya Chen", "address_line1": "1 Main St", "city": "Austin",
    "state": "TX", "zip": "78704", "country": "United States", "phone": "+1 512 555 0199",
}


class FakeCursor:
    def __init__(self):
        self.executed = []
        self._next = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.executed.append((s, params))
        if s.startswith("SELECT id, brand_name FROM pr_brands"):
            self._next = {"id": 42, "brand_name": "Glow Recipe"}
        elif s.startswith("SELECT shipping_address FROM creators"):
            self._next = {"shipping_address": {"city": "Old"}}
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class PitchPrepRouteTests(unittest.TestCase):
    def _post(self, body):
        cursor = FakeCursor()
        conn = FakeConn(cursor)
        with patch.object(brand_apply_routes, "get_creator_id_from_session", return_value=7), \
             patch.object(brand_apply_routes, "get_db_connection", return_value=conn), \
             patch.object(brand_apply_routes, "_ensure_schema"), \
             patch("social_verification_routes.country_value_is_restricted", return_value=False):
            resp = app.test_client().post("/api/pr-crm/brands/42/pitch-prep", json=body)
        return resp, cursor, conn

    def test_saves_address_and_returns_samples_without_applying(self):
        resp, cursor, conn = self._post({"selected_posts": POSTS, "shipping": SHIP, "source": "foryou"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["brand_name"], "Glow Recipe")
        self.assertEqual(data["sample_posts"], [p["post_url"] for p in POSTS])
        sqls = [s for s, _ in cursor.executed]
        self.assertTrue(any(s.startswith("UPDATE creators SET shipping_address") for s in sqls))
        self.assertTrue(any("INSERT INTO brand_pr_events" in s for s in sqls))
        self.assertFalse(any("brand_pr_applications" in s for s in sqls))
        self.assertTrue(conn.committed)

    def test_needs_three_posts(self):
        resp, _, _ = self._post({"selected_posts": POSTS[:2], "shipping": SHIP})
        self.assertEqual(resp.status_code, 400)

    def test_needs_full_address(self):
        resp, _, _ = self._post({"selected_posts": POSTS, "shipping": {**SHIP, "city": ""}})
        self.assertEqual(resp.status_code, 400)


class WorkSampleTests(unittest.TestCase):
    URLS = [p["post_url"] for p in POSTS]

    def test_clean_sample_posts(self):
        raw = ["javascript:alert(1)", POSTS[0], POSTS[0]["post_url"], POSTS[1], {"url": POSTS[2]["post_url"]}, POSTS[0]]
        self.assertEqual(clean_sample_posts(raw), self.URLS)
        self.assertEqual(clean_sample_posts(None), [])

    def test_samples_go_above_the_gifted_ask(self):
        pitch = {
            "body": "Hi team,\n\nI love your toner.\n\nNo fee. Just product + shipping to Austin.\n\nMaya",
            "subject": "Collab", "email": "pr@glow.com",
        }
        out = apply_work_samples_to_pitch(pitch, self.URLS)
        paras = out["body"].split("\n\n")
        self.assertTrue(paras[2].startswith("A few recent posts:"))
        self.assertTrue(paras[3].startswith("No fee"))
        self.assertIn("instagram.com/p/a1", out["mailto"])

    def test_samples_go_above_sign_off_without_ask(self):
        out = apply_work_samples_to_pitch({"body": "Hi team,\n\nLove it.\n\nMaya"}, self.URLS[:1])
        self.assertEqual(out["body"].split("\n\n")[-2], "A few recent posts:\n" + self.URLS[0])

    def test_skips_links_already_in_pitch(self):
        pitch = {"body": f"Hi,\n\nSee {self.URLS[0]}\n\nMaya"}
        self.assertIs(apply_work_samples_to_pitch(pitch, self.URLS[:1]), pitch)


if __name__ == "__main__":
    unittest.main()
