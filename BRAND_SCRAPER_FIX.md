# Brand Scraper Database Issue - SOLVED

## Problem

You scraped 22 brands successfully, but they're not showing up in your local database.

## Root Cause

**The scraper saved brands to your REMOTE Supabase database instead of LOCAL database.**

Your `.env` file contains:
```bash
DB_HOST=aws-0-eu-west-3.pooler.supabase.com  # Remote Supabase
DB_NAME=postgres
DB_USER=postgres.kyawgtojxoglvlhzsotm
```

The scraper reads these environment variables and connected to Supabase, not your local `creator_db`.

---

## Solution

### Option 1: Quick Fix - Run Scraper with Local DB (Recommended)

Use the `scrape_to_local.py` script I created:

```bash
python scrape_to_local.py beauty
```

This forces the scraper to use your local database:
- DB_HOST: localhost
- DB_NAME: creator_db
- DB_USER: postgres
- DB_PASSWORD: Mahermaz1

### Option 2: Modify the Scraper Directly

Edit `scripts/free_brand_scraper.py` line 33-38:

**Change from:**
```python
def _get_db_connection(self):
    """Connect to PostgreSQL database"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        database=os.getenv('DB_NAME', 'creator_dashboard'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD')
    )
```

**To:**
```python
def _get_db_connection(self):
    """Connect to LOCAL PostgreSQL database"""
    # Force local database for development
    return psycopg2.connect(
        host='localhost',
        database='creator_db',
        user='postgres',
        password='Mahermaz1'
    )
```

Then run scraper normally:
```bash
python scripts/free_brand_scraper.py beauty
```

---

## Verification

After running the scraper with local database, check Flask app:

1. Start Flask server: `python app.py`
2. Go to Discovery page in browser
3. You should see the 22 brands appear

Or check the database directly through Flask's connection (which uses local DB).

---

## Why This Happened

Your Flask app (`app.py`) uses local database connection:
```python
# app.py uses local database
DATABASE_URL = "postgresql://postgres:Mahermaz1@localhost/creator_db"
```

But the scraper reads from `.env` file which has Supabase credentials for production deployment.

---

## Long-term Solution

Create two environment files:

**`.env.production`** (for Vercel/Supabase):
```bash
DB_HOST=aws-0-eu-west-3.pooler.supabase.com
DB_NAME=postgres
DB_USER=postgres.kyawgtojxoglvlhzsotm
DB_PASSWORD=<supabase-password>
```

**`.env.local`** (for local development):
```bash
DB_HOST=localhost
DB_NAME=creator_db
DB_USER=postgres
DB_PASSWORD=Mahermaz1
```

Modify scraper to prefer `.env.local`:
```python
from dotenv import load_dotenv
import os

# Load .env.local if it exists, otherwise .env
if os.path.exists('.env.local'):
    load_dotenv('.env.local')
else:
    load_dotenv()
```

---

## Summary

✅ **Brands were successfully scraped** (22 brands)
❌ **Saved to wrong database** (Supabase instead of local)
🔧 **Quick fix**: Use `scrape_to_local.py`
📝 **Permanent fix**: Modify `_get_db_connection()` to use local DB

The scraper works perfectly - it just needs to be pointed to the right database!
