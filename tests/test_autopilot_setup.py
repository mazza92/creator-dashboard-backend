"""Pro setup journey API: saves each step, never lets creators pick send days, writes the real first pitch."""
import unittest
from unittest import mock

from flask import Flask

import routes.polly_autopilot as rpa


class SetupApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "t"
        self.app.register_blueprint(rpa.autopilot_bp, url_prefix="/api/polly/autopilot")
        self.client = self.app.test_client()
        self.conn = mock.MagicMock(closed=0)
        self.notes = {"prefs": {"tone": "friendly"}}
        self.saved_notes, self.facts, self.settings_saved = [], [], []
        self.enabled = []
        patches = [
            mock.patch.object(rpa, "_guard", side_effect=lambda require_gmail=True: (
                7, self.conn, {"id": 7, "user_id": 70, "creator_niches": ["skincare"]}, None)),
            mock.patch.object(rpa, "_creator_context", side_effect=lambda c, cr: (dict(self.notes), {})),
            mock.patch.object(rpa, "_setup_payload", return_value={"success": True}),
            mock.patch("services.polly_memory.save_notes",
                       side_effect=lambda c, cid, notes: self.saved_notes.append(notes)),
            mock.patch("services.pitch_facts.save",
                       side_effect=lambda c, cid, patch, confirm=False: self.facts.append((patch, confirm))),
            mock.patch.object(rpa, "save_settings", side_effect=lambda c, cid, p: self.settings_saved.append(p)),
            mock.patch.object(rpa, "set_enabled", side_effect=lambda c, cid, on: self.enabled.append(on)),
            mock.patch.object(rpa, "prioritize_fill"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _save(self, body):
        res = self.client.post("/api/polly/autopilot/setup", json=body)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return self.saved_notes[-1]

    def test_voice_step_saves_tone_and_keeps_chat_in_sync(self):
        notes = self._save({"tone": "professional", "voice": "preset", "step": 1})
        self.assertEqual(notes["pitch_profile"]["tone"], "professional")
        self.assertEqual(notes["prefs"]["tone"], "professional")
        notes = self._save({"tone": "playful"})
        self.assertEqual(notes["pitch_profile"]["tone"], "playful")
        self.assertEqual(notes["prefs"]["tone"], "friendly")

    def test_unknown_tone_ignored(self):
        notes = self._save({"tone": "aggressive"})
        self.assertNotIn("tone", notes["pitch_profile"])

    def test_style_rules_and_proof_posts_cleaned(self):
        notes = self._save({"style_rules": ["  Short   sentences ", "", "x" * 300] + ["r"] * 6,
                            "style_summary": "Short and warm",
                            "proof_posts": ["https://tiktok.com/1", "javascript:alert(1)", "https://tiktok.com/2",
                                            "https://tiktok.com/3"],
                            "win": "  Got 40 orders for Glowly  "})
        prof = notes["pitch_profile"]
        self.assertEqual(prof["style_rules"][0], "Short sentences")
        self.assertEqual(len(prof["style_rules"]), 5)
        self.assertLessEqual(len(prof["style_rules"][1]), 120)
        self.assertEqual(prof["proof_posts"], ["https://tiktok.com/1", "https://tiktok.com/2"])
        self.assertEqual(prof["win"], "Got 40 orders for Glowly")

    def test_offer_and_niches_go_through_pitch_facts(self):
        self._save({"offer_type": "gifted_ugc_photos", "niches": ["skincare", "beauty", "wellness", "food"],
                    "city": "Austin", "country": "United States"})
        patch, confirm = self.facts[-1]
        self.assertEqual(patch["offer_type"], "gifted_ugc_photos")
        self.assertEqual(patch["niches"], ["skincare", "beauty", "wellness"])
        self.assertEqual(patch["city"], "Austin")
        self.assertTrue(confirm)

    def test_bad_offer_dropped(self):
        self._save({"offer_type": "free_money"})
        self.assertEqual(self.facts, [])

    def test_send_days_always_every_weekday(self):
        self._save({"send_days": [5], "deal": "paid", "monthly_target": 90})
        self.assertEqual(self.settings_saved[-1], {"send_days": [0, 1, 2, 3, 4], "deal_focus": "paid",
                                                   "monthly_target": 90})

    def test_step_only_moves_forward(self):
        self.notes = {"pitch_profile": {"step": 4}}
        notes = self._save({"step": 2})
        self.assertEqual(notes["pitch_profile"]["step"], 4)

    def test_done_switches_autopilot_on(self):
        notes = self._save({"done": True})
        self.assertTrue(notes["pitch_profile"]["setup_done_at"])
        self.assertEqual(self.enabled, [True])
        rpa.prioritize_fill.assert_called_once()

    def test_not_done_leaves_autopilot_alone(self):
        self._save({"tone": "warm"})
        self.assertEqual(self.enabled, [])


class SetupPreviewTests(SetupApiTests):
    BODY = ("Hi Saltwell team,\n\nI have an idea for Sea Mineral Cleanser: a 15-second first-wash video.\n\n"
            "No fee. Just product + shipping to Austin.\n\nWorth a try?\nMaya\nhttps://newcollab.co/kit/maya")

    def _draft(self, conn, cid, creator, brand_id, notes, scrape, announced=False, angle=0):
        self.drafts.append((brand_id, announced, angle))
        return {"id": 55, "brand_id": brand_id, "brand_name": "Saltwell", "subject": "Video idea",
                "body": self.BODY}, None

    def setUp(self):
        super().setUp()
        self.drafts = []
        self.conn.cursor.return_value.fetchone.return_value = (
            {"writer": "v2", "source": "gemini", "ideas": ["a texture close-up", "a morning routine"]},)
        for p in (mock.patch.object(rpa, "_pick_brands", return_value=([{"id": 3}], None)),
                  mock.patch.object(rpa, "_write_draft", side_effect=self._draft),
                  mock.patch("services.polly_autopilot._creator_names", return_value={"first": "Maya"})):
            p.start()
            self.addCleanup(p.stop)

    def test_preview_writes_real_first_pitch_with_checks(self):
        res = self.client.post("/api/polly/autopilot/setup/preview", json={})
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(self.drafts, [(3, True, 0)])
        self.assertTrue(all(c["ok"] for c in data["checks"]))
        self.assertEqual(len(data["followups"]), 2)
        for f in data["followups"]:
            self.assertTrue(f["subject"].startswith("Re:"))

    def test_another_angle_rewrites_same_brand(self):
        self.client.post("/api/polly/autopilot/setup/preview", json={"brand_id": 3, "angle": 2})
        self.assertEqual(self.drafts, [(3, True, 2)])

    def test_no_matches(self):
        with mock.patch.object(rpa, "_pick_brands", return_value=([], "No matches yet")):
            res = self.client.post("/api/polly/autopilot/setup/preview", json={})
        self.assertEqual(res.status_code, 409)


if __name__ == "__main__":
    unittest.main()


class SetupPayloadTests(unittest.TestCase):
    def test_payload_shape(self):
        facts = {"offer_type": "gifted_ugc", "city": "Austin", "country": "United States", "followers": 9000,
                 "engagement_rate": 0.8, "niches": ["skincare"], "niche_options": ["skincare", "beauty"]}
        posts = [{"post_url": f"https://tiktok.com/{i}", "thumbnail_url": "t", "views": i * 100} for i in range(10)]
        notes = {"pitch_profile": {"tone": "warm", "step": 3}, "prefs": {"lead_metric": "views"}}
        with mock.patch("services.pitch_facts.load", return_value=facts), \
                mock.patch("services.pitch_writer.creator_stats",
                           return_value={"avg_views": "14.8K", "top_posts": ["https://tiktok.com/9"]}), \
                mock.patch("pr_crm_routes._live_pitch_posts", return_value=posts), \
                mock.patch.object(rpa, "_is_pro", return_value=True), \
                mock.patch.object(rpa, "settings", return_value={"deal_focus": "auto", "monthly_target": 60}), \
                mock.patch.object(rpa, "gmail_configured", return_value=True), \
                mock.patch.object(rpa, "load_account", return_value={"email": "m@x.co"}):
            data = rpa._setup_payload(mock.MagicMock(), 7, {"user_id": 70}, notes)
        self.assertFalse(data["setup_done"])
        self.assertEqual(data["step"], 3)
        self.assertEqual(data["profile"]["tone"], "warm")
        self.assertEqual(data["profile"]["proof_posts"], ["https://tiktok.com/9"])
        self.assertFalse(data["stats"]["engagement_strong"])
        self.assertEqual(len(data["posts"]), 8)
        self.assertTrue(data["gmail_connected"])
        self.assertNotIn("paid_only", [o["id"] for o in data["offer"]["options"]])
        self.assertEqual(len(data["tones"]), 6)


class SetupStateTests(unittest.TestCase):
    def test_state_summarises_style(self):
        thread = {"notes": {"pitch_profile": {"tone": "warm", "step": 2}}}
        with mock.patch("services.polly_memory.load_thread", return_value=thread):
            st = rpa._setup_state(mock.MagicMock(), 7)
        self.assertEqual(st["step"], 2)
        self.assertFalse(st["done"])
        self.assertIn("Warm", st["style"])

    def test_state_sample_voice(self):
        thread = {"notes": {"pitch_profile": {"voice": "sample", "style_rules": ["Short sentences"],
                                              "style_summary": "Short and warm", "setup_done_at": "x"}}}
        with mock.patch("services.polly_memory.load_thread", return_value=thread):
            st = rpa._setup_state(mock.MagicMock(), 7)
        self.assertTrue(st["done"])
        self.assertEqual(st["style"], "Short and warm")


class GoLiveSchedulesTests(unittest.TestCase):
    def test_waiting_drafts_scheduled(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.fetchall.return_value = [(5,), (6,)]
        with mock.patch.object(rpa, "approve", return_value=[{"id": 5}, {"id": 6}]) as approve:
            out = rpa._schedule_waiting_drafts(conn, 7)
        approve.assert_called_once_with(conn, 7, [5, 6])
        self.assertEqual(len(out), 2)
