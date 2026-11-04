"""Monday check-in: one-tap pitch check-ins when they pitched, ready-to-pitch brands either way, real numbers."""
from datetime import datetime, timedelta, timezone
from unittest import mock

from jinja2 import Environment, FileSystemLoader

from services import monday_checkin as mc

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)


def _ctx(checks=True, gig=True, trial=False, credits=2):
    pitches = [{"brand_id": 3, "brand_name": "Saltwell Skin", "occurred_at": NOW - timedelta(days=6)},
               {"brand_id": 4, "brand_name": "Kindle Candle Co", "occurred_at": NOW - timedelta(days=5)}] if checks else []
    with mock.patch.object(mc, "_niches", return_value=["skincare"]), \
            mock.patch.object(mc, "pitches_to_check", return_value=pitches), \
            mock.patch.object(mc, "paid_gig", return_value={"id": 9, "name": "Glowly", "badge": "PAID UGC",
                                                            "badge_kind": "gold", "line": "Budget $500 · open now"} if gig else None), \
            mock.patch.object(mc, "brand_picks", side_effect=lambda cur, cid, n, limit=2: [
                {"id": 10 + i, "name": f"Brand {i}", "badge": "GIFTED PR", "badge_kind": "grey", "line": "x"} for i in range(limit)]), \
            mock.patch.object(mc, "brands_waiting", return_value=41), \
            mock.patch.object(mc, "pitched_last_week", return_value=3), \
            mock.patch("services.checkin_links.one_tap_url", side_effect=lambda *a, **k: f"https://api/checkin?{k.get('brand_id')}"):
        return mc.build_context(mock.MagicMock(), 7, "maya rose", credits, now=NOW, trial=trial)


def _render(ctx):
    env = Environment(loader=FileSystemLoader("templates"))
    return env.get_template("lifecycle/21_weekly_digest.html").render(
        unsubscribe_url="u", preferences_url="p", subject=ctx["subject_override"], **ctx)


def test_pitched_last_week_gets_the_checkin():
    ctx = _ctx()
    assert ctx["variant"] == "checkin"
    assert ctx["subject_override"] == "Did Saltwell Skin reply, Maya?"
    assert [c["name"] for c in ctx["checks"]] == ["Saltwell Skin", "Kindle Candle Co"]
    html = _render(ctx)
    for text in ("how did last week's pitches go", "They replied", "Email bounced", "No reply yet",
                 "Check in Gmail", "Glowly", "PAID UGC", "Open my pitch", "2 of 3 free pitches left",
                 "41", "Put Polly on Autopilot", "$19/mo"):
        assert text in html, text
    assert "Reply Chance" not in html and "Start 7 days free" not in html


def test_quiet_week_leads_with_brands_and_trial_when_eligible():
    ctx = _ctx(checks=False, trial=True, credits=0)
    assert ctx["variant"] == "quiet"
    assert ctx["subject_override"] == "A paid UGC job and 2 brands for you, Maya"
    html = _render(ctx)
    assert "They replied" not in html
    assert "Start 7 days free" in html and "Your 3 free pitches are used" in html
    assert len(ctx["picks"]) == 3 and ctx["picks"][0]["cta"] == "See the job"


def test_no_gig_three_brands():
    ctx = _ctx(checks=False, gig=False)
    assert len(ctx["picks"]) == 3 and all(p["cta"] == "Open my pitch" for p in ctx["picks"])
    assert ctx["subject_override"] == "3 skincare brands to pitch this week"
