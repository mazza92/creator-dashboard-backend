"""users.last_seen_at: when a signed-in user last used the app.

users.last_login only moves on a password or Google login, and a session lasts 7 days, so a creator who comes back
while still signed in never shows up as returning. This stamps last_seen_at on any successful signed-in request,
at most once an hour per session (the hour is kept in the session, so no database read per request).
"""

import time

SEEN_EVERY_SECONDS = 3600
_SESSION_KEY = 'last_seen_ts'


def should_touch(session, now=None):
    """True when this signed-in session hasn't stamped last_seen_at in the last hour."""
    if not session.get('user_id'):
        return False
    now = time.time() if now is None else now
    try:
        last = float(session.get(_SESSION_KEY) or 0)
    except (TypeError, ValueError):
        last = 0
    return not last or now - last >= SEEN_EVERY_SECONDS


def touch_last_seen(session, get_conn, logger=None, now=None):
    """Stamps users.last_seen_at for the session's user if due. Never raises: tracking must not break a request."""
    if not should_touch(session, now):
        return False
    now = time.time() if now is None else now
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_seen_at = NOW() WHERE id = %s", (session.get('user_id'),))
        if not getattr(conn, 'autocommit', False):
            conn.commit()
        session[_SESSION_KEY] = now
        return True
    except Exception as e:  # noqa: BLE001
        if logger:
            logger.warning(f"last_seen_at update failed: {e}")
        # Don't retry on every request after a failure; try again next hour.
        session[_SESSION_KEY] = now
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
