"""One rulebook for every creator email Polly sends, so the inbox reads as one assistant, not 20 crons.

Goal: each email brings something new (a brand, a reply, proof, progress) and earns the right to mention Pro.
Checked in polly_alerts._recipient(..., kind=...), which every creator email already calls before it is written.

Rules (Nov 2026, from 30 days of send data):
- retired kinds never send (pro_week: generic pitch, 271 sent, 4% click, 1 upgrade);
- each kind has an audience: offers and 'go send it yourself' reminders are for free creators only, Autopilot and
  roundup emails for Pro only;
- at most one non-transactional email per creator per 20 hours, and 3 a week for free / 4 for Pro (one creator got
  62 in a week). Event emails (a brand opened your kit, a brand you match just joined) skip the daily slot: they're
  the news creators open (52-90% open) but still count toward the week;
- one offer (paywall follow-up or free week) per 7 days;
- per-kind spacing for the chattiest Pro emails (follow-up heads-up was 19 per person a month).
Transactional emails (checkout recovery, trial and billing, Gmail broken, Autopilot paused) always go.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

TRANSACTIONAL = {
    "checkout_recovery", "checkout_recovery_2", "trial_started", "trial_first_sent", "trial_ending", "trial_gmail",
    "renewal_note", "autopilot_paused", "autopilot_gmail", "hooks_ready",
}
RETIRED = {"pro_week", "nudge", "autopilot_drafts", "autopilot_week"}
EVENT = {"kit_view", "new_brand_push"}
# max_quota_hit (lifecycle) and paywall_followup (Polly) fire at the same moment: one offer covers both.
OFFER = {"paywall_followup", "free_week", "max_quota_hit"}
FREE_ONLY = OFFER | {"polly_brief", "polly_intro", "month_report", "monday_checkin", "weekly_digest"}
PRO_ONLY = {"autopilot_headsup", "ugc_roundup", "monday_board"}
MIN_GAP = {
    "autopilot_headsup": timedelta(hours=72),
    "ugc_roundup": timedelta(days=7),
    "polly_brief": timedelta(days=2),
}
DAILY_GAP = timedelta(hours=20)
WEEK = timedelta(days=7)
WEEKLY_CAP = {"free": 3, "pro": 4}
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


def decide(conn, creator_id: int, kind: str, is_pro: bool, now: Optional[datetime] = None) -> Tuple[bool, str]:
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
    recent = [(k, t) for k, t in _recent(conn, creator_id, now - WEEK)
              if k not in TRANSACTIONAL and not k.startswith("founder_")]
    gap = MIN_GAP.get(kind)
    if gap and any(k == kind and t and t > now - gap for k, t in recent):
        return False, "kind_gap"
    if kind in OFFER and any(k in OFFER and t and t > now - OFFER_GAP for k, t in recent):
        return False, "offer_gap"
    if len(recent) >= WEEKLY_CAP["pro" if is_pro else "free"]:
        return False, "weekly_cap"
    if kind not in EVENT and any(t and t > now - DAILY_GAP for _, t in recent):
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
            "weekly_cap": WEEKLY_CAP, "daily_gap_hours": DAILY_GAP.total_seconds() / 3600}
