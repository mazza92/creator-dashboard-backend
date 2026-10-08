"""Polly's tools. Gemini decides which to call; the server runs them and owns the facts.

Every handler takes (state, args) and returns a JSON-safe dict the model reads.
UI output (cards, pitch, chips, paywall) is written onto the TurnState.
Credits, paywall and send-logging rules are enforced here, not by the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from services.polly import (
    apply_gifted_ask_to_pitch,
    apply_handle_revision_to_pitch,
    apply_location_to_pitch,
    apply_paid_ask_to_pitch,
    brand_names_agree,
    cold_brand_chips,
    drop_pending_draft,
    drop_pitched,
    drop_shown,
    last_thread_pitch,
    mark_draft_pending,
    mark_pitched,
    mark_shown_brands,
    next_unlock_brand,
    out_of_free_unlocks,
    paywall_unlock_chips,
    pitch_coach_line,
    pitch_confirm_chips,
    pitch_from_followup_response,
    pitch_from_package_response,
    pitch_has_placeholder,
    pitched_id_set,
    resolve_pitch_location,
    unmark_pitched,
)
import services.polly_tracker as tracker
from services.polly_discovery import merge_notes_patch, paid_first, stated_niches
from services.polly_persona import (
    paywall_preview_lines,
    persona_cold_brand_warning,
    persona_followup_too_early,
    persona_paywall_say,
    persona_unlocks_after_send,
)
from services.polly_prefs import filter_brands_by_prefs, gifted_only, merge_prefs
from services.polly_turn_state import TurnState, brand_card


def _routes():
    from routes import polly as routes_polly
    return routes_polly


def _int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out or None


def _niche(state: TurnState) -> str:
    return str(
        (stated_niches(state.notes) or [None])[0]
        or (state.scrape or {}).get("primary_niche")
        or ""
    ).strip().lower()


def find_brand(state: TurnState, brand_id: Any = None, brand_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A published directory row. A typed name always beats an id that names someone else."""
    lookup = _routes()._lookup_published_brand
    name = (brand_name or "").strip()
    bid = _int(brand_id)
    conn = state.db()
    by_id = lookup(conn, brand_id=bid) if bid else None
    if by_id and (not name or brand_names_agree(name, by_id.get("name"))):
        return by_id
    if name:
        by_name = lookup(conn, brand_name=name)
        if by_name and brand_names_agree(name, by_name.get("name")):
            return by_name
    return None


def _similar(state: TurnState, name: str) -> List[Dict[str, Any]]:
    try:
        return _routes()._lookup_similar_brands(
            name,
            scrape=state.scrape,
            notes=state.notes,
            creator=state.creator,
            creator_id=state.creator_id,
            exclude_ids=state.notes.get("pitched_brand_ids") or [],
        ) or []
    except Exception as err:
        print(f"[Polly] similar brands skipped: {err}")
        return []


# ----- tools ----------------------------------------------------------------

def search_directory(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    row = find_brand(state, brand_name=name)
    if row:
        state.show_brands([row])
        draft = state.draft()
        return {
            "ok": True,
            "found": True,
            "brand": brand_card(row),
            "already_pitched": state.is_pitched(row),
            "has_unsent_draft": bool(draft and brand_names_agree(draft.get("name"), row.get("name"))),
            "card_shown": True,
        }
    similar = _similar(state, name)
    if similar:
        state.show_brands(similar)
    return {
        "ok": True,
        "found": False,
        "searched_for": name,
        "similar_in_directory": [brand_card(r) for r in similar[:3]],
        "cards_shown": bool(similar),
    }


def suggest_brands(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    deal = str(args.get("deal") or "").strip().lower()
    if deal in ("gifted", "paid") and not (deal == "paid" and gifted_only(state.notes)):
        state.notes["deal_intent"] = deal
    R = _routes()
    try:
        status, rows, err = R._suggest_payload(
            state.scrape, state.creator, notes=state.notes, creator_id=state.creator_id,
        )
    except Exception as exc:
        print(f"[Polly] suggest failed: {exc}")
        status, rows, err = 200, [], str(exc)[:180]
    if status == 401:
        state.auth_failed = True
        return {"ok": False, "error": "not authenticated"}
    rows = drop_shown(drop_pending_draft(rows, state.notes), state.notes)
    if not rows:
        rows = drop_shown(drop_pending_draft(drop_pitched(state.suggested, state.notes), state.notes), state.notes)
    rows = filter_brands_by_prefs(R._hydrate_brand_cards(rows), state.notes)
    rows = sorted(rows, key=lambda b: 0 if (b or {}).get("source") in ("recruiting", "open_lists") else 1)
    if (state.chip or {}).get("id") == "first_matches":
        rows = rows[:3]
    rows = rows[:6]
    if not rows:
        if err:
            state.error = err
        return {
            "ok": True,
            "brands": [],
            "note": "No fresh matches right now. Every match has been shown or pitched.",
        }
    state.brands = rows
    state.notes = mark_shown_brands(state.notes, rows)
    return {
        "ok": True,
        "brands": [brand_card(r) for r in rows],
        "cards_shown": True,
        "live_rosters": sum(1 for r in rows if r.get("source") in ("recruiting", "open_lists")),
    }


def draft_pitch(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    followup = bool(args.get("followup"))
    name = str(args.get("brand_name") or "").strip()
    row = find_brand(state, brand_id=args.get("brand_id"), brand_name=name)
    if not row and not name and not args.get("brand_id"):
        return {"ok": False, "error": "Say which brand. Pass brand_name or brand_id."}
    if not row:
        similar = _similar(state, name) if name else []
        if similar:
            state.show_brands(similar)
        return {
            "ok": True,
            "status": "not_in_directory",
            "searched_for": name or args.get("brand_id"),
            "similar_in_directory": [brand_card(r) for r in similar[:3]],
        }
    brand_id = row.get("id")
    brand_name = row.get("name") or name
    prior = state.draft()
    prior_name = (prior or {}).get("name") if prior and not brand_names_agree(prior.get("name"), brand_name) else None

    if followup:
        try:
            unlocks_at = tracker.followup_unlocks_at(state.db(), state.creator_id, brand_id, brand_name)
        except Exception as err:
            print(f"[Polly] follow-up gate skipped: {err}")
            state.db().rollback()
            unlocks_at = None
        if unlocks_at:
            state.final_say = persona_followup_too_early(brand_name, unlocks_at)
            state.add_chips([
                {"id": "checkin_replied", "label": f"{brand_name} replied", "action": "task_act",
                 "brand_id": brand_id, "brand_name": brand_name},
                {"id": "line_up", "label": "Pitch another brand", "action": "suggest_brands",
                 "skip_discovery": True, "deal": "gifted"},
            ])
            return {"ok": True, "status": "followup_too_early", "brand": brand_name,
                    "follow_up_ready_on": unlocks_at.date().isoformat()}

    shipping = resolve_pitch_location(state.creator, state.scrape, state.notes)
    loc_display = ""
    if not shipping.get("needs_location"):
        loc_display = str(shipping.get("display") or "").strip()
        if pitch_has_placeholder(loc_display):
            loc_display = ""

    cold = {}
    if not followup and not args.get("confirm_cold") and not (state.chip or {}).get("confirm_cold"):
        try:
            from pr_crm_routes import cold_spend_warning
            cold = cold_spend_warning(state.creator_id, brand_id) or {}
        except Exception as err:
            print(f"[Polly] cold check skipped: {err}")
            cold = {}
    if cold.get("warn"):
        state.show_brands(cold.get("alternatives") or [])
        state.final_say = persona_cold_brand_warning(
            brand_name, cold.get("signal"), state.brands, cold.get("remaining"),
        )
        state.add_chips(cold_brand_chips({"id": brand_id, "name": brand_name}, state.brands))
        R._log_polly_event(state.creator_id, "cold_brand_warned",
                           {"brand_id": brand_id, "alternatives": len(state.brands)})
        return {"ok": True, "status": "cold_brand_warning", "brand": brand_name}

    if followup:
        status, pkg = R._invoke_generate_followup(brand_id, row.get("slug"))
    else:
        status, pkg = R._invoke_generate_pr_package(
            brand_id, row.get("slug"),
            city=shipping.get("city") or "", country=shipping.get("country") or "",
        )
    pkg = pkg or {}
    if status == 401:
        state.auth_failed = True
        return {"ok": False, "error": "not authenticated"}
    if status == 402 or pkg.get("paywall"):
        state.paywall = True
        state.notes["paywall_brand"] = {"id": brand_id, "name": brand_name}
        state.paywall_payload = {
            "remaining": pkg.get("remaining", 0),
            "reset_at": pkg.get("reset_at"),
            "message": pkg.get("message") or "You've used all 3 unlocks this month.",
            "brand_name": brand_name,
            "brand_id": brand_id,
            "preview": paywall_preview_lines(brand_name, state.first, _niche(state)),
        }
        state.paywall_moment = {"brand_id": brand_id, "moment": "pitch", "followup": followup}
        views_n, sent_n = R._pipeline_proof(state.tracker_ctx)
        state.final_say = persona_paywall_say(brand_name, kit_views=views_n, sent=sent_n)
        state.add_chips(paywall_unlock_chips({"id": brand_id, "name": brand_name}))
        state.balance = R._unlock_balance(state.creator_id)
        return {"ok": True, "status": "locked_out_of_free_pitches", "brand": brand_name}
    if not pkg.get("success"):
        state.error = pkg.get("error") or "Could not generate pitch"
        print(f"[Polly] pitch failed status={status} error={state.error}")
        return {"ok": False, "status": "failed", "brand": brand_name, "error": "pitch generator failed"}

    pitch = pitch_from_followup_response(pkg, row) if followup else pitch_from_package_response(pkg)
    if not pitch:
        return {"ok": False, "status": "failed", "brand": brand_name, "error": "empty pitch"}
    pitch_paid = (
        state.notes.get("deal_intent") == "paid"
        and not gifted_only(state.notes)
        and paid_first(state.notes)
    )
    if not followup:
        if pitch_paid:
            pitch = apply_paid_ask_to_pitch(pitch, kit=state.kit, scrape=state.scrape,
                                            location_display=loc_display or None)
        elif state.notes.get("deal_intent") == "gifted":
            pitch = apply_gifted_ask_to_pitch(pitch, location_display=loc_display or None)
        if loc_display:
            pitch = apply_location_to_pitch(pitch, shipping.get("city") or "",
                                            shipping.get("country") or "", loc_display)
    needs_loc = pitch_has_placeholder(pitch.get("body") or "")
    pitch["needs_location"] = needs_loc
    if loc_display and not needs_loc:
        pitch["location_display"] = loc_display
    pitch["brand_id"] = pitch.get("brand_id") or brand_id
    pitch["brand_name"] = pitch.get("brand_name") or brand_name
    if followup:
        pitch["is_followup"] = True
    else:
        R._record_polly_draft(state.creator_id, pitch["brand_id"], pitch["brand_name"],
                              credit_used=int(pkg.get("credits_used") or 0) > 0)
        state.notes = mark_draft_pending(state.notes, {"id": brand_id, "name": brand_name})
    state.pitch = pitch
    state.brands = []
    state.add_chips(pitch_confirm_chips({"id": brand_id, "name": brand_name}))
    state.balance = R._unlock_balance(state.creator_id)
    return {
        "ok": True,
        "status": "drafted",
        "brand": brand_name,
        "is_followup": followup,
        "inbox": pitch.get("email") or None,
        "opens_in_mail_app": bool(pitch.get("mailto")),
        "needs_city_and_country": needs_loc,
        "ships_from": pitch.get("location_display") or None,
        "paid_ask": bool(pitch_paid and not followup),
        "parked_previous_draft": prior_name,
        "coach_tip": "" if followup else (pitch_coach_line(pkg, brand_name) or ""),
        "pitch_card_shown": True,
    }


def log_pitch_sent(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    named = str(args.get("brand_name") or "").strip()
    named_id = _int(args.get("brand_id"))
    draft = state.draft()
    locked = state.locked()

    target = None
    if named or named_id:
        if draft and (
            (named and brand_names_agree(named, draft.get("name")))
            or (named_id and named_id == _int(draft.get("id")))
        ):
            target = draft
        else:
            row = find_brand(state, brand_id=named_id, brand_name=named)
            if row:
                target = {"id": row.get("id"), "name": row.get("name"), "needs_location": False}
            elif named:
                return {"ok": False, "status": "unknown_brand", "brand": named}
    else:
        target = draft

    if locked and (not target or not brand_names_agree(locked.get("name"), target.get("name"))):
        if not target or not (named or named_id):
            if draft:
                state.add_chips([{
                    "id": "i_sent_it", "label": "I sent it", "action": "chat",
                    "brand_id": draft.get("id"), "brand_name": draft.get("name"),
                }])
            state.add_chips(paywall_unlock_chips(state.paywall_brand() or locked))
            return {
                "ok": False,
                "status": "latest_pitch_is_locked",
                "locked_brand": locked.get("name"),
                "unsent_draft": (draft or {}).get("name"),
                "explain": "The locked pitch was never shown in full, so it can't have been sent. "
                           "Ask whether they meant the unsent draft, if there is one.",
            }
    if not target:
        return {"ok": False, "status": "no_unsent_draft",
                "explain": "There is no drafted pitch to log. Ask which brand they emailed."}

    name = target.get("name") or "that brand"
    if target.get("needs_location"):
        state.add_chips(pitch_confirm_chips(target))
        return {"ok": False, "status": "draft_still_has_city_placeholder", "brand": name,
                "explain": "The draft still says [CITY, COUNTRY]. Ask for city and country before it goes out."}

    try:
        already = (_int(target.get("id")) or 0) in pitched_id_set(state.notes)
    except Exception:
        already = False
    state.notes = mark_pitched(state.notes, target)
    if not already:
        try:
            tracker.record_pitch_sent(state.db(), state.creator_id, target, drafted=False)
        except Exception as err:
            print(f"[Polly] pitch tracker skipped: {err}")
        state.sent_now = True
    ready_at = datetime.now(timezone.utc) + tracker.FOLLOWUP_GAP
    for key in (target.get("id"), target.get("name")):
        if key:
            state.early_fu.setdefault(str(key).strip().lower(), ready_at)

    state.balance = R._unlock_balance(state.creator_id)
    unlock_line = persona_unlocks_after_send(state.balance)
    if unlock_line:
        state.say_tail.append(unlock_line)
    out_of_credits = out_of_free_unlocks(state.balance)
    leftover = drop_pitched(state.suggested, state.notes)
    next_cards: List[str] = []
    if out_of_credits:
        next_pro = next_unlock_brand(target, leftover, state.notes.get("pending_pitch"))
        wall = state.paywall_brand()
        if wall and wall.get("name") and not brand_names_agree(wall.get("name"), name):
            next_pro = wall
        state.add_chips(paywall_unlock_chips(next_pro))
        state.after_send_empty = True
        state.paywall_moment = {"moment": "after_send", "brand_id": (next_pro or {}).get("id")}
    elif leftover and not state.brands:
        state.show_brands(leftover[:3])
        next_cards = [b.get("name") for b in state.brands if b.get("name")]
    return {
        "ok": True,
        "status": "already_logged" if already else "logged",
        "brand": name,
        "follow_up_check": "day 4",
        "free_pitches_left": None if state.balance.get("is_unlimited") else state.balance.get("remaining"),
        "next_brand_cards_shown": next_cards,
        "server_adds_credit_line": bool(unlock_line),
    }


_EVENTS = {
    "replied_interested": "brand_replied_interested",
    "replied_rejected": "brand_replied_rejected",
    "replied_question": "brand_replied_question",
    "pr_shipped": "pr_shipped",
    "pr_received": "pr_received",
    "content_posted": "content_posted",
    "payment_received": "payment_received",
    "email_bounced": "email_bounced",
    "pitch_not_sent": "pitch_not_sent",
    "still_quiet": "still_quiet",
    "kit_published": "portfolio_published",
}


def log_brand_update(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    event = str(args.get("event") or "").strip().lower()
    intent = _EVENTS.get(event)
    if not intent:
        return {"ok": False, "error": f"event must be one of {sorted(_EVENTS)}"}
    name = str(args.get("brand_name") or "").strip()
    brand: Optional[Dict[str, Any]] = None
    if intent != "portfolio_published":
        row = find_brand(state, brand_id=args.get("brand_id"), brand_name=name)
        if row:
            brand = {"id": row.get("id"), "name": row.get("name")}
        elif name:
            brand = {"id": None, "name": name}
        else:
            draft = state.draft()
            brand = {"id": draft.get("id"), "name": draft.get("name")} if draft else None
        if not brand:
            return {"ok": False, "error": "Which brand? Pass brand_name."}
    life = {"intent": intent, "confidence": 1.0,
            "brand": (brand or {}).get("name"), "brand_id": (brand or {}).get("id")}
    try:
        result = tracker.apply_lifecycle_intent(state.db(), state.creator_id, life, brand)
        tracker.log_intent(state.db(), state.creator_id, state.user_text, life, result.get("action") or "")
    except Exception as err:
        print(f"[Polly] lifecycle apply skipped: {err}")
        return {"ok": False, "error": "could not update the tracker"}
    if intent == "pitch_not_sent" and brand:
        state.notes = unmark_pitched(state.notes, brand)
    state.add_chips(result.get("chips") or [])
    try:
        state.tracker_ctx = tracker.load_creator_context(state.db(), state.creator_id)
    except Exception:
        pass
    return {
        "ok": True,
        "status": result.get("action"),
        "brand": (brand or {}).get("name"),
        "what_changed": result.get("say_hint") or "",
    }


def find_paid_gigs(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    from services.polly_gigs import mark_shown_gigs, page_polly_gigs
    if not args.get("more"):
        state.notes["shown_gig_ids"] = []
    try:
        gigs, has_more = page_polly_gigs(state.creator_id, scrape=state.scrape, notes=state.notes,
                                         history=state.messages)
    except Exception as err:
        print(f"[Polly] gigs failed: {err}")
        gigs, has_more = [], False
    state.gigs = gigs or []
    state.gigs_has_more = bool(has_more)
    state.brands = []
    state.notes["wanted_gigs"] = True
    if state.gigs:
        state.notes["saw_gigs"] = True
        state.notes = mark_shown_gigs(state.notes, state.gigs)
    return {
        "ok": True,
        "gigs": [
            {"brand": g.get("brand_name"), "product": g.get("product_name"),
             "source": g.get("source_label"), "pay": g.get("pay_label"), "where": g.get("location")}
            for g in state.gigs[:6]
        ],
        "more_available": state.gigs_has_more,
        "rates_on_kit": bool(((state.kit or {}).get("rates") or {})),
    }


_TOPICS = {
    "kit": "coach_portfolio",
    "profile": "coach_profile",
    "replies": "coach_profile",
    "rates": "coach_rates",
    "week": "coach_week",
    "how_it_works": "explain_newcollab",
}


def get_coaching_facts(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    topic = str(args.get("topic") or "").strip().lower()
    intent = _TOPICS.get(topic)
    if not intent:
        return {"ok": False, "error": f"topic must be one of {sorted(_TOPICS)}"}
    from services.polly_kit import kit_actions, kit_context
    from services.polly_tracker import session_context_text
    playbook = R._coach_say(intent, state.profile_context, state.notes, state.scrape,
                            kit=state.kit, coach_moves=state.coach_moves) or ""
    if topic in ("kit", "profile", "replies"):
        state.kit_actions = kit_actions(state.kit)
    return {
        "ok": True,
        "topic": topic,
        "playbook": playbook[:2500],
        "kit": kit_context(state.kit)[:1500],
        "tracker": session_context_text(state.tracker_ctx)[:1200],
    }


def save_profile_facts(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    saved: List[str] = []
    city = str(args.get("city") or "").strip()
    country = str(args.get("country") or "").strip()
    draft_updated = False
    if city and country:
        display = f"{city}, {country}"
        state.notes["location"] = display
        R._persist_shipping_location(state.creator_id, city, country)
        saved.append("location")
        card = last_thread_pitch(state.messages)
        if card and pitch_has_placeholder(card.get("body") or ""):
            updated = apply_location_to_pitch(card, city, country)
            state.pitch_update = updated
            state.pitch = updated
            draft_updated = True
            state.add_chips(pitch_confirm_chips({
                "id": updated.get("brand_id"), "name": updated.get("brand_name"),
            }))
    prefs: Dict[str, Any] = {}
    if args.get("gifted_only") is not None:
        prefs["gifted_only"] = bool(args.get("gifted_only"))
    for key in ("avoid_categories", "avoid_retailers"):
        vals = [str(v).strip().lower() for v in (args.get(key) or []) if str(v).strip()]
        if vals:
            prefs[key] = vals
    if prefs:
        state.notes = merge_prefs(state.notes, prefs)
        saved.append("preferences")
    patch = {k: args.get(k) for k in ("goal_30d", "stage", "niche", "biggest_challenge", "dream_brands")
             if args.get(k)}
    if patch:
        state.notes = merge_notes_patch(state.notes, patch)
        saved.extend(sorted(patch))
    return {"ok": True, "saved": saved, "draft_city_filled": draft_updated}


def fix_draft_handle(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    keep = str(args.get("correct_handle") or "").strip().lstrip("@")
    drop = str(args.get("wrong_handle") or "").strip().lstrip("@")
    card = last_thread_pitch(state.messages)
    if not card:
        return {"ok": False, "status": "no_draft_on_screen"}
    if not keep or keep.lower() == drop.lower():
        return {"ok": False, "error": "correct_handle must differ from wrong_handle"}
    updated = apply_handle_revision_to_pitch(card, keep, drop)
    state.pitch_update = updated
    state.pitch = updated
    state.add_chips(pitch_confirm_chips({"id": updated.get("brand_id"), "name": updated.get("brand_name")}))
    return {"ok": True, "status": "draft_updated", "brand": updated.get("brand_name")}


def offer_pro(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("brand_name") or "").strip()
    target = state.paywall_brand()
    if name:
        row = find_brand(state, brand_name=name)
        if row:
            target = {"id": row.get("id"), "name": row.get("name")}
    state.add_chips(paywall_unlock_chips(target))
    state.paywall_moment = {"moment": "offer", "brand_id": (target or {}).get("id")}
    return {
        "ok": True,
        "price": "$19/mo",
        "pro_does": "Polly on autopilot: picks 20-30 brands a month, writes and sends the pitches "
                    "from their Gmail, day-4 follow-ups, unlimited roster applications.",
        "free_path": "Follow-ups are free. Publishing the kit is free. Drafts left unsent for 7 days "
                     "give the credit back.",
        "button_shown_for": (target or {}).get("name"),
    }


# ----- registry -------------------------------------------------------------

def _obj(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_directory",
        "description": "Look up one company by name in the brand directory. Use whenever the creator "
                       "names a brand or asks whether a brand is available. Shows its card if found, "
                       "or similar directory brands if not. Never say a brand is or isn't in the "
                       "directory without calling this.",
        "parameters": _obj({"name": {**_STR, "description": "Company name only, e.g. Gucci"}}, ["name"]),
        "handler": search_directory,
    },
    {
        "name": "suggest_brands",
        "description": "Show fresh brand matches as cards (gifted PR / directory brands for this creator's "
                       "niche). Use for 'more brands', 'who should I pitch', 'next', or after a send when "
                       "they want the next target.",
        "parameters": _obj({"deal": {**_STR, "enum": ["gifted", "paid"],
                                     "description": "Only if they asked for gifted or paid specifically"}}),
        "handler": suggest_brands,
    },
    {
        "name": "draft_pitch",
        "description": "Write the pitch email for one directory brand and show it as a card. Only when "
                       "they want to pitch/contact that brand (or tapped its button). Server checks credits; "
                       "if they're out it returns locked and shows the paywall. Use followup=true for a "
                       "follow-up to a brand they already pitched.",
        "parameters": _obj({
            "brand_name": {**_STR, "description": "The company they mean, exactly as named"},
            "brand_id": {**_INT, "description": "Directory id, only if it is that same company"},
            "followup": _BOOL,
            "confirm_cold": {**_BOOL, "description": "They chose to pitch anyway after a low-reply warning"},
        }),
        "handler": draft_pitch,
    },
    {
        "name": "log_pitch_sent",
        "description": "Record that the creator sent a pitch from their inbox ('I sent it', 'done', "
                       "'emailed them'). Omit the brand to log the unsent draft. Refuses if the latest pitch "
                       "is locked or the draft still needs a city. Only this tool may log a send.",
        "parameters": _obj({"brand_name": _STR, "brand_id": _INT}),
        "handler": log_pitch_sent,
    },
    {
        "name": "log_brand_update",
        "description": "Record any news the creator reports about a brand: a reply (interested, rejected, "
                       "question), PR shipped or received, content posted, payment, bounce, the pitch "
                       "wasn't actually sent, or still no reply. Call it even if the brand isn't in "
                       "tracker_tasks; the server finds it. Also kit_published.",
        "parameters": _obj({
            "event": {**_STR, "enum": sorted(_EVENTS)},
            "brand_name": _STR,
            "brand_id": _INT,
        }, ["event"]),
        "handler": log_brand_update,
    },
    {
        "name": "find_paid_gigs",
        "description": "Show live paid UGC briefs they can apply to (pulled from AspireIQ, LinkedIn and "
                       "other boards). Use for paid UGC / gigs / who pays. Not for gifted PR.",
        "parameters": _obj({"more": {**_BOOL, "description": "Next page of gigs already shown"}}),
        "handler": find_paid_gigs,
    },
    {
        "name": "get_coaching_facts",
        "description": "Get this creator's real kit, tracker and playbook facts before coaching on "
                       "their kit, profile, getting more replies, rates, what to do this week, or how "
                       "the app works.",
        "parameters": _obj({"topic": {**_STR, "enum": sorted(_TOPICS)}}, ["topic"]),
        "handler": get_coaching_facts,
    },
    {
        "name": "save_profile_facts",
        "description": "Save facts the creator just told you: shipping city + country (also fills the "
                       "draft's [CITY, COUNTRY]), gifted-only or open to paid, categories or retailers to "
                       "avoid, goal, stage, niche, biggest challenge, dream brands.",
        "parameters": _obj({
            "city": _STR, "country": _STR,
            "gifted_only": _BOOL,
            "avoid_categories": {"type": "array", "items": _STR},
            "avoid_retailers": {"type": "array", "items": _STR},
            "goal_30d": _STR, "stage": _STR, "niche": _STR,
            "biggest_challenge": _STR, "dream_brands": _STR,
        }),
        "handler": save_profile_facts,
    },
    {
        "name": "fix_draft_handle",
        "description": "Correct the creator's social handle inside the pitch draft on screen.",
        "parameters": _obj({"correct_handle": _STR, "wrong_handle": _STR}, ["correct_handle"]),
        "handler": fix_draft_handle,
    },
    {
        "name": "offer_pro",
        "description": "Show the Pro upgrade button (autopilot, $19/mo) when they ask about Pro, more "
                       "pitches, or credits resetting. Returns the free path too, for when they can't afford it.",
        "parameters": _obj({"brand_name": {**_STR, "description": "Brand they want to pitch next, if any"}}),
        "handler": offer_pro,
    },
]

_BY_NAME: Dict[str, Callable[[TurnState, Dict[str, Any]], Dict[str, Any]]] = {
    t["name"]: t["handler"] for t in TOOLS
}

INTENT_FOR_TOOL = {
    "suggest_brands": "suggest_brands",
    "search_directory": "chat",
    "draft_pitch": "generate_pitch",
    "find_paid_gigs": "suggest_gigs",
}


def function_declarations() -> List[Dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOLS
    ]


def run_tool(state: TurnState, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    handler = _BY_NAME.get(name)
    args = dict(args or {})
    if not handler:
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        result = handler(state, args) or {}
    except Exception as err:
        print(f"[Polly] tool={name} crashed: {err}")
        try:
            state.db().rollback()
        except Exception:
            pass
        result = {"ok": False, "error": "tool failed"}
    names = []
    for key in ("brand", "locked_brand", "unsent_draft"):
        val = result.get(key)
        if isinstance(val, dict):
            val = val.get("name")
        if val:
            names.append(str(val))
    for key in ("brands", "similar_in_directory"):
        for row in result.get(key) or []:
            if isinstance(row, dict) and row.get("name"):
                names.append(row["name"])
    state.tool_log.append({
        "name": name, "args": args, "status": result.get("status") or ("ok" if result.get("ok") else "error"),
        "brand_names": names, "result": result,
    })
    if name in INTENT_FOR_TOOL and result.get("ok"):
        state.intent = INTENT_FOR_TOOL[name]
    print(f"[Polly] tool={name} args={args} status={state.tool_log[-1]['status']}")
    return result
