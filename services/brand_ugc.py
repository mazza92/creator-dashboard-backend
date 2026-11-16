"""UGC facts for one brand's public page, from real signals only (never a guess presented as a fact):

- hot / seenRunningAdsOn: the brand was found (or re-seen) running ads by our Meta Ad Library crawl, and when.
- creatorStyleAds + adExamples: those ads were classified creator-style (first person, testimonial, #ad); only then
  do we show their opening lines as UGC ad examples. Promo copy ("Buy 1 Get 1") is not a UGC example.
- liveBriefs: paid UGC briefs from this brand that are open on Newcollab right now (title + pay, no apply links).
"""
from typing import Any, Dict, List, Optional

from services.meta_ads_signal import HOT_FRESH_DAYS, strip_internal
from services.public_showcase import MIN_SHOWN_PAY, _clean, _pay_value, _tidy_title

MAX_EXAMPLES = 2
MAX_BRIEFS = 3


def _first_line(text: str, limit: int = 160) -> str:
    s = strip_internal(text)
    for stop in (". ", "! ", "? "):
        i = s.find(stop)
        if 20 <= i <= limit:
            return s[: i + 1].strip()
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


def brand_ugc(cur, brand: Dict[str, Any]) -> Dict[str, Any]:
    """``brand`` needs id, source and created_at. Returns the ``ugc`` block of the public brand payload."""
    out: Dict[str, Any] = {"hot": False, "seenRunningAdsOn": None, "creatorStyleAds": False, "adExamples": [],
                           "liveBriefs": []}
    brand_id = brand.get("id")
    if not brand_id:
        return out
    try:
        if brand.get("source") == "meta_ads_library":
            cur.execute("""
                SELECT GREATEST(b.created_at::date, COALESCE(s.ads_last_seen, DATE '1900-01-01')) AS seen_on,
                       s.ugc_style, s.ad_samples,
                       GREATEST(b.created_at::date, COALESCE(s.ads_last_seen, DATE '1900-01-01'))
                         >= CURRENT_DATE - %s AS fresh
                FROM pr_brands b LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
                WHERE b.id = %s
            """, (HOT_FRESH_DAYS, brand_id))
            row = cur.fetchone()
            if row:
                row = row if isinstance(row, dict) else dict(zip([d[0] for d in cur.description], row))
                out["seenRunningAdsOn"] = row["seen_on"].isoformat() if row.get("seen_on") else None
                out["hot"] = bool(row.get("fresh"))
                out["creatorStyleAds"] = bool(row.get("ugc_style"))
                if out["creatorStyleAds"]:
                    samples = row.get("ad_samples") or []
                    examples: List[str] = []
                    for s in samples if isinstance(samples, list) else []:
                        line = _first_line(str(s or ""))
                        if len(line) >= 20 and line not in examples:
                            examples.append(line)
                    out["adExamples"] = examples[:MAX_EXAMPLES]
        cur.execute("""
            SELECT product_name, listing_brief ->> 'pay' AS pay, published_at
            FROM opportunities
            WHERE pr_brand_id = %s AND status = 'live' AND (closes_at IS NULL OR closes_at > NOW())
            ORDER BY published_at DESC NULLS LAST LIMIT %s
        """, (brand_id, MAX_BRIEFS))
        briefs = []
        for r in cur.fetchall():
            r = r if isinstance(r, dict) else dict(zip([d[0] for d in cur.description], r))
            title = _tidy_title(_clean(r.get("product_name"), 90))
            if not title:
                continue
            pay = _clean(r.get("pay"), 24)
            briefs.append({"title": title, "pay": pay if _pay_value(pay) >= MIN_SHOWN_PAY else None})
        out["liveBriefs"] = briefs
    except Exception as e:  # the brand page must render without this block
        print(f"[brand_ugc] skipped brand={brand_id}: {e}")
        try:
            cur.connection.rollback()
        except Exception:
            pass
    return out
