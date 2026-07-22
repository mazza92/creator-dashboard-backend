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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import threading
from werkzeug.utils import secure_filename
from supabase import create_client, Client
from jinja2 import Environment, FileSystemLoader

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


from media_proxy_routes import to_proxied_media_url

FREE_POST_LIMIT = 3


def _is_pro_tier(tier) -> bool:
    return (tier or "free").lower() in ("pro", "elite")


def enforce_free_kit_limits(cursor, creator_id: int, *, commit_conn=None) -> dict:
    """
    Free kits: max 3 posts (keep highest engagement).
    Does NOT gate publish — My Kit publish stays free; Pro is kit views / more posts / PR-Ready artifacts.
    """
    cursor.execute(
        "SELECT subscription_tier FROM creators WHERE id = %s",
        (creator_id,),
    )
    creator = cursor.fetchone() or {}
    if _is_pro_tier(creator.get("subscription_tier")):
        return {"trimmed": 0, "unpublished": False}

    changed = False
    trimmed = 0

    cursor.execute(
        """
        SELECT id,
               COALESCE(views,0) AS views, COALESCE(likes,0) AS likes,
               COALESCE(comments,0) AS comments, COALESCE(shares,0) AS shares,
               COALESCE(saves,0) AS saves
        FROM portfolio_posts WHERE creator_id = %s
        """,
        (creator_id,),
    )
    rows = list(cursor.fetchall())
    if len(rows) > FREE_POST_LIMIT:
        ranked = sorted(
            rows,
            key=lambda r: (
                int(r["views"] or 0)
                + int(r["likes"] or 0)
                + int(r["comments"] or 0) * 5
                + int(r["shares"] or 0) * 3
                + int(r["saves"] or 0) * 3
            ),
            reverse=True,
        )
        keep_ids = {r["id"] for r in ranked[:FREE_POST_LIMIT]}
        drop_ids = [r["id"] for r in ranked if r["id"] not in keep_ids]
        if drop_ids:
            cursor.execute(
                "DELETE FROM portfolio_posts WHERE creator_id = %s AND id = ANY(%s)",
                (creator_id, drop_ids),
            )
            trimmed = len(drop_ids)
            changed = True
            for order, rid in enumerate(r["id"] for r in ranked[:FREE_POST_LIMIT]):
                cursor.execute(
                    "UPDATE portfolio_posts SET display_order = %s, is_featured = %s WHERE id = %s",
                    (order, order < 3, rid),
                )

    if changed and commit_conn is not None:
        commit_conn.commit()

    return {"trimmed": trimmed, "unpublished": False}


def serialize_post(post):
    """Serialize a portfolio post for JSON response"""
    thumb = post.get("thumbnail_url")
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
        'saves': post.get('saves', 0),
        'thumbnail_url': to_proxied_media_url(thumb) if thumb else None,
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

        # Correct legacy free kits that auto-filled past the 3-post / publish rules
        enforce_free_kit_limits(cursor, creator_id, commit_conn=conn)

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

        # Enforce free post cap (same as My Kit UI)
        cursor.execute(
            "SELECT subscription_tier FROM creators WHERE id = %s",
            (creator_id,),
        )
        creator = cursor.fetchone() or {}
        tier = (creator.get("subscription_tier") or "free").lower()
        is_pro = tier in ("pro", "elite")
        if not is_pro:
            cursor.execute(
                "SELECT COUNT(*) AS c FROM portfolio_posts WHERE creator_id = %s",
                (creator_id,),
            )
            count = int(cursor.fetchone()["c"] or 0)
            if count >= FREE_POST_LIMIT:
                cursor.close()
                conn.close()
                return jsonify({
                    "error": f"Free kits include {FREE_POST_LIMIT} posts. Upgrade to Pro to add more.",
                    "upgrade_required": True,
                    "code": "post_limit",
                }), 403

        cursor.execute('''
            INSERT INTO portfolio_posts (
                creator_id, post_url, platform, post_type, brand_name,
                collab_type, views, likes, comments, shares, saves,
                thumbnail_url, display_order, is_featured
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            int(data.get('saves', 0)),
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

        # Handle publish action (My Kit — available on free; Pro adds kit views + more posts)
        if data.get('publish'):
            cursor.execute(
                "SELECT kit_slug, username FROM creators WHERE id = %s",
                (creator_id,),
            )
            creator = cursor.fetchone() or {}

            updates.append("kit_published = %s")
            values.append(True)
            # Track when kit was last published
            updates.append("kit_published_at = NOW()")

            if creator and not creator.get('kit_slug'):
                updates.append("kit_slug = %s")
                values.append(creator.get('username'))

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

        # Free users cannot keep a live public kit (closes the auto-fill publish loophole)
        enforce_free_kit_limits(cursor, creator_id, commit_conn=conn)

        cursor.execute('''
            SELECT
                kit_tagline, kit_published, kit_published_at, kit_slug,
                rates_reel, rates_tiktok, rates_photo, rates_gifted,
                username, subscription_tier
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
            'is_pro': _is_pro_tier(creator.get('subscription_tier')),
        })

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching kit settings: {e}")
        return jsonify({'error': 'Failed to fetch settings'}), 500


# ============================================
# KIT VIEWS & INTERACTIONS TRACKING
# ============================================

@portfolio_bp.route('/views', methods=['GET'])
def get_kit_views():
    """
    GET /api/portfolio/views
    Returns kit view stats and interaction analytics for the creator
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

        # Get interaction stats this week (if table exists)
        interactions = {
            'portfolio_clicks': 0,
            'share_clicks': 0,
            'social_clicks': 0,
            'contact_clicks': 0
        }
        try:
            cursor.execute('''
                SELECT interaction_type, COUNT(*) as count
                FROM kit_interactions
                WHERE creator_id = %s AND created_at >= %s
                GROUP BY interaction_type
            ''', (creator_id, week_ago))
            for row in cursor.fetchall():
                # Map singular db types to plural response keys
                type_to_key = {
                    'portfolio_click': 'portfolio_clicks',
                    'share_click': 'share_clicks',
                    'social_click': 'social_clicks',
                    'contact_click': 'contact_clicks'
                }
                key = type_to_key.get(row['interaction_type'])
                if key:
                    interactions[key] = row['count']
        except Exception as e:
            # Table may not exist yet
            pass

        # Get recent views with unique referrers (deduplicated)
        cursor.execute('''
            SELECT id, viewed_at, referrer,
                   ROW_NUMBER() OVER (PARTITION BY COALESCE(referrer, '') ORDER BY viewed_at DESC) as rn
            FROM kit_views
            WHERE creator_id = %s AND viewed_at >= %s
            ORDER BY viewed_at DESC
        ''', (creator_id, week_ago))
        all_views = cursor.fetchall()

        # Keep only the most recent view from each unique referrer source
        seen_sources = set()
        recent = []
        for v in all_views:
            source = v['referrer'] or 'direct'
            if source not in seen_sources and len(recent) < 5:
                seen_sources.add(source)
                recent.append(v)

        cursor.close()
        conn.close()

        return jsonify({
            'views_this_week': total_week,
            'portfolio_clicks': interactions['portfolio_clicks'],
            'share_clicks': interactions['share_clicks'],
            'social_clicks': interactions['social_clicks'],
            'contact_clicks': interactions['contact_clicks'],
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


@portfolio_bp.route('/interaction', methods=['POST'])
def log_kit_interaction():
    """
    POST /api/portfolio/interaction
    Log an interaction on a public media kit (portfolio click, share, social, contact)
    Body: { creator_id, interaction_type, target_value? }
    """
    try:
        data = request.get_json() or {}
        creator_id = data.get('creator_id')
        interaction_type = data.get('interaction_type')
        target_value = data.get('target_value', '')

        if not creator_id or not interaction_type:
            return jsonify({'error': 'creator_id and interaction_type required'}), 400

        valid_types = ['portfolio_click', 'share_click', 'social_click', 'contact_click']
        if interaction_type not in valid_types:
            return jsonify({'error': f'Invalid interaction_type. Must be one of: {valid_types}'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        referrer = request.headers.get('Referer', '')[:500]
        viewer_ip = request.remote_addr

        cursor.execute('''
            INSERT INTO kit_interactions (creator_id, interaction_type, target_value, referrer, viewer_ip)
            VALUES (%s, %s, %s, %s, %s)
        ''', (creator_id, interaction_type, target_value[:255] if target_value else '', referrer, viewer_ip))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error logging kit interaction: {e}")
        return jsonify({'error': 'Failed to log interaction'}), 500


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
                    c.id, c.username, c.username as first_name, c.image_profile as avatar_url,
                    COALESCE(c.kit_tagline, '') as tagline, c.niche as niches,
                    c.followers_count as follower_count, c.engagement_rate,
                    COALESCE(c.kit_published, false) as kit_published,
                    COALESCE(c.rates_reel, 0) as rates_reel,
                    COALESCE(c.rates_tiktok, 0) as rates_tiktok,
                    COALESCE(c.rates_photo, 0) as rates_photo,
                    COALESCE(c.rates_gifted, true) as rates_gifted,
                    COALESCE(c.regions, '[]') as regions,
                    COALESCE(c.primary_age_range, '') as primary_age_range,
                    COALESCE(c.subscription_tier, 'free') as subscription_tier,
                    COALESCE(c.social_links, '[]') as social_links,
                    u.email AS user_email
                FROM creators c
                LEFT JOIN users u ON u.id = c.user_id
                WHERE c.username = %s OR c.kit_slug = %s
            ''', (slug, slug))
            creator = cursor.fetchone()
        except Exception as col_err:
            # Kit columns don't exist, fallback to basic query
            print(f"Kit columns not found, using fallback: {col_err}")
            has_kit_columns = False
            conn.rollback()  # Reset the failed transaction
            cursor.execute('''
                SELECT
                    c.id, c.username, c.username as first_name, c.image_profile as avatar_url,
                    '' as tagline, c.niche as niches,
                    c.followers_count as follower_count, c.engagement_rate,
                    false as kit_published,
                    0 as rates_reel,
                    0 as rates_tiktok,
                    0 as rates_photo,
                    true as rates_gifted,
                    COALESCE(c.regions, '[]') as regions,
                    COALESCE(c.primary_age_range, '') as primary_age_range,
                    COALESCE(c.social_links, '[]') as social_links,
                    u.email AS user_email
                FROM creators c
                LEFT JOIN users u ON u.id = c.user_id
                WHERE c.username = %s
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

            # Check for tracking token (ref) from pitch-generated URL
            ref_token = request.args.get('ref')
            print(f"[KIT_VIEW] Portfolio route - ref token: {ref_token}, username: {slug}")

            if ref_token:
                # Look up the pipeline entry for this token to get brand attribution
                cursor.execute('''
                    SELECT cp.id as pipeline_id, cp.creator_id, cp.brand_id,
                           pb.brand_name, pb.category as brand_category
                    FROM creator_pipeline cp
                    JOIN pr_brands pb ON pb.id = cp.brand_id
                    WHERE cp.kit_token = %s
                ''', (ref_token,))
                pipeline = cursor.fetchone()
                print(f"[KIT_VIEW] Pipeline lookup result: {pipeline}")

                if pipeline:
                    # Check for existing view from this pipeline today (dedupe)
                    cursor.execute('''
                        SELECT id FROM kit_views
                        WHERE pipeline_id = %s AND viewer_ip = %s
                        AND viewed_at > NOW() - INTERVAL '1 day'
                    ''', (pipeline['pipeline_id'], viewer_ip))
                    existing = cursor.fetchone()

                    if existing:
                        print(f"[KIT_VIEW] Duplicate view within 24h, skipping: pipeline={pipeline['pipeline_id']}")
                    else:
                        # New view - insert with brand attribution
                        cursor.execute('''
                            INSERT INTO kit_views (creator_id, brand_id, pipeline_id, viewer_ip, referrer, viewed_at)
                            VALUES (%s, %s, %s, %s, %s, NOW())
                        ''', (pipeline['creator_id'], pipeline['brand_id'], pipeline['pipeline_id'], viewer_ip, referrer))
                        print(f"[KIT_VIEW] Inserted new kit_view: creator={pipeline['creator_id']}, brand={pipeline['brand_id']}, brand_name={pipeline['brand_name']}")

                        # Mark pipeline entry as "opened" - brand viewed the media kit
                        cursor.execute('''
                            UPDATE creator_pipeline
                            SET email_opened = true,
                                email_opened_at = COALESCE(email_opened_at, NOW()),
                                email_open_count = COALESCE(email_open_count, 0) + 1,
                                updated_at = NOW()
                            WHERE id = %s
                        ''', (pipeline['pipeline_id'],))
                        print(f"[KIT_VIEW] Marked pipeline {pipeline['pipeline_id']} as opened")

                        # Send brand view notification email (Pro upgrade CTA for free users)
                        try:
                            # Get creator email and subscription tier
                            cursor.execute('''
                                SELECT c.username, c.subscription_tier, c.brand_view_email_sent_at,
                                       u.email
                                FROM creators c
                                JOIN users u ON c.user_id = u.id
                                WHERE c.id = %s
                            ''', (pipeline['creator_id'],))
                            creator_info = cursor.fetchone()

                            if creator_info and creator_info['email']:
                                # Rate limit: Don't send more than 1 brand view email per hour
                                last_sent = creator_info.get('brand_view_email_sent_at')
                                should_send = True
                                if last_sent:
                                    time_since = datetime.now() - last_sent
                                    if time_since < timedelta(hours=1):
                                        should_send = False
                                        print(f"[BRAND_VIEW_EMAIL] Skipping - sent {int(time_since.total_seconds()/60)} mins ago")

                                if should_send:
                                    tier = creator_info.get('subscription_tier', 'free') or 'free'
                                    is_pro = tier in ('pro', 'elite')

                                    # Update last sent timestamp
                                    cursor.execute('''
                                        UPDATE creators SET brand_view_email_sent_at = NOW() WHERE id = %s
                                    ''', (pipeline['creator_id'],))

                                    # Send email in background thread
                                    def send_async():
                                        send_brand_view_notification(
                                            to_email=creator_info['email'],
                                            creator_name=creator_info['username'],
                                            brand_name=pipeline['brand_name'],
                                            brand_category=pipeline.get('brand_category'),
                                            is_pro=is_pro,
                                            viewed_at=datetime.now()
                                        )
                                    threading.Thread(target=send_async, daemon=True).start()
                                    print(f"[BRAND_VIEW_EMAIL] Queued for {creator_info['email']} (Pro: {is_pro}, Category: {pipeline.get('brand_category')})")
                        except Exception as email_err:
                            print(f"[BRAND_VIEW_EMAIL] Error: {email_err}")
                            pass  # Don't fail the request if email fails

                    conn.commit()
                else:
                    # No pipeline found for token, log basic view
                    cursor.execute('''
                        INSERT INTO kit_views (creator_id, viewer_ip, viewer_ua, referrer)
                        VALUES (%s, %s, %s, %s)
                    ''', (creator['id'], viewer_ip, viewer_ua, referrer))
                    conn.commit()
            else:
                # No ref token, log basic view
                cursor.execute('''
                    INSERT INTO kit_views (creator_id, viewer_ip, viewer_ua, referrer)
                    VALUES (%s, %s, %s, %s)
                ''', (creator['id'], viewer_ip, viewer_ua, referrer))
                conn.commit()
        except Exception as view_err:
            print(f"[KIT_VIEW] Error logging view: {view_err}")
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

        # Mailto CTA uses account email from users table only (never bio/scrape)
        contact_email = (creator.get('user_email') or '').strip() or None

        return jsonify({
            'creator_id': creator['id'],  # For interaction tracking
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
            'contact_email': contact_email,
            'posts': [serialize_post(p) for p in posts],
        })

    except Exception as e:
        if conn:
            conn.close()
        print(f"Error fetching public kit: {e}")
        return jsonify({'error': 'Failed to fetch kit'}), 500


# ============================================
# KIT VIEW NOTIFICATIONS (LinkedIn-style)
# ============================================

def send_brand_view_notification(to_email, creator_name, brand_name, brand_category, is_pro, viewed_at=None):
    """
    Send a brand view notification email when a brand clicks a tracked ref link.
    For free users: Shows brand category (taste of value) + upgrade CTA
    For Pro users: Brand name revealed with follow-up CTA

    Returns (success: bool, error_message: str or None)
    """
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')
        sender_name = os.getenv('EMAIL_SENDER_NAME', 'Newcollab')
        frontend_url = os.getenv('FRONTEND_URL', 'https://app.newcollab.co')

        # Format time ago
        time_ago = "just now"
        if viewed_at:
            diff = datetime.now() - viewed_at
            minutes = int(diff.total_seconds() / 60)
            hours = int(diff.total_seconds() / 3600)
            if minutes < 1:
                time_ago = "just now"
            elif minutes < 60:
                time_ago = f"{minutes} minute{'s' if minutes > 1 else ''} ago"
            elif hours < 24:
                time_ago = f"{hours} hour{'s' if hours > 1 else ''} ago"

        # Format brand category for display (e.g. "skincare" -> "A skincare brand")
        category_display = "A brand"
        if brand_category:
            cat_lower = brand_category.lower().strip()
            if cat_lower and cat_lower not in ('other', 'unknown', 'n/a'):
                # Add article
                vowels = ('a', 'e', 'i', 'o', 'u')
                article = "An" if cat_lower[0] in vowels else "A"
                category_display = f"{article} {cat_lower} brand"

        if is_pro:
            subject = f"{brand_name} just viewed your media kit"
            preheader = f"They checked out your profile {time_ago}. Follow up now."
            headline = f"{brand_name} viewed your kit"
            subtitle = f"They checked out your profile {time_ago}. Now is the perfect time to follow up."
            body_html = f"""
                <p style="margin: 0 0 16px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Hey {creator_name},
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    <strong>{brand_name}</strong> clicked through to your media kit {time_ago}. This means they are actively evaluating you for a potential collab.
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Follow up while they are engaged. Most replies happen in the first 24 hours.
                </p>
            """
            cta_label = "Send Follow-Up Now"
            cta_url = f"{frontend_url}/creator/dashboard/pr-pipeline?utm_source=email&utm_medium=brand_view"
            urgency_box = ""
            features_html = ""
            footer_note = ""
        else:
            # Free users get brand category (taste of value) but not identity
            subject = f"{category_display} just viewed your media kit"
            preheader = f"See who it was and follow up while they are still interested."
            headline = f"{category_display} viewed your kit"
            subtitle = f"They checked out your profile {time_ago}. See who and follow up while they are still engaged."
            body_html = f"""
                <p style="margin: 0 0 16px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Hey {creator_name},
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    {category_display} just clicked through to view your media kit. They are checking you out right now.
                </p>
            """
            urgency_box = """
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 0 0 24px 0;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"
                                   style="background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border-radius: 12px; border: 1px solid #fbbf24;">
                                <tr>
                                    <td style="padding: 20px 24px; text-align: center;">
                                        <p style="margin: 0 0 8px 0; font-size: 24px;">🔥</p>
                                        <p style="margin: 0 0 6px 0; font-size: 16px; font-weight: 700; color: #92400e;">
                                            Strike while it is hot
                                        </p>
                                        <p style="margin: 0; font-size: 14px; color: #a16207; line-height: 1.5;">
                                            Follow up while they are engaged. Most replies happen in the first 24 hours.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            """
            features_html = """
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 0 0 24px 0;">
                            <p style="margin: 0 0 14px 0; font-size: 13px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px;">
                                With Pro you can:
                            </p>
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#128065; <strong>See exactly which brand</strong> viewed your kit</td></tr>
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#128231; <strong>Send a follow-up pitch</strong> while they are engaged</td></tr>
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#128230; <strong>Unlimited pitches</strong> to any brand, every month</td></tr>
                            </table>
                        </td>
                    </tr>
                </table>
            """
            cta_label = "See Who and Follow Up - $19/mo"
            # Link to for-you page with upgrade param to trigger upgrade modal -> Stripe checkout
            cta_url = f"{frontend_url}/creator/dashboard/for-you?upgrade=kit_views&utm_source=email&utm_medium=brand_view"
            footer_note = '<p style="margin: 14px 0 0 0; font-size: 12px; color: #9ca3af;">Cancel anytime. One PR package pays for a year of Pro.</p>'

        # Preheader padding to prevent email client from pulling body text
        preheader_padding = '&nbsp;' * 100

        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{headline}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
    <!-- Preheader text (hidden) -->
    <div style="display: none; max-height: 0; overflow: hidden;">{preheader}{preheader_padding}</div>

    <div style="max-width: 560px; margin: 0 auto; padding: 32px 16px;">

        <!-- Logo -->
        <div style="text-align: center; padding-bottom: 28px;">
            <a href="{frontend_url}" style="text-decoration: none;">
                <img src="https://app.newcollab.co/newcollab-logo-dark.png" alt="Newcollab" height="36" style="height: 36px; width: auto;" />
            </a>
        </div>

        <!-- Main Card -->
        <div style="background: #ffffff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 36px 40px;">

            <!-- Hero -->
            <div style="text-align: center; margin-bottom: 24px;">
                <h1 style="margin: 0 0 8px 0; font-size: 24px; font-weight: 800; color: #111827;">{headline}</h1>
                <p style="margin: 0; font-size: 15px; color: #6b7280;">{subtitle}</p>
            </div>

            <!-- Body -->
            {body_html}

            <!-- Urgency Box (free users) -->
            {urgency_box}

            <!-- Features (free users) -->
            {features_html}

            <!-- CTA -->
            <div style="text-align: center; padding-top: 8px;">
                <a href="{cta_url}" style="display: inline-block; background: linear-gradient(135deg, #7C3AED, #E11D48); color: #ffffff; font-size: 16px; font-weight: 700; padding: 16px 40px; border-radius: 10px; text-decoration: none;">
                    {cta_label}
                </a>
                {footer_note}
            </div>
        </div>

        <!-- Footer -->
        <div style="text-align: center; padding: 28px 24px;">
            <p style="margin: 0 0 10px 0; font-size: 13px; color: #6b7280;">
                You are receiving this because a brand clicked your tracked kit link.
            </p>
            <p style="margin: 0; font-size: 12px; color: #d1d5db;">
                2026 Newcollab. All rights reserved.
            </p>
        </div>
    </div>
</body>
</html>
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = f"{sender_name} <{smtp_username}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        print(f"[BRAND_VIEW_EMAIL] Sent to {to_email} (Pro: {is_pro}, Brand: {brand_name})")
        return True, None

    except Exception as e:
        print(f"[BRAND_VIEW_EMAIL] Error sending: {e}")
        return False, str(e)


def send_kit_view_email(to_email, creator_name, views_count, referrer=None):
    """
    Send a kit view notification email.
    Returns (success: bool, error_message: str or None)
    """
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')
        sender_name = os.getenv('EMAIL_SENDER_NAME', 'NewCollab')
        frontend_url = os.getenv('FRONTEND_URL', 'https://app.newcollab.co')

        subject = f"👀 Someone viewed your media kit"

        # Build referrer text
        referrer_text = ""
        if referrer:
            try:
                domain = referrer.split('/')[2].replace('www.', '')
                referrer_text = f"<p style='color:#6B7280;font-size:13px;margin:8px 0 0;'>Came from: {domain}</p>"
            except:
                pass

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#F5F5F7;padding:40px 20px;margin:0;">
            <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
                <div style="text-align:center;margin-bottom:24px;">
                    <div style="width:56px;height:56px;background:linear-gradient(135deg,#6366F1,#8B5CF6);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;">
                        <span style="font-size:24px;">👁️</span>
                    </div>
                </div>

                <h1 style="font-size:22px;font-weight:700;color:#111827;text-align:center;margin:0 0 8px;">
                    {views_count} {'person' if views_count == 1 else 'people'} viewed your kit
                </h1>

                <p style="font-size:14px;color:#6B7280;text-align:center;margin:0 0 24px;">
                    Someone's checking you out, {creator_name}! Keep your kit updated to make a great impression.
                </p>

                {referrer_text}

                <div style="text-align:center;margin-top:28px;">
                    <a href="{frontend_url}/creator/dashboard/my-kit" style="display:inline-block;background:#0F0F0F;color:#fff;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:14px;">
                        View your kit analytics →
                    </a>
                </div>

                <p style="font-size:12px;color:#9CA3AF;text-align:center;margin-top:32px;">
                    You're receiving this because you have a published media kit on NewCollab.
                </p>
            </div>
        </body>
        </html>
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = f"{sender_name} <{smtp_username}>"
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        return True, None
    except Exception as e:
        print(f"Error sending kit view email: {e}")
        return False, str(e)


def maybe_send_kit_view_notification(creator_id, conn=None):
    """
    Check if a creator should receive a kit view notification and send it.

    Rules:
    - Must have at least 1 view in last 15 minutes
    - Must not have been notified in last 15 minutes
    - Respects global email cooloff

    Returns: (sent: bool, reason: str)
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator info and check notification timing
        cursor.execute('''
            SELECT
                c.id,
                c.username,
                u.email,
                c.kit_view_notified_at,
                c.subscription_tier,
                c.last_any_email_sent,
                COALESCE(c.emails_sent_this_week, 0) as emails_sent_this_week
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s AND c.kit_published = true
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            if close_conn:
                conn.close()
            return False, "Creator not found or kit not published"

        # Check if Pro (free users don't get detailed notifications)
        tier = creator.get('subscription_tier', 'free') or 'free'

        # Check notification cooldown (15 minutes)
        notified_at = creator.get('kit_view_notified_at')
        if notified_at:
            time_since = datetime.now() - notified_at
            if time_since < timedelta(minutes=15):
                if close_conn:
                    conn.close()
                return False, "Notified too recently"

        # Check global email cooldown (24h between emails, max 3/week)
        last_email = creator.get('last_any_email_sent')
        if last_email:
            time_since = datetime.now() - last_email
            if time_since < timedelta(hours=24):
                if close_conn:
                    conn.close()
                return False, "Global email cooldown active"

        if creator.get('emails_sent_this_week', 0) >= 3:
            if close_conn:
                conn.close()
            return False, "Weekly email limit reached"

        # Check for recent views (last 15 minutes)
        cursor.execute('''
            SELECT COUNT(*) as count, MAX(referrer) as latest_referrer
            FROM kit_views
            WHERE creator_id = %s AND viewed_at >= NOW() - INTERVAL '15 minutes'
        ''', (creator_id,))
        views = cursor.fetchone()

        if not views or views['count'] == 0:
            if close_conn:
                conn.close()
            return False, "No recent views"

        # Send the notification
        success, error = send_kit_view_email(
            to_email=creator['email'],
            creator_name=creator['username'],
            views_count=views['count'],
            referrer=views.get('latest_referrer')
        )

        if success:
            # Update notification timestamp and email counters
            cursor.execute('''
                UPDATE creators
                SET kit_view_notified_at = NOW(),
                    last_any_email_sent = NOW(),
                    emails_sent_this_week = COALESCE(emails_sent_this_week, 0) + 1
                WHERE id = %s
            ''', (creator_id,))
            conn.commit()

        cursor.close()
        if close_conn:
            conn.close()

        if success:
            return True, f"Sent notification for {views['count']} views"
        else:
            return False, f"Email send failed: {error}"

    except Exception as e:
        if close_conn and conn:
            conn.close()
        return False, str(e)


@portfolio_bp.route('/notify-view', methods=['POST'])
def trigger_kit_view_notification():
    """
    POST /api/portfolio/notify-view
    Manually trigger kit view notification check for current user.
    Called after a view is logged to potentially send immediate notification.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Not authenticated'}), 401

    sent, reason = maybe_send_kit_view_notification(creator_id)

    return jsonify({
        'sent': sent,
        'reason': reason
    })


@portfolio_bp.route('/cron/check-kit-views', methods=['POST'])
def cron_check_kit_view_notifications():
    """
    POST /api/portfolio/cron/check-kit-views
    Cron endpoint to check all creators with recent views and send notifications.
    Should be called every 15 minutes by a scheduler.

    Requires X-Cron-Secret header for authentication.
    """
    cron_secret = os.getenv('CRON_SECRET')
    if cron_secret and request.headers.get('X-Cron-Secret') != cron_secret:
        return jsonify({'error': 'Unauthorized'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Find creators with recent views who haven't been notified recently
        cursor.execute('''
            SELECT DISTINCT kv.creator_id
            FROM kit_views kv
            JOIN creators c ON c.id = kv.creator_id
            WHERE kv.viewed_at >= NOW() - INTERVAL '15 minutes'
            AND c.kit_published = true
            AND (c.kit_view_notified_at IS NULL OR c.kit_view_notified_at < NOW() - INTERVAL '15 minutes')
            AND (c.last_any_email_sent IS NULL OR c.last_any_email_sent < NOW() - INTERVAL '24 hours')
            AND COALESCE(c.emails_sent_this_week, 0) < 3
        ''')
        creators = cursor.fetchall()
        cursor.close()

        results = []
        for row in creators:
            sent, reason = maybe_send_kit_view_notification(row['creator_id'], conn)
            results.append({
                'creator_id': row['creator_id'],
                'sent': sent,
                'reason': reason
            })

        conn.close()

        return jsonify({
            'checked': len(creators),
            'results': results
        })

    except Exception as e:
        if conn:
            conn.close()
        return jsonify({'error': str(e)}), 500
