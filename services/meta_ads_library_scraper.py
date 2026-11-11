# -*- coding: utf-8 -*-
"""
In-house Meta Ads Library → Shopify DTC brand crawler.

DISCOVERY (Meta Ads Library):
  - Searches facebook.com/ads/library with keyword_unordered + active ads
  - Unwraps l.facebook.com/l.php landing URLs (Meta wraps every outbound click)
  - Drops marketplaces / apps / socials before enrichment

ENRICHMENT (Shopify sites):
  - One /products.json GET + one homepage GET (no HEAD spam)
  - Social, email, description, creator-program scripts from that HTML
  - Qualified for outreach = Shopify + (email or Instagram)

Output: Enriched brand records for pr_brands (draft status).
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

try:
    from services.tiktok_shop_scraper import (
        _UA,
        _extract_emails,
        _pick_contact_email,
        is_generic_domain,
    )
except ImportError:
    from tiktok_shop_scraper import (
        _UA,
        _extract_emails,
        _pick_contact_email,
        is_generic_domain,
    )

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "_meta_ads_state.json",
)

DEFAULT_DAILY_KEYWORDS = [
    "skincare",
    "clean beauty",
    "hair serum",
    "body oil",
    "vitamin c serum",
    "moisturizer",
    "mineral sunscreen",
    "scalp treatment",
    "lip treatment",
    "face mask",
    "makeup",
    "jewelry",
    "fashion",
    "perfume",
    "lipstick",
]

# Rotated each run so 09:00 / 17:00 UTC and successive days are not the same 15 queries.
KEYWORD_POOL = DEFAULT_DAILY_KEYWORDS + [
    "hair growth oil",
    "retinol cream",
    "collagen peptides",
    "teeth whitening",
    "natural deodorant",
    "brow serum",
    "eyelash serum",
    "self tanner",
    "acne treatment",
    "niacinamide serum",
    "hyaluronic acid",
    "dry shampoo",
    "hair mask",
    "beard oil",
    "body scrub",
    "gold necklace",
    "hoop earrings",
    "loungewear",
    "activewear",
    "yoga set",
    "silk pillowcase",
    "protein powder",
    "greens powder",
    "mushroom coffee",
    "sleep gummies",
    "electrolyte drink",
    "collagen powder",
    "ashwagandha",
    "baby skincare",
    "pet shampoo",
    "candle brand",
    "protein bar",
]

COUNTRY_ROTATION = ("US", "GB", "CA", "AU")


def select_run_keywords(count: int = 12, now: Optional[datetime] = None) -> List[str]:
    """Slice KEYWORD_POOL so consecutive cron slots are not identical."""
    pool = list(KEYWORD_POOL)
    count = max(1, min(int(count), len(pool)))
    if now is None:
        now = datetime.now(timezone.utc)
    slot = 0 if now.hour < 14 else 1
    offset = ((now.toordinal() * 2 + slot) * count) % len(pool)
    doubled = pool + pool
    return doubled[offset : offset + count]


def select_run_country(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    return COUNTRY_ROTATION[now.toordinal() % len(COUNTRY_ROTATION)]

# Hosts that look like ads but are never a DTC brand site.
_SKIP_HOST_SUFFIXES = (
    "play.google.com",
    "itunes.apple.com",
    "apps.apple.com",
    "mfilterit.net",
    "mikmak.ai",
    "pearcommerce.com",
    "shop.app",
    "google.com",
    "apple.com",
    "spotify.com",
    "youtu.be",
    "youtube.com",
)

_CREATOR_PROGRAM_HREF_HINTS = (
    "/pages/ambassador",
    "/pages/ambassadors",
    "/pages/creator",
    "/pages/creators",
    "/pages/affiliate",
    "/pages/affiliates",
    "/pages/influencer",
    "/pages/influencers",
    "/pages/collab",
    "/pages/collaboration",
    "/pages/brand-ambassador",
    "/a/ambassador",
    "/a/affiliates",
)

_CREATOR_PROGRAM_SCRIPTS = {
    "socialsnowball.io": "Social Snowball",
    "goaffpro.com": "GoAffPro",
    "refersion.com": "Refersion",
    "uppromote.com": "UpPromote",
    "getarchive.com": "Archive",
    "hashtagpaid.com": "#paid",
    "grin.co": "GRIN",
    "aspireiq.com": "Aspire",
    "aspire.io": "Aspire",
    "shareasale.com": "ShareASale",
    "impact.com": "Impact",
    "pepperjam.com": "Pepperjam",
    "partnerstack.com": "PartnerStack",
    "rewardful.com": "Rewardful",
    "firstpromoter.com": "FirstPromoter",
    "tapfiliate.com": "Tapfiliate",
    "affiliatewp.com": "AffiliateWP",
    "leaddyno.com": "LeadDyno",
}

_EXTRACT_ADS_JS = """() => {
    const results = [];
    const seen = new Set();

    function push(advertiserName, advertiserPage, landingUrl, adText) {
        if (!landingUrl || seen.has(landingUrl)) return;
        seen.add(landingUrl);
        results.push({
            advertiser_name: advertiserName || null,
            advertiser_page: advertiserPage || null,
            landing_url: landingUrl,
            ad_creative_text: (adText || '').slice(0, 300)
        });
    }

    const cards = document.querySelectorAll(
        '[data-testid*="ad"], [class*="ad-card"], [role="article"]'
    );
    cards.forEach((card) => {
        const links = [...card.querySelectorAll('a[href]')];
        let advertiserName = null;
        let advertiserPage = null;
        let landingUrl = null;
        for (const link of links) {
            const href = link.getAttribute('href') || '';
            if (!href) continue;
            if (href.includes('l.facebook.com/l.php') || href.includes('lm.facebook.com/l.php')) {
                if (!landingUrl) landingUrl = href;
                continue;
            }
            if (href.includes('facebook.com/') && !advertiserPage) {
                advertiserName = (link.innerText || '').trim() || advertiserName;
                advertiserPage = href;
            }
            if (href.startsWith('http') && !href.includes('facebook.com') && !href.includes('instagram.com')) {
                if (!landingUrl) landingUrl = href;
            }
        }
        let adText = '';
        const text = (card.innerText || '').trim();
        if (text) adText = text.slice(0, 300);
        if (advertiserName || landingUrl) {
            push(advertiserName, advertiserPage, landingUrl, adText);
        }
    });

    document.querySelectorAll('a[href*="l.php"]').forEach((link) => {
        const href = link.getAttribute('href') || '';
        if (href.includes('l.facebook.com/l.php') || href.includes('lm.facebook.com/l.php')) {
            push(null, null, href, (link.innerText || '').trim().slice(0, 300));
        }
    });

    return results;
}"""


# Every Ad Library card on the page (one per "Library ID"), for the per-brand ad signal (services.meta_ads_signal):
# how many ads, since when, the ad text, video or image. Climbs from the "Library ID" label to the largest
# ancestor that still holds only that one card.
_EXTRACT_AD_CARDS_JS = """() => {
    const out = [];
    const seen = new Set();
    const count = (t) => ((t || '').match(/Library ID/g) || []).length;
    const labels = [...document.querySelectorAll('span, div')].filter(
        (n) => n.childElementCount === 0 && /^Library ID:?\\s*\\d+/.test((n.textContent || '').trim()));
    labels.forEach((label) => {
        const m = (label.textContent || '').match(/(\\d{6,})/);
        if (!m || seen.has(m[1])) return;
        seen.add(m[1]);
        let card = label;
        while (card.parentElement && count(card.parentElement.innerText) === 1) card = card.parentElement;
        const text = (card.innerText || '').trim();
        let landing = null, page = null, name = null;
        for (const a of card.querySelectorAll('a[href]')) {
            const href = a.getAttribute('href') || '';
            if (!landing && (href.includes('l.php') || (href.startsWith('http') && !href.includes('facebook.com')
                    && !href.includes('instagram.com')))) landing = href;
            if (!page && href.includes('facebook.com/') && !href.includes('/ads/library')) {
                page = href; name = (a.innerText || '').trim() || null;
            }
        }
        const started = (text.match(/Started running on[^\\n]*/) || [null])[0];
        out.push({library_id: m[1], started_text: started, text: text.slice(0, 900), landing_url: landing,
                  advertiser_page: page, advertiser_name: name, has_video: !!card.querySelector('video')});
    });
    const total = ((document.body.innerText || '').match(/~?\\s*([\\d,.]+K?)\\s+results?/i) || [null, null])[1];
    return {cards: out, results: total};
}"""


def parse_results_count(raw: Optional[str]) -> Optional[int]:
    """'~1,200' / '1.2K' / '37' from the Ad Library header -> int."""
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        if text.upper().endswith("K"):
            return int(float(text[:-1]) * 1000)
        return int(float(text))
    except ValueError:
        return None


def extract_ad_cards(page) -> Dict[str, Any]:
    """{'cards': [...raw cards], 'results': int|None}. Never raises: the signal is a bonus to discovery."""
    try:
        got = page.evaluate(_EXTRACT_AD_CARDS_JS) or {}
    except Exception as err:
        print(f"[MetaAds] card extraction skipped: {err}")
        return {"cards": [], "results": None}
    if not isinstance(got, dict):
        return {"cards": [], "results": None}
    return {"cards": list(got.get("cards") or []), "results": parse_results_count(got.get("results"))}


def _attach_cards(ads_data: List[Dict], raw_cards: List[Dict]) -> None:
    """Give each discovered domain the cards whose landing page is on that domain (same registrable domain)."""
    if not raw_cards:
        return
    by_root: Dict[str, List[Dict]] = {}
    for card in raw_cards:
        real = unwrap_landing_url(card.get("landing_url") or "") if card.get("landing_url") else None
        if not real:
            continue
        by_root.setdefault(registrable_domain(_host_of(real)), []).append(card)
    for ad in ads_data:
        root = registrable_domain(ad.get("shopify_domain") or "")
        if root in by_root:
            ad["ad_cards"] = by_root[root]


def _proxy_config() -> Optional[Dict[str, str]]:
    """Playwright proxy dict from META_ADS_PROXY (falls back to IG_PROXY)."""
    raw = (os.getenv("META_ADS_PROXY") or os.getenv("IG_PROXY") or "").strip().strip('"').strip("'")
    if not raw or "residential-proxy" in raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        return None
    cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


def _launch_browser(p, headless: bool = True):
    proxy = _proxy_config()
    kwargs = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        kwargs["proxy"] = proxy
        print(f"[MetaAds] using proxy {proxy['server']}")
    browser = p.chromium.launch(**kwargs)
    ctx_kwargs = {
        "user_agent": _UA,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "viewport": {"width": 1440, "height": 900},
    }
    if os.path.exists(_STATE_FILE):
        ctx_kwargs["storage_state"] = _STATE_FILE
    context = browser.new_context(**ctx_kwargs)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


def _open_browser(p, *, headless: bool = True, cdp_url: Optional[str] = None):
    if cdp_url:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        print(f"[MetaAds] attached to existing browser via CDP ({cdp_url})")
        return browser, context, False
    browser, context = _launch_browser(p, headless=headless)
    return browser, context, True


def _save_browser_state(context) -> None:
    try:
        context.storage_state(path=_STATE_FILE)
    except Exception:
        pass


def _ads_library_url(keyword: str, country: str) -> str:
    q = quote_plus(keyword)
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}"
        f"&q={q}&search_type=keyword_unordered"
    )


def unwrap_landing_url(url: str) -> Optional[str]:
    """Turn Meta's l.php wrapper into the real destination URL."""
    if not url:
        return None
    current = url.strip()
    for _ in range(3):
        parsed = urlparse(current)
        host = (parsed.hostname or "").lower()
        if host in {"l.facebook.com", "lm.facebook.com"} and "l.php" in parsed.path:
            dest = parse_qs(parsed.query).get("u", [""])[0]
            if not dest:
                return None
            current = unquote(dest)
            continue
        break
    if not current.startswith("http"):
        return None
    return current


_GENERIC_ADVERTISER_NAMES = {
    "join", "shop", "try", "learn more", "sponsored", "acheter", "buy", "shop now",
}
_SUBDOMAIN_NOISE = {"shop", "try", "join", "www", "us", "uk", "ca", "store", "get"}


def brand_name_from_domain(domain: str) -> str:
    parts = [p for p in (domain or "").split(".") if p]
    if len(parts) >= 3 and parts[0].lower() in _SUBDOMAIN_NOISE:
        parts = parts[1:]
    if parts and parts[-1] in {"com", "net", "io", "org", "ca", "nz", "us", "uk", "au", "co"}:
        parts = parts[:-1]
    if parts and parts[-1] == "co":
        parts = parts[:-1]
    core = parts[0] if parts else (domain or "brand")
    return core.replace("-", " ").title()


def _clean_advertiser_name(name: Optional[str], domain: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or cleaned.lower() in _GENERIC_ADVERTISER_NAMES:
        return brand_name_from_domain(domain)
    return cleaned


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


_MULTI_PART_TLDS = {
    "co.uk", "org.uk", "com.au", "net.au", "co.nz", "co.za", "com.br", "co.jp",
}


def registrable_domain(host: str) -> str:
    """vegamour.com from try.vegamour.com / www.vegamour.com."""
    host = (host or "").lower().removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last2


def _is_known_host(host: str, known_hosts: Set[str], known_roots: Set[str]) -> bool:
    host = (host or "").lower().removeprefix("www.")
    if not host:
        return False
    if host in known_hosts:
        return True
    return registrable_domain(host) in known_roots


def is_skip_landing_host(host: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    if not host:
        return True
    if is_generic_domain(host):
        return True
    for suffix in _SKIP_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def extract_candidate_domain(landing_url: str) -> Optional[str]:
    real = unwrap_landing_url(landing_url)
    if not real:
        return None
    host = _host_of(real)
    if is_skip_landing_host(host):
        return None
    return host


def _extract_shopify_domain(landing_url: str) -> Optional[str]:
    return extract_candidate_domain(landing_url)


def _collect_from_raw_ads(
    raw_ads: List[Dict],
    *,
    max_ads: int,
    seen_domains: Set[str],
    known_hosts: Optional[Set[str]] = None,
    known_roots: Optional[Set[str]] = None,
) -> List[Dict]:
    ads_data: List[Dict] = []
    known_hosts = known_hosts or set()
    known_roots = known_roots or set()
    for ad in raw_ads:
        if len(ads_data) >= max_ads:
            break
        wrapped = ad.get("landing_url")
        real = unwrap_landing_url(wrapped) if wrapped else None
        domain = extract_candidate_domain(wrapped or "")
        if not domain or domain in seen_domains:
            continue
        if _is_known_host(domain, known_hosts, known_roots):
            seen_domains.add(domain)
            continue
        seen_domains.add(domain)
        ads_data.append({
            "advertiser_name": _clean_advertiser_name(ad.get("advertiser_name"), domain),
            "advertiser_page": ad.get("advertiser_page"),
            "landing_url": real,
            "shopify_domain": domain,
            "ad_creative_text": ad.get("ad_creative_text"),
        })
    return ads_data


def _discover_on_page(
    page,
    keyword: str,
    *,
    country: str,
    max_scroll: int,
    max_ads: int,
    seen_domains: Set[str],
    known_hosts: Optional[Set[str]] = None,
    known_roots: Optional[Set[str]] = None,
) -> List[Dict]:
    url = _ads_library_url(keyword, country)
    print(f"[MetaAds] searching for '{keyword}' in {country}...")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    for i in range(max_scroll):
        page.mouse.wheel(0, 2800)
        page.wait_for_timeout(random.uniform(1600, 2600))
        print(f"[MetaAds] scroll {i + 1}/{max_scroll}")

    print("[MetaAds] extracting ad data...")
    raw_ads = page.evaluate(_EXTRACT_ADS_JS) or []
    print(f"[MetaAds] extracted {len(raw_ads)} raw landing links")
    ads_data = _collect_from_raw_ads(
        raw_ads,
        max_ads=max_ads,
        seen_domains=seen_domains,
        known_hosts=known_hosts,
        known_roots=known_roots,
    )
    _attach_cards(ads_data, extract_ad_cards(page)["cards"])
    print(f"[MetaAds] {len(ads_data)} unique DTC-looking domains for '{keyword}'")
    return ads_data


def _load_library(page, url: str, max_scroll: int) -> Dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    for _ in range(max_scroll):
        page.mouse.wheel(0, 2800)
        page.wait_for_timeout(random.uniform(1200, 2000))
    return extract_ad_cards(page)


def fetch_brand_ads(
    page,
    brand_name: str,
    domain: str,
    *,
    page_id: Optional[str] = None,
    country: str = "ALL",
    max_scroll: int = 4,
) -> Dict[str, Any]:
    """Live ads of one known brand (backfill). Search the brand name, keep cards that land on the brand's own
    domain (so a same-named advertiser never counts), then open that advertiser's page view for the full list.
    Returns {'cards': [...], 'results': int|None, 'page_id': str|None, 'query': str}."""
    root = registrable_domain(domain or "")
    query = brand_name or root
    if not page_id:
        found = _load_library(page, _ads_library_url(query, country), max_scroll)
        own = []
        for card in found["cards"]:
            real = unwrap_landing_url(card.get("landing_url") or "") if card.get("landing_url") else None
            if real and registrable_domain(_host_of(real)) == root:
                own.append(card)
        if not own and root and root != query:
            found = _load_library(page, _ads_library_url(root, country), max_scroll)
            own = [c for c in found["cards"]
                   if c.get("landing_url") and registrable_domain(_host_of(unwrap_landing_url(c["landing_url"]) or "")) == root]
        from services.meta_ads_signal import page_id_from_url

        page_id = next((page_id_from_url(c.get("advertiser_page")) for c in own
                        if page_id_from_url(c.get("advertiser_page"))), None)
        if not page_id:
            # Keyword results only: the result counter there counts everyone's ads, not this brand's.
            return {"cards": own, "results": None, "page_id": None, "query": query}
    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}&view_all_page_id={page_id}"
        "&search_type=page&media_type=all"
    )
    got = _load_library(page, url, max_scroll)
    return {"cards": got["cards"], "results": got["results"], "page_id": page_id, "query": query}


def discover_ads(
    keyword: str,
    *,
    country: str = "US",
    max_scroll: int = 8,
    max_ads: int = 50,
    headless: bool = True,
    cdp_url: Optional[str] = None,
    debug_dump: Optional[str] = None,
) -> List[Dict]:
    """Search Meta Ads Library for one keyword and return unique landing domains."""
    from playwright.sync_api import sync_playwright

    ads_data: List[Dict] = []
    seen_domains: Set[str] = set()

    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=headless, cdp_url=cdp_url)
        page = context.new_page()
        try:
            ads_data = _discover_on_page(
                page,
                keyword,
                country=country,
                max_scroll=max_scroll,
                max_ads=max_ads,
                seen_domains=seen_domains,
            )
            _save_browser_state(context)
        finally:
            if owns:
                browser.close()
            else:
                page.close()

    if debug_dump:
        with open(debug_dump, "w", encoding="utf-8") as f:
            json.dump(ads_data, f, indent=2, ensure_ascii=False)
        print(f"[MetaAds] dumped {len(ads_data)} ads → {debug_dump}")

    print(f"[MetaAds] found {len(ads_data)} unique advertisers with landing pages")
    return ads_data


def _fetch_shopify_products(domain: str, timeout: int = 8) -> Optional[Dict]:
    try:
        r = requests.get(
            f"https://{domain}/products.json?limit=50",
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and "products" in data:
                return data
    except Exception:
        pass
    return None


def _fetch_homepage(domain: str, timeout: int = 10) -> str:
    try:
        r = requests.get(
            f"https://{domain}",
            headers={"User-Agent": _UA, "Accept": "text/html"},
            timeout=timeout,
        )
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return ""


def _looks_like_shopify(html: str, products_data: Optional[Dict]) -> bool:
    if products_data and products_data.get("products") is not None:
        return True
    low = (html or "").lower()
    return "cdn.shopify.com" in low or "myshopify.com" in low


def _extract_social_links(html: str, domain: str) -> Dict[str, Optional[str]]:
    social = {"instagram_handle": None, "tiktok_handle": None}
    ig_matches = re.findall(r"(?:instagram\.com|instagr\.am)/([a-zA-Z0-9._]+)", html or "")
    for handle in ig_matches:
        if handle and handle not in ("p", "reel", "stories", "explore", "accounts", "direct"):
            social["instagram_handle"] = handle.strip(".")
            break
    tt_matches = re.findall(r"tiktok\.com/@([a-zA-Z0-9._]+)", html or "")
    for handle in tt_matches:
        if handle:
            social["tiktok_handle"] = handle.strip("@.")
            break
    return social


def _meta_description(html: str) -> Optional[str]:
    if not html:
        return None
    for pattern in (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
    ):
        match = re.search(pattern, html, flags=re.I)
        if match:
            text = re.sub(r"\s+", " ", match.group(1)).strip()
            if text:
                return text[:400]
    return None


def detect_creator_program_from_html(html: str, domain: str = "") -> Dict[str, Any]:
    """Detect creator/affiliate programs from HTML only (no extra HTTP)."""
    result = {
        "has_program": False,
        "program_type": None,
        "program_url": None,
        "platform": None,
    }
    if not html:
        return result
    low = html.lower()
    for script_domain, platform in _CREATOR_PROGRAM_SCRIPTS.items():
        if script_domain in low:
            result.update({
                "has_program": True,
                "program_type": "script",
                "platform": platform,
            })
            return result
    for hint in _CREATOR_PROGRAM_HREF_HINTS:
        if hint in low:
            result.update({
                "has_program": True,
                "program_type": "page",
                "program_url": f"https://{domain}{hint}" if domain else hint,
            })
            return result
    return result


def _detect_creator_program(domain: str, timeout: int = 8, html: Optional[str] = None) -> Dict[str, Any]:
    if html is None:
        html = _fetch_homepage(domain, timeout=timeout)
    return detect_creator_program_from_html(html, domain)


def _verify_shopify(domain: str, timeout: int = 8) -> bool:
    products = _fetch_shopify_products(domain, timeout=timeout)
    if products is not None:
        return True
    html = _fetch_homepage(domain, timeout=timeout)
    return _looks_like_shopify(html, None)


def is_qualified_brand(record: Dict) -> bool:
    """Outreach-ready: a real store we can email or DM."""
    return bool(record.get("contact_email") or record.get("instagram_handle"))


def _score_micro_friendly(record: Dict) -> bool:
    score = 0
    if record.get("has_creator_program"):
        score += 2
    if record.get("contact_email"):
        score += 2
    if record.get("instagram_handle") or record.get("tiktok_handle"):
        score += 1
    products = record.get("products") or []
    if products and len(products) <= 20:
        score += 1
    return score >= 3


def enrich_shopify_brand(ad_data: Dict, *, verify_shopify: bool = True) -> Optional[Dict]:
    domain = ad_data.get("shopify_domain")
    if not domain:
        return None

    print(f"[MetaAds] enriching {domain}...")
    products_data = _fetch_shopify_products(domain)
    html = _fetch_homepage(domain)

    if verify_shopify and not _looks_like_shopify(html, products_data):
        print(f"[MetaAds] {domain} is not a Shopify store, skipping")
        return None

    record = {
        "brand_name": _clean_advertiser_name(ad_data.get("advertiser_name"), domain),
        "website": f"https://{domain}",
        "description": _meta_description(html),
        "contact_email": None,
        "instagram_handle": None,
        "tiktok_handle": None,
        "facebook_page": ad_data.get("advertiser_page"),
        "hero_product": None,
        "products": [],
        "has_creator_program": False,
        "creator_program_type": None,
        "creator_program_url": None,
        "creator_platform": None,
        "ad_creative_sample": ad_data.get("ad_creative_text"),
        # Ad Library cards seen for this domain: services.meta_ads_signal turns them into the brand's ad signal.
        "ad_cards": ad_data.get("ad_cards") or [],
        "micro_friendly": False,
        "qualified": False,
    }

    social = _extract_social_links(html, domain)
    record["instagram_handle"] = social.get("instagram_handle")
    record["tiktok_handle"] = social.get("tiktok_handle")

    emails = _extract_emails(html)
    if emails:
        record["contact_email"] = _pick_contact_email(emails)

    if products_data and products_data.get("products"):
        products = products_data["products"]
        record["hero_product"] = (products[0] or {}).get("title")
        record["products"] = [p.get("title") for p in products[:10] if p.get("title")]
        print(f"[MetaAds] found {len(products)} products")

    creator_info = detect_creator_program_from_html(html, domain)
    if creator_info["has_program"]:
        record["has_creator_program"] = True
        record["creator_program_type"] = creator_info["program_type"]
        record["creator_program_url"] = creator_info["program_url"]
        record["creator_platform"] = creator_info["platform"]
        print(
            f"[MetaAds] creator program: {creator_info.get('platform') or creator_info.get('program_url')}"
        )

    if not record["contact_email"]:
        for path in ("/pages/contact", "/pages/contact-us"):
            try:
                r = requests.get(
                    f"https://{domain}{path}",
                    headers={"User-Agent": _UA},
                    timeout=6,
                )
                if r.status_code == 200:
                    found = _extract_emails(r.text)
                    if found:
                        record["contact_email"] = _pick_contact_email(found)
                        break
            except Exception:
                pass

    record["micro_friendly"] = _score_micro_friendly(record)
    record["qualified"] = is_qualified_brand(record)
    return record


def discover_and_enrich(
    keywords: List[str],
    *,
    country: str = "US",
    max_ads_per_keyword: int = 40,
    max_scroll: int = 8,
    quota: int = 50,
    headless: bool = True,
    cdp_url: Optional[str] = None,
    debug_dump: Optional[str] = None,
    exclude_domains: Optional[Set[str]] = None,
) -> List[Dict]:
    """
    One browser session across keywords. Enrich until `quota` qualified
    Shopify brands (email or Instagram) or keywords run out.

    exclude_domains: already in pr_brands — skip so quota means new brands.
    """
    from playwright.sync_api import sync_playwright

    all_brands: List[Dict] = []
    known_hosts = {(d or "").lower().removeprefix("www.") for d in (exclude_domains or set()) if d}
    known_roots = {registrable_domain(h) for h in known_hosts}
    seen_domains: Set[str] = set(known_hosts)
    enriched_domains: Set[str] = set()
    discovered: List[Dict] = []
    if known_hosts:
        print(
            f"[MetaAds] skipping {len(known_hosts)} hosts / {len(known_roots)} root domains already in pr_brands"
        )

    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=headless, cdp_url=cdp_url)
        page = context.new_page()
        try:
            for keyword in keywords:
                if len([b for b in all_brands if b.get("qualified")]) >= quota:
                    break
                ads = _discover_on_page(
                    page,
                    keyword,
                    country=country,
                    max_scroll=max_scroll,
                    max_ads=max_ads_per_keyword,
                    seen_domains=seen_domains,
                    known_hosts=known_hosts,
                    known_roots=known_roots,
                )
                discovered.extend(ads)
                time.sleep(random.uniform(1.0, 2.0))
            _save_browser_state(context)
        finally:
            if owns:
                browser.close()
            else:
                page.close()

    if debug_dump:
        with open(debug_dump, "w", encoding="utf-8") as f:
            json.dump(discovered, f, indent=2, ensure_ascii=False)
        print(f"[MetaAds] dumped {len(discovered)} discovered ads → {debug_dump}")

    print(f"\n[MetaAds] ===== Enriching {len(discovered)} unique domains (quota {quota}) =====")
    for ad in discovered:
        qualified_count = sum(1 for b in all_brands if b.get("qualified"))
        if qualified_count >= quota:
            print(f"[MetaAds] hit quota ({quota} qualified)")
            break
        domain = ad.get("shopify_domain")
        if not domain or domain in enriched_domains:
            continue
        host = domain.lower().removeprefix("www.")
        if _is_known_host(host, known_hosts, known_roots):
            print(f"[MetaAds] skip known {host}")
            continue
        enriched_domains.add(domain)
        brand = enrich_shopify_brand(ad, verify_shopify=True)
        if not brand:
            print(f"[MetaAds] ✗ {domain} - not Shopify or enrichment failed")
            continue
        all_brands.append(brand)
        mark = "qualified" if brand.get("qualified") else "shopify-only"
        print(
            f"[MetaAds] ✓ {brand['brand_name']} [{mark}] "
            f"email={brand.get('contact_email')} ig={brand.get('instagram_handle')}"
        )
        time.sleep(random.uniform(0.2, 0.5))

    qualified = [b for b in all_brands if b.get("qualified")]
    print(
        f"\n[MetaAds] ===== DONE: {len(all_brands)} Shopify, "
        f"{len(qualified)} qualified of quota {quota} ====="
    )
    return all_brands
