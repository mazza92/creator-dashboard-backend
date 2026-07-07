Good question — this is the right problem to solve. Manual enrichment of 450+ brands is not viable. Here's the best approach.

---

## The Strategy: Automated Enrichment Script

You already have each brand's website URL in your DB. The fastest path is a one-time Python script that:

1. Visits each brand's website
2. Extracts the page content
3. Passes it to an LLM to pull out the hero product, brand tone, and target audience
4. Writes the result back to the DB

Run it once. Takes about 15 to 20 minutes for all 450 brands.

---

## The Stack

**Jina AI Reader (free, no API key)**

Converts any URL to clean markdown in one request. Handles JS-rendered pages. No scraping setup needed.

```
GET https://r.jina.ai/https://brandwebsite.com
```

Returns clean text of the page — product names, hero copy, about section. Exactly what the LLM needs.

**Claude or OpenAI for extraction**

Pass the clean markdown and ask for structured output.

---

## The Script

```python
import requests
import json
import time
from your_app import db, Brand

ANTHROPIC_API_KEY = "your_key"

def fetch_page_content(url):
    try:
        res = requests.get(f"https://r.jina.ai/{url}", timeout=10)
        return res.text[:4000]  # cap tokens
    except:
        return None

def extract_brand_data(brand_name, page_content):
    prompt = f"""
You are extracting structured data from a brand's website.

Brand: {brand_name}
Website content:
{page_content}

Extract the following as JSON:
{{
  "hero_product": "their most well-known or bestselling product, specific name (e.g. 'Peptide Glazing Fluid', 'Power Leggings', 'Extra Virgin Olive Oil'). If unclear, use their primary product category.",
  "target_audience": "who they sell to in under 10 words (e.g. 'women 25-40 into skincare and wellness')",
  "brand_tone": "one of: premium / casual / wellness / functional / luxury / playful",
  "price_point": "estimated single product price in USD as integer, e.g. 32",
  "one_line_description": "one sentence: [Brand] makes [product] for [customer]. [One differentiating detail.] Under 20 words."
}}

Return only valid JSON. No explanation.
"""

    res = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    try:
        text = res.json()["content"][0]["text"]
        return json.loads(text)
    except:
        return None

def enrich_brands():
    # Prioritise brands with most pitches first
    brands = Brand.query\
        .filter(Brand.hero_product == None)\
        .outerjoin(Pitch)\
        .group_by(Brand.id)\
        .order_by(db.func.count(Pitch.id).desc())\
        .all()

    print(f"Enriching {len(brands)} brands...")

    for i, brand in enumerate(brands):
        if not brand.website:
            print(f"[{i+1}] SKIP {brand.name} — no website")
            continue

        print(f"[{i+1}] {brand.name}")

        content = fetch_page_content(brand.website)
        if not content:
            print(f"  FAIL — could not fetch page")
            continue

        data = extract_brand_data(brand.name, content)
        if not data:
            print(f"  FAIL — could not extract data")
            continue

        brand.hero_product         = data.get("hero_product")
        brand.target_audience      = data.get("target_audience")
        brand.tone                 = data.get("brand_tone")
        brand.price_point          = data.get("price_point")
        brand.description          = data.get("one_line_description")

        db.session.commit()
        print(f"  OK — {data.get('hero_product')}")

        time.sleep(1)  # be polite to Jina

    print("Done.")

if __name__ == "__main__":
    enrich_brands()
```

---

## DB Migration First

Add the new fields to your Brand model before running:

```python
# Add to Brand model
hero_product    = db.Column(db.String(255))
target_audience = db.Column(db.String(255))
tone            = db.Column(db.String(50))
price_point     = db.Column(db.Integer)
# description already exists or add it:
description     = db.Column(db.Text)
```

```sql
ALTER TABLE brands ADD COLUMN hero_product VARCHAR(255);
ALTER TABLE brands ADD COLUMN target_audience VARCHAR(255);
ALTER TABLE brands ADD COLUMN tone VARCHAR(50);
ALTER TABLE brands ADD COLUMN price_point INTEGER;
ALTER TABLE brands ADD COLUMN description TEXT;
```

---

## Cost Estimate

| Item | Cost |
|------|------|
| Jina AI (450 fetches) | Free |
| Claude Haiku (450 extractions, ~500 tokens each) | ~$0.15 total |
| Runtime | ~15 min |

Use Haiku for bulk extraction — it is fast, cheap, and accurate enough for structured JSON from clean page content.

---

## After Enrichment: Updated Pitch Prompt

Once the fields are populated, the pitch generator uses them directly:

```python
pitch_prompt = f"""
Write a PR pitch email body under 80 words.

Creator: {creator.follower_count} followers on {creator.primary_platform},
posts about {creator.niche},
engagement rate {creator.engagement_rate}%,
audience: {creator.audience_demographic or creator.niche + ' enthusiasts'}.

Brand: {brand.name}
Their hero product: {brand.hero_product}
They target: {brand.target_audience}
Brand tone: {brand.tone}

Rules:
- Start with "Hi," only
- Line 1: reference {brand.hero_product} specifically — not just "your products"
- Line 2: creator reach in one stat with audience description
- Line 3: specific content idea featuring {brand.hero_product}
- Line 4: "Would you be open to sending [product]?"
- Sign off: first name only
- Under 80 words, no em dashes
"""
```

The difference in output quality between `brand.category = "skincare"` and `brand.hero_product = "Peptide Glazing Fluid"` is the difference between a generic pitch and one that reads like the creator actually knows the brand.

---

## Fallback for Failed Enrichment

Some brands will fail (Jina timeout, JS-heavy sites, etc.). For those, use a category-level fallback map rather than leaving the field empty:

```python
CATEGORY_HERO_FALLBACK = {
    'skincare':    'their hero skincare product',
    'fitness':     'their activewear',
    'supplements': 'their supplements',
    'food':        'their signature product',
    'beauty':      'their bestselling product',
    'pet':         'their pet care product',
    'lifestyle':   'their products',
}

hero = brand.hero_product or CATEGORY_HERO_FALLBACK.get(brand.category, 'your products')
```

This ensures every pitch has something specific even for unenriched brands — and you can re-run enrichment later for failed ones only.