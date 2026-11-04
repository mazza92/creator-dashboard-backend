"""Founder dashboard: MRR milestones on the way to $10k, growth pace, and the 7-day free trial funnel.

Stripe MRR only counts active subscriptions, so trialists are pipeline until their first charge.
A trial is anyone who got the trial welcome (logged at checkout) or is trialing now.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

ULTIMATE_GOAL = 10000
MILESTONES = (100, 250, 500, 1000, 2000, 3000, 5000, 7500, 10000)
TRIAL_DAYS = 7
BENCHMARK_TRIAL_RATE = 50
MIN_ENDED_FOR_RATE = 5
LOST_STATUSES = ("canceled", "incomplete_expired", "unpaid", "incomplete")


def milestones(current_mrr: float, price: int) -> Dict[str, Any]:
    """Current stage = the next rung above today's MRR; the bar runs from $0 to that rung."""
    current = max(0.0, float(current_mrr or 0))
    goal = next((m for m in MILESTONES if m > current), ULTIMATE_GOAL)
    after = next((m for m in MILESTONES if m > goal), None)
    reached = [m for m in MILESTONES if m <= current]

    def subs_for(target: float) -> int:
        return max(0, math.ceil((target - current) / price)) if price else 0

    return {
        "goal": goal,
        "previous": reached[-1] if reached else 0,
        "next_after": after,
        "progress_pct": min(round(current / goal * 100, 1), 100) if goal else 100,
        "subs_needed": subs_for(goal),
        "scale": [round(goal * q / 4) for q in range(5)],
        "ultimate": ULTIMATE_GOAL,
        "ultimate_pct": min(round(current / ULTIMATE_GOAL * 100, 1), 100),
        "ultimate_subs_needed": subs_for(ULTIMATE_GOAL),
        "ladder": [{"mrr": m, "reached": m <= current} for m in MILESTONES],
    }


def pace(new_paid_30d: int, churned_30d: int, current_mrr: float, goal: float, price: int) -> Dict[str, Any]:
    """Net new MRR over the last 30 days, and months to the stage goal and to $10k at that pace."""
    net_subs = int(new_paid_30d or 0) - int(churned_30d or 0)
    net_mrr = net_subs * price

    def months_to(target: float) -> Optional[float]:
        gap = target - float(current_mrr or 0)
        if gap <= 0:
            return 0
        return round(gap / net_mrr, 1) if net_mrr > 0 else None

    return {
        "new_paid_30d": int(new_paid_30d or 0),
        "churned_30d": int(churned_30d or 0),
        "net_subs_30d": net_subs,
        "net_mrr_30d": net_mrr,
        "months_to_goal": months_to(goal),
        "months_to_ultimate": months_to(ULTIMATE_GOAL),
    }


def summarize_trials(rows: List[Dict[str, Any]], price: int) -> Dict[str, Any]:
    active = [r for r in rows if r.get("status") == "trialing"]
    converted = [r for r in rows if r.get("status") == "active"]
    failing = [r for r in rows if r.get("status") == "past_due"]
    lost = [r for r in rows if r.get("status") in LOST_STATUSES or
            (r.get("status") not in ("trialing", "active", "past_due") and r.get("ended"))]
    cancel_scheduled = [r for r in active if r.get("cancel_scheduled")]
    ended = len(converted) + len(lost) + len(failing)
    rate = round(len(converted) / ended * 100, 1) if ended else None
    use_observed = rate is not None and ended >= MIN_ENDED_FOR_RATE
    expected_rate = rate if use_observed else BENCHMARK_TRIAL_RATE
    pipeline = (len(active) - len(cancel_scheduled)) * price
    return {
        "started": len(rows),
        "active": len(active),
        "cancel_scheduled": len(cancel_scheduled),
        "gmail_connected": sum(1 for r in active if r.get("gmail")),
        "sending": sum(1 for r in active if (r.get("sent") or 0) > 0),
        "converted": len(converted),
        "lost": len(lost),
        "payment_failing": len(failing),
        "ended": ended,
        "conversion_rate": rate,
        "expected_rate": expected_rate,
        "expected_rate_basis": "observed" if use_observed else "benchmark",
        "pipeline_mrr": pipeline,
        "expected_mrr": round(pipeline * expected_rate / 100),
        "mrr_from_trials": len(converted) * price,
        "upcoming": [
            {k: r.get(k) for k in ("creator_id", "username", "trial_ends", "gmail", "sent", "cancel_scheduled")}
            for r in sorted(active, key=lambda r: str(r.get("trial_ends") or ""))
        ][:20],
    }


TRIAL_ROWS_SQL = """
    WITH starts AS (
        SELECT creator_id, MIN(sent_at) AS started FROM polly_email_log
        WHERE kind = 'trial_started' GROUP BY creator_id
        UNION ALL
        SELECT id, subscription_started_at FROM creators
        WHERE subscription_status = 'trialing' AND subscription_started_at IS NOT NULL
    ), trials AS (
        SELECT creator_id, MIN(started) AS started FROM starts GROUP BY creator_id
    )
    SELECT c.id AS creator_id, c.username, c.subscription_status AS status, t.started,
           t.started + make_interval(days => %(days)s) AS trial_ends,
           NOW() >= t.started + make_interval(days => %(days)s) AS ended,
           c.stripe_subscription_id,
           EXISTS (SELECT 1 FROM polly_gmail_accounts g
                   WHERE g.creator_id = c.id AND g.revoked_at IS NULL) AS gmail,
           (SELECT COUNT(*) FROM polly_autopilot_queue q
            WHERE q.creator_id = c.id AND q.status = 'sent' AND q.sent_at >= t.started) AS sent
    FROM trials t JOIN creators c ON c.id = t.creator_id
    WHERE t.started >= %(since)s
    ORDER BY t.started
"""

PAID_STARTS_SQL = """
    SELECT COUNT(*) AS n FROM creators c
    WHERE c.subscription_started_at >= %(since)s
      AND c.subscription_status IN ('active', 'past_due')
      AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.kind = 'trial_started')
"""

PACE_SQL = """
    WITH trials AS (SELECT DISTINCT creator_id FROM polly_email_log WHERE kind = 'trial_started')
    SELECT
      COUNT(*) FILTER (
        WHERE c.subscription_status IN ('active', 'past_due')
          AND c.subscription_started_at + CASE WHEN t.creator_id IS NULL THEN INTERVAL '0 days'
                                               ELSE make_interval(days => %(days)s) END
              >= NOW() - INTERVAL '30 days'
      ) AS new_paid,
      COUNT(*) FILTER (
        WHERE c.subscription_status NOT IN ('active', 'past_due', 'trialing')
          AND c.subscription_ends_at >= NOW() - INTERVAL '30 days'
          AND NOT (t.creator_id IS NOT NULL
                   AND c.subscription_ends_at < c.subscription_started_at + make_interval(days => %(days)s + 1))
      ) AS churned
    FROM creators c LEFT JOIN trials t ON t.creator_id = c.id
    WHERE c.subscription_started_at IS NOT NULL
"""


# Paid subscribers only: trial-only cancels (ended inside the free week) are trial losses, not churn.
# A trialist counts as paid from their first charge. One row per creator, so a resubscribe counts once.
CHURN_SUBS_CTE = """
    WITH trials AS (SELECT DISTINCT creator_id FROM polly_email_log WHERE kind = 'trial_started'),
    subs AS (
      SELECT c.subscription_started_at + CASE WHEN t.creator_id IS NULL THEN INTERVAL '0 days'
                                              ELSE make_interval(days => %(days)s) END AS paid_from,
             CASE WHEN c.subscription_status NOT IN ('active', 'past_due', 'trialing')
                  THEN c.subscription_ends_at END AS lost_at,
             (t.creator_id IS NOT NULL
              AND c.subscription_status NOT IN ('active', 'past_due', 'trialing')
              AND c.subscription_ends_at < c.subscription_started_at + make_interval(days => %(days)s + 1)) AS trial_only
      FROM creators c LEFT JOIN trials t ON t.creator_id = c.id
      WHERE c.subscription_started_at IS NOT NULL
    ), paid AS (SELECT * FROM subs WHERE NOT trial_only AND paid_from <= NOW())
"""

CHURN_MONTHS_SQL = CHURN_SUBS_CTE + """
    , months AS (
      SELECT generate_series(date_trunc('month', NOW()) - make_interval(months => %(months)s - 1),
                             date_trunc('month', NOW()), INTERVAL '1 month') AS m
    )
    SELECT m AS month,
      (SELECT COUNT(*) FROM paid WHERE paid_from < m AND (lost_at IS NULL OR lost_at >= m)) AS start_subs,
      (SELECT COUNT(*) FROM paid WHERE paid_from >= m AND paid_from < m + INTERVAL '1 month') AS new_paid,
      (SELECT COUNT(*) FROM paid WHERE lost_at >= m AND lost_at < m + INTERVAL '1 month') AS churned,
      (SELECT COUNT(*) FROM paid WHERE lost_at >= m AND lost_at < m + INTERVAL '1 month'
                                   AND lost_at < paid_from + INTERVAL '31 days') AS churned_first_month
    FROM months ORDER BY m
"""

CHURN_WINDOW_SQL = CHURN_SUBS_CTE + """
    SELECT
      (SELECT COUNT(*) FROM paid WHERE paid_from < NOW() - INTERVAL '30 days'
                                   AND (lost_at IS NULL OR lost_at >= NOW() - INTERVAL '30 days')) AS start_30,
      (SELECT COUNT(*) FROM paid WHERE paid_from >= NOW() - INTERVAL '30 days') AS new_30,
      (SELECT COUNT(*) FROM paid WHERE lost_at >= NOW() - INTERVAL '30 days') AS churned_30,
      (SELECT COUNT(*) FROM paid WHERE lost_at >= NOW() - INTERVAL '30 days'
                                   AND lost_at < paid_from + INTERVAL '31 days') AS early_30,
      (SELECT COUNT(*) FROM paid WHERE paid_from < NOW() - INTERVAL '60 days'
                                   AND (lost_at IS NULL OR lost_at >= NOW() - INTERVAL '60 days')) AS start_prev,
      (SELECT COUNT(*) FROM paid WHERE paid_from >= NOW() - INTERVAL '60 days'
                                   AND paid_from < NOW() - INTERVAL '30 days') AS new_prev,
      (SELECT COUNT(*) FROM paid WHERE lost_at >= NOW() - INTERVAL '60 days'
                                   AND lost_at < NOW() - INTERVAL '30 days') AS churned_prev
"""

CHURN_MONTHS = 6
CHURN_TARGET_PCT = 8  # healthy monthly churn for a $19 consumer subscription


def churn_rate(churned: int, start: int, new: int) -> Optional[float]:
    """Share of the paying customers we had in a window (at its start + new in it) who cancelled in it.
    Counting new ones too keeps a month with many fast cancels from showing over 100%."""
    base = int(start or 0) + int(new or 0)
    return round(int(churned or 0) / base * 100, 1) if base else None


def churn_summary(months: List[Dict[str, Any]], window: Dict[str, Any], price: int) -> Dict[str, Any]:
    w = {k: int(v or 0) for k, v in (window or {}).items()}
    rate = churn_rate(w.get("churned_30"), w.get("start_30"), w.get("new_30"))
    prev = churn_rate(w.get("churned_prev"), w.get("start_prev"), w.get("new_prev"))
    series = []
    for r in months:
        month = r.get("month")
        series.append({
            "month": month.strftime("%Y-%m") if hasattr(month, "strftime") else str(month)[:7],
            "start": int(r.get("start_subs") or 0),
            "new": int(r.get("new_paid") or 0),
            "churned": int(r.get("churned") or 0),
            "first_month": int(r.get("churned_first_month") or 0),
            "rate": churn_rate(r.get("churned"), r.get("start_subs"), r.get("new_paid")),
        })
    if series:
        series[-1]["partial"] = True
    churned = w.get("churned_30", 0)
    return {
        "rate_30d": rate,
        "rate_prev_30d": prev,
        "change_pts": round(rate - prev, 1) if rate is not None and prev is not None else None,
        "churned_30d": churned,
        "mrr_lost_30d": churned * price,
        "first_month_share": round(w.get("early_30", 0) / churned * 100) if churned else None,
        "retention_30d": round(100 - rate, 1) if rate is not None else None,
        "target_pct": CHURN_TARGET_PCT,
        "months": series,
    }


def churn_metrics(cursor, price: int) -> Dict[str, Any]:
    cursor.execute(CHURN_MONTHS_SQL, {"days": TRIAL_DAYS, "months": CHURN_MONTHS})
    months = [dict(r) for r in cursor.fetchall()]
    cursor.execute(CHURN_WINDOW_SQL, {"days": TRIAL_DAYS})
    return churn_summary(months, dict(cursor.fetchone() or {}), price)


def trial_rows(cursor, since, cancel_flags: Optional[Dict[str, bool]] = None) -> List[Dict[str, Any]]:
    cursor.execute(TRIAL_ROWS_SQL, {"days": TRIAL_DAYS, "since": since})
    out = []
    for r in cursor.fetchall():
        row = dict(r)
        sub_id = row.pop("stripe_subscription_id", None)
        row["cancel_scheduled"] = bool(cancel_flags and sub_id and cancel_flags.get(sub_id))
        for key in ("started", "trial_ends"):
            if row.get(key) is not None and hasattr(row[key], "isoformat"):
                row[key] = row[key].isoformat()
        row["sent"] = int(row.get("sent") or 0)
        out.append(row)
    return out


def founder_metrics(cursor, current_mrr: float, price: int, period_start,
                    cancel_flags: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    stage = milestones(current_mrr, price)
    cursor.execute(PACE_SQL, {"days": TRIAL_DAYS})
    p = dict(cursor.fetchone() or {})
    growth = pace(p.get("new_paid") or 0, p.get("churned") or 0, current_mrr, stage["goal"], price)

    all_trials = summarize_trials(trial_rows(cursor, "2000-01-01", cancel_flags), price)
    all_trials["cancel_known"] = cancel_flags is not None
    period_rows = trial_rows(cursor, period_start)
    cursor.execute(PAID_STARTS_SQL, {"since": period_start})
    paid_direct = int((cursor.fetchone() or {}).get("n") or 0)
    trials = {
        **all_trials,
        "period": {
            "started": len(period_rows),
            "paid_direct": paid_direct,
            "share_pct": round(len(period_rows) / max(len(period_rows) + paid_direct, 1) * 100),
        },
        "mrr_with_pipeline": round(float(current_mrr or 0) + all_trials["pipeline_mrr"]),
    }
    try:
        churn = churn_metrics(cursor, price)
    except Exception as exc:  # never lose the rest of the dashboard over this block
        print(f"[FOUNDER] churn metrics failed: {exc}")
        cursor.connection.rollback()
        churn = None
    return {"milestones": stage, "pace": growth, "trials": trials, "churn": churn}
