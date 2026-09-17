"""Brand attribution for creator profile / media-kit views.

Used by the gifted-PR roster (drawer open) and public kit ``?ref=`` links.
Tokens are deterministic per creator+brand so application-era views do not
need a ``creator_pipeline.kit_token`` row.
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime


def generate_kit_token(creator_id, brand_id):
    """Deterministic 12-char token for a creator/brand pair."""
    secret = os.getenv("SECRET_KEY", "fallback-secret-key-change-me")
    raw = f"{creator_id}-{brand_id}-{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def public_kit_url(kit_slug, creator_id, brand_id):
    slug = (kit_slug or "").strip().lstrip("/")
    if not slug or not creator_id or not brand_id:
        return ""
    token = generate_kit_token(creator_id, brand_id)
    base = os.getenv("PUBLIC_KIT_BASE_URL", "https://newcollab.co").rstrip("/")
    return f"{base}/kit/{slug}?ref={token}"


def _as_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return None


def resolve_brand_from_kit_ref(cursor, ref_token, creator_id=None):
    """Map a ``?ref=`` token to brand attribution.

    Prefers a stored ``creator_pipeline.kit_token`` (legacy pitches), then
    recomputes tokens for this creator's gifted-PR applications.
    """
    token = (ref_token or "").strip()
    if not token:
        return None

    cursor.execute(
        """
        SELECT cp.id AS pipeline_id, cp.creator_id, cp.brand_id,
               pb.brand_name, pb.category AS brand_category
        FROM creator_pipeline cp
        JOIN pr_brands pb ON pb.id = cp.brand_id
        WHERE cp.kit_token = %s
        """,
        (token,),
    )
    pipeline = _as_dict(cursor.fetchone())
    if pipeline:
        if creator_id and int(pipeline["creator_id"]) != int(creator_id):
            return None
        return pipeline

    if not creator_id:
        return None

    cursor.execute(
        """
        SELECT DISTINCT a.creator_id, a.brand_id,
               pb.brand_name, pb.category AS brand_category
        FROM brand_pr_applications a
        JOIN pr_brands pb ON pb.id = a.brand_id
        WHERE a.creator_id = %s
          AND a.status IS DISTINCT FROM 'hidden'
        """,
        (creator_id,),
    )
    for raw in cursor.fetchall() or []:
        row = _as_dict(raw) or {}
        cid = row.get("creator_id")
        bid = row.get("brand_id")
        if cid is None or bid is None:
            continue
        if generate_kit_token(cid, bid) == token:
            return {
                "pipeline_id": None,
                "creator_id": cid,
                "brand_id": bid,
                "brand_name": row.get("brand_name"),
                "brand_category": row.get("brand_category"),
            }
    return _resolve_from_creator_brands(cursor, token, creator_id)


def _token_match_row(token, creator_id, row, pipeline_id=None):
    row = _as_dict(row) or {}
    bid = row.get("brand_id")
    if bid is None:
        return None
    if generate_kit_token(creator_id, bid) != token:
        return None
    return {
        "pipeline_id": pipeline_id if pipeline_id is not None else row.get("pipeline_id"),
        "creator_id": creator_id,
        "brand_id": bid,
        "brand_name": row.get("brand_name"),
        "brand_category": row.get("brand_category"),
    }


def _resolve_from_creator_brands(cursor, token, creator_id):
    """Match a deterministic token against brands this creator already pitched."""
    queries = (
        """
        SELECT DISTINCT cp.id AS pipeline_id, cp.brand_id,
               pb.brand_name, pb.category AS brand_category
        FROM creator_pipeline cp
        JOIN pr_brands pb ON pb.id = cp.brand_id
        WHERE cp.creator_id = %s
        """,
        """
        SELECT DISTINCT t.brand_id, pb.brand_name, pb.category AS brand_category
        FROM polly_tasks t
        JOIN pr_brands pb ON pb.id = t.brand_id
        WHERE t.creator_id = %s AND t.brand_id IS NOT NULL
        """,
        """
        SELECT DISTINCT e.brand_id, pb.brand_name, pb.category AS brand_category
        FROM polly_timeline_events e
        JOIN pr_brands pb ON pb.id = e.brand_id
        WHERE e.creator_id = %s AND e.brand_id IS NOT NULL
        """,
    )
    for sql in queries:
        rows = _safe_fetchall(cursor, sql, (creator_id,))
        for raw in rows:
            hit = _token_match_row(token, creator_id, raw)
            if hit:
                return hit
    return None


def _safe_fetchall(cursor, sql, params):
    try:
        cursor.execute("SAVEPOINT polly_kit_ref")
        cursor.execute(sql, params)
        rows = list(cursor.fetchall() or [])
        cursor.execute("RELEASE SAVEPOINT polly_kit_ref")
        return rows
    except Exception:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT polly_kit_ref")
        except Exception:
            pass
        return []


def _hours_since(ts):
    if not ts:
        return 9999.0
    now = datetime.now(ts.tzinfo) if getattr(ts, "tzinfo", None) else datetime.now()
    try:
        return (now - ts).total_seconds() / 3600.0
    except Exception:
        return 9999.0


def _queue_brand_view_email(cursor, creator_id, brand_name, brand_category):
    cursor.execute(
        """
        SELECT c.username, c.subscription_tier, c.brand_view_email_sent_at,
               u.email, u.first_name
        FROM creators c
        JOIN users u ON c.user_id = u.id
        WHERE c.id = %s
        """,
        (creator_id,),
    )
    info = _as_dict(cursor.fetchone())
    if not info or not info.get("email"):
        return False

    if _hours_since(info.get("brand_view_email_sent_at")) < 1:
        print(
            f"[BRAND_VIEW_EMAIL] Skipping - sent "
            f"{int(_hours_since(info.get('brand_view_email_sent_at')) * 60)} mins ago"
        )
        return False

    tier = (info.get("subscription_tier") or "free").lower()
    is_pro = tier in ("pro", "elite")
    first = (info.get("first_name") or "").strip()
    creator_name = first.split()[0].capitalize() if first else (info.get("username") or "there")

    cursor.execute(
        "UPDATE creators SET brand_view_email_sent_at = NOW() WHERE id = %s",
        (creator_id,),
    )

    viewed_at = datetime.now()

    def send_async():
        try:
            from portfolio_routes import send_brand_view_notification

            send_brand_view_notification(
                to_email=info["email"],
                creator_name=creator_name,
                brand_name=brand_name,
                brand_category=brand_category,
                is_pro=is_pro,
                viewed_at=viewed_at,
            )
        except Exception as email_err:
            print(f"[BRAND_VIEW_EMAIL] Error: {email_err}")

    threading.Thread(target=send_async, daemon=True).start()
    print(
        f"[BRAND_VIEW_EMAIL] Queued for {info['email']} "
        f"(Pro: {is_pro}, Category: {brand_category})"
    )
    return True


def record_brand_profile_view(
    cursor,
    *,
    creator_id,
    brand_id,
    brand_name,
    brand_category=None,
    viewer_ip=None,
    referrer=None,
    pipeline_id=None,
    notify=True,
):
    """Insert or bump a branded ``kit_views`` row. Dedupe 24h per creator+brand.

    Returns ``{"recorded": bool, "emailed": bool}``.
    """
    if not creator_id or not brand_id:
        return {"recorded": False, "emailed": False}

    cursor.execute(
        """
        SELECT id FROM kit_views
        WHERE creator_id = %s AND brand_id = %s
          AND viewed_at > NOW() - INTERVAL '1 day'
        ORDER BY viewed_at DESC
        LIMIT 1
        """,
        (creator_id, brand_id),
    )
    existing = _as_dict(cursor.fetchone())
    if existing:
        cursor.execute(
            """
            UPDATE kit_views
            SET view_count = COALESCE(view_count, 1) + 1,
                viewed_at = NOW()
            WHERE id = %s
            """,
            (existing["id"],),
        )
        print(
            f"[KIT_VIEW] Duplicate within 24h, incremented: "
            f"creator={creator_id}, brand={brand_id}"
        )
        return {"recorded": False, "emailed": False}

    cursor.execute(
        """
        INSERT INTO kit_views (creator_id, brand_id, pipeline_id, viewer_ip, referrer, viewed_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """,
        (creator_id, brand_id, pipeline_id, viewer_ip, referrer),
    )
    print(
        f"[KIT_VIEW] Inserted kit_view: creator={creator_id}, "
        f"brand={brand_id}, brand_name={brand_name}"
    )

    _notify_polly_portfolio_view(cursor, creator_id, brand_id, brand_name)

    if pipeline_id:
        cursor.execute(
            """
            UPDATE creator_pipeline
            SET email_opened = true,
                email_opened_at = COALESCE(email_opened_at, NOW()),
                email_open_count = COALESCE(email_open_count, 0) + 1,
                updated_at = NOW()
            WHERE id = %s
            """,
            (pipeline_id,),
        )

    emailed = False
    if notify:
        try:
            emailed = bool(
                _queue_brand_view_email(cursor, creator_id, brand_name, brand_category)
            )
        except Exception as email_err:
            print(f"[BRAND_VIEW_EMAIL] Error: {email_err}")

    return {"recorded": True, "emailed": emailed}


def _notify_polly_portfolio_view(cursor, creator_id, brand_id, brand_name):
    """Write the view onto Polly's brand timeline and chat thread."""
    try:
        conn = getattr(cursor, "connection", None)
        if conn is None:
            return
        from services.polly_tracker import record_portfolio_viewed

        record_portfolio_viewed(
            conn,
            creator_id,
            brand_id,
            brand_name=brand_name,
            source="kit_ref",
        )
    except Exception as err:
        print(f"[KIT_VIEW] Polly tracker skip: {err}")
