"""Directory access for creators Newcollab hasn't approved: 10 unlocks in total free, 500 a month on Pro, pitches
that never mention Newcollab, no Polly / Autopilot, no free week, and paying never approves."""
import json
from unittest import mock

import pytest

from services import directory_tier as dt


class Cur:
    def __init__(self, status="pending", total=0, month=0):
        self.status, self.total, self.month, self.last = status, total, month, None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT approval_status"):
            self.last = None if self.status is None else {"approval_status": self.status}
        elif "date_trunc('month'" in s:
            self.last = {"n": self.month}
        elif s.startswith("SELECT COUNT(DISTINCT brand_id)"):
            self.last = {"n": self.total}
        else:
            raise AssertionError(s)

    def fetchone(self):
        return self.last


@pytest.mark.parametrize("status,expected", [("pending", True), ("rejected", True), ("approved", False),
                                             ("pro_approved", False)])
def test_who_gets_directory_access(status, expected):
    assert dt.is_directory(Cur(status), 7) is expected


def test_unknown_creator_is_not_directory():
    assert dt.is_directory(Cur(None), 7) is False


def test_free_is_10_in_total():
    b = dt.balance(Cur(total=7), 7, is_pro=False)
    assert b == {**b, "access": "directory", "limit": 10, "used": 7, "remaining": 3, "period": "total",
                 "is_unlimited": False, "tier": "free"}
    assert dt.at_limit(Cur(total=10), 7, is_pro=False) is True
    assert dt.at_limit(Cur(total=9), 7, is_pro=False) is False


def test_pro_is_500_a_month():
    b = dt.balance(Cur(total=900, month=68), 7, is_pro=True)
    assert (b["limit"], b["used"], b["remaining"], b["period"], b["tier"]) == (500, 68, 432, "month", "pro")
    assert dt.at_limit(Cur(month=500), 7, is_pro=True) is True


def test_scrub_removes_every_newcollab_line():
    body = ("Hi Glow team,\n\nI'd love to make a video.\n\n"
            "My media kit (rates, stats and recent videos): https://newcollab.co/kit/maya?ref=abc\n\n"
            "Thanks,\nMaya\nSent via Newcollab")
    out = dt.scrub(body)
    assert "newcollab" not in out.lower()
    assert out.startswith("Hi Glow team,") and "Maya" in out


def test_scrub_html_and_payload():
    html = ('<p>Hi Glow,</p><p>My kit: <a href="https://newcollab.co/kit/maya">kit</a></p>'
            '<img src="https://api.newcollab.co/track/open/1.png"/>')
    out = dt.scrub_payload({"pitches": {"short": {"body_html": html, "subject": "Video idea"}},
                            "media_kit_url": "https://newcollab.co/kit/maya"})
    assert "newcollab" not in json.dumps(out).lower()
    assert out["pitches"]["short"]["subject"] == "Video idea"
    assert "Hi Glow" in out["pitches"]["short"]["body_html"]


def test_pitch_proof_uses_social_profile_not_the_kit():
    import pr_crm_routes as R
    creator = {"username": "maya", "kit_published": True, "approval_status": "pending",
               "tiktok_handle": "maya", "id": 7}
    with mock.patch.object(R, "resolve_creator_social",
                           return_value={"platform_label": "TikTok", "url": "https://www.tiktok.com/@maya",
                                         "at_handle": "@maya", "handle": "maya", "platform": "tiktok"}):
        proof = R.build_pitch_proof(creator, creator_id=7, brand_id=3)
    assert proof["kind"] == "social" and "newcollab" not in json.dumps(proof).lower()
    approved = R.build_pitch_proof({**creator, "approval_status": "approved"}, creator_id=7, brand_id=3)
    assert approved["kind"] == "kit"


def test_no_free_week_for_directory_creators():
    from services import quota_trial_test as qt
    with mock.patch("services.start_choice.picked_free", return_value=False), \
            mock.patch.object(dt, "is_directory", return_value=True), \
            mock.patch("services.creator_offers.has_trial_offer", return_value=True):
        assert qt.checkout_trial_eligible(object(), 7) is False


def test_polly_and_autopilot_are_blocked_for_directory_creators():
    from app import app
    with mock.patch("pr_crm_routes.get_creator_id_from_session", return_value=7), \
            mock.patch.object(dt, "is_directory_id", return_value=True):
        client = app.test_client()
        r1 = client.get("/api/polly/thread")
        r2 = client.get("/api/polly/autopilot/status")
    assert r1.status_code == 403 and r1.get_json()["error"] == "directory_access"
    assert r2.status_code == 403


def test_crons_are_never_blocked():
    from app import app
    with app.test_request_context("/api/polly/cron/alerts"):
        from routes.polly import directory_block
        with mock.patch.object(dt, "is_directory_id", return_value=True), \
                mock.patch("pr_crm_routes.get_creator_id_from_session", return_value=7):
            assert directory_block() is None


def test_paying_no_longer_approves():
    import inspect
    import subscription_routes
    src = inspect.getsource(subscription_routes)
    start = src.index("Paying no longer approves a pending creator")
    block = src[start:src.index("SET approval_status = 'pro_approved'", start)]
    assert "if False:" in block  # the auto-approval on payment never runs


def test_dashboard_init_uses_the_directory_balance():
    import inspect
    import pr_crm_routes as R
    src = inspect.getsource(R.dashboard_init) if hasattr(R, "dashboard_init") else inspect.getsource(R)
    assert "directory_tier.balance(cursor, creator_id, is_pro)" in src
