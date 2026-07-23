"""One-off runner for migrations/add_micro_friendly.sql (idempotent)."""
import os
import sys

from dotenv import load_dotenv
import psycopg2

load_dotenv()

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sql_path = os.path.join(root, 'migrations', 'add_micro_friendly.sql')

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print('DATABASE_URL not set')
    sys.exit(1)

with open(sql_path, encoding='utf-8') as f:
    sql = f.read()

conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute(sql)
conn.commit()

cur.execute(
    "SELECT COUNT(*) FILTER (WHERE micro_friendly = TRUE), COUNT(*) FROM pr_brands"
)
micro, total = cur.fetchone()
print(f'Migration applied: {micro}/{total} brands flagged micro-friendly by backfill')
conn.close()
