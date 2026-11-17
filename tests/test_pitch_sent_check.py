"""'Did it send?' after a mail-app or copy handoff on a pitch card."""
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask


class SentCheckTests(unittest.TestCase):
    def setUp(self):
        from routes import polly as polly_routes
        self.r = polly_routes
        app = Flask(__name__)
        app.register_blueprint(polly_routes.polly_bp)
        self.client = app.test_client()
        self.conn = MagicMock()

    def post(self, body):
        with patch.object(self.r, "_creator_auth", return_value=(7, self.conn, {})), \
                patch("services.polly_usage.log_usage") as log:
            resp = self.client.post("/api/polly/pitch/sent-check", json=body)
        return resp, log

    def test_yes_logs_the_send_once(self):
        with patch.object(self.r, "_log_send_on_open", return_value=True) as logged:
            resp, log = self.post({"brand_id": 5, "brand_name": "Glow", "sent": True, "method": "copy_pitch"})
        self.assertEqual(resp.get_json()["outcome"], "logged")
        logged.assert_called_once_with(self.conn, 7, 5, "Glow")
        self.assertEqual(log.call_args.kwargs["intent"], "sent")

    def test_yes_after_open_email_is_a_confirmation(self):
        with patch.object(self.r, "_log_send_on_open", return_value=False):
            resp, _ = self.post({"brand_id": 5, "sent": True})
        self.assertEqual(resp.get_json()["outcome"], "confirmed")

    def test_not_yet_unlogs_and_keeps_it_a_draft(self):
        stored = {"messages": [{"role": "assistant"}], "suggested_brands": [], "notes": {"pitched": [{"id": 5}]}}
        with patch("services.polly_tracker.apply_lifecycle_intent") as apply, \
                patch.object(self.r, "load_thread", return_value=stored), \
                patch.object(self.r, "unmark_pitched", return_value={"pitched": []}) as unmark, \
                patch.object(self.r, "save_thread") as save:
            resp, log = self.post({"brand_id": 5, "brand_name": "Glow", "sent": False})
        self.assertEqual(resp.get_json()["outcome"], "not_sent")
        self.assertEqual(apply.call_args[0][2]["intent"], "pitch_not_sent")
        unmark.assert_called_once()
        self.assertEqual(save.call_args.kwargs["notes"], {"pitched": []})
        self.assertEqual(log.call_args.kwargs["intent"], "not_sent")

    def test_bad_input(self):
        resp, _ = self.post({"brand_id": 5, "sent": "yes"})
        self.assertEqual(resp.status_code, 400)
        resp, _ = self.post({"sent": True})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
