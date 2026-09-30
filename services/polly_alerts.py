"""Polly outside Polly: alert emails, one-tap outcomes, and unsent-draft refunds.

Runs from the Polly cron. Resend only (no SMTP). Every email deep-links back
into Polly with a chip so one tap records the outcome (e.g. "They replied").
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

POLLY_PATH = "/creator/dashboard/for-you"
REFUND_AFTER = timedelta(days=7)
KIT_VIEW_EMAIL_WINDOW = timedelta(hours=48)
KIT_VIEW_THROTTLE = timedelta(hours=6)
NUDGE_EMAIL_THROTTLE = timedelta(hours=20)

NUDGE_SUBJECTS = {
    "follow_up_d4": "Any reply from {brand}?",
    "follow_up_d10": "Last nudge for {brand}?",
    "follow_up_d14": "{brand} went quiet — here's the next move",
    "reply_needed": "{brand} replied — answer while they're warm",
    "pr_shipped": "Did your {brand} box arrive?",
    "content_due": "Time to film your {brand} content",
}

EMAIL_CHIP_IDS = (
    "checkin_replied", "checkin_quiet", "draft_followup", "checkin_not_sent",
    "pr_arrived", "pr_not_yet", "help_reply", "line_up", "move_on", "need_idea",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def ensure_alert_tables(conn) -> None:
    cur = _cursor(conn)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_drafts (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            brand_id INTEGER NOT NULL,
            brand_name TEXT,
            drafted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            credit_used BOOLEAN NOT NULL DEFAULT FALSE,
            handoff_at TIMESTAMPTZ,
            handoff_method TEXT,
            refunded_at TIMESTAMPTZ,
            UNIQUE (creator_id, brand_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_email_log (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            brand_id INTEGER,
            ref_id INTEGER,
            subject TEXT,
            message_id TEXT,
            ok BOOLEAN NOT NULL DEFAULT TRUE,
            error TEXT,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_polly_email_log_creator ON polly_email_log (creator_id, kind, sent_at DESC)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Drafts: credit tracking + handoff (Open email / Copy email / Copy pitch)
# ---------------------------------------------------------------------------

def record_draft(conn, creator_id: int, brand_id: Any, brand_name: str = "", credit_used: bool = False) -> None:
    try:
        bid = int(brand_id or 0)
    except (TypeError, ValueError):
        bid = 0
    if not creator_id or not bid:
        return
    ensure_alert_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_drafts (creator_id, brand_id, brand_name, credit_used)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (creator_id, brand_id) DO UPDATE
        SET credit_used = polly_drafts.credit_used OR EXCLUDED.credit_used,
            brand_name = COALESCE(polly_drafts.brand_name, EXCLUDED.brand_name)
        """,
        (creator_id, bid, (brand_name or "")[:120] or None, bool(credit_used)),
    )
    conn.commit()


def record_handoff(conn, creator_id: int, brand_id: Any, method: str = "") -> bool:
    try:
        bid = int(brand_id or 0)
    except (TypeError, ValueError):
        bid = 0
    if not creator_id or not bid:
        return False
    ensure_alert_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_drafts
        SET handoff_at = COALESCE(handoff_at, NOW()),
            handoff_method = COALESCE(handoff_method, %s)
        WHERE creator_id = %s AND brand_id = %s
        """,
        ((method or "")[:24] or None, creator_id, bid),
    )
    conn.commit()
    return bool(cur.rowcount)


STALE_DRAFTS_SQL = """
    SELECT d.id, d.creator_id, d.brand_id, COALESCE(d.brand_name, b.brand_name) AS brand_name
    FROM polly_drafts d
    JOIN creators c ON c.id = d.creator_id
    LEFT JOIN pr_brands b ON b.id = d.brand_id
    WHERE d.credit_used
      AND d.refunded_at IS NULL
      AND d.handoff_at IS NULL
      AND d.drafted_at <= NOW() - INTERVAL '7 days'
      AND d.drafted_at >= date_trunc('month', NOW())
      AND COALESCE(c.unlocks_tier, 'free') <> 'pro'
      AND COALESCE(c.subscription_tier, 'free') NOT IN ('pro', 'elite')
      AND NOT EXISTS (
          SELECT 1 FROM polly_tasks t
          WHERE t.creator_id = d.creator_id AND t.brand_id = d.brand_id
            AND t.type = 'pitch_sent'
            AND COALESCE(t.metadata->>'drafted', 'false') <> 'true'
      )
      AND NOT EXISTS (
          SELECT 1 FROM creator_pipeline cp
          WHERE cp.creator_id = d.creator_id AND cp.brand_id = d.brand_id
            AND (cp.pitched_at IS NOT NULL OR cp.send_confirmed IS TRUE)
      )
      AND NOT EXISTS (
          SELECT 1 FROM brand_pr_applications a
          WHERE a.creator_id = d.creator_id AND a.brand_id = d.brand_id
      )
    ORDER BY d.drafted_at ASC
    LIMIT %s
"""


def refund_stale_drafts(conn, limit: int = 50, dry_run: bool = False) -> List[Dict[str, Any]]:
    """Give the credit back for Polly drafts never sent or handed off within 7 days.

    Only drafts from the current month: last month's credits already reset.
    The refund is a pack credit, so the brand stays unlocked for them.
    """
    from services.pack_credits import pack_credits_column_exists

    ensure_alert_tables(conn)
    if not pack_credits_column_exists(conn):
        print("[Polly alerts] refunds skipped: creators.pack_credits missing")
        return []
    cur = _cursor(conn)
    cur.execute(STALE_DRAFTS_SQL, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    if dry_run:
        return rows
    done = []
    for row in rows:
        try:
            cur.execute(
                "UPDATE polly_drafts SET refunded_at = NOW() WHERE id = %s AND refunded_at IS NULL",
                (row["id"],),
            )
            if not cur.rowcount:
                conn.rollback()
                continue
            cur.execute(
                "UPDATE creators SET pack_credits = COALESCE(pack_credits, 0) + 1 WHERE id = %s",
                (row["creator_id"],),
            )
            conn.commit()
            _post_refund_notice(conn, row["creator_id"], row.get("brand_id"), row.get("brand_name") or "")
            done.append(row)
        except Exception as err:
            print(f"[Polly alerts] refund failed draft={row.get('id')}: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
    return done


def _post_refund_notice(conn, creator_id: int, brand_id: Any, brand_name: str) -> None:
    from services.polly_memory import load_thread, save_thread
    from services.polly_persona import persona_credit_refunded
    from services.polly_tracker import add_event

    try:
        add_event(
            conn, creator_id, "milestone",
            f"Credit refunded · {brand_name or 'unsent draft'}",
            brand_id=brand_id,
            event_data={"kind": "credit_refunded", "brand_name": brand_name},
            polly_notes="Draft sat unsent for 7 days, so the credit came back.",
        )
    except Exception as err:
        print(f"[Polly alerts] refund event skipped: {err}")
    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or [])
    messages.append({
        "role": "assistant",
        "content": persona_credit_refunded(brand_name),
        "kind": "nudge",
        "task_chips": [
            {
                "id": "line_up",
                "label": "Line up a brand I'll send today",
                "action": "suggest_brands",
                "deal": "gifted",
            },
        ],
    })
    save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=thread.get("notes"))


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def frontend_url() -> str:
    return (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/")


def chip_link(chip_id: str, task_id: Any = None, brand_id: Any = None, brand_name: str = "", kind: str = "") -> str:
    params = {
        "chip": chip_id,
        "utm_source": "email",
        "utm_medium": "polly_alert",
        "utm_campaign": kind or "polly",
    }
    if task_id:
        params["task_id"] = task_id
    if brand_id:
        params["brand_id"] = brand_id
    if brand_name:
        params["brand_name"] = brand_name
    return f"{frontend_url()}{POLLY_PATH}?{urlencode(params)}"


def _unsubscribe_url(user_id: Any) -> str:
    try:
        from public_routes import make_unsubscribe_token
        backend = (os.getenv("BACKEND_URL") or "https://api.newcollab.co").rstrip("/")
        return f"{backend}/api/public/unsubscribe?uid={user_id}&token={make_unsubscribe_token(str(user_id))}"
    except Exception:
        return ""


def _button(label: str, href: str, primary: bool = False) -> str:
    bg = "#111827" if primary else "#F3F4F6"
    fg = "#FFFFFF" if primary else "#111827"
    return (
        f'<a href="{escape(href, quote=True)}" style="display:inline-block;margin:0 8px 10px 0;'
        f'padding:12px 18px;border-radius:999px;background:{bg};color:{fg};font-weight:600;'
        f'font-size:14px;text-decoration:none;">{escape(label)}</a>'
    )


def render_email(first_name: str, headline: str, body_lines: List[str], buttons: List[Dict[str, str]], unsubscribe_url: str = "") -> str:
    name = escape((first_name or "").strip() or "there")
    paras = "".join(
        f'<p style="margin:0 0 14px 0;font-size:15px;line-height:1.6;color:#374151;">{line}</p>'
        for line in body_lines
    )
    btns = "".join(_button(b["label"], b["href"], primary=(i == 0)) for i, b in enumerate(buttons))
    unsub = (
        f'<p style="margin:24px 0 0 0;font-size:12px;color:#9CA3AF;">'
        f'<a href="{escape(unsubscribe_url, quote=True)}" style="color:#9CA3AF;">Unsubscribe</a></p>'
        if unsubscribe_url else ""
    )
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#F7F5F0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#FFFFFF;border-radius:16px;">'
        '<tr><td style="padding:28px 28px 20px 28px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">'
        '<p style="margin:0 0 6px 0;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#C2410C;">Polly</p>'
        f'<h1 style="margin:0 0 16px 0;font-size:22px;line-height:1.3;color:#111827;">{escape(headline)}</h1>'
        f'<p style="margin:0 0 14px 0;font-size:15px;line-height:1.6;color:#374151;">Hey {name},</p>'
        f"{paras}"
        f'<div style="margin-top:8px;">{btns}</div>'
        '<p style="margin:18px 0 0 0;font-size:13px;color:#6B7280;">— Polly, your Newcollab manager</p>'
        f"{unsub}"
        "</td></tr></table></td></tr></table></body></html>"
    )


def kit_view_email(
    first_name: str,
    brand_name: str,
    brand_id: Any,
    is_pro: bool,
    category: str = "",
    unsubscribe_url: str = "",
) -> Dict[str, str]:
    """Pro sees the brand name. Free sees the category and opens Polly to find out who."""
    if is_pro:
        subject = f"{brand_name} just opened your kit"
        headline = f"{brand_name} opened your kit"
        lines = [
            f"<strong>{escape(brand_name)}</strong> opened your Newcollab kit. That's a real look.",
            "Brands decide fast. A two-line follow-up today beats a perfect one next week — "
            "and follow-ups never use a credit.",
        ]
        buttons = [
            {"label": f"Draft {brand_name} follow-up", "href": chip_link("draft_followup", brand_id=brand_id, brand_name=brand_name, kind="kit_view")},
            {"label": "They already replied", "href": chip_link("checkin_replied", brand_id=brand_id, brand_name=brand_name, kind="kit_view")},
        ]
    else:
        cat = (category or "").strip().lower()
        if cat and cat not in ("other", "unknown", "n/a"):
            who = f"{'An' if cat[0] in 'aeiou' else 'A'} {cat} brand"
        else:
            who = "A brand"
        subject = f"{who} just opened your kit"
        headline = f"{who} opened your kit"
        lines = [
            f"{escape(who)} opened your Newcollab kit just now.",
            "Open Polly to see who it was and send a short follow-up while you're top of mind. "
            "Follow-ups never use a credit.",
        ]
        buttons = [
            {"label": "See who & follow up", "href": chip_link("draft_followup", brand_id=brand_id, kind="kit_view")},
        ]
    return {
        "subject": subject,
        "html": render_email(first_name, headline, lines, buttons, unsubscribe_url),
    }


def nudge_email(first_name: str, nudge: Dict[str, Any], unsubscribe_url: str = "") -> Dict[str, str]:
    import re

    key = nudge.get("key") or "follow_up_d4"
    brand = nudge.get("brand_name") or "that brand"
    subject = NUDGE_SUBJECTS.get(key, "Update on {brand}?").format(brand=brand)
    message = str(nudge.get("message") or "")
    message_html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(message))
    buttons = []
    for chip in nudge.get("chips") or []:
        cid = chip.get("id")
        if cid not in EMAIL_CHIP_IDS:
            continue
        buttons.append({
            "label": chip.get("label") or cid,
            "href": chip_link(cid, nudge.get("task_id"), nudge.get("brand_id"), brand, kind=key),
        })
        if len(buttons) >= 3:
            break
    if not buttons:
        buttons = [{"label": "Open Polly", "href": chip_link("checkin_replied", nudge.get("task_id"), nudge.get("brand_id"), brand, kind=key)}]
    lines = [message_html, "One tap and I'll take the next step."]
    return {
        "subject": subject,
        "html": render_email(first_name, subject, lines, buttons, unsubscribe_url),
    }


def _recipient(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT c.id AS creator_id, u.id AS user_id, u.email, u.first_name,
               c.subscription_tier, c.unlocks_tier, u.unsubscribed_at
        FROM creators c JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (creator_id,),
    )
    row = cur.fetchone()
    if not row or not row.get("email") or row.get("unsubscribed_at"):
        return None
    row = dict(row)
    row["is_pro"] = (
        (row.get("unlocks_tier") or "") == "pro"
        or (row.get("subscription_tier") or "").lower() in ("pro", "elite")
    )
    first = (row.get("first_name") or "").strip()
    row["first"] = first.split()[0].capitalize() if first else ""
    return row


def _recently_emailed(conn, creator_id: int, kinds: List[str], within: timedelta) -> bool:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT 1 FROM polly_email_log
        WHERE creator_id = %s AND kind = ANY(%s) AND ok AND sent_at > %s
        LIMIT 1
        """,
        (creator_id, kinds, utc_now() - within),
    )
    return cur.fetchone() is not None


def _already_emailed_ref(conn, kind: str, ref_id: Any) -> bool:
    cur = _cursor(conn)
    cur.execute(
        "SELECT 1 FROM polly_email_log WHERE kind = %s AND ref_id = %s AND ok LIMIT 1",
        (kind, ref_id),
    )
    return cur.fetchone() is not None


def _log_email(conn, creator_id, kind, brand_id, ref_id, subject, result) -> None:
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_email_log (creator_id, kind, brand_id, ref_id, subject, message_id, ok, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            creator_id, kind, brand_id, ref_id, (subject or "")[:200],
            (result or {}).get("message_id"),
            bool((result or {}).get("success")),
            ((result or {}).get("error") or "")[:300] or None,
        ),
    )
    conn.commit()


def _default_send(to_email: str, subject: str, html: str, unsubscribe_url: str, kind: str) -> Dict[str, Any]:
    from services.resend_mail import send_resend_email

    return send_resend_email(
        to_email, subject, html,
        unsubscribe_url=unsubscribe_url or None,
        tags=[{"name": "type", "value": "polly_alert"}, {"name": "kind", "value": kind}],
    )


def email_kit_views(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None, test_email: str = "") -> List[Dict[str, Any]]:
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT e.id, e.creator_id, e.brand_id,
               COALESCE(b.brand_name, e.event_data->>'brand_name') AS brand_name,
               b.category
        FROM polly_timeline_events e
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.event_type = 'portfolio_viewed' AND e.occurred_at > %s
        ORDER BY e.occurred_at DESC
        LIMIT %s
        """,
        (utc_now() - KIT_VIEW_EMAIL_WINDOW, limit * 3),
    )
    out = []
    for row in [dict(r) for r in cur.fetchall()]:
        if len(out) >= limit:
            break
        if _already_emailed_ref(conn, "kit_view", row["id"]):
            continue
        if _recently_emailed(conn, row["creator_id"], ["kit_view"], KIT_VIEW_THROTTLE):
            continue
        who = _recipient(conn, row["creator_id"])
        if not who:
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = kit_view_email(
            who["first"], row.get("brand_name") or "A brand", row.get("brand_id"),
            who["is_pro"], row.get("category") or "", unsub,
        )
        item = {"creator_id": row["creator_id"], "kind": "kit_view", "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "kit_view")
        _log_email(conn, row["creator_id"], "kit_view", row.get("brand_id"), row["id"], mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


def email_nudges(conn, delivered: List[Dict[str, Any]], dry_run: bool = False, send_fn: Optional[Callable] = None, test_email: str = "") -> List[Dict[str, Any]]:
    """Email the nudges the cron just posted in-thread (one per creator per 20h)."""
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    out = []
    for entry in delivered or []:
        cid = entry.get("creator_id")
        nudge = entry.get("nudge") or {}
        if not cid or not nudge:
            continue
        if _recently_emailed(conn, cid, ["nudge"], NUDGE_EMAIL_THROTTLE):
            continue
        who = _recipient(conn, cid)
        if not who:
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = nudge_email(who["first"], nudge, unsub)
        item = {"creator_id": cid, "kind": "nudge", "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, nudge.get("key") or "nudge")
        _log_email(conn, cid, "nudge", nudge.get("brand_id"), nudge.get("task_id"), mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


def run_alerts(conn, dry_run: bool = False, limit: int = 40, test_email: str = "", send_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Cron entry: post due nudges in-thread, email them + kit views, refund stale drafts."""
    from services.polly_tracker import process_due_nudges
    from services.resend_mail import resend_configured

    can_email = dry_run or bool(send_fn) or resend_configured()
    delivered: List[Dict[str, Any]] = []
    nudged = 0
    if not dry_run:
        nudged = process_due_nudges(conn, creator_limit=limit, delivered=delivered)
    emails = []
    if can_email:
        emails += email_nudges(conn, delivered, dry_run=dry_run, send_fn=send_fn, test_email=test_email)
        emails += email_kit_views(conn, limit=limit, dry_run=dry_run, send_fn=send_fn, test_email=test_email)
    refunds = refund_stale_drafts(conn, limit=limit, dry_run=dry_run)
    return {
        "nudged": nudged,
        "emails": emails,
        "email_enabled": can_email,
        "refunds": [
            {"creator_id": r.get("creator_id"), "brand_id": r.get("brand_id"), "brand_name": r.get("brand_name")}
            for r in refunds
        ],
    }
