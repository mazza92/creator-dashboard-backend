# -*- coding: utf-8 -*-
"""
In-house Meta Ads Library → Shopify DTC brand crawler.

DISCOVERY (Meta Ads Library):
  - Searches Meta's Ad Library (facebook.com/ads/library) for beauty/skincare keywords
  - Extracts advertiser info (name, Facebook/Instagram page) and landing page URLs
  - Filters for Shopify stores (cdn.shopify.com detection)
  - Intent signal: brands running ads = actively spending on marketing = likely to hire creators

ENRICHMENT (Shopify sites):
  - Fast deterministic extraction via Shopify's public APIs and standard URL patterns:
    • /products.json → product catalog (up to 250 products per page)
    • Footer → Instagram/TikTok social links
    • /pages/contact, /pages/about → contact email
    • Meta tags → brand description
  - Creator program detection:
    • Pages: /pages/ambassador, /pages/creators, /pages/affiliates, /pages/partner
    • Scripts: Social Snowball, GoAffPro, Refersion, UpPromote, Archive, #paid, GRIN, Aspire

Output: Enriched brand records for pr_brands (draft status) with micro_friendly scoring.
"""
import json
import os
import re
import time
import random
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin
from html import unescape

import requests

# Reuse utilities from TikTok Shop scraper
try:
    from services.tiktok_shop_scraper import (
        _UA, _EMAIL_RE, _EMAIL_EXCLUDES, _pick_contact_email, _extract_emails,
        is_generic_domain, _scrape_website_meta
    )
except ImportError:
    from tiktok_shop_scraper import (
        _UA, _EMAIL_RE, _EMAIL_EXCLUDES, _pick_contact_email, _extract_emails,
        is_generic_domain, _scrape_website_meta
    )

# Browser state file for persisting login/captcha solutions
_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_meta_ads_state.json")


# ---------------------------------------------------------------------------
# Discovery: Meta Ads Library
# ---------------------------------------------------------------------------

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
    """Launch Chromium with anti-detection."""
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
    """
    Return (browser, context, owns).
    owns=True  -> launched browser, must close
    owns=False -> attached via CDP, never close
    """
    if cdp_url:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        print(f"[MetaAds] attached to existing browser via CDP ({cdp_url})")
        return browser, context, False
    browser, context = _launch_browser(p, headless=headless)
    return browser, context, True


def _save_browser_state(context) -> None:
    """Persist cookies/localStorage for future headless runs."""
    try:
        context.storage_state(path=_STATE_FILE)
    except Exception:
        pass


def _is_shopify_url(url: str) -> bool:
    """Check if a URL is a Shopify store (fast heuristic)."""
    if not url:
        return False
    # Check URL path for common Shopify patterns
    if "/products/" in url or "/collections/" in url:
        return True
    # Will verify via HEAD request in enrichment
    return False


def _extract_shopify_domain(landing_url: str) -> Optional[str]:
    """Extract clean Shopify domain from landing page URL."""
    if not landing_url:
        return None
    parsed = urlparse(landing_url)
    domain = (parsed.hostname or "").lower().removeprefix("www.")
    if not domain or is_generic_domain(domain):
        return None
    return domain


def discover_ads(
    keyword: str,
    *,
    country: str = "US",
    max_scroll: int = 5,
    max_ads: int = 50,
    headless: bool = True,
    cdp_url: Optional[str] = None,
    debug_dump: Optional[str] = None,
) -> List[Dict]:
    """
    Search Meta Ads Library for a keyword and extract advertiser + landing page data.

    Returns:
        [{"advertiser_name": "...", "advertiser_page": "...", "landing_url": "...",
          "shopify_domain": "...", "ad_creative_text": "..."}, ...]
    """
    from playwright.sync_api import sync_playwright

    url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country={country}&q={keyword}"

    ads_data: List[Dict] = []
    seen_domains: Set[str] = set()

    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=headless, cdp_url=cdp_url)
        page = context.new_page()

        try:
            print(f"[MetaAds] searching for '{keyword}' in {country}...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            # Scroll to load more ads
            for i in range(max_scroll):
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(random.uniform(2000, 3500))
                print(f"[MetaAds] scroll {i+1}/{max_scroll}")

            # Extract ad cards from the page
            # Meta Ads Library structure: each ad is in a card with advertiser link and "View on Facebook" or landing page link
            print(f"[MetaAds] extracting ad data...")

            # Try to extract via DOM selectors
            # Note: Selectors may need adjustment based on current Meta UI
            ads_html = page.content()

            # Parse advertiser names and pages
            advertiser_pattern = r'href="(https://www\.facebook\.com/[^"]+)"[^>]*>([^<]+)</a>'
            landing_pattern = r'href="([^"]+)"[^>]*(?:target="_blank"|rel="noopener")'

            # Better approach: use page.evaluate to extract structured data
            raw_ads = page.evaluate("""() => {
                const results = [];
                // Find all ad cards - adjust selector based on current Meta UI
                const cards = document.querySelectorAll('[data-testid*="ad"], [class*="ad-card"], [class*="_8n_"]');

                cards.forEach(card => {
                    try {
                        // Extract advertiser link (usually points to Facebook page)
                        const advertiserLink = card.querySelector('a[href*="facebook.com/"]');
                        const advertiserName = advertiserLink ? advertiserLink.innerText.trim() : null;
                        const advertiserPage = advertiserLink ? advertiserLink.getAttribute('href') : null;

                        // Extract landing page (external link, usually has target="_blank")
                        const links = [...card.querySelectorAll('a[href]')];
                        let landingUrl = null;
                        for (const link of links) {
                            const href = link.getAttribute('href');
                            if (href && !href.includes('facebook.com') && !href.includes('instagram.com') && href.startsWith('http')) {
                                landingUrl = href;
                                break;
                            }
                        }

                        // Extract ad creative text
                        const textEls = card.querySelectorAll('[class*="text"], [class*="body"], p, span');
                        let adText = '';
                        for (const el of textEls) {
                            const text = el.innerText || el.textContent;
                            if (text && text.length > adText.length && text.length < 500) {
                                adText = text.trim();
                            }
                        }

                        if (advertiserName || landingUrl) {
                            results.push({
                                advertiser_name: advertiserName,
                                advertiser_page: advertiserPage,
                                landing_url: landingUrl,
                                ad_creative_text: adText.slice(0, 300)
                            });
                        }
                    } catch (e) {
                        // Skip malformed cards
                    }
                });

                return results;
            }""")

            print(f"[MetaAds] extracted {len(raw_ads)} ad cards")

            # Filter for Shopify stores and dedupe by domain
            for ad in raw_ads:
                if len(ads_data) >= max_ads:
                    break

                landing_url = ad.get("landing_url")
                if not landing_url:
                    continue

                domain = _extract_shopify_domain(landing_url)
                if not domain or domain in seen_domains:
                    continue

                # Add to results (we'll verify Shopify in enrichment)
                seen_domains.add(domain)
                ads_data.append({
                    "advertiser_name": ad.get("advertiser_name") or domain.split(".")[0].title(),
                    "advertiser_page": ad.get("advertiser_page"),
                    "landing_url": landing_url,
                    "shopify_domain": domain,
                    "ad_creative_text": ad.get("ad_creative_text"),
                })

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


# ---------------------------------------------------------------------------
# Enrichment: Shopify sites
# ---------------------------------------------------------------------------

def _fetch_shopify_products(domain: str, timeout: int = 10) -> Optional[Dict]:
    """Fetch /products.json from Shopify store."""
    try:
        r = requests.get(
            f"https://{domain}/products.json",
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            return data
    except Exception:
        pass
    return None


def _extract_social_links(html: str, domain: str) -> Dict[str, Optional[str]]:
    """Extract Instagram and TikTok handles from footer/social links."""
    social = {"instagram_handle": None, "tiktok_handle": None}

    # Find Instagram links
    ig_pattern = r'(?:instagram\.com|instagr\.am)/([a-zA-Z0-9._]+)'
    ig_matches = re.findall(ig_pattern, html)
    for handle in ig_matches:
        if handle and handle not in ("p", "reel", "stories", "explore", "accounts", "direct"):
            social["instagram_handle"] = handle.strip(".")
            break

    # Find TikTok links
    tt_pattern = r'tiktok\.com/@([a-zA-Z0-9._]+)'
    tt_matches = re.findall(tt_pattern, html)
    for handle in tt_matches:
        if handle:
            social["tiktok_handle"] = handle.strip("@.")
            break

    return social


# Creator/affiliate program detection
_CREATOR_PROGRAM_PATHS = [
    "/pages/ambassador",
    "/pages/ambassadors",
    "/pages/creator",
    "/pages/creators",
    "/pages/affiliate",
    "/pages/affiliates",
    "/pages/partner",
    "/pages/partners",
    "/pages/influencer",
    "/pages/influencers",
    "/pages/collab",
    "/pages/collaboration",
    "/pages/brand-ambassador",
    "/a/ambassador",
    "/a/affiliates",
    "/partner",
    "/partners",
]

# Creator program platform scripts/domains
_CREATOR_PROGRAM_SCRIPTS = [
    "socialsnowball.io",
    "goaffpro.com",
    "refersion.com",
    "uppromote.com",
    "getarchive.com",
    "hashtagpaid.com",
    "grin.co",
    "aspireiq.com",
    "aspire.io",
    "impact.com",
    "shareasale.com",
    "cj.com",
    "pepperjam.com",
    "partnerstack.com",
    "rewardful.com",
    "firstpromoter.com",
    "tapfiliate.com",
    "post-affiliate-pro",
    "affiliatewp.com",
    "leaddyno.com",
]


def _detect_creator_program(domain: str, timeout: int = 8) -> Dict[str, Any]:
    """
    Check if a Shopify store has a creator/affiliate program.

    Returns:
        {
            "has_program": bool,
            "program_type": "page" | "script" | None,
            "program_url": str | None,
            "platform": str | None (e.g., "Social Snowball", "GoAffPro"),
        }
    """
    result = {
        "has_program": False,
        "program_type": None,
        "program_url": None,
        "platform": None,
    }

    if not domain:
        return result

    # Check common creator program paths
    for path in _CREATOR_PROGRAM_PATHS[:8]:  # Check first 8 paths to avoid too many requests
        try:
            r = requests.head(
                f"https://{domain}{path}",
                headers={"User-Agent": _UA},
                timeout=timeout,
                allow_redirects=True,
            )
            if r.status_code == 200:
                result["has_program"] = True
                result["program_type"] = "page"
                result["program_url"] = f"https://{domain}{path}"
                print(f"[MetaAds] found creator program page: {path}")
                return result
        except Exception:
            pass
        time.sleep(random.uniform(0.2, 0.5))

    # Check homepage for creator program scripts
    try:
        r = requests.get(
            f"https://{domain}",
            headers={"User-Agent": _UA},
            timeout=timeout,
        )
        if r.status_code == 200:
            html = r.text.lower()
            for script_domain in _CREATOR_PROGRAM_SCRIPTS:
                if script_domain in html:
                    result["has_program"] = True
                    result["program_type"] = "script"
                    # Derive platform name from domain
                    platform_name = script_domain.split(".")[0].replace("get", "").title()
                    if "socialsnowball" in script_domain:
                        platform_name = "Social Snowball"
                    elif "goaffpro" in script_domain:
                        platform_name = "GoAffPro"
                    elif "hashtagpaid" in script_domain:
                        platform_name = "#paid"
                    elif "aspire" in script_domain:
                        platform_name = "Aspire"
                    elif "grin" in script_domain:
                        platform_name = "GRIN"
                    result["platform"] = platform_name
                    print(f"[MetaAds] found creator program script: {platform_name}")
                    return result
    except Exception:
        pass

    return result


def _verify_shopify(domain: str, timeout: int = 8) -> bool:
    """Verify that a domain is actually a Shopify store."""
    if not domain:
        return False

    # Try to fetch /products.json - Shopify-specific endpoint
    try:
        r = requests.head(
            f"https://{domain}/products.json",
            headers={"User-Agent": _UA},
            timeout=timeout,
            allow_redirects=True,
        )
        if r.status_code == 200:
            return True
    except Exception:
        pass

    # Check for cdn.shopify.com in homepage HTML
    try:
        r = requests.get(
            f"https://{domain}",
            headers={"User-Agent": _UA},
            timeout=timeout,
            stream=True,
        )
        if r.status_code == 200:
            # Read first 50KB only
            content = ""
            for chunk in r.iter_content(chunk_size=8192, decode_unicode=True):
                if chunk:
                    content += chunk
                if len(content) > 50000:
                    break
            r.close()
            if "cdn.shopify.com" in content.lower() or "shopify" in content.lower():
                return True
    except Exception:
        pass

    return False


def enrich_shopify_brand(ad_data: Dict, *, verify_shopify: bool = True) -> Optional[Dict]:
    """
    Enrich a brand from Meta Ads Library with Shopify data.

    Args:
        ad_data: Dict from discover_ads() with shopify_domain, advertiser_name, etc.
        verify_shopify: If True, verify the site is actually Shopify before enriching

    Returns:
        Enriched brand record or None if not a Shopify store or enrichment fails
    """
    domain = ad_data.get("shopify_domain")
    if not domain:
        return None

    # Verify it's a Shopify store
    if verify_shopify and not _verify_shopify(domain):
        print(f"[MetaAds] {domain} is not a Shopify store, skipping")
        return None

    print(f"[MetaAds] enriching {domain}...")

    record = {
        "brand_name": ad_data.get("advertiser_name") or domain.split(".")[0].title(),
        "website": f"https://{domain}",
        "description": None,
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
        "micro_friendly": False,
    }

    # Fetch homepage for meta tags, social links, email
    try:
        r = requests.get(
            f"https://{domain}",
            headers={"User-Agent": _UA},
            timeout=12,
        )
        if r.status_code == 200:
            html = r.text

            # Extract social handles
            social = _extract_social_links(html, domain)
            record["instagram_handle"] = social.get("instagram_handle")
            record["tiktok_handle"] = social.get("tiktok_handle")

            # Get meta description
            meta = _scrape_website_meta(f"https://{domain}")
            if meta and not meta.get("parked"):
                record["description"] = meta.get("description")
                if meta.get("email"):
                    record["contact_email"] = meta["email"]
    except Exception as e:
        print(f"[MetaAds] failed to fetch homepage: {e}")

    # Fetch products
    products_data = _fetch_shopify_products(domain)
    if products_data and "products" in products_data:
        products = products_data["products"]
        if products:
            record["hero_product"] = products[0].get("title")
            record["products"] = [p.get("title") for p in products[:10] if p.get("title")]
            print(f"[MetaAds] found {len(products)} products")

    # Check for creator program
    creator_info = _detect_creator_program(domain)
    if creator_info["has_program"]:
        record["has_creator_program"] = True
        record["creator_program_type"] = creator_info["program_type"]
        record["creator_program_url"] = creator_info["program_url"]
        record["creator_platform"] = creator_info["platform"]

    # Scrape contact pages for email if not found yet
    if not record["contact_email"]:
        contact_paths = ["/pages/contact", "/pages/contact-us", "/contact", "/pages/about"]
        for path in contact_paths:
            try:
                r = requests.get(
                    f"https://{domain}{path}",
                    headers={"User-Agent": _UA},
                    timeout=8,
                )
                if r.status_code == 200:
                    emails = _extract_emails(r.text)
                    if emails:
                        record["contact_email"] = _pick_contact_email(emails)
                        break
            except Exception:
                pass
            time.sleep(random.uniform(0.3, 0.7))

    # Micro-friendly scoring
    # Heuristics: has creator program, small product catalog, recently launched (can't detect easily), etc.
    micro_signals = 0
    if record["has_creator_program"]:
        micro_signals += 3  # Strong signal
    if record["products"] and len(record["products"]) < 20:
        micro_signals += 1  # Small catalog = likely small brand
    if record["instagram_handle"] or record["tiktok_handle"]:
        micro_signals += 1  # Social presence
    if record["contact_email"]:
        micro_signals += 1  # Reachable

    record["micro_friendly"] = micro_signals >= 3

    return record


def discover_and_enrich(
    keywords: List[str],
    *,
    country: str = "US",
    max_ads_per_keyword: int = 30,
    headless: bool = True,
    cdp_url: Optional[str] = None,
) -> List[Dict]:
    """
    End-to-end pipeline: search Meta Ads Library for keywords, enrich Shopify brands.

    Args:
        keywords: List of search terms (e.g., ["skincare", "beauty", "cosmetics"])
        country: ISO country code (default: "US")
        max_ads_per_keyword: Max ads to process per keyword
        headless: Run browser headlessly
        cdp_url: Optional CDP URL to attach to existing browser

    Returns:
        List of enriched brand records
    """
    all_brands = []
    seen_domains = set()

    for keyword in keywords:
        print(f"\n[MetaAds] ===== Searching for '{keyword}' =====")

        # Discover ads
        ads = discover_ads(
            keyword,
            country=country,
            max_ads=max_ads_per_keyword,
            headless=headless,
            cdp_url=cdp_url,
        )

        # Enrich each unique Shopify brand
        for ad in ads:
            domain = ad.get("shopify_domain")
            if not domain or domain in seen_domains:
                continue

            seen_domains.add(domain)

            brand = enrich_shopify_brand(ad, verify_shopify=True)
            if brand:
                all_brands.append(brand)
                print(f"[MetaAds] ✓ {brand['brand_name']} - creator program: {brand['has_creator_program']}, micro-friendly: {brand['micro_friendly']}")
            else:
                print(f"[MetaAds] ✗ {domain} - not Shopify or enrichment failed")

            # Rate limiting
            time.sleep(random.uniform(1.5, 3.0))

    print(f"\n[MetaAds] ===== DONE: {len(all_brands)} brands enriched =====")
    return all_brands
