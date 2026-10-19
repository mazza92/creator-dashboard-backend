"""Brand acquisition pipeline: cold sequence, reply handling, stages and activity log.

Closing = the brand publishes its first brief (an opportunities row). Every email,
reply, classification, held draft and stage change lands in brand_activity.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import secrets
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

BOOKING_URL = os.getenv("BRAND_BOOKING_URL") or "https://calendar.app.google/nzAibfYdjVnW1u1FA"
APP_URL = (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/")
OUR_DOMAIN = "newcollab.co"
FROM_NAME = "Mazza from Newcollab"
SIGNATURE = "Mazza\nFounder, Newcollab"

PIPELINE = "v2"
VARIANTS = ("swap", "ads", "ambassador")
MIN_TEST_PER_VARIANT = 80
FOLLOWUP_GAP_DAYS = {1: 3, 2: 4, 3: 7}
LAST_STEP = 3
NUDGE_DAYS = 3
MAX_NEW_PER_RUN = 50
MAX_QUEUE = 150
AUTO_CONFIDENCE = 0.7

POSITIVE_INTENTS = ("interested", "question", "meeting")
LOG_ONLY_INTENTS = ("bounce", "auto_reply", "booking", "not_interested", "affiliate", "unsubscribe", "later",
                    "forwarded")
FORWARDED_WAIT_DAYS = 5
CLOSED_STAGES = ("won", "lost", "bad_contact", "do_not_contact")
FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "live.com", "me.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
}
SKIP_CATEGORIES = ("gaming", "travel", "tech")
CORE_CATEGORIES = ("skincare", "beauty", "haircare", "wellness", "supplements", "fashion")
NICHE_FOR_CATEGORY = {
    "activewear": "fitness", "jewelry": "fashion", "luxury": "beauty", "sustainable": "home",
    "other": "lifestyle", "": "lifestyle",
}
LEGACY_STATUS = {
    "bounce": "wrong_email", "auto_reply": "auto_reply", "unsubscribe": "do_not_contact",
    "not_interested": "not_interested", "interested": "interested", "question": "interested",
    "meeting": "interested", "booking": "interested",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_HANDLE_RE = re.compile(r"(?<![\w.@])@[A-Za-z0-9_.]{3,}")
_NUMBER_RE = re.compile(r"\d[\d,.]*")


# --------------------------------------------------------------------------- names


def domain_of(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "@" in raw and "/" not in raw:
        return raw.rsplit("@", 1)[-1].strip(" .>")
    host = urlparse(raw if raw.startswith("http") else f"https://{raw}").hostname or ""
    return host.removeprefix("www.").strip(".")


def display_name(brand_name: Optional[str], website: Optional[str]) -> str:
    name = re.sub(r"\s+", " ", (brand_name or "")).strip()
    if len(name) >= 3 and not re.match(r"^(brand|testbrand|company|unknown|none|null|n/?a)[\s_\-]*\d*$", name, re.I):
        return name.replace("<", "").replace(">", "")
    host = domain_of(website)
    parts = [p for p in host.split(".") if p]
    if not parts:
        return ""
    prefixes = ("shop", "store", "my", "get", "try", "offer", "au", "eu", "us", "uk", "ca", "en")
    first = parts[1] if parts[0] in prefixes and len(parts) > 2 else parts[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", first) if w)


def first_name(contact_name: Optional[str]) -> str:
    token = re.split(r"[\s,]+", (contact_name or "").strip())[0] if contact_name else ""
    token = re.sub(r"[^A-Za-zÀ-ÿ'\-]", "", token)
    if len(token) < 2 or token.lower() in ("team", "the", "hello", "info", "support", "customer"):
        return ""
    return token[:1].upper() + token[1:].lower()


def niche_for(category: Optional[str]) -> str:
    cat = (category or "").strip().lower()
    return NICHE_FOR_CATEGORY.get(cat, cat or "lifestyle")


def brief_link(category: Optional[str], ref: str) -> str:
    return (
        f"{APP_URL}/brands/launch?niche={niche_for(category)}&ref={ref}"
        "&utm_source=cold_email&utm_medium=email&utm_campaign=brand_pipeline"
    )


def new_ref() -> str:
    return secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")


def text_to_html(text: str) -> str:
    blocks = []
    for part in (text or "").strip().split("\n\n"):
        lines = []
        for line in part.split("\n"):
            safe = html.escape(line)
            safe = _URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', safe)
            lines.append(safe)
        blocks.append(
            '<p style="margin:0 0 16px;font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'line-height:1.6;color:#222;">{"<br/>".join(lines)}</p>'
        )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:20px 24px;background:#fff;">' + "".join(blocks) + "</body></html>"
    )


def _greet(first: str, word: str = "Hey") -> str:
    return f"{word} {first}," if first else f"{word},"


# --------------------------------------------------------------------------- templates


def initial_email(variant: str, brand: str, first: str) -> Tuple[str, str]:
    greet = _greet(first)
    if variant == "ads":
        return (
            f"UGC ads for {brand}, product + shipping only",
            f"{greet}\n\n"
            f"Quick idea for {brand}: 5 creators make UGC you can run as ads, and all it costs is product + shipping.\n\n"
            "• You pick the creators from real profiles\n"
            "• Each one posts organically and sends you the raw content\n"
            "• No agency, no platform fee\n\n"
            f"Want me to line up 5 who fit {brand}?\n\n{SIGNATURE}",
        )
    if variant == "ambassador":
        return (
            f"5 creators for {brand}, no fee",
            f"{greet}\n\n"
            f"Start {brand} with a gifting test: 5 vetted micro-creators, product + shipping only.\n\n"
            "The ones who perform become long-term ambassadors. Every post and UGC file lands in one place, ready for ads.\n\n"
            f"Open to a test this month?\n\n{SIGNATURE}",
        )
    return (
        f"PR + UGC swap for {brand}?",
        f"{greet}\n\n"
        "We work with 1,800+ small creators looking for their first brand PR. "
        "Mostly beauty, skincare, and fashion, all under 10K followers.\n\n"
        "Would you want to test with 5 vetted creators? You approve them from fit profiles "
        "(niche, engagement, past content). You send PR. In return:\n\n"
        "• 5 organic posts on their accounts\n"
        "• 5 UGC pieces (photo or video) yours to reuse or run as ads\n"
        "• No platform fee. Just product + shipping.\n\n"
        f"Worth a look?\n\n{SIGNATURE}",
    )


def followup_email(step: int, brand: str, first: str, link: str) -> str:
    greet = _greet(first)
    if step == 1:
        return (
            f"{greet}\n\nBumping this in case it got buried. I can send 5 creator profiles that fit {brand} "
            "so you can judge before committing.\n\n"
            "If you're not the right person, who handles creators or UGC?\n\nMazza"
        )
    if step == 2:
        return (
            f"{greet}\n\nEasiest way to see it: pick your creators here, it takes 2 minutes.\n{link}\n\n"
            "Product + shipping only. The UGC is yours to run as ads.\n\nMazza"
        )
    return (
        f"{greet}\n\nLast note from me. If creator content isn't a priority for {brand} right now, no worries.\n\n"
        f"If it ever is, the creators are here:\n{link}\n\nMazza"
    )


def nudge_email(brand: str, first: str, link: str) -> str:
    return (
        f"{_greet(first, 'Hi')}\n\nDid you get a chance to look at the creators? Once you post the brief, "
        f"Polly pre-selects the 5 best fits for {brand} automatically.\n{link}\n\n"
        f"Or grab 15 min with me: {BOOKING_URL}\n\nMazza"
    )


def redirect_email(brand: str, first: str, referrer: str, link: str) -> str:
    who = referrer or "Your team"
    return (
        f"{_greet(first, 'Hi')}\n\n{who} pointed me your way for creator partnerships at {brand}.\n\n"
        f"Short version: 5 vetted micro-creators make UGC for {brand}, organic posts plus content you can run as ads. "
        "No fee, just product + shipping. You approve every creator first.\n\n"
        f"Creators who fit {brand}:\n{link}\n\nWorth a look?\n\n{SIGNATURE}"
    )


QUESTION_LINES = {
    "pricing": "Cost for the test: no platform fee, just product + shipping.",
    "countries": "In the brief you choose where you ship, and you only see creators in those countries.",
    "niche": "You see each creator's niche, engagement and past content before approving anyone, so you only pick real fits.",
    "demographics": "Each profile shows the creator's niche and audience, so you can check the fit before approving.",
    "portfolio": "The link shows real creator profiles with their past content, so you can judge the quality first.",
    "vetting": "Every creator is reviewed on niche, engagement and past content, and you approve each one yourself.",
    "usage_rights": "The UGC is yours to reuse on your channels and run as ads.",
}


def fallback_reply(first: str, brand: str, link: str, questions: List[str], wants_call: bool) -> str:
    asked = [q for q in dict.fromkeys(questions) if q in QUESTION_LINES and q != "pricing"]
    if "niche" in asked and "demographics" in asked:
        asked.remove("demographics")
    extra = [QUESTION_LINES[q] for q in asked[:2]]
    call = f"\n\nIf a quick call is easier, grab 15 min here: {BOOKING_URL}" if wants_call or extra else ""
    return (
        f"{_greet(first, 'Hi')}\n\nThanks for getting back to me. Here's how it works:\n\n"
        f"1. Post a quick brief (2 min): {link}\n"
        f"2. You see matched creators for {brand} with their profiles, engagement and past content. You approve who you want.\n"
        "3. You ship product. Each creator posts organically and sends you UGC you can reuse or run as ads.\n\n"
        "Your first 5 creators: no platform fee, just product + shipping."
        + ("\n\n" + " ".join(extra) if extra else "")
        + call
        + f"\n\n{SIGNATURE}"
    )


# --------------------------------------------------------------------------- classification

_AUTO_PATTERNS = (
    "out of office", "out-of-office", "automatic reply", "auto-reply", "autoreply", "auto reply",
    "away from the office", "on vacation", "on leave", "request received", "we've received your",
    "we have received your", "received your request", "received your message", "ticket", "how would you rate",
    "rate your", "rate the help", "take 10 seconds", "10 seconds to", "satisfaction survey", "did .* help",
    "this is an automated", "automated response", "auto-generated", "do not reply", "higher-than-usual volume",
    "how did we do", "how was your experience", "rate your experience", "feedback survey",
)
_LATER = (
    "not yet", "do not yet", "don't yet", "not at the moment", "not right now", "not currently looking",
    "not the right time", "in the near future", "in the future", "plan to introduce", "planning to introduce",
    "primarily focused", "currently focused", "maybe later", "later this year", "next year",
    "next quarter", "circle back", "revisit", "keep your details", "keep your information", "keep you in mind",
    "on file", "fully booked", "no budget",
)
_FORWARDED = (
    "passed your message", "passed your email", "passed your request", "passed this along", "passed it along",
    "passed along", "passed on your", "forwarded your", "forwarded this", "forwarded it", "shared your message",
    "shared this with", "escalate", "appropriate team", "relevant team", "right team",
    "someone will be in touch", "will be in touch if", "contact you if", "get back to you if",
)
_UNSUB = ("unsubscribe", "remove me", "stop emailing", "take me off", "do not contact", "don't contact")
_NOT_INTERESTED = (
    "not interested", "not looking", "no thank", "not at this time", "we'll pass", "we will pass", "not a fit",
    "don't currently", "do not currently", "shifted away", "not be moving forward", "not something we",
    "no longer accepting", "already committed",
)
_AFFILIATE = ("affiliate program", "affiliate programme", "affiliate team", "join our affiliate", "% commission")
_FORM = ("fill out this form", "fill in this form", "fill out the form", "complete this form", "via the lobby",
         "apply here", "application form", "dm our", "direct message")
_REDIRECT = ("please contact", "reach out to", "send your proposal", "please send", "forward your proposal",
             "email our", "write a letter to", "please email", "contact our", "send the details over",
             "is no longer with", "no longer with the business", "no longer with the company")
_MEETING = ("a call", "hop on", "jump on", "zoom", "demo", "meet", "chat sometime", "quick chat", "schedule")
_INTERESTED = (
    "interested", "sounds interesting", "sounds great", "love to", "would like to", "yes", "sure", "let's",
    "worth a look", "send me", "send over", "next steps", "how does it work", "how it works", "more details",
    "more info", "tell me more", "learn more", "we want", "happy to", "fit profiles",
)
_QUESTION_KEYS = {
    "pricing": ("price", "pricing", "cost", "fee", "how much", "budget", "pay"),
    "countries": ("country", "countries", "us-based", "uk-based", "united states", "singapore", "australia", "located"),
    "niche": ("niche", "in the space", "specifically", "aligned with", "genuine interest"),
    "demographics": ("demographic", "aged", "age ", "40+", "audience"),
    "portfolio": ("portfolio", "media kit", "deck", "examples", "case stud", "previous collaborations"),
    "vetting": ("vetting", "vetted", "selection process", "approve each"),
    "usage_rights": ("usage rights", "rights", "license", "licence"),
}


def _has_any(text: str, keys) -> bool:
    return any(re.search(k, text) if any(c in k for c in ".*") else k in text for k in keys)


def detect_questions(text: str) -> List[str]:
    low = (text or "").lower()
    return [q for q, keys in _QUESTION_KEYS.items() if any(k in low for k in keys)]


def strip_quoted(body: str) -> str:
    """Their words only: drop the quoted thread and signatures we can spot."""
    lines = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith(">"):
            continue
        if re.match(r"^(on .{0,120}wrote:|le .{0,80}a écrit|-----original message|from: .*newcollab|"
                     r"sent from my|发件人|差出人|_{6,})", s, re.I):
            break
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def redirect_target(text: str, from_addr: str) -> str:
    sender = (from_addr or "").lower()
    for found in _EMAIL_RE.findall(text or ""):
        e = found.lower().strip(".")
        if e == sender or e.endswith("@" + OUR_DOMAIN) or e.startswith(("noreply", "no-reply", "support@", "help@")):
            continue
        return e
    return ""


def classify_rules(subject: str, body: str, from_addr: str) -> Dict[str, Any]:
    subj = (subject or "").lower()
    sender = (from_addr or "").lower()
    text = strip_quoted(body)
    low = f"{subj}\n{text.lower()}"
    out: Dict[str, Any] = {"confidence": 0.6, "summary": text[:160], "questions": detect_questions(text)}

    if sender.startswith(("mailer-daemon@", "postmaster@", "mail-daemon@")) or _has_any(
        subj, ("undeliverable", "delivery status notification", "failure notice", "returned mail", "delivery failure")
    ):
        return {**out, "intent": "bounce", "confidence": 0.95}
    if sender.endswith("@google.com") and ("calendar" in sender or _has_any(subj, ("appointment", "booked", "invitation"))):
        return {**out, "intent": "booking", "confidence": 0.9}
    target = redirect_target(text, sender)
    if _has_any(low, _UNSUB):
        return {**out, "intent": "unsubscribe", "confidence": 0.85}
    if target and _has_any(low, _REDIRECT):
        return {**out, "intent": "redirect", "redirect_email": target, "confidence": 0.8}
    if _has_any(low, _AUTO_PATTERNS):
        return {**out, "intent": "auto_reply", "confidence": 0.85}
    if _has_any(low, _AFFILIATE) and not _has_any(low, ("interested", "sounds")):
        return {**out, "intent": "affiliate", "confidence": 0.75}
    if _has_any(low, _NOT_INTERESTED):
        return {**out, "intent": "not_interested", "confidence": 0.75}
    if _has_any(low, _LATER):
        return {**out, "intent": "later", "confidence": 0.8}
    if _has_any(low, _FORM):
        return {**out, "intent": "redirect", "form_url": (_URL_RE.findall(text) or [""])[0], "confidence": 0.7}
    if _has_any(low, _FORWARDED):
        return {**out, "intent": "forwarded", "confidence": 0.8}
    if target:
        return {**out, "intent": "redirect", "redirect_email": target, "confidence": 0.65}
    if _has_any(low, _MEETING):
        return {**out, "intent": "meeting", "confidence": 0.7}
    if _has_any(low, _INTERESTED):
        return {**out, "intent": "question" if "?" in text else "interested", "confidence": 0.72}
    return {**out, "intent": "other", "confidence": 0.4}


_CLASSIFY_SYS = """You triage replies to a cold email from Newcollab (creator gifting + UGC platform) to a brand.
Return JSON only:
{"intent":"interested|question|meeting|redirect|affiliate|later|forwarded|not_interested|unsubscribe|auto_reply|other",
 "confidence":0.0-1.0, "summary":"<=20 words, what they said",
 "questions":["pricing","countries","niche","demographics","portfolio","vetting","usage_rights","process","analytics"],
 "redirect_email":"", "redirect_name":"", "form_url":"",
 "flags":["complaint","legal","negotiation","partnership_offer","asks_for_paid_creators","angry","wants_human"]}
Rules:
- interested: positive, wants details or next steps. question: positive but asks something specific. meeting: asks for a call/demo/deck chat.
- redirect: tells us to email another person/address or fill a form. Put the exact address from the text in redirect_email.
- affiliate: invites us to their affiliate program instead.
- later: not now / not yet / focused elsewhere / maybe in the future. Polite postponement is later, not interested.
- forwarded: support or a generic inbox says they passed it to another team who will reach out if interested.
  Thanking us and passing it along is NOT interest.
- other: anything unusual (they pitch us, complaint, legal, they want to negotiate, unclear). When unsure, use other.
- Only list questions they actually asked. Only add flags that clearly apply."""


def _gemini_json(system: str, user: str, max_tokens: int = 500) -> Dict[str, Any]:
    try:
        from services.polly import get_gemini_key, get_polly_model, llm_disabled
    except Exception:
        return {}
    if llm_disabled() or not get_gemini_key():
        return {}
    try:
        import requests

        model = get_polly_model() or "gemini-2.5-flash"
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens,
                                     "responseMimeType": "application/json"},
            },
            headers={"Content-Type": "application/json", "x-goog-api-key": get_gemini_key()},
            timeout=15,
        )
        if resp.status_code >= 400:
            print(f"[brand_pipeline] Gemini HTTP {resp.status_code}")
            return {}
        data = resp.json()
        try:
            from services.gemini_usage import record_gemini_usage

            record_gemini_usage("brand_pipeline", model, data)
        except Exception:
            pass
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip() or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        print(f"[brand_pipeline] Gemini failed: {exc}")
        return {}


def classify(subject: str, body: str, from_addr: str, llm: Optional[Callable] = None) -> Dict[str, Any]:
    rules = classify_rules(subject, body, from_addr)
    rules["rules_intent"] = rules["intent"]
    if rules["intent"] in ("bounce", "booking", "auto_reply", "unsubscribe", "later", "forwarded"):
        return {**rules, "source": "rules"}
    ask = llm or _gemini_json
    text = strip_quoted(body)[:3000]
    got = ask(_CLASSIFY_SYS, json.dumps({"subject": subject, "from": from_addr, "reply": text}), 400)
    intent = str((got or {}).get("intent") or "").strip().lower()
    if intent not in POSITIVE_INTENTS + ("redirect", "affiliate", "later", "forwarded", "not_interested",
                                         "unsubscribe", "auto_reply", "other"):
        return {**rules, "source": "rules"}
    try:
        confidence = max(0.0, min(1.0, float(got.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.5
    target = str(got.get("redirect_email") or "").strip().lower()
    if target and target not in text.lower():
        target = ""
    return {
        "intent": intent,
        "confidence": confidence,
        "summary": str(got.get("summary") or rules["summary"])[:200],
        "questions": [q for q in (got.get("questions") or []) if isinstance(q, str)
                      and (q in rules["questions"] or q not in QUESTION_LINES)][:6] or rules["questions"],
        "rules_intent": rules["intent"],
        "redirect_email": target or rules.get("redirect_email", ""),
        "redirect_name": str(got.get("redirect_name") or "")[:80],
        "form_url": str(got.get("form_url") or rules.get("form_url") or "")[:300],
        "flags": [f for f in (got.get("flags") or []) if isinstance(f, str)][:5],
        "source": "llm",
    }


_COMPOSE_SYS = """You are Mazza, founder of Newcollab, replying to a brand that answered your cold email.
Write the reply body as plain text. JSON only: {"body":"..."}
Rules:
- Max 120 words. Warm, direct, no fluff, no markdown, no subject line.
- Greeting: "Hi {first}," (or "Hi there," if no name). Sign off exactly: "Mazza\\nFounder, Newcollab".
- Answer each of their questions using ONLY the FACTS. If the FACTS don't cover it (specific creator ages,
  medical niches, analytics, case studies), say the link shows matching creators so they can check, and offer the call.
- Never invent creator names, handles, follower counts, results, clients or prices.
- Always give the brief link as the next step. Add the booking link if they asked for a call, demo, deck or more detail.
- Reply in the language they wrote in."""

FACTS = """- Newcollab connects brands with 1,800+ small creators (mostly under 10K followers), strongest in beauty, skincare, wellness and fashion.
- How it works: the brand posts a 2-minute brief, sees matched creators with real profiles (niche, engagement, past content, country) and approves who they want. Polly, our matching assistant, pre-selects the 5 best fits.
- The brand ships product. Each creator posts organically and delivers UGC (photo or video) the brand can reuse and run as paid ads.
- The test: no platform fee, just product + shipping. For scaling or ongoing pricing, offer a quick call.
- Shipping regions are chosen in the brief; the brand only sees creators who match.
- All posts and UGC are tracked on one private roster page, no login needed.
- Creators who perform can be kept as long-term ambassadors."""


def _safe_reply(body: str, their_text: str, link: str) -> bool:
    if not body or len(body) > 1400 or link not in body:
        return False
    if _HANDLE_RE.search(body):
        return False
    allowed = {"1,800", "1800", "5", "10", "10K", "15", "2", "1", "3"}
    theirs = set(_NUMBER_RE.findall(their_text or ""))
    for n in _NUMBER_RE.findall(body.replace(link, "").replace(BOOKING_URL, "")):
        n = n.strip(".,")
        if n and n not in allowed and n not in theirs:
            return False
    return True


def compose_reply(row: Dict[str, Any], their_text: str, cls: Dict[str, Any], link: str,
                  llm: Optional[Callable] = None) -> Tuple[str, str]:
    """Returns (body, source). Falls back to a fixed reply when the model is off or unsafe."""
    brand = display_name(row.get("brand_name"), row.get("website"))
    first = first_name(row.get("contact_name"))
    wants_call = cls.get("intent") == "meeting" or bool({"process", "analytics"} & set(cls.get("questions") or []))
    ask = llm or _gemini_json
    got = ask(
        _COMPOSE_SYS.replace("{first}", first or "there"),
        json.dumps({
            "brand": brand,
            "their_reply": their_text[:2500],
            "their_questions": cls.get("questions") or [],
            "FACTS": FACTS,
            "brief_link": link,
            "booking_link": BOOKING_URL,
        }),
        500,
    )
    body = str((got or {}).get("body") or "").strip()
    if _safe_reply(body, their_text, link):
        return body, "llm"
    return fallback_reply(first, brand, link, cls.get("questions") or [], wants_call), "template"


def decide(cls: Dict[str, Any], row: Dict[str, Any]) -> str:
    """auto = send now, hold = Gmail draft + ping Mazza, log = state change only."""
    intent = cls.get("intent")
    if intent in LOG_ONLY_INTENTS:
        return "log"
    if (row.get("stage") or "") in ("meeting", "won") or row.get("pipeline") != PIPELINE:
        return "hold"
    if intent == "redirect":
        return "auto" if cls.get("redirect_email") and cls.get("confidence", 0) >= 0.6 else "hold"
    if intent in POSITIVE_INTENTS:
        if cls.get("flags") or int(row.get("auto_reply_count") or 0) > 0:
            return "hold"
        if cls.get("rules_intent", intent) not in POSITIVE_INTENTS:
            return "hold"
        return "auto" if float(cls.get("confidence") or 0) >= AUTO_CONFIDENCE else "hold"
    return "hold"


# --------------------------------------------------------------------------- DB helpers


def log_activity(cursor, brand_id: int, kind: str, **fields) -> None:
    cursor.execute(
        """
        INSERT INTO brand_activity
            (brand_id, kind, actor, stage_from, stage_to, intent, variant, email, subject, body,
             message_id, in_reply_to, meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            brand_id, kind, fields.get("actor") or "agent", fields.get("stage_from"), fields.get("stage_to"),
            fields.get("intent"), fields.get("variant"), fields.get("email"), fields.get("subject"),
            (fields.get("body") or "")[:4000] or None, fields.get("message_id"), fields.get("in_reply_to"),
            json.dumps(fields.get("meta") or {}, default=str),
        ),
    )


def ensure_tracking(cursor, brand_id: int) -> None:
    cursor.execute(
        """
        INSERT INTO brand_outreach_tracking (brand_id, outreach_count, pipeline, stage)
        VALUES (%s, 0, %s, 'new')
        ON CONFLICT (brand_id) DO NOTHING
        """,
        (brand_id, PIPELINE),
    )


def load_row(cursor, brand_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT b.id AS brand_id, b.brand_name, b.website, b.category, b.contact_email AS brand_email,
               b.email_status, bo.pipeline, bo.stage, bo.sequence_step, bo.variant, bo.next_action,
               bo.next_action_at, bo.contact_email, bo.contact_name, bo.contact_role, bo.ref_code,
               bo.thread_subject, bo.thread_message_id, bo.last_inbound_message_id, bo.last_inbound_subject,
               bo.reply_count, bo.auto_reply_count, bo.last_contacted_at, bo.outreach_count
        FROM pr_brands b
        LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
        WHERE b.id = %s
        """,
        (brand_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _set_stage(cursor, row: Dict[str, Any], stage: str, reason: str = "") -> None:
    before = row.get("stage") or "new"
    if before == stage or before == "won":
        return
    if before == "meeting" and stage in ("interested", "replied", "contacted"):
        return
    cursor.execute("UPDATE brand_outreach_tracking SET stage = %s WHERE brand_id = %s", (stage, row["brand_id"]))
    log_activity(cursor, row["brand_id"], "stage_change", stage_from=before, stage_to=stage,
                 meta={"reason": reason} if reason else None)
    row["stage"] = stage


def skip_brand(cursor, brand_id: int, reason: str = "", already_contacted: bool = False) -> None:
    """Take a brand out of the new queue. Brands already emailed outside the pipeline become legacy,
    so their replies are held for a human instead of auto-answered."""
    ensure_tracking(cursor, brand_id)
    row = load_row(cursor, brand_id)
    if already_contacted:
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET pipeline = CASE WHEN last_contacted_at IS NULL THEN 'legacy' ELSE COALESCE(pipeline, 'legacy') END
            WHERE brand_id = %s
            """,
            (brand_id,),
        )
        _set_stage(cursor, row, "contacted", reason or "already contacted outside the pipeline")
        return
    cursor.execute("UPDATE brand_outreach_tracking SET lost_reason = %s WHERE brand_id = %s",
                   ((reason or "no contact")[:300], brand_id))
    _set_stage(cursor, row, "bad_contact", reason or "no contact")


def pick_variant(cursor, rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    cursor.execute(
        """
        SELECT variant, COUNT(*) AS sent,
               COUNT(*) FILTER (WHERE stage IN ('interested', 'meeting', 'won')
                                   OR intent IN ('interested', 'question', 'meeting')) AS positive
        FROM brand_outreach_tracking
        WHERE pipeline = %s AND variant IS NOT NULL AND last_contacted_at IS NOT NULL
        GROUP BY variant
        """,
        (PIPELINE,),
    )
    stats = {v: {"sent": 0, "positive": 0} for v in VARIANTS}
    for r in cursor.fetchall():
        if r["variant"] in stats:
            stats[r["variant"]] = {"sent": int(r["sent"]), "positive": int(r["positive"])}
    if any(s["sent"] < MIN_TEST_PER_VARIANT for s in stats.values()):
        return min(VARIANTS, key=lambda v: (stats[v]["sent"], VARIANTS.index(v)))
    best = max(VARIANTS, key=lambda v: (stats[v]["positive"] + 1) / (stats[v]["sent"] + 2))
    return best if rng.random() < 0.7 else rng.choice([v for v in VARIANTS if v != best])


def queue_new(cursor, limit: int) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_QUEUE))
    cursor.execute(
        """
        SELECT LOWER(COALESCE(b.website, '')) AS website,
               LOWER(COALESCE(bo.contact_email, b.contact_email, '')) AS email
        FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
        WHERE bo.last_contacted_at IS NOT NULL
        UNION ALL
        SELECT LOWER(COALESCE(o.brand_website, '')), LOWER(COALESCE(o.brand_email, ''))
        FROM opportunities o
        """
    )
    taken = set()
    for r in cursor.fetchall():
        for d in (domain_of(r["website"]), domain_of(r["email"])):
            if d and d not in FREEMAIL:
                taken.add(d)
    cursor.execute(
        """
        SELECT b.id AS brand_id, b.brand_name, b.website, b.contact_email, b.email_status, b.category, b.source
        FROM pr_brands b
        LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
        WHERE bo.last_contacted_at IS NULL
          AND COALESCE(bo.stage, 'new') = 'new'
          AND COALESCE(TRIM(b.website), '') <> ''
          AND COALESCE(b.email_status, '') <> 'invalid'
          AND COALESCE(LOWER(b.category), 'other') <> ALL(%s)
          AND NOT EXISTS (SELECT 1 FROM opportunities o WHERE o.pr_brand_id = b.id)
        ORDER BY
          (CASE COALESCE(b.email_status, '') WHEN 'valid' THEN 3 WHEN 'catch-all' THEN 2 ELSE 1 END
           + CASE b.source WHEN 'meta_ads_library' THEN 3 WHEN 'curated' THEN 1 ELSE 0 END
           + CASE WHEN LOWER(COALESCE(b.category, '')) = ANY(%s) THEN 2 ELSE 0 END) DESC,
          b.id DESC
        LIMIT %s
        """,
        (list(SKIP_CATEGORIES), list(CORE_CATEGORIES), limit * 5),
    )
    out, seen = [], set()
    for r in cursor.fetchall():
        dom = domain_of(r["website"])
        name = display_name(r["brand_name"], r["website"])
        if not dom or not name or dom in taken or dom in seen:
            continue
        seen.add(dom)
        out.append({**dict(r), "brand_name": name, "domain": dom})
        if len(out) >= limit:
            break
    return out


def record_contact(cursor, brand_id: int, email: str, name: str = "", role: str = "",
                   source: str = "hunter", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        return {"ok": False, "error": "invalid email"}
    ensure_tracking(cursor, brand_id)
    cursor.execute(
        """
        UPDATE brand_outreach_tracking
        SET contact_email = %s, contact_name = NULLIF(%s, ''), contact_role = NULLIF(%s, ''),
            contact_source = %s, pipeline = COALESCE(pipeline, %s)
        WHERE brand_id = %s
        """,
        (email, (name or "")[:255], (role or "")[:255], (source or "hunter")[:30], PIPELINE, brand_id),
    )
    log_activity(cursor, brand_id, "contact_found", email=email,
                 meta={"name": name, "role": role, "source": source, **(meta or {})})
    return {"ok": True, "brand_id": brand_id, "email": email}


def _default_sender():
    from routes.admin_email import send_email_gmail_with_meta

    return send_email_gmail_with_meta


def _log_outbound(cursor, brand_id: int, to: str, subject: str, html_body: str, message_id: str) -> None:
    cursor.execute(
        """
        INSERT INTO brand_outreach_log (brand_id, email_sent_to, subject, html_content, status, sent_at, message_id)
        VALUES (%s, %s, %s, %s, 'sent', NOW(), %s)
        """,
        (brand_id, to, subject, html_body, message_id),
    )


def _deliver(cursor, row, to, subject, text, sender, in_reply_to=None, references=None):
    body_html = text_to_html(text)
    res = (sender or _default_sender())(
        to, subject, body_html, text_content=text, in_reply_to=in_reply_to,
        references=references, from_name=FROM_NAME,
    )
    if not res.get("success"):
        log_activity(cursor, row["brand_id"], "send_failed", email=to, subject=subject,
                     meta={"error": str(res.get("error"))[:300]})
        return None
    mid = res.get("message_id")
    _log_outbound(cursor, row["brand_id"], to, subject, body_html, mid)
    return mid


def send_step(cursor, brand_id: int, kind: str = "initial", dry_run: bool = False,
              sender: Optional[Callable] = None, rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """kind: initial | followup | nudge."""
    row = load_row(cursor, brand_id)
    if not row:
        return {"ok": False, "error": "brand not found"}
    stage = row.get("stage") or "new"
    to = (row.get("contact_email") or row.get("brand_email") or "").strip().lower()
    if not _EMAIL_RE.fullmatch(to):
        return {"ok": False, "error": "no contact email"}
    brand = display_name(row.get("brand_name"), row.get("website"))
    if not brand:
        return {"ok": False, "error": "no usable brand name"}
    first = first_name(row.get("contact_name"))
    ref = row.get("ref_code") or new_ref()
    link = brief_link(row.get("category"), ref)
    step = int(row.get("sequence_step") or 0)
    in_reply_to = None

    if kind == "initial":
        if row.get("last_contacted_at") or stage not in ("new",):
            return {"ok": False, "error": f"already in pipeline (stage={stage})"}
        variant = pick_variant(cursor, rng)
        subject, text = initial_email(variant, brand, first)
        step = 0
    elif kind == "followup":
        if row.get("pipeline") != PIPELINE or stage != "contacted" or row.get("next_action") != "followup":
            return {"ok": False, "error": f"not due for follow-up (stage={stage})"}
        step += 1
        if step > LAST_STEP:
            return {"ok": False, "error": "sequence finished"}
        variant = row.get("variant")
        subject = "Re: " + (row.get("thread_subject") or initial_email(variant or "swap", brand, first)[0])
        text = followup_email(step, brand, first, link)
        in_reply_to = row.get("thread_message_id")
    elif kind == "nudge":
        if stage != "interested" or row.get("next_action") != "nudge":
            return {"ok": False, "error": f"not due for nudge (stage={stage})"}
        variant = row.get("variant")
        last_subject = row.get("last_inbound_subject") or row.get("thread_subject") or brand
        subject = last_subject if last_subject.lower().startswith("re:") else f"Re: {last_subject}"
        text = nudge_email(brand, first, link)
        in_reply_to = row.get("last_inbound_message_id")
    else:
        return {"ok": False, "error": f"unknown kind {kind}"}

    preview = {"ok": True, "brand_id": brand_id, "brand_name": brand, "to": to, "kind": kind, "step": step,
               "variant": variant, "subject": subject, "text": text}
    if dry_run:
        return {**preview, "dry_run": True}

    mid = _deliver(cursor, row, to, subject, text, sender, in_reply_to=in_reply_to, references=in_reply_to)
    if not mid:
        return {"ok": False, "brand_id": brand_id, "error": "send failed"}

    ensure_tracking(cursor, brand_id)
    if kind == "initial":
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET outreach_count = COALESCE(outreach_count, 0) + 1, last_contacted_at = NOW(), last_subject = %s,
                pipeline = %s, sequence_step = 0, variant = %s, ref_code = %s, thread_subject = %s,
                thread_message_id = %s, next_action = 'followup',
                next_action_at = NOW() + (%s * INTERVAL '1 day')
            WHERE brand_id = %s
            """,
            (subject, PIPELINE, variant, ref, subject, mid, FOLLOWUP_GAP_DAYS[1], brand_id),
        )
        _set_stage(cursor, row, "contacted")
    elif kind == "followup":
        nxt = FOLLOWUP_GAP_DAYS.get(step + 1)
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET outreach_count = COALESCE(outreach_count, 0) + 1, last_contacted_at = NOW(), last_subject = %s,
                sequence_step = %s, ref_code = %s,
                next_action = CASE WHEN %s IS NULL THEN NULL ELSE 'followup' END,
                next_action_at = CASE WHEN %s IS NULL THEN NULL ELSE NOW() + (%s * INTERVAL '1 day') END
            WHERE brand_id = %s
            """,
            (subject, step, ref, nxt, nxt, nxt or 0, brand_id),
        )
        if step >= LAST_STEP:
            _set_stage(cursor, row, "nurture", "sequence finished without reply")
    else:
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET last_contacted_at = NOW(), last_subject = %s, ref_code = %s,
                next_action = 'mazza_review', next_action_at = NOW() + INTERVAL '4 days'
            WHERE brand_id = %s
            """,
            (subject, ref, brand_id),
        )
    log_activity(cursor, brand_id, "sent", variant=variant, email=to, subject=subject, body=text,
                 message_id=mid, in_reply_to=in_reply_to, meta={"kind": kind, "step": step})
    return {**preview, "message_id": mid}


def queue_followups(cursor, limit: int = 100) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT bo.brand_id, bo.next_action, bo.sequence_step
        FROM brand_outreach_tracking bo
        WHERE bo.pipeline = %s
          AND bo.next_action_at <= NOW()
          AND ((bo.next_action = 'followup' AND bo.stage = 'contacted')
               OR (bo.next_action = 'nudge' AND bo.stage = 'interested'))
        ORDER BY bo.next_action_at
        LIMIT %s
        """,
        (PIPELINE, max(1, min(int(limit), 200))),
    )
    return [
        {"brand_id": r["brand_id"], "kind": r["next_action"], "step": int(r["sequence_step"] or 0) + 1}
        for r in cursor.fetchall()
    ]


# --------------------------------------------------------------------------- inbound


def _ids(value) -> List[str]:
    if isinstance(value, (list, tuple)):
        raw = " ".join(str(v) for v in value)
    else:
        raw = str(value or "")
    found = re.findall(r"<([^>]+)>", raw) or raw.split()
    return [f.strip().strip("<>") for f in found if f.strip().strip("<>")]


def match_brand(cursor, from_addr: str, message_ids: List[str], body: str = "") -> Optional[int]:
    if message_ids:
        tokens = list({m.lower() for m in message_ids})
        cursor.execute(
            """
            SELECT brand_id FROM brand_outreach_log
            WHERE LOWER(TRIM(BOTH '<>' FROM COALESCE(message_id, ''))) = ANY(%s)
            ORDER BY sent_at DESC LIMIT 1
            """,
            (tokens,),
        )
        hit = cursor.fetchone()
        if hit:
            return int(hit["brand_id"])
    sender = (from_addr or "").lower()
    candidates = [sender] if sender else []
    if sender.endswith("@google.com"):
        candidates = [e.lower() for e in _EMAIL_RE.findall(body or "") if not e.lower().endswith("google.com")]
    for addr in candidates:
        cursor.execute(
            """
            SELECT brand_id FROM brand_outreach_tracking WHERE LOWER(contact_email) = %s
            UNION ALL
            SELECT brand_id FROM (
                SELECT brand_id FROM brand_outreach_log WHERE LOWER(TRIM(email_sent_to)) = %s
                ORDER BY sent_at DESC LIMIT 1
            ) l
            LIMIT 1
            """,
            (addr, addr),
        )
        hit = cursor.fetchone()
        if hit:
            return int(hit["brand_id"])
        dom = domain_of(addr)
        if dom and dom not in FREEMAIL and dom != OUR_DOMAIN:
            cursor.execute(
                """
                SELECT bo.brand_id FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
                WHERE bo.last_contacted_at IS NOT NULL
                  AND (LOWER(COALESCE(bo.contact_email, b.contact_email, '')) LIKE %s
                       OR LOWER(COALESCE(b.website, '')) ~ %s)
                ORDER BY bo.last_contacted_at DESC LIMIT 1
                """,
                (f"%@{dom}", rf"(^|[/.]){re.escape(dom)}(/|$)"),
            )
            hit = cursor.fetchone()
            if hit:
                return int(hit["brand_id"])
    return None


def ingest_message(cursor, msg: Dict[str, Any], sender: Optional[Callable] = None,
                   llm: Optional[Callable] = None, dry_run: bool = False) -> Dict[str, Any]:
    mid = (_ids(msg.get("message_id")) or [""])[0]
    from_addr = (msg.get("from_email") or "").strip().lower()
    subject = (msg.get("subject") or "").strip()
    body = msg.get("body") or ""
    if from_addr.endswith("@" + OUR_DOMAIN):
        return {"action": "skip", "reason": "own message", "message_id": mid}
    if mid:
        cursor.execute("SELECT brand_id FROM brand_activity WHERE kind = 'reply_in' AND message_id = %s LIMIT 1", (mid,))
        if cursor.fetchone():
            return {"action": "skip", "reason": "duplicate", "message_id": mid}

    refs = _ids(msg.get("in_reply_to")) + _ids(msg.get("references"))
    brand_id = match_brand(cursor, from_addr, refs, body)
    if not brand_id:
        return {"action": "unmatched", "from": from_addr, "subject": subject[:120], "message_id": mid}
    row = load_row(cursor, brand_id)
    ensure_tracking(cursor, brand_id)
    cls = classify(subject, body, from_addr, llm=llm)
    their_text = strip_quoted(body)
    intent = cls["intent"]
    action = decide(cls, row)
    brand = display_name(row.get("brand_name"), row.get("website"))
    result = {"action": action, "brand_id": brand_id, "brand_name": brand, "intent": intent,
              "confidence": cls.get("confidence"), "summary": cls.get("summary"), "message_id": mid}
    if dry_run:
        return {**result, "dry_run": True, "classification": cls}

    if intent != "booking":
        log_activity(cursor, brand_id, "reply_in", intent=intent, email=from_addr, subject=subject,
                     body=their_text, message_id=mid or None, in_reply_to=(refs or [None])[0],
                     meta={k: cls.get(k) for k in ("confidence", "questions", "flags", "source", "redirect_email", "form_url")})
    human = intent not in ("bounce", "auto_reply", "booking", "forwarded")
    if human:
        reply_refs = " ".join(f"<{r}>" for r in (refs + ([mid] if mid else []))[-10:])
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET reply_count = COALESCE(reply_count, 0) + 1, intent = %s, last_response_status = %s,
                last_response_at = NOW(), response_notes = %s, last_inbound_message_id = %s,
                last_inbound_subject = %s, next_action = NULL, next_action_at = NULL
            WHERE brand_id = %s
            """,
            (intent, LEGACY_STATUS.get(intent, "reply"), (cls.get("summary") or "")[:500],
             f"<{mid}>" if mid else None, subject[:300], brand_id),
        )
        result["references"] = reply_refs

    ref = row.get("ref_code") or new_ref()
    if not row.get("ref_code"):
        cursor.execute("UPDATE brand_outreach_tracking SET ref_code = %s WHERE brand_id = %s", (ref, brand_id))
    link = brief_link(row.get("category"), ref)
    first = first_name(row.get("contact_name"))

    if intent == "bounce":
        cursor.execute(
            """
            UPDATE brand_outreach_tracking SET last_response_status = 'wrong_email', last_response_at = NOW(),
                next_action = 'find_contact', next_action_at = NOW()
            WHERE brand_id = %s
            """,
            (brand_id,),
        )
        cursor.execute(
            """
            UPDATE pr_brands SET email_bounce_count = COALESCE(email_bounce_count, 0) + 1,
                email_last_bounced_at = NOW()
            WHERE id = %s
            """,
            (brand_id,),
        )
        _set_stage(cursor, row, "bad_contact", "bounce")
    elif intent == "auto_reply":
        if re.search(r"out of (the )?office|vacation|on leave|away", their_text, re.I) and row.get("next_action") == "followup":
            cursor.execute(
                "UPDATE brand_outreach_tracking SET next_action_at = GREATEST(next_action_at, NOW() + INTERVAL '5 days') WHERE brand_id = %s",
                (brand_id,),
            )
    elif intent == "booking":
        log_activity(cursor, brand_id, "meeting_booked", subject=subject, body=their_text[:800])
        _set_stage(cursor, row, "meeting", "booked via calendar link")
    elif intent == "unsubscribe":
        _set_stage(cursor, row, "do_not_contact", "asked to stop")
    elif intent == "not_interested":
        cursor.execute("UPDATE brand_outreach_tracking SET lost_reason = %s WHERE brand_id = %s",
                       ((cls.get("summary") or "not interested")[:300], brand_id))
        _set_stage(cursor, row, "lost", "not interested")
    elif intent == "affiliate":
        cursor.execute("UPDATE brand_outreach_tracking SET lost_reason = 'affiliate only' WHERE brand_id = %s", (brand_id,))
        _set_stage(cursor, row, "nurture", "affiliate program only")
    elif intent == "later":
        cursor.execute("UPDATE brand_outreach_tracking SET lost_reason = %s WHERE brand_id = %s",
                       (("not now: " + (cls.get("summary") or ""))[:300], brand_id))
        _set_stage(cursor, row, "nurture", "not right now")
    elif intent == "forwarded":
        cursor.execute(
            """
            UPDATE brand_outreach_tracking
            SET intent = 'forwarded', last_response_at = NOW(),
                next_action_at = GREATEST(COALESCE(next_action_at, NOW()), NOW() + (%s * INTERVAL '1 day'))
            WHERE brand_id = %s AND next_action = 'followup'
            """,
            (FORWARDED_WAIT_DAYS, brand_id),
        )
    elif intent == "redirect":
        _set_stage(cursor, row, "replied", "redirected")
        target = cls.get("redirect_email") or ""
        if action == "auto" and target:
            name = cls.get("redirect_name") or ""
            referrer = first_name(msg.get("from_name")) or "Your team"
            subject_new = row.get("thread_subject") or initial_email(row.get("variant") or "swap", brand, "")[0]
            text = redirect_email(brand, first_name(name), referrer, link)
            new_mid = _deliver(cursor, row, target, subject_new, text, sender)
            if new_mid:
                record_contact(cursor, brand_id, target, name, "", "redirect", {"from": from_addr})
                cursor.execute(
                    """
                    UPDATE brand_outreach_tracking
                    SET thread_message_id = %s, thread_subject = %s, sequence_step = 0, next_action = 'followup',
                        next_action_at = NOW() + (%s * INTERVAL '1 day'), last_contacted_at = NOW(),
                        outreach_count = COALESCE(outreach_count, 0) + 1
                    WHERE brand_id = %s
                    """,
                    (new_mid, subject_new, FOLLOWUP_GAP_DAYS[1], brand_id),
                )
                log_activity(cursor, brand_id, "sent", email=target, subject=subject_new, body=text,
                             message_id=new_mid, meta={"kind": "redirect_intro", "from": from_addr})
                row["stage"] = "replied"
                _set_stage(cursor, row, "contacted", f"new contact {target}")
                result.update({"sent_to": target, "subject": subject_new})
            else:
                result["action"] = "hold"
        if result["action"] == "hold":
            note = f"Redirect: {target or cls.get('form_url') or 'see reply'}"
            log_activity(cursor, brand_id, "draft_held", intent=intent, subject=subject, body=note)
            result["note"] = note
    else:
        _set_stage(cursor, row, "interested" if intent in POSITIVE_INTENTS else "replied", intent)
        text, source = compose_reply(row, their_text, cls, link, llm=llm)
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        to = from_addr
        if action == "auto":
            sent_mid = _deliver(cursor, row, to, reply_subject, text, sender,
                                in_reply_to=f"<{mid}>" if mid else None, references=result.get("references"))
            if sent_mid:
                cursor.execute(
                    """
                    UPDATE brand_outreach_tracking
                    SET auto_reply_count = COALESCE(auto_reply_count, 0) + 1, last_contacted_at = NOW(),
                        next_action = 'nudge', next_action_at = NOW() + (%s * INTERVAL '1 day')
                    WHERE brand_id = %s
                    """,
                    (NUDGE_DAYS, brand_id),
                )
                log_activity(cursor, brand_id, "auto_reply_sent", intent=intent, email=to, subject=reply_subject,
                             body=text, message_id=sent_mid, in_reply_to=mid or None, meta={"source": source})
                result.update({"sent_to": to, "subject": reply_subject, "text": text})
            else:
                result["action"] = action = "hold"
        if result["action"] == "hold":
            cursor.execute(
                "UPDATE brand_outreach_tracking SET next_action = 'mazza_review', next_action_at = NOW() WHERE brand_id = %s",
                (brand_id,),
            )
            log_activity(cursor, brand_id, "draft_held", intent=intent, email=to, subject=reply_subject, body=text,
                         in_reply_to=mid or None, meta={"source": source, "flags": cls.get("flags")})
            result["draft"] = {"to": to, "subject": reply_subject, "text": text,
                               "in_reply_to": f"<{mid}>" if mid else "", "references": result.get("references", "")}
    return result


# --------------------------------------------------------------------------- closing


def record_brief(cursor, opportunity_id: int, ref: str = "", brand_email: str = "", website: str = "",
                 pr_brand_id: Optional[int] = None) -> Optional[int]:
    """Mark the brand won when it publishes its first brief. Returns the brand_id or None."""
    brand_id = None
    if ref:
        cursor.execute("SELECT brand_id FROM brand_outreach_tracking WHERE ref_code = %s", (ref.strip(),))
        hit = cursor.fetchone()
        brand_id = int(hit["brand_id"]) if hit else None
    if not brand_id and pr_brand_id:
        cursor.execute(
            "SELECT brand_id FROM brand_outreach_tracking WHERE brand_id = %s AND last_contacted_at IS NOT NULL",
            (pr_brand_id,),
        )
        hit = cursor.fetchone()
        brand_id = int(hit["brand_id"]) if hit else None
    if not brand_id:
        for dom in {domain_of(brand_email), domain_of(website)}:
            if not dom or dom in FREEMAIL:
                continue
            cursor.execute(
                """
                SELECT bo.brand_id FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
                WHERE bo.last_contacted_at IS NOT NULL
                  AND (LOWER(COALESCE(bo.contact_email, b.contact_email, '')) LIKE %s
                       OR LOWER(COALESCE(b.website, '')) ~ %s)
                ORDER BY bo.last_contacted_at DESC LIMIT 1
                """,
                (f"%@{dom}", rf"(^|[/.]){re.escape(dom)}(/|$)"),
            )
            hit = cursor.fetchone()
            if hit:
                brand_id = int(hit["brand_id"])
                break
    if not brand_id:
        return None
    row = load_row(cursor, brand_id)
    if (row.get("stage") or "") == "won":
        return brand_id
    cursor.execute(
        """
        UPDATE brand_outreach_tracking
        SET won_at = NOW(), opportunity_id = %s, next_action = NULL, next_action_at = NULL,
            last_response_status = 'signed_up'
        WHERE brand_id = %s
        """,
        (opportunity_id, brand_id),
    )
    log_activity(cursor, brand_id, "won", meta={"opportunity_id": opportunity_id, "via": "ref" if ref else "match"})
    _set_stage(cursor, row, "won", "first brief published")
    return brand_id


def brief_prefill(cursor, ref: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT b.id AS brand_id, b.brand_name, b.website, b.category
        FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
        WHERE bo.ref_code = %s
        """,
        ((ref or "").strip()[:24],),
    )
    row = cursor.fetchone()
    if not row:
        return None
    cursor.execute(
        """
        SELECT 1 FROM brand_activity
        WHERE brand_id = %s AND kind = 'brief_opened' AND created_at > NOW() - INTERVAL '1 day' LIMIT 1
        """,
        (row["brand_id"],),
    )
    if not cursor.fetchone():
        log_activity(cursor, row["brand_id"], "brief_opened", actor="brand")
    return {
        "brand_name": display_name(row["brand_name"], row["website"]),
        "brand_website": row["website"] or "",
        "niche": niche_for(row["category"]),
    }


# --------------------------------------------------------------------------- report


def report(cursor, hours: int = 24) -> Dict[str, Any]:
    hours = max(1, min(int(hours), 24 * 31))
    cursor.execute(
        "SELECT COALESCE(stage, 'new') AS stage, COUNT(*) AS n FROM brand_outreach_tracking WHERE pipeline = %s GROUP BY 1",
        (PIPELINE,),
    )
    funnel = {r["stage"]: int(r["n"]) for r in cursor.fetchall()}
    cursor.execute(
        """
        SELECT a.kind, COALESCE(a.meta->>'kind', '') AS sub, COALESCE(a.intent, '') AS intent, COUNT(*) AS n
        FROM brand_activity a
        WHERE a.created_at > NOW() - (%s * INTERVAL '1 hour')
        GROUP BY 1, 2, 3
        """,
        (hours,),
    )
    window: Dict[str, int] = {}
    for r in cursor.fetchall():
        key = r["kind"] + (f":{r['sub']}" if r["sub"] else "") + (f":{r['intent']}" if r["intent"] else "")
        window[key] = window.get(key, 0) + int(r["n"])
    cursor.execute(
        """
        SELECT variant, COUNT(*) AS sent,
               COUNT(*) FILTER (WHERE reply_count > 0 AND COALESCE(intent, '') NOT IN ('auto_reply', 'bounce')) AS replied,
               COUNT(*) FILTER (WHERE stage IN ('interested', 'meeting', 'won')
                                   OR intent IN ('interested', 'question', 'meeting')) AS positive,
               COUNT(*) FILTER (WHERE stage = 'won') AS won,
               COUNT(*) FILTER (WHERE stage = 'bad_contact') AS bounced
        FROM brand_outreach_tracking
        WHERE pipeline = %s AND variant IS NOT NULL AND last_contacted_at IS NOT NULL
        GROUP BY variant ORDER BY variant
        """,
        (PIPELINE,),
    )
    variants = [dict(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COUNT(*) FILTER (WHERE kind = 'sent') AS sent,
               COUNT(*) FILTER (WHERE kind = 'reply_in' AND intent = 'bounce') AS bounces
        FROM brand_activity WHERE created_at > NOW() - INTERVAL '7 days'
        """
    )
    wk = cursor.fetchone() or {}
    sent7, bounces7 = int(wk.get("sent") or 0), int(wk.get("bounces") or 0)
    cursor.execute(
        """
        SELECT b.brand_name, b.website, bo.stage, bo.intent, bo.next_action, bo.response_notes, bo.last_response_at
        FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
        WHERE bo.pipeline = %s AND bo.stage IN ('interested', 'meeting')
        ORDER BY bo.last_response_at DESC NULLS LAST LIMIT 15
        """,
        (PIPELINE,),
    )
    hot = [dict(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT b.brand_name, a.intent, a.subject, LEFT(COALESCE(a.body, ''), 160) AS preview, a.created_at
        FROM brand_activity a JOIN pr_brands b ON b.id = a.brand_id
        WHERE a.kind = 'draft_held' AND a.created_at > NOW() - (%s * INTERVAL '1 hour')
        ORDER BY a.created_at DESC LIMIT 15
        """,
        (hours,),
    )
    held = [dict(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT b.brand_name, bo.opportunity_id, bo.won_at
        FROM brand_outreach_tracking bo JOIN pr_brands b ON b.id = bo.brand_id
        WHERE bo.won_at > NOW() - (%s * INTERVAL '1 hour') ORDER BY bo.won_at DESC
        """,
        (hours,),
    )
    wins = [dict(r) for r in cursor.fetchall()]
    return {
        "hours": hours,
        "funnel": funnel,
        "window": window,
        "variants": variants,
        "sent_7d": sent7,
        "bounces_7d": bounces7,
        "bounce_rate_7d": round(bounces7 / sent7, 4) if sent7 else 0.0,
        "hot": hot,
        "held": held,
        "wins": wins,
    }


def daily_new_limit(rep: Dict[str, Any], base: int = 40) -> int:
    """Ramp: drop to 20 on bounces above 5%, grow to 50 when bounces stay low and replies come in."""
    if rep.get("sent_7d", 0) >= 50 and rep.get("bounce_rate_7d", 0) > 0.05:
        return 20
    positive = sum(int(v.get("positive") or 0) for v in rep.get("variants") or [])
    if rep.get("sent_7d", 0) >= 150 and rep.get("bounce_rate_7d", 0) < 0.03 and positive >= 3:
        return MAX_NEW_PER_RUN
    return base
