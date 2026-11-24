"""UGC facts for one brand's public page, from real signals only (never a guess presented as a fact):

- hot / seenRunningAdsOn: the brand was found (or re-seen) running ads by our Meta Ad Library crawl, and when.
- creatorStyleAds + adExamples: those ads were classified creator-style (first person, testimonial, #ad); only then
  do we show their opening lines as UGC ad examples. Promo copy ("Buy 1 Get 1") is not a UGC example.
- liveBriefs: paid UGC briefs from this brand that are open on Newcollab right now (title + pay, no apply links).
"""
import re
from typing import Any, Dict, List, Optional

from services.meta_ads_signal import HOT_FRESH_DAYS, strip_internal
from services.public_showcase import MIN_SHOWN_PAY, _clean, _pay_value, _tidy_title

MAX_EXAMPLES = 2
MAX_BRIEFS = 3


# Ad Library labels around the ad's own words when a sample was saved as one line: "Brand Sponsored I used to..."
_SPONSORED = re.compile(r"^(?:sponsored|sponsoris[ée]|gesponsert|patrocinado|sponsorizzato|gesponsord)\b[\s:·-]*",
                        re.IGNORECASE)


def _without_labels(text: str, brand_name: str = "") -> str:
    s = text.strip()
    name = (brand_name or "").strip()
    if name and s.lower().startswith(name.lower()):
        s = s[len(name):].lstrip(" :·-")
    return _SPONSORED.sub("", s).strip()


# Zero-width and other invisible characters some ads pad their text with.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
# Promo copy is not a UGC example: sales, discounts, launches.
_PROMO = re.compile(r"(?i)\b(sale|% ?off|discount|promo code|use code|free shipping|shop now|black friday|"
                    r"cyber monday|limited time|meet our new|introducing|new arrival|buy \d|bogo)\b")


def _first_line(text: str, limit: int = 160, brand_name: str = "") -> str:
    s = " ".join(_INVISIBLE.sub("", str(text or "")).split())
    s = _without_labels(strip_internal(s), brand_name)
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
                        line = _first_line(str(s or ""), brand_name=brand.get("brand_name") or "")
                        if len(line) >= 20 and line not in examples and not _PROMO.search(line):
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


# --- Category hubs: "<category> brands looking for UGC creators" -------------------------------------------------

HUB_MIN_BRANDS = 10
HUB_LIMIT = 60

_SIGNALS_SQL = """
    SELECT b.id, b.brand_name, b.slug, b.logo_url, LOWER(COALESCE(b.category, '')) AS category,
           (b.source = 'meta_ads_library'
              AND GREATEST(b.created_at::date, COALESCE(s.ads_last_seen, DATE '1900-01-01')) >= CURRENT_DATE - %(fresh)s)
              AS hot,
           CASE WHEN b.source = 'meta_ads_library'
                THEN GREATEST(b.created_at::date, COALESCE(s.ads_last_seen, DATE '1900-01-01')) END AS seen_on,
           COALESCE(s.ugc_style, FALSE) AS creator_ads,
           (to_jsonb(b) ->> 'gifting_confirmed_at') IS NOT NULL AS gifting,
           EXISTS (SELECT 1 FROM opportunities o WHERE o.pr_brand_id = b.id AND o.status = 'live'
                     AND (o.closes_at IS NULL OR o.closes_at > NOW())) AS brief
    FROM pr_brands b LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
    WHERE COALESCE(b.status, 'published') = 'published' AND NULLIF(TRIM(b.slug), '') IS NOT NULL
"""


def _signal_rows(cur, category: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM ({_SIGNALS_SQL}) x WHERE (hot OR creator_ads OR gifting OR brief)"
    params: Dict[str, Any] = {"fresh": HOT_FRESH_DAYS}
    if category:
        sql += " AND category = %(cat)s"
        params["cat"] = category.lower()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]


def _strength(r: Dict[str, Any]):
    """Open brief > creator-style ads > seen running ads (newest first) > confirmed gifting."""
    seen = r.get("seen_on")
    return (not r.get("brief"), not r.get("creator_ads"), not r.get("hot"),
            -(seen.toordinal() if seen and r.get("hot") else 0), not r.get("gifting"), str(r.get("brand_name") or ""))


def ugc_hub(cur, category: str, limit: int = HUB_LIMIT) -> Dict[str, Any]:
    rows = sorted(_signal_rows(cur, category), key=_strength)
    brands = [{
        "name": _clean(r.get("brand_name"), 60),
        "slug": r.get("slug"),
        "logo": r.get("logo_url") or None,
        "hot": bool(r.get("hot")),
        "seenOn": r["seen_on"].isoformat() if r.get("seen_on") and r.get("hot") else None,
        "creatorAds": bool(r.get("creator_ads")),
        "gifting": bool(r.get("gifting")),
        "brief": bool(r.get("brief")),
    } for r in rows[:limit]]
    return {
        "category": category.lower(),
        "total": len(rows),
        "counts": {k: sum(1 for r in rows if r.get(f)) for k, f in
                   (("hot", "hot"), ("creatorAds", "creator_ads"), ("gifting", "gifting"), ("briefs", "brief"))},
        "indexable": len(rows) >= HUB_MIN_BRANDS,
        "brands": brands,
    }


def ugc_hub_index(cur) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for r in _signal_rows(cur):
        cat = r.get("category") or ""
        if cat and cat != "other":
            counts[cat] = counts.get(cat, 0) + 1
    return [{"category": c, "total": n} for c, n in sorted(counts.items(), key=lambda kv: -kv[1])
            if n >= HUB_MIN_BRANDS]


# --- Monthly data pages (Nov 2026 UGC pivot) ---------------------------------------------------------------------

def _hub_item(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _clean(r.get("brand_name"), 60),
        "slug": r.get("slug"),
        "logo": r.get("logo_url") or None,
        "category": r.get("category") or None,
        "hot": bool(r.get("hot")),
        "seenOn": r["seen_on"].isoformat() if r.get("seen_on") and r.get("hot") else None,
        "creatorAds": bool(r.get("creator_ads")),
        "gifting": bool(r.get("gifting")),
        "brief": bool(r.get("brief")),
    }


def brands_running_ads(cur, limit: int = HUB_LIMIT) -> Dict[str, Any]:
    """'Brands running UGC ads right now': every category, brands seen running ads in the last 30 days or running
    creator-style ads, strongest and freshest first."""
    rows = sorted([r for r in _signal_rows(cur) if r.get("hot") or r.get("creator_ads")], key=_strength)
    cats: Dict[str, int] = {}
    for r in rows:
        c = r.get("category") or ""
        if c and c != "other":
            cats[c] = cats.get(c, 0) + 1
    return {
        "total": len(rows),
        "counts": {"hot": sum(1 for r in rows if r.get("hot")),
                   "creatorAds": sum(1 for r in rows if r.get("creator_ads")),
                   "briefs": sum(1 for r in rows if r.get("brief"))},
        "categories": [{"category": c, "total": n} for c, n in sorted(cats.items(), key=lambda kv: -kv[1])],
        "brands": [_hub_item(r) for r in rows[:limit]],
    }


REPLYING_MIN_REPLIES = 2  # one reply is an anecdote; quote brands from 2 creator replies (or a roster pick) up


def brands_replying(cur, limit: int = HUB_LIMIT) -> Dict[str, Any]:
    """'Brands replying to UGC creators': brands that answered creators who pitched them on Newcollab, from what
    creators logged (services/brand_reply_signal). Counts of creators only, never who."""
    from services.brand_reply_signal import classify, load_stats

    stats = load_stats(cur)
    keep = {bid: s for bid, s in stats.items()
            if classify(s) == "replies"
            and (int(s.get("replied") or 0) >= REPLYING_MIN_REPLIES or int(s.get("selected") or 0) >= 1)}
    if not keep:
        return {"total": 0, "brands": [], "categories": []}
    cur.execute(
        """
        SELECT id, brand_name, slug, logo_url, LOWER(COALESCE(category, '')) AS category
        FROM pr_brands
        WHERE id = ANY(%s) AND COALESCE(status, 'published') = 'published' AND NULLIF(TRIM(slug), '') IS NOT NULL
        """,
        (list(keep.keys()),),
    )
    cols = [d[0] for d in cur.description]
    rows = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]
    items = []
    for r in rows:
        s = keep.get(int(r["id"])) or {}
        pitched, replied, selected = (int(s.get(k) or 0) for k in ("pitched", "replied", "selected"))
        items.append({
            "name": _clean(r.get("brand_name"), 60),
            "slug": r.get("slug"),
            "logo": r.get("logo_url") or None,
            "category": r.get("category") or None,
            "replied": replied,
            "pitched": pitched,
            "selected": selected,
            "replyRate": round(100 * replied / pitched) if pitched else None,
        })
    # Most creator replies first (the proof a creator cares about); roster picks and reply rate break ties.
    items.sort(key=lambda i: (-i["replied"], -i["selected"], -(i["replyRate"] or 0), str(i["name"])))
    cats: Dict[str, int] = {}
    for i in items:
        c = i.get("category") or ""
        if c and c != "other":
            cats[c] = cats.get(c, 0) + 1
    return {
        "total": len(items),
        "categories": [{"category": c, "total": n} for c, n in sorted(cats.items(), key=lambda kv: -kv[1])],
        "brands": items[:limit],
    }
