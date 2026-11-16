"""Self-hosted TikTok showcase media: queue, cap, collect."""

import os
import unittest
from unittest import mock

import psycopg2
from psycopg2.extras import RealDictCursor

from services import radar_source
from services import tiktok_media as tm

SCHEMA = "tiktok_media_t"


def _connect(**kw):
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None, **kw)


class Pure(unittest.TestCase):
    def test_clean_ids(self):
        pairs = tm.clean_ids(["7697236927891705110", "bad", "7697236927891705110", "7683925529241701654"],
                             ["ugcbyanna.uk", "x", "dup", "@simpleexplorerugc"])
        self.assertEqual(pairs, [("7697236927891705110", "ugcbyanna.uk"), ("7683925529241701654", "simpleexplorerugc")])

    def test_media_from_item_prefers_downloaded_file(self):
        item = {"mediaUrls": ["https://api.apify.com/v2/key-value-stores/s/records/v.mp4"],
                "videoMeta": {"downloadAddr": "https://v16.tiktokcdn.com/x", "coverUrl": "https://p16.tiktokcdn.com/c"}}
        self.assertEqual(tm.media_from_item(item), ("https://api.apify.com/v2/key-value-stores/s/records/v.mp4",
                                                    "https://p16.tiktokcdn.com/c"))


class Flow(unittest.TestCase):
    def setUp(self):
        try:
            boot = _connect()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"no database: {e}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
        boot.close()
        tm._READY = False
        self.conn = _connect(options=f"-c search_path={SCHEMA}")
        self.conn.cursor_factory = RealDictCursor

    def tearDown(self):
        self.conn.close()
        boot = _connect()
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def test_lookup_queues_unknown_ids_within_daily_cap(self):
        with mock.patch.object(tm, "DAILY_NEW_CAP", 1):
            media = tm.lookup(self.conn, [("1111111111", "a"), ("2222222222", "b")])
        self.assertEqual(media, {"1111111111": {"status": "queued", "video": None, "cover": None}})

    def test_collect_starts_a_run_then_stores_media(self):
        tm.lookup(self.conn, [("1111111111", "anna"), ("2222222222", "bo")])
        with mock.patch.object(radar_source, "start_media_run", return_value="run1") as start:
            out = tm.collect(self.conn)
        self.assertEqual(out["started"], 2)
        self.assertIn("https://www.tiktok.com/@anna/video/1111111111", start.call_args[0][0])

        items = [{"id": "1111111111", "mediaUrls": ["https://api.apify.com/v"], "videoMeta": {"coverUrl": "c"}}]
        with mock.patch.object(radar_source, "run_state", return_value={"finished": True, "dataset_id": "d"}), \
             mock.patch.object(radar_source, "fetch_items", return_value=items), \
             mock.patch.object(radar_source, "start_media_run", return_value="run2"), \
             mock.patch.object(tm, "_store_one", return_value=("https://s/tiktok/1111111111.mp4", "https://s/c.jpg")):
            out = tm.collect(self.conn)
        self.assertEqual(out["stored"], 1)
        media = tm.lookup(self.conn, [("1111111111", None), ("2222222222", None)], queue_missing=False)
        self.assertEqual(media["1111111111"], {"status": "stored", "video": "https://s/tiktok/1111111111.mp4",
                                               "cover": "https://s/c.jpg"})
        # Not in the run results: back in the queue, then picked up by the next run.
        self.assertIn(media["2222222222"]["status"], ("queued", "running"))


if __name__ == "__main__":
    unittest.main()
