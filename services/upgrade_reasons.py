"""Why this creator should upgrade right now: one headline led by their strongest real trigger.

Used by every "free pitches are out" moment (the in-app card, services/waiting_room; the email,
templates/lifecycle/16_max_quota_hit.html) so they say the same thing. Strongest trigger first, all from their own
data, nothing invented:

  1. reply      a brand they pitched replied (they told Polly) in the last 30 days
  2. kit_view   a brand opened their portfolio in the last 14 days
  3. hot        matched brands in their niche running UGC campaigns right now (Thanos picks with creator-style ads
                or confirmed gifting)
  4. matched    how many brands Thanos has lined up in their niche that they haven't pitched

Trial wording follows checkout (quota_trial_test.checkout_trial_eligible): "7 days free" only when Stripe will
give the free week; creators who chose to pitch manually see $19/mo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question")
MATCH_DEPTH = 40

BODY = "You've used your free pitches. Let me take over your outreach so you can focus on creating."
CTA = "Let Polly take over"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _one(conn, sql: str, params) -> Optional[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def _latest_brand(conn, creator_id: int, events, days: int) -> Optional[str]:
    row = _one(conn, """
        SELECT COALESCE(b.brand_name, e.event_data->>'brand_name') AS name
        FROM polly_timeline_events e LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.creator_id = %s AND e.event_type = ANY(%s) AND e.occurred_at > %s
          AND COALESCE(b.brand_name, e.event_data->>'brand_name') IS NOT NULL
        ORDER BY e.occurred_at DESC LIMIT 1
    """, (creator_id, list(events), _now() - timedelta(days=days)))
    return (row or {}).get("name")


def _matches(conn, creator_id: int):
    from services.thanos import rank
    ranking = rank(conn, creator_id, surface="polly", n=MATCH_DEPTH)
    cards = ranking.brands or []
    hot = [c.get("name") for c in cards if (c.get("ugc_style") or c.get("gifting_confirmed")) and c.get("name")]
    return len(cards), hot


def _niche(conn, creator_id: int) -> str:
    try:
        from services.creator_tracker import creator_profile
        niches = [str(n).replace("_", " ") for n in (creator_profile(conn, creator_id).get("niches") or []) if n]
    except Exception:  # noqa: BLE001
        niches = []
    return " and ".join(niches[:2])


def _trial(conn, creator_id: int) -> bool:
    try:
        from services.quota_trial_test import checkout_trial_eligible
        return bool(checkout_trial_eligible(conn.cursor(), creator_id))
    except Exception:  # noqa: BLE001
        return False


def _both(names: List[str]) -> str:
    names = [n for n in names if n][:2]
    return " and ".join(names)


def compose(lead: str, *, brand: Optional[str] = None, hot: Optional[List[str]] = None, matched: int = 0,
            niche: str = "", trial: bool = False, reset: str = "") -> Dict[str, Any]:
    """The words, from the facts. Pure, so every channel and the tests agree."""
    hot = [h for h in (hot or []) if h]
    content = f"your {niche} content" if niche else "your content"
    more = max(0, matched - (2 if lead == "hot" else 0))
    if lead == "reply" and brand:
        headline = f"{brand} replied. Let's line up more like them."
        reason = (f"Brands that answer creators your size are the ones to chase. I've found {matched} more that fit "
                  f"{content}." if matched else "Brands that answer creators your size are the ones to chase.")
    elif lead == "kit_view" and brand:
        headline = f"{brand} just opened your portfolio."
        reason = (f"Brands that look usually reply. I've found {matched} more brands that fit {content}."
                  if matched else "Brands that look usually reply. Let's keep them coming.")
    elif lead == "hot" and hot:
        pair = _both(hot)
        verb = "is" if len(hot[:2]) == 1 else "are"
        headline = f"{pair} {verb} running UGC campaigns right now."
        reason = (f"They fit {content}, and I've lined up {more} more brands like them."
                  if more else f"They fit {content}.")
    else:
        lead = "matched"
        headline = "Your next brand opportunities are waiting."
        if matched:
            extra = ", including brands already running UGC campaigns" if hot else ""
            reason = f"I've found {matched} brands that fit {content}{extra}."
        else:
            reason = f"I keep finding new brands that fit {content}."
    price = "7 days free, then $19/mo. Cancel anytime." if trial else "$19/mo. Cancel anytime."
    return {
        "lead": lead,
        "headline": headline,
        "reason": reason,
        "body": BODY,
        "cta": CTA,
        "price": price,
        "trial": bool(trial),
        "brands": hot[:3],
        "matched": int(matched or 0),
        "reset": reset or "",
    }


def build(conn, creator_id: int, reset: str = "") -> Dict[str, Any]:
    """Never raises: any missing piece just falls back to the general line."""
    def safe(fn, default):
        try:
            return fn()
        except Exception as err:  # noqa: BLE001
            print(f"[upgrade_reasons] partial creator={creator_id}: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
            return default

    replied = safe(lambda: _latest_brand(conn, creator_id, REPLY_EVENTS, 30), None)
    viewed = None if replied else safe(lambda: _latest_brand(conn, creator_id, ("portfolio_viewed",), 14), None)
    matched, hot = safe(lambda: _matches(conn, creator_id), (0, []))
    lead = "reply" if replied else "kit_view" if viewed else "hot" if hot else "matched"
    return compose(lead, brand=replied or viewed, hot=hot, matched=matched,
                   niche=safe(lambda: _niche(conn, creator_id), ""), trial=_trial(conn, creator_id), reset=reset)
