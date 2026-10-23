from datetime import datetime, timedelta, timezone

from services import gifting_radar as radar
from services.radar_extract import (
    brand_key,
    brand_signals,
    candidates,
    display_name,
    keep_video,
    key_variants,
    radar_niche,
    signal_types,
    video_niches,
)
from services.radar_source import normalize, run_input

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def _item(**over):
    base = {
        "id": "7698484852206619918",
        "webVideoUrl": "https://www.tiktok.com/@creator_a/video/7698484852206619918",
        "text": "THANK YOU @Good Molecules 🎁 I love this PR box #goodmolecules #prunboxing",
        "hashtags": [{"name": "goodmolecules"}, {"name": "prunboxing"}],
        "mentions": ["@Good Molecules"],
        "detailedMentions": [{"name": "goodmolecules"}],
        "playCount": 432, "diggCount": 40, "createTimeISO": "2026-10-19T20:53:26.000Z",
        "videoMeta": {"coverUrl": "https://p16-sign.tiktokcdn.com/x.jpeg"},
        "isAd": True, "isSponsored": True, "textLanguage": "en",
        "authorMeta": {"name": "creator_a", "fans": 5231, "signature": "skincare girlie"},
    }
    base.update(over)
    return base


def test_normalize_maps_actor_fields():
    v = normalize(_item())
    assert v["tiktok_id"] == "7698484852206619918" and v["mentions"] == ["goodmolecules"]
    assert v["creator"] == {"handle": "creator_a", "followers": 5231, "region": None, "bio": "skincare girlie"}
    assert v["hashtags"] == ["goodmolecules", "prunboxing"] and v["is_ad"] is True
    assert normalize({"id": "1"}) is None


def test_run_input_never_downloads_media():
    inp = run_input(["prpackage"], ["skincare pr unboxing"], 20)
    assert inp["shouldDownloadVideos"] is False and inp["shouldDownloadCovers"] is False
    assert inp["commentsPerPost"] == 0 and inp["resultsPerPage"] == 20


def test_brand_key_strips_store_suffixes():
    assert brand_key("@AXIS-Y Store US") == "axisy"
    assert brand_key("beautyofjoseon_official") == "beautyofjoseon"
    assert brand_key("arencia_global") == "arencia"
    assert brand_key("isntree") == "isntree"


def test_key_variants_and_directory_match():
    assert key_variants("garnierusa") == ["garnierusa", "garnier"]
    assert key_variants("lorealparisusa") == ["lorealparisusa", "lorealparis"]
    assert key_variants("lotus") == ["lotus"]
    directory = {"garnier": {"id": 9, "name": "Garnier"}}
    assert radar.match_directory("garnierusa", directory) == ("garnier", {"id": 9, "name": "Garnier"})
    assert radar.match_directory("iunik", directory) == ("iunik", {})


def test_display_name_cleans_tiktok_names():
    assert display_name("AXIS-Y Store US") == "AXIS-Y"
    assert display_name("ISNTREE US") == "Isntree"
    assert display_name("Dr.Althea US") == "Dr.Althea"
    assert display_name("UNOVE.global") == "UNOVE"
    assert display_name("Dermafactory_official") == "Dermafactory"
    assert display_name("Centellian24 (US)") == "Centellian24"
    assert display_name("Nalia Skin - Aussie Skincare") == "Nalia Skin"
    assert display_name("GARNIER") == "Garnier"
    assert display_name("IUNIK") == "IUNIK"
    assert display_name("Beauty of Joseon") == "Beauty of Joseon"


def test_candidates_skip_retailers_and_the_creator():
    v = normalize(_item(detailedMentions=[{"name": "isntree"}, {"name": "yesstyle"}, {"name": "creator_a"}],
                        mentions=["@ISNTREE US", "@YesStyle", "@me"]))
    keys = [c["key"] for c in candidates(v)]
    assert keys == ["isntree"]


def test_branded_hashtag_needs_directory_or_mention():
    v = normalize(_item(text="Gifted by Olay #olaypartner #gifted", detailedMentions=[], mentions=[],
                        hashtags=[{"name": "olaypartner"}, {"name": "gifted"}]))
    assert brand_signals(v) == []
    found = brand_signals(v, directory_keys={"olay"})
    assert found[0]["key"] == "olay" and "paid" in found[0]["types"] and "gifted" in found[0]["types"]


def test_signal_types():
    gifted = normalize(_item(isAd=False))
    assert signal_types(gifted) == ["gifted"]
    paid = normalize(_item(text="#ad loving my new serum @brand", isAd=False, hashtags=[{"name": "ad"}]))
    assert "paid" in signal_types(paid)
    shop = normalize(_item(text="Thank you @x for sending! Use my code GLOW15", isAd=False))
    assert set(signal_types(shop)) == {"gifted", "affiliate"}
    nothing = normalize(_item(text="my morning routine @brand", isAd=False, hashtags=[]))
    assert signal_types(nothing) == []


def test_keep_video_filters_size_age_language():
    v = normalize(_item())
    assert keep_video(v, NOW)
    assert not keep_video(normalize(_item(authorMeta={"name": "big", "fans": 3_400_000})), NOW)
    assert not keep_video(normalize(_item(createTimeISO="2026-07-01T00:00:00Z")), NOW)
    assert not keep_video(normalize(_item(textLanguage="id")), NOW)


def test_radar_niche_and_video_niches():
    assert radar_niche("Beauty & Skincare") == "skincare"
    assert radar_niche("tech, gaming") == "tech"
    assert radar_niche("Beauty") == "makeup"
    assert radar_niche("") == ""
    assert "skincare" in video_niches(normalize(_item(text="new serum PR #skincare")))


def test_query_plan_shares_hashtags_then_niche_queries():
    plan = radar.query_plan("tech")
    tags = [p["query"] for p in plan if p["type"] == "hashtag"]
    assert tags[:2] == ["prpackage", "gifted"] and "techpr" in tags
    searches = [p["query"] for p in plan if p["type"] == "search"]
    assert len(searches) >= 10 and "keyboard pr unboxing" in searches and "gifted tech" not in searches
    assert radar.ROUND_SIZE * radar.PER_QUERY <= 250
    assert len(radar.query_plan("travel")) >= 6


class _NoCacheConn:
    def cursor(self, *a, **k):
        return self

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return []


def test_ai_queries_are_narrow_and_carry_gifting_intent():
    from services.radar_queries import ai_queries
    asked = []

    def fake(prompt, _):
        asked.append(prompt)
        return {"queries": ["Mechanical Keyboard PR package", "#desksetup gifted", "iphone case review",
                            "keyboard pr unboxing", "x", "mechanical keyboard pr package"]}

    got = ai_queries("tech", {"themes": "desk setups; keyboards", "country": "US"},
                     avoid=["keyboard pr unboxing"], write=fake)
    assert got == ["mechanical keyboard pr package", "desksetup gifted", "iphone case review pr"]
    assert "desk setups; keyboards" in asked[0] and "keyboard pr unboxing" in asked[0]
    assert ai_queries("tech", write=lambda *a: 1 / 0) == []


def test_next_round_leads_with_written_queries_then_bank():
    plan = radar.next_round(_NoCacheConn(), "tech", {"themes": "keyboards"},
                            write=lambda *a: {"queries": [f"niche thing {i} pr" for i in range(16)]})
    searches = [p["query"] for p in plan if p["type"] == "search"]
    assert len(plan) == radar.ROUND_SIZE and searches[0] == "niche thing 0 pr"
    deeper = radar.next_round(_NoCacheConn(), "tech", avoid=[p["query"] for p in plan],
                              size=radar.DEEPER_SIZE, write=lambda *a: {})
    assert deeper and not {p["query"] for p in deeper} & {p["query"] for p in plan}
    assert all(p["type"] == "search" for p in deeper)


def test_radar_paywall_copy():
    from services.polly_persona import persona_radar_paywall_say
    say = persona_radar_paywall_say("tech")
    assert "free TikTok search is used" in say and "**tech**" in say and "$19/mo" in say


def test_post_niches_trust_the_post_over_the_query():
    from services.radar_extract import post_niches
    beauty = normalize(_item(text="PR haul from @Aveeno #skincare #prhaul"))
    beauty["query"] = "small creator tech pr"
    assert post_niches(beauty, "tech") == ["skincare"]
    tech = normalize(_item(text="PR unboxing @Nothing earbuds #techtok", hashtags=[{"name": "techtok"}]))
    tech["query"] = "tech pr unboxing"
    assert post_niches(tech, "tech") == ["tech"]
    vague = normalize(_item(text="my first PR package!", hashtags=[]))
    vague["query"] = "tech pr package"
    assert post_niches(vague, "tech") == ["tech"]
    assert video_niches(normalize(_item(text="new carpet and the team", hashtags=[]))) == []


def test_nicheless_roundup_files_by_what_the_brand_sells():
    seen = []

    def fake(system, names):
        seen.append(names)
        return {"brands": {"Anua": "skincare", "Glowmode": "other", "Made Up": "pizza"}}

    got = radar.brand_niches({"Aveeno": "skincare", "Anua": "", "Glowmode": "", "Made Up": "",
                              "Belkin": "tech"}, classify=fake)
    assert got == {"Aveeno": "skincare", "Belkin": "tech", "Anua": "skincare", "Glowmode": "other"}
    assert "Aveeno" not in seen[0]
    assert radar.brand_niches({"X": ""}, classify=lambda *a: 1 / 0) == {}

    roundup = normalize(_item(text="Lets open some PR packages + how I got them @Aveeno", hashtags=[]))
    roundup["query"] = "tech pr unboxing"
    assert radar.signal_niches(roundup, "tech", "skincare") == ["skincare"]
    assert radar.signal_niches(roundup, "tech", "other") == ["general"]
    assert radar.signal_niches(roundup, "tech", "") == ["tech"]
    on_topic = normalize(_item(text="PR unboxing @Nothing earbuds #techtok", hashtags=[{"name": "techtok"}]))
    on_topic["query"] = "tech pr unboxing"
    assert radar.signal_niches(on_topic, "tech", "skincare") == ["tech"]


def test_thread_keeps_radar_results():
    from services.polly_memory import sanitize_thread
    radar_msg = {"role": "assistant", "content": "These brands are gifting tech creators",
                 "radar": {"status": "found", "run_id": 7, "brands": [{"name": str(i)} for i in range(20)]}}
    kept = sanitize_thread([radar_msg])["messages"][0]
    assert kept["radar"]["run_id"] == 7 and len(kept["radar"]["brands"]) == 12
    assert "radar" not in sanitize_thread([{**radar_msg, "radar": {"brands": []}}])["messages"][0]


def test_replying_to_mention_is_not_a_brand():
    v = normalize(_item(text="Replying to @sierra my first PR from @Dell #prunboxing",
                        mentions=["@sierra", "@Dell"], detailedMentions=[{"name": "sierra"}, {"name": "dell"}]))
    assert [c["key"] for c in candidates(v)] == ["dell"]


def test_score_prefers_creators_your_size():
    row = {"creators_60d": 4, "creators_14d": 1, "median_followers": 3400}
    near = radar.score(row, followers=3000, region_match=False, has_contact=False)
    far = radar.score(row, followers=20000, region_match=False, has_contact=False)
    assert near - far == 3
    assert radar.score(row, 3000, True, True) == near + 4


def test_evidence_line():
    row = {"creators_30d": 6, "creators_60d": 9, "median_followers": 3400, "max_followers": 9000}
    assert radar.evidence_line(row) == "6 creators under 10K · last 30 days · median 3.4K followers"
    row = {"creators_30d": 1, "creators_60d": 3, "median_followers": 12000, "max_followers": 22000}
    assert radar.evidence_line(row) == "3 creators under 25K · last 90 days · median 12K followers"
    row = {"creators_30d": 1, "creators_60d": 1, "median_followers": 4400, "max_followers": 4400}
    assert radar.evidence_line(row) == "1 creator with 4.4K followers · last 90 days"


def test_pick_examples_one_per_creator_closest_size():
    vids = [
        {"creator_handle": "a", "creator_followers": 3000, "views": 100},
        {"creator_handle": "a", "creator_followers": 3000, "views": 900},
        {"creator_handle": "b", "creator_followers": 20000, "views": 50000},
        {"creator_handle": "c", "creator_followers": 3500, "views": 200},
    ]
    picked = radar.pick_examples(vids, followers=3000)
    assert [v["creator_handle"] for v in picked] == ["a", "c", "b"]
    assert picked[0]["views"] == 900


def _card(i, max_f=9000):
    return {"id": f"radar-{i}", "name": f"B{i}", "evidence_data": {"max_followers": max_f}}


def test_envelope_states():
    ctx = {"niche": "skincare", "followers": 3000}
    found = radar.envelope([_card(i) for i in range(9)], ctx, run_id=1, served_from="database")
    assert found["status"] == "found" and len(found["brands"]) == radar.PAGE and found["has_more"]
    assert "under 10K" in found["intro"] and found["caveat"] == radar.CAVEAT
    weak = radar.envelope([_card(1)], ctx, run_id=1, served_from="database")
    assert weak["status"] == "weak" and weak["intro"].startswith("Only 1 brand came up")
    none = radar.envelope([], ctx, run_id=1, served_from="database")
    assert none["status"] == "none" and none["caveat"] == ""
    page2 = radar.envelope([_card(i, 20000) for i in range(9)], {**ctx, "offset": 7}, run_id=1, served_from="database")
    assert len(page2["brands"]) == 2 and not page2["has_more"]


def test_build_card_contact_states():
    row = {"id": 5, "name": "goodmolecules", "signal_types": ["gifted", "paid"], "creators_14d": 1,
           "creators_30d": 3, "creators_60d": 3, "median_followers": 4000, "max_followers": 6000,
           "last_signal_at": NOW}
    card = radar.build_card(row, {"id": 9, "name": "Good Molecules", "contact_email": "pr@x.com", "regions": ["US"]},
                            [], followers=3000, country="US", now=NOW)
    assert card["contact"] == "pitch" and card["ships"] == "Ships to you" and card["badges"] == ["🎁 Gifting", "💸 Paid"]
    unknown = radar.build_card(row, {}, [], followers=3000, country="AU", now=NOW)
    assert unknown["contact"] == "find_contact" and unknown["ships"] == "Shipping not confirmed"
    assert unknown["name"] == "goodmolecules" and unknown["brand_id"] is None


def test_evidence_pitch_line_never_names_creators():
    line = radar.evidence_pitch_line("Good Molecules", "skincare", {"creators_30d": 4})
    assert line.startswith("I've seen Good Molecules sending PR to skincare creators this month")
    assert "@" not in line


def test_radar_line_goes_into_pitch_body():
    from services.polly_tools import _with_radar_line
    pitch = {"body": "Hi team,\n\nI'm Ava.\n\nI'd love to film...\n\nThanks,\nAva", "email": "pr@x.com",
             "subject": "Collab"}
    out = _with_radar_line(pitch, "Good Molecules", {"niche": "skincare", "evidence": {"creators_30d": 3}})
    parts = out["body"].split("\n\n")
    assert parts[2].startswith("I've seen Good Molecules") and "mailto:pr@x.com" in out["mailto"]
    assert _with_radar_line(pitch, "X", {"niche": ""}) == pitch


def test_radar_chip_in_starters():
    from services.polly_discovery import starters_for
    chips = starters_for({"discovery_completed_at": "2026-10-01T00:00:00Z"})
    assert any(c["id"] == "gifting_radar" for c in chips)


def test_contact_helpers_pick_brand_inbox():
    from services.radar_contact import best_email, domain_of, email_rank
    assert domain_of("https://www.scentbird.com/pages/contact") == "scentbird.com"
    assert domain_of("shop.brand.co.uk") == "brand.co.uk"
    assert email_rank("influencers@x.com") < email_rank("pr@x.com") < email_rank("hello@x.com")
    assert email_rank("noreply@x.com") == 999
    raw = ["logo@2x.png", "hello@scentbird.com", "partners@scentbird.com", "dev@sentry.io",
           "privacy@scentbird.com", "jane@gmail.com"]
    assert best_email(raw, "scentbird.com") == "partners@scentbird.com"
    assert best_email(["privacy@scentbird.com"], "scentbird.com") == ""
    from services.radar_contact import hunter_rank
    assert hunter_rank({"value": "sergei@x.com", "department": "marketing"}) == 50
    assert hunter_rank({"value": "sergei@x.com", "position": "CTO"}) == 300


def test_find_contact_order_website_then_hunter(monkeypatch):
    from services import radar_contact as rc

    class Cur:
        def __init__(self):
            self.updates = []

        def execute(self, sql, params=None):
            self.updates.append(params)

    class Conn:
        def __init__(self):
            self.cur = Cur()

        def cursor(self, **kw):
            return self.cur

        def commit(self):
            pass

    row = {"id": 5, "name": "Scentbird", "tiktok_handle": "scentbird", "contact_email": None,
           "contact_source": None, "contact_checked_at": None, "website": None}
    monkeypatch.setattr(rc, "_load", lambda conn, bid: dict(row))
    calls = []
    monkeypatch.setattr(rc, "hunter_lookup",
                        lambda **kw: calls.append(kw) or ("scentbird.com", "sam@scentbird.com", 50))
    monkeypatch.setattr(rc, "website_email", lambda d: "partners@scentbird.com")
    out = rc.find_contact(Conn(), 5, now=NOW)
    assert out["email"] == "partners@scentbird.com" and out["source"] == "website"
    assert calls == [{"company": "Scentbird"}]

    monkeypatch.setattr(rc, "website_email", lambda d: "support@scentbird.com")
    out = rc.find_contact(Conn(), 5, now=NOW)
    assert out["email"] == "sam@scentbird.com" and out["source"] == "hunter"

    monkeypatch.setattr(rc, "website_email", lambda d: "")
    out = rc.find_contact(Conn(), 5, now=NOW)
    assert out["email"] == "sam@scentbird.com" and out["source"] == "hunter"

    monkeypatch.setattr(rc, "hunter_lookup", lambda **kw: ("", "", 999))
    out = rc.find_contact(Conn(), 5, now=NOW)
    assert out["email"] == "" and out["source"] == "tiktok_dm"


def test_dm_text_is_short_and_clean():
    from services.radar_contact import dm_text
    dm = dm_text("Scentbird", "fashion", first_name="Maya", followers=3200, median_views=1500,
                 kit_url="https://newcollab.co/kit/maya")
    assert dm.startswith("Hi Scentbird team!") and "3.2K followers" in dm and "1.5K views" in dm
    assert dm.endswith("https://newcollab.co/kit/maya") and len(dm.split()) < 90
    assert "I'm a fashion creator" in dm_text("Scentbird", "fashion")


def test_with_radar_chip_adds_once():
    from services.polly_discovery import starters_for, with_radar_chip
    pending = starters_for({"pending_pitch": {"id": 1, "name": "Tower 28"}})
    assert not any(c["id"] == "gifting_radar" for c in pending)
    chips = with_radar_chip(pending)
    assert [c["id"] for c in chips].count("gifting_radar") == 1 and chips[0]["id"] == "gifting_radar"
    assert with_radar_chip(chips) == chips
    assert with_radar_chip([])[0]["id"] == "gifting_radar"


def test_tidy_lists_drops_emptied_items_and_renumbers():
    from services.polly_persona import scrub_polly_voice, tidy_lists
    text = "Try these:\n\n1. **Hooks:** grab them fast.\n2.\n3. **Hashtags:** keep #ugc.\n\nThoughts?"
    assert tidy_lists(text) == "Try these:\n\n1. **Hooks:** grab them fast.\n2. **Hashtags:** keep #ugc.\n\nThoughts?"
    assert tidy_lists("1. a\n\n1. b\n\n1. c") == "1. a\n\n2. b\n\n3. c"
    assert tidy_lists("1. a\nDone.\n1. b") == "1. a\nDone.\n1. b"
    scrubbed = scrub_polly_voice("Steps:\n1. Write the hook.\n2. Check your pitches tab later.\n3. Post it.")
    assert scrubbed == "Steps:\n1. Write the hook.\n2. Post it."


def test_salvage_keeps_list_markers_with_their_text(monkeypatch):
    from services import polly_brain
    monkeypatch.setattr(polly_brain, "reply_problems", lambda state, s: ["bad"] if "BAD" in s else [])
    out = polly_brain.salvage_reply(None, "Ideas for you:\n1. Hook in 3 seconds.\n2. BAD claim here.\n3. Keep hashtags tight.")
    assert out == "Ideas for you:\n1. Hook in 3 seconds.\n2. Keep hashtags tight."


def test_fallback_routes_radar_asks():
    from services.polly_fallback import _RADAR_ASK
    assert _RADAR_ASK.search("which brands are sending PR right now")
    assert _RADAR_ASK.search("who's gifting in skincare?")
    assert _RADAR_ASK.search("find brands that gift small creators")
    assert not _RADAR_ASK.search("pitch Glow Recipe")
