"""
Mentor Matchmaker — For You brand ranking.

Scoring is brand-aware (name, description, hero SKU vs creator bio/themes/interests).
Category DNA is only the baseline so two beauty brands do not share one percentage.

Flow:
1. SQL candidate pool (intent lanes + limited scrape proof)
2. Brand-aware calculator scores every candidate
3. Diversity cap + weekly rotation already applied upstream
4. Gemini optionally reorders IDs; displayed score stays calculator
5. Cache successful ranks (~1 hour)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

import requests

from services.fit_score_calculator import (
    score_brand_for_creator,
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
Rank pre-approved brand IDs for ONE creator using their scraped social profile AND onboarding interests.

Return ONLY: {"ranked_ids":[123,456,789]}
- Best product fit first (match creator themes, bio, and interests to the brand description)
- Return UP TO 8 IDs whenever the candidate list has enough — aim for 8
- Only IDs from the candidate list
- Honor USER INTERESTS as pitching intent. Scrape is content proof, not a reason to ignore Beauty/Wellness
- Do not rank coffee, CBD, cannabis, optical/eyewear, or fashion for beauty/parenting/wellness creators unless the brand clearly matches those interests
- Prefer brands whose products the creator actually talks about
- Spread across the list — do not always pick the same popular beauty names
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

    return f"""CREATOR SOCIAL PROFILE (scraped — content proof)
Handle: @{_safe_text(profile.get('handle') or 'creator', 40)}
Followers: {profile.get('follower_count') or 0}
Bio: {_safe_text(profile.get('raw_bio'), 220)}
Primary niche (effective): {_safe_text(profile.get('primary_niche') or 'n/a', 40)}
Secondary niches: {_safe_text(', '.join(str(s) for s in secondary) if secondary else 'n/a', 120)}
Content themes: {_safe_text(', '.join(str(t) for t in themes[:12]) if themes else 'n/a', 160)}
Aesthetic: {_safe_text(', '.join(str(x) for x in (aesthetic.get('aesthetic_descriptors') or [])[:8]), 120)}
Engagement: {profile.get('engagement_rate')} | Posts/week: {profile.get('posting_cadence_per_week')}

USER INTERESTS (pitching intent — rank brands in these lanes first):
{_safe_text(', '.join(interest_niches) if interest_niches else 'none', 120)}
If interests include beauty, skincare, wellness, parenting, or baby, do not rank coffee, CBD, cannabis, optical/eyewear, or fashion unless the brand category is clearly in those interests.
"""


def _brand_card(brand: Dict) -> str:
    return (
        f"- id={brand.get('id')} | {_safe_text(brand.get('name') or brand.get('brand_name'), 50)} | "
        f"cat={_safe_text(brand.get('category'), 24)} | "
        f"score={brand.get('match_score')} | "
        f"hero={_safe_text(brand.get('hero_product'), 40)} | "
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


_GENERIC_NAME_STEMS = frozenset({
    'baby', 'beauty', 'skin', 'the', 'for', 'and', 'your', 'new', 'our',
})
_PARENTING_FAMILIES = frozenset({'baby', 'parenting', 'family', 'kids', 'maternity'})
_BEAUTY_FAMILIES = frozenset({'beauty', 'skincare', 'makeup', 'haircare', 'cosmetics'})


def _intent_bucket(category: str) -> str:
    raw = (category or '').lower().strip()
    mapped = _mapped_category(raw) or raw
    if mapped in _PARENTING_FAMILIES or raw in _PARENTING_FAMILIES:
        return 'parenting'
    if mapped in _BEAUTY_FAMILIES or raw in _BEAUTY_FAMILIES:
        return 'beauty'
    return mapped or 'other'


def diversify_matches(
    brands: List[Dict],
    limit: int = 8,
    max_per_raw: int = 2,
    max_per_family: int = 4,
    interest_niches: Optional[List[str]] = None,
) -> List[Dict]:
    """Keep the strongest brands without filling the feed with one category.

    Beauty + Baby creators get reserved seats in both lanes so deodorant
    and EMF brands cannot consume the leftover slots.
    """
    intent = set()
    for n in interest_niches or []:
        token = str(n or '').lower().strip()
        if token:
            intent.add(token)
            intent.add(_mapped_category(token))
    wants_parenting = bool(intent & _PARENTING_FAMILIES)
    wants_beauty = bool(intent & _BEAUTY_FAMILIES)

    quotas = None
    if wants_parenting and wants_beauty and limit <= 8:
        quotas = {'beauty': 4, 'parenting': 3, 'other': 1}
    elif wants_parenting and wants_beauty:
        quotas = {'beauty': max(6, limit // 2), 'parenting': max(6, limit // 2), 'other': 2}

    out: List[Dict] = []
    raw_counts: Counter = Counter()
    family_counts: Counter = Counter()
    bucket_counts: Counter = Counter()
    seen_stems = set()
    seen_ids = set()

    def _try_append(brand: Dict, enforce_quota: bool) -> bool:
        bid = brand.get('id')
        bid_int = None
        if bid is not None:
            try:
                bid_int = int(bid)
            except (TypeError, ValueError):
                bid_int = None
            if bid_int is not None and bid_int in seen_ids:
                return False
        raw_cat = (brand.get('category') or '').lower().strip() or 'unknown'
        family = _mapped_category(raw_cat) or raw_cat
        bucket = _intent_bucket(raw_cat)
        raw_limit = max_per_raw
        if enforce_quota and quotas and bucket == 'parenting':
            raw_limit = max(max_per_raw, int(quotas.get('parenting') or max_per_raw))
        if raw_counts[raw_cat] >= raw_limit:
            return False
        if family_counts[family] >= max_per_family:
            return False
        if enforce_quota and quotas:
            quota_key = bucket if bucket in quotas else 'other'
            if bucket_counts[quota_key] >= quotas.get(quota_key, 0):
                return False
        name = (brand.get('name') or brand.get('brand_name') or '').lower()
        stem = re.split(r'[\s+/&-]+', name)[0] if name else ''
        if stem and len(stem) > 3 and stem not in _GENERIC_NAME_STEMS and stem in seen_stems:
            return False
        out.append(brand)
        raw_counts[raw_cat] += 1
        family_counts[family] += 1
        bucket_counts[bucket if bucket in (quotas or {}) else 'other'] += 1
        if stem and len(stem) > 3 and stem not in _GENERIC_NAME_STEMS:
            seen_stems.add(stem)
        if bid_int is not None:
            seen_ids.add(bid_int)
        return True

    for brand in brands:
        if len(out) >= limit:
            break
        _try_append(brand, enforce_quota=True)

    if len(out) < limit:
        for brand in brands:
            if len(out) >= limit:
                break
            _try_append(brand, enforce_quota=False)

    return out


def _apply_brand_score(brand: Dict, profile: Dict, interest_niches: Optional[List[str]] = None) -> Tuple[Dict, Dict]:
    brand_dict = dict(brand)
    fit = score_brand_for_creator(profile, brand_dict, interest_niches=interest_niches)
    brand_dict['match_score'] = fit['overall_score']
    brand_dict['fit_tier'] = fit['tier']
    brand_dict['fit_status'] = fit['status']
    brand_dict['fit_label'] = fit['label']
    return brand_dict, fit


def _prefilter_candidates(
    profile: Dict,
    brands: List[Dict],
    interest_niches: Optional[List[str]] = None,
    min_score: int = 35,
) -> List[Dict]:
    """Brand-aware gate. Rank by real scores, then diversify the shortlist."""
    interest_niches = interest_niches or []
    scored = []
    for brand in brands:
        brand_dict, fit = _apply_brand_score(brand, profile, interest_niches)
        if fit['overall_score'] < min_score or fit['tier'] in ('stretch_match', 'not_recommended'):
            continue
        scored.append(brand_dict)

    scored.sort(key=lambda b: b.get('match_score') or 0, reverse=True)
    kept = diversify_matches(
        scored, limit=20, max_per_raw=3, max_per_family=8,
        interest_niches=interest_niches,
    )
    print(
        f"[MentorMatch] scored={len(scored)} shortlist={len(kept)} "
        f"primary={profile.get('primary_niche')} interests={interest_niches}"
    )
    return kept


def _fallback_from_calculator(
    profile: Dict,
    brands: List[Dict],
    limit: int = 8,
    require_scrape_lane: bool = False,
    interest_niches: Optional[List[str]] = None,
) -> List[Dict]:
    """Rank calculator-approved brands by brand-aware score, then diversify."""
    ranked = []
    usable_lane = _has_usable_scrape_lane(profile)
    for brand in brands:
        b = dict(brand)
        if b.get('fit_tier') and b.get('match_score') is not None:
            fit_tier = b.get('fit_tier')
            fit_score = b.get('match_score') or 0
            if fit_tier in ('stretch_match', 'not_recommended') or fit_score < 35:
                continue
        else:
            b, fit = _apply_brand_score(b, profile, interest_niches)
            if fit['tier'] in ('stretch_match', 'not_recommended') or fit['overall_score'] < 35:
                continue
        if require_scrape_lane and usable_lane and not _in_scrape_lane(profile, b.get('category') or ''):
            continue
        b['mentor_why'] = None
        b['match_source'] = b.get('match_source') or 'calculator_fallback'
        ranked.append(b)
    ranked.sort(key=lambda x: x.get('match_score') or 0, reverse=True)
    return diversify_matches(
        ranked, limit=limit, max_per_raw=2, max_per_family=5,
        interest_niches=interest_niches,
    )


def _hydrate_from_ids(
    ranked_ids: List[int],
    by_id: Dict[int, Dict],
    profile: Dict,
    interest_niches: Optional[List[str]] = None,
) -> List[Dict]:
    ranked: List[Dict] = []
    seen = set()
    for bid in ranked_ids:
        if bid in seen or bid not in by_id:
            continue
        brand = dict(by_id[bid])
        if brand.get('match_score') is None or not brand.get('fit_tier'):
            brand, fit = _apply_brand_score(brand, profile, interest_niches)
        else:
            fit = {
                'overall_score': brand.get('match_score') or 0,
                'tier': brand.get('fit_tier'),
            }
        if fit['overall_score'] < 35 or fit['tier'] in ('stretch_match', 'not_recommended'):
            print(
                f"[MentorMatch] Dropping {brand.get('name')} — unlock would be "
                f"{fit['overall_score']}% {fit['tier']}"
            )
            continue
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
    interest_niches: Optional[List[str]] = None,
) -> List[Dict]:
    """Pad thin LLM lists from the pre-approved shortlist."""
    out = [dict(b) for b in ranked]
    seen = {int(b['id']) for b in out if b.get('id') is not None}
    for b in _fallback_from_calculator(
        profile, shortlist, limit=limit, require_scrape_lane=False,
        interest_niches=interest_niches,
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
    out.sort(key=lambda x: x.get('match_score') or 0, reverse=True)
    return diversify_matches(
        out, limit=limit, max_per_raw=2, max_per_family=5,
        interest_niches=interest_niches,
    )


def rank_matches_with_mentor(
    creator_profile: Dict,
    candidate_brands: List[Dict],
    niches: Optional[List[str]] = None,
    creator_id: Optional[int] = None,
    force_refresh: bool = False,
) -> List[Dict]:
    """
    niches = user interest checkboxes (pitching intent).
    creator_profile should already be prepared (intent + scrape proof).
    Always aims to return up to 8 brands with differentiated scores.
    """
    interest_niches = niches or []
    if not candidate_brands:
        return []

    profile = creator_profile or {}
    fp = _profile_fingerprint(profile, interest_niches) + ':v5lane'

    if creator_id and not force_refresh:
        cached = _MATCH_CACHE.get(int(creator_id))
        if cached and cached[0] > time.time() and cached[1] == fp:
            cached_rows = cached[2] or []
            if len(cached_rows) >= 6:
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

    ranked = _fallback_from_calculator(
        profile, shortlist, limit=8, require_scrape_lane=False,
        interest_niches=interest_niches,
    )
    for b in ranked:
        b['match_source'] = 'calculator'

    # Gemini may reorder the already-scored 8. Thin/salvaged lists are ignored.
    try:
        brand_lines = '\n'.join(_brand_card(b) for b in ranked)
        prompt = f"""{_creator_summary(profile, interest_niches)}

CANDIDATE BRAND IDS (already scored — return ALL of these IDs, best product fit first):
{brand_lines}

Return JSON with every ID from the list:
{{"ranked_ids":[...ids...]}}
"""
        ranked_ids = _call_gemini_rank(prompt)
        allowed = set(by_id.keys()) | {int(b['id']) for b in ranked if b.get('id') is not None}
        valid = [i for i in ranked_ids if i in allowed]
        if len(valid) >= min(6, len(ranked)):
            reordered = _hydrate_from_ids(valid, {int(b['id']): b for b in ranked}, profile, interest_niches)
            if len(reordered) >= min(6, len(ranked)):
                ranked = reordered
                for b in ranked:
                    b['match_source'] = 'mentor_llm'
                print(f'[MentorMatch] LLM reordered {len(ranked)} scored brands')
        else:
            print(f'[MentorMatch] ignoring thin LLM reorder n={len(valid)}')
    except Exception as e:
        print(f'[MentorMatch] LLM reorder skipped: {e}')

    ranked.sort(key=lambda x: x.get('match_score') or 0, reverse=True)
    ranked = diversify_matches(
        ranked, limit=8, max_per_raw=2, max_per_family=5,
        interest_niches=interest_niches,
    )
    print(f"[MentorMatch] ranked {len(ranked)} brands for creator={creator_id}")
    if creator_id and len(ranked) >= 4:
        _MATCH_CACHE[int(creator_id)] = (
            time.time() + _CACHE_TTL_SEC,
            fp,
            [dict(b) for b in ranked],
        )
    elif creator_id:
        _MATCH_CACHE.pop(int(creator_id), None)
    return ranked


def invalidate_mentor_matches(creator_id: int) -> None:
    _MATCH_CACHE.pop(int(creator_id), None)
