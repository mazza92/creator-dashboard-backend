"""
Mentor Matchmaker — sole For You brand ranking brain.

Source of truth for scoring: scraped social profile (same as unlock).
User dashboard/onboarding niches are INTERESTS only — soft preference for
ordering, never merged into fit scoring.

Flow:
1. SQL candidate pool (scrape lane + optional interest expansion)
2. Calculator unlock-parity prefilter
3. Gemini returns ordered brand IDs only (tiny JSON — avoids truncation)
4. Calculator score/status displayed on cards
5. Cache successful ranks (~1 hour)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import requests

from services.fit_score_calculator import (
    calculate_fit_score,
    SCORE_TIERS,
    PRIMARY_NICHE_ADJACENCY,
    _mapped_category,
)


def _gemini_api_key() -> Optional[str]:
    return os.getenv('GEMINI_API_KEY')


def _gemini_model() -> str:
    return os.getenv('MENTOR_MATCH_MODEL') or os.getenv('GEMINI_MODEL') or 'gemini-2.5-flash'


_MATCH_CACHE: Dict[int, Tuple[float, str, List[Dict]]] = {}
_CACHE_TTL_SEC = int(os.getenv('MENTOR_MATCH_CACHE_TTL', '3600'))

MATCHMAKER_SYSTEM = '''You are NewCollab's AI talent manager.
Rank pre-approved brand IDs for ONE creator using their scraped social profile.

Return ONLY: {"ranked_ids":[123,456,789]}
- Best fit first
- Return UP TO 8 IDs whenever the candidate list has enough — aim for 8
- Only IDs from the candidate list
- Prefer PRIMARY niche brands over bio buzzwords
- Never return only 1 ID if more candidates exist
'''

MATCH_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'ranked_ids': {
            'type': 'ARRAY',
            'items': {'type': 'INTEGER'},
        },
    },
    'required': ['ranked_ids'],
}


def _profile_fingerprint(profile: Dict, interest_niches: List[str]) -> str:
    raw = json.dumps({
        'primary': profile.get('primary_niche'),
        'secondary': profile.get('secondary_niches'),
        'themes': profile.get('content_themes'),
        'bio': (profile.get('raw_bio') or '')[:200],
        'followers': profile.get('follower_count'),
        'interests': interest_niches,
        'scraped_at': str(profile.get('scraped_at') or ''),
    }, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _safe_text(value, max_len: int = 160) -> str:
    if value is None:
        return ''
    text = str(value).replace('\n', ' ').replace('\r', ' ').replace('"', "'")
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len]


def _creator_summary(profile: Dict, interest_niches: List[str]) -> str:
    themes = profile.get('content_themes') or []
    if isinstance(themes, str):
        try:
            themes = json.loads(themes)
        except Exception:
            themes = [themes]
    secondary = profile.get('secondary_niches') or []
    if isinstance(secondary, str):
        try:
            secondary = json.loads(secondary)
        except Exception:
            secondary = [secondary]
    aesthetic = profile.get('aesthetic') or {}
    if isinstance(aesthetic, str):
        try:
            aesthetic = json.loads(aesthetic)
        except Exception:
            aesthetic = {}

    return f"""CREATOR SOCIAL PROFILE (scraped — scoring source of truth)
Handle: @{_safe_text(profile.get('handle') or 'creator', 40)}
Followers: {profile.get('follower_count') or 0}
Bio: {_safe_text(profile.get('raw_bio'), 220)}
Primary niche (AI scrape): {_safe_text(profile.get('primary_niche') or 'n/a', 40)}
Secondary niches (AI scrape): {_safe_text(', '.join(str(s) for s in secondary) if secondary else 'n/a', 120)}
Content themes: {_safe_text(', '.join(str(t) for t in themes[:12]) if themes else 'n/a', 160)}
Aesthetic: {_safe_text(', '.join(str(x) for x in (aesthetic.get('aesthetic_descriptors') or [])[:8]), 120)}
Engagement: {profile.get('engagement_rate')} | Posts/week: {profile.get('posting_cadence_per_week')}

USER INTERESTS (soft preference only — do NOT treat as content proof):
{_safe_text(', '.join(interest_niches) if interest_niches else 'none', 120)}
"""


def _brand_card(brand: Dict) -> str:
    return (
        f"- id={brand.get('id')} | {_safe_text(brand.get('name') or brand.get('brand_name'), 50)} | "
        f"cat={_safe_text(brand.get('category'), 24)} | "
        f"desc={_safe_text(brand.get('description'), 80)}"
    )


def _extract_json_object(text: str) -> str:
    text = (text or '').strip()
    if '```json' in text:
        text = text.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in text:
        text = text.split('```', 1)[1].split('```', 1)[0].strip()
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _parse_ranked_ids(text: str) -> List[int]:
    cleaned = _extract_json_object(text)
    try:
        data = json.loads(cleaned)
        ids = data.get('ranked_ids') or data.get('matches') or []
        if isinstance(ids, list) and ids and isinstance(ids[0], dict):
            out = []
            for item in ids:
                try:
                    out.append(int(item.get('brand_id')))
                except (TypeError, ValueError):
                    continue
            return out
        return [int(x) for x in ids]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Salvage bare integers after ranked_ids
    ids = [int(x) for x in re.findall(r'\b(\d{2,6})\b', text or '')]
    # Dedupe preserving order
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    if out:
        print(f'[MentorMatch] salvaged {len(out)} ids from text')
        return out
    raise ValueError(f'Unparseable ranked_ids ({len(text or "")} chars)')


def _call_gemini_rank(prompt: str) -> List[int]:
    api_key = _gemini_api_key()
    if not api_key:
        raise ValueError('GEMINI_API_KEY not configured')

    model = _gemini_model()
    url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{model}:generateContent?key={api_key}'
    )
    payload = {
        'contents': [{'parts': [{'text': f'{MATCHMAKER_SYSTEM}\n\n{prompt}'}]}],
        'generationConfig': {
            'temperature': 0.1,
            'topK': 1,
            'topP': 0.8,
            'maxOutputTokens': 512,
            'responseMimeType': 'application/json',
            'responseSchema': MATCH_SCHEMA,
        },
    }
    response = requests.post(url, json=payload, timeout=45)
    if response.status_code >= 400:
        print(f'[MentorMatch] schema call HTTP {response.status_code}; retrying plain JSON')
        payload['generationConfig'].pop('responseSchema', None)
        response = requests.post(url, json=payload, timeout=45)
    response.raise_for_status()
    result = response.json()
    candidates = result.get('candidates') or []
    if not candidates:
        raise ValueError(f'Gemini returned no candidates: {result.get("promptFeedback")}')
    parts = candidates[0].get('content', {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if isinstance(p, dict))
    if not text:
        raise ValueError(f'Gemini empty text (finishReason={candidates[0].get("finishReason")})')
    return _parse_ranked_ids(text)


_WEAK_PRIMARIES = frozenset({
    'other', 'unknown', 'general', 'misc', 'miscellaneous', 'n/a', 'na', 'none', '',
})


def _has_usable_scrape_lane(profile: Dict) -> bool:
    primary = (profile.get('primary_niche') or '').lower().strip()
    if not primary or primary in _WEAK_PRIMARIES:
        return False
    mapped = _mapped_category(primary)
    return mapped in PRIMARY_NICHE_ADJACENCY or mapped not in _WEAK_PRIMARIES


def _in_scrape_lane(profile: Dict, brand_category: str) -> bool:
    primary = (profile.get('primary_niche') or '').lower().strip()
    if not primary or primary in _WEAK_PRIMARIES:
        # No usable scrape lane — treat all calculator-approved as equal
        return True
    mapped_primary = _mapped_category(primary)
    mapped_brand = _mapped_category(brand_category)
    adjacent = set(PRIMARY_NICHE_ADJACENCY.get(mapped_primary, {mapped_primary}))
    secondary = profile.get('secondary_niches') or []
    if isinstance(secondary, str):
        try:
            secondary = json.loads(secondary)
        except Exception:
            secondary = []
    for s in secondary:
        s_mapped = _mapped_category(str(s))
        adjacent |= PRIMARY_NICHE_ADJACENCY.get(s_mapped, {s_mapped})
        adjacent.add(s_mapped)
    return mapped_brand in adjacent


def _interest_boost(brand_category: str, interest_niches: List[str]) -> float:
    """Tiny soft boost — never enough to rescue a Stretch brand."""
    if not interest_niches:
        return 0.0
    cat = (brand_category or '').lower()
    mapped_cat = _mapped_category(cat)
    for n in interest_niches:
        n = (n or '').lower()
        if not n:
            continue
        if n in cat or cat in n or _mapped_category(n) == mapped_cat:
            return 3.0
    return 0.0


def _prefilter_candidates(
    profile: Dict,
    brands: List[Dict],
    interest_niches: Optional[List[str]] = None,
    min_score: int = 35,
) -> List[Dict]:
    """Unlock-parity calculator gate. Prefer scrape-lane brands in shortlist."""
    interest_niches = interest_niches or []
    usable_lane = _has_usable_scrape_lane(profile)
    in_lane = []
    out_lane = []
    for brand in brands:
        brand_dict = dict(brand)
        cat = brand_dict.get('category') or ''
        fit = calculate_fit_score(profile, cat, brand=brand_dict)
        if fit['overall_score'] < min_score or fit['tier'] in ('stretch_match', 'not_recommended'):
            continue
        brand_dict['match_score'] = fit['overall_score']
        brand_dict['fit_tier'] = fit['tier']
        brand_dict['fit_status'] = fit['status']
        brand_dict['fit_label'] = fit['label']
        in_lane_flag = _in_scrape_lane(profile, cat) if usable_lane else True
        brand_dict['_sort'] = (
            fit['overall_score']
            + _interest_boost(cat, interest_niches)
            + (10 if in_lane_flag and usable_lane else 0)
        )
        if in_lane_flag:
            in_lane.append(brand_dict)
        else:
            out_lane.append(brand_dict)

    in_lane.sort(key=lambda b: b.get('_sort') or 0, reverse=True)
    out_lane.sort(key=lambda b: b.get('_sort') or 0, reverse=True)

    if usable_lane:
        # Cap off-lane interest brands so beauty feeds aren't flooded with bags/tech
        kept = in_lane[:14] + out_lane[:4]
    else:
        # Weak scrape niche — use full calculator-approved pool (up to 16)
        kept = in_lane + out_lane
        print(
            f"[MentorMatch] Weak scrape lane — shortlist from "
            f"{len(kept)} calculator-approved brands (no 2-cap)"
        )

    kept.sort(key=lambda b: b.get('_sort') or 0, reverse=True)
    for b in kept:
        b.pop('_sort', None)
    return kept[:16]


def _fallback_from_calculator(
    profile: Dict,
    brands: List[Dict],
    limit: int = 8,
    require_scrape_lane: bool = False,
) -> List[Dict]:
    """
    Rank calculator-approved brands.
    require_scrape_lane=False by default so backfill can fill to 8 when the
    LLM returns a thin list (critical when scrape niche was weak/'other').
    """
    ranked = []
    usable_lane = _has_usable_scrape_lane(profile)
    for brand in brands:
        b = dict(brand)
        # Reuse scores from prefilter when present
        if b.get('fit_tier') and b.get('match_score') is not None:
            fit_tier = b.get('fit_tier')
            fit_score = b.get('match_score') or 0
            if fit_tier in ('stretch_match', 'not_recommended') or fit_score < 35:
                continue
        else:
            fit = calculate_fit_score(profile, b.get('category') or '', brand=b)
            if fit['tier'] in ('stretch_match', 'not_recommended') or fit['overall_score'] < 35:
                continue
            b['match_score'] = fit['overall_score']
            b['fit_tier'] = fit['tier']
            b['fit_status'] = fit['status']
            b['fit_label'] = fit['label']

        if require_scrape_lane and usable_lane and not _in_scrape_lane(profile, b.get('category') or ''):
            continue
        b['mentor_why'] = None
        b['match_source'] = 'calculator_fallback'
        ranked.append(b)
    ranked.sort(
        key=lambda x: (
            0 if x.get('fit_status') in ('ready', 'almost') else 1,
            -(x.get('match_score') or 0),
        )
    )
    return ranked[:limit]


def _hydrate_from_ids(
    ranked_ids: List[int],
    by_id: Dict[int, Dict],
    profile: Dict,
) -> List[Dict]:
    ranked: List[Dict] = []
    seen = set()
    for bid in ranked_ids:
        if bid in seen or bid not in by_id:
            continue
        brand = dict(by_id[bid])
        fit = calculate_fit_score(profile, brand.get('category') or '', brand=brand)
        if fit['overall_score'] < 35 or fit['tier'] in ('stretch_match', 'not_recommended'):
            print(
                f"[MentorMatch] Dropping {brand.get('name')} — unlock would be "
                f"{fit['overall_score']}% {fit['tier']}"
            )
            continue
        brand['match_score'] = fit['overall_score']
        brand['fit_tier'] = fit['tier']
        brand['fit_status'] = fit['status']
        brand['fit_label'] = fit['label']
        brand['mentor_why'] = None
        brand['match_source'] = 'mentor_llm'
        ranked.append(brand)
        seen.add(bid)
        if len(ranked) >= 8:
            break
    return ranked


def _backfill_calculator(
    ranked: List[Dict],
    profile: Dict,
    shortlist: List[Dict],
    limit: int = 8,
) -> List[Dict]:
    """Pad thin LLM lists from the pre-approved shortlist (not scrape-lane only)."""
    out = [dict(b) for b in ranked]
    seen = {int(b['id']) for b in out if b.get('id') is not None}
    for b in _fallback_from_calculator(
        profile, shortlist, limit=limit, require_scrape_lane=False
    ):
        bid = int(b['id'])
        if bid in seen:
            continue
        b = dict(b)
        if ranked:
            b['match_source'] = 'mentor_llm+calculator'
        out.append(b)
        seen.add(bid)
        if len(out) >= limit:
            break
    return out


def rank_matches_with_mentor(
    creator_profile: Dict,
    candidate_brands: List[Dict],
    niches: Optional[List[str]] = None,
    creator_id: Optional[int] = None,
    force_refresh: bool = False,
) -> List[Dict]:
    """
    niches = user interest checkboxes (soft only).
    creator_profile must be scrape-based — do not merge checkbox niches into it.
    Always aims to return up to 8 brands.
    """
    interest_niches = niches or []
    if not candidate_brands:
        return []

    profile = creator_profile or {}
    fp = _profile_fingerprint(profile, interest_niches) + ':v2fill8'

    if creator_id and not force_refresh:
        cached = _MATCH_CACHE.get(int(creator_id))
        if cached and cached[0] > time.time() and cached[1] == fp:
            cached_rows = cached[2] or []
            # Never serve thin caches — recompute so For You stays full
            if len(cached_rows) >= 6 and any(
                str(b.get('match_source', '')).startswith('mentor') for b in cached_rows
            ):
                print(f"[MentorMatch] cache hit creator={creator_id} n={len(cached_rows)}")
                return [dict(b) for b in cached_rows]
            print(
                f"[MentorMatch] ignoring thin/stale cache creator={creator_id} "
                f"n={len(cached_rows)}"
            )

    shortlist = _prefilter_candidates(profile, candidate_brands, interest_niches)
    by_id = {int(b['id']): b for b in shortlist if b.get('id') is not None}
    if not by_id:
        print('[MentorMatch] No calculator-approved candidates')
        return []

    print(
        f"[MentorMatch] shortlist={len(shortlist)} primary={profile.get('primary_niche')} "
        f"interests={interest_niches}"
    )

    last_err = None
    ranked: List[Dict] = []
    for attempt in range(1, 3):
        try:
            brand_lines = '\n'.join(_brand_card(b) for b in shortlist)
            prompt = f"""{_creator_summary(profile, interest_niches)}

CANDIDATE BRAND IDS (pre-approved by fit calculator — pick best first):
{brand_lines}

Return JSON with up to 8 IDs (use as many as possible from the list):
{{"ranked_ids":[...up to 8 ids...]}}
"""
            ranked_ids = _call_gemini_rank(prompt)
            ranked = _hydrate_from_ids(ranked_ids, by_id, profile)
            if ranked:
                break
            print(f'[MentorMatch] LLM ids failed unlock gate on attempt {attempt}')
        except Exception as e:
            last_err = e
            print(f'[MentorMatch] LLM rank failed (attempt {attempt}): {e}')

    if ranked:
        if len(ranked) < 8:
            before = len(ranked)
            ranked = _backfill_calculator(ranked, profile, shortlist, limit=8)
            print(f'[MentorMatch] backfilled {before} -> {len(ranked)} brands')
        print(f"[MentorMatch] LLM ranked {len(ranked)} brands for creator={creator_id}")
        if creator_id and len(ranked) >= 4:
            _MATCH_CACHE[int(creator_id)] = (
                time.time() + _CACHE_TTL_SEC,
                fp,
                [dict(b) for b in ranked],
            )
        elif creator_id:
            _MATCH_CACHE.pop(int(creator_id), None)
        return ranked

    if last_err:
        print(f'[MentorMatch] falling back to calculator after: {last_err}')

    fallback = _fallback_from_calculator(
        profile, shortlist, limit=8, require_scrape_lane=False
    )
    if creator_id:
        _MATCH_CACHE.pop(int(creator_id), None)
    return fallback


def invalidate_mentor_matches(creator_id: int) -> None:
    _MATCH_CACHE.pop(int(creator_id), None)
