"""After a brand locks a gifted roster: brief the picked creators, nudge the brand to
ship, collect the content, and hand it to the brand with the next-roster offer."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlparse

from markupsafe import Markup
from psycopg2.extras import Json, RealDictCursor

from services.pg_hotpath_schema import ensure_columns, public_column_exists
from services.roster_waiting_email import (
    BRAND_FOOTER_NOTE,
    _jinja_env,
    ensure_brand_emails_table,
    frontend_base,
    greeting_for_brand,
    safe_brand_name,
)

DELIVERY_COLUMNS = {
    "delivered_content": "JSONB",
    "delivered_at": "TIMESTAMPTZ",
    "picked_notified_at": "TIMESTAMPTZ",
    "shipped_notified_at": "TIMESTAMPTZ",
    "variant_answer": "TEXT",
    "variant_answered_at": "TIMESTAMPTZ",
}
# A mistaken "shipped" can be undone this long after the click.
UNSHIP_WINDOW_HOURS = 24
MAX_VARIANT_LEN = 120
CREATOR_NOTICE_WINDOW_DAYS = 21
CONTENT_DUE_DAYS = 10
# Past this, the creator can't apply for more gifted PR until they deliver.
CONTENT_OVERDUE_DAYS = CONTENT_DUE_DAYS + 4
# (days after shipping, reminder number); the last one is the overdue notice.
CONTENT_REMINDERS = ((5, 1), (CONTENT_DUE_DAYS, 2), (CONTENT_OVERDUE_DAYS, 3))
CONTENT_REMINDER_WINDOW_DAYS = 60
CONTENT_REMINDER_EVENT = "roster_content_reminder"
SHIP_REMINDERS = ((2, 21, "ship_reminder_1"), (5, 22, "ship_reminder_2"))
SHIP_REMINDER_MAX_AGE_DAYS = 30
# Days since the brand's last pick on a roster they never locked.
LOCK_REMINDERS = ((1, 11, "lock_reminder_1"), (3, 12, "lock_reminder_2"))
LOCK_REMINDER_MAX_AGE_DAYS = 21
CONTENT_READY_TRIGGER = 30
CONTENT_READY_TYPE = "content_delivered"
PLAN_PRICE_LABEL = "$299/mo"
MAX_URL_LEN = 600
MAX_NOTE_LEN = 500

BRIEF_FIELDS = {
    "message": 280,
    "must_include": 500,
    "avoid": 300,
    "tags": 160,
    # e.g. "Which bedsheet size do you need? Twin, Full, Queen or King". Picked creators answer it on Newcollab.
    "variant_question": 160,
}

_READY = False
_BRIEF_READY = False
_READY_LOCK = threading.Lock()


def ensure_delivery_columns(cursor, conn=None) -> bool:
    global _READY
    if _READY:
        return True
    with _READY_LOCK:
        if _READY:
            return True
        ok = ensure_columns(cursor, "brand_pr_applications", DELIVERY_COLUMNS)
        if ok and conn is not None:
            conn.commit()
        _READY = ok
        return ok


def ensure_brief_column(cursor, conn=None) -> bool:
    global _BRIEF_READY
    if _BRIEF_READY:
        return True
    ok = ensure_columns(cursor, "brand_pr_campaigns", {"brief": "JSONB"})
    if ok and conn is not None:
        conn.commit()
    _BRIEF_READY = ok
    return ok


def brief_select_sql(cursor, alias: str = "camp") -> str:
    global _BRIEF_READY
    if not _BRIEF_READY and public_column_exists(cursor, "brand_pr_campaigns", "brief"):
        _BRIEF_READY = True
    return f"{alias}.brief" if _BRIEF_READY else "NULL::jsonb AS brief"


def clean_brief(raw: Any) -> tuple:
    """Returns (brief, error). Empty fields are dropped; an all-empty brief clears it."""
    if not isinstance(raw, dict):
        return None, "Brief must be an object"
    out = {}
    for key, limit in BRIEF_FIELDS.items():
        text = str(raw.get(key) or "").replace("\r", "").strip()
        if len(text) > limit:
            return None, f"Keep {key.replace('_', ' ')} under {limit} characters"
        if text:
            out[key] = text
    ref = str(raw.get("reference_url") or "").strip()
    if ref:
        url = clean_content_url(ref)
        if not url:
            return None, "Reference link must be a full http(s) URL"
        out["reference_url"] = url
    if out:
        out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out, None


def brief_public(raw: Any) -> Optional[dict]:
    data = parse_delivered(raw)
    keys = list(BRIEF_FIELDS) + ["reference_url"]
    if not any(data.get(k) for k in keys):
        return None
    return {k: data.get(k) or "" for k in keys + ["updated_at"]}


def delivery_select_sql(cursor, alias: str = "a") -> str:
    """Delivery columns for a SELECT list; NULLs until the migration has landed.
    Never ALTERs: the roster GET is a hot path."""
    global _READY
    if not _READY and public_column_exists(cursor, "brand_pr_applications", "picked_notified_at"):
        _READY = True
    if _READY:
        return f"{alias}.delivered_content, {alias}.delivered_at"
    return "NULL::jsonb AS delivered_content, NULL::timestamptz AS delivered_at"


def clean_content_url(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text or len(text) > MAX_URL_LEN:
        return None
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or "." not in (parsed.netloc or ""):
        return None
    return text


def parse_delivered(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def delivered_public(row: dict) -> Optional[dict]:
    content = parse_delivered(row.get("delivered_content"))
    if not content.get("organic_url") and not content.get("ugc_url"):
        return None
    at = row.get("delivered_at")
    return {
        "organic_url": content.get("organic_url") or "",
        "ugc_url": content.get("ugc_url") or "",
        "note": content.get("note") or "",
        "delivered_at": at.isoformat() if hasattr(at, "isoformat") else at,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _p(text: str) -> str:
    return f'<p style="margin: 0 0 16px;">{text}</p>'


def _steps(items: list) -> str:
    lis = "".join(f'<li style="margin: 0 0 10px;">{item}</li>' for item in items)
    return f'<ol style="margin: 0 0 16px; padding-left: 20px;">{lis}</ol>'


def _wrap(subject: str, preheader: str, inner: str, action_url: str, action_text: str,
          footer_note: str = None) -> str:
    html = _jinja_env().get_template("welcome_email.html").render(
        subject=subject,
        preheader=preheader,
        message=Markup(inner),
        action_url=action_url,
        action_text=action_text,
        unsubscribe_url=None,
        footer_note=footer_note,
    )
    return html.replace("\u2014", "-")


def _link(base_path: str, campaign: str, medium: str, extra: dict = None) -> str:
    query = {"utm_source": "email", "utm_campaign": campaign, "utm_medium": medium}
    query.update(extra or {})
    return f"{frontend_base().rstrip('/')}{base_path}?{urlencode(query)}"


def _first_name(raw: Any) -> str:
    first = str(raw or "").strip().split(" ")[0]
    return first if first and first.lower() not in ("none", "null") else "there"


def _brand_tag(row: dict) -> str:
    handle = str(row.get("instagram_handle") or row.get("tiktok_handle") or "").strip().lstrip("@")
    if handle and "/" not in handle and " " not in handle:
        return f"@{handle}"
    return safe_brand_name(row.get("brand_name"))


def _product(row: dict) -> str:
    return str(row.get("sku_note") or row.get("hero_product") or "").strip()[:120]


def _ship_city(addr: Any) -> str:
    data = parse_delivered(addr)
    return ", ".join(p for p in (str(data.get("city") or "").strip(), str(data.get("country") or "").strip()) if p)


def _deliverables(row: dict) -> str:
    tag = escape(_brand_tag(row))
    return (
        f"<b>1 organic post</b> on your TikTok or Instagram tagging {tag}, and "
        "<b>1 raw UGC video</b> (no music or captions) they can reuse in ads for 6 months."
    )


def _brief_teaser(row: dict) -> str:
    brief = brief_public(row.get("brief"))
    if not brief or not brief.get("message"):
        return ""
    return (
        '<p style="margin: 0 0 16px; padding: 12px 14px; background: #f5f5f7; border-radius: 10px; '
        'font-size: 15px; line-height: 1.5; color: #1d1d1f;">'
        f'<span style="color: #6e6e73;">Their brief in one line:</span><br>{escape(brief["message"])}</p>'
    )


def _variant_ask(row: dict) -> str:
    brief = brief_public(row.get("brief"))
    question = (brief or {}).get("variant_question")
    if not question:
        return ""
    return (
        '<p style="margin: 0 0 16px; padding: 12px 14px; background: #fff7ed; border-radius: 10px; '
        'font-size: 15px; line-height: 1.5; color: #1d1d1f;">'
        f'<b>{escape(safe_brand_name(row.get("brand_name")))} asks:</b> {escape(question)}<br>'
        '<span style="color: #6e6e73;">Answer on Newcollab so they send the right one.</span></p>'
    )


def can_unship(shipped_at: Any, now: Optional[datetime] = None) -> bool:
    if not isinstance(shipped_at, datetime):
        return False
    shipped = shipped_at if shipped_at.tzinfo else shipped_at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - shipped <= timedelta(hours=UNSHIP_WINDOW_HOURS)


def save_variant_answer(cursor, *, creator_id: int, application_id: int, answer: Any) -> tuple:
    """Picked creator answers the brand's size / shade / variant question. Returns (answer, error)."""
    text = " ".join(str(answer or "").split())[:MAX_VARIANT_LEN]
    if not text:
        return None, "Add your answer"
    cursor.execute(
        """
        UPDATE brand_pr_applications SET variant_answer = %s, variant_answered_at = NOW(), updated_at = NOW()
        WHERE id = %s AND creator_id = %s AND status IN ('ships', 'posted')
        RETURNING id
        """,
        (text, application_id, creator_id),
    )
    if not cursor.fetchone():
        return None, "Application not found"
    return text, None


def unship(cursor, campaign: dict, application_id: Optional[int] = None,
           now: Optional[datetime] = None) -> tuple:
    """Undo a mistaken "shipped" within UNSHIP_WINDOW_HOURS. Returns (rows_to_correct, error).

    Reverts the creator(s), the campaign status and, when this campaign was the brand's free one, the
    free-campaign use. rows_to_correct are creators who already got the "on its way" email."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=UNSHIP_WINDOW_HOURS)
    cursor.execute(
        """
        SELECT a.id, a.shipped_notified_at, u.email, u.first_name
        FROM brand_pr_applications a
        JOIN creators cr ON cr.id = a.creator_id
        JOIN users u ON u.id = cr.user_id
        WHERE a.brand_id = %s AND a.campaign_id = %s AND a.status = 'ships'
          AND a.shipped_at IS NOT NULL AND a.shipped_at >= %s
          AND (%s::int IS NULL OR a.id = %s::int)
        """,
        (campaign["brand_id"], campaign["id"], cutoff, application_id, application_id),
    )
    rows = [dict(r) for r in cursor.fetchall() or []]
    if not rows:
        return None, f"Only shipments marked in the last {UNSHIP_WINDOW_HOURS} hours can be undone"
    ids = [r["id"] for r in rows]
    cursor.execute(
        """UPDATE brand_pr_applications SET shipped_at = NULL, shipped_notified_at = NULL, updated_at = NOW()
           WHERE id = ANY(%s)""",
        (ids,),
    )
    if campaign.get("status") == "shipped":
        cursor.execute(
            "UPDATE brand_pr_campaigns SET status = 'locked', shipped_at = NULL, updated_at = NOW() WHERE id = %s",
            (campaign["id"],),
        )
        cursor.execute(
            "SELECT 1 FROM brand_pr_campaigns WHERE brand_id = %s AND id <> %s AND shipped_at IS NOT NULL LIMIT 1",
            (campaign["brand_id"], campaign["id"]),
        )
        if not cursor.fetchone():  # this was their free campaign: it counts again when they really ship
            cursor.execute(
                """UPDATE brand_billing SET status = 'none', free_campaigns_used = 0, updated_at = NOW()
                   WHERE brand_id = %s AND status = 'trial_used' AND stripe_subscription_id IS NULL""",
                (campaign["brand_id"],),
            )
    return [r for r in rows if r.get("shipped_notified_at") and r.get("email")], None


def build_creator_unshipped_email(row: dict) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    subject = f"Update on your {brand} box"
    inner = "".join([
        _p(f"Hi {escape(_first_name(row.get('first_name')))},"),
        _p(f"Quick correction: {escape(brand)} marked your PR box as shipped by mistake. It hasn't left yet."),
        _variant_ask(row),
        _p("You'll get an email from us the moment it's really on its way. Nothing to do until then."),
    ])
    url = _link("/creator/dashboard/pr-brands", "roster_creator", "unshipped", {"picked": row["id"]})
    return {"subject": subject, "html": _wrap(subject, f"Your {brand} box hasn't shipped yet.", inner, url,
                                              "See my PR box"), "url": url}


def notify_unshipped_async(rows: list, brand_name: str, brief: Any) -> None:
    def run():
        for row in rows or []:
            try:
                payload = build_creator_unshipped_email({**row, "brand_name": brand_name, "brief": brief})
                res = _default_send(row["email"], payload["subject"], payload["html"], "roster_creator_unshipped")
                if not res.get("success"):
                    print(f"[roster-delivery] unshipped email failed app={row['id']}: {res.get('error')}")
            except Exception:
                traceback.print_exc()

    threading.Thread(target=run, daemon=True).start()


def build_creator_brief_added_email(row: dict) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    subject = f"{brand} added a brief for your post"
    inner = "".join([
        _p(f"Hi {escape(_first_name(row.get('first_name')))},"),
        _p(f"{escape(brand)} just shared what they'd love to see in your content."),
        _brief_teaser(row),
        _variant_ask(row),
        _p("Take a minute to read it before you film. It's the easiest way to get picked again."),
    ])
    url = _link("/creator/dashboard/pr-brands", "roster_creator", "brief", {"picked": row["id"]})
    return {
        "subject": subject,
        "html": _wrap(subject, f"{brand} shared their brief for your PR post.", inner, url, "See my brief"),
        "url": url,
    }


def notify_brief_added_async(campaign_id: int) -> None:
    """One email per picked creator when a brand adds a brief after locking."""

    def run():
        from pr_crm_routes import get_db_connection

        try:
            conn = get_db_connection()
        except Exception:
            traceback.print_exc()
            return
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                SELECT a.id, b.brand_name, camp.brief, u.email, u.first_name
                FROM brand_pr_applications a
                JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
                JOIN pr_brands b ON b.id = a.brand_id
                JOIN creators cr ON cr.id = a.creator_id
                JOIN users u ON u.id = cr.user_id
                WHERE camp.id = %s AND a.status = 'ships'
                  AND COALESCE(TRIM(u.email), '') <> ''
                """,
                (campaign_id,),
            )
            for row in cursor.fetchall() or []:
                payload = build_creator_brief_added_email(dict(row))
                res = _default_send(row["email"], payload["subject"], payload["html"], "roster_creator_brief")
                if not res.get("success"):
                    print(f"[roster-delivery] brief email failed app={row['id']}: {res.get('error')}")
        except Exception:
            traceback.print_exc()
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()


def build_creator_picked_email(row: dict, *, already_shipped: bool = False) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    product = _product(row)
    city = _ship_city(row.get("shipping_address"))
    gift = f"<b>{escape(product)}</b>" if product else "a gifted PR package"
    subject = f"{brand} picked you for a gifted PR package"
    ship_line = (
        f"{escape(brand)} has already shipped your box"
        + (f" to {escape(city)}." if city else ".")
        if already_shipped
        else f"{escape(brand)} ships to the address you gave"
        + (f" ({escape(city)})." if city else ".")
        + " Wrong address? Reply to this email today."
    )
    inner = "".join([
        _p(f"Hi {escape(_first_name(row.get('first_name')))},"),
        _p(f"Good news: {escape(brand)} picked you from their creator roster. They're sending you {gift} for free."),
        _brief_teaser(row),
        _variant_ask(row),
        _p("<b>What happens next</b>"),
        _steps([
            ship_line,
            f"When it arrives, create {_deliverables(row)}",
            f"Submit both links on Newcollab within {CONTENT_DUE_DAYS} days of the box arriving. "
            f"{escape(brand)} sees them straight away.",
        ]),
        _p("Brands see who delivered on time. It's how you get picked again."),
    ])
    url = _link("/creator/dashboard/pr-brands", "roster_creator", "picked", {"picked": row["id"]})
    return {
        "subject": subject,
        "html": _wrap(subject, f"{brand} is sending you a PR box. Here's your brief.", inner, url, "See my brief"),
        "url": url,
    }


def build_creator_shipped_email(row: dict) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    subject = f"Your {brand} box is on its way"
    inner = "".join([
        _p(f"Hi {escape(_first_name(row.get('first_name')))},"),
        _p(f"{escape(brand)} just marked your PR package as shipped."),
        _p(f"Once it arrives, create {_deliverables(row)}"),
        _p(f"Then submit both links on Newcollab within {CONTENT_DUE_DAYS} days. "
           "A Google Drive or Dropbox link works for the raw video."),
    ])
    url = _link("/creator/dashboard/pr-brands", "roster_creator", "shipped", {"picked": row["id"]})
    return {
        "subject": subject,
        "html": _wrap(subject, f"Your {brand} PR box shipped. Post within {CONTENT_DUE_DAYS} days of delivery.", inner, url, "Submit my content"),
        "url": url,
    }


def build_ship_reminder_email(row: dict, reminder: int) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    picks = int(row.get("pick_count") or 0)
    who = "creator" if picks == 1 else f"{picks} creators"
    locked = row.get("locked_at")
    when = f"{locked:%b} {locked.day}" if hasattr(locked, "day") else "last week"
    if reminder == 1:
        subject = f"Your {brand} creator is ready to ship" if picks == 1 else f"Your {who} for {brand} are ready to ship"
        body = [
            _p(f"Hi {escape(greeting_for_brand(row))},"),
            _p(f"You locked your {escape(brand)} roster on {escape(when)} and {who} "
               f"{'is' if picks == 1 else 'are'} still waiting on a box. Their addresses are ready."),
            _steps([
                "Download the shipping CSV and import it into Shopify or ShipStation.",
                "Mark each creator shipped as their box goes out (or all at once).",
                "We brief each creator and collect their content in your inbox.",
            ]),
            _p("Each creator posts 1 organic video tagging you and sends 1 raw UGC video you can reuse in ads for 6 months."),
        ]
    else:
        subject = f"Still shipping to your {brand} creators?"
        body = [
            _p(f"Hi {escape(greeting_for_brand(row))},"),
            _p(f"Your {who} are still waiting on their {escape(brand)} box. They've agreed to post and send UGC as soon as it arrives."),
            _p("If something's in the way (stock, a different product, a question about the creators), just reply and I'll sort it."),
        ]
    inner = "".join(body)
    url = _link(f"/r/{row['token']}", "roster_delivery", f"ship_reminder_{reminder}", {"step": "ship"})
    return {
        "subject": subject,
        "html": _wrap(subject, f"Addresses for your {brand} creators are ready to export.", inner, url,
                      "Open shipping list", BRAND_FOOTER_NOTE),
        "url": url,
        "trigger_number": SHIP_REMINDERS[reminder - 1][1],
        "email_type": SHIP_REMINDERS[reminder - 1][2],
        "applicant_count": picks,
    }


def build_content_ready_email(row: dict) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    creator = str(row.get("creator_name") or "A creator").strip()[:60]
    content = parse_delivered(row.get("delivered_content"))
    delivered = int(row.get("delivered_count") or 1)
    picks = max(int(row.get("pick_count") or 1), delivered)
    waiting = int(row.get("waiting_applicants") or 0)
    subject = f"{creator} delivered their {brand} content"
    links = []
    if content.get("organic_url"):
        links.append(f'<a href="{escape(content["organic_url"])}" style="color:#1d1d1f;">Organic post</a>')
    if content.get("ugc_url"):
        links.append(f'<a href="{escape(content["ugc_url"])}" style="color:#1d1d1f;">Raw UGC video</a>')
    body = [
        _p(f"Hi {escape(greeting_for_brand(row))},"),
        _p(f"{escape(creator)} just delivered their content for {escape(brand)}: {' · '.join(links)}."),
        _p(f"That's {delivered} of {picks} creators delivered. You can reuse the UGC in ads for 6 months."),
    ]
    if not row.get("subscribed"):
        more = (
            f"{waiting} more creators have already applied to {escape(brand)}. "
            if waiting > 0 else ""
        )
        body.append(_p(
            f"{more}Keep a fresh batch of 5 creators coming every month for {PLAN_PRICE_LABEL}. "
            "Product and shipping only, cancel anytime."
        ))
    url = _link(f"/r/{row['token']}", "roster_delivery", "content_ready", {"step": "content"})
    return {
        "subject": subject,
        "html": _wrap(subject, f"New UGC for {brand} is in your inbox.", "".join(body), url,
                      "See the content", BRAND_FOOTER_NOTE),
        "url": url,
        "trigger_number": CONTENT_READY_TRIGGER,
        "email_type": CONTENT_READY_TYPE,
        "applicant_count": waiting,
    }


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _default_send(to_email: str, subject: str, html: str, kind: str) -> dict:
    from services.resend_mail import resend_configured, send_resend_email

    if not resend_configured():
        return {"success": False, "message_id": None, "error": "RESEND_API_KEY not set"}
    return send_resend_email(to_email, subject, html, tags=[{"name": "type", "value": kind}])


def _log_brand_email(cursor, row: dict, payload: dict, send_res: dict) -> None:
    cursor.execute(
        """
        INSERT INTO brand_emails (
            brand_id, campaign_id, trigger_number, email_type, to_email,
            subject, subject_variant, applicant_count, roster_link,
            message_id, status, skip_reason, sent_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 'A', %s, %s, %s, %s, %s, NOW())
        """,
        (
            row["brand_id"],
            row.get("campaign_id"),
            payload["trigger_number"],
            payload["email_type"],
            row["contact_email"],
            payload["subject"],
            int(payload.get("applicant_count") or 0),
            payload["url"],
            send_res.get("message_id"),
            "sent" if send_res.get("success") else "failed",
            None if send_res.get("success") else (send_res.get("error") or "send_failed"),
        ),
    )


_CREATOR_NOTICE_SQL = """
    SELECT a.id, a.creator_id, a.brand_id, a.shipping_address,
           a.picked_notified_at, a.shipped_notified_at,
           camp.id AS campaign_id, camp.status AS campaign_status,
           a.shipped_at AS creator_shipped_at, camp.sku_note, {brief},
           b.brand_name, b.hero_product, b.instagram_handle, b.tiktok_handle,
           u.email, u.first_name
    FROM brand_pr_applications a
    JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
    JOIN pr_brands b ON b.id = a.brand_id
    JOIN creators cr ON cr.id = a.creator_id
    JOIN users u ON u.id = cr.user_id
    WHERE a.status = 'ships'
      AND camp.status IN ('locked', 'shipped')
      AND camp.locked_at > NOW() - make_interval(days => %s)
      AND COALESCE(TRIM(u.email), '') <> ''
      AND (
        a.picked_notified_at IS NULL
        OR (a.shipped_at IS NOT NULL AND a.shipped_notified_at IS NULL)
      )
"""


def _claim(cursor, app_id: int, column: str, extra_column: Optional[str] = None) -> bool:
    sets = f"{column} = NOW()" + (f", {extra_column} = NOW()" if extra_column else "")
    cursor.execute(
        f"UPDATE brand_pr_applications SET {sets} WHERE id = %s AND {column} IS NULL RETURNING id",
        (app_id,),
    )
    return cursor.fetchone() is not None


def _release(cursor, app_id: int, column: str, extra_column: Optional[str] = None) -> None:
    sets = f"{column} = NULL" + (f", {extra_column} = NULL" if extra_column else "")
    cursor.execute(f"UPDATE brand_pr_applications SET {sets} WHERE id = %s", (app_id,))


def send_creator_notices(
    cursor,
    conn,
    *,
    campaign_id: int = None,
    dry_run: bool = False,
    send_fn: Callable = None,
) -> dict:
    """Picked / shipped emails to creators. Each send is claimed first so cron and
    the inline hook never double-send."""
    send = send_fn or _default_send
    sql = _CREATOR_NOTICE_SQL.format(brief=brief_select_sql(cursor))
    params: list = [CREATOR_NOTICE_WINDOW_DAYS]
    if campaign_id:
        sql += " AND camp.id = %s"
        params.append(int(campaign_id))
    cursor.execute(sql + " ORDER BY a.id", params)
    rows = [dict(r) for r in cursor.fetchall() or []]
    stats = {"picked": 0, "shipped": 0, "failed": 0, "candidates": len(rows)}
    if dry_run:
        stats["preview"] = [
            {"application_id": r["id"], "brand_id": r["brand_id"],
             "kind": "picked" if r["picked_notified_at"] is None else "shipped"}
            for r in rows
        ]
        return stats
    for row in rows:
        shipped = row.get("creator_shipped_at") is not None
        if row["picked_notified_at"] is None:
            column, extra = "picked_notified_at", ("shipped_notified_at" if shipped else None)
            payload, kind = build_creator_picked_email(row, already_shipped=shipped), "picked"
        else:
            column, extra = "shipped_notified_at", None
            payload, kind = build_creator_shipped_email(row), "shipped"
        if not _claim(cursor, row["id"], column, extra):
            continue
        conn.commit()
        res = send(row["email"], payload["subject"], payload["html"], f"roster_creator_{kind}")
        if res.get("success"):
            stats[kind] += 1
        else:
            stats["failed"] += 1
            _release(cursor, row["id"], column, extra)
            conn.commit()
            print(f"[roster-delivery] creator {kind} email failed app={row['id']}: {res.get('error')}")
    return stats


def notify_creators_async(campaign_id: int) -> None:
    """Fire-and-forget from the lock / ship routes; cron retries anything missed."""

    def run():
        from pr_crm_routes import get_db_connection

        try:
            conn = get_db_connection()
        except Exception:
            traceback.print_exc()
            return
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            if ensure_delivery_columns(cursor, conn):
                send_creator_notices(cursor, conn, campaign_id=campaign_id)
            conn.commit()
        except Exception:
            conn.rollback()
            traceback.print_exc()
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()


def _brand_first_name_sql(cursor) -> str:
    if public_column_exists(cursor, "pr_brands", "contact_first_name"):
        return "b.contact_first_name"
    return "NULL::text AS contact_first_name"


def send_ship_reminders(cursor, conn, *, dry_run: bool = False, send_fn: Callable = None) -> dict:
    send = send_fn or _default_send
    ensure_brand_emails_table(cursor, conn)
    cursor.execute(
        f"""
        SELECT camp.id AS campaign_id, camp.brand_id, camp.token, camp.locked_at,
               b.brand_name, b.contact_email, {_brand_first_name_sql(cursor)},
               (SELECT COUNT(*) FROM brand_pr_applications a
                 WHERE a.campaign_id = camp.id AND a.status = 'ships' AND a.shipped_at IS NULL) AS pick_count,
               (SELECT MAX(e.sent_at) FROM brand_emails e
                 WHERE e.campaign_id = camp.id AND e.trigger_number = %s AND e.status = 'sent') AS r1_at,
               (SELECT MAX(e.sent_at) FROM brand_emails e
                 WHERE e.campaign_id = camp.id AND e.trigger_number = %s AND e.status = 'sent') AS r2_at,
               EXTRACT(EPOCH FROM (NOW() - camp.locked_at)) / 86400.0 AS age_days
        FROM brand_pr_campaigns camp
        JOIN pr_brands b ON b.id = camp.brand_id
        WHERE camp.status = 'locked'
          AND camp.locked_at < NOW() - make_interval(days => %s)
          AND camp.locked_at > NOW() - make_interval(days => %s)
          AND COALESCE(TRIM(b.contact_email), '') <> ''
        ORDER BY camp.locked_at
        """,
        (SHIP_REMINDERS[0][1], SHIP_REMINDERS[1][1], SHIP_REMINDERS[0][0], SHIP_REMINDER_MAX_AGE_DAYS),
    )
    rows = [dict(r) for r in cursor.fetchall() or []]
    stats = {"sent": 0, "failed": 0, "candidates": 0, "preview": []}
    for row in rows:
        reminder = pick_ship_reminder(row)
        if not reminder or int(row.get("pick_count") or 0) < 1:
            continue
        stats["candidates"] += 1
        payload = build_ship_reminder_email(row, reminder)
        if dry_run:
            stats["preview"].append({"campaign_id": row["campaign_id"], "reminder": reminder})
            continue
        res = send(row["contact_email"], payload["subject"], payload["html"], payload["email_type"])
        _log_brand_email(cursor, row, payload, res)
        conn.commit()
        stats["sent" if res.get("success") else "failed"] += 1
    return stats


def build_lock_reminder_email(row: dict, reminder: int) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    picks = int(row.get("pick_count") or 0)
    who = "1 creator" if picks == 1 else f"{picks} creators"
    hi = _p(f"Hi {escape(greeting_for_brand(row))},")
    if reminder == 1:
        subject = f"Your {brand} picks are saved. Lock them to get addresses"
        body = [
            hi,
            _p(f"You picked {who} for {escape(brand)}. They're saved, but nobody hears back until you lock."),
            _steps([
                "Open your roster and tap Lock (you can lock with fewer than 5).",
                "Shipping addresses unlock right away, ready for a CSV export.",
                "We tell each creator they're in and send them your brief.",
            ]),
        ]
    else:
        subject = f"{who.capitalize()} {'is' if picks == 1 else 'are'} waiting to hear from {brand}"
        body = [
            hi,
            _p(f"The {who} you picked for {escape(brand)} applied and are waiting for a yes. "
               "Lock your roster and we'll let them know and collect their content for you."),
            _p("Not sure about someone? Swap them before you lock, or reply and I'll help you choose."),
        ]
    url = _link(f"/r/{row['token']}", "roster_delivery", f"lock_reminder_{reminder}", {})
    return {
        "subject": subject,
        "html": _wrap(subject, f"Your {brand} picks are one tap from shipping.", "".join(body), url,
                      "Lock my picks", BRAND_FOOTER_NOTE),
        "url": url,
        "trigger_number": LOCK_REMINDERS[reminder - 1][1],
        "email_type": LOCK_REMINDERS[reminder - 1][2],
        "applicant_count": picks,
    }


def send_lock_reminders(cursor, conn, *, dry_run: bool = False, send_fn: Callable = None) -> dict:
    """Brands that picked creators but never locked: nudge at day 1 and day 3 after their last pick."""
    send = send_fn or _default_send
    ensure_brand_emails_table(cursor, conn)
    cursor.execute(
        f"""
        SELECT * FROM (
            SELECT camp.id AS campaign_id, camp.brand_id, camp.token,
                   b.brand_name, b.contact_email, {_brand_first_name_sql(cursor)},
                   jsonb_array_length(COALESCE(camp.selected_application_ids, '[]'::jsonb)) AS pick_count,
                   (SELECT MAX(e.created_at) FROM brand_pr_events e
                     WHERE e.brand_id = camp.brand_id AND e.event = 'roster_select') AS last_pick_at,
                   (SELECT MAX(e.sent_at) FROM brand_emails e
                     WHERE e.campaign_id = camp.id AND e.trigger_number = %s AND e.status = 'sent') AS r1_at,
                   (SELECT MAX(e.sent_at) FROM brand_emails e
                     WHERE e.campaign_id = camp.id AND e.trigger_number = %s AND e.status = 'sent') AS r2_at
            FROM brand_pr_campaigns camp
            JOIN pr_brands b ON b.id = camp.brand_id
            WHERE camp.status = 'active'
              AND jsonb_array_length(COALESCE(camp.selected_application_ids, '[]'::jsonb)) > 0
              AND COALESCE(TRIM(b.contact_email), '') <> ''
        ) q
        WHERE q.last_pick_at < NOW() - make_interval(days => %s)
          AND q.last_pick_at > NOW() - make_interval(days => %s)
        ORDER BY q.last_pick_at
        """,
        (LOCK_REMINDERS[0][1], LOCK_REMINDERS[1][1], LOCK_REMINDERS[0][0], LOCK_REMINDER_MAX_AGE_DAYS),
    )
    rows = [dict(r) for r in cursor.fetchall() or []]
    stats = {"sent": 0, "failed": 0, "preview": []}
    for row in rows:
        last = row["last_pick_at"]
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        row["age_days"] = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        reminder = pick_ship_reminder(row, LOCK_REMINDERS)
        if not reminder:
            continue
        payload = build_lock_reminder_email(row, reminder)
        if dry_run:
            stats["preview"].append({"campaign_id": row["campaign_id"], "reminder": reminder})
            continue
        res = send(row["contact_email"], payload["subject"], payload["html"], payload["email_type"])
        _log_brand_email(cursor, row, payload, res)
        conn.commit()
        stats["sent" if res.get("success") else "failed"] += 1
    return stats


def pick_ship_reminder(row: dict, schedule=SHIP_REMINDERS) -> Optional[int]:
    """Same two-step cadence for ship and lock reminders: second one waits 2 days after the first."""
    age = float(row.get("age_days") or 0)
    if row.get("r1_at") is None:
        return 1 if age >= schedule[0][0] else None
    if row.get("r2_at") is None and age >= schedule[1][0]:
        r1 = row["r1_at"]
        if r1.tzinfo is None:
            r1 = r1.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - r1).total_seconds() >= 2 * 86400:
            return 2
    return None


# ---------------------------------------------------------------------------
# Creator delivery
# ---------------------------------------------------------------------------


def deliver_content(cursor, *, creator_id: int, application_id: int, organic_url: Any, ugc_url: Any, note: Any):
    """Returns (result, error). Caller commits, then calls notify_brand_content_ready."""
    organic = clean_content_url(organic_url) if organic_url else None
    ugc = clean_content_url(ugc_url) if ugc_url else None
    if (organic_url and not organic) or (ugc_url and not ugc):
        return None, "Links must be full http(s) URLs."
    if not organic and not ugc:
        return None, "Add your organic post link or your UGC video link."
    cursor.execute(
        """
        SELECT a.id, a.brand_id, a.status, a.campaign_id, a.delivered_at,
               camp.status AS campaign_status
        FROM brand_pr_applications a
        LEFT JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
        WHERE a.id = %s AND a.creator_id = %s
        FOR UPDATE OF a
        """,
        (application_id, creator_id),
    )
    app = cursor.fetchone()
    if not app:
        return None, "Application not found"
    if app["status"] not in ("ships", "posted"):
        return None, "You can submit content once the brand picks you."
    content = {
        "organic_url": organic or "",
        "ugc_url": ugc or "",
        "note": str(note or "").strip()[:MAX_NOTE_LEN],
    }
    first = app.get("delivered_at") is None
    cursor.execute(
        """
        UPDATE brand_pr_applications
        SET status = 'posted', delivered_content = %s,
            delivered_at = COALESCE(delivered_at, NOW()),
            shipped_at = COALESCE(shipped_at, NOW()), updated_at = NOW()
        WHERE id = %s
        """,
        (Json(content), app["id"]),
    )
    if app.get("campaign_id") and app.get("campaign_status") == "locked":
        cursor.execute(
            """
            UPDATE brand_pr_campaigns
            SET status = 'shipped', shipped_at = COALESCE(shipped_at, NOW()), updated_at = NOW()
            WHERE id = %s AND status = 'locked'
            """,
            (app["campaign_id"],),
        )
        from services.brand_billing import mark_free_campaign_consumed

        mark_free_campaign_consumed(cursor, app["brand_id"])
    cursor.execute(
        """
        INSERT INTO brand_pr_events (creator_id, brand_id, event, source, meta)
        VALUES (%s, %s, 'roster_content_delivered', 'creator', %s)
        """,
        (creator_id, app["brand_id"], Json({"application_id": app["id"], "campaign_id": app.get("campaign_id"), "first": first})),
    )
    return {"application_id": app["id"], "brand_id": app["brand_id"], "campaign_id": app.get("campaign_id"),
            "first": first, "content": content}, None


def notify_brand_content_ready(cursor, conn, application_id: int, *, send_fn: Callable = None) -> dict:
    send = send_fn or _default_send
    ensure_brand_emails_table(cursor, conn)
    cursor.execute(
        f"""
        SELECT a.id, a.brand_id, a.campaign_id, a.delivered_content,
               COALESCE(NULLIF(TRIM(u.first_name), ''), cr.username, 'A creator') AS creator_name,
               camp.token, b.brand_name, b.contact_email, {_brand_first_name_sql(cursor)},
               COALESCE(bb.status = 'active', FALSE) AS subscribed,
               (SELECT COUNT(*) FROM brand_pr_applications x
                 WHERE x.campaign_id = a.campaign_id AND x.status IN ('ships', 'posted')) AS pick_count,
               (SELECT COUNT(*) FROM brand_pr_applications x
                 WHERE x.campaign_id = a.campaign_id AND x.delivered_at IS NOT NULL) AS delivered_count,
               (SELECT COUNT(*) FROM brand_pr_applications x
                 WHERE x.brand_id = a.brand_id AND x.status = 'review') AS waiting_applicants
        FROM brand_pr_applications a
        JOIN creators cr ON cr.id = a.creator_id
        JOIN users u ON u.id = cr.user_id
        JOIN pr_brands b ON b.id = a.brand_id
        LEFT JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
        LEFT JOIN brand_billing bb ON bb.brand_id = a.brand_id
        WHERE a.id = %s
        """,
        (application_id,),
    )
    row = cursor.fetchone()
    if not row or not row.get("token") or not str(row.get("contact_email") or "").strip():
        return {"sent": False, "reason": "no_brand_contact"}
    row = dict(row)
    payload = build_content_ready_email(row)
    res = send(row["contact_email"], payload["subject"], payload["html"], CONTENT_READY_TYPE)
    _log_brand_email(cursor, row, payload, res)
    conn.commit()
    return {"sent": bool(res.get("success"))}


def content_due_at(shipped_at: Any) -> Optional[datetime]:
    if not hasattr(shipped_at, "tzinfo"):
        return None
    at = shipped_at if shipped_at.tzinfo else shipped_at.replace(tzinfo=timezone.utc)
    return at + timedelta(days=CONTENT_DUE_DAYS)


def content_reminder_due(age_days: float, last_sent: int) -> Optional[int]:
    """Next reminder number to send, or None. Skips straight to the latest one that's due."""
    due = [n for days, n in CONTENT_REMINDERS if age_days >= days]
    if not due or due[-1] <= int(last_sent or 0):
        return None
    return due[-1]


def overdue_delivery(cursor, creator_id: int) -> Optional[dict]:
    """The creator's oldest picked-and-shipped PR with no content past the overdue line."""
    if not public_column_exists(cursor, "brand_pr_applications", "delivered_at"):
        return None
    cursor.execute(
        """
        SELECT a.id AS application_id, b.brand_name
        FROM brand_pr_applications a
        JOIN pr_brands b ON b.id = a.brand_id
        WHERE a.creator_id = %s AND a.status = 'ships' AND a.delivered_at IS NULL
          AND a.shipped_at < NOW() - make_interval(days => %s)
        ORDER BY a.shipped_at
        LIMIT 1
        """,
        (creator_id, CONTENT_OVERDUE_DAYS),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def build_content_reminder_email(row: dict, reminder: int) -> dict:
    brand = safe_brand_name(row.get("brand_name"))
    due = content_due_at(row.get("shipped_at"))
    due_label = f"{due:%b} {due.day}" if due else f"{CONTENT_DUE_DAYS} days after delivery"
    hi = _p(f"Hi {escape(_first_name(row.get('first_name')))},")
    if reminder == 1:
        subject = f"Your {brand} content is due {due_label}"
        body = [
            hi,
            _p(f"{escape(brand)} shipped your PR box. Once it arrives, create {_deliverables(row)}"),
            _brief_teaser(row),
            _p(f"Submit both links on Newcollab by <b>{escape(due_label)}</b>. {escape(brand)} sees them straight away."),
        ]
    elif reminder == 2:
        subject = f"{brand} is waiting on your post"
        body = [
            hi,
            _p(f"Your {escape(brand)} content is due today. {escape(brand)} gifted you the product "
               "and is checking their content inbox."),
            _p(f"Still need to film? Create {_deliverables(row)} Then submit the links, even if it's a day late."),
        ]
    else:
        subject = f"Your {brand} post is overdue, so new PR applications are paused"
        body = [
            hi,
            _p(f"We haven't received your {escape(brand)} content yet, and it was due {escape(due_label)}."),
            _p("Until you submit it, you can't apply for new gifted PR on Newcollab. "
               "Brands pick creators who deliver, and late posts cost everyone future boxes."),
            _p("Submit your links and applications reopen right away. Box never arrived? Reply to this email."),
        ]
    url = _link("/creator/dashboard/pr-brands", "roster_creator", f"content_due_{reminder}", {"picked": row["id"]})
    return {"subject": subject, "html": _wrap(subject, subject, "".join(body), url, "Submit my content"), "url": url}


def content_reminder_note(row: dict, reminder: int) -> str:
    brand = safe_brand_name(row.get("brand_name"))
    if reminder == 3:
        return (f"⚠️ Your **{brand}** post is overdue, so new gifted PR applications are paused. "
                "Submit your links in **PR brands** and they reopen right away.")
    due = content_due_at(row.get("shipped_at"))
    when = f"by **{due:%b} {due.day}**" if due else "soon"
    return (f"📦 **{brand}** shipped your PR box. Your organic post and raw UGC video are due {when}. "
            "Submit both links in **PR brands** when they're live.")


def send_content_reminders(cursor, conn, *, dry_run: bool = False, send_fn: Callable = None,
                           note_fn: Callable = None) -> dict:
    """Day 5, due day, and overdue: email + Polly note to creators who haven't delivered."""
    send = send_fn or _default_send
    cursor.execute(
        f"""
        SELECT a.id, a.creator_id, a.brand_id, a.shipped_at, camp.sku_note, {brief_select_sql(cursor)},
               b.brand_name, b.hero_product, b.instagram_handle, b.tiktok_handle,
               u.email, u.first_name,
               EXTRACT(EPOCH FROM (NOW() - a.shipped_at)) / 86400.0 AS age_days,
               (SELECT MAX((e.meta->>'n')::int) FROM brand_pr_events e
                 WHERE e.event = %s AND e.meta->>'application_id' = a.id::text) AS last_sent
        FROM brand_pr_applications a
        JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
        JOIN pr_brands b ON b.id = a.brand_id
        JOIN creators cr ON cr.id = a.creator_id
        JOIN users u ON u.id = cr.user_id
        WHERE a.status = 'ships' AND a.delivered_at IS NULL AND a.shipped_at IS NOT NULL
          AND a.shipped_at > NOW() - make_interval(days => %s)
          AND COALESCE(TRIM(u.email), '') <> ''
        ORDER BY a.shipped_at
        """,
        (CONTENT_REMINDER_EVENT, CONTENT_REMINDER_WINDOW_DAYS),
    )
    rows = [dict(r) for r in cursor.fetchall() or []]
    stats = {"sent": 0, "failed": 0, "preview": []}
    for row in rows:
        reminder = content_reminder_due(float(row.get("age_days") or 0), row.get("last_sent") or 0)
        if not reminder:
            continue
        if dry_run:
            stats["preview"].append({"application_id": row["id"], "reminder": reminder})
            continue
        cursor.execute(
            """
            INSERT INTO brand_pr_events (creator_id, brand_id, event, source, meta)
            VALUES (%s, %s, %s, 'cron', %s)
            """,
            (row["creator_id"], row["brand_id"], CONTENT_REMINDER_EVENT,
             Json({"application_id": row["id"], "n": reminder})),
        )
        conn.commit()
        payload = build_content_reminder_email(row, reminder)
        res = send(row["email"], payload["subject"], payload["html"], f"roster_content_due_{reminder}")
        stats["sent" if res.get("success") else "failed"] += 1
        try:
            if note_fn is None:
                from services.polly_autopilot import post_thread_note as note_fn
            note_fn(conn, row["creator_id"], content_reminder_note(row, reminder), [])
            conn.commit()
        except Exception as err:
            conn.rollback()
            print(f"[roster-delivery] content note skipped app={row['id']}: {err}")
    return stats


def process_roster_delivery(*, dry_run: bool = False, send_fn: Callable = None) -> dict:
    from pr_crm_routes import get_db_connection

    conn = get_db_connection()
    out: dict = {}
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        if not ensure_delivery_columns(cursor, conn):
            return {"skipped": "delivery columns not ready"}
        out["creators"] = send_creator_notices(cursor, conn, dry_run=dry_run, send_fn=send_fn)
        out["ship_reminders"] = send_ship_reminders(cursor, conn, dry_run=dry_run, send_fn=send_fn)
        out["lock_reminders"] = send_lock_reminders(cursor, conn, dry_run=dry_run, send_fn=send_fn)
        out["content_reminders"] = send_content_reminders(cursor, conn, dry_run=dry_run, send_fn=send_fn)
        if dry_run:
            conn.rollback()
        return out
    except Exception as err:
        conn.rollback()
        traceback.print_exc()
        out["error"] = str(err)
        return out
    finally:
        conn.close()
