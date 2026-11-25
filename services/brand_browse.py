"""'Find me K-beauty brands': search the whole directory by category, sub-niche or keyword,
not only the creator's saved niche."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from brand_categories import CANONICAL_CATEGORIES

# Sub-niche -> (canonical categories it lives in, keywords that mark it).
SUBNICHES: Dict[str, Dict[str, Any]] = {
    "k-beauty": {"label": "K-beauty", "cats": ["skincare", "beauty"],
                 "words": ["k-beauty", "kbeauty", "k beauty", "korean", "seoul", "glass skin", "rom&nd", "cosrx"]},
    "fragrance": {"label": "fragrance", "cats": ["beauty", "luxury"],
                  "words": ["fragrance", "fragrances", "perfume", "perfumes", "parfum", "parfums",
                            "eau de parfum", "cologne"]},
    "sustainable fashion": {"label": "sustainable fashion", "cats": ["fashion", "sustainable"],
                            "words": ["sustainable", "eco-friendly", "ethical", "recycled", "organic cotton",
                                      "slow fashion", "upcycled"],
                            "also": ["fashion", "clothing", "apparel", "wear", "denim", "dresses", "knitwear",
                                     "linen", "garments", "wardrobe"]},
    "teacher": {"label": "teacher & education", "cats": [],
                "words": ["for teachers", "teacher gifts", "teacher-approved", "classroom", "school supplies",
                          "stationery", "homeschool", "homeschooling", "back to school", "educators"]},
    "matcha": {"label": "matcha & wellness drinks", "cats": ["food", "wellness"],
               "words": ["matcha", "tea", "teas", "adaptogen", "adaptogenic", "mushroom coffee", "greens powder",
                         "electrolyte", "electrolytes", "functional beverage", "wellness drink"]},
    "automotive": {"label": "automotive", "cats": [],
                   "words": ["car care", "automotive", "detailing", "car accessories", "vehicle", "dash cam"]},
    "travel": {"label": "travel", "cats": ["travel"],
               "words": ["travel", "luggage", "suitcase", "packing cubes", "carry-on"]},
    "nails": {"label": "nails", "cats": ["beauty"],
              "words": ["nail", "nails", "press-on", "press-ons", "manicure", "nail polish"]},
}
_ALIASES = {
    "kbeauty": "k-beauty", "k beauty": "k-beauty", "korean skincare": "k-beauty", "korean beauty": "k-beauty",
    "perfume": "fragrance", "perfumes": "fragrance", "scents": "fragrance",
    "eco fashion": "sustainable fashion", "ethical fashion": "sustainable fashion", "slow fashion": "sustainable fashion",
    "edu": "teacher", "education": "teacher", "teachers": "teacher", "classroom": "teacher",
    "tea": "matcha", "wellness drinks": "matcha", "drinks": "matcha", "beverages": "matcha",
    "car": "automotive", "cars": "automotive", "auto": "automotive",
    "luggage": "travel",
    "nail": "nails", "manicure": "nails",
}
_CAT_ALIASES = {"pets": "pet", "pet": "pet", "jewellery": "jewelry", "hair": "haircare", "hair care": "haircare",
                "makeup": "beauty", "cosmetics": "beauty", "gym": "fitness", "workout": "fitness",
                "supplement": "supplements", "vitamins": "supplements", "clothing": "fashion",
                "clothes": "fashion", "home decor": "home", "homeware": "home", "kids": "baby",
                "skin care": "skincare", "games": "gaming", "gadgets": "tech", "athleisure": "activewear"}
_STOP = {"brand", "brands", "find", "show", "me", "some", "any", "for", "the", "and", "with", "that",
         "gifted", "pr", "companies", "company", "products", "product", "please", "more", "other"}


def _sub_plan(key: str) -> Dict[str, Any]:
    sub = SUBNICHES[key]
    return {"label": sub["label"], "cats": list(sub["cats"]), "words": list(sub["words"]),
            "also": list(sub.get("also") or []), "strict": True}


def parse_query(query: str) -> Dict[str, Any]:
    """{"label", "cats", "words"} for a free-text ask like 'K-beauty brands' or 'matcha'."""
    text = re.sub(r"\s+", " ", (query or "").lower()).strip()
    text = re.sub(r"\b(brands?|companies|products?)\b", "", text).strip(" .?!")
    key = _ALIASES.get(text, text)
    if key in SUBNICHES:
        return _sub_plan(key)
    cat = _CAT_ALIASES.get(text, text)
    if cat in CANONICAL_CATEGORIES:
        return {"label": cat, "cats": [cat], "words": [], "strict": False}
    for alias, sub_key in _ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return _sub_plan(sub_key)
    for sub_key in SUBNICHES:
        if re.search(rf"\b{re.escape(sub_key)}\b", text):
            return _sub_plan(sub_key)
    words = [w for w in re.findall(r"[a-z0-9&'-]{3,}", text) if w not in _STOP]
    cats = [c for c in (_CAT_ALIASES.get(w, w) for w in words) if c in CANONICAL_CATEGORIES]
    return {"label": text or "those", "cats": cats, "words": [w for w in words if w not in cats], "strict": False}


def word_pattern(word: str) -> str:
    """Postgres regex for a whole word/phrase, so 'tea' doesn't match 'team'."""
    return r"\m" + re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", word.lower()) + r"\M"


_ASK_RE = re.compile(
    r"(?i)\b(?:find|show|give|got|any|have\s+you\s+got|do\s+you\s+have|are\s+there|looking\s+for|want)\b"
    r"(?:\s+\w+){0,3}?\s+([a-z][a-z&' -]{1,30}?)\s+(?:brands?|companies|products)\b"
)


def browse_ask(text: str) -> Optional[str]:
    """'find me K-beauty brands' -> 'k-beauty'; None unless it names a known category or sub-niche."""
    m = _ASK_RE.search(text or "")
    if not m:
        return None
    phrase = re.sub(r"^(?:me|some|any|more|other|good|new)\s+", "", m.group(1).strip().lower())
    phrase = re.sub(r"^(?:me|some|any|more|other|good|new)\s+", "", phrase)
    plan = parse_query(phrase)
    return phrase if (plan["strict"] or plan["cats"]) else None


def browse_brands(conn, query: str, exclude_ids: Optional[List[int]] = None, limit: int = 12,
                  creator_id: Optional[int] = None) -> Dict[str, Any]:
    """Directory brands for a category / sub-niche / keyword. The query decides which brands match; for a
    creator, Thanos (surface "search") decides their order and drops ones that never fit them."""
    from psycopg2.extras import RealDictCursor

    plan = parse_query(query)
    cats, words = plan["cats"], plan["words"]
    if not cats and not words:
        return {"plan": plan, "brands": []}
    text = ("LOWER(CONCAT_WS(' ', brand_name, description, hero_product, target_audience, "
            "niches::text, product_types::text))")
    conds, params = [], []
    word_sql = ""
    if words:
        word_sql = " OR ".join([f"{text} ~ %s"] * len(words))
        params_words = [word_pattern(w) for w in words]
    else:
        params_words = []
    if plan["strict"] and words:
        # Sub-niche: the keyword must be there; the category only ranks.
        also = plan.get("also") or []
        if also:
            also_sql = " OR ".join([f"{text} ~ %s"] * len(also))
            conds.append(f"(({word_sql}) AND ({also_sql}))")
            params.extend(params_words + [word_pattern(w) for w in also])
        else:
            conds.append(f"({word_sql})")
            params.extend(params_words)
    else:
        if cats:
            conds.append("LOWER(COALESCE(category, '')) = ANY(%s)")
            params.append(cats)
        if words:
            conds.append(f"({word_sql})")
            params.extend(params_words)
    where = " OR ".join(conds)
    skip = [int(i) for i in (exclude_ids or []) if str(i).isdigit()]
    sql = f"""
        SELECT id, slug, brand_name AS name, logo_url AS logo, description, category, website,
               application_form_url, hero_product, target_audience, regions
        FROM pr_brands
        WHERE slug IS NOT NULL
          AND COALESCE(status, 'published') = 'published'
          AND ({where})
          {"AND NOT (id = ANY(%s))" if skip else ""}
        ORDER BY (LOWER(COALESCE(category, '')) = ANY(%s)) DESC, id DESC
        LIMIT %s
    """
    all_params = params + ([skip] if skip else []) + [cats or [""], SEARCH_POOL]
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, all_params)
    rows = [dict(r) for r in cur.fetchall()]
    from services.new_brand_push import looks_like_test_brand
    rows = [r for r in rows if not looks_like_test_brand({"brand_name": r.get("name"), "slug": r.get("slug")})]
    if creator_id and rows:
        rows = _thanos_order(conn, int(creator_id), rows)
    return {"plan": plan, "brands": _with_badges(cur, rows)[:limit]}


SEARCH_POOL = 60  # keyword matches handed to Thanos to order


def _thanos_order(conn, creator_id: int, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Matches in Thanos's order for this creator, with its fit and reply chance on each row."""
    from services.thanos import score_brands

    by_id = {int(r["id"]): r for r in rows}
    try:
        cards = score_brands(conn, creator_id, list(by_id), surface="search").brands
    except Exception as err:
        print(f"[browse] Thanos order skipped: {err}")
        conn.rollback()
        return rows
    out = []
    for c in cards:
        row = by_id.get(int(c["id"]))
        if row is not None:
            out.append({**row, "match_score": c.get("match_score"), "reply_chance": c.get("reply_chance"),
                        "reply_band": c.get("reply_band"), "reply_reason": c.get("reply_reason")})
    return out


def _with_badges(cur, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The reply badge cards show (Thanos evidence). Order is left as it is."""
    try:
        from services.brand_reply_signal import annotate, load_stats
        return annotate(rows, load_stats(cur))
    except Exception as err:
        print(f"[browse] reply badge skipped: {err}")
        return rows
