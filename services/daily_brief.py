"""The daily brief: one email each weekday around 7am in the creator's time, instead of a flood of alerts.

Why (Nov 2026): a Pro member could get a roundup, a reminder and a report on the same day, each saying a bit of
the same thing. The brief says it once, from the Tracker (services.creator_tracker.build_tracker):
- goal progress this month;
- "Your move": up to 3 brands waiting on the creator, each with a one-tap button into the Tracker card;
- today's hot brand and why (services.hot_brands, when it exists);
- what Autopilot sends today and the follow-ups due today;
- paid gigs Autopilot applied to since the last brief, and fresh paid gigs in their niche (Pro; this replaces
  the 48h paid-gig roundup for creators on the daily brief);
- free creators: pitches Polly wrote that are waiting for their tap (replaces the unsent-pitch reminder), brands
  lined up beyond their free pitches, and one soft Pro line.

Rules:
- weekdays only, local hour 7 (a 3-hour window so a missed cron run still catches up), once per creator per day:
  a row in polly_email_log (kind 'daily_brief', ref_id = local date YYYYMMDD, unique) is claimed before sending;
- Monday: the Monday report (Pro board / free check-in) replaces the brief. Never both;
- nothing worth saying (only the goal line, or only locked brands) means no email that day;
- preferences: daily / weekly (Monday report only) / off (no brief, no Monday report), in email_preferences.
  Default: daily for Pro, weekly for free;
- the email budget (services.email_policy) still decides: Pro 1 routine a day and 6 a week, free 3 a week.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

KIND = "daily_brief"
MODES = ("daily", "weekly", "off")
SEND_HOUR_LOCAL = 7
SEND_WINDOW_HOURS = 3
MAX_MOVES = 3
MAX_NAMES = 3
MAX_FRESH_GIGS = 2
TRACKER_PATH = "/creator/dashboard/tracker"
SETTINGS_PATH = "/creator/dashboard/settings"
DEFAULT_TZ = "America/New_York"
# Since-last-brief lookback: the brief, the Monday report and the old roundup all reported gigs up to their send.
SINCE_KINDS = (KIND, "monday_board", "ugc_roundup")
SINCE_DEFAULT = timedelta(hours=24)
SINCE_MAX = timedelta(days=4)

# Creators pick a country (services.creator_country); multi-zone countries get their most populous zone.
COUNTRY_TZ = {
    "US": "America/New_York", "CA": "America/Toronto", "GB": "Europe/London", "IE": "Europe/Dublin",
    "AU": "Australia/Sydney", "NZ": "Pacific/Auckland", "PT": "Europe/Lisbon", "FI": "Europe/Helsinki",
    "EE": "Europe/Tallinn", "LV": "Europe/Riga", "LT": "Europe/Vilnius", "GR": "Europe/Athens",
    "BG": "Europe/Sofia", "RO": "Europe/Bucharest", "CY": "Asia/Nicosia",
}
CET_COUNTRIES = ("AT", "BE", "HR", "CZ", "DK", "FR", "DE", "HU", "IT", "LU", "MT", "NL", "PL", "SK", "SI", "ES",
                 "SE", "NO", "CH")

MONEY_STAGES = ("negotiating",)
ACTION_BUTTON = {
    "answer": "Reply", "address": "Send address", "date": "Set the date", "post": "Mark posted",
    "ad_usage": "Ask about ads", "replied": "Log their reply",
}

_READY = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Preferences and time zone
# ---------------------------------------------------------------------------

def ensure_tables(conn) -> None:
    """email_preferences.daily_brief / brief_timezone, and one brief per creator per local day in the email log."""
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_columns, ensure_index
    from services.polly_alerts import ensure_alert_tables

    ensure_alert_tables(conn)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS email_preferences (
        id SERIAL PRIMARY KEY, creator_id INT UNIQUE NOT NULL,
        receive_onboarding BOOLEAN DEFAULT true, receive_education BOOLEAN DEFAULT true,
        receive_behavioral BOOLEAN DEFAULT true, receive_weekly_digest BOOLEAN DEFAULT true,
        receive_celebration BOOLEAN DEFAULT true, receive_reengagement BOOLEAN DEFAULT true,
        receive_pro_nudges BOOLEAN DEFAULT true, unsubscribed_all BOOLEAN DEFAULT false,
        unsubscribed_at TIMESTAMP, timezone VARCHAR(50) DEFAULT 'America/New_York',
        created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())""")
    ensure_columns(cur, "ALTER TABLE email_preferences ADD COLUMN IF NOT EXISTS daily_brief TEXT, "
                        "ADD COLUMN IF NOT EXISTS brief_timezone TEXT")
    ensure_index(cur, f"CREATE UNIQUE INDEX IF NOT EXISTS uq_polly_email_log_daily_brief "
                      f"ON polly_email_log (creator_id, ref_id) WHERE kind = '{KIND}'")
    conn.commit()
    _READY = True


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (name,))
    row = cur.fetchone()
    return bool(row and (row["ok"] if isinstance(row, dict) else row[0]))


def brief_settings(conn, creator_id: int) -> Dict[str, Any]:
    """Stored mode and time zone hints. Never raises (the email rulebook calls this mid-transaction): missing
    tables or columns read as unset."""
    try:
        cur = _cursor(conn)
        if not _table_exists(cur, "creators"):
            return {}
        if _table_exists(cur, "email_preferences"):
            cur.execute("""SELECT to_jsonb(c)->>'country' AS country, to_jsonb(p)->>'daily_brief' AS mode,
                                  to_jsonb(p)->>'brief_timezone' AS brief_timezone, to_jsonb(p)->>'timezone' AS timezone,
                                  to_jsonb(p)->>'unsubscribed_all' AS unsubscribed_all
                           FROM creators c LEFT JOIN email_preferences p ON p.creator_id = c.id
                           WHERE c.id = %s""", (creator_id,))
        else:
            cur.execute("SELECT to_jsonb(c)->>'country' AS country FROM creators c WHERE c.id = %s", (creator_id,))
        return dict(cur.fetchone() or {})
    except Exception as err:
        print(f"[daily brief] settings lookup failed creator={creator_id}: {err}")
        return {}


def default_mode(is_pro: bool) -> str:
    return "daily" if is_pro else "weekly"


def resolve_mode(raw: Optional[str], is_pro: bool) -> str:
    raw = (raw or "").strip().lower()
    return raw if raw in MODES else default_mode(is_pro)


def _valid_tz(name: Optional[str]) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return name
    except Exception:
        return None


def creator_timezone(settings: Dict[str, Any]) -> str:
    """The brief's own time zone (set from the app), a lifecycle time zone someone changed from its default,
    the creator's country, then New York (where most creators are)."""
    explicit = _valid_tz(settings.get("brief_timezone"))
    if explicit:
        return explicit
    stored = _valid_tz(settings.get("timezone"))
    if stored and stored != DEFAULT_TZ:
        return stored
    country = str(settings.get("country") or "").strip().upper()
    if country in COUNTRY_TZ:
        return COUNTRY_TZ[country]
    if country in CET_COUNTRIES:
        return "Europe/Paris"
    return DEFAULT_TZ


def local_time(now: datetime, tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return _aware(now).astimezone(ZoneInfo(tz_name))
    except Exception:
        return _aware(now).astimezone(timezone.utc)


def get_preferences(conn, creator_id: int, is_pro: bool) -> Dict[str, Any]:
    ensure_tables(conn)
    settings = brief_settings(conn, creator_id)
    raw = (settings.get("mode") or "").strip().lower() or None
    return {
        "daily_brief": resolve_mode(raw, is_pro),
        "is_default": raw not in MODES,
        "default": default_mode(is_pro),
        "timezone": creator_timezone(settings),
        "options": [
            {"id": "daily", "label": "Daily", "detail": "Each weekday around 7am. Monday is your weekly report."},
            {"id": "weekly", "label": "Weekly", "detail": "Monday's weekly report only."},
            {"id": "off", "label": "Off", "detail": "No brief and no weekly report. Brand news still reaches you."},
        ],
    }


def set_preferences(conn, creator_id: int, mode: Optional[str] = None, tz_name: Optional[str] = None) -> None:
    """Upsert the brief mode and/or time zone. Raises ValueError on a bad value."""
    ensure_tables(conn)
    sets: List[Tuple[str, Any]] = []
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in MODES:
            raise ValueError("daily_brief must be daily, weekly or off")
        sets.append(("daily_brief", mode))
    if tz_name is not None:
        tz = _valid_tz(str(tz_name))
        if not tz:
            raise ValueError("unknown timezone")
        sets.append(("brief_timezone", tz))
    if not sets:
        return
    cols = ", ".join(c for c, _ in sets)
    cur = conn.cursor()
    cur.execute(
        f"""INSERT INTO email_preferences (creator_id, {cols}) VALUES (%s, {', '.join(['%s'] * len(sets))})
            ON CONFLICT (creator_id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c, _ in sets)},
            updated_at = NOW()""",
        (creator_id, *[v for _, v in sets]))
    conn.commit()


# ---------------------------------------------------------------------------
# What goes in (pure: tracker dict + today's facts -> content)
# ---------------------------------------------------------------------------

def tracker_link(brand_id: Any = None, campaign: str = KIND) -> str:
    from services.polly_alerts import frontend_url

    params: Dict[str, Any] = {}
    try:
        if brand_id and int(brand_id) > 0:
            params["brand"] = int(brand_id)
    except (TypeError, ValueError):
        pass
    params.update({"utm_source": "email", "utm_medium": KIND, "utm_campaign": campaign})
    return f"{frontend_url()}{TRACKER_PATH}?{urlencode(params)}"


def _money(cents: Any) -> str:
    try:
        dollars = int(cents or 0) / 100
    except (TypeError, ValueError):
        return ""
    return f"${dollars:,.0f}" if dollars == int(dollars) else f"${dollars:,.2f}"


def _all_cards(tracker: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for col in tracker.get("columns") or [] for c in col.get("cards") or []]


def your_moves(tracker: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every card waiting on the creator: paid offers first, then the hot brand, then by money and date."""
    moves = [c for c in _all_cards(tracker) if (c.get("next") or {}).get("your_move")]

    def rank(c):
        paid = c.get("type") in ("paid", "gig") and c.get("stage") in MONEY_STAGES
        return (not paid, not c.get("hot"), -int(c.get("value_cents") or 0), (c.get("next") or {}).get("date") or "9999")

    return sorted(moves, key=rank)


def assemble(tracker: Dict[str, Any], *, local: datetime, today: Optional[List[Dict[str, Any]]] = None,
             gigs_applied: Optional[List[Dict[str, Any]]] = None, fresh_gigs: Optional[List[Dict[str, Any]]] = None,
             drafts: Optional[List[Dict[str, Any]]] = None, since: Optional[datetime] = None,
             pulse: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the brief can say today, from the Tracker and today's Autopilot rows."""
    is_pro = bool(tracker.get("is_pro"))
    goal = tracker.get("goal") or {}
    moves = your_moves(tracker)
    hot = tracker.get("hot") if (tracker.get("hot") or {}).get("brand_id") else None
    hot_card = None
    if hot:
        hot_card = next((c for c in _all_cards(tracker) if c.get("brand_id") == hot["brand_id"]), None)
    sends, followups = [], []
    for row in today or []:
        meta = row.get("pitch_meta") if isinstance(row.get("pitch_meta"), dict) else {}
        item = {"brand_id": row.get("brand_id"), "brand_name": row.get("brand_name") or "a brand",
                "gig": meta.get("origin") == "paid_gig", "pay": meta.get("pay")}
        (followups if row.get("what") == "followup" else sends).append(item)
    free = tracker.get("free") or None
    facts = outreach_facts(tracker, local, since)
    return {
        **facts,
        "pulse": pulse or None,
        "is_pro": is_pro,
        "weekday": local.strftime("%A"),
        "local_date": local.date().isoformat(),
        "month": local.strftime("%B"),
        "days_left": tracker.get("days_left"),
        "goal": {"amount_cents": int(goal.get("amount_cents") or 0), "total_cents": int(goal.get("total_cents") or 0),
                 "pipeline_cents": int(goal.get("pipeline_cents") or 0), "set": bool(goal.get("set"))},
        "moves": moves[:MAX_MOVES],
        "moves_more": max(0, len(moves) - MAX_MOVES),
        "hot": ({"brand_id": hot["brand_id"], "brand_name": hot.get("brand_name") or (hot_card or {}).get("brand_name")
                 or "A brand", "reason": hot.get("reason") or "", "logo": hot.get("logo"),
                 "locked": bool((hot_card or {}).get("locked")), "queued": bool(hot.get("queued_item_id")),
                 "pitched": (hot_card or {}).get("column") not in (None, "lined_up")} if hot else None),
        "sends": sends,
        "followups": followups,
        "gigs_applied": list(gigs_applied or []),
        "fresh_gigs": list(fresh_gigs or [])[:MAX_FRESH_GIGS],
        "drafts": list(drafts or [])[:MAX_MOVES],
        "free": free,
    }


REPLIED_COLUMNS = ("replied", "negotiating")
CHECK_WINDOW_DAYS = 14


def outreach_facts(tracker: Dict[str, Any], local: datetime, since: Optional[datetime] = None) -> Dict[str, Any]:
    """What Autopilot did, what's new and what's next, from the Tracker's calendar, funnel and cards."""
    cal = tracker.get("calendar") or []
    today = local.date()
    yday = (today - timedelta(days=1)).isoformat()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    t_iso = today.isoformat()
    sent = [e for e in cal if e.get("kind") == "sent"]
    y_sent = [e for e in sent if e.get("date") == yday]
    y_fu = [e for e in cal if e.get("kind") == "fu" and e.get("label") == "Follow-up sent" and e.get("date") == yday]
    week_sent = [e for e in sent if week_start <= str(e.get("date")) <= t_iso]
    seen_week = (today - timedelta(days=6)).isoformat()
    opened_week = {e.get("brand_id") for e in cal if e.get("kind") == "view" and str(e.get("date")) >= seen_week}
    funnel = tracker.get("funnel") or {}
    cards = _all_cards(tracker)
    moves = your_moves(tracker)
    replies = [c for c in moves if c.get("column") in REPLIED_COLUMNS]
    new_views = []
    for v in tracker.get("recent_views") or []:
        at = _aware(v.get("last_at")) if not isinstance(v.get("last_at"), str) else None
        if isinstance(v.get("last_at"), str):
            try:
                at = _aware(datetime.fromisoformat(v["last_at"].replace("Z", "+00:00")))
            except ValueError:
                at = None
        if v.get("replied"):
            continue
        if since is None or (at and at >= since):
            new_views.append(v)
    last_sent = {}
    for e in sent:
        last_sent[e.get("brand_id")] = max(str(e.get("date")), last_sent.get(e.get("brand_id"), ""))
    oldest = (today - timedelta(days=CHECK_WINDOW_DAYS)).isoformat()
    check = sorted((c for c in cards if c.get("column") in ("pitched", "followed_up")
                    and not (c.get("next") or {}).get("your_move")
                    and last_sent.get(c.get("brand_id"), "") >= oldest
                    and last_sent.get(c.get("brand_id"), "") < t_iso),
                   key=lambda c: last_sent.get(c.get("brand_id"), ""), reverse=True)
    upcoming: Dict[str, List[Dict[str, Any]]] = {}
    for e in cal:
        if e.get("kind") in ("sched", "due", "post", "pay") and str(e.get("date")) > t_iso:
            upcoming.setdefault(str(e["date"]), []).append(e)
    next_days = sorted(upcoming)[:2]
    week_end = (today + timedelta(days=7)).isoformat()
    later = sum(1 for d, v in upcoming.items() if d not in next_days and d <= week_end
                for x in v if x.get("kind") == "sched")
    return {
        "yesterday_pitches": [{"brand_id": e.get("brand_id"), "brand_name": e.get("brand_name") or "a brand"} for e in y_sent],
        "yesterday_followups": [{"brand_id": e.get("brand_id"), "brand_name": e.get("brand_name") or "a brand"} for e in y_fu],
        "week_pitched": len(week_sent),
        "month_pitched": int(funnel.get("pitched") or 0),
        "month_needed": int(funnel.get("pitches_needed") or 0),
        "opened_week": len([b for b in opened_week if b]),
        "replied_month": int(funnel.get("replied") or 0),
        "replies": replies[:MAX_MOVES],
        "new_views": new_views[:MAX_MOVES],
        "check": [{"brand_id": c.get("brand_id"), "brand_name": c.get("brand_name") or "a brand"} for c in check[:3]],
        "upcoming": [{"date": d, "items": [{"brand_name": x.get("brand_name") or "a brand", "kind": x.get("kind"),
                                             "hot": bool(x.get("hot")), "label": x.get("label")} for x in upcoming[d]]}
                     for d in next_days],
        "planned_later": later,
    }


def worth_sending(content: Dict[str, Any]) -> Tuple[bool, str]:
    """Something new to act on or to know. Goal progress or locked brands alone are not a reason to email."""
    if content.get("moves"):
        return True, "your_move"
    if content.get("yesterday_pitches") or content.get("yesterday_followups"):
        return True, "done_for_you"
    if content.get("new_views"):
        return True, "kit_opens"
    if content.get("hot") and not content["hot"].get("pitched"):
        return True, "hot"
    for key in ("gigs_applied", "fresh_gigs", "sends", "followups", "drafts"):
        if content.get(key):
            return True, key
    return False, "nothing_new"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

FONT = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"


def _names(items: List[Dict[str, Any]], limit: int = MAX_NAMES) -> str:
    names = [f"<strong>{escape(i.get('brand_name') or 'a brand')}</strong>" for i in items[:limit]]
    more = len(items) - limit
    if more > 0:
        return ", ".join(names) + f" and {more} more"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


HERO_MIN_DAILY = 5          # under this, "this week" is the big number
MIN_PER_PITCH = 12          # minutes a creator spends finding, writing and sending one pitch
MIN_PER_FOLLOWUP = 5
INK, MUTED, LINE, PAPER = "#111827", "#6B7280", "#ECE9E1", "#FAF8F3"
ACCENT, GREEN, VIOLET = "#C2410C", "#047857", "#6D28D9"
ACTION_BUTTON_V2 = {**ACTION_BUTTON, "answer": "Review reply", "date": "Set the date", "post": "Open brief"}


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _kicker(title: str, aside: str = "") -> str:
    side = (f'<span style="font-weight:600;color:#9CA3AF;letter-spacing:0;text-transform:none;"> {escape(aside)}</span>'
            if aside else "")
    return (f'<div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:{INK};'
            f'margin:0 0 10px 0;">{title}{side}</div>')


def _section(inner: str, top: int = 26) -> str:
    return f'<tr><td class="pad" style="padding:{top}px 28px 0 28px;">{inner}</td></tr>'


def _btn(label: str, href: str, dark: bool = True) -> str:
    style = (f"background:{INK};color:#FFFFFF;border:1px solid {INK};" if dark
             else f"background:#FFFFFF;color:{INK};border:1px solid #D1CEC6;")
    return (f'<a href="{escape(href, quote=True)}" style="display:inline-block;padding:9px 14px;border-radius:999px;'
            f'{style}font-size:13px;font-weight:700;text-decoration:none;white-space:nowrap;">{escape(label)}</a>')


def summary_line(c: Dict[str, Any]) -> str:
    bits = []
    replies = c.get("replies") or []
    if replies:
        bits.append(f"<strong>{escape(replies[0]['brand_name'])} replied</strong>" if len(replies) == 1
                    else f"<strong>{len(replies)} brands replied</strong>")
    y = len(c.get("yesterday_pitches") or [])
    if y:
        bits.append(f"Polly pitched <strong>{_plural(y, 'brand')}</strong> for you yesterday")
    drafts = c.get("drafts") or []
    if drafts and not c.get("is_pro"):
        bits.append(f"your <strong>{escape(drafts[0].get('brand_name') or 'next')}</strong> pitch is ready to send"
                    if len(drafts) == 1 else f"<strong>{len(drafts)} pitches</strong> are ready to send")
    needs = len(c.get("moves") or [])
    if needs:
        bits.append(f"<strong>{_plural(needs, 'thing needs', 'things need')} you</strong> today")
    elif c.get("is_pro"):
        bits.append("nothing needs you today")
    if not bits:
        return "Here's where your outreach stands this morning."
    text = ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1]
    return text[0].upper() + text[1:] + "."


def preheader_text(c: Dict[str, Any]) -> str:
    plain = summary_line(c)
    for tag in ("<strong>", "</strong>"):
        plain = plain.replace(tag, "")
    return plain


def hero_block(c: Dict[str, Any]) -> str:
    y, week = len(c.get("yesterday_pitches") or []), int(c.get("week_pitched") or 0)
    if not (y or week or c.get("month_pitched")):
        return ""
    if y >= HERO_MIN_DAILY or (y and y >= week):
        label, num, sub = "Yesterday", y, "For you, from your Gmail, each written for that brand."
    else:
        label, num, sub = "This week", week, "Pitched for you from your Gmail, each written for that brand."
    fu = len(c.get("yesterday_followups") or [])
    if fu:
        sub += f" Plus {_plural(fu, 'follow-up')} yesterday."
    rows = (f'<div style="font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#9CA3AF;">'
            f'{label}</div><div style="font-size:30px;line-height:1.15;font-weight:800;letter-spacing:-.02em;'
            f'margin-top:4px;color:{INK};">{_plural(num, "brand")} pitched</div>'
            f'<div style="font-size:13px;color:{MUTED};margin-top:4px;line-height:1.45;">{sub}</div>')
    month, need = int(c.get("month_pitched") or 0), int(c.get("month_needed") or 0)
    pace = ""
    if need:
        try:
            from calendar import monthrange
            local = datetime.fromisoformat(c["local_date"])
            share = local.day / monthrange(local.year, local.month)[1]
        except Exception:
            share = 0.5
        on_pace = month >= need * share * 0.9
        pct = max(2, min(100, round(month * 100 / need)))
        tag = (f'<span style="color:{GREEN};font-weight:700;white-space:nowrap;">On pace ✓</span>' if on_pace
               else f'<span style="color:{ACCENT};font-weight:700;white-space:nowrap;">Behind pace</span>')
        goal = c.get("goal") or {}
        money = ""
        if int(goal.get("total_cents") or 0) > 0:
            money += f" · <strong style=\"color:{INK};\">{_money(goal['total_cents'])}</strong> earned"
        if int(goal.get("pipeline_cents") or 0) > 0:
            money += f" · <strong style=\"color:{INK};\">{_money(goal['pipeline_cents'])}</strong> in deals you're negotiating"
        target = f"Your plan for a {_money(goal['amount_cents'])} month" if int(goal.get("amount_cents") or 0) else "Your plan this month"
        pace = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;"><tr>'
                f'<td style="font-size:13.5px;color:#4B5563;"><strong style="color:{INK};">{month} of {need}</strong> '
                f'brands pitched in {escape(c.get("month") or "")}</td><td align="right" style="font-size:12.5px;">{tag}</td>'
                f'</tr></table><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 4px 0;">'
                f'<tr><td style="background:{LINE};border-radius:999px;height:8px;font-size:0;line-height:8px;">'
                f'<div style="width:{pct}%;height:8px;background:{ACCENT};border-radius:999px;"></div></td></tr></table>'
                f'<div style="font-size:12.5px;color:{MUTED};">{target}{money}</div>')
    tiles = "".join(
        f'<td class="tile" width="33%" style="padding:5px;"><div style="background:#FFFFFF;border-radius:12px;padding:10px 12px;">'
        f'<div style="font-size:18px;font-weight:800;color:{color};">{n}</div>'
        f'<div style="font-size:12px;color:{MUTED};">{label}</div></div></td>'
        for n, label, color in ((week, "pitched this week", INK), (c.get("opened_week") or 0, "opened your kit", VIOLET),
                                (c.get("replied_month") or 0, "replied", GREEN)))
    return _section(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};'
        f'border:1px solid {LINE};border-radius:16px;"><tr><td style="padding:18px 18px 6px 18px;">{rows}{pace}</td></tr>'
        f'<tr><td style="padding:8px 12px 12px 12px;"><table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0"><tr>{tiles}</tr></table></td></tr></table>', top=20)


def _line(icon: str, html: str, last: bool = False) -> str:
    border = "" if last else "border-bottom:1px solid #F1EFE9;"
    return (f'<tr><td style="padding:10px 0;{border}font-size:14.5px;line-height:1.45;color:{INK};">'
            f'<span style="display:inline-block;width:24px;">{icon}</span>{html}</td></tr>')


def whats_new_block(c: Dict[str, Any]) -> str:
    rows = []
    for r in (c.get("replies") or [])[:2]:
        rows.append(("💬", f"<strong>{escape(r['brand_name'])} replied</strong>"
                           f' <span style="color:{MUTED};">· {move_detail(r)}</span>'))
    for v in c.get("new_views") or []:
        n = int(v.get("count") or 1)
        rows.append(("👀", f"<strong>{escape(v.get('brand_name') or 'A brand')}</strong> opened your kit"
                           + (f" {n}×" if n > 1 else "") + f' <span style="color:{MUTED};">· a follow-up lands well now</span>'))
    hot = c.get("hot")
    if hot and not hot.get("pitched"):
        from services.polly_alerts import upgrade_link
        why = escape(hot.get("reason") or "running UGC ads right now")
        cta = ""
        if not c.get("is_pro") and hot.get("locked"):
            cta = (f' <a href="{escape(upgrade_link("brief_hot"), quote=True)}" style="color:{ACCENT};font-weight:700;'
                   f'text-decoration:none;">Send it with Pro →</a>')
        elif c.get("is_pro") and hot.get("queued"):
            cta = f' <span style="color:{MUTED};">· Autopilot pitches them today</span>'
        rows.append(("🔥", f"<strong>{escape(hot['brand_name'])}</strong> is hot today"
                           f' <span style="color:{MUTED};">· {why}</span>{cta}'))
    fresh = c.get("fresh_gigs") or []
    if fresh:
        rows.append(("💼", f"<strong>{_plural(len(fresh), 'new paid gig')}</strong> in your niche "
                           f'<a href="{escape(tracker_link(campaign="brief_gigs").replace(TRACKER_PATH, "/creator/dashboard/paid-gigs"), quote=True)}" '
                           f'style="color:{ACCENT};font-weight:700;text-decoration:none;">See them →</a>'))
    if not rows:
        return ""
    body = "".join(_line(i, h, last=(k == len(rows) - 1)) for k, (i, h) in enumerate(rows))
    return _section(_kicker("✨ What's new", "since yesterday")
                    + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{body}</table>')


def needs_you_block(c: Dict[str, Any]) -> str:
    from services.polly_alerts import chip_link

    moves, check = c.get("moves") or [], c.get("check") or []
    if not c.get("is_pro") and not moves:
        return ""
    cards = []
    for i, m in enumerate(moves):
        nxt = m.get("next") or {}
        kick = "Brand replied" if m.get("column") in REPLIED_COLUMNS else ("Booked" if m.get("column") == "booked" else "Your move")
        if m.get("type") in ("paid", "gig") and m.get("value_cents"):
            kick += f" · {_money(m['value_cents'])} paid offer"
        accent = i == 0
        cards.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid '
            f'{"#FDBA74" if accent else "#E7E4DC"};{"background:#FFF9F3;" if accent else ""}border-radius:14px;'
            f'margin:0 0 10px 0;"><tr><td style="padding:14px 10px 14px 16px;">'
            f'<div style="font-size:11.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;'
            f'color:{ACCENT if accent else MUTED};">{escape(kick)}</div>'
            f'<div style="font-size:15px;font-weight:800;line-height:1.3;margin-top:3px;color:{INK};">'
            f'{escape(m.get("brand_name") or "A brand")}</div>'
            f'<div style="font-size:13px;color:{MUTED};line-height:1.45;margin-top:2px;">{escape(nxt.get("label") or "")}</div>'
            f'</td><td width="1" align="right" valign="middle" style="padding:14px 14px 14px 0;">'
            f'{_btn(ACTION_BUTTON_V2.get(nxt.get("action"), "Open"), tracker_link(m.get("brand_id")), dark=accent)}'
            f'</td></tr></table>')
    more = int(c.get("moves_more") or 0)
    if more:
        cards.append(f'<div style="font-size:13px;color:{MUTED};margin:0 0 10px 0;">+ {more} more on '
                     f'<a href="{escape(tracker_link(), quote=True)}" style="color:{MUTED};font-weight:600;">your Tracker</a></div>')
    if not moves:
        cards.append(f'<div style="background:#F2FBF5;border:1px solid #DCF2E3;border-radius:14px;padding:14px 16px;'
                     f'font-size:14.5px;font-weight:700;color:#14532D;">Nothing needs you today. Polly\'s on it ✓</div>')
    if check:
        chips = "".join(
            f'<a href="{escape(chip_link("checkin_replied", brand_id=b.get("brand_id"), brand_name=b["brand_name"], kind=KIND), quote=True)}" '
            f'style="display:inline-block;margin:0 6px 6px 0;padding:7px 12px;border-radius:999px;border:1px solid #D1CEC6;'
            f'background:#FFFFFF;color:{INK};font-size:13px;font-weight:600;text-decoration:none;">{escape(b["brand_name"])}</a>'
            for b in check)
        cards.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAPER};'
            f'border-radius:14px;margin:4px 0 0 0;"><tr><td style="padding:14px 16px;">'
            f'<div style="font-size:14px;font-weight:700;color:{INK};">Quick check: did any of these reply?</div>'
            f'<div style="font-size:12.5px;color:{MUTED};margin:2px 0 10px 0;">One tap and I stop following up and track the deal.</div>'
            f'{chips}</td></tr></table>')
    return _section(_kicker("👋 Needs you", "only you can do these") + "".join(cards))


def _done_row(num: int, label: str, sub: str, last: bool = False) -> str:
    border = "" if last else "border-bottom:1px solid #DCF2E3;"
    return (f'<tr><td width="58" valign="top" style="padding:12px 0 12px 16px;{border}"><div style="font-size:22px;'
            f'line-height:1.1;font-weight:800;color:{GREEN};">{num}</div></td><td style="padding:12px 16px 12px 4px;'
            f'{border}"><div style="font-size:14.5px;font-weight:700;color:{INK};line-height:1.3;">{label}</div>'
            f'<div style="font-size:12.5px;color:{MUTED};line-height:1.45;margin-top:2px;">{sub}</div></td></tr>')


def done_block(c: Dict[str, Any]) -> str:
    from services.polly_alerts import chip_link, upgrade_link

    if not c.get("is_pro"):
        drafts = c.get("drafts") or []
        rows = "".join(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E7E4DC;'
            f'border-radius:14px;margin:0 0 10px 0;"><tr><td style="padding:14px 16px;"><div style="font-size:15px;'
            f'font-weight:800;color:{INK};">{escape(d.get("brand_name") or "Brand")}</div><div style="font-size:13px;'
            f'color:{MUTED};margin-top:2px;">Pitch written. Sends from your Gmail.</div></td><td width="1" align="right" '
            f'style="padding:14px 14px 14px 0;">{_btn("Send it", chip_link("send_draft", brand_id=d.get("brand_id"), brand_name=d.get("brand_name") or "", kind=KIND))}'
            f'</td></tr></table>' for d in drafts)
        free = c.get("free") or {}
        locked = int(free.get("locked") or 0)
        upsell = (f'<div style="font-size:13.5px;color:{MUTED};line-height:1.5;">{_plural(locked, "more brand is", "more brands are")} '
                  f'lined up for you. On <a href="{escape(upgrade_link("brief_locked"), quote=True)}" style="color:{INK};'
                  f'font-weight:700;">Pro ($19/mo)</a> Polly pitches them from your Gmail every weekday.</div>') if locked else ""
        if not (rows or upsell):
            return ""
        return _section((_kicker("✉️ Ready to send") + rows if rows else "") + upsell)
    y, fu = c.get("yesterday_pitches") or [], c.get("yesterday_followups") or []
    gigs = c.get("gigs_applied") or []
    items = []
    if y:
        items.append((len(y), "Pitches sent from your Gmail", escape(_names_plain(y))))
    if fu:
        items.append((len(fu), "Follow-ups sent", escape(_names_plain(fu))))
    if gigs:
        items.append((len(gigs), "Paid gigs applied to", " · ".join(
            escape(g.get("brand_name") or "a brand") + (f" {escape(str(g['pay']))}" if g.get("pay") else "") for g in gigs[:3])))
    if not items:
        return ""
    minutes = len(y) * MIN_PER_PITCH + len(fu) * MIN_PER_FOLLOWUP + len(gigs) * MIN_PER_PITCH
    saved = f"~{max(1, round(minutes / 60))} hr{'s' if round(minutes / 60) > 1 else ''} saved" if minutes >= 45 else f"~{minutes} min saved"
    head = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="font-size:12px;'
            f'font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:{INK};">🤖 Done for you</td>'
            f'<td align="right" style="font-size:12px;color:{GREEN};font-weight:700;">{saved}</td></tr></table>'
            f'<div style="height:10px;line-height:10px;">&nbsp;</div>')
    body = "".join(_done_row(n, l, sub, last=(k == len(items) - 1)) for k, (n, l, sub) in enumerate(items))
    return _section(head + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                           f'style="background:#F2FBF5;border:1px solid #DCF2E3;border-radius:14px;">{body}</table>')


def _names_plain(items: List[Dict[str, Any]], limit: int = MAX_NAMES) -> str:
    names = [i.get("brand_name") or "a brand" for i in items]
    more = len(names) - limit
    head = ", ".join(names[:limit])
    return f"{head} + {more} more" if more > 0 else head


def _day_label(iso: str, local_date: str) -> str:
    try:
        d = date.fromisoformat(iso)
        today = date.fromisoformat(local_date)
    except ValueError:
        return iso
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a")


def up_next_block(c: Dict[str, Any]) -> str:
    rows = []
    sends, fus = c.get("sends") or [], c.get("followups") or []
    if sends or fus:
        txt = []
        if sends:
            txt.append("Pitch " + ", ".join(("🔥 " if (c.get("hot") or {}).get("brand_id") == s.get("brand_id") else "")
                                            + f"<strong>{escape(s['brand_name'])}</strong>" if i == 0 else escape(s["brand_name"])
                                            for i, s in enumerate(sends[:3]))
                       + (f" + {len(sends) - 3} more" if len(sends) > 3 else ""))
        if fus:
            txt.append(_plural(len(fus), "follow-up"))
        rows.append(("Today", " · ".join(txt), ACCENT))
    for day in c.get("upcoming") or []:
        items = day.get("items") or []
        pitches = [i for i in items if i.get("kind") == "sched"]
        other = [i for i in items if i.get("kind") != "sched"]
        bits = []
        if pitches:
            bits.append("Pitch " + ", ".join(escape(i["brand_name"]) for i in pitches[:3])
                        + (f" + {len(pitches) - 3} more" if len(pitches) > 3 else ""))
        bits += [f"{escape(i['brand_name'])} {escape((i.get('label') or '').lower())}" for i in other[:2]]
        if bits:
            rows.append((_day_label(day["date"], c.get("local_date") or ""), " · ".join(bits), MUTED))
    later = int(c.get("planned_later") or 0)
    if later:
        rows.append(("This week", f"{_plural(later, 'more brand')} planned to stay on pace", MUTED))
    if not rows:
        return ""
    body = "".join(
        f'<tr><td width="84" valign="top" style="padding:7px 0;font-size:13px;font-weight:700;color:{color};">{label}</td>'
        f'<td style="padding:7px 0;font-size:14px;line-height:1.5;color:{INK};">{text}</td></tr>'
        for label, text, color in rows)
    return _section(_kicker("📅 Up next") + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{body}</table>')


def pulse_block(c: Dict[str, Any]) -> str:
    from services.polly_alerts import frontend_url
    from brand_categories import category_label

    p = c.get("pulse") or {}
    hook = p.get("hook") or {}
    if not hook.get("text"):
        return ""
    niche = category_label(p.get("niche") or "") or "your niche"
    href = (f"{frontend_url()}/creator/dashboard/pulse?"
            + urlencode({"niche": p.get("niche") or "", "utm_source": "email", "utm_medium": KIND, "utm_campaign": "brief_pulse"}))
    more = int(p.get("more") or 0)
    parts = [f'<div style="font-size:11.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:{VIOLET};">'
             f'Hook of the day</div><div style="font-size:15px;line-height:1.45;font-weight:700;margin:4px 0 2px 0;color:{INK};">'
             f'{escape(hook["text"])}</div><div style="font-size:12.5px;color:{MUTED};">'
             + (f"Seen in {hook['score']} {escape(niche.lower())} ads this week" if int(hook.get("score") or 0) > 1
                else f"From a live {escape(niche.lower())} ad")
             + (f' · <a href="{escape(href, quote=True)}" style="color:{VIOLET};font-weight:600;text-decoration:none;">'
                f'{_plural(more, "more hook")} →</a>' if more else
                f' · <a href="{escape(href, quote=True)}" style="color:{VIOLET};font-weight:600;text-decoration:none;">Open Pulse →</a>')
             + '</div>']
    for label, item in (("Trending now", p.get("trend")), ("Editing tip", p.get("tip"))):
        if not item or not item.get("text"):
            continue
        body = escape(item["text"]) if label == "Editing tip" else (
            f"<strong>{escape(item['text'])}</strong>" + (f". {escape(item['why'])}" if item.get("why") else ""))
        parts.append(f'<div style="height:1px;background:#ECE8FB;margin:14px 0;"></div><div style="font-size:11.5px;'
                     f'font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:{VIOLET};">{label}</div>'
                     f'<div style="font-size:14px;line-height:1.5;margin-top:4px;color:{INK};">{body}</div>')
    return _section(_kicker("📈 UGC pulse", f"{niche.lower()} · 2 min read")
                    + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F7F5FF;'
                      f'border:1px solid #ECE8FB;border-radius:14px;"><tr><td style="padding:16px;">{"".join(parts)}</td></tr></table>')


def move_detail(card: Dict[str, Any]) -> str:
    nxt = card.get("next") or {}
    bits = []
    if card.get("type") in ("paid", "gig") and card.get("value_cents"):
        bits.append(f"<strong>{_money(card['value_cents'])}</strong> paid offer")
    elif card.get("type") == "gifted" and card.get("stage") == "offered":
        bits.append("Gifted offer")
    bits.append(escape(nxt.get("label") or "Your move"))
    return " &middot; ".join(bits)


def subject_for(content: Dict[str, Any]) -> str:
    moves, hot = content.get("moves") or [], content.get("hot")
    paid = next((m for m in moves if m.get("type") in ("paid", "gig") and m.get("stage") in MONEY_STAGES), None)
    if paid:
        value = f" {_money(paid['value_cents'])}" if paid.get("value_cents") else ""
        return f"{paid['brand_name']} offered you{value}: your move"
    replies = content.get("replies") or []
    if replies:
        return (f"{replies[0]['brand_name']} replied 💬" if len(replies) == 1
                else f"{len(replies)} brands replied 💬")
    y = len(content.get("yesterday_pitches") or [])
    views = content.get("new_views") or []
    if y:
        tail = f" · {views[0].get('brand_name')} opened your kit" if views else ""
        return f"{_plural(y, 'brand')} pitched for you yesterday{tail}"
    if views:
        return f"{views[0].get('brand_name') or 'A brand'} opened your kit 👀"
    if hot and not hot.get("pitched"):
        return f"🔥 {hot['brand_name']} is hot today"
    if moves:
        if len(moves) == 1:
            return f"{moves[0]['brand_name']} is waiting on you"
        return f"{len(moves) + int(content.get('moves_more') or 0)} brands are waiting on you"
    gigs = content.get("gigs_applied") or []
    if gigs:
        return f"I applied to {len(gigs)} paid gig{'s' if len(gigs) != 1 else ''} for you"
    if content.get("fresh_gigs"):
        return "New paid UGC gigs in your niche"
    sends = content.get("sends") or []
    if sends:
        return f"{len(sends)} pitch{'es' if len(sends) != 1 else ''} go out today"
    if content.get("followups"):
        return "Follow-ups go out today"
    if content.get("drafts"):
        return "Your pitches are ready to send"
    return f"Your {content.get('weekday') or 'daily'} brief"


def brief_email(first_name: str, content: Dict[str, Any], unsubscribe_url: str = "",
                creator_id: Any = None) -> Dict[str, str]:
    """The morning brief: what's new, what's done, what's next. Each section only when it has something."""
    from services.polly_alerts import frontend_url

    c = content
    name = escape((first_name or "").strip() or "there")
    try:
        day = date.fromisoformat(c.get("local_date") or "")
        date_label = f"{day.strftime('%A')}, {day.strftime('%b')} {day.day}"
    except ValueError:
        date_label = c.get("weekday") or ""
    pre = escape(preheader_text(c))
    filler = "&#847;&zwnj;&nbsp;" * 90
    settings = f"{frontend_url()}{SETTINGS_PATH}?section=emails"
    sections = "".join(b for b in (hero_block(c), whats_new_block(c), needs_you_block(c), done_block(c),
                                   up_next_block(c), pulse_block(c)) if b)
    unsub = (f' · <a href="{escape(unsubscribe_url, quote=True)}" style="color:#9CA3AF;">Unsubscribe</a>'
             if unsubscribe_url else "")
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<title>{escape(subject_for(c))}</title>
<style>body{{margin:0;padding:0;background:#F4F2EC;}}
@media (max-width:600px){{.wrap{{padding:12px 8px !important;}}.pad{{padding-left:18px !important;padding-right:18px !important;}}.tile{{padding:3px !important;}}}}</style>
</head><body style="margin:0;padding:0;background:#F4F2EC;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;line-height:1px;color:transparent;">{pre}{filler}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F4F2EC;"><tr>
<td class="wrap" align="center" style="padding:24px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#FFFFFF;border-radius:20px;font-family:{FONT};color:{INK};">
<tr><td class="pad" style="padding:28px 28px 4px 28px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#9CA3AF;">{escape(date_label)} · Daily brief</td>
<td align="right" style="font-size:12px;font-weight:800;color:{INK};">NEWCOLLAB</td></tr></table>
<div style="font-size:26px;line-height:1.2;font-weight:800;letter-spacing:-.02em;margin:10px 0 6px 0;color:{INK};">Morning, {name} ☀️</div>
<div style="font-size:15px;line-height:1.5;color:#4B5563;">{summary_line(c)}</div>
</td></tr>
{sections}
<tr><td class="pad" align="center" style="padding:28px 28px 8px 28px;">
<a href="{escape(tracker_link(campaign='brief_open'), quote=True)}" style="display:block;background:{INK};color:#FFFFFF;text-decoration:none;font-size:15px;font-weight:800;padding:15px 20px;border-radius:999px;text-align:center;">Open my Tracker</a>
</td></tr>
<tr><td class="pad" align="center" style="padding:6px 28px 26px 28px;font-size:12.5px;color:#9CA3AF;line-height:1.6;">
— Polly, your pitching assistant<br>
<a href="{escape(settings, quote=True)}" style="color:#9CA3AF;">Daily, weekly or off</a>{unsub}
</td></tr>
</table></td></tr></table></body></html>"""
    return {"subject": subject_for(c), "html": html}


# ---------------------------------------------------------------------------
# Facts from the database
# ---------------------------------------------------------------------------

def autopilot_today(conn, creator_id: int, day_start: datetime, day_end: datetime) -> List[Dict[str, Any]]:
    """Pitches Autopilot sends (or sent) today and follow-ups due today, in the creator's local day."""
    try:
        cur = _cursor(conn)
        if not _table_exists(cur, "polly_autopilot_queue"):
            return []
        enabled_join = ("LEFT JOIN polly_autopilot a ON a.creator_id = q.creator_id"
                        if _table_exists(cur, "polly_autopilot") else "")
        enabled = "COALESCE(a.enabled, TRUE)" if enabled_join else "TRUE"
        cur.execute(f"""
            SELECT q.brand_id, q.brand_name, q.pitch_meta,
                   CASE WHEN q.followup_status IN ('scheduled', 'headsup') AND q.followup_due_at >= %(s)s
                             AND q.followup_due_at < %(e)s THEN 'followup' ELSE 'send' END AS what,
                   COALESCE(q.followup_due_at, q.scheduled_for, q.sent_at) AS at
            FROM polly_autopilot_queue q {enabled_join}
            WHERE q.creator_id = %(c)s AND (
                ({enabled} AND q.status IN ('draft', 'approved') AND q.scheduled_for >= %(s)s AND q.scheduled_for < %(e)s)
                OR (q.status = 'sent' AND q.sent_at >= %(s)s AND q.sent_at < %(e)s)
                OR ({enabled} AND q.status = 'sent' AND q.followup_status IN ('scheduled', 'headsup')
                    AND q.followup_due_at >= %(s)s AND q.followup_due_at < %(e)s))
            ORDER BY at""", {"c": creator_id, "s": day_start, "e": day_end})
        return [dict(r) for r in cur.fetchall()]
    except Exception as err:
        print(f"[daily brief] autopilot today skipped creator={creator_id}: {err}")
        _rollback(conn)
        return []


def last_brief_at(conn, creator_id: int, now: datetime) -> datetime:
    cur = _cursor(conn)
    cur.execute("SELECT MAX(sent_at) AS at FROM polly_email_log WHERE creator_id = %s AND ok AND kind = ANY(%s)",
                (creator_id, list(SINCE_KINDS)))
    row = cur.fetchone() or {}
    at = _aware(row.get("at"))
    if not at:
        return now - SINCE_DEFAULT
    return max(at, now - SINCE_MAX)


def paid_gigs_applied(conn, creator_id: int, since: datetime) -> List[Dict[str, Any]]:
    try:
        from services.gig_autopilot import gigs_applied
        return gigs_applied(conn, creator_id, since)
    except Exception as err:
        print(f"[daily brief] gigs applied skipped creator={creator_id}: {err}")
        _rollback(conn)
        return []


def fresh_paid_gigs(conn, creator_id: int, since: datetime, pool: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The 48h paid-gig roundup, folded in: new paid UGC gigs in the creator's niche since the last brief."""
    if not pool:
        return []
    try:
        from services.ugc_roundup import _creator_context, pick_gigs
        tokens, applied = _creator_context(conn, creator_id)
        fresh = [g for g in pool if _aware(g.get("published_at")) and _aware(g["published_at"]) > since]
        return [g for g in pick_gigs(fresh, tokens, applied, limit=MAX_FRESH_GIGS * 2) if g.get("is_matched")][:MAX_FRESH_GIGS]
    except Exception as err:
        print(f"[daily brief] fresh gigs skipped creator={creator_id}: {err}")
        _rollback(conn)
        return []


def gig_pool(conn, now: datetime) -> List[Dict[str, Any]]:
    try:
        from services.ugc_roundup import load_fresh_gigs
        return load_fresh_gigs(conn, (now - SINCE_MAX).replace(tzinfo=None))
    except Exception as err:
        print(f"[daily brief] gig pool skipped: {err}")
        _rollback(conn)
        return []


def waiting_drafts(conn, creator_id: int) -> List[Dict[str, Any]]:
    try:
        from services.polly_manager import unsent_drafts
        return unsent_drafts(conn, creator_id)
    except Exception as err:
        print(f"[daily brief] drafts skipped creator={creator_id}: {err}")
        _rollback(conn)
        return []


def gather(conn, creator_id: int, now: datetime, tz_name: str, is_pro: bool,
           pool: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    from services.creator_tracker import build_tracker

    local = local_time(now, tz_name)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    tracker = build_tracker(conn, creator_id, now=now)
    since = last_brief_at(conn, creator_id, now)
    return assemble(
        tracker, local=local,
        today=autopilot_today(conn, creator_id, day_start.astimezone(timezone.utc), day_end.astimezone(timezone.utc)),
        gigs_applied=paid_gigs_applied(conn, creator_id, since) if is_pro else [],
        fresh_gigs=fresh_paid_gigs(conn, creator_id, since, pool) if is_pro else [],
        drafts=[] if is_pro else waiting_drafts(conn, creator_id),
        since=since,
        pulse=creator_pulse(conn, creator_id),
    )


def creator_pulse(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    """Today's UGC Pulse (hook of the day, trend, tip) in the creator's niche. Never a reason to send on its own."""
    try:
        from services.creator_niches import recorded_niches
        from services.ugc_pulse import brief_pulse
        cur = _cursor(conn)
        cur.execute("SELECT niche, creator_niches FROM creators WHERE id = %s", (creator_id,))
        row = cur.fetchone()
        return brief_pulse(conn, recorded_niches(dict(row)) if row else [])
    except Exception as err:
        print(f"[daily brief] pulse skipped creator={creator_id}: {err}")
        _rollback(conn)
        return None


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

def local_ref(local: datetime) -> int:
    return int(local.strftime("%Y%m%d"))


def due_today(local: datetime) -> Tuple[bool, str]:
    """(due, reason) for the creator's local clock. Monday belongs to the Monday report."""
    if local.weekday() >= 5:
        return False, "weekend"
    if not (SEND_HOUR_LOCAL <= local.hour < SEND_HOUR_LOCAL + SEND_WINDOW_HOURS):
        return False, "not_7am"
    if local.weekday() == 0:
        return False, "monday_report"
    return True, "due"


def claim(conn, creator_id: int, ref: int, now: Optional[datetime] = None) -> Optional[int]:
    """One brief per creator per local day: the unique (creator_id, ref_id) row is taken before anything is sent."""
    cur = _cursor(conn)
    cur.execute(f"""INSERT INTO polly_email_log (creator_id, kind, ref_id, ok, error, sent_at)
                    VALUES (%s, %s, %s, FALSE, 'claimed', %s)
                    ON CONFLICT (creator_id, ref_id) WHERE kind = '{KIND}' DO NOTHING
                    RETURNING id""", (creator_id, KIND, ref, _aware(now or utc_now())))
    row = cur.fetchone()
    conn.commit()
    return int(row["id"]) if row else None


def finish(conn, log_id: int, *, ok: bool, subject: str = "", message_id: Optional[str] = None,
           error: Optional[str] = None, brand_id: Any = None, now: Optional[datetime] = None) -> None:
    cur = conn.cursor()
    cur.execute("""UPDATE polly_email_log SET ok = %s, subject = %s, message_id = %s, error = %s, brand_id = %s,
                   sent_at = %s WHERE id = %s""",
                (ok, (subject or "")[:200], message_id, (error or "")[:300] or None, brand_id,
                 _aware(now or utc_now()), log_id))
    conn.commit()


def candidates(conn, now: datetime, limit: int) -> List[Dict[str, Any]]:
    """Creators on the daily brief (Pro by default, free who chose it) with no brief claimed in the last 14h."""
    from services.waitlist_gate import WAITLIST_EXCLUDE_SQL

    cur = _cursor(conn)
    cur.execute(f"""
        SELECT c.id AS creator_id,
               (LOWER(COALESCE(c.subscription_tier, '')) = 'pro' OR COALESCE(c.unlocks_tier, '') = 'pro') AS is_pro,
               to_jsonb(c)->>'country' AS country, p.daily_brief AS mode, p.brief_timezone, p.timezone
        FROM creators c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN email_preferences p ON p.creator_id = c.id
        WHERE u.email IS NOT NULL AND u.unsubscribed_at IS NULL
          AND NOT COALESCE(p.unsubscribed_all, FALSE)
          AND {WAITLIST_EXCLUDE_SQL}
          AND (p.daily_brief = 'daily'
               OR (p.daily_brief IS NULL AND (LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
                                              OR COALESCE(c.unlocks_tier, '') = 'pro')))
          AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.kind = %s
                          AND l.sent_at > %s)
        ORDER BY c.id
        LIMIT %s""", (KIND, now - timedelta(hours=14), limit))
    return [dict(r) for r in cur.fetchall()]


def send_brief(conn, creator_id: int, *, now: datetime, is_pro: Optional[bool] = None,
               settings: Optional[Dict[str, Any]] = None, dry_run: bool = False, send_fn: Optional[Callable] = None,
               test_email: str = "", pool: Optional[List[Dict[str, Any]]] = None, force: bool = False) -> Dict[str, Any]:
    """Decide, build and send one creator's brief. Returns {'creator_id', 'status', 'reason', ...}.

    ``force`` (the creator's own preview) ignores the local clock; the budget and empty-day rules still apply."""
    from services import email_policy
    from services.polly_alerts import _default_send, _recipient, _unsubscribe_url

    settings = settings if settings is not None else brief_settings(conn, creator_id)
    tz_name = creator_timezone(settings)
    local = local_time(now, tz_name)
    out: Dict[str, Any] = {"creator_id": creator_id, "kind": KIND, "tz": tz_name, "local": local.isoformat()}
    due, why = due_today(local)
    if not due:
        if not force:
            return {**out, "status": "not_due", "reason": why}
        out["clock"] = why
    who = _recipient(conn, creator_id)
    if not who:
        return {**out, "status": "skipped", "reason": "no_recipient"}
    is_pro = who["is_pro"] if is_pro is None else is_pro
    mode = resolve_mode(settings.get("mode"), is_pro)
    ref = local_ref(local)
    log_id = None
    if not dry_run:
        log_id = claim(conn, creator_id, ref, now)
        if not log_id:
            return {**out, "status": "skipped", "reason": "already_done"}
    ok, rule = email_policy.decide(conn, creator_id, KIND, is_pro, now=now, brief={"mode": mode, "tz": tz_name})
    if not ok:
        if log_id:
            finish(conn, log_id, ok=False, error=f"skip:{rule}", now=now)
        return {**out, "status": "skipped", "reason": rule}
    try:
        content = gather(conn, creator_id, now, tz_name, is_pro, pool=pool)
    except Exception as err:
        print(f"[daily brief] build failed creator={creator_id}: {err}")
        _rollback(conn)
        if log_id:
            finish(conn, log_id, ok=False, error=f"build_failed: {err}", now=now)
        return {**out, "status": "error", "reason": "build_failed"}
    worth, what = worth_sending(content)
    if not worth:
        if log_id:
            finish(conn, log_id, ok=False, error="skip:nothing_new", now=now)
        return {**out, "status": "skipped", "reason": "nothing_new"}
    unsub = _unsubscribe_url(who["user_id"])
    mail = brief_email(who["first"], content, unsub, creator_id=creator_id)
    out.update({"subject": mail["subject"], "to": who["email"], "lead": what})
    if dry_run:
        return {**out, "status": "would_send", "reason": what, "html": mail["html"]}
    send = send_fn or _default_send
    try:
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, KIND) or {}
    except Exception as err:
        result = {"success": False, "error": str(err)}
    lead = (content.get("moves") or [{}])[0].get("brand_id") or (content.get("hot") or {}).get("brand_id")
    finish(conn, log_id, ok=bool(result.get("success")), subject=mail["subject"],
           message_id=result.get("message_id"), error=result.get("error"),
           brand_id=lead if isinstance(lead, int) and lead > 0 else None, now=now)
    return {**out, "status": "sent" if result.get("success") else "error", "reason": what}


def send_due_briefs(conn, now: Optional[datetime] = None, limit: int = 300, dry_run: bool = False,
                    send_fn: Optional[Callable] = None, test_email: str = "") -> Dict[str, Any]:
    """Hourly cron: every creator whose local clock says it's brief time gets theirs (once a day)."""
    now = _aware(now or utc_now())
    ensure_tables(conn)
    results: List[Dict[str, Any]] = []
    pool: Optional[List[Dict[str, Any]]] = None
    from services.email_policy import brief_rollout
    for row in candidates(conn, now, limit * 4):
        if not brief_rollout(row["creator_id"]):
            continue
        settings = {k: row.get(k) for k in ("country", "mode", "brief_timezone", "timezone")}
        local = local_time(now, creator_timezone(settings))
        if not due_today(local)[0]:
            continue
        if pool is None:
            pool = gig_pool(conn, now)
        try:
            res = send_brief(conn, row["creator_id"], now=now, is_pro=bool(row.get("is_pro")), settings=settings,
                             dry_run=dry_run, send_fn=send_fn, test_email=test_email, pool=pool)
        except Exception as err:
            print(f"[daily brief] creator={row['creator_id']} failed: {err}")
            _rollback(conn)
            res = {"creator_id": row["creator_id"], "status": "error", "reason": str(err)[:120]}
        res.pop("html", None)
        results.append(res)
        if sum(1 for r in results if r.get("status") in ("sent", "would_send")) >= limit:
            break
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"now": now.isoformat(), "counts": counts, "results": results}
