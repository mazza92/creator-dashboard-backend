import time

import fakeredis

from services import cover_refresh as cr

DEAD = f"https://p16-sign.tiktokcdn-us.com/a.jpg?x-expires={int(time.time()) - 60}"
LIVE = f"https://p16-sign.tiktokcdn-us.com/a.jpg?x-expires={int(time.time()) + 86400}"
STORED = "https://x.supabase.co/storage/v1/object/public/creators/radar/1.jpg"


def _v(i, cover):
    return {"tiktok_id": str(i), "url": f"https://www.tiktok.com/@a/video/{i}?utm=x", "cover_url": cover}


def test_queue_only_dead_covers_once(monkeypatch):
    r = fakeredis.FakeRedis()
    started = []
    monkeypatch.setattr(cr.source, "start_posts_run", lambda urls: started.append(urls) or "run1")
    n = cr.queue([_v(1, DEAD), _v(2, LIVE), _v(3, STORED), _v(4, None)], r)
    assert n == 2 and started == [["https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@a/video/4"]]
    assert cr.queue([_v(1, DEAD)], r) == 0  # seen recently
    assert r.hget(cr.PENDING, "run1")


def test_queue_needs_redis_and_respects_daily_cap(monkeypatch):
    assert cr.queue([_v(1, DEAD)], None) == 0
    r = fakeredis.FakeRedis()
    monkeypatch.setattr(cr.source, "start_posts_run", lambda urls: "run")
    r.set(f"cover_refresh:runs:{time.strftime('%Y%m%d')}", cr.RUNS_PER_DAY)
    assert cr.queue([_v(9, DEAD)], r) == 0


def test_queue_survives_apify_errors(monkeypatch):
    def boom(urls):
        raise RuntimeError("apify down")
    monkeypatch.setattr(cr.source, "start_posts_run", boom)
    assert cr.queue([_v(1, DEAD)], fakeredis.FakeRedis()) == 0
