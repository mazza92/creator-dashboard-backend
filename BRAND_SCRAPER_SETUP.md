# Brand Scraper Setup Guide

This guide will help you set up the automated brand scraping system to populate your database with real, verified brand contacts.

## Overview

The brand scraper collects data from multiple sources:
- **Instagram**: Brand profiles, follower counts, bios, websites
- **Hunter.io**: Find PR/marketing contact emails
- **ZeroBounce**: Verify email deliverability
- **Clearbit**: Company data enrichment (industry, size, etc.)

## Prerequisites

### 1. API Keys Required

You'll need to sign up for these services (all have free tiers):

#### Hunter.io (Email Finder)
- Sign up: https://hunter.io/users/sign_up
- Free tier: 25 searches/month
- Paid plans: $49/month for 500 searches
- Get API key from: https://hunter.io/api_keys

#### ZeroBounce (Email Verification)
- Sign up: https://www.zerobounce.net/members/signup/
- Free tier: 100 verifications/month
- Paid plans: $16/month for 2,000 verifications
- Get API key from dashboard after signup

#### RapidAPI (Instagram Scraper)
- Sign up: https://rapidapi.com/auth/sign-up
- Subscribe to "Instagram Scraper API": https://rapidapi.com/socialapi/api/instagram-scraper-api2
- Free tier: 100 requests/month
- Paid plans: $10/month for 10,000 requests
- Get API key from: https://rapidapi.com/developer/security

#### Clearbit (Optional - Company Enrichment)
- Sign up: https://clearbit.com/
- Free tier: 50 requests/month
- Paid plans: Custom pricing
- Get API key from: https://dashboard.clearbit.com/api

### 2. Environment Setup

Create a `.env` file in the root directory:

```bash
# Database
DB_HOST=localhost
DB_NAME=creator_dashboard
DB_USER=postgres
DB_PASSWORD=your_password

# API Keys
HUNTER_API_KEY=your_hunter_api_key
ZEROBOUNCE_API_KEY=your_zerobounce_api_key
RAPIDAPI_KEY=your_rapidapi_key
CLEARBIT_API_KEY=your_clearbit_api_key  # Optional
```

### 3. Install Python Dependencies

```bash
cd scripts
pip install -r scraper_requirements.txt
```

### 4. Database Schema Update

Run the schema migration:

```bash
psql -U postgres -d creator_dashboard -f brand_scraper_schema.sql
```

Or use your database client to execute the SQL file.

## Usage

### Basic Usage

Scrape a single brand:

```python
from brand_scraper import BrandScraper

scraper = BrandScraper()

# Scrape by Instagram handle
brand_id = scraper.scrape_full_brand(instagram_handle='glossier')

# Scrape by website
brand_id = scraper.scrape_full_brand(website='https://www.glossier.com')
```

### Batch Scraping

Create a file `brands_to_scrape.txt` with one Instagram handle per line:

```
glossier
fentybeauty
kyliecosmetics
anastasiabeverlyhills
rarebeauty
```

Then run:

```python
python batch_scraper.py brands_to_scrape.txt
```

### Category-Specific Scraping

The system can target brands by category. Here are curated lists:

#### Beauty & Cosmetics
```
glossier, fentybeauty, kyliecosmetics, rarebeauty, milkmakeup,
tatcha, drunkelephant, theinkey.list, cetaphil, cerave
```

#### Fashion & Apparel
```
fashionnova, prettylittlething, boohoo, asos, revolve,
shopbop, nordstrom, zara, hm, urbanoutfitters
```

#### Fitness & Wellness
```
gymshark, fabletics, lululemon, nike, adidas, alo.yoga,
outdoorvoices, sweaty_betty, underarmour, athleta
```

#### Food & Beverage
```
celsius, redbull, liquidiv, gorgie, huel, truwomen,
perfectbar, rxbar, kindsnacks, questnutrition
```

#### Tech & Electronics
```
apple, samsung, microsoft, google, amazon, sony,
logitech, razer, corsair, hyperx
```

## Data Quality

The scraper implements several quality checks:

### Email Filtering
- ✅ Prioritizes: marketing@, pr@, partnerships@, brand@
- ❌ Filters out: admin@, info@, support@, sales@
- ✅ Prefers: Named contacts (john@) over role addresses

### Verification Steps
1. **Domain validation**: Ensures website is accessible
2. **MX record check**: Verifies email domain accepts mail
3. **SMTP validation**: Checks if mailbox exists
4. **Disposable detection**: Filters temporary email services
5. **Role-based detection**: Identifies generic addresses

### Data Enrichment
- Company size estimation
- Industry classification
- Geographic location
- Social media handles
- Logo and branding assets

## Rate Limiting

To avoid API limits and bans:

- **Instagram**: 2 second delay between requests
- **Hunter.io**: Track monthly quota (25 free)
- **ZeroBounce**: Track monthly quota (100 free)
- **Clearbit**: 1 request per second max

The scraper automatically implements delays.

## Monitoring

Check scraping logs:

```sql
SELECT * FROM scraping_logs
ORDER BY started_at DESC
LIMIT 10;
```

View email verification results:

```sql
SELECT
    b.brand_name,
    b.contact_email,
    ev.is_valid,
    ev.verification_method,
    ev.verification_date
FROM brands b
JOIN email_verifications ev ON b.id = ev.brand_id
WHERE ev.is_valid = true
ORDER BY ev.verification_date DESC;
```

## Scaling Strategy

### Phase 1: Manual Lists (Current)
- Manually curate 50-100 brands per category
- Focus on high-quality, responsive brands
- Estimated time: 1-2 weeks

### Phase 2: Competitor Analysis
- Scrape brands that work with similar creators
- Use Instagram "Sponsored" post analysis
- Estimated brands: 500-1,000

### Phase 3: Automated Discovery
- Instagram hashtag mining (#brandpartnership, #gifted)
- Creator tagged posts analysis
- Estimated brands: 5,000+

### Phase 4: Full Coverage
- Industry directories scraping
- LinkedIn company database
- Estimated brands: 50,000+

## Cost Estimation

### Free Tier (First Month)
- Hunter.io: 25 emails/month = $0
- ZeroBounce: 100 verifications/month = $0
- RapidAPI: 100 requests/month = $0
- **Total**: $0 for ~25 verified brands

### Starter Plan (100 brands/month)
- Hunter.io: 500 searches = $49/month
- ZeroBounce: 2,000 verifications = $16/month
- RapidAPI: 10,000 requests = $10/month
- **Total**: $75/month for ~500 verified brands

### Growth Plan (1,000 brands/month)
- Hunter.io: 5,000 searches = $149/month
- ZeroBounce: 10,000 verifications = $40/month
- RapidAPI: 100,000 requests = $50/month
- **Total**: $239/month for ~5,000 verified brands

## Alternative Free Methods

### 1. Instagram Profile Scraping (Free)
Use `instaloader` to scrape public Instagram data:

```python
import instaloader

L = instaloader.Instaloader()
profile = instaloader.Profile.from_username(L.context, 'glossier')

print(profile.full_name)
print(profile.biography)
print(profile.external_url)
print(profile.followers)
```

### 2. LinkedIn Sales Navigator (Manual)
- Search for brands in your niche
- Find marketing/PR contacts
- Export to CSV
- Verify emails manually

### 3. Brand Partnership Directories (Free)
Scrape these public directories:
- Aspire.io brand directory
- GRIN marketplace
- CreatorIQ brand database
- #paid brand list

### 4. Google Search + Email Patterns
For each brand:
1. Find website
2. Identify email pattern (firstname@brand.com)
3. Look up PR/marketing team on LinkedIn
4. Generate probable emails
5. Verify with free tools

## Troubleshooting

### Common Issues

**Issue**: Instagram rate limiting
**Solution**: Add longer delays (5-10 seconds), use residential proxies

**Issue**: Hunter.io quota exceeded
**Solution**: Implement queue system, prioritize high-value brands

**Issue**: Email verification fails
**Solution**: Manual verification for high-priority brands

**Issue**: No website in Instagram bio
**Solution**: Google search "[brand name] official website"

## Next Steps

1. **Run schema migration** (`brand_scraper_schema.sql`)
2. **Add API keys** to `.env` file
3. **Test with 5 brands** to verify setup
4. **Create target brand list** (50-100 brands)
5. **Run batch scraper** overnight
6. **Verify data quality** next morning
7. **Scale gradually** to avoid API limits

## Support

Need help? Common resources:
- Hunter.io docs: https://hunter.io/api-documentation
- ZeroBounce docs: https://www.zerobounce.net/docs/
- RapidAPI docs: https://docs.rapidapi.com/
- Clearbit docs: https://clearbit.com/docs

## Legal Compliance

⚠️ **Important**: Ensure compliance with:
- GDPR (EU data protection)
- CAN-SPAM Act (US email marketing)
- CCPA (California privacy)
- Instagram Terms of Service
- Fair use of public data

Only scrape publicly available information and provide opt-out mechanisms.
