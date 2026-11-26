"""Brands directory: Polly writes the pitch inside the flow (POST /api/polly/pitch/draft), no trip to chat."""
import unittest
from unittest.mock import MagicMock, patch

from app import app
from routes import polly as routes_polly


class PitchDraftRoute(unittest.TestCase):
    def _post(self, body, tool):
        conn = MagicMock()
        saved = {}

        def save(_conn, _cid, _messages, _suggested, notes=None):
            saved["notes"] = notes

        with patch.object(routes_polly, "_creator_auth", return_value=(7, conn, {"first_name": "Maya", "user_id": 3})), \
             patch.object(routes_polly, "load_thread", return_value={"notes": {"x": 1}, "messages": [{"m": 1}]}), \
             patch.object(routes_polly, "save_thread", side_effect=save), \
             patch.object(routes_polly, "_load_scrape", return_value={}), \
             patch.object(routes_polly, "_unlock_balance", return_value={"remaining": 2}), \
             patch.object(routes_polly, "_log_polly_event"), \
             patch("services.polly_autopilot.gmail_connected_at", return_value=None), \
             patch("services.polly_tools.run_tool", side_effect=tool):
            resp = app.test_client().post("/api/polly/pitch/draft", json=body)
        return resp, saved

    def test_returns_the_pitch_and_keeps_it_in_polly_notes(self):
        def tool(state, name, args):
            self.assertEqual(name, "draft_pitch")
            self.assertEqual(args, {"brand_id": 42, "sample_posts": ["https://tiktok.com/@a/video/1"]})
            state.pitch = {"brand_id": 42, "subject": "Hi", "body": "Hello", "email": "pr@b.com", "mailto": "mailto:x"}
            state.notes["draft_pending"] = {"id": 42}
            return {"ok": True, "status": "drafted"}

        resp, saved = self._post({"brand_id": 42, "sample_posts": ["https://tiktok.com/@a/video/1"]}, tool)
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200, data)
        self.assertEqual(data["pitch"]["email"], "pr@b.com")
        self.assertFalse(data["paywall"])
        self.assertFalse(data["gmail_connected"])
        self.assertEqual(saved["notes"]["draft_pending"], {"id": 42})
        self.assertEqual(saved["notes"]["x"], 1)

    def test_paywall_comes_back_without_a_pitch(self):
        def tool(state, name, args):
            state.paywall = True
            state.final_say = "Rhode is on Pro"
            return {"ok": True, "status": "locked_out_of_free_pitches"}

        data = self._post({"brand_id": 42}, tool)[0].get_json()
        self.assertTrue(data["paywall"])
        self.assertIsNone(data["pitch"])
        self.assertEqual(data["message"], "Rhode is on Pro")

    def test_needs_brand(self):
        resp, _ = self._post({}, lambda *a: {})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
