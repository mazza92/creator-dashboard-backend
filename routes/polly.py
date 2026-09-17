"""Polly chat API — Gemini brain over For You matching + PR package pitch."""

from flask import Blueprint, jsonify, request, session, current_app

from services.polly import (
    build_profile_context,
    chat_reply,
    classify_intent,
    drop_pending_draft,
    drop_pitched,
    flatten_for_you,
    gemini_available,
    is_done_turn,
    is_more_brands_turn,
    json_safe,
    last_pitch_brand,
    llm_available,
    mark_draft_pending,
    mark_pitched,
    unmark_pitched,
    narrate_kit_review,
    narrate_tool_result,
    pitch_confirm_chips,
    pitch_from_followup_response,
    pitch_from_package_response,
    public_profile_summary,
    resolve_brand,
    sanitize_brand_card,
    say_claims_unconfirmed_send,
    unpack_view_result,
    wants_followup_pitch,
)
from services.polly_kit import kit_actions, kit_context, kit_reply_grounded, load_kit_snapshot
from services.polly_pain import diagnose_pain, stamp_pain
from services.polly_memory import load_thread, save_thread
from services.polly_discovery import (
    advance as advance_discovery,
    apply_answer,
    assign_track,
    required_categories_for_match,
    detour_from_action,
    discovery_brief,
    discovery_complete,
    discovery_started,
    explain_newcollab,
    field_from_history,
    merge_notes_patch,
    notes_context,
    opener as discovery_opener,
    skip_discovery,
    starters_for,
    stated_niches,
)
from services.polly_persona import (
    creator_first_name,
    is_robotic,
    persona_brand_intro,
    persona_followup_intro,
    persona_greeting,
    persona_more_brands_intro,
    persona_pitch_intro,
    persona_portfolio_review,
    persona_rate_card,
    persona_week_plan,
    scrub_polly_voice,
    strip_embedded_pitch,
)
from services.polly_tracker import (
    apply_lifecycle_intent,
    apply_task_chip,
    backfill_from_notes,
    brand_from_notes,
    brand_timeline,
    classify_lifecycle_heuristic,
    list_relationships,
    load_creator_context,
    log_intent,
    lookup_brand,
    maybe_bootstrap_nudge,
    morning_brief,
    process_due_nudges,
    record_pitch_sent,
    session_context_text,
)

polly_bp = Blueprint("polly", __name__, url_prefix="/api/polly")


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


def _unlock_balance(creator_id):
    from pr_crm_routes import get_creator_unlock_balance
    try:
        return get_creator_unlock_balance(creator_id) or {}
    except Exception:
        return {}


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
        return [dict(row) for row in cursor.fetchall()]
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
    exclude = list(notes.get("pitched_brand_ids") or []) + _pipeline_pitched_ids(creator_id or (creator or {}).get("id"))
    pooled = _fetch_brands_by_category(cats, exclude_ids=exclude) if cats else []
    status, for_you = _invoke_for_you()
    if status == 401:
        return status, [], "Not authenticated"
    payload = dict(for_you or {}) if for_you.get("success") else {}
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
    return 200, drop_pitched(brands, notes, extra_ids=exclude), None


def _coach_say(intent, profile_context, notes, scrape, kit=None):
    if intent == "explain_newcollab":
        return explain_newcollab()
    if intent == "coach_portfolio":
        return persona_portfolio_review(profile_context, kit)
    if intent == "coach_rates":
        followers = None
        try:
            followers = int((scrape or {}).get("follower_count") or 0) or None
        except (TypeError, ValueError):
            followers = None
        return persona_rate_card(followers)
    if intent == "coach_week":
        return persona_week_plan(profile_context, notes)
    return None


def _invoke_for_you():
    from pr_crm_routes import get_for_you
    return unpack_view_result(get_for_you())


def _invoke_generate_pr_package(brand_id, slug=None):
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
    from psycopg2.extras import RealDictCursor
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
    if name:
        cursor.execute(
            """
            SELECT id, slug, brand_name AS name, logo_url AS logo, description, category, website,
                   application_form_url
            FROM pr_brands
            WHERE slug IS NOT NULL
              AND COALESCE(status, 'published') = 'published'
              AND (LOWER(brand_name) = LOWER(%s) OR LOWER(slug) = LOWER(%s))
            LIMIT 1
            """,
            (name, name),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


@polly_bp.route("/bootstrap", methods=["GET"])
def bootstrap():
    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        scrape = _load_scrape(conn, (creator or {}).get("user_id") or session.get("user_id"))
        balance = _unlock_balance(creator_id)
        kit = load_kit_snapshot(conn, creator_id, scrape)
        profile_context = build_profile_context(scrape, creator) + "\n\n" + kit_context(kit)
        first = creator_first_name(creator, scrape)
        thread = load_thread(conn, creator_id)
        notes = assign_track(thread.get("notes") or {}, scrape)
        try:
            backfill_from_notes(conn, creator_id, notes)
        except Exception as err:
            print(f"[Polly] tracker backfill skipped: {err}")
        prior = thread.get("notes") or {}
        tracker = {}
        try:
            tracker = load_creator_context(conn, creator_id)
        except Exception as err:
            print(f"[Polly] tracker context skipped: {err}")
            tracker = {}
        pain = diagnose_pain(notes, tracker, kit, scrape)
        notes = stamp_pain(notes, pain)
        if pain:
            tracker["active_pain"] = pain
        if notes.get("active_pain") != prior.get("active_pain") or notes.get("polly_track") != prior.get("polly_track") or notes.get("polly_track_provisional") != prior.get("polly_track_provisional"):
            save_thread(
                conn,
                creator_id,
                messages=thread.get("messages") or [],
                suggested=thread.get("suggested_brands") or [],
                notes=notes,
            )
        queue = drop_pitched(thread.get("suggested_brands") or [], notes)
        greeting = (
            discovery_opener(first)
            if not discovery_complete(notes) and not (thread.get("messages") or [])
            else persona_greeting(profile_context, first_name=first)
        )
        top_name = None
        for row in queue:
            if isinstance(row, dict) and row.get("name"):
                top_name = row.get("name")
                break
        brief = morning_brief(tracker, first)
        nudge = None
        try:
            nudge = maybe_bootstrap_nudge(conn, creator_id)
        except Exception as err:
            print(f"[Polly] nudge peek skipped: {err}")
        try:
            from services.polly_usage import log_usage
            log_usage(conn, creator_id, "open")
        except Exception:
            pass
        return jsonify({
            "success": True,
            "profile": public_profile_summary(scrape, creator),
            "credits": {
                "remaining": balance.get("remaining"),
                "limit": balance.get("limit"),
                "is_unlimited": bool(balance.get("is_unlimited")),
                "used": balance.get("used"),
            },
            "greeting": greeting,
            "opener": greeting if not (thread.get("messages") or []) else None,
            "starters": starters_for(notes, top_brand=top_name),
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
            "nudge": nudge,
        })
    finally:
        if conn:
            conn.close()


@polly_bp.route("/chat", methods=["POST"])
def chat():
    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

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
        balance = _unlock_balance(creator_id)
        first = creator_first_name(creator, scrape)
        stored = load_thread(conn, creator_id)
        notes = dict(stored.get("notes") or {})
        skip_flag = bool(data.get("skip_discovery"))
        asked = field_from_history(messages)
        if asked and discovery_started(notes) and not discovery_complete(notes):
            notes = apply_answer(notes, asked, user_text)
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
            "suggest_brands", "generate_pitch", "chat", "discovery",
            "explain_newcollab", "coach_week", "coach_portfolio", "coach_rates",
            "task_act",
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
        decision = classify_intent(
            user_text,
            profile_context + "\n\n" + notes_context(notes) + "\n\n" + tracker_hint,
            history=messages,
            suggested_brands=suggested,
            brand_id=explicit_brand_id,
            discovery_hint=discovery_plus,
            force_intent=explicit_action if explicit_action in allowed_actions and explicit_action != "task_act" else None,
            pitched_names=notes.get("pitched_brand_names") or [],
        )
        notes = merge_notes_patch(notes, decision.get("notes_patch"))

        intent = decision.get("intent") or "chat"
        if is_more_brands_turn(user_text) and explicit_action not in ("generate_pitch",):
            intent = "suggest_brands"
        if skip_flag:
            notes = skip_discovery(notes)
            if intent in ("discovery", "chat"):
                intent = "suggest_brands"
        notes = assign_track(notes, scrape)

        brands = []
        pitch = None
        paywall = False
        paywall_payload = None
        error = None
        say = ""
        wrap_say = ""
        live_brain = bool(decision.get("brain")) and not is_robotic(decision.get("say"))

        last_pitch = last_pitch_brand(messages, notes) or brand_from_notes(notes)
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
        checkin_handled = str(data.get("chip_id") or data.get("starter") or "").startswith("checkin_")
        if (
            not more_brands_turn
            and not checkin_handled
            and life.get("confidence", 0) >= 0.7
            and life.get("intent") not in skip_life
            and not (life.get("intent") == "pitch_sent" and not last_pitch)
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
            notes = mark_pitched(notes, last_pitch)
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
            if intent == "suggest_brands" and not explicit_action:
                intent = "chat"

        remaining = drop_pitched(suggested, notes)
        progress_remaining = False
        if is_done_turn(user_text) and intent == "chat" and remaining:
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

        wants_matches = intent == "suggest_brands" or notes.get("wants_matches")
        open_discovery = not discovery_complete(notes)
        can_match = (
            discovery_complete(notes)
            or skip_flag
            or bool(stated_niches(notes))
            or bool((scrape or {}).get("primary_niche"))
        )

        if live_brain:
            say = decision.get("say") or ""
            if wants_matches:
                notes["wants_matches"] = True
            if open_discovery and not discovery_started(notes):
                notes["discovery_step"] = 1
        elif intent == "explain_newcollab":
            say = explain_newcollab(first)
        elif intent == "coach_portfolio":
            say = _coach_say(intent, profile_context, notes, scrape, kit=kit)
        elif intent in ("coach_week", "coach_rates") and not open_discovery:
            say = _coach_say(intent, profile_context, notes, scrape, kit=kit)
        elif open_discovery and intent != "explain_newcollab":
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

        if intent == "suggest_brands" and can_match and not progress_remaining:
            if conn:
                conn.close()
                conn = None
            status, brands, err = _suggest_payload(scrape, creator, notes=notes, creator_id=creator_id)
            if status == 401:
                return jsonify({"success": False, "error": "Not authenticated"}), 401
            if err:
                error = err
            brands = drop_pending_draft(brands, notes)
            if not brands:
                brands = drop_pending_draft(drop_pitched(suggested, notes), notes)
            brands = _hydrate_brand_cards(brands)
            pending_name = ""
            pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
            if pending:
                pending_name = pending.get("name") or pending.get("brand_name") or ""
            if more_brands_turn or pending_name:
                fallback_say = persona_more_brands_intro(brands, pending_name, profile_context)
                say = fallback_say
            else:
                fallback_say = persona_brand_intro(brands, profile_context)
                llm_say = decision.get("say") if not is_robotic(decision.get("say")) else ""
                say = narrate_tool_result(
                    profile_context,
                    user_text,
                    history=messages,
                    brands=brands,
                    fallback=llm_say or fallback_say,
                    discovery_hint=discovery_brief(notes),
                )
                if is_robotic(say) or say_claims_unconfirmed_send(say):
                    say = fallback_say
            if wrap_say:
                say = wrap_say + "\n\n" + say
        elif intent == "generate_pitch" and can_match:
            resolved = resolve_brand(
                suggested,
                brand_id=decision.get("brand_id") or explicit_brand_id,
                brand_name=decision.get("brand_name") or data.get("brand_name"),
            )
            if not resolved:
                pooled = _lookup_published_brand(
                    conn,
                    brand_id=decision.get("brand_id") or explicit_brand_id,
                    brand_name=decision.get("brand_name") or data.get("brand_name"),
                )
                resolved = pooled
            if conn:
                conn.close()
                conn = None
            if not resolved:
                say = (
                    decision.get("say")
                    if not is_robotic(decision.get("say"))
                    else "Which one feels right from that list? Tell me the name and I'll draft it."
                )
            else:
                wants_followup = wants_followup_pitch(data, user_text)
                if wants_followup:
                    status, pkg = _invoke_generate_followup(resolved.get("id"), resolved.get("slug"))
                else:
                    status, pkg = _invoke_generate_pr_package(resolved.get("id"), resolved.get("slug"))
                if status == 402 or pkg.get("paywall"):
                    paywall = True
                    paywall_payload = {
                        "remaining": pkg.get("remaining", 0),
                        "reset_at": pkg.get("reset_at"),
                        "message": pkg.get("message") or "You've used all 3 unlocks this month.",
                    }
                    say = "Right — you're out of free unlocks this month. Upgrade if you want to keep going, or wait for the reset."
                    balance = _unlock_balance(creator_id)
                elif status == 401:
                    return jsonify({"success": False, "error": "Not authenticated"}), 401
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
                    if pitch:
                        pitch["brand_id"] = pitch.get("brand_id") or resolved.get("id")
                        pitch["brand_name"] = pitch.get("brand_name") or resolved.get("name")
                        if wants_followup:
                            pitch["is_followup"] = True
                    brand_name = (pitch or {}).get("brand_name") or resolved.get("name") or "them"
                    notes = mark_draft_pending(notes, {
                        "id": resolved.get("id") or (pitch or {}).get("brand_id"),
                        "name": brand_name,
                    })
                    fallback_say = (
                        persona_followup_intro(brand_name, has_mailto=bool(pitch and pitch.get("mailto")))
                        if wants_followup
                        else persona_pitch_intro(brand_name, has_mailto=bool(pitch and pitch.get("mailto")))
                    )
                    say = fallback_say
                    data["_task_chips"] = pitch_confirm_chips({
                        "id": resolved.get("id"),
                        "name": brand_name,
                    })
                    balance = _unlock_balance(creator_id)
        elif not say:
            if conn:
                conn.close()
                conn = None
            if intent in ("coach_week", "coach_portfolio", "coach_rates", "explain_newcollab"):
                say = _coach_say(intent, profile_context, notes, scrape, kit=kit)
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

        kit_cta = []
        if intent == "coach_portfolio":
            fallback_say = persona_portfolio_review(profile_context, kit)
            if not kit_reply_grounded(say, kit):
                say = narrate_kit_review(
                    profile_context,
                    user_text,
                    history=messages,
                    kit=kit,
                    fallback=fallback_say,
                    discovery_hint=discovery_brief(notes),
                )
            if not kit_reply_grounded(say, kit):
                say = fallback_say
            kit_cta = kit_actions(kit)

        say = scrub_polly_voice(say)
        pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
        if (
            pending
            and not is_done_turn(user_text)
            and say_claims_unconfirmed_send(say)
        ):
            say = persona_more_brands_intro(
                brands,
                pending.get("name") or pending.get("brand_name") or "",
                profile_context,
            )
        if pitch:
            cleaned = strip_embedded_pitch(say)
            say = cleaned or persona_pitch_intro(
                (pitch.get("brand_name") or "them"),
                has_mailto=bool(pitch.get("mailto")),
            )
        task_chips = list(data.get("_task_chips") or [])
        if life_result.get("say_hint") and not more_brands_turn:
            hint = scrub_polly_voice(life_result["say_hint"])
            if hint and hint.lower() not in (say or "").lower():
                say = (say + "\n\n" + hint).strip() if say else hint
            task_chips = task_chips or list(life_result.get("chips") or [])
        if data.get("_task_say") and not say:
            say = scrub_polly_voice(data["_task_say"])

        if conn:
            conn.close()
            conn = None

        brands = _hydrate_brand_cards(brands)
        queue = _hydrate_brand_cards(drop_pitched(brands or suggested, notes))
        payload = {
            "success": not error,
            "message": say,
            "intent": intent,
            "brands": json_safe(brands),
            "suggested_brands": json_safe(queue),
            "pitch": json_safe(pitch),
            "kit_actions": json_safe(kit_cta),
            "task_chips": json_safe(task_chips),
            "paywall": paywall,
            "starters": starters_for(
                notes,
                top_brand=(queue[0]["name"] if queue else None),
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
        if paywall_payload:
            payload["paywall_payload"] = paywall_payload
        assistant = {
            "role": "assistant",
            "content": say,
            "brands": json_safe(brands) or [],
            "pitch": json_safe(pitch),
            "kit_actions": json_safe(kit_cta) or [],
            "task_chips": json_safe(task_chips) or [],
        }
        try:
            persist_conn = None
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
                from services.polly import last_polly_brain
                from services.polly_usage import log_usage
                brain = last_polly_brain()
                log_usage(
                    persist_conn,
                    creator_id,
                    "chat",
                    intent=intent,
                    llm=brain.get("provider"),
                    ok=not error,
                    error=error if error else None,
                )
            except Exception:
                pass
        except Exception as persist_err:
            print(f"[Polly] persist skipped: {persist_err}")
        finally:
            if persist_conn:
                persist_conn.close()
        if error:
            payload["error"] = error
        if paywall:
            return jsonify(payload), 402
        return jsonify(payload)
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


@polly_bp.route("/cron/nudges", methods=["GET", "POST"])
def cron_nudges():
    import os
    secret = os.getenv("CRON_SECRET")
    provided = request.headers.get("X-Cron-Secret") or request.args.get("secret")
    if secret and provided != secret:
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
