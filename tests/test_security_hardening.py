"""Security regressions from the Nov 2026 audit: each test is an attack that used to work."""
import base64
import json
from unittest import mock

import pytest

from app import app as flask_app, _clean_person_name, _public_creator
import media_proxy_routes as mp
import social_verification_routes as svr
from services import admin_auth


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_anonymous_cannot_list_creators(client):
    assert client.get("/creators").status_code == 401


def test_public_creator_drops_private_fields():
    row = {"id": 1, "username": "maya", "shipping_address": "1 St", "shipping_phone": "+1", "email": "a@b.c",
           "social_oauth_token": "t", "stripe_customer_id": "cus", "niche": "beauty"}
    assert _public_creator(row) == {"id": 1, "username": "maya", "niche": "beauty"}


def test_onboarding_ignores_user_id_and_email_in_body(client):
    resp = client.post("/profile/onboarding", json={"user_id": 1, "email": "team@newcollab.co", "bio": "x",
                                                     "username": "x"})
    assert resp.status_code == 401


def test_admin_email_account_is_not_admin():
    assert not admin_auth.session_user_is_admin({"id": 9, "email": "team@newcollab.co"})
    with mock.patch.dict("os.environ", {"ADMIN_USER_IDS": "9"}):
        assert admin_auth.session_user_is_admin({"id": 9, "email": "x@y.z"})


def test_vercel_cron_user_agent_is_not_auth():
    with mock.patch.dict("os.environ", {"CRON_SECRET": "s3cret", "ADMIN_TOKEN": ""}):
        with flask_app.test_request_context("/api/polly/cron/alerts", headers={"User-Agent": "vercel-cron/1.0"}):
            assert not admin_auth.cron_request_ok()
        with flask_app.test_request_context("/api/polly/cron/alerts", headers={"Authorization": "Bearer s3cret"}):
            assert admin_auth.cron_request_ok()
        with flask_app.test_request_context("/x", headers={"Authorization": "Bearer newcollab-cron-2026"}):
            assert not admin_auth.cron_request_ok()


def test_email_cron_routes_need_the_secret(client):
    with mock.patch.dict("os.environ", {"CRON_SECRET": "s3cret", "ADMIN_TOKEN": ""}):
        assert client.post("/api/cron/send-reengagement").status_code == 401
        assert client.post("/api/cron/reset-weekly-email-counter").status_code == 401


def test_credentialed_cors_only_for_our_sites(client):
    resp = client.get("/profile", headers={"Origin": "https://evil.example"})
    assert resp.headers.get("Access-Control-Allow-Origin") != "https://evil.example"
    assert resp.headers.get("Access-Control-Allow-Credentials") != "true"
    ok = client.get("/profile", headers={"Origin": "https://app.newcollab.co"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "https://app.newcollab.co"
    assert ok.headers.get("X-Content-Type-Options") == "nosniff"


def test_pusher_auth_only_for_own_channel(client):
    assert client.post("/pusher/auth", data={"channel_name": "private-user-42-creator",
                                             "socket_id": "1.2"}).status_code == 401
    import app as app_module
    with mock.patch.object(app_module, "session", {"user_id": 7}):
        resp = client.post("/pusher/auth", data={"channel_name": "private-user-42-creator", "socket_id": "1.2"})
    assert resp.status_code == 403


def test_media_proxy_rejects_userinfo_tricks():
    assert mp.is_social_cdn_url("https://scontent.cdninstagram.com/v/x.jpg")
    assert not mp.is_social_cdn_url("http://x.cdninstagram.com:a@169.254.169.254/latest/meta-data")
    assert not mp.is_public_post_url("https://www.tiktok.com:a@evil.example/x")


def test_social_oauth_state_must_be_signed():
    forged = base64.urlsafe_b64encode(json.dumps({"user_id": 5, "creator_id": 5}).encode()).decode()
    assert svr._decode_tiktok_oauth_state(forged) == {}
    good = svr._encode_oauth_state({"user_id": 5, "return_url": "https://app.newcollab.co/onboarding"})
    assert svr._decode_tiktok_oauth_state(good)["user_id"] == 5
    tampered = good.replace(good.split(".")[0], base64.urlsafe_b64encode(b'{"user_id": 6}').decode())
    assert svr._decode_tiktok_oauth_state(tampered) == {}
    evil = svr._encode_oauth_state({"user_id": 5, "return_url": "https://evil.example/x"})
    assert "return_url" not in svr._decode_tiktok_oauth_state(evil)


def test_signup_names_cannot_carry_links_or_markup():
    cleaned = _clean_person_name('<a href="https://evil.example">Verify now</a> www.evil.example')
    assert "<" not in cleaned and ">" not in cleaned and "http" not in cleaned and "www." not in cleaned
    assert _clean_person_name("  Maya  Lee ") == "Maya Lee"


def test_debug_routes_are_gone(client):
    assert client.get("/test-supabase").status_code == 404
    assert client.get("/debug/session").status_code == 404
    assert client.get("/callback?code=<script>").status_code == 404
