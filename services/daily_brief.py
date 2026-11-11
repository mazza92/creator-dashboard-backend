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
             drafts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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
    return {
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


def worth_sending(content: Dict[str, Any]) -> Tuple[bool, str]:
    """Something new to act on or to know. Goal progress or locked brands alone are not a reason to email."""
    if content.get("moves"):
        return True, "your_move"
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


def _section(title: str) -> str:
    return (f'<span style="display:block;margin:6px 0 0 0;font-size:12px;font-weight:700;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#6B7280;">{escape(title)}</span>')


def _row(name: str, detail: str, href: str, label: str, logo: Optional[str] = None, accent: bool = False) -> str:
    """One brand row: mark, name + what's next, and a one-tap button. Stacks cleanly at 320-390px."""
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    mark = (f'<img src="{escape(logo, quote=True)}" width="40" height="40" alt="" style="display:block;width:40px;'
            f'height:40px;border-radius:10px;object-fit:contain;background:#fff;border:1px solid #EEECE6;">' if logo else
            f'<div style="width:40px;height:40px;border-radius:10px;background:{"#FFF1E6" if accent else "#F1EFE9"};'
            f'color:#111827;font-weight:800;font-size:14px;line-height:40px;text-align:center;">{escape(initials)}</div>')
    border = "#FDBA74" if accent else "#E7E4DC"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {border};'
        f'border-radius:14px;margin:0 0 10px 0;"><tr>'
        f'<td width="52" style="padding:12px 0 12px 12px;vertical-align:top;">{mark}</td>'
        f'<td style="padding:12px;vertical-align:top;font-family:{FONT};">'
        f'<div style="font-size:15px;font-weight:800;color:#111827;line-height:1.3;">{escape(name)}</div>'
        f'<div style="font-size:13px;color:#4B5563;margin:2px 0 10px 0;line-height:1.45;">{detail}</div>'
        f'<a href="{escape(href, quote=True)}" style="display:inline-block;padding:9px 14px;border-radius:999px;'
        f'background:#111827;color:#FFFFFF;font-size:13px;font-weight:700;text-decoration:none;">{escape(label)}</a>'
        f'</td></tr></table>'
    )


def goal_line(content: Dict[str, Any]) -> str:
    goal = content["goal"]
    target, total = goal["amount_cents"], goal["total_cents"]
    if not target:
        return ""
    pct = max(0, min(100, round(total * 100 / target))) if target else 0
    days = content.get("days_left")
    head = (f"<strong>{_money(total)} of {_money(target)}</strong> toward your {escape(content['month'])} goal"
            + (f" &middot; {days} day{'s' if days != 1 else ''} left" if days is not None else ""))
    bar = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 0 0;">'
           f'<tr><td style="background:#F1EFE9;border-radius:999px;height:8px;line-height:8px;font-size:0;">'
           f'<div style="width:{max(pct, 2)}%;background:#C2410C;border-radius:999px;height:8px;"></div>'
           f'</td></tr></table>')
    pipe = goal["pipeline_cents"]
    extra = (f'<span style="display:block;margin-top:6px;font-size:13px;color:#6B7280;">'
             f'Plus {_money(pipe)} from brands you\'re talking to.</span>' if pipe else "")
    if not goal.get("set"):
        extra += (f'<span style="display:block;margin-top:6px;font-size:13px;color:#6B7280;">That\'s my suggested '
                  f'goal. <a href="{escape(tracker_link(campaign="brief_goal"), quote=True)}" '
                  f'style="color:#6B7280;">Set your own</a>.</span>')
    return head + bar + extra


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


def brief_lines(content: Dict[str, Any], creator_id: Any = None) -> List[str]:
    from services.polly_alerts import chip_link, upgrade_link

    lines: List[str] = []
    g = goal_line(content)
    if g:
        lines.append(g)

    moves = content.get("moves") or []
    if moves:
        rows = "".join(
            _row(m.get("brand_name") or "Brand", move_detail(m), tracker_link(m.get("brand_id")),
                 ACTION_BUTTON.get((m.get("next") or {}).get("action"), "Open"), m.get("logo"), accent=bool(m.get("hot")))
            for m in moves)
        more = content.get("moves_more") or 0
        tail = (f'<span style="display:block;font-size:13px;color:#6B7280;">And {more} more on '
                f'<a href="{escape(tracker_link(), quote=True)}" style="color:#6B7280;">your Tracker</a>.</span>'
                if more else "")
        lines.append(_section("Your move") + '<span style="display:block;height:8px;"></span>' + rows + tail)

    hot = content.get("hot")
    if hot and not hot.get("pitched"):
        reason = escape(hot.get("reason") or "Brands like this pick creators fast.")
        if not content.get("is_pro") and hot.get("locked"):
            detail = reason + "<br>Your free pitches are used this month. On Pro it goes out today."
            label, href = "Send it with Pro", upgrade_link("brief_hot")
        elif content.get("is_pro"):
            detail = reason + ("<br>Autopilot pitches them today, from your Gmail." if hot.get("queued")
                               else "<br>Your pitch for them is ready.")
            label, href = "See the pitch", tracker_link(hot["brand_id"], "brief_hot")
        else:
            detail = reason
            label, href = "Pitch it", tracker_link(hot["brand_id"], "brief_hot")
        lines.append(_section("🔥 Hot today") + '<span style="display:block;height:8px;"></span>'
                     + _row(hot["brand_name"], detail, href, label, hot.get("logo"), accent=True))

    sends, followups = content.get("sends") or [], content.get("followups") or []
    if sends or followups:
        bits = []
        gig_sends = [s for s in sends if s.get("gig")]
        brand_sends = [s for s in sends if not s.get("gig")]
        if brand_sends:
            bits.append(f"📤 Today I pitch {_names(brand_sends)} from your Gmail.")
        if gig_sends:
            bits.append(f"💼 I apply to {_names(gig_sends)} (paid gig{'s' if len(gig_sends) != 1 else ''}).")
        if followups:
            replied = " &middot; ".join(
                f'<a href="{escape(chip_link("checkin_replied", brand_id=f.get("brand_id"), brand_name=f["brand_name"], kind=KIND), quote=True)}" '
                f'style="color:#C2410C;font-weight:600;">{escape(f["brand_name"])} replied</a>'
                for f in followups[:MAX_NAMES])
            bits.append(f"🔁 Follow-ups today: {_names(followups)}. Already heard back? Tap it and I'll hold "
                        f"that follow-up:<br>{replied}")
        lines.append(_section("Autopilot today") + "<br>" + "<br>".join(bits))

    gigs, fresh = content.get("gigs_applied") or [], content.get("fresh_gigs") or []
    if gigs or fresh:
        bits = []
        if gigs:
            named = ", ".join(f"<strong>{escape(gg.get('brand_name') or 'a brand')}</strong>"
                              + (f" ({escape(str(gg['pay']))})" if gg.get("pay") else "") for gg in gigs[:4])
            bits.append(f"💰 I applied to {len(gigs)} paid UGC gig{'s' if len(gigs) != 1 else ''} for you: {named}.")
        for gig in fresh:
            from services.ugc_roundup import apply_href, gig_meta
            meta = " &middot; ".join(escape(b) for b in gig_meta(gig))
            href = escape(apply_href(gig), quote=True)
            bits.append(f'<a href="{href}" style="color:#111827;font-weight:700;text-decoration:none;">'
                        f'{escape(gig.get("headline") or "Paid UGC gig")}</a>'
                        + (f'<br><span style="font-size:13px;color:#6B7280;">{meta}</span>' if meta else "")
                        + f' <a href="{href}" style="font-size:13px;font-weight:600;color:#C2410C;">Apply &rarr;</a>')
        lines.append(_section("Paid gigs") + "<br>" + "<br>".join(bits))

    drafts = content.get("drafts") or []
    if drafts:
        rows = "".join(_row(d.get("brand_name") or "Brand", "Pitch written. Sends from your Gmail.",
                            chip_link("send_draft", brand_id=d.get("brand_id"), brand_name=d.get("brand_name") or "",
                                      kind=KIND), "Send it")
                       for d in drafts)
        lines.append(_section("Ready to send") + '<span style="display:block;height:8px;"></span>' + rows)

    free = content.get("free")
    if free and not content.get("is_pro"):
        locked, left = int(free.get("locked") or 0), free.get("pitches_left")
        if locked:
            lines.append(f'<span style="color:#6B7280;">{locked} more brand{"s are" if locked != 1 else " is"} lined '
                         f'up for you, waiting on a pitch. On <a href="{escape(upgrade_link("brief_locked"), quote=True)}" '
                         f'style="color:#6B7280;font-weight:600;">Pro ($19/mo)</a> I send them from your Gmail every '
                         f'weekday.</span>')
        elif left is not None:
            lines.append(f'<span style="color:#6B7280;">{left} free pitch{"es" if left != 1 else ""} left this month.'
                         f'</span>')
    return lines


def brief_email(first_name: str, content: Dict[str, Any], unsubscribe_url: str = "",
                creator_id: Any = None) -> Dict[str, str]:
    from services.polly_alerts import frontend_url, render_email

    subject = subject_for(content)
    headline = f"Your {content.get('weekday') or 'daily'} brief"
    buttons = [{"label": "Open my Tracker", "href": tracker_link(campaign="brief_open")}]
    links = [{"label": "Daily, weekly or off", "href": f"{frontend_url()}{SETTINGS_PATH}?section=emails"}]
    html = render_email(first_name, headline, brief_lines(content, creator_id), buttons, unsubscribe_url, links=links)
    html = html.replace(
        "<html>", '<html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                  '<meta name="color-scheme" content="light"></head>', 1)
    return {"subject": subject, "html": html}


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
    )


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
