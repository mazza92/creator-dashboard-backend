"""One AI brand profiler for admin enrichment (single "AI enrich" and bulk), grounded in real evidence.

Why: brands scraped from the Meta ads library (and TikTok radar drafts) were inserted as 'beauty' and the old
enrichers never fixed them: the single enrich never asked for a category, and the bulk one asked Claude to guess
from the brand name alone ("Current category: beauty" anchored it) and only ran on brands with empty fields.

Now:
- evidence first: the website's readable text (Jina reader, raw HTML fallback), its meta description, Shopify
  product titles / types / prices when the store is on Shopify, and for radar brands the TikTok captions that
  tagged them;
- Claude returns JSON that matches a schema (structured outputs): category from our fixed list plus a confidence,
  and only facts it can read from the evidence (no invented testimonials, no guessed handles);
- the caller overwrites the category whenever confidence is high or medium, and fills the other fields.
"""

from __future__ import annotations

import json
import os
import re
from html import unescape
from typing import Any, Dict, List, Optional  # noqa
from urllib.parse import urlparse

import requests

MODEL = os.getenv("BRAND_PROFILE_MODEL", "claude-haiku-5-5")
CATEGORIES = [
    "skincare", "beauty", "haircare", "fragrance", "fashion", "jewelry", "activewear", "fitness", "wellness",
    "supplements", "food", "home", "pet", "baby", "tech", "gaming", "travel", "lifestyle", "luxury",
    "sustainable", "other",
]
# Our pr_brands table predates 'fragrance': store it as beauty.
STORE_AS = {"fragrance": "beauty"}
TONES = ["premium", "casual", "wellness", "functional", "luxury", "playful", "minimalist", "bold"]
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "category_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "category_reason": {"type": "string"},
        "is_consumer_brand": {"type": "boolean"},
        "hero_product": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "target_audience": {"type": ["string", "null"]},
        "tone": {"type": ["string", "null"], "enum": TONES + [None]},
        "price_point": {"type": ["integer", "null"]},
        "instagram_handle": {"type": ["string", "null"]},
        "tiktok_handle": {"type": ["string", "null"]},
        "youtube_handle": {"type": ["string", "null"]},
        "min_followers": {"type": ["integer", "null"]},
        "collaboration_type": {"type": ["string", "null"], "enum": ["gifted", "paid", "both", None]},
        "seo_title": {"type": ["string", "null"]},
        "seo_description": {"type": ["string", "null"]},
    },
    "required": ["category", "category_confidence", "category_reason", "is_consumer_brand", "hero_product",
                 "description", "target_audience", "tone", "price_point", "instagram_handle", "tiktok_handle",
                 "youtube_handle", "min_followers", "collaboration_type", "seo_title", "seo_description"],
    "additionalProperties": False,
}

SYSTEM = """You profile consumer brands for a directory that UGC creators use to pitch brands for PR boxes.
Work only from the evidence given (website text, product list, ad or TikTok captions). Never invent facts.

Category: pick the ONE category of what the brand mainly sells, judged from its products, not its name:
- skincare: serums, moisturisers, cleansers, SPF, masks. beauty: makeup/cosmetics, nails, multi-category beauty,
  personal care (deodorant, body care, oral care). haircare: shampoo, styling, hair tools. fragrance: perfume.
- fashion: clothing, shoes, bags, accessories. jewelry: jewellery and watches. activewear: sportswear.
- fitness: gym equipment and training gear. wellness: holistic health, sleep, mental wellness, wellness devices.
  supplements: vitamins, protein powder, collagen, gummies. food: food, snacks, drinks, coffee, baking mixes.
- home: homeware, kitchen, cleaning, decor, candles, car accessories. pet. baby: baby and kids products.
- tech: electronics and gadgets. gaming. travel: luggage and travel gear. lifestyle: gifts and mixed goods.
- luxury: high-end designer houses. sustainable: only when eco is the product itself (e.g. refill stations).
- other: B2B, services, marketplaces or anything that isn't a consumer product brand.
category_confidence: high = the products clearly show it; medium = likely; low = evidence too thin or mixed.

Other fields, null when the evidence doesn't show it:
- hero_product: a real product name seen in the evidence (prefer a bestseller or the first core product).
- description: one sentence, max 25 words: "<Brand> makes <products> for <customer>."
- target_audience: under 12 words. tone: one of the listed tones. price_point: typical single-item USD price.
- instagram/tiktok/youtube handles: only if a link or @handle for the brand appears in the evidence, without @.
- min_followers: 1000-5000 small indie, 5000-10000 mid DTC, 10000-50000 major brands.
- collaboration_type: gifted unless the evidence shows paid creator work.
- seo_title (max 60 chars) "<Brand> PR Contact & Gifting | Free Products for Creators";
  seo_description (max 155 chars) naming what they gift and who should pitch.
- is_consumer_brand: false for B2B, services, agencies, generic marketplaces or parked/empty sites."""


# ----- evidence -----------------------------------------------------------------------------------------------

def _domain(website: str) -> str:
    host = urlparse(website if "//" in (website or "") else f"https://{website}").netloc
    return host.lower()


def _html_text(html: str, limit: int = 4000) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html or "")
    text = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _meta(html: str) -> Dict[str, str]:
    out = {}
    for key, pattern in (("title", r"<title[^>]*>(.*?)</title>"),
                         ("description", r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']+)'),
                         ("og_title", r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)')):
        m = re.search(pattern, html or "", flags=re.I | re.S)
        if m:
            out[key] = re.sub(r"\s+", " ", unescape(m.group(1))).strip()[:300]
    return out


def _shopify_products(domain: str) -> List[Dict[str, Any]]:
    try:
        r = requests.get(f"https://{domain}/products.json?limit=30", headers={"User-Agent": _UA}, timeout=8)
        if r.status_code != 200:
            return []
        products = (r.json() or {}).get("products") or []
    except Exception:
        return []
    out = []
    for p in products[:20]:
        prices = [float(v.get("price") or 0) for v in (p.get("variants") or []) if v.get("price")]
        out.append({"title": (p.get("title") or "")[:90], "type": (p.get("product_type") or "")[:40],
                    "price": round(min(prices), 2) if prices else None})
    return out


def site_evidence(website: str, page_text: Optional[str] = None) -> Dict[str, Any]:
    """Readable homepage text, meta tags and Shopify products. ``page_text`` reuses a fetch the caller made."""
    ev: Dict[str, Any] = {"website": website}
    if not website:
        return ev
    domain = _domain(website)
    html = ""
    try:
        r = requests.get(f"https://{domain}", headers={"User-Agent": _UA, "Accept": "text/html"}, timeout=10,
                         allow_redirects=True)
        if r.status_code == 200:
            html = r.text or ""
    except Exception:
        pass
    ev["meta"] = _meta(html)
    text = (page_text or "").strip()
    if not text:
        try:
            jr = requests.get(f"https://r.jina.ai/{website}", timeout=20, headers={"User-Agent": _UA})
            if jr.status_code == 200:
                text = jr.text
        except Exception:
            pass
    ev["page_text"] = (text or _html_text(html))[:6000]
    ev["products"] = _shopify_products(domain)
    socials = {}
    ig = re.search(r"instagram\.com/([A-Za-z0-9._]{2,30})", html)
    tt = re.search(r"tiktok\.com/@([A-Za-z0-9._]{2,30})", html)
    if ig and ig.group(1).lower() not in ("p", "reel", "explore", "accounts"):
        socials["instagram"] = ig.group(1).strip(".")
    if tt:
        socials["tiktok"] = tt.group(1).strip(".")
    ev["social_links"] = socials
    return ev


def tiktok_evidence(conn, brand_id: int, limit: int = 8) -> List[str]:
    """Captions of TikTok posts our radar tied to this brand (gifted / paid / affiliate)."""
    cur = conn.cursor()
    try:
        cur.execute("SAVEPOINT brand_profile_tt")
        cur.execute("""
            SELECT v.caption FROM discovered_brands d
            JOIN brand_signals s ON s.discovered_brand_id = d.id
            JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
            WHERE d.brand_id = %s AND COALESCE(v.caption, '') <> ''
            ORDER BY v.created_at DESC LIMIT %s""", (brand_id, limit))
        rows = [str(r[0] if not isinstance(r, dict) else r["caption"])[:220] for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT brand_profile_tt")
        return rows
    except Exception as err:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT brand_profile_tt")
        except Exception:
            pass
        print(f"[brand profile] tiktok evidence skipped: {err}")
        return []


def has_evidence(ev: Dict[str, Any]) -> bool:
    return bool((ev.get("page_text") or "").strip() or ev.get("products") or ev.get("captions")
                or (ev.get("meta") or {}).get("description") or ev.get("existing_description"))


# ----- Claude --------------------------------------------------------------------------------------------------

def _prompt(brand: Dict[str, Any], ev: Dict[str, Any]) -> str:
    parts = [f"Brand: {brand.get('brand_name') or brand.get('name')}"]
    if ev.get("website"):
        parts.append(f"Website: {ev['website']}")
    if brand.get("tiktok_handle"):
        parts.append(f"TikTok: @{brand['tiktok_handle']}")
    if ev.get("meta"):
        parts.append("Page meta: " + json.dumps(ev["meta"], ensure_ascii=False))
    if ev.get("products"):
        parts.append("Products from the store (title | type | USD price):\n" + "\n".join(
            f"- {p['title']} | {p['type'] or '-'} | {p['price'] if p['price'] is not None else '-'}"
            for p in ev["products"]))
    if ev.get("social_links"):
        parts.append("Social links found on the site: " + json.dumps(ev["social_links"]))
    if ev.get("captions"):
        parts.append("TikTok captions from creators who tagged this brand:\n" + "\n".join(f"- {c}" for c in ev["captions"]))
    if ev.get("existing_description"):
        parts.append(f"Existing directory note: {ev['existing_description']}")
    if ev.get("page_text"):
        parts.append("Website text:\n<website>\n" + ev["page_text"] + "\n</website>")
    return "\n\n".join(parts)


_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=60)
    return _client


def profile_brand(brand: Dict[str, Any], ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Structured profile, or None when there's no evidence or the call fails."""
    import anthropic

    if not has_evidence(ev):
        return None
    try:
        response = _anthropic().messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"},
            messages=[{"role": "user", "content": _prompt(brand, ev)}],
        )
    except anthropic.RateLimitError as err:
        print(f"[brand profile] rate limited for {brand.get('brand_name')}: {err.message}")
        return None
    except anthropic.APIStatusError as err:
        print(f"[brand profile] API error {err.status_code} for {brand.get('brand_name')}: {err.message}")
        return None
    except anthropic.APIConnectionError as err:
        print(f"[brand profile] connection error for {brand.get('brand_name')}: {err}")
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        print(f"[brand profile] {brand.get('brand_name')}: stop_reason={response.stop_reason}")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        print(f"[brand profile] unreadable JSON for {brand.get('brand_name')}")
        return None


def store_category(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """The category to write, or None to keep the current one (low confidence or no result)."""
    if not result or result.get("category_confidence") not in ("high", "medium"):
        return None
    cat = result.get("category")
    if cat not in CATEGORIES:
        return None
    return STORE_AS.get(cat, cat)


def clean_handle(value: Any) -> Optional[str]:
    handle = str(value or "").strip().lstrip("@").strip("/")
    if not handle or handle.lower() in ("null", "none") or len(handle) > 60 or " " in handle:
        return None
    return handle


# ----- applying a profile ----------------------------------------------------------------------------------------

PROFILE_COLUMNS_DDL = """ALTER TABLE pr_brands
    ADD COLUMN IF NOT EXISTS category_confidence TEXT,
    ADD COLUMN IF NOT EXISTS category_reason TEXT,
    ADD COLUMN IF NOT EXISTS category_checked_at TIMESTAMPTZ"""

# Fields the profile can fill; overwrite=True (an admin pressing "AI enrich" on one brand) replaces them too.
_TEXT_FIELDS = {"hero_product": 255, "description": 400, "target_audience": 255, "seo_title": 255,
                "seo_description": 500}


def ensure_profile_columns(cursor) -> None:
    from services.db_ddl import ensure_columns
    ensure_columns(cursor, PROFILE_COLUMNS_DDL)


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def profile_updates(brand: Dict[str, Any], result: Optional[Dict[str, Any]], overwrite: bool = False) -> Dict[str, Any]:
    """Column -> value for pr_brands. Category always follows a confident profile; the rest fills blanks
    (or replaces them when ``overwrite``). Social handles only fill blanks: admins type those by hand."""
    if not result:
        return {}
    out: Dict[str, Any] = {"category_confidence": result.get("category_confidence"),
                           "category_reason": (result.get("category_reason") or "")[:300] or None}
    cat = store_category(result)
    if cat and cat != (brand.get("category") or "").lower():
        out["category"] = cat

    def take(col: str, value: Any) -> None:
        if _blank(value):
            return
        if overwrite or _blank(brand.get(col)):
            out[col] = value

    for col, cap in _TEXT_FIELDS.items():
        v = result.get(col)
        take(col, v.strip()[:cap] if isinstance(v, str) else None)
    if result.get("tone") in TONES:
        take("tone", result["tone"])
    try:
        price = int(result.get("price_point") or 0)
        if 0 < price < 20000:
            take("price_point", price)
    except (TypeError, ValueError):
        pass
    try:
        mf = int(result.get("min_followers") or 0)
        if mf:
            take("min_followers", max(1000, min(mf, 50000)))
    except (TypeError, ValueError):
        pass
    if result.get("collaboration_type") in ("gifted", "paid", "both"):
        take("collaboration_type", result["collaboration_type"])
    for col in ("instagram_handle", "tiktok_handle", "youtube_handle"):
        h = clean_handle(result.get(col))
        if h and _blank(brand.get(col)):
            out[col] = h
    return out


def gather_evidence(conn, brand: Dict[str, Any], page_text: Optional[str] = None) -> Dict[str, Any]:
    website = (brand.get("website") or "").strip()
    ev = site_evidence(website, page_text=page_text) if website else {"website": None}
    if brand.get("id"):
        ev["captions"] = tiktok_evidence(conn, int(brand["id"]))
    desc = (brand.get("description") or "").strip()
    if desc:
        ev["existing_description"] = desc[:400]
    return ev


def enrich(conn, brand: Dict[str, Any], overwrite: bool = False,
           page_text: Optional[str] = None) -> Dict[str, Any]:
    """Profile one brand and return {"updates": {...}, "profile": {...} | None, "evidence": [...]}. No write."""
    ev = gather_evidence(conn, brand, page_text=page_text)
    result = profile_brand(brand, ev)
    used = [k for k in ("page_text", "products", "captions", "existing_description") if ev.get(k)]
    return {"updates": profile_updates(brand, result, overwrite=overwrite), "profile": result, "evidence": used}


def write_updates(cursor, brand_id: int, updates: Dict[str, Any], checked: bool = True) -> None:
    cols = dict(updates)
    sets = [f"{c} = %s" for c in cols]
    if checked:
        sets.append("category_checked_at = NOW()")
    if not sets:
        return
    sets.append("updated_at = NOW()")
    cursor.execute(f"UPDATE pr_brands SET {', '.join(sets)} WHERE id = %s", list(cols.values()) + [brand_id])
