"""Ask once when a creator's posts and their signup picks point to different niches.

For You used to settle the conflict silently (signup picks win), so a pet creator who ticked skincare saw only
skincare brands and never knew why. Now, the first time Polly lines up brands, she asks which to match on and
saves the answer as the creator's niches, the same record Settings writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CONFIDENT = 70  # scrape confidence needed before we trust it enough to ask
_LABELS = {"pet": "Pets", "pets": "Pets", "diy": "DIY", "ugc": "UGC", "food": "Food & drink"}
_WEAK = {"other", "unknown", "general", "misc", "miscellaneous", "n/a", "na", "none", "", "lifestyle"}


def label(niche: str) -> str:
    key = str(niche or "").strip().lower()
    return _LABELS.get(key) or key.replace("_", " ").title()


def _agrees(scraped: str, picked: List[str]) -> bool:
    try:
        from services.fit_score_calculator import scrape_agrees_with_intent
    except Exception:
        return scraped in picked
    return scrape_agrees_with_intent(scraped, picked)


def conflict(scrape: Optional[Dict[str, Any]], creator: Optional[Dict[str, Any]],
             notes: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"scraped", "picked"} when the posts clearly say one niche and the signup picks say another."""
    from services.polly_discovery import onboarding_niches, stated_niches

    notes = notes or {}
    if notes.get("niche_check") or stated_niches(notes):
        return None  # already asked, or the creator told Polly their niche
    scrape = scrape or {}
    scraped = str(scrape.get("primary_niche") or "").strip().lower()
    try:
        confidence = int(scrape.get("primary_niche_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if scraped in _WEAK or confidence < CONFIDENT:
        return None
    picked = [p for p in onboarding_niches(creator) if p and p != scraped][:3]
    if not picked or _agrees(scraped, picked):
        return None
    return {"scraped": scraped, "picked": picked}


def question(found: Dict[str, Any]) -> Dict[str, Any]:
    """What Polly says and the three answers, each saved in one tap."""
    scraped, picked = found["scraped"], found["picked"]
    picked_label = ", ".join(label(p) for p in picked)
    say = (f"Quick check before I match brands: your posts look like **{label(scraped)}** content, "
           f"but at signup you picked **{picked_label}**. Which should I find brands for?")
    chips = [
        {"id": "niche_scraped", "label": label(scraped), "action": "set_niche", "niche": scraped},
        {"id": "niche_picked", "label": picked_label, "action": "set_niche", "niche": ",".join(picked)},
        {"id": "niche_both", "label": "Both", "action": "set_niche", "niche": ",".join([scraped] + picked)},
    ]
    return {"say": say, "chips": chips}


def mark_asked(notes: Dict[str, Any], found: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(notes or {})
    out["niche_check"] = {"asked_at": datetime.now(timezone.utc).isoformat(), **found}
    return out


def mark_answered(notes: Dict[str, Any], niches: List[str]) -> Dict[str, Any]:
    out = dict(notes or {})
    check = dict(out.get("niche_check") or {})
    check.update({"answered": list(niches), "answered_at": datetime.now(timezone.utc).isoformat()})
    out["niche_check"] = check
    return out


def parse_choice(raw: Any) -> List[str]:
    return [p.strip().lower() for p in str(raw or "").split(",") if p.strip()]
