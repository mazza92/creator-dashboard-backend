"""
Creator Apply for Brand PR — roster apply, no cold email to the brand.

Reuses attempt_unlock() for the 3/month quota. Status lives on
brand_pr_applications, not the email-pitch pipeline.
"""

from flask import Blueprint, request, jsonify
from psycopg2.extras import RealDictCursor, Json
import json
import re
import threading
import traceback

from pr_crm_routes import (
    get_creator_id_from_session,
    get_db_connection,
    attempt_unlock,
    get_creator_unlock_balance,
    convert_decimals,
)

brand_apply_bp = Blueprint("brand_apply", __name__, url_prefix="/api/pr-crm")

_TIKTOK_ID_RE = re.compile(r"(?:video|player/v1)/(\d{8,})")
_IG_CODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def _ensure_schema(cursor, conn=None):
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_pr_applications (
                id SERIAL PRIMARY KEY,
                creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
                status VARCHAR(32) NOT NULL DEFAULT 'review',
                selected_posts JSONB NOT NULL DEFAULT '[]'::jsonb,
                shipping_address JSONB,
                agreed_at TIMESTAMPTZ,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (creator_id, brand_id)
            )
            """
        )
        cursor.execute(
            """
            ALTER TABLE pr_brands
            ADD COLUMN IF NOT EXISTS pr_example_posts JSONB
            """
        )
        cursor.execute(
            """
            ALTER TABLE pr_brands
            ADD COLUMN IF NOT EXISTS pr_social_profile JSONB
            """
        )
        cursor.execute(
            """
            ALTER TABLE creators
            ADD COLUMN IF NOT EXISTS shipping_address JSONB
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_applications_creator
            ON brand_pr_applications(creator_id, applied_at DESC)
            """
        )
        cursor.execute(
            """
            ALTER TABLE brand_pr_applications
            ADD COLUMN IF NOT EXISTS source VARCHAR(32)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_pr_events (
                id SERIAL PRIMARY KEY,
                creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                brand_id INTEGER,
                event VARCHAR(64) NOT NULL,
                source VARCHAR(32),
                meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_events_event
            ON brand_pr_events(event, created_at DESC)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_pr_events_creator
            ON brand_pr_events(creator_id, created_at DESC)
            """
        )
        if conn:
            conn.commit()
        _SCHEMA_READY = True


_CLIENT_EVENTS = frozenset({
    "apply_home_view",
    "apply_opened",
    "apply_paywall",
    "apply_related_click",
})
_SOURCES = frozenset({
    "foryou",
    "directory",
    "related",
    "deeplink",
    "credits_chip",
    "meter",
    "card_credits",
    "done_last_credit",
    "submit_402",
    "ugc_jobs",
})


def _clean_source(raw):
    value = str(raw or "").strip().lower()[:32]
    return value if value in _SOURCES else (value or None)


def _record_event(cursor, creator_id, event, brand_id=None, source=None, meta=None):
    cursor.execute(
        """
        INSERT INTO brand_pr_events (creator_id, brand_id, event, source, meta)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (creator_id, brand_id, event, _clean_source(source), Json(meta or {})),
    )


def _parse_addr(raw):
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _tiktok_id(url):
    if not url:
        return None
    m = _TIKTOK_ID_RE.search(str(url))
    return m.group(1) if m else None


def _ig_code(url):
    if not url:
        return None
    m = _IG_CODE_RE.search(str(url))
    return m.group(1) if m else None


def _proxy_thumb(url):
    if not url:
        return ""
    try:
        from media_proxy_routes import to_proxied_media_url

        return to_proxied_media_url(url) or url
    except Exception:
        return url


def _normalize_examples(raw, default_handle=None):
    items = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return items
    for item in raw[:6]:
        if isinstance(item, str):
            url = item
            title = "Previous PR"
            handle = default_handle or ""
            thumb = ""
            platform = ""
            raw_id = None
        elif isinstance(item, dict):
            url = item.get("url") or item.get("post_url") or ""
            title = item.get("title") or "Previous PR"
            handle = item.get("handle") or default_handle or ""
            thumb = item.get("thumbnail_url") or item.get("thumb") or ""
            platform = (item.get("platform") or "").lower()
            raw_id = item.get("id")
        else:
            continue
        tiktok_id = None
        if str(raw_id or "").isdigit() and len(str(raw_id)) >= 8:
            tiktok_id = str(raw_id)
        tiktok_id = tiktok_id or _tiktok_id(url)
        ig_id = None
        if not tiktok_id:
            if raw_id and not str(raw_id).isdigit():
                ig_id = str(raw_id)
            else:
                ig_id = _ig_code(url)
        if tiktok_id:
            platform = "tiktok"
            url = url or f"https://www.tiktok.com/player/v1/{tiktok_id}"
            example_id = tiktok_id
        elif url or ig_id:
            platform = platform or "instagram"
            example_id = str(ig_id or raw_id or url)
            if not url and ig_id:
                url = f"https://www.instagram.com/p/{ig_id}/"
        else:
            continue
        items.append(
            {
                "id": example_id,
                "platform": platform,
                "url": url,
                "thumbnail_url": _proxy_thumb(thumb),
                "title": title,
                "handle": handle or "",
            }
        )
    return items


def _parse_social(raw):
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        followers = int(raw.get("followers") or 0)
    except (TypeError, ValueError):
        followers = 0
    handle = str(raw.get("handle") or "").lstrip("@").strip()
    bio = str(raw.get("bio") or "").strip()
    if followers <= 0 and not handle and not bio:
        return None
    return {
        "platform": str(raw.get("platform") or "").lower()[:16],
        "handle": handle,
        "nickname": str(raw.get("nickname") or "")[:80],
        "followers": followers,
        "following": _safe_int(raw.get("following")),
        "likes": _safe_int(raw.get("likes")),
        "posts": _safe_int(raw.get("posts")),
        "bio": bio[:280],
        "verified": bool(raw.get("verified")),
        "avatar_url": str(raw.get("avatar_url") or raw.get("avatarUrl") or "")[:500],
    }


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_social_handles(brand):
    return bool(
        str(brand.get("tiktok_handle") or brand.get("tiktok") or "").strip()
        or str(brand.get("instagram_handle") or brand.get("instagram") or "").strip()
    )


def _media_pending(brand, examples, social):
    if not _has_social_handles(brand):
        return False
    has_examples = bool(examples)
    has_stats = bool(social and _safe_int(social.get("followers")) > 0)
    return not has_examples or not has_stats


_ENRICH_LOCKS = {}
_ENRICH_LOCKS_GUARD = threading.Lock()


def _enrich_lock(brand_id):
    with _ENRICH_LOCKS_GUARD:
        lock = _ENRICH_LOCKS.get(brand_id)
        if lock is None:
            lock = threading.Lock()
            _ENRICH_LOCKS[brand_id] = lock
        return lock


def _enrich_brand_media(brand):
    """Scrape examples + social stats and persist. Own DB connection. Never invents counts."""
    brand_id = brand.get("id")
    if not brand_id:
        return [], None
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    from services.pr_example_enricher import (
        collect_examples_and_social,
        collect_social_overview,
        social_overview_usable,
    )
    from psycopg2.extras import Json as PgJson

    examples = _normalize_examples(
        brand.get("pr_example_posts"),
        brand.get("tiktok_handle") or brand.get("instagram_handle"),
    )
    social = _parse_social(brand.get("pr_social_profile"))
    need_examples = not examples
    need_social = not social or not (social.get("followers") or 0)
    if not need_examples and not need_social:
        return examples, social

    scraped_examples = []
    scraped_social = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            if need_examples:
                fut = pool.submit(
                    collect_examples_and_social,
                    brand.get("instagram_handle"),
                    brand.get("tiktok_handle"),
                    3,
                )
            else:
                fut = pool.submit(
                    collect_social_overview,
                    brand.get("tiktok_handle"),
                    brand.get("instagram_handle"),
                )
            try:
                scraped = fut.result(timeout=8)
            except FuturesTimeout:
                scraped = ([], None) if need_examples else None
        if need_examples:
            scraped_examples, scraped_social = scraped or ([], None)
        else:
            scraped_social = scraped
    except Exception as enrich_err:
        print(f"[brand-apply] media enrich scrape skipped: {enrich_err}")
        return examples, social

    parsed_social = _parse_social(scraped_social)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if scraped_examples:
            cursor.execute(
                "UPDATE pr_brands SET pr_example_posts = %s WHERE id = %s",
                (PgJson(scraped_examples), brand_id),
            )
            examples = _normalize_examples(
                scraped_examples,
                brand.get("tiktok_handle") or brand.get("instagram_handle"),
            )
        if parsed_social and (social_overview_usable(parsed_social) or parsed_social.get("bio")):
            cursor.execute(
                "UPDATE pr_brands SET pr_social_profile = %s WHERE id = %s",
                (PgJson(parsed_social), brand_id),
            )
            social = parsed_social
        if scraped_examples or parsed_social:
            conn.commit()
    except Exception as save_err:
        print(f"[brand-apply] media enrich save skipped: {save_err}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cursor.close()
        conn.close()
    return examples, social


def _brand_card(row):
    if not row:
        return None
    value = row.get("avg_product_value") or row.get("price_point")
    try:
        value = int(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    regions = row.get("regions") or []
    if isinstance(regions, str):
        try:
            regions = json.loads(regions)
        except Exception:
            regions = [regions] if regions else []
    platforms = row.get("platforms") or []
    if isinstance(platforms, str):
        try:
            platforms = json.loads(platforms)
        except Exception:
            platforms = [platforms] if platforms else []
    niches = row.get("niches") or []
    if isinstance(niches, str):
        try:
            niches = json.loads(niches)
        except Exception:
            niches = [niches] if niches else []
    return convert_decimals(
        {
            "id": row.get("id"),
            "slug": row.get("slug"),
            "name": row.get("brand_name") or row.get("name"),
            "logo": row.get("logo_url") or row.get("logo"),
            "cover": row.get("cover_image_url") or row.get("coverImage") or row.get("cover"),
            "website": row.get("website"),
            "category": row.get("category"),
            "description": row.get("description"),
            "hero_product": row.get("hero_product"),
            "min_followers": row.get("min_followers") or 0,
            "micro_friendly": bool(row.get("micro_friendly")),
            "regions": regions if isinstance(regions, list) else [],
            "platforms": platforms if isinstance(platforms, list) else [],
            "niches": niches if isinstance(niches, list) else [],
            "collaboration_type": row.get("collaboration_type") or "gifted",
            "estimated_value": value,
            "match_score": row.get("match_score"),
            "has_application_form": bool(row.get("has_application_form")),
            "has_email_contact": bool(row.get("has_email_contact")),
            "response_rate": row.get("response_rate"),
            "avg_response_time": row.get("avg_response_time_days") or row.get("avg_response_time"),
            "instagram": row.get("instagram_handle") or row.get("instagram"),
            "tiktok": row.get("tiktok_handle") or row.get("tiktok"),
            "social": _parse_social(row.get("pr_social_profile") or row.get("social")),
        }
    )


@brand_apply_bp.route("/applications", methods=["GET"])
def list_applications():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_schema(cursor, conn)
        cursor.execute(
            """
            SELECT
                a.id AS application_id,
                a.brand_id,
                a.status,
                a.source,
                a.applied_at,
                a.selected_posts,
                b.slug, b.brand_name, b.logo_url, b.category, b.description,
                b.hero_product, b.min_followers, b.micro_friendly, b.regions,
                b.platforms, b.collaboration_type, b.avg_product_value,
                b.has_application_form, b.response_rate, b.price_point,
                b.application_form_url,
                (b.contact_email IS NOT NULL AND TRIM(b.contact_email) != '') AS has_email_contact
            FROM brand_pr_applications a
            JOIN pr_brands b ON b.id = a.brand_id
            WHERE a.creator_id = %s
            ORDER BY a.applied_at DESC
            """,
            (creator_id,),
        )
        rows = cursor.fetchall() or []
        apps = []
        for row in rows:
            card = _brand_card(row)
            card["application_id"] = row["application_id"]
            card["apply_status"] = row["status"] or "review"
            card["applied_at"] = row["applied_at"].isoformat() if row.get("applied_at") else None
            card["source"] = row.get("source")
            apps.append(card)
        return jsonify({"success": True, "applications": apps})
    except Exception as e:
        print(f"[brand-apply] list error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@brand_apply_bp.route("/apply-pack/<int:brand_id>", methods=["GET"])
def get_apply_pack(brand_id):
    """Brand examples + creator posts + saved shipping, for the 3-step apply modal."""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_schema(cursor, conn)

        cursor.execute(
            """
            SELECT id, slug, brand_name, logo_url, cover_image_url, website, category,
                   description, hero_product, niches, min_followers, micro_friendly,
                   regions, platforms, collaboration_type, avg_product_value, price_point,
                   has_application_form, response_rate, avg_response_time_days,
                   pr_example_posts, pr_social_profile, application_form_url, instagram_handle, tiktok_handle,
                   (contact_email IS NOT NULL AND TRIM(contact_email) != '') AS has_email_contact
            FROM pr_brands
            WHERE id = %s AND COALESCE(status, 'published') = 'published'
            """,
            (brand_id,),
        )
        brand = cursor.fetchone()
        if not brand:
            return jsonify({"success": False, "error": "Brand not found"}), 404

        cursor.execute(
            "SELECT shipping_address FROM creators WHERE id = %s",
            (creator_id,),
        )
        ship_row = cursor.fetchone() or {}
        shipping = _parse_addr(ship_row.get("shipping_address"))

        cursor.execute(
            """
            SELECT status FROM brand_pr_applications
            WHERE creator_id = %s AND brand_id = %s
            """,
            (creator_id, brand_id),
        )
        existing = cursor.fetchone()

        from pr_crm_routes import _live_pitch_posts

        cursor.execute("SELECT user_id FROM creators WHERE id = %s", (creator_id,))
        user_row = cursor.fetchone() or {}
        posts = _live_pitch_posts(cursor, user_row.get("user_id"))

        examples = _normalize_examples(
            brand.get("pr_example_posts"),
            brand.get("tiktok_handle") or brand.get("instagram_handle"),
        )
        social = _parse_social(brand.get("pr_social_profile"))
        media_pending = _media_pending(brand, examples, social)

        card = _brand_card(brand)
        if card is not None and social:
            card["social"] = social

        return jsonify(
            {
                "success": True,
                "brand": card,
                "social": social,
                "examples": examples,
                "posts": posts or [],
                "shipping": shipping,
                "already_applied": bool(existing),
                "apply_status": existing["status"] if existing else None,
                "media_pending": media_pending,
            }
        )
    except Exception as e:
        print(f"[brand-apply] pack error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@brand_apply_bp.route("/apply-pack/<int:brand_id>/media", methods=["GET"])
def get_apply_pack_media(brand_id):
    """Slow path: scrape + cache last-PR examples and social stats. Step 1 does not wait on this."""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    with _enrich_lock(brand_id):
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            _ensure_schema(cursor, conn)
            cursor.execute(
                """
                SELECT id, instagram_handle, tiktok_handle, pr_example_posts, pr_social_profile
                FROM pr_brands
                WHERE id = %s AND COALESCE(status, 'published') = 'published'
                """,
                (brand_id,),
            )
            brand = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()
        if not brand:
            return jsonify({"success": False, "error": "Brand not found"}), 404
        examples, social = _enrich_brand_media(dict(brand))

    return jsonify(
        {
            "success": True,
            "examples": examples or [],
            "social": social,
            "media_pending": False,
        }
    )


@brand_apply_bp.route("/brands/<int:brand_id>/apply", methods=["POST"])
def submit_apply(brand_id):
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    payload = request.get_json() or {}
    posts = payload.get("selected_posts") or []
    shipping = payload.get("shipping") or {}
    agreed = bool(payload.get("agreed"))
    save_shipping = payload.get("save_shipping", True)

    if not agreed:
        return jsonify({"success": False, "error": "Agree to the gifted terms and 6-month UGC usage to apply."}), 400
    if not isinstance(posts, list) or len(posts) != 3:
        return jsonify({"success": False, "error": "Pick 3 posts."}), 400

    full_name = (shipping.get("full_name") or "").strip()
    line1 = (shipping.get("address_line1") or "").strip()
    zip_code = (shipping.get("zip") or shipping.get("postal_code") or "").strip()
    city = (shipping.get("city") or "").strip()
    state = (shipping.get("state") or shipping.get("region") or "").strip()
    country = (shipping.get("country") or "").strip()
    phone = (shipping.get("phone") or "").strip()
    phone_digits = "".join(ch for ch in phone if ch.isdigit())

    if len(full_name) < 2 or not line1 or not zip_code or not city or not state or not country:
        return jsonify({"success": False, "error": "Add a complete shipping address."}), 400
    if len(phone_digits) < 8:
        return jsonify({"success": False, "error": "Add a phone number so the courier can reach you."}), 400

    from social_verification_routes import country_value_is_restricted

    if country_value_is_restricted(country):
        return jsonify({"success": False, "error": "We can only ship to countries Newcollab serves."}), 400

    selected = []
    for p in posts[:3]:
        if isinstance(p, str):
            selected.append({"post_url": p})
        elif isinstance(p, dict) and (p.get("post_url") or p.get("url")):
            selected.append(
                {
                    "post_url": p.get("post_url") or p.get("url"),
                    "thumbnail_url": p.get("thumbnail_url") or "",
                }
            )
    if len(selected) != 3:
        return jsonify({"success": False, "error": "Pick 3 posts."}), 400

    addr = {
        "full_name": full_name[:120],
        "address_line1": line1[:200],
        "address_line2": (shipping.get("address_line2") or "").strip()[:200],
        "city": city[:80],
        "state": state[:80],
        "zip": zip_code[:20],
        "country": country[:80],
        "phone": phone[:40],
    }

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_schema(cursor, conn)

        cursor.execute(
            """
            SELECT id FROM brand_pr_applications
            WHERE creator_id = %s AND brand_id = %s
            """,
            (creator_id, brand_id),
        )
        if cursor.fetchone():
            return jsonify({"success": False, "error": "Already applied", "already_applied": True}), 409

        cursor.execute(
            "SELECT id FROM pr_brands WHERE id = %s AND COALESCE(status, 'published') = 'published'",
            (brand_id,),
        )
        if not cursor.fetchone():
            return jsonify({"success": False, "error": "Brand not found"}), 404

        unlock = attempt_unlock(creator_id, brand_id, conn=conn)
        if unlock.get("status") == "paywall":
            _record_event(
                cursor,
                creator_id,
                "apply_paywall",
                brand_id=brand_id,
                source=payload.get("source"),
                meta={"reason": "submit_402"},
            )
            conn.commit()
            return (
                jsonify(
                    {
                        "success": False,
                        "paywall": True,
                        "error": "No credits left this month",
                        "remaining": 0,
                        "reset_at": unlock.get("reset_at"),
                    }
                ),
                402,
            )
        if unlock.get("status") == "error":
            return jsonify({"success": False, "error": unlock.get("error") or "Unlock failed"}), 400

        cursor.execute(
            """
            INSERT INTO brand_pr_applications
                (creator_id, brand_id, status, selected_posts, shipping_address, source, agreed_at, applied_at, updated_at)
            VALUES (%s, %s, 'review', %s, %s, %s, NOW(), NOW(), NOW())
            RETURNING id, status, applied_at
            """,
            (creator_id, brand_id, Json(selected), Json(addr), _clean_source(payload.get("source"))),
        )
        app_row = cursor.fetchone()

        if save_shipping:
            cursor.execute(
                "SELECT shipping_address FROM creators WHERE id = %s",
                (creator_id,),
            )
            existing = _parse_addr((cursor.fetchone() or {}).get("shipping_address"))
            existing.update(addr)
            cursor.execute(
                "UPDATE creators SET shipping_address = %s WHERE id = %s",
                (Json(existing), creator_id),
            )

        _record_event(
            cursor,
            creator_id,
            "apply_submitted",
            brand_id=brand_id,
            source=payload.get("source"),
            meta={"application_id": app_row["id"]},
        )
        conn.commit()
        balance = get_creator_unlock_balance(creator_id)
        return jsonify(
            {
                "success": True,
                "application_id": app_row["id"],
                "status": app_row["status"],
                "applied_at": app_row["applied_at"].isoformat() if app_row.get("applied_at") else None,
                "source": _clean_source(payload.get("source")),
                "unlock": unlock,
                "quota": balance,
            }
        )
    except Exception as e:
        conn.rollback()
        print(f"[brand-apply] submit error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@brand_apply_bp.route("/related/<int:brand_id>", methods=["GET"])
def related_brands(brand_id):
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_schema(cursor, conn)
        cursor.execute(
            """
            SELECT category, micro_friendly
            FROM pr_brands
            WHERE id = %s AND COALESCE(status, 'published') = 'published'
            """,
            (brand_id,),
        )
        seed = cursor.fetchone()
        if not seed:
            return jsonify({"success": False, "error": "Brand not found"}), 404

        cursor.execute(
            """
            SELECT
                b.id, b.slug, b.brand_name, b.logo_url, b.cover_image_url, b.website,
                b.category, b.description, b.hero_product, b.niches, b.min_followers,
                b.micro_friendly, b.regions, b.platforms, b.collaboration_type,
                b.avg_product_value, b.price_point, b.has_application_form,
                b.response_rate, b.avg_response_time_days, b.instagram_handle,
                b.tiktok_handle,
                (b.contact_email IS NOT NULL AND TRIM(b.contact_email) != '') AS has_email_contact
            FROM pr_brands b
            WHERE COALESCE(b.status, 'published') = 'published'
              AND b.id <> %s
              AND NOT EXISTS (
                  SELECT 1 FROM brand_pr_applications a
                  WHERE a.creator_id = %s AND a.brand_id = b.id
              )
            ORDER BY
              CASE WHEN b.category IS NOT NULL AND b.category = %s THEN 0 ELSE 1 END,
              CASE WHEN b.micro_friendly = %s THEN 0 ELSE 1 END,
              b.id DESC
            LIMIT 6
            """,
            (brand_id, creator_id, seed.get("category"), bool(seed.get("micro_friendly"))),
        )
        brands = [_brand_card(row) for row in (cursor.fetchall() or [])]
        return jsonify({"success": True, "brands": brands})
    except Exception as e:
        print(f"[brand-apply] related error: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@brand_apply_bp.route("/apply-events", methods=["POST"])
def record_apply_event():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    payload = request.get_json() or {}
    event = str(payload.get("event") or "").strip()
    if event not in _CLIENT_EVENTS:
        return jsonify({"success": False, "error": "Unknown event"}), 400

    brand_id = payload.get("brand_id")
    try:
        brand_id = int(brand_id) if brand_id is not None else None
    except (TypeError, ValueError):
        brand_id = None

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_schema(cursor, conn)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        _record_event(
            cursor,
            creator_id,
            event,
            brand_id=brand_id,
            source=payload.get("source"),
            meta=meta,
        )
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        print(f"[brand-apply] event error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
