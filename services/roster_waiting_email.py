"""Automated roster-waiting emails for brands with applicants in review.

Four-step funnel (local 10am), Resend only (no SMTP), logged in brand_emails.
Does not send to brands without contact_email. Stops on picks or replies.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlencode
from html import unescape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from psycopg2.extras import RealDictCursor

from services.pg_hotpath_schema import public_column_exists, public_table_exists

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
FRONTEND_FALLBACK = "https://app.newcollab.co"

SEND_HOUR_LOCAL = 10
ACTIVITY_SKIP_DAYS = 3
EMAIL_CAP_DAYS = 5
TRIGGER1_MIN = 3
TRIGGER1_NO_OPEN_DAYS = 5
TRIGGER2_MIN = 10
TRIGGER2_NO_CLICK_DAYS = 7
TRIGGER3_MIN = 5
TRIGGER3_NO_PICK_DAYS = 14
TRIGGER3_SPACING_DAYS = 7
TRIGGER3_MAX = 3
TRIGGER4_WEEKDAY = 4  # Friday

EMAIL_TYPES = {
    1: "first_nudge",
    2: "volume_escalation",
    3: "weekly_reminder",
    4: "final_nudge",
}

SUBJECT_A = "{count} creators are waiting to work with {brand}"
SUBJECT_B = "Your {brand} roster has {count} creators ready to go"
SUBJECT_C = "{count} creators applied to {brand} - pick them now"
SUBJECT_D = "{brand}'s creator queue is filling up ({count} waiting)"
SUBJECT_VARIANTS = {"A": SUBJECT_A, "B": SUBJECT_B, "C": SUBJECT_C, "D": SUBJECT_D}
SUBJECT_T2 = "{count} creators are waiting on {brand} - don't leave them hanging"
SUBJECT_T4 = "Closing your {brand} roster on Friday"

BLOCKED_OUTREACH = {
    "replied",
    "interested",
    "not_interested",
    "signed_up",
    "wrong_email",
    "bounced",
    "do_not_contact",
    "unsubscribe",
    "reply",
}

REGION_TZ = {
    "us": "America/New_York",
    "usa": "America/New_York",
    "united states": "America/New_York",
    "america": "America/New_York",
    "uk": "Europe/London",
    "gb": "Europe/London",
    "united kingdom": "Europe/London",
    "england": "Europe/London",
    "au": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "ca": "America/Toronto",
    "canada": "America/Toronto",
    "fr": "Europe/Paris",
    "france": "Europe/Paris",
    "de": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "eu": "Europe/Paris",
    "europe": "Europe/Paris",
    "nz": "Pacific/Auckland",
    "new zealand": "Pacific/Auckland",
    "sg": "Asia/Singapore",
    "singapore": "Asia/Singapore",
    "jp": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
}

ROSTER_ACTIVITY_EVENTS = ("roster_view", "roster_select", "roster_lock")
# Views logged before internal traffic was split out carry no user agent and are
# mostly our own previews. Only trust views stamped with one.
BRAND_VIEW_SQL = "(e.event = 'roster_view' AND e.meta ? 'ua')"
TOP_PICKS_LIMIT = 5

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_EM_DASH_RE = re.compile(r"[\u2013\u2014\u2015]")


@dataclass
class RosterEmailState:
    applicant_count: int
    has_picks: bool = False
    campaign_active: bool = True
    engaged: bool = False
    last_roster_view_days: Optional[float] = None
    last_activity_days: Optional[float] = None
    last_email_days: Optional[float] = None
    waiting_days: Optional[float] = None
    trigger1_sent: bool = False
    trigger2_sent: bool = False
    weekly_sent_count: int = 0
    last_weekly_days: Optional[float] = None
    trigger4_sent: bool = False
    local_hour: int = SEND_HOUR_LOCAL
    local_weekday: int = 0  # Monday=0
    has_contact_email: bool = True


@dataclass(frozen=True)
class TriggerChoice:
    number: int
    email_type: str


def _as_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, tuple):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [text]
        except Exception:
            return [text]
    return []


def _days_ago(ts: Any, now: datetime) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 86400.0


def safe_brand_name(name: Any) -> str:
    text = re.sub(r"\s+", " ", str(name or "your brand").replace("\n", " ").replace("\r", " "))
    return text.strip()[:80] or "your brand"


def greeting_for_brand(brand: dict) -> str:
    raw = (
        brand.get("contact_first_name")
        or brand.get("pr_contact_name")
        or brand.get("contact_name")
        or ""
    )
    first = str(raw).strip().split()[0] if str(raw).strip() else ""
    if first and first.lower() not in ("null", "none", "team", "n/a", "-"):
        return first
    return "team"


def timezone_for_regions(regions: Any) -> str:
    for item in _as_list(regions):
        key = str(item or "").strip().lower()
        if key in REGION_TZ:
            return REGION_TZ[key]
    return "UTC"


def local_datetime(now_utc: datetime, tz_name: str) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return now_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        return now_utc.astimezone(timezone.utc)


def frontend_base() -> str:
    raw = (
        os.getenv("FRONTEND_URL")
        or os.getenv("REACT_APP_FRONTEND_URL")
        or ""
    ).rstrip("/")
    marketing = {
        "https://newcollab.co",
        "https://www.newcollab.co",
        "http://newcollab.co",
        "http://www.newcollab.co",
        "",
    }
    if raw in marketing:
        return FRONTEND_FALLBACK
    if raw in ("http://localhost:3000", "http://127.0.0.1:3000"):
        return "http://localhost:3001"
    return raw or FRONTEND_FALLBACK


def build_roster_link(token: str, applicant_count: int, trigger_number: int, base: str = None) -> str:
    root = (base or frontend_base()).rstrip("/")
    query = urlencode(
        {
            "count": int(applicant_count),
            "utm_source": "email",
            "utm_campaign": "roster_waiting",
            "utm_medium": f"trigger_{int(trigger_number)}",
        }
    )
    return f"{root}/r/{token}?{query}"


def subject_for(
    trigger_number: int,
    applicant_count: int,
    brand_name: str,
    variant: str = "A",
) -> str:
    brand = safe_brand_name(brand_name)
    count = int(applicant_count)
    if trigger_number == 4:
        template = SUBJECT_T4
    elif trigger_number == 2:
        template = SUBJECT_T2
    elif trigger_number == 3:
        # Second batch: test B after the first-nudge A send.
        template = SUBJECT_VARIANTS.get((variant or "B").upper(), SUBJECT_B)
    else:
        template = SUBJECT_VARIANTS.get((variant or "A").upper(), SUBJECT_A)
    return template.format(count=count, brand=brand)


def html_subject_for(trigger_number: int, applicant_count: int, brand_name: str, variant: str = "A") -> str:
    """HTML subject for clients that render markup. SMTP sends the plain subject."""
    plain = subject_for(trigger_number, applicant_count, brand_name, variant)
    count = str(int(applicant_count))
    return plain.replace(count, f"<strong>{count}</strong>", 1)


def default_subject_variant() -> str:
    raw = (os.getenv("ROSTER_WAITING_SUBJECT_VARIANT") or "A").strip().upper()
    return raw if raw in SUBJECT_VARIANTS else "A"


def variant_for_trigger(trigger_number: int, default_variant: str = None) -> str:
    default_variant = (default_variant or default_subject_variant()).upper()
    if trigger_number == 3:
        return "B" if default_variant == "A" else default_variant
    if trigger_number in (2, 4):
        return "fixed"
    return default_variant


def passes_caps(state: RosterEmailState) -> tuple[bool, str]:
    if not state.has_contact_email:
        return False, "no_contact_email"
    if not state.campaign_active:
        return False, "campaign_inactive"
    if state.engaged:
        return False, "engaged"
    if state.has_picks:
        return False, "already_picked"
    if state.applicant_count < 1:
        return False, "no_waiting_applicants"
    if state.last_activity_days is not None and state.last_activity_days < ACTIVITY_SKIP_DAYS:
        return False, "recent_roster_activity"
    if state.last_email_days is not None and state.last_email_days < EMAIL_CAP_DAYS:
        return False, "frequency_cap"
    return True, ""


def in_send_window(state: RosterEmailState, trigger_number: int, skip_time_checks: bool = False) -> bool:
    if skip_time_checks:
        return True
    if state.local_hour != SEND_HOUR_LOCAL:
        return False
    if trigger_number == 4 and state.local_weekday != TRIGGER4_WEEKDAY:
        return False
    return True


def choose_trigger(state: RosterEmailState) -> Optional[TriggerChoice]:
    ok, _reason = passes_caps(state)
    if not ok:
        return None

    n = int(state.applicant_count or 0)
    view_days = state.last_roster_view_days
    no_open_5 = view_days is None or view_days >= TRIGGER1_NO_OPEN_DAYS
    no_click_7 = view_days is None or view_days >= TRIGGER2_NO_CLICK_DAYS
    waiting = state.waiting_days if state.waiting_days is not None else 0
    weekly_ok = state.weekly_sent_count == 0 or (
        state.last_weekly_days is not None and state.last_weekly_days >= TRIGGER3_SPACING_DAYS
    )

    if state.weekly_sent_count >= TRIGGER3_MAX and not state.trigger4_sent and n >= 1:
        return TriggerChoice(4, EMAIL_TYPES[4])

    if n >= TRIGGER1_MIN and not state.trigger1_sent and no_open_5:
        return TriggerChoice(1, EMAIL_TYPES[1])

    if n >= TRIGGER2_MIN and not state.trigger2_sent and no_click_7:
        return TriggerChoice(2, EMAIL_TYPES[2])

    if (
        n >= TRIGGER3_MIN
        and state.weekly_sent_count < TRIGGER3_MAX
        and waiting >= TRIGGER3_NO_PICK_DAYS
        and weekly_ok
    ):
        return TriggerChoice(3, EMAIL_TYPES[3])

    return None


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_roster_waiting_html(
    trigger_number: int,
    brand_name: str,
    greeting: str,
    applicant_count: int,
    roster_link: str,
    subject: str,
    top_picks: list = None,
    quick_pick_link: str = None,
) -> str:
    env = _jinja_env()
    picks = list(top_picks or []) if quick_pick_link else []
    inner = env.get_template("roster_waiting_email.html").render(
        trigger_number=int(trigger_number),
        brand_name=safe_brand_name(brand_name),
        greeting=greeting or "team",
        applicant_count=int(applicant_count),
        roster_link=roster_link,
        top_picks=picks,
        quick_pick_link=quick_pick_link if picks else None,
    )
    if picks:
        action_url = quick_pick_link
        action_text = f"Approve these {len(picks)} creators"
    else:
        action_url = roster_link
        action_text = "Review your roster" if trigger_number != 4 else "Keep this roster open"
    outer = env.get_template("welcome_email.html").render(
        subject=subject,
        preheader=_preheader(trigger_number, applicant_count, brand_name),
        message=Markup(inner),
        action_url=action_url,
        action_text=action_text,
        unsubscribe_url=None,
    )
    return outer


def _preheader(trigger_number: int, applicant_count: int, brand_name: str) -> str:
    brand = safe_brand_name(brand_name)
    n = int(applicant_count)
    if trigger_number == 4:
        return f"Your {brand} roster archives Friday unless you pick creators."
    if trigger_number == 3:
        return f"{n} creators are still waiting on your {brand} roster."
    return f"{n} small creators applied to {brand}. Product + shipping only."


def build_email(
    trigger_number: int,
    brand_name: str,
    greeting: str,
    applicant_count: int,
    token: str,
    variant: str = None,
    base_url: str = None,
    top_picks: list = None,
) -> dict:
    variant = variant_for_trigger(trigger_number, variant)
    subject = subject_for(trigger_number, applicant_count, brand_name, variant)
    link = build_roster_link(token, applicant_count, trigger_number, base=base_url)
    picks = list(top_picks or [])[:TOP_PICKS_LIMIT]
    quick_link = (
        build_quick_pick_link(token, picks, applicant_count, trigger_number, base=base_url)
        if picks else None
    )
    html = render_roster_waiting_html(
        trigger_number=trigger_number,
        brand_name=brand_name,
        greeting=greeting,
        applicant_count=applicant_count,
        roster_link=link,
        subject=subject,
        top_picks=picks,
        quick_pick_link=quick_link,
    )
    if _EM_DASH_RE.search(html) or _EM_DASH_RE.search(subject):
        html = _EM_DASH_RE.sub("-", html)
        subject = _EM_DASH_RE.sub("-", subject)
    return {
        "subject": subject,
        "subject_html": html_subject_for(trigger_number, applicant_count, brand_name, variant),
        "subject_variant": variant,
        "html": html,
        "roster_link": link,
        "quick_pick_link": quick_link,
        "top_picks": picks,
        "preheader": _preheader(trigger_number, applicant_count, brand_name),
        "greeting": greeting or "team",
        "trigger_number": int(trigger_number),
        "email_type": EMAIL_TYPES[int(trigger_number)],
        "applicant_count": int(applicant_count),
        "plain_preview": unescape(re.sub(r"<[^>]+>", " ", html)),
    }


def ensure_brand_emails_table(cursor, conn=None) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        if public_table_exists(cursor, "brand_emails"):
            _SCHEMA_READY = True
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_emails (
                id SERIAL PRIMARY KEY,
                brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
                campaign_id INTEGER REFERENCES brand_pr_campaigns(id) ON DELETE SET NULL,
                trigger_number INTEGER NOT NULL,
                email_type VARCHAR(64) NOT NULL,
                to_email VARCHAR(255) NOT NULL,
                subject TEXT NOT NULL,
                subject_variant VARCHAR(8) NOT NULL DEFAULT 'A',
                applicant_count INTEGER NOT NULL DEFAULT 0,
                roster_link TEXT,
                message_id VARCHAR(255),
                status VARCHAR(32) NOT NULL DEFAULT 'sent',
                skip_reason TEXT,
                roster_opened_at TIMESTAMPTZ,
                roster_clicked_at TIMESTAMPTZ,
                engaged BOOLEAN NOT NULL DEFAULT FALSE,
                engaged_at TIMESTAMPTZ,
                engaged_reason VARCHAR(64),
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_emails_brand_sent
            ON brand_emails (brand_id, sent_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_emails_trigger
            ON brand_emails (brand_id, trigger_number, sent_at DESC)
            WHERE status = 'sent'
            """
        )
        if conn is not None:
            conn.commit()
        _SCHEMA_READY = True


def mark_brand_roster_engaged(cursor, brand_id: int, reason: str = "reply", conn=None) -> int:
    """Stop further automated roster mail. Call when a brand replies or picks."""
    if not brand_id or not public_table_exists(cursor, "brand_emails"):
        return 0
    cursor.execute(
        """
        UPDATE brand_emails
        SET engaged = TRUE,
            engaged_at = COALESCE(engaged_at, NOW()),
            engaged_reason = COALESCE(engaged_reason, %s)
        WHERE brand_id = %s AND engaged = FALSE
        """,
        (reason, brand_id),
    )
    updated = cursor.rowcount or 0
    if conn is not None:
        conn.commit()
    return updated


def state_from_row(row: dict, now: datetime) -> RosterEmailState:
    outreach = str(row.get("outreach_status") or "").strip().lower()
    engaged = bool(row.get("email_engaged")) or outreach in BLOCKED_OUTREACH
    selected = _as_list(row.get("selected_application_ids"))
    picked_count = int(row.get("picked_count") or 0)
    has_picks = bool(selected) or picked_count > 0
    local = local_datetime(now, timezone_for_regions(row.get("regions")))
    return RosterEmailState(
        applicant_count=int(row.get("applicant_count") or 0),
        has_picks=has_picks,
        campaign_active=(row.get("campaign_status") or "active") == "active" and not row.get("locked_at"),
        engaged=engaged,
        last_roster_view_days=_days_ago(row.get("last_roster_view_at"), now),
        last_activity_days=_days_ago(row.get("last_roster_activity_at"), now),
        last_email_days=_days_ago(row.get("last_email_at"), now),
        waiting_days=_days_ago(row.get("oldest_waiting_at"), now),
        trigger1_sent=int(row.get("trigger1_count") or 0) > 0,
        trigger2_sent=int(row.get("trigger2_count") or 0) > 0,
        weekly_sent_count=int(row.get("weekly_count") or 0),
        last_weekly_days=_days_ago(row.get("last_weekly_at"), now),
        trigger4_sent=int(row.get("trigger4_count") or 0) > 0,
        local_hour=local.hour,
        local_weekday=local.weekday(),
        has_contact_email=bool(str(row.get("contact_email") or "").strip()),
    )


def _candidate_sql(has_first_name: bool, has_outreach: bool, has_emails: bool) -> str:
    first_name_col = "b.contact_first_name" if has_first_name else "NULL AS contact_first_name"
    outreach_join = ""
    outreach_col = "NULL::text AS outreach_status"
    if has_outreach:
        outreach_join = "LEFT JOIN brand_outreach_tracking bot ON bot.brand_id = b.id"
        outreach_col = "bot.last_response_status AS outreach_status"

    if has_emails:
        email_cols = """
            (
                SELECT MAX(be.sent_at) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.status = 'sent'
            ) AS last_email_at,
            (
                SELECT COUNT(*) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.trigger_number = 1 AND be.status = 'sent'
            ) AS trigger1_count,
            (
                SELECT COUNT(*) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.trigger_number = 2 AND be.status = 'sent'
            ) AS trigger2_count,
            (
                SELECT COUNT(*) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.trigger_number = 3 AND be.status = 'sent'
            ) AS weekly_count,
            (
                SELECT MAX(be.sent_at) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.trigger_number = 3 AND be.status = 'sent'
            ) AS last_weekly_at,
            (
                SELECT COUNT(*) FROM brand_emails be
                WHERE be.brand_id = b.id AND be.trigger_number = 4 AND be.status = 'sent'
            ) AS trigger4_count,
            EXISTS (
                SELECT 1 FROM brand_emails be
                WHERE be.brand_id = b.id AND be.engaged = TRUE
            ) AS email_engaged
        """
    else:
        email_cols = """
            NULL::timestamptz AS last_email_at,
            0 AS trigger1_count,
            0 AS trigger2_count,
            0 AS weekly_count,
            NULL::timestamptz AS last_weekly_at,
            0 AS trigger4_count,
            FALSE AS email_engaged
        """

    return f"""
        SELECT DISTINCT ON (b.id)
            b.id AS brand_id,
            b.brand_name,
            b.contact_email,
            b.regions,
            {first_name_col},
            c.id AS campaign_id,
            c.token,
            c.status AS campaign_status,
            c.selected_application_ids,
            c.locked_at,
            (
                SELECT COUNT(*)::int FROM brand_pr_applications a
                WHERE a.brand_id = b.id AND a.status = 'review'
            ) AS applicant_count,
            (
                SELECT COUNT(*)::int FROM brand_pr_applications a
                WHERE a.brand_id = b.id AND a.status IN ('ships', 'posted')
            ) AS picked_count,
            (
                SELECT MIN(a.applied_at) FROM brand_pr_applications a
                WHERE a.brand_id = b.id AND a.status = 'review'
            ) AS oldest_waiting_at,
            (
                SELECT MAX(e.created_at) FROM brand_pr_events e
                WHERE e.brand_id = b.id AND {BRAND_VIEW_SQL}
            ) AS last_roster_view_at,
            (
                SELECT MAX(e.created_at) FROM brand_pr_events e
                WHERE e.brand_id = b.id
                  AND ({BRAND_VIEW_SQL} OR e.event IN ('roster_select', 'roster_lock'))
            ) AS last_roster_activity_at,
            {email_cols},
            {outreach_col}
        FROM pr_brands b
        JOIN brand_pr_campaigns c
          ON c.brand_id = b.id AND c.status = 'active'
        {outreach_join}
        WHERE b.contact_email IS NOT NULL
          AND TRIM(b.contact_email) <> ''
          AND EXISTS (
              SELECT 1 FROM brand_pr_applications a
              WHERE a.brand_id = b.id AND a.status = 'review'
          )
        ORDER BY b.id, c.created_at DESC
    """


def load_candidates(cursor, test_email: str = None, brand_id: int = None) -> list:
    has_first = public_column_exists(cursor, "pr_brands", "contact_first_name")
    has_outreach = public_table_exists(cursor, "brand_outreach_tracking")
    has_emails = public_table_exists(cursor, "brand_emails")
    sql = _candidate_sql(has_first, has_outreach, has_emails)
    clauses = []
    params: list = []
    if test_email:
        clauses.append("LOWER(b.contact_email) = %s")
        params.append(test_email.strip().lower())
    if brand_id:
        clauses.append("b.id = %s")
        params.append(int(brand_id))
    if clauses:
        sql = sql.replace(
            "ORDER BY b.id, c.created_at DESC",
            " AND " + " AND ".join(clauses) + " ORDER BY b.id, c.created_at DESC",
        )
    cursor.execute(sql, params)
    return list(cursor.fetchall() or [])


def _followers_label(n: Any) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "K"
    return str(n) if n > 0 else ""


def _niche_text(raw: Any) -> str:
    items = _as_list(raw)
    if items:
        return str(items[0]).strip()[:40]
    return str(raw or "").split(",")[0].strip()[:40]


def shortlist_from_rows(rows: list, limit: int = TOP_PICKS_LIMIT) -> list:
    picks = []
    for row in rows or []:
        username = str(row.get("username") or "").strip().lstrip("@")
        name = str(row.get("first_name") or "").strip() or username or "Creator"
        picks.append({
            "application_id": int(row["application_id"]),
            "name": safe_brand_name(name)[:40],
            "handle": f"@{username}" if username else "",
            "followers_label": _followers_label(row.get("followers")),
            "niche": _niche_text(row.get("niche")),
        })
        if len(picks) >= limit:
            break
    return picks


def load_top_picks(cursor, brand_id: int, campaign_id: int = None, limit: int = TOP_PICKS_LIMIT) -> list:
    """Best waiting applicants for the one-click shortlist: Pro, then content, then reach."""
    cursor.execute(
        """
        SELECT
            a.id AS application_id,
            c.username,
            c.niche,
            u.first_name,
            GREATEST(COALESCE(c.followers_count, 0), COALESCE(c.social_follower_count, 0)) AS followers
        FROM brand_pr_applications a
        JOIN creators c ON c.id = a.creator_id
        JOIN users u ON u.id = c.user_id
        WHERE a.brand_id = %s
          AND a.status = 'review'
          AND (a.campaign_id IS NULL OR a.campaign_id = %s)
        ORDER BY
            (LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
             AND LOWER(COALESCE(c.subscription_status, '')) = 'active') DESC,
            (COALESCE(a.selected_posts::text, '[]') NOT IN ('[]', 'null', '')) DESC,
            GREATEST(COALESCE(c.followers_count, 0), COALESCE(c.social_follower_count, 0)) DESC,
            a.applied_at ASC
        LIMIT %s
        """,
        (int(brand_id), int(campaign_id or 0), int(limit)),
    )
    return shortlist_from_rows(list(cursor.fetchall() or []), limit=limit)


def build_quick_pick_link(token: str, picks: list, applicant_count: int, trigger_number: int, base: str = None) -> str:
    root = (base or frontend_base()).rstrip("/")
    query = urlencode(
        {
            "pick": ",".join(str(p["application_id"]) for p in picks),
            "count": int(applicant_count),
            "utm_source": "email",
            "utm_campaign": "roster_waiting",
            "utm_medium": f"trigger_{int(trigger_number)}_quickpick",
        }
    )
    return f"{root}/r/{token}?{query}"


def _attach_waiting_apps_to_active_rosters(cursor) -> None:
    """Applicants often land with campaign_id NULL until a roster is opened."""
    if not public_table_exists(cursor, "brand_pr_applications"):
        return
    cursor.execute(
        """
        UPDATE brand_pr_applications a
        SET campaign_id = c.id
        FROM brand_pr_campaigns c
        WHERE c.brand_id = a.brand_id
          AND c.status = 'active'
          AND a.campaign_id IS NULL
          AND a.status IN ('review', 'ships', 'posted')
        """
    )


def _sync_opens_and_engagement(cursor, now: datetime) -> None:
    if not public_table_exists(cursor, "brand_emails"):
        return
    cursor.execute(
        f"""
        UPDATE brand_emails be
        SET roster_opened_at = e.opened_at,
            roster_clicked_at = COALESCE(be.roster_clicked_at, e.opened_at)
        FROM (
            SELECT e.brand_id, MIN(e.created_at) AS opened_at
            FROM brand_pr_events e
            WHERE {BRAND_VIEW_SQL}
            GROUP BY e.brand_id
        ) e
        WHERE be.brand_id = e.brand_id
          AND be.status = 'sent'
          AND be.roster_opened_at IS NULL
          AND e.opened_at >= be.sent_at
        """
    )
    cursor.execute(
        """
        UPDATE brand_emails be
        SET engaged = TRUE,
            engaged_at = COALESCE(be.engaged_at, NOW()),
            engaged_reason = COALESCE(be.engaged_reason, 'pick')
        FROM brand_pr_campaigns c
        WHERE c.brand_id = be.brand_id
          AND be.engaged = FALSE
          AND (
              c.status = 'locked'
              OR jsonb_array_length(COALESCE(c.selected_application_ids, '[]'::jsonb)) > 0
          )
        """
    )


def _log_send(cursor, row: dict, payload: dict, send_res: dict) -> None:
    cursor.execute(
        """
        INSERT INTO brand_emails (
            brand_id, campaign_id, trigger_number, email_type, to_email,
            subject, subject_variant, applicant_count, roster_link,
            message_id, status, skip_reason, sent_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, NOW()
        )
        """,
        (
            row["brand_id"],
            row.get("campaign_id"),
            payload["trigger_number"],
            payload["email_type"],
            row["contact_email"],
            payload["subject"],
            payload["subject_variant"],
            payload["applicant_count"],
            payload["roster_link"],
            send_res.get("message_id"),
            "sent" if send_res.get("success") else "failed",
            None if send_res.get("success") else (send_res.get("error") or "send_failed"),
        ),
    )


def _default_send_fn(to_email: str, subject: str, html: str, trigger_number: int = 1) -> dict:
    from services.resend_mail import resend_configured, send_resend_email

    if not resend_configured():
        return {
            "success": False,
            "message_id": None,
            "error": "RESEND_API_KEY not set",
            "retryable": False,
        }
    return send_resend_email(
        to_email,
        subject,
        html,
        tags=[
            {"name": "type", "value": "roster_waiting"},
            {"name": "trigger", "value": str(int(trigger_number))},
        ],
    )


def process_roster_waiting_emails(
    *,
    dry_run: bool = False,
    limit: int = 25,
    test_email: str = None,
    brand_id: int = None,
    skip_time_checks: bool = False,
    send_fn: Callable = None,
    now: datetime = None,
    conn=None,
) -> dict:
    """Evaluate active rosters and send at most `limit` waiting emails."""
    now = now or datetime.now(timezone.utc)
    owns_conn = conn is None
    if owns_conn:
        from pr_crm_routes import get_db_connection

        conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    stats = {
        "scanned": 0,
        "eligible": 0,
        "sent": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
        "would_send": [],
        "skip_reasons": {},
    }
    try:
        ensure_brand_emails_table(cursor, conn)
        _attach_waiting_apps_to_active_rosters(cursor)
        if not dry_run:
            _sync_opens_and_engagement(cursor, now)
        conn.commit()
        rows = load_candidates(cursor, test_email=test_email, brand_id=brand_id)
        stats["scanned"] = len(rows)
        mailer = send_fn or _default_send_fn
        if send_fn is None and not dry_run:
            from services.resend_mail import resend_configured

            if not resend_configured():
                stats["errors"] += 1
                stats["skip_reasons"]["resend_not_configured"] = 1
                return stats
        for row in rows:
            if stats["sent"] + len(stats["would_send"]) >= int(limit):
                break
            state = state_from_row(row, now)
            ok, reason = passes_caps(state)
            if not ok:
                stats["skipped"] += 1
                stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1
                continue
            choice = choose_trigger(state)
            if not choice:
                stats["skipped"] += 1
                stats["skip_reasons"]["no_trigger"] = stats["skip_reasons"].get("no_trigger", 0) + 1
                continue
            if not in_send_window(state, choice.number, skip_time_checks=skip_time_checks):
                stats["skipped"] += 1
                stats["skip_reasons"]["outside_send_window"] = stats["skip_reasons"].get(
                    "outside_send_window", 0
                ) + 1
                continue
            stats["eligible"] += 1
            greeting = greeting_for_brand(row)
            try:
                cursor.execute("SAVEPOINT top_picks")
                top_picks = load_top_picks(cursor, row["brand_id"], row.get("campaign_id"))
                cursor.execute("RELEASE SAVEPOINT top_picks")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT top_picks")
                print(f"[roster-waiting] top picks failed brand={row['brand_id']}: {exc}")
                top_picks = []
            payload = build_email(
                trigger_number=choice.number,
                brand_name=row.get("brand_name") or "",
                greeting=greeting,
                applicant_count=state.applicant_count,
                token=row.get("token") or "",
                top_picks=top_picks,
            )
            preview = {
                "brand_id": row["brand_id"],
                "brand_name": row.get("brand_name"),
                "to": row.get("contact_email"),
                "trigger": choice.number,
                "email_type": choice.email_type,
                "subject": payload["subject"],
                "applicant_count": state.applicant_count,
                "roster_link": payload["roster_link"],
                "quick_pick_link": payload["quick_pick_link"],
                "top_picks": [p["handle"] or p["name"] for p in payload["top_picks"]],
            }
            if dry_run:
                stats["would_send"].append(preview)
                continue
            try:
                send_res = mailer(
                    row["contact_email"],
                    payload["subject"],
                    payload["html"],
                    payload["trigger_number"],
                ) or {}
            except Exception as exc:
                send_res = {"success": False, "error": str(exc), "message_id": None}
            if send_res.get("success"):
                stats["sent"] += 1
            else:
                stats["errors"] += 1
            _log_send(cursor, row, payload, send_res)
            conn.commit()
        return stats
    finally:
        if owns_conn:
            conn.close()
