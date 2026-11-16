"""Creator-side tools for the AI-assistant connectors (ChatGPT, Claude, Gemini).

- find_brands: brands in a niche worth pitching now, strongest real signal first (open paid brief, creator-style
  ads, found running ads in the last 30 days, confirmed gifting).
- get_brand_pr_info: what we know about one brand: does it send PR packages / gift creators, is it running ads,
  open briefs, public socials, and where to see its contact on Newcollab.

Read-only and zero-auth, like the creator search. Contact emails and application links never leave Newcollab:
results point to the public brand page and to the creator signup, where Polly can pitch for them.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from services.brand_ugc import _signal_rows, _strength, brand_ugc
from services.creator_directory_search import clean_country, frontend_base, kit_base
from services.public_showcase import _clean

DEFAULT_LIMIT = 5
MAX_LIMIT = 8

CATEGORIES = (
    "skincare", "beauty", "supplements", "wellness", "fashion", "haircare", "food", "home", "jewelry", "fitness",
    "activewear", "tech", "pet", "luxury", "lifestyle", "sustainable", "baby", "travel", "gaming",
)
_ALIASES = {
    "skin": "skincare", "skin care": "skincare", "spf": "skincare", "acne": "skincare",
    "makeup": "beauty", "make-up": "beauty", "cosmetics": "beauty", "nails": "beauty", "fragrance": "beauty",
    "perfume": "beauty", "hair": "haircare", "hair care": "haircare",
    "vitamins": "supplements", "protein": "supplements", "nutrition": "supplements",
    "health": "wellness", "self care": "wellness", "self-care": "wellness", "mental health": "wellness",
    "clothing": "fashion", "apparel": "fashion", "streetwear": "fashion", "shoes": "fashion", "accessories": "fashion",
    "snacks": "food", "drinks": "food", "beverage": "food", "beverages": "food", "coffee": "food", "cooking": "food",
    "kitchen": "home", "decor": "home", "home decor": "home", "cleaning": "home", "interior": "home",
    "gym": "fitness", "workout": "fitness", "sports": "fitness", "yoga": "activewear", "athleisure": "activewear",
    "gadgets": "tech", "electronics": "tech", "apps": "tech", "dog": "pet", "dogs": "pet", "cat": "pet",
    "cats": "pet", "pets": "pet", "mom": "baby", "mum": "baby", "mums": "baby", "moms": "baby", "mama": "baby",
    "mummy": "baby", "mommy": "baby", "motherhood": "baby", "postpartum": "baby", "pregnancy": "baby",
    "toddler": "baby", "parenting": "baby", "kids": "baby", "family": "baby",
    "eco": "sustainable", "sustainability": "sustainable", "games": "gaming", "jewellery": "jewelry",
}
_COUNTRY_NAMES = {
    "US": ("us", "usa", "united states"), "GB": ("uk", "gb", "united kingdom", "england"),
    "CA": ("ca", "canada"), "AU": ("au", "australia"), "FR": ("fr", "france"), "DE": ("de", "germany"),
    "ES": ("es", "spain"), "IT": ("it", "italy"), "NL": ("nl", "netherlands"), "IE": ("ie", "ireland"),
    "NZ": ("nz", "new zealand"),
}
UTM_MEDIUM = "connector"


def niche_category(raw: Any) -> Optional[str]:
    """Free-text niche -> one of our brand categories, or None when we can't map it."""
    text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not text:
        return None
    if text in CATEGORIES:
        return text
    if text in _ALIASES:
        return _ALIASES[text]
    words = [w for w in re.split(r"[^a-z]+", text) if w]
    for word in words:  # a category named outright wins over a looser alias ("postpartum fashion")
        if word in CATEGORIES:
            return word
    for word in words:
        if word in _ALIASES:
            return _ALIASES[word]
    for alias, cat in _ALIASES.items():
        if " " in alias and alias in text:
            return cat
    return None


def clean_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, n))


def ships_to(regions: Any, country: Optional[str]) -> bool:
    """Lenient: unknown or worldwide shipping passes; only an explicit list without the country fails."""
    if not country:
        return True
    items = regions if isinstance(regions, list) else []
    names = [str(x).strip().lower() for x in items if str(x).strip()]
    if not names:
        return True
    wanted = set(_COUNTRY_NAMES.get(country, (country.lower(),)))
    return any(n in ("worldwide", "global", "international") or n in wanted for n in names)


def _utm(source: str, campaign: str) -> Dict[str, str]:
    return {"utm_source": source, "utm_medium": UTM_MEDIUM, "utm_campaign": campaign}


def brand_page_url(slug: str, source: str) -> str:
    return f"{kit_base()}/brand/{slug}?{urlencode(_utm(source, 'creator_tools'))}"


def creator_cta(source: str, niche: Optional[str] = None) -> Dict[str, str]:
    query = _utm(source, "creator_tools")
    if niche:
        query["niche"] = niche
    return {
        "text": "Polly, Newcollab's AI agent, can pitch these brands for you: up to 20 a day, sent from your own "
                "Gmail, with follow-ups. Free to start.",
        "url": f"{frontend_base()}/register/creator?{urlencode(query)}",
    }


def _why(row: Dict) -> List[str]:
    out = []
    if row.get("brief"):
        out.append("Has an open paid UGC brief on Newcollab")
    if row.get("creator_ads"):
        out.append("Runs creator-style (UGC) ads")
    if row.get("hot") and row.get("seen_on"):
        out.append(f"Seen running ads on {row['seen_on'].isoformat()}")
    if row.get("gifting"):
        out.append("Confirmed gifting products to creators")
    if row.get("accepting") and not out:
        out.append("Listed as accepting PR requests")
    return out


def find_brands(cur, params: Dict, source: str = "chatgpt") -> Dict:
    niche_raw = _clean(params.get("niche"), 60)
    category = niche_category(niche_raw)
    if not niche_raw:
        raise ValueError("niche is required")
    country = clean_country(params.get("country"))
    limit = clean_limit(params.get("limit"))
    base = {"niche": niche_raw, "category": category, "country": country, "creator_cta": creator_cta(source, category)}
    if not category:
        return {**base, "brands": [], "total_matches": 0,
                "note": "No brand category matches this niche. Categories: " + ", ".join(CATEGORIES) + "."}

    rows = sorted(_signal_rows(cur, category), key=_strength)
    if len(rows) < limit:
        # Thin niches: top up with brands that list themselves as accepting PR requests (labelled as such).
        seen = [r["id"] for r in rows] or [0]
        cur.execute("""
            SELECT id, brand_name, slug FROM pr_brands
            WHERE COALESCE(status, 'published') = 'published' AND NULLIF(TRIM(slug), '') IS NOT NULL
              AND LOWER(COALESCE(category, '')) = %s AND accepting_pr IS TRUE AND NOT (id = ANY(%s))
            ORDER BY COALESCE(response_rate, 0) DESC, updated_at DESC NULLS LAST
            LIMIT %s
        """, (category, seen, (limit - len(rows)) * 3))
        for r in cur.fetchall():
            r = r if isinstance(r, dict) else dict(zip([d[0] for d in cur.description], r))
            rows.append({**r, "accepting": True})
    if country and rows:
        cur.execute("SELECT id, regions FROM pr_brands WHERE id = ANY(%s)", ([r["id"] for r in rows],))
        regions = {}
        for r in cur.fetchall():
            r = r if isinstance(r, dict) else dict(zip([d[0] for d in cur.description], r))
            regions[r["id"]] = r.get("regions")
        rows = [r for r in rows if ships_to(regions.get(r["id"]), country)]
    brands = [{
        "name": _clean(r.get("brand_name"), 60),
        "category": category,
        "why_now": _why(r),
        "brand_page": brand_page_url(r["slug"], source),
    } for r in rows[:limit]]
    return {**base, "brands": brands, "total_matches": len(rows)}


def _find_brand_row(cur, query: str) -> Optional[Dict]:
    q = query.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
    cur.execute("""
        SELECT id, brand_name, slug, category, website, description, pr_description, instagram_handle,
               tiktok_handle, regions, accepting_pr, gifting_confirmed_at, source, created_at,
               (NULLIF(TRIM(contact_email), '') IS NOT NULL) AS has_email,
               (NULLIF(TRIM(application_form_url), '') IS NOT NULL) AS has_form
        FROM pr_brands
        WHERE COALESCE(status, 'published') = 'published' AND NULLIF(TRIM(slug), '') IS NOT NULL
          AND (slug = %(slug)s OR LOWER(brand_name) = LOWER(%(q)s) OR LOWER(brand_name) LIKE LOWER(%(prefix)s))
        ORDER BY (slug = %(slug)s) DESC, (LOWER(brand_name) = LOWER(%(q)s)) DESC, LENGTH(brand_name)
        LIMIT 1
    """, {"slug": slug, "q": q, "prefix": q.replace("%", "").replace("_", "\\_") + "%"})
    row = cur.fetchone()
    if not row:
        return None
    return row if isinstance(row, dict) else dict(zip([d[0] for d in cur.description], row))


def _handle(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().lstrip("@")
    text = re.sub(r"^https?://(www\.)?(instagram|tiktok)\.com/@?", "", text).strip("/")
    return text or None


def get_brand_pr_info(cur, params: Dict, source: str = "chatgpt") -> Dict:
    query = _clean(params.get("brand"), 80)
    if not query:
        raise ValueError("brand is required")
    row = _find_brand_row(cur, query)
    if not row:
        return {"brand": query, "found": False, "creator_cta": creator_cta(source),
                "note": "This brand is not in Newcollab's brand directory yet."}
    ugc = brand_ugc(cur, row)
    socials = {k: v for k, v in (("instagram", _handle(row.get("instagram_handle"))),
                                   ("tiktok", _handle(row.get("tiktok_handle")))) if v}
    return {
        "brand": _clean(row.get("brand_name"), 60),
        "found": True,
        "category": (row.get("category") or "").lower() or None,
        "about": _clean(row.get("pr_description") or row.get("description"), 300) or None,
        "website": row.get("website") or None,
        "socials": socials,
        "gifting_confirmed": bool(row.get("gifting_confirmed_at")),
        "listed_as_accepting_pr": bool(row.get("accepting_pr")),
        "running_ads": {
            "seen_in_last_30_days": ugc["hot"],
            "last_seen_on": ugc["seenRunningAdsOn"],
            "creator_style_ads": ugc["creatorStyleAds"],
            "ad_opening_lines": ugc["adExamples"],
        },
        "open_paid_briefs": ugc["liveBriefs"],
        "ships_to": [str(x) for x in row.get("regions")] if isinstance(row.get("regions"), list) else [],
        "contact": {
            "pr_email_on_newcollab": bool(row.get("has_email")),
            "application_form_on_newcollab": bool(row.get("has_form")),
            "where": "Available to creators on Newcollab (brand_page).",
        },
        "brand_page": brand_page_url(row["slug"], source),
        "creator_cta": creator_cta(source, (row.get("category") or "").lower() or None),
    }
