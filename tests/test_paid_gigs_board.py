from datetime import datetime, timedelta, timezone

import services.paid_gigs_board as board

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def _row(i, brand="Glow Labs", title="TikTok UGC video for our serum", pay=300, days_ago=1,
         matched=False, applied=False, source="sideshift", desc=None):
    return {
        "id": i,
        "brand_name": brand,
        "product_name": title,
        "campaign_description": desc or f"We need a creator to film {title.lower()}. Paid on delivery.",
        "pr_value_usd": pay,
        "pay_label": f"${pay}" if pay else "",
        "posted_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "is_matched": matched,
        "already_applied": applied,
        "source_platform": source,
        "is_sourced": True,
        "apply_mode": "url",
        "external_apply_url": f"https://sideshift.app/jobs/{i}",
    }


def test_board_drops_gifted_and_collapses_reposts():
    rows = [
        _row(1, brand="Glow Labs", title="TikTok UGC video for our serum", source="craigslist"),
        _row(2, brand="Glow Labs", title="TikTok UGC video for our serum", source="craigslist", days_ago=2),
        _row(3, brand="Box Co", title="Unboxing reel", pay=0, desc="Gifted product only, unpaid."),
        _row(4, brand="Fit Fuel", title="Gym protein UGC ad"),
    ]
    rows[2]["pay_label"] = "Gifted"
    rows[2]["is_sourced"] = False
    ids = [c["id"] for c in board.board_cards(rows)]
    assert 3 not in ids
    assert ids.count(1) + ids.count(2) == 1
    assert 4 in ids


def test_rank_puts_fresh_niche_matches_first_and_applied_last():
    cards = board.board_cards([
        _row(1, brand="Old Match", matched=True, days_ago=12),
        _row(2, brand="New Other", days_ago=0),
        _row(3, brand="Fresh Match", matched=True, days_ago=2),
        _row(4, brand="Applied One", matched=True, days_ago=0, applied=True),
    ])
    ranked = board.rank_cards(cards, NOW)
    assert [c["id"] for c in ranked] == [3, 2, 1, 4]


def test_free_users_get_the_same_three_open_on_every_filter():
    ranked = board.rank_cards(board.board_cards([_row(i, brand=f"Brand {i}", title=f"UGC ad number {i}")
                                                 for i in range(1, 8)]), NOW)
    preview = board.preview_ids(ranked, is_pro=False)
    assert len(preview) == board.FREE_PREVIEW
    assert board.preview_ids(ranked, is_pro=True) is None
    locked = board.public_card(ranked[-1], locked=True, now=NOW)
    assert "brand_name" not in locked and "headline" not in locked
    assert locked["pay_label"] and locked["locked"] is True


def test_public_card_never_ships_apply_links():
    card = board.board_cards([_row(1)])[0]
    out = board.public_card(card, now=NOW)
    assert "external_apply_url" not in out and "apply_email" not in out
    assert out["brand_name"] == "Glow Labs"
    assert out["is_new"] is True


def test_views_and_stats():
    cards = board.board_cards([
        _row(1, brand="A", title="UGC ad alpha", pay=200, matched=True, days_ago=1),
        _row(2, brand="B", title="UGC ad beta", pay=800, days_ago=6),
        _row(3, brand="C", title="UGC ad gamma", pay=600, days_ago=20),
    ])
    assert [c["id"] for c in board.filter_view(cards, "for_you")] == [1]
    assert [c["id"] for c in board.filter_view(cards, "high_pay")] == [2, 3]
    assert [c["id"] for c in board.filter_view(cards, "new", NOW)] == [1]
    stats = board.board_stats(cards, NOW)
    assert stats["total"] == 3 and stats["new_this_week"] == 2
    assert stats["median_pay_usd"] == 600 and stats["open_pay_usd"] == 1600


def test_draft_uses_only_known_facts():
    creator = {"first_name": "Maya", "username": "maya.moves", "niche": "Fitness, Wellness",
               "followers_count": 12400}
    gig = {"brand_name": "Fit Fuel", "headline": "Gym protein UGC ad", "deliverable": "2 TikTok videos"}
    draft = board.draft_application(creator, gig, "https://newcollab.co/kit/maya.moves")
    body = draft["body"]
    assert body.startswith("Hi Fit Fuel team,")
    assert "I'm Maya, a fitness UGC creator (@maya.moves, 12.4K followers)." in body
    assert "https://newcollab.co/kit/maya.moves" in body
    assert "@maya.moves" in draft["subject"]
    assert draft["subject"].startswith("UGC application: Fit Fuel, Gym protein UGC ad")
    assert "$" not in body


def test_gig_title_in_brand_field_is_never_greeted():
    title = "Approximately 20–35 Minutes Of Raw, Unedited"
    creator = {"first_name": "Test", "username": "newcollabco", "niche": '["fitness"]', "followers_count": 419}
    gig = {"brand_name": title, "headline": title, "source_label": "Upwork"}
    body = board.draft_application(creator, gig)["body"]
    assert body.startswith("Hi there,")
    assert title not in body
    assert "a fitness UGC creator" in body
    assert "I saw your brief on Upwork" in body


def test_greeting_brand_keeps_real_names_and_drops_titles():
    assert board.greeting_brand({"brand_name": "Miso: Your Nutrition Coach", "headline": "Miso UGC Creator"}) == "Miso"
    assert board.greeting_brand({"brand_name": "The Feel Good Project Ltd", "headline": "Faceless Carousel"}) == "The Feel Good Project Ltd"
    assert board.greeting_brand({"brand_name": "WarriorBabe", "headline": "Womens Fitness Video Story"}) == "WarriorBabe"
    assert board.greeting_brand({"brand_name": "product review site", "headline": "30s Male UGC Creator"}) == ""
    assert board.greeting_brand({"brand_name": "Short Talking-to-camera Videos For A Chronic", "headline": "x"}) == ""
    assert board.greeting_brand({"brand_name": "Snazo", "headline": "Snazo"}) == "Snazo"
    assert board.greeting_brand({"brand_name": "Glow", "headline": "Glow", "brand_unknown": True}) == ""


def test_draft_without_kit_or_brand_stays_clean():
    draft = board.draft_application({"username": "sam"}, {"brand_name": "Unknown brand", "headline": ""})
    assert draft["body"].startswith("Hi there,")
    assert "Portfolio" not in draft["body"]
    assert "None" not in draft["body"]
