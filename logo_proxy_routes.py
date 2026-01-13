"""
Logo Proxy Routes - Server-side logo fetching
Solves CORS and DNS issues by proxying logo requests through our backend
"""

from flask import Blueprint, send_file, abort
import requests
from io import BytesIO
from urllib.parse import urlparse

logo_proxy = Blueprint('logo_proxy', __name__)

@logo_proxy.route('/api/logo-proxy/<path:url>', methods=['GET'])
def proxy_logo(url):
    """
    Proxy logo requests through our server to avoid CORS issues

    Usage: /api/logo-proxy/https://logo.clearbit.com/example.com
    """
    try:
        # Security: Only allow specific logo services
        allowed_domains = [
            'logo.clearbit.com',
            'logo.uplead.com',
            'img.logo.dev',
            'unavatar.io'
        ]

        parsed = urlparse(url)
        if parsed.netloc not in allowed_domains:
            abort(403, description="Domain not allowed")

        # Fetch the logo
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=5, stream=True)

        if response.status_code != 200:
            abort(404, description="Logo not found")

        # Get content type
        content_type = response.headers.get('Content-Type', 'image/png')

        # Stream the image
        img_io = BytesIO(response.content)
        img_io.seek(0)

        return send_file(img_io, mimetype=content_type, max_age=86400)  # Cache for 1 day

    except requests.RequestException:
        abort(404, description="Failed to fetch logo")
    except Exception as e:
        print(f"Logo proxy error: {str(e)}")
        abort(500, description="Internal error")


@logo_proxy.route('/api/brand-logo/<brand_id>', methods=['GET'])
def get_brand_logo(brand_id):
    """
    Get logo for a specific brand by ID
    Tries multiple sources in order:
    1. Clearbit
    2. Logo.dev
    3. Unavatar (falls back to initial)
    """
    try:
        from app import get_db_connection
        from psycopg2.extras import RealDictCursor

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "SELECT website, brand_name FROM pr_brands WHERE id = %s",
            (brand_id,)
        )
        brand = cursor.fetchone()
        cursor.close()
        conn.close()

        if not brand or not brand['website']:
            abort(404, description="Brand not found or no website")

        # Extract domain
        website = brand['website']
        if not website.startswith('http'):
            website = f'https://{website}'

        parsed = urlparse(website)
        domain = parsed.netloc.replace('www.', '')

        # Try Clearbit first
        logo_urls = [
            f'https://logo.clearbit.com/{domain}',
            f'https://img.logo.dev/{domain}?token=pk_X-HmAbFVSiG2s0wH0OtqCw',  # Free tier
            f'https://unavatar.io/{domain}'
        ]

        for logo_url in logo_urls:
            try:
                response = requests.get(logo_url, timeout=3, stream=True)
                if response.status_code == 200:
                    content_type = response.headers.get('Content-Type', 'image/png')
                    img_io = BytesIO(response.content)
                    img_io.seek(0)
                    return send_file(img_io, mimetype=content_type, max_age=86400)
            except:
                continue

        # If all fail, return 404
        abort(404, description="No logo found")

    except Exception as e:
        print(f"Brand logo error: {str(e)}")
        abort(500, description="Internal error")
