"""Creator-requested edits to the pitch on screen.

Every edit either returns a new pitch version whose text really differs, or says why it
couldn't be made. Polly may only tell a creator the pitch changed when this returned one.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.polly_prefs import get_prefs

_ENG = r"(?:\bengagement\b|\beng\.?\s*rate\b|\bER\b)"
_NO_ENGAGEMENT_RE = re.compile(
    r"(?i)(?:\b(?:remove|delete|drop|cut|hide|without|leave out|get rid of|take)\b"
    r"|\b(?:don'?t|dont|do not|not|never|stop)\b[^.!?\n]{0,30}\b(?:shar|mention|includ|show|put)\w*)"
    rf"[^.!?\n]{{0,25}}{_ENG}"
    rf"|{_ENG}[^.!?\n]{{0,25}}\b(?:out|off|removed|gone)\b"
    rf"|\bno\s+{_ENG}"
)
_ENGAGEMENT_CLAUSE_RE = re.compile(
    r"(?i),?\s*(?:with|at|and|plus|averaging|,|where\s+(?:my\s+)?(?:posts|videos|content)\s+averages?(?:\s+around)?)"
    r"\s+(?:an?\s+)?(?:~|about\s+|around\s+)?(?:\d+(?:[.,]\d+)?\s*%\s+)?(?:average\s+|avg\.?\s+)?"
    r"engagement(?:\s+rate)?(?:\s+(?:of|at)\s+(?:~|about\s+|around\s+)?\d+(?:[.,]\d+)?\s*%)?"
)
_ENGAGEMENT_SENTENCE_RE = re.compile(r"(?i)\bengagement\b|\b\d+(?:[.,]\d+)?\s*%\s*(?:ER|eng\.?)\b")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*\s*[kKmM%]?")


def wants_no_engagement(instruction: str) -> bool:
    return bool(_NO_ENGAGEMENT_RE.search(instruction or ""))


def strip_engagement(body: str) -> str:
    """Body with every engagement figure and mention gone, rest untouched."""
    text = _ENGAGEMENT_CLAUSE_RE.sub("", body or "")
    lines = []
    for line in text.split("\n"):
        if not _ENGAGEMENT_SENTENCE_RE.search(line):
            lines.append(line)
            continue
        kept = [s for s in _SENTENCE_RE.split(line) if not _ENGAGEMENT_SENTENCE_RE.search(s)]
        if kept:
            lines.append(" ".join(kept))
    out = "\n".join(lines)
    out = re.sub(r"[ \t]+([,.!?])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def apply_pitch_prefs(pitch: Optional[Dict[str, Any]], notes: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Standing pitch preferences, applied to every pitch Polly or Autopilot writes."""
    if not pitch or get_prefs(notes).get("show_engagement_rate") is not False:
        return pitch
    out = dict(pitch)
    out["body"] = strip_engagement(str(out.get("body") or ""))
    return _with_mailto(out)


def _with_mailto(pitch: Dict[str, Any]) -> Dict[str, Any]:
    if pitch.get("email"):
        from services.polly import build_mailto
        pitch["mailto"] = build_mailto(pitch["email"], pitch.get("subject") or "", pitch.get("body") or "")
    return pitch


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _numbers(text: str) -> set:
    return {re.sub(r"[\s,]", "", m.group(0)).lower() for m in _NUMBER_RE.finditer(text or "")}


_EDIT_RULES = """You edit one email pitch a creator will send to a brand. Apply ONLY the creator's
instruction; keep every other sentence as it is. Rules:
- Never invent numbers, brands, products, handles, links or past collabs. A number may appear only if it
  is already in the pitch or the creator gave it in the instruction.
- Keep [CITY, COUNTRY] exactly if it is there. Keep the greeting and the sign-off name.
- Plain text, no markdown. Same language as the pitch.
Return JSON only: {"subject": "...", "body": "...", "changed": true, "why_not": ""}.
If the instruction needs a fact you don't have, or doesn't apply to this text, return the pitch unchanged
with "changed": false and a one-line "why_not" written to the creator."""


def _llm_edit(pitch: Dict[str, Any], instruction: str) -> Dict[str, Any]:
    from services.polly_brain import BrainUnavailable, _Gemini, _parts, _text
    payload = {
        "systemInstruction": {"parts": [{"text": _EDIT_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps({
            "instruction": instruction,
            "subject": pitch.get("subject") or "",
            "body": pitch.get("body") or "",
        })}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1400,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        raw = _text(_parts(_Gemini().call(payload)))
    except BrainUnavailable:
        return {"changed": False, "why_not": "I couldn't reach my writing model just now. Try again in a minute."}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
    except ValueError:
        return {"changed": False, "why_not": "That edit didn't come out right. Tell me the exact line to change."}
    return data if isinstance(data, dict) else {"changed": False, "why_not": ""}


def revise_pitch(pitch: Dict[str, Any], instruction: str) -> Dict[str, Any]:
    """{"changed": bool, "pitch": new version | None, "why_not": str, "pref": prefs patch}."""
    old_subject = str(pitch.get("subject") or "")
    old_body = str(pitch.get("body") or "")
    pref: Dict[str, Any] = {}
    if wants_no_engagement(instruction):
        pref["show_engagement_rate"] = False
        new_subject, new_body = old_subject, strip_engagement(old_body)
        if _norm(new_body) == _norm(old_body):
            return {"changed": False, "pitch": None, "pref": pref,
                    "why_not": "This pitch doesn't mention engagement, so there was nothing to remove."}
    else:
        data = _llm_edit(pitch, instruction)
        new_subject = str(data.get("subject") or old_subject).strip()
        new_body = str(data.get("body") or old_body).strip()
        if data.get("changed") is False:
            return {"changed": False, "pitch": None, "pref": pref,
                    "why_not": str(data.get("why_not") or "").strip()}
        invented = _numbers(new_subject + " " + new_body) - _numbers(old_subject + " " + old_body + " " + instruction)
        if invented:
            return {"changed": False, "pitch": None, "pref": pref,
                    "why_not": "I'd have to make up a number to do that. Tell me the exact figure and I'll put it in."}
        if "[CITY, COUNTRY]" in old_body and "[CITY, COUNTRY]" not in new_body:
            return {"changed": False, "pitch": None, "pref": pref,
                    "why_not": "Tell me your city and country first, then I'll make that change."}
    if _norm(new_body) == _norm(old_body) and _norm(new_subject) == _norm(old_subject):
        return {"changed": False, "pitch": None, "pref": pref,
                "why_not": "I couldn't find that in the pitch. Tell me the exact line to change."}
    out = dict(pitch)
    out["subject"] = new_subject
    out["body"] = new_body
    out["version"] = int(pitch.get("version") or 1) + 1
    out["edited_at"] = datetime.now(timezone.utc).isoformat()
    return {"changed": True, "pitch": _with_mailto(out), "why_not": "", "pref": pref}
