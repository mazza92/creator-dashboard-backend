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


_SHORT_SQL = r'(vm|vt)\.tiktok\.com/|tiktok\.com/t/'
MAX_LINKS_PER_RUN = 24


def fix_kit_share_links(get_conn, follow=None, max_links=MAX_LINKS_PER_RUN) -> dict:
    """Expands share links in saved kits, a few kits per call (run from the 10-minute TikTok media cron).

    Reads with one short connection, resolves with no connection open (a connection held across these requests
    gets closed and broke the public kit page), then writes with a fresh connection."""
    from psycopg2.extras import Json

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, kit_theme FROM creators WHERE kit_theme IS NOT NULL "
            "AND ((kit_theme->'example_posts')::text ~* %s OR (kit_theme->'examples')::text ~* %s) "
            "ORDER BY random() LIMIT 20",
            (_SHORT_SQL, _SHORT_SQL),
        )
        rows = cur.fetchall()
        conn.rollback()
    finally:
        conn.close()

    picked, budget = [], max_links
    for row in rows:
        creator_id, theme = row[0], row[1]
        if not isinstance(theme, dict):
            continue
        n = sum(1 for p in (theme.get('example_posts') or []) if isinstance(p, dict) and is_short_tiktok(p.get('url')))
        if picked and n > budget:
            break
        picked.append((creator_id, theme))
        budget -= n
        if budget <= 0:
            break

    fixed = []
    for creator_id, theme in picked:
        new_theme, changed = expand_kit_examples(theme, follow=follow)
        if changed:
            fixed.append((creator_id, new_theme))
    if fixed:
        conn = get_conn()
        try:
            cur = conn.cursor()
            for creator_id, new_theme in fixed:
                cur.execute(
                    "UPDATE creators SET kit_theme = COALESCE(kit_theme, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                    (Json({'example_posts': new_theme['example_posts'], 'examples': new_theme['examples']}),
                     creator_id),
                )
            conn.commit()
        finally:
            conn.close()
    return {'kits_checked': len(picked), 'kits_fixed': len(fixed)}
