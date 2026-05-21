"""
Canonical brand category slugs for pr_brands.
Used by API filters, admin, imports, and DB normalization.
"""
import re

CANONICAL_CATEGORIES = [
    'skincare',
    'beauty',
    'fashion',
    'wellness',
    'fitness',
    'food',
    'travel',
    'tech',
    'gaming',
    'lifestyle',
    'home',
    'pet',
    'baby',
    'jewelry',
    'haircare',
    'sustainable',
    'luxury',
    'activewear',
    'supplements',
    'other',
]

# Raw/import/legacy values -> canonical slug
CATEGORY_ALIASES = {
    'pets': 'pet',
    'pet products': 'pet',
    'food-beverage': 'food',
    'food & beverage': 'food',
    'food and beverage': 'food',
    'food & nutrition': 'food',
    'accessories': 'fashion',
    'k-beauty': 'skincare',
    'kbeauty': 'skincare',
    'k beauty': 'skincare',
    'makeup': 'beauty',
    'health': 'wellness',
    'health & wellness': 'wellness',
    'health and wellness': 'wellness',
    'home & living': 'home',
    'home and living': 'home',
    'sportswear': 'activewear',
    'sports': 'fitness',
    'nutrition': 'supplements',
    'supplement': 'supplements',
    'tech & gadgets': 'tech',
    'technology': 'tech',
    'parenting': 'baby',
    'parenting & family': 'baby',
    'kids': 'baby',
    'jewellery': 'jewelry',
    'eco': 'sustainable',
    'sustainability': 'sustainable',
    'games': 'gaming',
    'entertainment': 'lifestyle',
    'music': 'lifestyle',
    'finance': 'lifestyle',
    'business': 'lifestyle',
}

CATEGORY_LABELS = {
    'skincare': 'Skincare',
    'beauty': 'Beauty',
    'fashion': 'Fashion',
    'wellness': 'Wellness',
    'fitness': 'Fitness',
    'food': 'Food & Beverage',
    'travel': 'Travel',
    'tech': 'Tech',
    'gaming': 'Gaming',
    'lifestyle': 'Lifestyle',
    'home': 'Home & Living',
    'pet': 'Pet',
    'baby': 'Baby & Parenting',
    'jewelry': 'Jewelry',
    'haircare': 'Haircare',
    'sustainable': 'Sustainable',
    'luxury': 'Luxury',
    'activewear': 'Activewear',
    'supplements': 'Supplements',
    'other': 'Other',
}


def _slug_key(raw: str) -> str:
    s = str(raw).strip().lower()
    s = s.replace('_', '-')
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s*&\s*', ' & ', s)
    return s


def normalize_category(raw):
    """
    Map any category string to a canonical slug, or None if empty.
    Unknown non-empty values become 'other'.
    """
    if raw is None:
        return None
    key = _slug_key(raw)
    if not key:
        return None
    if key in CANONICAL_CATEGORIES:
        return key
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    key_hyp = key.replace(' ', '-')
    if key_hyp in CANONICAL_CATEGORIES:
        return key_hyp
    if key_hyp in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key_hyp]
    key_nospace = key.replace(' ', '').replace('-', '')
    for alias, canon in CATEGORY_ALIASES.items():
        if alias.replace(' ', '').replace('-', '') == key_nospace:
            return canon
    for canon in CANONICAL_CATEGORIES:
        if canon.replace('-', '') == key_nospace:
            return canon
    return 'other'


def category_label(slug):
    if not slug:
        return ''
    canon = normalize_category(slug) or slug
    return CATEGORY_LABELS.get(canon, canon.replace('-', ' ').title())


def aggregate_category_counts(rows):
    """Sum brand counts by canonical category from (category, count) rows."""
    totals = {c: 0 for c in CANONICAL_CATEGORIES}
    for row in rows:
        raw = row.get('category') if isinstance(row, dict) else row[0]
        count = row.get('brand_count') if isinstance(row, dict) else row[1]
        canon = normalize_category(raw) or 'other'
        totals[canon] = totals.get(canon, 0) + (count or 0)
    return [
        {
            'value': slug,
            'label': category_label(slug),
            'count': totals[slug],
        }
        for slug in CANONICAL_CATEGORIES
        if totals.get(slug, 0) > 0
    ]


def raw_values_for_canonical(canon):
    """All raw DB strings that should match a canonical filter (post-migration mostly identity)."""
    canon = normalize_category(canon)
    if not canon:
        return []
    raws = {canon}
    for alias, target in CATEGORY_ALIASES.items():
        if target == canon:
            raws.add(alias)
    return list(raws)
