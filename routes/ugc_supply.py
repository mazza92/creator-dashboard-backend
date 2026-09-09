# -*- coding: utf-8 -*-
"""
Admin API for UGC supply (creator prospects). Separate from pr_brands.

Hermes (creator onboarding) uses the same X-Admin-Token as brand outreach.
Prefix: /api/admin/ugc-supply
"""
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request, session
from psycopg2.extras import RealDictCursor

from services.resend_mail import send_resend_email
from services.tiktok_ugc_lead_writer import ensure_schema, insert_leads

ugc_supply_bp = Blueprint(
    "ugc_supply",
    __name__,
    url_prefix="/api/admin/ugc-supply",
)

BLOCKED_OUTREACH_STATUSES = {
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

SIGNUP_URL = "https://app.newcollab.co/register/creator"
DEFAULT_SUBJECT = "PR / gifting campaigns — @{{handle}}"
DEFAULT_FOLLOWUP_HOURS = 96


def get_db_connection():
    import os
    import psycopg2

    return psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get("X-Admin-Token")
        if admin_token == "pr-hunter-admin-2026":
            return f(*args, **kwargs)

        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Authentication required"}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            conn.close()
            if not user or user.get("email", "").lower() != "team@newcollab.co":
                return jsonify({"error": "Admin access required"}), 403
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return f(*args, **kwargs)

    return decorated_function


def _json_ready(row):
    if not row:
        return row
    out = dict(row)
    for key, val in list(out.items()):
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    handle = (out.get("handle") or "").lstrip("@").strip()
    if handle:
        out["profile_url"] = (out.get("profile_url") or "").strip() or (
            f"https://www.tiktok.com/@{handle}"
        )
    return out


def personalize(text, lead):
    mapping = {
        "{{display_name}}": lead.get("display_name") or lead.get("handle") or "there",
        "{{handle}}": lead.get("handle") or "",
        "{{niche}}": lead.get("niche") or "UGC",
        "{{email}}": lead.get("contact_email") or "",
        "{{profile_url}}": lead.get("profile_url") or (
            f"https://www.tiktok.com/@{lead['handle']}" if lead.get("handle") else ""
        ),
        "{{followers}}": str(lead.get("followers") or 0),
        "{{signup_url}}": SIGNUP_URL,
    }
    out = text or ""
    for token, value in mapping.items():
        out = out.replace(token, str(value))
    return out


def default_onboarding_html(lead=None):
    """Match invite, not unpaid-work pitch — avoids 'I only do fixed rate' replies."""
    return """
<p>Hey,</p>
<p>Saw your TikTok (@{{handle}}).</p>
<p>We have brands running PR / gifting campaigns, and your profile could be a good match.</p>
<p>If you're interested, you can sign up and apply here:<br><a href="{{signup_url}}">{{signup_url}}</a></p>
<p>Worth a look?</p>
<p>Mazza<br>Founder, Newcollab</p>
"""


def _ensure(conn):
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()


def _lead_select():
    return """
        SELECT
            l.id, l.handle, l.display_name, l.bio, l.contact_email, l.niche,
            l.location, l.followers, l.likes, l.video_count, l.bio_link,
            l.avatar_url, l.profile_url, l.source, l.status, l.qualified,
            l.notes, l.created_at, l.updated_at,
            COALESCE(t.outreach_count, 0) AS times_contacted,
            t.last_contacted_at,
            t.last_response_status
        FROM ugc_supply l
        LEFT JOIN ugc_supply_outreach_tracking t ON t.lead_id = l.id
    """


@ugc_supply_bp.route("", methods=["GET"])
@admin_required
def list_leads():
    """List scraped TikTok UGC leads. Default: qualified drafts with email."""
    try:
        qualified = request.args.get("qualified", "true").lower() == "true"
        has_email = request.args.get("has_email", "true").lower() == "true"
        not_contacted = request.args.get("not_contacted", "false").lower() == "true"
        status = (request.args.get("status") or "").strip()
        niche = (request.args.get("niche") or "").strip()
        min_followers = int(request.args.get("min_followers") or 0)
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))

        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        where = ["1=1"]
        params = []
        if qualified:
            where.append("l.qualified = TRUE")
        if has_email:
            where.append("l.contact_email IS NOT NULL AND TRIM(l.contact_email) <> ''")
        if status:
            where.append("l.status = %s")
            params.append(status)
        if niche:
            where.append("l.niche ILIKE %s")
            params.append(niche)
        if min_followers:
            where.append("l.followers >= %s")
            params.append(min_followers)
        if not_contacted:
            where.append("t.lead_id IS NULL")
            where.append(
                "COALESCE(t.last_response_status, '') NOT IN "
                "('replied','interested','not_interested','signed_up',"
                "'wrong_email','bounced','do_not_contact','unsubscribe','reply')"
            )

        where_sql = " AND ".join(where)
        cur.execute(
            f"{_lead_select()} WHERE {where_sql} ORDER BY l.created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = [_json_ready(r) for r in (cur.fetchall() or [])]
        cur.execute(
            f"SELECT COUNT(*) AS total FROM ugc_supply l "
            f"LEFT JOIN ugc_supply_outreach_tracking t ON t.lead_id = l.id "
            f"WHERE {where_sql}",
            params,
        )
        total = int((cur.fetchone() or {}).get("total") or 0)
        conn.close()
        return jsonify({"supply": rows, "leads": rows, "total": total, "limit": limit, "offset": offset})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/for-outreach", methods=["GET"])
@admin_required
def leads_for_outreach():
    """Ready for Hermes bulk send: qualified + email + not yet contacted."""
    try:
        niche = (request.args.get("niche") or "").strip()
        min_followers = int(request.args.get("min_followers") or 0)
        limit = min(int(request.args.get("limit", 40)), 100)
        offset = int(request.args.get("offset", 0))
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        where = [
            "l.qualified = TRUE",
            "l.contact_email IS NOT NULL AND TRIM(l.contact_email) <> ''",
            "COALESCE(l.status, 'draft') IN ('draft', 'review')",
            "t.lead_id IS NULL",
        ]
        params = []
        if niche:
            where.append("l.niche ILIKE %s")
            params.append(niche)
        if min_followers:
            where.append("l.followers >= %s")
            params.append(min_followers)
        where_sql = " AND ".join(where)
        cur.execute(
            f"{_lead_select()} WHERE {where_sql} ORDER BY l.created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = [_json_ready(r) for r in (cur.fetchall() or [])]
        cur.execute(
            f"SELECT COUNT(*) AS total FROM ugc_supply l "
            f"LEFT JOIN ugc_supply_outreach_tracking t ON t.lead_id = l.id "
            f"WHERE {where_sql}",
            params,
        )
        total = int((cur.fetchone() or {}).get("total") or 0)
        conn.close()
        return jsonify({"supply": rows, "leads": rows, "total": total, "limit": limit, "offset": offset})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/<int:lead_id>", methods=["GET"])
@admin_required
def get_lead(lead_id):
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(_lead_select() + " WHERE l.id = %s", (lead_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Lead not found"}), 404
        return jsonify({"lead": _json_ready(row)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/<int:lead_id>", methods=["PATCH"])
@admin_required
def patch_lead(lead_id):
    data = request.get_json() or {}
    allowed = {"status", "notes", "last_response_status"}
    if not any(k in data for k in allowed):
        return jsonify({"error": "nothing to update"}), 400
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if "status" in data or "notes" in data:
            sets = ["updated_at = NOW()"]
            params = []
            if "status" in data:
                sets.append("status = %s")
                params.append(data["status"])
            if "notes" in data:
                sets.append("notes = %s")
                params.append(data["notes"])
            params.append(lead_id)
            cur.execute(
                f"UPDATE ugc_supply SET {', '.join(sets)} WHERE id = %s RETURNING id",
                params,
            )
            if not cur.fetchone():
                conn.close()
                return jsonify({"error": "Lead not found"}), 404
        if "last_response_status" in data:
            cur.execute(
                """
                INSERT INTO ugc_supply_outreach_tracking
                    (lead_id, last_response_status, last_response_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (lead_id) DO UPDATE SET
                    last_response_status = EXCLUDED.last_response_status,
                    last_response_at = NOW()
                """,
                (lead_id, data["last_response_status"]),
            )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "lead_id": lead_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/ingest", methods=["POST"])
@admin_required
def ingest_leads():
    """Upsert crawler JSON into ugc_supply (qualified-only by default)."""
    data = request.get_json() or {}
    records = data.get("supply") or data.get("leads") or data.get("records") or []
    if not isinstance(records, list) or not records:
        return jsonify({"error": "supply or leads array is required"}), 400
    only_qualified = data.get("only_qualified", True)
    try:
        stats = insert_leads(records, only_qualified=bool(only_qualified))
        return jsonify({"success": True, **stats})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _load_lead(cur, lead_id):
    cur.execute(_lead_select() + " WHERE l.id = %s", (lead_id,))
    return cur.fetchone()


def _send_one(cur, conn, lead, subject, html_content, allow_followup, min_hours):
    if not lead.get("contact_email"):
        return False, "no_email", 400
    status = (lead.get("last_response_status") or "").lower()
    if status in BLOCKED_OUTREACH_STATUSES:
        return False, f"blocked_status:{status}", 409
    if lead.get("last_contacted_at") and not allow_followup:
        return False, "already_contacted", 409
    if allow_followup and lead.get("last_contacted_at") and min_hours:
        last = lead["last_contacted_at"]
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(hours=int(min_hours)):
            return False, "followup_cooldown", 409

    subj = personalize(subject or DEFAULT_SUBJECT, lead)
    html = personalize(html_content or default_onboarding_html(lead), lead)
    send_res = send_resend_email(
        lead["contact_email"],
        subj,
        html,
    )
    if not send_res.get("success"):
        return False, send_res.get("error") or "send_failed", 500

    cur.execute(
        """
        INSERT INTO ugc_supply_outreach_tracking
            (lead_id, outreach_count, last_contacted_at, last_subject)
        VALUES (%s, 1, NOW(), %s)
        ON CONFLICT (lead_id) DO UPDATE SET
            outreach_count = ugc_supply_outreach_tracking.outreach_count + 1,
            last_contacted_at = NOW(),
            last_subject = EXCLUDED.last_subject
        """,
        (lead["id"], subj),
    )
    cur.execute(
        """
        INSERT INTO ugc_supply_outreach_log
            (lead_id, email_sent_to, subject, status, sent_at, message_id)
        VALUES (%s, %s, %s, 'sent', NOW(), %s)
        """,
        (lead["id"], lead["contact_email"], subj, send_res.get("message_id")),
    )
    cur.execute(
        "UPDATE ugc_supply SET status = 'contacted', updated_at = NOW() WHERE id = %s",
        (lead["id"],),
    )
    conn.commit()
    return True, None, 200


@ugc_supply_bp.route("/outreach/send", methods=["POST"])
@admin_required
def send_one():
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    if not lead_id:
        return jsonify({"error": "lead_id is required"}), 400
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        lead = _load_lead(cur, int(lead_id))
        if not lead:
            conn.close()
            return jsonify({"error": "Lead not found"}), 404
        ok, err, code = _send_one(
            cur,
            conn,
            lead,
            data.get("subject"),
            data.get("html_content"),
            bool(data.get("allow_followup", False)),
            int(data.get("min_followup_hours", DEFAULT_FOLLOWUP_HOURS)),
        )
        conn.close()
        if not ok:
            return jsonify({"error": err, "lead_id": lead_id}), code
        return jsonify({
            "success": True,
            "lead_id": lead_id,
            "email": lead.get("contact_email"),
            "handle": lead.get("handle"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/outreach/bulk", methods=["POST"])
@admin_required
def send_bulk():
    data = request.get_json() or {}
    lead_ids = data.get("lead_ids") or []
    if not lead_ids:
        return jsonify({"error": "lead_ids list is required"}), 400
    lead_ids = [int(i) for i in lead_ids][:50]
    delay = float(data.get("delay_seconds") or 0.4)
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        results = {"sent": [], "failed": [], "skipped": []}
        import time as _time

        for lead_id in lead_ids:
            lead = _load_lead(cur, lead_id)
            if not lead:
                results["failed"].append({"lead_id": lead_id, "reason": "not_found"})
                continue
            ok, err, code = _send_one(
                cur,
                conn,
                lead,
                data.get("subject"),
                data.get("html_content"),
                bool(data.get("allow_followup", False)),
                int(data.get("min_followup_hours", DEFAULT_FOLLOWUP_HOURS)),
            )
            if ok:
                results["sent"].append({
                    "lead_id": lead_id,
                    "email": lead.get("contact_email"),
                    "handle": lead.get("handle"),
                })
            elif code == 409:
                results["skipped"].append({"lead_id": lead_id, "reason": err})
            else:
                results["failed"].append({"lead_id": lead_id, "reason": err})
            if delay:
                _time.sleep(delay)
        conn.close()
        return jsonify({"success": True, **results, "sent_count": len(results["sent"])})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/outreach/stats", methods=["GET"])
@admin_required
def outreach_stats():
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM ugc_supply) AS total_leads,
              (SELECT COUNT(*) FROM ugc_supply WHERE qualified) AS qualified,
              (SELECT COUNT(*) FROM ugc_supply
                 WHERE qualified AND contact_email IS NOT NULL) AS ready_email,
              (SELECT COUNT(*) FROM ugc_supply WHERE status = 'contacted') AS contacted,
              (SELECT COUNT(*) FROM ugc_supply_outreach_log WHERE status = 'sent') AS emails_sent
            """
        )
        row = _json_ready(cur.fetchone() or {})
        conn.close()
        return jsonify(row)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
