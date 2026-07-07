import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brand_categories import normalize_category, aggregate_category_counts
from scripts.enrich_brands_from_csv import get_connection

load_dotenv()

conn = get_connection()
cur = conn.cursor()
cur.execute(
    """
    SELECT category, COUNT(*) as n
    FROM pr_brands
    WHERE COALESCE(status, 'published') = 'published'
      AND category IS NOT NULL
    GROUP BY category
    """
)
rows = [{'category': r['category'], 'brand_count': r['n']} for r in cur.fetchall()]
pet = next(x for x in aggregate_category_counts(rows) if x['value'] == 'pet')
print('pet dropdown:', pet)
print('normalize pets ->', normalize_category('pets'))
cur.execute(
    """
    SELECT COUNT(*) as n FROM pr_brands
    WHERE category = 'pet' AND COALESCE(status, 'published') = 'published'
    """
)
print('pet brands:', cur.fetchone()['n'])
cur.close()
conn.close()
