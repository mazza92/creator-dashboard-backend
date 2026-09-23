# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from services.tiktok_ugc_lead_writer import IncrementalInserter
from services.tiktok_ugc_profile_scraper import discover_and_enrich


class TestIncrementalInserter(unittest.TestCase):
    def test_dry_run_counts_qualified_once(self):
        ins = IncrementalInserter(dry_run=True)
        rec = {
            "handle": "hannahs.ugccorner",
            "qualified": True,
            "contact_email": "hannah@gmail.com",
        }
        first = ins.add([rec])
        second = ins.add([rec, rec])
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(ins.totals["inserted"], 1)
        self.assertEqual(ins.totals["considered"], 1)

    def test_skips_unqualified_when_filtered(self):
        ins = IncrementalInserter(dry_run=True, only_qualified=True)
        ins.add([{"handle": "nope", "qualified": False, "contact_email": "x@gmail.com"}])
        self.assertEqual(ins.totals["inserted"], 0)
        self.assertEqual(ins.totals["considered"], 0)


class TestDiscoverStopCallback(unittest.TestCase):
    @patch("services.tiktok_ugc_profile_scraper.discover_handles", return_value=["a", "b", "c", "d"])
    @patch("services.tiktok_ugc_profile_scraper._enrich_batch")
    def test_should_stop_ends_after_current_batch(self, enrich, _seeds):
        enrich.side_effect = lambda handles, **kw: [
            {"handle": h, "qualified": False} for h in handles
        ]
        batches = []
        with patch.dict("os.environ", {"UGC_PLAYWRIGHT_MAX": "0"}):
            out = discover_and_enrich(
                ["q"],
                quota=20,
                max_handles=20,
                workers=1,
                serp_pages=1,
                ignore_seen=True,
                expand_graph=False,
                on_batch=batches.append,
                should_stop=lambda: True,
            )
        self.assertTrue(out)
        self.assertEqual(len(batches), 1)
        self.assertEqual(enrich.call_count, 1)


class TestTikTokProxyTls(unittest.TestCase):
    def test_session_disables_verify_when_proxied(self):
        import services.inhouse_social_scraper as m

        with patch.object(m, "_TT_PROXY", "http://user:pass@gw.example:823"), patch.object(
            m, "_TT_PROXY_RAW", "http://user:pass@gw.example:823"
        ), patch.object(m, "_PROXY_TLS_LOGGED", False):
            session = m._session(for_tiktok=True)
        self.assertFalse(session.verify)
        self.assertEqual(session.proxies.get("https"), "http://user:pass@gw.example:823")

    def test_session_keeps_verify_without_proxy(self):
        import services.inhouse_social_scraper as m

        with patch.object(m, "_TT_PROXY", None), patch.object(m, "_TT_PROXY_RAW", ""):
            session = m._session(for_tiktok=True)
        self.assertTrue(session.verify)

    def test_playwright_ignores_https_errors_with_proxy(self):
        import services.inhouse_social_scraper as m

        with patch.object(m, "_TT_PROXY", "http://gw.example:823"):
            kwargs = m._tiktok_playwright_context_kwargs()
        self.assertTrue(kwargs.get("ignore_https_errors"))


if __name__ == "__main__":
    unittest.main()
