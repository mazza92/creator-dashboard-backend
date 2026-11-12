import unittest
from unittest import mock

from services import brand_autopublish as ap

GOOD = {"id": 1, "brand_name": "Glow", "website": "https://glow.co", "contact_email": "hi@glow.co",
        "email_status": "unverified", "logo_url": None, "description": "",
        "category_checked_at": None}


class TestPublishReady(unittest.TestCase):
    def test_all_checks_needed(self):
        brand = {"logo_url": "https://img.logo.dev/glow.co", "description": "Clean skincare made for sensitive, reactive skin."}
        self.assertEqual(ap.publish_ready({"pass": True}, brand), [])
        self.assertIn("logo: missing", ap.publish_ready({"pass": True}, {**brand, "logo_url": None}))
        self.assertIn("logo: missing", ap.publish_ready(
            {"pass": True}, {**brand, "logo_url": "https://www.google.com/s2/favicons?domain=glow.co"}))
        self.assertIn("description: missing or too short", ap.publish_ready({"pass": True}, {**brand, "description": "Skincare"}))
        self.assertEqual(ap.publish_ready({"pass": False, "reasons": ["inbox: no contact email"]}, brand),
                         ["inbox: no contact email"])
        self.assertEqual(ap.publish_ready({"pass": True, "override": "rejected"}, brand), ["admin rejected"])


class TestRun(unittest.TestCase):
    def _run(self, gate, profile_updates=None, logo="https://img.logo.dev/glow.co", brand=None, found=None):
        conn = mock.MagicMock()
        brand = dict(brand or GOOD)
        published, tried = [], []
        updates = profile_updates if profile_updates is not None else {
            "description": "Clean skincare made for sensitive skin, loved by creators.", "category": "skincare"}
        with mock.patch("services.db_ddl.ensure_columns"), \
                mock.patch.object(ap, "_drafts", return_value=[brand]), \
                mock.patch("services.contact_repair.hunter_verify", return_value="valid") as verify, \
                mock.patch("services.contact_repair._save_status"), \
                mock.patch("services.contact_repair.find_replacement", return_value=found) as finder, \
                mock.patch("services.contact_repair._swap") as swap, \
                mock.patch("services.brand_image_scraper.scrape_brand_images", return_value={"logo_url": logo}), \
                mock.patch("services.brand_profile_ai.ensure_profile_columns"), \
                mock.patch("services.brand_profile_ai.enrich", return_value={"profile": {"x": 1}, "updates": updates}), \
                mock.patch("services.brand_profile_ai.write_updates"), \
                mock.patch("services.brand_quality_gate.evaluate", return_value=gate), \
                mock.patch.object(ap, "_publish", side_effect=lambda c, b: published.append(b)), \
                mock.patch.object(ap, "_tried", side_effect=lambda c, b: tried.append(b)):
            out = ap.run(conn)
        self.finder, self.swap = finder, swap
        return out, published, tried, verify

    def test_publishes_when_everything_passes(self):
        out, published, tried, verify = self._run({"pass": True})
        self.assertEqual(published, [1])
        self.assertEqual(tried, [])
        verify.assert_called_once()
        self.assertEqual((out["verified"], out["logos"], out["profiled"], out["published_count"]), (1, 1, 1, 1))

    def test_gate_fail_stays_draft(self):
        out, published, tried, _ = self._run({"pass": False, "reasons": ["inbox: email status invalid"]})
        self.assertEqual(published, [])
        self.assertEqual(tried, [1])
        self.assertEqual(out["waiting"], {"inbox": 1})

    def test_no_real_logo_stays_draft(self):
        out, published, _tried, _ = self._run({"pass": True}, logo="https://www.google.com/s2/favicons?domain=x")
        self.assertEqual(published, [])
        self.assertEqual(out["waiting"], {"logo": 1})

    def test_finds_an_inbox_when_the_scanner_had_none(self):
        out, published, _tried, verify = self._run(
            {"pass": True}, brand={**GOOD, "contact_email": None, "email_status": None},
            found={"email": "pr@glow.co", "status": "valid"})
        verify.assert_not_called()
        self.finder.assert_called_once()
        self.assertEqual(self.swap.call_args.args[3], "pr@glow.co")
        self.assertEqual((out["verified"], published), (1, [1]))


if __name__ == "__main__":
    unittest.main()
