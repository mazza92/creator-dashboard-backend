"""
Pool Routes for Creator Pool Feature
Handles the "give to get" follow exchange system
"""

from flask import Blueprint, request, jsonify, session
import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
from jinja2 import Environment, FileSystemLoader

pool_bp = Blueprint('pool', __name__, url_prefix='/api/pool')

# Import Pusher for real-time notifications
try:
    from pusher_config import pusher_client
except ImportError:
    pusher_client = None

# Email configuration
GMAIL_USER = os.getenv('SMTP_USERNAME', 'team@newcollab.co')
GMAIL_APP_PASSWORD = os.getenv('SMTP_PASSWORD')


def send_pool_follower_email(target_email, target_first_name, supporter_username, supporter_niche, supporter_social_url, total_followers, supporter_image=None):
    """Send email notification when someone follows from the pool (runs in background thread)"""
    if not GMAIL_APP_PASSWORD:
        print(f"[Pool] SMTP_PASSWORD not set, skipping email to {target_email}")
        return False

    try:
        env = Environment(loader=FileSystemLoader('templates'))
        template = env.get_template('pool_new_follower.html')

        # Get first initial for avatar fallback
        supporter_initial = (supporter_username[0] if supporter_username else '?').upper()

        html_content = template.render(
            first_name=target_first_name or 'there',
            supporter_username=supporter_username,
            supporter_initial=supporter_initial,
            supporter_niche=supporter_niche,
            supporter_social_url=supporter_social_url,
            supporter_image=supporter_image,
            total_followers=total_followers,
            pool_url='https://app.newcollab.co/creator/dashboard/pool',
            unsubscribe_url=None
        )

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🎉 @{supporter_username} just boosted you from the Pool!"
        msg['From'] = f"Newcollab <{GMAIL_USER}>"
        msg['To'] = target_email

        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, target_email, msg.as_string())

        print(f"[Pool] Email sent successfully to {target_email}")
        return True

    except Exception as e:
        print(f"[Pool] Email send error: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_pool_follow_notification(target_user_id, supporter_username):
    """Send real-time notification when someone follows from the pool"""
    if not pusher_client:
        return

    try:
        channel = f'private-notifications-{target_user_id}-creator'
        pusher_client.trigger(channel, 'pool-follow', {
            'type': 'pool_follow',
            'title': 'New Pool Follower!',
            'message': f'@{supporter_username} just followed you from the Pool',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        print(f"[Pool] Pusher notification error: {e}")

# Niche adjacency for matching algorithm
NICHE_ADJACENCY = {
    'beauty': ['skincare', 'haircare', 'fashion', 'lifestyle'],
    'skincare': ['beauty', 'wellness', 'lifestyle'],
    'fitness': ['wellness', 'nutrition', 'lifestyle', 'health'],
    'fashion': ['beauty', 'lifestyle', 'luxury'],
    'lifestyle': ['fashion', 'beauty', 'travel', 'food'],
    'food': ['lifestyle', 'health', 'nutrition', 'travel'],
    'travel': ['lifestyle', 'food', 'adventure', 'photography'],
    'tech': ['gaming', 'productivity', 'gadgets'],
    'gaming': ['tech', 'entertainment', 'streaming'],
    'parenting': ['family', 'lifestyle', 'education'],
    'wellness': ['fitness', 'skincare', 'health', 'nutrition'],
    'health': ['fitness', 'wellness', 'nutrition'],
    'nutrition': ['fitness', 'health', 'food', 'wellness'],
    'photography': ['travel', 'art', 'lifestyle'],
    'art': ['photography', 'design', 'lifestyle'],
    'music': ['entertainment', 'lifestyle'],
    'entertainment': ['music', 'gaming', 'lifestyle'],
    'education': ['parenting', 'productivity'],
    'finance': ['productivity', 'lifestyle'],
    'pets': ['lifestyle', 'family'],
}

def get_db_connection():
    """Get database connection"""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def get_creator_id_from_session():
    """Get creator ID from session"""
    return session.get('creator_id') or session.get('user_id')

def ensure_pool_credits(cursor, creator_id):
    """Ensure pool_credits row exists for creator"""
    cursor.execute("""
        INSERT INTO pool_credits (creator_id, balance, week_start)
        VALUES (%s, 0, DATE_TRUNC('week', NOW()))
        ON CONFLICT (creator_id) DO NOTHING
    """, (creator_id,))

def normalize_niche(niche_value):
    """Normalize niche value to lowercase string"""
    if not niche_value:
        return None
    if isinstance(niche_value, list):
        return niche_value[0].lower().strip() if niche_value else None
    niche_str = str(niche_value).strip()
    if niche_str.startswith('['):
        try:
            import json
            parsed = json.loads(niche_str)
            return parsed[0].lower().strip() if parsed else None
        except:
            pass
    return niche_str.lower().strip()


def parse_social_links(social_links_value):
    """
    Parse social_links field and extract instagram/tiktok handles.
    Returns dict with instagram_handle and tiktok_handle.

    Handles the actual DB format:
    - JSON array: [{"platform": "instagram", "url": "https://instagram.com/handle"}, ...]
    """
    import json
    import re

    result = {'instagram_handle': None, 'tiktok_handle': None}

    if not social_links_value:
        return result

    # Parse JSON if string
    if isinstance(social_links_value, str):
        try:
            links_list = json.loads(social_links_value)
        except (json.JSONDecodeError, TypeError):
            return result
    elif isinstance(social_links_value, list):
        links_list = social_links_value
    else:
        return result

    # Process array of {platform, handle/url} objects
    for link in links_list:
        if not isinstance(link, dict):
            continue

        platform = (link.get('platform') or '').lower()
        # DB uses 'handle' key, but also support 'url' for backwards compatibility
        handle_or_url = link.get('handle') or link.get('url') or ''

        if platform in ('instagram', 'ig') and handle_or_url:
            # If it's a URL, extract handle
            if 'instagram.com/' in handle_or_url:
                match = re.search(r'instagram\.com/([^/?]+)', handle_or_url)
                if match:
                    handle = match.group(1).lstrip('@')
                    if handle and handle not in ['p', 'reel', 'stories', 'explore']:
                        result['instagram_handle'] = handle
            else:
                # It's already a handle
                result['instagram_handle'] = handle_or_url.lstrip('@')

        elif platform in ('tiktok', 'tik tok', 'tt') and handle_or_url:
            # If it's a URL, extract handle
            if 'tiktok.com/@' in handle_or_url:
                match = re.search(r'tiktok\.com/@([^/?]+)', handle_or_url)
                if match:
                    result['tiktok_handle'] = match.group(1)
            elif 'tiktok.com/' in handle_or_url:
                match = re.search(r'tiktok\.com/([^/?]+)', handle_or_url)
                if match:
                    handle = match.group(1).lstrip('@')
                    if handle and handle not in ['discover', 'foryou', 'following']:
                        result['tiktok_handle'] = handle
            else:
                # It's already a handle
                result['tiktok_handle'] = handle_or_url.lstrip('@')

    return result


@pool_bp.route('/matches', methods=['GET'])
def get_pool_matches():
    """
    Get matching creators for the pool queue.
    Matching priority: same niche > adjacent niche > same region > any with credits
    Only shows creators with balance > 0 OR pro subscription
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    limit = request.args.get('limit', 10, type=int)
    # Accept comma-separated list of IDs to exclude (from frontend tracking)
    exclude_param = request.args.get('exclude', '')
    frontend_exclude = []
    if exclude_param:
        try:
            frontend_exclude = [int(x) for x in exclude_param.split(',') if x.strip().isdigit()]
        except:
            pass

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get current creator's info
        cursor.execute("""
            SELECT id, niche, regions, subscription_tier
            FROM creators WHERE id = %s
        """, (creator_id,))
        current_creator = cursor.fetchone()

        if not current_creator:
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404

        my_niche = normalize_niche(current_creator.get('niche'))
        my_region = current_creator.get('regions')
        adjacent_niches = NICHE_ADJACENCY.get(my_niche, [])

        # Get creators already supported by this user (to exclude)
        cursor.execute("""
            SELECT target_id FROM pool_supports
            WHERE supporter_id = %s AND confirmed_at IS NOT NULL
        """, (creator_id,))
        already_supported = [row['target_id'] for row in cursor.fetchall()]
        already_supported.append(creator_id)  # Also exclude self

        # Merge with frontend exclusions
        already_supported.extend(frontend_exclude)

        # Build the matching query with priority scoring
        # RANKING PHILOSOPHY: Activity > Everything. Keep the Pool 100% active for snowball effect.
        # 1. Active users (boosted in last 7 days) always rank highest
        # 2. Pro + Active beats Non-Pro + Active
        # 3. Active Non-Pro beats Inactive Pro (key change!)
        # 4. Then by match score and recency
        cursor.execute("""
            WITH boost_counts AS (
                SELECT supporter_id, COUNT(*) as boosts_given,
                       MAX(confirmed_at) as last_boost_at
                FROM pool_supports
                WHERE confirmed_at >= NOW() - INTERVAL '30 days'
                GROUP BY supporter_id
            ),
            recent_activity AS (
                SELECT supporter_id, COUNT(*) as recent_boosts
                FROM pool_supports
                WHERE confirmed_at >= NOW() - INTERVAL '7 days'
                GROUP BY supporter_id
            ),
            eligible AS (
                SELECT
                    c.id, c.username, c.username as display_name, c.niche, c.regions,
                    c.image_profile as profile_image_url, c.followers_count, c.subscription_tier,
                    c.social_links,
                    COALESCE(pc.balance, 0) as pool_balance,
                    COALESCE(bc.boosts_given, 0) as boosts_given,
                    COALESCE(ra.recent_boosts, 0) as recent_boosts,
                    bc.last_boost_at,
                    CASE
                        WHEN LOWER(c.niche) = %s THEN 100
                        WHEN LOWER(c.niche) = ANY(%s) THEN 80
                        WHEN c.regions = %s AND %s IS NOT NULL THEN 60
                        ELSE 40
                    END as match_score,
                    -- Activity score: recent boosts in last 7 days (0-10 scale)
                    LEAST(COALESCE(ra.recent_boosts, 0), 10) as activity_score,
                    -- Is actively participating (boosted in last 7 days)
                    CASE WHEN COALESCE(ra.recent_boosts, 0) > 0 THEN 1 ELSE 0 END as is_recently_active
                FROM creators c
                LEFT JOIN pool_credits pc ON c.id = pc.creator_id
                LEFT JOIN boost_counts bc ON c.id = bc.supporter_id
                LEFT JOIN recent_activity ra ON c.id = ra.supporter_id
                WHERE c.id != ALL(%s)
                AND c.kit_published = true
                AND c.social_links IS NOT NULL
                AND c.social_links != '[]'
                AND c.social_links != ''
            )
            SELECT * FROM eligible
            ORDER BY
                -- 1. Recently active users ALWAYS first (boosted in last 7 days)
                is_recently_active DESC,
                -- 2. Among active users: Pro + Active > Non-Pro + Active
                CASE WHEN is_recently_active = 1 AND subscription_tier = 'pro' THEN 2
                     WHEN is_recently_active = 1 THEN 1
                     ELSE 0 END DESC,
                -- 3. More recent activity = higher rank
                activity_score DESC,
                -- 4. For inactive users: Pro still gets some priority, but below ALL active users
                CASE WHEN subscription_tier = 'pro' THEN 1 ELSE 0 END DESC,
                -- 5. Match score and balance
                match_score DESC,
                pool_balance DESC,
                -- 6. Recency of last boost (even if outside 7 days)
                last_boost_at DESC NULLS LAST,
                RANDOM()
            LIMIT %s
        """, (my_niche, adjacent_niches, my_region, my_region, already_supported, limit))

        matches = cursor.fetchall()
        conn.close()

        print(f"[Pool] Found {len(matches)} raw matches for creator {creator_id}, niche: {my_niche}")

        # Process matches to extract social handles from social_links
        # Only include creators with at least one valid social handle
        processed_matches = []
        for match in matches:
            match_dict = dict(match)
            # Parse social_links to get individual handles
            social_handles = parse_social_links(match_dict.get('social_links'))

            # Only include if they have at least one valid social handle
            if social_handles['instagram_handle'] or social_handles['tiktok_handle']:
                match_dict['instagram_handle'] = social_handles['instagram_handle']
                match_dict['tiktok_handle'] = social_handles['tiktok_handle']
                # is_active indicates if they're actively in the pool (pro or has credits)
                match_dict['is_active'] = match_dict.get('is_active', 0) == 1
                processed_matches.append(match_dict)

        print(f"[Pool] Returning {len(processed_matches)} processed matches after filtering")

        return jsonify({
            'matches': processed_matches,
            'my_niche': my_niche,
            'count': len(processed_matches)
        })

    except Exception as e:
        print(f"[Pool] Error getting matches: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/credits', methods=['GET'])
def get_pool_credits():
    """Get current user's pool credit balance and stats"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        ensure_pool_credits(cursor, creator_id)
        conn.commit()

        cursor.execute("""
            SELECT
                pc.balance,
                pc.lifetime_earned,
                pc.lifetime_spent,
                pc.streak_days,
                pc.last_support_at,
                c.subscription_tier
            FROM pool_credits pc
            JOIN creators c ON c.id = pc.creator_id
            WHERE pc.creator_id = %s
        """, (creator_id,))
        credits = cursor.fetchone()

        # Get followers received this week
        cursor.execute("""
            SELECT COUNT(*) as received_this_week
            FROM pool_supports
            WHERE target_id = %s
            AND confirmed_at >= DATE_TRUNC('week', NOW())
        """, (creator_id,))
        received = cursor.fetchone()

        # Get supports given this week
        cursor.execute("""
            SELECT COUNT(*) as given_this_week
            FROM pool_supports
            WHERE supporter_id = %s
            AND confirmed_at >= DATE_TRUNC('week', NOW())
        """, (creator_id,))
        given = cursor.fetchone()

        # Get supports given today (for daily quota)
        cursor.execute("""
            SELECT COUNT(*) as given_today
            FROM pool_supports
            WHERE supporter_id = %s
            AND confirmed_at >= DATE_TRUNC('day', NOW())
        """, (creator_id,))
        given_today = cursor.fetchone()

        conn.close()

        is_pro = credits.get('subscription_tier') == 'pro'
        streak_days = credits['streak_days'] or 0

        # Calculate daily limit: base 5 + 2 bonus for 3+ day streak
        base_limit = 5
        streak_bonus = 2 if streak_days >= 3 else 0
        daily_limit = base_limit + streak_bonus

        return jsonify({
            'balance': credits['balance'] if not is_pro else 999,
            'is_pro': is_pro,
            'lifetime_earned': credits['lifetime_earned'] or 0,
            'lifetime_spent': credits['lifetime_spent'] or 0,
            'streak_days': streak_days,
            'last_support_at': credits['last_support_at'].isoformat() if credits['last_support_at'] else None,
            'received_this_week': received['received_this_week'],
            'given_this_week': given['given_this_week'],
            'given_today': given_today['given_today'],
            'daily_limit': daily_limit,
            'streak_bonus': streak_bonus,
            'has_streak_bonus': streak_days >= 3
        })

    except Exception as e:
        print(f"[Pool] Error getting credits: {e}")
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/support', methods=['POST'])
def confirm_support():
    """
    Confirm that the user followed a creator.
    Creates a support record and updates credits.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    data = request.get_json() or {}
    target_id = data.get('target_id')
    platform = data.get('platform', 'instagram')

    if not target_id:
        return jsonify({'error': 'target_id required'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Ensure pool_credits exists for both users
        ensure_pool_credits(cursor, creator_id)
        ensure_pool_credits(cursor, target_id)

        # Check if already supported
        cursor.execute("""
            SELECT id FROM pool_supports
            WHERE supporter_id = %s AND target_id = %s AND platform = %s
        """, (creator_id, target_id, platform))
        existing = cursor.fetchone()

        if existing:
            conn.close()
            return jsonify({'error': 'Already boosted this creator'}), 400

        # Check daily limit for free users
        # Base: 5/day, Streak bonus: +2 for 3+ day streak = 7 max
        cursor.execute("""
            SELECT c.subscription_tier, COALESCE(pc.streak_days, 0) as streak_days
            FROM creators c
            LEFT JOIN pool_credits pc ON c.id = pc.creator_id
            WHERE c.id = %s
        """, (creator_id,))
        creator_info = cursor.fetchone()
        is_pro = creator_info and creator_info.get('subscription_tier') == 'pro'
        streak_days = creator_info.get('streak_days', 0) if creator_info else 0

        if not is_pro:
            # Base limit: 5, Streak bonus: +2 if 3+ day streak
            base_limit = 5
            streak_bonus = 2 if streak_days >= 3 else 0
            daily_limit = base_limit + streak_bonus

            cursor.execute("""
                SELECT COUNT(*) as today_count
                FROM pool_supports
                WHERE supporter_id = %s
                AND confirmed_at >= DATE_TRUNC('day', NOW())
            """, (creator_id,))
            daily_count = cursor.fetchone()
            if daily_count and daily_count['today_count'] >= daily_limit:
                conn.close()
                return jsonify({'error': 'Daily limit reached. Come back tomorrow!'}), 429

        # Create support record
        cursor.execute("""
            INSERT INTO pool_supports (supporter_id, target_id, platform, confirmed_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING id
        """, (creator_id, target_id, platform))
        support_id = cursor.fetchone()['id']

        # Update supporter's credits (+1 for giving support)
        cursor.execute("""
            UPDATE pool_credits
            SET balance = balance + 1,
                lifetime_earned = lifetime_earned + 1,
                last_support_at = NOW(),
                streak_days = CASE
                    WHEN last_support_at >= NOW() - INTERVAL '1 day' THEN streak_days + 1
                    ELSE 1
                END
            WHERE creator_id = %s
            RETURNING balance, streak_days
        """, (creator_id,))
        new_credits = cursor.fetchone()

        # Target spends 1 credit (they were shown in the pool)
        cursor.execute("""
            UPDATE pool_credits
            SET balance = GREATEST(balance - 1, 0),
                lifetime_spent = lifetime_spent + 1
            WHERE creator_id = %s
        """, (target_id,))

        # Get supporter's info for notification
        cursor.execute("""
            SELECT c.username, c.niche, c.social_links, c.image_profile, u.id as user_id
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        supporter_info = cursor.fetchone()

        # Get target's info for notification (including email)
        cursor.execute("""
            SELECT c.username, u.id as user_id, u.email, u.first_name
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (target_id,))
        target_info = cursor.fetchone()

        # Get total followers from pool for target
        cursor.execute("""
            SELECT COUNT(*) as total_followers
            FROM pool_supports
            WHERE target_id = %s AND confirmed_at IS NOT NULL
        """, (target_id,))
        total_followers_result = cursor.fetchone()
        total_followers = total_followers_result['total_followers'] if total_followers_result else 0

        conn.commit()
        conn.close()

        # Send real-time notification to target
        if supporter_info and target_info:
            send_pool_follow_notification(
                target_user_id=target_info['user_id'],
                supporter_username=supporter_info['username'] or 'Someone'
            )

            # Send email notification to target (async - don't block response)
            if target_info.get('email'):
                # Get supporter's social URL from their social_links
                supporter_social_url = None
                social_handles = parse_social_links(supporter_info.get('social_links'))
                if social_handles.get('instagram_handle'):
                    supporter_social_url = f"https://instagram.com/{social_handles['instagram_handle']}"
                elif social_handles.get('tiktok_handle'):
                    supporter_social_url = f"https://tiktok.com/@{social_handles['tiktok_handle']}"

                # Get supporter's niche (first item if array)
                supporter_niche = normalize_niche(supporter_info.get('niche'))

                # Send email in background thread to avoid blocking the response
                email_thread = threading.Thread(
                    target=send_pool_follower_email,
                    args=(
                        target_info['email'],
                        target_info.get('first_name'),
                        supporter_info['username'] or 'creator',
                        supporter_niche,
                        supporter_social_url,
                        total_followers,
                        supporter_info.get('image_profile')  # Add profile image
                    ),
                    daemon=True
                )
                email_thread.start()

        return jsonify({
            'success': True,
            'support_id': support_id,
            'new_balance': new_credits['balance'],
            'streak_days': new_credits['streak_days'],
            'message': '+1 credit earned!'
        })

    except Exception as e:
        print(f"[Pool] Error confirming support: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/skip', methods=['POST'])
def skip_creator():
    """
    Skip a creator in the pool (don't follow them).
    Records the skip but doesn't affect credits.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    data = request.get_json() or {}
    target_id = data.get('target_id')

    if not target_id:
        return jsonify({'error': 'target_id required'}), 400

    # For now, just acknowledge the skip - could track skips later for better matching
    return jsonify({'success': True, 'message': 'Skipped'})


@pool_bp.route('/visit', methods=['POST'])
def record_pool_visit():
    """Record when user visits the Pool tab (for badge calculation)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE creators
            SET pool_last_visit = NOW()
            WHERE id = %s
        """, (creator_id,))

        conn.commit()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"[Pool] Error recording visit: {e}")
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/badge', methods=['GET'])
def get_pool_badge():
    """Get badge count (new followers since last Pool visit)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT pool_last_visit FROM creators WHERE id = %s
        """, (creator_id,))
        creator = cursor.fetchone()
        last_visit = creator.get('pool_last_visit') if creator else None

        if last_visit:
            cursor.execute("""
                SELECT COUNT(*) as new_followers
                FROM pool_supports
                WHERE target_id = %s
                AND confirmed_at > %s
            """, (creator_id, last_visit))
        else:
            # Never visited - show all followers as badge
            cursor.execute("""
                SELECT COUNT(*) as new_followers
                FROM pool_supports
                WHERE target_id = %s
                AND confirmed_at IS NOT NULL
            """, (creator_id,))

        result = cursor.fetchone()
        conn.close()

        return jsonify({'badge': result['new_followers'] if result else 0})

    except Exception as e:
        print(f"[Pool] Error getting badge: {e}")
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/recent-activity', methods=['GET'])
def check_recent_activity():
    """Check if user has recent pool activity (for nudge visibility)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        # Return false for unauthenticated users (show the nudge)
        return jsonify({'has_recent_activity': False, 'recent_supports': 0})

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if they gave a support in the last 7 days
        cursor.execute("""
            SELECT COUNT(*) as recent_supports
            FROM pool_supports
            WHERE supporter_id = %s
            AND confirmed_at >= NOW() - INTERVAL '7 days'
        """, (creator_id,))
        result = cursor.fetchone()
        conn.close()

        has_recent = result['recent_supports'] > 0 if result else False

        return jsonify({
            'has_recent_activity': has_recent,
            'recent_supports': result['recent_supports'] if result else 0
        })

    except Exception as e:
        print(f"[Pool] Error checking activity: {e}")
        import traceback
        traceback.print_exc()
        # Return false on error (show the nudge)
        return jsonify({'has_recent_activity': False, 'recent_supports': 0})


@pool_bp.route('/active-members', methods=['GET'])
def get_active_pool_members():
    """Get active pool members for social proof banner"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creators who gave a support in the last 7 days
        cursor.execute("""
            SELECT DISTINCT c.id, c.username, c.username as display_name, c.image_profile as profile_image_url
            FROM creators c
            JOIN pool_supports ps ON ps.supporter_id = c.id
            WHERE ps.confirmed_at >= NOW() - INTERVAL '7 days'
            ORDER BY RANDOM()
            LIMIT 5
        """)
        members = cursor.fetchall()

        # If no recent activity, get any creators with published kits as fallback
        if not members:
            cursor.execute("""
                SELECT c.id, c.username, c.username as display_name, c.image_profile as profile_image_url
                FROM creators c
                WHERE c.kit_published = true
                AND c.image_profile IS NOT NULL
                ORDER BY RANDOM()
                LIMIT 5
            """)
            members = cursor.fetchall()

        conn.close()

        return jsonify({'members': members or [], 'count': len(members) if members else 0})

    except Exception as e:
        print(f"[Pool] Error getting active members: {e}")
        import traceback
        traceback.print_exc()
        # Return empty array instead of error to prevent frontend issues
        return jsonify({'members': [], 'count': 0})


@pool_bp.route('/stats', methods=['GET'])
def get_pool_stats():
    """Get user's pool statistics for their profile"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Total followers received from pool
        cursor.execute("""
            SELECT COUNT(*) as total_received
            FROM pool_supports
            WHERE target_id = %s AND confirmed_at IS NOT NULL
        """, (creator_id,))
        received = cursor.fetchone()

        # This month's followers
        cursor.execute("""
            SELECT COUNT(*) as this_month
            FROM pool_supports
            WHERE target_id = %s
            AND confirmed_at >= DATE_TRUNC('month', NOW())
        """, (creator_id,))
        this_month = cursor.fetchone()

        conn.close()

        return jsonify({
            'total_followers_received': received['total_received'] if received else 0,
            'followers_this_month': this_month['this_month'] if this_month else 0
        })

    except Exception as e:
        print(f"[Pool] Error getting stats: {e}")
        return jsonify({'error': str(e)}), 500


@pool_bp.route('/supporters', methods=['GET'])
def get_my_supporters():
    """Get list of creators who supported (followed) the current user this week"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get supporters from the last 7 days
        cursor.execute("""
            SELECT
                c.id,
                c.username,
                c.username as display_name,
                c.image_profile as profile_image_url,
                c.niche,
                ps.confirmed_at,
                ps.platform
            FROM pool_supports ps
            JOIN creators c ON c.id = ps.supporter_id
            WHERE ps.target_id = %s
            AND ps.confirmed_at >= NOW() - INTERVAL '7 days'
            ORDER BY ps.confirmed_at DESC
            LIMIT 20
        """, (creator_id,))
        supporters = cursor.fetchall()
        conn.close()

        # Convert datetime to ISO string for JSON serialization
        for s in supporters:
            if s.get('confirmed_at'):
                s['confirmed_at'] = s['confirmed_at'].isoformat()

        return jsonify({
            'supporters': supporters,
            'count': len(supporters)
        })

    except Exception as e:
        print(f"[Pool] Error getting supporters: {e}")
        return jsonify({'error': str(e)}), 500
