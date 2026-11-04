"""Monday check-in for free creators (replaces "your monday picks from Polly").

Two versions from the same template:
- they pitched in the last 3-14 days: one-tap check-in on each pitch (replied / bounced / no reply yet), so we
  learn outcomes we can't read from their inbox, and Polly takes the next step;
- they didn't: three brands ready to pitch, with Polly writing the pitch when they open it.
Both end on what Pro would do for them this week, with the free week when checkout honours it.
Every number shown is real for that creator; nothing here is invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

KIND = "monday_checkin"
CHECKIN_MIN_AGE = timedelta(days=3)
CHECKIN_MAX_AGE = timedelta(days=14)
MAX_CHECKINS = 3
PRO_PER_WEEK = 15
FREE_PITCHES = 3
# Anything after the pitch that already tells us how it went.
OUTCOME_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected", "creator_replied",
                  "dropped", "email_bounced", "pitch_not_sent", "still_quiet")
DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts):
    return ts.replace(tzinfo=timezone.utc) if ts is not None and ts.tzinfo is None else ts


def _niches(cur, creator_id: int) -> List[str]:
    from services.creator_niches import recorded_niches

    cur.execute("SELECT niche, creator_niches FROM creators WHERE id = %s", (creator_id,))
    row = cur.fetchone()
    return [n.lower() for n in recorded_niches(dict(row) if row else {}) if n][:3]


def pitches_to_check(cur, creator_id: int, now: datetime) -> List[Dict[str, Any]]:
    """Their own pitches from 3-14 days ago with no known outcome yet, newest first, one per brand."""
    cur.execute(
        """
        SELECT DISTINCT ON (e.brand_id) e.brand_id, e.occurred_at,
               COALESCE(NULLIF(e.event_data->>'brand_name', ''), b.brand_name, '') AS brand_name
        FROM polly_timeline_events e
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.creator_id = %(cid)s AND e.event_type = 'pitch_sent' AND e.brand_id IS NOT NULL
          AND e.event_label NOT ILIKE 'Follow-up%%'
          AND e.occurred_at BETWEEN %(oldest)s AND %(newest)s
          AND NOT EXISTS (SELECT 1 FROM polly_timeline_events o
                          WHERE o.creator_id = e.creator_id AND o.brand_id = e.brand_id
                            AND o.event_type = ANY(%(outcomes)s) AND o.occurred_at >= e.occurred_at)
        ORDER BY e.brand_id, e.occurred_at DESC
        """,
        {"cid": creator_id, "oldest": now - CHECKIN_MAX_AGE, "newest": now - CHECKIN_MIN_AGE,
         "outcomes": list(OUTCOME_EVENTS)},
    )
    from services.pitch_writer import clean_brand

    rows = sorted((dict(r) for r in cur.fetchall()), key=lambda r: r["occurred_at"], reverse=True)
    rows = [{**r, "brand_name": clean_brand(r.get("brand_name"))} for r in rows]
    return [r for r in rows if r.get("brand_name")][:MAX_CHECKINS]


def brand_picks(cur, creator_id: int, niches: List[str], limit: int = 2) -> List[Dict[str, Any]]:
    """Matched, emailable brands they haven't pitched or unlocked, the ones that reply to creators first."""
    if not niches:
        return []
    try:
        from services.brand_reply_signal import id_sets
        replies = [int(i) for i in id_sets(cur).get("replies") or [] if i]
    except Exception:
        replies = []
    week = datetime.now().isocalendar()[1]
    cur.execute(
        """
        SELECT b.id, b.brand_name, COALESCE(b.payment_offered, FALSE) AS pays, (b.id = ANY(%(replies)s)) AS replies,
               (b.created_at > NOW() - INTERVAL '30 days') AS fresh
        FROM pr_brands b
        WHERE LOWER(b.category) = ANY(%(niches)s) AND b.status = 'published' AND b.accepting_pr
          AND COALESCE(b.contact_email, '') <> ''
          AND NOT EXISTS (SELECT 1 FROM brand_unlocks u WHERE u.creator_id = %(cid)s AND u.brand_id = b.id)
          AND NOT EXISTS (SELECT 1 FROM polly_timeline_events e
                          WHERE e.creator_id = %(cid)s AND e.brand_id = b.id AND e.event_type = 'pitch_sent')
        ORDER BY (b.id = ANY(%(replies)s)) DESC, (b.id + %(rot)s) %% 997
        LIMIT %(limit)s
        """,
        {"niches": niches, "cid": creator_id, "replies": replies, "rot": (creator_id * 17 + week * 7) % 997,
         "limit": limit},
    )
    out = []
    for r in cur.fetchall():
        r = dict(r)
        if r["pays"]:
            badge, kind, line = "GIFTS + PAYS", "green", "Pays creators as well as gifting"
        elif r["replies"]:
            badge, kind, line = "REPLIES", "green", "Has replied to creators on Newcollab"
        elif r["fresh"]:
            badge, kind, line = "NEW THIS WEEK", "grey", "Just added to your matches"
        else:
            badge, kind, line = "GIFTED PR", "grey", "Sends product to creators in your niche"
        from services.pitch_writer import clean_brand
        out.append({"id": r["id"], "name": clean_brand(r["brand_name"]), "badge": badge, "badge_kind": kind, "line": line})
    return out


def paid_gig(cur, niches: List[str]) -> Optional[Dict[str, Any]]:
    """One live paid UGC job, in their niche when there is one."""
    cur.execute(
        """
        SELECT id, brand_name, pr_value_usd
        FROM opportunities
        WHERE status = 'live' AND COALESCE(pr_value_usd, 0) > 0
          AND COALESCE(published_at, created_at) > NOW() - INTERVAL '14 days'
        ORDER BY (LOWER(COALESCE(brand_category, '')) = ANY(%s)) DESC, COALESCE(published_at, created_at) DESC
        LIMIT 1
        """,
        (niches or [""],),
    )
    row = cur.fetchone()
    if not row:
        return None
    row = dict(row)
    from services.pitch_writer import clean_brand
    return {"id": row["id"], "name": clean_brand(row["brand_name"]), "badge": "PAID UGC", "badge_kind": "gold",
            "line": f"Budget ${int(row['pr_value_usd']):,} · open now"}


def brands_waiting(cur, creator_id: int, niches: List[str]) -> int:
    if not niches:
        return 0
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM pr_brands b
        WHERE LOWER(b.category) = ANY(%(niches)s) AND b.status = 'published' AND b.accepting_pr
          AND COALESCE(b.contact_email, '') <> ''
          AND NOT EXISTS (SELECT 1 FROM polly_timeline_events e
                          WHERE e.creator_id = %(cid)s AND e.brand_id = b.id AND e.event_type = 'pitch_sent')
        """,
        {"niches": niches, "cid": creator_id},
    )
    row = cur.fetchone() or {}
    return int((row.get("n") if isinstance(row, dict) else row[0]) or 0)


def pitched_last_week(cur, creator_id: int, now: datetime) -> int:
    cur.execute(
        """SELECT COUNT(DISTINCT brand_id) AS n FROM polly_timeline_events
           WHERE creator_id = %s AND event_type = 'pitch_sent' AND occurred_at > %s""",
        (creator_id, now - timedelta(days=7)),
    )
    row = cur.fetchone() or {}
    return int((row.get("n") if isinstance(row, dict) else row[0]) or 0)


def build_context(cur, creator_id: int, first_name: str, credits_remaining: Optional[int],
                  now: Optional[datetime] = None, trial: Optional[bool] = None) -> Dict[str, Any]:
    from services.checkin_links import one_tap_url
    from services.polly_alerts import chip_link, frontend_url, upgrade_link

    now = now or _now()
    first = (first_name or "").strip().split(" ")[0].capitalize() or "there"
    niches = _niches(cur, creator_id)
    niche = niches[0].replace("_", " ") if niches else ""

    checks = []
    for p in pitches_to_check(cur, creator_id, now):
        bid, name = p["brand_id"], p["brand_name"]
        sent = _aware(p["occurred_at"])
        checks.append({
            "name": name,
            "sent_label": f"Sent {DAY[sent.weekday()]}" if now - sent < timedelta(days=7) else f"Sent {sent.strftime('%b %-d')}",
            "replied_url": chip_link("checkin_replied", brand_id=bid, brand_name=name, kind=KIND),
            "bounced_url": one_tap_url(creator_id, "checkin_bounced", brand_id=bid, brand_name=name, kind=KIND),
            "quiet_url": one_tap_url(creator_id, "checkin_quiet", brand_id=bid, brand_name=name, kind=KIND),
            "gmail_url": "https://mail.google.com/mail/u/0/#search/" + quote(f'"{name}" newer_than:21d'),
        })

    picks = []
    gig = paid_gig(cur, niches)
    if gig:
        gig["url"] = f"{frontend_url()}/creator/dashboard/paid-gigs?gig={gig['id']}&utm_source=email&utm_campaign={KIND}"
        gig["cta"] = "See the job"
        picks.append(gig)
    for b in brand_picks(cur, creator_id, niches, limit=3 - len(picks)):
        b["url"] = chip_link("pitch_brand", brand_id=b["id"], brand_name=b["name"], kind=KIND)
        b["cta"] = "Open my pitch"
        picks.append(b)

    if trial is None:
        from services.quota_trial_test import trial_eligible
        try:
            trial = trial_eligible(cur, creator_id)
        except Exception:
            trial = False

    waiting = brands_waiting(cur, creator_id, niches)
    pitched = pitched_last_week(cur, creator_id, now)
    variant = "checkin" if checks else "quiet"
    if checks:
        subject = f"Did {checks[0]['name']} reply, {first}?" if first != "there" else f"Did {checks[0]['name']} reply?"
        preheader = "One tap to tell me, and I'll handle the next step."
    elif gig:
        subject = f"A paid UGC job and 2 brands for you, {first}" if first != "there" else "A paid UGC job and 2 brands for you"
        preheader = "Open one and Polly writes the pitch for you."
    else:
        subject = f"{len(picks)} {niche} brands to pitch this week".replace("  ", " ") if picks else "Your week with Polly"
        preheader = "Open one and Polly writes the pitch for you."
    return {
        "variant": variant,
        "first_name": first,
        "subject_override": subject,
        "preheader": preheader,
        "checks": checks,
        "picks": picks,
        "niche": niche,
        "credits_remaining": credits_remaining,
        "credits_quota": FREE_PITCHES,
        "reset_label": (now.replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%b %-d"),
        "brands_waiting": waiting,
        "pitched_last_week": pitched,
        "pro_per_week": PRO_PER_WEEK,
        "trial": bool(trial),
        "pro_url": upgrade_link(f"{KIND}_{'trial' if trial else 'pro'}"),
        "polly_url": chip_link("line_up", kind=KIND),
        "cta_url": f"{frontend_url()}/creator/dashboard/for-you",
    }
