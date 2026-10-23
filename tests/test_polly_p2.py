from datetime import datetime, timedelta, timezone

from services.brand_browse import browse_ask, parse_query, word_pattern
from services.brand_shipping import apply_shipping, creator_country, ships_to, ships_to_country
from services.creator_stats import snapshot, stats_line
from services.polly_content import content_ask, content_stats, niche_summary, pick_post, posts_from_scrape, run_task
from services.polly_gigs import gig_fit_note
from services.polly_opener import paid_ladder_target
from services.polly_prefs import add_deal_intent, deal_intents, get_prefs, merge_prefs, parse_preferences

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


# ----- shipping ---------------------------------------------------------------

def test_ships_to_groups_and_worldwide():
    assert ships_to(["Worldwide"]) == "worldwide"
    assert ships_to(["EU"]) and "FR" in ships_to(["EU"])
    assert ships_to([]) is None
    assert ships_to_country(["US", "Canada"], "AU") is False
    assert ships_to_country(["Australia"], "AU") is True
    assert ships_to_country(None, "AU") is None
    assert ships_to_country(["Europe"], "United Kingdom") is True


def test_creator_country_prefers_shipping_address():
    creator = {"shipping_address": {"country": "Australia"}, "country": "US"}
    assert creator_country(creator, {"location": "London, UK"}) == "AU"
    assert creator_country({}, {"location": "London, United Kingdom"}) == "GB"
    assert creator_country({}, {}) is None


def test_apply_shipping_drops_labels_and_orders():
    brands = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]
    regions = {1: ["US"], 2: None, 3: ["Worldwide"]}
    out = apply_shipping(brands, "AU", regions)
    assert [b["name"] for b in out["brands"]] == ["C", "B"]
    assert out["brands"][0]["ships_to_you"] is True
    assert out["brands"][1]["shipping_note"] == "Shipping not confirmed"
    assert out["dropped"] == 1 and out["ships_to"] == "Australia"


def test_apply_shipping_no_country_is_untouched():
    brands = [{"id": 1}]
    assert apply_shipping(brands, None, {1: ["US"]})["brands"] == brands


def test_shipping_summary_never_says_zero_of():
    from services.polly_tools import shipping_summary
    rows = [{"shipping_note": "Shipping not confirmed"}]
    say = shipping_summary(rows, {"ships_to": "Australia", "dropped": 2})["say"]
    assert "0 of" not in say and "Australia" in say and "left out 2" in say
    say = shipping_summary([{"ships_to_you": True}] * 6, {"ships_to": "Australia", "dropped": 0})["say"]
    assert say == "These 6 ship to Australia."


# ----- stats ------------------------------------------------------------------

def test_manual_followers_win_until_higher_sync():
    scrape = {"follower_count": 3000, "scraped_at": (NOW - timedelta(days=2)).isoformat()}
    override = {"followers": 4000, "at": (NOW - timedelta(days=1)).isoformat()}
    snap = snapshot(scrape, 0, override, now=NOW)
    assert snap["followers"] == 4000 and snap["source"] == "manual"
    newer = {"follower_count": 4500, "scraped_at": NOW.isoformat()}
    snap = snapshot(newer, 0, override, now=NOW)
    assert snap["followers"] == 4500 and snap["manual_superseded"]


def test_stats_line_freshness_and_failure():
    fresh = snapshot({"follower_count": 10, "scraped_at": (NOW - timedelta(hours=3)).isoformat()}, now=NOW)
    assert stats_line(fresh, now=NOW) == "Stats updated 3h ago"
    old = snapshot({"follower_count": 10, "scraped_at": "2026-10-01T00:00:00+00:00"},
                   failed_recently=True, now=NOW)
    assert "couldn't refresh" in stats_line(old, now=NOW)
    old["failed"] = False
    assert stats_line(old, now=NOW).endswith("refreshing now")


# ----- paid lane --------------------------------------------------------------

def test_gig_fit_note_flags_agency_briefs_for_small_creators():
    opp = {"title": "Agency seeks UGC creators", "description": "Our agency manages 40 brands",
           "pr_value_usd": 2500}
    assert gig_fit_note(opp, followers=2000)["rank"] >= 2
    assert "Agency-scale" in gig_fit_note(opp, followers=2000)["note"]
    assert gig_fit_note(opp, followers=50000)["rank"] == 0


def test_gig_fit_note_region_mismatch():
    fit = gig_fit_note({"title": "UGC", "shipping_regions": ["US"]}, followers=50000, country="AU")
    assert fit["rank"] == 1 and "US" in fit["note"]


def test_deal_intents_list():
    notes = add_deal_intent({}, "gifted")
    notes = add_deal_intent(notes, "paid")
    assert deal_intents(notes) == ["gifted", "paid"]
    gifted = merge_prefs({}, {"gifted_only": True})
    assert "paid" not in deal_intents(add_deal_intent(gifted, "paid"))


def test_paid_ladder_after_three_posts():
    posted = [{"brand_name": n} for n in ("A", "B", "C")]
    assert paid_ladder_target({"posted_brands": posted[:2]}) is None
    assert paid_ladder_target({"posted_brands": posted})["brand_name"] == "A"
    asked = posted[:2] + [{"brand_name": "C", "paid_asked": True}]
    assert paid_ladder_target({"posted_brands": asked}) is None


# ----- niches -----------------------------------------------------------------

def test_parse_query_subniches():
    assert parse_query("K-beauty brands")["label"] == "K-beauty"
    assert parse_query("korean skincare")["strict"] is True
    assert parse_query("perfume")["label"] == "fragrance"
    assert parse_query("matcha brands")["strict"] is True
    assert parse_query("fitness")["cats"] == ["fitness"]


def test_word_pattern_is_whole_word():
    assert word_pattern("tea") == r"\mtea\M"
    assert word_pattern("rom&nd").startswith(r"\m")


def test_browse_ask():
    assert browse_ask("find me K-beauty brands") == "k-beauty"
    assert browse_ask("any fragrance brands?") == "fragrance"
    assert browse_ask("do you have matcha brands") == "matcha"
    assert browse_ask("find me brands") is None
    assert browse_ask("I love this brand") is None


def test_allow_categories_turns_a_category_back_on():
    notes = merge_prefs({}, {"avoid_categories": ["nails", "fitness"]})
    patch = parse_preferences("I'm ok with nails now")
    assert patch.get("allow_categories") == ["nails"]
    notes = merge_prefs(notes, patch)
    assert get_prefs(notes)["avoid_categories"] == ["fitness"]
    assert "allow_categories" not in parse_preferences("don't show me nails brands")


# ----- content ----------------------------------------------------------------

def _scrape():
    posts = []
    for i, views in enumerate([1200, 800, 15000, 900, 1100, 700]):
        posts.append({"caption": f"Post {i} hook line. More text #skincare #grwm", "views": views,
                      "likes": views // 10, "comments": 5, "shares": 1, "saves": 2,
                      "post_url": f"https://x/{i}", "timestamp": (NOW - timedelta(days=2 * i + 1)).isoformat()})
    return {"recent_posts": posts, "primary_niche": "skincare", "content_themes": ["routines"]}


def test_content_stats_from_real_posts():
    stats = content_stats(_scrape(), now=NOW)
    assert stats["posts_analysed"] == 6 and stats["enough"]
    assert stats["best_post"]["views"] == 15000 and stats["best_post"]["hook"] == "Post 2 hook line"
    assert stats["weakest_post"]["views"] == 700
    assert stats["median_views"] == 1000
    assert stats["days_since_last_post"] == 1
    assert stats["posts_per_week"] == 3.5
    assert stats["top_hashtags"][:2] == ["skincare", "grwm"]


def test_missing_likes_are_not_zero_engagement():
    scrape = _scrape()
    for p in scrape["recent_posts"]:
        p.update(likes=0, comments=0, shares=0, saves=0)
    stats = content_stats(scrape, now=NOW)
    assert stats["likes_comments_synced"] is False
    assert "engagement_per_view_pct" not in stats and "likes" not in stats["best_post"]


def test_content_stats_empty_scrape():
    assert content_stats({}) == {"posts_analysed": 0, "enough": False}


def test_niche_summary_uses_kit_headline():
    out = niche_summary(_scrape(), {"niche": "beauty"}, {"headline": "Skincare for busy mums"})
    assert out["primary_niche"] == "skincare" and out["kit_headline"] == "Skincare for busy mums"


def test_pick_post():
    posts = posts_from_scrape(_scrape())
    assert pick_post(posts, "latest")["url"] == "https://x/0"
    assert pick_post(posts, "best")["views"] == 15000
    assert pick_post(posts, "https://x/3/")["views"] == 900
    assert pick_post(posts, "2")["url"] == "https://x/1"


def test_run_task_grounds_prompt_in_stats():
    seen = {}

    def llm(system, user):
        seen["user"] = user
        return {"hooks": ["a", "b"], "pattern": "p"}

    out = run_task("hooks", _scrape(), {}, {}, {"topic": "serum"}, llm=llm)
    assert out["status"] == "written" and out["content"]["hooks"] == ["a", "b"]
    assert '"median_views": 1000' in seen["user"] and "serum" in seen["user"]


def test_run_task_falls_back_without_model():
    def broken(*_):
        raise RuntimeError("down")

    out = run_task("weekly_plan", _scrape(), {}, {}, {}, llm=broken)
    assert out["source"] == "fallback" and len(out["content"]["posts"]) == 4
    assert run_task("script", _scrape(), {}, {}, {}, llm=broken)["status"] == "unavailable"
    assert run_task("nope", {}, {}, {}, {})["ok"] is False


def test_review_without_posts_asks_for_paste():
    out = run_task("review", {}, {}, {}, {"post": "latest"}, llm=lambda *_: {"verdict": "x"})
    assert out["status"] == "no_post"
    out = run_task("review", {}, {}, {}, {"draft": "my caption"}, llm=lambda *_: {"verdict": "ok"})
    assert out["status"] == "written"


def test_content_ask_routes():
    assert content_ask("can you make me a content plan for this week")[1]["task"] == "weekly_plan"
    assert content_ask("give me some hooks for my serum video")[1]["task"] == "hooks"
    assert content_ask("review my last post")[1]["task"] == "review"
    assert content_ask("write a script for a GRWM")[1]["task"] == "script"
    assert content_ask("what's my niche?") == ("get_content_stats", {})
    assert content_ask("my views are down") == ("get_content_stats", {})
    assert content_ask("pitch Glow Recipe") is None


def test_content_turn_guardrails_allow_lists():
    from services.polly_brain import reply_problems
    from tests.test_polly_brain import make_state

    state = make_state(user_text="hooks please")
    reply = "I've written you 6 hooks.\n\n**Monday** · GRWM: film your routine."
    assert reply_problems(state, reply)
    state.tool_log.append({"name": "content_assist", "status": "written", "result": {}, "brand_names": []})
    assert reply_problems(state, reply) == []
    assert reply_problems(state, "I've drafted your pitch.")
