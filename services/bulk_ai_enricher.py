# -*- coding: utf-8 -*-
"""
Bulk AI Brand Enrichment Service

Enriches brands with missing fields (hero_product, tone, description, category, etc.) using Claude Haiku.
This is the bulk version of the "AI Enrich" button in the brand admin interface.

Uses the same AI model as admin: Claude Haiku (claude-haiku-4-5-20251001)

Fills in:
- category (always updated to correct category)
- description (if missing)
- hero_product (if missing)
- tone/voice (if missing)
"""
import os
import sys
import json
import requests
import time
from typing import Dict, List, Optional

# Windows consoles default to cp1252 and crash on brand names like "ē"
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
CLAUDE_MODEL = 'claude-haiku-4-5-20251001'  # Same model as admin AI enricher
UNSPLASH_ACCESS_KEY = os.getenv('UNSPLASH_ACCESS_KEY', '')  # Optional: for cover images

try:
    from services.brand_image_scraper import is_broken_cover_url, scrape_brand_images
except ImportError:
    from brand_image_scraper import is_broken_cover_url, scrape_brand_images


def generate_slug(brand_name: str) -> str:
    """
    Generate URL-friendly slug from brand name.

    Examples:
        "Glow Recipe" -> "glow-recipe"
        "100% PURE" -> "100-pure"
        "Aveeno (US)" -> "aveeno"
    """
    import re

    # Remove suffixes like (US), (UK), etc.
    slug = re.sub(r'\s*\([A-Z]{2}\)\s*$', '', brand_name)

    # Convert to lowercase
    slug = slug.lower()

    # Replace spaces and special chars with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', slug)

    # Remove leading/trailing hyphens
    slug = slug.strip('-')

    return slug


def generate_unique_slug(brand_name: str, cursor) -> str:
    """
    Generate a unique slug by checking database and appending a number if needed.

    Examples:
        "Bala" -> "bala" (if available)
        "Bala" -> "bala-1" (if "bala" exists)
        "Bala" -> "bala-2" (if "bala" and "bala-1" exist)
    """
    base_slug = generate_slug(brand_name)
    slug = base_slug
    counter = 1

    while True:
        # Check if slug exists
        cursor.execute("SELECT COUNT(*) as count FROM pr_brands WHERE slug = %s", (slug,))
        if cursor.fetchone()['count'] == 0:
            return slug

        # Slug exists, try with counter
        slug = f"{base_slug}-{counter}"
        counter += 1

        # Safety limit to prevent infinite loop
        if counter > 100:
            # Fallback: append brand ID or timestamp
            import time
            return f"{base_slug}-{int(time.time())}"


def enrich_brand_with_ai(brand: Dict) -> Optional[Dict]:
    """
    Use Claude Haiku AI to enrich a single brand's missing fields.
    Matches the comprehensive enrichment scope of the individual AI enrich endpoint.

    Args:
        brand: Brand dict with at minimum: name, website

    Returns:
        Dict with enriched fields or None if enrichment fails
    """
    if not ANTHROPIC_API_KEY:
        print("[AI Enrich] ERROR: ANTHROPIC_API_KEY not configured")
        return None

    brand_name = brand.get('brand_name', '')
    website = brand.get('website', '')
    existing_description = brand.get('description', '')
    current_category = brand.get('category', 'beauty')

    if not brand_name or not website:
        return None

    # Comprehensive prompt matching the individual enrich endpoint
    prompt = f"""You are extracting structured data from a brand's website for a PR/influencer database.

Brand: {brand_name}
Website: {website}
Current category: {current_category}
{f"Existing description: {existing_description}" if existing_description else ""}

Extract the following as JSON:
{{
  "hero_product": "their most well-known or bestselling product with SPECIFIC name (e.g. 'Peptide Glazing Fluid', 'Power Leggings'). If unclear, use primary product category.",
  "target_audience": "who they sell to in under 12 words (e.g. 'women 25-40 into clean beauty')",
  "tone": "one of: premium / casual / wellness / functional / luxury / playful / minimalist / bold",
  "price_point": "average single product price in USD as integer (e.g. 32, 85). Use 0 if unclear.",
  "description": "one sentence max 25 words: [Brand] makes [product type] for [customer].",
  "category": "MUST be one of: skincare | beauty | fashion | wellness | fitness | food | travel | tech | gaming | lifestyle | home | pet | baby | jewelry | haircare | sustainable | luxury | activewear | supplements | other",

  "instagram_handle": "Instagram username WITHOUT @ (e.g. 'glossier'). Look for instagram.com links. Use null if not found.",
  "tiktok_handle": "TikTok username WITHOUT @ (e.g. 'glossier'). Look for tiktok.com links. Use null if not found.",
  "youtube_handle": "YouTube channel name or handle (e.g. 'Glossier'). Look for youtube.com links. Use null if not found.",

  "min_followers": "minimum follower count for PR gifting based on brand size. Small indie brands: 1000-5000. Mid-size DTC: 5000-10000. Major brands: 10000-50000. Return as integer.",
  "collaboration_type": "one of: gifted / paid / both. Most brands do 'gifted' for micro-influencers. Larger budgets do 'both'. Use 'gifted' if unclear.",

  "seo_title": "SEO-optimized title for brand page, max 60 chars. Format: '[Brand] PR Contact & Gifting | Free Products for Influencers'",
  "seo_description": "SEO meta description, max 155 chars. Include: brand name, what they gift, who should apply.",

  "success_stories": "Write 1-2 brief fictional but realistic success stories of influencers getting PR from this brand. Format: 'Sarah (@sarahbeauty, 12K followers) received [product] and created [content type]. Her post got [engagement].' Max 200 words total.",

  "response_rate": "estimated % of PR applications this brand responds to (10-90). Larger brands: 15-30%. Smaller brands: 40-70%. Return as integer.",
  "avg_response_time_days": "estimated days to respond (1-30). Larger brands: 7-14 days. Smaller brands: 3-7 days. Return as integer."
}}

CATEGORY RULES - VERY IMPORTANT:
- Analyze the brand's products carefully from the website and name
- skincare: Products focused on skin health (serums, moisturizers, cleansers)
- beauty: Makeup, cosmetics, multi-category beauty brands
- fashion: Clothing, accessories, apparel
- wellness: Supplements, holistic health, mental wellness
- fitness: Workout gear, athletic performance, gym equipment
- food: Food products, snacks, beverages, nutrition
- jewelry: Jewelry, watches, accessories
- haircare: Shampoo, conditioner, styling products
- activewear: Athletic clothing, sportswear
- sustainable: Eco-focused brands (can combine with primary category if sustainability is core)
- luxury: High-end premium brands
- other: If truly doesn't fit any category
- Select the MOST SPECIFIC category that matches the brand's primary products

Return ONLY valid JSON, no explanation.

JSON:"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1500,  # Increased for comprehensive enrichment
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=45  # Increased timeout for larger response
        )
        response.raise_for_status()

        result = response.json()
        text = result.get('content', [{}])[0].get('text', '')

        # Parse JSON from response
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        enriched = json.loads(text)
        return enriched

    except json.JSONDecodeError as e:
        print(f"[AI Enrich] JSON parse error for {brand_name}: {e}")
        print(f"[AI Enrich] Raw response: {text[:200]}...")
        return None
    except requests.RequestException as e:
        print(f"[AI Enrich] API error for {brand_name}: {e}")
        return None
    except Exception as e:
        print(f"[AI Enrich] Unexpected error for {brand_name}: {e}")
        return None


_SOCIAL_FALSE_POSITIVES = {
    'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'share', 'tags',
    'tv', 'live', 'direct', 'about', 'legal', 'privacy', 'help', 'login',
    'signup', 'embed', 'static', 'directory', 'popular', 'trending', 'foryou',
    'following', 'friends', 'music', 'discover', 'search', 'home', 'null', 'none',
}


def scrape_social_handles_from_website(website: str) -> Dict[str, Optional[str]]:
    """
    Fast HTML scrape for Instagram / TikTok handles from a brand website.
    Prefer this over full AI when only social handles are missing.
    """
    import re

    result = {'instagram_handle': None, 'tiktok_handle': None}
    if not website:
        return result

    url = website if str(website).startswith('http') else f'https://{website}'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        response.raise_for_status()
        page = response.text
    except Exception as e:
        print(f"[Social Fill] Scrape failed for {url}: {e}")
        return result

    ig_patterns = [
        r'instagram\.com/(?:@)?([A-Za-z0-9._]{2,40})/?',
    ]
    tt_patterns = [
        r'tiktok\.com/@([A-Za-z0-9._]{2,40})/?',
        r'tiktok\.com/([A-Za-z0-9._]{2,40})/?',
    ]

    for pattern in ig_patterns:
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            handle = match.group(1).strip().lstrip('@')
            if handle.lower() not in _SOCIAL_FALSE_POSITIVES and not handle.startswith('http'):
                result['instagram_handle'] = handle
                break

    for pattern in tt_patterns:
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            handle = match.group(1).strip().lstrip('@')
            if handle.lower() not in _SOCIAL_FALSE_POSITIVES and not handle.startswith('http'):
                result['tiktok_handle'] = handle
                break

    return result


def bulk_fill_social_handles(
    brand_ids: List[int],
    *,
    rate_limit_delay: float = 0.35,
    use_ai_fallback: bool = True,
) -> Dict[str, int]:
    """
    Fill missing Instagram/TikTok handles for brands that have a website.

    Prefer website scrape (fast). Optionally fall back to Claude when scrape
    finds nothing. Designed for small request batches so proxies don't time out.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("[Social Fill] ERROR: psycopg2 not installed")
        return {'processed': 0, 'updated': 0, 'skipped': 0}

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[Social Fill] ERROR: DATABASE_URL not found")
        return {'processed': 0, 'updated': 0, 'skipped': 0}

    stats = {'processed': 0, 'updated': 0, 'skipped': 0}
    conn = None

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, brand_name, website, description, category,
                   instagram_handle, tiktok_handle
            FROM pr_brands
            WHERE id = ANY(%s)
              AND website IS NOT NULL
              AND TRIM(website) <> ''
              AND (
                instagram_handle IS NULL OR TRIM(instagram_handle) = ''
                OR tiktok_handle IS NULL OR TRIM(tiktok_handle) = ''
              )
            ORDER BY id
        """, (brand_ids,))
        brands = cursor.fetchall()
        print(f"[Social Fill] Processing {len(brands)} brands missing social handles...")

        for i, brand in enumerate(brands):
            stats['processed'] += 1
            name = brand.get('brand_name') or brand['id']
            need_ig = not (brand.get('instagram_handle') or '').strip()
            need_tt = not (brand.get('tiktok_handle') or '').strip()
            if not need_ig and not need_tt:
                stats['skipped'] += 1
                continue

            scraped = scrape_social_handles_from_website(brand.get('website'))
            ig = scraped.get('instagram_handle') if need_ig else None
            tt = scraped.get('tiktok_handle') if need_tt else None

            if use_ai_fallback and ((need_ig and not ig) or (need_tt and not tt)):
                enriched = enrich_brand_with_ai(dict(brand))
                if enriched:
                    if need_ig and not ig and enriched.get('instagram_handle'):
                        handle = str(enriched['instagram_handle']).strip().lstrip('@')
                        if handle and handle.lower() not in _SOCIAL_FALSE_POSITIVES:
                            ig = handle
                    if need_tt and not tt and enriched.get('tiktok_handle'):
                        handle = str(enriched['tiktok_handle']).strip().lstrip('@')
                        if handle and handle.lower() not in _SOCIAL_FALSE_POSITIVES:
                            tt = handle

            updates = []
            values = []
            if need_ig and ig:
                updates.append("instagram_handle = %s")
                values.append(ig)
            if need_tt and tt:
                updates.append("tiktok_handle = %s")
                values.append(tt)

            if updates:
                updates.append("updated_at = NOW()")
                values.append(brand['id'])
                cursor.execute(
                    f"UPDATE pr_brands SET {', '.join(updates)} WHERE id = %s",
                    values,
                )
                stats['updated'] += 1
                print(f"[Social Fill] OK {name}: ig={ig or '-'} tt={tt or '-'}")
            else:
                stats['skipped'] += 1
                print(f"[Social Fill] - No handles found for {name}")

            if (i + 1) % 5 == 0:
                conn.commit()
            if i < len(brands) - 1:
                time.sleep(rate_limit_delay)

        conn.commit()
        print(f"[Social Fill] Done: {stats}")
    except Exception as e:
        print(f"[Social Fill] Database error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return stats


def bulk_enrich_brands(
    brand_ids: List[int],
    *,
    only_missing_fields: bool = True,
    only_missing_social: bool = False,
    rate_limit_delay: float = 1.0
) -> int:
    """
    Bulk enrich brands with AI-generated metadata.

    Args:
        brand_ids: List of pr_brands.id to enrich
        only_missing_fields: If True, only enrich brands with missing description/hero_product
        only_missing_social: If True, only brands missing Instagram AND TikTok
        rate_limit_delay: Seconds to wait between API calls

    Returns:
        Number of brands successfully enriched
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("[AI Enrich] ERROR: psycopg2 not installed")
        return 0

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[AI Enrich] ERROR: DATABASE_URL not found")
        return 0

    enriched_count = 0
    conn = None

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch brands with all fields we might enrich
        # IMPORTANT: Also fetch PR contact fields (application_form_url, contact_email) to preserve them
        if only_missing_social:
            cursor.execute("""
                SELECT id, brand_name, website, description, hero_product, target_audience, tone, price_point,
                       category, slug, cover_image_url, instagram_handle, tiktok_handle, youtube_handle,
                       min_followers, collaboration_type, seo_title, seo_description, success_stories,
                       response_rate, avg_response_time_days,
                       application_form_url, contact_email, has_application_form, logo_url
                FROM pr_brands
                WHERE id = ANY(%s)
                AND website IS NOT NULL AND TRIM(website) <> ''
                AND (instagram_handle IS NULL OR TRIM(instagram_handle) = '')
                AND (tiktok_handle IS NULL OR TRIM(tiktok_handle) = '')
            """, (brand_ids,))
        elif only_missing_fields:
            # Only enrich brands missing key fields
            cursor.execute("""
                SELECT id, brand_name, website, description, hero_product, target_audience, tone, price_point,
                       category, slug, cover_image_url, instagram_handle, tiktok_handle, youtube_handle,
                       min_followers, collaboration_type, seo_title, seo_description, success_stories,
                       response_rate, avg_response_time_days,
                       application_form_url, contact_email, has_application_form, logo_url
                FROM pr_brands
                WHERE id = ANY(%s)
                AND (
                    description IS NULL OR description = '' OR
                    hero_product IS NULL OR hero_product = '' OR
                    slug IS NULL OR slug = '' OR
                    cover_image_url IS NULL OR cover_image_url = '' OR
                    target_audience IS NULL OR target_audience = '' OR
                    tone IS NULL OR
                    instagram_handle IS NULL OR instagram_handle = '' OR
                    tiktok_handle IS NULL OR tiktok_handle = '' OR
                    seo_title IS NULL OR seo_title = ''
                )
                AND website IS NOT NULL
            """, (brand_ids,))
        else:
            # Enrich all brands
            cursor.execute("""
                SELECT id, brand_name, website, description, hero_product, target_audience, tone, price_point,
                       category, slug, cover_image_url, instagram_handle, tiktok_handle, youtube_handle,
                       min_followers, collaboration_type, seo_title, seo_description, success_stories,
                       response_rate, avg_response_time_days,
                       application_form_url, contact_email, has_application_form, logo_url
                FROM pr_brands
                WHERE id = ANY(%s)
                AND website IS NOT NULL
            """, (brand_ids,))

        brands = cursor.fetchall()
        print(f"[AI Enrich] Processing {len(brands)} brands...")

        for i, brand in enumerate(brands):
            print(f"\n[AI Enrich] {i+1}/{len(brands)}: {brand['brand_name']}...")

            # CRITICAL: NEVER modify these manually-entered PR contact fields during AI enrichment
            # These are precious data entry fields that must be preserved:
            # - application_form_url (PR Form URL)
            # - contact_email (PR Email)
            # - has_application_form (boolean flag)

            # Update brand with enriched fields (only update fields that are null/empty)
            updates = []
            values = []

            # 1. Generate unique slug if missing (no AI needed)
            if not brand.get('slug'):
                slug = generate_unique_slug(brand['brand_name'], cursor)
                updates.append("slug = %s")
                values.append(slug)
                print(f"[AI Enrich] Generated slug: {slug}")

            # 2. Logo + cover from homepage (OG/JSON-LD first, Shopify {width} normalized)
            if brand.get('website'):
                need_logo = not brand.get('logo_url')
                need_cover = is_broken_cover_url(brand.get('cover_image_url'))
                if need_logo or need_cover:
                    images = scrape_brand_images(brand['website'])
                    if need_logo and images.get('logo_url'):
                        updates.append("logo_url = %s")
                        values.append(images['logo_url'])
                        print(f"[AI Enrich] Found logo: {images['logo_url'][:80]}")
                    if need_cover and images.get('cover_image_url'):
                        updates.append("cover_image_url = %s")
                        values.append(images['cover_image_url'])
                        print(f"[AI Enrich] Found cover: {images['cover_image_url'][:80]}")

            # 3. AI enrichment for all fields (matching individual enrich endpoint)
            enriched = enrich_brand_with_ai(dict(brand))

            if enriched:
                # Core fields
                if not brand.get('description') and enriched.get('description'):
                    updates.append("description = %s")
                    values.append(enriched['description'])

                if not brand.get('hero_product') and enriched.get('hero_product'):
                    updates.append("hero_product = %s")
                    values.append(enriched['hero_product'])

                if not brand.get('target_audience') and enriched.get('target_audience'):
                    updates.append("target_audience = %s")
                    values.append(enriched['target_audience'][:255] if len(enriched['target_audience']) > 255 else enriched['target_audience'])

                # Tone - validate against allowed values
                if not brand.get('tone') and enriched.get('tone'):
                    valid_tones = ['premium', 'casual', 'wellness', 'functional', 'luxury', 'playful', 'minimalist', 'bold']
                    tone = enriched['tone'].lower().strip()
                    if tone in valid_tones:
                        updates.append("tone = %s")
                        values.append(tone)

                # Price point
                if not brand.get('price_point') and enriched.get('price_point'):
                    try:
                        price = int(enriched['price_point'])
                        if price > 0:
                            updates.append("price_point = %s")
                            values.append(price)
                    except (ValueError, TypeError):
                        pass

                # Social handles - only if missing
                if not brand.get('instagram_handle') and enriched.get('instagram_handle'):
                    handle = str(enriched['instagram_handle']).strip().lstrip('@')
                    if handle and handle.lower() not in ['null', 'none'] and len(handle) <= 100:
                        updates.append("instagram_handle = %s")
                        values.append(handle)

                if not brand.get('tiktok_handle') and enriched.get('tiktok_handle'):
                    handle = str(enriched['tiktok_handle']).strip().lstrip('@')
                    if handle and handle.lower() not in ['null', 'none'] and len(handle) <= 100:
                        updates.append("tiktok_handle = %s")
                        values.append(handle)

                if not brand.get('youtube_handle') and enriched.get('youtube_handle'):
                    handle = str(enriched['youtube_handle']).strip().lstrip('@')
                    if handle and handle.lower() not in ['null', 'none'] and len(handle) <= 100:
                        updates.append("youtube_handle = %s")
                        values.append(handle)

                # Min followers
                if not brand.get('min_followers') and enriched.get('min_followers'):
                    try:
                        min_followers = int(enriched['min_followers'])
                        if 500 <= min_followers <= 100000:
                            updates.append("min_followers = %s")
                            values.append(min_followers)
                        elif min_followers < 500:
                            updates.append("min_followers = %s")
                            values.append(1000)
                        elif min_followers > 100000:
                            updates.append("min_followers = %s")
                            values.append(50000)
                    except (ValueError, TypeError):
                        pass

                # Collaboration type
                if not brand.get('collaboration_type') and enriched.get('collaboration_type'):
                    collab = str(enriched['collaboration_type']).lower().strip()
                    if collab in ['gifted', 'paid', 'both']:
                        updates.append("collaboration_type = %s")
                        values.append(collab)

                # SEO fields
                if not brand.get('seo_title') and enriched.get('seo_title'):
                    updates.append("seo_title = %s")
                    values.append(enriched['seo_title'][:255])

                if not brand.get('seo_description') and enriched.get('seo_description'):
                    updates.append("seo_description = %s")
                    values.append(enriched['seo_description'][:500])

                # Success stories
                if not brand.get('success_stories') and enriched.get('success_stories'):
                    updates.append("success_stories = %s")
                    values.append(enriched['success_stories'][:2000])

                # Response metrics
                if not brand.get('response_rate') and enriched.get('response_rate'):
                    try:
                        rate = int(enriched['response_rate'])
                        if 5 <= rate <= 95:
                            updates.append("response_rate = %s")
                            values.append(rate)
                    except (ValueError, TypeError):
                        pass

                if not brand.get('avg_response_time_days') and enriched.get('avg_response_time_days'):
                    try:
                        days = int(enriched['avg_response_time_days'])
                        if 1 <= days <= 60:
                            updates.append("avg_response_time_days = %s")
                            values.append(days)
                    except (ValueError, TypeError):
                        pass

                # Always update category if AI provides one (to fix incorrectly categorized brands)
                if enriched.get('category'):
                    # Validate category is in allowed list
                    allowed_categories = [
                        'skincare', 'beauty', 'fashion', 'wellness', 'fitness', 'food',
                        'travel', 'tech', 'gaming', 'lifestyle', 'home', 'pet', 'baby',
                        'jewelry', 'haircare', 'sustainable', 'luxury', 'activewear',
                        'supplements', 'other'
                    ]
                    if enriched['category'] in allowed_categories:
                        updates.append("category = %s")
                        values.append(enriched['category'])
            else:
                print(f"[AI Enrich] ⚠ AI enrichment failed for {brand['brand_name']}")

            # Apply all updates (slug, cover_image_url, and/or AI-enriched fields)
            if updates:
                updates.append("updated_at = NOW()")
                values.append(brand['id'])

                update_sql = f"""
                    UPDATE pr_brands
                    SET {', '.join(updates)}
                    WHERE id = %s
                """

                cursor.execute(update_sql, values)
                enriched_count += 1
                print(f"[AI Enrich] OK Enriched {brand['brand_name']}")
            else:
                print(f"[AI Enrich] - No new fields to update for {brand['brand_name']}")

            # Commit every 10 brands
            if (i + 1) % 10 == 0:
                conn.commit()

            # Rate limiting
            if i < len(brands) - 1:
                time.sleep(rate_limit_delay)

        conn.commit()
        print(f"\n[AI Enrich] Successfully enriched {enriched_count}/{len(brands)} brands")

    except UnicodeEncodeError as e:
        print(f"[AI Enrich] Log encoding error (progress saved): {e}")
        if conn:
            conn.commit()
    except Exception as e:
        print(f"[AI Enrich] Database error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return enriched_count


def enrich_new_awin_brands(limit: int = 100) -> int:
    """
    Enrich recently imported Awin brands that are missing description/hero_product.

    Args:
        limit: Max number of brands to enrich in one run

    Returns:
        Number of brands enriched
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("[AI Enrich] ERROR: psycopg2 not installed")
        return 0

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[AI Enrich] ERROR: DATABASE_URL not found")
        return 0

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get Awin brands with missing fields
        cursor.execute("""
            SELECT id
            FROM pr_brands
            WHERE source = 'awin'
            AND status = 'draft'
            AND (description IS NULL OR description = '' OR hero_product IS NULL OR hero_product = '')
            ORDER BY id DESC
            LIMIT %s
        """, (limit,))

        brand_ids = [row['id'] for row in cursor.fetchall()]
        conn.close()

        if not brand_ids:
            print("[AI Enrich] No Awin brands need enrichment")
            return 0

        print(f"[AI Enrich] Found {len(brand_ids)} Awin brands to enrich")
        return bulk_enrich_brands(brand_ids, only_missing_fields=True)

    except Exception as e:
        print(f"[AI Enrich] Error: {e}")
        return 0


# ============================================================================
# FOLLOWER REQUIREMENTS ENRICHMENT
# ============================================================================

def enrich_follower_requirements_ai(brand: Dict) -> Optional[Dict]:
    """
    Use Claude AI to determine accurate min_followers and micro_friendly values.

    This specialized enrichment focuses ONLY on follower requirements:
    - min_followers: The minimum follower count this brand accepts for PR gifting
    - micro_friendly: Whether the brand actively works with micro-influencers (<10K)

    Args:
        brand: Brand dict with: brand_name, website, category, description, etc.

    Returns:
        Dict with {min_followers: int, micro_friendly: bool} or None if fails
    """
    if not ANTHROPIC_API_KEY:
        print("[Follower Enrich] ERROR: ANTHROPIC_API_KEY not configured")
        return None

    brand_name = brand.get('brand_name', '')
    website = brand.get('website', '')
    category = brand.get('category', '')
    description = brand.get('description', '')
    has_application_form = brand.get('has_application_form', False)
    application_form_url = brand.get('application_form_url', '')

    if not brand_name:
        return None

    # Build context from available data
    context_parts = [f"Brand: {brand_name}"]
    if website:
        context_parts.append(f"Website: {website}")
    if category:
        context_parts.append(f"Category: {category}")
    if description:
        context_parts.append(f"Description: {description}")
    if has_application_form:
        context_parts.append("Has public PR application form: Yes")
    if application_form_url:
        context_parts.append(f"Application URL: {application_form_url}")

    context = "\n".join(context_parts)

    prompt = f"""You are an expert at determining influencer requirements for brand PR programs.

{context}

Based on the brand information above, determine:

1. **min_followers**: The minimum follower count this brand likely requires for PR gifting.
   GUIDELINES:
   - Small indie/startup DTC brands (newer, niche): 500-2000 followers
   - Mid-size DTC brands (established online presence): 2000-5000 followers
   - Growing brands with some retail presence: 5000-10000 followers
   - Established mainstream brands: 10000-25000 followers
   - Major/luxury brands (Sephora-level, luxury goods): 25000-100000 followers
   - If the brand has a public application form, they're likely more open to smaller creators
   - Beauty/skincare brands tend to accept smaller creators than fashion brands
   - Brands in the "sustainable", "wellness", or "indie" space tend to be more micro-friendly

2. **micro_friendly**: Is this brand actively open to working with micro-influencers (<10K followers)?
   - TRUE if: indie brand, has public PR form, explicitly targets small creators, newer DTC brand, community-focused
   - FALSE if: luxury brand, requires professional content, high follower minimums, exclusive/premium positioning
   - When in doubt for DTC brands with application forms, lean towards TRUE

Return ONLY valid JSON:
{{"min_followers": <integer>, "micro_friendly": <true or false>}}

JSON:"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        response.raise_for_status()

        result = response.json()
        text = result.get('content', [{}])[0].get('text', '')

        # Parse JSON from response
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        enriched = json.loads(text)

        # Validate response
        min_followers = enriched.get('min_followers')
        micro_friendly = enriched.get('micro_friendly')

        if min_followers is None or micro_friendly is None:
            print(f"[Follower Enrich] Missing fields in response for {brand_name}")
            return None

        # Validate min_followers range
        try:
            min_followers = int(min_followers)
            if min_followers < 100:
                min_followers = 500
            elif min_followers > 500000:
                min_followers = 100000
        except (ValueError, TypeError):
            min_followers = 5000  # Default

        # Ensure micro_friendly is boolean
        if isinstance(micro_friendly, str):
            micro_friendly = micro_friendly.lower() in ['true', 'yes', '1']
        else:
            micro_friendly = bool(micro_friendly)

        return {
            'min_followers': min_followers,
            'micro_friendly': micro_friendly
        }

    except json.JSONDecodeError as e:
        print(f"[Follower Enrich] JSON parse error for {brand_name}: {e}")
        return None
    except requests.RequestException as e:
        print(f"[Follower Enrich] API error for {brand_name}: {e}")
        return None
    except Exception as e:
        print(f"[Follower Enrich] Unexpected error for {brand_name}: {e}")
        return None


def bulk_enrich_follower_requirements(
    *,
    limit: int = 500,
    offset: int = 0,
    only_null_values: bool = True,
    rate_limit_delay: float = 0.5,
    dry_run: bool = False
) -> Dict:
    """
    Bulk enrich min_followers and micro_friendly for all published brands.

    This task iterates through published brands and uses AI to determine
    accurate follower requirements based on brand characteristics.

    Args:
        limit: Maximum number of brands to process in one run
        offset: Number of brands to skip (for pagination)
        only_null_values: If True, only enrich brands with NULL values
        rate_limit_delay: Seconds to wait between API calls
        dry_run: If True, don't update database, just log what would happen

    Returns:
        Dict with {processed: int, updated: int, errors: int, skipped: int}
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("[Follower Enrich] ERROR: psycopg2 not installed")
        return {'processed': 0, 'updated': 0, 'errors': 0, 'skipped': 0}

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[Follower Enrich] ERROR: DATABASE_URL not found")
        return {'processed': 0, 'updated': 0, 'errors': 0, 'skipped': 0}

    stats = {'processed': 0, 'updated': 0, 'errors': 0, 'skipped': 0}

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query based on mode
        if only_null_values:
            # Only brands with NULL min_followers OR NULL micro_friendly
            cursor.execute("""
                SELECT id, brand_name, website, category, description,
                       has_application_form, application_form_url,
                       min_followers, micro_friendly, logo_url
                FROM pr_brands
                WHERE COALESCE(status, 'published') = 'published'
                  AND (min_followers IS NULL OR micro_friendly IS NULL)
                ORDER BY id ASC
                LIMIT %s OFFSET %s
            """, (limit, offset))
        else:
            # ALL published brands (re-evaluate existing values)
            cursor.execute("""
                SELECT id, brand_name, website, category, description,
                       has_application_form, application_form_url,
                       min_followers, micro_friendly, logo_url
                FROM pr_brands
                WHERE COALESCE(status, 'published') = 'published'
                ORDER BY id ASC
                LIMIT %s OFFSET %s
            """, (limit, offset))

        brands = cursor.fetchall()
        total = len(brands)
        print(f"[Follower Enrich] Found {total} brands to process...")

        if total == 0:
            conn.close()
            return stats

        for i, brand in enumerate(brands):
            stats['processed'] += 1
            brand_name = brand['brand_name']

            print(f"\n[Follower Enrich] {i+1}/{total}: {brand_name}...")

            # Skip if both fields already have values (when only_null_values=True)
            if only_null_values and brand.get('min_followers') is not None and brand.get('micro_friendly') is not None:
                print(f"[Follower Enrich] - Skipped (already has values)")
                stats['skipped'] += 1
                continue

            # Get AI enrichment
            result = enrich_follower_requirements_ai(dict(brand))

            if not result:
                print(f"[Follower Enrich] WARN Failed to enrich {brand_name}")
                stats['errors'] += 1
                time.sleep(rate_limit_delay)
                continue

            min_followers = result['min_followers']
            micro_friendly = result['micro_friendly']

            print(f"[Follower Enrich] AI result: min_followers={min_followers}, micro_friendly={micro_friendly}")

            if dry_run:
                print(f"[Follower Enrich] DRY RUN - would update {brand_name}")
                stats['updated'] += 1
            else:
                # Update database
                try:
                    cursor.execute("""
                        UPDATE pr_brands
                        SET min_followers = %s,
                            micro_friendly = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (min_followers, micro_friendly, brand['id']))
                    stats['updated'] += 1
                    print(f"[Follower Enrich] OK Updated {brand_name}")
                except Exception as db_err:
                    print(f"[Follower Enrich] DB error for {brand_name}: {db_err}")
                    stats['errors'] += 1

            # Commit every 25 brands
            if (i + 1) % 25 == 0:
                conn.commit()
                print(f"[Follower Enrich] Committed batch ({i+1}/{total})")

            # Rate limiting
            if i < total - 1:
                time.sleep(rate_limit_delay)

        conn.commit()
        conn.close()

        print(f"\n[Follower Enrich] COMPLETE:")
        print(f"  Processed: {stats['processed']}")
        print(f"  Updated: {stats['updated']}")
        print(f"  Errors: {stats['errors']}")
        print(f"  Skipped: {stats['skipped']}")

    except Exception as e:
        print(f"[Follower Enrich] Database error: {e}")
        if conn:
            conn.rollback()
            conn.close()

    return stats


def get_follower_enrichment_stats() -> Dict:
    """
    Get stats on min_followers and micro_friendly coverage.

    Returns:
        Dict with counts of null/filled values
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        return {'error': 'psycopg2 not installed'}

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return {'error': 'DATABASE_URL not found'}

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                COUNT(*) as total_published,
                SUM(CASE WHEN min_followers IS NULL THEN 1 ELSE 0 END) as min_followers_null,
                SUM(CASE WHEN min_followers IS NOT NULL THEN 1 ELSE 0 END) as min_followers_filled,
                SUM(CASE WHEN micro_friendly IS NULL THEN 1 ELSE 0 END) as micro_friendly_null,
                SUM(CASE WHEN micro_friendly IS NOT NULL THEN 1 ELSE 0 END) as micro_friendly_filled,
                SUM(CASE WHEN micro_friendly = TRUE THEN 1 ELSE 0 END) as micro_friendly_true,
                SUM(CASE WHEN micro_friendly = FALSE THEN 1 ELSE 0 END) as micro_friendly_false,
                SUM(CASE WHEN min_followers IS NULL OR micro_friendly IS NULL THEN 1 ELSE 0 END) as needs_enrichment
            FROM pr_brands
            WHERE COALESCE(status, 'published') = 'published'
        """)

        stats = cursor.fetchone()
        conn.close()

        return dict(stats) if stats else {}

    except Exception as e:
        return {'error': str(e)}
