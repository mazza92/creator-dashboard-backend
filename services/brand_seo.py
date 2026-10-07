"""SEO helpers for public brand pages: one indexability rule plus the
per-brand facts that make each /brand/<slug> page unique.

The frontend page, the sitemap and the A-Z index all read `indexable` from
the API so Google never gets a sitemap URL that the page itself noindexes.
"""

import ast
import json
import re

# Scraped page titles ("Maje Site Officiel") are junk even when stored as the name.
_SCRAPED_SLUG_PATTERNS = [re.compile(p, re.I) for p in (
    r'official.*website', r'official.*site', r'site-officiel', r'welcome-to-',
    r'skin-care.*products', r'beauty-products', r'makeup-and-beauty',
    r'home-furniture', r'clothing-and', r'-for-healthier-', r'-for-healthy-',
    r'luxury-organic', r'luxury-skin', r'technical-apparel',
    r'maquillage-soins', r'collagen-protein', r'collagen-supplements',
)]
# Store-style affixes are fine when they are part of the real name ("The Body Shop").
_AFFIX_SLUG_PATTERNS = [re.compile(p, re.I) for p in (
    r'^buy-', r'^shop-', r'-shop$', r'-store$', r'-home$', r'-us$', r'-eu$',
)]

MIN_DESCRIPTION = 40
MIN_FACTS = 4


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _loads(value):
    """Columns hold JSON, Python-repr strings, or already-decoded values."""
    if value is None or isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(value)
        except (ValueError, SyntaxError, TypeError):
            continue
    return None


def _list(value):
    data = _loads(value)
    if isinstance(data, list):
        return [x for x in data if x not in (None, '')]
    return []


def is_low_quality_slug(slug, name=None):
    if not slug or len(slug) > 50 or re.search(r'[/?#\s]', slug):
        return True
    if any(p.search(slug) for p in _SCRAPED_SLUG_PATTERNS):
        return True
    brand = re.sub(r'[^a-z0-9]', '', (name or '').lower())
    flat = re.sub(r'[^a-z0-9]', '', slug.lower())
    if not (brand and flat == brand) and any(p.search(slug) for p in _AFFIX_SLUG_PATTERNS):
        return True
    if brand and len(brand) <= 20 and brand[:5] not in flat and len(slug) > len(brand) * 3:
        return True
    return False


def brand_fact_count(row):
    facts = [
        _text(row.get('hero_product')),
        _text(row.get('target_audience')),
        row.get('price_point'),
        row.get('avg_product_value'),
        _text(row.get('collaboration_type')),
        _list(row.get('regions')),
        _text(row.get('instagram_handle')) or _text(row.get('tiktok_handle')),
        _text(row.get('application_method')),
        _list(row.get('product_types')) or _list(row.get('niches')),
        (row.get('min_followers') or 0) > 0,
    ]
    return sum(1 for f in facts if f)


def is_indexable(row):
    """A brand page is index-worthy when it has a clean slug, a logo, a real
    description and enough brand-specific facts to differ from its siblings."""
    if not row:
        return False
    if (row.get('status') or 'published') != 'published':
        return False
    if is_low_quality_slug(row.get('slug'), row.get('brand_name') or row.get('name')):
        return False
    if not row.get('logo_url'):
        return False
    if len(_text(row.get('description'))) < MIN_DESCRIPTION:
        return False
    return brand_fact_count(row) >= MIN_FACTS


def example_posts(value, limit=4):
    posts = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        url = _text(item.get('url'))
        if not url.startswith('https://'):
            continue
        title = re.sub(r'\s+', ' ', _text(item.get('title'))).strip(' …')
        posts.append({
            'url': url,
            'title': title[:140] or None,
            'platform': _text(item.get('platform')).lower() or None,
            'handle': _text(item.get('handle')) or None,
        })
        if len(posts) >= limit:
            break
    return posts


def social_profile(value):
    data = _loads(value)
    if not isinstance(data, dict):
        return None
    followers = data.get('followers')
    handle = _text(data.get('handle'))
    if not handle or not isinstance(followers, (int, float)) or followers < 100:
        return None
    return {
        'platform': _text(data.get('platform')).lower() or None,
        'handle': handle,
        'followers': int(followers),
        'posts': int(data['posts']) if isinstance(data.get('posts'), (int, float)) and data['posts'] > 0 else None,
        'verified': bool(data.get('verified')),
    }


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else None


def public_seo_fields(row, estimated_value=None):
    """Brand-specific facts for the public detail payload (no gated data)."""
    value = row.get('avg_product_value') or estimated_value
    price = row.get('price_point')
    return {
        'heroProduct': _text(row.get('hero_product')) or None,
        'targetAudience': _text(row.get('target_audience')) or None,
        'tone': _text(row.get('tone')) or None,
        'pricePoint': int(price) if isinstance(price, (int, float)) and price > 0 else None,
        'estimatedValue': int(value) if isinstance(value, (int, float)) and value > 0 else None,
        'collaborationType': _text(row.get('collaboration_type')) or None,
        'microFriendly': bool(row.get('micro_friendly')),
        'acceptingPr': row.get('accepting_pr') is not False,
        'youtube': _text(row.get('youtube_handle')) or None,
        'socialProfile': social_profile(row.get('pr_social_profile')),
        'examplePosts': example_posts(row.get('pr_example_posts')),
        'updatedAt': _iso(row.get('updated_at')),
        'lastVerifiedAt': _iso(row.get('last_verified_at')),
        'indexable': is_indexable(row),
    }
