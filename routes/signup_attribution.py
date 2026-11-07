"""First-touch signup attribution: where a new account came from (TikTok ad, Google, email...).

The web app stores utm_* / ttclid / gclid when someone lands, then posts them here once after signup. Saved to
users.signup_meta only while it is empty and only for accounts created in the last 3 days, so an old user who
clicks an ad later is never re-attributed. Lets us report cost per *approved* creator per campaign and ad.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, jsonify, request, session

signup_attribution_bp = Blueprint("signup_attribution", __name__)

KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_id", "utm_content", "utm_term", "ttclid", "gclid",
        "landing", "ref", "ts")
_SAFE = re.compile(r"[^\w\-\.:/\s|+%=&?#@]")


def clean(payload) -> dict:
    out = {}
    if not isinstance(payload, dict):
        return out
    for k in KEYS:
        v = payload.get(k)
        if v is None or isinstance(v, (dict, list)):
            continue
        v = _SAFE.sub("", str(v)).strip()[:300]
        if v:
            out[k] = v
    return out


@signup_attribution_bp.route("/api/me/attribution", methods=["POST"])
def save_attribution():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    meta = clean(request.get_json(silent=True) or {})
    if not meta:
        return jsonify({"saved": False, "reason": "empty"}), 200
    from pr_crm_routes import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""UPDATE users SET signup_meta = %s::jsonb
                       WHERE id = %s AND signup_meta IS NULL AND created_at > NOW() - INTERVAL '3 days'""",
                    (json.dumps(meta), user_id))
        saved = cur.rowcount > 0
        conn.commit()
        return jsonify({"saved": saved}), 200
    finally:
        conn.close()
