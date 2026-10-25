"""Keep the paid-gig feed to offers posted in the last 20 days.

A listing's own post date is what counts. When a scraper does not send one, the
day we published it is the clock. Older live scanner gigs are paused, not deleted,
so the next scrape can fill the feed with fresh ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

FRESH_DAYS = 20

_RELATIVE = re.compile(
    r"(\d+)\s*(minutes?|hours?|days?|weeks?|months?)\s+ago",
    re.I,
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_posted_at(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort posted date. Unknown text returns None so we do not drop it."""
    if value is None or value == "":
        return None
    now = _aware(now or datetime.now(timezone.utc))
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, (int, float)):
        secs = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return parse_posted_at(int(text), now)
    relative = _RELATIVE.search(text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        if unit.startswith("minute"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("hour"):
            delta = timedelta(hours=amount)
        elif unit.startswith("week"):
            delta = timedelta(weeks=amount)
        elif unit.startswith("month"):
            delta = timedelta(days=30 * amount)
        else:
            delta = timedelta(days=amount)
        return now - delta
    cleaned = text.replace("Z", "+00:00")
    try:
        return _aware(datetime.fromisoformat(cleaned))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return _aware(datetime.strptime(text[: len(fmt) + 8], fmt))
        except ValueError:
            continue
    return None


def posted_too_old(value: Any, now: Optional[datetime] = None, days: int = FRESH_DAYS) -> bool:
    posted = parse_posted_at(value, now)
    if posted is None:
        return False
    now = _aware(now or datetime.now(timezone.utc))
    return posted < now - timedelta(days=days)


def gig_posted_too_old(raw: dict, now: Optional[datetime] = None, days: int = FRESH_DAYS) -> bool:
    for key in ("posted_at", "date_posted", "published_at", "created_at", "createdAt", "postedAt"):
        if key in raw and posted_too_old(raw.get(key), now, days):
            return True
    return False


def pause_stale_paid_gigs(conn, days: int = FRESH_DAYS) -> int:
    """Pause live scanner gigs that have been up longer than `days`. Commits."""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE opportunities
        SET status = 'paused',
            closes_at = NOW(),
            additional_notes = CASE
                WHEN COALESCE(additional_notes, '') ILIKE '%%recycled=stale_20d%%'
                    THEN additional_notes
                ELSE COALESCE(additional_notes, '') || ' | recycled=stale_20d'
            END
        WHERE status = 'live'
          AND additional_notes ILIKE '%%[scanner:%%'
          AND COALESCE(published_at, created_at) < NOW() - (%s * INTERVAL '1 day')
        RETURNING id
        """,
        (int(days),),
    )
    rows = cursor.fetchall() or []
    conn.commit()
    cursor.close()
    return len(rows)


def parse_gig_deadline(value: Any, now: Optional[datetime] = None):
    """Scanner deadline -> date, or None for text like 'ASAP', 'Rolling', 'Ongoing' or a past date.
    Postgres rejects the whole row on a bad date, so anything unclear is dropped, not guessed."""
    from datetime import date

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        if not re.search(r"\d", text):
            return None
        try:
            from dateutil import parser as date_parser
            parsed = date_parser.parse(text, fuzzy=False).date()
        except (ValueError, OverflowError, TypeError):
            return None
    today = (now or datetime.now(timezone.utc)).date()
    if parsed < today or parsed.year > today.year + 2:
        return None
    return parsed
