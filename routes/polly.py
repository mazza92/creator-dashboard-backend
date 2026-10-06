"""Polly chat API — Gemini brain over For You matching + PR package pitch."""

import re
import threading
import time
from datetime import datetime, timezone

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
    coaching_moves,
    pitch_coach_line,
    pitch_coaching_context,
    begin_polly_turn,
    brand_in_suggested,
    build_profile_context,
    chat_reply,
    classify_intent,
    drop_pending_draft,
    drop_pitched,
    empty_unlock_open,
    follow_brand_from_tracker,
    flatten_for_you,
    gemini_available,
    is_done_turn,
    is_more_brands_turn,
    json_safe,
    last_pitch_brand,
    llm_available,
    looks_like_brand_request,
    brand_lookup_names,
    asked_brand_query,
    leftover_looks_like_query,
    leftover_is_category,
    leftover_is_prompt,
    brain_brand_name,
    followup_brand_query,
    last_followup_brand,
    match_named_brand,
    is_brand_reject,
    candidate_looks_like_brand_name,
    claims_pitch_elsewhere,
    allow_fuzzy_brand_lookup,
    strip_brand_ask,
    is_deal_search,
    is_casual_ack,
    is_pitch_email_ask,
    is_approval_timing_ask,
    is_sent_wrong_detail,
    is_out_of_script_chat,
    named_brand_in_message,
    parse_handle_revision,
    deal_search_kind,
    deal_pool_intent,
    apply_paid_ask_to_pitch,
    apply_gifted_ask_to_pitch,
    apply_handle_revision_to_pitch,
    apply_location_to_pitch,
    last_thread_pitch,
    parse_location_reply,
    patch_last_pitch_in_history,
    pitch_has_placeholder,
    resolve_pitch_location,
    mark_draft_pending,
    mark_pitched,
    unmark_pitched,
    narrate_kit_review,
    out_of_free_unlocks,
    is_cant_afford,
    is_unlock_reset_ask,
    reset_label,
    next_unlock_brand,
    paywall_unlock_chips,
    cold_brand_chips,
    pitch_confirm_chips,
    pitch_from_followup_response,
    pitch_from_package_response,
    public_profile_summary,
    reads_like_sentence,
    requested_brand_name,
    names_mentioned_by_assistant,
    resolve_brand,
    sanitize_brand_card,
    say_claims_unconfirmed_send,
    unpack_view_result,
    wants_followup_pitch,
)
from services.polly_kit import kit_actions, kit_context, kit_reply_grounded, load_kit_snapshot, strip_kit_editor_paths
from services.polly_pain import diagnose_pain, stamp_pain
from services.polly_memory import load_thread, save_thread
from services.polly_prefs import filter_brands_by_prefs, gifted_only, merge_prefs, parse_preferences
from services.polly_discovery import (
    advance as advance_discovery,
    apply_answer,
    assign_track,
    bump_setup_continues,
    required_categories_for_match,
    detour_from_action,
    discovery_brief,
    discovery_complete,
    discovery_started,
    explain_newcollab,
    field_from_history,
    is_non_answer_chip,
    is_setup_chip_tap,
    load_onboarding_survey,
    merge_notes_patch,
    notes_context,
    opener as discovery_opener,
    paid_first,
    seed_from_survey,
    should_auto_skip_setup,
    skip_discovery,
    starters_for,
    stated_niches,
    utc_now as utc_iso_now,
)
from services.polly_opener import (
    looks_like_place,
    single_city_reply,
    state_opener,
    with_location_ask,
)
from services.polly_persona import (
    creator_first_name,
    is_robotic,
    persona_ask_brand,
    persona_brand_intro,
    persona_cant_afford,
    persona_cold_brand_warning,
    persona_first_matches,
    persona_gigs_intro,
    persona_paid_ladder,
    paywall_preview_lines,
    persona_pref_ack,
    persona_kit_after_cards,
    persona_kit_after_gigs,
    persona_followup_intro,
    persona_followup_too_early,
    persona_more_brands_intro,
    persona_off_match_pitch,
    persona_low_effort_skip,
    persona_park_draft,
    persona_paywall_retry,
    persona_paywall_say,
    persona_hold_pitch_send,
    persona_location_filled,
    persona_pitch_intro,
    persona_portfolio_review,
    persona_profile_audit,
    persona_rate_card,
    persona_thanks_after_draft,
    persona_pitch_email,
    persona_handle_fixed,
    persona_sent_wrong_detail,
    persona_approval_timing,
    persona_unknown_brand,
    persona_unlocks_after_send,
    persona_week_plan,
    say_already_logged,
    scrub_polly_voice,
    strip_embedded_pitch,
)
from services.polly_tracker import (
    FOLLOWUP_GAP,
    drop_early_followups,
    early_followup_brands,
    followup_unlocks_at,
    apply_lifecycle_intent,
    apply_task_chip,
    backfill_from_notes,
    brand_from_notes,
    brand_timeline,
    _creator_applications as creator_applications,
    classify_lifecycle_heuristic,
    list_relationships,
    load_creator_context,
    log_intent,
    lookup_brand,
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
    return 200, filter_brands_by_prefs(drop_pitched(brands, notes, extra_ids=exclude), notes), None


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
        career = career_snapshot(conn, creator_id, notes, balance)
        if not checked_in and not empty_thread and career:
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

    try:
        scrape = _load_scrape(conn, (creator or {}).get("user_id") or session.get("user_id"))
        kit = load_kit_snapshot(conn, creator_id, scrape)
        profile_context = build_profile_context(scrape, creator) + "\n\n" + kit_context(kit)
        coaching_rows = _load_pitch_coaching(conn, creator_id)
        coaching_block = pitch_coaching_context(coaching_rows)
        if coaching_block:
            profile_context += "\n\n" + coaching_block
        coach_moves = coaching_moves(coaching_rows)
        balance = _unlock_balance(creator_id, conn=conn)
        timer.mark("load")
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
        skip_flag = bool(data.get("skip_discovery"))
        chip_id = str(data.get("starter") or data.get("chip_id") or "").strip()
        notes = bump_setup_continues(notes, user_text, chip_id, explicit_action)
        pref_patch = parse_preferences(user_text) if user_text and not chip_id else {}
        notes = merge_prefs(notes, pref_patch)
        chip_deal = str(data.get("deal") or "").strip().lower()
        if chip_deal in ("gifted", "paid") and not (chip_deal == "paid" and gifted_only(notes)):
            notes["deal_intent"] = chip_deal
        named_ask = (
            looks_like_brand_request(user_text, messages)
            and not leftover_is_prompt(user_text)
            and not is_casual_ack(user_text)
            and not is_more_brands_turn(user_text)
            and not is_setup_chip_tap(user_text, chip_id, explicit_action)
            and not is_deal_search(user_text)
            and not is_out_of_script_chat(user_text)
        )
        deal_kind = deal_search_kind(user_text)
        if pref_patch:
            data["_pref_ack"] = persona_pref_ack(pref_patch)
        if pref_patch.get("gifted_only"):
            deal_kind = "gifted" if deal_kind else None
        elif deal_kind == "paid" and gifted_only(notes) and not chip_id:
            notes = merge_prefs(notes, {"gifted_only": False})
        named_in_text = named_brand_in_message(user_text, suggested, messages)
        if named_in_text and deal_kind:
            named_ask = True
        if deal_kind in ("paid", "gifted"):
            notes["deal_intent"] = deal_kind
            if deal_kind == "paid" and not notes.get("goal_30d"):
                notes["goal_30d"] = "paid UGC"
        if deal_kind and not explicit_brand_id and explicit_action not in ("generate_pitch",) and not named_in_text:
            skip_flag = True
            notes["wants_matches"] = True
            data["_deal_first_cards"] = True
        if should_auto_skip_setup(notes) and not named_ask and not is_out_of_script_chat(user_text):
            skip_flag = True
            if is_setup_chip_tap(user_text, chip_id, explicit_action):
                data["_repeat_skip"] = True
                if explicit_action in (None, "", "discovery"):
                    explicit_action = "suggest_brands"
        asked = field_from_history(messages)
        if (
            asked
            and discovery_started(notes)
            and not discovery_complete(notes)
            and not is_non_answer_chip(user_text)
            and not is_setup_chip_tap(user_text, chip_id, explicit_action)
        ):
            notes = apply_answer(notes, asked, user_text)
            if asked == "location":
                answered = parse_location_reply(user_text)
                if answered:
                    _persist_shipping_location(creator_id, answered.get("city"), answered.get("country"))
        notes = assign_track(notes, scrape)
        tracker_ctx = {}
        try:
            tracker_ctx = load_creator_context(conn, creator_id)
        except Exception as err:
            print(f"[Polly] tracker context skipped: {err}")
        pain = diagnose_pain(notes, tracker_ctx, kit, scrape)
        notes = stamp_pain(notes, pain)
        if pain:
            tracker_ctx["active_pain"] = pain
        tracker_hint = session_context_text(tracker_ctx)
        print(
            f"[Polly] turn creator={creator_id} llm={'on' if llm_available() else 'off'} "
            f"gemini={'on' if gemini_available() else 'off'} "
            f"msg={user_text[:80]!r} action={explicit_action}"
        )

        allowed_actions = (
            "suggest_gigs", "suggest_brands", "generate_pitch", "chat", "discovery",
            "explain_newcollab", "coach_week", "coach_portfolio", "coach_rates",
            "coach_profile", "ask_brand", "task_act",
        )
        last_pitch = last_pitch_brand(messages, notes)
        if explicit_action == "task_act":
            chip_res = apply_task_chip(
                conn,
                creator_id,
                (data.get("starter") or data.get("chip_id") or ""),
                task_id=data.get("task_id"),
                brand_id=explicit_brand_id,
                brand_name=data.get("brand_name"),
            )
            route = chip_res.get("route") or "chat"
            if route in allowed_actions and route != "task_act":
                explicit_action = route
            if chip_res.get("brand_id") and not explicit_brand_id:
                explicit_brand_id = chip_res.get("brand_id")
            if chip_res.get("say"):
                data["_task_say"] = chip_res.get("say")
            data["_task_chips"] = chip_res.get("chips") or []
            if chip_res.get("is_followup"):
                data["is_followup"] = True
            if chip_res.get("unmark_pitched"):
                notes = unmark_pitched(notes, {
                    "id": chip_res.get("brand_id"),
                    "name": chip_res.get("brand_name"),
                })

        discovery_plus = (discovery_brief(notes) + " " + tracker_hint).strip()
        force_for_brain = (
            explicit_action
            if explicit_action in allowed_actions and explicit_action != "task_act"
            else None
        )
        pending_early = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
        pending_for_brain = str(
            (pending_early or {}).get("name") or (pending_early or {}).get("brand_name") or ""
        ).strip()
        paywall_followup = (
            is_unlock_reset_ask(user_text)
            and isinstance(notes.get("paywall_brand"), dict)
            and not named_ask
            and explicit_action not in ("generate_pitch", "suggest_brands")
        )
        draft_for_brain = last_thread_pitch(messages)
        open_pitch = None
        if draft_for_brain:
            open_pitch = {
                "brand_name": draft_for_brain.get("brand_name") or draft_for_brain.get("name"),
                "email": draft_for_brain.get("email"),
                "needs_location": bool(
                    draft_for_brain.get("needs_location")
                    or pitch_has_placeholder(draft_for_brain.get("body") or "")
                ),
            }
        if paywall_followup:
            decision = {"intent": "chat", "say": "", "brain": False}
        else:
            decision = classify_intent(
                user_text,
                profile_context + "\n\n" + notes_context(notes) + "\n\n" + tracker_hint,
                history=messages,
                suggested_brands=suggested,
                brand_id=explicit_brand_id,
                discovery_hint=discovery_plus,
                force_intent=force_for_brain,
                pitched_names=notes.get("pitched_brand_names") or [],
                pending_draft=pending_for_brain,
                open_pitch=open_pitch,
            )
        notes = merge_notes_patch(notes, decision.get("notes_patch"))
        timer.mark("classify")

        intent = decision.get("intent") or "chat"
        from services.polly_gigs import wants_more_gigs
        chip_more_gigs = str(data.get("starter") or data.get("chip_id") or "") == "more_gigs"
        more_gigs = chip_more_gigs or (
            intent == "suggest_gigs" and wants_more_gigs(user_text, messages, notes)
        )
        if chip_more_gigs:
            intent = "suggest_gigs"
        if (
            intent == "suggest_gigs"
            and gifted_only(notes)
            and deal_kind != "paid"
            and chip_id not in ("paid_ugc", "more_gigs")
        ):
            intent = "suggest_brands"
            decision["say"] = ""
            skip_flag = True
        asked_brand = brain_brand_name(
            decision.get("brand_name") or data.get("brand_name")
        )
        pending_now = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
        pending_label = str((pending_now or {}).get("name") or (pending_now or {}).get("brand_name") or "").strip()
        typed_ask = asked_brand or ""
        if data.get("is_followup") or (
            explicit_action == "generate_pitch"
            and wants_followup_pitch(data, user_text, messages, notes.get("pitched_brand_names"))
        ):
            intent = "generate_pitch"
            data["is_followup"] = True
            pools = list(suggested or []) + names_mentioned_by_assistant(messages)
            for name in notes.get("pitched_brand_names") or []:
                pools.append({"name": name})
            chip_name = brain_brand_name(
                data.get("brand_name")
                or followup_brand_query(user_text)
                or match_named_brand(user_text, pools)
                or last_followup_brand(messages)
            ) or ""
            if chip_name:
                asked_brand = chip_name
            data["_repeat_skip"] = False
            early_name = chip_name or data.get("brand_name") or ""
            try:
                unlocks_at = followup_unlocks_at(conn, creator_id, explicit_brand_id, early_name)
            except Exception as err:
                print(f"[Polly] follow-up gate skipped: {err}")
                conn.rollback()
                unlocks_at = None
            if unlocks_at:
                held_id = explicit_brand_id
                intent = "chat"
                explicit_action = None
                explicit_brand_id = None
                named_ask = False
                asked_brand = None
                data["is_followup"] = False
                decision["brand_name"] = None
                decision["brand_id"] = None
                say = persona_followup_too_early(early_name, unlocks_at)
                data["_server_say"] = say
                data["_keep_pitch_say"] = True
                data["_keep_draft"] = True
                data["_local_say"] = True
                data["_task_chips"] = [
                    {"id": "checkin_replied", "label": f"{early_name or 'They'} replied", "action": "task_act",
                     "brand_id": held_id, "brand_name": early_name or None},
                    {"id": "line_up", "label": "Pitch another brand", "action": "suggest_brands",
                     "skip_discovery": True, "deal": "gifted"},
                ]
        if is_casual_ack(user_text) and not explicit_brand_id and (
            decision.get("brain") != "llm" or is_robotic(decision.get("say"))
        ):
            intent = "chat"
            data["_ack_draft"] = pending_label
        print(
            f"[Polly] brand-ask typed={typed_ask!r} asked={asked_brand!r} "
            f"intent={intent} pending={pending_label!r} action={explicit_action!r} "
            f"brain={decision.get('brain')!r}"
        )
        if skip_flag:
            notes = skip_discovery(notes)
            if data.get("_repeat_skip") and explicit_action in ("suggest_brands", "suggest_gigs"):
                intent = explicit_action
        notes = assign_track(notes, scrape)

        brands = []
        gigs = []
        gigs_has_more = False
        pitch = None
        paywall = False
        paywall_payload = None
        error = None
        say = ""
        wrap_say = persona_low_effort_skip() if data.get("_repeat_skip") else ""
        live_brain = bool(decision.get("brain")) and not is_robotic(decision.get("say"))
        paywall_brand = notes.get("paywall_brand") if isinstance(notes.get("paywall_brand"), dict) else None
        if (
            is_unlock_reset_ask(user_text)
            and paywall_brand
            and explicit_action not in ("generate_pitch", "suggest_brands")
            and not named_ask
        ):
            intent = "chat"
            live_brain = False
            say = persona_paywall_retry(paywall_brand.get("name") or paywall_brand.get("brand_name"))
            data["_task_chips"] = paywall_unlock_chips(paywall_brand)
            data["_keep_pitch_say"] = True
            data["_paywall_moment"] = {"moment": "retry", "brand_id": paywall_brand.get("id")}
        if (
            is_cant_afford(user_text)
            and not balance.get("is_unlimited")
            and explicit_action not in ("generate_pitch", "suggest_brands", "suggest_gigs")
        ):
            intent = "chat"
            live_brain = False
            named_ask = False
            asked_brand = None
            decision["brand_name"] = None
            decision["brand_id"] = None
            follow = follow_brand_from_tracker(tracker_ctx, notes)
            say = persona_cant_afford(
                reset_label(balance),
                (follow or {}).get("name"),
                kit_live=bool((kit or {}).get("published")),
            )
            data["_server_say"] = say
            data["_keep_pitch_say"] = True
            data["_keep_draft"] = True
            data["_local_say"] = True
            chips = []
            if follow and follow.get("name"):
                chips.append({
                    "id": "draft_followup",
                    "label": f"Draft {follow['name']} follow-up",
                    "action": "generate_pitch",
                    "brand_id": follow.get("id"),
                    "brand_name": follow.get("name"),
                    "is_followup": True,
                })
            if not (kit or {}).get("published"):
                chips.append({"id": "portfolio", "label": "Help me publish my kit", "action": "coach_portfolio"})
            chips.append({"id": "week_plan", "label": "What should I do this week?", "action": "coach_week"})
            data["_task_chips"] = chips
        if data.get("_repeat_skip") or (
            is_setup_chip_tap(user_text, chip_id, explicit_action) and not skip_flag
        ) or explicit_action in ("ask_brand", "coach_profile"):
            live_brain = False
            if explicit_action == "discovery":
                intent = "discovery"
            elif explicit_action in ("ask_brand", "coach_profile"):
                intent = explicit_action

        last_pitch = last_pitch_brand(messages, notes) or brand_from_notes(notes)
        draft_pitch = last_thread_pitch(messages)
        draft_needs_location = pitch_has_placeholder((draft_pitch or {}).get("body") or "")
        loc_reply = parse_location_reply(user_text) if draft_needs_location else None
        if (
            loc_reply
            and draft_pitch
            and explicit_action not in ("generate_pitch", "suggest_brands", "suggest_gigs")
            and not is_done_turn(user_text)
        ):
            updated = apply_location_to_pitch(
                draft_pitch,
                loc_reply.get("city") or "",
                loc_reply.get("country") or "",
            )
            loc_display = str((updated or {}).get("location_display") or "")
            notes["location"] = loc_display or f"{loc_reply.get('city')}, {loc_reply.get('country')}"
            _persist_shipping_location(creator_id, loc_reply.get("city"), loc_reply.get("country"))
            messages = patch_last_pitch_in_history(messages, updated)
            data["_filled_location"] = True
            data["_pitch_update"] = updated
            data["_keep_pitch_say"] = True
            data["_task_chips"] = pitch_confirm_chips({
                "id": (updated or {}).get("brand_id") or (last_pitch or {}).get("id"),
                "name": (updated or {}).get("brand_name") or (last_pitch or {}).get("name"),
            })
            intent = "chat"
            asked_brand = None
            named_ask = False
            decision["brand_name"] = None
            decision["brand_id"] = None
            say = persona_location_filled(
                (updated or {}).get("brand_name") or (last_pitch or {}).get("name"),
                loc_display,
            )
        awaiting_loc = bool(notes.pop("awaiting_location", None))
        if (
            awaiting_loc
            and not data.get("_filled_location")
            and user_text
            and not chip_id
            and explicit_action in (None, "", "chat")
            and looks_like_place(user_text)
        ):
            place = parse_location_reply(user_text) or single_city_reply(
                user_text, str((creator or {}).get("country") or "")
            )
            if place:
                display = f"{place['city']}, {place['country']}"
                notes["location"] = display
                _persist_shipping_location(creator_id, place["city"], place["country"])
                intent = "chat"
                asked_brand = None
                named_ask = False
                decision["brand_name"] = None
                decision["brand_id"] = None
                say = (
                    f"Saved — every pitch will say you ship from **{display}**, "
                    "so they're ready to send the moment I write them.\n\n"
                    "What do you want to land first?"
                )
                data["_server_say"] = say
                data["_local_say"] = True
                data["_task_chips"] = starters_for(notes)[:3]
        draft_name = (
            (draft_pitch or {}).get("brand_name")
            or (last_pitch or {}).get("name")
            or (last_pitch or {}).get("brand_name")
            or pending_label
        )
        gem_ok = (
            bool((decision.get("say") or "").strip())
            and not is_robotic(decision.get("say"))
            and "isn't in our directory" not in (decision.get("say") or "").lower()
            and "closest we do have" not in (decision.get("say") or "").lower()
        )
        if (
            not data.get("_filled_location")
            and explicit_action not in ("generate_pitch", "suggest_brands", "suggest_gigs")
            and is_pitch_email_ask(user_text)
        ):
            intent = "chat"
            data["_keep_pitch_say"] = True
            data["_keep_draft"] = True
            data["_task_chips"] = pitch_confirm_chips({
                "id": (draft_pitch or {}).get("brand_id") or (last_pitch or {}).get("id"),
                "name": draft_name,
            })
            if not gem_ok:
                say = persona_pitch_email(draft_name, (draft_pitch or {}).get("email"))
                live_brain = False
                data["_local_say"] = True
            else:
                data["_keep_draft"] = True
        elif (
            not data.get("_filled_location")
            and draft_pitch
            and explicit_action not in ("generate_pitch", "suggest_brands", "suggest_gigs")
        ):
            handle_fix = parse_handle_revision(user_text)
            if handle_fix:
                updated = apply_handle_revision_to_pitch(
                    draft_pitch, handle_fix.get("keep") or "", handle_fix.get("drop") or "",
                )
                messages = patch_last_pitch_in_history(messages, updated)
                data["_pitch_update"] = updated
                data["_keep_pitch_say"] = True
                data["_keep_draft"] = True
                data["_task_chips"] = pitch_confirm_chips({
                    "id": (updated or {}).get("brand_id") or (last_pitch or {}).get("id"),
                    "name": (updated or {}).get("brand_name") or draft_name,
                })
                intent = "chat"
                if not gem_ok:
                    live_brain = False
                    data["_local_say"] = True
                    say = persona_handle_fixed(
                        (updated or {}).get("brand_name") or draft_name,
                        handle_fix.get("keep") or "",
                        handle_fix.get("drop") or "",
                    )
            elif is_sent_wrong_detail(user_text):
                intent = "chat"
                data["_keep_draft"] = True
                if not gem_ok:
                    handle_fix = parse_handle_revision(user_text)
                    keep = (handle_fix or {}).get("keep") or ""
                    drop = (handle_fix or {}).get("drop") or ""
                    live_brain = False
                    data["_local_say"] = True
                    say = persona_sent_wrong_detail(draft_name, keep, drop)
            elif is_approval_timing_ask(user_text):
                intent = "chat"
                data["_keep_draft"] = True
                if not gem_ok:
                    live_brain = False
                    data["_local_say"] = True
                    say = persona_approval_timing(draft_name)
        if last_pitch and conn:
            found = lookup_brand(
                conn,
                last_pitch.get("id") or last_pitch.get("brand_id"),
                last_pitch.get("name") or last_pitch.get("brand_name"),
            )
            if found:
                last_pitch = {
                    "id": found["id"],
                    "brand_id": found["id"],
                    "name": found["name"],
                    "brand_name": found["name"],
                }
        life = classify_lifecycle_heuristic(
            user_text,
            list(suggested or []) + [
                {"id": t.get("brand_id"), "name": t.get("brand_name")}
                for t in (tracker_ctx.get("active_tasks") or [])
            ],
            last_pitch,
        )
        life_result = {}
        skip_life = {"no_action", "casual_chat", "ask_for_brands", "ask_for_help"}
        more_brands_turn = is_more_brands_turn(user_text) and explicit_action not in ("generate_pitch",)
        if named_ask:
            more_brands_turn = False
        elif (
            pending_label
            and explicit_action == "suggest_brands"
            and not is_done_turn(user_text)
        ):
            more_brands_turn = True
        checkin_handled = str(data.get("chip_id") or data.get("starter") or "").startswith("checkin_")
        if (
            not paywall_followup
            and not paywall
            and not more_brands_turn
            and not checkin_handled
            and intent not in ("suggest_gigs",)
            and not is_deal_search(user_text)
            and life.get("confidence", 0) >= 0.7
            and life.get("intent") not in skip_life
            and not (life.get("intent") == "pitch_sent" and not last_pitch)
            and not data.get("_filled_location")
            and not data.get("_local_say")
            and intent not in ("chat", "coach_profile", "ask_brand")
            and not (life.get("intent") == "pitch_sent" and draft_needs_location)
        ):
            try:
                life_brand = last_pitch or resolve_brand(
                    suggested,
                    brand_id=life.get("brand_id") or explicit_brand_id,
                    brand_name=life.get("brand") or data.get("brand_name"),
                ) or {"id": life.get("brand_id"), "name": life.get("brand")}
                life_result = apply_lifecycle_intent(conn, creator_id, life, life_brand)
                log_intent(conn, creator_id, user_text, life, life_result.get("action") or "")
            except Exception as err:
                print(f"[Polly] lifecycle apply skipped: {err}")
        elif life.get("intent") not in ("no_action",):
            try:
                log_intent(conn, creator_id, user_text, life, "logged")
            except Exception:
                pass

        if is_done_turn(user_text) and last_pitch:
            if draft_needs_location:
                data["_hold_placeholder"] = True
                data["_keep_pitch_say"] = True
                data["_task_chips"] = pitch_confirm_chips(last_pitch)
                intent = "chat"
                say = persona_hold_pitch_send(
                    last_pitch.get("name") or last_pitch.get("brand_name")
                )
            else:
                from services.polly import pitched_id_set
                try:
                    already_logged = int(last_pitch.get("id") or last_pitch.get("brand_id") or 0) in pitched_id_set(notes)
                except (TypeError, ValueError):
                    already_logged = False
                notes = mark_pitched(notes, last_pitch)
                if already_logged and not life_result:
                    life_result = {
                        "action": "pitch_sent+followups",
                        "say_hint": (
                            f"Already logged. **{last_pitch.get('name') or last_pitch.get('brand_name')}** "
                            "is on your Timeline — I'll check if they replied on day 4."
                        ),
                    }
                if (life_result or {}).get("action") not in ("pitch_sent+followups",):
                    try:
                        record_pitch_sent(conn, creator_id, last_pitch, drafted=False)
                        if not life_result:
                            life_result = {
                                "action": "pitch_sent+followups",
                                "say_hint": (
                                    f"Logged. **{last_pitch.get('name') or last_pitch.get('brand_name')}** "
                                    "is on your Timeline — I'll nudge you day 4 if they're quiet."
                                ),
                                "chips": [],
                            }
                    except Exception as err:
                        print(f"[Polly] pitch tracker skipped: {err}")
                if not already_logged:
                    data["_sent_now"] = True
                ready_at = datetime.now(timezone.utc) + FOLLOWUP_GAP
                for key in (last_pitch.get("id") or last_pitch.get("brand_id"),
                            last_pitch.get("name") or last_pitch.get("brand_name")):
                    if key:
                        early_fu.setdefault(str(key).strip().lower(), ready_at)
                if intent == "suggest_brands" and not explicit_action:
                    intent = "chat"
                balance = _unlock_balance(creator_id)
                unlock_line = persona_unlocks_after_send(balance)
                if unlock_line:
                    data["_unlock_after_send"] = unlock_line
                leftover_cards = drop_pitched(suggested, notes)
                next_pro = next_unlock_brand(last_pitch, leftover_cards, pending_now)
                if out_of_free_unlocks(balance) and not data.get("_task_chips"):
                    data["_task_chips"] = paywall_unlock_chips(next_pro)
                    data["_after_send_empty"] = True
                    data["_paywall_moment"] = {"moment": "after_send", "brand_id": (next_pro or {}).get("id")}

        remaining = drop_pitched(suggested, notes)
        progress_remaining = False
        if (
            is_done_turn(user_text)
            and intent == "chat"
            and remaining
            and not data.get("_hold_placeholder")
        ):
            if out_of_free_unlocks(balance):
                progress_remaining = False
            else:
                brands = _hydrate_brand_cards(remaining[:3])
                progress_remaining = True

        if (life_result or {}).get("unmark_pitched"):
            notes = unmark_pitched(notes, last_pitch or {
                "id": life_result.get("brand_id"),
                "name": life_result.get("brand_name"),
            })
        try:
            tracker_ctx = load_creator_context(conn, creator_id)
        except Exception:
            pass
        pain = diagnose_pain(notes, tracker_ctx, kit, scrape)
        notes = stamp_pain(notes, pain)
        if pain:
            tracker_ctx["active_pain"] = pain

        wants_matches = intent in ("suggest_brands", "suggest_gigs") or notes.get("wants_matches")
        open_discovery = not discovery_complete(notes)
        can_match = (
            discovery_complete(notes)
            or skip_flag
            or bool(stated_niches(notes))
            or bool((scrape or {}).get("primary_niche"))
        )

        if live_brain and not data.get("_filled_location") and not data.get("_hold_placeholder") and not data.get("_local_say"):
            say = decision.get("say") or ""
            if wants_matches:
                notes["wants_matches"] = True
            if (
                open_discovery
                and not discovery_started(notes)
                and not skip_flag
                and not deal_kind
                and not is_casual_ack(user_text)
            ):
                notes["discovery_step"] = 1
        elif intent == "explain_newcollab":
            say = explain_newcollab(first)
        elif intent == "coach_portfolio":
            say = _coach_say(intent, profile_context, notes, scrape, kit=kit, coach_moves=coach_moves)
        elif intent in ("coach_profile", "ask_brand"):
            say = _coach_say(intent, profile_context, notes, scrape, kit=kit, coach_moves=coach_moves)
        elif intent in ("coach_week", "coach_rates") and not open_discovery:
            say = _coach_say(intent, profile_context, notes, scrape, kit=kit, coach_moves=coach_moves)
        elif (
            open_discovery
            and not is_casual_ack(user_text)
            and not data.get("_filled_location")
            and not data.get("_hold_placeholder")
            and not data.get("_local_say")
            and intent not in (
            "explain_newcollab", "generate_pitch", "ask_brand", "coach_profile",
            "suggest_brands", "suggest_gigs",
        )
        ):
            if wants_matches:
                notes["wants_matches"] = True
            notes, say, pull = advance_discovery(
                notes, user_text, first_name=first, history=messages,
            )
            if wants_matches and not discovery_started(stored.get("notes") or {}):
                say = detour_from_action() + say
            intent = "discovery"
            if pull and notes.get("wants_matches"):
                wrap_say = say
                intent = "suggest_brands"
            elif pull:
                intent = "discovery"

        chip_key = str(data.get("starter") or data.get("chip_id") or "")
        if intent == "suggest_gigs" and chip_key not in ("paid_anyway", "more_gigs"):
            from services.polly_gigs import is_small_creator, wants_more_gigs as _wants_more
            if is_small_creator(scrape) and not _wants_more(user_text, messages, notes):
                # Paid briefs at this size are mostly agency work; redirect to the gifted ladder.
                intent = "suggest_brands"
                can_match = True
                data["_paid_ladder"] = True
                notes["wanted_gigs"] = True
                if not gifted_only(notes):
                    notes["deal_intent"] = "gifted"

        if intent == "suggest_gigs":
            try:
                from services.polly_gigs import page_polly_gigs, mark_shown_gigs, wants_more_gigs
                more_gigs = wants_more_gigs(user_text, messages, notes) or str(
                    data.get("starter") or data.get("chip_id") or ""
                ) == "more_gigs"
                fresh = str(data.get("starter") or data.get("chip_id") or "") == "paid_ugc" and not more_gigs
                if fresh:
                    notes["shown_gig_ids"] = []
                gigs, gigs_has_more = page_polly_gigs(
                    creator_id,
                    scrape=scrape,
                    notes=notes,
                    history=messages,
                )
            except Exception as err:
                print(f"[Polly] gigs failed: {err}")
                gigs = []
                more_gigs = False
                gigs_has_more = False
            print(f"[Polly] gigs n={len(gigs)} more={more_gigs}")
            brands = []
            notes["wanted_gigs"] = True
            if gigs:
                notes["saw_gigs"] = True
                notes = mark_shown_gigs(notes, gigs)
            fallback_say = persona_gigs_intro(gigs, more=more_gigs)
            gem_say = (decision.get("say") or "").strip()
            if live_brain and gem_say and not is_robotic(gem_say):
                say = gem_say
            else:
                say = fallback_say
            kit_line = persona_kit_after_gigs(kit) if gigs else ""
            if kit_line and kit_line.lower() not in (say or "").lower():
                say = f"{say}\n\n{kit_line}"
            if wrap_say:
                say = wrap_say + "\n\n" + say

        if intent == "suggest_brands" and can_match and not progress_remaining:
            if conn:
                conn.close()
                conn = None
            timer.mark("route")
            try:
                status, brands, err = _suggest_payload(scrape, creator, notes=notes, creator_id=creator_id)
            except Exception as err:
                print(f"[Polly] suggest failed: {err}")
                status, brands, err = 200, [], str(err)[:180]
            timer.mark("match")
            if status == 401:
                return jsonify({"success": False, "error": "Not authenticated"}), 401
            if err and not brands:
                error = err
            brands = drop_pending_draft(brands, notes)
            if not brands:
                brands = drop_pending_draft(drop_pitched(suggested, notes), notes)
            brands = filter_brands_by_prefs(_hydrate_brand_cards(brands), notes)
            pending_name = ""
            pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
            if pending:
                pending_name = pending.get("name") or pending.get("brand_name") or ""
            try:
                if more_brands_turn:
                    fallback_say = persona_more_brands_intro(brands, pending_name, profile_context)
                    say = fallback_say
                else:
                    fallback_say = persona_brand_intro(
                        brands, profile_context, deal_intent=notes.get("deal_intent"),
                    )
                    llm_say = decision.get("say") if not is_robotic(decision.get("say")) else ""
                    say = llm_say or fallback_say
                    if is_robotic(say) or say_claims_unconfirmed_send(say) or "isn't in our directory" in (say or "").lower():
                        say = fallback_say
                    if brands and (
                        data.get("_deal_first_cards") or notes.get("deal_intent") == "paid"
                    ) and not more_brands_turn:
                        kit_line = persona_kit_after_cards(
                            kit, deal_intent=notes.get("deal_intent"),
                        )
                        if kit_line and kit_line.lower() not in (say or "").lower():
                            say = f"{say}\n\n{kit_line}"
            except Exception as err:
                print(f"[Polly] suggest narrate skipped: {err}")
                say = persona_more_brands_intro(brands, pending_name, profile_context) if pending_name else persona_brand_intro(brands, profile_context, deal_intent=notes.get("deal_intent"))
            live_first = sorted(
                brands,
                key=lambda b: 0 if (b or {}).get("source") in ("recruiting", "open_lists") else 1,
            )
            if data.get("_paid_ladder"):
                brands = live_first[:2]
                say = persona_paid_ladder(brands)
                ladder_chips = [
                    {
                        "id": "pitch_ladder",
                        "label": f"Pitch {b['name']}",
                        "action": "generate_pitch",
                        "brand_id": b.get("id"),
                        "brand_name": b["name"],
                    }
                    for b in brands if b.get("name")
                ]
                ladder_chips.append({
                    "id": "paid_anyway",
                    "label": "Show paid briefs anyway",
                    "action": "suggest_gigs",
                    "skip_discovery": True,
                })
                data["_task_chips"] = ladder_chips
            elif chip_key == "first_matches":
                brands = live_first[:3]
                live = any((b or {}).get("source") in ("recruiting", "open_lists") for b in brands)
                niche = (
                    (stated_niches(notes) or [None])[0]
                    or (scrape or {}).get("primary_niche")
                    or ""
                )
                say = persona_first_matches(
                    first, notes.get("survey"), brands, niche=str(niche).strip().lower(), live=live,
                )
                shipping = resolve_pitch_location(creator, scrape, notes)
                if shipping.get("needs_location") and not notes.get("location_asked_at"):
                    say = with_location_ask(say)
                    notes["location_asked_at"] = utc_iso_now()
                    notes["awaiting_location"] = True
            if not brands and not say:
                say = (
                    "I couldn't pull a fresh list just now. Tap again in a second, "
                    "or pick from the cards already here."
                )
            if wrap_say:
                say = wrap_say + "\n\n" + say
        elif intent == "generate_pitch":
            asked_name = brain_brand_name(
                asked_brand or decision.get("brand_name") or data.get("brand_name")
            )
            prior_draft = pending_label
            lookup_id = explicit_brand_id or decision.get("brand_id")
            resolved = resolve_brand(
                suggested,
                brand_id=lookup_id,
                brand_name=asked_name,
            )
            in_pool = bool(resolved)
            sentence_ask = bool(
                not lookup_id
                and asked_name
                and reads_like_sentence(asked_name)
            )
            if not resolved and not sentence_ask:
                pooled = _lookup_published_brand(
                    conn,
                    brand_id=lookup_id,
                    brand_name=asked_name,
                )
                resolved = pooled
            if conn:
                conn.close()
                conn = None
            if not resolved:
                gem = (decision.get("say") or say or "").strip()
                if gem and not is_robotic(gem) and "isn't in our directory" not in gem.lower():
                    intent = "chat"
                    brands = []
                    say = gem
                    data["_keep_draft"] = True
                elif (
                    asked_name
                    and candidate_looks_like_brand_name(asked_name)
                    and not reads_like_sentence(asked_name)
                    and not reads_like_sentence(user_text)
                ):
                    try:
                        alts = _lookup_similar_brands(
                            asked_name,
                            scrape=scrape,
                            notes=notes,
                            creator=creator,
                            creator_id=creator_id,
                            exclude_ids=notes.get("pitched_brand_ids") or [],
                        )
                    except Exception as err:
                        print(f"[Polly] similar brands skipped: {err}")
                        alts = []
                    brands = _hydrate_brand_cards(alts)
                    say = persona_park_draft(prior_draft, asked_name) + persona_unknown_brand(asked_name, brands)
                else:
                    intent = "chat"
                    brands = []
                    if not gem and user_text and not explicit_brand_id:
                        try:
                            gem = chat_reply(
                                profile_context + "\n\n" + notes_context(notes),
                                user_text,
                                history=messages,
                                first_name=first,
                                discovery_hint=discovery_plus,
                            )
                        except Exception as err:
                            print(f"[Polly] sentence fallback skipped: {err}")
                            gem = ""
                        if is_robotic(gem) or "isn't in our directory" in (gem or "").lower():
                            gem = ""
                    say = gem or (
                        "I didn't catch a brand name there. Tell me the company and I'll pull "
                        "the inbox or draft the pitch."
                    )
                    data["_keep_draft"] = True
            else:
                wants_followup = wants_followup_pitch(
                    data, user_text, messages, notes.get("pitched_brand_names")
                )
                pitch_paid = (
                    notes.get("deal_intent") == "paid"
                    and not gifted_only(notes)
                    and (paid_first(notes) or deal_kind == "paid")
                )
                shipping = resolve_pitch_location(creator, scrape, notes)
                loc_display = ""
                if not shipping.get("needs_location"):
                    loc_display = str(shipping.get("display") or "").strip()
                    if pitch_has_placeholder(loc_display):
                        loc_display = ""
                cold = {}
                if not wants_followup and not data.get("confirm_cold") and creator_id:
                    try:
                        from pr_crm_routes import cold_spend_warning
                        cold = cold_spend_warning(creator_id, resolved.get("id")) or {}
                    except Exception as err:
                        print(f"[Polly] cold check skipped: {err}")
                        cold = {}
                if cold.get("warn"):
                    status, pkg = 200, {"_cold": cold}
                elif wants_followup:
                    status, pkg = _invoke_generate_followup(resolved.get("id"), resolved.get("slug"))
                else:
                    status, pkg = _invoke_generate_pr_package(
                        resolved.get("id"),
                        resolved.get("slug"),
                        city=shipping.get("city") or "",
                        country=shipping.get("country") or "",
                    )
                if status == 402 or pkg.get("paywall"):
                    paywall = True
                    brand_name = (resolved.get("name") or asked_name or "them").strip()
                    notes["paywall_brand"] = {
                        "id": resolved.get("id"),
                        "name": brand_name,
                    }
                    paywall_payload = {
                        "remaining": pkg.get("remaining", 0),
                        "reset_at": pkg.get("reset_at"),
                        "message": pkg.get("message") or "You've used all 3 unlocks this month.",
                        "brand_name": brand_name,
                        "brand_id": resolved.get("id"),
                        "preview": paywall_preview_lines(
                            brand_name,
                            first,
                            str((stated_niches(notes) or [None])[0] or (scrape or {}).get("primary_niche") or "").strip().lower(),
                        ),
                    }
                    data["_paywall_moment"] = {
                        "brand_id": resolved.get("id"), "moment": "pitch", "followup": bool(wants_followup),
                    }
                    views_n, sent_n = _pipeline_proof(tracker_ctx)
                    say = persona_paywall_say(brand_name, kit_views=views_n, sent=sent_n)
                    data["_server_say"] = say
                    data["_task_chips"] = paywall_unlock_chips({
                        "id": resolved.get("id"),
                        "name": brand_name,
                    })
                    data["_keep_pitch_say"] = True
                    balance = _unlock_balance(creator_id)
                elif status == 401:
                    return jsonify({"success": False, "error": "Not authenticated"}), 401
                elif pkg.get("_cold"):
                    cold = pkg["_cold"]
                    brand_name = (resolved.get("name") or asked_name or "this brand").strip()
                    brands = _hydrate_brand_cards(cold.get("alternatives") or [])
                    say = persona_cold_brand_warning(
                        brand_name, cold.get("signal"), brands, cold.get("remaining"),
                    )
                    data["_server_say"] = say
                    data["_keep_draft"] = True
                    data["_task_chips"] = cold_brand_chips(
                        {"id": resolved.get("id"), "name": brand_name}, brands,
                    )
                    _log_polly_event(
                        creator_id, "cold_brand_warned",
                        {"brand_id": resolved.get("id"), "alternatives": len(brands)},
                    )
                elif not pkg.get("success"):
                    error = pkg.get("error") or "Could not generate pitch"
                    print(f"[Polly] pitch failed status={status} error={error}")
                    say = (
                        "I couldn't draft that follow-up. Try again in a moment."
                        if wants_followup
                        else "I couldn't generate that pitch. Try another brand from the list."
                    )
                else:
                    if wants_followup:
                        pitch = pitch_from_followup_response(pkg, resolved)
                    else:
                        pitch = pitch_from_package_response(pkg)
                    if pitch and pitch_paid and not wants_followup:
                        pitch = apply_paid_ask_to_pitch(
                            pitch,
                            kit=kit,
                            scrape=scrape,
                            location_display=loc_display or None,
                        )
                    elif pitch and notes.get("deal_intent") == "gifted" and not wants_followup:
                        pitch = apply_gifted_ask_to_pitch(
                            pitch,
                            location_display=loc_display or None,
                        )
                    if pitch and not wants_followup:
                        if loc_display:
                            pitch = apply_location_to_pitch(
                                pitch,
                                shipping.get("city") or "",
                                shipping.get("country") or "",
                                loc_display,
                            )
                        pitch["needs_location"] = pitch_has_placeholder(pitch.get("body") or "")
                        if loc_display and not pitch["needs_location"]:
                            pitch["location_display"] = loc_display
                    if not pitch:
                        say = (
                            "I couldn't draft that follow-up. Try again in a moment."
                            if wants_followup
                            else "I couldn't generate that pitch. Try another brand from the list."
                        )
                        data["_keep_pitch_say"] = True
                    else:
                        pitch["brand_id"] = pitch.get("brand_id") or resolved.get("id")
                        pitch["brand_name"] = pitch.get("brand_name") or resolved.get("name")
                        if wants_followup:
                            pitch["is_followup"] = True
                        else:
                            _record_polly_draft(
                                creator_id,
                                pitch.get("brand_id"),
                                pitch.get("brand_name"),
                                credit_used=int(pkg.get("credits_used") or 0) > 0,
                            )
                        brand_name = pitch.get("brand_name") or resolved.get("name") or "them"
                        notes = mark_draft_pending(notes, {
                            "id": resolved.get("id") or pitch.get("brand_id"),
                            "name": brand_name,
                        })
                        off_match = (
                            not named_ask
                            and not named_in_text
                            and not in_pool
                            and not brand_in_suggested(suggested, resolved)
                        )
                        needs_loc = pitch_has_placeholder(pitch.get("body") or "")
                        pitch["needs_location"] = needs_loc
                        shown_loc = "" if needs_loc else (
                            pitch.get("location_display") or loc_display or ""
                        )
                        gem_say = (decision.get("say") or "").strip()
                        if (
                            live_brain
                            and gem_say
                            and not is_robotic(gem_say)
                            and "isn't in our directory" not in gem_say.lower()
                            and "[city, country]" not in gem_say.lower()
                        ):
                            fallback_say = gem_say
                        elif wants_followup:
                            fallback_say = persona_followup_intro(
                                brand_name, has_mailto=bool(pitch.get("mailto"))
                            )
                        elif off_match:
                            fallback_say = persona_off_match_pitch(
                                brand_name,
                                has_mailto=bool(pitch.get("mailto")),
                                paid=pitch_paid,
                                kit=kit,
                                needs_location=needs_loc,
                                location_display=shown_loc,
                            )
                        else:
                            fallback_say = persona_pitch_intro(
                                brand_name,
                                has_mailto=bool(pitch.get("mailto")),
                                paid=pitch_paid,
                                kit=kit,
                                needs_location=needs_loc,
                                location_display=shown_loc,
                            )
                        say = persona_park_draft(prior_draft, brand_name) + fallback_say
                        coach_line = "" if wants_followup else pitch_coach_line(pkg, brand_name)
                        if coach_line:
                            say = f"{say}\n\n{coach_line}"
                        data["_keep_pitch_say"] = True
                        data["_task_chips"] = pitch_confirm_chips({
                            "id": resolved.get("id"),
                            "name": brand_name,
                        })
                    balance = _unlock_balance(creator_id)
        elif not say:
            if conn:
                conn.close()
                conn = None
            if intent in ("coach_week", "coach_portfolio", "coach_rates", "coach_profile", "ask_brand", "explain_newcollab"):
                say = _coach_say(intent, profile_context, notes, scrape, kit=kit, coach_moves=coach_moves)
            else:
                say = decision.get("say")
                if is_robotic(say):
                    say = chat_reply(
                        profile_context,
                        user_text,
                        history=messages,
                        first_name=first,
                        discovery_hint=discovery_brief(notes),
                    )

        if "_ack_draft" in data:
            label = str(data.get("_ack_draft") or "").strip()
            fallback = (
                persona_thanks_after_draft(label)
                if label
                else "Anytime. What do you want to do next?"
            )
            gem = (say or decision.get("say") or "").strip()
            if (
                not gem
                or is_robotic(gem)
                or "isn't in our directory" in gem.lower()
                or "closest we do have" in gem.lower()
                or claims_pitch_elsewhere(gem)
            ):
                say = fallback
            brands = []
            pitch = None

        if leftover_is_prompt(user_text) and intent not in (
            "generate_pitch", "suggest_gigs", "suggest_brands",
        ) and not explicit_brand_id and not data.get("_keep_draft"):
            brands = []
            pitch = None
            gem = (say or decision.get("say") or "").strip()
            if gem and not is_robotic(gem) and "isn't in our directory" not in gem.lower():
                say = gem
            elif intent in ("coach_profile", "coach_week", "coach_rates", "coach_portfolio", "ask_brand"):
                say = _coach_say(intent, profile_context, notes, scrape, kit=kit, coach_moves=coach_moves)
            elif gem:
                say = gem

        kit_cta = []
        if intent in ("coach_portfolio", "coach_profile"):
            keep_gemini = (
                leftover_is_prompt(user_text)
                and say
                and not is_robotic(say)
                and "isn't in our directory" not in (say or "").lower()
            )
            fallback_say = persona_portfolio_review(profile_context, kit)
            if not keep_gemini and not kit_reply_grounded(say, kit):
                say = narrate_kit_review(
                    profile_context,
                    user_text,
                    history=messages,
                    kit=kit,
                    fallback=fallback_say,
                    discovery_hint=discovery_brief(notes),
                )
            if not keep_gemini and not kit_reply_grounded(say, kit):
                say = fallback_say
            kit_cta = kit_actions(kit)

        say = scrub_polly_voice(say)
        raw_say = say
        say = strip_kit_editor_paths(say)
        if not kit_cta and (
            say != raw_say
            or re.search(r"(?i)(open your kit|publish.{0,40}kit|my kit)", say or "")
        ):
            kit_cta = kit_actions(kit)
        pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
        if (
            pending
            and not is_done_turn(user_text)
            and say_claims_unconfirmed_send(say)
            and not (
                asked_brand
                and asked_brand.strip().lower() != str(pending.get("name") or pending.get("brand_name") or "").strip().lower()
            )
        ):
            say = persona_more_brands_intro(
                brands,
                pending.get("name") or pending.get("brand_name") or "",
                profile_context,
            )
        if pitch and not data.get("_keep_pitch_say"):
            cleaned = strip_embedded_pitch(say)
            say = cleaned or persona_pitch_intro(
                (pitch.get("brand_name") or "them"),
                has_mailto=bool(pitch.get("mailto")),
            )
        elif pitch:
            say = strip_embedded_pitch(say) or say
        task_chips = list(data.get("_task_chips") or [])
        if paywall or paywall_followup:
            wrap_say = ""
        if (
            life_result.get("say_hint")
            and not more_brands_turn
            and not paywall
            and not paywall_followup
            and not data.get("_hold_placeholder")
            and not data.get("_filled_location")
            and intent not in ("suggest_gigs",)
            and not is_deal_search(user_text)
        ):
            hint = scrub_polly_voice(life_result["say_hint"])
            if (
                hint
                and hint.lower() not in (say or "").lower()
                and not say_already_logged(say)
            ):
                say = (say + "\n\n" + hint).strip() if say else hint
            task_chips = task_chips or list(life_result.get("chips") or [])
        if data.get("_task_say") and not say:
            say = scrub_polly_voice(data["_task_say"])
        if wrap_say and wrap_say not in (say or ""):
            say = (wrap_say + "\n\n" + (say or "")).strip()
        unlock_line = data.get("_unlock_after_send") or ""
        if unlock_line and unlock_line.lower() not in (say or "").lower():
            say = ((say or "") + "\n\n" + unlock_line).strip()
        if data.get("_server_say"):
            say = data["_server_say"]
        pref_ack = data.get("_pref_ack") or ""
        if pref_ack and pref_ack.lower() not in (say or "").lower():
            say = (pref_ack + "\n\n" + (say or "")).strip()

        if data.get("_hold_placeholder"):
            say = persona_hold_pitch_send(
                (last_pitch or {}).get("name") or (last_pitch or {}).get("brand_name")
            )
            pitch = None
        elif data.get("_filled_location"):
            update = data.get("_pitch_update") or {}
            say = persona_location_filled(
                update.get("brand_name") or (last_pitch or {}).get("name"),
                update.get("location_display"),
            )
            pitch = update or None

        brands = filter_brands_by_prefs(_hydrate_brand_cards(brands), notes)
        queue = filter_brands_by_prefs(_hydrate_brand_cards(drop_pitched(brands or suggested, notes)), notes)
        task_chips = drop_early_followups(task_chips, early_fu)
        payload = {
            "success": not error,
            "message": say,
            "intent": intent,
            "brands": json_safe(brands),
            "gigs": json_safe(gigs),
            "gigs_has_more": bool(gigs_has_more),
            "suggested_brands": json_safe(queue),
            "pitch": json_safe(pitch),
            "pitch_update": json_safe(data.get("_pitch_update")),
            "kit_actions": json_safe(kit_cta),
            "task_chips": json_safe(task_chips),
            "paywall": paywall,
            "starters": (
                _after_send_empty_starters(notes, tracker_ctx, data.get("_task_chips") or [])
                if data.get("_after_send_empty")
                else (
                    empty_unlock_open(first, tracker_ctx, notes)["starters"]
                    if out_of_free_unlocks(balance)
                    else starters_for(
                        notes,
                        top_brand=(queue[0]["name"] if queue else None),
                    )
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
        payload["starters"] = drop_early_followups(payload["starters"], early_fu)
        if career and data.get("_sent_now"):
            totals = dict(career.get("totals") or {})
            totals["pitched"] = int(totals.get("pitched") or 0) + 1
            totals["pitched_month"] = int(totals.get("pitched_month") or 0) + 1
            career = career_from_counts(totals, notes, pitch_cap_for(balance))
        if career:
            payload["career"] = career
        if paywall_payload:
            payload["paywall_payload"] = paywall_payload
        assistant = {
            "role": "assistant",
            "content": say,
            "brands": json_safe(brands) or [],
            "gigs": json_safe(gigs) or [],
            "gigs_has_more": bool(gigs_has_more),
            "pitch": json_safe(pitch),
            "kit_actions": json_safe(kit_cta) or [],
            "task_chips": json_safe(task_chips) or [],
        }
        if paywall_payload and paywall_payload.get("preview"):
            assistant["locked_pitch"] = {
                "brand_name": paywall_payload.get("brand_name"),
                "lines": paywall_payload["preview"],
            }
        if brands or gigs:
            _log_polly_event(creator_id, "matches_shown", {
                "brands": len(brands or []), "gigs": len(gigs or []), "intent": intent,
            })
        paywall_moment = funnel_paywall_moment(
            data.get("_paywall_moment"), paywall, task_chips, payload.get("starters"),
        )
        if paywall_moment:
            _log_polly_event(creator_id, "paywall_shown", paywall_moment)
        timer.mark("reply")
        try:
            persist_conn = None
            if conn and not getattr(conn, "closed", 1):
                persist_conn, conn = conn, None
            else:
                from pr_crm_routes import get_db_connection
                persist_conn = get_db_connection()
            save_thread(
                persist_conn,
                creator_id,
                list(messages or []) + [assistant],
                queue,
                notes=notes,
            )
            try:
                from services.polly import last_polly_brain, last_polly_cost
                from services.polly_usage import log_usage
                brain = last_polly_brain()
                cost = last_polly_cost()
                log_usage(
                    persist_conn,
                    creator_id,
                    "chat",
                    intent=intent,
                    llm=brain.get("provider"),
                    ok=not error,
                    error=error if error else None,
                    meta={
                        "model": cost.get("model") or brain.get("model"),
                        "usd": cost.get("usd") or 0,
                        "input_tokens": cost.get("input_tokens") or 0,
                        "output_tokens": cost.get("output_tokens") or 0,
                        "calls": cost.get("calls") or 0,
                        "source": "response_usage",
                    },
                )
            except Exception:
                pass
        except Exception as persist_err:
            print(f"[Polly] persist skipped: {persist_err}")
        finally:
            if persist_conn:
                persist_conn.close()
        timer.mark("persist")
        if error:
            payload["error"] = error
        tag = f"intent={intent} chip={chip_id or '-'} brain={decision.get('brain')}"
        if paywall:
            return timer.finish(jsonify(payload), extra=tag), 402
        return timer.finish(jsonify(payload), extra=tag)
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
        if conn:
            conn.close()


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
