# -*- coding: utf-8 -*-
"""
Scrape a brand homepage for a working logo and cover image.

Designed for Shopify (and similar) stores where img src often contains
unreplaced Liquid placeholders like `{width}` / `%7Bwidth%7D`.

One HTML fetch per site. Validates only the top few candidates.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT = 6
VALIDATE_TIMEOUT = 4
COVER_WIDTH = 1400
LOGO_WIDTH = 400
LOGO_DEV_TOKEN = os.getenv("LOGO_DEV_TOKEN", "pk_X-HmAbFVSiG2s0wH0OtqCw")
NO_RETRY_STATUS = {400, 401, 402, 403, 404, 410, 451}

JUNK_URL_PARTS = (
    "favicon",
    "ajaxloader",
    "spinner",
    "placeholder",
    "pixel.gif",
    "1x1",
    "blank.gif",
    "spacer",
    "tracking",
    "grabbing.png",
    "parking-page",
    "parking-p",
    "/universal/images-v6/",
    "icon-sprite",
    "sprite.svg",
    "data:image/svg+xml",
    "/extensions/",
    "withdrawal",
    "widerruf",
    "cookie-consent",
    "cookiebot",
    "shopify_pay",
    "recaptcha",
)
JUNK_COVER_PARTS = (
    "logo",
    "icon",
    "favicon",
    "_32x32",
    "_16x16",
    "_64x64",
    "apple-touch",
)
SHOPIFY_TINY_SIZE = re.compile(r"_(\d+)x(\d+)?(?=\.|$)")
LIQUID_WIDTH = re.compile(r"%7Bwidth%7D|\{width\}", re.I)
LIQUID_HEIGHT = re.compile(r"%7Bheight%7D|\{height\}", re.I)
SRCSET_ITEM = re.compile(r"(\S+)\s+(\d+)[wx]", re.I)
BROKEN_COVER_MARKERS = (
    "{width}",
    "{height}",
    "%7Bwidth%7D",
    "%7Bheight%7D",
    "parking-page",
    "parking-p",
    "/universal/images-v6/",
)


def is_broken_cover_url(url: Optional[str]) -> bool:
    if not url:
        return True
    lowered = url.lower()
    return any(marker.lower() in lowered for marker in BROKEN_COVER_MARKERS)


def scrape_brand_images(website: str, *, logo_only: bool = False) -> Dict[str, Optional[str]]:
    """
    Return {"logo_url": str|None, "cover_image_url": str|None}.
    Logos are resolved even when the brand site is down (logo.dev / unavatar / Google).
    Never raises.
    """
    result = {"logo_url": None, "cover_image_url": None}
    html, final_url = (None, website)
    if not logo_only:
        html, final_url = _fetch_homepage(website)

    if html:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            on_page = _pick_first_live(_logo_candidates(soup, final_url), limit=3)
            if on_page:
                result["logo_url"] = on_page
            if not logo_only:
                result["cover_image_url"] = _pick_first_live(
                    _cover_candidates(soup, final_url), limit=4
                )
        except Exception as exc:
            print(f"[Images] Parse failed for {website}: {exc}")

    if not result["logo_url"]:
        result["logo_url"] = logo_from_domain(final_url or website)
    return result


def logo_from_domain(website: str) -> Optional[str]:
    """Fast logo lookup that does not need the brand homepage."""
    domain = _domain(website)
    if not domain:
        return None
    candidates = [
        f"https://unavatar.io/{domain}?fallback=false",
        f"https://img.logo.dev/{domain}?token={LOGO_DEV_TOKEN}&format=png&size=256",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
    ]
    for url in candidates:
        if _url_is_live_image(url):
            return url
    return candidates[-1]


def _domain(website: str) -> Optional[str]:
    raw = (website or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.replace("www.", "").split(":")[0].lower()
    if not host or "." not in host:
        return None
    return host


UNAVAILABLE_BODY_MARKERS = (
    "this store is currently unavailable",
    "store unavailable",
    "shop-404",
    "this shop is unavailable",
    "password-protected",
    "enter using password",
    "this shop is password protected",
)


def check_website_status(website: str) -> Dict[str, Optional[object]]:
    """
    Probe a brand site. Dead Shopify stores return 402 and
    "This store is currently unavailable."
    """
    url = (website or "").strip()
    if not url:
        return {"ok": False, "reason": "no_website", "status_code": None}
    if not url.startswith("http"):
        url = "https://" + url
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        code = response.status_code
        body = (response.text or "")[:8000].lower()
        if code == 429 or code == 503:
            return {"ok": True, "reason": "rate_limited", "status_code": code}
        if code == 402 or any(marker in body for marker in UNAVAILABLE_BODY_MARKERS):
            return {"ok": False, "reason": "shopify_unavailable", "status_code": code}
        if code in (401, 403) and ("password" in body or "myshopify" in body):
            return {"ok": False, "reason": "shopify_password", "status_code": code}
        if code >= 400:
            return {"ok": False, "reason": f"http_{code}", "status_code": code}
        return {"ok": True, "reason": None, "status_code": code}
    except requests.exceptions.SSLError:
        return {"ok": False, "reason": "ssl_error", "status_code": None}
    except requests.exceptions.Timeout:
        return {"ok": False, "reason": "timeout", "status_code": None}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "reason": "connection_error", "status_code": None}
    except Exception as exc:
        return {"ok": False, "reason": f"error:{exc.__class__.__name__}", "status_code": None}


def _fetch_homepage(website: str) -> Tuple[Optional[str], Optional[str]]:
    url = (website or "").strip()
    if not url:
        return None, None
    if not url.startswith("http"):
        url = "https://" + url
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code in NO_RETRY_STATUS:
            return None, response.url or url
        if response.status_code >= 400:
            return None, response.url or url
        return response.text, response.url or url
    except Exception:
        return None, url


def _absolute(url: Optional[str], base: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip().strip("'\"")
    if not url or url.startswith("data:"):
        return None
    if url.startswith("//"):
        url = "https:" + url
    return urljoin(base, url)


def normalize_image_url(url: Optional[str], base: str, *, target_width: int = COVER_WIDTH) -> Optional[str]:
    url = _absolute(url, base)
    if not url:
        return None
    url = LIQUID_WIDTH.sub(str(target_width), url)
    url = LIQUID_HEIGHT.sub("800", url)
    parsed = urlparse(url)
    path = parsed.path

    def _bump_size(match: re.Match) -> str:
        width = int(match.group(1))
        if width < 800:
            return f"_{target_width}x"
        return match.group(0)

    path = SHOPIFY_TINY_SIZE.sub(_bump_size, path)
    path = re.sub(r"_(?:pico|icon|thumb|small|compact|medium|large)(?=\.|$)", f"_{target_width}x", path)

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    host = (parsed.netloc or "").lower()
    if "cdn.shopify.com" in host or "/cdn/shop/" in path:
        if "width" not in query:
            query["width"] = str(target_width)

    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def _is_junk(url: str, *, for_cover: bool) -> bool:
    lowered = url.lower()
    if any(part in lowered for part in JUNK_URL_PARTS):
        return True
    if lowered.endswith(".gif") or ".gif?" in lowered:
        return True
    if for_cover and any(part in lowered for part in JUNK_COVER_PARTS):
        return True
    if "{width}" in lowered or "%7bwidth%7d" in lowered:
        return True
    return False


def _meta(soup, property: Optional[str] = None, name: Optional[str] = None) -> Optional[str]:
    attrs = {}
    if property:
        attrs["property"] = property
    if name:
        attrs["name"] = name
    if not attrs:
        return None
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _largest_srcset(srcset: Optional[str], base: str, target_width: int) -> Optional[str]:
    if not srcset:
        return None
    best_url = None
    best_size = -1
    for match in SRCSET_ITEM.finditer(srcset):
        size = int(match.group(2))
        if size > best_size:
            best_size = size
            best_url = match.group(1)
    if not best_url:
        first = srcset.split(",")[0].strip().split(" ")[0]
        best_url = first or None
    return normalize_image_url(best_url, base, target_width=target_width)


def _img_url(img, base: str, target_width: int) -> Optional[str]:
    srcset = img.get("srcset") or img.get("data-srcset") or img.get("data-bgset")
    from_srcset = _largest_srcset(srcset, base, target_width)
    if from_srcset:
        return from_srcset
    src = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-lazy-src")
        or img.get("data-original")
        or img.get("data-bg")
    )
    return normalize_image_url(src, base, target_width=target_width)


def _jsonld_blocks(soup) -> List[dict]:
    blocks = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            blocks.extend([item for item in data if isinstance(item, dict)])
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                blocks.extend([item for item in data["@graph"] if isinstance(item, dict)])
            else:
                blocks.append(data)
    return blocks


def _jsonld_url(value) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("url") or value.get("contentUrl") or value.get("@id")
    if isinstance(value, list) and value:
        return _jsonld_url(value[0])
    return None


def _cover_candidates(soup, base: str) -> List[str]:
    seen = set()
    ordered: List[str] = []

    def add(url: Optional[str]) -> None:
        url = normalize_image_url(url, base, target_width=COVER_WIDTH)
        if not url or url in seen or _is_junk(url, for_cover=True):
            return
        seen.add(url)
        ordered.append(url)

    add(_meta(soup, property="og:image:secure_url"))
    add(_meta(soup, property="og:image"))
    add(_meta(soup, name="og:image"))
    add(_meta(soup, name="twitter:image"))
    add(_meta(soup, property="twitter:image"))
    link_img = soup.find("link", rel="image_src")
    if link_img:
        add(link_img.get("href"))

    for block in _jsonld_blocks(soup):
        add(_jsonld_url(block.get("image")))
        add(_jsonld_url(block.get("primaryImageOfPage")))

    hero_selectors = (
        "[data-hero] img",
        ".slideshow img",
        ".slideshow__slide img",
        ".banner img",
        ".hero img",
        "section.hero img",
        ".image-banner img",
        ".slideshow-wrapper img",
        ".carousel img",
        ".swiper-slide img",
    )
    for selector in hero_selectors:
        for img in soup.select(selector)[:4]:
            add(_img_url(img, base, COVER_WIDTH))

    # Fallback: large homepage images (skip when og:image was a padded logo)
    for img in soup.find_all("img")[:40]:
        url = _img_url(img, base, COVER_WIDTH)
        if not url or url in seen:
            continue
        if _is_junk(url, for_cover=True):
            continue
        if _looks_large(url):
            add(url)
        if len(ordered) >= 8:
            break

    return ordered


def _looks_large(url: str) -> bool:
    path = urlparse(url).path.lower()
    match = SHOPIFY_TINY_SIZE.search(path)
    if match and int(match.group(1)) >= 800:
        return True
    if "/cdn/shop/files/" in path and not any(part in path for part in ("logo", "icon", "favicon")):
        return True
    query = urlparse(url).query.lower()
    width_q = re.search(r"width=(\d+)", query)
    if width_q and int(width_q.group(1)) >= 800:
        return True
    return False


def _logo_candidates(soup, base: str) -> List[str]:
    seen = set()
    ordered: List[str] = []

    def add(url: Optional[str]) -> None:
        url = normalize_image_url(url, base, target_width=LOGO_WIDTH)
        if not url or url in seen or _is_junk(url, for_cover=False):
            return
        if url.lower().endswith(".gif") or ".gif?" in url.lower():
            return
        seen.add(url)
        ordered.append(url)

    for block in _jsonld_blocks(soup):
        types = block.get("@type")
        type_text = " ".join(types) if isinstance(types, list) else str(types or "")
        if re.search(r"Organization|Brand|LocalBusiness|Store", type_text, re.I):
            add(_jsonld_url(block.get("logo")))

    logo_selectors = (
        "img.header__heading-logo",
        ".header__heading-logo",
        ".header__logo img",
        ".site-header__logo img",
        "a.header__heading img",
        ".logo img",
        "a.logo img",
        "header a[href='/'] img",
        ".site-header img.logo",
        "img[itemprop='logo']",
        "img[alt*='logo' i]",
        "img[class*='logo' i]",
        "img[id*='logo' i]",
    )
    for selector in logo_selectors:
        for img in soup.select(selector)[:3]:
            add(_img_url(img, base, LOGO_WIDTH))

    for rel in ("apple-touch-icon", "apple-touch-icon-precomposed", "icon"):
        for link in soup.find_all("link", rel=lambda value: value and rel in (value if isinstance(value, list) else [value])):
            href = link.get("href")
            sizes = (link.get("sizes") or "").split("x")[0]
            try:
                size_n = int(sizes)
            except (TypeError, ValueError):
                size_n = 0
            if rel == "icon" and size_n and size_n < 64:
                continue
            add(href)

    # Filename contains "logo" but skip app widgets
    for img in soup.find_all("img")[:40]:
        src = img.get("src") or img.get("data-src") or ""
        if "logo" in src.lower():
            add(_img_url(img, base, LOGO_WIDTH))

    return ordered


def _pick_first_live(urls: List[str], *, for_cover: bool = False, limit: int = 4) -> Optional[str]:
    for url in urls[:limit]:
        if _url_is_live_image(url):
            return url
    return None


def _url_is_live_image(url: str) -> bool:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
            timeout=VALIDATE_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        status = response.status_code
        content_type = (response.headers.get("Content-Type") or "").lower()
        if status >= 400:
            response.close()
            return False
        if "text/html" in content_type or "application/json" in content_type:
            response.close()
            return False
        chunk = next(response.iter_content(1024), b"")
        response.close()
        if not chunk:
            return False
        if content_type.startswith("image/") or "octet-stream" in content_type:
            return True
        if chunk.startswith(b"\xff\xd8\xff") or chunk.startswith(b"\x89PNG") or chunk.startswith(b"RIFF") or chunk.startswith(b"GIF"):
            return True
        if chunk[:20].lstrip().startswith(b"<svg") or b"<svg" in chunk[:200]:
            return True
        return False
    except Exception:
        return False
