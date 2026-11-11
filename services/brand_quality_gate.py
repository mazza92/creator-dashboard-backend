"""Quality gate for Meta-ads brands before a creator ever sees them as "hot".

A brand passes when every check holds:
- active_ads:  >= 2 live ads in the Ad Library, and we saw them live in the last 30 days (services.meta_ads_signal)
- ugc_style:   those ads look like creator / UGC ads
- category:    the AI enricher checked it (category_checked_at), with high/medium confidence, and the category is
               one of our canonical niches (not 'other')
- inbox:       contact email verified valid or catch-all, never bounced
- safety:      nothing adult, cannabis/CBD, gambling, crypto, vape/nicotine, prescription or weight-loss drugs
- real_store:  a website on the brand's own domain (no marketplace, link-in-bio or store-builder preview links,
               no dropship tells)

An admin approve / reject (routes/admin_brand_quality.py) overrides the checks both ways. Failing brands stay in the
directory; they are only kept out of the creator hotlist and the daily hot brand.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from psycopg2.extras import RealDictCursor

MIN_LIVE_ADS = 2
LIVE_WITHIN_DAYS = 30
OK_EMAIL = ("valid", "catch-all")
CONFIDENT = ("high", "medium")
CHECK_ORDER = ("active_ads", "ugc_style", "category", "inbox", "safety", "real_store")
# Creator-style ads rank a brand higher and show in its reason; most Meta ads are promo copy, so it isn't required.
REQUIRED = ("active_ads", "category", "inbox", "safety", "real_store")
# Our Meta scraper only finds brands while their ads run: until the Ad Library backfill has checked a brand,
# being found in the last DISCOVERY_WITHIN_DAYS counts as live ads.
DISCOVERY_WITHIN_DAYS = 60

DDL = """
CREATE TABLE IF NOT EXISTS brand_quality_checks (
    brand_id INTEGER PRIMARY KEY,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    auto_passed BOOLEAN,
    run_id TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    override TEXT,
    override_note TEXT,
    override_by TEXT,
    override_at TIMESTAMPTZ
)
"""
_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_index

    cur = conn.cursor()
    cur.execute("SELECT to_regclass('brand_quality_checks') IS NOT NULL")
    if not cur.fetchone()[0]:
        cur.execute(DDL)
    ensure_index(cur, "CREATE INDEX IF NOT EXISTS idx_brand_quality_run ON brand_quality_checks (run_id, passed)")
    conn.commit()
    _READY = True


# ----- brand safety ------------------------------------------------------------------------------------------------

_UNSAFE_WORDS = [
    ("adult", r"adult (toys?|content|store|shop|entertainment|products?)|adults only|\bxxx\b|porn\w*|onlyfans|"
              r"sex ?toys?|vibrators?|\bdildos?\b|\bescorts?\b|\bfetish\b|\bbdsm\b|\bcamgirls?\b|\bnsfw\b"),
    ("cannabis", r"\bcbd\b|\bthc\b|cannabis|marijuana|\bdelta[- ]?(8|9|10)\b|\bhhc\b|\bkratom\b|"
                 r"hemp (extract|flower|gummies)|full[- ]spectrum hemp|\bdispensary\b|\bedibles\b"),
    ("gambling", r"gambl\w*|casinos?|sports? ?betting|\bsportsbook\b|\bbetting\b|\bpoker\b|\broulette\b|"
                 r"slot machines?|\blottery\b"),
    ("crypto", r"\bcrypto\w*|bitcoin|\bnfts?\b|\bblockchain\b|\bdefi\b|\btoken presale\b|memecoin"),
    ("vape_nicotine", r"\bvap(e|es|ing|or|ors)\b|\be-?cig\w*|\be-?liquids?\b|nicotine|tobacco products|"
                      r"chewing tobacco|\bcigars?\b|cigarettes?|\bsnus\b|\bhookah\b"),
    ("prescription_drugs", r"prescription (drugs?|medication|only)|\brx[- ]only\b|semaglutide|tirzepatide|ozempic|"
                           r"wegovy|mounjaro|zepbound|\bglp-?1\b|phentermine|"
                           r"weight[- ]loss (injections?|shots?|drugs?|medication)|"
                           r"(?<!without )(?<!no )(?<!free of )\binjections?\b|\binjectables?\b|"
                           r"\bpeptide (injections?|vials?)\b|\bsarms?\b|\bsteroids?\b(?![- ]free)|\bhcg\b|"
                           r"\bviagra\b|\bcialis\b|\bfinasteride\b|\bminoxidil (tablets|pills)\b|\bketamine\b|"
                           r"\bpsilocybin\b|magic mushrooms?"),
]
_UNSAFE = [(k, re.compile(rx, re.I)) for k, rx in _UNSAFE_WORDS]
# Substrings that are unambiguous inside a domain name (no word boundaries there). 'thc' is not one: healtHCare.
_UNSAFE_DOMAIN = {"cbd": "cannabis", "cannabis": "cannabis", "kratom": "cannabis", "delta8": "cannabis",
                  "vape": "vape_nicotine", "nicotine": "vape_nicotine", "casino": "gambling", "betting": "gambling",
                  "porn": "adult", "xxx": "adult", "onlyfans": "adult", "crypto": "crypto", "bitcoin": "crypto",
                  "ozempic": "prescription_drugs", "semaglutide": "prescription_drugs", "glp1": "prescription_drugs"}


def safety_flags(*texts: Any, website: Optional[str] = None) -> List[str]:
    """Unsafe topics found in the brand's name, category, description, product or ad text, or its domain."""
    blob = " ".join(str(t) for t in texts if t)
    flags: List[str] = []
    for key, rx in _UNSAFE:
        m = rx.search(blob)
        if m and key not in flags:
            flags.append(f"{key}: '{m.group(0).strip()}'")
    host = _host(website)
    for word, key in _UNSAFE_DOMAIN.items():
        if word in host.replace("-", "") and not any(f.startswith(key) for f in flags):
            flags.append(f"{key}: domain '{host}'")
    return flags


# ----- real store --------------------------------------------------------------------------------------------------

_NOT_OWN_DOMAIN = (
    "amazon.", "amzn.", "etsy.com", "ebay.", "walmart.com", "target.com", "temu.com", "aliexpress.", "alibaba.com",
    "shein.com", "tiktok.com", "instagram.com", "facebook.com", "fb.com", "youtube.com", "linktr.ee", "linktree",
    "beacons.ai", "stan.store", "linkin.bio", "lnk.bio", "bio.link", "campsite.bio", "hoo.be", "carrd.co",
    "gumroad.com", "myshopify.com", "shopify.com", "shop.app", "shopifypreview.com", "wixsite.com", "square.site",
    "squarespace.com", "bigcartel.com", "webflow.io", "vercel.app", "netlify.app", "godaddysites.com",
    "mystrikingly.com", "sites.google.com", "notion.site", "weebly.com", "wordpress.com", "blogspot.com",
    "ueniweb.com", "company.site", "mybigcommerce.com", "storenvy.com", "depop.com", "poshmark.com",
)
_DROPSHIP = re.compile(r"dropship\w*|aliexpress|\btemu\b|\bcjdropshipping\b|ships? in 2-?4 weeks|"
                       r"\bviral (gadget|product)s?\b|tiktok made me buy it", re.I)


def _host(website: Optional[str]) -> str:
    url = (website or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _platform_match(host: str, bad: str) -> bool:
    if bad.endswith("."):  # any TLD: amazon.com, amazon.co.uk, smile.amazon.com
        return ("." + host).find("." + bad) >= 0
    if "." not in bad:  # a word anywhere: linktree
        return bad in host
    return host == bad or host.endswith("." + bad)


def store_problem(website: Optional[str], *texts: Any, hero_product: Optional[str] = None,
                  description: Optional[str] = None) -> Optional[str]:
    host = _host(website)
    if not host or "." not in host:
        return "no website"
    if any(_platform_match(host, bad) for bad in _NOT_OWN_DOMAIN):
        return f"not its own store domain ({host})"
    blob = " ".join(str(t) for t in (description, hero_product) + texts if t)
    m = _DROPSHIP.search(blob)
    if m:
        return f"dropship tell: '{m.group(0)}'"
    if not (hero_product or "").strip() and not (description or "").strip():
        return "no product or description on file (looks like a one-page store)"
    return None


# ----- evaluation --------------------------------------------------------------------------------------------------

def _as_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _samples(sig: Dict[str, Any]) -> List[str]:
    raw = sig.get("ad_samples") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    return [str(s) for s in raw if s]


def evaluate_row(brand: Dict[str, Any], sig: Optional[Dict[str, Any]] = None,
                 override: Optional[str] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """Pure gate: brand row (+ to_jsonb extras) and its ad signal row -> {pass, reasons, checks, auto_pass}."""
    from services.creator_niches import NICHE_IDS

    today = today or datetime.now(timezone.utc).date()
    sig = sig or {}
    checks: Dict[str, Dict[str, Any]] = {}

    count = sig.get("active_ad_count")
    last = _as_date(sig.get("ads_last_seen"))
    found = _as_date(brand.get("created_at")) if brand.get("source") == "meta_ads_library" else None
    if count is None and found and (today - found).days < DISCOVERY_WITHIN_DAYS:
        checks["active_ads"] = {"ok": True, "detail": f"ads running when found on {found}"}
    elif count is None:
        checks["active_ads"] = {"ok": False, "detail": "ad library not checked yet"}
    elif int(count) < MIN_LIVE_ADS:
        checks["active_ads"] = {"ok": False, "detail": f"{int(count)} live ad(s), needs {MIN_LIVE_ADS}+"}
    elif not last or (today - last).days >= LIVE_WITHIN_DAYS:
        checks["active_ads"] = {"ok": False, "detail": f"ads last seen {last or 'never'} (over {LIVE_WITHIN_DAYS} days)"}
    else:
        checks["active_ads"] = {"ok": True, "detail": f"{int(count)} live ads, seen {last}"}

    ugc = sig.get("ugc_style")
    checks["ugc_style"] = {"ok": ugc is True, "required": False,
                           "detail": sig.get("ugc_style_reason") or ("not classified yet" if ugc is None else "")}
    if ugc is False:
        checks["ugc_style"]["detail"] = "not creator-style: " + (sig.get("ugc_style_reason") or "")

    cat = str(brand.get("category") or "").strip().lower()
    conf = str(brand.get("category_confidence") or "").strip().lower()
    if not brand.get("category_checked_at"):
        checks["category"] = {"ok": False, "detail": "AI enricher hasn't checked the category"}
    elif conf not in CONFIDENT:
        checks["category"] = {"ok": False, "detail": f"category confidence {conf or 'missing'}"}
    elif cat not in NICHE_IDS:
        checks["category"] = {"ok": False, "detail": f"category '{cat or 'empty'}' is not a creator niche"}
    else:
        checks["category"] = {"ok": True, "detail": f"{cat} ({conf})"}

    status = str(brand.get("email_status") or "").strip().lower()
    bounces = int(brand.get("email_bounce_count") or 0)
    if not (brand.get("contact_email") or "").strip():
        checks["inbox"] = {"ok": False, "detail": "no contact email"}
    elif status not in OK_EMAIL:
        checks["inbox"] = {"ok": False, "detail": f"email status {status or 'unverified'}"}
    elif bounces:
        checks["inbox"] = {"ok": False, "detail": f"email bounced {bounces}x"}
    else:
        checks["inbox"] = {"ok": True, "detail": status}

    flags = safety_flags(brand.get("brand_name"), brand.get("category"), brand.get("description"),
                         brand.get("hero_product"), *_samples(sig), website=brand.get("website"))
    checks["safety"] = {"ok": not flags, "detail": "; ".join(flags) if flags else "clean"}

    problem = store_problem(brand.get("website"), *_samples(sig), hero_product=brand.get("hero_product"),
                            description=brand.get("description"))
    checks["real_store"] = {"ok": problem is None, "detail": problem or _host(brand.get("website"))}

    auto = all(checks[k]["ok"] for k in REQUIRED)
    reasons = [f"{k}: {checks[k]['detail']}" for k in REQUIRED if not checks[k]["ok"]]
    passed = auto
    if override == "approved":
        passed = True
        reasons = ["manual approve"] + reasons
    elif override == "rejected":
        passed = False
        reasons = ["manual reject"] + reasons
    return {"pass": passed, "auto_pass": auto, "reasons": reasons, "checks": checks, "override": override}


_BRAND_SQL = """
    SELECT b.id, b.brand_name, b.website, b.category, b.description, b.hero_product, b.contact_email,
           b.email_status, b.status, b.source, b.created_at,
           to_jsonb(b) ->> 'category_confidence' AS category_confidence,
           to_jsonb(b) ->> 'category_checked_at' AS category_checked_at,
           COALESCE((to_jsonb(b) ->> 'email_bounce_count')::int, 0) AS email_bounce_count,
           s.active_ad_count, s.ads_first_seen, s.ads_last_seen, s.ugc_style, s.ugc_style_reason, s.ad_samples,
           q.override
    FROM pr_brands b
    LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
    LEFT JOIN brand_quality_checks q ON q.brand_id = b.id
"""
_SIG_KEYS = ("active_ad_count", "ads_first_seen", "ads_last_seen", "ugc_style", "ugc_style_reason", "ad_samples")


def _split(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return dict(row), {k: row.get(k) for k in _SIG_KEYS}


def _persist(cur, brand_id: int, result: Dict[str, Any], run_id: Optional[str]) -> None:
    cur.execute(
        """INSERT INTO brand_quality_checks (brand_id, passed, auto_passed, reasons, checks, run_id, checked_at)
           VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, NOW())
           ON CONFLICT (brand_id) DO UPDATE SET passed = EXCLUDED.passed, auto_passed = EXCLUDED.auto_passed,
             reasons = EXCLUDED.reasons, checks = EXCLUDED.checks, run_id = EXCLUDED.run_id, checked_at = NOW()""",
        (brand_id, result["pass"], result["auto_pass"], json.dumps(result["reasons"]),
         json.dumps(result["checks"], default=str), run_id))


def _ready(conn) -> None:
    from services.meta_ads_signal import ensure_table as ensure_signals

    ensure_signals(conn)
    ensure_table(conn)


def evaluate(conn, brand_id: int, persist: bool = True, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Gate one brand. Returns {pass, reasons, checks} (+ auto_pass, override); stored in brand_quality_checks."""
    _ready(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(_BRAND_SQL + " WHERE b.id = %s", (int(brand_id),))
    row = cur.fetchone()
    if not row:
        return {"pass": False, "auto_pass": False, "reasons": ["brand not found"], "checks": {}, "override": None}
    brand, sig = _split(row)
    result = evaluate_row(brand, sig, override=row.get("override"))
    if persist:
        _persist(cur, int(brand_id), result, run_id or f"single-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
        conn.commit()
    return result


def run_gate(conn, brand_ids: Optional[Iterable[int]] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Gate every published meta_ads_library brand (or just ``brand_ids``) in one pass. One SELECT, one upsert each."""
    _ready(conn)
    run_id = run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if brand_ids is not None:
        cur.execute(_BRAND_SQL + " WHERE b.id = ANY(%s)", ([int(i) for i in brand_ids],))
    else:
        cur.execute(_BRAND_SQL + " WHERE b.source = 'meta_ads_library' AND b.status = 'published'")
    rows = cur.fetchall()
    today = datetime.now(timezone.utc).date()
    out = {"run_id": run_id, "checked": 0, "passed": 0, "failed": 0, "failing_checks": {}}
    for row in rows:
        brand, sig = _split(row)
        result = evaluate_row(brand, sig, override=row.get("override"), today=today)
        _persist(cur, int(row["id"]), result, run_id)
        out["checked"] += 1
        out["passed" if result["pass"] else "failed"] += 1
        for key in CHECK_ORDER:
            if not result["checks"][key]["ok"]:
                out["failing_checks"][key] = out["failing_checks"].get(key, 0) + 1
    conn.commit()
    return out


def set_override(conn, brand_id: int, decision: Optional[str], note: Optional[str] = None,
                 by: Optional[str] = None) -> Dict[str, Any]:
    """Admin approve / reject (None clears it), then re-gate so ``passed`` reflects it at once."""
    if decision not in ("approved", "rejected", None):
        raise ValueError("decision must be approved, rejected or None")
    _ready(conn)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO brand_quality_checks (brand_id, override, override_note, override_by, override_at)
           VALUES (%s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
           ON CONFLICT (brand_id) DO UPDATE SET override = EXCLUDED.override, override_note = EXCLUDED.override_note,
             override_by = EXCLUDED.override_by, override_at = EXCLUDED.override_at""",
        (int(brand_id), decision, note, by, decision))
    conn.commit()
    return evaluate(conn, brand_id)
