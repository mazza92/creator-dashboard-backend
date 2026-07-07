"""
Public Profile Fetcher for Social Verification
Fetches Instagram/TikTok profile data from public endpoints (no OAuth required)
"""

import requests
import re
import json
import time
from typing import Optional, Dict, Any

# Create a session to persist cookies across requests
_session = requests.Session()
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',  # Removed 'br' (Brotli) as it requires extra library
    'Connection': 'keep-alive',
})


class ProfileFetchError(Exception):
    """Custom exception for profile fetch errors"""
    pass


def fetch_instagram_profile(username: str, debug: bool = False) -> Dict[str, Any]:
    """
    Fetch Instagram public profile data.
    Uses multiple methods for reliability.

    Args:
        username: Instagram username
        debug: If True, saves HTML response to file for inspection

    Returns:
        {
            'username': str,
            'full_name': str,
            'follower_count': int,
            'following_count': int,
            'media_count': int,
            'is_private': bool,
            'is_verified': bool,
            'biography': str,
            'profile_pic_url': str
        }
    """
    # Clean username (remove @ if present)
    username = username.lstrip('@').strip().lower()

    if not username:
        raise ProfileFetchError("Username is required")

    # Validate username format
    if not re.match(r'^[a-z0-9._]+$', username):
        raise ProfileFetchError("Invalid Instagram username format")

    # Try Method 1: Instagram's web API endpoint
    result = _try_instagram_web_api(username)
    if result and result.get('follower_count', 0) > 0:
        return result

    # Try Method 2: Instagram GraphQL API
    result = _try_instagram_graphql(username)
    if result and result.get('follower_count', 0) > 0:
        return result

    # Try Method 3: i.instagram.com mobile API (often less restricted)
    result = _try_instagram_mobile_api(username)
    if result and result.get('follower_count', 0) > 0:
        return result

    # Try Method 4: HTML fallback with embedded JSON
    return _fetch_instagram_from_html(username, debug=debug)


def _try_instagram_web_api(username: str) -> Optional[Dict[str, Any]]:
    """Try Instagram's web API endpoint"""
    try:
        # First, visit the profile page to get cookies
        profile_url = f"https://www.instagram.com/{username}/"
        _session.get(profile_url, timeout=5)

        # Small delay to seem more human-like
        time.sleep(0.3)

        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"

        headers = {
            'Accept': '*/*',
            'X-IG-App-ID': '936619743392459',  # Instagram web app ID
            'X-Requested-With': 'XMLHttpRequest',
            'X-ASBD-ID': '129477',
            'Referer': profile_url,
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }

        response = _session.get(url, headers=headers, timeout=10)
        print(f"[IG] Instagram Web API response status: {response.status_code}")

        if response.status_code == 404:
            raise ProfileFetchError("Instagram account not found")

        if response.status_code != 200:
            print(f"[IG] Instagram Web API failed ({response.status_code})")
            return None

        data = response.json()
        print(f"[IG] Instagram API raw data keys: {data.keys() if data else 'None'}")
        user_data = data.get('data', {}).get('user', {})
        print(f"[IG] Instagram user_data keys: {list(user_data.keys()) if user_data else 'None'}")

        if not user_data:
            print("[IG] No user_data in response")
            return None

        is_private = user_data.get('is_private', False)
        follower_count = user_data.get('edge_followed_by', {}).get('count', 0)
        media_count = user_data.get('edge_owner_to_timeline_media', {}).get('count', 0)

        print(f"[IG] Instagram @{username}: is_private={is_private}, followers={follower_count}, posts={media_count}")

        return {
            'username': user_data.get('username', username),
            'full_name': user_data.get('full_name', ''),
            'follower_count': follower_count,
            'following_count': user_data.get('edge_follow', {}).get('count', 0),
            'media_count': media_count,
            'is_private': is_private,
            'is_verified': user_data.get('is_verified', False),
            'biography': user_data.get('biography', ''),
            'profile_pic_url': user_data.get('profile_pic_url_hd', user_data.get('profile_pic_url', '')),
            'account_type': 'CREATOR' if user_data.get('is_business_account') else 'PERSONAL'
        }

    except requests.exceptions.RequestException as e:
        print(f"[IG] Instagram Web API error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[IG] Instagram Web API JSON error: {e}")
        return None


def _try_instagram_mobile_api(username: str) -> Optional[Dict[str, Any]]:
    """Try Instagram's mobile web API - sometimes less restricted"""
    try:
        # Mobile web endpoint
        url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"

        headers = {
            'User-Agent': 'Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-IG-App-ID': '567067343352427',  # Instagram Android app ID
            'X-IG-Device-ID': 'android-1234567890123456',
            'X-IG-Android-ID': 'android-1234567890123456',
        }

        response = requests.get(url, headers=headers, timeout=10)
        print(f"[IG] Instagram Mobile API response status: {response.status_code}")

        if response.status_code != 200:
            return None

        data = response.json()
        user_data = data.get('data', {}).get('user', {})

        if not user_data:
            return None

        is_private = user_data.get('is_private', False)
        follower_count = user_data.get('edge_followed_by', {}).get('count', 0)
        media_count = user_data.get('edge_owner_to_timeline_media', {}).get('count', 0)

        print(f"[IG] Mobile API @{username}: is_private={is_private}, followers={follower_count}, posts={media_count}")

        return {
            'username': user_data.get('username', username),
            'full_name': user_data.get('full_name', ''),
            'follower_count': follower_count,
            'following_count': user_data.get('edge_follow', {}).get('count', 0),
            'media_count': media_count,
            'is_private': is_private,
            'is_verified': user_data.get('is_verified', False),
            'biography': user_data.get('biography', ''),
            'profile_pic_url': user_data.get('profile_pic_url_hd', user_data.get('profile_pic_url', '')),
            'account_type': 'CREATOR' if user_data.get('is_business_account') else 'PERSONAL'
        }

    except Exception as e:
        print(f"[IG] Instagram Mobile API error: {e}")
        return None


def _try_instagram_graphql(username: str) -> Optional[Dict[str, Any]]:
    """Try Instagram's GraphQL endpoint - Note: often blocked, kept as fallback"""
    try:
        url = f"https://www.instagram.com/{username}/?__a=1&__d=dis"

        headers = {
            'Accept': 'application/json',
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }

        response = _session.get(url, headers=headers, timeout=10)
        print(f"[IG] Instagram GraphQL response status: {response.status_code}")

        if response.status_code != 200:
            return None

        # Check if response is actually JSON
        content_type = response.headers.get('Content-Type', '')
        if 'application/json' not in content_type and 'text/javascript' not in content_type:
            print(f"[IG] GraphQL returned non-JSON: {content_type}")
            return None

        data = response.json()

        # Try different JSON paths
        user_data = data.get('graphql', {}).get('user', {}) or data.get('user', {})

        if not user_data:
            print("[IG] No user data in GraphQL response")
            return None

        is_private = user_data.get('is_private', False)
        follower_count = user_data.get('edge_followed_by', {}).get('count', 0)
        media_count = user_data.get('edge_owner_to_timeline_media', {}).get('count', 0)

        print(f"[IG] GraphQL @{username}: is_private={is_private}, followers={follower_count}, posts={media_count}")

        return {
            'username': user_data.get('username', username),
            'full_name': user_data.get('full_name', ''),
            'follower_count': follower_count,
            'following_count': user_data.get('edge_follow', {}).get('count', 0),
            'media_count': media_count,
            'is_private': is_private,
            'is_verified': user_data.get('is_verified', False),
            'biography': user_data.get('biography', ''),
            'profile_pic_url': user_data.get('profile_pic_url_hd', user_data.get('profile_pic_url', '')),
            'account_type': 'CREATOR' if user_data.get('is_business_account') else 'PERSONAL'
        }

    except json.JSONDecodeError as e:
        print(f"[IG] Instagram GraphQL not JSON: {e}")
        return None
    except Exception as e:
        print(f"[IG] Instagram GraphQL error: {e}")
        return None


def _fetch_instagram_from_html(username: str, debug: bool = False) -> Dict[str, Any]:
    """
    Fallback: Fetch Instagram profile from HTML page.
    Extracts data from embedded JSON or meta tags.
    """
    try:
        url = f"https://www.instagram.com/{username}/"
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Cache-Control': 'no-cache',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        }

        response = _session.get(url, headers=headers, timeout=10)
        print(f"[IG] Instagram HTML response status: {response.status_code}")

        if response.status_code == 404:
            raise ProfileFetchError("Instagram account not found")

        html = response.text

        # Debug: Save HTML to file for inspection
        if debug:
            debug_file = f"instagram_debug_{username}.html"
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"[IG] DEBUG: Saved HTML to {debug_file}")

        # Check if we got a login page or challenge
        if 'Login • Instagram' in html or 'challenge' in html.lower():
            print("[IG] WARNING: Instagram returned login/challenge page")

        # Method 1: Try to find shared_data JSON (older Instagram format)
        shared_data_match = re.search(r'window\._sharedData\s*=\s*(\{.+?\});', html)
        if shared_data_match:
            try:
                shared_data = json.loads(shared_data_match.group(1))
                user_data = shared_data.get('entry_data', {}).get('ProfilePage', [{}])[0].get('graphql', {}).get('user', {})
                if user_data:
                    print(f"[IG] Found shared_data JSON")
                    return _parse_instagram_user_data(user_data, username)
            except (json.JSONDecodeError, IndexError, KeyError) as e:
                print(f"[IG] shared_data parse error: {e}")

        # Method 2: Try additional_data format
        additional_data_match = re.search(r'"additional_data":\s*(\{[^}]+\})', html)

        # Method 3: Look for __PRELOADED_QUERIES__ or similar data structures
        preloaded_match = re.search(r'"xdt_api__v1__users__web_profile_info"[^}]*"user":\s*(\{[^}]+(?:\{[^}]*\}[^}]*)*\})', html)
        if preloaded_match:
            try:
                # This is complex nested JSON, try to extract key fields
                user_json = preloaded_match.group(1)
                print(f"[IG] Found preloaded user data")
            except Exception as e:
                print(f"[IG] Preloaded data parse error: {e}")

        # Method 4: Try to find embedded user info in script tags
        # Instagram embeds data in various script formats
        user_json_patterns = [
            r'"user":\s*(\{"id"[^}]+\})',
            r'ProfilePage":\s*\[(\{.+?\})\]',
            r'"graphql":\s*\{"user":\s*(\{.+?\})\}',
            r'"username":"' + re.escape(username) + r'"[^}]*"edge_followed_by":\s*\{"count":\s*(\d+)\}',
        ]

        for pattern in user_json_patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    user_data = json.loads(match.group(1))
                    print(f"[IG] Found user JSON via pattern")
                    return _parse_instagram_user_data(user_data, username)
                except json.JSONDecodeError:
                    continue

        # Method 5: Extract individual fields with targeted patterns
        # This works even when we can't parse the full JSON
        extracted_followers = None
        extracted_posts = None
        extracted_private = None

        # Look for follower count in various formats
        follower_json_match = re.search(rf'"username":"{re.escape(username)}"[^{{}}]*?"edge_followed_by":\s*{{\s*"count":\s*(\d+)', html)
        if follower_json_match:
            extracted_followers = int(follower_json_match.group(1))
            print(f"[IG] Extracted followers from JSON: {extracted_followers}")

        # Look for is_private
        private_match = re.search(rf'"username":"{re.escape(username)}"[^{{}}]*?"is_private":\s*(true|false)', html, re.IGNORECASE)
        if private_match:
            extracted_private = private_match.group(1).lower() == 'true'
            print(f"[IG] Extracted is_private from JSON: {extracted_private}")

        # Look for media count
        media_json_match = re.search(rf'"username":"{re.escape(username)}"[^{{}}]*?"edge_owner_to_timeline_media":\s*{{\s*"count":\s*(\d+)', html)
        if media_json_match:
            extracted_posts = int(media_json_match.group(1))
            print(f"[IG] Extracted posts from JSON: {extracted_posts}")

        # Method 6: Parse from meta tags and visible text
        print(f"[IG] Falling back to meta tag parsing")

        # Meta description often contains: "123 Followers, 45 Following, 67 Posts"
        meta_match = re.search(r'<meta[^>]+content="([^"]*(?:Followers|Following|Posts)[^"]*)"', html, re.IGNORECASE)
        if meta_match:
            meta_content = meta_match.group(1)
            print(f"[IG] Meta content: {meta_content}")

        # Use extracted values if we found them, otherwise search more
        follower_count = extracted_followers if extracted_followers is not None else 0
        media_count = extracted_posts if extracted_posts is not None else 0

        # If we didn't extract from JSON, try other patterns
        if follower_count == 0:
            follower_patterns = [
                r'"edge_followed_by":\s*\{\s*"count":\s*(\d+)',
                r'"follower_count":\s*(\d+)',
                r'(\d+(?:,\d+)*(?:\.\d+)?[KMB]?)\s*Followers',
                r'content="[^"]*?(\d+(?:,\d+)*)\s*Followers',
            ]

            for pattern in follower_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    follower_count = _parse_count(match.group(1))
                    if follower_count > 0:
                        print(f"[IG] Found followers via fallback pattern: {follower_count}")
                        break

        if media_count == 0:
            media_patterns = [
                r'"edge_owner_to_timeline_media":\s*\{\s*"count":\s*(\d+)',
                r'"media_count":\s*(\d+)',
                r'(\d+(?:,\d+)*(?:\.\d+)?[KMB]?)\s*Posts',
            ]

            for pattern in media_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    media_count = _parse_count(match.group(1))
                    if media_count >= 0:
                        print(f"[IG] Found posts via fallback pattern: {media_count}")
                        break

        # Check if private account - use extracted value first, then fallback to text detection
        # NOTE: Private detection is NOT reliable without OAuth - Instagram doesn't expose
        # is_private in HTML when serving login/challenge pages
        if extracted_private is not None:
            is_private = extracted_private
            print(f"[IG] Private status from JSON: {is_private}")
        else:
            # Try multiple detection methods
            is_private = False

            # Method 1: Direct text patterns
            if 'This Account is Private' in html or 'This account is private' in html:
                is_private = True
                print("[IG] Detected private via text pattern")
            # Method 2: JSON patterns
            elif '"is_private":true' in html or '"is_private": true' in html:
                is_private = True
                print("[IG] Detected private via JSON pattern")
            # Method 3: No media thumbnails visible (heuristic)
            # Private accounts typically don't show post grid
            elif follower_count > 0 and media_count > 0:
                # If we have counts but got a login page, we can't determine privacy
                # Default to False (public) but log the uncertainty
                if 'Login' in html and 'Instagram' in html:
                    print("[IG] WARNING: Got login page - cannot reliably determine privacy status")
                    print("[IG] Defaulting to is_private=False (privacy check requires OAuth)")
                    # For accounts below minimum, this doesn't matter since they fail on followers anyway
                    is_private = False

        print(f"[IG] Final values: followers={follower_count}, posts={media_count}, is_private={is_private}")

        return {
            'username': username,
            'full_name': '',
            'follower_count': follower_count,
            'following_count': 0,
            'media_count': media_count,
            'is_private': is_private,
            'is_verified': False,
            'biography': '',
            'profile_pic_url': '',
            'account_type': 'UNKNOWN'
        }

    except requests.exceptions.RequestException as e:
        raise ProfileFetchError(f"Failed to fetch Instagram profile: {str(e)}")


def _parse_instagram_user_data(user_data: dict, username: str) -> Dict[str, Any]:
    """Parse Instagram user data from JSON object"""
    is_private = user_data.get('is_private', False)
    follower_count = user_data.get('edge_followed_by', {}).get('count', 0) or user_data.get('follower_count', 0)
    media_count = user_data.get('edge_owner_to_timeline_media', {}).get('count', 0) or user_data.get('media_count', 0)

    print(f"[IG] Parsed @{username}: is_private={is_private}, followers={follower_count}, posts={media_count}")

    return {
        'username': user_data.get('username', username),
        'full_name': user_data.get('full_name', ''),
        'follower_count': follower_count,
        'following_count': user_data.get('edge_follow', {}).get('count', 0),
        'media_count': media_count,
        'is_private': is_private,
        'is_verified': user_data.get('is_verified', False),
        'biography': user_data.get('biography', ''),
        'profile_pic_url': user_data.get('profile_pic_url_hd', user_data.get('profile_pic_url', '')),
        'account_type': 'CREATOR' if user_data.get('is_business_account') else 'PERSONAL'
    }


def _parse_count(count_str: str) -> int:
    """Parse count string like '1.5K' or '1,234' to int"""
    if not count_str:
        return 0
    count_str = str(count_str).replace(',', '')
    multiplier = 1
    if count_str.endswith('K'):
        multiplier = 1000
        count_str = count_str[:-1]
    elif count_str.endswith('M'):
        multiplier = 1000000
        count_str = count_str[:-1]
    elif count_str.endswith('B'):
        multiplier = 1000000000
        count_str = count_str[:-1]
    try:
        return int(float(count_str) * multiplier)
    except ValueError:
        return 0


def fetch_tiktok_profile(username: str) -> Dict[str, Any]:
    """
    Fetch TikTok public profile data.

    Returns:
        {
            'username': str,
            'nickname': str,
            'follower_count': int,
            'following_count': int,
            'video_count': int,
            'heart_count': int,
            'is_private': bool,
            'is_verified': bool,
            'biography': str,
            'profile_pic_url': str
        }
    """
    # Clean username (remove @ if present)
    username = username.lstrip('@').strip()

    if not username:
        raise ProfileFetchError("Username is required")

    try:
        # TikTok web profile
        url = f"https://www.tiktok.com/@{username}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)

        if response.status_code == 404:
            raise ProfileFetchError("TikTok account not found")

        html = response.text

        # TikTok embeds JSON data in a script tag with id="__UNIVERSAL_DATA_FOR_REHYDRATION__"
        # or in SIGI_STATE
        json_match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)</script>', html)

        if json_match:
            try:
                data = json.loads(json_match.group(1))
                user_data = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {})
                user = user_data.get('user', {})
                stats = user_data.get('stats', {})

                return {
                    'username': user.get('uniqueId', username),
                    'nickname': user.get('nickname', ''),
                    'follower_count': stats.get('followerCount', 0),
                    'following_count': stats.get('followingCount', 0),
                    'video_count': stats.get('videoCount', 0),
                    'heart_count': stats.get('heartCount', 0),
                    'is_private': user.get('privateAccount', False),
                    'is_verified': user.get('verified', False),
                    'biography': user.get('signature', ''),
                    'profile_pic_url': user.get('avatarLarger', ''),
                    'account_type': 'CREATOR'
                }
            except json.JSONDecodeError:
                pass

        # Fallback: Parse from meta tags or other patterns
        # TikTok's HTML structure changes frequently, so this is a best-effort
        follower_match = re.search(r'"followerCount":\s*(\d+)', html)
        video_match = re.search(r'"videoCount":\s*(\d+)', html)

        return {
            'username': username,
            'nickname': '',
            'follower_count': int(follower_match.group(1)) if follower_match else 0,
            'following_count': 0,
            'video_count': int(video_match.group(1)) if video_match else 0,
            'heart_count': 0,
            'is_private': False,  # TikTok accounts are generally public
            'is_verified': False,
            'biography': '',
            'profile_pic_url': '',
            'account_type': 'CREATOR'
        }

    except requests.exceptions.RequestException as e:
        raise ProfileFetchError(f"Failed to fetch TikTok profile: {str(e)}")


def verify_bio_contains_code(platform: str, username: str, verification_code: str) -> bool:
    """
    Verify that the user's bio contains the verification code.
    Used to prove account ownership.

    Args:
        platform: 'instagram' or 'tiktok'
        username: The social media handle
        verification_code: The code that should be in their bio

    Returns:
        True if the code is found in the bio
    """
    try:
        if platform == 'instagram':
            profile = fetch_instagram_profile(username)
        elif platform == 'tiktok':
            profile = fetch_tiktok_profile(username)
        else:
            return False

        biography = profile.get('biography', '')
        return verification_code in biography

    except ProfileFetchError:
        return False


# Quick test
if __name__ == '__main__':
    import sys

    # Allow testing specific username via command line
    # Usage: python social_profile_fetcher.py instagram mlz1192 [--debug]
    if len(sys.argv) >= 3:
        platform = sys.argv[1].lower()
        test_username = sys.argv[2]
        debug_mode = '--debug' in sys.argv

        print(f"\n{'='*50}")
        print(f"Testing {platform} profile: @{test_username}")
        if debug_mode:
            print("DEBUG MODE: Will save HTML response to file")
        print(f"{'='*50}\n")

        try:
            if platform == 'instagram':
                profile = fetch_instagram_profile(test_username, debug=debug_mode)
            elif platform == 'tiktok':
                profile = fetch_tiktok_profile(test_username)
            else:
                print(f"Unknown platform: {platform}")
                sys.exit(1)

            print(f"\n{'='*50}")
            print("RESULT:")
            print(f"{'='*50}")
            for key, value in profile.items():
                print(f"  {key}: {value}")

            # Summary for verification
            print(f"\n{'='*50}")
            print("VERIFICATION STATUS:")
            print(f"{'='*50}")
            if profile.get('is_private'):
                print("  [X] FAIL: Account is PRIVATE")
            elif profile.get('follower_count', 0) < 500:
                print(f"  [X] FAIL: Only {profile.get('follower_count', 0)} followers (need 500+)")
            elif profile.get('media_count', 0) < 5:
                print(f"  [X] FAIL: Only {profile.get('media_count', 0)} posts (need 5+)")
            else:
                print("  [OK] PASS: Account meets all requirements")

        except ProfileFetchError as e:
            print(f"Error: {e}")
    else:
        # Default test with public accounts
        print("Usage: python social_profile_fetcher.py <platform> <username>")
        print("Example: python social_profile_fetcher.py instagram mlz1192")
        print("\nRunning default tests...\n")

        try:
            print("Testing Instagram fetch (official account)...")
            ig_profile = fetch_instagram_profile('instagram')
            print(f"Instagram: {ig_profile}")
        except ProfileFetchError as e:
            print(f"Instagram error: {e}")

        try:
            print("\nTesting TikTok fetch (official account)...")
            tt_profile = fetch_tiktok_profile('tiktok')
            print(f"TikTok: {tt_profile}")
        except ProfileFetchError as e:
            print(f"TikTok error: {e}")
