# Scraper Database Issue - Brands Saved to Wrong Database

## 🔴 Problem Identified

The scraper successfully scraped 22 brands, but they were saved to the **remote Supabase database** instead of your **local database**.

---

## Root Cause

### Environment Variables
Your `.env` file contains:
```bash
DB_HOST=aws-0-eu-west-3.pooler.supabase.com
DB_NAME=postgres
DB_USER=postgres.kyawgtojxoglvlhzsotm
DB_PASSWORD=<supabase-password>
```

### Scraper Configuration
The scraper uses these environment variables:
```python
return psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    database=os.getenv('DB_NAME', 'creator_dashboard'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD')
)
```

**Result**: The scraper connected to Supabase and saved 22 brands there, not to your local `creator_db`.

---

## 🔍 Verification

To check if brands are in Supabase:
1. Log into Supabase dashboard
2. Go to Table Editor
3. Check `pr_brands` table
4. You should see 22 brands there

---

## ✅ Solutions

### Option 1: Use Local Database for Scraper (Recommended for Development)

Create a separate `.env.local` file for local development:

**`.env.local`** (for scraper and local testing):
```bash
DB_HOST=localhost
DB_NAME=creator_db
DB_USER=postgres
DB_PASSWORD=Mahermaz1
```

**Run scraper with local env**:
```bash
# Load local env vars first
export $(cat .env.local | xargs)
python scripts/free_brand_scraper.py beauty
```

Or modify the scraper to explicitly use local database:
```python
# In free_brand_scraper.py, replace _get_db_connection:
def _get_db_connection(self):
    """Connect to LOCAL PostgreSQL database"""
    return psycopg2.connect(
        host='localhost',
        database='creator_db',
        user='postgres',
        password='Mahermaz1'  # Or use local env var
    )
```

---

### Option 2: Copy Brands from Supabase to Local

Create a script to copy brands from Supabase to local:

```python
# copy_brands_from_supabase.py
import psycopg2
from psycopg2.extras import RealDictCursor

# Supabase (remote) connection
supabase_conn = psycopg2.connect(
    host='aws-0-eu-west-3.pooler.supabase.com',
    database='postgres',
    user='postgres.kyawgtojxoglvlhzsotm',
    password=os.getenv('DB_PASSWORD')
)

# Local connection
local_conn = psycopg2.connect(
    host='localhost',
    database='creator_db',
    user='postgres',
    password='Mahermaz1'
)

# Copy brands
# ...
```

---

### Option 3: Re-run Scraper with Correct Database

1. **Temporarily disable Supabase env vars**:
```bash
# Rename .env to .env.production
mv .env .env.production

# Create .env with local settings
echo "DB_HOST=localhost" > .env
echo "DB_NAME=creator_db" >> .env
echo "DB_USER=postgres" >> .env
echo "DB_PASSWORD=Mahermaz1" >> .env
```

2. **Re-run scraper**:
```bash
python scripts/free_brand_scraper.py beauty
```

3. **Restore production .env**:
```bash
mv .env.production .env
```

---

## 🎯 Recommended Approach

For development:

1. **Create `.env.local`** for local database
2. **Keep `.env`** for production/Supabase
3. **Modify scraper** to use `.env.local` when it exists:

```python
from dotenv import load_dotenv
import os

# Try to load .env.local first (for development)
if os.path.exists('.env.local'):
    load_dotenv('.env.local')
    print("[INFO] Using .env.local (local database)")
else:
    load_dotenv()
    print("[INFO] Using .env (production database)")
```

---

## 🔧 Quick Fix Script

Create `use_local_db.py`:
```python
#!/usr/bin/env python3
"""Temporarily set env vars to use local database"""
import os
import sys
import subprocess

# Set local database env vars
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_NAME'] = 'creator_db'
os.environ['DB_USER'] = 'postgres'
os.environ['DB_PASSWORD'] = 'Mahermaz1'

# Run the scraper with these env vars
if len(sys.argv) > 1:
    category = sys.argv[1]
    subprocess.run(['python', 'scripts/free_brand_scraper.py', category])
else:
    print("Usage: python use_local_db.py <category>")
```

**Usage**:
```bash
python use_local_db.py beauty
```

---

## ✅ Verification After Fix

After running scraper with local database, verify:

```bash
python check_brands.py
```

You should see:
- Total brands: 22 (or more)
- Recent brands listed
- All with proper data

---

## 📝 Summary

**Issue**: Scraper connected to Supabase instead of local database
**Cause**: `.env` has remote database credentials
**Impact**: 22 brands saved to Supabase, not visible locally
**Solution**: Create `.env.local` for local development or modify scraper to use local DB

The brands **were successfully scraped**, just saved to the wrong database!
