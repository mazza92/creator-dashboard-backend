from datetime import datetime, timedelta, timezone

import services.ugc_roundup as ur


def _opp(i, title, desc="Film 2 UGC TikToks for our brand.", hours_ago=5, niches=None, pay=150):
    published = datetime.utcnow() - timedelta(hours=hours_ago)
    return {
        "id": i, "brand_name": f"Brand {i}", "brand_category": None, "brand_logo_url": None,
        "brand_website": None, "product_name": title, "campaign_description": desc,
        "pr_value_usd": pay, "creator_count_range": "5-10", "shipping_regions": ["US"],
        "follower_ranges": [], "content_types": ["UGC"], "creator_niches": niches or [],
        "additional_notes": f"[scanner:upwork:{i}] | source=upwork | apply_url=https://www.upwork.com/jobs/{i}",
        "spots_total": 10, "spots_filled": 0, "closes_at": datetime.utcnow() + timedelta(days=20),
        "created_at": published, "published_at": published, "listing_brief": None,
    }


def test_pick_gigs_drops_non_ugc_and_applied_and_puts_matches_first():
    rows = [
        _opp(1, "TikTok UGC Creator", hours_ago=2),
        _opp(2, "Beauty UGC Video", niches=["Beauty"], hours_ago=10),
        _opp(3, "PAID ACTORS WANTED", desc="Film a short promo video"),
        _opp(4, "Instagram Reels Creator", hours_ago=1),
    ]
    gigs = ur.pick_gigs(rows, {"beauty"}, applied={4})
    ids = [g["id"] for g in gigs]
    assert 3 not in ids and 4 not in ids
    assert ids[0] == 2 and gigs[0]["is_matched"]
    assert ids == [2, 1]


def test_apply_href_uses_listing_url_or_polly():
    assert ur.apply_href({"apply_mode": "url", "external_apply_url": "https://upwork.com/jobs/1"}) == "https://upwork.com/jobs/1"
    assert ur.apply_href({"apply_mode": "email", "apply_email": "x@y.co"}).startswith(ur.polly_href()[:30])
    assert "javascript" not in ur.apply_href({"apply_mode": "url", "external_apply_url": "javascript:alert(1)"})


def test_roundup_email_lists_each_gig_with_apply_link():
    gigs = [
        {"id": 1, "headline": "TikTok UGC <Creator>", "brand_name": "Glow", "pay_label": "$150",
         "source_label": "Upwork", "apply_mode": "url", "external_apply_url": "https://upwork.com/jobs/1", "is_matched": True},
        {"id": 2, "headline": "Reels Creator", "brand_name": "Reels Creator", "pay_label": "$80",
         "apply_mode": "kit"},
    ]
    mail = ur.roundup_email("Sam", gigs, "https://api.example/unsub")
    assert mail["subject"] == "2 new paid UGC gigs for you"
    html = mail["html"]
    assert "TikTok UGC &lt;Creator&gt;" in html
    assert "Glow &middot; $150 &middot; via Upwork" in html
    assert html.count("Apply &rarr;") == 2
    assert 'href="https://upwork.com/jobs/1"' in html
    assert "Reels Creator &middot;" not in html
    assert "1 in your niche" in html
    assert ur.roundup_subject(gigs[:1]) == "New paid UGC gig: TikTok UGC <Creator>"


def test_roundup_only_runs_in_send_window(monkeypatch):
    called = []
    monkeypatch.setattr(ur, "load_fresh_gigs", lambda conn, since: called.append(since) or [])
    off_hours = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)
    assert ur.email_ugc_roundups(object(), now=off_hours) == []
    assert called == []


def test_aware_handles_naive_and_aware():
    naive = datetime(2026, 1, 1, 12)
    assert ur._aware(naive).tzinfo == timezone.utc
    aware = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert ur._aware(aware) is aware
