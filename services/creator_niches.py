"""The creator's niche: one record, one vocabulary, one writer.

Source of truth is creators.creator_niches (TEXT[]), set at onboarding and in Account Settings.
creators.niche is the legacy JSON mirror and Polly's notes["niche"] is a cached copy; both are
rewritten here on every save so Polly, Autopilot, the radar and For You read the same niches.
Values are the Settings/onboarding ids (brand_categories canonical slugs), never 'makeup' etc.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from brand_categories import CANONICAL_CATEGORIES, normalize_category

NICHE_IDS = tuple(c for c in CANONICAL_CATEGORIES if c != "other")
MAX_NICHES = 3  # same cap as onboarding, Account Settings and Autopilot


def clean_niches(values: Optional[Iterable[Any]]) -> List[str]:
    """Any niche labels ('Makeup', 'Home & Living', 'beauty') -> Settings ids, in order, unknowns dropped."""
    if isinstance(values, str):
        values = [values]
    out: List[str] = []
    for raw in values or []:
        slug = normalize_category(raw)
        # Old signup labels paired two niches ('Beauty & Fashion'); 'Home & Living' is one alias.
        parts = [slug] if slug in NICHE_IDS else [normalize_category(p) for p in re.split(r"\s*(?:&|\band\b)\s*", str(raw))]
        for part in parts:
            if part in NICHE_IDS and part not in out:
                out.append(part)
    return out


def recorded_niches(creator: Optional[Dict[str, Any]]) -> List[str]:
    """The creator's niches as recorded: creator_niches, else the legacy niche column."""
    from services.polly_discovery import onboarding_niches

    return clean_niches(onboarding_niches(creator))


def with_recorded_niches(notes: Optional[Dict[str, Any]], creator: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Polly's notes with niche replaced by the record, so a stale chat copy never outranks Settings."""
    out = dict(notes or {})
    picked = recorded_niches(creator)
    if picked:
        out["niche"] = picked
    return out


def save_creator_niches(conn, creator_id: int, niches: Iterable[Any], commit: bool = True) -> List[str]:
    """Write the niches everywhere they live, top MAX_NICHES kept. Returns what was saved; empty writes nothing.
    Older records may hold more; they stay until the creator saves again."""
    cleaned = clean_niches(niches)[:MAX_NICHES]
    if not cleaned or not creator_id:
        return cleaned
    cur = conn.cursor()
    cur.execute("UPDATE creators SET creator_niches = %s, niche = %s WHERE id = %s",
                (cleaned, json.dumps(cleaned), creator_id))
    cur.execute("""UPDATE polly_threads SET notes = jsonb_set(notes, '{niche}', %s::jsonb)
                   WHERE creator_id = %s AND notes ? 'niche'""", (json.dumps(cleaned), creator_id))
    if commit:
        conn.commit()
    _forget_matches(creator_id)
    return cleaned


def _forget_matches(creator_id: int) -> None:
    try:
        from routes.polly import _FOR_YOU_CACHE, _FOR_YOU_LOCK
        from services.mentor_matchmaker import invalidate_mentor_matches

        with _FOR_YOU_LOCK:
            _FOR_YOU_CACHE.pop(int(creator_id), None)
        invalidate_mentor_matches(creator_id)
    except Exception as err:
        print(f"[niches] match cache reset skipped for {creator_id}: {err}")
