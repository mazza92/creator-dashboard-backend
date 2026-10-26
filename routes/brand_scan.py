"""Brand Scan API: public scan + poll + email unlock, and an admin warm endpoint for Hermes outreach."""
from __future__ import annotations

import time

from flask import Blueprint, current_app, jsonify, request

from routes.admin_email import admin_required
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
    # Plain tuple rows, the same connection Polly's radar uses (admin_email's returns dict rows).
    from pr_crm_routes import get_db_connection

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


FREE_SCAN_TTL = 30 * 86400


def _free_key(ip: str) -> str:
    return f"brand_scan:free:{ip}"


def _read_free(ip: str):
    """{key, name, scan_id} of this IP's free scan, or None. Older entries stored only the key."""
    import json

    r = _redis()
    if r is None or not ip:
        return None
    try:
        raw = r.get(_free_key(ip))
    except Exception:
        return None
    if not raw:
        return None
    raw = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"key": raw}
    except ValueError:
        return {"key": raw}


def free_scan_for_ip():
    """For signup: the free scan this request's IP ran on the landing page."""
    return _read_free(_ip())


def _free_scan_ok(ip: str, brand_key: str) -> bool:
    """Visitors get one free brand scan. Re-opening that same brand is fine. Fails open without Redis;
    the global daily $ cap still holds."""
    r = _redis()
    if r is None or not ip:
        return True
    used = _read_free(ip)
    return not used or used.get("key") == brand_key


def _mark_free_scan(ip: str, target: dict, scan_id) -> None:
    import json

    r = _redis()
    if r is None or not ip:
        return
    try:
        r.set(_free_key(ip), json.dumps({"key": target["key"], "name": target["name"], "scan_id": scan_id}),
              ex=FREE_SCAN_TTL, nx=True)
    except Exception:
        pass


def _viewer(conn):
    from routes.brand_workspace import viewer

    try:
        return viewer(conn)
    except Exception as err:
        conn.rollback()
        print(f"[brand-scan] viewer lookup skipped: {type(err).__name__}: {err}")
        return None


GATE_COPY = {
    "signup": "You've used your free Brand Scan. Try your first campaign free to keep scanning and see every creator.",
    "pending": "Your brand account is being reviewed (usually within 24h). You can scan more brands once it's approved.",
    "daily_limit": "You've scanned 3 brands today. Subscribe for unlimited scans, or come back tomorrow.",
}


def _gate(kind: str, target: dict, **extra):
    from services.brand_scan import signup_url

    return {"status": "gated", "gate": kind, "message": GATE_COPY[kind],
            "brand": {"key": target["key"], "name": target["name"]}, "cta_url": signup_url(), **extra}


@brand_scan_bp.route("/api/public/brand-scan", methods=["POST"])
def scan():
    if _limited():
        return jsonify({"success": False, "error": "Too many scans, try again in a minute."}), 429
    body = _body()
    ip = _ip()

    def go(conn):
        from services import brand_workspace as ws

        target = brand_scan.resolve(conn, body.get("brand"))
        if not target:
            return {"status": "invalid"}
        me = _viewer(conn)
        approved = bool(me and me["approved"])
        if approved:
            allow = ws.scan_allowance(conn, me["brand"]["id"], target["key"], me["subscribed"])
            if not allow["allowed"]:
                return _gate("daily_limit", target, limit=allow["limit"], used=allow["used"])
        elif me:
            # Signed up, awaiting approval: one competitor scan (their landing-page one carries over), unlocked.
            if not ws.pending_scan_allowed(conn, me["brand"]["id"], target["key"]):
                return _gate("pending", target)
        elif not _free_scan_ok(ip, target["key"]):
            return _gate("signup", target)
        out = brand_scan.start(conn, body.get("brand"), can_start=_can_start(ip), redis_client=_redis(),
                               ref=str(body.get("ref") or ""), locked=not me)
        if out.get("status") in ("ready", "empty", "searching"):
            if me:
                ws.record_scan(conn, me["brand"]["id"], target["key"], target["name"], out.get("scan_id"))
            else:
                _mark_free_scan(ip, target, out.get("scan_id"))
        return out

    try:
        out = _run(go)
    except Exception as err:
        print(f"[brand-scan] scan failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not run the scan."}), 500
    if out.get("started"):
        _count_start(ip)
    return _reply(out)


@brand_scan_bp.route("/api/public/brand-scan/<int:scan_id>", methods=["GET"])
def scan_poll(scan_id):
    def go(conn):
        me = _viewer(conn)
        return brand_scan.poll(conn, scan_id, redis_client=_redis(), locked=not me)

    try:
        out = _run(go)
    except Exception as err:
        print(f"[brand-scan] poll failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not load the scan."}), 500
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
