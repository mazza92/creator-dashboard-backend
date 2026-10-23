"""How to reach a Gifting Radar brand: an email on their website, then Hunter, then a TikTok DM."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
from psycopg2.extras import RealDictCursor

HUNTER_URL = "https://api.hunter.io/v2/domain-search"
CONTACT_PATHS = ("", "/pages/contact", "/pages/contact-us", "/contact", "/contact-us", "/pages/influencers",
                 "/pages/collab", "/pages/ambassadors", "/pages/partnerships")
PAGE_TIMEOUT = 6
HUNTER_TIMEOUT = 10
RECHECK = timedelta(days=30)
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0 Safari/537.36"}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_FILE_TLDS = ("png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js")
_TIER1 = ("influencer", "creator", "ugc", "ambassador", "partner", "collab", "affiliate", "marketing", "social")
_TIER2 = ("pr", "press", "media")
_TIER3 = ("hello", "hi", "hey", "info", "contact", "team", "community", "support", "care", "help")
_BAD = ("noreply", "no-reply", "donotreply", "privacy", "legal", "careers", "jobs", "hr", "billing", "invoice",
        "orders", "order", "shipping", "wholesale", "returns", "accessibility", "unsubscribe", "abuse", "dmca",
        "webmaster", "postmaster", "accounts", "investor")
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


def site_root(host: str) -> str:
    """'shop.brand.co.uk' -> 'brand.co.uk', 'www.brand.com' -> 'brand.com'."""
    parts = (host or "").lower().strip(".").removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_of(website: Any) -> str:
    raw = str(website or "").strip()
    if not raw:
        return ""
    host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or ""
    return site_root(host) if "." in host else ""


def email_rank(email: str) -> int:
    """Lower is better: creator/partnership inboxes, then PR, then general. 999 = never use."""
    local = email.split("@")[0].lower()
    if any(local == b or local.startswith(b + ".") or local.startswith(b + "-") for b in _BAD):
        return 999
    for tier, words in ((0, _TIER1), (100, _TIER2), (200, _TIER3)):
        for i, word in enumerate(words):
            if word in re.split(r"[._+-]", local) or (tier == 0 and word in local):
                return tier + i
    return 300


def best_email(emails, root: str) -> str:
    """Best inbox on the brand's own domain from raw page text matches."""
    seen = {}
    for raw in emails:
        email = raw.strip(".").lower()
        tld = email.rsplit(".", 1)[-1]
        if tld in _FILE_TLDS or domain_of(email.split("@")[1]) != root:
            continue
        rank = email_rank(email)
        if rank < 999:
            seen[email] = rank
    return min(seen, key=lambda e: (seen[e], len(e))) if seen else ""


def _page_emails(url: str) -> list:
    try:
        resp = requests.get(url, headers=_UA, timeout=PAGE_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            return []
        return _EMAIL_RE.findall(resp.text.replace("%40", "@").replace("&#64;", "@"))
    except requests.RequestException:
        return []


def website_email(domain: str) -> str:
    if not domain:
        return ""
    urls = [f"https://{domain}{path}" for path in CONTACT_PATHS]
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        found = [e for batch in pool.map(_page_emails, urls) for e in batch]
    return best_email(found, domain)


def hunter_lookup(*, company: str = "", domain: str = "") -> Tuple[str, str, int]:
    """(domain, best email, its rank) from Hunter's domain search. Empty strings when unavailable."""
    key = os.getenv("HUNTER_API_KEY")
    if not key or not (company or domain):
        return "", "", 999
    params = {"api_key": key, "limit": 10, **({"domain": domain} if domain else {"company": company})}
    try:
        resp = requests.get(HUNTER_URL, params=params, timeout=HUNTER_TIMEOUT)
        if resp.status_code >= 400:
            return "", "", 999
        data = resp.json().get("data") or {}
    except (requests.RequestException, ValueError):
        return "", "", 999
    found = domain_of(data.get("domain")) or domain
    usable = [e for e in data.get("emails") or []
              if e.get("value") and int(e.get("confidence") or 0) >= 30 and email_rank(e["value"]) < 999]
    usable.sort(key=lambda e: (hunter_rank(e), -int(e.get("confidence") or 0)))
    if not usable:
        return found, "", 999
    return found, usable[0]["value"].lower(), hunter_rank(usable[0])


_ROLE_WORDS = ("influencer", "creator", "partnership", "social", "community", "brand", "pr ", "public relations",
               "communications", "marketing")


def hunter_rank(entry: Dict[str, Any]) -> int:
    """email_rank, lifted for people whose role is marketing / PR / partnerships."""
    rank = email_rank(entry.get("value") or "")
    role = f"{entry.get('position') or ''} {entry.get('department') or ''} ".lower()
    if any(word in role for word in _ROLE_WORDS):
        rank = min(rank, 50)
    return rank


def _service_inbox(email: str) -> bool:
    return email.split("@")[0].lower() in ("support", "care", "help", "customercare", "customerservice", "service")


def _load(conn, discovered_brand_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT db.id, db.name, db.tiktok_handle, db.contact_email, db.contact_source,
                          db.contact_checked_at, COALESCE(db.website, pb.website) AS website
                   FROM discovered_brands db LEFT JOIN pr_brands pb ON pb.id = db.brand_id
                   WHERE db.id = %s""", (discovered_brand_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def find_contact(conn, discovered_brand_id: int, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """{"name", "email", "source", "website", "tiktok_handle"}; cached on the brand for RECHECK."""
    now = now or datetime.now(timezone.utc)
    row = _load(conn, discovered_brand_id)
    if not row:
        return None
    checked = row.get("contact_checked_at")
    if checked and now - checked < RECHECK:
        return {"name": row["name"], "email": row.get("contact_email") or "", "source": row.get("contact_source"),
                "website": row.get("website"), "tiktok_handle": row.get("tiktok_handle")}
    domain = domain_of(row.get("website"))
    hunter_email, hunter_score, asked_hunter = "", 999, False
    if not domain:
        domain, hunter_email, hunter_score = hunter_lookup(company=row["name"])
        asked_hunter = True
    email, source = website_email(domain), "website"
    if not email or _service_inbox(email):
        if not asked_hunter and domain:
            _, hunter_email, hunter_score = hunter_lookup(domain=domain)
        if hunter_email and (not email or hunter_score < 200):
            email, source = hunter_email, "hunter"
    if not email:
        source = "tiktok_dm" if row.get("tiktok_handle") else None
    website = row.get("website") or (f"https://{domain}" if domain else None)
    cur = conn.cursor()
    cur.execute("""UPDATE discovered_brands SET contact_email = %s, contact_source = %s, contact_checked_at = %s,
                     website = COALESCE(website, %s), contact_requests = contact_requests + 1,
                     status = CASE WHEN %s AND status IN ('new', 'enriching') THEN 'ready'
                                   WHEN status = 'new' THEN 'enriching' ELSE status END
                   WHERE id = %s""",
                (email or None, source, now, website, bool(email), discovered_brand_id))
    if website:
        cur.execute("""UPDATE pr_brands SET website = COALESCE(NULLIF(website, ''), %s)
                       WHERE source = 'tiktok_radar'
                         AND id = (SELECT brand_id FROM discovered_brands WHERE id = %s)""",
                    (website, discovered_brand_id))
    conn.commit()
    return {"name": row["name"], "email": email, "source": source, "website": website,
            "tiktok_handle": row.get("tiktok_handle")}


def _k(n: Any) -> str:
    n = float(n or 0)
    return f"{n / 1000:.1f}K".replace(".0K", "K") if n >= 1000 else str(int(n))


def dm_text(brand_name: str, niche: str, *, first_name: str = "", followers: int = 0, median_views: int = 0,
            kit_url: str = "") -> str:
    """Short cold DM: what we noticed, who they are in one line, an easy yes/no ask, the kit."""
    niche = (niche or "").strip() or "lifestyle"
    who = f"I'm {first_name.strip()}, a {niche} creator" if first_name.strip() else f"I'm a {niche} creator"
    stats = []
    if followers:
        stats.append(f"{_k(followers)} followers")
    if median_views:
        stats.append(f"around {_k(median_views)} views a video")
    if stats:
        who += f" ({' and '.join(stats)})"
    lines = [
        f"Hi {brand_name} team! I've seen you sending PR to {niche} creators on TikTok lately, "
        "and I'd love to be part of the next round.",
        f"{who}, and my audience is always asking what I'm trying next.",
        f"Would you be open to sending me something to try? I'd film an honest {niche} video for my page.",
    ]
    if kit_url:
        lines.append(f"My media kit: {kit_url}")
    return "\n\n".join(lines)
