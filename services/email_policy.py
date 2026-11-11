"""One rulebook for every creator email Polly sends, so the inbox reads as one assistant, not 20 crons.

Goal: each email brings something new (a brand, a reply, proof, progress) and earns the right to mention Pro.
Checked in polly_alerts._recipient(..., kind=...), which every creator email already calls before it is written.

Rules (Nov 2026, from 30 days of send data):
- retired kinds never send (pro_week: generic pitch, 271 sent, 4% click, 1 upgrade);
- each kind has an audience: offers and 'go send it yourself' reminders are for free creators only, Autopilot and
  roundup emails for Pro only;
- the email budget (Nov 11 2026, with the daily brief): one routine email a day, and 6 a week for Pro / 3 a week
  for free (one creator got 62 in a week). "A day" is a rolling 20 hours, except for the daily brief, which owns
  the creator's morning: it is blocked only by a routine email since their local midnight or in the last 10 hours,
  so yesterday afternoon's email doesn't eat today's brief. Event emails (a brand you match just joined, a reply)
  skip the daily slot and don't take it: they're the news creators open (52-90% open) but count toward the week;
- kit views ("<brand> opened your kit") keep exactly the rules they had before the brief (the founder was explicit:
  don't change them): an event email, so no daily slot, counted toward the week; their own throttle lives in
  polly_alerts.email_kit_views;
- one offer (paywall follow-up or free week) per 7 days;
- per-kind spacing for the chattiest Pro emails (follow-up heads-up was 19 per person a month);
- the daily brief (services.daily_brief) is one email instead of several: when a creator gets it daily, the
  routine alerts it already carries (paid-gig roundup, unsent-pitch reminder) don't send on their own; when they
  turned the brief off, the Monday report is off too (weekly = Monday report only).
Transactional emails (checkout recovery, trial and billing, Gmail broken, Autopilot paused) always go.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

TRANSACTIONAL = {
    "checkout_recovery", "checkout_recovery_2", "trial_started", "trial_first_sent", "trial_ending", "trial_gmail",
    "renewal_note", "autopilot_paused", "autopilot_gmail", "hooks_ready",
}
# waitlist_*: pending creators are mostly ones we won't approve; no marketing email until approved.
# autopilot_headsup retired Nov 2026: one email per follow-up batch (33 in a month for one Pro member) repeated
# the daily Polly line; the week's follow-ups now sit in the Monday report with one-tap "replied" buttons.
RETIRED = {"pro_week", "nudge", "autopilot_drafts", "autopilot_week", "autopilot_headsup", "waitlist_1", "waitlist_2",
           "waitlist_3"}
EVENT = {"kit_view", "new_brand_push", "reply_win"}
# Kinds outside the budget entirely (never capped, never counted). Empty: kit views keep their pre-brief rules.
BUDGET_EXEMPT: set = set()
BRIEF_KIND = "daily_brief"
# Routine alerts whose content the daily brief already carries: folded into it for creators on the daily brief.
FOLDED_INTO_BRIEF = {"ugc_roundup", "polly_brief"}
# The Monday report (Pro board / free check-in): it replaces Monday's brief, and goes when the brief is daily or
# weekly, not when the creator turned the brief off.
WEEKLY_REPORT = {"monday_board", "weekly_digest", "monday_checkin"}
# max_quota_hit (lifecycle) and paywall_followup (Polly) fire at the same moment: one offer covers both.
OFFER = {"paywall_followup", "free_week", "max_quota_hit"}
FREE_ONLY = OFFER | {"polly_brief", "polly_intro", "month_report", "monday_checkin", "weekly_digest",
                     "saved_brands", "first_pitch", "waitlist_1", "waitlist_2", "waitlist_3"}  # Pro: Autopilot pitches saved brands itself
PRO_ONLY = {"autopilot_headsup", "ugc_roundup", "monday_board"}
MIN_GAP = {
    "autopilot_headsup": timedelta(hours=72),
    "ugc_roundup": timedelta(days=7),
    "polly_brief": timedelta(days=2),
    "saved_brands": timedelta(days=7),
    "niche_gifting": timedelta(days=6),
}
DAILY_GAP = timedelta(hours=20)
BRIEF_MIN_GAP = timedelta(hours=10)
WEEK = timedelta(days=7)
DAILY_CAP = 1
WEEKLY_CAP = {"free": 3, "pro": 6}
OFFER_GAP = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts):
    if ts is None:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _recent(conn, creator_id: int, since: datetime):
    """Polly emails and lifecycle-engine emails, as (kind, sent_at)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT kind, sent_at FROM polly_email_log WHERE creator_id = %s AND ok AND sent_at > %s
        UNION ALL
        SELECT template_slug, sent_at FROM lifecycle_email_sends
        WHERE creator_id = %s AND sent_at > %s AND COALESCE(status, 'sent') IN ('sent', 'delivered')
    """, (creator_id, since, creator_id, since))
    return [(r[0] if not isinstance(r, dict) else r["kind"],
             _aware(r[1] if not isinstance(r, dict) else r["sent_at"])) for r in cur.fetchall()]


def _brief_context(conn, creator_id: int, is_pro: bool, brief: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{'mode': daily|weekly|off, 'tz': IANA name}. Callers that already know them pass ``brief``."""
    brief = dict(brief or {})
    if brief.get("mode") and brief.get("tz"):
        return brief
    from services.daily_brief import brief_settings, creator_timezone, resolve_mode

    settings = brief_settings(conn, creator_id)
    brief.setdefault("mode", resolve_mode(settings.get("mode"), is_pro))
    brief.setdefault("tz", creator_timezone(settings))
    return brief


def _local_midnight(now: datetime, tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = now.astimezone(timezone.utc)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def brief_rollout(creator_id: Any) -> bool:
    """Daily brief rollout switch: env DAILY_BRIEF_ROLLOUT = 'all' or comma-separated creator ids (default: nobody).
    Creators outside it keep the pre-brief emails exactly (no brief, nothing folded, Monday report as before)."""
    import os
    raw = (os.getenv("DAILY_BRIEF_ROLLOUT") or "").strip().lower()
    if raw == "all":
        return True
    try:
        return int(creator_id) in {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}
    except (TypeError, ValueError):
        return False


def decide(conn, creator_id: int, kind: str, is_pro: bool, now: Optional[datetime] = None,
           brief: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """(allowed, reason). Reason names the rule that blocked it, for the cron log."""
    now = now or _now()
    if kind in RETIRED:
        return False, "retired"
    if kind in TRANSACTIONAL or kind.startswith("founder_"):
        return True, "transactional"
    if is_pro and kind in FREE_ONLY:
        return False, "free_only"
    if not is_pro and kind in PRO_ONLY:
        return False, "pro_only"
    if kind == BRIEF_KIND and not brief_rollout(creator_id):
        return False, "brief_rollout"
    if (kind == BRIEF_KIND or kind in FOLDED_INTO_BRIEF or kind in WEEKLY_REPORT) and brief_rollout(creator_id):
        brief = _brief_context(conn, creator_id, is_pro, brief)
        mode = brief["mode"]
        if kind == BRIEF_KIND and mode != "daily":
            return False, f"brief_{mode}"
        if kind in FOLDED_INTO_BRIEF and mode == "daily":
            return False, "in_daily_brief"
        if kind in WEEKLY_REPORT and mode == "off":
            return False, "brief_off"
    recent = [(k, t) for k, t in _recent(conn, creator_id, now - WEEK)
              if k not in TRANSACTIONAL and not k.startswith("founder_") and k not in BUDGET_EXEMPT]
    gap = MIN_GAP.get(kind)
    if gap and any(k == kind and t and t > now - gap for k, t in recent):
        return False, "kind_gap"
    if kind in OFFER and any(k in OFFER and t and t > now - OFFER_GAP for k, t in recent):
        return False, "offer_gap"
    if kind in BUDGET_EXEMPT:
        return True, "ok"
    if len(recent) >= WEEKLY_CAP["pro" if is_pro else "free"]:
        return False, "weekly_cap"
    if kind in EVENT:
        return True, "ok"
    routine = [t for k, t in recent if k not in EVENT and t]
    if kind == BRIEF_KIND:
        since = max(_local_midnight(now, brief["tz"]), now - BRIEF_MIN_GAP)
        if sum(1 for t in routine if t > since) >= DAILY_CAP:
            return False, "daily_cap"
        return True, "ok"
    if sum(1 for t in routine if t > now - DAILY_GAP) >= DAILY_CAP:
        return False, "daily_cap"
    return True, "ok"


def allowed(conn, creator_id: int, kind: str, is_pro: bool) -> bool:
    try:
        ok, reason = decide(conn, creator_id, kind, is_pro)
    except Exception as err:  # never let the rulebook break a cron: fall back to sending
        print(f"[email policy] check failed creator={creator_id} kind={kind}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return True
    if not ok:
        print(f"[email policy] skip creator={creator_id} kind={kind} rule={reason}")
    return ok


def allowed_for_creator(conn, creator_id: int, kind: str) -> bool:
    """Same check when the caller doesn't know the creator's tier (the lifecycle engine)."""
    try:
        cur = conn.cursor()
        cur.execute("""SELECT (LOWER(COALESCE(subscription_tier, '')) = 'pro' OR COALESCE(unlocks_tier, '') = 'pro')
                       FROM creators WHERE id = %s""", (creator_id,))
        row = cur.fetchone()
        is_pro = bool(row[0] if row and not isinstance(row, dict) else (row or {}).get("bool"))
    except Exception as err:
        print(f"[email policy] tier lookup failed creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return True
    return allowed(conn, creator_id, kind, is_pro)


def summary() -> Dict[str, Any]:
    return {"transactional": sorted(TRANSACTIONAL), "retired": sorted(RETIRED), "event": sorted(EVENT),
            "offer": sorted(OFFER), "free_only": sorted(FREE_ONLY), "pro_only": sorted(PRO_ONLY),
            "weekly_cap": WEEKLY_CAP, "daily_cap": DAILY_CAP, "daily_gap_hours": DAILY_GAP.total_seconds() / 3600,
            "budget_exempt": sorted(BUDGET_EXEMPT), "folded_into_brief": sorted(FOLDED_INTO_BRIEF),
            "weekly_report": sorted(WEEKLY_REPORT), "brief_min_gap_hours": BRIEF_MIN_GAP.total_seconds() / 3600}
