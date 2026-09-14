"""
Portfolio Builder Routes for Creator Dashboard
API endpoints for the new media kit portfolio builder - posts CRUD, settings, public kit, views tracking
"""

from io import BytesIO
from urllib.parse import parse_qs, urlparse, quote

from flask import Blueprint, request, jsonify, session, abort, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
import psycopg2
from psycopg2.extras import Json, RealDictCursor
import os
import re
import json
import secrets
import requests
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import threading
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
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


from media_proxy_routes import fetch_post_preview_bytes, to_proxied_media_url
from services.public_kit import (
    build_public_socials,
    parse_kit_niches,
    serialize_public_recent_posts,
    social_kit_stats,
)

_KIT_LAYOUT_READY = False
_KIT_THEME_READY = False
_KIT_LOOKS = {'ivory', 'blush', 'ink', 'gallery', 'sage'}
_KIT_FONTS = {'playfair', 'cormorant', 'fraunces', 'instrument', 'syne'}
_KIT_LAYOUTS = {'maison', 'gallery', 'ratecard', 'editorial', 'studio'}


def _is_pro_tier(tier) -> bool:
    return (tier or "free").lower() in ("pro", "elite")


def _normalize_kit_layout(raw) -> str:
    aliases = {
        'editorial': 'maison',
        'studio': 'gallery',
        'maison': 'maison',
        'gallery': 'gallery',
        'ratecard': 'ratecard',
    }
    return aliases.get(str(raw or '').lower().strip(), 'maison')


def _social_profile_url(platform, handle):
    handle = re.sub(r'[^a-zA-Z0-9._]', '', str(handle or '').lstrip('@'))[:40]
    if not handle:
        return ''
    if platform == 'instagram':
        return f'https://instagram.com/{handle}'
    if platform == 'tiktok':
        return f'https://tiktok.com/@{handle}'
    if platform == 'youtube':
        return f'https://youtube.com/@{handle}'
    return ''


def _sanitize_social_profiles(raw, fallback_platform='', fallback_handle=''):
    seen = set()
    out = []
    rows = raw if isinstance(raw, list) else []
    for item in rows[:3]:
        if not isinstance(item, dict):
            continue
        platform = str(item.get('platform') or '').strip().lower()
        if platform not in ('instagram', 'tiktok', 'youtube') or platform in seen:
            continue
        handle = re.sub(r'[^a-zA-Z0-9._]', '', str(item.get('handle') or '').lstrip('@'))[:40]
        if not handle:
            continue
        seen.add(platform)
        followers_raw = re.sub(r'[^\d]', '', str(item.get('followers') or ''))[:10]
        try:
            followers = int(followers_raw) if followers_raw else None
        except Exception:
            followers = None
        row = {
            'platform': platform,
            'handle': handle,
            'url': _social_profile_url(platform, handle),
        }
        if followers:
            row['followers'] = followers
        out.append(row)
    if not out:
        platform = str(fallback_platform or '').strip().lower()
        handle = re.sub(r'[^a-zA-Z0-9._]', '', str(fallback_handle or '').lstrip('@'))[:40]
        if handle and platform in ('instagram', 'tiktok', 'youtube'):
            out.append({
                'platform': platform,
                'handle': handle,
                'url': _social_profile_url(platform, handle),
            })
    return out


def _public_social_profiles(rows):
    out = []
    socials = {}
    counts = []
    for item in rows or []:
        platform = item.get('platform') or ''
        handle = item.get('handle') or ''
        url = item.get('url') or _social_profile_url(platform, handle)
        row = {
            'platform': platform,
            'handle': f'@{handle}' if handle and not str(handle).startswith('@') else (handle or None),
            'url': url,
        }
        if item.get('followers'):
            try:
                row['followers'] = int(item['followers'])
                counts.append(row['followers'])
            except (TypeError, ValueError):
                pass
        out.append(row)
        if platform and url:
            socials[platform] = url
    return out, socials, counts


def _as_theme_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) or {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _sanitize_kit_theme(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    if isinstance(raw, str):
        try:
            src = json.loads(raw) or {}
        except Exception:
            src = {}
    look = src.get('look') if src.get('look') in _KIT_LOOKS else 'ivory'
    font = src.get('font') if src.get('font') in _KIT_FONTS else 'playfair'
    accent = str(src.get('accent') or '')
    if not re.match(r'^#[0-9A-Fa-f]{6}$', accent):
        accent = ''
    services = []
    for row in (src.get('services') or [])[:12]:
        if not isinstance(row, dict):
            continue
        title = str(row.get('title') or '')[:48]
        body = str(row.get('body') or '')[:160]
        sid = str(row.get('id') or '')[:32]
        if title:
            services.append({'id': sid, 'title': title, 'body': body})
    email = str(src.get('email') or '').strip()[:120]
    if email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        email = ''
    platform = str(src.get('social_platform') or '').strip().lower()
    if platform not in ('instagram', 'tiktok', 'youtube'):
        platform = ''
    handle = re.sub(r'[^a-zA-Z0-9._]', '', str(src.get('social_handle') or src.get('handle') or '').lstrip('@'))[:40]
    profiles = _sanitize_social_profiles(src.get('social_profiles'), platform, handle)
    if profiles:
        platform = profiles[0].get('platform') or platform
        handle = profiles[0].get('handle') or handle
    example_posts = _merge_example_posts(src.get('example_posts'), src.get('examples'))
    theme = {
        'look': look,
        'font': font,
        'accent': accent,
        'cover_url': str(src.get('cover_url') or '')[:500],
        'display_name': str(src.get('display_name') or '')[:80],
        'headline': str(src.get('headline') or '')[:140],
        'about': str(src.get('about') or '')[:600],
        'location': str(src.get('location') or '')[:80],
        'email': email,
        'social_platform': platform,
        'social_handle': handle,
        'social_profiles': profiles,
        'services': services,
        'examples': [row['url'] for row in example_posts],
        'example_posts': example_posts,
        'brand_logos': _sanitize_brand_logos(src.get('brand_logos')),
        'testimonials': _sanitize_testimonials(src.get('testimonials')),
    }
    return theme


def _sanitize_testimonials(raw) -> list:
    rows = raw if isinstance(raw, list) else []
    out = []
    for item in rows[:6]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get('quote') or item.get('text') or '').strip()[:320]
        if len(quote) < 2:
            continue
        name = str(item.get('name') or '').strip()[:60]
        role = str(item.get('role') or item.get('brand') or item.get('title') or '').strip()[:80]
        out.append({
            'quote': quote,
            'name': name,
            'role': role,
        })
    return out


def _example_media_key(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return str(url or '').lower()
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = (parsed.path or '').rstrip('/').lower()
    tiktok = re.search(r'/video/(\d+)', path)
    if host.endswith('tiktok.com') and tiktok:
        return f'tiktok:{tiktok.group(1)}'
    instagram = re.search(r'/(?:p|reel|reels|tv)/([^/]+)', path)
    if (host.endswith('instagram.com') or host == 'instagr.am') and instagram:
        return f'instagram:{instagram.group(1)}'
    youtube = (parse_qs(parsed.query).get('v') or [None])[0]
    if not youtube:
        match = re.search(r'/(?:embed|shorts|live)/([^/]+)', path)
        youtube = match.group(1) if match else (path.lstrip('/').split('/')[0] if host == 'youtu.be' else '')
    if youtube and (host.endswith('youtube.com') or host == 'youtu.be' or host.endswith('youtube-nocookie.com')):
        return f'youtube:{youtube}'
    return f'{host}{path}'


def _sanitize_example_posts(raw) -> list:
    rows = raw if isinstance(raw, list) else []
    out = []
    seen = {}
    for item in rows[:24]:
        if isinstance(item, dict):
            url = item.get('url') or item.get('post_url') or ''
            title = str(item.get('title') or '')[:80]
            description = str(item.get('description') or item.get('body') or '')[:200]
        else:
            url = item
            title = ''
            description = ''
        urls = _sanitize_example_urls([url])
        if not urls:
            continue
        clean = urls[0]
        key = _example_media_key(clean)
        existing = seen.get(key)
        if not existing:
            row = {'url': clean, 'title': title, 'description': description}
            seen[key] = row
            out.append(row)
            continue
        if not existing.get('title') and title:
            existing['title'] = title
        if not existing.get('description') and description:
            existing['description'] = description
    return out


def _merge_example_posts(posts, examples) -> list:
    return _sanitize_example_posts(
        (posts if isinstance(posts, list) else []) + (examples if isinstance(examples, list) else [])
    )


def _sanitize_example_urls(raw) -> list:
    allowed_hosts = (
        'instagram.com',
        'instagr.am',
        'tiktok.com',
        'youtube.com',
        'youtu.be',
        'youtube-nocookie.com',
    )
    rows = raw if isinstance(raw, list) else []
    out = []
    seen = set()
    for item in rows[:24]:
        url = str(item or '').strip()[:300]
        if not url:
            continue
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            continue
        host = (parsed.hostname or '').lower()
        if host.startswith('www.'):
            host = host[4:]
        if not any(host == allowed or host.endswith('.' + allowed) for allowed in allowed_hosts):
            continue
        clean = parsed.geturl()
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _sanitize_brand_logos(raw) -> list:
    rows = raw if isinstance(raw, list) else []
    out = []
    seen = set()
    for item in rows[:12]:
        if isinstance(item, str):
            name = ''
            url = item
        elif isinstance(item, dict):
            name = str(item.get('name') or '')[:40]
            url = str(item.get('logo_url') or item.get('url') or '')
        else:
            continue
        url = url.strip()[:500]
        if not url:
            continue
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            continue
        clean = parsed.geturl()
        if clean in seen:
            continue
        seen.add(clean)
        out.append({'name': name, 'logo_url': clean})
    return out


def _ensure_kit_layout_column(cursor, conn=None):
    global _KIT_LAYOUT_READY
    if _KIT_LAYOUT_READY:
        return
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'creators' AND column_name = 'kit_layout'
        LIMIT 1
        """
    )
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE creators ADD COLUMN kit_layout VARCHAR(32) DEFAULT 'editorial'")
        if conn is not None:
            conn.commit()
    _KIT_LAYOUT_READY = True


def _ensure_kit_theme_column(cursor, conn=None):
    global _KIT_THEME_READY
    if _KIT_THEME_READY:
        return
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'creators' AND column_name = 'kit_theme'
        LIMIT 1
        """
    )
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE creators ADD COLUMN kit_theme JSONB DEFAULT '{}'::jsonb")
        if conn is not None:
            conn.commit()
    _KIT_THEME_READY = True


_FREE_PORTFOLIOS_READY = False
_RESERVED_PORTFOLIO_SLUGS = {
    'admin', 'api', 'app', 'brand', 'brands', 'c', 'contact', 'directory',
    'kit', 'login', 'media-kit', 'portfolio', 'register', 'static', 'www',
}


def _normalize_portfolio_email(raw):
    return str(raw or '').strip().lower()[:120]


def _ensure_free_portfolios_table(cursor, conn=None):
    global _FREE_PORTFOLIOS_READY
    if _FREE_PORTFOLIOS_READY:
        return
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS free_portfolios (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(80) UNIQUE NOT NULL,
            email VARCHAR(255),
            edit_token VARCHAR(64) UNIQUE NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            view_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS free_portfolios_email_lower_uidx
        ON free_portfolios (lower(btrim(email)))
        WHERE email IS NOT NULL AND btrim(email) <> ''
        """
    )
    if conn is not None:
        conn.commit()
    _FREE_PORTFOLIOS_READY = True


def _free_portfolio_by_token(cursor, token):
    token = str(token or '').strip()
    if not token:
        return None
    cursor.execute('SELECT * FROM free_portfolios WHERE edit_token = %s LIMIT 1', (token,))
    return cursor.fetchone()


def _free_portfolio_by_email(cursor, email):
    email = _normalize_portfolio_email(email)
    if not email:
        return None
    cursor.execute(
        'SELECT * FROM free_portfolios WHERE lower(btrim(email)) = %s LIMIT 1',
        (email,),
    )
    return cursor.fetchone()


def _slugify_portfolio(handle, name):
    raw = str(handle or name or 'portfolio').lstrip('@').lower()
    slug = re.sub(r'[^a-z0-9]+', '', raw)[:40]
    return slug or 'portfolio'


def _unique_portfolio_slug(cursor, desired, keep_slug=None):
    base = desired if desired not in _RESERVED_PORTFOLIO_SLUGS else 'portfolio'
    slug = base
    n = 2
    while True:
        if keep_slug and slug == keep_slug:
            return slug
        cursor.execute(
            "SELECT 1 FROM creators WHERE username = %s OR kit_slug = %s LIMIT 1",
            (slug, slug),
        )
        taken_creator = cursor.fetchone()
        cursor.execute("SELECT 1 FROM free_portfolios WHERE slug = %s LIMIT 1", (slug,))
        taken_free = cursor.fetchone()
        if not taken_creator and not taken_free:
            return slug
        slug = f"{base}{n}"[:80]
        n += 1


def _free_portfolio_public(row):
    payload = row.get('payload') or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload) or {}
        except Exception:
            payload = {}
    theme = _sanitize_kit_theme(payload.get('kit_theme'))
    raw_theme = payload.get('kit_theme') if isinstance(payload.get('kit_theme'), dict) else {}
    quotes = _sanitize_testimonials(raw_theme.get('testimonials') or theme.get('testimonials'))
    if quotes:
        theme['testimonials'] = quotes
    social_profiles, socials, profile_counts = _public_social_profiles(theme.get('social_profiles'))
    followers = payload.get('followers')
    try:
        follower_count = int(re.sub(r'[^\d]', '', str(followers or '')) or 0)
    except Exception:
        follower_count = 0
    if profile_counts:
        follower_count = max(profile_counts)
    niche = str(payload.get('niche') or '').strip()
    email = theme.get('email') or str(payload.get('email') or '').strip()
    handle = theme.get('social_handle') or str(payload.get('handle') or '').lstrip('@')
    platform = theme.get('social_platform') or str(payload.get('social_platform') or '').strip().lower()
    if not social_profiles and handle and platform:
        url = _social_profile_url(platform, handle)
        if url:
            social_profiles = [{'platform': platform, 'handle': f'@{handle}', 'url': url}]
            socials = {platform: url}
    return {
        'username': row['slug'],
        'first_name': theme.get('display_name') or row['slug'],
        'display_name': theme.get('display_name') or row['slug'],
        'avatar_url': '',
        'tagline': theme.get('headline') or '',
        'bio': theme.get('about') or '',
        'niches': [niche] if niche else [],
        'follower_count': follower_count,
        'email': email,
        'engagement_rate': 0,
        'regions': [],
        'primary_age_range': '',
        'rates_reel': payload.get('rates_reel') or 0,
        'rates_tiktok': payload.get('rates_tiktok') or 0,
        'rates_photo': payload.get('rates_photo') or 0,
        'rates_gifted': payload.get('rates_gifted') if payload.get('rates_gifted') is not None else True,
        'kit_layout': _normalize_kit_layout(payload.get('layout')),
        'kit_theme': theme,
        'kit_views': None,
        'is_pro': False,
        'is_free_portfolio': True,
        'social_handle': handle,
        'social_platform': platform,
        'socials': socials,
        'social_profiles': social_profiles,
        'posts': [],
        'posts_source': None,
    }


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

        if 'kit_layout' in data:
            layout = _normalize_kit_layout(data.get('kit_layout'))
            _ensure_kit_layout_column(cursor, conn)
            updates.append("kit_layout = %s")
            values.append(layout)

        if 'kit_theme' in data:
            _ensure_kit_theme_column(cursor, conn)
            incoming = data.get('kit_theme') if isinstance(data.get('kit_theme'), dict) else {}
            theme = _sanitize_kit_theme(incoming)
            quotes = _sanitize_testimonials(incoming.get('testimonials'))
            if quotes:
                theme['testimonials'] = quotes
            else:
                cursor.execute('SELECT kit_theme FROM creators WHERE id = %s', (creator_id,))
                existing = cursor.fetchone() or {}
                kept = _sanitize_testimonials(_as_theme_dict(existing.get('kit_theme')).get('testimonials'))
                if kept:
                    theme['testimonials'] = kept
                else:
                    theme['testimonials'] = []
            updates.append("kit_theme = COALESCE(kit_theme, '{}'::jsonb) || %s::jsonb")
            values.append(Json(theme))
        else:
            theme = None

        # Publish stays free. Pro unlocks brand view tracking.
        if data.get('publish'):
            if theme is None:
                _ensure_kit_theme_column(cursor, conn)
                cursor.execute('SELECT kit_theme FROM creators WHERE id = %s', (creator_id,))
                existing = cursor.fetchone() or {}
                theme = _sanitize_kit_theme(existing.get('kit_theme'))
            email = theme.get('email') or ''
            if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
                cursor.close()
                conn.close()
                return jsonify({'error': 'Add an email so brands can reach you from this portfolio'}), 400
            if not theme.get('social_platform') or not theme.get('social_handle'):
                cursor.close()
                conn.close()
                return jsonify({'error': 'Add your Instagram, TikTok, or YouTube handle'}), 400
            if len(str(theme.get('about') or '').strip()) < 150:
                cursor.close()
                conn.close()
                return jsonify({'error': 'About you needs at least 150 characters so a brand can brief you'}), 400
            if not theme.get('examples'):
                cursor.close()
                conn.close()
                return jsonify({'error': 'Add at least one Instagram, TikTok, or YouTube post'}), 400
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
        _ensure_kit_layout_column(cursor, conn)
        _ensure_kit_theme_column(cursor, conn)

        cursor.execute('''
            SELECT
                kit_tagline, kit_published, kit_published_at, kit_slug,
                rates_reel, rates_tiktok, rates_photo, rates_gifted,
                COALESCE(kit_layout, 'editorial') AS kit_layout,
                COALESCE(kit_theme, '{}'::jsonb) AS kit_theme,
                username, subscription_tier, user_id,
                social_platform, social_handle,
                social_follower_count, followers_count,
                social_media_count, total_likes, engagement_rate, social_oauth_videos,
                bio, image_profile
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404

        recent_posts = None
        scrape_row = {}
        scrape_name = ''
        scrape_bio = ''
        user_id = creator.get('user_id')
        if user_id:
            try:
                cursor.execute(
                    '''
                    SELECT recent_posts, full_name, raw_bio, engagement_rate, post_count
                    FROM creator_profile_data
                    WHERE user_id = %s
                    LIMIT 1
                    ''',
                    (user_id,),
                )
                scrape_row = cursor.fetchone() or {}
                recent_posts = scrape_row.get('recent_posts')
                scrape_name = (scrape_row.get('full_name') or '').strip()
                scrape_bio = (scrape_row.get('raw_bio') or '').strip()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        cursor.close()
        conn.close()

        platform = (creator.get('social_platform') or '').strip().lower()
        handle = (creator.get('social_handle') or '').strip().lstrip('@')
        follower_count = int(creator.get('social_follower_count') or 0) or int(creator.get('followers_count') or 0)
        stats = social_kit_stats(
            creator=creator,
            scrape={
                'full_name': scrape_name,
                'raw_bio': scrape_bio,
                'engagement_rate': scrape_row.get('engagement_rate') if scrape_row else None,
                'post_count': scrape_row.get('post_count') if scrape_row else None,
                'recent_posts': recent_posts,
            },
            oauth_videos=creator.get('social_oauth_videos') if platform == 'tiktok' else None,
            handle=handle,
        )
        likes_count = stats['likes_count']
        video_count = stats['video_count']
        avg_views = stats['avg_views']
        tiktok_videos = stats['tiktok_videos'] if platform == 'tiktok' else []
        display_name = stats['display_name'] or scrape_name or handle
        bio = (creator.get('bio') or '').strip() or scrape_bio

        return jsonify({
            'kit_tagline': creator['kit_tagline'],
            'kit_published': creator['kit_published'],
            'kit_published_at': creator['kit_published_at'].isoformat() if creator['kit_published_at'] else None,
            'kit_slug': creator['kit_slug'] or creator['username'],
            'rates_reel': creator['rates_reel'],
            'rates_tiktok': creator['rates_tiktok'],
            'rates_photo': creator['rates_photo'],
            'rates_gifted': creator['rates_gifted'],
            'kit_layout': _normalize_kit_layout(creator.get('kit_layout')),
            'kit_theme': _sanitize_kit_theme(creator.get('kit_theme')),
            'is_pro': _is_pro_tier(creator.get('subscription_tier')),
            'social_platform': platform or None,
            'social_handle': handle or None,
            'follower_count': follower_count,
            'social_follower_count': int(creator.get('social_follower_count') or 0),
            'likes_count': likes_count,
            'video_count': video_count,
            'avg_views': avg_views,
            'engagement_rate': float(stats.get('engagement_rate') or 0),
            'display_name': display_name or None,
            'bio': bio or None,
            'avatar_url': (creator.get('image_profile') or '').strip() or None,
            'tiktok_videos': tiktok_videos,
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


@portfolio_bp.route('/media-preview', methods=['GET'])
def media_preview():
    """Public poster lookup for kit studio / published video cards. No auth.

    JSON: { thumbnail_url, platform }
    ?as=img streams the still so <img> can use this URL directly.
    """
    url = str(request.args.get('url') or '').strip()[:300]
    as_img = str(request.args.get('as') or '').lower() in ('img', 'image', '1')
    if not url:
        if as_img:
            abort(400)
        return jsonify({'thumbnail_url': None, 'platform': 'unknown'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    try:
        parsed = urlparse(url)
    except Exception:
        if as_img:
            abort(400)
        return jsonify({'thumbnail_url': None, 'platform': 'unknown'}), 400
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    allowed = ('instagram.com', 'instagr.am', 'tiktok.com', 'youtube.com', 'youtu.be', 'youtube-nocookie.com')
    if not any(host == item or host.endswith('.' + item) for item in allowed):
        if as_img:
            abort(400)
        return jsonify({'thumbnail_url': None, 'platform': 'unknown'}), 400

    thumbnail_url = None
    platform = 'unknown'
    try:
        if 'tiktok.com' in host:
            platform = 'tiktok'
            if as_img:
                fetched = fetch_post_preview_bytes(url)
                if not fetched:
                    abort(404)
                content, content_type = fetched
                img_io = BytesIO(content)
                img_io.seek(0)
                response = send_file(img_io, mimetype=content_type or 'image/jpeg', max_age=3600)
                response.headers['Cross-Origin-Resource-Policy'] = 'cross-origin'
                response.headers['Cache-Control'] = 'public, max-age=3600'
                return response
            thumbnail_url = f"{request.host_url.rstrip('/')}/api/portfolio/media-preview?url={quote(url, safe='')}&as=img"
        elif 'youtu' in host:
            platform = 'youtube'
            video_id = None
            if 'youtu.be' in host:
                match = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', url)
                video_id = match.group(1) if match else None
            else:
                match = re.search(r'/shorts/([a-zA-Z0-9_-]+)', url) or re.search(r'[?&]v=([a-zA-Z0-9_-]+)', url)
                video_id = match.group(1) if match else None
            if video_id:
                thumbnail_url = f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'
                if as_img:
                    return jsonify({}), 302, {'Location': thumbnail_url}
        elif 'instagram' in host or host == 'instagr.am':
            platform = 'instagram'
            if as_img:
                fetched = fetch_post_preview_bytes(url)
                if not fetched:
                    abort(404)
                content, content_type = fetched
                img_io = BytesIO(content)
                img_io.seek(0)
                response = send_file(img_io, mimetype=content_type or 'image/jpeg', max_age=3600)
                response.headers['Cross-Origin-Resource-Policy'] = 'cross-origin'
                return response
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching media preview: {e}")
        if as_img:
            abort(404)
    return jsonify({'thumbnail_url': thumbnail_url, 'platform': platform})


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

@portfolio_bp.route('/free', methods=['POST'])
def publish_free_portfolio():
    """Guest UGC portfolio publish — no account required."""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:80]
    handle = str(data.get('handle') or '').strip()[:80]
    if not name and not handle:
        return jsonify({'error': 'Add a name or handle'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_kit_theme_column(cursor, conn)
        _ensure_kit_layout_column(cursor, conn)
        _ensure_free_portfolios_table(cursor, conn)

        theme = _sanitize_kit_theme(data.get('kit_theme'))
        incoming_theme = data.get('kit_theme') if isinstance(data.get('kit_theme'), dict) else {}
        quotes = _sanitize_testimonials(incoming_theme.get('testimonials') or theme.get('testimonials'))
        theme['testimonials'] = quotes
        if name and not theme.get('display_name'):
            theme['display_name'] = name
        email = _normalize_portfolio_email(theme.get('email') or data.get('email'))
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email or ''):
            cursor.close()
            conn.close()
            return jsonify({'error': 'Add an email so brands can reach you from this portfolio'}), 400
        edit_token = str(data.get('edit_token') or '').strip()
        row = _free_portfolio_by_token(cursor, edit_token)
        if row:
            owned = _free_portfolio_by_email(cursor, email)
            if owned and owned['id'] != row['id']:
                cursor.close()
                conn.close()
                return jsonify({
                    'error': 'This email already has a published portfolio.',
                    'code': 'email_in_use',
                    'slug': owned['slug'],
                }), 409
        else:
            owned = _free_portfolio_by_email(cursor, email)
            if owned:
                cursor.close()
                conn.close()
                return jsonify({
                    'error': 'This email already has a published portfolio. Update it from the browser that published it.',
                    'code': 'email_in_use',
                    'slug': owned['slug'],
                }), 409

        desired = _slugify_portfolio(handle, name)
        slug = row['slug'] if row else _unique_portfolio_slug(cursor, desired)
        if not theme.get('social_platform') or not theme.get('social_handle'):
            cursor.close()
            conn.close()
            return jsonify({'error': 'Add your Instagram, TikTok, or YouTube handle'}), 400
        if len(str(theme.get('about') or '').strip()) < 150:
            cursor.close()
            conn.close()
            return jsonify({'error': 'About you needs at least 150 characters so a brand can brief you'}), 400
        if not theme.get('examples'):
            cursor.close()
            conn.close()
            return jsonify({'error': 'Add at least one Instagram, TikTok, or YouTube post'}), 400
        payload = {
            'name': name,
            'handle': handle,
            'social_platform': theme.get('social_platform') or str(data.get('social_platform') or '').strip().lower(),
            'email': email,
            'niche': str(data.get('niche') or '').strip()[:40],
            'followers': str(data.get('followers') or '').strip()[:20],
            'layout': _normalize_kit_layout(data.get('layout')),
            'kit_theme': theme,
            'rates_reel': data.get('rates_reel') or 0,
            'rates_tiktok': data.get('rates_tiktok') or 0,
            'rates_photo': data.get('rates_photo') or 0,
            'rates_gifted': data.get('rates_gifted') if data.get('rates_gifted') is not None else True,
        }

        if row:
            cursor.execute(
                """
                UPDATE free_portfolios
                SET slug = %s, email = %s, payload = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING slug, edit_token
                """,
                (slug, email or None, Json(payload), row['id']),
            )
        else:
            token = secrets.token_urlsafe(24)
            cursor.execute(
                """
                INSERT INTO free_portfolios (slug, email, edit_token, payload)
                VALUES (%s, %s, %s, %s)
                RETURNING slug, edit_token
                """,
                (slug, email or None, token, Json(payload)),
            )
        saved = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        frontend = os.getenv('FRONTEND_URL', 'https://newcollab.co').rstrip('/')
        if 'app.newcollab.co' in frontend:
            frontend = 'https://newcollab.co'
        return jsonify({
            'ok': True,
            'slug': saved['slug'],
            'edit_token': saved['edit_token'],
            'url': f"{frontend}/kit/{saved['slug']}",
        })
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        print(f"Error publishing free portfolio: {e}")
        return jsonify({'error': 'Failed to publish portfolio'}), 500


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
        _ensure_kit_layout_column(cursor, conn)
        _ensure_kit_theme_column(cursor, conn)

        # Try to find creator - use basic columns first, then try kit columns
        creator = None
        has_kit_columns = True

        try:
            cursor.execute('''
                SELECT
                    c.id, c.user_id, c.username,
                    NULLIF(BTRIM(COALESCE(u.first_name, '')), '') as first_name,
                    c.image_profile as avatar_url,
                    COALESCE(NULLIF(BTRIM(COALESCE(c.kit_tagline, '')), ''), NULLIF(BTRIM(COALESCE(c.bio, '')), '')) as tagline,
                    c.bio,
                    c.niche as niches,
                    COALESCE(c.followers_count, c.social_follower_count, 0) as follower_count,
                    c.engagement_rate,
                    COALESCE(c.kit_published, false) as kit_published,
                    COALESCE(c.rates_reel, 0) as rates_reel,
                    COALESCE(c.rates_tiktok, 0) as rates_tiktok,
                    COALESCE(c.rates_photo, 0) as rates_photo,
                    COALESCE(c.rates_gifted, true) as rates_gifted,
                    COALESCE(c.kit_layout, 'editorial') as kit_layout,
                    COALESCE(c.kit_theme, '{}'::jsonb) as kit_theme,
                    COALESCE(c.regions, '[]') as regions,
                    COALESCE(c.primary_age_range, '') as primary_age_range,
                    COALESCE(c.subscription_tier, 'free') as subscription_tier,
                    COALESCE(c.social_links, '[]') as social_links,
                    c.social_handle,
                    c.social_platform,
                    c.social_follower_count,
                    c.social_media_count,
                    c.total_likes,
                    c.social_oauth_videos,
                    c.platforms
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
                    c.id, c.user_id, c.username,
                    NULLIF(BTRIM(COALESCE(u.first_name, '')), '') as first_name,
                    c.image_profile as avatar_url,
                    COALESCE(c.bio, '') as tagline, c.bio, c.niche as niches,
                    COALESCE(c.followers_count, 0) as follower_count, c.engagement_rate,
                    false as kit_published,
                    0 as rates_reel,
                    0 as rates_tiktok,
                    0 as rates_photo,
                    true as rates_gifted,
                    'editorial' as kit_layout,
                    '{}'::jsonb as kit_theme,
                    COALESCE(c.regions, '[]') as regions,
                    COALESCE(c.primary_age_range, '') as primary_age_range,
                    COALESCE(c.social_links, '[]') as social_links,
                    c.social_handle,
                    c.social_platform,
                    NULL as social_oauth_videos,
                    c.platforms
                FROM creators c
                LEFT JOIN users u ON u.id = c.user_id
                WHERE c.username = %s
            ''', (slug,))
            creator = cursor.fetchone()

        if not creator:
            _ensure_free_portfolios_table(cursor, conn)
            cursor.execute('SELECT * FROM free_portfolios WHERE slug = %s LIMIT 1', (slug,))
            free_row = cursor.fetchone()
            if free_row:
                try:
                    cursor.execute(
                        'UPDATE free_portfolios SET view_count = view_count + 1 WHERE id = %s',
                        (free_row['id'],),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                public = _free_portfolio_public(free_row)
                cursor.close()
                conn.close()
                return jsonify(public)
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
                from services.kit_view_tracking import (
                    record_brand_profile_view,
                    resolve_brand_from_kit_ref,
                )
                attribution = resolve_brand_from_kit_ref(
                    cursor, ref_token, creator_id=creator['id']
                )
                print(f"[KIT_VIEW] Attribution lookup result: {attribution}")

                if attribution:
                    record_brand_profile_view(
                        cursor,
                        creator_id=attribution['creator_id'],
                        brand_id=attribution['brand_id'],
                        brand_name=attribution.get('brand_name'),
                        brand_category=attribution.get('brand_category'),
                        viewer_ip=viewer_ip,
                        referrer=referrer,
                        pipeline_id=attribution.get('pipeline_id'),
                        notify=True,
                    )
                    conn.commit()
                else:
                    # No brand match for token, log basic view
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

        scrape_items = creator.get('social_oauth_videos')
        scrape_thumbs = None
        scrape_platform = creator.get('social_platform')
        scrape_row = None
        try:
            user_id = creator.get('user_id')
            handle = (creator.get('social_handle') or creator.get('username') or '').strip().lstrip('@')
            # Onboarding scrape is keyed by users.id, not creators.id.
            if user_id:
                cursor.execute('''
                    SELECT recent_posts, recent_post_thumbnails, primary_platform,
                           full_name, engagement_rate, post_count
                    FROM creator_profile_data
                    WHERE user_id = %s
                    LIMIT 1
                ''', (user_id,))
                scrape_row = cursor.fetchone()
            if not scrape_row and handle:
                cursor.execute('''
                    SELECT recent_posts, recent_post_thumbnails, primary_platform,
                           full_name, engagement_rate, post_count
                    FROM creator_profile_data
                    WHERE LOWER(handle) = LOWER(%s)
                    ORDER BY scraped_at DESC NULLS LAST
                    LIMIT 1
                ''', (handle,))
                scrape_row = cursor.fetchone()
            if scrape_row:
                extra = scrape_row.get('recent_posts')
                if extra and not posts:
                    if isinstance(scrape_items, list) and isinstance(extra, list):
                        scrape_items = scrape_items + extra
                    elif not scrape_items:
                        scrape_items = extra
                scrape_thumbs = scrape_row.get('recent_post_thumbnails')
                scrape_platform = scrape_row.get('primary_platform') or scrape_platform
        except Exception as scrape_err:
            print(f"[PUBLIC_KIT] scrape posts lookup: {scrape_err}")
            try:
                conn.rollback()
            except Exception:
                pass

        cursor.close()
        conn.close()

        # Parse niches (JSON array or comma string)
        niches = parse_kit_niches(creator.get('niches'))

        # Parse regions (stored as JSON array)
        regions = creator.get('regions', '[]')
        if isinstance(regions, str):
            try:
                regions = json.loads(regions)
            except Exception:
                regions = []
        if not regions:
            regions = []

        # Check if creator is Pro
        tier = creator.get('subscription_tier', 'free') or 'free'
        is_pro = tier in ('pro', 'elite')

        socials, social_profiles = build_public_socials(
            creator.get('social_links'),
            social_handle=creator.get('social_handle'),
            social_platform=creator.get('social_platform'),
            username=creator.get('username'),
        )
        theme = _sanitize_kit_theme(creator.get('kit_theme'))
        public_theme = theme
        theme_profiles, theme_socials, theme_counts = _public_social_profiles(theme.get('social_profiles'))
        if theme_profiles:
            seen = {item.get('platform') for item in theme_profiles}
            for item in social_profiles:
                if item.get('platform') not in seen:
                    theme_profiles.append(item)
            social_profiles = theme_profiles
            socials = {**(theme_socials or {}), **socials}
            for item in social_profiles:
                if item.get('platform') and item.get('url'):
                    socials[item['platform']] = item['url']
        follower_count = creator['follower_count'] or 0
        if not follower_count and theme_counts:
            follower_count = max(theme_counts)

        serialized_posts = [serialize_post(p) for p in posts]
        posts_source = 'portfolio' if serialized_posts else None
        if not serialized_posts:
            serialized_posts = serialize_public_recent_posts(
                scrape_items,
                thumbnails=scrape_thumbs,
                default_platform=scrape_platform,
            )
            for post in serialized_posts:
                if post.get('thumbnail_url'):
                    post['thumbnail_url'] = to_proxied_media_url(post['thumbnail_url'])
            if serialized_posts:
                posts_source = 'scrape'

        bio = (creator.get('bio') or '').strip() or None
        theme = public_theme if isinstance(public_theme, dict) else {}
        raw_theme = _as_theme_dict(creator.get('kit_theme'))
        quotes = _sanitize_testimonials(raw_theme.get('testimonials') or theme.get('testimonials'))
        if quotes:
            theme['testimonials'] = quotes
        theme_name = str((theme or {}).get('display_name') or '').strip()
        if theme_name.lower() == 'your name':
            theme_name = ''
        stats = social_kit_stats(
            creator=creator,
            scrape=scrape_row or {},
            oauth_videos=creator.get('social_oauth_videos'),
            handle=(creator.get('social_handle') or creator.get('username') or ''),
        )
        display_name = (
            theme_name
            or stats.get('display_name')
            or (creator.get('first_name') or '').strip()
            or creator['username']
        )
        likes_count = stats['likes_count']
        video_count = stats['video_count']
        avg_views = stats['avg_views']

        return jsonify({
            'creator_id': creator['id'],
            'username': creator['username'],
            'first_name': display_name,
            'display_name': display_name,
            'avatar_url': creator['avatar_url'],
            'tagline': creator['tagline'] or '',
            'bio': bio,
            'niches': niches,
            'follower_count': follower_count,
            'likes_count': likes_count,
            'video_count': video_count,
            'avg_views': avg_views,
            'engagement_rate': float(stats.get('engagement_rate') or 0),
            'regions': regions,
            'primary_age_range': creator.get('primary_age_range', ''),
            'rates_reel': creator['rates_reel'],
            'rates_tiktok': creator['rates_tiktok'],
            'rates_photo': creator['rates_photo'],
            'rates_gifted': creator['rates_gifted'],
            'kit_layout': _normalize_kit_layout(creator.get('kit_layout')),
            'kit_theme': theme,
            'kit_views': kit_views if is_pro else None,
            'is_pro': is_pro,
            'socials': socials,
            'social_profiles': social_profiles,
            'posts': serialized_posts,
            'posts_source': posts_source,
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
    Notify a creator that a brand reviewed their gifted PR application / kit.
    Free users: brand category + upgrade CTA. Pro users: brand name.

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
            subject = f"{brand_name} just reviewed your application"
            preheader = f"They opened your profile {time_ago}."
            headline = f"{brand_name} reviewed your application"
            subtitle = f"They opened your profile on their gifted PR list {time_ago}."
            body_html = f"""
                <p style="margin: 0 0 16px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Hey {creator_name},
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    <strong>{brand_name}</strong> is reviewing creators for gifted PR and opened your profile {time_ago}.
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Keep your kit and shipping details up to date so you are ready if they add you to the gift list.
                </p>
            """
            cta_label = "See who's reviewing you"
            cta_url = f"{frontend_url}/creator/dashboard/for-you?utm_source=email&utm_medium=brand_view"
            urgency_box = ""
            features_html = ""
            footer_note = ""
        else:
            # Free users get brand category (taste of value) but not identity
            subject = f"{category_display} just reviewed your application"
            preheader = "See which brand is reviewing you."
            headline = f"{category_display} reviewed your application"
            subtitle = f"They opened your profile {time_ago}. Upgrade to see which brand."
            body_html = f"""
                <p style="margin: 0 0 16px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    Hey {creator_name},
                </p>
                <p style="margin: 0 0 24px 0; font-size: 15px; color: #374151; line-height: 1.7;">
                    {category_display} is reviewing gifted PR applications and opened your profile {time_ago}.
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
                                            Brands are reviewing now
                                        </p>
                                        <p style="margin: 0; font-size: 14px; color: #a16207; line-height: 1.5;">
                                            See which brand opened your profile, and apply to more gifted PR lists.
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
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#128065; <strong>See exactly which brand</strong> reviewed you</td></tr>
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#127873; <strong>Apply to more gifted PR lists</strong> on For You</td></tr>
                                <tr><td style="padding: 0 0 10px 0; font-size: 14px; color: #374151;">&#9889; <strong>Priority placement</strong> when brands are picking</td></tr>
                            </table>
                        </td>
                    </tr>
                </table>
            """
            cta_label = "See which brand — $19/mo"
            cta_url = f"{frontend_url}/creator/dashboard/for-you?upgrade=kit_views&utm_source=email&utm_medium=brand_view"
            footer_note = '<p style="margin: 14px 0 0 0; font-size: 12px; color: #9ca3af;">Cancel anytime. One gifted PR collab pays for a year of Pro.</p>'

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
                You are receiving this because a brand reviewed your gifted PR application.
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
