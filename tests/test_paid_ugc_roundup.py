from datetime import datetime, timedelta, timezone

import services.paid_ugc_roundup as roundup

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def _card(i, days_old, pay=0, brand="Glow Co", headline="Glow Co skincare UGC video"):
    return {
        "id": i,
        "brand_name": brand,
        "headline": headline,
        "pay_label": f"${pay}" if pay else "",
        "pr_value_usd": pay,
        "posted_at": (NOW - timedelta(days=days_old)).isoformat(),
        "source_label": "SideShift",
    }


def test_title_cleans_cut_and_generic_headlines():
    assert roundup._title("Play Flip Diving, Flip Master &", "MotionVolt") == "Play Flip Diving, Flip Master"
    assert roundup._title("Collect UGC Creator ($1M+ Paid To", "Collect") == "Collect UGC Creator"
    assert roundup._title("HIRING \U0001F6A8 Fashion UGC Creators \u2014", "") == "Fashion UGC Creators"
    assert roundup._title("Creator", "TLDL") == "TLDL UGC campaign"
    assert roundup._title("Snazo", "Snazo") == "Snazo UGC campaign"
    assert roundup._title("", "") == "Paid UGC brief"


def test_context_newest_first_then_top_paying_from_this_week():
    cards = [_card(i, days_old=i * 0.5, pay=100 + i) for i in range(12)]
    cards.append(_card(99, days_old=3, pay=5000))
    cards.append(_card(98, days_old=20, pay=9000))
    ctx = roundup.roundup_context(cards, limit=10, now=NOW)

    assert [it["id"] for it in ctx["items"]] == list(range(10))
    assert ctx["top_items"][0]["id"] == 99
    assert 98 not in [it["id"] for it in ctx["top_items"]]
    assert ctx["total"] == 14
    assert ctx["top_pay"] == 9000
    assert ctx["more"] == 14 - 10 - len(ctx["top_items"])


def test_subject_leads_with_new_count_and_top_pay():
    assert roundup.roundup_subject({"new_this_week": 155, "top_pay": 5000, "total": 216}) == \
        "155 new paid UGC offers this week (up to $5,000)"
    assert roundup.roundup_subject({"new_this_week": 0, "top_pay": None, "total": 40}) == \
        "40 paid UGC offers open now"


def test_local_dev_urls_never_reach_inboxes(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    assert roundup._public_url("FRONTEND_URL", "https://newcollab.co") == "https://newcollab.co"
    monkeypatch.setenv("FRONTEND_URL", "https://staging.newcollab.co")
    assert roundup._public_url("FRONTEND_URL", "https://newcollab.co") == "https://staging.newcollab.co"


def test_email_uses_polly_shell_without_source_names():
    ctx = roundup.roundup_context([_card(1, 0.2, pay=300), _card(2, 1, brand="", pay=0)], now=NOW)
    mail = roundup.roundup_email("Ana", ctx, is_pro=False, unsubscribe_url="https://x/unsub")
    html = mail["html"]
    assert ">Polly</p>" in html
    assert "Hey Ana," in html
    assert "Polly, your Newcollab manager" in html
    assert "Your paid UGC radar: 2 new offers this week" in html
    assert "Glow Co skincare UGC video" in html
    assert "Brand confidential" in html
    assert "SideShift" not in html
    assert "https://x/unsub" in html
    assert "upgrade=pro" in html
    pro_html = roundup.roundup_email("Ana", ctx, is_pro=True)["html"]
    assert "upgrade=pro" not in pro_html
    assert "Free accounts" not in pro_html
