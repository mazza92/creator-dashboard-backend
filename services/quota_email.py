"""Context for the "free pitches are out" email (lifecycle max_quota_hit).

Same story as the in-app upsell card (routes/polly._upsell_payload): the creator's own brands waiting in their plan
(🔥 = running Meta ads), how many brands opened their kit, their goal and days left. Falls back to hot brands from
their niche when there's no plan yet. Every part is optional: the template drops what's missing.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

SHOWN = 3


def _money(cents: int) -> str:
    dollars = int(round((cents or 0) / 100))
    return f"${dollars:,}"


def _waiting(conn, creator_id: int) -> List[Dict[str, Any]]:
    from services.creator_tracker import planned_rows
    today = date.today()
    return [{"brand_name": r.get("brand_name") or "Brand", "offer": r.get("offer") or "gifted",
             "meta_ads": bool(r.get("meta_ads"))}
            for r in planned_rows(conn, creator_id) if r.get("planned_for") and r["planned_for"] >= today]


def _hot_fallback(conn, creator_id: int) -> List[Dict[str, Any]]:
    from services.weekly_hotlist import weekly_hotlist
    return [{"brand_name": b["brand_name"], "offer": "gifted", "meta_ads": bool(b.get("ads"))}
            for b in weekly_hotlist(conn, creator_id, limit=SHOWN)]


def _kit_opens(conn, creator_id: int) -> int:
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(DISTINCT brand_id) FROM polly_timeline_events
                   WHERE creator_id = %s AND event_type = 'portfolio_viewed' AND brand_id IS NOT NULL
                     AND occurred_at >= %s""",
                (creator_id, datetime.now(timezone.utc) - timedelta(days=30)))
    row = cur.fetchone()
    return int((row[0] if not isinstance(row, dict) else list(row.values())[0]) or 0) if row else 0


def quota_email_context(conn, creator_id: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"waiting": [], "waiting_count": 0, "waiting_more": 0, "from_plan": False,
                           "kit_views": 0, "goal_label": "", "days_left": 0}
    steps = (
        ("plan", lambda: _waiting(conn, creator_id)),
        ("kit", lambda: _kit_opens(conn, creator_id)),
    )
    for name, fn in steps:
        try:
            value = fn()
        except Exception as err:  # optional pieces: never block the email
            print(f"[quota_email] {name} skipped: {err}")
            conn.rollback()
            continue
        if name == "plan" and value:
            out.update(waiting=value[:SHOWN], waiting_count=len(value),
                       waiting_more=max(0, len(value) - SHOWN), from_plan=True)
        elif name == "kit":
            out["kit_views"] = value
    if not out["waiting"]:
        try:
            out["waiting"] = _hot_fallback(conn, creator_id)
        except Exception as err:
            print(f"[quota_email] hotlist skipped: {err}")
            conn.rollback()
    try:
        from services.creator_tracker import get_goal, month_bounds
        goal = get_goal(conn, creator_id)
        if goal and goal.get("amount_cents"):
            out["goal_label"] = _money(int(goal["amount_cents"]))
        out["days_left"] = max(0, (month_bounds()[1] - datetime.now(timezone.utc)).days)
    except Exception as err:
        print(f"[quota_email] goal skipped: {err}")
        conn.rollback()
    return out


def quota_preheader(ctx: Dict[str, Any], trial: bool = False) -> str:
    line = _preheader(ctx)
    return f"{line} Try Pro free for 7 days." if trial else line


def _preheader(ctx: Dict[str, Any]) -> str:
    if ctx.get("from_plan") and ctx.get("waiting_count"):
        n = ctx["waiting_count"]
        return f"Nice start! {n} brand{'' if n == 1 else 's'} from your plan {'is' if n == 1 else 'are'} ready to pitch next."
    if ctx.get("kit_views"):
        n = ctx["kit_views"]
        return f"{n} brand{'' if n == 1 else 's'} opened your kit. Keep the momentum going."
    return "Your next brands are lined up. Keep the momentum going."
