"""After a brand locks a gifted roster: brief the picked creators, nudge the brand to
ship, collect the content, and hand it to the brand with the next-roster offer."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timezone
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
}
CREATOR_NOTICE_WINDOW_DAYS = 21
CONTENT_DUE_DAYS = 10
SHIP_REMINDERS = ((2, 21, "ship_reminder_1"), (5, 22, "ship_reminder_2"))
SHIP_REMINDER_MAX_AGE_DAYS = 30
CONTENT_READY_TRIGGER = 30
CONTENT_READY_TYPE = "content_delivered"
PLAN_PRICE_LABEL = "$299/mo"
MAX_URL_LEN = 600
MAX_NOTE_LEN = 500

_READY = False
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
           a.shipped_at AS creator_shipped_at, camp.sku_note,
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
    sql = _CREATOR_NOTICE_SQL
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


def pick_ship_reminder(row: dict) -> Optional[int]:
    age = float(row.get("age_days") or 0)
    if row.get("r1_at") is None:
        return 1 if age >= SHIP_REMINDERS[0][0] else None
    if row.get("r2_at") is None and age >= SHIP_REMINDERS[1][0]:
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
