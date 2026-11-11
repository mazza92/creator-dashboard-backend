"""The creator's goal and Tracker: one monthly collab goal (paid deals + gifted products), every brand in one
pipeline, and the dates that matter.

Why (Nov 2026): Pro members cancelled after "70 brands pitched, no replies" with no sense of progress. A creator's
real aim is a paid UGC deal; gifted PR is the way in. So one dollar goal counts both (gifted products at retail
value), the pitch math shows how many brands the goal needs, and every brand sits on one board with its next step.

What moves by itself: lined up / pitched / followed up (Autopilot queue + Polly pitches), kit views.
What the creator taps: "Brand replied" (gifted offer, paid offer + amount, question, passed), then booked / box
arrived / posted / paid. Gmail read access (pending Google review) will make replies automatic later.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

# Defaults until a creator has their own numbers (30+ pitches with check-ins).
DEFAULT_REPLY_RATE = 0.08
DEFAULT_DEAL_RATE = 0.30
OWN_RATES_MIN_PITCHES = 30
GIFTED_AVG_CENTS = 4000
# Typical paid UGC per-video rate by follower band (USD cents): (max_followers, low, high).
RATE_BANDS = (
    (2_000, 8000, 15000),
    (10_000, 12000, 25000),
    (50_000, 20000, 40000),
    (10**12, 35000, 70000),
)
GOAL_MIN_CENTS = 5000
GOAL_MAX_CENTS = 5_000_000

COLUMNS = (
    ("lined_up", "Lined up"),
    ("pitched", "Pitched"),
    ("followed_up", "Followed up"),
    ("replied", "Replied"),
    ("negotiating", "Negotiating"),
    ("booked", "Booked"),
    ("done", "Paid / received"),
)
DEAL_TYPES = ("gifted", "paid", "gig")
DEAL_STAGES = ("replied", "offered", "negotiating", "booked", "shipped", "arrived", "posted", "paid", "closed")
STAGE_COLUMN = {
    "replied": "replied", "offered": "replied", "negotiating": "negotiating", "booked": "booked",
    "shipped": "booked", "arrived": "booked", "posted": "done", "paid": "done", "closed": "closed",
}
REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")
FREE_MONTHLY_PITCHES = 3

_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def month_key(now: Optional[datetime] = None) -> str:
    return (now or _now()).strftime("%Y-%m")


def month_bounds(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    now = now or _now()
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end = datetime(now.year + (now.month // 12), (now.month % 12) + 1, 1, tzinfo=timezone.utc)
    return start, end


def _cur(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def ensure_tables(conn) -> None:
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_index
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS creator_goals (
        creator_id INTEGER NOT NULL, month CHAR(7) NOT NULL, amount_cents INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT 'suggested', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (creator_id, month))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS creator_deals (
        id SERIAL PRIMARY KEY, creator_id INTEGER NOT NULL, brand_id INTEGER, brand_name TEXT NOT NULL,
        deal_type TEXT NOT NULL DEFAULT 'gifted', stage TEXT NOT NULL DEFAULT 'replied', value_cents INTEGER,
        deliverables TEXT, offered_at TIMESTAMPTZ, booked_at TIMESTAMPTZ, due_date DATE, shipped_at TIMESTAMPTZ,
        arrived_at TIMESTAMPTZ, posted_at TIMESTAMPTZ, payment_due DATE, paid_at TIMESTAMPTZ,
        closed_at TIMESTAMPTZ, closed_reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    ensure_index(cur, "CREATE UNIQUE INDEX IF NOT EXISTS uq_creator_deals_brand ON creator_deals (creator_id, brand_id) "
                      "WHERE brand_id IS NOT NULL")
    ensure_index(cur, "CREATE INDEX IF NOT EXISTS idx_creator_deals_creator ON creator_deals (creator_id, updated_at DESC)")
    conn.commit()
    _READY = True


# ---------------------------------------------------------------------------
# Goal suggestion and pitch math
# ---------------------------------------------------------------------------

def creator_profile(conn, creator_id: int) -> Dict[str, Any]:
    cur = _cur(conn)
    cur.execute("""SELECT c.id, c.user_id, c.followers_count, c.social_follower_count, c.creator_niches, c.niche,
                          COALESCE(c.subscription_tier, 'free') AS tier, c.unlocks_tier,
                          c.pitches_sent_this_week, c.last_pitch_reset,
                          d.engagement_rate
                   FROM creators c LEFT JOIN creator_profile_data d ON d.user_id = c.user_id
                   WHERE c.id = %s""", (creator_id,))
    row = dict(cur.fetchone() or {})
    followers = 0
    for k in ("social_follower_count", "followers_count"):
        try:
            followers = max(followers, int(row.get(k) or 0))
        except (TypeError, ValueError):
            pass
    row["followers"] = followers
    row["is_pro"] = str(row.get("tier") or "").lower() == "pro" or row.get("unlocks_tier") == "pro"
    try:
        from services.creator_niches import recorded_niches
        row["niches"] = recorded_niches(row)
    except Exception:
        row["niches"] = []
    return row


def rate_band(followers: int) -> Tuple[int, int]:
    for cap, low, high in RATE_BANDS:
        if followers < cap:
            return low, high
    return RATE_BANDS[-1][1], RATE_BANDS[-1][2]


def _round_goal(cents: float) -> int:
    step = 5000 if cents < 200000 else 10000
    return int(max(GOAL_MIN_CENTS, round(cents / step) * step))


def suggest_goal(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Starter / suggested / stretch monthly goals from followers (and engagement), with the reason shown."""
    followers = int(profile.get("followers") or 0)
    low, high = rate_band(followers)
    mid = (low + high) / 2
    try:
        er = float(profile.get("engagement_rate") or 0)
    except (TypeError, ValueError):
        er = 0.0
    er_pct = er * 100 if 0 < er < 1 else er
    if not 0 < er_pct <= 30:  # scraped values like 70% are data errors, not engagement
        er_pct = 0.0
    boost = 1.15 if er_pct >= 4 else 1.0
    options = [
        {"id": "starter", "amount_cents": _round_goal(mid * 1.5), "deals": 2},
        {"id": "suggested", "amount_cents": _round_goal(mid * 3 * boost), "deals": 4},
        {"id": "stretch", "amount_cents": _round_goal(mid * 6 * boost), "deals": 7},
    ]
    return {
        "options": options,
        "recommended": "suggested",
        "basis": {
            "followers": followers,
            "niches": profile.get("niches") or [],
            "engagement_pct": round(er_pct, 1) if er_pct else None,
            "rate_low_cents": low, "rate_high_cents": high, "gifted_avg_cents": GIFTED_AVG_CENTS,
        },
    }


def own_rates(conn, creator_id: int) -> Dict[str, Any]:
    """The creator's reply and deal rates once they have enough data, else the defaults."""
    cur = _cur(conn)
    cur.execute("""SELECT
          (SELECT COUNT(*) FROM polly_autopilot_queue WHERE creator_id = %(c)s AND sent_at IS NOT NULL)
          + (SELECT COUNT(*) FROM creator_pipeline WHERE creator_id = %(c)s
             AND (pitched_at IS NOT NULL OR send_confirmed IS TRUE)) AS pitched,
          (SELECT COUNT(DISTINCT brand_id) FROM polly_timeline_events WHERE creator_id = %(c)s
             AND event_type = ANY(%(ev)s)) AS replied,
          (SELECT COUNT(*) FROM creator_deals WHERE creator_id = %(c)s
             AND stage IN ('booked','shipped','arrived','posted','paid')) AS deals""",
                {"c": creator_id, "ev": list(REPLY_EVENTS)})
    row = dict(cur.fetchone() or {})
    pitched, replied, deals = (int(row.get(k) or 0) for k in ("pitched", "replied", "deals"))
    if pitched >= OWN_RATES_MIN_PITCHES and replied >= 1:
        reply = min(0.30, max(0.02, replied / pitched))
        deal = min(0.60, max(0.10, (deals / replied) if replied else DEFAULT_DEAL_RATE))
        return {"reply_rate": reply, "deal_rate": deal, "source": "yours"}
    return {"reply_rate": DEFAULT_REPLY_RATE, "deal_rate": DEFAULT_DEAL_RATE, "source": "default"}


def pitch_math(goal_cents: int, followers: int, rates: Dict[str, Any]) -> Dict[str, Any]:
    low, high = rate_band(followers)
    avg = int((low + high) / 2)
    deals = max(1, math.ceil(goal_cents / avg))
    pitches = math.ceil(deals / (rates["reply_rate"] * rates["deal_rate"]))
    return {"avg_deal_cents": avg, "deals": deals, "pitches": pitches,
            "reply_rate": round(rates["reply_rate"], 3), "deal_rate": round(rates["deal_rate"], 3),
            "rates_source": rates["source"]}


def get_goal(conn, creator_id: int, month: Optional[str] = None) -> Optional[Dict[str, Any]]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT amount_cents, source, month FROM creator_goals WHERE creator_id = %s AND month = %s",
                (creator_id, month or month_key()))
    row = cur.fetchone()
    if row:
        return dict(row)
    # Carry last month's goal forward (the creator set it once; it stays until they change it).
    cur.execute("""SELECT amount_cents, source FROM creator_goals WHERE creator_id = %s AND month < %s
                   ORDER BY month DESC LIMIT 1""", (creator_id, month or month_key()))
    prev = cur.fetchone()
    if prev:
        set_goal(conn, creator_id, int(prev["amount_cents"]), prev["source"], month=month, sync_autopilot=False)
        return {"amount_cents": int(prev["amount_cents"]), "source": prev["source"], "month": month or month_key()}
    return None


def set_goal(conn, creator_id: int, amount_cents: int, source: str = "custom", month: Optional[str] = None,
             sync_autopilot: bool = True) -> Dict[str, Any]:
    ensure_tables(conn)
    amount = int(max(GOAL_MIN_CENTS, min(GOAL_MAX_CENTS, int(amount_cents))))
    source = source if source in ("suggested", "custom", "default") else "custom"
    cur = conn.cursor()
    cur.execute("""INSERT INTO creator_goals (creator_id, month, amount_cents, source) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (creator_id, month) DO UPDATE SET amount_cents = EXCLUDED.amount_cents,
                   source = EXCLUDED.source, updated_at = NOW()""", (creator_id, month or month_key(), amount, source))
    conn.commit()
    if sync_autopilot:
        sync_autopilot_target(conn, creator_id, amount)
    return {"amount_cents": amount, "source": source, "month": month or month_key()}


def sync_autopilot_target(conn, creator_id: int, goal_cents: int) -> Optional[int]:
    """Autopilot's monthly pitch count follows the goal's pitch math, unless the creator set it by hand."""
    try:
        from services.polly_autopilot import clamp_target, ensure_autopilot_tables
        ensure_autopilot_tables(conn)
        prof = creator_profile(conn, creator_id)
        target = clamp_target(pitch_math(goal_cents, prof["followers"], own_rates(conn, creator_id))["pitches"])
        cur = conn.cursor()
        cur.execute("""UPDATE polly_autopilot SET monthly_target = %s, target_source = 'goal', updated_at = NOW()
                       WHERE creator_id = %s AND COALESCE(target_source, '') <> 'manual'""", (target, creator_id))
        conn.commit()
        return target
    except Exception as err:
        print(f"[tracker] autopilot target sync skipped: {err}")
        conn.rollback()
        return None


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------

def _brand_name(conn, brand_id: Optional[int]) -> str:
    if not brand_id:
        return ""
    cur = conn.cursor()
    cur.execute("SELECT brand_name FROM pr_brands WHERE id = %s", (brand_id,))
    row = cur.fetchone()
    return (row[0] if row and not isinstance(row, dict) else (row or {}).get("brand_name")) or ""


def get_deal(conn, creator_id: int, brand_id: int) -> Optional[Dict[str, Any]]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT * FROM creator_deals WHERE creator_id = %s AND brand_id = %s", (creator_id, brand_id))
    row = cur.fetchone()
    return dict(row) if row else None


ACTIONS = {
    # action: (deal_type or None to keep, stage, timestamp column, reply event)
    "gifted_offer": ("gifted", "offered", "offered_at", "brand_replied_interested"),
    "paid_offer": ("paid", "negotiating", "offered_at", "brand_replied_interested"),
    "question": (None, "replied", None, "brand_replied_question"),
    "booked": (None, "booked", "booked_at", None),
    "shipped": ("gifted", "shipped", "shipped_at", None),
    "arrived": ("gifted", "arrived", "arrived_at", None),
    "posted": (None, "posted", "posted_at", None),
    "paid": ("paid", "paid", "paid_at", None),
    "closed": (None, "closed", "closed_at", "brand_replied_rejected"),
}


def _dollars_to_cents(raw: Any) -> Optional[int]:
    if raw in (None, ""):
        return None
    try:
        return max(0, int(round(float(str(raw).replace("$", "").replace(",", "").strip()) * 100)))
    except (TypeError, ValueError):
        return None


def record_deal(conn, creator_id: int, brand_id: Optional[int], action: str, *, brand_name: str = "",
                amount: Any = None, due_date: Optional[str] = None, payment_due: Optional[str] = None,
                deliverables: Optional[str] = None, reason: Optional[str] = None,
                log_event: bool = True) -> Dict[str, Any]:
    """One tap on a deal: 'Brand replied' answers and every later step. Returns the deal row."""
    ensure_tables(conn)
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action}")
    deal_type, stage, ts_col, reply_event = ACTIONS[action]
    name = (brand_name or _brand_name(conn, brand_id) or "Brand").strip()[:120]
    value = _dollars_to_cents(amount)
    existing = get_deal(conn, creator_id, brand_id) if brand_id else None
    if value is None and not existing and deal_type == "gifted":
        value = _gifted_value(conn, creator_id, brand_id)
    cur = _cur(conn)
    if existing:
        sets = ["stage = %s", "updated_at = NOW()"]
        params: List[Any] = [stage]
        if deal_type:
            sets.append("deal_type = %s")
            params.append(deal_type)
        if ts_col:
            sets.append(f"{ts_col} = COALESCE({ts_col}, NOW())")
        if value is not None:
            sets.append("value_cents = %s")
            params.append(value)
        for col, val in (("due_date", due_date), ("payment_due", payment_due), ("deliverables", deliverables),
                         ("closed_reason", reason)):
            if val:
                sets.append(f"{col} = %s")
                params.append(val)
        if stage != "closed":
            sets.append("closed_at = NULL")
        cur.execute(f"UPDATE creator_deals SET {', '.join(sets)} WHERE id = %s RETURNING *", (*params, existing["id"]))
    else:
        cur.execute(
            f"""INSERT INTO creator_deals (creator_id, brand_id, brand_name, deal_type, stage, value_cents, due_date,
                    payment_due, deliverables, closed_reason{', ' + ts_col if ts_col else ''})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s{', NOW()' if ts_col else ''}) RETURNING *""",
            (creator_id, brand_id, name, deal_type or "gifted", stage, value, due_date or None, payment_due or None,
             deliverables, reason))
    deal = dict(cur.fetchone())
    conn.commit()
    if brand_id and log_event:
        _log_event(conn, creator_id, brand_id, name, action, reply_event, deal)
    return deal


def _gifted_value(conn, creator_id: int, brand_id: Optional[int]) -> Optional[int]:
    if not brand_id:
        return None
    try:
        cur = conn.cursor()
        cur.execute("""SELECT COALESCE(cp.package_value, cp.pr_package_value)
                       FROM creator_pipeline cp WHERE cp.creator_id = %s AND cp.brand_id = %s
                       ORDER BY cp.id DESC LIMIT 1""", (creator_id, brand_id))
        row = cur.fetchone()
        if row and row[0]:
            return int(float(row[0]) * 100)
    except Exception:
        conn.rollback()
    return GIFTED_AVG_CENTS


def _log_event(conn, creator_id, brand_id, name, action, reply_event, deal) -> None:
    """Timeline events keep Autopilot and Polly in sync: a reply event stops that brand's follow-ups."""
    try:
        from services.polly_tracker import add_event
        labels = {
            "gifted_offer": f"Gifted offer · {name}", "paid_offer": f"Paid offer · {name}",
            "question": f"Brand replied · {name}", "booked": f"Booked · {name}", "shipped": f"Box shipped · {name}",
            "arrived": f"Box arrived · {name}", "posted": f"Content posted · {name}", "paid": f"Paid · {name}",
            "closed": f"Closed · {name}",
        }
        event_type = reply_event or {"posted": "content_posted", "arrived": "pr_received", "shipped": "pr_shipped",
                                     "paid": "milestone", "booked": "milestone"}.get(action, "milestone")
        add_event(conn, creator_id, event_type, labels.get(action, name), brand_id=brand_id,
                  event_data={"deal_id": deal.get("id"), "action": action, "value_cents": deal.get("value_cents")})
    except Exception as err:
        print(f"[tracker] event skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Board, calendar, goal progress
# ---------------------------------------------------------------------------

def _fmt_day(dt) -> str:
    dt = _aware(dt)
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}" if dt else ""


def _queue_rows(conn, creator_id: int) -> List[Dict[str, Any]]:
    cur = _cur(conn)
    cur.execute("""SELECT q.id, q.brand_id, q.brand_name, q.status, q.scheduled_for, q.sent_at, q.followups_sent,
                          q.followup_status, q.followup_due_at, q.followup_sent_at, q.pitch_meta, q.created_at,
                          LEFT(q.body, 2000) AS body,
                          b.logo_url AS logo, b.category
                   FROM polly_autopilot_queue q LEFT JOIN pr_brands b ON b.id = q.brand_id
                   WHERE q.creator_id = %s AND q.status IN ('draft', 'approved', 'sent')
                   ORDER BY q.created_at DESC LIMIT 400""", (creator_id,))
    return [dict(r) for r in cur.fetchall()]


_PAID_ASK = re.compile(r"\b(my rate|rates? (?:start|are|is)|per video|paid (?:collab|partnership|ugc)|"
                       r"budget|\$\s?\d{2,})", re.I)


def pitch_offer(meta: Optional[Dict[str, Any]], body: Optional[str] = None) -> str:
    """'paid' | 'gifted' | 'gig' for one Autopilot pitch: the offer Autopilot recorded, else read from the pitch."""
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("origin") == "paid_gig":
        return "gig"
    if meta.get("offer") in ("paid", "gifted"):
        return meta["offer"]
    return "paid" if _PAID_ASK.search(body or "") else "gifted"


# Goal-driven mix: the bigger the goal (vs what one paid video earns), the more pitches ask for a paid deal.
PAID_SHARE_MIN, PAID_SHARE_MAX = 0.3, 0.8


def paid_share(goal_cents: int, followers: int, deal_intent: Optional[str] = None) -> float:
    low, high = rate_band(followers)
    videos = goal_cents / ((low + high) / 2)          # paid videos the goal is worth
    share = PAID_SHARE_MIN + (PAID_SHARE_MAX - PAID_SHARE_MIN) * min(1.0, max(0.0, (videos - 1) / 6))
    if deal_intent == "paid":
        share = max(share, 0.7)
    return round(share, 2)


def next_offer(conn, creator_id: int, deal_intent: Optional[str] = None) -> str:
    """For Autopilot's 'auto' focus: paid or gifted for the next pitch, keeping this month's mix on the goal's share."""
    goal = get_goal(conn, creator_id)
    prof = creator_profile(conn, creator_id)
    cents = int(goal["amount_cents"]) if goal else next(
        o["amount_cents"] for o in suggest_goal(prof)["options"] if o["id"] == "suggested")
    share = paid_share(cents, prof["followers"], deal_intent)
    cur = conn.cursor()
    cur.execute("""SELECT pitch_meta, LEFT(body, 2000) FROM polly_autopilot_queue
                   WHERE creator_id = %s AND created_at >= date_trunc('month', NOW()) AND status <> 'skipped'""",
                (creator_id,))
    offers = [pitch_offer(r[0], r[1]) for r in cur.fetchall()]
    offers = [o for o in offers if o != "gig"]
    paid = sum(1 for o in offers if o == "paid")
    return "paid" if paid < share * (len(offers) + 1) else "gifted"


def _autopilot_on(conn, creator_id: int) -> bool:
    try:
        cur = conn.cursor()
        cur.execute("SELECT enabled FROM polly_autopilot WHERE creator_id = %s", (creator_id,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        conn.rollback()
        return False


def _manual_pitches(conn, creator_id: int) -> List[Dict[str, Any]]:
    cur = _cur(conn)
    cur.execute("""SELECT DISTINCT ON (cp.brand_id) cp.brand_id, b.brand_name, b.logo_url AS logo, b.category,
                          COALESCE(cp.pitched_at, cp.created_at) AS pitched_at, cp.follow_up_count, cp.followup_count,
                          cp.expected_delivery, cp.content_deadline
                   FROM creator_pipeline cp JOIN pr_brands b ON b.id = cp.brand_id
                   WHERE cp.creator_id = %s AND (cp.pitched_at IS NOT NULL OR cp.send_confirmed IS TRUE)
                   ORDER BY cp.brand_id, cp.id DESC""", (creator_id,))
    return [dict(r) for r in cur.fetchall()]


def _reply_events(conn, creator_id: int) -> Dict[int, Dict[str, Any]]:
    cur = _cur(conn)
    cur.execute("""SELECT DISTINCT ON (brand_id) brand_id, event_type, occurred_at FROM polly_timeline_events
                   WHERE creator_id = %s AND brand_id IS NOT NULL AND event_type = ANY(%s)
                   ORDER BY brand_id, occurred_at DESC""", (creator_id, list(REPLY_EVENTS)))
    return {int(r["brand_id"]): dict(r) for r in cur.fetchall()}


def _kit_views(conn, creator_id: int) -> Dict[int, int]:
    cur = _cur(conn)
    cur.execute("""SELECT brand_id, COUNT(*) AS n FROM polly_timeline_events
                   WHERE creator_id = %s AND event_type = 'portfolio_viewed' AND brand_id IS NOT NULL
                   GROUP BY brand_id""", (creator_id,))
    return {int(r["brand_id"]): int(r["n"]) for r in cur.fetchall()}


def _deals(conn, creator_id: int) -> List[Dict[str, Any]]:
    cur = _cur(conn)
    cur.execute("""SELECT d.*, b.logo_url AS logo FROM creator_deals d LEFT JOIN pr_brands b ON b.id = d.brand_id
                   WHERE d.creator_id = %s ORDER BY d.updated_at DESC LIMIT 300""", (creator_id,))
    return [dict(r) for r in cur.fetchall()]


def _hot_today(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    try:
        from services.hot_brands import hot_brand_today
        return hot_brand_today(conn, creator_id)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def free_balance(conn, creator_id: int, profile: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """None on Pro. Free: the same monthly credit balance Polly's send gate uses (3 brands a month)."""
    if profile.get("is_pro"):
        return None
    try:
        from pr_crm_routes import get_creator_unlock_balance
        bal = get_creator_unlock_balance(creator_id, conn=conn) or {}
        if bal.get("is_unlimited"):
            return None
        limit = int(bal.get("limit") if bal.get("limit") is not None else FREE_MONTHLY_PITCHES)
        left = int(bal.get("remaining") if bal.get("remaining") is not None else limit)
        return {"left": max(0, left), "limit": limit}
    except Exception as err:
        print(f"[tracker] balance fallback creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {"left": FREE_MONTHLY_PITCHES, "limit": FREE_MONTHLY_PITCHES}


def _deal_next(deal: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    stage, kind = deal.get("stage"), deal.get("deal_type")
    due = deal.get("due_date")
    if stage == "replied":
        return {"label": "Answer their question", "your_move": True, "action": "answer"}
    if stage == "offered" and kind == "gifted":
        return {"label": "Send your shipping address", "your_move": True, "action": "address"}
    if stage == "negotiating":
        return {"label": "Answer with your rate", "your_move": True, "action": "answer"}
    if stage == "booked":
        if due:
            return {"label": f"Draft due {_fmt_day(due)}", "date": str(due), "your_move": _aware(due) <= now + timedelta(days=2)}
        return {"label": "Agree the delivery date", "your_move": True, "action": "date"}
    if stage == "shipped":
        return {"label": "Box on its way", "your_move": False}
    if stage == "arrived":
        return {"label": "Film and post your video", "your_move": True, "action": "post"}
    if stage == "posted" and kind == "gifted":
        posted = _aware(deal.get("posted_at"))
        if posted and now - posted >= timedelta(days=3):
            return {"label": "Ask them to run it as an ad", "your_move": True, "action": "ad_usage"}
        return {"label": "Posted", "your_move": False}
    if stage == "posted":
        pay = deal.get("payment_due")
        return {"label": f"Payment due {_fmt_day(pay)}" if pay else "Waiting for payment", "date": str(pay) if pay else None,
                "your_move": bool(pay and _aware(pay) < now)}
    if stage == "paid":
        return {"label": f"Paid {_fmt_day(deal.get('paid_at'))}", "your_move": False}
    return {"label": deal.get("closed_reason") or "Closed", "your_move": False}


def build_tracker(conn, creator_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    ensure_tables(conn)
    now = now or _now()
    profile = creator_profile(conn, creator_id)
    m_start, m_end = month_bounds(now)

    deals = _deals(conn, creator_id)
    deal_by_brand = {int(d["brand_id"]): d for d in deals if d.get("brand_id")}
    replies = _reply_events(conn, creator_id)
    views = _kit_views(conn, creator_id)
    hot = _hot_today(conn, creator_id)
    hot_id = int(hot["brand_id"]) if hot and hot.get("brand_id") else None
    balance = free_balance(conn, creator_id, profile)
    left = None if balance is None else balance["left"]

    cards: Dict[int, Dict[str, Any]] = {}
    calendar: List[Dict[str, Any]] = []
    autopilot_on = _autopilot_on(conn, creator_id)

    def card(bid, name, logo=None, category=None):
        c = cards.get(bid)
        if not c:
            c = cards[bid] = {"brand_id": bid, "brand_name": name or "Brand", "logo": logo, "category": category,
                              "type": "gifted", "value_cents": None, "column": "lined_up", "next": {},
                              "kit_views": views.get(bid, 0), "hot": bid == hot_id, "locked": False}
        return c

    # Autopilot queue: lined up, pitched, followed up (+ calendar).
    lined: List[Dict[str, Any]] = []
    for q in _queue_rows(conn, creator_id):
        bid = int(q["brand_id"]) if q.get("brand_id") else None
        if not bid:
            continue
        meta = q.get("pitch_meta") if isinstance(q.get("pitch_meta"), dict) else {}
        c = card(bid, q.get("brand_name"), q.get("logo"), q.get("category"))
        c["type"] = pitch_offer(meta, q.get("body"))
        if q.get("sent_at"):
            fu = int(q.get("followups_sent") or 0)
            col = "followed_up" if fu else "pitched"
            if c["column"] in ("lined_up", "pitched"):
                c["column"] = col
                due = q.get("followup_due_at") if q.get("followup_status") in ("scheduled", "headsup") else None
                c["next"] = ({"label": f"Follow-up {_fmt_day(due)}", "date": _aware(due).date().isoformat()} if due
                             else {"label": "Waiting for a reply"})
            sent = _aware(q["sent_at"])
            calendar.append({"date": sent.date().isoformat(), "kind": "sent", "brand_id": bid,
                             "brand_name": c["brand_name"], "label": "Pitch sent"})
            if q.get("followup_status") in ("scheduled", "headsup") and q.get("followup_due_at"):
                fd = _aware(q["followup_due_at"])
                calendar.append({"date": fd.date().isoformat(), "kind": "fu", "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": f"Follow-up {fu + 1}"})
            if q.get("followup_sent_at"):
                fs = _aware(q["followup_sent_at"])
                calendar.append({"date": fs.date().isoformat(), "kind": "fu", "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": "Follow-up sent"})
        elif c["column"] == "lined_up":
            when = _aware(q.get("scheduled_for"))
            if q.get("status") == "draft":
                c["next"] = {"label": "Pitch ready to check", "your_move": not autopilot_on}
            elif when and when < now - timedelta(hours=1):
                # Past its slot: paused Autopilot (or a send that failed and waits for the next run).
                c["next"] = {"label": "Autopilot paused" if not autopilot_on else "Sending next"}
                when = None
            else:
                c["next"] = {"label": f"Pitch {_fmt_day(when)}" if when else "Pitch being written",
                             "date": when.date().isoformat() if when else None}
            lined.append(c)
            if when:
                calendar.append({"date": when.date().isoformat(), "kind": "sched", "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": f"Pitch {when.strftime('%H:%M')}",
                                 "hot": bid == hot_id})

    # Pitches sent with Polly (Send for me / Open email) that aren't in the Autopilot queue.
    for p in _manual_pitches(conn, creator_id):
        bid = int(p["brand_id"])
        c = card(bid, p.get("brand_name"), p.get("logo"), p.get("category"))
        if c["column"] == "lined_up":
            c["column"] = "followed_up" if int(p.get("followup_count") or p.get("follow_up_count") or 0) else "pitched"
            c["next"] = {"label": "Waiting for a reply"}
            if p.get("pitched_at"):
                calendar.append({"date": _aware(p["pitched_at"]).date().isoformat(), "kind": "sent", "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": "Pitch sent"})
        for col, kind, label in (("expected_delivery", "ship", "Box arrives"), ("content_deadline", "post", "Post video")):
            if p.get(col):
                calendar.append({"date": _aware(p[col]).date().isoformat(), "kind": kind, "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": label})

    # Replies logged before deals existed (check-ins): replied, or closed when they passed.
    for bid, ev in replies.items():
        if bid in deal_by_brand:
            continue
        c = cards.get(bid) or card(bid, _brand_name(conn, bid))
        if ev["event_type"] == "brand_replied_rejected":
            c["column"], c["next"] = "closed", {"label": "They passed"}
        else:
            c["column"] = "replied"
            c["next"] = {"label": "Tell me what they offered", "your_move": True, "action": "replied"}

    # Deals win: they're what the creator told us.
    for d in deals:
        bid = int(d["brand_id"]) if d.get("brand_id") else -int(d["id"])
        c = cards.get(bid) or card(bid, d.get("brand_name"), d.get("logo"))
        c.update({"type": d.get("deal_type") or c["type"], "value_cents": d.get("value_cents"),
                  "column": STAGE_COLUMN.get(d.get("stage"), "replied"), "deal_id": d["id"], "stage": d.get("stage"),
                  "next": _deal_next(d, now)})
        for col, kind, label in (("due_date", "due", "Draft due"), ("payment_due", "pay", "Payment due")):
            if d.get(col) and d.get("stage") not in ("paid", "closed"):
                calendar.append({"date": _aware(d[col]).date().isoformat(), "kind": kind, "brand_id": bid,
                                 "brand_name": c["brand_name"], "label": label})

    # Hot brand of the day: on the board even when it isn't queued (free creators).
    if hot and hot_id and hot_id not in cards:
        c = card(hot_id, hot.get("brand_name"), hot.get("logo"))
        c["next"] = {"label": "Hot today: pitch ready"}
        lined.insert(0, c)
    if hot and hot_id:
        cards[hot_id]["hot_reason"] = hot.get("reason")
        if not any(e.get("hot") for e in calendar):
            calendar.append({"date": now.date().isoformat(), "kind": "hot", "brand_id": hot_id,
                             "brand_name": cards[hot_id]["brand_name"], "label": "Hot today", "hot": True})

    # Free plan: lined-up brands beyond the remaining free pitches show as locked ("Sends on Pro").
    if left is not None:
        for i, c in enumerate([c for c in cards.values() if c["column"] == "lined_up"]):
            if i >= left:
                c["locked"] = True

    columns = []
    for col_id, label in COLUMNS:
        items = [c for c in cards.values() if c["column"] == col_id]
        items.sort(key=lambda c: (not c.get("hot"), not (c.get("next") or {}).get("your_move"),
                                  (c.get("next") or {}).get("date") or "9999"))
        columns.append({"id": col_id, "label": label, "count": len(items), "cards": items})
    closed = [c for c in cards.values() if c["column"] == "closed"]

    # Goal progress: paid deals paid this month + gifted products received or posted this month.
    paid = gifted = pipeline = 0
    negotiating_n = booked_n = 0
    replied_month = 0
    for d in deals:
        v = int(d.get("value_cents") or 0)
        paid_at, got = _aware(d.get("paid_at")), _aware(d.get("arrived_at") or d.get("posted_at"))
        if d.get("deal_type") in ("paid", "gig") and paid_at and m_start <= paid_at < m_end:
            paid += v
        elif d.get("deal_type") == "gifted" and got and m_start <= got < m_end:
            gifted += v
        if d.get("stage") == "negotiating":
            negotiating_n += 1
            pipeline += v
        if d.get("stage") in ("booked", "shipped", "arrived", "posted", "paid"):
            booked_n += 1
            if d.get("deal_type") != "gifted" and d.get("stage") != "paid":
                pipeline += v
        off = _aware(d.get("offered_at") or d.get("created_at"))
        if off and m_start <= off < m_end:
            replied_month += 1
    for bid, ev in replies.items():
        occ = _aware(ev.get("occurred_at"))
        if bid not in deal_by_brand and occ and m_start <= occ < m_end:
            replied_month += 1
    pitched_month = sum(1 for e in calendar if e["kind"] == "sent" and e["date"] >= m_start.date().isoformat()
                        and e["date"] < m_end.date().isoformat())

    goal = get_goal(conn, creator_id)
    suggestion = suggest_goal(profile)
    goal_cents = int(goal["amount_cents"]) if goal else next(
        o["amount_cents"] for o in suggestion["options"] if o["id"] == suggestion["recommended"])
    math_ = pitch_math(goal_cents, profile["followers"], own_rates(conn, creator_id))

    window_start, window_end = (now - timedelta(days=7)).date().isoformat(), (now + timedelta(days=21)).date().isoformat()
    calendar = sorted([e for e in calendar if window_start <= e["date"] <= window_end],
                      key=lambda e: (e["date"], not e.get("hot"), e["kind"]))
    your_move = [c for c in cards.values() if (c.get("next") or {}).get("your_move")]

    return {
        "month": month_key(now),
        "days_left": max(0, (m_end - now).days),
        "is_pro": bool(profile.get("is_pro")),
        "goal": {
            "set": bool(goal), "amount_cents": goal_cents, "source": (goal or {}).get("source", "default"),
            "paid_cents": paid, "gifted_cents": gifted, "total_cents": paid + gifted, "pipeline_cents": pipeline,
            "math": math_,
            "paid_share": paid_share(goal_cents, profile["followers"]),
        },
        "suggestion": suggestion,
        "funnel": {"pitched": pitched_month, "pitches_needed": math_["pitches"], "replied": replied_month,
                   "negotiating": negotiating_n, "negotiating_cents": sum(int(d.get("value_cents") or 0)
                                                                           for d in deals if d.get("stage") == "negotiating"),
                   "booked_or_paid": booked_n},
        "columns": columns,
        "closed": closed,
        "calendar": calendar,
        "your_move_count": len(your_move),
        "hot": hot,
        "free": None if balance is None else {"pitches_left": left, "limit": balance["limit"],
                                           "locked": sum(1 for c in cards.values() if c.get("locked"))},
    }


def brand_detail(conn, creator_id: int, brand_id: int) -> Dict[str, Any]:
    """One card's history (existing timeline) plus its deal."""
    out: Dict[str, Any] = {"brand_id": brand_id, "deal": get_deal(conn, creator_id, brand_id)}
    try:
        from services.polly_tracker import brand_timeline
        out.update(brand_timeline(conn, creator_id, brand_id) or {})
    except Exception as err:
        print(f"[tracker] brand timeline skipped: {err}")
        conn.rollback()
    if out.get("deal"):
        out["deal"]["next"] = _deal_next(out["deal"], _now())
    return out
