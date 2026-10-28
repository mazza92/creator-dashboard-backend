"""Roster messaging: one light thread per picked creator, between the brand and the creator.

Opens once the brand has locked its picks, so details like a bedsheet size or a delivery date get sorted on
Newcollab instead of over email, where we lose the relationship. Like Airbnb, emails and phone numbers typed
into a message are hidden, so contact stays here.

Each new message is emailed to the other side, at most once per thread every EMAIL_GAP while it's unread
(the brand via its roster contact email, the creator via their account email), with a link back into the
conversation. Reading a thread marks the other side's messages as seen (shown as "Seen" to the sender).
"""
from __future__ import annotations

import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

MAX_BODY = 1000
EMAIL_GAP = timedelta(minutes=10)
OPEN_STATUSES = ("ships", "posted")
SIDES = ("brand", "creator")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# 9+ digits with the usual separators: phone and WhatsApp numbers, not sizes, dates or prices.
_PHONE = re.compile(r"(?<![\w/])(\+?\d[\d\s().-]{7,}\d)(?![\w/])")
HIDDEN = "[hidden]"

_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roster_messages (
            id SERIAL PRIMARY KEY,
            application_id INTEGER NOT NULL,
            campaign_id INTEGER,
            brand_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            body TEXT NOT NULL,
            masked BOOLEAN NOT NULL DEFAULT FALSE,
            read_at TIMESTAMPTZ,
            emailed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roster_messages_app ON roster_messages (application_id, created_at)")
    conn.commit()
    _READY = True


def mask(text: str) -> Tuple[str, bool]:
    """Hide emails and phone numbers. Returns (text, anything_hidden)."""
    out = _EMAIL.sub(HIDDEN, text)
    out = _PHONE.sub(lambda m: HIDDEN if len(re.sub(r"\D", "", m.group(1))) >= 9 else m.group(1), out)
    return out, out != text


def clean_body(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    text = str(raw or "").replace("\r", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return None, "Write a message"
    if len(text) > MAX_BODY:
        return None, f"Keep messages under {MAX_BODY} characters"
    return text, None


def thread_context(conn, application_id: int) -> Optional[Dict[str, Any]]:
    """Who's in this thread, and whether it's open (the creator was picked and the brand locked)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT a.id AS application_id, a.creator_id, a.brand_id, a.campaign_id, a.status,
               camp.token, camp.status AS campaign_status, camp.sku_note,
               b.brand_name, b.contact_email, b.logo_url,
               u.email AS creator_email, u.first_name AS creator_first, cr.username, cr.image_profile
        FROM brand_pr_applications a
        JOIN pr_brands b ON b.id = a.brand_id
        LEFT JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
        JOIN creators cr ON cr.id = a.creator_id
        JOIN users u ON u.id = cr.user_id
        WHERE a.id = %s
        """,
        (application_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    ctx = dict(row)
    ctx["open"] = ctx["status"] in OPEN_STATUSES and ctx.get("campaign_status") in ("locked", "shipped")
    return ctx


def _public(m: Dict[str, Any], viewer: str) -> Dict[str, Any]:
    return {
        "id": m["id"],
        "from": m["sender"],
        "mine": m["sender"] == viewer,
        "body": m["body"],
        "masked": bool(m.get("masked")),
        "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
        "seen": bool(m.get("read_at")) if m["sender"] == viewer else None,
    }


def load_thread(conn, application_id: int, viewer: str, mark_read: bool = True) -> List[Dict[str, Any]]:
    """Messages oldest first. Viewing marks the other side's messages as read."""
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if mark_read:
        cur.execute(
            "UPDATE roster_messages SET read_at = NOW() WHERE application_id = %s AND sender <> %s AND read_at IS NULL",
            (application_id, viewer),
        )
        conn.commit()
    cur.execute("SELECT * FROM roster_messages WHERE application_id = %s ORDER BY created_at, id", (application_id,))
    return [_public(dict(r), viewer) for r in cur.fetchall()]


def unread_counts(conn, application_ids: List[int], viewer: str) -> Dict[int, int]:
    """Messages from the other side not yet read, per application."""
    if not application_ids:
        return {}
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT application_id, COUNT(*) AS n FROM roster_messages
           WHERE application_id = ANY(%s) AND sender <> %s AND read_at IS NULL GROUP BY application_id""",
        (list(application_ids), viewer),
    )
    return {int(r["application_id"]): int(r["n"]) for r in cur.fetchall()}


def post_message(conn, ctx: Dict[str, Any], sender: str, raw_body: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if sender not in SIDES:
        return None, "Unknown sender"
    if not ctx.get("open"):
        return None, "Messaging opens once the brand has locked its picks"
    body, error = clean_body(raw_body)
    if error:
        return None, error
    body, masked = mask(body)
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """INSERT INTO roster_messages (application_id, campaign_id, brand_id, creator_id, sender, body, masked)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (ctx["application_id"], ctx.get("campaign_id"), ctx["brand_id"], ctx["creator_id"], sender, body, masked),
    )
    msg = dict(cur.fetchone())
    conn.commit()
    return _public(msg, sender), None


# ---------------------------------------------------------------------------
# Email the other side
# ---------------------------------------------------------------------------

def _should_email(conn, application_id: int, sender: str, now: datetime) -> bool:
    """One email per thread per EMAIL_GAP while the recipient hasn't read what we already sent."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT MAX(emailed_at) AS last FROM roster_messages
           WHERE application_id = %s AND sender = %s AND emailed_at IS NOT NULL AND read_at IS NULL""",
        (application_id, sender),
    )
    last = (cur.fetchone() or {}).get("last")
    return not last or now - last >= EMAIL_GAP


def build_email(ctx: Dict[str, Any], sender: str, body: str) -> Dict[str, str]:
    from services.roster_delivery import _first_name, _link, _p, _wrap
    from services.roster_waiting_email import frontend_base

    brand = ctx.get("brand_name") or "The brand"
    creator = (ctx.get("creator_first") or ctx.get("username") or "Your creator").strip()
    preview = body if len(body) <= 280 else body[:277] + "…"
    quote = ('<p style="margin: 0 0 16px; padding: 12px 14px; background: #f5f5f7; border-radius: 12px; '
             f'font-size: 15px; line-height: 1.5; color: #1d1d1f; white-space: pre-line;">{escape(preview)}</p>')
    if sender == "brand":
        subject = f"{brand} sent you a message"
        inner = "".join([_p(f"Hi {escape(_first_name(ctx.get('creator_first')))},"),
                         _p(f"{escape(brand)} wrote to you about your PR box:"), quote,
                         _p("Reply on Newcollab so everything about this collab stays in one place.")])
        url = _link("/creator/dashboard/pr-brands", "roster_message", "email",
                    {"picked": ctx["application_id"], "chat": 1})
    else:
        subject = f"{creator} replied about your {brand} PR box"
        inner = "".join([_p("Hi,"), _p(f"{escape(creator)} sent you a message:"), quote,
                         _p("Reply from your roster so the conversation stays with the shipment.")])
        url = f"{frontend_base().rstrip('/')}/r/{ctx.get('token')}?chat={ctx['application_id']}&utm_source=email&utm_medium=roster_message"
    return {"subject": subject, "html": _wrap(subject, preview[:90], inner, url, "Reply"), "url": url}


def notify_async(application_id: int, sender: str, message_id: int, send_fn=None) -> None:
    def run():
        from pr_crm_routes import get_db_connection
        from services.roster_delivery import _default_send

        conn = get_db_connection()
        try:
            ctx = thread_context(conn, application_id)
            if not ctx or not _should_email(conn, application_id, sender, _now()):
                return
            to = ctx.get("contact_email") if sender == "creator" else ctx.get("creator_email")
            if not str(to or "").strip():
                return
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT body FROM roster_messages WHERE id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                return
            mail = build_email(ctx, sender, row["body"])
            res = (send_fn or _default_send)(to.strip(), mail["subject"], mail["html"], f"roster_message_{sender}")
            if res.get("success"):
                cur.execute("UPDATE roster_messages SET emailed_at = NOW() WHERE id = %s", (message_id,))
                conn.commit()
        except Exception:
            conn.rollback()
            traceback.print_exc()
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()
