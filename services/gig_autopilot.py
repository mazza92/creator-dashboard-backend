"""Pro Autopilot applies to paid UGC gigs for the creator, not only gifted brand pitches.

Why (Nov 2026): Pro members saw "70 brands pitched, no replies yet" in their renewal note and churned. Gifted
outreach is slow by nature; paid gigs are briefs a brand posted this week with a budget and a real inbox we
found ourselves (services/gig_contact.py). Each week Autopilot applies to the best fitting ones in the creator's
niches, from their Gmail (or Newcollab sending), with a short paid-work pitch that names the brief. The gig is
marked applied, so it shows in their Paid gigs as done.

Gig brands aren't directory brands: each gets a hidden pr_brands row (status 'gig', no slug, never listed) so
the send gate, follow-ups and tracking work exactly as for any Autopilot pitch.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

GIGS_PER_WEEK = 2
MAX_AGE = timedelta(days=30)
GIG_ORIGIN = "paid_gig"

# Gig niches are free text ("Home Workouts, Busy Parents, Lifestyle Fitness"): map words to our niche ids.
NICHE_WORDS = {
    "fitness": r"fitness|workout|gym|kettlebell|exercise|running|runners?|walkers?|yoga|pilates|body recomposition",
    "activewear": r"activewear|athleisure|sportswear",
    "wellness": r"wellness|mental health|self[- ]care|meditation|sleep|mindful|therapy|gut health",
    "supplements": r"supplements?|vitamins?|protein|collagen|nutrition",
    "skincare": r"skin ?care|skincare|serum|acne|spf",
    "beauty": r"beauty|makeup|cosmetics?|nails?|lashes",
    "haircare": r"hair ?care|haircare|scalp|hair",
    "fashion": r"fashion|clothing|apparel|outfits?|style",
    "jewelry": r"jewel(?:le)?ry|rings?|necklaces?",
    "food": r"food|recipes?|snacks?|drinks?|beverages?|cooking|kitchen",
    "home": r"home decor|home goods|furniture|interior|cleaning",
    "travel": r"travel|trips?|hotels?|flights?",
    "tech": r"\btech\b|gadgets?|software|apps?\b|\bai\b",
    "gaming": r"gaming|games?|gamers?|discord|twitch",
    "pet": r"pets?|dogs?|cats?|puppy",
    "baby": r"baby|babies|parents?|parenting|moms?|mums?|toddlers?",
    "lifestyle": r"lifestyle",
}
# Briefs we never apply to on a creator's behalf.
SENSITIVE = re.compile(r"adult|porn|onlyfans|nsfw|cbd|thc|cannabis|gambl|casino|betting|crypto|antidepress"
                       r"|prescription|weight[- ]loss (?:drug|injection)|vape|nicotine", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def gig_niches(opp: Dict[str, Any]) -> List[str]:
    text = " ".join([str(opp.get("brand_category") or "")]
                    + [str(x) for x in (opp.get("creator_niches") or []) if x]
                    + [str(opp.get("campaign_description") or "")[:400]]).lower()
    return [n for n, pattern in NICHE_WORDS.items() if re.search(rf"\b(?:{pattern})\b", text)]


def fits(opp: Dict[str, Any], niches: List[str], followers: int) -> bool:
    """Same strict rule as brand matching: the gig has to be in a niche the creator picked."""
    from services.polly_discovery import NICHE_CATEGORIES
    from services.polly_gigs import gig_fits_creator

    text = " ".join(str(opp.get(k) or "") for k in ("brand_name", "brand_category", "campaign_description"))
    if SENSITIVE.search(text):
        return False
    want = set()
    for n in niches:
        want.update(NICHE_CATEGORIES.get(n, [n]))
        want.add(n)
    if not want & set(gig_niches(opp)):
        return False
    return gig_fits_creator(opp, followers)


def candidates(conn, creator_id: int, limit: int = 40, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Live scanned gigs with a verified brand inbox, newest first, not applied to or queued yet."""
    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT o.id, o.brand_name, o.brand_email, o.brand_website, o.brand_category, o.creator_niches,
               o.follower_ranges, o.campaign_description, o.pr_value_usd, o.listing_brief, o.additional_notes,
               o.pr_brand_id, o.published_at, o.product_name
        FROM opportunities o
        WHERE o.status = 'live'
          AND o.additional_notes LIKE '%%[scanner:%%'
          AND o.additional_notes ~ '\\|\\s*contact=[a-z_]+'
          AND o.brand_email LIKE '%%@%%'
          AND COALESCE(o.published_at, o.created_at) >= %s
          AND NOT EXISTS (SELECT 1 FROM opportunity_applications a WHERE a.opportunity_id = o.id AND a.creator_id = %s)
          AND NOT EXISTS (SELECT 1 FROM polly_autopilot_queue q WHERE q.creator_id = %s
                          AND q.pitch_meta->>'gig_id' = o.id::text)
        ORDER BY COALESCE(o.published_at, o.created_at) DESC
        LIMIT %s
        """,
        (now - MAX_AGE, creator_id, creator_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def queued_this_week(conn, creator_id: int, now: Optional[datetime] = None) -> int:
    now = now or _now()
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM polly_autopilot_queue WHERE creator_id = %s
                   AND pitch_meta->>'origin' = %s AND created_at >= %s""",
                (creator_id, GIG_ORIGIN, now - timedelta(days=7)))
    row = cur.fetchone()
    return int((row[0] if not isinstance(row, dict) else list(row.values())[0]) or 0)


def gig_brand_id(conn, opp: Dict[str, Any]) -> int:
    """The gig's hidden brand row (created once, linked on the gig)."""
    if opp.get("pr_brand_id"):
        return int(opp["pr_brand_id"])
    cur = conn.cursor()
    cur.execute("""SELECT id FROM pr_brands WHERE status = 'gig' AND LOWER(contact_email) = LOWER(%s) LIMIT 1""",
                (opp["brand_email"],))
    row = cur.fetchone()
    if row:
        bid = int(row[0] if not isinstance(row, dict) else row["id"])
    else:
        cur.execute(
            """INSERT INTO pr_brands (brand_name, contact_email, website, category, status, description)
               VALUES (%s, %s, %s, %s, 'gig', %s) RETURNING id""",
            (opp.get("brand_name") or "Brand", opp["brand_email"], opp.get("brand_website"),
             (gig_niches(opp) or ["other"])[0], "Paid UGC gig contact (hidden, not a directory listing)."),
        )
        row = cur.fetchone()
        bid = int(row[0] if not isinstance(row, dict) else row["id"])
    cur.execute("UPDATE opportunities SET pr_brand_id = %s WHERE id = %s AND pr_brand_id IS NULL", (bid, opp["id"]))
    conn.commit()
    return bid


def _brief(opp: Dict[str, Any]) -> Dict[str, str]:
    from services.polly_gigs import parse_listing_brief

    brief = parse_listing_brief(opp.get("listing_brief")) or {}
    summary = str(brief.get("summary") or opp.get("campaign_description") or "").strip()
    summary = re.sub(r"\s+", " ", summary)[:160].rstrip(" .")
    pay = str(brief.get("pay") or (f"${int(opp['pr_value_usd'])}" if opp.get("pr_value_usd") else "")).strip()
    return {"summary": summary, "pay": pay}


def compose(opp: Dict[str, Any], creator: Dict[str, Any], stats: Optional[Dict[str, Any]] = None,
            kit_url: Optional[str] = None) -> Dict[str, str]:
    """A short paid-work application that names their brief. Facts only; nothing invented."""
    first = (creator.get("first_name") or "").strip() or "there"
    brand = (opp.get("brand_name") or "your team").strip()
    b = _brief(opp)
    niche = ", ".join((creator.get("niches") or [])[:2]) or "UGC"
    lines = [f"Hi {brand} team,", ""]
    seen = f"I saw your paid UGC brief ({b['summary']})" if b["summary"] else "I saw your paid UGC brief"
    lines.append(f"{seen} and I'd love to create it for you.")
    proof = []
    if creator.get("followers"):
        from services.pitch_writer import fmt_count
        proof.append(f"{fmt_count(creator['followers'])} followers")
    if (stats or {}).get("avg_views"):
        proof.append(f"{stats['avg_views']} median views")
    who = f"I'm a {niche} creator" + (f" ({', '.join(proof)})" if proof else "")
    lines.append(f"{who} making short-form videos that feel native to TikTok and Reels.")
    for url in ((stats or {}).get("top_posts") or [])[:2]:
        lines.append(f"Recent video: {url}")
    if kit_url:
        lines.append(f"My media kit: {kit_url}")
    lines.append("")
    budget = f" within your {b['pay']} budget" if b["pay"] else ""
    lines.append(f"I can start this week{budget} and send a first draft fast. Want me to go ahead?")
    lines += ["", "Thanks,", first if first != "there" else ""]
    body = "\n".join(lines).rstrip() + "\n"
    product = (opp.get("product_name") or brand).strip()
    subject = f"UGC creator for {product}: your paid brief"
    return {"subject": subject[:120], "body": body}


def _creator_facts(conn, creator_id: int) -> Dict[str, Any]:
    from services.creator_niches import recorded_niches

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT c.creator_niches, c.niche, c.kit_slug, c.followers_count, c.social_follower_count,
                          u.first_name
                   FROM creators c JOIN users u ON u.id = c.user_id WHERE c.id = %s""", (creator_id,))
    row = dict(cur.fetchone() or {})
    followers = 0
    for k in ("social_follower_count", "followers_count"):
        try:
            followers = max(followers, int(row.get(k) or 0))
        except (TypeError, ValueError):
            pass
    return {"niches": recorded_niches(row), "followers": followers, "first_name": row.get("first_name"),
            "kit_slug": row.get("kit_slug")}


def queue_gigs(conn, creator_id: int, now: Optional[datetime] = None, per_week: int = GIGS_PER_WEEK) -> List[str]:
    """Add this week's paid gigs to the creator's Autopilot queue. Returns the brand names queued."""
    from services.polly_autopilot import add_draft

    now = now or _now()
    room = per_week - queued_this_week(conn, creator_id, now)
    if room <= 0:
        return []
    facts = _creator_facts(conn, creator_id)
    if not facts["niches"]:
        return []
    picked = [o for o in candidates(conn, creator_id, now=now) if fits(o, facts["niches"], facts["followers"])]
    if not picked:
        return []
    try:
        from services.pitch_writer import creator_stats
        stats = creator_stats(conn, creator_id)
    except Exception:
        stats = {}
    kit_url = None
    if facts.get("kit_slug"):
        try:
            from services.polly_kit import public_kit_url
            kit_url = public_kit_url(facts["kit_slug"])
        except Exception:
            kit_url = None
    names: List[str] = []
    for opp in picked[:room]:
        try:
            bid = gig_brand_id(conn, opp)
            mail = compose(opp, facts, stats, kit_url)
            add_draft(conn, creator_id, bid, opp.get("brand_name") or "Brand", opp["brand_email"], mail["subject"],
                      mail["body"], meta={"origin": GIG_ORIGIN, "gig_id": int(opp["id"]), "writer": "gig_v1",
                                          "pay": _brief(opp)["pay"]})
            names.append(opp.get("brand_name") or "a brand")
        except Exception as err:
            print(f"[gig autopilot] queue failed creator={creator_id} gig={opp.get('id')}: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
    return names


def mark_applied(conn, creator_id: int, meta: Optional[Dict[str, Any]]) -> None:
    """After the pitch is sent: the gig counts as applied (Paid gigs tab, pitch quota, admin)."""
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("origin") != GIG_ORIGIN or not meta.get("gig_id"):
        return
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO opportunity_applications (opportunity_id, creator_id, applied_at, status)
                       SELECT %s, %s, NOW(), 'applied'
                       WHERE NOT EXISTS (SELECT 1 FROM opportunity_applications
                                         WHERE opportunity_id = %s AND creator_id = %s)""",
                    (int(meta["gig_id"]), creator_id, int(meta["gig_id"]), creator_id))
        conn.commit()
    except Exception as err:
        print(f"[gig autopilot] mark applied failed: {err}")
        conn.rollback()


def gigs_applied(conn, creator_id: int, since: datetime) -> List[Dict[str, Any]]:
    """Paid gigs Autopilot applied to since a date: brand and pay, for the reports."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT brand_name, pitch_meta->>'pay' AS pay FROM polly_autopilot_queue
                   WHERE creator_id = %s AND pitch_meta->>'origin' = %s AND sent_at >= %s
                   ORDER BY sent_at""", (creator_id, GIG_ORIGIN, since))
    return [dict(r) for r in cur.fetchall()]
