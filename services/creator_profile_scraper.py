"""
Creator Profile Scraper Service

Handles:
1. In-house Instagram/TikTok scraping
2. Post-scrape processing (derived metrics)
3. Gemini Vision analysis for thumbnails
4. Storage in creator_profile_data table
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from services.inhouse_social_scraper import (
    scrape_instagram as diy_scrape_instagram,
    scrape_tiktok as diy_scrape_tiktok,
    diy_scrape_is_acceptable,
)

# Gemini configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_VISION_MODEL = 'gemini-2.5-flash'


class CreatorProfileScraper:
    """Handles scraping and enrichment of creator profiles."""

    def __init__(self, db_conn=None):
        self.db_conn = db_conn

    def scrape_instagram_profile(self, handle: str) -> Dict[str, Any]:
        """Scrape Instagram profile via in-house scraper only."""
        handle = handle.lstrip('@').strip()
        try:
            profile = diy_scrape_instagram(handle, results_limit=12)
            if diy_scrape_is_acceptable(profile, 'instagram'):
                print(f"[Scrape] ig @{handle} via diy")
                return profile
            raise ValueError(f"In-house Instagram scrape thin for @{handle}")
        except Exception as e:
            print(f"[Scrape] ig @{handle} diy failed: {e}")
            raise ValueError(f"Instagram scrape failed for @{handle}: {e}") from e

    def scrape_tiktok_profile(self, handle: str) -> Dict[str, Any]:
        """Scrape TikTok profile via in-house scraper only."""
        handle = handle.lstrip('@').strip()
        try:
            profile = diy_scrape_tiktok(handle, results_limit=12)
            if diy_scrape_is_acceptable(profile, 'tiktok'):
                print(f"[Scrape] tt @{handle} via diy")
                return profile
            raise ValueError(f"In-house TikTok scrape thin for @{handle}")
        except Exception as e:
            print(f"[Scrape] tt @{handle} diy failed: {e}")
            raise ValueError(f"TikTok scrape failed for @{handle}: {e}") from e

    def process_scrape(self, raw_scrape: Dict, platform: str) -> Dict[str, Any]:
        """
        Compute derived fields from raw scrape data.

        Args:
            raw_scrape: Raw data from in-house Instagram/TikTok scraper
            platform: 'instagram' or 'tiktok'

        Returns:
            Processed profile data with derived metrics
        """
        if platform == 'instagram':
            posts = raw_scrape.get('latestPosts', [])
            followers = raw_scrape.get('followersCount', 0)
            bio = raw_scrape.get('biography', '')
        else:  # tiktok
            posts = raw_scrape.get('latestVideos', [])
            followers = raw_scrape.get('followerCount', 0)
            bio = raw_scrape.get('signature', '')

        # Filter out pinned posts for recency calculation
        # TikTok pinned posts have isPinnedItem=True
        non_pinned_posts = [p for p in posts if not p.get('isPinnedItem', False)]
        # If all posts are pinned, fall back to all posts
        if not non_pinned_posts:
            non_pinned_posts = posts

        # Engagement rate (last 12 posts avg)
        total_engagement = 0
        for p in posts[:12]:
            if platform == 'instagram':
                total_engagement += (p.get('likesCount', 0) + p.get('commentsCount', 0))
            else:
                # TikTok: likes (diggCount), comments, shares
                total_engagement += (p.get('diggCount', 0) + p.get('commentCount', 0) + p.get('shareCount', 0))

        avg_engagement_per_post = total_engagement / max(len(posts), 1)
        # Without follower stats (common for DIY Instagram), don't invent a rate
        engagement_rate = (
            (avg_engagement_per_post / followers) * 100 if followers and followers > 0 else 0.0
        )

        # Post cadence (posts per week over last 30 days)
        # Use UTC to match timezone-aware post dates from Instagram
        from datetime import timezone
        now = datetime.now(timezone.utc)
        recent_posts = []
        for p in posts:
            post_date = self._parse_post_date(p, platform)
            if post_date:
                # Make timezone-naive dates UTC-aware for comparison
                if post_date.tzinfo is None:
                    post_date = post_date.replace(tzinfo=timezone.utc)
                if (now - post_date).days <= 30:
                    recent_posts.append(p)
        cadence = len(recent_posts) / 4.3  # per week

        # Recency - scan all non-pinned posts (feeds often put pinned/older first)
        latest_post_days_ago = 999
        if non_pinned_posts:
            for post in non_pinned_posts:
                post_date = self._parse_post_date(post, platform)
                if post_date:
                    # Make timezone-naive dates UTC-aware for comparison
                    if post_date.tzinfo is None:
                        post_date = post_date.replace(tzinfo=timezone.utc)
                    days_ago = (now - post_date).days
                    if days_ago < latest_post_days_ago:
                        latest_post_days_ago = days_ago

        # Bio signal extraction
        has_collab_email = bool(re.search(r'[\w.-]+@[\w.-]+\.\w+', bio))
        collab_email = None
        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', bio)
        if email_match:
            collab_email = email_match.group()

        # Extract recent captions
        recent_captions = []
        for p in posts[:12]:
            if platform == 'instagram':
                caption = p.get('caption', '')
            else:
                # TikTok: caption is in 'text' field
                caption = p.get('text', '')
            if caption:
                recent_captions.append(caption)

        # Extract thumbnail URLs (last 9 for vision analysis)
        # Use non-pinned posts first, then fill with pinned if needed
        thumbnail_urls = []
        posts_for_thumbnails = non_pinned_posts[:9] if len(non_pinned_posts) >= 3 else posts[:9]
        for p in posts_for_thumbnails:
            if platform == 'instagram':
                url = p.get('displayUrl', '')
            else:
                # TikTok: cover URL is in videoMeta.coverUrl or covers array
                video_meta = p.get('videoMeta', {})
                url = video_meta.get('coverUrl', '') or video_meta.get('originalCoverUrl', '')
                # Some TikTok scrapers use 'covers' array
                if not url and p.get('covers'):
                    url = p.get('covers', [''])[0]
            if url:
                thumbnail_urls.append(url)

        # Kit / PR-Ready need per-post engagement + shortcodes (not thumbs alone)
        recent_posts_payload = []
        for p in posts_for_thumbnails:
            if not isinstance(p, dict):
                continue
            if platform == 'instagram':
                thumb = p.get('displayUrl') or ''
                code = (p.get('shortCode') or p.get('shortcode') or '').strip()
                post_url = f"https://www.instagram.com/p/{code}/" if code else None
                likes = int(p.get('likesCount') or 0)
                comments = int(p.get('commentsCount') or 0)
                views = int(p.get('videoViewCount') or p.get('viewsCount') or p.get('views') or 0)
                caption = (p.get('caption') or '')[:500]
            else:
                video_meta = p.get('videoMeta') or {}
                thumb = (
                    video_meta.get('coverUrl')
                    or video_meta.get('originalCoverUrl')
                    or ((p.get('covers') or [''])[0] if p.get('covers') else '')
                    or ''
                )
                vid = str(p.get('id') or p.get('videoId') or '').strip()
                post_url = f"https://www.tiktok.com/@{raw_scrape.get('uniqueId') or ''}/video/{vid}" if vid else None
                likes = int(p.get('diggCount') or p.get('likesCount') or 0)
                comments = int(p.get('commentCount') or p.get('commentsCount') or 0)
                views = int(p.get('playCount') or p.get('videoViewCount') or 0)
                caption = (p.get('text') or p.get('caption') or '')[:500]
                code = vid
            if not thumb and not code:
                continue
            recent_posts_payload.append({
                'thumbnail_url': thumb,
                'post_url': post_url,
                'shortCode': code,
                'likes': likes,
                'comments': comments,
                'views': views,
                'shares': int(p.get('shareCount') or 0),
                'saves': int(p.get('collectCount') or 0),
                'caption': caption,
                'timestamp': p.get('timestamp') or p.get('createTime') or '',
            })

        # Build result
        result = {
            # Platform metadata
            'primary_platform': platform,
            'handle': raw_scrape.get('username') or raw_scrape.get('uniqueId', ''),
            'full_name': raw_scrape.get('fullName') or raw_scrape.get('nickname', ''),
            'raw_bio': bio,
            'external_url': raw_scrape.get('externalUrl') or raw_scrape.get('bioLink', ''),

            # Public stats
            'follower_count': followers,
            'following_count': raw_scrape.get('followsCount') or raw_scrape.get('followingCount', 0),
            'post_count': raw_scrape.get('postsCount') or raw_scrape.get('videoCount', 0),
            'is_verified': raw_scrape.get('isVerified') or raw_scrape.get('verified', False),
            'is_public': not (raw_scrape.get('isPrivate') or raw_scrape.get('privateAccount', False)),
            'is_business_account': raw_scrape.get('isBusinessAccount', False),
            'business_category': raw_scrape.get('businessCategoryName', ''),

            # Derived metrics
            'engagement_rate': round(engagement_rate, 2),
            'posting_cadence_per_week': round(cadence, 1),
            'latest_post_days_ago': latest_post_days_ago,

            # Bio signals
            'has_collab_email': has_collab_email,
            'collab_email_extracted': collab_email,

            # Content archive
            'recent_post_thumbnails': thumbnail_urls,
            'recent_posts': recent_posts_payload,
            'recent_captions': recent_captions,

            # Freshness
            'scraped_at': datetime.now(),
            'next_refresh_at': datetime.now() + timedelta(days=7),
        }

        return result

    def _parse_post_date(self, post: Dict, platform: str) -> Optional[datetime]:
        """Parse post date from raw data."""
        try:
            if platform == 'instagram':
                timestamp_str = post.get('timestamp')
                if timestamp_str:
                    return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:  # tiktok
                create_time = post.get('createTime')
                if create_time:
                    from datetime import timezone as _tz
                    return datetime.fromtimestamp(int(create_time), tz=_tz.utc)
        except:
            pass
        return None

    def run_text_analysis(self, bio: str, captions: List[str],
                           handle: str, followers: int,
                           hashtags: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run text-only analysis on creator bio, captions, and hashtags.
        Much cheaper and faster than vision analysis.

        Args:
            bio: Creator's bio text
            captions: Recent post captions
            handle: Creator's handle
            followers: Follower count
            hashtags: Optional list of hashtags from posts

        Returns:
            Analysis results (same schema as vision)
        """
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")

        # Clean text by removing emojis and special unicode characters
        def clean_text(text):
            if not text:
                return ''
            return re.sub(r'[^\x00-\x7F]+', ' ', text).strip()

        # Clean inputs
        bio = clean_text(bio)
        captions = [clean_text(c) for c in captions]

        # Extract hashtags from captions if not provided
        if not hashtags:
            hashtags = []
            for caption in captions:
                tags = re.findall(r'#(\w+)', caption)
                hashtags.extend(tags)

        # System prompt for text analysis
        system_prompt = '''You analyze a creator's bio, captions, and hashtags to determine their niche
and content style. Output ONE strict JSON object:

{
  "primary_niche": "beauty" | "makeup" | "haircare" | "skincare" | "wellness" |
                   "fashion" | "fitness" | "food" | "lifestyle" |
                   "travel" | "tech" | "gaming" | "pet" | "home" | "other",
  "primary_niche_confidence": integer 0-100,
  "secondary_niches": ["string"],
  "content_themes": ["string"],
  "content_format_breakdown": {
    "product_close_ups": integer 0-12 (count of posts showing product close-ups),
    "grwm_routine": integer 0-12 (count of get-ready-with-me or routine posts),
    "before_after": integer 0-12 (count of transformation/before-after posts),
    "selfies_face_focus": integer 0-12,
    "lifestyle_context": integer 0-12,
    "other": integer 0-12
  },
  "aesthetic": {
    "color_palette": "warm" | "cool" | "neutral" | "vibrant" | "muted" | "mixed",
    "composition_style": "clean_minimal" | "maximalist" | "casual_authentic" | "polished_studio" | "mixed",
    "aesthetic_descriptors": ["string"]
  },
  "brand_readiness_signals": {
    "shows_products_in_use": boolean (TRUE if captions mention applying, trying, swatching, reviewing, testing products),
    "captions_niche_relevant": boolean,
    "already_features_brands": boolean,
    "brands_already_tagged": ["string"]
  },
  "content_gaps": ["string"]
}

RULES:
- Detect niche from hashtags, caption keywords, and bio
- "makeup" and "beauty" are separate niches - use "makeup" if they focus on makeup tutorials/looks
- brands_already_tagged: extract brand names mentioned in captions/bio (e.g., "Rhode", "Rare Beauty", "Charlotte Tilbury")
- shows_products_in_use: TRUE if captions contain words like: apply, applying, trying, tried, swatch, review, testing, using, wore, wearing, demo
- content_format_breakdown: estimate counts based on caption content (tutorials, reviews, GRWM mentions, etc.)
- content_themes: main topics/themes in their content'''

        # Build user prompt
        hashtag_text = ', '.join(list(set(hashtags))[:30]) if hashtags else 'None'
        caption_text = '\n'.join(captions[:12]) if captions else 'No captions'

        user_prompt = f'''Handle: @{handle}
Followers: {followers:,}

Bio:
{bio}

Hashtags (from recent posts):
{hashtag_text}

Recent captions:
{caption_text}

Analyze and return JSON only.'''

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_VISION_MODEL}:generateContent?key={GEMINI_API_KEY}"

            payload = {
                'contents': [{
                    'parts': [
                        {'text': system_prompt},
                        {'text': user_prompt}
                    ]
                }],
                'generationConfig': {
                    'temperature': 0.1,  # Lower temp for more consistent JSON
                    'maxOutputTokens': 2048,
                    'responseMimeType': 'application/json',  # Force JSON output
                }
            }

            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()

            result = response.json()

            # Extract text from response
            candidates = result.get('candidates', [])
            if not candidates:
                print(f"[TextAnalysis] No candidates in response")
                return self._get_fallback_vision_result()

            text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')

            if not text:
                print(f"[TextAnalysis] Empty text in response")
                return self._get_fallback_vision_result()

            # Parse JSON from response with robust extraction
            analysis_result = self._extract_json_safely(text)

            # Add fields expected by downstream code
            if 'content_format_breakdown' not in analysis_result:
                analysis_result['content_format_breakdown'] = {
                    'product_close_ups': 0,
                    'selfies_face_focus': 0,
                    'grwm_routine': 0,
                    'before_after': 0,
                    'lifestyle_context': 0,
                    'text_heavy': 0,
                    'other': 0
                }

            if 'aesthetic' not in analysis_result:
                analysis_result['aesthetic'] = {}

            analysis_result['aesthetic'].setdefault('aesthetic_consistency_score', 70)
            analysis_result['aesthetic'].setdefault('specific_colors', [])

            if 'brand_readiness_signals' not in analysis_result:
                analysis_result['brand_readiness_signals'] = {}

            # Smart default for shows_products_in_use based on niche and content format
            # Beauty/makeup/skincare creators almost always show products in use
            niche = (analysis_result.get('primary_niche') or '').lower()
            content_format = analysis_result.get('content_format_breakdown', {})
            product_content = (
                content_format.get('product_close_ups', 0) +
                content_format.get('grwm_routine', 0) +
                content_format.get('before_after', 0)
            )
            beauty_niches = ['beauty', 'makeup', 'skincare', 'haircare', 'cosmetics']
            default_shows_products = niche in beauty_niches or product_content > 0
            analysis_result['brand_readiness_signals'].setdefault('shows_products_in_use', default_shows_products)
            analysis_result['brand_readiness_signals'].setdefault('professional_lighting', False)
            analysis_result['brand_readiness_signals'].setdefault('text_overlays_frequent', False)
            analysis_result['brand_readiness_signals'].setdefault('consistent_editing_style', False)

            return analysis_result

        except json.JSONDecodeError as e:
            print(f"[TextAnalysis] JSON parse error: {e}")
            return self._get_fallback_vision_result()
        except requests.RequestException as e:
            print(f"[TextAnalysis] API request failed: {e}")
            return self._get_fallback_vision_result()

    def run_vision_analysis(self, thumbnail_urls: List[str], bio: str,
                            captions: List[str], handle: str,
                            followers: int) -> Dict[str, Any]:
        """
        Run text-based analysis (cheaper than vision).
        Falls back to basic extraction if Gemini fails.

        Args:
            thumbnail_urls: List of thumbnail URLs (not used, kept for compatibility)
            bio: Creator's bio text
            captions: Recent post captions
            handle: Creator's handle
            followers: Follower count

        Returns:
            Analysis results
        """
        # Use text analysis instead of vision (cheaper, faster, no blocking)
        return self.run_text_analysis(bio, captions, handle, followers)

    def _extract_json_safely(self, text: str) -> Dict[str, Any]:
        """
        Robustly extract and parse JSON from LLM response.
        Handles code blocks, malformed JSON, and common errors.
        """
        if not text:
            raise json.JSONDecodeError("Empty text", "", 0)

        original_text = text

        # Step 1: Remove markdown code blocks
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            parts = text.split('```')
            if len(parts) >= 3:
                text = parts[1].strip()

        # Step 2: Find JSON object boundaries
        start_idx = text.find('{')
        if start_idx == -1:
            raise json.JSONDecodeError("No JSON object found", text, 0)

        # Find matching closing brace
        brace_count = 0
        end_idx = -1
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start_idx:], start_idx):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break

        if end_idx == -1:
            # Try to fix unterminated JSON by adding closing braces
            text = text[start_idx:]
            # Count unclosed braces and brackets
            open_braces = text.count('{') - text.count('}')
            open_brackets = text.count('[') - text.count(']')

            # Fix unterminated strings - find the last unclosed quote
            quote_count = 0
            for i, char in enumerate(text):
                if char == '"' and (i == 0 or text[i-1] != '\\'):
                    quote_count += 1
            if quote_count % 2 == 1:
                text = text + '"'

            # Add missing closing brackets/braces
            text = text + (']' * max(0, open_brackets)) + ('}' * max(0, open_braces))
        else:
            text = text[start_idx:end_idx + 1]

        # Step 3: Clean common JSON issues
        # Remove trailing commas before ] or }
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)

        # Fix single quotes to double quotes (common LLM error)
        # Only replace if not inside a string already
        text = re.sub(r"(?<![a-zA-Z])'([^']*)'(?=\s*[,}\]])", r'"\1"', text)

        # Step 4: Try to parse
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[TextAnalysis] JSON repair failed: {e}")
            # Last resort: try to extract key fields manually
            return self._extract_fields_manually(original_text)

    def _extract_fields_manually(self, text: str) -> Dict[str, Any]:
        """
        Extract key fields using regex when JSON parsing completely fails.
        Returns a valid structure with whatever we can extract.
        """
        result = self._get_fallback_vision_result()

        # Try to extract primary_niche
        niche_patterns = [
            r'"primary_niche"\s*:\s*"([^"]+)"',
            r"primary_niche['\"]?\s*:\s*['\"]?(\w+)",
        ]
        for pattern in niche_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result['primary_niche'] = match.group(1).lower()
                break

        # Try to extract confidence
        conf_match = re.search(r'"primary_niche_confidence"\s*:\s*(\d+)', text)
        if conf_match:
            result['primary_niche_confidence'] = int(conf_match.group(1))

        # Extract brands_already_tagged
        brands_match = re.search(r'"brands_already_tagged"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if brands_match:
            brands_str = brands_match.group(1)
            brands = re.findall(r'"([^"]+)"', brands_str)
            result['brand_readiness_signals']['brands_already_tagged'] = brands

        # Extract content_themes
        themes_match = re.search(r'"content_themes"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if themes_match:
            themes_str = themes_match.group(1)
            themes = re.findall(r'"([^"]+)"', themes_str)
            result['content_themes'] = themes

        # Check for shows_products_in_use
        products_match = re.search(r'"shows_products_in_use"\s*:\s*(true|false)', text, re.IGNORECASE)
        if products_match:
            result['brand_readiness_signals']['shows_products_in_use'] = products_match.group(1).lower() == 'true'

        result['_extracted_manually'] = True
        print(f"[TextAnalysis] Extracted manually: niche={result['primary_niche']}")
        return result

    def _get_fallback_vision_result(self) -> Dict[str, Any]:
        """Return a minimal valid vision result when analysis fails."""
        return {
            'primary_niche': 'lifestyle',
            'primary_niche_confidence': 30,
            'secondary_niches': [],
            'content_format_breakdown': {
                'product_close_ups': 0,
                'selfies_face_focus': 0,
                'grwm_routine': 0,
                'before_after': 0,
                'lifestyle_context': 0,
                'text_heavy': 0,
                'other': 9
            },
            'aesthetic': {
                'color_palette': 'mixed',
                'specific_colors': [],
                'composition_style': 'casual_authentic',
                'aesthetic_consistency_score': 50,
                'aesthetic_descriptors': []
            },
            'content_themes': [],
            'brand_readiness_signals': {
                'shows_products_in_use': False,
                'professional_lighting': False,
                'text_overlays_frequent': False,
                'consistent_editing_style': False,
                'captions_niche_relevant': False,
                'already_features_brands': False,
                'brands_already_tagged': []
            },
            'content_gaps': ['vision_analysis_unavailable'],
            '_fallback': True
        }

    def save_creator_profile(self, user_id, profile_data: Dict,
                            vision_data: Optional[Dict] = None) -> bool:
        """
        Save or update creator profile data in database.

        Args:
            user_id: User ID (integer)
            profile_data: Processed profile data from scraper
            vision_data: Optional vision analysis results

        Returns:
            True on success
        """
        if not self.db_conn:
            raise ValueError("Database connection required")

        cursor = self.db_conn.cursor()

        # Merge vision data if available
        vision_status = 'pending'
        if vision_data:
            profile_data['primary_niche'] = vision_data.get('primary_niche')
            profile_data['primary_niche_confidence'] = vision_data.get('primary_niche_confidence')
            profile_data['secondary_niches'] = vision_data.get('secondary_niches', [])
            profile_data['content_format_breakdown'] = json.dumps(vision_data.get('content_format_breakdown', {}))
            profile_data['aesthetic'] = json.dumps(vision_data.get('aesthetic', {}))
            profile_data['content_themes'] = vision_data.get('content_themes', [])
            profile_data['brand_readiness_signals'] = json.dumps(vision_data.get('brand_readiness_signals', {}))
            profile_data['content_gaps'] = vision_data.get('content_gaps', [])
            profile_data['brands_already_tagged'] = vision_data.get('brand_readiness_signals', {}).get('brands_already_tagged', [])
            vision_status = 'success'

        try:
            # Kit engagement lives in recent_posts (added after initial schema)
            try:
                cursor.execute(
                    "ALTER TABLE creator_profile_data "
                    "ADD COLUMN IF NOT EXISTS recent_posts JSONB DEFAULT '[]'::jsonb"
                )
            except Exception as col_err:
                print(f"[Scrape] recent_posts column ensure: {col_err}")
                try:
                    self.db_conn.rollback()
                except Exception:
                    pass
                cursor = self.db_conn.cursor()

            # Upsert creator profile data
            cursor.execute('''
                INSERT INTO creator_profile_data (
                    user_id, primary_platform, handle, full_name, raw_bio, external_url,
                    follower_count, following_count, post_count, is_verified, is_public,
                    is_business_account, business_category,
                    engagement_rate, posting_cadence_per_week, latest_post_days_ago,
                    has_collab_email, collab_email_extracted,
                    primary_niche, primary_niche_confidence, secondary_niches,
                    content_format_breakdown, aesthetic, content_themes,
                    brand_readiness_signals, content_gaps, brands_already_tagged,
                    recent_post_thumbnails, recent_posts, recent_captions,
                    data_confidence, vision_analysis_status,
                    scraped_at, last_refresh_at, next_refresh_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s::jsonb, %s,
                    %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (user_id) DO UPDATE SET
                    primary_platform = EXCLUDED.primary_platform,
                    handle = EXCLUDED.handle,
                    full_name = EXCLUDED.full_name,
                    raw_bio = EXCLUDED.raw_bio,
                    external_url = EXCLUDED.external_url,
                    follower_count = EXCLUDED.follower_count,
                    following_count = EXCLUDED.following_count,
                    post_count = EXCLUDED.post_count,
                    is_verified = EXCLUDED.is_verified,
                    is_public = EXCLUDED.is_public,
                    is_business_account = EXCLUDED.is_business_account,
                    business_category = EXCLUDED.business_category,
                    engagement_rate = EXCLUDED.engagement_rate,
                    posting_cadence_per_week = EXCLUDED.posting_cadence_per_week,
                    latest_post_days_ago = EXCLUDED.latest_post_days_ago,
                    has_collab_email = EXCLUDED.has_collab_email,
                    collab_email_extracted = EXCLUDED.collab_email_extracted,
                    primary_niche = EXCLUDED.primary_niche,
                    primary_niche_confidence = EXCLUDED.primary_niche_confidence,
                    secondary_niches = EXCLUDED.secondary_niches,
                    content_format_breakdown = EXCLUDED.content_format_breakdown,
                    aesthetic = EXCLUDED.aesthetic,
                    content_themes = EXCLUDED.content_themes,
                    brand_readiness_signals = EXCLUDED.brand_readiness_signals,
                    content_gaps = EXCLUDED.content_gaps,
                    brands_already_tagged = EXCLUDED.brands_already_tagged,
                    recent_post_thumbnails = EXCLUDED.recent_post_thumbnails,
                    recent_posts = EXCLUDED.recent_posts,
                    recent_captions = EXCLUDED.recent_captions,
                    data_confidence = EXCLUDED.data_confidence,
                    vision_analysis_status = EXCLUDED.vision_analysis_status,
                    scraped_at = EXCLUDED.scraped_at,
                    last_refresh_at = EXCLUDED.last_refresh_at,
                    next_refresh_at = EXCLUDED.next_refresh_at
            ''', (
                user_id,
                profile_data.get('primary_platform'),
                profile_data.get('handle'),
                profile_data.get('full_name'),
                profile_data.get('raw_bio'),
                profile_data.get('external_url'),
                profile_data.get('follower_count'),
                profile_data.get('following_count'),
                profile_data.get('post_count'),
                profile_data.get('is_verified'),
                profile_data.get('is_public'),
                profile_data.get('is_business_account'),
                profile_data.get('business_category'),
                profile_data.get('engagement_rate'),
                profile_data.get('posting_cadence_per_week'),
                profile_data.get('latest_post_days_ago'),
                profile_data.get('has_collab_email'),
                profile_data.get('collab_email_extracted'),
                profile_data.get('primary_niche'),
                profile_data.get('primary_niche_confidence'),
                profile_data.get('secondary_niches', []),
                profile_data.get('content_format_breakdown'),
                profile_data.get('aesthetic'),
                profile_data.get('content_themes', []),
                profile_data.get('brand_readiness_signals'),
                profile_data.get('content_gaps', []),
                profile_data.get('brands_already_tagged', []),
                profile_data.get('recent_post_thumbnails', []),
                json.dumps(profile_data.get('recent_posts') or []),
                profile_data.get('recent_captions', []),
                'scraped',
                vision_status,
                profile_data.get('scraped_at'),
                datetime.now(),
                profile_data.get('next_refresh_at'),
            ))

            self.db_conn.commit()
            return True

        except Exception as e:
            self.db_conn.rollback()
            raise e

    def get_creator_profile(self, user_id) -> Optional[Dict]:
        """
        Get creator profile data from database.

        Args:
            user_id: User ID (integer)

        Returns:
            Profile data dict or None
        """
        if not self.db_conn:
            raise ValueError("Database connection required")

        # Ensure user_id is an integer
        try:
            user_id_int = int(user_id)
        except (ValueError, TypeError):
            return None

        from psycopg2.extras import RealDictCursor
        cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT * FROM creator_profile_data WHERE user_id = %s
        ''', (user_id_int,))

        result = cursor.fetchone()
        if result:
            # Parse JSONB fields
            if result.get('content_format_breakdown'):
                if isinstance(result['content_format_breakdown'], str):
                    result['content_format_breakdown'] = json.loads(result['content_format_breakdown'])
            if result.get('aesthetic'):
                if isinstance(result['aesthetic'], str):
                    result['aesthetic'] = json.loads(result['aesthetic'])
            if result.get('brand_readiness_signals'):
                if isinstance(result['brand_readiness_signals'], str):
                    result['brand_readiness_signals'] = json.loads(result['brand_readiness_signals'])

        return dict(result) if result else None


def scrape_and_enrich_creator(user_id, handle: str, platform: str,
                              db_conn=None, skip_minimums: bool = False) -> Tuple[Dict, Optional[Dict]]:
    """
    Full pipeline: scrape profile, process, run text analysis, save.

    Args:
        user_id: User UUID
        handle: Social media handle
        platform: 'instagram' or 'tiktok'
        db_conn: Database connection
        skip_minimums: Skip follower/post minimum checks (for onboarding)

    Returns:
        Tuple of (profile_data, vision_data)
    """
    scraper = CreatorProfileScraper(db_conn)

    # Step 1: Scrape profile
    if platform == 'instagram':
        raw_scrape = scraper.scrape_instagram_profile(handle)
    else:
        raw_scrape = scraper.scrape_tiktok_profile(handle)

    # Check for private account (always enforce)
    is_private = raw_scrape.get('isPrivate') or raw_scrape.get('privateAccount', False) or raw_scrape.get('private', False)
    if is_private:
        raise ValueError(f"Account @{handle} is private")

    # Check minimum followers (skip for onboarding)
    followers = raw_scrape.get('followersCount') or raw_scrape.get('followerCount', 0)
    if not skip_minimums and followers < 500:
        raise ValueError(f"Account @{handle} has fewer than 500 followers")

    # Check minimum posts (skip for onboarding)
    posts = raw_scrape.get('postsCount') or raw_scrape.get('videoCount', 0)
    if not skip_minimums and posts < 5:
        raise ValueError(f"Account @{handle} has fewer than 5 posts")

    # Step 2: Process scrape
    profile_data = scraper.process_scrape(raw_scrape, platform)

    # Step 3: Run vision analysis
    vision_data = None
    try:
        vision_data = scraper.run_vision_analysis(
            thumbnail_urls=profile_data.get('recent_post_thumbnails', []),
            bio=profile_data.get('raw_bio', ''),
            captions=profile_data.get('recent_captions', []),
            handle=profile_data.get('handle', ''),
            followers=profile_data.get('follower_count', 0)
        )
    except Exception as e:
        print(f"Vision analysis failed for @{handle}: {e}")
        # Continue without vision data

    # Step 4: Save to database
    if db_conn:
        scraper.save_creator_profile(user_id, profile_data, vision_data)

    return profile_data, vision_data
