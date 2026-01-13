#!/usr/bin/env python3
"""
Run the brand scraper but force it to use LOCAL database
Usage: python scrape_to_local.py <category>
"""
import os
import sys
import subprocess

# Force local database environment variables
local_env = os.environ.copy()
local_env['DB_HOST'] = 'localhost'
local_env['DB_NAME'] = 'creator_db'
local_env['DB_USER'] = 'postgres'
local_env['DB_PASSWORD'] = 'Mahermaz1'

print("=" * 60)
print("🏠 FORCING LOCAL DATABASE CONNECTION")
print("=" * 60)
print(f"DB_HOST: {local_env['DB_HOST']}")
print(f"DB_NAME: {local_env['DB_NAME']}")
print(f"DB_USER: {local_env['DB_USER']}")
print("=" * 60)
print()

if len(sys.argv) < 2:
    print("❌ Error: No category specified")
    print("\nUsage: python scrape_to_local.py <category>")
    print("\nExamples:")
    print("  python scrape_to_local.py beauty")
    print("  python scrape_to_local.py fashion")
    print("  python scrape_to_local.py tech")
    sys.exit(1)

category = sys.argv[1]
print(f"🔍 Scraping category: {category}")
print()

# Run the scraper with local database env vars
try:
    result = subprocess.run(
        ['python', 'scripts/free_brand_scraper.py', category],
        env=local_env,
        check=True
    )

    print()
    print("=" * 60)
    print("✅ Scraping complete! Brands saved to LOCAL database.")
    print("=" * 60)
    print("\nVerify with: python check_brands.py")

except subprocess.CalledProcessError as e:
    print()
    print("=" * 60)
    print(f"❌ Scraping failed with exit code: {e.returncode}")
    print("=" * 60)
    sys.exit(1)
except KeyboardInterrupt:
    print()
    print("=" * 60)
    print("⚠️  Scraping interrupted by user")
    print("=" * 60)
    sys.exit(1)
