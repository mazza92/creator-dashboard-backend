# -*- coding: utf-8 -*-
"""
In-house TikTok Shop brand crawler.

Two independent legs:

1. DISCOVERY (region-gated — requires an IP in a TikTok Shop market, or set
   TIKTOK_SHOP_PROXY to a US/UK residential proxy URL):
     - Renders https://www.tiktok.com/shop/c/<slug>/<id> category pages in
       headless Chromium (Playwright), scrolls to trigger lazy loads, and
       captures both the product-feed XHR JSON and the rendered DOM.
     - Extracts unique sellers: seller name, seller id, store URL, and the
       product titles seen for that seller (hero product candidates).

2. ENRICHMENT (works from any region):
     - Resolves the seller's TikTok profile via the existing in-house scraper
       (bio -> description, bioLink -> website).
     - Extracts a contact email from the bio, or scrapes the brand website's
       contact pages as a fallback.

The crawl runner (scripts/crawl_tiktok_shop.py) turns the enriched records
into pr_brands rows with status='draft' for admin review.
"""
import json
import os
import re
import time
import random
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from html import unescape

import requests

try:
    from services.inhouse_social_scraper import scrape_tiktok, InHouseScrapeError
except ImportError:  # pragma: no cover - script sys.path variations
    from inhouse_social_scraper import scrape_tiktok, InHouseScrapeError

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_EMAIL_EXCLUDES = (
    "example.com", "email.com", "domain.com", "yoursite.com", "yourdomain.com",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", "wixpress.com", "sentry.io",
    "sentry.wixpress", "facebook.com", "twitter.com", "instagram.com",
    "tiktok.com", "shopify.com", "@2x", "u003e",
)

# TikTok Shop category slug -> our canonical pr_brands category
SHOP_CATEGORY_MAP = {
    "beauty-personal-care": "beauty",
    "beauty": "beauty",
    "skincare": "skincare",
    "phones-electronics": "tech",
    "computers-office-equipment": "tech",
    "home-supplies": "home",
    "kitchenware": "home",
    "textiles-soft-furnishings": "home",
    "household-appliances": "home",
    "womenswear-underwear": "fashion",
    "menswear-underwear": "fashion",
    "fashion-accessories": "fashion",
    "shoes": "fashion",
    "luggage-bags": "fashion",
    "jewelry-accessories-derivatives": "jewelry",
    "sports-outdoor": "fitness",
    "toys-hobbies": "lifestyle",
    "furniture": "home",
    "tools-hardware": "home",
    "home-improvement": "home",
    "automotive-motorcycle": "lifestyle",
    "food-beverages": "food",
    "health": "wellness",
    "pet-supplies": "pet",
    "baby-maternity": "baby",
    "muslim-fashion": "fashion",
    "books-magazines-audio": "lifestyle",
    "kids-fashion": "baby",
    "collectibles": "lifestyle",
}


class TikTokShopRegionError(Exception):
    """Raised when TikTok Shop redirects with not_supported_region."""


# ---------------------------------------------------------------------------
# Discovery leg (Playwright)
# ---------------------------------------------------------------------------

def _proxy_config() -> Optional[Dict[str, str]]:
    """Playwright proxy dict from TIKTOK_SHOP_PROXY (falls back to IG_PROXY)."""
    raw = (os.getenv("TIKTOK_SHOP_PROXY") or os.getenv("IG_PROXY") or "").strip().strip('"').strip("'")
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


# Cookies persisted here after a successful crawl so a manually solved
# captcha carries over to later (headless) runs.
_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_ts_shop_state.json")


def _launch_browser(p, headless: bool = True):
    proxy = _proxy_config()
    kwargs = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        kwargs["proxy"] = proxy
        print(f"[TikTokShop] using proxy {proxy['server']}")
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

    owns=True  -> we launched Chromium and must close it.
    owns=False -> we attached to the user's already-running browser over CDP;
                  reuse its logged-in context and never close it.
    """
    if cdp_url:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        print(f"[TikTokShop] attached to existing browser via CDP ({cdp_url})")
        return browser, context, False
    browser, context = _launch_browser(p, headless=headless)
    return browser, context, True


def _save_browser_state(context) -> None:
    try:
        context.storage_state(path=_STATE_FILE)
    except Exception:
        pass


def _walk_for_sellers(node: Any, sellers: Dict[str, Dict], depth: int = 0) -> None:
    """Recursively pull seller/product info out of arbitrary shop API JSON."""
    if depth > 14:
        return
    if isinstance(node, dict):
        seller = node.get("seller") or node.get("seller_info") or {}
        seller_name = None
        seller_id = None
        if isinstance(seller, dict):
            seller_name = seller.get("name") or seller.get("seller_name") or seller.get("shop_name")
            seller_id = str(seller.get("seller_id") or seller.get("id") or "") or None
        seller_name = seller_name or node.get("seller_name") or node.get("shop_name")
        seller_id = seller_id or (str(node.get("seller_id")) if node.get("seller_id") else None)
        product_title = (
            node.get("title") or node.get("product_name") or node.get("name")
            if any(k in node for k in ("product_id", "product_id_str", "spu_id", "sku_list"))
            else None
        )
        if seller_name:
            key = seller_id or seller_name.strip().lower()
            rec = sellers.setdefault(
                key,
                {"seller_name": seller_name.strip(), "seller_id": seller_id,
                 "store_url": None, "products": []},
            )
            if product_title and product_title not in rec["products"]:
                rec["products"].append(str(product_title)[:160])
        for v in node.values():
            _walk_for_sellers(v, sellers, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_for_sellers(v, sellers, depth + 1)


def _sellers_from_dom(page) -> List[Dict]:
    """DOM fallback: store links + product card titles."""
    try:
        cards = page.eval_on_selector_all(
            "a[href*='/shop/store/'], a[href*='/shop/pdp/']",
            """els => els.map(e => ({
                href: e.getAttribute('href') || '',
                text: (e.innerText || '').trim().slice(0, 160)
            }))""",
        )
    except Exception:
        return []
    sellers: Dict[str, Dict] = {}
    for c in cards:
        href = c.get("href") or ""
        m = re.search(r"/shop/store/([^/?#]+)/(\d+)", href)
        if m:
            slug, sid = m.group(1), m.group(2)
            name = re.sub(r"[-_]+", " ", slug).strip().title()
            rec = sellers.setdefault(sid, {"seller_name": name, "seller_id": sid,
                                           "store_url": f"https://www.tiktok.com/shop/store/{slug}/{sid}",
                                           "products": []})
            if c.get("text") and c["text"] not in rec["products"]:
                rec["products"].append(c["text"])
    return list(sellers.values())


class TikTokShopBotWallError(RuntimeError):
    """TikTok served a verification/captcha page instead of shop content."""


def _is_bot_walled(page) -> bool:
    """Detect TikTok's verification/captcha interstitial."""
    url = page.url.lower()
    if "verify" in url or "security-check" in url:
        return True
    try:
        return page.evaluate(
            """() => {
                const els = document.querySelectorAll('#captcha-verify-container, .captcha_verify_container, [class*="captcha"], iframe[src*="verify"]');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 50 && r.height > 50) return true;  // visible, not a stub
                }
                const t = (document.title || '').toLowerCase();
                return t.includes('security check') || t.includes('verify');
            }"""
        )
    except Exception:
        return False


def _handle_bot_wall(page, headless: bool, wait_s: int = 300) -> None:
    """
    On a bot wall: headless -> raise; headful/attached -> give the human time to
    solve the captcha, polling until the interstitial clears. Never closes the
    browser — the caller owns cleanup.
    """
    if not _is_bot_walled(page):
        return
    if headless:
        raise TikTokShopBotWallError(
            "TikTok served a verification/captcha wall (IP likely flagged as VPN/datacenter). "
            "Re-run with --headful to solve it manually, or use a residential TIKTOK_SHOP_PROXY."
        )
    try:
        page.bring_to_front()
    except Exception:
        pass
    print(f"[TikTokShop] >>> CAPTCHA — solve the puzzle in the browser window now ({wait_s}s) <<<")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        if not _is_bot_walled(page):
            print("[TikTokShop] captcha cleared, continuing")
            page.wait_for_timeout(3000)
            _save_browser_state(page.context)
            return
    raise TikTokShopBotWallError("Captcha was not solved in time.")


def discover_categories(headless: bool = True, cdp_url: Optional[str] = None) -> List[Dict]:
    """List TikTok Shop browse categories from /shop (region-gated)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=headless, cdp_url=cdp_url)
        page = context.new_page()
        try:
            page.goto("https://www.tiktok.com/shop", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if "not_supported_region" in page.url:
                raise TikTokShopRegionError(
                    "TikTok Shop is not available from this IP region. "
                    "Set TIKTOK_SHOP_PROXY to a US/UK residential proxy."
                )
            _handle_bot_wall(page, headless and owns)
            html = page.content()
            _save_browser_state(context)
        finally:
            if owns:
                browser.close()
            else:
                page.close()
    cats = []
    for slug, cid in dict.fromkeys(re.findall(r"/shop/c/([a-z0-9-]+)/(\d+)", html)):
        cats.append({
            "slug": slug,
            "id": cid,
            "url": f"https://www.tiktok.com/shop/c/{slug}/{cid}",
            "our_category": SHOP_CATEGORY_MAP.get(slug, "other"),
        })
    return cats


def _store_url_for(seller_id: Optional[str]) -> Optional[str]:
    return f"https://shop.tiktok.com/us/store/x/{seller_id}" if seller_id else None


def _collect_pdp_links(page) -> List[str]:
    """Product detail links rendered on a category/search grid."""
    try:
        return page.evaluate(
            """() => [...new Set([...document.querySelectorAll('a[href*="/pdp/"]')]
                .map(a => a.href))].slice(0, 60)"""
        )
    except Exception:
        return []


def discover_sellers(
    category_url: str,
    *,
    max_scrolls: int = 8,
    max_products: int = 5,
    headless: bool = True,
    debug_dump: Optional[str] = None,
    cdp_url: Optional[str] = None,
) -> List[Dict]:
    """
    Crawl one TikTok Shop category page and return unique sellers:
    [{seller_name, seller_id, store_url, products: [...]}, ...]

    Sellers aren't listed on the category grid — it renders product cards.
    We collect product (PDP) links, then open each one; every PDP fires an
    `api/shop/pdp_desktop/page_data` XHR whose body carries seller_id +
    shop_name for the product and a recommendation carousel of other shops.
    `_walk_for_sellers` harvests all of them.
    """
    from playwright.sync_api import sync_playwright

    api_payloads: List[Dict] = []

    def on_response(resp):
        u = resp.url
        if any(t in u for t in ("/api/shop", "oec", "product", "marketplace", "mall")):
            try:
                body = resp.json()
            except Exception:
                return
            api_payloads.append({"url": u, "body": body})

    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=headless, cdp_url=cdp_url)
        page = context.new_page()
        page.on("response", on_response)
        try:
            page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            if "not_supported_region" in page.url:
                raise TikTokShopRegionError(
                    "TikTok Shop is not available from this IP region. "
                    "Set TIKTOK_SHOP_PROXY to a US/UK residential proxy."
                )

            _handle_bot_wall(page, headless and owns)

            for _ in range(max_scrolls):
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(random.uniform(1000, 1800))

            pdp_links = _collect_pdp_links(page)
            print(f"[TikTokShop] {len(pdp_links)} product links on grid; opening up to {max_products}", flush=True)

            for i, link in enumerate(pdp_links[:max_products]):
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(random.uniform(2500, 4000))
                    # Category page already cleared the wall; a PDP that re-walls
                    # is skipped after a short grace period, never a 5-min block.
                    if _is_bot_walled(page):
                        page.wait_for_timeout(8000)
                        if _is_bot_walled(page):
                            print(f"[TikTokShop] PDP {i+1} still walled — skipping", flush=True)
                            continue
                    print(f"[TikTokShop] captured PDP {i+1}/{min(len(pdp_links), max_products)}", flush=True)
                except Exception:
                    continue

            _save_browser_state(context)
        finally:
            if owns:
                browser.close()
            else:
                page.close()

    if debug_dump:
        with open(debug_dump, "w", encoding="utf-8") as f:
            json.dump(api_payloads, f, indent=1, ensure_ascii=False)
        print(f"[TikTokShop] dumped {len(api_payloads)} API payloads -> {debug_dump}")

    sellers: Dict[str, Dict] = {}
    for payload in api_payloads:
        _walk_for_sellers(payload.get("body"), sellers)

    for s in sellers.values():
        if not s.get("store_url"):
            s["store_url"] = _store_url_for(s.get("seller_id"))
    return list(sellers.values())


# ---------------------------------------------------------------------------
# Enrichment leg (region-independent)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _extract_emails(text: str) -> List[str]:
    emails = _EMAIL_RE.findall((text or ""))
    out = []
    for e in emails:
        el = e.lower()
        if not any(x in el for x in _EMAIL_EXCLUDES) and el not in out:
            out.append(el)
    return out


def _pick_contact_email(emails: List[str]) -> Optional[str]:
    """Prefer PR/press/collab inboxes, then info/hello, then anything."""
    priority = ("pr@", "press@", "collab", "influencer", "partnerships@", "partner@",
                "marketing@", "hello@", "info@", "contact@", "support@")
    for token in priority:
        for e in emails:
            if e.startswith(token) or token in e.split("@")[0]:
                return e
    return emails[0] if emails else None


_SOCIAL_DOMAINS = (
    "tiktok.com", "instagram.com", "facebook.com", "youtube.com", "twitter.com",
    "x.com", "pinterest.com", "linkedin.com", "amazon.", "walmart.", "target.",
    "ebay.", "aliexpress.", "temu.", "wikipedia.org", "reddit.com", "duckduckgo.com",
)

# Link-in-bio aggregators: never a brand's website, never a dedupe identity.
_LINK_IN_BIO_HOSTS = (
    "linktr.ee", "linktree", "beacons.ai", "lnk.bio", "milkshake.app",
    "stan.store", "carrd.co", "bio.site", "hoo.be", "campsite.bio", "tap.bio",
    "shor.by", "linkin.bio", "flowpage.com", "snipfeed.co", "solo.to",
    "komi.io", "allmylinks.com", "linkpop.com", "bit.ly", "linkgenie",
    "direct.me", "withkoji.com", "pillar.io", "liinks.co",
)

# Retailers/marketplaces: a brand sold at Sephora doesn't own sephora.com.
_RETAILER_HOSTS = (
    "sephora.", "ulta.", "nordstrom.", "macys.", "bloomingdales.", "boots.",
    "douglas.", "cultbeauty.", "revolve.", "asos.", "qvc.", "hsn.", "kohls.",
    "walgreens.", "cvs.", "riteaid.", "superdrug.", "lookfantastic.",
    "dermstore.", "spacenk.", "beautylish.", "credobeauty.", "yesstyle.",
    "stylevana.", "sokoglam.", "shein.", "zalando.", "flannels.", "selfridges.",
    "harrods.", "johnlewis.", "next.", "very.", "argos.",
)

# Domain parking / for-sale landers.
_PARKED_HOSTS = (
    "dynadot", "sedo.", "godaddy", "hugedomains", "afternic", "dan.com",
    "parkingcrew", "bodis.", "domainmarket", "undeveloped.", "porkbun",
    "namecheap", "forsale", "domainnamesales", "buydomains", "brandbucket",
)

_PARKED_CONTENT_MARKERS = (
    "domain is for sale", "buy this domain", "domain for sale",
    "this domain may be for sale", "parked domain", "domain parking",
    "make an offer on this domain",
)


_SEARCH_ENGINE_HOSTS = ("bing.", "duckduckgo.", "startpage.", "microsoft.", "go.microsoft", "brave.com", "search.brave")


def is_generic_domain(host: str) -> bool:
    """True when a host can never identify a brand (socials, retailers, parking, link-in-bio)."""
    host = (host or "").lower().removeprefix("www.")
    if not host:
        return True
    return (
        any(s in host for s in _SOCIAL_DOMAINS)
        or any(s in host for s in _LINK_IN_BIO_HOSTS)
        or any(s in host for s in _RETAILER_HOSTS)
        or any(s in host for s in _PARKED_HOSTS)
        or any(s in host for s in _SEARCH_ENGINE_HOSTS)
    )


def _probe_host(host: str, timeout: int = 8) -> Optional[str]:
    """Return canonical https URL if the host responds with a real brand page."""
    if not host:
        return None
    r = None
    try:
        r = requests.head(
            f"https://{host}",
            allow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _UA, "Accept": "text/html"},
        )
    except Exception:
        r = None
    if r is None or r.status_code >= 400:
        # Some hosts drop HEAD or reset the first connection — retry with GET
        try:
            r = requests.get(
                f"https://{host}",
                allow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": _UA, "Accept": "text/html"},
                stream=True,
            )
            r.close()
        except Exception:
            return None
    if r.status_code >= 400:
        return None
    final = (urlparse(r.url).hostname or host).lower().removeprefix("www.")
    if is_generic_domain(final):
        return None
    return f"https://{final}"


def _domain_candidates(brand_name: str) -> List[str]:
    base = re.sub(r"[^a-z0-9]", "", (brand_name or "").lower())
    dashed = re.sub(r"[^a-z0-9]+", "-", (brand_name or "").lower()).strip("-")
    if not base or len(base) < 3:
        return []
    cands = [
        f"{base}.com",
        f"{dashed}.com" if dashed != base else None,
        f"{base}cosmetics.com",
        f"{base}beauty.com",
        f"{base}official.com",
        f"get{base}.com",
        f"shop{base}.com",
        f"{base}.co",
        f"{base}.shop",
    ]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def resolve_website_via_search(brand_name: str, timeout: int = 12) -> Optional[str]:
    """
    Resolve a brand's official website.
    1) Probe likely domains (brand.com, brandcosmetics.com, …) — fastest & most reliable
    2) Fall back to Brave / Bing SERP HTML when domain guessing misses
    """
    if not brand_name:
        return None

    token = _norm(brand_name)[:12]
    for host in _domain_candidates(brand_name):
        url = _probe_host(host, timeout=min(timeout, 8))
        if not url:
            continue
        # Prefer hosts that contain the brand token
        host_only = (urlparse(url).hostname or "").removeprefix("www.")
        if token and token in _norm(host_only):
            return url
        # Accept first live host if no stronger match appears later
        # (e.g. short names that redirect correctly)
        if token and token in _norm(host):
            return url
        time.sleep(random.uniform(0.15, 0.4))

    from urllib.parse import quote_plus, unquote

    query = f"{brand_name} official website"
    if len(brand_name.strip()) <= 8:
        query = f"{brand_name} brand official website"
    q = quote_plus(query)
    engines = [
        f"https://search.brave.com/search?q={q}",
        f"https://www.bing.com/search?q={q}&setlang=en",
        f"https://html.duckduckgo.com/html/?q={q}",
    ]
    best = None
    for url in engines:
        try:
            r = requests.get(
                url,
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html"},
                timeout=timeout,
            )
            if r.status_code != 200 or not r.text:
                continue
            html = r.text
            candidates = []
            for href in re.findall(r'href="(https?://[^"]+)"', html):
                candidates.append(href)
            for wrapped in re.findall(r'uddg=([^"&]+)', html):
                candidates.append(unquote(wrapped))
            for cite in re.findall(r"<cite[^>]*>(.*?)</cite>", html, re.S):
                candidates.append(re.sub(r"<[^>]+>", "", cite).strip())
            for c in candidates:
                if not c.startswith("http"):
                    c = "https://" + c.strip().rstrip("/")
                host = (urlparse(c).hostname or "").lower().removeprefix("www.")
                if not host or is_generic_domain(host):
                    continue
                if token and token in _norm(host):
                    return f"https://{host}"
                if best is None:
                    best = f"https://{host}"
            if best and token and token in _norm(best):
                return best
        except Exception:
            continue
        time.sleep(random.uniform(0.3, 0.8))
    return best


_ASSET_HOSTS = (
    "googleapis.com", "gstatic.com", "cloudfront.net", "jsdelivr", "cdn.",
    "shopifycdn", "unpkg.com", "cloudflare", "googletagmanager",
    "google-analytics", "apple.com", "play.google", "onelink", "app.link",
    "fonts.", "typekit", "segment.com", "sentry", "branch.io", "google.com",
    "youtu.be", "spotify.com", "apple.co", "smart.link", "shopify.com",
)


def _resolve_link_in_bio(url: str, brand_name: str = "", timeout: int = 12) -> Optional[str]:
    """Extract the brand's real website from a linktr.ee-style aggregator page."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        html = r.text
        # Outbound links live in the SSR JSON ("url":"…") and in anchors.
        candidates = re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', html)
        candidates += re.findall(r'href="(https?://[^"]+)"', html)
        token = _norm(brand_name)[:12]
        best = None
        for c in candidates:
            host = (urlparse(c).hostname or "").lower().removeprefix("www.")
            if not host or is_generic_domain(host) or any(a in host for a in _ASSET_HOSTS):
                continue
            if token and token in _norm(host):
                return f"https://{host}"
            if best is None:
                best = f"https://{host}"
        return best
    except Exception:
        return None


def _scrape_website_meta(website: str, timeout: int = 12) -> Dict[str, Optional[str]]:
    """Pull og:description / meta description from a brand homepage.

    Sets out["parked"] = True when the page is a domain-parking lander so the
    caller can discard the website entirely.
    """
    out = {"description": None, "email": None, "parked": False}
    if not website:
        return out
    if not website.startswith("http"):
        website = "https://" + website
    try:
        r = requests.get(
            website,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return out
        html = r.text
        low = html[:20000].lower()
        if any(marker in low for marker in _PARKED_CONTENT_MARKERS):
            out["parked"] = True
            return out
        for pat in (
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        ):
            m = re.search(pat, html, re.I)
            if m:
                desc = re.sub(r"\s+", " ", m.group(1)).strip()
                if len(desc) >= 20:
                    out["description"] = unescape(desc)[:500]
                    break
        emails = _extract_emails(html)
        out["email"] = _pick_contact_email(emails)
    except Exception:
        pass
    return out


def _scrape_website_for_email(website: str, timeout: int = 12) -> Optional[str]:
    """Fetch homepage + common contact pages, return the best contact email."""
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website
    base = website.rstrip("/")
    paths = ["", "/pages/contact", "/pages/contact-us", "/contact", "/contact-us", "/pages/pr", "/pages/about"]
    found: List[str] = []
    for path in paths:
        try:
            r = requests.get(
                base + path,
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                timeout=timeout,
            )
            if r.status_code != 200:
                continue
            for e in _extract_emails(r.text):
                if e not in found:
                    found.append(e)
            if found:
                break
        except Exception:
            continue
        time.sleep(random.uniform(0.4, 1.0))
    return _pick_contact_email(found)


_HANDLE_SUFFIXES = (
    "beauty", "cosmetics", "haircare", "skincare", "makeup", "official",
    "shop", "store", "us", "usa", "brand",
)


def _handle_candidates(seller: Dict) -> List[str]:
    """Guess the seller's TikTok @handle from the store name/slug."""
    cands: List[str] = []
    name = seller.get("seller_name") or ""
    store_url = seller.get("store_url") or ""
    m = re.search(r"/shop/store/([^/?#]+)/", store_url)
    if m:
        slug = m.group(1).lower()
        cands.extend([slug.replace("-", ""), slug.replace("-", "_"), slug.replace("-", ".")])
    base = _norm(name)
    if base:
        cands.append(base)
        dotted = re.sub(r"\s+", ".", name.strip().lower())
        under = re.sub(r"\s+", "_", name.strip().lower())
        cands.extend([re.sub(r"[^a-z0-9._]", "", dotted), re.sub(r"[^a-z0-9._]", "", under)])
        # Brands often drop the category suffix on TikTok (@theouai, not @ouaihaircare)
        for suffix in _HANDLE_SUFFIXES:
            if base.endswith(suffix) and len(base) - len(suffix) >= 3:
                stripped = base[: -len(suffix)]
                cands.extend([stripped, f"the{stripped}", f"{stripped}beauty"])
                break
        cands.extend([f"the{base}", f"{base}official"])
    seen, out = set(), []
    for c in cands:
        c = c.strip(".")
        if c and 2 <= len(c) <= 24 and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:6]


def _profile_matches_seller(profile: Dict, seller_name: str) -> bool:
    nick = _norm(profile.get("nickname") or "")
    uid = _norm(profile.get("uniqueId") or "")
    target = _norm(seller_name)
    if not target:
        return False
    if target in (nick, uid):
        return True

    # Containment only counts when the contained string is substantial —
    # otherwise @elf "matches" elfcosmetics with no evidence it's the brand.
    def contains(haystack: str, needle: str) -> bool:
        return len(needle) >= 4 and needle in haystack

    return (
        contains(nick, target) or contains(target, nick)
        or contains(uid, target) or contains(target, uid)
    )


def enrich_seller(seller: Dict, *, scrape_website: bool = True) -> Dict:
    """
    Turn a discovered seller into a brand record ready for pr_brands insert.
    Never raises — enrichment failures leave fields as None.
    """
    record = {
        "brand_name": (seller.get("seller_name") or "").strip(),
        "tiktok_handle": None,
        "website": None,
        "description": None,
        "contact_email": None,
        "hero_product": (seller.get("products") or [None])[0],
        "products": seller.get("products") or [],
        "store_url": seller.get("store_url"),
        "seller_id": seller.get("seller_id"),
        "followers": None,
    }

    profile = None
    for handle in _handle_candidates(seller):
        try:
            candidate = scrape_tiktok(handle, results_limit=3)
        except InHouseScrapeError:
            continue
        except Exception:
            continue
        if candidate and _profile_matches_seller(candidate, record["brand_name"]):
            profile = candidate
            record["tiktok_handle"] = candidate.get("uniqueId") or handle
            break
        time.sleep(random.uniform(0.5, 1.2))

    if profile:
        bio = (profile.get("signature") or "").strip()
        if bio:
            record["description"] = bio
        link = (profile.get("bioLink") or "").strip()
        if link:
            if not link.startswith("http"):
                link = "https://" + link
            host = (urlparse(link).hostname or "").lower().removeprefix("www.")
            if any(s in host for s in _LINK_IN_BIO_HOSTS):
                # linktr.ee etc: follow it to the brand's real site
                record["website"] = (
                    _resolve_link_in_bio(link, record["brand_name"]) if scrape_website else None
                )
            elif not is_generic_domain(host):
                record["website"] = f"https://{host}"
        record["followers"] = profile.get("followerCount")
        bio_email = _pick_contact_email(_extract_emails(bio))
        if bio_email:
            record["contact_email"] = bio_email

    if not record["website"] and scrape_website:
        record["website"] = resolve_website_via_search(record["brand_name"])

    if scrape_website and record["website"]:
        meta = _scrape_website_meta(record["website"])
        if meta.get("parked"):
            # Parking lander slipped through host checks — not the brand's site.
            record["website"] = None
        else:
            if not record["description"] and meta.get("description"):
                record["description"] = meta["description"]
            if not record["contact_email"] and meta.get("email"):
                record["contact_email"] = meta["email"]
            if not record["contact_email"]:
                record["contact_email"] = _scrape_website_for_email(record["website"])

    return record
