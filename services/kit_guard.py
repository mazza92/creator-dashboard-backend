"""Every pitch carries the creator's Newcollab kit: it's the only way we see a brand looking at them.

Why (Nov 2026): 313 of 975 pitches sent in 14 days had no kit link. 95% were creators with no kit at all
(no portfolio posts, no slug), so no link was ever written and "<brand> opened your kit" could never happen.

The guard runs right before a pitch leaves (Autopilot, Send for me): it builds the kit from the creator's
scraped posts when it's empty, publishes it, and adds the link above the sign-off when the pitch lacks it.
track_kit_links then gives the link the brand's ?ref= token.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

KIT_LINK_RE = re.compile(r"https?://(?:www\.)?newcollab\.co/kit/[A-Za-z0-9_.\-]+", re.I)
SIGNOFF_RE = re.compile(r"^\s*(thanks|thank you|best|cheers|warmly|xx|x|kind regards|regards|talk soon|love)\b[^\n]*$",
                        re.I | re.M)
_SLUG_OK = re.compile(r"^[A-Za-z0-9_.\-]{2,60}$")


def kit_url(slug: str) -> str:
    from services.polly_kit import public_kit_url
    return public_kit_url(slug) or f"https://newcollab.co/kit/{slug}"


def has_kit_link(body: Optional[str]) -> bool:
    return bool(KIT_LINK_RE.search(body or ""))


def add_kit_link(body: str, url: str) -> str:
    """Put 'My media kit: <url>' just above the sign-off (or at the end). No-op when a kit link is there."""
    body = body or ""
    if has_kit_link(body) or not url:
        return body
    line = f"My media kit (rates, stats and recent videos): {url}"
    # A sign-off is a short line ("Thanks," / "Best, Ana"), not an opener like "Love your new launch...".
    matches = [m for m in SIGNOFF_RE.finditer(body) if len(m.group(0).strip()) <= 30]
    if matches:
        at = matches[-1].start()
        head = body[:at].rstrip("\n")
        return f"{head}\n\n{line}\n\n{body[at:].lstrip(chr(10))}"
    return f"{body.rstrip()}\n\n{line}\n"


def ensure_kit(conn, creator_id: int) -> Optional[str]:
    """The creator's kit slug, with a kit worth opening behind it. None only when we have nothing to build from."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT c.id, c.user_id, c.username, c.kit_slug, COALESCE(c.kit_published, FALSE) AS published,
                          COALESCE(c.subscription_tier, 'free') AS tier,
                          (SELECT COUNT(*) FROM portfolio_posts p WHERE p.creator_id = c.id) AS posts
                   FROM creators c WHERE c.id = %s""", (creator_id,))
    row = dict(cur.fetchone() or {})
    if not row:
        return None
    slug = (row.get("kit_slug") or "").strip()
    if not slug:
        # Lowercase: kit links are written lowercase (polly_kit.public_kit_url) and the kit page matches exactly.
        candidate = (row.get("username") or "").strip().lstrip("@").lower()
        if candidate and _SLUG_OK.match(candidate):
            cur.execute("""SELECT 1 FROM creators WHERE (LOWER(kit_slug) = %s OR LOWER(username) = %s) AND id <> %s
                           LIMIT 1""", (candidate, candidate, creator_id))
            if not cur.fetchone():
                cur.execute("UPDATE creators SET kit_slug = %s WHERE id = %s AND kit_slug IS NULL",
                            (candidate, creator_id))
                slug = candidate
    if not slug:
        return None
    if int(row.get("posts") or 0) == 0:
        _fill_from_scrape(conn, row)
    if not row.get("published"):
        cur.execute("UPDATE creators SET kit_published = TRUE WHERE id = %s", (creator_id,))
    conn.commit()
    return slug


def _fill_from_scrape(conn, row: Dict[str, Any]) -> None:
    try:
        from pr_ready_routes import _load_scrape
        from services.pr_ready import auto_fill_kit_from_scrape

        scrape = _load_scrape(conn, row["user_id"])
        if not scrape:
            return
        auto_fill_kit_from_scrape(conn, row["id"], scrape, publish=True, is_pro=row.get("tier") == "pro",
                                  max_posts=9 if row.get("tier") == "pro" else 3)
    except Exception as err:
        print(f"[kit guard] auto-fill skipped creator={row.get('id')}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass


def with_kit(conn, creator_id: int, body: str) -> str:
    """The pitch body with the creator's kit link in it (built and published first if needed)."""
    if has_kit_link(body):
        return body
    try:
        slug = ensure_kit(conn, creator_id)
    except Exception as err:
        print(f"[kit guard] ensure_kit failed creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        slug = None
    return add_kit_link(body, kit_url(slug)) if slug else body
