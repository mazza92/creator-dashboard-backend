"""Polly chat API — Gemini brain over For You matching + PR package pitch."""

import re
import threading
import time

from flask import Blueprint, jsonify, request, session, current_app

from services.polly_usage import funnel_paywall_moment
from services.polly_manager import (
    away_brief_message,
    away_items,
    career_context,
    career_from_counts,
    career_snapshot,
    load_last_month,
    month_key,
    month_plan_message,
    pitch_cap_for,
    plan_open_note,
    unsent_drafts,
)
from services.polly import (
    credits_out_offer_due,
    credits_out_offer_message,
    coaching_moves,
    pitch_coaching_context,
    begin_polly_turn,
    build_profile_context,
    drop_pitched,
    empty_unlock_open,
    follow_brand_from_tracker,
    flatten_for_you,
    json_safe,
    llm_available,
    brand_lookup_names,
    asked_brand_query,
    leftover_is_prompt,
    candidate_looks_like_brand_name,
    allow_fuzzy_brand_lookup,
    is_casual_ack,
    resolve_pitch_location,
    mark_pitched,
    unmark_pitched,
    out_of_free_unlocks,
    paywall_unlock_chips,
    public_profile_summary,
    sanitize_brand_card,
    unpack_view_result,
)
from services.polly_brain import BrainUnavailable, compose_reply, run_turn
from services.polly_fallback import run_fallback, template_reply
from services.polly_tools import run_tool
from services.polly_turn_state import TurnState
from services.polly_kit import kit_actions, kit_context, load_kit_snapshot, strip_kit_editor_paths
from services.polly_pain import diagnose_pain, stamp_pain
from services.polly_memory import load_thread, save_thread
from services.polly_prefs import filter_brands_by_prefs, gifted_only
from services.polly_discovery import (
    assign_track,
    bump_setup_continues,
    required_categories_for_match,
    discovery_brief,
    discovery_complete,
    discovery_started,
    explain_newcollab,
    load_onboarding_survey,
    opener as discovery_opener,
    seed_from_survey,
    skip_discovery,
    starters_for,
    stated_niches,
    utc_now as utc_iso_now,
)
from services.polly_opener import (
    state_opener,
    with_location_ask,
)
from services.polly_persona import (
    creator_first_name,
    persona_ask_brand,
    persona_first_matches,
    persona_portfolio_review,
    persona_profile_audit,
    persona_rate_card,
    persona_week_plan,
    scrub_polly_voice,
    strip_embedded_pitch,
)
from services.polly_tracker import (
    drop_early_followups,
    early_followup_brands,
    apply_task_chip,
    backfill_from_notes,
    brand_timeline,
    _creator_applications as creator_applications,
    list_relationships,
    load_creator_context,
    maybe_bootstrap_nudge,
    maybe_deliver_login_checkin,
    morning_brief,
    process_due_nudges,
    record_pitch_sent,
    session_context_text,
)

polly_bp = Blueprint("polly", __name__, url_prefix="/api/polly")


class _StepTimer:
    """Per-request step timings, logged and sent as Server-Timing."""

    def __init__(self, name):
        self.name = name
        self.start = self.last = time.perf_counter()
        self.steps = []

    def mark(self, label):
        now = time.perf_counter()
        self.steps.append((label, (now - self.last) * 1000))
        self.last = now

    def finish(self, resp, extra=""):
        total = (time.perf_counter() - self.start) * 1000
        parts = " ".join(f"{label}={ms:.0f}" for label, ms in self.steps)
        print(f"[Polly timing] {self.name} total={total:.0f}ms {parts} {extra}".rstrip())
        try:
            header = ", ".join(f"{label};dur={ms:.0f}" for label, ms in self.steps)
            resp.headers["Server-Timing"] = (header + ", " if header else "") + f"total;dur={total:.0f}"
        except Exception:
            pass
        return resp


def _creator_auth():
    from pr_crm_routes import get_creator_id_from_session, get_db_connection
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return None, None, None
    from psycopg2.extras import RealDictCursor
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        """
        SELECT c.id, c.user_id, c.niche, c.creator_niches,
               to_jsonb(c) -> 'shipping_address' AS shipping_address,
               u.first_name, u.last_name, u.country
        FROM creators c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (creator_id,),
    )
    creator = cursor.fetchone()
    return creator_id, conn, dict(creator) if creator else None


def _load_scrape(conn, user_id):
    if not user_id:
        return None
    try:
        from services.creator_profile_scraper import CreatorProfileScraper
        return CreatorProfileScraper(conn).get_creator_profile(user_id)
    except Exception as err:
        print(f"[Polly] scrape load skipped: {err}")
        return None


def _load_pitch_coaching(conn, creator_id, limit=5):
    """Stored fit coaching from recent PR packages, newest first."""
    if not conn or not creator_id:
        return []
    from psycopg2.extras import RealDictCursor
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SAVEPOINT polly_coaching")
        cursor.execute(
            """
            SELECT b.brand_name, p.ai_status, p.ai_coaching, p.ai_quick_wins
            FROM pr_packages p
            JOIN pr_brands b ON b.id = p.brand_id
            WHERE p.creator_id = %s
              AND (p.ai_coaching IS NOT NULL OR p.ai_quick_wins IS NOT NULL)
            ORDER BY p.id DESC
            LIMIT %s
            """,
            (creator_id, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.execute("RELEASE SAVEPOINT polly_coaching")
        return rows
    except Exception as err:
        print(f"[Polly] coaching load skipped: {err}")
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT polly_coaching")
        except Exception:
            pass
        return []
    finally:
        cursor.close()


def _unlock_balance(creator_id, conn=None):
    from pr_crm_routes import get_creator_unlock_balance
    try:
        return get_creator_unlock_balance(creator_id, conn=conn) or {}
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return {}


def _persist_shipping_location(creator_id, city, country):
    """Save the ship-to city once so every later pitch (Polly or Directory) has it."""
    city = str(city or "").strip()[:80]
    country = str(country or "").strip()[:80]
    if not creator_id or not city:
        return False
    from pr_crm_routes import get_db_connection
    from psycopg2.extras import Json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT to_jsonb(c) -> 'shipping_address' FROM creators c WHERE c.id = %s",
            (creator_id,),
        )
        row = cursor.fetchone()
        addr = row[0] if row and isinstance(row[0], dict) else {}
        addr = dict(addr)
        addr["city"] = city
        if country:
            addr["country"] = country
        cursor.execute(
            "UPDATE creators SET shipping_address = %s WHERE id = %s",
            (Json(addr), creator_id),
        )
        conn.commit()
        return True
    except Exception as err:
        print(f"[Polly] shipping location save skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def _record_polly_draft(creator_id, brand_id, brand_name, credit_used=False):
    from pr_crm_routes import get_db_connection
    from services.polly_alerts import record_draft
    conn = get_db_connection()
    try:
        record_draft(conn, creator_id, brand_id, brand_name or "", credit_used=credit_used)
    except Exception as err:
        print(f"[Polly] draft record skipped: {err}")
    finally:
        conn.close()


def _cron_authorized():
    import os
    ua = (request.headers.get("User-Agent") or "").lower()
    if ua.startswith("vercel-cron"):
        return True
    allowed = {os.getenv("CRON_SECRET"), os.getenv("ADMIN_TOKEN"), "pr-hunter-admin-2026"}
    allowed.discard(None)
    provided = (
        request.headers.get("X-Cron-Secret")
        or request.headers.get("X-Admin-Token")
        or request.args.get("secret")
    )
    auth = request.headers.get("Authorization") or ""
    bearer = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
    return (provided in allowed) or (bearer in allowed)


def _log_polly_event(creator_id, event, meta=None):
    """Analytics only — never on the reply's critical path."""
    if not creator_id:
        return

    def _write():
        try:
            from pr_crm_routes import get_db_connection
            from services.polly_usage import log_usage
            conn = get_db_connection()
            try:
                log_usage(conn, creator_id, event, meta=meta or {})
            finally:
                conn.close()
        except Exception as err:
            print(f"[Polly] usage log skipped: {err}")

    threading.Thread(target=_write, daemon=True).start()


def _replied_chip_from_tracker(tracker):
    """One-tap 'X replied' for the oldest open follow-up, so outcomes get logged."""
    for task in (tracker or {}).get("active_tasks") or []:
        if (task.get("type") or "") != "follow_up_due" or not task.get("brand_id"):
            continue
        name = task.get("brand_name") or "They"
        return {
            "id": "checkin_replied",
            "label": f"{name} replied",
            "action": "task_act",
            "task_id": task.get("id"),
            "brand_id": task.get("brand_id"),
            "brand_name": task.get("brand_name"),
        }
    return None


def _pipeline_proof(tracker):
    """(distinct brands that opened the kit, pitches sent) from recent tracker events."""
    events = (tracker or {}).get("recent_timeline") or []
    viewed = {
        e.get("brand_id") or e.get("brand_name")
        for e in events
        if e.get("event_type") == "portfolio_viewed"
    }
    sent = sum(1 for e in events if e.get("event_type") == "pitch_sent")
    return len({v for v in viewed if v}), sent


def _after_send_empty_starters(notes, tracker, task_chips):
    """Follow-up + week only — Pro chip already sits on the send confirmation."""
    chips = []
    follow = follow_brand_from_tracker(tracker, notes)
    if follow and follow.get("name"):
        chips.append({
            "id": "draft_followup",
            "label": f"Draft {follow['name']} follow-up",
            "action": "generate_pitch",
            "brand_id": follow.get("id"),
            "brand_name": follow["name"],
            "is_followup": True,
        })
    has_pro = any((c or {}).get("action") == "unlock_pro" for c in (task_chips or []))
    if not has_pro:
        chips.extend(paywall_unlock_chips(follow))
    chips.append({
        "id": "week_plan",
        "label": "What should I do this week?",
        "action": "coach_week",
    })
    return chips[:3]


def _copy_session():
    return {key: session.get(key) for key in list(session.keys())}


def _match_scrape(scrape, notes):
    overlay = dict(scrape or {})
    stated = stated_niches(notes)
    if stated:
        overlay["primary_niche"] = stated[0]
        secondary = overlay.get("secondary_niches") or []
        if isinstance(secondary, str):
            secondary = [secondary]
        overlay["secondary_niches"] = list(dict.fromkeys(list(stated) + [s for s in secondary if s]))
    if notes.get("location"):
        overlay["location"] = notes.get("location")
    return overlay


def _fetch_brands_by_category(categories, limit=40, exclude_ids=None):
    cats = [str(c).lower().strip() for c in (categories or []) if c]
    if not cats:
        return []
    skip = []
    for item in exclude_ids or []:
        try:
            skip.append(int(item))
        except (TypeError, ValueError):
            continue
    from pr_crm_routes import get_db_connection
    from psycopg2.extras import RealDictCursor
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        like_sql = " OR ".join(["LOWER(COALESCE(category, '')) LIKE %s"] * len(cats))
        params = [f"%{c}%" for c in cats]
        exclude_sql = ""
        if skip:
            exclude_sql = " AND NOT (id = ANY(%s))"
            params.append(skip)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT id, slug, brand_name AS name, logo_url AS logo, description, category,
                   website, application_form_url, hero_product, target_audience
            FROM pr_brands
            WHERE slug IS NOT NULL
              AND COALESCE(status, 'published') = 'published'
              AND ({like_sql})
              {exclude_sql}
            ORDER BY COALESCE(response_rate, 0) DESC NULLS LAST, id DESC
            LIMIT %s
            """,
            params,
        )
        from services.brand_reply_signal import annotate_and_sort
        return annotate_and_sort(cursor, [dict(row) for row in cursor.fetchall()])
    except Exception as err:
        print(f"[Polly] category pool skipped: {err}")
        return []
    finally:
        conn.close()


def _hydrate_brand_cards(brands):
    """Fill logo/category/description when the client sent id/name/slug only."""
    rows = [dict(b) for b in (brands or []) if isinstance(b, dict)]
    need = []
    for brand in rows:
        if brand.get("logo") or brand.get("logo_url"):
            continue
        try:
            bid = int(brand.get("id") or brand.get("brand_id") or 0)
        except (TypeError, ValueError):
            bid = 0
        if bid:
            need.append(bid)
    found = {}
    if need:
        from pr_crm_routes import get_db_connection
        from psycopg2.extras import RealDictCursor
        conn = get_db_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                SELECT id, slug, brand_name AS name, logo_url AS logo, description, category,
                       website, application_form_url
                FROM pr_brands
                WHERE id = ANY(%s)
                """,
                (list(dict.fromkeys(need)),),
            )
            found = {int(row["id"]): dict(row) for row in cursor.fetchall() if row.get("id")}
        except Exception as err:
            print(f"[Polly] brand hydrate skipped: {err}")
            found = {}
        finally:
            conn.close()
    out = []
    for brand in rows:
        try:
            bid = int(brand.get("id") or brand.get("brand_id") or 0)
        except (TypeError, ValueError):
            bid = 0
        extra = found.get(bid) or {}
        merged = dict(extra)
        merged.update({k: v for k, v in brand.items() if v not in (None, "")})
        if not (merged.get("logo") or merged.get("logo_url")):
            merged["logo"] = extra.get("logo")
            merged["logo_url"] = extra.get("logo") or extra.get("logo_url")
        card = sanitize_brand_card(merged, source=brand.get("source") or "matched")
        if card:
            out.append(card)
    return out


def _pipeline_pitched_ids(creator_id):
    if not creator_id:
        return []
    from pr_crm_routes import get_db_connection
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT brand_id FROM creator_pipeline
            WHERE creator_id = %s
              AND brand_id IS NOT NULL
              AND (pitched_at IS NOT NULL OR send_confirmed IS TRUE)
            """,
            (creator_id,),
        )
        return [row[0] for row in cursor.fetchall() if row and row[0]]
    except Exception as err:
        print(f"[Polly] pipeline pitched skip: {err}")
        return []
    finally:
        conn.close()


def _suggest_payload(scrape, creator, notes=None, creator_id=None):
    notes = notes or {}
    stated = stated_niches(notes)
    match_scrape = _match_scrape(scrape, notes)
    cats = required_categories_for_match(notes, match_scrape)
    niches = cats or stated or (creator or {}).get("creator_niches") or (creator or {}).get("niche")
    cid = creator_id or (creator or {}).get("id")
    noted = list(notes.get("pitched_brand_ids") or [])
    from concurrent.futures import ThreadPoolExecutor
    from flask import copy_current_request_context

    @copy_current_request_context
    def _for_you():
        try:
            return _cached_for_you(cid)
        except Exception as err:
            print(f"[Polly] for_you skipped: {err}")
            return 200, {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_pitched = pool.submit(_pipeline_pitched_ids, cid)
        f_pooled = pool.submit(_fetch_brands_by_category, cats, exclude_ids=noted) if cats else None
        f_for_you = pool.submit(_for_you)
        exclude = noted + f_pitched.result()
        pooled = f_pooled.result() if f_pooled else []
        status, for_you = f_for_you.result()
    skip = {str(x) for x in exclude}
    pooled = [b for b in pooled if str(b.get("id")) not in skip]
    if status == 401:
        return status, [], "Not authenticated"
    payload = dict(for_you or {}) if (for_you or {}).get("success") else {}
    if pooled:
        payload["matched"] = list(payload.get("matched") or []) + pooled
    has_pool = any(payload.get(key) for key in ("recruiting", "open_lists", "matched", "hot", "newest"))
    if not has_pool:
        return status, [], (for_you or {}).get("error") or "Could not load matches"
    brands = flatten_for_you(
        payload,
        scrape=match_scrape,
        niches=niches,
        required_categories=cats,
        exclude_ids=exclude,
    )
    from services.polly import drop_shown
    fresh = drop_shown(brands, notes)
    if not fresh and brands:
        fresh = brands
    return 200, filter_brands_by_prefs(drop_pitched(fresh, notes, extra_ids=exclude), notes), None


def _coach_say(intent, profile_context, notes, scrape, kit=None, coach_moves=None):
    if intent == "explain_newcollab":
        return explain_newcollab()
    if intent == "coach_portfolio":
        return persona_portfolio_review(profile_context, kit)
    if intent == "coach_profile":
        return persona_profile_audit(
            profile_context, kit=kit, scrape=scrape, notes=notes, coaching=coach_moves,
        )
    if intent == "ask_brand":
        return persona_ask_brand()
    if intent == "coach_rates":
        followers = None
        try:
            followers = int((scrape or {}).get("follower_count") or 0) or None
        except (TypeError, ValueError):
            followers = None
        return persona_rate_card(followers)
    if intent == "coach_week":
        return persona_week_plan(profile_context, notes, coaching=coach_moves)
    return None


def _invoke_for_you():
    from pr_crm_routes import get_for_you
    return unpack_view_result(get_for_you())


_FOR_YOU_TTL_SEC = 180
_FOR_YOU_CACHE = {}
_FOR_YOU_LOCK = threading.Lock()
_FOR_YOU_KEY_LOCKS = {}


def _cached_for_you(creator_id):
    """The For You feed is ~20 queries; reuse it for a few minutes per creator.
    Pitched/excluded brands are filtered after, so a warm copy stays correct."""
    import copy

    key = int(creator_id or 0)
    if not key:
        return _invoke_for_you()
    with _FOR_YOU_LOCK:
        key_lock = _FOR_YOU_KEY_LOCKS.setdefault(key, threading.Lock())
    # A bootstrap prefetch may already be running: wait for it instead of doubling the work.
    with key_lock:
        with _FOR_YOU_LOCK:
            hit = _FOR_YOU_CACHE.get(key)
        if hit and time.time() - hit[0] < _FOR_YOU_TTL_SEC:
            return hit[1], copy.deepcopy(hit[2])
        status, data = _invoke_for_you()
        if status == 200 and (data or {}).get("success"):
            with _FOR_YOU_LOCK:
                if len(_FOR_YOU_CACHE) > 500:
                    _FOR_YOU_CACHE.clear()
                    _FOR_YOU_KEY_LOCKS.clear()
                _FOR_YOU_CACHE[key] = (time.time(), status, copy.deepcopy(data))
        return status, data


def _prefetch_for_you(creator_id):
    """Warm the feed while the client renders bootstrap and fires first matches."""
    from flask import copy_current_request_context

    @copy_current_request_context
    def _run():
        try:
            _cached_for_you(creator_id)
        except Exception as err:
            print(f"[Polly] for_you prefetch skipped: {err}")

    threading.Thread(target=_run, daemon=True).start()


def _invoke_generate_pr_package(brand_id, slug=None, city="", country=""):
    from flask import session as flask_session
    from pr_crm_routes import generate_pr_package

    try:
        brand_id = int(brand_id) if brand_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        brand_id = None
    saved = _copy_session()
    headers = {}
    for key in ("Authorization", "Cookie", "X-CSRF-Token"):
        val = request.headers.get(key)
        if val:
            headers[key] = val
    payload = {
        "is_for_you_match": True,
        "source": "polly",
    }
    if brand_id is not None:
        payload["brand_id"] = brand_id
    if slug:
        payload["slug"] = slug
    if city:
        payload["city"] = city
    if country:
        payload["country"] = country
    with current_app.test_request_context(
        "/api/pr-crm/generate-pr-package",
        method="POST",
        json=payload,
        headers=headers,
    ):
        flask_session.update(saved)
        resp = generate_pr_package()
    status, data = unpack_view_result(resp)
    print(
        f"[Polly] generate-pr-package status={status} "
        f"success={data.get('success')} error={data.get('error')} paywall={data.get('paywall')}"
    )
    return status, data


def _invoke_generate_followup(brand_id, slug=None):
    from flask import session as flask_session
    from pr_crm_routes import generate_pitch as generate_pitch_view

    try:
        brand_id = int(brand_id) if brand_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        brand_id = None
    saved = _copy_session()
    headers = {}
    for key in ("Authorization", "Cookie", "X-CSRF-Token"):
        val = request.headers.get(key)
        if val:
            headers[key] = val
    payload = {
        "is_followup": True,
        "source": "polly",
    }
    if brand_id is not None:
        payload["brand_id"] = brand_id
    if slug:
        payload["slug"] = slug
    with current_app.test_request_context(
        "/api/pr-crm/generate-pitch",
        method="POST",
        json=payload,
        headers=headers,
    ):
        flask_session.update(saved)
        resp = generate_pitch_view()
    status, data = unpack_view_result(resp)
    print(
        f"[Polly] generate-followup status={status} "
        f"success={data.get('success')} error={data.get('error')} paywall={data.get('paywall')}"
    )
    return status, data


def _lookup_published_brand(conn, brand_id=None, brand_name=None):
    import re as _re
    from psycopg2.extras import RealDictCursor
    if not conn:
        return None
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    if brand_id not in (None, "", 0, "0"):
        try:
            bid = int(brand_id)
        except (TypeError, ValueError):
            bid = None
        if bid is not None:
            cursor.execute(
                """
                SELECT id, slug, brand_name AS name, logo_url AS logo, description, category, website,
                       application_form_url
                FROM pr_brands
                WHERE id = %s
                  AND slug IS NOT NULL
                  AND COALESCE(status, 'published') = 'published'
                """,
                (bid,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
    name = (brand_name or "").strip()
    names = brand_lookup_names(name) if name else []
    if name and name not in names:
        names.insert(0, name)
    for candidate in names:
        if len(candidate) < 2:
            continue
        if is_casual_ack(candidate) or not candidate_looks_like_brand_name(candidate):
            continue
        slug = _re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
        compact = _re.sub(r"[^a-z0-9]+", "", candidate.lower())
        like = candidate if allow_fuzzy_brand_lookup(candidate) else None
        cursor.execute(
            """
            SELECT id, slug, brand_name AS name, logo_url AS logo, description, category, website,
                   application_form_url
            FROM pr_brands
            WHERE slug IS NOT NULL
              AND COALESCE(status, 'published') = 'published'
              AND (
                    LOWER(brand_name) = LOWER(%s)
                 OR LOWER(REPLACE(brand_name, '&', 'and')) = LOWER(%s)
                 OR LOWER(slug) = LOWER(%s)
                 OR LOWER(slug) = %s
                 OR regexp_replace(LOWER(brand_name), '[^a-z0-9]', '', 'g') = %s
                 OR (%s IS NOT NULL AND brand_name ILIKE %s)
              )
            ORDER BY
              CASE
                WHEN LOWER(brand_name) = LOWER(%s) THEN 0
                WHEN LOWER(slug) = %s THEN 1
                ELSE 2
              END,
              id
            LIMIT 1
            """,
            (
                candidate, candidate, candidate, slug, compact,
                like, f"%{candidate}%" if like else None,
                candidate, slug,
            ),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


_PRODUCT_HINTS = {
    "dell": ["computer", "laptop", "pc", "electronics", "monitor", "keyboard", "tech"],
    "hp": ["computer", "laptop", "printer", "electronics"],
    "lenovo": ["computer", "laptop", "pc", "electronics"],
    "asus": ["computer", "laptop", "electronics"],
    "apple": ["iphone", "mac", "electronics", "tech"],
    "samsung": ["phone", "electronics", "tv"],
    "logitech": ["keyboard", "mouse", "electronics", "tech"],
    "elgato": ["streaming", "capture", "electronics", "tech"],
}
_CROSS_FAMILY_SKIP = re.compile(
    r"\b(fitness|workout|strava|fitbit|skincare|beauty|makeup|fashion|food|coffee|wellness)\b",
    re.I,
)
_COMPUTERISH = re.compile(
    r"\b(dell|hp|lenovo|asus|acer|laptop|computer|\bpc\b|electronics|monitor|keyboard)\b",
    re.I,
)


def _product_tokens(query_name: str) -> list:
    if (
        leftover_is_prompt(query_name)
        or is_casual_ack(query_name)
        or not candidate_looks_like_brand_name(query_name or "")
    ):
        return []
    asked = asked_brand_query(query_name)
    toks = []
    for t in re.split(r"[^a-z0-9]+", (asked or "").lower()):
        if not t or t in {
            "fin", "find", "the", "and", "for", "thanks", "thank", "you",
            "more", "how", "get", "got", "can", "our", "your", "this", "that",
            "with", "from", "replies", "reply", "help", "please", "just",
            "not", "brand", "brands", "draft", "follow", "followup",
        }:
            continue
        if len(t) < 3:
            continue
        if len(t) < 4 and t not in _PRODUCT_HINTS:
            continue
        toks.append(t)
    extra = []
    for tok in toks:
        extra.extend(_PRODUCT_HINTS.get(tok, []))
    return list(dict.fromkeys(toks + extra))


def _fetch_brands_by_tokens(tokens, limit=24, exclude_ids=None):
    toks = [str(t).lower().strip() for t in (tokens or []) if t and len(str(t)) >= 3]
    if not toks:
        return []
    skip = []
    for item in exclude_ids or []:
        try:
            skip.append(int(item))
        except (TypeError, ValueError):
            continue
    from pr_crm_routes import get_db_connection
    from psycopg2.extras import RealDictCursor
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        likes = [f"%{t}%" for t in toks]
        clauses = " OR ".join(
            ["brand_name ILIKE %s OR COALESCE(description,'') ILIKE %s OR COALESCE(hero_product,'') ILIKE %s OR LOWER(COALESCE(category,'')) LIKE %s"]
            * len(toks)
        )
        params = []
        for like, tok in zip(likes, toks):
            params.extend([like, like, like, f"%{tok}%"])
        exclude_sql = ""
        if skip:
            exclude_sql = " AND NOT (id = ANY(%s))"
            params.append(skip)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT id, slug, brand_name AS name, logo_url AS logo, description, category,
                   website, application_form_url, hero_product, target_audience
            FROM pr_brands
            WHERE slug IS NOT NULL
              AND COALESCE(status, 'published') = 'published'
              AND ({clauses})
              {exclude_sql}
            ORDER BY COALESCE(response_rate, 0) DESC NULLS LAST, id DESC
            LIMIT %s
            """,
            params,
        )
        from services.brand_reply_signal import annotate_and_sort
        return annotate_and_sort(cursor, [dict(row) for row in cursor.fetchall()])
    except Exception as err:
        print(f"[Polly] token pool skipped: {err}")
        return []
    finally:
        conn.close()


def _lookup_similar_brands(query_name, scrape=None, notes=None, creator=None, creator_id=None, exclude_ids=None, limit=3):
    """Same-product directory brands for an asked name that is not in the DB."""
    if (
        leftover_is_prompt(query_name)
        or is_casual_ack(query_name)
        or not candidate_looks_like_brand_name(query_name or "")
    ):
        return []
    notes = notes or {}
    skip = list(exclude_ids or []) + list((notes or {}).get("pitched_brand_ids") or [])
    tokens = _product_tokens(query_name)
    if not tokens:
        return []
    pooled = _fetch_brands_by_tokens(tokens, limit=max(24, limit * 8), exclude_ids=skip)
    cats = required_categories_for_match(notes, scrape) or stated_niches(notes)
    if not cats:
        niche = (scrape or {}).get("primary_niche") or (creator or {}).get("niche")
        if niche:
            cats = [niche]
    if cats:
        pooled.extend(_fetch_brands_by_category(cats, limit=max(12, limit * 4), exclude_ids=skip))
    computerish = bool(_COMPUTERISH.search(query_name or "") or any(t in {"dell", "laptop", "computer"} for t in tokens))
    ranked = []
    seen = set()
    for row in pooled:
        card = sanitize_brand_card(row, source="matched")
        if not card or card["id"] in seen:
            continue
        seen.add(card["id"])
        blob = " ".join([
            str(card.get("name") or ""),
            str(card.get("category") or ""),
            str(card.get("description") or ""),
            str(row.get("hero_product") or ""),
        ]).lower()
        if computerish and _CROSS_FAMILY_SKIP.search(blob):
            continue
        token_hits = sum(1 for tok in tokens if tok in blob)
        if tokens and token_hits <= 0:
            continue
        tier = (card.get("reply_signal") or {}).get("tier")
        reply_rank = 0 if tier == "replies" else 2 if tier == "cold" else 1
        ranked.append((reply_rank, -token_hits, -int(card.get("match_score") or 0), len(ranked), card))
    ranked.sort()
    return [item[-1] for item in ranked[:limit]]


def _deliver_credits_out_offer(conn, creator_id, first, balance, tracker):
    """Once a month, post the autopilot offer when a free creator has used all 3 credits."""
    try:
        thread = load_thread(conn, creator_id)
        notes = dict(thread.get("notes") or {})
        month = month_key()
        if not credits_out_offer_due(notes, balance, month):
            return False
        views_n, sent_n = _pipeline_proof(tracker)
        message = credits_out_offer_message(
            first, balance, tracker, notes, kit_views=views_n, sent=sent_n,
        )
        notes["credits_out_offer_month"] = month
        messages = list(thread.get("messages") or [])
        messages.append(message)
        save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=notes)
        _log_polly_event(creator_id, "paywall_shown", {"moment": "credits_out"})
        return True
    except Exception as err:
        print(f"[Polly] credits-out offer skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _deliver_manager_note(conn, creator_id, first, career, tracker, balance, queue):
    """Post the month plan (first open of the month) or a while-you-were-away brief into the thread."""
    try:
        thread = load_thread(conn, creator_id)
        notes = dict(thread.get("notes") or {})
        which = plan_open_note(notes, bool(thread.get("messages")))
        if not which:
            return None
        is_pro = bool((balance or {}).get("is_unlimited"))
        now_iso = utc_iso_now()
        if which == "month":
            message = month_plan_message(
                first, career, load_last_month(conn, creator_id), is_pro,
                brands=[b for b in (queue or []) if isinstance(b, dict) and b.get("name")],
            )
            notes["month_plan_month"] = month_key()
        else:
            message = away_brief_message(
                away_items(tracker, unsent_drafts(conn, creator_id), since=notes.get("away_brief_at")),
                career,
                early=early_followup_brands(conn, creator_id),
            )
        notes["away_brief_at"] = now_iso
        messages = list(thread.get("messages") or [])
        if message:
            messages.append(message)
        save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=notes)
        if not message:
            return None
        _log_polly_event(creator_id, "manager_note", {"kind": which, "stage": career.get("stage")})
        return which
    except Exception as err:
        print(f"[Polly] manager note skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


@polly_bp.route("/bootstrap", methods=["GET"])
def bootstrap():
    timer = _StepTimer("bootstrap")
    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        timer.mark("auth")
        scrape = _load_scrape(conn, (creator or {}).get("user_id") or session.get("user_id"))
        balance = _unlock_balance(creator_id, conn=conn)
        timer.mark("profile")
        kit = load_kit_snapshot(conn, creator_id, scrape)
        profile_context = build_profile_context(scrape, creator) + "\n\n" + kit_context(kit)
        first = creator_first_name(creator, scrape)
        thread = load_thread(conn, creator_id)
        timer.mark("kit_thread")
        notes = thread.get("notes") or {}
        seeded = not notes.get("survey_seeded_at")
        if seeded:
            notes = seed_from_survey(notes, load_onboarding_survey(conn, creator_id), scrape)
        notes = assign_track(notes, scrape)
        try:
            backfill_from_notes(conn, creator_id, notes)
        except Exception as err:
            print(f"[Polly] tracker backfill skipped: {err}")
        prior = {} if seeded else (thread.get("notes") or {})
        tracker = {}
        try:
            tracker = load_creator_context(conn, creator_id)
        except Exception as err:
            print(f"[Polly] tracker context skipped: {err}")
            tracker = {}
        timer.mark("tracker")
        pain = diagnose_pain(notes, tracker, kit, scrape)
        notes = stamp_pain(notes, pain)
        if pain:
            tracker["active_pain"] = pain
        if seeded or notes.get("active_pain") != prior.get("active_pain") or notes.get("polly_track") != prior.get("polly_track") or notes.get("polly_track_provisional") != prior.get("polly_track_provisional"):
            save_thread(
                conn,
                creator_id,
                messages=thread.get("messages") or [],
                suggested=thread.get("suggested_brands") or [],
                notes=notes,
            )
        queue = drop_pitched(thread.get("suggested_brands") or [], notes)
        greeting = discovery_opener(first)
        top_name = None
        for row in queue:
            if isinstance(row, dict) and row.get("name"):
                top_name = row.get("name")
                break
        brief = morning_brief(tracker, first)
        credit_open = None
        if out_of_free_unlocks(balance):
            credit_open = empty_unlock_open(first, tracker, notes)
            greeting = credit_open["greeting"]
            brief = credit_open["brief"]
            follow = credit_open.get("follow") or {}
            if follow.get("name") and not (isinstance(notes.get("paywall_brand"), dict) and notes["paywall_brand"].get("name")):
                notes["paywall_brand"] = {
                    "id": follow.get("id"),
                    "name": follow.get("name"),
                }
                try:
                    save_thread(
                        conn,
                        creator_id,
                        messages=thread.get("messages") or [],
                        suggested=thread.get("suggested_brands") or [],
                        notes=notes,
                    )
                except Exception as err:
                    print(f"[Polly] paywall_brand stamp skipped: {err}")
        starters = (
            credit_open["starters"]
            if credit_open
            else starters_for(notes, top_brand=top_name)
        )
        replied = _replied_chip_from_tracker(tracker)
        if replied and not any((s or {}).get("id") == "checkin_replied" for s in starters):
            starters = list(starters)
            starters.insert(1 if starters else 0, replied)
        empty_thread = not (thread.get("messages") or [])
        auto_action = None
        if empty_thread:
            applications = []
            try:
                applications = creator_applications(conn, creator_id)
            except Exception as err:
                print(f"[Polly] opener applications skipped: {err}")
            opened = state_opener(first, kit, tracker, notes, applications=applications)
            if opened:
                greeting = opened["greeting"]
                starters = opened["starters"]
                if opened.get("state") == "kit_view" and out_of_free_unlocks(balance):
                    from services.polly_persona import PRO_VALUE_LINE
                    greeting = (
                        greeting
                        + "\n\nThe follow-up's on me. If you want more brands like this one looking, "
                        + PRO_VALUE_LINE
                    )
                    starters = list(starters) + [{
                        "id": "unlock_pro",
                        "label": "Put Polly on autopilot · Pro",
                        "action": "unlock_pro",
                    }]
                    _log_polly_event(creator_id, "paywall_shown", {"moment": "kit_view"})
                _log_polly_event(creator_id, "state_opener", {"state": opened.get("state")})
            if not opened or opened.get("state") == "kit_not_live":
                # Message one is three matched gifted brands, written from the survey.
                _prefetch_for_you(creator_id)
                auto_action = {
                    "action": "suggest_brands",
                    "starter": "first_matches",
                    "chip_id": "first_matches",
                    "deal": "gifted",
                    "skip_discovery": True,
                }
            shipping = resolve_pitch_location(creator, scrape, notes)
            if not auto_action and shipping.get("needs_location") and not notes.get("location_asked_at"):
                greeting = with_location_ask(greeting)
                notes["location_asked_at"] = utc_iso_now()
                notes["awaiting_location"] = True
                try:
                    save_thread(
                        conn,
                        creator_id,
                        messages=thread.get("messages") or [],
                        suggested=thread.get("suggested_brands") or [],
                        notes=notes,
                    )
                except Exception as err:
                    print(f"[Polly] location ask stamp skipped: {err}")
        timer.mark("state")
        checked_in = False
        try:
            if maybe_deliver_login_checkin(conn, creator_id, tracker):
                checked_in = True
                thread = load_thread(conn, creator_id)
        except Exception as err:
            print(f"[Polly] login checkin skipped: {err}")
        offered = False
        if not checked_in and _deliver_credits_out_offer(conn, creator_id, first, balance, tracker):
            offered = True
            thread = load_thread(conn, creator_id)
            brief = None
            auto_action = None
        career = career_snapshot(conn, creator_id, notes, balance)
        if not checked_in and not offered and not empty_thread and career:
            posted = _deliver_manager_note(conn, creator_id, first, career, tracker, balance, queue)
            if posted:
                thread = load_thread(conn, creator_id)
                brief = None
        nudge = None
        try:
            nudge = maybe_bootstrap_nudge(conn, creator_id)
        except Exception as err:
            print(f"[Polly] nudge peek skipped: {err}")
        try:
            early_fu = early_followup_brands(conn, creator_id)
        except Exception as err:
            print(f"[Polly] early follow-up lookup skipped: {err}")
            conn.rollback()
            early_fu = {}
        starters = drop_early_followups(starters, early_fu)
        if brief and brief.get("chips"):
            brief = dict(brief, chips=drop_early_followups(brief["chips"], early_fu))
        timer.mark("opener")
        _log_polly_event(creator_id, "open")
        return timer.finish(jsonify({
            "success": True,
            "profile": public_profile_summary(scrape, creator),
            "credits": {
                "remaining": balance.get("remaining"),
                "limit": balance.get("limit"),
                "is_unlimited": bool(balance.get("is_unlimited")),
                "used": balance.get("used"),
            },
            "greeting": greeting,
            "opener": greeting,
            "starters": starters,
            "discovery": {
                "complete": discovery_complete(notes),
                "started": discovery_started(notes),
            },
            "messages": thread.get("messages") or [],
            "suggested_brands": queue,
            "tracker": {
                "due_soon": tracker.get("due_soon") or [],
                "active_count": len(tracker.get("active_tasks") or []),
            },
            "brief": brief,
            "career": career,
            "nudge": nudge,
            "auto_action": auto_action,
        }), extra=f"empty={int(empty_thread)} auto={int(bool(auto_action))}")
    finally:
        if conn:
            conn.close()


@polly_bp.route("/gigs/more", methods=["POST"])
def more_gigs():
    """Append the next paid-UGC page without a new Polly turn."""
    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    exclude = data.get("exclude_ids") or []
    try:
        scrape = _load_scrape(conn, (creator or {}).get("user_id") or session.get("user_id"))
        stored = load_thread(conn, creator_id)
        notes = dict(stored.get("notes") or {})
        history = data.get("messages") if isinstance(data.get("messages"), list) else (stored.get("messages") or [])
        from services.polly_gigs import page_polly_gigs, mark_shown_gigs
        gigs, has_more = page_polly_gigs(
            creator_id,
            scrape=scrape,
            notes=notes,
            exclude_ids=exclude,
            history=history,
        )
        notes["wanted_gigs"] = True
        if gigs:
            notes["saw_gigs"] = True
            notes = mark_shown_gigs(notes, gigs)
        save_thread(
            conn,
            creator_id,
            history,
            stored.get("suggested_brands") or [],
            notes=notes,
        )
        return jsonify({
            "success": True,
            "gigs": json_safe(gigs),
            "gigs_has_more": bool(has_more),
        })
    except Exception as err:
        print(f"[Polly] gigs/more failed: {err}")
        return jsonify({"success": False, "error": "Could not load more offers"}), 500
    finally:
        if conn:
            conn.close()


@polly_bp.route("/chat", methods=["POST"])
def chat():
    timer = _StepTimer("chat")
    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    begin_polly_turn()

    data = request.get_json(silent=True) or {}
    messages = data.get("messages") or []
    suggested = data.get("suggested_brands") or []
    explicit_action = (data.get("action") or "").strip().lower() or None
    explicit_brand_id = data.get("brand_id")
    user_text = ""
    for msg in reversed(messages):
        if (msg.get("role") or "").lower() == "user" and (msg.get("content") or "").strip():
            user_text = msg.get("content").strip()
            break
    if not user_text and not explicit_action and not explicit_brand_id:
        if conn:
            conn.close()
        return jsonify({"success": False, "error": "message required"}), 400

    state = None
    try:
        scrape = _load_scrape(conn, (creator or {}).get("user_id") or session.get("user_id"))
        kit = load_kit_snapshot(conn, creator_id, scrape)
        profile_context = build_profile_context(scrape, creator) + "\n\n" + kit_context(kit)
        coaching_rows = _load_pitch_coaching(conn, creator_id)
        coaching_block = pitch_coaching_context(coaching_rows)
        if coaching_block:
            profile_context += "\n\n" + coaching_block
        balance = _unlock_balance(creator_id, conn=conn)
        first = creator_first_name(creator, scrape)
        stored = load_thread(conn, creator_id)
        notes = dict(stored.get("notes") or {})
        career = career_snapshot(conn, creator_id, notes, balance)
        career_block = career_context(career)
        if career_block:
            profile_context += "\n\n" + career_block
        try:
            early_fu = early_followup_brands(conn, creator_id)
        except Exception as err:
            print(f"[Polly] early follow-up lookup skipped: {err}")
            conn.rollback()
            early_fu = {}
        if not notes.get("survey_seeded_at"):
            notes = seed_from_survey(notes, load_onboarding_survey(conn, creator_id), scrape)
        chip_id = str(data.get("starter") or data.get("chip_id") or "").strip()
        notes = bump_setup_continues(notes, user_text, chip_id, explicit_action)
        if data.get("skip_discovery"):
            notes = skip_discovery(notes)
        chip_deal = str(data.get("deal") or "").strip().lower()
        if chip_deal in ("gifted", "paid") and not (chip_deal == "paid" and gifted_only(notes)):
            notes["deal_intent"] = chip_deal
        notes = assign_track(notes, scrape)
        tracker_ctx = {}
        try:
            tracker_ctx = load_creator_context(conn, creator_id)
        except Exception as err:
            print(f"[Polly] tracker context skipped: {err}")
            conn.rollback()
        pain = diagnose_pain(notes, tracker_ctx, kit, scrape)
        notes = stamp_pain(notes, pain)
        if pain:
            tracker_ctx["active_pain"] = pain
        chip = {
            "id": chip_id or None,
            "action": explicit_action,
            "label": user_text if (chip_id or explicit_action) else None,
            "brand_id": explicit_brand_id,
            "brand_name": data.get("brand_name"),
            "is_followup": bool(data.get("is_followup")),
            "confirm_cold": bool(data.get("confirm_cold")),
            "deal": chip_deal or None,
        } if (chip_id or explicit_action or explicit_brand_id) else {}
        state = TurnState(
            creator_id=creator_id,
            creator=creator or {},
            first=first,
            scrape=scrape or {},
            kit=kit or {},
            profile_context=profile_context,
            coach_moves=coaching_moves(coaching_rows),
            balance=balance,
            notes=notes,
            messages=messages,
            stored_messages=list(stored.get("messages") or []),
            suggested=suggested,
            tracker_ctx=tracker_ctx,
            early_fu=early_fu,
            career=career,
            user_text=user_text,
            chip=chip,
            conn=conn,
        )
        timer.mark("load")
        print(
            f"[Polly] turn creator={creator_id} gemini={'on' if llm_available() else 'off'} "
            f"msg={user_text[:80]!r} action={explicit_action} chip={chip_id or '-'}"
        )

        prior = _run_chip(state, explicit_action, chip_id, data)
        if state.auth_failed:
            return jsonify({"success": False, "error": "Not authenticated"}), 401
        brain = "gemini"
        if state.final_say:
            say = state.final_say
            brain = "server"
        else:
            hint = (discovery_brief(state.notes) + " " + session_context_text(state.tracker_ctx)).strip()
            try:
                say = run_turn(state, discovery_hint=hint, prior_tools=prior)
            except BrainUnavailable as err:
                print(f"[Polly] brain unavailable, fallback: {err}")
                brain = "fallback"
                say = template_reply(state) if prior else run_fallback(state)
        if state.auth_failed:
            return jsonify({"success": False, "error": "Not authenticated"}), 401
        timer.mark("brain")
        return _finish_turn(state, say, brain, timer, chip_id)
    except Exception as err:
        print(f"[Polly] chat error: {err}")
        import traceback
        traceback.print_exc()
        try:
            from services.polly_usage import log_usage
            log_usage(conn, creator_id, "chat_error", ok=False, error=str(err)[:180])
        except Exception:
            pass
        return jsonify({"success": False, "error": "Polly hit a snag. Try again."}), 500
    finally:
        live = state.conn if state is not None else conn
        for c in {id(conn): conn, id(live): live}.values():
            if c and not getattr(c, "closed", 1):
                c.close()


_CHIP_COACH_TOPICS = {
    "coach_portfolio": "kit",
    "coach_profile": "profile",
    "coach_rates": "rates",
    "coach_week": "week",
    "explain_newcollab": "how_it_works",
}


def _run_chip(state, action, chip_id, data):
    """A tapped button already says which tool to run. Run it before the brain writes the reply."""
    prior = []
    brand_id = data.get("brand_id")
    brand_name = data.get("brand_name")
    if action == "task_act":
        chip_res = apply_task_chip(
            state.db(), state.creator_id, chip_id,
            task_id=data.get("task_id"), brand_id=brand_id, brand_name=brand_name,
        )
        if chip_res.get("unmark_pitched"):
            state.notes = unmark_pitched(state.notes, {
                "id": chip_res.get("brand_id"), "name": chip_res.get("brand_name"),
            })
        state.add_chips(chip_res.get("chips") or [])
        brand_id = brand_id or chip_res.get("brand_id")
        if chip_res.get("is_followup"):
            data["is_followup"] = True
        prior.append({"tool": "task_button", "result": {
            "route": chip_res.get("route"), "said": chip_res.get("say") or "",
        }})
        route = chip_res.get("route") or "chat"
        action = route if route != "task_act" else "chat"
    call = None
    if action == "generate_pitch" and (brand_id or brand_name):
        call = ("draft_pitch", {
            "brand_id": brand_id, "brand_name": brand_name,
            "followup": bool(data.get("is_followup")), "confirm_cold": bool(data.get("confirm_cold")),
        })
    elif action == "suggest_brands":
        call = ("suggest_brands", {"deal": data.get("deal")} if data.get("deal") else {})
    elif action == "suggest_gigs":
        call = ("find_paid_gigs", {"more": chip_id == "more_gigs"})
    elif chip_id == "i_sent_it":
        call = ("log_pitch_sent", {"brand_id": brand_id, "brand_name": brand_name})
    elif action in _CHIP_COACH_TOPICS:
        call = ("get_coaching_facts", {"topic": _CHIP_COACH_TOPICS[action]})
    if not call:
        return prior
    result = run_tool(state, call[0], call[1])
    prior.append({"tool": call[0], "args": call[1], "result": result})
    if chip_id == "first_matches" and state.brands and not state.final_say:
        live = any((b or {}).get("source") in ("recruiting", "open_lists") for b in state.brands)
        niche = str((stated_niches(state.notes) or [None])[0] or state.scrape.get("primary_niche") or "")
        say = persona_first_matches(state.first, state.notes.get("survey"), state.brands,
                                    niche=niche.strip().lower(), live=live)
        shipping = resolve_pitch_location(state.creator, state.scrape, state.notes)
        if shipping.get("needs_location") and not state.notes.get("location_asked_at"):
            say = with_location_ask(say)
            state.notes["location_asked_at"] = utc_iso_now()
        state.final_say = say
    return prior


def _finish_turn(state, say, brain, timer, chip_id):
    from services.polly_discovery import remaining_fields

    notes = state.notes
    if not discovery_complete(notes) and discovery_started(notes) and not remaining_fields(notes):
        notes["discovery_completed_at"] = utc_iso_now()
    say = compose_reply(state, scrub_polly_voice(say))
    raw_say = say
    say = strip_kit_editor_paths(say)
    kit_cta = list(state.kit_actions or [])
    if not kit_cta and (
        say != raw_say
        or re.search(r"(?i)(open your kit|publish.{0,40}kit|my kit)", say or "")
    ):
        kit_cta = kit_actions(state.kit)
    pitch = state.pitch
    if pitch:
        say = strip_embedded_pitch(say) or say
    brands = filter_brands_by_prefs(_hydrate_brand_cards(state.brands), notes) if state.brands else []
    queue = filter_brands_by_prefs(
        _hydrate_brand_cards(drop_pitched(brands or state.suggested, notes)), notes,
    )
    task_chips = drop_early_followups(list(state.task_chips), state.early_fu)
    balance = state.balance
    intent = state.intent
    payload = {
        "success": not state.error,
        "message": say,
        "intent": intent,
        "brands": json_safe(brands),
        "gigs": json_safe(state.gigs),
        "gigs_has_more": bool(state.gigs_has_more),
        "suggested_brands": json_safe(queue),
        "pitch": json_safe(pitch),
        "pitch_update": json_safe(state.pitch_update),
        "kit_actions": json_safe(kit_cta),
        "task_chips": json_safe(task_chips),
        "paywall": state.paywall,
        "starters": (
            _after_send_empty_starters(notes, state.tracker_ctx, state.task_chips)
            if state.after_send_empty
            else (
                empty_unlock_open(state.first, state.tracker_ctx, notes)["starters"]
                if out_of_free_unlocks(balance)
                else starters_for(notes, top_brand=(queue[0]["name"] if queue else None))
            )
        ),
        "discovery": {
            "complete": discovery_complete(notes),
            "started": discovery_started(notes),
        },
        "credits": {
            "remaining": balance.get("remaining"),
            "limit": balance.get("limit"),
            "is_unlimited": bool(balance.get("is_unlimited")),
            "used": balance.get("used"),
        },
    }
    payload["starters"] = drop_early_followups(payload["starters"], state.early_fu)
    career = state.career
    if career and state.sent_now:
        totals = dict(career.get("totals") or {})
        totals["pitched"] = int(totals.get("pitched") or 0) + 1
        totals["pitched_month"] = int(totals.get("pitched_month") or 0) + 1
        career = career_from_counts(totals, notes, pitch_cap_for(balance))
    if career:
        payload["career"] = career
    if state.paywall_payload:
        payload["paywall_payload"] = state.paywall_payload
    if state.error:
        payload["error"] = state.error
    assistant = {
        "role": "assistant",
        "content": say,
        "brands": json_safe(brands) or [],
        "gigs": json_safe(state.gigs) or [],
        "gigs_has_more": bool(state.gigs_has_more),
        "pitch": json_safe(pitch),
        "kit_actions": json_safe(kit_cta) or [],
        "task_chips": json_safe(task_chips) or [],
    }
    if state.paywall_payload and state.paywall_payload.get("preview"):
        assistant["locked_pitch"] = {
            "brand_name": state.paywall_payload.get("brand_name"),
            "lines": state.paywall_payload["preview"],
        }
    if brands or state.gigs:
        _log_polly_event(state.creator_id, "matches_shown", {
            "brands": len(brands or []), "gigs": len(state.gigs or []), "intent": intent,
        })
    paywall_moment = funnel_paywall_moment(
        state.paywall_moment, state.paywall, task_chips, payload.get("starters"),
    )
    if paywall_moment:
        _log_polly_event(state.creator_id, "paywall_shown", paywall_moment)
    tools = ",".join(e.get("name") for e in state.tool_log) or "-"
    try:
        persist_conn = state.db()
        save_thread(persist_conn, state.creator_id, list(state.messages or []) + [assistant], queue, notes=notes)
        try:
            from services.polly import last_polly_brain, last_polly_cost
            from services.polly_usage import log_usage
            used = last_polly_brain()
            cost = last_polly_cost()
            log_usage(
                persist_conn,
                state.creator_id,
                "chat",
                intent=intent,
                llm=used.get("provider") or brain,
                ok=not state.error,
                error=state.error or None,
                meta={
                    "model": cost.get("model") or used.get("model"),
                    "usd": cost.get("usd") or 0,
                    "input_tokens": cost.get("input_tokens") or 0,
                    "output_tokens": cost.get("output_tokens") or 0,
                    "calls": cost.get("calls") or 0,
                    "source": "response_usage",
                    "brain": brain,
                    "tools": tools,
                },
            )
        except Exception:
            pass
    except Exception as persist_err:
        print(f"[Polly] persist skipped: {persist_err}")
    timer.mark("persist")
    tag = f"intent={intent} chip={chip_id or '-'} brain={brain} tools={tools}"
    if state.paywall:
        return timer.finish(jsonify(payload), extra=tag), 402
    return timer.finish(jsonify(payload), extra=tag)


@polly_bp.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Same turn as /chat, as server-sent events: intent, say deltas, then the full payload."""
    import json
    import queue

    from flask import Response, copy_current_request_context
    from services.polly import clear_stream_sink, set_stream_sink, unpack_view_result

    events = queue.Queue()

    @copy_current_request_context
    def _run():
        set_stream_sink(events.put)
        try:
            status, body = unpack_view_result(chat())
        except Exception as err:
            print(f"[Polly] stream turn failed: {err}")
            status, body = 500, {"success": False, "error": "Polly hit a snag. Try again."}
        finally:
            clear_stream_sink()
        events.put({"type": "done", "status": status, "data": json_safe(body or {})})

    threading.Thread(target=_run, daemon=True).start()

    def _generate():
        # Padding nudges proxies to flush the first bytes immediately.
        yield ":" + " " * 2048 + "\n\n"
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event.get("type") == "done":
                return

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@polly_bp.route("/thread", methods=["GET", "PUT"])
def thread():
    creator_id, conn, _creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        if request.method == "GET":
            data = load_thread(conn, creator_id)
            return jsonify({"success": True, **json_safe(data)})
        body = request.get_json(silent=True) or {}
        saved = save_thread(
            conn,
            creator_id,
            body.get("messages") or [],
            body.get("suggested_brands") or [],
        )
        return jsonify({"success": True, **json_safe(saved)})
    except Exception as err:
        print(f"[Polly] thread error: {err}")
        return jsonify({"success": False, "error": "Could not save chat"}), 500
    finally:
        if conn:
            conn.close()


@polly_bp.route("/timeline", methods=["GET"])
def timeline_list():
    creator_id, conn, _creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        status = (request.args.get("status") or "all").strip().lower()
        notes = (load_thread(conn, creator_id) or {}).get("notes") or {}
        try:
            backfill_from_notes(conn, creator_id, notes)
        except Exception as err:
            print(f"[Polly] tracker backfill skipped: {err}")
        rows = list_relationships(conn, creator_id, status=status)
        return jsonify({"success": True, "relationships": json_safe(rows)})
    except Exception as err:
        print(f"[Polly] timeline list error: {err}")
        return jsonify({"success": False, "error": "Could not load timeline"}), 500
    finally:
        if conn:
            conn.close()


@polly_bp.route("/timeline/<int:brand_id>", methods=["GET"])
def timeline_brand(brand_id):
    creator_id, conn, _creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        data = brand_timeline(conn, creator_id, brand_id)
        if not data:
            return jsonify({"success": False, "error": "Brand not found"}), 404
        return jsonify({"success": True, **json_safe(data)})
    except Exception as err:
        print(f"[Polly] timeline brand error: {err}")
        return jsonify({"success": False, "error": "Could not load timeline"}), 500
    finally:
        if conn:
            conn.close()


def _log_send_on_open(conn, creator_id, brand_id, brand_name=None):
    """Open email counts as sent: log it and schedule the day-4 follow-up, so Polly asks
    'did they reply?' later instead of 'did you send it?' now."""
    try:
        bid = int(brand_id or 0)
    except (TypeError, ValueError):
        bid = 0
    if not bid:
        return False
    stored = load_thread(conn, creator_id)
    notes = dict(stored.get("notes") or {})
    from services.polly import pitched_id_set
    if bid in pitched_id_set(notes):
        return False
    brand = {"id": bid, "name": (brand_name or "").strip() or None}
    if not brand["name"]:
        pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else {}
        brand["name"] = pending.get("name") or pending.get("brand_name")
    record_pitch_sent(conn, creator_id, brand, drafted=False)
    notes = mark_pitched(notes, brand)
    save_thread(
        conn,
        creator_id,
        messages=stored.get("messages") or [],
        suggested=stored.get("suggested_brands") or [],
        notes=notes,
    )
    return True


@polly_bp.route("/pitch/handoff", methods=["POST"])
def pitch_handoff():
    """Open email / Copy email / Copy pitch tapped — the draft left Polly."""
    creator_id, conn, _creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        body = request.get_json(silent=True) or {}
        method = str(body.get("method") or "").strip().lower()
        if method not in ("open_email", "copy_email", "copy_pitch"):
            return jsonify({"success": False, "error": "unknown method"}), 400
        from services.polly_alerts import record_handoff
        from services.polly_usage import log_usage
        updated = record_handoff(conn, creator_id, body.get("brand_id"), method)
        log_usage(conn, creator_id, "pitch_handoff", intent=method, meta={"brand_id": body.get("brand_id")})
        logged = False
        if method == "open_email" and not body.get("is_followup"):
            logged = _log_send_on_open(conn, creator_id, body.get("brand_id"), body.get("brand_name"))
        return jsonify({"success": True, "recorded": updated, "logged_sent": logged})
    except Exception as err:
        print(f"[Polly] handoff log failed: {err}")
        return jsonify({"success": False, "error": "could not log"}), 500
    finally:
        if conn:
            conn.close()


@polly_bp.route("/cron/alerts", methods=["GET", "POST"])
def cron_alerts():
    """Due nudges in-thread + by email, kit-view emails."""
    if not _cron_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    from pr_crm_routes import get_db_connection
    from services.polly_alerts import run_alerts
    dry_run = str(request.args.get("dry_run") or "").lower() in ("1", "true", "yes")
    try:
        limit = max(1, min(200, int(request.args.get("limit") or 40)))
    except (TypeError, ValueError):
        limit = 40
    test_email = (request.args.get("test_email") or "").strip()
    conn = get_db_connection()
    try:
        result = run_alerts(conn, dry_run=dry_run, limit=limit, test_email=test_email)
        return jsonify({"success": True, "dry_run": dry_run, **json_safe(result)})
    except Exception as err:
        print(f"[Polly] cron alerts error: {err}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": "alerts run failed"}), 500
    finally:
        conn.close()


_CHECKIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Polly</title></head>
<body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:#F7F5F0;text-align:center;padding:64px 16px;">
<form method="post"><input type="hidden" name="t" value="{token}"><input type="hidden" name="src" value="{src}">
<p style="font-size:16px;color:#374151;">Saving your update for Polly&hellip;</p>
<button type="submit" style="padding:12px 18px;border-radius:999px;border:0;background:#111827;color:#fff;font-weight:600;">Continue</button>
</form><script>document.forms[0].submit();</script></body></html>"""


def _checkin_already_applied(conn, token_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM polly_usage_events WHERE event = 'checkin_tap' AND meta->>'token' = %s LIMIT 1",
        (token_id,),
    )
    return cur.fetchone() is not None


@polly_bp.route("/checkin", methods=["GET", "POST"])
def checkin_one_tap():
    """Signed email link: record the outcome (replied, quiet, PR arrived...) without a login."""
    from html import escape
    from flask import redirect
    from pr_crm_routes import get_db_connection
    from services.checkin_links import next_polly_url, read_token
    from services.polly_alerts import frontend_url
    from services.polly_usage import log_usage

    token = (request.values.get("t") or "").strip()
    src = re.sub(r"[^a-z0-9_]", "", (request.values.get("src") or "").lower())[:24]
    info = read_token(token)
    if not info:
        return redirect(f"{frontend_url()}/creator/dashboard/for-you", code=303)
    if request.method == "GET":
        page = _CHECKIN_PAGE.format(token=escape(token, quote=True), src=escape(src, quote=True))
        return page, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
    cid = info["creator_id"]
    conn = get_db_connection()
    try:
        if not _checkin_already_applied(conn, info["token_id"]):
            res = apply_task_chip(conn, cid, info["chip_id"], info["task_id"], info["brand_id"],
                                  info["brand_name"])
            if res.get("unmark_pitched"):
                thread = load_thread(conn, cid)
                notes = unmark_pitched(thread.get("notes") or {}, {
                    "id": info["brand_id"], "name": info["brand_name"]})
                save_thread(conn, cid, thread.get("messages"), thread.get("suggested_brands"), notes)
            log_usage(conn, cid, "checkin_tap", intent=info["chip_id"], meta={
                "token": info["token_id"], "src": src, "brand_id": info["brand_id"],
                "task_id": info["task_id"]})
            print(f"[Polly] one-tap checkin creator={cid} chip={info['chip_id']} src={src}")
    except Exception as err:
        print(f"[Polly] one-tap checkin failed creator={cid}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return redirect(next_polly_url(frontend_url(), info, src), code=303)


@polly_bp.route("/cron/nudges", methods=["GET", "POST"])
def cron_nudges():
    if not _cron_authorized():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    from pr_crm_routes import get_db_connection
    conn = get_db_connection()
    try:
        sent = process_due_nudges(conn)
        return jsonify({"success": True, "sent": sent})
    except Exception as err:
        print(f"[Polly] cron nudges error: {err}")
        return jsonify({"success": False, "error": "nudge run failed"}), 500
    finally:
        conn.close()
