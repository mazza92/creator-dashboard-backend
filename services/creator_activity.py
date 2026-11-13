"""The creator's activity feed (the bell): what happened in their outreach, newest first, like Instagram / TikTok
creator activity. Built from events we already store, so nothing new has to be written for it:

- a brand replied / made an offer (creator_deals)
- a brand opened the creator's kit (polly_timeline_events portfolio_viewed), grouped per brand per day
- Polly pitched brands for them (polly_autopilot_queue sends, and pitches sent from the app), grouped per day
- follow-ups sent, grouped per day
- paid UGC gigs Autopilot applied to, grouped per day
- today's UGC Pulse is ready
- the last Pro payment failed (pinned on top while it lasts)

Unread = newer than the last time they opened the bell (creator_activity_seen). Each item links to where to act.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

WINDOW_DAYS = 30
MAX_ITEMS = 40
NAMES = 3

_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS creator_activity_seen (
        creator_id INTEGER PRIMARY KEY, seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    conn.commit()
    _READY = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, 9, tzinfo=timezone.utc)
    return None


def _names(names: List[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= NAMES:
        return ", ".join(names)
    return ", ".join(names[:NAMES]) + f" +{len(names) - NAMES}"


def _rows(conn, sql: str, params: tuple) -> List[Dict[str, Any]]:
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception as err:  # a missing table or column never breaks the bell
        print(f"[activity] query skipped: {err}")
        conn.rollback()
        return []


def tracker_href(brand_id: Any = None) -> str:
    return f"/creator/dashboard/tracker?brand={int(brand_id)}" if brand_id else "/creator/dashboard/tracker"


def build_items(conn, creator_id: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or _now()
    since = now - timedelta(days=WINDOW_DAYS)
    items: List[Dict[str, Any]] = []

    for d in _rows(conn, """SELECT id, brand_id, brand_name, stage, deal_type, value_cents, created_at
                            FROM creator_deals WHERE creator_id = %s AND created_at >= %s""", (creator_id, since)):
        paid = d.get("deal_type") in ("paid", "gig") and d.get("value_cents")
        text = (f"{d['brand_name']} offered you ${int(d['value_cents']) // 100:,}" if paid
                else f"{d['brand_name']} replied to your pitch")
        items.append({"id": f"deal-{d['id']}", "kind": "reply", "icon": "reply", "at": _aware(d["created_at"]),
                      "text": text, "sub": "Your move: answer them" if d.get("stage") in ("replied", "offered", "negotiating")
                      else None, "href": tracker_href(d.get("brand_id"))})

    for v in _rows(conn, """SELECT e.brand_id, COALESCE(b.brand_name, e.event_data->>'brand_name') AS brand_name,
                                   (e.occurred_at AT TIME ZONE 'UTC')::date AS day, COUNT(*) AS n, MAX(e.occurred_at) AS at
                            FROM polly_timeline_events e LEFT JOIN pr_brands b ON b.id = e.brand_id
                            WHERE e.creator_id = %s AND e.event_type = 'portfolio_viewed' AND e.occurred_at >= %s
                              AND e.brand_id IS NOT NULL
                            GROUP BY 1, 2, 3""", (creator_id, since)):
        n = int(v.get("n") or 1)
        items.append({"id": f"view-{v['brand_id']}-{v['day']}", "kind": "kit_view", "icon": "eye", "at": _aware(v["at"]),
                      "text": f"{v.get('brand_name') or 'A brand'} opened your kit" + (f" {n}×" if n > 1 else ""),
                      "sub": "A follow-up lands well now", "href": tracker_href(v.get("brand_id"))})

    sends: Dict[Any, Dict[str, Any]] = {}
    for r in _rows(conn, """SELECT brand_name, sent_at AS at, pitch_meta->>'origin' AS origin, pitch_meta->>'pay' AS pay
                            FROM polly_autopilot_queue WHERE creator_id = %s AND status = 'sent' AND sent_at >= %s""",
                   (creator_id, since)):
        key = ("gig" if r.get("origin") == "paid_gig" else "pitch", _aware(r["at"]).date())
        g = sends.setdefault(key, {"names": [], "at": _aware(r["at"]), "pays": []})
        g["names"].append(r.get("brand_name") or "a brand")
        g["at"] = max(g["at"], _aware(r["at"]))
        if r.get("pay"):
            g["pays"].append(r["pay"])
    for r in _rows(conn, """SELECT p.pitched_at AS at, COALESCE(b.brand_name, 'a brand') AS brand_name
                            FROM creator_pipeline p LEFT JOIN pr_brands b ON b.id = p.brand_id
                            WHERE p.creator_id = %s AND p.pitched_at >= %s
                              AND NOT EXISTS (SELECT 1 FROM polly_autopilot_queue q WHERE q.creator_id = p.creator_id
                                              AND q.brand_id = p.brand_id AND q.status = 'sent')""",
                   (creator_id, since)):
        key = ("pitch", _aware(r["at"]).date())
        g = sends.setdefault(key, {"names": [], "at": _aware(r["at"]), "pays": []})
        g["names"].append(r.get("brand_name") or "a brand")
        g["at"] = max(g["at"], _aware(r["at"]))
    for (kind, day), g in sends.items():
        n = len(g["names"])
        if kind == "gig":
            items.append({"id": f"gig-{day}", "kind": "gigs", "icon": "gig", "at": g["at"],
                          "text": f"Polly applied to {n} paid gig{'s' if n != 1 else ''} for you",
                          "sub": _names(g["names"]), "href": "/creator/dashboard/paid-gigs"})
        else:
            items.append({"id": f"sent-{day}", "kind": "pitched", "icon": "send", "at": g["at"],
                          "text": f"{n} brand{'s' if n != 1 else ''} pitched from your Gmail",
                          "sub": _names(g["names"]), "href": "/creator/dashboard/tracker"})

    fus: Dict[date, Dict[str, Any]] = {}
    for r in _rows(conn, """SELECT brand_name, followup_sent_at AS at FROM polly_autopilot_queue
                            WHERE creator_id = %s AND followup_sent_at >= %s""", (creator_id, since)):
        day = _aware(r["at"]).date()
        g = fus.setdefault(day, {"names": [], "at": _aware(r["at"])})
        g["names"].append(r.get("brand_name") or "a brand")
        g["at"] = max(g["at"], _aware(r["at"]))
    for day, g in fus.items():
        n = len(g["names"])
        items.append({"id": f"fu-{day}", "kind": "followups", "icon": "followup", "at": g["at"],
                      "text": f"{n} follow-up{'s' if n != 1 else ''} sent", "sub": _names(g["names"]),
                      "href": "/creator/dashboard/tracker"})

    pulse = pulse_today(conn, creator_id, now.date())
    if pulse:
        items.append({"id": f"pulse-{pulse['day']}", "kind": "pulse", "icon": "pulse",
                      "at": _aware(pulse.get("created_at")) or now,
                      "text": f"Today's UGC Pulse is in: {pulse['count']} new hook{'s' if pulse['count'] != 1 else ''}",
                      "sub": pulse.get("hook"), "href": f"/creator/dashboard/pulse?niche={pulse['niche']}"})

    items = [i for i in items if i.get("at")]
    items.sort(key=lambda i: i["at"], reverse=True)
    items = items[:MAX_ITEMS]

    billing = payment_issue(conn, creator_id)
    if billing:
        items.insert(0, {"id": "billing", "kind": "billing", "icon": "alert", "at": billing, "pinned": True,
                         "text": "Your last Pro payment didn't go through",
                         "sub": "Update your card or cancel in Settings", "href": "/creator/dashboard/settings"})
    return items


def creator_niches(conn, creator_id: int) -> List[str]:
    rows = _rows(conn, "SELECT niche, creator_niches FROM creators WHERE id = %s", (creator_id,))
    if not rows:
        return []
    try:
        from services.creator_niches import recorded_niches
        return recorded_niches(rows[0]) or []
    except Exception:
        return []


def pulse_today(conn, creator_id: int, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """The newest pulse day in the creator's niches (today or the latest one), with its hook count."""
    niches = creator_niches(conn, creator_id)
    if not niches:
        return None
    rows = _rows(conn, """SELECT niche, day, COUNT(*) FILTER (WHERE kind = 'hook') AS count, MIN(created_at) AS created_at,
                                 (ARRAY_AGG(text ORDER BY score DESC, id) FILTER (WHERE kind = 'hook'))[1] AS hook
                          FROM ugc_pulse_items WHERE niche = ANY(%s) AND day >= %s AND day <= %s
                          GROUP BY niche, day ORDER BY day DESC, count DESC LIMIT 1""",
                 (niches, (today or _now().date()) - timedelta(days=1), today or _now().date()))
    if not rows or not int(rows[0].get("count") or 0):
        return None
    r = rows[0]
    return {"niche": r["niche"], "day": r["day"].isoformat(), "count": int(r["count"]), "hook": r.get("hook"),
            "created_at": r.get("created_at")}


def payment_issue(conn, creator_id: int) -> Optional[datetime]:
    rows = _rows(conn, """SELECT subscription_status, to_jsonb(c)->>'past_due_since' AS since FROM creators c
                          WHERE id = %s""", (creator_id,))
    if not rows or rows[0].get("subscription_status") != "past_due":
        return None
    raw = rows[0].get("since")
    try:
        return _aware(datetime.fromisoformat(raw)) if raw else _now()
    except ValueError:
        return _now()


def seen_at(conn, creator_id: int) -> Optional[datetime]:
    ensure_table(conn)
    rows = _rows(conn, "SELECT seen_at FROM creator_activity_seen WHERE creator_id = %s", (creator_id,))
    return _aware(rows[0]["seen_at"]) if rows else None


def mark_seen(conn, creator_id: int, at: Optional[datetime] = None) -> None:
    ensure_table(conn)
    cur = conn.cursor()
    cur.execute("""INSERT INTO creator_activity_seen (creator_id, seen_at) VALUES (%s, %s)
                   ON CONFLICT (creator_id) DO UPDATE SET seen_at = EXCLUDED.seen_at""", (creator_id, at or _now()))
    conn.commit()


def feed(conn, creator_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    items = build_items(conn, creator_id, now)
    last = seen_at(conn, creator_id)
    # First visit: only the last 2 days count as new, so a new bell doesn't open on "40".
    cutoff = last or (now - timedelta(days=2))
    for i in items:
        i["unread"] = bool(i["at"] > cutoff) or bool(i.get("pinned"))
        i["at"] = i["at"].isoformat()
    pulse = pulse_today(conn, creator_id, now.date())
    return {"items": items, "unread": sum(1 for i in items if i["unread"]),
            "seen_at": last.isoformat() if last else None,
            "pulse": {"day": pulse["day"], "niche": pulse["niche"], "count": pulse["count"]} if pulse else None}
