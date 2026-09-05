# -*- coding: utf-8 -*-
"""
Brand PR Roster — magic-link portal for brands to pick creators, ship, and collect content.

Public routes are token-auth only (no brand login). Admin mint/revoke uses X-Admin-Token.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import threading
import traceback
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Response, jsonify, request, session
from psycopg2.extras import RealDictCursor, Json

from pr_crm_routes import get_db_connection, convert_decimals
from social_verification_routes import normalize_country_code
from services.roster_demand import fill_target, mark_focus

brand_pr_roster_bp = Blueprint("brand_pr_roster", __name__, url_prefix="/api/brand-pr")

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

DEFAULT_DEAL_CHIPS = [
    "Organic posts",
    "UGC files you own",
    "No platform fee",
]
DEFAULT_HEADLINE = "Pick creators. Ship product. Keep the content."
DEFAULT_LEDE = (
    "We already vetted these creators for your niche. Approve the ones you want. "
    "Skip anyone. Addresses stay hidden until you lock your picks."
)


def _admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_token = request.headers.get("X-Admin-Token")
        if admin_token == "pr-hunter-admin-2026":
            return f(*args, **kwargs)

        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            conn.close()
            if not user or (user.get("email") or "").lower() != "team@newcollab.co":
                return jsonify({"success": False, "error": "Admin access required"}), 403
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        return f(*args, **kwargs)

    return decorated


def _ensure_schema(cursor, conn=None):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_pr_campaigns (
                id SERIAL PRIMARY KEY,
                brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
                token VARCHAR(64) NOT NULL UNIQUE,
                title VARCHAR(255) NOT NULL,
                headline TEXT,
                lede TEXT,
                deal_chips JSONB NOT NULL DEFAULT '[]'::jsonb,
                slot_limit INTEGER NOT NULL DEFAULT 5,
                sku_note TEXT,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                selected_application_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                locked_at TIMESTAMPTZ,
                shipped_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_brand
            ON brand_pr_campaigns(brand_id, created_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_token
            ON brand_pr_campaigns(token)
            """
        )
        for stmt in (
            "ALTER TABLE brand_pr_applications ADD COLUMN IF NOT EXISTS campaign_id INTEGER",
            "ALTER TABLE brand_pr_applications ADD COLUMN IF NOT EXISTS declined_at TIMESTAMPTZ",
            "ALTER TABLE brand_pr_applications ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ",
        ):
            cursor.execute(stmt)
        # FK may already exist — ignore failures on re-run
        try:
            cursor.execute(
                """
                DO $$ BEGIN
                  ALTER TABLE brand_pr_applications
                    ADD CONSTRAINT brand_pr_applications_campaign_id_fkey
                    FOREIGN KEY (campaign_id) REFERENCES brand_pr_campaigns(id)
                    ON DELETE SET NULL;
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
        except Exception:
            pass
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_applications_campaign
            ON brand_pr_applications(campaign_id)
            WHERE campaign_id IS NOT NULL
            """
        )
        try:
            cursor.execute(
                "ALTER TABLE brand_pr_events ALTER COLUMN creator_id DROP NOT NULL"
            )
        except Exception:
            pass
        try:
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uniq_brand_pr_one_live_roster
                ON brand_pr_campaigns(brand_id)
                WHERE status IN ('active', 'locked')
                """
            )
        except Exception:
            pass
        if conn:
            conn.commit()
        _SCHEMA_READY = True


def _frontend_base():
    """Roster portal lives on the CRA app, not the marketing Next site."""
    raw = (
        os.getenv("FRONTEND_URL")
        or os.getenv("REACT_APP_FRONTEND_URL")
        or ""
    ).rstrip("/")
    marketing = {
        "https://newcollab.co",
        "https://www.newcollab.co",
        "http://newcollab.co",
        "http://www.newcollab.co",
    }
    if raw in marketing:
        return "https://app.newcollab.co"
    if raw in ("http://localhost:3000", "http://127.0.0.1:3000"):
        return "http://localhost:3001"
    if raw:
        return raw
    return "http://localhost:3001"


def _parse_json_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _parse_addr(raw):
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _selected_ids(campaign):
    ids = []
    for raw in _parse_json_list(campaign.get("selected_application_ids")):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    # de-dupe preserve order
    seen = set()
    out = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _save_selected(cursor, campaign_id, ids):
    cursor.execute(
        """
        UPDATE brand_pr_campaigns
        SET selected_application_ids = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (Json(ids), campaign_id),
    )


def _record_roster_event(cursor, event, brand_id=None, creator_id=None, meta=None):
    cursor.execute(
        """
        INSERT INTO brand_pr_events (creator_id, brand_id, event, source, meta)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (creator_id, brand_id, event, "brand_roster", Json(meta or {})),
    )


DEFAULT_SLOT_LIMIT = 5


def _insert_campaign(cursor, brand, slot_limit=DEFAULT_SLOT_LIMIT, title=None, headline=None, lede=None, sku_note=None, chips=None):
    slot_limit = max(1, min(int(slot_limit or DEFAULT_SLOT_LIMIT), 50))
    name = brand.get("brand_name") or "Brand"
    token = secrets.token_urlsafe(24)
    cursor.execute(
        """
        INSERT INTO brand_pr_campaigns (
            brand_id, token, title, headline, lede, deal_chips,
            slot_limit, sku_note, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')
        RETURNING *
        """,
        (
            brand["id"],
            token,
            (title or f"{name} · PR roster")[:255],
            (headline or DEFAULT_HEADLINE),
            (lede or DEFAULT_LEDE),
            Json(chips if isinstance(chips, list) and chips else list(DEFAULT_DEAL_CHIPS)),
            slot_limit,
            (sku_note or None),
        ),
    )
    campaign = cursor.fetchone()
    _attach_open_apps(cursor, campaign["id"], brand["id"])
    return campaign


def _attach_open_apps(cursor, campaign_id, brand_id):
    cursor.execute(
        """
        UPDATE brand_pr_applications
        SET campaign_id = %s
        WHERE brand_id = %s
          AND campaign_id IS NULL
          AND status IN ('review', 'ships', 'posted')
        """,
        (campaign_id, brand_id),
    )


def _review_fill_count(cursor, brand_id):
    cursor.execute(
        """
        SELECT COUNT(*)::int AS n
        FROM brand_pr_applications
        WHERE brand_id = %s AND status IN ('review', 'ships', 'posted')
        """,
        (brand_id,),
    )
    return int((cursor.fetchone() or {}).get("n") or 0)


def maybe_mint_roster_for_brand(cursor, brand_id, slot_limit=DEFAULT_SLOT_LIMIT):
    """Attach to a live roster, or mint once the list is worth sending."""
    from services.roster_demand import ROSTER_MINT_MIN
    _ensure_schema(cursor)
    cursor.execute(
        """
        SELECT c.*
        FROM brand_pr_campaigns c
        WHERE c.brand_id = %s AND c.status IN ('active', 'locked')
        ORDER BY CASE WHEN c.status = 'active' THEN 0 ELSE 1 END, c.created_at DESC
        LIMIT 1
        """,
        (brand_id,),
    )
    existing = cursor.fetchone()
    if existing:
        _attach_open_apps(cursor, existing["id"], brand_id)
        return existing, False
    if _review_fill_count(cursor, brand_id) < ROSTER_MINT_MIN:
        return None, False
    return ensure_active_roster_for_brand(cursor, brand_id, slot_limit=slot_limit)


def ensure_active_roster_for_brand(cursor, brand_id, slot_limit=DEFAULT_SLOT_LIMIT):
    """Mint an active roster if this brand has none in progress. Idempotent.

    Admin / threshold mint only. First applicant must not call this.
    """
    _ensure_schema(cursor)
    cursor.execute(
        """
        SELECT c.*
        FROM brand_pr_campaigns c
        WHERE c.brand_id = %s AND c.status IN ('active', 'locked')
        ORDER BY CASE WHEN c.status = 'active' THEN 0 ELSE 1 END, c.created_at DESC
        LIMIT 1
        """,
        (brand_id,),
    )
    existing = cursor.fetchone()
    if existing:
        _attach_open_apps(cursor, existing["id"], brand_id)
        return existing, False

    cursor.execute(
        "SELECT id, brand_name, slug FROM pr_brands WHERE id = %s",
        (brand_id,),
    )
    brand = cursor.fetchone()
    if not brand:
        return None, False

    try:
        campaign = _insert_campaign(cursor, brand, slot_limit=slot_limit)
    except Exception:
        cursor.execute(
            """
            SELECT * FROM brand_pr_campaigns
            WHERE brand_id = %s AND status IN ('active', 'locked')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (brand_id,),
        )
        raced = cursor.fetchone()
        if raced:
            return raced, False
        raise
    try:
        _record_roster_event(
            cursor,
            "roster_auto_minted",
            brand_id=brand_id,
            meta={"campaign_id": campaign["id"], "token": campaign.get("token")},
        )
    except Exception:
        pass
    return campaign, True


def backfill_waiting_rosters(cursor):
    """Manual only — do not call from schema init. Minting every waiting brand
    creates a flood of 1-applicant lists that never fill."""
    cursor.execute(
        """
        SELECT DISTINCT a.brand_id
        FROM brand_pr_applications a
        WHERE a.status IN ('review', 'ships', 'posted')
          AND NOT EXISTS (
            SELECT 1 FROM brand_pr_campaigns c
            WHERE c.brand_id = a.brand_id
              AND c.status IN ('active', 'locked')
          )
        LIMIT 50
        """
    )
    minted = 0
    for row in cursor.fetchall() or []:
        _, created = ensure_active_roster_for_brand(cursor, row["brand_id"])
        if created:
            minted += 1
    return minted


def _city_from_shipping(addr):
    addr = _parse_addr(addr)
    city = (addr.get("city") or "").strip()
    state = (addr.get("state") or addr.get("region") or "").strip()
    country = (addr.get("country") or "").strip()
    parts = [p for p in (city, state) if p]
    if parts:
        return ", ".join(parts)
    return country or ""


def _display_name(row):
    first = (row.get("first_name") or "").strip()
    if first:
        return first
    username = (row.get("username") or "").strip()
    if username:
        return username
    return "Creator"


def _handle(row):
    username = (row.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    return ""


_EXTRA_COUNTRY_ISO = {
    "mexico": "MX",
    "brazil": "BR",
    "india": "IN",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "singapore": "SG",
    "united arab emirates": "AE",
    "uae": "AE",
    "south africa": "ZA",
    "philippines": "PH",
    "indonesia": "ID",
    "malaysia": "MY",
    "thailand": "TH",
    "vietnam": "VN",
    "hong kong": "HK",
    "taiwan": "TW",
    "china": "CN",
    "colombia": "CO",
    "argentina": "AR",
    "chile": "CL",
    "peru": "PE",
}


def _country_code(name):
    raw = (name or "").strip()
    if not raw:
        return None
    code = normalize_country_code(raw)
    if code:
        return code.lower()
    extra = _EXTRA_COUNTRY_ISO.get(raw.lower())
    return extra.lower() if extra else None


def _norm_platform(value):
    p = str(value or "").strip().lower()
    if "tiktok" in p:
        return "tiktok"
    if "insta" in p:
        return "instagram"
    if "youtube" in p or p in ("yt", "google"):
        return "youtube"
    return ""


def _profile_url(platform, raw):
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    handle = value.lstrip("@")
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    return ""


def _socials_public(row):
    found = {}
    links = row.get("social_links")
    if isinstance(links, str):
        try:
            links = json.loads(links)
        except Exception:
            links = []
    if isinstance(links, dict):
        links = [links]
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            platform = _norm_platform(item.get("platform") or item.get("type"))
            url = _profile_url(platform, item.get("url") or item.get("handle") or item.get("username"))
            if not platform:
                url_hint = str(item.get("url") or "")
                platform = _norm_platform(url_hint)
                url = url or url_hint
            if platform and url:
                found[platform] = {"platform": platform, "url": url}

    platform = _norm_platform(row.get("social_platform"))
    handle = row.get("social_handle") or row.get("username")
    if platform and handle and platform not in found:
        url = _profile_url(platform, handle)
        if url:
            found[platform] = {"platform": platform, "url": url}

    for post in _posts_public(row.get("selected_posts")):
        url = post.get("post_url") or ""
        if "tiktok.com" in url and "tiktok" not in found:
            m = re.search(r"tiktok\.com/@([^/?]+)", url)
            if m:
                found["tiktok"] = {"platform": "tiktok", "url": f"https://www.tiktok.com/@{m.group(1)}"}
        elif "instagram.com" in url and "instagram" not in found:
            m = re.search(r"instagram\.com/([^/?]+)", url)
            if m and m.group(1) not in ("p", "reel", "reels", "stories"):
                found["instagram"] = {"platform": "instagram", "url": f"https://www.instagram.com/{m.group(1)}"}

    order = ["instagram", "tiktok", "youtube"]
    return [found[p] for p in order if p in found]


def _engagement(row):
    raw = row.get("engagement_rate")
    if raw in (None, ""):
        raw = row.get("avg_engagement_rate")
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None, None
    if n <= 0:
        return None, None
    if n > 100:
        n = 99.9
    label = f"{n:.1f}".rstrip("0").rstrip(".") + "%"
    return n, label


def _creator_country(row, addr):
    addr = addr or {}
    name = (addr.get("country") or row.get("user_country") or "").strip()
    return name, _country_code(name)


def _followers(row):
    try:
        n = int(row.get("followers_count") or 0)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def _fmt_followers(n):
    n = int(n or 0)
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        v = n / 1000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "K"
    if n > 0:
        return str(n)
    return None


def _niche_label(row):
    niches = row.get("niche")
    if isinstance(niches, list) and niches:
        return str(niches[0])
    if isinstance(niches, str) and niches.strip():
        try:
            parsed = json.loads(niches)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0])
        except Exception:
            pass
        return niches.strip().split(",")[0].strip()
    return ""


def _posts_public(raw):
    posts = _parse_json_list(raw)
    out = []
    for p in posts[:3]:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "post_url": p.get("post_url") or p.get("url") or "",
                "thumbnail_url": p.get("thumbnail_url") or p.get("thumb") or "",
            }
        )
    return out


def _load_campaign(cursor, token, *, allow_closed=False, skip_expiry=False):
    if not token or not _TOKEN_RE.match(token):
        return None
    return _load_campaign_row(
        cursor,
        "c.token = %s",
        (token,),
        allow_closed=allow_closed,
        skip_expiry=skip_expiry,
    )


def _load_campaign_by_id(cursor, campaign_id, *, allow_closed=True, skip_expiry=True):
    try:
        cid = int(campaign_id)
    except (TypeError, ValueError):
        return None
    return _load_campaign_row(
        cursor,
        "c.id = %s",
        (cid,),
        allow_closed=allow_closed,
        skip_expiry=skip_expiry,
    )


def _load_campaign_row(cursor, where_sql, params, *, allow_closed=False, skip_expiry=False):
    cursor.execute(
        f"""
        SELECT
            c.*,
            b.brand_name,
            b.logo_url,
            b.slug AS brand_slug,
            b.category AS brand_category,
            b.hero_product
        FROM brand_pr_campaigns c
        JOIN pr_brands b ON b.id = c.brand_id
        WHERE {where_sql}
        """,
        params,
    )
    campaign = cursor.fetchone()
    if not campaign:
        return None
    if not allow_closed and campaign.get("status") == "closed":
        return None
    if skip_expiry:
        return campaign
    expires = campaign.get("expires_at")
    if expires:
        now = datetime.now(timezone.utc)
        exp = expires if getattr(expires, "tzinfo", None) else expires.replace(tzinfo=timezone.utc)
        if exp < now:
            return None
    return campaign


def _fetch_applications(cursor, campaign):
    brand_id = campaign["brand_id"]
    campaign_id = campaign["id"]
    cursor.execute(
        """
        SELECT
            a.id AS application_id,
            a.creator_id,
            a.brand_id,
            a.status,
            a.selected_posts,
            a.shipping_address,
            a.applied_at,
            a.declined_at,
            a.shipped_at,
            a.campaign_id,
            c.username,
            c.image_profile,
            c.followers_count,
            c.niche,
            c.kit_slug,
            c.social_links,
            c.social_handle,
            c.social_platform,
            c.engagement_rate,
            c.avg_engagement_rate,
            u.first_name,
            u.country AS user_country
        FROM brand_pr_applications a
        JOIN creators c ON c.id = a.creator_id
        JOIN users u ON u.id = c.user_id
        WHERE a.brand_id = %s
          AND (a.campaign_id IS NULL OR a.campaign_id = %s)
          AND a.status IN ('review', 'ships', 'posted', 'declined')
        ORDER BY a.applied_at DESC
        """,
        (brand_id, campaign_id),
    )
    return cursor.fetchall() or []


def _card_from_row(row, *, reveal_shipping, selected_ids, campaign_status):
    app_id = int(row["application_id"])
    status = row.get("status") or "review"
    is_selected = app_id in selected_ids or (
        campaign_status in ("locked", "shipped") and status in ("ships", "posted")
    )
    addr = _parse_addr(row.get("shipping_address"))
    followers = _followers(row)
    avatar = row.get("image_profile") or ""
    engagement, engagement_label = _engagement(row)
    country, country_code = _creator_country(row, addr)
    card = {
        "application_id": app_id,
        "creator_id": row["creator_id"],
        "status": status,
        "selected": is_selected,
        "skipped": status == "declined",
        "name": _display_name(row),
        "handle": _handle(row),
        "avatar_url": avatar,
        "city": _city_from_shipping(addr) if (addr or not reveal_shipping) else "",
        "country": country,
        "country_code": country_code,
        "followers": followers,
        "followers_label": _fmt_followers(followers),
        "engagement": engagement,
        "engagement_label": engagement_label,
        "niche": _niche_label(row),
        "socials": _socials_public(row),
        "posts": _posts_public(row.get("selected_posts")),
        "applied_at": row.get("applied_at").isoformat() if row.get("applied_at") else None,
        "shipped_at": row.get("shipped_at").isoformat() if row.get("shipped_at") else None,
        "kit_slug": row.get("kit_slug") or "",
    }
    if reveal_shipping and is_selected and status != "declined":
        card["shipping_address"] = {
            "full_name": addr.get("full_name") or card["name"],
            "address_line1": addr.get("address_line1") or "",
            "address_line2": addr.get("address_line2") or "",
            "city": addr.get("city") or "",
            "state": addr.get("state") or addr.get("region") or "",
            "zip": addr.get("zip") or addr.get("postal_code") or "",
            "country": addr.get("country") or "",
            "phone": addr.get("phone") or "",
        }
    return card


def _campaign_public(campaign, cards):
    chips = _parse_json_list(campaign.get("deal_chips"))
    if not chips:
        chips = list(DEFAULT_DEAL_CHIPS)
    selected_ids = _selected_ids(campaign)
    # After lock, selected = ships/posted
    if campaign.get("status") in ("locked", "shipped"):
        selected_ids = [
            c["application_id"]
            for c in cards
            if c["status"] in ("ships", "posted")
        ]
    slot_limit = int(campaign.get("slot_limit") or 5)
    return convert_decimals(
        {
            "success": True,
            "campaign": {
                "id": campaign["id"],
                "token": campaign["token"],
                "title": campaign.get("title") or campaign.get("brand_name"),
                "headline": campaign.get("headline") or DEFAULT_HEADLINE,
                "lede": campaign.get("lede") or DEFAULT_LEDE,
                "deal_chips": chips,
                "slot_limit": slot_limit,
                "sku_note": campaign.get("sku_note") or "",
                "status": campaign.get("status"),
                "locked_at": campaign.get("locked_at").isoformat() if campaign.get("locked_at") else None,
                "shipped_at": campaign.get("shipped_at").isoformat() if campaign.get("shipped_at") else None,
                "brand": {
                    "id": campaign["brand_id"],
                    "name": campaign.get("brand_name"),
                    "logo": campaign.get("logo_url"),
                    "slug": campaign.get("brand_slug"),
                    "category": campaign.get("brand_category"),
                    "hero_product": campaign.get("hero_product"),
                },
                "selected_application_ids": selected_ids,
                "selected_count": len(selected_ids),
                "can_lock": campaign.get("status") == "active" and len(selected_ids) == slot_limit,
                "portal_url": f"{_frontend_base()}/r/{campaign['token']}",
            },
            "creators": cards,
        }
    )


def _build_roster_response(cursor, campaign):
    rows = _fetch_applications(cursor, campaign)
    reveal = campaign.get("status") in ("locked", "shipped")
    selected_ids = _selected_ids(campaign)
    cards = [
        _card_from_row(
            row,
            reveal_shipping=reveal,
            selected_ids=selected_ids,
            campaign_status=campaign.get("status"),
        )
        for row in rows
    ]
    return _campaign_public(campaign, cards)


# ---------------------------------------------------------------------------
# Public roster endpoints
# ---------------------------------------------------------------------------


@brand_pr_roster_bp.route("/r/<token>", methods=["GET"])
def get_roster(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        payload = _build_roster_response(cursor, campaign)
        _record_roster_event(
            cursor,
            "roster_view",
            brand_id=campaign["brand_id"],
            meta={"campaign_id": campaign["id"]},
        )
        conn.commit()
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/select", methods=["POST"])
def select_creator(token):
    try:
        data = request.get_json(silent=True) or {}
        app_id = int(data.get("application_id") or 0)
        if not app_id:
            return jsonify({"success": False, "error": "application_id required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") != "active":
            conn.close()
            return jsonify({"success": False, "error": "Picks are locked for this roster"}), 400

        cursor.execute(
            """
            SELECT id, status, creator_id FROM brand_pr_applications
            WHERE id = %s AND brand_id = %s
              AND (campaign_id IS NULL OR campaign_id = %s)
            """,
            (app_id, campaign["brand_id"], campaign["id"]),
        )
        app = cursor.fetchone()
        if not app:
            conn.close()
            return jsonify({"success": False, "error": "Application not found"}), 404
        if app["status"] == "declined":
            conn.close()
            return jsonify({"success": False, "error": "Unskip this creator before approving"}), 400
        if app["status"] not in ("review",):
            conn.close()
            return jsonify({"success": False, "error": "Creator is not available to pick"}), 400

        selected = _selected_ids(campaign)
        slot_limit = int(campaign.get("slot_limit") or 5)
        if app_id in selected:
            payload = _build_roster_response(cursor, campaign)
            conn.close()
            return jsonify(payload), 200
        if len(selected) >= slot_limit:
            conn.close()
            return jsonify({
                "success": False,
                "error": f"{slot_limit} already selected. Remove one to swap.",
            }), 400

        selected.append(app_id)
        _save_selected(cursor, campaign["id"], selected)
        _record_roster_event(
            cursor,
            "roster_select",
            brand_id=campaign["brand_id"],
            creator_id=app["creator_id"],
            meta={"campaign_id": campaign["id"], "application_id": app_id},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/deselect", methods=["POST"])
def deselect_creator(token):
    try:
        data = request.get_json(silent=True) or {}
        app_id = int(data.get("application_id") or 0)
        if not app_id:
            return jsonify({"success": False, "error": "application_id required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") != "active":
            conn.close()
            return jsonify({"success": False, "error": "Picks are locked for this roster"}), 400

        selected = [i for i in _selected_ids(campaign) if i != app_id]
        _save_selected(cursor, campaign["id"], selected)
        _record_roster_event(
            cursor,
            "roster_deselect",
            brand_id=campaign["brand_id"],
            meta={"campaign_id": campaign["id"], "application_id": app_id},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/skip", methods=["POST"])
def skip_creator(token):
    try:
        data = request.get_json(silent=True) or {}
        app_id = int(data.get("application_id") or 0)
        if not app_id:
            return jsonify({"success": False, "error": "application_id required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") != "active":
            conn.close()
            return jsonify({"success": False, "error": "Picks are locked for this roster"}), 400

        cursor.execute(
            """
            UPDATE brand_pr_applications
            SET status = 'declined', declined_at = NOW(), updated_at = NOW()
            WHERE id = %s AND brand_id = %s
              AND (campaign_id IS NULL OR campaign_id = %s)
              AND status IN ('review', 'declined')
            RETURNING id, creator_id
            """,
            (app_id, campaign["brand_id"], campaign["id"]),
        )
        app = cursor.fetchone()
        if not app:
            conn.close()
            return jsonify({"success": False, "error": "Application not found"}), 404

        selected = [i for i in _selected_ids(campaign) if i != app_id]
        _save_selected(cursor, campaign["id"], selected)
        _record_roster_event(
            cursor,
            "roster_skip",
            brand_id=campaign["brand_id"],
            creator_id=app["creator_id"],
            meta={"campaign_id": campaign["id"], "application_id": app_id},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/unskip", methods=["POST"])
def unskip_creator(token):
    try:
        data = request.get_json(silent=True) or {}
        app_id = int(data.get("application_id") or 0)
        if not app_id:
            return jsonify({"success": False, "error": "application_id required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") != "active":
            conn.close()
            return jsonify({"success": False, "error": "Picks are locked for this roster"}), 400

        cursor.execute(
            """
            UPDATE brand_pr_applications
            SET status = 'review', declined_at = NULL, updated_at = NOW()
            WHERE id = %s AND brand_id = %s
              AND (campaign_id IS NULL OR campaign_id = %s)
              AND status = 'declined'
            RETURNING id, creator_id
            """,
            (app_id, campaign["brand_id"], campaign["id"]),
        )
        app = cursor.fetchone()
        if not app:
            conn.close()
            return jsonify({"success": False, "error": "Application not found"}), 404

        _record_roster_event(
            cursor,
            "roster_unskip",
            brand_id=campaign["brand_id"],
            creator_id=app["creator_id"],
            meta={"campaign_id": campaign["id"], "application_id": app_id},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/lock", methods=["POST"])
def lock_roster(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") != "active":
            # Idempotent: already locked
            payload = _build_roster_response(cursor, campaign)
            conn.close()
            return jsonify(payload), 200

        selected = _selected_ids(campaign)
        slot_limit = int(campaign.get("slot_limit") or 5)
        if len(selected) != slot_limit:
            conn.close()
            return jsonify({
                "success": False,
                "error": f"Select exactly {slot_limit} creators before locking",
            }), 400

        # Validate all still review
        cursor.execute(
            """
            SELECT id, status FROM brand_pr_applications
            WHERE id = ANY(%s) AND brand_id = %s
            """,
            (selected, campaign["brand_id"]),
        )
        rows = cursor.fetchall() or []
        if len(rows) != slot_limit or any(r["status"] != "review" for r in rows):
            conn.close()
            return jsonify({
                "success": False,
                "error": "One or more picks are no longer available. Refresh and try again.",
            }), 400

        cursor.execute(
            """
            UPDATE brand_pr_applications
            SET status = 'ships',
                campaign_id = COALESCE(campaign_id, %s),
                updated_at = NOW()
            WHERE id = ANY(%s) AND brand_id = %s AND status = 'review'
            """,
            (campaign["id"], selected, campaign["brand_id"]),
        )
        cursor.execute(
            """
            UPDATE brand_pr_campaigns
            SET status = 'locked', locked_at = NOW(), updated_at = NOW()
            WHERE id = %s AND status = 'active'
            """,
            (campaign["id"],),
        )
        _record_roster_event(
            cursor,
            "roster_lock",
            brand_id=campaign["brand_id"],
            meta={"campaign_id": campaign["id"], "application_ids": selected},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/shipping.csv", methods=["GET"])
def shipping_csv(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") not in ("locked", "shipped"):
            conn.close()
            return jsonify({"success": False, "error": "Lock picks before exporting shipping"}), 400

        payload = _build_roster_response(cursor, campaign)
        conn.close()
        selected = [
            c for c in payload["creators"]
            if c.get("status") in ("ships", "posted") and c.get("shipping_address")
        ]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "creator_name", "handle", "full_name", "address_line1", "address_line2",
            "city", "state", "zip", "country", "phone", "sku_note",
        ])
        sku = campaign.get("sku_note") or ""
        for c in selected:
            ship = c.get("shipping_address") or {}
            writer.writerow([
                c.get("name") or "",
                c.get("handle") or "",
                ship.get("full_name") or "",
                ship.get("address_line1") or "",
                ship.get("address_line2") or "",
                ship.get("city") or "",
                ship.get("state") or "",
                ship.get("zip") or "",
                ship.get("country") or "",
                ship.get("phone") or "",
                sku,
            ])
        slug = (campaign.get("brand_slug") or "brand").replace("/", "-")
        filename = f"{slug}-pr-ship.csv"
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/mark-shipped", methods=["POST"])
def mark_shipped(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") not in ("locked", "shipped"):
            conn.close()
            return jsonify({"success": False, "error": "Lock picks before marking shipped"}), 400

        cursor.execute(
            """
            UPDATE brand_pr_applications
            SET shipped_at = COALESCE(shipped_at, NOW()), updated_at = NOW()
            WHERE brand_id = %s
              AND status = 'ships'
              AND (campaign_id = %s OR id = ANY(%s))
            """,
            (campaign["brand_id"], campaign["id"], _selected_ids(campaign)),
        )
        cursor.execute(
            """
            UPDATE brand_pr_campaigns
            SET status = 'shipped', shipped_at = COALESCE(shipped_at, NOW()), updated_at = NOW()
            WHERE id = %s
            """,
            (campaign["id"],),
        )
        _record_roster_event(
            cursor,
            "roster_shipped",
            brand_id=campaign["brand_id"],
            meta={"campaign_id": campaign["id"]},
        )
        conn.commit()
        campaign = _load_campaign(cursor, token)
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/r/<token>/content", methods=["GET"])
def content_inbox(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        if campaign.get("status") not in ("locked", "shipped"):
            conn.close()
            return jsonify({"success": False, "error": "Content unlocks after you lock and ship"}), 400

        payload = _build_roster_response(cursor, campaign)
        conn.close()
        inbox = []
        for c in payload["creators"]:
            if c.get("status") not in ("ships", "posted"):
                continue
            ready = c.get("status") == "posted"
            inbox.append({
                "application_id": c["application_id"],
                "name": c["name"],
                "handle": c["handle"],
                "avatar_url": c.get("avatar_url"),
                "status": c["status"],
                "ready": ready,
                "posts": c.get("posts") or [],
                "message": (
                    "Ready to download"
                    if ready
                    else "Usually 5–10 days after delivery"
                ),
                "shipped_at": c.get("shipped_at"),
            })
        return jsonify({
            "success": True,
            "campaign_status": payload["campaign"]["status"],
            "inbox": inbox,
        }), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Admin mint / list / revoke
# ---------------------------------------------------------------------------


@brand_pr_roster_bp.route("/admin/campaigns", methods=["POST"])
@_admin_required
def admin_create_campaign():
    """Mint a magic-link campaign. Also mounted conceptually under /api/admin via path."""
    try:
        data = request.get_json(silent=True) or {}
        brand_id = int(data.get("brand_id") or 0)
        if not brand_id:
            return jsonify({"success": False, "error": "brand_id required"}), 400

        slot_limit = int(data.get("slot_limit") or 5)
        slot_limit = max(1, min(slot_limit, 50))
        title = (data.get("title") or "").strip()
        headline = (data.get("headline") or "").strip() or DEFAULT_HEADLINE
        lede = (data.get("lede") or "").strip() or DEFAULT_LEDE
        sku_note = (data.get("sku_note") or "").strip()
        chips = data.get("deal_chips")
        if not isinstance(chips, list) or not chips:
            chips = list(DEFAULT_DEAL_CHIPS)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)

        cursor.execute(
            "SELECT id, brand_name, slug FROM pr_brands WHERE id = %s",
            (brand_id,),
        )
        brand = cursor.fetchone()
        if not brand:
            conn.close()
            return jsonify({"success": False, "error": "Brand not found"}), 404

        existing, created = ensure_active_roster_for_brand(cursor, brand_id, slot_limit=slot_limit)
        if existing and not created:
            conn.commit()
            conn.close()
            return jsonify({
                "success": True,
                "campaign": convert_decimals({
                    "id": existing["id"],
                    "brand_id": brand_id,
                    "brand_name": brand["brand_name"],
                    "token": existing["token"],
                    "title": existing["title"],
                    "slot_limit": existing["slot_limit"],
                    "status": existing["status"],
                    "portal_url": f"{_frontend_base()}/r/{existing['token']}",
                    "already_exists": True,
                }),
            }), 200

        # Just auto-minted with defaults — if admin passed a custom title/SKU, patch it.
        if existing and created and (title or sku_note):
            if not title:
                title = existing.get("title") or f"{brand['brand_name']} · PR roster"
            cursor.execute(
                """
                UPDATE brand_pr_campaigns
                SET title = %s, headline = %s, lede = %s, sku_note = %s,
                    slot_limit = %s, deal_chips = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    title[:255],
                    headline,
                    lede,
                    sku_note or None,
                    slot_limit,
                    Json(chips),
                    existing["id"],
                ),
            )
            existing = cursor.fetchone() or existing

        conn.commit()
        conn.close()
        token = existing["token"]
        return jsonify({
            "success": True,
            "campaign": convert_decimals({
                "id": existing["id"],
                "brand_id": brand_id,
                "brand_name": brand["brand_name"],
                "token": token,
                "title": existing["title"],
                "slot_limit": existing.get("slot_limit") or slot_limit,
                "status": existing["status"],
                "portal_url": f"{_frontend_base()}/r/{token}",
            }),
        }), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/admin/campaigns", methods=["GET"])
@_admin_required
def admin_list_campaigns():
    try:
        brand_id = request.args.get("brand_id")
        status = (request.args.get("status") or "").strip().lower()
        q = (request.args.get("q") or "").strip()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        clauses = []
        params = []
        if brand_id:
            clauses.append("c.brand_id = %s")
            params.append(int(brand_id))
        if status and status != "all":
            clauses.append("c.status = %s")
            params.append(status)
        if q:
            clauses.append("(b.brand_name ILIKE %s OR c.title ILIKE %s)")
            like = f"%{q}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor.execute(
            f"""
            SELECT
                c.id, c.brand_id, c.token, c.title, c.slot_limit, c.status, c.sku_note,
                c.selected_application_ids, c.created_at, c.locked_at, c.shipped_at,
                b.brand_name, b.slug AS brand_slug, b.logo_url,
                COUNT(a.id) FILTER (
                    WHERE a.status IN ('review', 'ships', 'posted', 'declined')
                ) AS applicant_count,
                COUNT(a.id) FILTER (WHERE a.status = 'review') AS review_count,
                COUNT(a.id) FILTER (WHERE a.status = 'declined') AS skipped_count,
                COUNT(a.id) FILTER (WHERE a.status IN ('ships', 'posted')) AS shipped_picks
            FROM brand_pr_campaigns c
            JOIN pr_brands b ON b.id = c.brand_id
            LEFT JOIN brand_pr_applications a
              ON a.brand_id = c.brand_id
             AND (a.campaign_id IS NULL OR a.campaign_id = c.id)
            {where}
            GROUP BY c.id, b.id
            ORDER BY c.created_at DESC
            LIMIT 200
            """,
            params,
        )
        rows = cursor.fetchall() or []
        conn.close()
        campaigns = []
        for r in rows:
            selected_ids = _parse_json_list(r.get("selected_application_ids"))
            selected_count = (
                int(r["shipped_picks"] or 0)
                if r.get("status") in ("locked", "shipped")
                else len([x for x in selected_ids if str(x).isdigit() or isinstance(x, int)])
            )
            target = fill_target(r.get("slot_limit"))
            fill_count = int(r["review_count"] or 0) + int(r.get("shipped_picks") or 0)
            hunger = max(0, target - fill_count) if r.get("status") == "active" else 0
            campaigns.append(convert_decimals({
                "id": r["id"],
                "brand_id": r["brand_id"],
                "brand_name": r["brand_name"],
                "brand_slug": r["brand_slug"],
                "logo_url": r.get("logo_url"),
                "token": r["token"],
                "title": r["title"],
                "slot_limit": r["slot_limit"],
                "sku_note": r.get("sku_note") or "",
                "status": r["status"],
                "applicant_count": int(r["applicant_count"] or 0),
                "review_count": int(r["review_count"] or 0),
                "skipped_count": int(r["skipped_count"] or 0),
                "selected_count": selected_count,
                "fill_count": fill_count,
                "fill_target": target,
                "hunger": hunger,
                "fill_ready": r.get("status") == "active" and hunger == 0,
                "in_focus": False,
                "portal_url": f"{_frontend_base()}/r/{r['token']}",
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "locked_at": r["locked_at"].isoformat() if r.get("locked_at") else None,
                "shipped_at": r["shipped_at"].isoformat() if r.get("shipped_at") else None,
            }))
        campaigns = mark_focus(campaigns)
        return jsonify({"success": True, "campaigns": campaigns}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/admin/campaigns/<int:campaign_id>", methods=["GET"])
@_admin_required
def admin_get_campaign(campaign_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        campaign = _load_campaign_by_id(cursor, campaign_id)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Campaign not found"}), 404
        payload = _build_roster_response(cursor, campaign)
        conn.close()
        return jsonify(payload), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/admin/queue", methods=["GET"])
@_admin_required
def admin_roster_queue():
    """Brands with review applications and no live roster — mint these next."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        cursor.execute(
            """
            SELECT
                b.id AS brand_id,
                b.brand_name,
                b.slug,
                b.logo_url,
                COUNT(*) AS review_count
            FROM brand_pr_applications a
            JOIN pr_brands b ON b.id = a.brand_id
            WHERE a.status = 'review'
              AND NOT EXISTS (
                SELECT 1 FROM brand_pr_campaigns c
                WHERE c.brand_id = b.id
                  AND c.status IN ('active', 'locked', 'shipped')
              )
            GROUP BY b.id
            ORDER BY review_count DESC, b.brand_name
            LIMIT 50
            """
        )
        rows = cursor.fetchall() or []
        conn.close()
        return jsonify({
            "success": True,
            "queue": [convert_decimals(dict(r)) for r in rows],
        }), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/admin/brands/search", methods=["GET"])
@_admin_required
def admin_search_pr_brands():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"success": True, "brands": []}), 200
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT id, brand_name, slug, logo_url
            FROM pr_brands
            WHERE brand_name ILIKE %s
            ORDER BY brand_name
            LIMIT 20
            """,
            (f"%{q}%",),
        )
        rows = cursor.fetchall() or []
        conn.close()
        return jsonify({"success": True, "brands": [dict(r) for r in rows]}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_pr_roster_bp.route("/admin/campaigns/<int:campaign_id>/revoke", methods=["POST"])
@_admin_required
def admin_revoke_campaign(campaign_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_schema(cursor, conn)
        cursor.execute(
            """
            UPDATE brand_pr_campaigns
            SET status = 'closed', updated_at = NOW()
            WHERE id = %s
            RETURNING id, token, brand_id
            """,
            (campaign_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Campaign not found"}), 404
        _record_roster_event(
            cursor,
            "roster_revoke",
            brand_id=row["brand_id"],
            meta={"campaign_id": campaign_id},
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": campaign_id, "status": "closed"}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# Alias blueprint matching plan paths: /api/admin/brand-pr/campaigns
admin_brand_pr_bp = Blueprint("admin_brand_pr", __name__, url_prefix="/api/admin/brand-pr")


@admin_brand_pr_bp.route("/campaigns", methods=["POST"])
@_admin_required
def admin_create_campaign_alias():
    return admin_create_campaign()


@admin_brand_pr_bp.route("/campaigns", methods=["GET"])
@_admin_required
def admin_list_campaigns_alias():
    return admin_list_campaigns()


@admin_brand_pr_bp.route("/campaigns/<int:campaign_id>", methods=["GET"])
@_admin_required
def admin_get_campaign_alias(campaign_id):
    return admin_get_campaign(campaign_id)


@admin_brand_pr_bp.route("/campaigns/<int:campaign_id>/revoke", methods=["POST"])
@_admin_required
def admin_revoke_campaign_alias(campaign_id):
    return admin_revoke_campaign(campaign_id)


@admin_brand_pr_bp.route("/queue", methods=["GET"])
@_admin_required
def admin_roster_queue_alias():
    return admin_roster_queue()


@admin_brand_pr_bp.route("/brands/search", methods=["GET"])
@_admin_required
def admin_search_pr_brands_alias():
    return admin_search_pr_brands()
