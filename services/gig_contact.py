"""Direct contacts for scanned paid gigs (SideShift first).

Once a scanned gig is live, find the brand's own public contact so creators can pitch the brand directly instead
of only applying on the source platform. We never take contacts from the source platform itself: the gig only
tells us the brand's name. From there:
  1. the brand's website: the gig's own (if it isn't a job board), else the App Store developer site, since most of
     these posters are new apps, else Hunter's company lookup;
  2. an email on that website (contact / support / partnership pages; generic and support inboxes are fine here);
  3. Hunter's domain search when the site has none;
  4. Hunter's verifier: an invalid address is dropped.
Found: brand_email holds the real inbox and the gig gets a "direct contact" badge. Not found: nothing changes and
the gig keeps sending creators to the source platform. Every gig is checked once, then again after RECHECK.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

ENRICH_SOURCES = ("sideshift",)
PLACEHOLDER_EMAILS = ("sourced@newcollab.co", "mahery@newcollab.co")
RECHECK = timedelta(days=30)
APPSTORE_URL = "https://itunes.apple.com/search"
PER_RUN = 4
RUN_BUDGET_SECONDS = 40
_CHECKED = re.compile(r"\|\s*contact_checked=(\d{4}-\d{2}-\d{2})")
_CONTACT = re.compile(r"\|\s*contact=([a-z_]+)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def name_matches(brand: str, *candidates: str) -> bool:
    """'Kettlebell Body' vs 'Kettlebell Body: Home Workouts' -> True. Every brand word must appear."""
    words = [w for w in _norm(brand).split() if len(w) > 1 and w not in ("app", "the", "inc", "llc", "ltd", "co")]
    if not words:
        return False
    for cand in candidates:
        hay = f" {_norm(cand)} "
        if all(f" {w} " in hay or (len(w) > 4 and w in hay) for w in words):
            return True
    return False


def appstore_site(name: str, http: Callable = requests.get) -> str:
    """The developer's website from the App Store listing whose app or seller name matches the brand."""
    try:
        resp = http(APPSTORE_URL, params={"term": name, "entity": "software", "limit": 5}, timeout=8)
        if resp.status_code >= 400:
            return ""
        results = resp.json().get("results") or []
    except (requests.RequestException, ValueError):
        return ""
    from services.polly_gigs import public_brand_site

    for app in results:
        if name_matches(name, app.get("trackName") or "", app.get("sellerName") or ""):
            site = public_brand_site(app.get("sellerUrl"))
            if site:
                return site
    return ""


def gig_source(notes: str) -> str:
    m = re.search(r"\[scanner:([^:\]]+)", notes or "")
    return (m.group(1) if m else "").lower()


def direct_contact_source(notes: str) -> str:
    """'website' / 'hunter' when enrichment found the brand's own inbox, else ''."""
    m = _CONTACT.search(notes or "")
    return m.group(1) if m else ""


def find_gig_contact(conn, brand_name: str, website: str = "", http: Callable = requests.get) -> Dict[str, str]:
    """{"email", "source", "website"}; empty email when nothing usable was found."""
    from services import contact_repair as cr
    from services.polly_gigs import public_brand_site
    from services.radar_contact import domain_of, hunter_lookup, website_email

    site = public_brand_site(website) or appstore_site(brand_name, http=http)
    domain = domain_of(site)
    hunter_email, hunter_score = "", 999
    if not domain and cr._spend(conn, "gig_company"):
        domain, hunter_email, hunter_score = hunter_lookup(company=brand_name)
        site = site or (f"https://{domain}" if domain else "")
    email, source = (website_email(domain), "website") if domain else ("", "")
    if not email and not hunter_email and domain and cr._spend(conn, "gig_domain"):
        _, hunter_email, hunter_score = hunter_lookup(domain=domain)
    if not email and hunter_email:
        email, source = hunter_email, "hunter"
    if email:
        verdict = cr.hunter_verify(conn, email)
        if verdict == "invalid":
            email, source = "", ""
    return {"email": email or "", "source": source if email else "", "website": site or ""}


def due_gigs(conn, limit: int = PER_RUN, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Live scanned gigs from ENRICH_SOURCES without a real contact, never checked or checked over RECHECK ago."""
    from psycopg2.extras import RealDictCursor

    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, brand_name, brand_email, brand_website, additional_notes
        FROM opportunities
        WHERE status = 'live' AND additional_notes ILIKE ANY(%s)
          AND (COALESCE(brand_email, '') = '' OR LOWER(brand_email) = ANY(%s))
        ORDER BY COALESCE(published_at, created_at) DESC
        LIMIT 200
        """,
        ([f"%[scanner:{s}:%" for s in ENRICH_SOURCES], list(PLACEHOLDER_EMAILS)),
    )
    out = []
    for row in cur.fetchall():
        m = _CHECKED.search(row.get("additional_notes") or "")
        if m and now.date() - datetime.strptime(m.group(1), "%Y-%m-%d").date() < RECHECK:
            continue
        if not (row.get("brand_name") or "").strip():
            continue
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def save_result(conn, opp: Dict[str, Any], found: Dict[str, str], now: Optional[datetime] = None) -> None:
    now = now or _now()
    notes = _CHECKED.sub("", _CONTACT.sub("", opp.get("additional_notes") or "")).rstrip(" |")
    notes += f" | contact_checked={now.date().isoformat()}"
    if found.get("email"):
        notes += f" | contact={found['source']}"
    cur = conn.cursor()
    cur.execute(
        """UPDATE opportunities
           SET brand_email = COALESCE(%s, brand_email),
               brand_website = COALESCE(NULLIF(brand_website, ''), %s),
               additional_notes = %s
           WHERE id = %s""",
        (found.get("email") or None, found.get("website") or None, notes, opp["id"]),
    )
    conn.commit()


def enrich_due_gigs(conn, limit: int = PER_RUN, http: Callable = requests.get,
                    budget_seconds: float = RUN_BUDGET_SECONDS) -> Dict[str, int]:
    """Cron step: a few gigs per run so the job stays fast. Returns counts."""
    from services.contact_repair import ensure_tables

    ensure_tables(conn)
    started = time.monotonic()
    out = {"checked": 0, "found": 0}
    for opp in due_gigs(conn, limit=limit):
        if time.monotonic() - started > budget_seconds:
            break
        try:
            found = find_gig_contact(conn, opp["brand_name"], opp.get("brand_website") or "", http=http)
        except Exception as err:  # one bad site never stops the run
            print(f"[gig_contact] {opp['id']} {opp.get('brand_name')!r} failed: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
            found = {"email": "", "source": "", "website": ""}
        save_result(conn, opp, found)
        out["checked"] += 1
        out["found"] += int(bool(found.get("email")))
    return out
