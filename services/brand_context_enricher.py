"""
Brand Context Enrichment Service

Handles:
1. Scraping brand's own Instagram for aesthetic analysis
2. Aggregating historical accepted creator patterns
3. Storing enriched brand context for AI matching
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# Apify configuration
APIFY_API_TOKEN = os.getenv('APIFY_API_TOKEN')
APIFY_INSTAGRAM_ACTOR = 'apify/instagram-profile-scraper'

# Gemini configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_VISION_MODEL = 'gemini-2.0-flash'


class BrandContextEnricher:
    """Handles enrichment of brand context data for AI matching."""

    def __init__(self, db_conn=None):
        self.db_conn = db_conn
        self.apify_token = APIFY_API_TOKEN

    def scrape_brand_instagram(self, handle: str) -> Dict[str, Any]:
        """
        Scrape brand's Instagram profile for aesthetic analysis.

        Args:
            handle: Brand's Instagram handle

        Returns:
            Raw profile data
        """
        handle = handle.lstrip('@').strip()

        if not self.apify_token:
            raise ValueError("APIFY_API_TOKEN not configured")

        url = f"https://api.apify.com/v2/acts/{APIFY_INSTAGRAM_ACTOR}/run-sync-get-dataset-items"

        headers = {
            'Authorization': f'Bearer {self.apify_token}',
            'Content-Type': 'application/json'
        }

        payload = {
            'usernames': [handle],
            'resultsLimit': 12,
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()
            if not data or len(data) == 0:
                raise ValueError(f"No data returned for brand @{handle}")

            return data[0]

        except requests.Timeout:
            raise TimeoutError(f"Scrape timeout for brand @{handle}")
        except requests.HTTPError as e:
            raise ValueError(f"Failed to scrape brand @{handle}: {e}")

    def analyze_brand_aesthetic(self, raw_scrape: Dict) -> Dict[str, Any]:
        """
        Run Gemini Vision analysis on brand's Instagram to extract aesthetic.

        Args:
            raw_scrape: Raw Instagram profile data

        Returns:
            Aesthetic analysis results
        """
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")

        posts = raw_scrape.get('latestPosts', [])
        if not posts:
            raise ValueError("No posts to analyze")

        # Get thumbnail URLs
        thumbnail_urls = [p.get('displayUrl', '') for p in posts[:9] if p.get('displayUrl')]
        if not thumbnail_urls:
            raise ValueError("No thumbnails available")

        # System prompt for brand aesthetic analysis
        system_prompt = '''You analyze a brand's Instagram feed to extract their visual aesthetic
and content preferences. This data will be used to match creators to brands.

Output ONE strict JSON object:

{
  "aesthetic_color_palette": "warm" | "cool" | "neutral" | "high_contrast" | "muted" | "vibrant",
  "aesthetic_specific_colors": ["string"],  // e.g. ["cream", "gold", "sage green"]
  "aesthetic_style": "clean_minimal" | "maximalist" | "editorial" | "casual_authentic" | "polished_studio" | "mixed",
  "aesthetic_descriptors": ["string"],  // 3-5 short descriptors

  "preferred_content_formats": ["string"],  // e.g. ["Reels", "product close-ups", "before_after"]
  "preferred_content_themes": ["string"],  // e.g. ["wash-day routine", "bathroom shelfie"]

  "hero_products": ["string"],  // product names visible in posts
  "recent_launches": ["string"],  // any new products featured

  "target_audience_desc": "string"  // inferred from content, e.g. "women 25-40, curly hair"
}

RULES:
- Be specific about colors (not just "blue" but "dusty blue", "navy")
- aesthetic_descriptors should be phrases a designer would use
- hero_products should only include product names actually visible
- target_audience_desc should be inferred from models shown and content context'''

        bio = raw_scrape.get('biography', '')
        brand_name = raw_scrape.get('fullName', raw_scrape.get('username', ''))

        user_prompt = f'''Brand: {brand_name}
Bio: {bio}

Attached: 9 recent posts from this brand's Instagram.

Analyze their aesthetic and return the JSON.'''

        try:
            # Build image parts
            image_parts = []
            for url in thumbnail_urls[:9]:
                try:
                    img_response = requests.get(url, timeout=10)
                    if img_response.status_code == 200:
                        import base64
                        img_data = base64.b64encode(img_response.content).decode('utf-8')
                        content_type = img_response.headers.get('Content-Type', 'image/jpeg')
                        image_parts.append({
                            'inline_data': {
                                'mime_type': content_type,
                                'data': img_data
                            }
                        })
                except:
                    continue

            if not image_parts:
                raise ValueError("Could not fetch brand thumbnails")

            # Call Gemini Vision
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_VISION_MODEL}:generateContent?key={GEMINI_API_KEY}"

            payload = {
                'contents': [{
                    'parts': [
                        {'text': system_prompt},
                        *image_parts,
                        {'text': user_prompt}
                    ]
                }],
                'generationConfig': {
                    'temperature': 0.2,
                    'topK': 1,
                    'topP': 0.8,
                    'maxOutputTokens': 1024,
                }
            }

            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()

            result = response.json()
            text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')

            # Parse JSON
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0].strip()
            elif '```' in text:
                text = text.split('```')[1].split('```')[0].strip()

            return json.loads(text)

        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse brand aesthetic JSON: {e}")
        except requests.RequestException as e:
            raise ValueError(f"Brand vision API request failed: {e}")

    def aggregate_accepted_creator_stats(self, brand_id: str) -> Dict[str, Any]:
        """
        Aggregate statistics from creators who received PR from this brand.

        Uses pr_packages table where status indicates positive reply.

        Args:
            brand_id: Brand UUID

        Returns:
            Aggregated stats dict
        """
        if not self.db_conn:
            return {}

        from psycopg2.extras import RealDictCursor
        cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)

        # Get creators who had positive interactions with this brand
        cursor.execute('''
            SELECT
                c.follower_count,
                c.engagement_rate,
                c.niche as primary_niche,
                cpd.primary_niche as vision_niche
            FROM pr_packages pp
            JOIN creators c ON pp.creator_id = c.id
            LEFT JOIN creator_profile_data cpd ON c.user_id = cpd.user_id
            WHERE pp.brand_id = %s
            AND pp.status IN ('replied', 'accepted', 'gifted')
        ''', (brand_id,))

        accepted = cursor.fetchall()

        if not accepted:
            return {}

        # Calculate aggregates
        followers = [c['follower_count'] for c in accepted if c.get('follower_count')]
        engagement_rates = [float(c['engagement_rate']) for c in accepted if c.get('engagement_rate')]
        niches = []
        for c in accepted:
            if c.get('vision_niche'):
                niches.append(c['vision_niche'])
            elif c.get('primary_niche'):
                niches.append(c['primary_niche'])

        result = {}

        if followers:
            result['accepted_follower_range_min'] = min(followers)
            result['accepted_follower_range_max'] = max(followers)

        if engagement_rates:
            result['accepted_engagement_rate_min'] = min(engagement_rates)
            result['accepted_engagement_rate_median'] = sorted(engagement_rates)[len(engagement_rates) // 2]

        if niches:
            # Find most common niche
            from collections import Counter
            niche_counts = Counter(niches)
            result['accepted_niche_primary'] = niche_counts.most_common(1)[0][0]
            result['accepted_niches_all'] = list(set(niches))

        return result

    def enrich_brand(self, brand_id: str, brand_ig_handle: Optional[str] = None) -> Dict[str, Any]:
        """
        Full enrichment pipeline for a brand.

        Args:
            brand_id: Brand UUID
            brand_ig_handle: Optional Instagram handle (fetched from DB if not provided)

        Returns:
            Enriched brand context dict
        """
        if not self.db_conn:
            raise ValueError("Database connection required")

        from psycopg2.extras import RealDictCursor
        cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)

        # Get brand info
        cursor.execute('SELECT * FROM pr_brands WHERE id = %s', (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            raise ValueError(f"Brand {brand_id} not found")

        brand_name = brand.get('brand_name') or brand.get('name')

        # Get Instagram handle
        ig_handle = brand_ig_handle or brand.get('instagram_handle')
        if not ig_handle and brand.get('social_links'):
            # Try to extract from social links
            links = brand['social_links']
            if isinstance(links, str):
                links = json.loads(links)
            if isinstance(links, dict) and links.get('instagram'):
                ig_handle = links['instagram'].rstrip('/').split('/')[-1]

        context = {
            'brand_id': brand_id,
            'brand_instagram_handle': ig_handle,
            'enriched_at': datetime.now(),
            'next_enrichment_at': datetime.now() + timedelta(days=30),
            'data_sources': {}
        }

        # Step 1: Scrape brand's Instagram
        if ig_handle:
            try:
                raw_scrape = self.scrape_brand_instagram(ig_handle)
                context['data_sources']['instagram_scraped'] = True

                # Step 2: Analyze aesthetic
                aesthetic = self.analyze_brand_aesthetic(raw_scrape)
                context.update({
                    'aesthetic_color_palette': aesthetic.get('aesthetic_color_palette'),
                    'aesthetic_specific_colors': aesthetic.get('aesthetic_specific_colors', []),
                    'aesthetic_style': aesthetic.get('aesthetic_style'),
                    'aesthetic_descriptors': aesthetic.get('aesthetic_descriptors', []),
                    'preferred_content_formats': aesthetic.get('preferred_content_formats', []),
                    'preferred_content_themes': aesthetic.get('preferred_content_themes', []),
                    'hero_products': aesthetic.get('hero_products', []),
                    'recent_launches': aesthetic.get('recent_launches', []),
                    'target_audience_desc': aesthetic.get('target_audience_desc'),
                })
                context['data_sources']['vision_analyzed'] = True

            except Exception as e:
                print(f"Failed to scrape/analyze brand IG for {brand_name}: {e}")
                context['data_sources']['instagram_scraped'] = False

        # Step 3: Aggregate accepted creator stats
        try:
            creator_stats = self.aggregate_accepted_creator_stats(brand_id)
            context.update(creator_stats)
            context['data_sources']['creator_stats_aggregated'] = True
        except Exception as e:
            print(f"Failed to aggregate creator stats for {brand_name}: {e}")

        # Step 4: Extract brand mission from DB if available
        if brand.get('description'):
            context['brand_mission_summary'] = brand['description'][:500]  # Truncate

        return context

    def save_brand_context(self, context: Dict) -> bool:
        """
        Save brand context to database.

        Args:
            context: Enriched brand context dict

        Returns:
            True on success
        """
        if not self.db_conn:
            raise ValueError("Database connection required")

        cursor = self.db_conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO brand_context (
                    brand_id, aesthetic_color_palette, aesthetic_specific_colors,
                    aesthetic_style, aesthetic_descriptors,
                    preferred_content_formats, preferred_content_themes,
                    accepted_follower_range_min, accepted_follower_range_max,
                    accepted_engagement_rate_min, accepted_engagement_rate_median,
                    accepted_niche_primary, accepted_niches_all,
                    hero_products, recent_launches,
                    target_audience_desc, brand_mission_summary,
                    brand_instagram_handle,
                    enriched_at, next_enrichment_at, data_sources
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (brand_id) DO UPDATE SET
                    aesthetic_color_palette = EXCLUDED.aesthetic_color_palette,
                    aesthetic_specific_colors = EXCLUDED.aesthetic_specific_colors,
                    aesthetic_style = EXCLUDED.aesthetic_style,
                    aesthetic_descriptors = EXCLUDED.aesthetic_descriptors,
                    preferred_content_formats = EXCLUDED.preferred_content_formats,
                    preferred_content_themes = EXCLUDED.preferred_content_themes,
                    accepted_follower_range_min = EXCLUDED.accepted_follower_range_min,
                    accepted_follower_range_max = EXCLUDED.accepted_follower_range_max,
                    accepted_engagement_rate_min = EXCLUDED.accepted_engagement_rate_min,
                    accepted_engagement_rate_median = EXCLUDED.accepted_engagement_rate_median,
                    accepted_niche_primary = EXCLUDED.accepted_niche_primary,
                    accepted_niches_all = EXCLUDED.accepted_niches_all,
                    hero_products = EXCLUDED.hero_products,
                    recent_launches = EXCLUDED.recent_launches,
                    target_audience_desc = EXCLUDED.target_audience_desc,
                    brand_mission_summary = EXCLUDED.brand_mission_summary,
                    brand_instagram_handle = EXCLUDED.brand_instagram_handle,
                    enriched_at = EXCLUDED.enriched_at,
                    next_enrichment_at = EXCLUDED.next_enrichment_at,
                    data_sources = EXCLUDED.data_sources
            ''', (
                context.get('brand_id'),
                context.get('aesthetic_color_palette'),
                context.get('aesthetic_specific_colors', []),
                context.get('aesthetic_style'),
                context.get('aesthetic_descriptors', []),
                context.get('preferred_content_formats', []),
                context.get('preferred_content_themes', []),
                context.get('accepted_follower_range_min'),
                context.get('accepted_follower_range_max'),
                context.get('accepted_engagement_rate_min'),
                context.get('accepted_engagement_rate_median'),
                context.get('accepted_niche_primary'),
                context.get('accepted_niches_all', []),
                context.get('hero_products', []),
                context.get('recent_launches', []),
                context.get('target_audience_desc'),
                context.get('brand_mission_summary'),
                context.get('brand_instagram_handle'),
                context.get('enriched_at'),
                context.get('next_enrichment_at'),
                json.dumps(context.get('data_sources', {})),
            ))

            self.db_conn.commit()
            return True

        except Exception as e:
            self.db_conn.rollback()
            raise e

    def get_brand_context(self, brand_id: str) -> Optional[Dict]:
        """
        Get brand context from database.

        Args:
            brand_id: Brand UUID

        Returns:
            Brand context dict or None
        """
        if not self.db_conn:
            raise ValueError("Database connection required")

        from psycopg2.extras import RealDictCursor
        cursor = self.db_conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT * FROM brand_context WHERE brand_id = %s', (brand_id,))
        result = cursor.fetchone()

        if result:
            # Parse JSONB fields
            if result.get('data_sources') and isinstance(result['data_sources'], str):
                result['data_sources'] = json.loads(result['data_sources'])

        return dict(result) if result else None


def enrich_and_save_brand(brand_id: str, db_conn, ig_handle: Optional[str] = None) -> Dict:
    """
    Full pipeline to enrich and save brand context.

    Args:
        brand_id: Brand UUID
        db_conn: Database connection
        ig_handle: Optional Instagram handle

    Returns:
        Enriched brand context
    """
    enricher = BrandContextEnricher(db_conn)
    context = enricher.enrich_brand(brand_id, ig_handle)
    enricher.save_brand_context(context)
    return context
