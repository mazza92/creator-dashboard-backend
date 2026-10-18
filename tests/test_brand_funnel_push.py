from datetime import datetime, timedelta, timezone

from services import new_brand_push as nbp
from services import roster_delivery as rd
from tests.test_roster_delivery import FakeConn, FakeCursor

ROSTER = {
    "campaign_id": 5, "brand_id": 9, "slug": "glowco", "brand_name": "GlowCo", "category": "Skincare",
    "brand_niches": ["skincare"], "description": "", "hero_product": "Vitamin C serum", "regions": ["US"],
    "pushed": 0,
}


def test_matching_creators_needs_niche_and_shipping_country():
    candidates = [
        {"creator_id": 1, "niche": "skincare", "creator_niches": [], "country": "US"},
        {"creator_id": 2, "niche": "skincare", "creator_niches": [], "country": "FR"},
        {"creator_id": 3, "niche": "gaming", "creator_niches": [], "country": "US"},
        {"creator_id": 4, "niche": "beauty", "creator_niches": ["skincare"], "country": None},
    ]
    picked = [c["creator_id"] for c in nbp.matching_creators(ROSTER, candidates, limit=10)]
    assert 1 in picked and 4 in picked
    assert 2 not in picked and 3 not in picked
    assert len(nbp.matching_creators(ROSTER, candidates, limit=1)) == 1


def test_matching_ranks_main_niche_first_and_reads_supplements():
    roster = dict(ROSTER, category="supplements", brand_niches=None, regions=["United Kingdom"])
    candidates = [
        {"creator_id": 1, "niche": "lifestyle", "creator_niches": ["fitness", "beauty"], "country": None},
        {"creator_id": 2, "niche": "", "creator_niches": ["supplements"], "country": "GB", "has_applied": True},
        {"creator_id": 3, "niche": "", "creator_niches": ["beauty", "wellness"], "country": "US"},
        {"creator_id": 4, "niche": "", "creator_niches": ["fashion"], "country": "GB"},
    ]
    picked = [c["creator_id"] for c in nbp.matching_creators(roster, candidates, limit=10)]
    assert picked == [2, 1]


def test_normalize_regions():
    assert nbp.normalize_regions(["United States", "Europe"]) == ["US", "EU"]
    assert nbp.normalize_regions(["UK", "Worldwide"]) == []
    assert nbp.normalize_regions("CA") == ["CA"]


def test_push_email_names_brand_product_and_apply_link():
    mail = nbp.push_email("Ana", ROSTER)
    assert mail["subject"] == "GlowCo is gifting Vitamin C serum to creators like you"
    assert "brand=glowco" in mail["html"]
    assert "utm_campaign=new_brand_push" in mail["html"]
    assert "\u2014" not in mail["subject"]
    assert "**GlowCo**" in nbp.push_note(ROSTER)


def test_push_waits_for_send_hours(monkeypatch):
    monkeypatch.setattr(nbp, "load_fresh_rosters", lambda conn: [ROSTER])
    late = datetime(2026, 10, 18, 3, tzinfo=timezone.utc)
    assert nbp.email_new_brand_pushes(object(), now=late) == []


def test_push_sends_within_budget_and_logs(monkeypatch):
    import services.polly_alerts as pa

    monkeypatch.setattr(nbp, "load_fresh_rosters", lambda conn: [ROSTER])
    monkeypatch.setattr(nbp, "load_candidates", lambda conn, bid: [
        {"creator_id": i, "niche": "skincare", "creator_niches": [], "country": "US"} for i in range(1, 6)
    ])
    monkeypatch.setattr(pa, "ensure_alert_tables", lambda conn: None)
    monkeypatch.setattr(pa, "_recipient", lambda conn, cid: {"user_id": cid, "email": f"c{cid}@x.co", "first": "A"})
    monkeypatch.setattr(pa, "_unsubscribe_url", lambda uid: "")
    logged = []
    monkeypatch.setattr(pa, "_log_email", lambda *a: logged.append(a))
    import services.polly_autopilot as auto
    monkeypatch.setattr(auto, "post_thread_note", lambda conn, cid, text, chips=None: None)

    noon = datetime(2026, 10, 18, 15, tzinfo=timezone.utc)
    out = nbp.email_new_brand_pushes(FakeConn(), limit=3, send_fn=lambda *a: {"success": True}, now=noon)
    assert [o["creator_id"] for o in out] == [1, 2, 3]
    assert all(o["ok"] for o in out)
    assert logged[0][2] == nbp.PUSH_KIND and logged[0][3] == 9


def test_lock_reminder_cadence_and_copy():
    sched = rd.LOCK_REMINDERS
    assert rd.pick_ship_reminder({"age_days": 0.5}, sched) is None
    assert rd.pick_ship_reminder({"age_days": 1.2}, sched) == 1
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2, hours=1)
    assert rd.pick_ship_reminder({"age_days": 3.1, "r1_at": two_days_ago}, sched) == 2
    row = {"brand_name": "GlowCo", "pick_count": 3, "token": "tok", "contact_first_name": "Sam"}
    first = rd.build_lock_reminder_email(row, 1)
    assert first["subject"] == "Your GlowCo picks are saved. Lock them to get addresses"
    assert first["trigger_number"] == 11 and "lock_reminder_1" in first["url"]
    assert rd.build_lock_reminder_email(row, 2)["subject"] == "3 creators are waiting to hear from GlowCo"


def test_send_lock_reminders_emails_once_per_step(monkeypatch):
    monkeypatch.setattr(rd, "ensure_brand_emails_table", lambda cursor, conn=None: None)
    monkeypatch.setattr(rd, "_brand_first_name_sql", lambda cursor: "NULL::text AS contact_first_name")
    picked = datetime.now(timezone.utc) - timedelta(days=1, hours=2)
    rows = [
        {"campaign_id": 1, "brand_id": 9, "token": "t1", "brand_name": "GlowCo", "contact_email": "b@x.co",
         "pick_count": 2, "last_pick_at": picked, "r1_at": None, "r2_at": None},
        {"campaign_id": 2, "brand_id": 8, "token": "t2", "brand_name": "Other", "contact_email": "o@x.co",
         "pick_count": 1, "last_pick_at": picked, "r1_at": picked, "r2_at": None},
    ]
    cur = FakeCursor([rows, None])
    sent = []
    stats = rd.send_lock_reminders(cur, FakeConn(), send_fn=lambda to, s, h, kind: sent.append(kind) or {"success": True})
    assert sent == ["lock_reminder_1"]
    assert stats["sent"] == 1


def test_proof_keeps_only_public_social_posts():
    from brand_pr_roster_routes import _delivered_proof

    shipped = datetime(2026, 10, 1, tzinfo=timezone.utc)
    base = {"shipped_at": shipped, "delivered_at": shipped + timedelta(days=6), "brand_name": "Other",
            "username": "ana", "image_profile": "", "followers_count": 4200, "social_follower_count": None,
            "social_handle": "ana", "social_platform": "tiktok", "social_links": [], "first_name": "Ana"}
    rows = [
        dict(base, delivered_content={"organic_url": "https://www.tiktok.com/@ana/video/1", "ugc_url": "https://drive.google.com/x"}),
        dict(base, delivered_content={"organic_url": "https://drive.google.com/file/d/abc"}),
        dict(base, delivered_content={"ugc_url": "https://drive.google.com/y"}),
    ]

    class Cur(FakeCursor):
        pass

    import brand_pr_roster_routes as routes
    orig = routes.public_column_exists
    routes.public_column_exists = lambda *a: True
    try:
        proof = _delivered_proof(Cur([rows]), brand_id=9)
    finally:
        routes.public_column_exists = orig
    assert len(proof) == 1
    assert proof[0]["platform"] == "TikTok"
    assert proof[0]["days_to_post"] == 6
    assert "drive.google" not in str(proof)
