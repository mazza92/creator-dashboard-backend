"""Day-5 Pro email: show a free creator the week Polly would run for them on Autopilot.

Replaces the old education series ("the 5 real reasons brands don't reply").
One email per creator, to free creators who opened Polly in their first weeks.
The trial arm of the quota trial test gets the free week, everyone else $19/mo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

KIND = "pro_week"
SEND_AFTER = timedelta(days=5)
SEND_WINDOW = timedelta(days=45)
OFFER_GAP = timedelta(days=7)
FREE_PITCHES = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word}es" if word.endswith(("ch", "sh", "s")) else f"{n} {word}s"


def pro_week_email(first_name: str, facts: Dict[str, Any], trial: bool = False,
                   unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import chip_link, render_email, upgrade_link

    pitched = int(facts.get("pitched") or 0)
    left = int(facts.get("left") or 0)
    names = [n for n in (facts.get("brand_names") or []) if n][:2]
    matched = int(facts.get("matched") or 0)

    subject = "Try a week of me pitching for you, free" if trial else "Want me to pitch 3 brands for you every day?"
    if pitched:
        lines = [f"You've had me write {_plural(pitched, 'pitch')} so far. On the free plan that's "
                 f"{FREE_PITCHES} a month, and you send each one yourself."]
    else:
        lines = [f"You've met me, but no pitch has gone out yet. On the free plan you get {FREE_PITCHES} "
                 "a month, and you send each one yourself."]
    if names:
        shown = " and ".join(f"<strong>{escape(n)}</strong>" for n in names)
        more = matched - len(names)
        tail = f", plus {_plural(more, 'other brand')}," if more > 0 else ""
        lines.append(f"I already have {shown}{tail} matched to you and waiting.")
    lines += [
        "On Pro I run it for you on <strong>Autopilot</strong>: <strong>3 new matched brands every weekday</strong>. "
        "Each one fits your niche and has a verified contact, each pitch is written for "
        "that brand, sent from your Gmail and followed up on day 4 and day 10. Nothing to approve.",
        "Doing that yourself is about 20 hours a month of finding brands, hunting contacts and writing pitches. "
        "The creators who land boxes and paid deals are the ones who pitch every week without fail, "
        "and that's the part I take off your plate. Replies land in your own inbox.",
    ]
    if trial:
        lines.append("Try it free for 7 days. Cancel before day 7 and you pay nothing.")
        label = "Start my free week"
    else:
        lines.append("It's $19/mo and you can cancel anytime.")
        label = "Put Polly on autopilot"
    buttons = [{"label": label, "href": upgrade_link(f"{KIND}_{'trial' if trial else 'pro'}")}]
    links = None
    if left:
        links = [{"label": f"Or use your {_plural(left, 'free pitch')} left this month",
                  "href": chip_link("line_up", kind=KIND)}]
    return {
        "subject": subject,
        "html": render_email(first_name, subject, lines, buttons, unsubscribe_url, links=links),
    }


def _candidates(conn, limit: int, now: datetime) -> List[Dict[str, Any]]:
    from services.polly_alerts import OTHER_ALERT_GAP, _cursor
    from services.waitlist_gate import WAITLIST_EXCLUDE_SQL

    cur = _cursor(conn)
    cur.execute(
        f"""
        SELECT c.id AS creator_id
        FROM creators c
        WHERE c.created_at < %(after)s AND c.created_at > %(window)s
          AND COALESCE(c.onboarding_survey->>'segment', '') <> 'is_brand'
          AND COALESCE(c.unlocks_tier, 'free') <> 'pro'
          AND LOWER(COALESCE(c.subscription_tier, '')) NOT IN ('pro', 'elite')
          AND {WAITLIST_EXCLUDE_SQL}
          AND EXISTS (SELECT 1 FROM polly_usage_events e
                      WHERE e.creator_id = c.id AND e.event = 'open')
          AND NOT EXISTS (SELECT 1 FROM polly_usage_events e
                          WHERE e.creator_id = c.id AND e.event = 'checkout_completed')
          AND NOT EXISTS (SELECT 1 FROM polly_email_log l
                          WHERE l.creator_id = c.id AND l.ok AND (
                              l.kind = %(kind)s
                              OR (l.kind IN ('paywall_followup', 'checkout_recovery') AND l.sent_at > %(offer)s)
                              OR l.sent_at > %(gap)s))
          AND NOT EXISTS (SELECT 1 FROM lifecycle_email_sends s
                          WHERE s.creator_id = c.id AND s.status = 'sent' AND (
                              (s.template_slug = 'max_quota_hit' AND s.sent_at > %(offer)s)
                              OR s.sent_at > %(gap)s))
        ORDER BY c.created_at DESC
        LIMIT %(limit)s
        """,
        {
            "after": now - SEND_AFTER,
            "window": now - SEND_WINDOW,
            "kind": KIND,
            "offer": now - OFFER_GAP,
            "gap": now - OTHER_ALERT_GAP,
            "limit": limit,
        },
    )
    return [dict(r) for r in cur.fetchall()]


def pro_week_facts(conn, creator_id: int) -> Dict[str, Any]:
    from services.polly_alerts import _cursor
    from services.polly_memory import load_thread
    from services.unlock_quota import count_delivered_unlocks_all_time, count_delivered_unlocks_this_month

    cur = _cursor(conn)
    pitched = count_delivered_unlocks_all_time(cur, creator_id)
    this_month = count_delivered_unlocks_this_month(cur, creator_id)
    brands = load_thread(conn, creator_id).get("suggested_brands") or []
    return {
        "pitched": pitched,
        "left": max(0, FREE_PITCHES - this_month),
        "matched": len(brands),
        "brand_names": [b.get("name") for b in brands if isinstance(b, dict)],
    }


def email_pro_week(conn, limit: int = 25, dry_run: bool = False, send_fn: Optional[Callable] = None,
                   test_email: str = "") -> List[Dict[str, Any]]:
    from services import polly_alerts as pa
    from services.quota_trial_test import trial_eligible

    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    out = []
    for row in _candidates(conn, limit, _now()):
        cid = row["creator_id"]
        who = pa._recipient(conn, cid)
        if not who or who["is_pro"]:
            continue
        trial = trial_eligible(pa._cursor(conn), cid)
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = pro_week_email(who["first"], pro_week_facts(conn, cid), trial=trial, unsubscribe_url=unsub)
        item = {"creator_id": cid, "kind": KIND, "trial": trial, "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, KIND)
        pa._log_email(conn, cid, KIND, None, None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
