"""Audit distinct category values in pr_brands."""
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.enrich_brands_from_csv import get_connection

load_dotenv()

conn = get_connection()
cur = conn.cursor()
cur.execute(
    """
    SELECT category, COUNT(*) as cnt
    FROM pr_brands
    WHERE category IS NOT NULL AND TRIM(category) != ''
    GROUP BY category
    ORDER BY cnt DESC
    """
)
for r in cur.fetchall():
    print(f"{r['cnt']:4d}  {r['category']!r}")
cur.close()
conn.close()
