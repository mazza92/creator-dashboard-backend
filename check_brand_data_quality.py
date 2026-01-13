"""Check data quality of scraped brands"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)

cur = conn.cursor()

# Get recent 20 brands
cur.execute("""
    SELECT
        brand_name,
        description,
        logo_url,
        contact_email,
        instagram_handle,
        category
    FROM pr_brands
    ORDER BY id DESC
    LIMIT 20
""")

results = cur.fetchall()

print("\nDATA QUALITY CHECK - Recent 20 Brands")
print("=" * 100)

missing_desc = 0
missing_logo = 0
missing_email = 0
poor_desc = 0

for r in results:
    brand_name = r[0]
    description = r[1] if r[1] else ''
    logo = r[2] if r[2] else ''
    email = r[3] if r[3] else ''
    instagram = r[4] if r[4] else ''
    category = r[5] if r[5] else ''

    # Check for issues
    has_logo = 'YES' if logo else 'NO '
    has_email = 'YES' if email else 'NO '

    # Check if description is just the brand name or too short
    desc_quality = 'GOOD'
    if not description:
        desc_quality = 'MISSING'
        missing_desc += 1
    elif description.lower() == brand_name.lower() or len(description) < 20:
        desc_quality = 'POOR'
        poor_desc += 1

    desc_preview = description[:40] + '...' if len(description) > 40 else description

    if not logo:
        missing_logo += 1
    if not email:
        missing_email += 1

    print(f"{brand_name[:20]:20s} | Desc: {desc_quality:7s} | Logo: {has_logo} | Email: {has_email} | IG: {instagram[:20]:20s}")
    if desc_quality in ['MISSING', 'POOR']:
        print(f"  -> Description: '{desc_preview}'")

print("\n" + "=" * 100)
print("SUMMARY:")
print(f"  Missing/Poor Descriptions: {missing_desc + poor_desc}/{len(results)} ({((missing_desc + poor_desc)/len(results))*100:.1f}%)")
print(f"  Missing Logos: {missing_logo}/{len(results)} ({(missing_logo/len(results))*100:.1f}%)")
print(f"  Missing Emails: {missing_email}/{len(results)} ({(missing_email/len(results))*100:.1f}%)")

# Overall data quality
total_issues = missing_desc + poor_desc + missing_logo + missing_email
max_possible = len(results) * 3  # 3 fields checked
quality_score = ((max_possible - total_issues) / max_possible) * 100

print(f"\nOverall Data Quality: {quality_score:.1f}%")

if quality_score < 70:
    print("\n[WARNING] Data quality is too low for production!")
    print("Need to improve scraper to extract better descriptions and contact info")
else:
    print("\n[OK] Data quality is acceptable")

cur.close()
conn.close()
