"""
Sitemap Generator for NewCollab Brand Directory
Generates XML sitemap for all public brand pages
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    """Connect to Supabase PostgreSQL"""
    return psycopg2.connect(
        host=os.getenv('SUPABASE_HOST'),
        database=os.getenv('SUPABASE_DB'),
        user=os.getenv('SUPABASE_USER'),
        password=os.getenv('SUPABASE_PASSWORD'),
        port=os.getenv('SUPABASE_PORT', 5432)
    )

def generate_sitemap():
    """
    Generate sitemap XML for all brand pages
    Returns: XML string
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch all brands with slugs
        cursor.execute('''
            SELECT slug, updated_at
            FROM brands
            WHERE slug IS NOT NULL
            AND is_public = true
            ORDER BY updated_at DESC
        ''')
        brands = cursor.fetchall()

        cursor.close()
        conn.close()

        # Build sitemap XML
        sitemap_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
'''

        # Add homepage
        sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/</loc>
    <lastmod>{datetime.now().strftime('%Y-%m-%d')}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
'''

        # Add directory page
        sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/directory</loc>
    <lastmod>{datetime.now().strftime('%Y-%m-%d')}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
'''

        # Add all brand pages
        for brand in brands:
            last_mod = brand['updated_at'].strftime('%Y-%m-%d') if brand.get('updated_at') else datetime.now().strftime('%Y-%m-%d')
            sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/brand/{brand['slug']}</loc>
    <lastmod>{last_mod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
'''

        sitemap_xml += '</urlset>'

        return sitemap_xml

    except Exception as e:
        print(f"Error generating sitemap: {str(e)}")
        return None


def save_sitemap(output_path='public/sitemap.xml'):
    """
    Generate and save sitemap to file

    Args:
        output_path: Path where sitemap.xml should be saved
    """
    sitemap_xml = generate_sitemap()

    if sitemap_xml:
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(sitemap_xml)

        print(f"✅ Sitemap generated: {output_path}")
        print(f"   Total URLs: {sitemap_xml.count('<url>')}")
        return True
    else:
        print("❌ Failed to generate sitemap")
        return False


if __name__ == '__main__':
    # Generate sitemap when run directly
    save_sitemap()
