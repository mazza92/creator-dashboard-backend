"""
Marketplace Routes - Public creator discovery for brands
"""
from flask import Blueprint, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

marketplace_bp = Blueprint('marketplace', __name__, url_prefix='/api/marketplace')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

@marketplace_bp.route('/creators', methods=['GET'])
def get_marketplace_creators():
    """
    Get public creator profiles for marketplace
    Query params:
    - niche: Filter by niche
    - country: Filter by location
    - sort: newest, engagement, followers
    - public: true (only show public profiles)
    """
    try:
        niche = request.args.get('niche')
        country = request.args.get('country')
        sort_by = request.args.get('sort', 'newest')
        public_only = request.args.get('public', 'true').lower() == 'true'

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query
        query = '''
            SELECT
                id,
                full_name,
                instagram_handle,
                instagram_followers,
                engagement_rate,
                niche,
                country,
                bio,
                profile_image_url,
                is_marketplace_visible,
                created_at
            FROM creators
            WHERE 1=1
        '''
        params = []

        # Only show profiles visible in marketplace
        if public_only:
            query += ' AND is_marketplace_visible = true'

        # Filter by niche
        if niche and niche != 'All Niches':
            query += ' AND niche = %s'
            params.append(niche)

        # Filter by country
        if country and country != 'All Locations':
            query += ' AND country = %s'
            params.append(country)

        # Sorting
        if sort_by == 'engagement':
            query += ' ORDER BY engagement_rate DESC NULLS LAST'
        elif sort_by == 'followers':
            query += ' ORDER BY instagram_followers DESC NULLS LAST'
        else:  # newest
            query += ' ORDER BY created_at DESC'

        # Limit results
        query += ' LIMIT 100'

        cursor.execute(query, params)
        creators = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'creators': creators,
            'total_available': len(creators),
            'is_limited': False
        }), 200

    except Exception as e:
        print(f"Error fetching marketplace creators: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
