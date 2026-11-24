"""Expand TikTok share links in portfolio (kit) examples to full post URLs.

Creators often paste the link from TikTok's Share button (tiktok.com/t/..., vm.tiktok.com/..., vt.tiktok.com/...).
Those carry no video id, so the kit can't load the stored video or its cover and the tile renders empty.
They redirect to https://www.tiktok.com/@handle/video/<id>, so we follow the redirect once and keep the full URL.
"""

import re
from concurrent.futures import ThreadPoolExecutor

SHORT_TIKTOK = re.compile(r'^https?://(?:(?:vm|vt)\.tiktok\.com/|(?:www\.|m\.)?tiktok\.com/t/)', re.I)
_FULL_TIKTOK = re.compile(r'tiktok\.com/@([\w.-]+)/(video|photo)/(\d+)', re.I)
RESOLVE_TIMEOUT = 5


def is_short_tiktok(url) -> bool:
    return bool(SHORT_TIKTOK.match(str(url or '').strip()))


def canonical_tiktok(url):
    """https://www.tiktok.com/@handle/video/<id> from any full TikTok post URL, else None."""
    m = _FULL_TIKTOK.search(str(url or ''))
    if not m:
        return None
    return f'https://www.tiktok.com/@{m.group(1)}/{m.group(2).lower()}/{m.group(3)}'


def _follow_redirect(url):
    import requests

    resp = requests.get(url, allow_redirects=True, timeout=RESOLVE_TIMEOUT,
                        headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}, stream=True)
    try:
        return resp.url
    finally:
        resp.close()


def resolve_short_links(urls, follow=None) -> dict:
    """{short_url: full_url} for the share links that resolved. Failures are left out (the link is kept as is)."""
    follow = follow or _follow_redirect
    todo = sorted({u for u in urls if is_short_tiktok(u)})
    if not todo:
        return {}

    def one(u):
        try:
            return u, canonical_tiktok(follow(u))
        except Exception:  # noqa: BLE001
            return u, None

    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
        return {u: full for u, full in pool.map(one, todo) if full}


def expand_kit_examples(theme, follow=None):
    """(theme, changed): example_posts / examples with TikTok share links swapped for full URLs."""
    if not isinstance(theme, dict):
        return theme, False
    posts = theme.get('example_posts') if isinstance(theme.get('example_posts'), list) else []
    examples = theme.get('examples') if isinstance(theme.get('examples'), list) else []
    urls = [p.get('url') for p in posts if isinstance(p, dict)] + [e for e in examples if isinstance(e, str)]
    mapping = resolve_short_links(urls, follow=follow)
    if not mapping:
        return theme, False
    out = dict(theme)
    out['example_posts'] = [
        {**p, 'url': mapping.get(p.get('url'), p.get('url'))} if isinstance(p, dict) else p for p in posts
    ]
    out['examples'] = [mapping.get(e, e) if isinstance(e, str) else e for e in examples]
    return out, True


def has_short_links(theme) -> bool:
    if not isinstance(theme, dict):
        return False
    posts = theme.get('example_posts') if isinstance(theme.get('example_posts'), list) else []
    examples = theme.get('examples') if isinstance(theme.get('examples'), list) else []
    return any(is_short_tiktok(p.get('url')) for p in posts if isinstance(p, dict)) or any(
        is_short_tiktok(e) for e in examples if isinstance(e, str))
