"""The Gmail connect link asks for exactly the scopes on the verified consent screen, nothing carried over."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import services.polly_gmail as pg


def test_connect_link_requests_only_verified_scopes():
    cfg = {"client_id": "c", "client_secret": "s", "redirect_uri": "https://api.newcollab.co/cb"}
    with patch.object(pg, "_config", return_value=cfg), patch.object(pg, "make_state", return_value="st"):
        q = parse_qs(urlparse(pg.auth_url(7)).query)
    assert set(q["scope"][0].split()) == {
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.send",
    }
    assert "include_granted_scopes" not in q
