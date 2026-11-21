"""The out-of-free-pitches "waiting room": what a free creator can still do while their 3 pitches are out, and what
Pro would send next. Everything here is real data for that creator:

  - momentum: pitches sent this cycle, brands that opened the kit, brands that replied (polly_timeline_events);
  - follow-up: the follow-up timing is the same as Autopilot's (day 4). A follow-up is the main action only once
    it's due; before that the card shows when it opens, with no button;
  - checklist: free things worth doing meanwhile (rates, today's Pulse hook, the kit, matched paid gigs);
  - drafts: the next 5 brands in the creator's plan with a subject and opener from pitch_writer (no credit spent,
    no contact unlocked, no LLM call, no website fetch). Pro sends these brands first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

FOLLOWUP_AFTER = timedelta(days=4)  # same as services.polly_autopilot.FOLLOWUP_GAPS[0]
WINDOW = timedelta(days=45)
FOLLOWED_UP_GAP = timedelta(days=3)
REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")
DRAFTS = 5


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _label(d: datetime) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _safe(conn, fn, default):
    try:
        return fn()
    except Exception as err:
        print(f"[waiting_room] partial: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return default


def pitched_brands(conn, creator_id: int, now: datetime) -> List[Dict[str, Any]]:
    """Brands pitched in the last 45 days: first send, whether it was followed up, kit opened, replied.
    A send is logged more than once at times (chat + Gmail tracking), so a follow-up is a send 3+ days after the
    first one, not a second row."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT e.brand_id, MAX(b.brand_name) AS brand_name,
               MIN(e.occurred_at) FILTER (WHERE e.event_type IN ('pitch_sent', 'paid_pitch_sent')) AS sent_at,
               MAX(e.occurred_at) FILTER (WHERE e.event_type IN ('pitch_sent', 'paid_pitch_sent')) AS last_sent_at,
               BOOL_OR(e.event_type = 'portfolio_viewed') AS opened,
               BOOL_OR(e.event_type = ANY(%s)) AS replied
        FROM polly_timeline_events e
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.creator_id = %s AND e.brand_id IS NOT NULL AND e.occurred_at > %s
        GROUP BY e.brand_id
        """,
        (list(REPLY_EVENTS), creator_id, now - WINDOW),
    )
    rows = []
    for r in cur.fetchall():
        if not r.get("sent_at"):
            continue
        r = dict(r)
        r["followed_up"] = (_aware(r["last_sent_at"]) - _aware(r["sent_at"])) >= FOLLOWED_UP_GAP
        rows.append(r)
    rows.sort(key=lambda r: _aware(r["sent_at"]))
    return rows


def followup(rows: List[Dict[str, Any]], now: datetime) -> Optional[Dict[str, Any]]:
    """The pitch to follow up: the oldest one that's due (day 4+, no reply, not followed up yet), else the next
    one to become due, with when it opens. None when every pitch got a reply or a follow-up."""
    open_rows = [r for r in rows if not r.get("replied") and not r.get("followed_up")]
    if not open_rows:
        return None
    due = [r for r in open_rows if _aware(r["sent_at"]) + FOLLOWUP_AFTER <= now]
    r = due[0] if due else min(open_rows, key=lambda x: _aware(x["sent_at"]))
    sent = _aware(r["sent_at"])
    opens = sent + FOLLOWUP_AFTER
    return {
        "brand_id": int(r["brand_id"]),
        "brand_name": r.get("brand_name") or "the brand",
        "sent_on": _label(sent),
        "day": max(0, (now - sent).days),
        "due": bool(due),
        "opens_on": _label(opens),
        "opens_at": opens.isoformat(),
        "opened_kit": bool(r.get("opened")),
    }


def _followers(conn, creator_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT GREATEST(COALESCE(social_follower_count, 0), COALESCE(followers_count, 0)) FROM creators "
                "WHERE id = %s", (creator_id,))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def checklist(conn, creator_id: int, now: datetime) -> List[Dict[str, Any]]:
    from services.creator_activity import new_gigs, pulse_today
    from services.creator_tracker import kit_has_rates
    from services.polly_persona import rate_band

    items: List[Dict[str, Any]] = []
    has_rates = _safe(conn, lambda: kit_has_rates(conn, creator_id), False)
    band = _safe(conn, lambda: rate_band(_followers(conn, creator_id)), None)
    rates = {"id": "rates", "done": bool(has_rates), "path": "/creator/dashboard/my-kit"}
    if band:
        rates["band"] = f"${band['video'][0]}–${band['video'][1]} a video"
    items.append(rates)
    pulse = _safe(conn, lambda: pulse_today(conn, creator_id, now.date()), None)
    if pulse and pulse.get("hook"):
        items.append({"id": "pulse", "hook": pulse["hook"], "niche": pulse["niche"], "count": pulse["count"],
                      "path": "/creator/dashboard/pulse"})
    items.append({"id": "kit", "path": "/creator/dashboard/my-kit"})
    gigs = _safe(conn, lambda: new_gigs(conn, creator_id, now - timedelta(days=30)), [])
    if gigs:
        items.append({"id": "gigs", "count": len(gigs), "path": "/creator/dashboard/paid-gigs"})
    return items


def drafts(conn, creator_id: int, first_name: str = "", limit: int = DRAFTS) -> Dict[str, Any]:
    """Subject + opener for the next brands in the plan, written the way pitch_writer writes them."""
    from services.creator_tracker import planned_rows
    from services.pitch_writer import creator_intro_facts, ideas_for, pick_subject
    from services.product_truth import range_phrase, verified_product

    today = datetime.now(timezone.utc).date()
    plan = [r for r in planned_rows(conn, creator_id) if r["planned_for"] >= today]
    out: Dict[str, Any] = {"plan_count": len(plan), "items": []}
    if not plan:
        return out
    ids = [int(r["brand_id"]) for r in plan[:limit]]
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, brand_name, hero_product, source, description, website, category
                   FROM pr_brands WHERE id = ANY(%s)""", (ids,))
    brands = {int(b["id"]): dict(b) for b in cur.fetchall()}
    niche = (creator_intro_facts(conn, creator_id) or {}).get("niche") or ""
    for r in plan[:limit]:
        b = brands.get(int(r["brand_id"])) or {"id": r["brand_id"], "brand_name": r.get("brand_name")}
        name = b.get("brand_name") or r.get("brand_name") or "Brand"
        product = verified_product(conn, b, fetch=False)
        seed = f"{creator_id}:{r['brand_id']}"
        subject = pick_subject(name, product, first_name or "", seed, niche)["text"]
        ideas = ideas_for(b.get("category") or niche)
        idea = ideas[int(r["brand_id"]) % len(ideas)] if ideas else "a short first-impression video"
        about = product or range_phrase(b.get("category"))
        out["items"].append({
            "brand_id": int(r["brand_id"]),
            "brand_name": name,
            "offer": r.get("offer") or "gifted",
            "meta_ads": bool(r.get("meta_ads")),
            "planned_for": _label(datetime.combine(r["planned_for"], datetime.min.time())),
            "subject": subject,
            "opener": f"I have an idea for {about}: {idea}, short and easy to reuse in your ads.",
            "product_verified": bool(product),
        })
    return out


def build(conn, creator_id: int, first_name: str = "", reset: str = "",
          now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = _safe(conn, lambda: pitched_brands(conn, creator_id, now), [])
    plan = _safe(conn, lambda: drafts(conn, creator_id, first_name), {"plan_count": 0, "items": []})
    return {
        "momentum": {
            "sent": len(rows),
            "opened": sum(1 for r in rows if r.get("opened")),
            "replied": sum(1 for r in rows if r.get("replied")),
        },
        "followup": followup(rows, now),
        "checklist": checklist(conn, creator_id, now),
        "drafts": plan["items"],
        "plan_count": plan["plan_count"],
        "reset": reset,
    }
