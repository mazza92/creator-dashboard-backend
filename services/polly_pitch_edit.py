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
_GENERAL_RE = re.compile(r"(?i)\b(?:always|from now on|going forward|in future|every pitch|all (?:my |future )?pitches)\b")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*(?:\s*[kKmM](?![a-zA-Z])|\s*%)?")


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


_HANDLE_RE = re.compile(r"(?<![\w.])@[\w.]{2,30}\w")
_SLOT_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}", re.I)
_SLOT_ALIASES = {"brand_name": "brand", "product_name": "product", "name": "first_name",
                 "creator": "first_name", "location": "city", "kit": "kit_url"}


def fill_template(template: str, slots: Dict[str, Any]) -> str:
    """Creator's own template, verbatim, with {{brand}} / {{product}} / ... filled."""
    def sub(m: "re.Match[str]") -> str:
        key = m.group(1).lower()
        key = _SLOT_ALIASES.get(key, key)
        val = str(slots.get(key) or "").strip()
        if val:
            return val
        return {"product": "your products", "city": "[CITY, COUNTRY]"}.get(key, m.group(0))
    return _SLOT_RE.sub(sub, template)


_PREF_RULES = """You adjust one email pitch a creator will send to a brand so it follows the creator's
standing pitch preferences. Change only what a preference requires. Every sentence no preference touches
stays word for word: the greeting, the hook about the brand, every platform, handle, number and link, the
sign-off. Write changes naturally, e.g. "I'd create one organic TikTok plus two UGC videos you're welcome
to use in your ads for 3 months." Rules:
- lead_metric: open the credentials line with that metric (followers, views or engagement), using only a
  figure already in the pitch or in facts. "none" means no audience numbers at all. If the figure isn't
  available, keep the pitch's existing numbers. Facts are exact: top_post_views are views on their best
  posts ("videos reaching up to 48K views"), never an average; only avg_views is an average.
- platforms: mention only these platforms and handles; drop the others.
- offer_type: gifted_post = product for an organic post; gifted_ugc = product for an organic post plus UGC
  videos the brand can use; gifted_ugc_photos = the same plus photos; paid_only = ask for a paid fee, no
  gifted ask.
- deliverables: state exactly these deliverables.
- usage_rights_months: offer usage rights for exactly that many months (0 = don't offer usage rights).
- tone: friendly = warm and conversational; professional = polished, no slang or emojis.
- Never invent numbers, brands, products, handles or past collabs. Keep [CITY, COUNTRY] if present.
- Plain text, no markdown, same language as the pitch.
Return JSON only: {"subject": "...", "body": "..."}."""


def _llm_apply_prefs(pitch: Dict[str, Any], prefs: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    from services.polly_brain import BrainUnavailable, _Gemini, _parts, _text
    payload = {
        "systemInstruction": {"parts": [{"text": _PREF_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps({
            "preferences": {k: prefs[k] for k in prefs if k != "custom_template"},
            "facts": facts,
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
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    except (BrainUnavailable, ValueError) as err:
        print(f"[Polly] pitch prefs rewrite skipped: {err}")
        return {}
    return data if isinstance(data, dict) else {}


def _rewrite_with_prefs(pitch: Dict[str, Any], prefs: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    data = _llm_apply_prefs(pitch, prefs, facts)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not body:
        return pitch
    old = str(pitch.get("subject") or "") + " " + str(pitch.get("body") or "")
    allowed = _numbers(old + " " + json.dumps(prefs) + " " + json.dumps(facts))
    if _numbers(subject + " " + body) - allowed:
        print("[Polly] pitch prefs rewrite dropped: invented a number")
        return pitch
    if "[CITY, COUNTRY]" in str(pitch.get("body") or "") and "[CITY, COUNTRY]" not in body:
        return pitch
    if not prefs.get("platforms"):
        lost = set(_HANDLE_RE.findall(str(pitch.get("body") or "").lower())) - set(_HANDLE_RE.findall(body.lower()))
        if lost:
            print("[Polly] pitch prefs rewrite dropped: lost a handle")
            return pitch
    return {**pitch, "subject": subject or pitch.get("subject"), "body": body}


def _first_number(*values: Any) -> Optional[int]:
    for val in values:
        try:
            n = int(float(str(val).replace(",", "")))
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def pitch_pref_inputs(conn, notes: Optional[Dict], brand_id: Any = None, *, creator: Optional[Dict] = None,
                      scrape: Optional[Dict] = None, kit: Optional[Dict] = None,
                      location: str = "") -> Dict[str, Dict[str, Any]]:
    """{"slots": template slots, "facts": real numbers the rewrite may use}. Cheap when no prefs."""
    prefs = get_prefs(notes)
    creator, scrape, kit = creator or {}, scrape or {}, kit or {}
    slots: Dict[str, Any] = {}
    facts: Dict[str, Any] = {}
    if prefs.get("custom_template"):
        name = str(creator.get("first_name") or creator.get("username") or scrape.get("full_name") or "").strip()
        slots = {
            "first_name": name.split(" ")[0] if name else "",
            "handle": creator.get("social_handle") or scrape.get("username") or "",
            "city": location,
            "kit_url": kit.get("url") or "",
        }
        if conn and brand_id and "product" in prefs["custom_template"].lower():
            try:
                cur = conn.cursor()
                cur.execute("SELECT hero_product FROM pr_brands WHERE id = %s", (int(brand_id),))
                row = cur.fetchone()
                slots["product"] = (row[0] if row and not isinstance(row, dict) else (row or {}).get("hero_product")) or ""
            except Exception as err:
                print(f"[Polly] template product lookup skipped: {err}")
                conn.rollback()
    if prefs.get("lead_metric") in ("followers", "views", "engagement"):
        followers = _first_number(scrape.get("followers"), scrape.get("follower_count"),
                                  scrape.get("tiktok_followers"), creator.get("follower_count"))
        if followers:
            facts["followers"] = followers
        views = sorted((p.get("views") for p in (kit.get("posts") or []) if _first_number(p.get("views"))),
                       key=lambda v: _first_number(v) or 0, reverse=True)
        if views:
            facts["top_post_views"] = [_first_number(v) for v in views[:3]]
        avg = _first_number(scrape.get("avg_views"), scrape.get("average_views"))
        if avg:
            facts["avg_views"] = avg
        eng = scrape.get("engagement_rate") or creator.get("engagement_rate")
        if eng and prefs.get("show_engagement_rate") is not False:
            facts["engagement_rate"] = eng
    return {"slots": slots, "facts": facts}


def apply_pitch_prefs(pitch: Optional[Dict[str, Any]], notes: Optional[Dict] = None, *,
                      slots: Optional[Dict[str, Any]] = None, facts: Optional[Dict[str, Any]] = None,
                      rewrite: bool = True) -> Optional[Dict[str, Any]]:
    """Standing pitch preferences, applied to every pitch Polly or Autopilot writes.

    rewrite=False (follow-ups, creator edits) only enforces the engagement rule."""
    if not pitch:
        return pitch
    from services.polly_prefs import needs_rewrite, pitch_prefs_line
    prefs = get_prefs(notes)
    out = dict(pitch)
    if rewrite and prefs.get("custom_template"):
        filled = fill_template(prefs["custom_template"],
                               {"brand": out.get("brand_name"), **(slots or {})})
        out["body"] = filled
        out["prefs_line"] = "Your template"
        return _with_mailto(out)
    if rewrite and needs_rewrite(prefs) and not out.get("prefs_applied"):
        out = _rewrite_with_prefs(out, prefs, facts or {})
        out["prefs_applied"] = True
    if prefs.get("show_engagement_rate") is False or prefs.get("lead_metric") == "none":
        out["body"] = strip_engagement(str(out.get("body") or ""))
    line = pitch_prefs_line(prefs)
    if line:
        out["prefs_line"] = line
    if out == pitch:
        return pitch
    return _with_mailto(out)


def _with_mailto(pitch: Dict[str, Any]) -> Dict[str, Any]:
    if pitch.get("email"):
        from services.polly import build_mailto
        pitch["mailto"] = build_mailto(pitch["email"], pitch.get("subject") or "", pitch.get("body") or "")
    return pitch


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _numbers(text: str) -> set:
    """Every figure as a comparable value: '12,400', '12.4K' and '12400' are the same number."""
    out = set()
    for m in _NUMBER_RE.finditer(text or ""):
        raw = re.sub(r"\s", "", m.group(0)).lower()
        if raw.endswith("%"):
            out.add(raw)
            continue
        mult = {"k": 1000, "m": 1_000_000}.get(raw[-1:], 1)
        digits = raw.rstrip("km")
        if "," in digits and re.fullmatch(r"\d{1,3}(?:,\d{3})+", digits):
            digits = digits.replace(",", "")
        try:
            out.add(str(int(round(float(digits.replace(",", ".")) * mult))))
        except ValueError:
            out.add(raw)
    return out


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
    if _GENERAL_RE.search(instruction or ""):
        from services.polly_prefs import parse_pitch_preferences
        pref.update(parse_pitch_preferences(instruction))
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
