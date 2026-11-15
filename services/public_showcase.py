"""Small, public, read-only teasers of the paid product for newcollab.co feature pages (rendered on the server so
crawlers see fresh data every day). Never returns contacts, apply links, ad library URLs or where a gig was found."""
import re
from typing import Any, Dict, List

GIG_LIMIT = 8
HOOK_LIMIT = 6
HOT_LIMIT = 12

_UNKNOWN_BRAND = re.compile(r"(?i)^(unknown brand|paid ugc offer|)$")
_SOURCE_NAMES = re.compile(r"(?i)\b(sideshift|upwork|fiverr|craigslist|indeed|reddit|backstage|insense|billo|joinbrands)\b")


def _rows(cur) -> List[Dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _clean(text: Any, limit: int = 140) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = _SOURCE_NAMES.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ·-")
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


_QUOTES = "\"'“”‘’«»"


def _unquote(text: str) -> str:
    return text.strip().strip(_QUOTES).strip()


def _tidy_title(text: str) -> str:
    """Scraped titles are sometimes cut mid-phrase ("Weight Loss UGC | Hearty ($500"): drop a dangling bracket
    clause and trailing separators."""
    s = re.sub(r"\s*\([^)]*$", "", text)
    s = re.sub(r"[\s,·|:;\-–—]+$", "", s)
    return s.strip()


def _pay_value(pay: Any) -> float:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", str(pay or ""))
    try:
        return float(m.group(1).replace(",", "")) if m else 0.0
    except ValueError:
        return 0.0


MIN_SHOWN_PAY = 20  # smaller figures in briefs are CPM or per-post bonuses, not what a video pays


def _named_like_a_brand(name: str, slug: str) -> bool:
    """'Ruby Hammer' over 'Chosensupps': names generated from a domain read badly on a public page."""
    flat = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return " " in (name or "").strip() or flat != re.sub(r"[^a-z0-9]", "", (slug or "").lower())


def latest_gigs(cur, limit: int = GIG_LIMIT) -> List[Dict[str, Any]]:
    cur.execute("""
        SELECT id, brand_name, product_name, content_types, shipping_regions, published_at,
               listing_brief ->> 'pay' AS pay
        FROM opportunities
        WHERE status = 'live' AND listing_brief IS NOT NULL
          AND (closes_at IS NULL OR closes_at > NOW())
        ORDER BY published_at DESC NULLS LAST, id DESC
        LIMIT %s
    """, (limit * 5,))
    out = []
    for r in _rows(cur):
        brand = _clean(r.get("brand_name"), 60)
        title = _tidy_title(_clean(r.get("product_name"), 90))
        if _UNKNOWN_BRAND.match(brand) or not title:
            continue
        pay = _clean(r.get("pay"), 24)
        out.append({
            "brand": brand,
            "title": title,
            "pay": pay if _pay_value(pay) >= MIN_SHOWN_PAY else None,
            "formats": [str(x) for x in (r.get("content_types") or [])][:3],
            "regions": [str(x) for x in (r.get("shipping_regions") or [])][:4],
            "posted": r["published_at"].date().isoformat() if r.get("published_at") else None,
        })
    # Newest first, but lead with briefs that show a real per-video fee.
    out.sort(key=lambda g: g["pay"] is None)
    return out[:limit]


def latest_hooks(cur, limit: int = HOOK_LIMIT) -> List[Dict[str, Any]]:
    cur.execute("""
        SELECT DISTINCT ON (niche) niche, day, pattern, text, score
        FROM ugc_pulse_items
        WHERE kind = 'hook' AND niche <> 'all' AND day > CURRENT_DATE - 7 AND COALESCE(text, '') <> ''
        ORDER BY niche, day DESC, score DESC NULLS LAST
    """)
    rows = sorted(_rows(cur), key=lambda r: (r.get("day"), r.get("score") or 0), reverse=True)[:limit]
    return [{
        "niche": r.get("niche"),
        "hook": _unquote(_clean(r.get("text"), 160)),
        "pattern": _clean(r.get("pattern"), 60) or None,
        "day": r["day"].isoformat() if r.get("day") else None,
    } for r in rows]


def hot_brands(cur, limit: int = HOT_LIMIT) -> List[Dict[str, Any]]:
    cur.execute("""
        SELECT b.brand_name, b.slug, LOWER(COALESCE(b.category, '')) AS category, b.logo_url,
               s.ads_last_seen
        FROM pr_brands b
        LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
        WHERE b.source = 'meta_ads_library' AND COALESCE(b.status, 'published') = 'published'
          AND NULLIF(TRIM(b.slug), '') IS NOT NULL
        ORDER BY s.ads_last_seen DESC NULLS LAST, b.id DESC
        LIMIT %s
    """, (limit * 6,))
    rows = _rows(cur)
    rows.sort(key=lambda r: not _named_like_a_brand(r.get("brand_name"), r.get("slug")))
    return [{
        "name": _clean(r.get("brand_name"), 60),
        "slug": r.get("slug"),
        "category": r.get("category") or None,
        "logo": r.get("logo_url") or None,
        "ads_last_seen": r["ads_last_seen"].isoformat() if r.get("ads_last_seen") else None,
    } for r in rows[:limit]]


def counts(cur) -> Dict[str, int]:
    cur.execute("""
        SELECT (SELECT COUNT(*) FROM opportunities WHERE status = 'live'
                  AND (closes_at IS NULL OR closes_at > NOW())) AS live_gigs,
               (SELECT COUNT(*) FROM pr_brands WHERE source = 'meta_ads_library'
                  AND COALESCE(status, 'published') = 'published') AS hot_brands,
               (SELECT COUNT(*) FROM pr_brands WHERE status = 'published') AS brands
    """)
    row = cur.fetchone()
    return {"live_gigs": int(row[0] or 0), "hot_brands": int(row[1] or 0), "brands": int(row[2] or 0)}


def showcase(conn) -> Dict[str, Any]:
    out: Dict[str, Any] = {"gigs": [], "hooks": [], "hot_brands": [], "counts": {}}
    for key, fn in (("gigs", latest_gigs), ("hooks", latest_hooks), ("hot_brands", hot_brands), ("counts", counts)):
        cur = conn.cursor()
        try:
            out[key] = fn(cur)
        except Exception as e:  # one missing table must not blank the others
            print(f"[showcase] {key} skipped: {e}")
            conn.rollback()
        finally:
            cur.close()
    return out
