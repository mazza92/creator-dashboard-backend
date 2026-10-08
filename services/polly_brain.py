"""Polly's brain: Gemini orchestrates every free-text turn.

Gemini reads the conversation plus the server's TurnState, calls tools (directory
search, draft pitch, log send, gigs, coaching facts...), reads their results, and
only then writes the reply. The server runs the tools and owns the facts; the
guardrails check the reply against what the tools actually did.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

import services.polly as polly
from services.polly_persona import polly_system_prompt, scrub_polly_voice
from services.polly_tools import INTENT_FOR_TOOL, function_declarations, run_tool
from services.polly_turn_state import TurnState

MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 4


class BrainUnavailable(Exception):
    """Gemini could not be reached; the caller uses the fallback path."""


ORCHESTRATOR_RULES = """You are Polly's brain. Every message reaches you first. You decide what to do,
call tools to do it, read what they return, then write the reply.

How to work:
- Understand what the creator means from the whole conversation, not keywords. A question is a real request.
- Facts come from tools and TURN STATE only. Never guess whether a brand is in the directory: call
  search_directory. Never claim a pitch was drafted, sent, logged or locked unless a tool said so this turn.
- Brand they name = the brand you act on. If TURN STATE shows other cards, ignore them for this request.
  "yes <Brand>" or "<Brand> please" after you mentioned it means that brand.
- Pitch only when they want to pitch/contact a brand or tapped its button. Asking about a brand is
  search_directory, not draft_pitch.
- "I sent it" / "done" / "sent" = log_pitch_sent (no brand unless they named one). If it refuses, explain why
  in one line and ask the question it suggests.
- Replies (yes, no, "said no thanks", a question), PR shipped, posted, paid, bounced, still quiet =
  log_brand_update, before you answer, for any brand they name.
- Paid UGC / gigs / who pays = find_paid_gigs. Gifted PR / more brands / who should I pitch = suggest_brands.
- Coaching (kit, bio, replies, rates, this week, how the app works) = get_coaching_facts first, then coach
  from those facts. Don't invent follower counts or kit details.
- They tell you their city/country, preferences, goal, niche or challenge = save_profile_facts.
- Out of free pitches, Pro, credits reset, can't afford = offer_pro. No calendar dates for resets.
- Small talk, thanks, opinions: no tool, just answer.
- You may call more than one tool when the request needs it.

Writing the reply (after tools):
- Polly's voice: professional-friendly startup manager, short paragraphs, **bold** the one thing to do.
  1-3 emojis max. No pet names. No greeting restart mid-conversation.
- Cards, the pitch and buttons are already on screen. Don't paste the email, subject or a list of the cards;
  1-2 sentences pointing at them is enough after a pitch or cards.
- Name only brands from tool results or TURN STATE. Quote an inbox only if a tool returned it.
- If draft_pitch says needs_city_and_country, ask for city + country before they send.
- Don't write credit counts or "unlock Pro" lines after a send; the server adds them.
- UI is: Polly, Directory, Timeline, My Kit tabs. The pitch card has Open email / Copy email / Copy pitch.
  They send from their own inbox, then tap I sent it. No other screens exist.
- Ask at most one question.
- Never mention tools, searches or "the system". Say the result: "Yes, **Gucci** is in the directory, card
  below." Then offer the obvious next step (e.g. draft the pitch).
"""


def _system_prompt(state: TurnState, discovery_hint: str) -> str:
    from services.polly_discovery import notes_context
    from services.polly_tracker import session_context_text
    context = "\n\n".join(bit for bit in (
        state.profile_context,
        notes_context(state.notes),
        session_context_text(state.tracker_ctx),
    ) if bit)
    extra = ORCHESTRATOR_RULES + f"\nManager memory: {discovery_hint or 'none yet.'}"
    return polly_system_prompt(context, extra=extra)


def _contents(state: TurnState, prior: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    contents = []
    for msg in polly._turn_messages(state.messages):
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    turn = "TURN STATE (server facts, not the creator's words):\n" + json.dumps(
        state.to_model_json(), default=str,
    )
    if prior:
        turn += "\n\nServer already ran these tools for the button they tapped:\n" + json.dumps(prior, default=str)
    if contents and contents[-1]["role"] == "user":
        contents[-1]["parts"].append({"text": turn})
    else:
        contents.append({"role": "user", "parts": [{"text": state.user_text or "(tapped a button)"}, {"text": turn}]})
    return contents


def _payload(system: str, contents: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": function_declarations()}],
        "toolConfig": {"functionCallingConfig": {"mode": mode}},
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 900,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def _merge_parts(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join streamed text parts; keep function calls (and their signatures) whole."""
    parts: List[Dict[str, Any]] = []
    for part in chunks:
        if not isinstance(part, dict):
            continue
        if "text" in part and not part.get("thought") and "functionCall" not in part:
            if parts and "text" in parts[-1] and "functionCall" not in parts[-1]:
                parts[-1]["text"] += str(part["text"])
                if part.get("thoughtSignature"):
                    parts[-1]["thoughtSignature"] = part["thoughtSignature"]
                continue
            parts.append(dict(part))
        elif "functionCall" in part:
            parts.append(dict(part))
    return parts


def _stream_round(model: str, payload: Dict[str, Any], headers: Dict[str, str], sink) -> Dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
    raw_parts: List[Dict[str, Any]] = []
    usage: Dict[str, Any] = {}
    finish = None
    streamed = False
    with requests.post(url, json=payload, headers=headers, timeout=polly._LLM_TIMEOUT_SEC, stream=True) as resp:
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:400]
            except Exception:
                pass
            raise _HttpError(resp.status_code, body)
        resp.encoding = "utf-8"
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            try:
                chunk = json.loads(raw[5:].strip())
            except ValueError:
                continue
            cand = ((chunk.get("candidates") or [{}])[0]) or {}
            for part in ((cand.get("content") or {}).get("parts") or []):
                if not isinstance(part, dict):
                    continue
                raw_parts.append(part)
                if part.get("text") and not part.get("thought") and sink:
                    sink({"type": "delta", "text": str(part["text"])})
                    streamed = True
            finish = cand.get("finishReason") or finish
            if chunk.get("usageMetadata"):
                usage = chunk["usageMetadata"]
    parts = _merge_parts(raw_parts)
    if streamed and any("functionCall" in p for p in parts):
        sink({"type": "reset"})
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": usage}


class _HttpError(Exception):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def _post_round(model: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(url, json=payload, headers=headers, timeout=polly._LLM_TIMEOUT_SEC)
    if resp.status_code >= 400:
        raise _HttpError(resp.status_code, (resp.text or "")[:400])
    data = resp.json()
    cand = ((data.get("candidates") or [{}])[0]) or {}
    parts = _merge_parts((cand.get("content") or {}).get("parts") or [])
    cand["content"] = {"role": "model", "parts": parts}
    data["candidates"] = [cand]
    return data


class _Gemini:
    """Sticks to the first model that answers; later rounds reuse it."""

    def __init__(self):
        self.api_key = polly.get_gemini_key()
        self.model: Optional[str] = None
        self.headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def call(self, payload: Dict[str, Any], sink=None) -> Dict[str, Any]:
        models = [self.model] if self.model else polly._model_candidates()
        last = "Gemini failed"
        for model in models:
            for attempt in range(2):
                try:
                    data = _stream_round(model, payload, self.headers, sink) if sink else _post_round(
                        model, payload, self.headers,
                    )
                except _HttpError as err:
                    low = err.body.lower()
                    if err.status == 429 and ("depleted" in low or "prepayment" in low):
                        polly._GEMINI_DEPLETED = True
                        raise BrainUnavailable("Gemini billing credits depleted")
                    if err.status == 400 and attempt == 0 and payload["generationConfig"].pop("thinkingConfig", None):
                        continue
                    if err.status == 429:
                        time.sleep(0.4)
                    last = f"{model} {err}"
                    break
                except requests.RequestException as err:
                    last = f"{model} {type(err).__name__}"
                    break
                self.model = model
                from services.polly_llm_cost import usage_from_gemini
                usage = usage_from_gemini(data, model)
                polly.remember_polly_brain("gemini", model, usage)
                print(f"[Polly] brain=gemini model={model} in={usage.get('input_tokens')} "
                      f"out={usage.get('output_tokens')} usd={usage.get('usd')}")
                return data
        raise BrainUnavailable(polly._redact_secrets(last))


def _parts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    cand = ((data.get("candidates") or [{}])[0]) or {}
    return list((cand.get("content") or {}).get("parts") or [])


def _text(parts: List[Dict[str, Any]]) -> str:
    return "".join(str(p.get("text") or "") for p in parts if "text" in p and not p.get("thought")).strip()


def run_turn(
    state: TurnState,
    discovery_hint: str = "",
    prior_tools: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Orchestrate one turn. Returns the reply. Raises BrainUnavailable before any tool runs."""
    if not polly.llm_available():
        raise BrainUnavailable("Gemini not configured")
    sink = polly.stream_sink()
    gemini = _Gemini()
    system = _system_prompt(state, discovery_hint)
    contents = _contents(state, prior_tools or [])
    if state.final_say:
        return state.final_say
    reply = ""
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        mode = "AUTO" if round_no < MAX_TOOL_ROUNDS else "NONE"
        try:
            data = gemini.call(_payload(system, contents, mode), sink=sink)
        except BrainUnavailable:
            if round_no == 0 and not state.tool_log:
                raise
            break
        parts = _parts(data)
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if not calls:
            reply = _text(parts)
            break
        contents.append({"role": "model", "parts": parts})
        responses = []
        for part in calls[:MAX_CALLS_PER_ROUND]:
            call = part["functionCall"]
            name = str(call.get("name") or "")
            if sink:
                sink({"type": "tool", "name": name})
                if name in INTENT_FOR_TOOL:
                    sink({"type": "intent", "intent": INTENT_FOR_TOOL[name]})
            result = run_tool(state, name, call.get("args") or {})
            response = {"name": name, "response": result}
            if call.get("id"):
                response["id"] = call["id"]
            responses.append({"functionResponse": response})
        if state.final_say or state.auth_failed:
            return state.final_say
        contents.append({"role": "user", "parts": responses})
    return check_reply(state, reply, gemini=gemini, system=system, contents=contents)


# ----- guardrails ------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SEND_CLAIM_RE = re.compile(
    r"(?i)\b(i('ve| have) logged|logged (it|your|the)|on your timeline|pitch is out|marked (it )?as sent|"
    r"added (it )?to your timeline)\b"
)
_DRAFT_CLAIM_RE = re.compile(r"(?i)\b(i('ve| have) (drafted|written)|here'?s (your|the) (pitch|draft)|draft is ready)\b")
_CREDIT_LINE_RE = re.compile(
    r"(?i)[^.!?\n]*\b(\d+|no|zero|one|two|three)\s+(free\s+)?(pitch(es)?|unlocks?|credits?)\s+(left|remaining)\b[^.!?\n]*[.!?]?"
)
_UI_WORDS = frozenset({
    "polly", "directory", "timeline", "my kit", "my portfolio", "i sent it", "open email", "copy email",
    "copy pitch", "pro", "polly pro", "autopilot", "gmail", "tiktok", "instagram", "youtube", "ugc",
    "gifted", "paid", "day 4", "more brands",
})


def _bold_names(text: str) -> List[str]:
    out = []
    for match in re.finditer(r"\*\*([^*]{2,40})\*\*", text or ""):
        val = match.group(1).strip(" .!,:")
        words = val.split()
        if not words or len(words) > 4 or not val[:1].isupper():
            continue
        if val.lower() in _UI_WORDS or re.search(r"\d", val):
            continue
        if not polly.candidate_looks_like_brand_name(val):
            continue
        out.append(val)
    return out


def reply_problems(state: TurnState, reply: str) -> List[str]:
    problems: List[str] = []
    text = reply or ""
    if not text.strip():
        return ["empty reply"]
    logged = state.tools_ran("log_pitch_sent", "logged") or state.tools_ran("log_pitch_sent", "already_logged")
    if _SEND_CLAIM_RE.search(text) and not logged and not state.tools_ran("log_brand_update"):
        problems.append("The reply says a pitch was logged or sent, but log_pitch_sent did not log one this turn.")
    if _DRAFT_CLAIM_RE.search(text) and not state.pitch:
        problems.append("The reply says a pitch was drafted, but no pitch card exists this turn.")
    known = state.known_brand_names()
    typed = (state.user_text or "").lower()
    for name in _bold_names(text):
        if name.lower() in typed:
            continue
        if not any(polly.brand_names_agree(name, k) for k in known):
            problems.append(f"**{name}** is not a brand from the tools or TURN STATE. Don't name it.")
    allowed_emails = {str((state.pitch or {}).get("email") or "").lower(),
                      str((state.draft() or {}).get("email") or "").lower()}
    for email in _EMAIL_RE.findall(text):
        if email.lower() not in allowed_emails:
            problems.append(f"{email} was not returned by a tool. Don't quote inboxes you weren't given.")
    return problems


def _strip_server_lines(text: str) -> str:
    text = _CREDIT_LINE_RE.sub("", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def check_reply(state: TurnState, reply: str, gemini: Optional[_Gemini] = None,
                system: str = "", contents: Optional[List[Dict[str, Any]]] = None) -> str:
    """Return a reply that matches what the server actually did."""
    reply = scrub_polly_voice(reply or "")
    problems = reply_problems(state, reply)
    if problems and gemini and contents is not None:
        print(f"[Polly] guardrail repair: {problems}")
        sink = polly.stream_sink()
        if sink:
            sink({"type": "reset"})
        repair = list(contents)
        if reply:
            repair.append({"role": "model", "parts": [{"text": reply}]})
        repair.append({"role": "user", "parts": [{"text": (
            "SERVER CHECK: that reply broke these rules:\n- " + "\n- ".join(problems)
            + "\nRewrite the reply so it only states what the tools returned. Reply text only."
        )}]})
        try:
            data = gemini.call(_payload(system, repair, "NONE"), sink=sink)
            fixed = scrub_polly_voice(_text(_parts(data)))
        except BrainUnavailable:
            fixed = ""
        if fixed and not reply_problems(state, fixed):
            reply, problems = fixed, []
        elif sink:
            sink({"type": "reset"})
    if problems:
        print(f"[Polly] guardrail template: {problems}")
        from services.polly_fallback import template_reply
        reply = template_reply(state)
    if state.tools_ran("log_pitch_sent"):
        reply = _strip_server_lines(reply)
    return reply


def compose_reply(state: TurnState, reply: str) -> str:
    """Server-owned lines go last so the model can't drop or contradict them."""
    out = (reply or "").strip()
    for line in state.say_tail:
        if line and line.lower() not in out.lower():
            out = (out + "\n\n" + line).strip()
    return out
