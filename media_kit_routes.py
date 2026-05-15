"""
Media Kit Routes for Creator Dashboard
API endpoints for creator media kit builder - create, edit, publish, and view media kits
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import jwt_required, get_jwt_identity
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import random
from datetime import datetime

media_kit_bp = Blueprint('media_kit', __name__, url_prefix='/api/media-kit')

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


# ============================================
# MEDIA KIT CRUD ENDPOINTS
# ============================================

@media_kit_bp.route('', methods=['GET'])
def get_my_media_kit():
    """
    Get current user's media kit
    Returns the media kit data and whether user can edit (based on subscription tier)
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get media kit with creator subscription info
        cursor.execute('''
            SELECT
                mk.*,
                c.subscription_tier,
                c.username as creator_username,
                c.bio,
                c.image_profile,
                c.followers_count,
                c.engagement_rate as profile_engagement_rate,
                c.niche as profile_niches,
                c.regions as profile_regions,
                c.has_media_kit,
                c.media_kit_url
            FROM creators c
            LEFT JOIN media_kits mk ON mk.creator_id = c.id
            WHERE c.id = %s
        ''', (creator_id,))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result:
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        # Extract media kit data (may be None if no kit exists)
        media_kit = None
        if result.get('id'):  # Media kit exists
            media_kit = {
                'id': result['id'],
                'display_name': result['display_name'],
                'username': result['username'],
                'tagline': result['tagline'],
                'profile_photo_url': result['profile_photo_url'],
                'location': result['location'],
                'total_followers': result['total_followers'],
                'engagement_rate': float(result['engagement_rate']) if result['engagement_rate'] else None,
                'platforms': result['platforms'] or [],
                'niches': result['niches'] or [],
                'content_types': result['content_types'] or [],
                'collaborations': result['collaborations'] or [],
                'rates': result['rates'] or [],
                'currency': result['currency'],
                'accepts_gifted': result['accepts_gifted'],
                'accepts_paid': result['accepts_paid'],
                'template_id': result['template_id'],
                'is_published': result['is_published'],
                'published_at': result['published_at'].isoformat() if result['published_at'] else None,
                'publish_count': result['publish_count'],
                'view_count': result['view_count'],
                'draft_data': result['draft_data'],
                'created_at': result['created_at'].isoformat() if result['created_at'] else None,
                'updated_at': result['updated_at'].isoformat() if result['updated_at'] else None,
            }

        # Creator profile data for pre-filling
        # Parse regions JSON for location
        profile_regions = result['profile_regions']
        if isinstance(profile_regions, str):
            try:
                profile_regions = json.loads(profile_regions)
            except:
                profile_regions = []
        location = ', '.join(profile_regions) if profile_regions else ''

        # Parse niches JSON
        profile_niches = result['profile_niches']
        if isinstance(profile_niches, str):
            try:
                profile_niches = json.loads(profile_niches)
            except:
                profile_niches = []

        profile = {
            'username': result['creator_username'],
            'bio': result['bio'],
            'profile_image': result['image_profile'],
            'followers_count': result['followers_count'],
            'engagement_rate': float(result['profile_engagement_rate']) if result['profile_engagement_rate'] else None,
            'niches': profile_niches if isinstance(profile_niches, list) else [profile_niches] if profile_niches else [],
            'location': location,
            'has_media_kit': result.get('has_media_kit', False),
            'media_kit_url': result.get('media_kit_url'),
        }

        tier = result['subscription_tier'] or 'free'
        has_kit = media_kit is not None
        publish_count = media_kit['publish_count'] if media_kit else 0

        # Free users can edit until they publish once
        can_edit = tier in ['pro', 'elite'] or publish_count == 0

        return jsonify({
            'success': True,
            'media_kit': media_kit,
            'profile': profile,
            'has_kit': has_kit,
            'can_edit': can_edit,
            'subscription_tier': tier,
            'public_url': f"https://newcollab.co/kit/{result['creator_username']}" if has_kit and media_kit.get('is_published') else None
        })

    except Exception as e:
        if conn:
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@media_kit_bp.route('', methods=['POST'])
def create_or_update_media_kit():
    """
    Create or update media kit
    Handles both initial creation and updates
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator info and check subscription
        cursor.execute('''
            SELECT username, subscription_tier
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        username = creator['username']

        # Check if media kit already exists
        cursor.execute('''
            SELECT id, publish_count, is_published
            FROM media_kits
            WHERE creator_id = %s
        ''', (creator_id,))
        existing = cursor.fetchone()

        # Freemium check: free users can't edit after publishing
        if existing and tier == 'free' and existing['publish_count'] >= 1 and existing['is_published']:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Free users can only publish once. Upgrade to Pro for unlimited updates.',
                'upgrade_required': True
            }), 403

        # Prepare data (handle empty strings as None for numeric fields)
        display_name = data.get('display_name', '').strip() or username
        tagline = data.get('tagline', '').strip()
        profile_photo_url = data.get('profile_photo_url') or None
        location = data.get('location', '').strip() or None
        total_followers = data.get('total_followers') or 0
        if isinstance(total_followers, str) and total_followers.strip() == '':
            total_followers = 0
        engagement_rate = data.get('engagement_rate')
        if engagement_rate == '' or engagement_rate is None:
            engagement_rate = None
        else:
            try:
                engagement_rate = float(engagement_rate)
            except (ValueError, TypeError):
                engagement_rate = None
        platforms = json.dumps(data.get('platforms', []))
        niches = json.dumps(data.get('niches', []))
        content_types = json.dumps(data.get('content_types', []))
        collaborations = json.dumps(data.get('collaborations', []))
        rates = json.dumps(data.get('rates', []))
        currency = data.get('currency', 'USD')
        accepts_gifted = data.get('accepts_gifted', True)
        accepts_paid = data.get('accepts_paid', True)
        template_id = data.get('template_id', 1)

        if existing:
            # Update existing media kit
            cursor.execute('''
                UPDATE media_kits
                SET display_name = %s,
                    tagline = %s,
                    profile_photo_url = %s,
                    location = %s,
                    total_followers = %s,
                    engagement_rate = %s,
                    platforms = %s::jsonb,
                    niches = %s::jsonb,
                    content_types = %s::jsonb,
                    collaborations = %s::jsonb,
                    rates = %s::jsonb,
                    currency = %s,
                    accepts_gifted = %s,
                    accepts_paid = %s,
                    template_id = %s,
                    updated_at = NOW()
                WHERE creator_id = %s
                RETURNING id
            ''', (
                display_name, tagline, profile_photo_url, location,
                total_followers, engagement_rate, platforms, niches,
                content_types, collaborations, rates, currency,
                accepts_gifted, accepts_paid, template_id, creator_id
            ))
            media_kit_id = cursor.fetchone()['id']
        else:
            # Create new media kit
            cursor.execute('''
                INSERT INTO media_kits (
                    creator_id, username, display_name, tagline, profile_photo_url,
                    location, total_followers, engagement_rate, platforms, niches,
                    content_types, collaborations, rates, currency, accepts_gifted,
                    accepts_paid, template_id, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW(), NOW()
                )
                RETURNING id
            ''', (
                creator_id, username, display_name, tagline, profile_photo_url,
                location, total_followers, engagement_rate, platforms, niches,
                content_types, collaborations, rates, currency, accepts_gifted,
                accepts_paid, template_id
            ))
            media_kit_id = cursor.fetchone()['id']

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'media_kit_id': media_kit_id,
            'message': 'Media kit saved successfully',
            'preview_url': f'https://newcollab.co/kit/{username}'
        })

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@media_kit_bp.route('/publish', methods=['POST'])
def publish_media_kit():
    """
    Publish media kit (makes it publicly visible)
    Free users can only publish once
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get media kit and subscription info
        cursor.execute('''
            SELECT mk.id, mk.username, mk.publish_count, mk.is_published,
                   c.subscription_tier
            FROM media_kits mk
            JOIN creators c ON mk.creator_id = c.id
            WHERE mk.creator_id = %s
        ''', (creator_id,))
        result = cursor.fetchone()

        if not result:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No media kit found. Create one first.'}), 404

        tier = result['subscription_tier'] or 'free'

        # Freemium check
        if tier == 'free' and result['publish_count'] >= 1:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Free users can only publish once. Upgrade to Pro for unlimited updates.',
                'upgrade_required': True
            }), 403

        # Publish the media kit
        cursor.execute('''
            UPDATE media_kits
            SET is_published = true,
                published_at = NOW(),
                publish_count = publish_count + 1,
                updated_at = NOW()
            WHERE id = %s
        ''', (result['id'],))

        # Update creator table
        public_url = f"https://newcollab.co/kit/{result['username']}"
        cursor.execute('''
            UPDATE creators
            SET has_media_kit = true,
                media_kit_url = %s
            WHERE id = %s
        ''', (public_url, creator_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Media kit published successfully!',
            'public_url': public_url
        })

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@media_kit_bp.route('/generate-tagline', methods=['POST'])
def generate_tagline():
    """
    AI-generate a professional tagline for media kit
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.json
        niches = data.get('niches', [])
        followers = data.get('followers', 0)
        username = data.get('username', 'Creator')

        # Format followers
        if followers >= 1000000:
            followers_str = f"{followers/1000000:.1f}M"
        elif followers >= 1000:
            followers_str = f"{followers/1000:.1f}K"
        else:
            followers_str = str(followers)

        # Format niches
        if niches and len(niches) > 0:
            if len(niches) == 1:
                niche_str = niches[0].lower()
            elif len(niches) == 2:
                niche_str = f"{niches[0].lower()} & {niches[1].lower()}"
            else:
                niche_str = f"{niches[0].lower()}, {niches[1].lower()} & more"
        else:
            niche_str = "content"

        # Generate tagline variations
        taglines = [
            f"Authentic {niche_str} creator connecting brands with {followers_str}+ engaged followers",
            f"{niche_str.title()} enthusiast creating content that resonates | {followers_str}+ community",
            f"Passionate {niche_str} creator helping brands tell their story authentically",
            f"Creating genuine {niche_str} content for brands that value authenticity",
            f"{niche_str.title()} creator with {followers_str}+ followers | Open to brand partnerships",
            f"Your next {niche_str} collaboration partner | Authentic content, real engagement",
        ]

        tagline = random.choice(taglines)

        return jsonify({
            'success': True,
            'tagline': tagline
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@media_kit_bp.route('/unpublish', methods=['POST'])
def unpublish_media_kit():
    """
    Unpublish media kit (hide from public view)
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            UPDATE media_kits
            SET is_published = false,
                updated_at = NOW()
            WHERE creator_id = %s
            RETURNING id
        ''', (creator_id,))

        result = cursor.fetchone()
        if not result:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No media kit found'}), 404

        # Update creator table
        cursor.execute('''
            UPDATE creators
            SET has_media_kit = false,
                media_kit_url = NULL
            WHERE id = %s
        ''', (creator_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Media kit unpublished'
        })

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@media_kit_bp.route('/draft', methods=['POST'])
def save_draft():
    """
    Save media kit draft (auto-save functionality)
    Stores work-in-progress data without publishing
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator username
        cursor.execute('SELECT username FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        # Upsert draft data
        cursor.execute('''
            INSERT INTO media_kits (creator_id, username, display_name, draft_data, last_draft_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, NOW(), NOW(), NOW())
            ON CONFLICT (creator_id) DO UPDATE
            SET draft_data = %s::jsonb,
                last_draft_at = NOW(),
                updated_at = NOW()
            RETURNING id
        ''', (
            creator_id,
            creator['username'],
            data.get('display_name', 'Untitled'),
            json.dumps(data),
            json.dumps(data)
        ))

        media_kit_id = cursor.fetchone()['id']
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'media_kit_id': media_kit_id,
            'message': 'Draft saved'
        })

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# PUBLIC ENDPOINTS (No auth required)
# ============================================

@media_kit_bp.route('/public/<username>', methods=['GET'])
def get_public_media_kit(username):
    """
    Get public media kit by username
    No authentication required - this is what brands see
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT
                mk.display_name,
                mk.username,
                mk.tagline,
                mk.profile_photo_url,
                mk.location,
                mk.total_followers,
                mk.engagement_rate,
                mk.platforms,
                mk.niches,
                mk.content_types,
                mk.collaborations,
                mk.rates,
                mk.currency,
                mk.accepts_gifted,
                mk.accepts_paid,
                mk.template_id,
                mk.created_at
            FROM media_kits mk
            WHERE mk.username = %s AND mk.is_published = true
        ''', (username,))

        media_kit = cursor.fetchone()

        if not media_kit:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Media kit not found'}), 404

        # Increment view count
        cursor.execute('''
            UPDATE media_kits
            SET view_count = view_count + 1
            WHERE username = %s
        ''', (username,))

        # Log view for analytics (optional - for pro users)
        viewer_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        referrer = request.headers.get('Referer', '')

        cursor.execute('''
            INSERT INTO media_kit_views (media_kit_id, viewer_ip, referrer, viewed_at)
            SELECT id, %s, %s, NOW()
            FROM media_kits WHERE username = %s
        ''', (viewer_ip, referrer, username))

        conn.commit()
        cursor.close()
        conn.close()

        # Format response
        return jsonify({
            'success': True,
            'media_kit': {
                'display_name': media_kit['display_name'],
                'username': media_kit['username'],
                'tagline': media_kit['tagline'],
                'profile_photo_url': media_kit['profile_photo_url'],
                'location': media_kit['location'],
                'total_followers': media_kit['total_followers'],
                'engagement_rate': float(media_kit['engagement_rate']) if media_kit['engagement_rate'] else None,
                'platforms': media_kit['platforms'] or [],
                'niches': media_kit['niches'] or [],
                'content_types': media_kit['content_types'] or [],
                'collaborations': media_kit['collaborations'] or [],
                'rates': media_kit['rates'] or [],
                'currency': media_kit['currency'],
                'accepts_gifted': media_kit['accepts_gifted'],
                'accepts_paid': media_kit['accepts_paid'],
                'template_id': media_kit['template_id'],
            }
        })

    except Exception as e:
        if conn:
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ANALYTICS ENDPOINTS (Pro feature)
# ============================================

@media_kit_bp.route('/analytics', methods=['GET'])
def get_media_kit_analytics():
    """
    Get media kit view analytics (Pro feature only)
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if Pro user
        cursor.execute('''
            SELECT subscription_tier
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        tier = creator['subscription_tier'] if creator else 'free'

        if tier not in ['pro', 'elite']:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Analytics is a Pro feature. Upgrade to see who viewed your media kit.',
                'upgrade_required': True
            }), 403

        # Get basic stats
        cursor.execute('''
            SELECT view_count, published_at, created_at
            FROM media_kits
            WHERE creator_id = %s
        ''', (creator_id,))
        stats = cursor.fetchone()

        if not stats:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No media kit found'}), 404

        # Get view history (last 30 days)
        cursor.execute('''
            SELECT DATE(viewed_at) as date, COUNT(*) as views
            FROM media_kit_views mkv
            JOIN media_kits mk ON mkv.media_kit_id = mk.id
            WHERE mk.creator_id = %s AND viewed_at > NOW() - INTERVAL '30 days'
            GROUP BY DATE(viewed_at)
            ORDER BY date DESC
        ''', (creator_id,))
        view_history = cursor.fetchall()

        # Get top referrers
        cursor.execute('''
            SELECT referrer, COUNT(*) as count
            FROM media_kit_views mkv
            JOIN media_kits mk ON mkv.media_kit_id = mk.id
            WHERE mk.creator_id = %s AND referrer IS NOT NULL AND referrer != ''
            GROUP BY referrer
            ORDER BY count DESC
            LIMIT 10
        ''', (creator_id,))
        top_referrers = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'analytics': {
                'total_views': stats['view_count'],
                'published_at': stats['published_at'].isoformat() if stats['published_at'] else None,
                'view_history': [{'date': str(v['date']), 'views': v['views']} for v in view_history],
                'top_referrers': [{'referrer': r['referrer'], 'count': r['count']} for r in top_referrers]
            }
        })

    except Exception as e:
        if conn:
            conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# CORS HANDLING
# ============================================

@media_kit_bp.after_request
def add_cors_headers(response):
    """Add CORS headers to all media kit responses"""
    origin = request.headers.get('Origin')
    allowed_origins = [
        'http://localhost:3000',
        'http://localhost:3001',
        'https://app.newcollab.co',
        'https://newcollab.co',
        'https://www.newcollab.co'
    ]
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-CSRF-Token'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response
