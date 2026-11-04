# -*- coding: utf-8 -*-
"""
Admin API for UGC supply (creator prospects). Separate from pr_brands.

Hermes (creator onboarding) uses the same X-Admin-Token as brand outreach.
Prefix: /api/admin/ugc-supply
"""
from datetime import datetime, timedelta, timezone
from functools import wraps
from services.admin_auth import admin_token_valid

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

# The ref survives signup (frontend keeps it), is stored by creator_offers, and checkout grants the 7-day
# trial; Polly opens the Autopilot card on first visit.
SIGNUP_URL = "https://app.newcollab.co/register/creator?ref=polly_trial7"
# These creators want paid deals, so the email sells Autopilot for paid UGC, not gifting. Subjects read like a
# note from a person: short, about paid work, no "free", no "done for you", no brackets. History: "Paid UGC for
# @handle" 2 signups from 9; "@handle, your brand pitches done for you (7 days free)" 0 from 49. Each lead gets
# one variant (stable per lead, so the follow-up's "Re:" matches) and the log keeps it, so the winner shows.
SUBJECT_VARIANTS = (
    "paid UGC deals for @{{handle}}",
    "more paid brand deals, @{{handle}}?",
    "quick question, @{{handle}}",
)
DEFAULT_SUBJECT = SUBJECT_VARIANTS[0]


def subject_for_lead(lead):
    import hashlib

    key = str((lead or {}).get("id") or (lead or {}).get("handle") or "")
    return SUBJECT_VARIANTS[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(SUBJECT_VARIANTS)]
DEFAULT_FOLLOWUP_HOURS = 96
MAX_EMAILS_PER_LEAD = 2  # first email + one follow-up
FOLLOWUP_WINDOW_DAYS = 21  # a "quick bump" weeks later reads oddly; older leads get a fresh pitch instead
# Creators who already have a Newcollab account never get the join pitch.
NOT_A_MEMBER_SQL = "NOT EXISTS (SELECT 1 FROM users u WHERE LOWER(u.email) = LOWER(TRIM(l.contact_email)))"


def get_db_connection():
    import os
    import psycopg2

    return psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get("X-Admin-Token")
        if admin_token_valid(admin_token):
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
    """Autopilot pitches brands that pay for UGC, with the creator's rate; 7 days free. Only what the product
    does: it finds brands, writes, sends from their Gmail and follows up. Replies land in their inbox and brands
    pay them directly, so the copy never says Polly negotiates, signs or guarantees deals."""
    return """
<p>Hey,</p>
<p>Saw your UGC on TikTok (@{{handle}}). Quick question: how many brands did you pitch for paid work last week?</p>
<p>Paid deals come from pitching a lot of brands, and that's the part that eats your week.</p>
<p>Polly, our AI assistant, runs it on autopilot:<br>&bull; finds brands that pay creators for UGC, each with a checked contact<br>&bull; writes a pitch for each brand with your rate, sent from your Gmail<br>&bull; follows up on day 4 and day 10 so no brand goes cold</p>
<p>Brands reply straight to your inbox and pay you directly.</p>
<p><a href="{{signup_url}}">Try it free for 7 days</a></p>
<p>Mazza<br>Founder, Newcollab</p>
"""


def followup_html(lead=None):
    """Day-4 bump, same offer as the first email. Sent once, never after a reply."""
    return """
<p>Hey,</p>
<p>Quick bump: your 7 free days are still open. Add your rate, connect Gmail, and this week's pitches to brands that pay for UGC go out for you while you keep creating.</p>
<p><a href="{{signup_url}}">Start your free week</a></p>
<p>Mazza</p>
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
            t.last_subject,
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
            NOT_A_MEMBER_SQL,
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
    email = (lead.get("contact_email") or "").strip().lower()
    if not email:
        return False, "no_email", 400
    from services.tiktok_ugc_profile_scraper import is_usable_contact_email
    if not is_usable_contact_email(email):
        return False, "placeholder_email", 400
    status = (lead.get("last_response_status") or "").lower()
    if status in BLOCKED_OUTREACH_STATUSES:
        return False, f"blocked_status:{status}", 409
    if lead.get("last_contacted_at") and not allow_followup:
        return False, "already_contacted", 409
    if allow_followup and int(lead.get("times_contacted") or 0) >= MAX_EMAILS_PER_LEAD:
        return False, "sequence_finished", 409
    if allow_followup and lead.get("last_contacted_at") and min_hours:
        last = lead["last_contacted_at"]
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(hours=int(min_hours)):
            return False, "followup_cooldown", 409

    subj = personalize(subject or subject_for_lead(lead), lead)
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
    followup = data.get("kind") == "followup"
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
            if followup:
                first = lead.get("last_subject") or personalize(subject_for_lead(lead), lead)
                subject = first if first.lower().startswith("re:") else f"Re: {first}"
                ok, err, code = _send_one(cur, conn, lead, subject, followup_html(lead), True,
                                          int(data.get("min_followup_hours", DEFAULT_FOLLOWUP_HOURS)))
            else:
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


@ugc_supply_bp.route("/outreach/followups", methods=["GET"])
@admin_required
def followups_due():
    """Contacted once, no reply, not a member, first email at least DEFAULT_FOLLOWUP_HOURS ago."""
    try:
        limit = min(int(request.args.get("limit", 50)), 100)
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            f"""{_lead_select()}
            WHERE t.outreach_count = 1
              AND t.last_contacted_at < NOW() - (%s * INTERVAL '1 hour')
              AND t.last_contacted_at > NOW() - (%s * INTERVAL '1 day')
              AND COALESCE(t.last_response_status, '') = ''
              AND COALESCE(l.status, '') = 'contacted'
              AND {NOT_A_MEMBER_SQL}
            ORDER BY t.last_contacted_at
            LIMIT %s""",
            (DEFAULT_FOLLOWUP_HOURS, FOLLOWUP_WINDOW_DAYS, limit),
        )
        rows = [_json_ready(r) for r in (cur.fetchall() or [])]
        conn.close()
        return jsonify({"success": True, "total": len(rows), "leads": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ugc_supply_bp.route("/replies", methods=["POST"])
@admin_required
def record_replies():
    """Mark leads whose contact email replied, so no follow-up goes out. Body: {"emails": [...]}."""
    data = request.get_json() or {}
    emails = sorted({(e or "").strip().lower() for e in (data.get("emails") or []) if "@" in (e or "")})[:200]
    if not emails:
        return jsonify({"success": True, "matched": []})
    try:
        conn = get_db_connection()
        _ensure(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            UPDATE ugc_supply_outreach_tracking t SET last_response_status = 'replied'
            FROM ugc_supply l
            WHERE l.id = t.lead_id AND LOWER(TRIM(l.contact_email)) = ANY(%s)
              AND COALESCE(t.last_response_status, '') = ''
            RETURNING l.id, l.handle, l.contact_email
            """,
            (emails,),
        )
        matched = [_json_ready(r) for r in (cur.fetchall() or [])]
        conn.commit()
        conn.close()
        return jsonify({"success": True, "matched": matched})
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
              (SELECT COUNT(*) FROM ugc_supply_outreach_log WHERE status = 'sent') AS emails_sent,
              (SELECT COUNT(*) FROM ugc_supply WHERE created_at > NOW() - INTERVAL '24 hours') AS new_leads_24h,
              (SELECT COUNT(*) FROM ugc_supply l JOIN ugc_supply_outreach_tracking t ON t.lead_id = l.id
                 JOIN users u ON LOWER(u.email) = LOWER(TRIM(l.contact_email))
                 WHERE u.created_at > t.last_contacted_at - INTERVAL '1 day') AS signed_up,
              (SELECT COUNT(*) FROM ugc_supply_outreach_tracking WHERE last_response_status = 'replied') AS replied
            """
        )
        row = _json_ready(cur.fetchone() or {})
        conn.close()
        return jsonify(row)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
