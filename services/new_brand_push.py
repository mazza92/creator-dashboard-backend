"""New brand push: when a brand's roster goes live, email matching creators so it fills in 24h.

The For You boost only reaches creators who open the app. Inbound and spotlighted
rosters minted in the last few days get an email + Polly note to creators who fit
on niche, audience and shipping country, until the list has enough applicants for
Polly to pick a strong 5.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

from services.opportunity_gifted_pr import apply_url_for_brand
from services.pro_roster_placement import _as_dict, _country_code, creator_niches, internal_emails
from services.audience_fit import audience_mismatch
from services.roster_fit import REGION_CODES, RELATED, _as_list, brand_targets, niche_group
from services.roster_delivery import CONTENT_OVERDUE_DAYS

PUSH_KIND = "new_brand_push"
PUSH_WINDOW = timedelta(hours=72)
PUSH_PER_BRAND = 60
CREATOR_GAP = timedelta(days=3)
SEND_HOURS_UTC = range(12, 23)  # 8am New York to 11pm London
CANDIDATE_POOL = 3000


def push_enabled() -> bool:
    """Off unless NEW_BRAND_PUSH_ENABLED is set: creators aren't emailed when a new brand's roster goes live."""
    return (os.getenv("NEW_BRAND_PUSH_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def load_fresh_rosters(conn) -> List[Dict[str, Any]]:
    """Inbound or spotlighted rosters that went live recently and still need applicants."""
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT c.id AS campaign_id, b.id AS brand_id, b.slug, b.brand_name, b.category,
               b.niches AS brand_niches, b.description, b.hero_product, b.regions,
               (SELECT COUNT(*)::int FROM brand_pr_applications a
                 WHERE a.brand_id = b.id AND a.status IN ('review', 'ships', 'posted')) AS fill_count,
               (SELECT COUNT(*)::int FROM polly_email_log l
                 WHERE l.kind = %s AND l.brand_id = b.id AND l.ok) AS pushed
        FROM brand_pr_campaigns c
        JOIN pr_brands b ON b.id = c.brand_id
        WHERE c.status = 'active'
          AND b.slug IS NOT NULL
          AND COALESCE(b.status, 'published') = 'published'
          AND (
                (b.source_opportunity_id IS NOT NULL AND c.created_at > %s)
             OR (c.creator_spotlighted_at IS NOT NULL AND c.creator_spotlighted_at > %s)
          )
        ORDER BY c.created_at
        """,
        (PUSH_KIND, utc_now() - PUSH_WINDOW, utc_now() - PUSH_WINDOW),
    )
    return [
        dict(r) for r in cur.fetchall() or []
        if int(r["pushed"] or 0) < PUSH_PER_BRAND
        and not looks_like_test_brand(r)
    ]


_TEST_NAME_RE = re.compile(r"(?i)^\s*(test|testing|dummy|sample|demo|asdf|qa)\b|\b(test|dummy|sample) (product|brand)\b")


def looks_like_test_brand(row: Dict[str, Any]) -> bool:
    """Test rosters (e.g. "Testing" gifting "Test product") must never reach creators."""
    if row.get("is_test"):
        return True
    return any(_TEST_NAME_RE.search(str(row.get(k) or "")) for k in ("brand_name", "hero_product", "slug"))


def load_candidates(conn, brand_id: int) -> List[Dict[str, Any]]:
    """Emailable creators not yet on this brand, not pushed lately, not owing content."""
    from services.waitlist_gate import WAITLIST_EXCLUDE_SQL

    cur = _cursor(conn)
    cur.execute(
        f"""
        SELECT c.id AS creator_id, c.niche, c.creator_niches, c.shipping_address, u.country AS user_country,
               EXISTS (SELECT 1 FROM brand_pr_applications p WHERE p.creator_id = c.id) AS has_applied
        FROM creators c
        JOIN users u ON u.id = c.user_id
        WHERE u.email IS NOT NULL AND u.unsubscribed_at IS NULL
          AND LOWER(u.email) NOT LIKE '%%@newcollab.co'
          AND LOWER(u.email) <> ALL(%s)
          AND COALESCE(c.onboarding_survey->>'segment', '') <> 'is_brand'
          AND {WAITLIST_EXCLUDE_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM brand_pr_applications a WHERE a.creator_id = c.id AND a.brand_id = %s
          )
          AND NOT EXISTS (
              SELECT 1 FROM polly_email_log l
              WHERE l.creator_id = c.id AND l.kind = %s AND l.sent_at > %s
          )
          AND NOT EXISTS (
              SELECT 1 FROM brand_pr_applications late
              WHERE late.creator_id = c.id AND late.status = 'ships' AND late.delivered_at IS NULL
                AND late.shipped_at < NOW() - make_interval(days => %s)
          )
        ORDER BY has_applied DESC, c.id DESC
        LIMIT %s
        """,
        (internal_emails() or [""], brand_id, PUSH_KIND, utc_now() - CREATOR_GAP,
         CONTENT_OVERDUE_DAYS, CANDIDATE_POOL),
    )
    rows = []
    for raw in cur.fetchall() or []:
        row = dict(raw)
        row["country"] = _as_dict(row.pop("shipping_address", None)).get("country") or row.get("user_country")
        rows.append(row)
    return rows


def normalize_regions(raw: Any) -> List[str]:
    """Region names to the codes roster_fit knows. Anything unreadable means worldwide."""
    out = []
    for region in _as_list(raw):
        text = str(region).strip()
        low = text.lower()
        if low in ("worldwide", "global", "international", "any", "all"):
            return []
        if low in ("eu", "europe", "european union"):
            out.append("EU")
        elif text.upper() in REGION_CODES:
            out.append(text.upper())
        else:
            code = _country_code(text)
            if not code:
                return []
            out.append(code)
    return out


def match_score(niches: List[str], targets: Dict[str, Any]) -> int:
    """3 = main niche is the brand's, 2 = in their top 3, 1 = close niche, 0 = no fit."""
    want = set(targets.get("niches") or [])
    groups = [niche_group(n) for n in niches]
    if not want or not groups:
        return 0
    if groups[0] in want:
        return 3
    if any(g in want for g in groups[:3]):
        return 2
    if groups[0] and RELATED.get(groups[0], set()) & want:
        return 1
    return 0


def matching_creators(roster: Dict[str, Any], candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Best fit first: niche strength, then a confirmed shipping country, then past applicants."""
    targets = brand_targets(roster.get("category"), normalize_regions(roster.get("regions")), roster.get("brand_niches"))
    countries = targets.get("countries")
    scored = []
    for creator in candidates:
        niches = creator_niches(creator)
        fit = match_score(niches, targets)
        if not fit or audience_mismatch(roster, {}, niches):
            continue
        code = _country_code(creator.get("country"))
        if countries and code and code not in countries:
            continue
        known = bool(code) and (not countries or code in countries)
        scored.append(((fit, known, bool(creator.get("has_applied"))), creator))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _key, c in scored[:limit]]


def apply_link(roster: Dict[str, Any]) -> str:
    params = urlencode({"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": PUSH_KIND})
    return f"{apply_url_for_brand(roster['slug'])}&{params}"


def push_email(first_name: str, roster: Dict[str, Any], unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    brand = (roster.get("brand_name") or "A brand").strip()
    product = (roster.get("hero_product") or "").strip()
    subject = f"{brand} is gifting {product} to creators like you" if product else f"{brand} just opened gifted PR"
    gift = f"<strong>{escape(product)}</strong>" if product else "their product"
    lines = [
        f"<strong>{escape(brand)}</strong> just opened a gifted PR roster on Newcollab and wants to send {gift} "
        "to creators in your niche.",
        "They pick 5 creators from this roster. You post once organically and deliver one raw UGC video. "
        "Product and shipping are free.",
        "Applying takes 2 minutes: pick 3 of your posts and confirm your address.",
    ]
    buttons = [{"label": f"Apply to {brand}", "href": apply_link(roster)}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def push_note(roster: Dict[str, Any]) -> str:
    brand = (roster.get("brand_name") or "A brand").strip()
    product = (roster.get("hero_product") or "").strip()
    gift = f" **{product}**" if product else ""
    return (f"📣 **{brand}** just opened gifted PR{' for' + gift if gift else ''} and you fit their roster. "
            f"They pick 5 creators. Find them in **For You** and apply in 2 minutes.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def email_new_brand_pushes(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                           test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Cron job: up to ``limit`` emails per run across fresh rosters, oldest roster first."""
    from services.polly_alerts import _default_send, _log_email, _recipient, _unsubscribe_url, ensure_alert_tables

    if not push_enabled() or (now or utc_now()).hour not in SEND_HOURS_UTC:
        return []
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    out: List[Dict[str, Any]] = []
    budget = limit
    for roster in load_fresh_rosters(conn):
        if budget <= 0:
            break
        room = min(budget, PUSH_PER_BRAND - int(roster.get("pushed") or 0))
        for creator in matching_creators(roster, load_candidates(conn, roster["brand_id"]), room):
            cid = creator["creator_id"]
            who = _recipient(conn, cid)
            if not who:
                continue
            unsub = _unsubscribe_url(who["user_id"])
            mail = push_email(who["first"], roster, unsub)
            item = {"creator_id": cid, "kind": PUSH_KIND, "brand_id": roster["brand_id"],
                    "subject": mail["subject"], "to": who["email"]}
            budget -= 1
            if dry_run:
                out.append(item)
                continue
            try:
                from services.polly_autopilot import post_thread_note

                post_thread_note(conn, cid, push_note(roster))
                conn.commit()
            except Exception as err:
                conn.rollback()
                print(f"[new brand push] note skipped creator={cid}: {err}")
            result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, PUSH_KIND)
            _log_email(conn, cid, PUSH_KIND, roster["brand_id"], roster["campaign_id"], mail["subject"], result)
            item["ok"] = bool(result.get("success"))
            out.append(item)
    return out
