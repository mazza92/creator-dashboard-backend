"""Polly's tools. Gemini decides which to call; the server runs them and owns the facts.

Every handler takes (state, args) and returns a JSON-safe dict the model reads.
UI output (cards, pitch, chips, paywall) is written onto the TurnState.
Credits, paywall and send-logging rules are enforced here, not by the model.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from services.polly import (
    apply_gifted_ask_to_pitch,
    apply_handle_revision_to_pitch,
    apply_location_to_pitch,
    apply_paid_ask_to_pitch,
    apply_work_samples_to_pitch,
    brand_names_agree,
    build_mailto,
    clean_sample_posts,
    cold_brand_chips,
    drop_pending_draft,
    drop_pitched,
    drop_shown,
    last_thread_pitch,
    mark_draft_pending,
    mark_pitched,
    mark_shown_brands,
    shown_brand_ids,
    next_unlock_brand,
    out_of_free_unlocks,
    parse_location_reply,
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
from services.polly_pitch_edit import apply_pitch_prefs, pitch_pref_inputs, revise_pitch
from services.polly_prefs import (
    PITCH_PREF_KEYS,
    clean_pitch_prefs,
    filter_brands_by_prefs,
    get_prefs,
    gifted_only,
    merge_prefs,
    pitch_prefs_line,
)
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


def _shown_rank(brand: Dict[str, Any], notes: Dict[str, Any]) -> int:
    try:
        bid = int((brand or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 10 ** 9
    shown = shown_brand_ids(notes)
    return shown.index(bid) if bid in shown else 10 ** 9


def _oldest_shown_first(rows: List[Dict[str, Any]], notes: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(rows or [], key=lambda b: _shown_rank(b, notes))


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
    pool = drop_pending_draft(rows, state.notes)
    fresh = drop_shown(pool, state.notes)
    repeated = False
    widened = False
    if fresh:
        picked = fresh
    elif pool:
        picked = _oldest_shown_first(pool, state.notes)
        repeated = True
    else:
        queued = drop_pending_draft(drop_pitched(state.suggested, state.notes), state.notes)
        unseen = drop_shown(queued, state.notes)
        picked = unseen or _oldest_shown_first(queued, state.notes)
        repeated = bool(picked) and not unseen
    if not picked:
        try:
            picked = R._fallback_brand_pool(state.notes, state.scrape, state.creator, state.creator_id)
        except Exception as exc:
            print(f"[Polly] directory fallback skipped: {exc}")
            picked = []
        picked = drop_pending_draft(drop_pitched(picked, state.notes), state.notes)
        unseen = drop_shown(picked, state.notes)
        if unseen:
            picked = unseen
        else:
            repeated = bool(picked)
        widened = bool(picked)
    hydrated = R._hydrate_brand_cards(picked)
    rows = filter_brands_by_prefs(hydrated, state.notes) or hydrated
    rows = sorted(rows, key=lambda b: (
        0 if (b or {}).get("source") in ("recruiting", "open_lists") else 1,
        _shown_rank(b, state.notes),
    ))
    cap = 3 if (state.chip or {}).get("id") == "first_matches" else 6
    rows = rows[:cap]
    if not rows:
        if err:
            state.error = err
        return {
            "ok": True,
            "brands": [],
            "note": "The directory could not be loaded. Ask again in a moment.",
        }
    state.brands = rows
    state.notes = mark_shown_brands(state.notes, rows)
    out = {
        "ok": True,
        "brands": [brand_card(r) for r in rows],
        "cards_shown": True,
        "live_rosters": sum(1 for r in rows if r.get("source") in ("recruiting", "open_lists")),
    }
    if repeated:
        out["repeated"] = True
        out["note"] = "These brands were shown before and are still unpitched. The cards are on screen."
    if widened:
        out["widened"] = True
        out["note"] = "The niche pool was used up, so these are the next directory brands. The cards are on screen."
    return out


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def clean_email(raw: Any) -> str:
    text = str(raw or "").strip().strip("<>.,;").replace("mailto:", "")
    return text.lower() if _EMAIL_RE.match(text) else ""


def _with_inbox(pitch: Dict[str, Any], email: str) -> Dict[str, Any]:
    out = dict(pitch)
    out["email"] = email
    out["mailto"] = build_mailto(email, out.get("subject") or "", out.get("body") or "")
    out["manual_email"] = True
    return out


def draft_pitch(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    followup = bool(args.get("followup"))
    name = str(args.get("brand_name") or "").strip()
    to_email = clean_email(args.get("to_email"))
    if args.get("to_email") and not to_email:
        return {"ok": False, "status": "invalid_email", "given": str(args.get("to_email"))[:120],
                "explain": "That isn't a full email address. Ask them to paste it exactly."}
    row = find_brand(state, brand_id=args.get("brand_id"), brand_name=name)
    if not row and not name and not args.get("brand_id"):
        return {"ok": False, "error": "Say which brand. Pass brand_name or brand_id."}
    manual = not row and bool(name) and not followup
    if not row and not (manual and to_email):
        similar = _similar(state, name) if name else []
        if similar:
            state.show_brands(similar)
        return {
            "ok": True,
            "status": "not_in_directory",
            "searched_for": name or args.get("brand_id"),
            "similar_in_directory": [brand_card(r) for r in similar[:3]],
            "explain": "No draft was written. If they have this brand's email, ask for it and call "
                       "draft_pitch again with to_email. Don't say a draft is coming until then.",
        }
    brand_id = row.get("id") if row else None
    brand_name = (row or {}).get("name") or name
    prior = state.draft()
    prior_name = (prior or {}).get("name") if prior and not brand_names_agree(prior.get("name"), brand_name) else None

    if manual:
        return _draft_manual(state, brand_name, to_email, prior_name)

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

    shipping, loc_display = _pitch_location(state)

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
    elif args.get("_prefetched"):
        status, pkg = args["_prefetched"]
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
    directory_inbox = pitch.get("email") or None
    pitch, pitch_paid = _dress_pitch(state, pitch, shipping, loc_display, followup)
    if not followup and args.get("sample_posts"):
        pitch = apply_work_samples_to_pitch(pitch, clean_sample_posts(args.get("sample_posts")))
    if to_email:
        pitch = _with_inbox(pitch, to_email)
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
        "inbox_from_creator": bool(to_email),
        "directory_inbox": directory_inbox if to_email and directory_inbox != to_email else None,
        "opens_in_mail_app": bool(pitch.get("mailto")),
        "needs_city_and_country": pitch["needs_location"],
        "ships_from": pitch.get("location_display") or None,
        "paid_ask": bool(pitch_paid and not followup),
        "parked_previous_draft": prior_name,
        "coach_tip": "" if followup else (pitch_coach_line(pkg, brand_name) or ""),
        "pitch_card_shown": True,
        "pitch_unlocked": True,
        "explain": "The full pitch is unlocked and on the card. Never call it locked; "
                   "free_pitches_left only limits the next pitch.",
    }


BATCH_MAX = 3


def batch_item(pitch: Dict[str, Any]) -> Dict[str, Any]:
    body = str(pitch.get("body") or "")
    return {
        "brand_id": _int(pitch.get("brand_id")),
        "brand_name": pitch.get("brand_name"),
        "brand_logo": pitch.get("brand_logo"),
        "email": pitch.get("email") or None,
        "subject": pitch.get("subject") or "",
        "body": body,
        "mailto": pitch.get("mailto") or None,
        "needs_location": bool(pitch.get("needs_location")) or pitch_has_placeholder(body),
    }


def _stored_batch_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: item.get(k) for k in ("brand_id", "brand_name", "brand_logo", "email", "subject", "body")}


def batch_waits_on_location(notes: Dict[str, Any]) -> bool:
    if (notes or {}).get("pending_batch"):
        return True
    items = ((notes or {}).get("pitch_batch") or {}).get("items") or []
    return any(pitch_has_placeholder(str(i.get("body") or "")) and not i.get("sent_at") for i in items)


def _fill_batch_location(state: TurnState, city: str, country: str, display: str) -> bool:
    """City arrived after a batch was written with the placeholder: fix every pitch in it."""
    stored = state.notes.get("pitch_batch") or {}
    items = stored.get("items") or []
    if not any(pitch_has_placeholder(str(i.get("body") or "")) for i in items):
        return False
    filled = []
    for item in items:
        if item.get("sent_at") or not pitch_has_placeholder(str(item.get("body") or "")):
            filled.append(item)
            continue
        fixed = apply_location_to_pitch(item, city, country, display)
        filled.append({**item, "body": fixed.get("body") or item.get("body")})
    state.notes["pitch_batch"] = {**stored, "items": filled}
    shown = [batch_item({**i, "mailto": build_mailto(i.get("email"), i.get("subject") or "", i.get("body") or "")
                         if i.get("email") else None}) for i in filled if not i.get("sent_at")]
    state.pitch_batch = shown
    sendable = sum(1 for i in shown if i["email"] and not i["needs_location"])
    say = f"Got it, {display}. "
    if sendable:
        say += (f"{'All ' + str(sendable) + ' pitches now say' if sendable > 1 else 'Your pitch now says'} "
                f"where to ship. Tap **Send {'all ' + str(sendable) if sendable > 1 else 'it'} from my Gmail** below.")
    else:
        say += "Your pitches now say where to ship."
    state.final_say = say
    return True


def draft_pitch_batch(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    """First-session wow: write every pitch on screen at once, ready to send together."""
    R = _routes()
    rows: List[Dict[str, Any]] = []
    seen = set()
    for raw in args.get("brand_ids") or []:
        bid = _int(raw)
        if not bid or bid in seen:
            continue
        seen.add(bid)
        row = find_brand(state, brand_id=bid)
        if row and not state.is_pitched(row):
            rows.append(row)
    rows = rows[:BATCH_MAX]
    if not rows:
        return {"ok": False, "status": "no_brands",
                "explain": "Those brands are already pitched or gone. Offer new matches."}

    shipping, _loc = _pitch_location(state)
    if shipping.get("needs_location"):
        state.notes["pending_batch"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "brand_ids": [int(r["id"]) for r in rows],
        }
        state.notes["location_asked_at"] = datetime.now(timezone.utc).isoformat()
        state.final_say = (
            f"Quick one before I write {'them' if len(rows) > 1 else 'it'}: which city and country "
            "do you live in? Brands need it to ship your package, and every pitch says it.\n\n"
            "Type it like **Paris, France**."
        )
        return {"ok": True, "status": "needs_location_first", "count": len(rows)}

    prefetched = R._invoke_generate_pr_packages(
        [{"id": int(r["id"]), "slug": r.get("slug")} for r in rows],
        city=shipping.get("city") or "", country=shipping.get("country") or "",
    )
    pitches: List[Dict[str, Any]] = []
    locked: List[str] = []
    for row in rows:
        state.pitch = None
        draft_pitch(state, {
            "brand_id": row["id"], "brand_name": row.get("name"), "confirm_cold": True,
            "_prefetched": prefetched.get(int(row["id"])),
        })
        if state.pitch:
            pitches.append(state.pitch)
        elif state.paywall:
            locked.append(row.get("name") or "")
    state.pitch = None
    state.brands = []
    state.task_chips = []
    state.balance = R._unlock_balance(state.creator_id)
    if not pitches:
        return {"ok": False, "status": "locked_out_of_free_pitches" if locked else "failed"}

    state.paywall = False
    state.paywall_payload = None
    state.paywall_moment = None
    items = [batch_item(p) for p in pitches]
    state.pitch_batch = items
    state.notes["pitch_batch"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "items": [_stored_batch_item(i) for i in items],
    }
    n = len(items)
    names = ", ".join(f"**{i['brand_name']}**" for i in items)
    say = (f"Done. I wrote {n} pitch{'es' if n > 1 else ''}: {names}. "
           "Each one is written for that brand and goes to their PR inbox.")
    sendable = sum(1 for i in items if i["email"] and not i["needs_location"])
    if sendable:
        say += (f"\n\nRead them below, then tap **Send {'all ' + str(sendable) if sendable > 1 else 'it'} "
                "from my Gmail**. They go out from your own address, and I'll log them and check in on day 4.")
    if any(i["needs_location"] for i in items):
        say += "\n\nTell me your city and country first, so each pitch says where to ship."
    if locked:
        say += f"\n\nYou're out of free pitches for {', '.join(name for name in locked if name)} this month."
    state.final_say = say
    return {"ok": True, "status": "drafted", "count": n, "brands": [{"name": i["brand_name"]} for i in items]}


def _dress_pitch(state: TurnState, pitch: Dict[str, Any], shipping: Dict[str, Any],
                 loc_display: str, followup: bool = False):
    """Deal ask + shipping line on a fresh pitch. Returns (pitch, paid_ask)."""
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
    inputs = pitch_pref_inputs(state.db(), state.notes, pitch.get("brand_id"), creator=state.creator,
                               scrape=state.scrape, kit=state.kit, location=loc_display)
    if state.first:
        inputs["slots"].setdefault("first_name", state.first)
        inputs["slots"]["first_name"] = inputs["slots"]["first_name"] or state.first
    pitch = apply_pitch_prefs(pitch, state.notes, slots=inputs["slots"], facts=inputs["facts"],
                              rewrite=not followup)
    needs_loc = pitch_has_placeholder(pitch.get("body") or "")
    pitch["needs_location"] = needs_loc
    if loc_display and not needs_loc:
        pitch["location_display"] = loc_display
    return pitch, pitch_paid


def _pitch_location(state: TurnState):
    shipping = resolve_pitch_location(state.creator, state.scrape, state.notes)
    loc_display = ""
    if not shipping.get("needs_location"):
        loc_display = str(shipping.get("display") or "").strip()
        if pitch_has_placeholder(loc_display):
            loc_display = ""
    return shipping, loc_display


def _draft_manual(state: TurnState, brand_name: str, to_email: str,
                  prior_name: Optional[str]) -> Dict[str, Any]:
    """Pitch a brand that isn't in the directory, to the inbox the creator gave."""
    R = _routes()
    shipping, loc_display = _pitch_location(state)
    base = R._manual_pitch(state.creator_id, brand_name,
                           city=shipping.get("city") or "", country=shipping.get("country") or "")
    if not base or not (base.get("subject") or base.get("body")):
        return {"ok": False, "status": "failed", "brand": brand_name, "error": "pitch generator failed"}
    pitch = {
        "brand_id": None,
        "brand_name": brand_name,
        "brand_logo": None,
        "subject": base.get("subject") or "",
        "body": base.get("body") or "",
    }
    pitch, pitch_paid = _dress_pitch(state, pitch, shipping, loc_display)
    pitch = _with_inbox(pitch, to_email)
    pitch["off_directory"] = True
    state.notes = mark_draft_pending(state.notes, {"id": None, "name": brand_name})
    state.pitch = pitch
    state.brands = []
    state.add_chips(pitch_confirm_chips({"id": None, "name": brand_name}))
    R._log_polly_event(state.creator_id, "manual_pitch_drafted", {"brand_name": brand_name[:120]})
    return {
        "ok": True,
        "status": "drafted",
        "brand": brand_name,
        "off_directory": True,
        "inbox": to_email,
        "inbox_from_creator": True,
        "opens_in_mail_app": bool(pitch.get("mailto")),
        "needs_city_and_country": pitch["needs_location"],
        "ships_from": pitch.get("location_display") or None,
        "paid_ask": bool(pitch_paid),
        "parked_previous_draft": prior_name,
        "used_free_pitch": False,
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
                target = {"id": None, "name": named, "needs_location": False}
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
        target_id = _int(target.get("id"))
        if target_id:
            already = target_id in pitched_id_set(state.notes)
        else:
            sent_names = {str(n).strip().lower() for n in state.notes.get("pitched_brand_names") or []}
            already = name.strip().lower() in sent_names
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
    pending = state.notes.get("pending_brand_update")
    then_logged = None
    if isinstance(pending, dict) and (
        (pending.get("brand_id") and _int(pending.get("brand_id")) == _int(target.get("id")))
        or brand_names_agree(pending.get("brand_name"), name)
    ):
        state.notes.pop("pending_brand_update", None)
        update = log_brand_update(state, {"event": pending.get("event"), "brand_name": name,
                                          "brand_id": target.get("id")})
        if update.get("ok"):
            then_logged = pending.get("event")
            state.brands = []
            next_cards = []
    return {
        "ok": True,
        "then_logged": then_logged,
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


def _status_row(row: Dict[str, Any]) -> Dict[str, Any]:
    tr = row.get("tracker") or {}
    return {
        "brand": row.get("name"),
        "status": tr.get("label") or row.get("stage_label"),
        "who_acts_next": tr.get("who_next"),
        "what_it_means": tr.get("copy"),
        "brand_signals": tr.get("signals") or [],
        "last_event": row.get("last_event"),
        "last_event_at": row.get("last_event_at"),
        "next_due_at": row.get("due_at"),
    }


def get_pitch_status(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("brand_name") or "").strip()
    try:
        rows = tracker.list_relationships(state.db(), state.creator_id)
    except Exception as err:
        print(f"[Polly] pitch status skipped: {err}")
        return {"ok": False, "error": "could not read the tracker"}
    if name or args.get("brand_id"):
        row = find_brand(state, brand_id=args.get("brand_id"), brand_name=name)
        bid = (row or {}).get("id")
        key = name.lower()
        hit = [r for r in rows if (bid and r.get("brand_id") == bid)
               or (key and key in str(r.get("name") or "").lower())]
        if not hit:
            return {"ok": True, "status": "not_tracked", "brand": (row or {}).get("name") or name,
                    "explain": "Nothing is logged for this brand: no pitch sent and no application. "
                               "Say so plainly; offer to draft a pitch."}
        return {"ok": True, "status": "found", "brand": hit[0].get("name"), "relationship": _status_row(hit[0])}
    return {"ok": True, "status": "overview", "count": len(rows),
            "relationships": [_status_row(r) for r in rows[:10]]}


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
    if result.get("action") == "needs_sent_first":
        state.notes["pending_brand_update"] = {"event": event, "brand_id": (brand or {}).get("id"),
                                               "brand_name": (brand or {}).get("name")}
        state.add_chips(result.get("chips") or [])
        return {
            "ok": False,
            "status": "needs_sent_first",
            "brand": (brand or {}).get("name"),
            "explain": "No send is logged for this brand, so its status can't change yet. Ask: "
                       "\"Did that pitch go out from your email first?\" If they say yes, call "
                       "log_pitch_sent for this brand; the reply is logged right after. Never say the "
                       "brand replied, is interested or shipped until then.",
        }
    state.notes.pop("pending_brand_update", None)
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
        gigs, has_more, locked = page_polly_gigs(state.creator_id, scrape=state.scrape, notes=state.notes,
                                                 history=state.messages)
    except Exception as err:
        print(f"[Polly] gigs failed: {err}")
        gigs, has_more, locked = [], False, 0
    state.gigs = gigs or []
    state.gigs_has_more = bool(has_more)
    state.gigs_locked = int(locked or 0)
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
        "locked_for_pro": state.gigs_locked,
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
    if topic == "week" and state.balance.get("is_unlimited"):
        return _monday_board(state)
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


def _monday_board(state: TurnState) -> Dict[str, Any]:
    """Pro's week is the Monday board, shown as its own card."""
    from services.pro_manager import board_summary, load_board, week_key
    try:
        board = load_board(state.db(), state.creator_id, kit=state.kit)
    except Exception as err:
        print(f"[Polly] board load failed: {err}")
        state.db().rollback()
        return {"ok": False, "error": "board unavailable"}
    state.board = board
    state.notes["board_week"] = week_key()
    state.final_say = board_summary(board, state.first)
    return {
        "ok": True,
        "topic": "week",
        "board_card_shown": True,
        "left_this_week": [t["label"] for t in board["tasks"] if not t["done"]],
    }


def save_profile_facts(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    R = _routes()
    saved: List[str] = []
    city = str(args.get("city") or "").strip()
    country = str(args.get("country") or "").strip()
    draft_updated = False
    rejected_location = bool(city and country) and not parse_location_reply(f"{city}, {country}")
    if rejected_location:
        city = country = ""
    if city and country:
        display = f"{city}, {country}"
        state.notes["location"] = display
        R._persist_shipping_location(state.creator_id, city, country)
        saved.append("location")
        card = last_thread_pitch(state.messages)
        pending = state.notes.pop("pending_batch", None)
        if pending and pending.get("brand_ids"):
            res = draft_pitch_batch(state, {"brand_ids": pending["brand_ids"]})
            if state.final_say and res.get("status") == "drafted":
                state.final_say = f"Got it, {display}. " + state.final_say.removeprefix("Done. ")
            draft_updated = bool(res.get("ok"))
        elif _fill_batch_location(state, city, country, display):
            draft_updated = True
        elif card and pitch_has_placeholder(card.get("body") or ""):
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
    pitch_raw = {k: args[k] for k in PITCH_PREF_KEYS if args.get(k) not in (None, "", [])}
    pitch_raw.update({k: None for k in (args.get("clear_pitch_prefs") or []) if k in PITCH_PREF_KEYS})
    prefs.update(clean_pitch_prefs(pitch_raw))
    for key in ("avoid_categories", "avoid_retailers"):
        vals = [str(v).strip().lower() for v in (args.get(key) or []) if str(v).strip()]
        if vals:
            prefs[key] = vals
    if prefs:
        state.notes = merge_prefs(state.notes, prefs)
        saved.append("preferences")
    pitch_saved = sorted(k for k in prefs if k in PITCH_PREF_KEYS)
    patch = {k: args.get(k) for k in ("goal_30d", "stage", "niche", "biggest_challenge", "dream_brands")
             if args.get(k)}
    if patch:
        state.notes = merge_notes_patch(state.notes, patch)
        saved.extend(sorted(patch))
    out = {"ok": True, "saved": saved, "draft_city_filled": draft_updated}
    if pitch_saved:
        out["pitch_prefs_saved"] = pitch_saved
        out["pitch_prefs_now"] = pitch_prefs_line(get_prefs(state.notes)) or "default pitch"
        out["explain"] = ("Confirm in one line, e.g. \"Saved. I won't include engagement in future pitches.\" "
                          "These apply to every new pitch, including Autopilot. The card already on screen "
                          "only changes if you call edit_pitch. You may advise against a preference once, "
                          "but never refuse it.")
    if rejected_location:
        out["location_not_saved"] = "That isn't a city and country. Ask where they ship from."
    return out


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


def edit_pitch(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    instruction = str(args.get("instruction") or state.user_text or "").strip()
    card = last_thread_pitch(state.messages)
    if not card:
        return {"ok": False, "status": "no_draft_on_screen",
                "explain": "There's no pitch card in this chat to edit. Offer to draft one."}
    brand = card.get("brand_name") or card.get("name")
    named = str(args.get("brand_name") or "").strip()
    if named and brand and not brand_names_agree(named, brand):
        return {"ok": False, "status": "other_brand_on_screen", "brand": brand,
                "explain": f"The pitch on screen is for {brand}. Offer to draft {named} first."}
    if not instruction:
        return {"ok": False, "error": "instruction required"}
    result = revise_pitch(card, instruction)
    saved_pref = bool(result.get("pref"))
    if saved_pref:
        state.notes = merge_prefs(state.notes, result["pref"])
    if not result.get("changed"):
        return {"ok": False, "status": "not_changed", "brand": brand,
                "why_not": result.get("why_not") or "", "saved_for_future_pitches": saved_pref,
                "explain": "The pitch did NOT change. Say so plainly and give why_not. Never say it was updated."}
    updated = apply_pitch_prefs(result["pitch"], state.notes, rewrite=False)
    state.pitch_update = updated
    state.pitch = updated
    state.add_chips(pitch_confirm_chips({"id": updated.get("brand_id"), "name": updated.get("brand_name")}))
    return {"ok": True, "status": "pitch_updated", "brand": brand, "version": updated.get("version"),
            "saved_for_future_pitches": saved_pref, "card_shows": "Updated just now"}


def _reply_thread(state: TurnState, brand_id: Optional[int], name: str) -> Dict[str, str]:
    """Inbox and subject of the pitch they're replying to: the Autopilot send, else the chat card."""
    if brand_id:
        cur = state.db().cursor()
        try:
            cur.execute(
                "SELECT to_email, subject FROM polly_autopilot_queue "
                "WHERE creator_id = %s AND brand_id = %s AND status = 'sent' LIMIT 1",
                (state.creator_id, brand_id),
            )
            row = cur.fetchone()
            if row:
                return {"email": row[0] or "", "subject": row[1] or ""}
        except Exception:
            state.db().rollback()
    for msg in reversed(state.stored_messages or state.messages or []):
        pitch = msg.get("pitch") if isinstance(msg, dict) else None
        if not isinstance(pitch, dict) or pitch.get("is_reply"):
            continue
        same_id = brand_id and _int(pitch.get("brand_id")) == brand_id
        if same_id or brand_names_agree(name, pitch.get("brand_name")):
            return {"email": pitch.get("email") or "", "subject": pitch.get("subject") or ""}
    return {"email": "", "subject": ""}


def draft_brand_reply(state: TurnState, args: Dict[str, Any]) -> Dict[str, Any]:
    from services.paid_ask import (
        ADDRESS_LINE, USAGE_DAYS, ad_usage_ask, free_teaser, interested_reply, mailto, reply_preview,
        usage_rate,
    )
    from services.polly_gigs import creator_followers

    posted = str(args.get("moment") or "").strip().lower() == "posted"
    name = str(args.get("brand_name") or "").strip()
    row = find_brand(state, brand_id=args.get("brand_id"), brand_name=name)
    brand_id = _int((row or {}).get("id") or args.get("brand_id"))
    brand_name = (row or {}).get("name") or name
    if not brand_name:
        return {"ok": False, "error": "Which brand? Pass brand_name."}
    followers = creator_followers(state.scrape) or None
    rate = usage_rate(followers)
    thread = _reply_thread(state, brand_id, brand_name)
    first = state.first or ""
    if posted:
        mail = ad_usage_ask(brand_name, first, followers, subject=thread["subject"])
    else:
        creator = state.creator or {}
        full = " ".join(p for p in (creator.get("first_name"), creator.get("last_name")) if p).strip()
        shipping = resolve_pitch_location(state.creator, state.scrape, state.notes)
        ship_to = "" if shipping.get("needs_location") else str(shipping.get("display") or "")
        address = creator.get("shipping_address")
        mail = interested_reply(brand_name, first, full, ship_to, followers,
                                subject=thread["subject"], address=address if isinstance(address, dict) else None)
    if not state.balance.get("is_unlimited"):
        state.final_say = free_teaser(brand_name, followers, posted=posted)
        state.paywall_payload = {
            "brand_name": brand_name,
            "brand_id": brand_id,
            "kind": "reply",
            "preview": reply_preview(mail["body"]),
        }
        state.add_chips(paywall_unlock_chips({"id": brand_id, "name": brand_name}))
        state.paywall_moment = {"moment": "paid_reply", "brand_id": brand_id}
        return {"ok": True, "status": "pro_only", "brand": brand_name, "usage_rate": rate}
    state.pitch = {
        "brand_id": brand_id,
        "brand_name": brand_name,
        "subject": mail["subject"],
        "body": mail["body"],
        "email": thread["email"] or None,
        "mailto": mailto(thread["email"], mail["subject"], mail["body"]),
        "is_reply": True,
        "reply_kind": "ad_usage" if posted else "interested",
    }
    state.brands = []
    try:
        tracker.add_event(
            state.db(), state.creator_id, "ad_usage_asked" if posted else "paid_ask_drafted",
            f"{'Ad usage ask' if posted else 'Reply + usage rate'} · {brand_name}",
            brand_id=brand_id, event_data={"usage_rate": rate},
        )
    except Exception as err:
        print(f"[Polly] reply event skipped: {err}")
        state.db().rollback()
    needs_street = ADDRESS_LINE in mail["body"]
    if posted:
        say = (f"Here's the ask for **{brand_name}**: {USAGE_DAYS} days of ad usage for **${rate}**, "
               "raw file included. Send it as a reply in your thread with them.")
    else:
        say = (f"Here's your reply to **{brand_name}**. It gives your shipping address and offers "
               f"{USAGE_DAYS} days of ad usage for **${rate}**. Brands that gift often pay for usage, and "
               "asking now costs nothing.")
        if needs_street:
            say += " **Fill in your street address** before you send."
    state.final_say = say
    return {
        "ok": True,
        "status": "reply_drafted",
        "brand": brand_name,
        "moment": "posted" if posted else "interested",
        "usage_rate": f"${rate} for {USAGE_DAYS} days of ad use",
        "reply_card_shown": True,
        "needs_street_address": needs_street,
    }


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
        "pro_does": "Polly as their manager. Goal: one yes this month, turned into a paid usage deal. "
                    "Every Monday a board with the week: setup, paid briefs to apply to, and replies to send. "
                    "Autopilot picks brands from their settings and sends ~6 gifted pitches a week from their "
                    "Gmail with no approval step, plus follow-ups. When a brand says yes, "
                    "Polly writes the reply with a usage rate to run the video as an ad. To change who gets "
                    "pitched they adjust niche and targets in Settings. Unlimited roster applications.",
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
        "description": "Write the pitch email for one brand and show it as a card. Only when they want "
                       "to pitch/contact that brand (or tapped its button). The card exists only after this "
                       "returns status=drafted; never say a pitch is ready or coming without calling it. "
                       "Brands outside the directory need to_email (ask them for the brand's PR email). "
                       "When they give an address for any brand (a different inbox, a retry after a "
                       "bounce), pass it as to_email; their address wins over the directory's. Server "
                       "checks credits; if they're out it returns locked and shows the paywall. Use "
                       "followup=true for a follow-up to a directory brand they already pitched.",
        "parameters": _obj({
            "brand_name": {**_STR, "description": "The company they mean, exactly as named"},
            "brand_id": {**_INT, "description": "Directory id, only if it is that same company"},
            "to_email": {**_STR, "description": "Inbox the creator gave for this brand, exactly as typed"},
            "followup": _BOOL,
            "confirm_cold": {**_BOOL, "description": "They chose to pitch anyway after a low-reply warning"},
        }),
        "handler": draft_pitch,
    },
    {
        "name": "log_pitch_sent",
        "description": "Record that the creator sent a pitch from their inbox ('I sent it', 'done', "
                       "'emailed them'), including brands outside the directory they emailed themselves. "
                       "Omit the brand to log the unsent draft. Refuses if the latest pitch is locked or "
                       "the draft still needs a city. Only this tool may log a send.",
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
        "name": "get_pitch_status",
        "description": "Read where things stand with a brand (or all brands): status (Drafted, Sent, "
                       "Replied, Shipping, Content due, Posted, Dropped; Applied / Picked / Not this time "
                       "for open PR campaigns), who acts next, and brand signals (how many creators "
                       "pitched it in the last 30 days, when it last replied to anyone, whether it opened "
                       "their kit). Use for 'did X reply', 'what's happening with X', 'where are my "
                       "pitches', 'should I follow up'. Only state what it returns.",
        "parameters": _obj({"brand_name": {**_STR, "description": "Omit for an overview of all brands"},
                            "brand_id": _INT}),
        "handler": get_pitch_status,
    },
    {
        "name": "find_paid_gigs",
        "description": "Show live paid UGC briefs they can apply to (pulled from AspireIQ, LinkedIn and "
                       "other boards). Use for paid UGC / gigs / who pays. Not for gifted PR. "
                       "If locked_for_pro > 0, say that many more are on the Paid UGC tab with Pro; "
                       "never describe gigs that weren't returned.",
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
                       "avoid, goal, stage, niche, biggest challenge, dream brands, and standing pitch "
                       "preferences for every future pitch (what to lead with, which platforms, offer, "
                       "deliverables, usage rights, tone, their own template).",
        "parameters": _obj({
            "city": _STR, "country": _STR,
            "gifted_only": _BOOL,
            "show_engagement_rate": {**_BOOL, "description": "false when they don't want engagement in pitches"},
            "lead_metric": {**_STR, "enum": ["followers", "views", "engagement", "none"]},
            "platforms": {"type": "array", "items": {**_STR, "enum": ["tiktok", "instagram", "youtube",
                                                                       "pinterest", "facebook", "twitch", "x"]},
                          "description": "Only the platforms they want mentioned in pitches"},
            "offer_type": {**_STR, "enum": ["gifted_post", "gifted_ugc", "gifted_ugc_photos", "paid_only"]},
            "deliverables": {**_STR, "description": "Exactly as they say it, e.g. 1 organic TikTok + 2 UGC videos"},
            "usage_rights_months": {**_INT, "description": "Months of usage rights to offer; 0 = none"},
            "tone": {**_STR, "enum": ["friendly", "professional"]},
            "custom_template": {**_STR, "description": "Their own pitch text, verbatim. Slots: {{brand}}, "
                                                       "{{product}}, {{first_name}}, {{handle}}, {{city}}, {{kit_url}}"},
            "clear_pitch_prefs": {"type": "array", "items": _STR,
                                  "description": "Pitch preference names to reset to default"},
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
        "name": "edit_pitch",
        "description": "Change the pitch on screen the way the creator asks: remove engagement numbers, "
                       "update a follower count they give, shorter, more professional, different "
                       "deliverables, etc. The only way a pitch changes. Only after it returns "
                       "status=pitch_updated may you say the pitch changed; if not_changed, say it didn't "
                       "and why. saved_for_future_pitches=true means the rule also applies to every "
                       "future pitch, so tell them that.",
        "parameters": _obj({
            "instruction": {**_STR, "description": "What to change, in the creator's words"},
            "brand_name": {**_STR, "description": "Only if they named the brand whose pitch to edit"},
        }, ["instruction"]),
        "handler": edit_pitch,
    },
    {
        "name": "draft_brand_reply",
        "description": "Write the creator's reply to a brand that said yes to gifting (moment=interested: "
                       "shipping address plus a paid usage rate to run the video as an ad), or the ask "
                       "after they posted (moment=posted: 'can you run this as an ad?'). Use when they say "
                       "a brand is interested / said yes and want help replying, or they posted and want "
                       "to get paid. Shows it as a card on Pro; on free it returns the one-line version.",
        "parameters": _obj({
            "brand_name": _STR,
            "brand_id": _INT,
            "moment": {**_STR, "enum": ["interested", "posted"]},
        }, ["brand_name"]),
        "handler": draft_brand_reply,
    },
    {
        "name": "offer_pro",
        "description": "Show the Pro upgrade button ($19/mo) when they ask about Pro, more "
                       "pitches, or credits resetting. Returns the free path too, for when they can't afford it.",
        "parameters": _obj({"brand_name": {**_STR, "description": "Brand they want to pitch next, if any"}}),
        "handler": offer_pro,
    },
]

_BY_NAME: Dict[str, Callable[[TurnState, Dict[str, Any]], Dict[str, Any]]] = {
    t["name"]: t["handler"] for t in TOOLS
}
# Button-only tools: run from a tapped chip, never offered to the model.
_BY_NAME["draft_pitch_batch"] = draft_pitch_batch

INTENT_FOR_TOOL = {
    "suggest_brands": "suggest_brands",
    "search_directory": "chat",
    "draft_pitch": "generate_pitch",
    "draft_pitch_batch": "generate_pitch",
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
