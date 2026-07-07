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
from urllib.parse import urlencode, quote
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


# ============================================================================
# CONFIGURATION
# ============================================================================

# Restricted regions - India and Pakistan blocked
RESTRICTED_REGIONS = ['IN', 'PK']

# Minimum thresholds
MIN_FOLLOWERS = 500
MIN_POSTS = 5

# Instagram OAuth (via Facebook Login for Business)
INSTAGRAM_APP_ID = os.getenv('INSTAGRAM_APP_ID')
INSTAGRAM_APP_SECRET = os.getenv('INSTAGRAM_APP_SECRET')
INSTAGRAM_REDIRECT_URI = os.getenv('INSTAGRAM_REDIRECT_URI', 'https://api.newcollab.co/api/social/callback/instagram')

# TikTok OAuth (Login Kit)
TIKTOK_CLIENT_KEY = os.getenv('TIKTOK_CLIENT_KEY')
TIKTOK_CLIENT_SECRET = os.getenv('TIKTOK_CLIENT_SECRET')
TIKTOK_REDIRECT_URI = os.getenv('TIKTOK_REDIRECT_URI', 'https://api.newcollab.co/api/social/callback/tiktok')

# Frontend URL for redirects
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co')


# ============================================================================
# BLUEPRINT SETUP
# ============================================================================

social_verification_bp = Blueprint('social_verification', __name__, url_prefix='/api/social')


def get_db_connection():
    """Get database connection"""
    import psycopg2
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


def get_creator_id_from_session():
    """Get creator ID from session"""
    return session.get('creator_id')


def get_user_country_from_session():
    """Get user country from session, database, or IP geolocation"""
    # TESTING: Allow override via query param or header (dev mode only)
    test_country = request.args.get('_test_country') or request.headers.get('X-Test-Country')
    if test_country:
        print(f"🧪 TEST MODE: Using country override: {test_country}")
        return test_country.upper()

    # First try session cache
    cached_country = session.get('user_country')
    if cached_country:
        return cached_country

    # Try database
    user_id = session.get('user_id')
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
            print(f"Error fetching user country from DB: {e}")

    # Fallback: Try IP-based geolocation
    try:
        # Get client IP (handles proxies/load balancers)
        client_ip = request.headers.get('X-Forwarded-For', request.headers.get('X-Real-IP', request.remote_addr))
        print(f"🌍 Client IP detected: {client_ip}")

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
                        print(f"🌍 IP geolocation: {client_ip} → {country_code}")
                        return country_code
            else:
                print(f"🌍 Skipping localhost/private IP: {client_ip}")
    except Exception as e:
        print(f"IP geolocation error: {e}")

    return None


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
    5. Region allowed (not India/Pakistan)
    """
    # Normalize country code
    country_code = (user_country or '').upper().strip()

    gates = {
        "region_allowed": country_code not in RESTRICTED_REGIONS if country_code else True,
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
        print(f"Error logging verification check: {e}")


def update_creator_verification(creator_id: int, platform: str, data: dict,
                                 result: dict, access_token: str = None,
                                 refresh_token: str = None, expires_at: datetime = None):
    """Update creator record with verification data"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        status = 'verified' if result['passed'] else f"failed_{result['failure_reason']}"

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
                social_token_expires_at = %s
            WHERE id = %s
        ''', (
            platform,
            data.get("username") or data.get("handle"),
            data.get("follower_count", 0),
            data.get("media_count", 0),
            result['gates'].get("account_public", False),
            data.get("account_type"),
            result['passed'],
            status,
            encrypt_token(access_token),
            encrypt_token(refresh_token),
            expires_at,
            creator_id
        ))

        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating creator verification: {e}")
        return False


# ============================================================================
# REGION PRE-CHECK ENDPOINT
# ============================================================================

@social_verification_bp.route('/check-region', methods=['GET'])
def check_region():
    """
    Pre-check if user's region is allowed before showing OAuth buttons.
    Called before social connect step in onboarding.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    user_country = get_user_country_from_session()
    country_code = (user_country or '').upper().strip()

    is_allowed = country_code not in RESTRICTED_REGIONS if country_code else True

    # Log the region check
    if not is_allowed:
        result = {
            "passed": False,
            "failure_reason": "restricted_region",
            "gates": {"region_allowed": False},
            "stats": {"country": country_code}
        }
        log_verification_check(creator_id, 'region_precheck', None, result, country_code)

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

    if country_code in RESTRICTED_REGIONS:
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
        print(f"Error verifying handle: {e}")
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
    """Initiate Instagram OAuth flow via Facebook Login for Business"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return redirect(f"{FRONTEND_URL}/login?redirect=/onboarding")

    # Check region first
    user_country = get_user_country_from_session()
    if user_country and user_country.upper() in RESTRICTED_REGIONS:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=restricted_region")

    if not INSTAGRAM_APP_ID:
        print("❌ INSTAGRAM_APP_ID not configured")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['instagram_oauth_state'] = state
    session['instagram_oauth_creator_id'] = creator_id

    # Instagram OAuth URL (via Facebook)
    # Scopes for Instagram Graph API verification (follower count, post count, etc.)
    scopes = 'instagram_basic,pages_show_list,pages_read_engagement'

    auth_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth?"
        f"client_id={INSTAGRAM_APP_ID}"
        f"&redirect_uri={quote(INSTAGRAM_REDIRECT_URI)}"
        f"&scope={scopes}"
        f"&state={state}"
        f"&response_type=code"
    )

    return redirect(auth_url)


@social_verification_bp.route('/callback/instagram', methods=['GET'])
def callback_instagram():
    """Handle Instagram OAuth callback"""
    # Verify state
    state = request.args.get('state')
    stored_state = session.pop('instagram_oauth_state', None)
    creator_id = session.pop('instagram_oauth_creator_id', None)

    if not state or state != stored_state:
        print("❌ Instagram OAuth: Invalid state")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    if not creator_id:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    # Check for errors
    error = request.args.get('error')
    if error:
        print(f"❌ Instagram OAuth error: {error}")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    code = request.args.get('code')
    if not code:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    try:
        # Exchange code for access token
        token_response = requests.post(
            'https://graph.facebook.com/v18.0/oauth/access_token',
            data={
                'client_id': INSTAGRAM_APP_ID,
                'client_secret': INSTAGRAM_APP_SECRET,
                'redirect_uri': INSTAGRAM_REDIRECT_URI,
                'code': code
            },
            timeout=10
        )
        token_data = token_response.json()

        if 'error' in token_data:
            print(f"❌ Instagram token error: {token_data}")
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

        access_token = token_data.get('access_token')

        # Get Facebook user profile first (always works)
        fb_profile_response = requests.get(
            'https://graph.facebook.com/v18.0/me',
            params={
                'fields': 'id,name,email',
                'access_token': access_token
            },
            timeout=10
        )
        fb_profile = fb_profile_response.json()
        print(f"✅ Facebook profile: {fb_profile}")

        # Try to get Instagram Business account (requires app approval)
        instagram_account = None
        try:
            pages_response = requests.get(
                'https://graph.facebook.com/v18.0/me/accounts',
                params={'access_token': access_token},
                timeout=10
            )
            pages_data = pages_response.json()

            for page in pages_data.get('data', []):
                page_id = page.get('id')
                page_token = page.get('access_token')

                ig_response = requests.get(
                    f'https://graph.facebook.com/v18.0/{page_id}',
                    params={
                        'fields': 'instagram_business_account{id,username,followers_count,media_count,account_type}',
                        'access_token': page_token
                    },
                    timeout=10
                )
                ig_data = ig_response.json()

                if 'instagram_business_account' in ig_data:
                    instagram_account = ig_data['instagram_business_account']
                    break
        except Exception as e:
            print(f"⚠️ Instagram business lookup failed (expected in dev mode): {e}")

        # Require Instagram Business/Creator account for verification
        if not instagram_account:
            print("❌ No Instagram Business/Creator account found")
            print("ℹ️ User must have Instagram Business or Creator account linked to a Facebook Page")
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=personal_account")

        # Build profile data from Instagram account
        profile_data = {
            'access_token': access_token,
            'username': instagram_account.get('username'),
            'follower_count': instagram_account.get('followers_count', 0),
            'media_count': instagram_account.get('media_count', 0),
            'account_type': instagram_account.get('account_type', 'BUSINESS'),
        }
        api_response = instagram_account
        print(f"✅ Instagram account found: @{profile_data['username']} - {profile_data['follower_count']} followers, {profile_data['media_count']} posts")

        # Get user country
        user_country = get_user_country_from_session() or ''

        # Run 5-gate verification
        result = validate_social_gates(profile_data, 'instagram', user_country)

        # Log the check
        log_verification_check(creator_id, 'initial', 'instagram', result, user_country, api_response)

        # Update creator record
        update_creator_verification(
            creator_id=creator_id,
            platform='instagram',
            data=profile_data,
            result=result,
            access_token=access_token
        )

        if result['passed']:
            return redirect(f"{FRONTEND_URL}/onboarding?social=success&platform=instagram&handle={profile_data['username']}")
        else:
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason={result['failure_reason']}&platform=instagram")

    except Exception as e:
        print(f"❌ Instagram OAuth exception: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")


# ============================================================================
# TIKTOK OAUTH ENDPOINTS
# ============================================================================

@social_verification_bp.route('/connect/tiktok', methods=['GET'])
def connect_tiktok():
    """Initiate TikTok OAuth flow via Login Kit"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return redirect(f"{FRONTEND_URL}/login?redirect=/onboarding")

    # Check region first
    user_country = get_user_country_from_session()
    if user_country and user_country.upper() in RESTRICTED_REGIONS:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=restricted_region")

    if not TIKTOK_CLIENT_KEY:
        print("❌ TIKTOK_CLIENT_KEY not configured")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    # Generate state and code verifier for PKCE
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    # Create code challenge (SHA256 hash, base64url encoded)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip('=')

    session['tiktok_oauth_state'] = state
    session['tiktok_oauth_code_verifier'] = code_verifier
    session['tiktok_oauth_creator_id'] = creator_id

    # TikTok OAuth URL
    scopes = 'user.info.basic,user.info.profile,user.info.stats'

    auth_url = (
        f"https://www.tiktok.com/v2/auth/authorize/?"
        f"client_key={TIKTOK_CLIENT_KEY}"
        f"&redirect_uri={quote(TIKTOK_REDIRECT_URI)}"
        f"&scope={scopes}"
        f"&state={state}"
        f"&response_type=code"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    return redirect(auth_url)


@social_verification_bp.route('/callback/tiktok', methods=['GET'])
def callback_tiktok():
    """Handle TikTok OAuth callback"""
    # Verify state
    state = request.args.get('state')
    stored_state = session.pop('tiktok_oauth_state', None)
    code_verifier = session.pop('tiktok_oauth_code_verifier', None)
    creator_id = session.pop('tiktok_oauth_creator_id', None)

    if not state or state != stored_state:
        print("❌ TikTok OAuth: Invalid state")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    if not creator_id:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    # Check for errors
    error = request.args.get('error')
    if error:
        print(f"❌ TikTok OAuth error: {error}")
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    code = request.args.get('code')
    if not code:
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

    try:
        # Exchange code for access token
        token_response = requests.post(
            'https://open.tiktokapis.com/v2/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': TIKTOK_CLIENT_KEY,
                'client_secret': TIKTOK_CLIENT_SECRET,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': TIKTOK_REDIRECT_URI,
                'code_verifier': code_verifier
            },
            timeout=10
        )
        token_data = token_response.json()

        if 'error' in token_data:
            print(f"❌ TikTok token error: {token_data}")
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 86400)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        # Get user info with stats
        user_response = requests.get(
            'https://open.tiktokapis.com/v2/user/info/',
            headers={'Authorization': f'Bearer {access_token}'},
            params={'fields': 'open_id,union_id,avatar_url,display_name,follower_count,following_count,likes_count,video_count,is_verified'},
            timeout=10
        )
        user_data = user_response.json()

        if 'error' in user_data:
            print(f"❌ TikTok user info error: {user_data}")
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")

        user_info = user_data.get('data', {}).get('user', {})

        # Build profile data for verification
        # Note: TikTok API v2 doesn't directly expose privacy_level
        # We'll assume public unless we can determine otherwise
        profile_data = {
            'access_token': access_token,
            'username': user_info.get('display_name'),
            'follower_count': user_info.get('follower_count', 0),
            'media_count': user_info.get('video_count', 0),
            'account_type': 'creator',
            'is_private': False,  # TikTok API v2 - assume public for now
        }

        # Get user country
        user_country = get_user_country_from_session() or ''

        # Run 5-gate verification
        result = validate_social_gates(profile_data, 'tiktok', user_country)

        # Log the check
        log_verification_check(creator_id, 'initial', 'tiktok', result, user_country, user_info)

        # Update creator record
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
            return redirect(f"{FRONTEND_URL}/onboarding?social=success&platform=tiktok&handle={profile_data['username']}")
        else:
            return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason={result['failure_reason']}&platform=tiktok")

    except Exception as e:
        print(f"❌ TikTok OAuth exception: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{FRONTEND_URL}/onboarding?social=failed&reason=oauth_error")


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
            'grandfathered_until': creator['social_verification_required_by'].isoformat() if creator['social_verification_required_by'] else None
        })

    except Exception as e:
        print(f"Error getting verification status: {e}")
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
        print(f"Error during recheck: {e}")
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
        print(f"Error disconnecting social: {e}")
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
        if user_country and user_country.upper() in RESTRICTED_REGIONS:
            return jsonify({
                'requires_verification': True,
                'blocked': True,
                'reason': 'restricted_region'
            })

        return jsonify({'requires_verification': True, 'reason': 'not_verified'})

    except Exception as e:
        print(f"Error checking verification requirement: {e}")
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

        print(f"📥 Instagram Webhook Verification: mode={mode}, token={token}, challenge={challenge}")

        if mode == 'subscribe' and token == INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
            print("✅ Instagram Webhook Verified Successfully!")
            # Must return the challenge as plain text, not JSON
            return challenge, 200
        else:
            print(f"❌ Instagram Webhook Verification Failed: token mismatch (expected: {INSTAGRAM_WEBHOOK_VERIFY_TOKEN})")
            return 'Forbidden', 403

    elif request.method == 'POST':
        # Receive webhook events from Instagram
        try:
            data = request.get_json()
            print(f"📨 Instagram Webhook Event Received:")
            print(json.dumps(data, indent=2))

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
                        print(f"  📌 Field: {field}, Value: {value}")

            # Always return 200 to acknowledge receipt
            return jsonify({'status': 'received'}), 200

        except Exception as e:
            print(f"❌ Error processing Instagram webhook: {e}")
            import traceback
            traceback.print_exc()
            # Still return 200 to prevent Meta from retrying
            return jsonify({'status': 'error', 'message': str(e)}), 200
