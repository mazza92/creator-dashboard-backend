"""
Portfolio Builder Routes for Creator Dashboard
API endpoints for the new media kit portfolio builder - posts CRUD, settings, public kit, views tracking
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import jwt_required, get_jwt_identity
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import re
import json
import requests
import uuid
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from supabase import create_client, Client

# Supabase credentials for file uploads
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "creators")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Allowed image extensions for thumbnails
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_image_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

portfolio_bp = Blueprint('portfolio', __name__, url_prefix='/api/portfolio')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def get_creator_id_from_session():
    """Get creator ID from session or JWT"""
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id

    try:
        user_id = get_jwt_identity()
        if user_id:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (user_id,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
    except:
        pass

    return None


def serialize_post(post):
    """Serialize a portfolio post for JSON response"""
    return {
        'id': post['id'],
        'post_url': post['post_url'],
        'platform': post['platform'],
        'post_type': post['post_type'],
        'brand_name': post['brand_name'],
        'collab_type': post['collab_type'],
        'views': post['views'],
        'likes': post['likes'],
        'comments': post['comments'],
        'shares': post['shares'],
        'thumbnail_url': post['thumbnail_url'],
        'display_order': post['display_order'],
        'is_featured': post['is_featured'],
        'created_at': post['created_at'].isoformat() if post['created_at'] else None,
    }


# ============================================
# PORTFOLIO POSTS CRUD
# ============================================

@portfolio_bp.route('/posts', methods=['GET'])
def get_portfolio_posts():
    """
    GET /api/portfolio/posts
    Returns all posts for the authenticated creator, ordered by display_order
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT * FROM portfolio_posts
            WHERE creator_id = %s
            ORDER BY display_order ASC, created_at DESC
        ''', (creator_id,))

        posts = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify([serialize_post(p) for p in posts])

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching portfolio posts: {e}")
        return jsonify({'error': 'Failed to fetch posts'}), 500


@portfolio_bp.route('/posts', methods=['POST'])
def create_portfolio_post():
    """
    POST /api/portfolio/posts
    Creates a new post entry
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Required fields
    platform = data.get('platform')
    post_type = data.get('post_type')

    if not platform or not post_type:
        return jsonify({'error': 'Platform and post_type are required'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            INSERT INTO portfolio_posts (
                creator_id, post_url, platform, post_type, brand_name,
                collab_type, views, likes, comments, shares,
                thumbnail_url, display_order, is_featured
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        ''', (
            creator_id,
            data.get('post_url'),
            platform,
            post_type,
            data.get('brand_name'),
            data.get('collab_type', 'organic'),
            int(data.get('views', 0)),
            int(data.get('likes', 0)),
            int(data.get('comments', 0)),
            int(data.get('shares', 0)),
            data.get('thumbnail_url'),
            int(data.get('display_order', 0)),
            data.get('is_featured', False),
        ))

        post = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify(serialize_post(post)), 201

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error creating portfolio post: {e}")
        return jsonify({'error': 'Failed to create post'}), 500


@portfolio_bp.route('/posts/<int:post_id>', methods=['PATCH'])
def update_portfolio_post(post_id):
    """
    PATCH /api/portfolio/posts/:id
    Updates a post entry
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check ownership
        cursor.execute('''
            SELECT * FROM portfolio_posts
            WHERE id = %s AND creator_id = %s
        ''', (post_id, creator_id))

        post = cursor.fetchone()
        if not post:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Post not found'}), 404

        # Build update query dynamically
        allowed_fields = ['brand_name', 'collab_type', 'views', 'likes', 'comments',
                          'shares', 'thumbnail_url', 'display_order', 'is_featured', 'post_type']
        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = %s")
                values.append(data[field])

        if updates:
            updates.append("updated_at = NOW()")
            values.extend([post_id, creator_id])

            cursor.execute(f'''
                UPDATE portfolio_posts
                SET {', '.join(updates)}
                WHERE id = %s AND creator_id = %s
                RETURNING *
            ''', values)

            post = cursor.fetchone()
            conn.commit()

        cursor.close()
        conn.close()

        return jsonify(serialize_post(post))

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error updating portfolio post: {e}")
        return jsonify({'error': 'Failed to update post'}), 500


@portfolio_bp.route('/posts/<int:post_id>', methods=['DELETE'])
def delete_portfolio_post(post_id):
    """
    DELETE /api/portfolio/posts/:id
    Removes a post
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM portfolio_posts
            WHERE id = %s AND creator_id = %s
        ''', (post_id, creator_id))

        deleted = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()

        if not deleted:
            return jsonify({'error': 'Post not found'}), 404

        return jsonify({'ok': True})

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error deleting portfolio post: {e}")
        return jsonify({'error': 'Failed to delete post'}), 500


# ============================================
# KIT SETTINGS
# ============================================

@portfolio_bp.route('/settings', methods=['PATCH'])
def update_kit_settings():
    """
    PATCH /api/portfolio/settings
    Updates kit tagline, rates, and publish status
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build update query dynamically
        updates = []
        values = []

        if 'kit_tagline' in data:
            updates.append("kit_tagline = %s")
            values.append(data['kit_tagline'])

        if 'rates_reel' in data:
            updates.append("rates_reel = %s")
            values.append(data['rates_reel'] if data['rates_reel'] else None)

        if 'rates_tiktok' in data:
            updates.append("rates_tiktok = %s")
            values.append(data['rates_tiktok'] if data['rates_tiktok'] else None)

        if 'rates_photo' in data:
            updates.append("rates_photo = %s")
            values.append(data['rates_photo'] if data['rates_photo'] else None)

        if 'rates_gifted' in data:
            updates.append("rates_gifted = %s")
            values.append(data['rates_gifted'])

        # Handle publish action
        if data.get('publish'):
            updates.append("kit_published = %s")
            values.append(True)
            # Track when kit was last published
            updates.append("kit_published_at = NOW()")

            # Set kit_slug to username if not already set
            cursor.execute('''
                SELECT kit_slug, username FROM creators WHERE id = %s
            ''', (creator_id,))
            creator = cursor.fetchone()

            if creator and not creator['kit_slug']:
                updates.append("kit_slug = %s")
                values.append(creator['username'])

        if updates:
            values.append(creator_id)
            cursor.execute(f'''
                UPDATE creators
                SET {', '.join(updates)}
                WHERE id = %s
            ''', values)
            conn.commit()

        cursor.close()
        conn.close()

        return jsonify({'ok': True})

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error updating kit settings: {e}")
        return jsonify({'error': 'Failed to update settings'}), 500


@portfolio_bp.route('/settings', methods=['GET'])
def get_kit_settings():
    """
    GET /api/portfolio/settings
    Returns current kit settings for the creator
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT
                kit_tagline, kit_published, kit_published_at, kit_slug,
                rates_reel, rates_tiktok, rates_photo, rates_gifted,
                username
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        return jsonify({
            'kit_tagline': creator['kit_tagline'],
            'kit_published': creator['kit_published'],
            'kit_published_at': creator['kit_published_at'].isoformat() if creator['kit_published_at'] else None,
            'kit_slug': creator['kit_slug'] or creator['username'],
            'rates_reel': creator['rates_reel'],
            'rates_tiktok': creator['rates_tiktok'],
            'rates_photo': creator['rates_photo'],
            'rates_gifted': creator['rates_gifted'],
        })

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching kit settings: {e}")
        return jsonify({'error': 'Failed to fetch settings'}), 500


# ============================================
# KIT VIEWS TRACKING
# ============================================

@portfolio_bp.route('/views', methods=['GET'])
def get_kit_views():
    """
    GET /api/portfolio/views
    Returns kit view stats for the creator
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        week_ago = datetime.now() - timedelta(days=7)

        # Get total views this week
        cursor.execute('''
            SELECT COUNT(*) as count FROM kit_views
            WHERE creator_id = %s AND viewed_at >= %s
        ''', (creator_id, week_ago))

        total_week = cursor.fetchone()['count']

        # Get recent views
        cursor.execute('''
            SELECT id, viewed_at, referrer FROM kit_views
            WHERE creator_id = %s
            ORDER BY viewed_at DESC
            LIMIT 10
        ''', (creator_id,))

        recent = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'views_this_week': total_week,
            'recent': [{
                'id': v['id'],
                'viewed_at': v['viewed_at'].isoformat() if v['viewed_at'] else None,
                'referrer': v['referrer'] or '',
            } for v in recent]
        })

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching kit views: {e}")
        return jsonify({'error': 'Failed to fetch views'}), 500


# ============================================
# URL DETECTION
# ============================================

@portfolio_bp.route('/detect-url', methods=['POST'])
def detect_url():
    """
    POST /api/portfolio/detect-url
    Detects platform and post type from a URL
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    url = data.get('url', '') if data else ''

    platform = 'instagram'
    post_type = 'post'

    if 'tiktok.com' in url:
        platform = 'tiktok'
        post_type = 'tiktok'
    elif 'youtube.com' in url or 'youtu.be' in url:
        platform = 'youtube'
        if '/shorts/' in url:
            post_type = 'short'
        else:
            post_type = 'youtube'
    elif 'instagram.com' in url:
        platform = 'instagram'
        if '/reel/' in url:
            post_type = 'reel'
        elif '/stories/' in url:
            post_type = 'story'
        else:
            post_type = 'photo'

    return jsonify({'platform': platform, 'post_type': post_type})


# ============================================
# OEMBED THUMBNAIL FETCHING
# ============================================

@portfolio_bp.route('/oembed', methods=['POST'])
def fetch_oembed():
    """
    POST /api/portfolio/oembed
    Fetches thumbnail from post URL using oEmbed
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json()
    url = data.get('url', '') if data else ''

    if not url:
        return jsonify({'error': 'URL required'}), 400

    thumbnail_url = None
    platform = 'unknown'
    post_type = 'post'

    try:
        # Detect platform and fetch oEmbed
        if 'instagram.com' in url:
            platform = 'instagram'
            post_type = 'reel' if '/reel/' in url else 'photo'
            # Instagram oEmbed requires Facebook App auth now, so we skip thumbnail
            # The frontend will show the platform icon instead
            thumbnail_url = None

        elif 'tiktok.com' in url:
            platform = 'tiktok'
            post_type = 'tiktok'
            # TikTok oEmbed
            oembed_url = f'https://www.tiktok.com/oembed?url={url}'
            resp = requests.get(oembed_url, timeout=10)
            if resp.status_code == 200:
                oembed_data = resp.json()
                thumbnail_url = oembed_data.get('thumbnail_url')

        elif 'youtube.com' in url or 'youtu.be' in url:
            platform = 'youtube'
            post_type = 'short' if '/shorts/' in url else 'youtube'
            # Extract video ID
            video_id = None
            if 'youtu.be/' in url:
                match = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', url)
                if match:
                    video_id = match.group(1)
            elif '/shorts/' in url:
                match = re.search(r'/shorts/([a-zA-Z0-9_-]+)', url)
                if match:
                    video_id = match.group(1)
            else:
                match = re.search(r'[?&]v=([a-zA-Z0-9_-]+)', url)
                if match:
                    video_id = match.group(1)

            if video_id:
                # YouTube thumbnails are predictable
                thumbnail_url = f'https://img.youtube.com/vi/{video_id}/hqdefault.jpg'

        return jsonify({
            'thumbnail_url': thumbnail_url,
            'platform': platform,
            'post_type': post_type
        })

    except Exception as e:
        print(f"Error fetching oEmbed: {e}")
        return jsonify({
            'thumbnail_url': None,
            'platform': platform,
            'post_type': post_type,
            'error': str(e)
        })


# ============================================
# THUMBNAIL UPLOAD
# ============================================

@portfolio_bp.route('/upload-thumbnail', methods=['POST'])
def upload_thumbnail():
    """
    POST /api/portfolio/upload-thumbnail
    Uploads a thumbnail image and returns its URL
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    if not supabase:
        return jsonify({'error': 'File storage not configured'}), 500

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_image_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: png, jpg, jpeg, gif, webp'}), 400

    try:
        # Generate unique filename
        unique_prefix = uuid.uuid4().hex
        filename = f"portfolio/{creator_id}/{unique_prefix}_{secure_filename(file.filename)}"

        # Read file content
        file_data = file.read()

        # Upload to Supabase
        upload_response = supabase.storage.from_(SUPABASE_BUCKET).upload(filename, file_data, {
            "content-type": file.content_type
        })

        # Check for upload errors
        if hasattr(upload_response, 'error') and upload_response.error:
            raise Exception(f"Upload error: {upload_response.error}")

        # Get public URL
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)

        return jsonify({
            'thumbnail_url': public_url,
            'ok': True
        })

    except Exception as e:
        print(f"Error uploading thumbnail: {e}")
        return jsonify({'error': f'Failed to upload: {str(e)}'}), 500


# ============================================
# PUBLIC KIT ENDPOINT (No auth required)
# ============================================

@portfolio_bp.route('/public/<slug>', methods=['GET'])
def get_public_kit(slug):
    """
    GET /api/portfolio/public/:slug
    Returns public kit data (no auth required)
    Logs the view
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Try to find creator - use basic columns first, then try kit columns
        creator = None
        has_kit_columns = True

        try:
            cursor.execute('''
                SELECT
                    id, username, username as first_name, image_profile as avatar_url,
                    COALESCE(kit_tagline, '') as tagline, niche as niches,
                    followers_count as follower_count, engagement_rate,
                    COALESCE(kit_published, false) as kit_published,
                    COALESCE(rates_reel, 0) as rates_reel,
                    COALESCE(rates_tiktok, 0) as rates_tiktok,
                    COALESCE(rates_photo, 0) as rates_photo,
                    COALESCE(rates_gifted, true) as rates_gifted,
                    COALESCE(regions, '[]') as regions,
                    COALESCE(primary_age_range, '') as primary_age_range,
                    COALESCE(subscription_tier, 'free') as subscription_tier,
                    COALESCE(social_links, '[]') as social_links
                FROM creators
                WHERE username = %s
            ''', (slug,))
            creator = cursor.fetchone()
        except Exception as col_err:
            # Kit columns don't exist, fallback to basic query
            print(f"Kit columns not found, using fallback: {col_err}")
            has_kit_columns = False
            conn.rollback()  # Reset the failed transaction
            cursor.execute('''
                SELECT
                    id, username, username as first_name, image_profile as avatar_url,
                    '' as tagline, niche as niches,
                    followers_count as follower_count, engagement_rate,
                    false as kit_published,
                    0 as rates_reel,
                    0 as rates_tiktok,
                    0 as rates_photo,
                    true as rates_gifted,
                    COALESCE(regions, '[]') as regions,
                    COALESCE(primary_age_range, '') as primary_age_range,
                    COALESCE(social_links, '[]') as social_links
                FROM creators
                WHERE username = %s
            ''', (slug,))
            creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404

        # Get portfolio posts
        posts = []
        try:
            cursor.execute('''
                SELECT * FROM portfolio_posts
                WHERE creator_id = %s
                ORDER BY display_order ASC, created_at DESC
            ''', (creator['id'],))
            posts = cursor.fetchall()
        except Exception as posts_err:
            print(f"portfolio_posts table not found or error: {posts_err}")
            conn.rollback()
            posts = []

        # Get kit views count for the past week
        kit_views = 0
        try:
            cursor.execute('''
                SELECT COUNT(*) as views FROM kit_views
                WHERE creator_id = %s
                AND viewed_at >= NOW() - INTERVAL '7 days'
            ''', (creator['id'],))
            result = cursor.fetchone()
            kit_views = result['views'] if result else 0
        except:
            pass  # kit_views table may not exist

        # Log the view (fire and forget)
        try:
            viewer_ip = request.remote_addr
            viewer_ua = request.headers.get('User-Agent', '')[:500]
            referrer = request.headers.get('Referer', '')[:500]

            cursor.execute('''
                INSERT INTO kit_views (creator_id, viewer_ip, viewer_ua, referrer)
                VALUES (%s, %s, %s, %s)
            ''', (creator['id'], viewer_ip, viewer_ua, referrer))
            conn.commit()
        except:
            pass  # Don't fail if view logging fails

        cursor.close()
        conn.close()

        # Parse niches (could be string or array)
        niches = creator['niches']
        if isinstance(niches, str):
            niches = [n.strip() for n in niches.split(',') if n.strip()]
        elif not niches:
            niches = []

        # Parse regions (stored as JSON array)
        regions = creator.get('regions', '[]')
        if isinstance(regions, str):
            try:
                regions = json.loads(regions)
            except:
                regions = []
        if not regions:
            regions = []

        # Check if creator is Pro
        tier = creator.get('subscription_tier', 'free') or 'free'
        is_pro = tier in ('pro', 'elite')

        # Parse social links from JSON array
        socials = {}
        social_links_raw = creator.get('social_links', '[]')
        if isinstance(social_links_raw, str):
            try:
                social_links_list = json.loads(social_links_raw)
            except:
                social_links_list = []
        else:
            social_links_list = social_links_raw or []

        for link in social_links_list:
            platform = (link.get('platform') or '').lower()
            url = link.get('url')
            if platform and url:
                # Normalize platform names
                if platform in ('instagram', 'ig'):
                    socials['instagram'] = url
                elif platform in ('tiktok', 'tik tok'):
                    socials['tiktok'] = url
                elif platform in ('youtube', 'yt'):
                    socials['youtube'] = url
                elif platform == 'linkedin':
                    socials['linkedin'] = url
                elif platform in ('twitter', 'x'):
                    socials['twitter'] = url

        return jsonify({
            'username': creator['username'],
            'first_name': creator['first_name'],
            'avatar_url': creator['avatar_url'],
            'tagline': creator['tagline'],
            'niches': niches,
            'follower_count': creator['follower_count'],
            'engagement_rate': float(creator['engagement_rate']) if creator.get('engagement_rate') else 0,
            'regions': regions,
            'primary_age_range': creator.get('primary_age_range', ''),
            'rates_reel': creator['rates_reel'],
            'rates_tiktok': creator['rates_tiktok'],
            'rates_photo': creator['rates_photo'],
            'rates_gifted': creator['rates_gifted'],
            'kit_views': kit_views if is_pro else None,  # Only show views for Pro creators
            'is_pro': is_pro,
            'socials': socials,
            'posts': [serialize_post(p) for p in posts],
        })

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching public kit: {e}")
        return jsonify({'error': 'Failed to fetch kit'}), 500
