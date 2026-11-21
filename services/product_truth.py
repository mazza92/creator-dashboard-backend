"""Which product a pitch may name: only one we can trace to the brand itself, never one a model guessed.

Nov 2026: about 2,000 of 3,275 brands carry a hero_product written by an AI enrichment step ("their best-known
product... if unclear, use the category"), so a stored name is not proof the product exists. A pitch names a
product only when it is:
  - scraped from the brand's own shop (source meta_ads_library: products.json titles),
  - written in the brand's own description,
  - or found on the brand's website (homepage or Shopify products.json), checked once and cached.
Otherwise the pitch talks about the brand's range ("your skincare range"), which is always true.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

import requests

CHECK_TTL = timedelta(days=30)
FETCH_TIMEOUT = 4
_READY = False

# A "product" that is really a category or a catalogue ("Designer Bikinis and One-Piece Swimwear",
# "Everyday Essentials Bundle", "Charcoal Clothing Collection").
_GENERIC = re.compile(r"(?i)\b(collection|range|products|line|bundle|essentials|assortment|selection|various|"
                      r"catalog(ue)?|platform|marketplace|services?|classes|subscription)\b|\band\b|&")
_RANGE_WORDS = {
    "skincare": "skincare", "beauty": "beauty", "makeup": "makeup", "haircare": "haircare", "hair": "haircare",
    "fashion": "fashion", "activewear": "activewear", "fitness": "fitness", "wellness": "wellness",
    "supplements": "supplements", "food": "food", "beverages": "drinks", "home": "home", "pet": "pet",
    "tech": "tech", "gaming": "gaming", "jewelry": "jewellery", "jewellery": "jewellery", "fragrance": "fragrance",
    "baby": "baby", "kids": "kids", "travel": "travel", "lifestyle": "",
}


def range_phrase(category: Any) -> str:
    """The always-true fallback: 'your skincare range', or 'your products'."""
    key = str(category or "").strip().lower()
    word = next((v for k, v in _RANGE_WORDS.items() if k in key), "")
    return f"your {word} range" if word else "your products"


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _tokens(name: str) -> list:
    return [t for t in _norm(name).split() if len(t) >= 3]


def name_in_text(name: str, text: str) -> bool:
    """The product name appears in the text: the whole name, or every significant word of it."""
    n, t = _norm(name), _norm(text)
    if not n or not t:
        return False
    if n in t:
        return True
    toks = _tokens(name)
    return len(toks) >= 2 and all(re.search(rf"\b{re.escape(w)}\b", t) for w in toks)


def looks_generic(name: str) -> bool:
    return bool(_GENERIC.search(name or "")) or len((name or "").split()) > 7


def _ensure(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS brand_product_checks (
        brand_id INTEGER PRIMARY KEY, product TEXT, ok BOOLEAN NOT NULL, checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    conn.commit()
    _READY = True


def _site_text(website: str, http: Callable = requests.get) -> str:
    site = str(website or "").strip()
    if not site:
        return ""
    if not site.startswith("http"):
        site = "https://" + site
    base = re.match(r"https?://[^/]+", site)
    base = base.group(0) if base else site
    chunks = []
    for url in (f"{base}/products.json?limit=250", base):
        try:
            resp = http(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 (compatible; PitchCheck)"})
            if getattr(resp, "status_code", 0) == 200:
                chunks.append(str(getattr(resp, "text", "") or "")[:400000])
        except Exception:
            continue
    return " ".join(chunks)


def verified_product(conn, brand: Dict[str, Any], http: Callable = requests.get, now: Optional[datetime] = None,
                     fetch: bool = True) -> str:
    """The brand's hero product when we can trace it to the brand, else ''. With fetch=False only the cached
    website check counts (no network, nothing written): for previews that must stay fast."""
    name = " ".join(str(brand.get("hero_product") or "").split())
    if not name or looks_generic(name):
        return ""
    if brand.get("source") == "meta_ads_library":
        return name  # title scraped from the brand's own shop
    if name_in_text(name, brand.get("description") or ""):
        return name
    bid = brand.get("id")
    if not bid:
        return ""
    now = now or datetime.now(timezone.utc)
    try:
        _ensure(conn)
        cur = conn.cursor()
        cur.execute("SELECT product, ok, checked_at FROM brand_product_checks WHERE brand_id = %s", (int(bid),))
        row = cur.fetchone()
        if row and row[0] == name and row[2] and (row[2] if row[2].tzinfo else row[2].replace(tzinfo=timezone.utc)) > now - CHECK_TTL:
            return name if row[1] else ""
        if not fetch:
            return ""
        ok = name_in_text(name, _site_text(brand.get("website") or "", http=http))
        cur.execute("""INSERT INTO brand_product_checks (brand_id, product, ok, checked_at) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (brand_id) DO UPDATE SET product = EXCLUDED.product, ok = EXCLUDED.ok,
                       checked_at = EXCLUDED.checked_at""", (int(bid), name, ok, now))
        conn.commit()
        return name if ok else ""
    except Exception as err:
        print(f"[product_truth] check skipped brand={bid}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return ""


def pitch_product(conn, brand: Dict[str, Any], **kw) -> Dict[str, Any]:
    """{'name': what the pitch calls it, 'verified': bool}."""
    name = verified_product(conn, brand, **kw)
    return {"name": name, "verified": True} if name else {"name": range_phrase(brand.get("category")), "verified": False}
