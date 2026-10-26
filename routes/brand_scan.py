"""Brand Scan API: public scan + poll + email unlock, and an admin warm endpoint for Hermes outreach."""
from __future__ import annotations

import time

from flask import Blueprint, current_app, jsonify, request

from routes.admin_email import admin_required, get_db_connection
from services import brand_scan

brand_scan_bp = Blueprint("brand_scan", __name__)

PER_MINUTE = 20
STARTS_PER_DAY = 3
MAX_WARM = 10


def _redis():
    return current_app.config.get("SESSION_REDIS")


def _ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or request.headers.get("X-Real-IP") or request.remote_addr or ""


def _body():
    return request.get_json(silent=True) or {}


def _starts_key(ip: str) -> str:
    return f"brand_scan:starts:{ip}:{time.strftime('%Y%m%d', time.gmtime())}"


def _can_start(ip: str) -> bool:
    """3 paid TikTok searches per IP per day. Fails open if Redis is down; the daily $ cap still holds."""
    r = _redis()
    if r is None or not ip:
        return True
    try:
        return int(r.get(_starts_key(ip)) or 0) < STARTS_PER_DAY
    except Exception:
        return True


def _count_start(ip: str) -> None:
    r = _redis()
    if r is None or not ip:
        return
    try:
        key = _starts_key(ip)
        r.incr(key)
        r.expire(key, 90_000)
    except Exception:
        pass


def _run(fn):
    conn = get_db_connection()
    try:
        return fn(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _limited() -> bool:
    """Fixed one-minute window per IP, separate from the creator search counter. Fails open."""
    r, ip = _redis(), _ip()
    if r is None or not ip:
        return False
    key = f"brand_scan:rl:{ip}:{int(time.time() // 60)}"
    try:
        count = r.incr(key)
        if count == 1:
            r.expire(key, 70)
        return int(count) > PER_MINUTE
    except Exception:
        return False


def _reply(out):
    out = dict(out or {})
    out.pop("started", None)
    if out.get("status") == "invalid":
        return jsonify({"success": False, "error": "Type a brand name, @handle or website."}), 400
    if out.get("status") == "not_found":
        return jsonify({"success": False, "error": "Scan not found."}), 404
    return jsonify({"success": True, "scan": out})


@brand_scan_bp.route("/api/public/brand-scan", methods=["POST"])
def scan():
    if _limited():
        return jsonify({"success": False, "error": "Too many scans, try again in a minute."}), 429
    body = _body()
    ip = _ip()
    try:
        out = _run(lambda c: brand_scan.start(c, body.get("brand"), can_start=_can_start(ip),
                                              redis_client=_redis(), ref=str(body.get("ref") or "")))
    except Exception as err:
        print(f"[brand-scan] scan failed: {err}")
        return jsonify({"success": False, "error": "Could not run the scan."}), 500
    if out.get("started"):
        _count_start(ip)
    return _reply(out)


@brand_scan_bp.route("/api/public/brand-scan/<int:scan_id>", methods=["GET"])
def scan_poll(scan_id):
    try:
        out = _run(lambda c: brand_scan.poll(c, scan_id, redis_client=_redis()))
    except Exception as err:
        print(f"[brand-scan] poll failed: {err}")
        return jsonify({"success": False, "error": "Could not load the scan."}), 500
    return _reply(out)


@brand_scan_bp.route("/api/public/brand-scan/unlock", methods=["POST"])
def scan_unlock():
    if _limited():
        return jsonify({"success": False, "error": "Too many requests, try again in a minute."}), 429
    body = _body()
    email = brand_scan.clean_email(body.get("email"))
    brand = str(body.get("brand") or "").strip()
    if not email:
        return jsonify({"success": False, "error": "Enter a valid work email."}), 400
    if not brand:
        return jsonify({"success": False, "error": "Missing brand."}), 400
    website, ref = str(body.get("website") or ""), str(body.get("ref") or "")

    def go(conn):
        report = brand_scan.full_report(conn, brand, redis_client=_redis())
        if report.get("status") != "invalid":
            brand_scan.save_lead(conn, email, brand, website, ref, _ip())
        return report

    try:
        out = _run(go)
    except Exception as err:
        print(f"[brand-scan] unlock failed: {err}")
        return jsonify({"success": False, "error": "Could not unlock the report."}), 500
    if out.get("status") != "invalid":
        brand_scan.notify_lead(email, brand, website, ref, out)
    return _reply(out)


@brand_scan_bp.route("/api/admin/brand-scan/warm", methods=["POST"])
@admin_required
def scan_warm():
    """Hermes pre-runs scans before sending, so the link in the email opens instantly."""
    brands = [str(b).strip() for b in (_body().get("brands") or []) if str(b).strip()][:MAX_WARM]
    if not brands:
        return jsonify({"success": False, "error": "brands is required"}), 400
    results = []
    for name in brands:
        try:
            out = _run(lambda c: brand_scan.start(c, name, redis_client=_redis(), ref="hermes"))
        except Exception as err:
            print(f"[brand-scan] warm failed for {name}: {err}")
            results.append({"brand": name, "status": "error"})
            continue
        results.append({
            "brand": name,
            "status": out.get("status"),
            "scan_id": out.get("scan_id"),
            "videos": (out.get("stats") or {}).get("videos"),
            "creators": (out.get("stats") or {}).get("creators"),
            "top_hook": ((out.get("hooks") or [{}])[0]).get("hook"),
            "summary": out.get("summary"),
        })
    return jsonify({"success": True, "results": results})
