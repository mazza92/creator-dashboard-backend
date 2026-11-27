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
                       waiting_more=max(0, len(value) - SHOWN), from_plan=True,
                       hours=max(1, round(len(value) * 12 / 60)))  # ~12 min of finding + writing per brand
        elif name == "kit":
            out["kit_views"] = value
    if not out["waiting"]:
        try:
            out["waiting"] = _hot_fallback(conn, creator_id)
        except Exception as err:
            print(f"[quota_email] hotlist skipped: {err}")
            conn.rollback()
    try:
        from services.upgrade_reasons import build as build_reasons
        out["reasons"] = build_reasons(conn, creator_id)
    except Exception as err:
        print(f"[quota_email] reasons skipped: {err}")
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


def quota_subject(ctx: Dict[str, Any]) -> str:
    """The lead trigger is the subject: "Glow Recipe just opened your portfolio." beats "Your 3 free pitches are out"."""
    reasons = ctx.get("reasons") or {}
    return str(reasons.get("headline") or "Your next brand opportunities are waiting.")


def quota_preheader(ctx: Dict[str, Any], trial: bool = False) -> str:
    reasons = ctx.get("reasons") or {}
    if reasons.get("reason"):
        return f"{reasons['reason']} {'Try Pro free for 7 days.' if trial else ''}".strip()
    line = _preheader(ctx)
    return f"{line} Try Pro free for 7 days." if trial else line


def _preheader(ctx: Dict[str, Any]) -> str:
    if ctx.get("from_plan") and ctx.get("waiting_count"):
        n = ctx["waiting_count"]
        return f"Nice start! Now let me pitch the other {n} brand{'' if n == 1 else 's'} for you."
    if ctx.get("kit_views"):
        n = ctx["kit_views"]
        return f"{n} brand{'' if n == 1 else 's'} opened your kit. Let me keep pitching for you."
    return "Nice start! Now let me do the pitching for you."


CATCHUP_SQL = """
    WITH delivered AS (
        SELECT creator_id, COUNT(*) AS n FROM (
            SELECT bu.creator_id, bu.brand_id FROM brand_unlocks bu
            WHERE bu.unlocked_at >= date_trunc('month', NOW())
              AND EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
            UNION
            SELECT pp.creator_id, pp.brand_id FROM pr_packages pp
            WHERE pp.generated_at IS NOT NULL AND pp.generated_at >= date_trunc('month', NOW())
        ) x GROUP BY 1
    )
    SELECT c.id
    FROM delivered d JOIN creators c ON c.id = d.creator_id JOIN users u ON u.id = c.user_id
    WHERE d.n >= %(limit_n)s
      AND COALESCE(c.subscription_tier, 'free') = 'free'
      AND c.approval_status IN ('approved', 'pro_approved')
      AND u.unsubscribed_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM lifecycle_email_sends s
                      WHERE s.creator_id = c.id AND s.template_slug = 'max_quota_hit'
                        AND s.sent_at >= date_trunc('month', NOW()))
      -- trigger_quota_hit skips these without recording anything: leave them out so they aren't retried forever
      AND NOT EXISTS (SELECT 1 FROM polly_email_log l
                      WHERE l.creator_id = c.id AND l.kind = 'paywall_followup' AND l.ok
                        AND l.sent_at >= date_trunc('month', NOW()))
    ORDER BY random()  -- anyone the engine throttles today doesn't hold the queue
    LIMIT %(batch)s
"""


def quota_catchup(conn, batch: int = 25, send=None) -> Dict[str, Any]:
    """Every at-limit creator gets the "free pitches are out" email once a month, however their 3rd pitch went
    out. It fired only on some send paths: on Nov 27, 179 of 250 at-limit creators had never had it tried.
    trigger_quota_hit keeps its own rules (quota really hit, once a month, unsubscribes, holdout)."""
    from services.unlock_quota import FREE_UNLOCK_LIMIT
    cur = conn.cursor()
    cur.execute(CATCHUP_SQL, {"limit_n": FREE_UNLOCK_LIMIT, "batch": batch})
    ids = [int(r[0] if not isinstance(r, dict) else r["id"]) for r in cur.fetchall()]
    if send is None:
        from lifecycle_email_engine import trigger_quota_hit as send
    sent = 0
    for cid in ids:
        try:
            ok, _msg = send(cid)
            sent += 1 if ok else 0
        except Exception as err:  # noqa: BLE001
            print(f"[quota_email] catch-up skipped creator={cid}: {err}")
    return {"candidates": len(ids), "sent": sent}
