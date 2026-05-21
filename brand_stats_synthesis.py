"""
Deterministic synthetic brand stats by vertical when real data is missing.
Same slug always gets the same values until backfilled with real pipeline data.
"""
import hashlib
import re

# (min_rate%, max_rate%, min_days, max_days) per category
CATEGORY_STATS = {
    'beauty': (32, 47, 4, 8),
    'skincare': (35, 52, 3, 7),
    'haircare': (33, 48, 4, 7),
    'fashion': (28, 42, 5, 10),
    'luxury': (24, 38, 6, 12),
    'activewear': (30, 44, 5, 9),
    'fitness': (34, 49, 3, 6),
    'wellness': (36, 50, 4, 7),
    'supplements': (32, 46, 4, 8),
    'food': (38, 55, 3, 6),
    'tech': (22, 36, 6, 11),
    'gaming': (20, 34, 7, 12),
    'lifestyle': (30, 44, 4, 8),
    'home': (28, 40, 5, 9),
    'pet': (35, 50, 3, 7),
    'baby': (33, 47, 4, 8),
    'jewelry': (26, 40, 5, 10),
    'travel': (29, 43, 5, 9),
    'sustainable': (31, 45, 4, 8),
    'k-beauty': (37, 52, 3, 6),
    'other': (28, 42, 4, 9),
}

DEFAULT_STATS = CATEGORY_STATS['lifestyle']


def normalize_category(category):
    if not category:
        return 'lifestyle'
    c = re.sub(r'[^a-z0-9]+', ' ', str(category).lower()).strip()
    if 'k beauty' in c or 'kbeauty' in c:
        return 'k-beauty'
    if 'health' in c and 'wellness' in c:
        return 'wellness'
    if 'food' in c or 'beverage' in c:
        return 'food'
    if 'home' in c and 'living' in c:
        return 'home'
    first = c.split()[0] if c else 'lifestyle'
    return first if first in CATEGORY_STATS else 'lifestyle'


def _seeded_int(slug, salt, lo, hi):
    raw = hashlib.md5(f'{slug}:{salt}'.encode('utf-8')).hexdigest()
    n = int(raw[:8], 16)
    return lo + (n % (hi - lo + 1))


def generate_synthetic_stats(slug, category=None):
    """Return (response_rate:int, avg_response_days:int) for a brand slug."""
    slug = (slug or 'brand').strip().lower()
    key = normalize_category(category)
    rate_lo, rate_hi, days_lo, days_hi = CATEGORY_STATS.get(key, DEFAULT_STATS)
    rate = _seeded_int(slug, 'rate', rate_lo, rate_hi)
    days = _seeded_int(slug, 'days', days_lo, days_hi)
    return rate, days


def needs_synthetic_response_rate(value):
    return value is None or value == 0


def needs_synthetic_avg_days(value):
    return value is None or value == 0


def resolve_brand_stats(slug, category, response_rate=None, avg_response_days=None):
    """
    Fill missing stats only; keep real non-zero values from DB/pipeline.
    """
    if needs_synthetic_response_rate(response_rate) or needs_synthetic_avg_days(avg_response_days):
        synth_rate, synth_days = generate_synthetic_stats(slug, category)
        if needs_synthetic_response_rate(response_rate):
            response_rate = synth_rate
        if needs_synthetic_avg_days(avg_response_days):
            avg_response_days = synth_days
    return response_rate, avg_response_days


def resolve_pitch_social_proof(slug, pitch_count, response_count, response_rate):
    """Light synthetic pitch counts when no pipeline activity yet."""
    pitch_count = pitch_count or 0
    response_count = response_count or 0
    if pitch_count > 0:
        return pitch_count, response_count
    slug = (slug or 'brand').strip().lower()
    pitch_count = _seeded_int(slug, 'pitches', 8, 64)
    if response_rate and response_rate > 0:
        response_count = max(1, round(pitch_count * response_rate / 100))
    else:
        response_count = _seeded_int(slug, 'responses', 2, max(3, pitch_count // 4))
    return pitch_count, response_count
