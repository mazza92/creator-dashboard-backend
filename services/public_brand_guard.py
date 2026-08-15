"""Guard public brand APIs: strip gated fields, slow down obvious scrapers."""

import hashlib
import os
import time
from functools import wraps

_SCRAPER_UA_MARKERS = (
    'python-requests',
    'python-urllib',
    'curl/',
    'wget/',
    'scrapy',
    'httpie',
    'go-http-client',
    'libwww-perl',
    'aiohttp',
    'httpx',
)
_SEARCH_ENGINE_MARKERS = (
    'googlebot',
    'bingbot',
    'slurp',
    'duckduckbot',
    'baiduspider',
    'yandex',
    'facebookexternalhit',
    'twitterbot',
    'linkedinbot',
    'applebot',
)
_OWN_ORIGINS = (
    'https://newcollab.co',
    'https://www.newcollab.co',
    'https://app.newcollab.co',
    'http://localhost:3000',
)

_rate_hits = {}
SCRAPER_LIMIT = 30
SCRAPER_WINDOW = 60


def _headers():
    from flask import request
    return request.headers


def _client_ip():
    from flask import request
    forwarded = request.headers.get('X-Forwarded-For') or ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.headers.get('X-Real-IP') or request.remote_addr or ''


def _ua():
    return (_headers().get('User-Agent') or '').lower()


def is_search_engine():
    ua = _ua()
    return any(marker in ua for marker in _SEARCH_ENGINE_MARKERS)


def is_own_frontend():
    origin = (_headers().get('Origin') or '').rstrip('/')
    referer = _headers().get('Referer') or ''
    if origin in _OWN_ORIGINS:
        return True
    return any(referer.startswith(o) for o in _OWN_ORIGINS)


def is_scraper_ua():
    if is_search_engine() or is_own_frontend():
        return False
    ua = _ua()
    if not ua:
        return False
    return any(marker in ua for marker in _SCRAPER_UA_MARKERS)


def _ip_bucket():
    ip = _client_ip()
    salt = os.getenv('IP_HASH_SALT', 'public-brands-salt')
    return hashlib.sha256(f'{salt}:{ip}'.encode()).hexdigest()[:32]


def scraper_rate_limit(view_fn):
    """Throttle curl/python scrapers. Browsers, our site, and Google pass through."""

    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        from flask import jsonify
        if not is_scraper_ua():
            return view_fn(*args, **kwargs)

        bucket = _ip_bucket()
        now = time.time()
        hits = [t for t in _rate_hits.get(bucket, []) if now - t < SCRAPER_WINDOW]
        if len(hits) >= SCRAPER_LIMIT:
            return jsonify({
                'error': 'Too many requests. Sign up at https://app.newcollab.co to browse brands.',
            }), 429
        hits.append(now)
        _rate_hits[bucket] = hits
        if len(_rate_hits) > 5000:
            stale = [k for k, v in _rate_hits.items() if not v or now - v[-1] > SCRAPER_WINDOW]
            for key in stale[:1000]:
                _rate_hits.pop(key, None)
        return view_fn(*args, **kwargs)

    return wrapped


def strip_gated_brand_fields(brand):
    """Remove PR emails and application URLs from a public brand payload."""
    if not brand:
        return brand
    data = dict(brand)
    has_email = bool(data.get('pr_contact_email') or data.get('contact_email'))
    has_form = bool(data.get('application_url') or data.get('application_form_url'))
    for key in (
        'pr_contact_email',
        'pr_manager_name',
        'application_url',
        'application_form_url',
        'contact_email',
    ):
        data.pop(key, None)
    data['hasEmailContact'] = has_email
    data['hasApplication'] = has_form
    structured = data.get('structuredData')
    if isinstance(structured, dict):
        contact = structured.get('applicationContact')
        if isinstance(contact, dict):
            contact['email'] = None
            contact['name'] = None
    return data
