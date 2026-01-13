# Brand Logo Fetcher

This script automatically fetches and updates brand logos for the `pr_brands` table.

## Features

- **Automatic logo fetching** from multiple sources:
  1. **Clearbit Logo API** (primary, best quality)
  2. **Google Favicon API** (fallback)
  3. **DuckDuckGo Icon API** (fallback)

- **Smart fallbacks**: Frontend automatically uses Clearbit API or UI Avatars if logo is missing
- **Rate limiting**: Built-in delays to be respectful to external APIs
- **Batch processing**: Can process all brands or a specific number

## Usage

### 1. List brands without logos

```bash
python scripts/fetch_brand_logos.py list
```

This will show you all brands that are missing logo URLs.

### 2. Update logos for specific number of brands

```bash
python scripts/fetch_brand_logos.py 10
```

This will fetch logos for the first 10 brands without logos.

### 3. Update all brands

```bash
python scripts/fetch_brand_logos.py
```

This will process all brands that are missing logo URLs.

## How It Works

### Backend (Python Script)

1. Queries database for brands without `logo_url` but with a `website`
2. Extracts domain from website URL
3. Tries multiple logo APIs in order:
   - Clearbit (high quality, free, no API key needed)
   - Google Favicon (smaller, but reliable)
   - DuckDuckGo Icon (fallback)
4. Updates database with the first successful logo URL

### Frontend (React)

The `getBrandLogoUrl()` utility function in both `PRBrandDiscovery.js` and `PRPipeline.js`:

1. **First**: Uses `logo_url` from database if available
2. **Second**: Falls back to Clearbit API using brand's website domain
3. **Third**: Uses UI Avatars to generate a branded placeholder with the brand's first letter

### Example

```javascript
// Frontend automatically handles missing logos:
const logoUrl = getBrandLogoUrl(brand);
// Returns: brand.logo_url || Clearbit API URL || UI Avatars placeholder
```

## Requirements

- Python 3.x
- `psycopg2` (PostgreSQL adapter)
- `requests` (HTTP library)
- Database connection configured via environment variables

## Environment Variables

The script uses the following database connection variables:

- `DB_HOST`
- `DB_PORT` (default: 5432)
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

## Logo Sources

### Clearbit Logo API
- **URL**: `https://logo.clearbit.com/{domain}`
- **Quality**: High (SVG when available)
- **Free**: Yes, no API key required
- **Example**: `https://logo.clearbit.com/asos.com`

### UI Avatars (Frontend Fallback)
- **URL**: `https://ui-avatars.com/api/?name={brandName}&size=128&background=3B82F6&color=fff&bold=true`
- **Quality**: Generated placeholder
- **Free**: Yes
- **Example**: `https://ui-avatars.com/api/?name=ASOS&size=128&background=3B82F6&color=fff&bold=true`

## Best Practices

1. **Run regularly**: Execute after adding new brands to keep logos up to date
2. **Monitor success rate**: Check the output to see how many logos were successfully fetched
3. **Manual review**: Some brands might need manual logo URL entry if automatic fetch fails
4. **Rate limiting**: The script includes built-in delays (0.5s between requests)

## Troubleshooting

### No logos found
- Check if brands have valid `website` URLs in the database
- Verify the domain is accessible and not behind a firewall
- Some brands might not be in Clearbit's database

### Database connection error
- Verify environment variables are set correctly
- Check database credentials and network connectivity

### Frontend shows placeholders
- Check browser console for image loading errors
- Verify the logo URL is accessible from the browser
- CORS issues might prevent loading from some domains
