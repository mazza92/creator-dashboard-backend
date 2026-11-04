"""Two old accounts can share an email in different case: login takes the one whose password matches."""
from unittest import mock

import bcrypt

import app as app_module


def _row(uid, pw):
    return {"id": uid, "password": bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode(), "role": "creator",
            "country": "US"}


def _login(rows, password):
    conn = mock.MagicMock()
    cur = conn.cursor.return_value
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = None
    captured = {}

    def fake_after(*a, **k):
        captured["ok"] = True
        raise RuntimeError("stop after credentials")  # we only test the credential choice

    with mock.patch.object(app_module, "get_db_connection", return_value=conn), \
            mock.patch.object(app_module, "should_block_auth_for_region", side_effect=fake_after):
        client = app_module.app.test_client()
        try:
            res = client.post("/login", json={"email": "Lexie@Gmail.com", "password": password})
        except RuntimeError:
            res = None
    return res, captured, cur


def test_matching_password_picks_the_right_account():
    rows = [_row(889, "old-pass"), _row(1146, "new-pass")]
    res, captured, cur = _login(rows, "new-pass")
    assert captured.get("ok")  # got past the password check


def test_wrong_password_still_rejected():
    rows = [_row(889, "old-pass"), _row(1146, "new-pass")]
    res, captured, _cur = _login(rows, "nope")
    assert not captured.get("ok")
    assert res is not None and res.status_code == 401
