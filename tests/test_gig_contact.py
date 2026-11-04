"""Direct contacts for scanned SideShift gigs: brand's own public inbox, else nothing changes."""
from datetime import datetime, timezone
from unittest import mock

from services import gig_contact as gc

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


APPSTORE = {"results": [
    {"trackName": "Some Other App", "sellerName": "Other LLC", "sellerUrl": "https://other.example"},
    {"trackName": "Kettlebell Body: Home Workouts", "sellerName": "KB Fitness Ltd", "sellerUrl": "https://kettlebellbody.app"},
]}


def test_name_matching():
    assert gc.name_matches("Kettlebell Body", "Kettlebell Body: Home Workouts")
    assert gc.name_matches("Flash Studio", "Flash Studio - AI Photos")
    assert not gc.name_matches("Peli", "Pelican Cases")
    assert not gc.name_matches("", "anything")


def test_appstore_site_picks_the_matching_app_and_skips_job_boards():
    assert gc.appstore_site("Kettlebell Body", http=lambda *a, **k: Resp(APPSTORE)) == "https://kettlebellbody.app"
    board = {"results": [{"trackName": "Smudge", "sellerName": "Smudge", "sellerUrl": "https://app.sideshift.app/x"}]}
    assert gc.appstore_site("Smudge", http=lambda *a, **k: Resp(board)) == ""
    assert gc.appstore_site("Smudge", http=lambda *a, **k: Resp({}, 500)) == ""


def _find(website_email="", hunter=("", "", 999), verdict="valid", spend=True, site_http=None, used=0):
    with mock.patch("services.radar_contact.website_email", return_value=website_email) as web, \
            mock.patch("services.radar_contact.hunter_lookup", return_value=hunter) as hl, \
            mock.patch("services.contact_repair._spend", return_value=spend), \
            mock.patch("services.contact_repair._calls_today", return_value=used), \
            mock.patch("services.contact_repair.daily_cap", return_value=150), \
            mock.patch("services.contact_repair.hunter_verify", return_value=verdict):
        found = gc.find_gig_contact(mock.MagicMock(), "Kettlebell Body", "",
                                    http=site_http or (lambda *a, **k: Resp(APPSTORE)))
    return found, web, hl


def test_website_inbox_wins_and_support_is_fine():
    found, web, hl = _find(website_email="support@kettlebellbody.app")
    assert found == {"email": "support@kettlebellbody.app", "source": "website", "website": "https://kettlebellbody.app"}
    web.assert_called_once_with("kettlebellbody.app")
    hl.assert_not_called()


def test_hunter_when_site_has_nothing():
    found, _, hl = _find(hunter=("kettlebellbody.app", "partners@kettlebellbody.app", 10))
    assert found["email"] == "partners@kettlebellbody.app" and found["source"] == "hunter"
    hl.assert_called_once_with(domain="kettlebellbody.app")


def test_company_lookup_when_no_website_found():
    found, _, hl = _find(website_email="", hunter=("kettlebellbody.com", "hello@kettlebellbody.com", 200),
                         site_http=lambda *a, **k: Resp({"results": []}))
    assert found["email"] == "hello@kettlebellbody.com" and found["website"] == "https://kettlebellbody.com"
    assert hl.call_args_list[0] == mock.call(company="Kettlebell Body")


def test_invalid_hunter_email_dropped_site_emails_trusted_and_hunter_budget_kept():
    assert _find(hunter=("kettlebellbody.app", "jo@kettlebellbody.app", 50), verdict="invalid")[0]["email"] == ""
    assert _find(website_email="info@kettlebellbody.app", verdict="invalid")[0]["email"] == "info@kettlebellbody.app"
    found, _, hl = _find(spend=False)
    assert found["email"] == "" and hl.call_count == 0
    found, _, hl = _find(used=80)  # past half of the shared daily cap: leave the rest to Autopilot
    assert found["email"] == "" and hl.call_count == 0


def test_domain_must_be_the_brands_own():
    good = [("FitFo", "fitfo.app"), ("Kettlebell Body: Home Workouts", "kettlebellbody.app"), ("CekatAI", "cekat.ai"),
            ("Oncourse AI", "getoncourse.ai"), ("claimHood", "claimhoodapp.com"), ("The UGC Lab", "theugclab.in"),
            ("Rods — Corner & Road Alerts", "driverods.com")]
    bad = [("Flash Studio", "flashforge.com"), ("Exhale", "mindbodyonline.com"), ("RELDA", "reldan.com"),
           ("Trendread", "trendreader.net"), ("TikTok", "tiktok.com"), ("Google", "google.com"),
           ("CreatorFlow", "youtube.com"), ("Bump", "thebump.com"), ("IQLab", "matlubapps.com")]
    assert all(gc.domain_matches(b, d) for b, d in good)
    assert not any(gc.domain_matches(b, d) for b, d in bad)


def test_other_company_from_appstore_is_ignored():
    other = {"results": [{"trackName": "Exhale", "sellerName": "Exhale", "sellerUrl": "https://www.mindbodyonline.com/x"}]}
    with mock.patch("services.radar_contact.website_email", return_value="sales@mindbodyonline.com") as web, \
            mock.patch("services.contact_repair._spend", return_value=False):
        found = gc.find_gig_contact(mock.MagicMock(), "Exhale", "", http=lambda *a, **k: Resp(other))
    assert found["email"] == "" and web.call_count == 0


def test_due_gigs_skips_recent_checks_and_real_emails():
    rows = [
        {"id": 1, "brand_name": "A", "brand_email": "sourced@newcollab.co", "additional_notes": "[scanner:sideshift:x]"},
        {"id": 2, "brand_name": "B", "brand_email": "sourced@newcollab.co",
         "additional_notes": "[scanner:sideshift:y] | contact_checked=2026-11-01"},
        {"id": 3, "brand_name": "C", "brand_email": "sourced@newcollab.co",
         "additional_notes": "[scanner:sideshift:z] | contact_checked=2026-09-01"},
        {"id": 4, "brand_name": "", "brand_email": "sourced@newcollab.co", "additional_notes": "[scanner:sideshift:w]"},
    ]
    conn = mock.MagicMock()
    conn.cursor.return_value.fetchall.return_value = rows
    assert [g["id"] for g in gc.due_gigs(conn, limit=5, now=NOW)] == [1, 3]


def test_save_result_records_contact_and_check_date():
    conn = mock.MagicMock()
    opp = {"id": 7, "additional_notes": "[scanner:sideshift:x] | apply_url=https://app.sideshift.app/jobs/x | contact_checked=2026-09-01"}
    gc.save_result(conn, opp, {"email": "hi@brand.app", "source": "website", "website": "https://brand.app"}, now=NOW)
    sql, params = conn.cursor.return_value.execute.call_args.args
    assert params[0] == "hi@brand.app" and params[1] == "https://brand.app"
    assert params[2].endswith("| contact_checked=2026-11-04 | contact=website")
    assert params[2].count("contact_checked") == 1 and "apply_url=" in params[2]
    assert gc.direct_contact_source(params[2]) == "website"
    gc.save_result(conn, opp, {"email": "", "source": "", "website": ""}, now=NOW)
    params = conn.cursor.return_value.execute.call_args.args[1]
    assert params[0] is None and "contact=" not in params[2].replace("contact_checked", "")


def test_apply_path_offers_direct_only_with_a_found_inbox():
    from opportunities_routes import _resolve_apply_path
    base = {"brand_website": "", "additional_notes": "[scanner:sideshift:x] | apply_url=https://app.sideshift.app/jobs/x"}
    plain = _resolve_apply_path({**base, "brand_email": "sourced@newcollab.co"})
    assert plain["apply_mode"] == "url" and not plain["direct_contact"]
    found = _resolve_apply_path({**base, "brand_email": "hi@brand.app",
                                 "additional_notes": base["additional_notes"] + " | contact_checked=2026-11-04 | contact=website"})
    assert found["apply_mode"] == "url" and found["direct_contact"] and found["apply_email"] == "hi@brand.app"


def test_card_carries_the_badge_flag_not_the_email():
    from services.paid_gigs_board import public_card
    from services.polly_gigs import gig_card_from_opp
    card = gig_card_from_opp({"id": 5, "brand_name": "Kettlebell Body", "apply_mode": "url", "direct_contact": True,
                              "external_apply_url": "https://app.sideshift.app/jobs/x", "apply_email": "hi@kb.app",
                              "source_platform": "sideshift", "is_sourced": True, "pr_value_usd": 300})
    pub = public_card(card)
    assert pub["direct_contact"] is True and "apply_email" not in pub


def test_direct_contact_gigs_rank_first_on_the_board():
    from services.paid_gigs_board import rank_cards
    cards = [
        {"id": 1, "posted_at": "2026-11-04T10:00:00Z", "is_match": True},
        {"id": 2, "posted_at": "2026-10-28T10:00:00Z", "direct_contact": True},
        {"id": 3, "posted_at": "2026-11-03T10:00:00Z", "direct_contact": True, "already_applied": True},
        {"id": 4, "posted_at": "2026-11-02T10:00:00Z", "direct_contact": True, "is_match": True},
    ]
    assert [c["id"] for c in rank_cards(cards, now=NOW)] == [4, 2, 1, 3]


def test_board_path_end_to_end_from_a_db_row():
    """Regression: the live-feed query didn't select brand_email, so no card ever got the badge."""
    import inspect
    from datetime import datetime as dt
    import opportunities_routes as orr
    from services.paid_gigs_board import board_cards, public_card, rank_cards

    assert "brand_email" in inspect.getsource(orr.fetch_live_opportunity_cards).split("FROM opportunities")[0]
    row = {"id": 1758, "brand_name": "Glosshouse", "brand_category": "beauty", "brand_logo_url": None,
           "brand_website": "https://glosshouse.com", "brand_email": "hello@glosshouse.com",
           "product_name": "Glosshouse UGC", "campaign_description": "2 TikToks a week", "pr_value_usd": 300,
           "creator_count_range": None, "shipping_regions": ["US"], "follower_ranges": [], "content_types": ["UGC"],
           "creator_niches": ["beauty"], "spots_total": 1, "spots_filled": 0, "closes_at": None,
           "created_at": dt(2026, 11, 4, 10), "published_at": dt(2026, 11, 4, 10), "listing_brief": None,
           "additional_notes": "[scanner:sideshift:abc] | source=sideshift | apply_url=https://app.sideshift.app/jobs/abc"
                               " | contact_checked=2026-11-04 | contact=website"}
    plain = {**row, "id": 1700, "brand_name": "Smudge", "brand_email": "sourced@newcollab.co",
             "additional_notes": "[scanner:sideshift:def] | apply_url=https://app.sideshift.app/jobs/def"}
    cards = []
    for r in (plain, row):
        serialized, _ = orr._opportunity_card(r, {"beauty"}, set())
        assert serialized["apply_email"] is None  # never in list payloads
        cards.append(serialized)
    ranked = rank_cards(board_cards(cards))
    assert ranked[0]["id"] == 1758 and ranked[0]["direct_contact"] is True
    pub = public_card(ranked[0])
    assert pub["direct_contact"] is True and "hello@glosshouse.com" not in str(pub)
    assert public_card(ranked[1])["direct_contact"] is False
