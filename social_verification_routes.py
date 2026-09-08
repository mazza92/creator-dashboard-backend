"""
Social Verification Routes for Creator Onboarding
Handles Instagram/TikTok verification with 5-gate system via public profile fetching
"""

from flask import Blueprint, request, jsonify, session, redirect, url_for
import os
import requests
import json
import secrets
import hashlib
import base64
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote, unquote
import re
from psycopg2.extras import RealDictCursor

# Import public profile fetcher
from social_profile_fetcher import fetch_instagram_profile, fetch_tiktok_profile, ProfileFetchError

# Optional: Token encryption (requires cryptography package)
try:
    from cryptography.fernet import Fernet
    ENCRYPTION_KEY = os.getenv('SOCIAL_TOKEN_ENCRYPTION_KEY')
    if ENCRYPTION_KEY:
        fernet = Fernet(ENCRYPTION_KEY.encode())
    else:
        fernet = None
except ImportError:
    fernet = None


def _log(msg):
    """ASCII-safe logging — Windows cp1252 consoles crash on emoji prints (→ 500)."""
    try:
        print(str(msg).encode('ascii', 'replace').decode('ascii'))
    except Exception:
        pass


# ============================================================================
# CONFIGURATION
# ============================================================================

# Serve only US, UK, CA, AU, NZ, and Europe. Everything else is blocked on
# signup, login, and onboarding. Empty/unknown geo still fail-opens
# (localhost, lookup timeout) so local dev and flaky IP APIs do not lock people out.
ALLOWED_REGIONS = frozenset({
    'US', 'GB', 'CA', 'AU', 'NZ',
    # EU-27
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU',
    'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
    # EFTA + microstates + UK crown dependencies
    'IS', 'LI', 'NO', 'CH', 'AD', 'MC', 'SM', 'VA', 'IM', 'JE', 'GG', 'GI', 'AX', 'FO',
    # Rest of Europe (not RU/BY/TR)
    'AL', 'BA', 'MK', 'ME', 'RS', 'XK', 'UA', 'MD',
})

# Full names historically stored on users.country (CountryDropdown).
# Used to skip the IP lookup on login for accounts already known to be allowed.
ALLOWED_COUNTRY_NAMES = {
    'united states': 'US', 'united states of america': 'US', 'usa': 'US', 'america': 'US',
    'united kingdom': 'GB', 'great britain': 'GB', 'england': 'GB', 'scotland': 'GB',
    'wales': 'GB', 'northern ireland': 'GB', 'uk': 'GB',
    'australia': 'AU',
    'canada': 'CA',
    'new zealand': 'NZ',
    'ireland': 'IE', 'republic of ireland': 'IE',
    'france': 'FR', 'germany': 'DE', 'spain': 'ES', 'italy': 'IT', 'portugal': 'PT',
    'netherlands': 'NL', 'the netherlands': 'NL', 'holland': 'NL',
    'belgium': 'BE', 'austria': 'AT', 'switzerland': 'CH', 'sweden': 'SE',
    'norway': 'NO', 'denmark': 'DK', 'finland': 'FI', 'iceland': 'IS',
    'poland': 'PL', 'czech republic': 'CZ', 'czechia': 'CZ', 'hungary': 'HU',
    'romania': 'RO', 'bulgaria': 'BG', 'greece': 'GR', 'croatia': 'HR',
    'slovakia': 'SK', 'slovenia': 'SI', 'lithuania': 'LT', 'latvia': 'LV',
    'estonia': 'EE', 'luxembourg': 'LU', 'malta': 'MT', 'cyprus': 'CY',
    'liechtenstein': 'LI', 'andorra': 'AD', 'monaco': 'MC', 'san marino': 'SM',
    'vatican': 'VA', 'vatican city': 'VA',
    'albania': 'AL', 'bosnia': 'BA', 'bosnia and herzegovina': 'BA',
    'north macedonia': 'MK', 'macedonia': 'MK', 'montenegro': 'ME',
    'serbia': 'RS', 'kosovo': 'XK', 'ukraine': 'UA', 'moldova': 'MD',
    'gibraltar': 'GI', 'isle of man': 'IM', 'jersey': 'JE', 'guernsey': 'GG',
}

# Minimum thresholds
MIN_FOLLOWERS = 500
MIN_POSTS = 5

# Instagram OAuth (via Facebook Login for Business)
INSTAGRAM_APP_ID = os.getenv('INSTAGRAM_APP_ID')
INSTAGRAM_APP_SECRET = os.getenv('INSTAGRAM_APP_SECRET')
INSTAGRAM_REDIRECT_URI = os.getenv('INSTAGRAM_REDIRECT_URI', 'https://api.newcollab.co/api/social/callback/instagram')

# TikTok OAuth (Login Kit). Off until TikTok approves the production app.
# Local sandbox: set TIKTOK_OAUTH_ENABLED=1. Do not set this in Vercel prod.
TIKTOK_CLIENT_KEY = os.getenv('TIKTOK_CLIENT_KEY')
TIKTOK_CLIENT_SECRET = os.getenv('TIKTOK_CLIENT_SECRET')
TIKTOK_WEB_REDIRECT_URI = 'https://api.newcollab.co/api/social/callback/tiktok'
TIKTOK_LOCAL_CALLBACK = 'http://localhost:5000/api/social/callback/tiktok'
TIKTOK_REDIRECT_URI = os.getenv('TIKTOK_REDIRECT_URI', TIKTOK_WEB_REDIRECT_URI)


def _tiktok_oauth_enabled():
    flag = (os.getenv('TIKTOK_OAUTH_ENABLED') or '').strip().lower()
    return flag in ('1', 'true', 'yes', 'on')


def _is_local_dev_url(url):
    host = (url or '').lower()
    return 'localhost' in host or '127.0.0.1' in host


def _decode_tiktok_oauth_state(state):
    if not state:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(state.encode()).decode()) or {}
    except Exception:
        return {}


def _tiktok_handle_from_user_info(user_info):
    """Real @username only. display_name is not a handle."""
    user_info = user_info or {}
    handle = (user_info.get('username') or '').strip().lstrip('@')
    if handle:
        return handle
    link = user_info.get('profile_deep_link') or ''
    match = re.search(r'tiktok\.com/@([^/?#]+)', str(link), re.I)
    if match:
        return unquote(match.group(1)).strip().lstrip('@')
    return ''


def _store_onboarding_oauth_proof(platform, profile_data, result, tokens=None):
    """Scrape already writes this; OAuth must too or step1 returns 400."""
    try:
        if session is None:
            _log('[oauth] No session — cannot store onboarding proof')
            return
        handle = (profile_data.get('username') or '').strip().lstrip('@')
        followers = int(profile_data.get('follower_count', 0) or 0)
        session['social_verification_result'] = {
            'verified': bool(result.get('passed')),
            'platform': platform,
            'profile': {
                'username': handle,
                'follower_count': followers,
                'media_count': int(profile_data.get('media_count') or 0),
                'likes_count': int(profile_data.get('likes_count') or 0),
                'open_id': profile_data.get('open_id'),
            },
            'failure_reason': result.get('failure_reason'),
        }
        session['onboarding_quality'] = {
            'handle': handle.lower(),
            'platform': platform,
            'followers': followers,
        }
        expires_at = (tokens or {}).get('expires_at')
        if hasattr(expires_at, 'isoformat'):
            expires_at = expires_at.isoformat()
        session['pending_oauth'] = {
            'platform': platform,
            'username': handle,
            'open_id': profile_data.get('open_id'),
            'follower_count': followers,
            'media_count': int(profile_data.get('media_count') or 0),
            'likes_count': int(profile_data.get('likes_count') or 0),
            'avatar_url': profile_data.get('avatar_url') or '',
            'oauth_videos': profile_data.get('oauth_videos') or [],
            'access_token': (tokens or {}).get('access_token'),
            'refresh_token': (tokens or {}).get('refresh_token'),
            'expires_at': expires_at,
            'verified': bool(result.get('passed')),
        }
        session.modified = True
    except Exception as e:
        _log(f'[oauth] Failed to store onboarding proof: {e}')


def apply_pending_oauth_to_creator(creator_id):
    """Write OAuth tokens/videos/stats after step1 creates the creator row."""
    pending = session.get('pending_oauth') if session is not None else None
    if not pending or not creator_id:
        return False
    expires_at = pending.get('expires_at')
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except Exception:
            expires_at = None
    update_creator_verification(
        creator_id=int(creator_id),
        platform=pending.get('platform') or 'tiktok',
        data={
            'username': pending.get('username'),
            'open_id': pending.get('open_id'),
            'follower_count': pending.get('follower_count') or 0,
            'media_count': pending.get('media_count') or 0,
            'likes_count': pending.get('likes_count') or 0,
            'avatar_url': pending.get('avatar_url') or '',
            'account_type': 'creator',
            'oauth_videos': pending.get('oauth_videos') or [],
        },
        result={'passed': pending.get('verified'), 'gates': {'account_public': True}},
        access_token=pending.get('access_token'),
        refresh_token=pending.get('refresh_token'),
        expires_at=expires_at,
    )
    session.pop('pending_oauth', None)
    session.modified = True
    _log(f"[oauth] Persisted pending OAuth snapshot on creator {creator_id}")
    return True


def _bounce_tiktok_callback_to_local_if_needed():
    """TikTok Web Login Kit rejects localhost hosts. Local Connect therefore
    registers the production HTTPS redirect URI; production must hand the
    unused code to local Flask before exchanging tokens (wrong secret)."""
    if _is_local_dev_url(request.host_url):
        return None
    state_data = _decode_tiktok_oauth_state(request.args.get('state'))
    if not _is_local_dev_url(state_data.get('return_url')):
        return None
    qs = request.query_string.decode('utf-8', errors='replace')
    target = f'{TIKTOK_LOCAL_CALLBACK}?{qs}' if qs else TIKTOK_LOCAL_CALLBACK
    _log('[tiktok] Bouncing callback to local Flask')
    return redirect(target)


def _tiktok_oauth_config(return_url=None):
    """Read TikTok creds from this repo's .env first so OS/prod keys cannot leak into Sandbox."""
    file_vals = {}
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        file_vals = dotenv_values(env_path) or {}
    except Exception:
        file_vals = {}
    key = (file_vals.get('TIKTOK_CLIENT_KEY') or os.getenv('TIKTOK_CLIENT_KEY') or TIKTOK_CLIENT_KEY or '').strip()
    secret = (file_vals.get('TIKTOK_CLIENT_SECRET') or os.getenv('TIKTOK_CLIENT_SECRET') or TIKTOK_CLIENT_SECRET or '').strip()
    redirect_uri = (
        file_vals.get('TIKTOK_REDIRECT_URI')
        or os.getenv('TIKTOK_REDIRECT_URI')
        or TIKTOK_REDIRECT_URI
        or TIKTOK_WEB_REDIRECT_URI
    ).strip()
    # Web Kit cannot callback on localhost. Use the registered HTTPS URI;
    # production bounces the unused code here when return_url is local.
    if _is_local_dev_url(return_url):
        redirect_uri = TIKTOK_WEB_REDIRECT_URI
    return key, secret, redirect_uri
# Match Kora-style consent: profile, additional profile, stats, public videos.
TIKTOK_SCOPES_FULL = 'user.info.basic,user.info.profile,user.info.stats,video.list'
TIKTOK_SCOPES_BASE = 'user.info.basic,user.info.profile,user.info.stats'
TIKTOK_USER_FIELDS = (
    'open_id,union_id,avatar_url,display_name,'
    'username,bio_description,profile_deep_link,is_verified,'
    'follower_count,following_count,likes_count,video_count'
)

# Frontend URL for redirects
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co')


def _ensure_tiktok_oauth_columns(cursor):
    """Add open_id + video snapshot columns if missing (idempotent)."""
    cursor.execute(
        'ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_open_id VARCHAR(128)'
    )
    cursor.execute(
        'ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_oauth_videos JSONB'
    )


def _fetch_tiktok_videos(access_token, max_count=20):
    """Return public videos via video.list, including stats when the scope allows."""
    fields_full = 'id,title,cover_image_url,create_time,share_url,like_count,comment_count,share_count,view_count'
    fields_base = 'id,title,cover_image_url,create_time,share_url'
    for fields in (fields_full, fields_base):
        try:
            resp = requests.post(
                'https://open.tiktokapis.com/v2/video/list/',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                },
                params={'fields': fields},
                json={'max_count': max_count},
                timeout=15,
            )
            payload = resp.json() if resp.content else {}
            err = (payload.get('error') or {})
            if err.get('code') and err.get('code') != 'ok':
                _log(f"[tiktok] video.list error ({fields}): {err}")
                continue
            videos = (payload.get('data') or {}).get('videos') or []
            out = []
            for v in videos:
                if not isinstance(v, dict) or not v.get('id'):
                    continue
                out.append({
                    'id': str(v.get('id')),
                    'title': v.get('title') or '',
                    'cover_image_url': v.get('cover_image_url') or '',
                    'create_time': v.get('create_time'),
                    'share_url': v.get('share_url') or '',
                    'url': v.get('share_url') or f"https://www.tiktok.com/@/video/{v.get('id')}",
                    'likes': int(v.get('like_count') or 0),
                    'comments': int(v.get('comment_count') or 0),
                    'shares': int(v.get('share_count') or 0),
                    'views': int(v.get('view_count') or 0),
                })
            _log(f"[tiktok] video.list returned {len(out)} videos")
            return out
        except Exception as e:
            _log(f"[tiktok] video.list exception: {e}")
    return []


# ============================================================================
# BLUEPRINT SETUP
# ============================================================================

social_verification_bp = Blueprint('social_verification', __name__, url_prefix='/api/social')


def get_db_connection():
    """Get database connection"""
    import psycopg2
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


def _session_get(key, default=None):
    """Safe session read — Flask-Session can leave session as None after a Redis blip."""
    try:
        if session is None:
            return default
        return session.get(key, default)
    except Exception:
        return default


def get_creator_id_from_session():
    """Get creator ID from session"""
    return _session_get('creator_id')


def get_user_country_from_session():
    """Get user country from session, database, or IP geolocation"""
    # TESTING: Allow override via query param or header (dev mode only)
    test_country = request.args.get('_test_country') or request.headers.get('X-Test-Country')
    if test_country:
        _log(f"🧪 TEST MODE: Using country override: {test_country}")
        return test_country.upper()

    # First try session cache
    cached_country = _session_get('user_country')
    if cached_country:
        return cached_country

    # Try database
    user_id = _session_get('user_id')
    if user_id:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT country FROM users WHERE id = %s', (user_id,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            if result and result.get('country'):
                session['user_country'] = result['country']
                return result['country']
        except Exception as e:
            _log(f"Error fetching user country from DB: {e}")

    # Fallback: Try IP-based geolocation
    try:
        # Get client IP (handles proxies/load balancers)
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        _log(f"🌍 Client IP detected: {client_ip}")

        if client_ip:
            # Take first IP if multiple (X-Forwarded-For can be comma-separated)
            client_ip = client_ip.split(',')[0].strip()

            # Skip localhost/private IPs
            if client_ip not in ['127.0.0.1', 'localhost', '::1'] and not client_ip.startswith('192.168.') and not client_ip.startswith('10.'):
                # Use free IP geolocation API
                geo_response = requests.get(f'http://ip-api.com/json/{client_ip}?fields=countryCode', timeout=3)
                if geo_response.status_code == 200:
                    geo_data = geo_response.json()
                    country_code = geo_data.get('countryCode')
                    if country_code:
                        session['user_country'] = country_code
                        _log(f"🌍 IP geolocation: {client_ip} → {country_code}")
                        return country_code
            else:
                _log(f"🌍 Skipping localhost/private IP: {client_ip}")
    except Exception as e:
        _log(f"IP geolocation error: {e}")

    return None


def detect_country_from_ip(timeout=3):
    """Detect country from current request IP - standalone function for callbacks.

    Returns None for localhost/private IPs and on lookup failure (fail-open).
    """
    try:
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        if client_ip:
            client_ip = client_ip.split(',')[0].strip()
            # Skip localhost/private IPs
            if client_ip in ['127.0.0.1', 'localhost', '::1'] or client_ip.startswith(('192.168.', '10.', '172.')):
                return None
            geo_response = requests.get(
                f'http://ip-api.com/json/{client_ip}?fields=countryCode',
                timeout=timeout,
            )
            if geo_response.status_code == 200:
                return geo_response.json().get('countryCode')
    except Exception as e:
        _log(f"⚠️ detect_country_from_ip error: {e}")
    return None


def normalize_country_code(country):
    """Map a stored ISO code or English country name to a 2-letter ISO code."""
    raw = (country or '').strip()
    if not raw:
        return None
    code = raw.upper()
    if code == 'UK':
        return 'GB'
    if len(code) == 2 and code.isalpha():
        return code
    return ALLOWED_COUNTRY_NAMES.get(raw.lower())


def region_code_is_allowed(country):
    """True when geo is unknown (fail-open) or the country is in the serve zone."""
    raw = (country or '').strip()
    if not raw:
        return True
    code = normalize_country_code(raw)
    return bool(code and code in ALLOWED_REGIONS)


def country_value_is_restricted(country):
    """True when a stored ISO code or English name is outside the serve zone."""
    raw = (country or '').strip()
    if not raw:
        return False
    code = normalize_country_code(raw)
    if not code:
        return True
    return code not in ALLOWED_REGIONS


def stored_country_is_allowed(country):
    """True when we already know this account is in an allowed country."""
    code = normalize_country_code(country)
    return bool(code and code in ALLOWED_REGIONS)


def is_request_from_restricted_region(timeout=3):
    """True only when IP country is known AND outside the serve zone. Fail-open otherwise."""
    code = detect_country_from_ip(timeout=timeout)
    if not code:
        return False
    return not region_code_is_allowed(code)


def should_block_auth_for_region(stored_country, timeout=1.0):
    """Login geo gate that does not change UX for allowed-country accounts.

    Known allowed countries skip the IP lookup (no extra latency, no travel lockout).
    Unknown or out-of-zone stored countries use the same fail-open IP check as signup.
    """
    if stored_country_is_allowed(stored_country):
        return False
    return is_request_from_restricted_region(timeout=timeout)


def encrypt_token(token):
    """Encrypt OAuth token for storage"""
    if not token:
        return None
    if fernet:
        return fernet.encrypt(token.encode()).decode()
    # Fallback: store plaintext (not recommended for production)
    return token


def decrypt_token(encrypted_token):
    """Decrypt OAuth token from storage"""
    if not encrypted_token:
        return None
    if fernet:
        try:
            return fernet.decrypt(encrypted_token.encode()).decode()
        except Exception:
            return encrypted_token  # Assume unencrypted
    return encrypted_token


# ============================================================================
# 5-GATE VERIFICATION FUNCTION
# ============================================================================

def validate_social_gates(data: dict, platform: str, user_country: str) -> dict:
    """
    Validate all 5 gates for social verification.
    Returns dict with passed status, failure reason, and gate details.

    Gates:
    1. OAuth connected (access token exists)
    2. Account is public
    3. Follower count >= 500
    4. Media/post count >= 5
    5. Region allowed (US, UK, AU, NZ, Europe)
    """
    # Normalize country code
    country_code = (user_country or '').upper().strip()

    gates = {
        "region_allowed": region_code_is_allowed(country_code),
        "oauth_connected": bool(data.get("access_token")),
        "account_public": _is_public(data, platform),
        "follower_min": (data.get("follower_count") or 0) >= MIN_FOLLOWERS,
        "content_min": (data.get("media_count") or 0) >= MIN_POSTS,
    }

    passed = all(gates.values())
    failure_reason = None

    if not passed:
        # Check gates in priority order
        if not gates["region_allowed"]:
            failure_reason = "restricted_region"
        elif not gates["oauth_connected"]:
            failure_reason = "oauth_expired"
        elif not gates["account_public"]:
            failure_reason = "private"
        elif not gates["follower_min"]:
            failure_reason = "below_follower_min"
        elif not gates["content_min"]:
            failure_reason = "below_post_min"

    return {
        "passed": passed,
        "failure_reason": failure_reason,
        "gates": gates,
        "stats": {
            "followers": data.get("follower_count", 0),
            "posts": data.get("media_count", 0),
            "account_type": data.get("account_type"),
            "is_public": gates["account_public"],
            "country": country_code
        }
    }


def _is_public(data: dict, platform: str) -> bool:
    """Check if account is public based on platform"""
    # First check explicit is_private flag (used by public profile fetcher)
    if 'is_private' in data:
        return not data.get('is_private', True)

    if platform == "instagram":
        # Instagram Business/Creator accounts are always public
        account_type = (data.get("account_type") or "").upper()
        return account_type in ["BUSINESS", "CREATOR", "MEDIA_CREATOR", "UNKNOWN"]
    elif platform == "tiktok":
        # TikTok: default to public if not explicitly private
        return not data.get("is_private", False)
    return True  # Default to public for public profile fetches


def log_verification_check(creator_id: int, check_type: str, platform: str,
                           result: dict, user_country: str, api_response: dict = None):
    """Log verification check to audit table"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        gates = result.get("gates", {})
        stats = result.get("stats", {})

        cursor.execute('''
            INSERT INTO social_verification_checks (
                creator_id, check_type, platform,
                gate_1_oauth_connected, gate_2_account_public,
                gate_3_follower_min_met, gate_4_content_min_met, gate_5_region_allowed,
                raw_follower_count, raw_media_count, raw_account_type, raw_is_public,
                user_country, verification_passed, failure_reason, api_response_snapshot
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            creator_id, check_type, platform,
            gates.get("oauth_connected", False),
            gates.get("account_public", False),
            gates.get("follower_min", False),
            gates.get("content_min", False),
            gates.get("region_allowed", False),
            stats.get("followers"),
            stats.get("posts"),
            stats.get("account_type"),
            stats.get("is_public"),
            user_country,
            result.get("passed", False),
            result.get("failure_reason"),
            json.dumps(api_response) if api_response else None
        ))

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        _log(f"Error logging verification check: {e}")


def update_creator_verification(creator_id: int, platform: str, data: dict,
                                 result: dict, access_token: str = None,
                                 refresh_token: str = None, expires_at: datetime = None):
    """Update creator record with verification data"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        _ensure_tiktok_oauth_columns(cursor)

        status = 'verified' if result['passed'] else f"failed_{result.get('failure_reason')}"
        videos = data.get('oauth_videos')
        videos_json = json.dumps(videos) if videos is not None else None
        handle = (data.get("username") or data.get("handle") or "").strip().lstrip("@")
        followers = int(data.get("follower_count") or 0)
        media_count = int(data.get("media_count") or 0)
        likes_count = int(data.get("likes_count") or 0)
        avatar_url = (data.get("avatar_url") or "").strip()
        gates = result.get("gates") or {}

        cursor.execute('''
            UPDATE creators SET
                social_platform = %s,
                social_handle = %s,
                social_follower_count = %s,
                social_media_count = %s,
                social_is_public = %s,
                social_account_type = %s,
                social_connected_at = NOW(),
                social_last_checked_at = NOW(),
                social_verified = %s,
                social_verification_status = %s,
                social_oauth_token = %s,
                social_oauth_refresh_token = %s,
                social_token_expires_at = %s,
                social_open_id = COALESCE(%s, social_open_id),
                social_oauth_videos = COALESCE(%s::jsonb, social_oauth_videos),
                followers_count = CASE WHEN %s > 0 THEN %s ELSE followers_count END,
                total_likes = CASE WHEN %s > 0 THEN %s ELSE total_likes END,
                total_posts = CASE WHEN %s > 0 THEN %s ELSE total_posts END,
                image_profile = COALESCE(NULLIF(%s, ''), image_profile)
            WHERE id = %s
        ''', (
            platform,
            handle,
            followers,
            media_count,
            gates.get("account_public", False),
            data.get("account_type"),
            result.get('passed'),
            status,
            encrypt_token(access_token),
            encrypt_token(refresh_token),
            expires_at,
            data.get('open_id'),
            videos_json,
            followers, followers,
            likes_count, likes_count,
            media_count, media_count,
            avatar_url,
            creator_id
        ))

        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        _log(f"Error updating creator verification: {e}")
        return False


# ============================================================================
# REGION PRE-CHECK ENDPOINT
# ============================================================================

@social_verification_bp.route('/check-region', methods=['GET'])
def check_region():
    """
    Pre-check if user's region is allowed before showing onboarding.
    Called on mount - does NOT require authentication.
    Uses IP-based geolocation to detect country.
    """
    creator_id = get_creator_id_from_session()  # Optional - may be None during early onboarding

    # Try to get country from session first
    user_country = get_user_country_from_session()

    # If no country in session, detect from IP
    if not user_country:
        user_country = detect_country_from_ip()
        if user_country:
            _log(f"🌍 Region check - IP detection: {user_country}")

    country_code = (user_country or '').upper().strip()

    # If we still have no country, allow by default (better UX than blocking)
    is_allowed = region_code_is_allowed(country_code)

    # Log the region check (only if we have creator_id)
    if not is_allowed and creator_id:
        result = {
            "passed": False,
            "failure_reason": "restricted_region",
            "gates": {"region_allowed": False},
            "stats": {"country": country_code}
        }
        log_verification_check(creator_id, 'region_precheck', None, result, country_code)
    elif not is_allowed:
        _log(f"🚫 Region check blocked: {country_code} (no creator_id yet)")

    return jsonify({
        'allowed': is_allowed,
        'country': country_code,
        'failure_reason': 'restricted_region' if not is_allowed else None
    })


# ============================================================================
# PUBLIC PROFILE VERIFICATION (NO OAUTH REQUIRED)
# ============================================================================

@social_verification_bp.route('/verify-handle', methods=['POST'])
def verify_handle():
    """
    Verify a social media handle by fetching public profile data.
    No OAuth required - uses public profile endpoints.

    Request body:
        {
            "platform": "instagram" or "tiktok",
            "handle": "@username" or "username"
        }

    Returns:
        {
            "success": bool,
            "verified": bool,
            "profile": { username, follower_count, media_count, is_private, ... },
            "gates": { oauth_connected, account_public, follower_min, content_min, region_allowed },
            "failure_reason": null or string
        }
    """
    # Get user_id (required for authentication)
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401

    # creator_id may not exist yet for new users in onboarding
    creator_id = get_creator_id_from_session()

    data = request.get_json()
    platform = data.get('platform', '').lower()
    handle = data.get('handle', '').strip()

    if platform not in ['instagram', 'tiktok']:
        return jsonify({'error': 'Invalid platform. Use "instagram" or "tiktok"'}), 400

    if not handle:
        return jsonify({'error': 'Handle is required'}), 400

    # Clean handle
    handle = handle.lstrip('@').strip()

    # Check region first
    user_country = get_user_country_from_session() or ''
    country_code = user_country.upper().strip()

    if not region_code_is_allowed(country_code):
        return jsonify({
            'success': False,
            'verified': False,
            'failure_reason': 'restricted_region',
            'gates': {'region_allowed': False}
        })

    try:
        # Fetch public profile data
        if platform == 'instagram':
            profile_data = fetch_instagram_profile(handle)
            profile_for_gates = {
                'access_token': 'public_profile',  # Marker for public fetch
                'username': profile_data.get('username'),
                'follower_count': profile_data.get('follower_count', 0),
                'media_count': profile_data.get('media_count', 0),
                'account_type': profile_data.get('account_type', 'UNKNOWN'),
                'is_private': profile_data.get('is_private', False),
            }
        else:  # tiktok
            profile_data = fetch_tiktok_profile(handle)
            profile_for_gates = {
                'access_token': 'public_profile',  # Marker for public fetch
                'username': profile_data.get('username'),
                'follower_count': profile_data.get('follower_count', 0),
                'media_count': profile_data.get('video_count', 0),
                'account_type': 'CREATOR',
                'is_private': profile_data.get('is_private', False),
            }

        # Run 5-gate verification
        result = validate_social_gates(profile_for_gates, platform, country_code)

        # Log and update DB only if creator_id exists (existing users)
        # For new users in onboarding, the record is created in step1 after verification
        if creator_id:
            log_verification_check(creator_id, 'public_profile', platform, result, country_code, profile_data)
            update_creator_verification(
                creator_id=creator_id,
                platform=platform,
                data=profile_for_gates,
                result=result,
                access_token=None  # No OAuth token for public fetch
            )

        # Store verification result in session for step1 to use
        session['social_verification_result'] = {
            'verified': result['passed'],
            'platform': platform,
            'profile': profile_for_gates,
            'failure_reason': result.get('failure_reason')
        }
        session.modified = True

        return jsonify({
            'success': True,
            'verified': result['passed'],
            'profile': {
                'username': profile_for_gates['username'],
                'follower_count': profile_for_gates['follower_count'],
                'media_count': profile_for_gates['media_count'],
                'is_private': profile_for_gates.get('is_private', False),
                'account_type': profile_for_gates.get('account_type'),
            },
            'gates': result['gates'],
            'failure_reason': result.get('failure_reason')
        })

    except ProfileFetchError as e:
        return jsonify({
            'success': False,
            'verified': False,
            'error': str(e),
            'failure_reason': 'profile_not_found'
        }), 404

    except Exception as e:
        _log(f"Error verifying handle: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'verified': False,
            'error': 'Failed to fetch profile',
            'failure_reason': 'fetch_error'
        }), 500


# ============================================================================
# INSTAGRAM OAUTH ENDPOINTS (DEPRECATED - requires app review)
# ============================================================================

@social_verification_bp.route('/connect/instagram', methods=['GET'])
def connect_instagram():
    """Initiate Instagram OAuth flow via Instagram Business Login"""
    # For onboarding, we may not have creator_id yet - just need user_id
    user_id = session.get('user_id')
    creator_id = get_creator_id_from_session()

    # Get return_url from query params (allows flexible redirect back to any frontend)
    return_url = request.args.get('return_url', f"{FRONTEND_URL}/onboarding")

    if not user_id:
        return redirect(f"{FRONTEND_URL}/login?redirect=/onboarding")

    # Check region first
    user_country = get_user_country_from_session()
    if user_country and not region_code_is_allowed(user_country):
        return redirect(f"{return_url}?social=failed&reason=restricted_region")

    if not INSTAGRAM_APP_ID:
        _log("❌ INSTAGRAM_APP_ID not configured")
        return redirect(f"{return_url}?social=failed&reason=oauth_error")

    # Encode user info in state to survive cross-subdomain redirect
    # State format: base64(json({csrf: token, user_id: id, creator_id: id, return_url: url}))
    csrf_token = secrets.token_urlsafe(16)
    state_data = {
        'csrf': csrf_token,
        'user_id': user_id,
        'creator_id': creator_id,
        'return_url': return_url
    }
    state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()

    # Also store in session as backup (works for same-domain)
    session['instagram_oauth_state'] = csrf_token

    _log(f"📤 Instagram Connect: user_id={user_id}, creator_id={creator_id}, state={state[:20]}...")

    # Instagram Business Login OAuth URL (new API)
    scopes = 'instagram_business_basic,instagram_business_manage_insights'

    auth_url = (
        f"https://www.instagram.com/oauth/authorize?"
        f"client_id={INSTAGRAM_APP_ID}"
        f"&redirect_uri={quote(INSTAGRAM_REDIRECT_URI)}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&state={state}"
    )

    return redirect(auth_url)


@social_verification_bp.route('/callback/instagram', methods=['GET'])
def callback_instagram():
    """Handle Instagram OAuth callback via Instagram Business Login API"""
    state = request.args.get('state')

    # Decode state to get user info (survives cross-subdomain redirect)
    user_id = None
    creator_id = None
    csrf_token = None
    return_url = f"{FRONTEND_URL}/onboarding"  # Default fallback

    if state:
        try:
            state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
            csrf_token = state_data.get('csrf')
            user_id = state_data.get('user_id')
            creator_id = state_data.get('creator_id')
            return_url = state_data.get('return_url', return_url)
        except Exception as e:
            _log(f"⚠️ Failed to decode state: {e}")

    # Fallback to session (works for same-domain)
    stored_csrf = session.pop('instagram_oauth_state', None)

    _log(f"📥 Instagram Callback: user_id={user_id}, creator_id={creator_id}, csrf_valid={csrf_token == stored_csrf if stored_csrf else 'no_session'}")

    # Validate we have user_id (required for the flow to work)
    if not user_id:
        _log("❌ Instagram OAuth: No user_id in state - session may have expired")
        return redirect(f"{return_url}?social=failed&reason=oauth_error")

    # Check for errors
    error = request.args.get('error')
    error_reason = request.args.get('error_reason')
    error_description = request.args.get('error_description')
    if error:
        _log(f"❌ Instagram OAuth error: {error} - {error_reason} - {error_description}")
        return redirect(f"{return_url}?social=failed&reason=oauth_error")

    code = request.args.get('code')
    if not code:
        return redirect(f"{return_url}?social=failed&reason=oauth_error")

    try:
        # Exchange code for short-lived access token (Instagram Business Login API)
        _log(f"📤 Exchanging code for token...")
        token_response = requests.post(
            'https://api.instagram.com/oauth/access_token',
            data={
                'client_id': INSTAGRAM_APP_ID,
                'client_secret': INSTAGRAM_APP_SECRET,
                'grant_type': 'authorization_code',
                'redirect_uri': INSTAGRAM_REDIRECT_URI,
                'code': code
            },
            timeout=15
        )
        token_data = token_response.json()
        _log(f"📥 Token response: {token_data}")

        if 'error_type' in token_data or 'error' in token_data:
            _log(f"❌ Instagram token error: {token_data}")
            return redirect(f"{return_url}?social=failed&reason=oauth_error")

        access_token = token_data.get('access_token')
        user_id = token_data.get('user_id')

        if not access_token:
            _log("❌ No access token in response")
            return redirect(f"{return_url}?social=failed&reason=oauth_error")

        # Get user profile using Instagram Graph API
        _log(f"📤 Fetching user profile for user_id: {user_id}")
        profile_response = requests.get(
            f'https://graph.instagram.com/v22.0/me',
            params={
                'fields': 'user_id,username,account_type,followers_count,media_count,profile_picture_url',
                'access_token': access_token
            },
            timeout=10
        )
        profile_data_raw = profile_response.json()
        _log(f"📥 Profile response: {profile_data_raw}")

        if 'error' in profile_data_raw:
            _log(f"❌ Instagram profile error: {profile_data_raw}")
            return redirect(f"{return_url}?social=failed&reason=oauth_error")

        # Note: Instagram Business/Creator accounts connected via OAuth are inherently public
        # The API only works for business accounts which must be public to function
        # No need to scrape public profile - account_type tells us everything we need

        # Build profile data
        # Business/Creator accounts via Instagram Business Login are always public
        account_type = profile_data_raw.get('account_type', 'BUSINESS').upper()
        is_public_account = account_type in ['BUSINESS', 'CREATOR', 'MEDIA_CREATOR']

        profile_data = {
            'access_token': access_token,
            'username': profile_data_raw.get('username'),
            'follower_count': profile_data_raw.get('followers_count', 0),
            'media_count': profile_data_raw.get('media_count', 0),
            'account_type': account_type,
            'is_private': not is_public_account,  # Business/Creator accounts are always public
        }
        api_response = profile_data_raw
        _log(f"✅ Instagram account found: @{profile_data['username']} - {profile_data['follower_count']} followers, {profile_data['media_count']} posts")

        # Get user country - try fresh IP detection in callback
        user_country = get_user_country_from_session()

        # If no country detected, try fresh IP geolocation (callback is from user's browser)
        if not user_country:
            user_country = detect_country_from_ip()
            if user_country:
                _log(f"🌍 Fresh IP detection in callback: {user_country}")
                # Store in database for future reference
                if user_id:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute('UPDATE users SET country = %s WHERE id = %s AND country IS NULL',
                                      (user_country, user_id))
                        conn.commit()
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        _log(f"⚠️ Failed to store country in DB: {e}")

        user_country = user_country or ''
        _log(f"🌍 Final country for verification: '{user_country}' (restricted: {not region_code_is_allowed(user_country)})")

        # Run 5-gate verification
        result = validate_social_gates(profile_data, 'instagram', user_country)
        _store_onboarding_oauth_proof('instagram', profile_data, result)

        # Log and update DB only if creator_id exists (skip for direct URL testing)
        if creator_id:
            log_verification_check(creator_id, 'initial', 'instagram', result, user_country, api_response)
            update_creator_verification(
                creator_id=creator_id,
                platform='instagram',
                data=profile_data,
                result=result,
                access_token=access_token
            )

        if result['passed']:
            # Include follower/post counts in URL for frontend (since creator_id may not exist yet)
            return redirect(
                f"{return_url}?social=success&platform=instagram"
                f"&handle={profile_data['username']}"
                f"&followers={profile_data['follower_count']}"
                f"&posts={profile_data['media_count']}"
            )
        else:
            return redirect(f"{return_url}?social=failed&reason={result['failure_reason']}&platform=instagram")

    except Exception as e:
        _log(f"❌ Instagram OAuth exception: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{return_url}?social=failed&reason=oauth_error")


# ============================================================================
# TIKTOK OAUTH ENDPOINTS
# ============================================================================

@social_verification_bp.route('/connect/tiktok', methods=['GET'])
def connect_tiktok():
    """Initiate TikTok OAuth flow via Login Kit. Disabled until TikTok approves us."""
    return_url = request.args.get('return_url', f"{FRONTEND_URL}/onboarding")
    if not _tiktok_oauth_enabled():
        _log("[tiktok] Connect blocked: TIKTOK_OAUTH_ENABLED is off")
        return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")
    try:
        # For onboarding, we may not have creator_id yet - just need user_id
        user_id = _session_get('user_id')
        creator_id = get_creator_id_from_session()

        if not user_id:
            return redirect(f"{FRONTEND_URL}/login?redirect=/onboarding")

        # Check region first
        user_country = get_user_country_from_session()
        if user_country and not region_code_is_allowed(user_country):
            return redirect(f"{return_url}?social=failed&reason=restricted_region")

        client_key, _secret, redirect_uri = _tiktok_oauth_config(return_url)

        if not client_key:
            _log("[tiktok] TIKTOK_CLIENT_KEY not configured")
            return redirect(f"{return_url}?social=failed&reason=oauth_error")

        # Generate code verifier for PKCE
        code_verifier = secrets.token_urlsafe(64)

        # Create code challenge (SHA256 hash, base64url encoded)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip('=')

        # Prefer full Kora-style scopes; allow base-only retry when portal rejects video.list
        use_base = request.args.get('scopes') == 'base'
        scopes = TIKTOK_SCOPES_BASE if use_base else TIKTOK_SCOPES_FULL

        # Encode user info and code_verifier in state to survive cross-subdomain redirect
        csrf_token = secrets.token_urlsafe(16)
        state_data = {
            'csrf': csrf_token,
            'user_id': user_id,
            'creator_id': creator_id,
            'code_verifier': code_verifier,
            'return_url': return_url,
            'scopes': 'base' if use_base else 'full',
            'redirect_uri': redirect_uri,
        }
        state = base64.urlsafe_b64encode(json.dumps(state_data, default=str).encode()).decode()

        try:
            session['tiktok_oauth_csrf'] = csrf_token
        except Exception:
            pass

        _log(f"[tiktok] Connect user_id={user_id} creator_id={creator_id} scopes={scopes} key={client_key[:8]}...")

        auth_url = (
            f"https://www.tiktok.com/v2/auth/authorize/?"
            f"client_key={client_key}"
            f"&redirect_uri={quote(redirect_uri)}"
            f"&scope={scopes}"
            f"&state={state}"
            f"&response_type=code"
            f"&code_challenge={code_challenge}"
            f"&code_challenge_method=S256"
            f"&disable_auto_auth=1"
        )

        return redirect(auth_url)
    except Exception as e:
        _log(f"[tiktok] Connect exception: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")


@social_verification_bp.route('/callback/tiktok', methods=['GET'])
def callback_tiktok():
    """Handle TikTok OAuth callback"""
    bounced = _bounce_tiktok_callback_to_local_if_needed()
    if bounced:
        return bounced

    state = request.args.get('state')

    # Decode state to get user info and code_verifier (survives cross-subdomain redirect)
    user_id = None
    creator_id = None
    code_verifier = None
    csrf_token = None
    return_url = f"{FRONTEND_URL}/onboarding"  # Default fallback
    scopes_mode = 'full'

    if state:
        try:
            state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
            csrf_token = state_data.get('csrf')
            user_id = state_data.get('user_id')
            creator_id = state_data.get('creator_id')
            code_verifier = state_data.get('code_verifier')
            return_url = state_data.get('return_url', return_url)
            scopes_mode = state_data.get('scopes') or 'full'
        except Exception as e:
            _log(f"⚠️ Failed to decode TikTok state: {e}")

    # Fallback to session (works for same-domain)
    stored_csrf = session.pop('tiktok_oauth_csrf', None)

    _log(f"📥 TikTok Callback: user_id={user_id}, creator_id={creator_id}, has_verifier={bool(code_verifier)}")

    # Validate we have required data
    if not user_id or not code_verifier:
        _log("❌ TikTok OAuth: Missing user_id or code_verifier in state")
        return redirect(f"{return_url}?social=failed&reason=oauth_error")

    # Check for errors from TikTok
    error = request.args.get('error')
    error_desc = (request.args.get('error_description') or '').lower()
    if error:
        _log(f"❌ TikTok OAuth error: {error} {error_desc}")
        # Unapproved video.list → retry once without it (clear fail, no silent scrape)
        if scopes_mode != 'base' and (
            error == 'invalid_scope'
            or 'scope' in error_desc
            or 'video.list' in error_desc
        ):
            retry = (
                f"/api/social/connect/tiktok?scopes=base"
                f"&return_url={quote(return_url)}"
            )
            _log(f"↩️ Retrying TikTok connect without video.list → {retry}")
            return redirect(retry)
        return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")

    code = request.args.get('code')
    if not code:
        return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")

    try:
        # Exchange code for access token
        client_key, client_secret, redirect_uri = _tiktok_oauth_config(return_url)
        if state:
            try:
                stored_redirect = json.loads(base64.urlsafe_b64decode(state.encode()).decode()).get('redirect_uri')
                if stored_redirect:
                    redirect_uri = stored_redirect
            except Exception:
                pass
        token_response = requests.post(
            'https://open.tiktokapis.com/v2/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': client_key,
                'client_secret': client_secret,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': redirect_uri,
                'code_verifier': code_verifier
            },
            timeout=10
        )
        token_data = token_response.json()

        if 'error' in token_data and token_data.get('error') not in (None, '', 'ok'):
            _log(f"❌ TikTok token error: {token_data}")
            err_msg = str(token_data.get('error_description') or token_data.get('error') or '').lower()
            if scopes_mode != 'base' and 'scope' in err_msg:
                return redirect(
                    f"/api/social/connect/tiktok?scopes=base&return_url={quote(return_url)}"
                )
            return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")

        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 86400)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        if not access_token:
            _log(f"❌ TikTok token missing access_token: {token_data}")
            return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")

        # Get user info with profile + stats fields
        user_response = requests.get(
            'https://open.tiktokapis.com/v2/user/info/',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'fields': TIKTOK_USER_FIELDS},
            timeout=10
        )
        user_data = user_response.json()

        err = (user_data.get('error') or {})
        if err.get('code') and err.get('code') != 'ok':
            _log(f"❌ TikTok user info error: {user_data}")
            return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")

        user_info = user_data.get('data', {}).get('user', {}) or {}

        # DEBUG: Log raw TikTok API response
        _log(f"🔍 TikTok user_info: {json.dumps(user_info, indent=2)}")

        handle = _tiktok_handle_from_user_info(user_info)
        open_id = user_info.get('open_id')
        if not handle:
            _log(f"❌ TikTok user.info missing username: keys={list(user_info.keys())}")
            return redirect(f"{return_url}?social=failed&reason=no_username&platform=tiktok")

        oauth_videos = []
        if scopes_mode != 'base':
            oauth_videos = _fetch_tiktok_videos(access_token)

        profile_data = {
            'access_token': access_token,
            'username': handle,
            'open_id': open_id,
            'follower_count': user_info.get('follower_count', 0) or 0,
            'media_count': user_info.get('video_count', 0) or 0,
            'likes_count': user_info.get('likes_count', 0) or 0,
            'avatar_url': user_info.get('avatar_url') or '',
            'account_type': 'creator',
            'is_private': False,  # TikTok API v2 - assume public for now
            'bio_description': user_info.get('bio_description') or '',
            'profile_deep_link': user_info.get('profile_deep_link') or '',
            'oauth_videos': oauth_videos,
        }

        # DEBUG: Log constructed profile_data
        _log(
            f"🔍 TikTok profile_data: handle={profile_data['username']} "
            f"followers={profile_data['follower_count']} videos={profile_data['media_count']} "
            f"likes={profile_data['likes_count']} oauth_videos={len(oauth_videos)}"
        )

        # Get user country - try fresh IP detection in callback
        user_country = get_user_country_from_session()

        # If no country detected, try fresh IP geolocation (callback is from user's browser)
        if not user_country:
            user_country = detect_country_from_ip()
            if user_country:
                _log(f"🌍 Fresh IP detection in TikTok callback: {user_country}")
                # Store in database for future reference
                if user_id:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            'UPDATE users SET country = %s WHERE id = %s AND country IS NULL',
                            (user_country, user_id)
                        )
                        conn.commit()
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        _log(f"⚠️ Failed to store country in DB: {e}")

        user_country = user_country or ''
        _log(
            f"🌍 Final country for TikTok verification: '{user_country}' "
            f"(restricted: {not region_code_is_allowed(user_country)})"
        )

        # Run 5-gate verification
        result = validate_social_gates(profile_data, 'tiktok', user_country)

        # DEBUG: Log validation result
        _log(f"🔍 TikTok validation result: passed={result['passed']}, reason={result.get('failure_reason')}, gates={json.dumps(result.get('gates', {}))}")

        _store_onboarding_oauth_proof(
            'tiktok',
            profile_data,
            result,
            tokens={
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': expires_at,
            },
        )

        # Log and update DB only if creator_id exists (skip for new onboarding users)
        if creator_id:
            log_verification_check(creator_id, 'initial', 'tiktok', result, user_country, user_info)
            update_creator_verification(
                creator_id=creator_id,
                platform='tiktok',
                data=profile_data,
                result=result,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at
            )

        if result['passed']:
            # Include follower/post counts in URL for frontend (since creator_id may not exist yet)
            handle_q = quote(str(profile_data['username'] or ''))
            return redirect(
                f"{return_url}?social=success&platform=tiktok"
                f"&handle={handle_q}"
                f"&followers={profile_data['follower_count']}"
                f"&posts={profile_data['media_count']}"
            )
        else:
            return redirect(
                f"{return_url}?social=failed&reason={result['failure_reason']}&platform=tiktok"
            )

    except Exception as e:
        _log(f"❌ TikTok OAuth exception: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{return_url}?social=failed&reason=oauth_error&platform=tiktok")


# ============================================================================
# STATUS & UTILITY ENDPOINTS
# ============================================================================

@social_verification_bp.route('/status', methods=['GET'])
def get_verification_status():
    """Get current social verification status for logged-in creator"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                social_platform,
                social_handle,
                social_follower_count,
                social_media_count,
                social_is_public,
                social_account_type,
                social_verified,
                social_verification_status,
                social_connected_at,
                social_last_checked_at,
                social_verification_required_by,
                social_verification_grandfathered
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        # Check if grandfathered period is still active
        is_grandfathered = False
        if creator['social_verification_grandfathered'] and creator['social_verification_required_by']:
            is_grandfathered = datetime.utcnow() < creator['social_verification_required_by']

        return jsonify({
            'verified': creator['social_verified'],
            'platform': creator['social_platform'],
            'handle': creator['social_handle'],
            'follower_count': creator['social_follower_count'],
            'media_count': creator['social_media_count'],
            'is_public': creator['social_is_public'],
            'account_type': creator['social_account_type'],
            'status': creator['social_verification_status'],
            'connected_at': creator['social_connected_at'].isoformat() if creator['social_connected_at'] else None,
            'last_checked_at': creator['social_last_checked_at'].isoformat() if creator['social_last_checked_at'] else None,
            'grandfathered': is_grandfathered,
            'grandfathered_until': creator['social_verification_required_by'].isoformat() if creator['social_verification_required_by'] else None,
            'connected': bool(creator['social_platform'] and creator['social_handle']),
        })

    except Exception as e:
        _log(f"Error getting verification status: {e}")
        return jsonify({'error': str(e)}), 500


@social_verification_bp.route('/recheck', methods=['POST'])
def recheck_verification():
    """Re-run verification check for a connected account (e.g., after user makes account public)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT social_platform, social_oauth_token, social_oauth_refresh_token
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator or not creator['social_platform']:
            return jsonify({'error': 'No social account connected'}), 400

        platform = creator['social_platform']
        access_token = decrypt_token(creator['social_oauth_token'])

        if not access_token:
            return jsonify({'error': 'OAuth token expired, please reconnect'}), 400

        # Re-fetch profile data based on platform
        if platform == 'instagram':
            # TODO: Re-fetch Instagram data
            return jsonify({'error': 'Instagram recheck not implemented yet'}), 501
        elif platform == 'tiktok':
            # TODO: Re-fetch TikTok data
            return jsonify({'error': 'TikTok recheck not implemented yet'}), 501

    except Exception as e:
        _log(f"Error during recheck: {e}")
        return jsonify({'error': str(e)}), 500


@social_verification_bp.route('/disconnect', methods=['POST'])
def disconnect_social():
    """Disconnect social account and clear verification"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE creators SET
                social_platform = NULL,
                social_handle = NULL,
                social_follower_count = 0,
                social_media_count = 0,
                social_is_public = FALSE,
                social_account_type = NULL,
                social_connected_at = NULL,
                social_verified = FALSE,
                social_verification_status = 'pending',
                social_oauth_token = NULL,
                social_oauth_refresh_token = NULL,
                social_token_expires_at = NULL
            WHERE id = %s
        ''', (creator_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True, 'message': 'Social account disconnected'})

    except Exception as e:
        _log(f"Error disconnecting social: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ONBOARDING INTEGRATION ENDPOINT
# ============================================================================

@social_verification_bp.route('/requires-verification', methods=['GET'])
def check_requires_verification():
    """
    Check if current user needs social verification.
    Called by onboarding to determine if step 3.5 is needed.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                social_verified,
                social_verification_required_by,
                social_verification_grandfathered
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'requires_verification': True, 'reason': 'new_user'})

        # Already verified
        if creator['social_verified']:
            return jsonify({'requires_verification': False, 'reason': 'already_verified'})

        # Check if grandfathered
        if creator['social_verification_grandfathered'] and creator['social_verification_required_by']:
            if datetime.utcnow() < creator['social_verification_required_by']:
                return jsonify({
                    'requires_verification': False,
                    'reason': 'grandfathered',
                    'can_skip': True,
                    'required_by': creator['social_verification_required_by'].isoformat()
                })

        # Check region
        user_country = get_user_country_from_session()
        if user_country and not region_code_is_allowed(user_country):
            return jsonify({
                'requires_verification': True,
                'blocked': True,
                'reason': 'restricted_region'
            })

        return jsonify({'requires_verification': True, 'reason': 'not_verified'})

    except Exception as e:
        _log(f"Error checking verification requirement: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INSTAGRAM WEBHOOK ENDPOINT (Required for Meta App Verification)
# ============================================================================

# Webhook verify token - set this in your environment variables
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv('INSTAGRAM_WEBHOOK_VERIFY_TOKEN', 'newcollab_instagram_verify_2026')

@social_verification_bp.route('/webhook/instagram', methods=['GET', 'POST'])
def instagram_webhook():
    """
    Instagram/Meta webhook endpoint.

    GET: Handles Meta's webhook verification challenge
    POST: Receives webhook events from Instagram

    For webhook setup in Meta Developer Console:
    - Callback URL: https://api.newcollab.co/api/social/webhook/instagram
    - Verify Token: (value of INSTAGRAM_WEBHOOK_VERIFY_TOKEN env var)
    """
    if request.method == 'GET':
        # Meta sends verification challenge
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        _log(f"📥 Instagram Webhook Verification: mode={mode}, token={token}, challenge={challenge}")

        if mode == 'subscribe' and token == INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
            _log("✅ Instagram Webhook Verified Successfully!")
            # Must return the challenge as plain text, not JSON
            return challenge, 200
        else:
            _log(f"❌ Instagram Webhook Verification Failed: token mismatch (expected: {INSTAGRAM_WEBHOOK_VERIFY_TOKEN})")
            return 'Forbidden', 403

    elif request.method == 'POST':
        # Receive webhook events from Instagram
        try:
            data = request.get_json()
            _log(f"📨 Instagram Webhook Event Received:")
            _log(json.dumps(data, indent=2))

            # Process different event types
            object_type = data.get('object')

            if object_type == 'instagram':
                entries = data.get('entry', [])
                for entry in entries:
                    # Handle different webhook fields
                    # - mentions: when someone mentions your business
                    # - comments: comments on your posts
                    # - messages: direct messages (requires permissions)
                    changes = entry.get('changes', [])
                    for change in changes:
                        field = change.get('field')
                        value = change.get('value')
                        _log(f"  📌 Field: {field}, Value: {value}")

            # Always return 200 to acknowledge receipt
            return jsonify({'status': 'received'}), 200

        except Exception as e:
            _log(f"❌ Error processing Instagram webhook: {e}")
            import traceback
            traceback.print_exc()
            # Still return 200 to prevent Meta from retrying
            return jsonify({'status': 'error', 'message': str(e)}), 200
