"""
Brand Enrichment Script
=======================
Extracts hero_product, target_audience, tone, and price_point from brand websites
using Jina AI Reader + Claude Haiku for intelligent extraction.

Usage:
    python scripts/enrich_brand_data.py              # Enrich all unenriched brands
    python scripts/enrich_brand_data.py --limit 50   # Enrich 50 brands max
    python scripts/enrich_brand_data.py --brand-id 123  # Enrich specific brand
    python scripts/enrich_brand_data.py --retry-failed  # Retry previously failed brands

Cost estimate: ~$0.15 for 450 brands (Claude Haiku)
Runtime: ~15-20 minutes for all brands
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import requests
import json
import time
import argparse
from datetime import datetime, timezone

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
JINA_TIMEOUT = 15  # seconds
CLAUDE_TIMEOUT = 30  # seconds
RATE_LIMIT_DELAY = 1  # seconds between requests


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )


def fetch_page_content(url):
    """
    Fetch clean markdown content from URL using Jina AI Reader.
    Free, no API key required, handles JS-rendered pages.
    """
    if not url:
        return None

    # Ensure URL has protocol
    if not url.startswith('http'):
        url = f'https://{url}'

    try:
        jina_url = f"https://r.jina.ai/{url}"
        res = requests.get(jina_url, timeout=JINA_TIMEOUT, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; BrandEnricher/1.0)'
        })

        if res.status_code == 200:
            # Cap content to avoid token limits (roughly 4000 chars = ~1000 tokens)
            return res.text[:6000]
        else:
            print(f"    Jina returned status {res.status_code}")
            return None
    except requests.Timeout:
        print(f"    Jina timeout")
        return None
    except Exception as e:
        print(f"    Jina error: {e}")
        return None


def extract_brand_data(brand_name, page_content, category=None):
    """
    Use Claude Haiku to extract structured brand data from page content.
    Returns dict with hero_product, target_audience, tone, price_point, description.
    """
    if not ANTHROPIC_API_KEY:
        print("    ERROR: ANTHROPIC_API_KEY not set")
        return None

    if not page_content:
        return None

    category_hint = f"\nBrand category hint: {category}" if category else ""

    prompt = f"""You are extracting structured data from a brand's website content.

Brand: {brand_name}{category_hint}

Website content:
{page_content}

Extract the following as JSON:
{{
  "hero_product": "their most well-known or bestselling product with SPECIFIC name (e.g. 'Peptide Glazing Fluid', 'Power Leggings', 'Grass-Fed Whey Protein'). If unclear, use their primary product category with a descriptor (e.g. 'vitamin C serums' not just 'skincare').",
  "target_audience": "who they sell to in under 12 words (e.g. 'women 25-40 into clean beauty and skincare', 'fitness enthusiasts 18-35')",
  "tone": "one of: premium / casual / wellness / functional / luxury / playful / minimalist / bold",
  "price_point": "estimated average single product price in USD as integer (e.g. 32, 85, 150). Use 0 if truly unclear.",
  "description": "one sentence max 25 words: [Brand] makes [specific product type] for [specific customer]. Include one differentiating detail."
}}

Rules:
- Be SPECIFIC with hero_product - never use generic terms like "skincare" or "supplements" alone
- For target_audience, include age range if evident from content
- Return ONLY valid JSON, no explanation or markdown

JSON:"""

    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=CLAUDE_TIMEOUT
        )

        if res.status_code != 200:
            print(f"    Claude API error: {res.status_code} - {res.text[:200]}")
            return None

        response_data = res.json()
        text = response_data.get("content", [{}])[0].get("text", "")

        # Clean up response - remove markdown code blocks if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        return None
    except requests.Timeout:
        print(f"    Claude timeout")
        return None
    except Exception as e:
        print(f"    Claude error: {e}")
        return None


def enrich_brands(limit=None, brand_id=None, retry_failed=False):
    """
    Main enrichment function. Fetches brands from DB, enriches them, and updates.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Build query based on options
    if brand_id:
        cursor.execute(
            'SELECT id, brand_name, website, category FROM pr_brands WHERE id = %s',
            (brand_id,)
        )
    elif retry_failed:
        # Brands with website but no hero_product after attempted enrichment (published only)
        cursor.execute('''
            SELECT id, brand_name, website, category
            FROM pr_brands
            WHERE website IS NOT NULL
              AND website != ''
              AND hero_product IS NULL
              AND enriched_at IS NOT NULL
              AND COALESCE(status, 'published') = 'published'
            ORDER BY id
        ''')
    else:
        # Unenriched published brands with websites, prioritized by pitch count
        cursor.execute('''
            SELECT pb.id, pb.brand_name, pb.website, pb.category,
                   COUNT(cp.id) as pitch_count
            FROM pr_brands pb
            LEFT JOIN creator_pipeline cp ON pb.id = cp.brand_id AND cp.stage != 'saved'
            WHERE pb.website IS NOT NULL
              AND pb.website != ''
              AND pb.hero_product IS NULL
              AND pb.enriched_at IS NULL
              AND COALESCE(pb.status, 'published') = 'published'
            GROUP BY pb.id
            ORDER BY pitch_count DESC, pb.id
        ''')

    brands = cursor.fetchall()
    total = len(brands)

    if limit:
        brands = brands[:limit]

    print(f"\n{'='*60}")
    print(f"Brand Enrichment Script")
    print(f"{'='*60}")
    print(f"Found {total} brands to enrich" + (f", processing {limit}" if limit else ""))
    print(f"{'='*60}\n")

    success_count = 0
    fail_count = 0

    for i, brand in enumerate(brands, 1):
        brand_id = brand['id']
        brand_name = brand['brand_name']
        website = brand['website']
        category = brand.get('category')

        print(f"[{i}/{len(brands)}] {brand_name}")
        print(f"    URL: {website}")

        # Fetch page content
        content = fetch_page_content(website)
        if not content:
            print(f"    SKIP - could not fetch page content")
            # Mark as attempted so we don't retry endlessly
            cursor.execute(
                'UPDATE pr_brands SET enriched_at = %s WHERE id = %s',
                (datetime.now(timezone.utc), brand_id)
            )
            conn.commit()
            fail_count += 1
            time.sleep(RATE_LIMIT_DELAY)
            continue

        # Extract data with AI
        data = extract_brand_data(brand_name, content, category)
        if not data:
            print(f"    SKIP - could not extract data")
            cursor.execute(
                'UPDATE pr_brands SET enriched_at = %s WHERE id = %s',
                (datetime.now(timezone.utc), brand_id)
            )
            conn.commit()
            fail_count += 1
            time.sleep(RATE_LIMIT_DELAY)
            continue

        # Update database
        hero_product = data.get('hero_product')
        target_audience = data.get('target_audience')
        tone_raw = data.get('tone')
        price_point_raw = data.get('price_point')
        description = data.get('description')

        # Validate/normalize tone (must be one of valid options, max 50 chars)
        valid_tones = ['premium', 'casual', 'wellness', 'functional', 'luxury', 'playful', 'minimalist', 'bold']
        tone = None
        if tone_raw:
            tone_lower = tone_raw.lower().strip()
            # Check if it matches a valid tone
            for valid in valid_tones:
                if valid in tone_lower:
                    tone = valid
                    break
            # If no match, truncate to 50 chars
            if not tone:
                tone = tone_raw[:50] if len(tone_raw) > 50 else tone_raw

        # Truncate other fields to avoid DB errors
        if hero_product and len(hero_product) > 255:
            hero_product = hero_product[:255]
        if target_audience and len(target_audience) > 255:
            target_audience = target_audience[:255]

        # Convert price_point to int (AI may return string or int)
        price_point = None
        if price_point_raw:
            try:
                price_point = int(price_point_raw)
                if price_point <= 0:
                    price_point = None
            except (ValueError, TypeError):
                price_point = None

        cursor.execute('''
            UPDATE pr_brands
            SET hero_product = %s,
                target_audience = %s,
                tone = %s,
                price_point = %s,
                description = COALESCE(description, %s),
                enriched_at = %s
            WHERE id = %s
        ''', (
            hero_product,
            target_audience,
            tone,
            price_point,
            description,  # Only update if description is null
            datetime.now(timezone.utc),
            brand_id
        ))
        conn.commit()

        print(f"    OK - {hero_product}")
        print(f"         Audience: {target_audience}")
        print(f"         Tone: {tone}, Price: ${price_point or '?'}")
        success_count += 1

        time.sleep(RATE_LIMIT_DELAY)

    cursor.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"COMPLETE")
    print(f"{'='*60}")
    print(f"Success: {success_count}")
    print(f"Failed:  {fail_count}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Enrich brand data from websites')
    parser.add_argument('--limit', type=int, help='Max number of brands to process')
    parser.add_argument('--brand-id', type=int, help='Enrich specific brand by ID')
    parser.add_argument('--retry-failed', action='store_true', help='Retry previously failed brands')

    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        print("Set it with: export ANTHROPIC_API_KEY='your-key'")
        exit(1)

    enrich_brands(
        limit=args.limit,
        brand_id=args.brand_id,
        retry_failed=args.retry_failed
    )
