from datetime import datetime, timedelta, timezone

from services import brand_scan as scan

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
TARGET = {"key": "glowrecipe", "name": "Glow Recipe", "handles": ["glowrecipe"], "logo_url": None,
          "category": "", "brand_id": None}


def _v(i, creator, caption, views=1000, followers=5000, **over):
    base = {"tiktok_id": str(i), "url": f"https://www.tiktok.com/@{creator}/video/{i}", "caption": caption,
            "hashtags": [], "mentions": [], "views": views, "likes": 10, "created_at": NOW - timedelta(days=5),
            "cover_url": None, "is_ad": False, "is_sponsored": False, "creator_handle": creator,
            "creator_followers": followers}
    base.update(over)
    return base


def _thumb(cover, url):
    return ""


def test_is_match_by_mention_hashtag_or_name():
    assert scan.is_match(_v(1, "a", "love it", mentions=["glowrecipe"]), TARGET)
    assert scan.is_match(_v(2, "b", "obsessed", hashtags=["glowrecipepartner"]), TARGET)
    assert scan.is_match(_v(3, "c", "my Glow Recipe haul"), TARGET)
    assert not scan.is_match(_v(4, "d", "glowrecipes are fun"), TARGET)


def test_is_match_skips_brand_and_retailers():
    assert not scan.is_match(_v(1, "glowrecipe", "new drop", mentions=["glowrecipe"]), TARGET)
    assert not scan.is_match(_v(2, "sephora", "Glow Recipe at sephora", mentions=["glowrecipe"]), TARGET)


def test_hook_key_strips_tags_and_emoji():
    cap = "When I say my skin is basically clear but I have scars 😭 #skincare @glowrecipe\nmore text"
    assert scan.hook_text(cap) == "when i say my skin is basically clear but i have scars"
    assert scan.hook_key(cap) == "when i say my skin is"
    assert scan.hook_key("#fyp #gifted") == ""


def test_group_hooks_needs_two_creators():
    vids = [
        _v(1, "a", "when i say my skin is basically clear", views=500),
        _v(2, "b", "When I say my skin is basically clear!", views=900),
        _v(3, "a", "when i say my skin is basically glowing", views=100),
        _v(4, "c", "solo hook nobody else used here", views=5000),
    ]
    groups = scan.group_hooks(vids)
    assert len(groups) == 1
    assert groups[0]["creators"] == 2 and len(groups[0]["videos"]) == 3
    assert groups[0]["videos"][0]["tiktok_id"] == "2"


def test_stats_and_mix():
    vids = [
        _v(1, "a", "thank you for the PR package #gifted", followers=4000),
        _v(2, "b", "#ad loving this", followers=12000),
        _v(3, "c", "just a review", followers=80000),
    ]
    st = scan.stats(vids, "glowrecipe")
    assert st["videos"] == 3 and st["creators"] == 3
    assert st["median_followers"] == 12000
    assert st["small_creator_share"] == 0.67
    assert st["mix"]["gifted"] == 0.33 and st["mix"]["paid"] == 0.33 and st["mix"]["organic"] == 0.33


def test_build_report_gates_only_the_creators_list():
    vids = [_v(i, f"c{i}", f"glow recipe review number {i}", views=i * 100) for i in range(1, 11)]
    matches = {"niche": "skincare", "total": 40, "creators": [{"handle": f"@m{i}"} for i in range(6)],
               "cta_url": "https://app.newcollab.co/for-brands?ref=brand_scan"}
    locked = scan.build_report(TARGET, vids, matches=matches, locked=True, now=NOW, thumb=_thumb)
    assert locked["status"] == "ready" and locked["locked"] is True
    assert len(locked["top_videos"]) == 10 and locked["top_videos"][0]["views"] == 1000
    assert len(locked["creators"]) == scan.FREE["creators"] == 6
    assert locked["unlock"] == {"creators": 4}
    assert len(locked["matches"]["creators"]) == 6
    full = scan.build_report(TARGET, vids, matches=matches, locked=False, now=NOW, thumb=_thumb)
    assert len(full["creators"]) == 10 and full["unlock"] == {"creators": 0}
    assert "**Glow Recipe** has **10 creators**" in full["summary"]


def test_empty_report_reads_as_opening():
    rep = scan.build_report(TARGET, [], matches=None, locked=True, now=NOW, thumb=_thumb)
    assert rep["status"] == "empty" and "opening" in rep["summary"]


def test_resolve_handles_domain_and_handle():
    t = scan.resolve(None, "glowrecipe.com", directory={})
    assert t["key"] == "glowrecipe"
    t = scan.resolve(None, "@GlowRecipe", directory={})
    assert t["key"] == "glowrecipe" and t["name"] == "GlowRecipe"
    t = scan.resolve(None, "summer fridays", directory={})
    assert t["key"] == "summerfridays" and t["name"] == "Summer Fridays"
    assert scan.resolve(None, "fyp", directory={}) is None
    assert scan.resolve(None, "", directory={}) is None


def test_resolve_uses_directory_row():
    row = {"id": 7, "name": "Glow Recipe", "tiktok_handle": "@glowrecipe", "logo_url": "https://x/logo.png",
           "category": "Skincare"}
    t = scan.resolve(None, "glow recipe", directory={"glowrecipe": row})
    assert t["brand_id"] == 7 and t["logo_url"] == "https://x/logo.png"
    assert scan.guess_niche(t, []) == "skincare"


def test_clean_email():
    assert scan.clean_email(" Founder@Brand.CO ") == "founder@brand.co"
    assert scan.clean_email("nope") == ""


def test_report_email_has_numbers_links_and_cta():
    vids = [_v(i, f"c{i % 4}", "when i say my skin is basically clear #gifted", views=1000 + i) for i in range(6)]
    matches = {"niche": "skincare", "total": 40, "creators": [],
               "cta_url": "https://app.newcollab.co/brands/launch?niche=skincare&utm_campaign=brand_scan"}
    rep = scan.build_report(TARGET, vids, matches=matches, locked=False, now=NOW, thumb=_thumb)
    msg = scan.report_email(rep)
    assert msg["subject"] == "Your Brand Scan: Glow Recipe's TikTok creators"
    html = msg["html"]
    assert "<b>Glow Recipe</b>" in html and "**" not in html
    assert "https://www.tiktok.com/@c1/video/1" in html
    assert "utm_campaign=brand_scan&amp;utm_medium=email" in html
    assert "40 vetted skincare creators" in html
    assert "brands/scan?b=Glow+Recipe&amp;ref=email" in html
    assert "<img" not in html


def test_report_email_escapes_captions():
    vids = [_v(1, "a", "<script>x</script> glow recipe haul"), _v(2, "b", "glow recipe haul again")]
    rep = scan.build_report(TARGET, vids, matches=None, locked=False, now=NOW, thumb=_thumb)
    assert "<script>" not in scan.report_email(rep)["html"]


def test_report_email_subject_possessive():
    rep = scan.build_report({**TARGET, "name": "Dewy Labs"}, [], matches=None, locked=False, now=NOW, thumb=_thumb)
    assert scan.report_email(rep)["subject"] == "Your Brand Scan: Dewy Labs' TikTok creators"


def test_respond_defaults_to_locked_and_caps(monkeypatch):
    monkeypatch.setattr(scan, "creator_matches", lambda niche, conn=None, redis_client=None: {
        "niche": niche, "total": 3, "creators": [], "cta_url": "https://x/for-brands"})
    monkeypatch.setattr(scan.radar, "_thumb", _thumb)
    vids = [_v(i, f"c{i}", f"glow recipe review number {i}", views=i * 100) for i in range(1, 81)]
    rep = scan._respond(None, TARGET, vids, scan_id=1)
    assert rep["locked"] is True and len(rep["creators"]) == 6
    assert rep["unlock"]["creators"] == scan.FULL["creators"] - 6
    full = scan._respond(None, TARGET, vids, scan_id=1, locked=False)
    assert len(full["creators"]) == scan.FULL["creators"] == 60
    assert len(full["top_videos"]) == scan.FULL["top_videos"]


def test_signup_url_goes_to_for_brands():
    url = scan.signup_url("skincare")
    assert url.startswith("https://app.newcollab.co/for-brands?") and "ref=brand_scan" in url and "niche=skincare" in url
