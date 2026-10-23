"""When Gemini is down: route with keyword heuristics to one tool, reply from templates.

Also supplies `template_reply`, the guardrails' last resort when the model's reply
contradicts what the tools did.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.polly import (
    classify_intent_heuristic,
    directory_brand_ask,
    is_done_turn,
    last_thread_pitch,
    parse_handle_revision,
    parse_location_reply,
)
from services.polly_persona import (
    persona_ask_brand,
    persona_brand_intro,
    persona_followup_intro,
    persona_gigs_intro,
    persona_handle_fixed,
    persona_hold_pitch_send,
    persona_location_filled,
    persona_paywall_retry,
    persona_pitch_intro,
    persona_unknown_brand,
)
from services.polly_faq import faq_answer
from services.polly_pitch_edit import wants_no_engagement
from services.polly_prefs import parse_preferences
from services.polly_tools import _EVENTS, run_tool
from services.polly_turn_state import TurnState

_LIFECYCLE_TO_EVENT = {v: k for k, v in _EVENTS.items()}
_COACH_TOPIC = {
    "coach_portfolio": "kit",
    "coach_profile": "profile",
    "coach_rates": "rates",
    "coach_week": "week",
    "explain_newcollab": "how_it_works",
}


def _last(state: TurnState) -> Optional[Dict[str, Any]]:
    return state.tool_log[-1] if state.tool_log else None


CANNED_MENU = "I'm here. Want me to line up brands, draft a pitch, or look at your kit?"


def no_tool_reply(user_text: Optional[str]) -> str:
    """Nothing ran this turn: answer the question if it's a known one, never a menu for a real message."""
    answer = faq_answer(user_text or "")
    if answer:
        return answer
    words = (user_text or "").split()
    if len(words) <= 1:
        return CANNED_MENU
    return ("I don't want to guess on this one and get it wrong. "
            "Tell me which brand it's about, or what you were trying to do, and I'll sort it.")


def template_reply(state: TurnState) -> str:
    """A reply built only from what the tools returned this turn."""
    if state.final_say:
        return state.final_say
    entry = _last(state)
    if not entry:
        return no_tool_reply(state.user_text)
    name = entry.get("name")
    res: Dict[str, Any] = entry.get("result") or {}
    status = res.get("status")
    brand = res.get("brand") if isinstance(res.get("brand"), str) else (res.get("brand") or {}).get("name")

    if name == "draft_pitch":
        if status == "drafted" and state.pitch:
            pitch = state.pitch
            if pitch.get("is_followup"):
                return persona_followup_intro(brand or "them", has_mailto=bool(pitch.get("mailto")))
            return persona_pitch_intro(
                brand or "them",
                has_mailto=bool(pitch.get("mailto")),
                paid=bool(res.get("paid_ask")),
                kit=state.kit,
                needs_location=bool(pitch.get("needs_location")),
                location_display=pitch.get("location_display") or "",
            )
        if status == "not_in_directory":
            return persona_unknown_brand(str(res.get("searched_for") or "that brand"), state.brands)
        return "I couldn't draft that one just now. Try again in a moment, or pick another brand."
    if name == "search_directory":
        if res.get("found"):
            extra = (" You've already pitched them." if res.get("already_pitched")
                     else " Want me to draft your pitch?")
            category = (res.get("brand") or {}).get("category")
            return f"Yes, **{brand}** is in the directory" + (f" ({category})." if category else ".") + extra
        return persona_unknown_brand(str(res.get("searched_for") or "that brand"), state.brands)
    if name == "suggest_brands":
        if state.brands:
            return persona_brand_intro(state.brands, state.profile_context,
                                       deal_intent=state.notes.get("deal_intent"))
        return ("That's every match I have for you right now. Pitch one of the brands above, "
                "or check back tomorrow and I'll have new ones.")
    if name == "log_pitch_sent":
        if status in ("logged", "already_logged"):
            lead = "Already logged." if status == "already_logged" else "Logged."
            return f"{lead} **{brand}** is on your Timeline. I'll check if they replied on day 4."
        if status == "latest_pitch_is_locked":
            line = f"The **{res.get('locked_brand')}** pitch is still locked, so it hasn't gone out yet."
            if res.get("unsent_draft"):
                line += f" Did you send the **{res['unsent_draft']}** draft instead?"
            return line
        if status == "draft_still_has_city_placeholder":
            return persona_hold_pitch_send(brand)
        return "Which brand did you send it to? I'll log it on your Timeline."
    if name == "log_brand_update":
        if status == "needs_sent_first":
            return (f"Did your **{brand or 'that'}** pitch go out from your email first? "
                    "Once I log the send, I'll log their reply.")
        if not res.get("ok"):
            return "Which brand is that about? I'll update your Timeline."
        return res.get("what_changed") or "Updated your Timeline."
    if name == "find_paid_gigs":
        return persona_gigs_intro(state.gigs, more=bool((entry.get("args") or {}).get("more")))
    if name == "get_coaching_facts":
        return res.get("playbook") or "Let's look at your kit together. Tap **My portfolio**."
    if name == "save_profile_facts":
        if res.get("draft_city_filled") and state.pitch:
            return persona_location_filled(state.pitch.get("brand_name"), state.notes.get("location"))
        if res.get("pitch_prefs_saved"):
            return (f"Saved. Every new pitch, Autopilot included, now follows this: "
                    f"{res.get('pitch_prefs_now')}.")
        return "Got it, saved. What do you want to land first?"
    if name == "fix_draft_handle" and state.pitch:
        args = entry.get("args") or {}
        return persona_handle_fixed(state.pitch.get("brand_name"), args.get("correct_handle") or "",
                                    args.get("wrong_handle") or "")
    if name == "edit_pitch":
        future = " I'll do the same in every pitch from now on." if res.get("saved_for_future_pitches") else ""
        if status == "pitch_updated":
            return f"Done, the **{brand or 'pitch'}** card above is updated.{future}"
        if status == "no_draft_on_screen":
            return "There's no pitch on screen to change yet. Which brand should I write one for?"
        why = res.get("why_not") or "I couldn't make that change."
        return f"The pitch hasn't changed: {why[:1].lower() + why[1:]}{future}"
    if name == "get_pitch_status":
        return status_reply(res)
    if name == "offer_pro":
        return persona_paywall_retry(res.get("button_shown_for"))
    return "Done. What's next?"


def _status_line(rel: dict) -> str:
    line = f"**{rel.get('brand')}**: {rel.get('status') or 'Sent'}"
    if rel.get("who_acts_next") and rel["who_acts_next"] != "Nobody":
        line += f" · next: {rel['who_acts_next'].lower()}"
    return line


def status_reply(res: dict) -> str:
    status = res.get("status")
    if status == "not_tracked":
        return (f"Nothing's logged for **{res.get('brand') or 'that brand'}** yet: no pitch sent and no "
                "application. Want me to write one?")
    if status == "found":
        rel = res.get("relationship") or {}
        parts = [_status_line(rel) + "."]
        if rel.get("what_it_means"):
            parts.append(rel["what_it_means"])
        if rel.get("brand_signals"):
            parts.append(rel["brand_signals"][0] + ".")
        return " ".join(parts)
    rels = res.get("relationships") or []
    if not rels:
        return "Nothing on your Timeline yet. Want me to line up three brands to pitch?"
    return "Here's where things stand:\n\n" + "\n".join(f"- {_status_line(r)}" for r in rels[:6])


def run_fallback(state: TurnState) -> str:
    """One heuristic tool call, then a template reply. No LLM."""
    text = state.user_text or ""
    draft = state.draft()
    if is_done_turn(text):
        run_tool(state, "log_pitch_sent", {})
        return template_reply(state)
    asked = directory_brand_ask(text)
    if asked:
        run_tool(state, "search_directory", {"name": asked})
        return template_reply(state)
    if draft and draft.get("needs_location"):
        place = parse_location_reply(text)
        if place:
            run_tool(state, "save_profile_facts", {"city": place.get("city"), "country": place.get("country")})
            return template_reply(state)
    handle = parse_handle_revision(text) if draft else None
    if handle:
        run_tool(state, "fix_draft_handle", {"correct_handle": handle.get("keep"), "wrong_handle": handle.get("drop")})
        return template_reply(state)
    if last_thread_pitch(state.messages) and wants_no_engagement(text):
        run_tool(state, "edit_pitch", {"instruction": text})
        return template_reply(state)
    prefs = parse_preferences(text)
    if prefs:
        run_tool(state, "save_profile_facts", prefs)
    else:
        answer = faq_answer(text)
        if answer:
            return answer

    decision = classify_intent_heuristic(text, state.suggested, history=state.messages)
    intent = decision.get("intent")
    if intent == "suggest_brands":
        run_tool(state, "suggest_brands", {})
    elif intent == "generate_pitch" and (decision.get("brand_name") or decision.get("brand_id")):
        run_tool(state, "draft_pitch", {"brand_name": decision.get("brand_name"),
                                        "brand_id": decision.get("brand_id")})
    elif intent == "suggest_gigs":
        run_tool(state, "find_paid_gigs", {})
    elif intent in _COACH_TOPIC:
        run_tool(state, "get_coaching_facts", {"topic": _COACH_TOPIC[intent]})
    elif intent == "ask_brand":
        return persona_ask_brand()
    else:
        from services.polly_tracker import classify_lifecycle_heuristic
        life = classify_lifecycle_heuristic(
            text,
            list(state.suggested or []) + [
                {"id": t.get("brand_id"), "name": t.get("brand_name")}
                for t in (state.tracker_ctx.get("active_tasks") or [])
            ],
            draft,
        )
        event = _LIFECYCLE_TO_EVENT.get(life.get("intent"))
        if event and life.get("confidence", 0) >= 0.7 and event != "pitch_not_sent":
            run_tool(state, "log_brand_update", {"event": event, "brand_name": life.get("brand"),
                                                 "brand_id": life.get("brand_id")})
    if prefs and len(state.tool_log) == 1:
        from services.polly_persona import persona_pref_ack
        return persona_pref_ack(prefs)
    return template_reply(state)
