"""Waitlist sequence: creators waiting for manual approval get value while they wait, and one clear way in.

Three emails, then silence, and they stop the moment the creator is approved or rejected:
- waitlist_1 (day 2+):  your place in line + brands already lined up for your niche (cards) + skip with Pro;
- waitlist_2 (day 5+, 3 days after #1): proof: brands gifting creators in your niche this month, place update;
- waitlist_3 (day 10+, 5 days after #2): how to get in faster (post this week, reviews favour active creators)
                         + skip with Pro.
Waitlisted creators are kept out of every other product email (waitlist_gate); this is their only stream.
Caps from services.email_policy still apply.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from services.brand_feed import NICHE_CATEGORIES

STEPS = (
    # kind, min days since signup, min days since previous step
    ("waitlist_1", 2, None),
    ("waitlist_2", 5, 3),
    ("waitlist_3", 10, 5),
)
KINDS = tuple(s[0] for s in STEPS)
MAX_AGE = timedelta(days=60)  # older sign-ups have gone cold: don't revive them with a sequence
PRO_PRICE = "$19/mo"


def _pa():
    from services import polly_alerts
    return polly_alerts


def skip_link(kind: str) -> str:
    pa = _pa()
    q = urlencode({"skip": "1", "utm_source": "email", "utm_medium": "waitlist", "utm_campaign": kind})
    return f"{pa.frontend_url()}/creator/waitlist?{q}"


def queue_position(conn, creator_id: int) -> int:
    cur = _pa()._cursor(conn)
    cur.execute("""SELECT COUNT(*) + 1 AS pos FROM creators
                   WHERE approval_status = 'pending'
                     AND created_at < (SELECT created_at FROM creators WHERE id = %s)""", (creator_id,))
    row = cur.fetchone() or {}
    return int(row.get("pos") or 1)


def niche_brands(conn, niche: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Brands we know are giving out boxes in their categories, logo first."""
    cats = list(NICHE_CATEGORIES.get(niche) or [])
    cur = _pa()._cursor(conn)
    try:
        cur.execute(f"""
            SELECT b.id AS brand_id, b.brand_name
            FROM pr_brands b
            WHERE COALESCE(b.status, 'published') = 'published'
              AND to_jsonb(b) ->> 'gifting_signal' IS NOT NULL
              {"AND LOWER(b.category) = ANY(%s)" if cats else ""}
            ORDER BY (b.logo_url IS NOT NULL) DESC, (to_jsonb(b) ->> 'gifting_confirmed_at') DESC NULLS LAST
            LIMIT %s
        """, ((cats, limit) if cats else (limit,)))
        return [dict(r) for r in cur.fetchall()]
    except Exception as err:
        conn.rollback()
        print(f"[waitlist] niche brands skipped: {err}")
        return []


def _niche(row: Dict[str, Any]) -> str:
    from services.radar_extract import radar_niche
    parts = list(row.get("creator_niches") or []) + [row.get("niche") or ""]
    return next((radar_niche(p) for p in parts if radar_niche(p)), "")


def waitlist_email(conn, kind: str, first: str, position: int, niche: str, brands: List[Dict[str, Any]],
                   unsub: str) -> Dict[str, str]:
    pa = _pa()
    label = niche or "your niche"
    skip = skip_link(kind)
    cards = "".join(pa._brief_card(b.get("brand_name") or "A brand",
                                   pa.brief_brand_facts(conn, [b["brand_id"]]).get(int(b["brand_id"])) or {},
                                   skip, label="Unlock") for b in brands)
    pos = f"You're <strong>#{position}</strong> in line."
    if kind == "waitlist_1":
        subject = f"You're #{position} in line. {len(brands) or 'Your'} brands are already lined up for you"
        lines = ["We review every profile by hand so brands only hear from real, active creators.",
                 f"While you wait, here are {label} brands giving out PR boxes right now. "
                 "Polly will pitch them for you once you're in:" if brands else
                 "Polly already has brands lined up for you once you're in.",
                 cards,
                 f"Don't want to wait? <strong>Pro ({PRO_PRICE})</strong> skips the line: you're in today, "
                 "and Polly pitches 3 brands for you every weekday."]
        button = "Skip the line with Pro"
    elif kind == "waitlist_2":
        subject = f"{label.capitalize()} brands are gifting creators your size this month"
        lines = [f"Quick update: you're now <strong>#{position}</strong> in line.",
                 f"These {label} brands sent PR to small creators on TikTok in the last few weeks. "
                 "The sooner you pitch, the sooner you're on their list:" if brands else
                 "Brands keep sending PR to creators your size every week.",
                 cards,
                 f"Pro ({PRO_PRICE}) gets you in today and Polly starts pitching for you."]
        button = "Get in today with Pro"
    else:
        subject = f"Still #{position} in line? Here's how to get in faster"
        lines = [f"You're <strong>#{position}</strong> in line. Two ways to get in faster:",
                 "<strong>1. Post this week.</strong> We approve creators with recent posts first: brands want "
                 "active accounts.",
                 f"<strong>2. Skip the line with Pro ({PRO_PRICE}).</strong> You're in today, Polly pitches "
                 "3 brands for you every weekday and follows up. Cancel anytime."]
        button = "Skip the line with Pro"
    buttons = [{"label": button, "href": skip}]
    links = [{"label": "Check my place in line", "href": f"{pa.frontend_url()}/creator/waitlist"}]
    return {"subject": subject, "html": pa.render_email(first, subject, [l for l in lines if l], buttons, unsub,
                                                        links=links)}


def _due(conn, now) -> List[Dict[str, Any]]:
    cur = _pa()._cursor(conn)
    cur.execute("""
        SELECT c.id AS creator_id, c.user_id, c.created_at, c.niche, c.creator_niches, u.email, u.first_name,
               u.unsubscribed_at,
               (SELECT json_object_agg(l.kind, l.sent_at) FROM polly_email_log l
                WHERE l.creator_id = c.id AND l.kind = ANY(%s) AND l.ok) AS sent
        FROM creators c JOIN users u ON u.id = c.user_id
        WHERE c.approval_status = 'pending' AND u.email IS NOT NULL AND u.unsubscribed_at IS NULL
          AND c.created_at < %s AND c.created_at > %s
        ORDER BY c.created_at DESC
    """, (list(KINDS), now - timedelta(days=STEPS[0][1]), now - MAX_AGE))
    return [dict(r) for r in cur.fetchall()]


def _next_step(row: Dict[str, Any], now) -> Optional[str]:
    from datetime import datetime
    sent = row.get("sent") or {}
    created = row["created_at"]
    if created.tzinfo is None:
        from datetime import timezone
        created = created.replace(tzinfo=timezone.utc)
    prev_at = None
    for kind, min_days, after_prev in STEPS:
        if kind in sent:
            raw = sent[kind]
            prev_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if isinstance(raw, str) else raw
            if prev_at.tzinfo is None:
                from datetime import timezone
                prev_at = prev_at.replace(tzinfo=timezone.utc)
            continue
        if now - created < timedelta(days=min_days):
            return None
        if after_prev and prev_at and now - prev_at < timedelta(days=after_prev):
            return None
        return kind
    return None


def email_waitlist_sequence(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                            test_email: str = "") -> List[Dict[str, Any]]:
    pa = _pa()
    from services.email_policy import allowed
    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    now = pa.utc_now()
    out: List[Dict[str, Any]] = []
    try:
        rows = _due(conn, now)
    except Exception as err:
        conn.rollback()
        print(f"[waitlist] sequence skipped: {err}")
        return out
    for row in rows:
        if len(out) >= limit:
            break
        kind = _next_step(row, now)
        if not kind or not allowed(conn, row["creator_id"], kind, False):
            continue
        niche = _niche(row)
        brands = niche_brands(conn, niche) if kind != "waitlist_3" else []
        first = (row.get("first_name") or "").strip().split(" ")[0].capitalize()
        unsub = pa._unsubscribe_url(row["user_id"])
        mail = waitlist_email(conn, kind, first, queue_position(conn, row["creator_id"]), niche, brands, unsub)
        item = {"creator_id": row["creator_id"], "kind": kind, "subject": mail["subject"], "to": row["email"]}
        if not dry_run:
            result = send(test_email or row["email"], mail["subject"], mail["html"], unsub, kind)
            pa._log_email(conn, row["creator_id"], kind, None, None, mail["subject"], result)
            item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
