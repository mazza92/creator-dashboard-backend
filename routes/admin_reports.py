"""
Admin Reports API Routes
KPI Dashboard for tracking creator usage and monetization metrics
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


admin_reports_bp = Blueprint('admin_reports', __name__, url_prefix='/api/admin/reports')


# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================

def admin_required(f):
    """Require admin authentication via X-Admin-Token header"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)

        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'Authentication required'}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT email FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            conn.close()

            if not user or user.get('email', '').lower() != 'team@newcollab.co':
                return jsonify({'error': 'Admin access required'}), 403

        except Exception as e:
            return jsonify({'error': str(e)}), 500

        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# OVERVIEW ENDPOINT - Main KPIs
# ============================================================================

@admin_reports_bp.route('/overview', methods=['GET'])
@admin_required
def get_overview():
    """
    Get main KPI overview stats

    Returns:
        {
            "total_users": 500,
            "total_creators": 450,
            "active_today": 25,
            "active_7d": 80,
            "active_30d": 200,
            "total_unlocks": 5000,
            "total_pipeline_saves": 3000,
            "subscription_breakdown": {...}
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Total users and creators
        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM creators")
        total_creators = cursor.fetchone()['count']

        # Active users based on creator_pipeline table (since brand_unlocks may be empty)
        # Active today (last 24 hours)
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        active_today = cursor.fetchone()['count']

        # Active last 7 days
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """)
        active_7d = cursor.fetchone()['count']

        # Active last 30 days
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """)
        active_30d = cursor.fetchone()['count']

        # Total unlocks (from brand_unlocks if exists, otherwise 0)
        cursor.execute("SELECT COUNT(*) as count FROM brand_unlocks")
        total_unlocks = cursor.fetchone()['count']

        # Total pipeline saves
        cursor.execute("SELECT COUNT(*) as count FROM creator_pipeline")
        total_pipeline = cursor.fetchone()['count']

        # Subscription breakdown
        cursor.execute("""
            SELECT
                COALESCE(subscription_tier, 'free') as tier,
                COUNT(*) as count
            FROM creators
            GROUP BY COALESCE(subscription_tier, 'free')
            ORDER BY count DESC
        """)
        subscription_breakdown = {row['tier']: row['count'] for row in cursor.fetchall()}

        # Top active creators (by pipeline saves) with full KPIs
        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                c.bio,
                c.followers_count,
                c.engagement_rate,
                c.niche,
                c.platforms,
                c.total_posts,
                c.total_views,
                c.daily_unlocks_used,
                c.last_unlock_date,
                c.brands_saved_count,
                COALESCE(c.subscription_tier, 'free') as tier,
                COUNT(DISTINCT cp.id) as total_saves,
                COUNT(CASE WHEN cp.created_at >= NOW() - INTERVAL '7 days' THEN 1 END) as saves_7d,
                MAX(cp.created_at) as last_activity,
                COUNT(DISTINCT bu.id) as unlocks_count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            LEFT JOIN creator_pipeline cp ON cp.creator_id = c.id
            LEFT JOIN brand_unlocks bu ON bu.creator_id = c.id
            GROUP BY c.id, u.email, c.username, c.bio, c.followers_count, c.engagement_rate,
                     c.niche, c.platforms, c.total_posts, c.total_views, c.daily_unlocks_used,
                     c.last_unlock_date, c.brands_saved_count, c.subscription_tier
            ORDER BY saves_7d DESC, total_saves DESC, c.followers_count DESC NULLS LAST
            LIMIT 20
        """)
        top_creators = []
        for row in cursor.fetchall():
            top_creators.append({
                'creator_id': row['creator_id'],
                'email': row['email'],
                'username': row['username'],
                'bio': row['bio'][:100] + '...' if row['bio'] and len(row['bio']) > 100 else row['bio'],
                'followers_count': row['followers_count'],
                'engagement_rate': float(row['engagement_rate']) if row['engagement_rate'] else None,
                'niche': row['niche'],
                'platforms': row['platforms'],
                'total_posts': row['total_posts'],
                'total_views': row['total_views'],
                'daily_unlocks_used': row['daily_unlocks_used'],
                'last_unlock_date': str(row['last_unlock_date']) if row['last_unlock_date'] else None,
                'brands_saved_count': row['brands_saved_count'],
                'unlocks_count': row['unlocks_count'],
                'tier': row['tier'],
                'total_saves': row['total_saves'],
                'saves_7d': row['saves_7d'],
                'last_activity': str(row['last_activity']) if row['last_activity'] else None
            })

        conn.close()

        return jsonify({
            'total_users': total_users,
            'total_creators': total_creators,
            'active_today': active_today,
            'active_7d': active_7d,
            'active_30d': active_30d,
            'total_unlocks': total_unlocks,
            'total_pipeline_saves': total_pipeline,
            'subscription_breakdown': subscription_breakdown,
            'top_creators': top_creators
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# TODAY'S DETAILED STATS - For Daily Piloting
# ============================================================================

@admin_reports_bp.route('/today', methods=['GET'])
@admin_required
def get_today_stats():
    """
    Get detailed stats for today - designed for daily piloting

    Returns:
        {
            "date": "2026-04-03",
            "signups_today": 5,
            "active_users_today": 25,
            "unlocks_today": 120,
            "pipeline_saves_today": 45,
            "users_at_limit": 8,
            "users_near_limit": 12,
            "new_pro_subscriptions": 2,
            "top_brands_today": [...],
            "most_active_users_today": [...]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Use rolling 24-hour windows for better timezone handling
        # "Today" = last 24 hours, "Yesterday" = 24-48 hours ago
        today = datetime.now().date()

        # Signups in last 24 hours
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        signups_today = cursor.fetchone()['count']

        # Signups 24-48 hours ago (for comparison)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE created_at >= NOW() - INTERVAL '48 hours'
            AND created_at < NOW() - INTERVAL '24 hours'
        """)
        signups_yesterday = cursor.fetchone()['count']

        # Active users in last 24 hours (from creator_pipeline since brand_unlocks may be empty)
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        active_users_today = cursor.fetchone()['count']

        # Active users 24-48 hours ago
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '48 hours'
            AND created_at < NOW() - INTERVAL '24 hours'
        """)
        active_users_yesterday = cursor.fetchone()['count']

        # Total pipeline saves in last 24 hours (as "unlocks")
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        unlocks_today = cursor.fetchone()['count']

        # Total pipeline saves 24-48 hours ago
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '48 hours'
            AND created_at < NOW() - INTERVAL '24 hours'
        """)
        unlocks_yesterday = cursor.fetchone()['count']

        # Pipeline saves in last 24 hours
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        pipeline_saves_today = cursor.fetchone()['count']

        # Users at daily limit (check last_unlock_date = today in server time)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE (subscription_tier = 'free' OR subscription_tier IS NULL)
            AND daily_unlocks_used >= 5
            AND last_unlock_date >= CURRENT_DATE - INTERVAL '1 day'
        """)
        users_at_limit = cursor.fetchone()['count']

        # Users near limit (3-4 unlocks)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE (subscription_tier = 'free' OR subscription_tier IS NULL)
            AND daily_unlocks_used >= 3 AND daily_unlocks_used < 5
            AND last_unlock_date >= CURRENT_DATE - INTERVAL '1 day'
        """)
        users_near_limit = cursor.fetchone()['count']

        # New pro subscriptions in last 24 hours
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE subscription_tier IN ('pro', 'elite')
            AND subscription_started_at >= NOW() - INTERVAL '24 hours'
        """)
        new_pro_subscriptions = cursor.fetchone()['count']

        # Top 5 brands saved in last 24 hours (from creator_pipeline)
        cursor.execute("""
            SELECT
                pb.brand_name,
                pb.category,
                COUNT(*) as unlock_count
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY pb.id, pb.brand_name, pb.category
            ORDER BY unlock_count DESC
            LIMIT 5
        """)
        top_brands_today = cursor.fetchall()

        # Most active users in last 24 hours (with KPIs)
        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                c.followers_count,
                c.engagement_rate,
                COALESCE(c.subscription_tier, 'free') as tier,
                COUNT(*) as unlocks_today
            FROM creator_pipeline cp
            JOIN creators c ON cp.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE cp.created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY c.id, u.email, c.username, c.followers_count, c.engagement_rate, c.subscription_tier
            ORDER BY unlocks_today DESC
            LIMIT 10
        """)
        most_active_today = []
        for row in cursor.fetchall():
            most_active_today.append({
                'creator_id': row['creator_id'],
                'email': row['email'],
                'username': row['username'],
                'followers_count': row['followers_count'],
                'engagement_rate': float(row['engagement_rate']) if row['engagement_rate'] else None,
                'tier': row['tier'],
                'unlocks_today': row['unlocks_today']
            })

        conn.close()

        return jsonify({
            'date': str(today),
            'signups': {
                'today': signups_today,
                'yesterday': signups_yesterday,
                'change': signups_today - signups_yesterday
            },
            'active_users': {
                'today': active_users_today,
                'yesterday': active_users_yesterday,
                'change': active_users_today - active_users_yesterday
            },
            'unlocks': {
                'today': unlocks_today,
                'yesterday': unlocks_yesterday,
                'change': unlocks_today - unlocks_yesterday
            },
            'pipeline_saves_today': pipeline_saves_today,
            'quota': {
                'at_limit': users_at_limit,
                'near_limit': users_near_limit
            },
            'new_pro_subscriptions': new_pro_subscriptions,
            'top_brands_today': top_brands_today,
            'most_active_users_today': most_active_today
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# USER SIGNUPS OVER TIME
# ============================================================================

@admin_reports_bp.route('/signups', methods=['GET'])
@admin_required
def get_signups():
    """
    Get user signups over time (daily for last 30 days, weekly for older)

    Query params:
        days: Number of days to look back (default 30)

    Returns:
        {
            "daily": [{"date": "2026-01-01", "count": 5}, ...],
            "total_period": 150
        }
    """
    try:
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Daily signups
        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(*) as count
            FROM creators
            WHERE DATE(created_at) >= %s
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (start_date,))
        daily = cursor.fetchall()

        # Convert dates to strings
        daily_data = [{'date': str(row['date']), 'count': row['count']} for row in daily]

        # Total in period
        total_period = sum(row['count'] for row in daily_data)

        conn.close()

        return jsonify({
            'daily': daily_data,
            'total_period': total_period,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# DAILY ACTIVE USERS (DAU) OVER TIME
# ============================================================================

@admin_reports_bp.route('/dau', methods=['GET'])
@admin_required
def get_dau():
    """
    Get daily active users trend

    Query params:
        days: Number of days (default 30)

    Returns:
        {
            "daily": [{"date": "2026-01-01", "active_users": 25}, ...],
            "avg_dau": 20
        }
    """
    try:
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # DAU based on creator_pipeline activity (brand_unlocks may be empty)
        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(DISTINCT creator_id) as active_users
            FROM creator_pipeline
            WHERE DATE(created_at) >= %s
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (start_date,))
        daily = cursor.fetchall()

        daily_data = [{'date': str(row['date']), 'active_users': row['active_users']} for row in daily]

        # Calculate average DAU
        avg_dau = sum(row['active_users'] for row in daily_data) / len(daily_data) if daily_data else 0

        conn.close()

        return jsonify({
            'daily': daily_data,
            'avg_dau': round(avg_dau, 1),
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# PIPELINE FUNNEL
# ============================================================================

@admin_reports_bp.route('/funnel', methods=['GET'])
@admin_required
def get_funnel():
    """
    Get pipeline funnel stats

    Returns:
        {
            "stages": {
                "saved": 500,
                "pitched": 200,
                "responded": 50,
                "success": 20,
                "rejected": 30
            },
            "conversion_rates": {
                "saved_to_pitched": 40.0,
                "pitched_to_responded": 25.0,
                "responded_to_success": 40.0
            }
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Pipeline stage counts (using stage column)
        cursor.execute("""
            SELECT
                stage,
                COUNT(*) as count
            FROM creator_pipeline
            GROUP BY stage
        """)
        stages = {row['stage']: row['count'] for row in cursor.fetchall()}

        # Map 'interested' to 'saved' for clearer display
        if 'interested' in stages:
            stages['saved'] = stages.pop('interested')

        # Ensure all stages exist
        all_stages = ['saved', 'pitched', 'responded', 'success', 'rejected', 'archived']
        for stage in all_stages:
            if stage not in stages:
                stages[stage] = 0

        # Calculate conversion rates
        conversion_rates = {}
        if stages['saved'] > 0:
            conversion_rates['saved_to_pitched'] = round((stages['pitched'] / stages['saved']) * 100, 1)
        else:
            conversion_rates['saved_to_pitched'] = 0

        if stages['pitched'] > 0:
            conversion_rates['pitched_to_responded'] = round((stages['responded'] / stages['pitched']) * 100, 1)
        else:
            conversion_rates['pitched_to_responded'] = 0

        if stages['responded'] > 0:
            conversion_rates['responded_to_success'] = round((stages['success'] / stages['responded']) * 100, 1)
        else:
            conversion_rates['responded_to_success'] = 0

        conn.close()

        return jsonify({
            'stages': stages,
            'conversion_rates': conversion_rates
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# TOP USERS (Power Users)
# ============================================================================

@admin_reports_bp.route('/top-users', methods=['GET'])
@admin_required
def get_top_users():
    """
    Get top users by activity (saves and pitches)

    Query params:
        limit: Number of users (default 20)
        metric: 'saves', 'pitches', or 'all' (default 'all')
        days: Time period (default 30)

    Returns:
        {
            "users": [
                {"creator_id": 1, "email": "...", "saves": 50, "pitches": 20, "tier": "pro"},
                ...
            ]
        }
    """
    try:
        limit = int(request.args.get('limit', 20))
        metric = request.args.get('metric', 'all')
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get top users with saves and pitches from creator_pipeline
        cursor.execute("""
            SELECT
                cp.creator_id,
                u.email,
                c.username,
                COALESCE(c.subscription_tier, 'free') as tier,
                COUNT(*) as saves,
                COUNT(cp.pitched_at) as pitches,
                (
                    SELECT COUNT(*) FROM creator_pipeline cp2
                    WHERE cp2.creator_id = c.id
                    AND cp2.pitched_at >= DATE_TRUNC('week', NOW())
                ) as pitches_this_week,
                MAX(COALESCE(cp.pitched_at, cp.created_at)) as last_activity
            FROM creator_pipeline cp
            JOIN creators c ON cp.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE DATE(cp.created_at) >= %s
            GROUP BY cp.creator_id, u.email, c.username, c.subscription_tier, c.id
            ORDER BY
                CASE WHEN %s = 'saves' THEN COUNT(*) END DESC,
                CASE WHEN %s = 'pitches' THEN COUNT(cp.pitched_at) END DESC,
                CASE WHEN %s = 'all' THEN COUNT(*) + COUNT(cp.pitched_at) * 2 END DESC
            LIMIT %s
        """, (start_date, metric, metric, metric, limit))

        users = cursor.fetchall()

        # Convert datetime to string
        users_data = []
        for user in users:
            users_data.append({
                'creator_id': user['creator_id'],
                'email': user['email'],
                'username': user['username'],
                'tier': user['tier'],
                'saves': user['saves'],
                'pitches': user['pitches'],
                'pitches_this_week': user['pitches_this_week'],
                'last_activity': str(user['last_activity']) if user['last_activity'] else None
            })

        conn.close()

        return jsonify({
            'users': users_data,
            'metric': metric,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# QUOTA HITS (Monetization Signal)
# ============================================================================

@admin_reports_bp.route('/quota-hits', methods=['GET'])
@admin_required
def get_quota_hits():
    """
    Get users hitting daily unlock limits (monetization signal)

    Returns:
        {
            "users_at_limit": 15,
            "users_near_limit": 30,
            "recent_limit_hits": [...]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        today = datetime.now().date()

        # Users at daily limit (5 unlocks for free tier)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE (subscription_tier = 'free' OR subscription_tier IS NULL)
            AND daily_unlocks_used >= 5
            AND last_unlock_date = %s
        """, (today,))
        users_at_limit = cursor.fetchone()['count']

        # Users near limit (3-4 unlocks)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creators
            WHERE (subscription_tier = 'free' OR subscription_tier IS NULL)
            AND daily_unlocks_used >= 3 AND daily_unlocks_used < 5
            AND last_unlock_date = %s
        """, (today,))
        users_near_limit = cursor.fetchone()['count']

        # Recent users who hit limit (last 7 days)
        week_ago = today - timedelta(days=7)
        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                c.daily_unlocks_used,
                c.last_unlock_date
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
            AND c.daily_unlocks_used >= 5
            AND c.last_unlock_date >= %s
            ORDER BY c.last_unlock_date DESC
            LIMIT 50
        """, (week_ago,))
        recent_hits = cursor.fetchall()

        recent_data = []
        for hit in recent_hits:
            recent_data.append({
                'creator_id': hit['creator_id'],
                'email': hit['email'],
                'username': hit['username'],
                'unlocks_used': hit['daily_unlocks_used'],
                'date': str(hit['last_unlock_date'])
            })

        conn.close()

        return jsonify({
            'users_at_limit_today': users_at_limit,
            'users_near_limit_today': users_near_limit,
            'recent_limit_hits': recent_data
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ACTIVITY HEATMAP DATA
# ============================================================================

@admin_reports_bp.route('/activity-heatmap', methods=['GET'])
@admin_required
def get_activity_heatmap():
    """
    Get activity data for heatmap visualization

    Query params:
        days: Number of days (default 90)

    Returns:
        {
            "data": [{"date": "2026-01-01", "count": 50}, ...]
        }
    """
    try:
        days = int(request.args.get('days', 90))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                DATE(unlocked_at) as date,
                COUNT(*) as count
            FROM brand_unlocks
            WHERE DATE(unlocked_at) >= %s
            GROUP BY DATE(unlocked_at)
            ORDER BY date ASC
        """, (start_date,))

        data = [{'date': str(row['date']), 'count': row['count']} for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'data': data,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# RETENTION COHORTS
# ============================================================================

@admin_reports_bp.route('/retention', methods=['GET'])
@admin_required
def get_retention():
    """
    Get retention cohort data based on creator_pipeline activity (saves + pitches)

    Returns:
        {
            "cohorts": [
                {
                    "signup_week": "2026-W01",
                    "total_users": 50,
                    "week_0": 30,
                    "week_1": 20,
                    "week_2": 15,
                    "week_3": 12,
                    "week_4": 10
                },
                ...
            ]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get signup cohorts from last 10 weeks using creator_pipeline activity
        cursor.execute("""
            WITH signup_cohorts AS (
                SELECT
                    id as creator_id,
                    DATE_TRUNC('week', created_at) as signup_week
                FROM creators
                WHERE created_at >= NOW() - INTERVAL '10 weeks'
            ),
            activity AS (
                -- Combine saves (created_at) and pitches (pitched_at) as activity signals
                SELECT creator_id, DATE_TRUNC('week', created_at) as activity_week
                FROM creator_pipeline
                WHERE created_at >= NOW() - INTERVAL '14 weeks'
                UNION
                SELECT creator_id, DATE_TRUNC('week', pitched_at) as activity_week
                FROM creator_pipeline
                WHERE pitched_at IS NOT NULL AND pitched_at >= NOW() - INTERVAL '14 weeks'
            )
            SELECT
                sc.signup_week,
                COUNT(DISTINCT sc.creator_id) as total_users,
                COUNT(DISTINCT CASE WHEN a.activity_week = sc.signup_week THEN sc.creator_id END) as week_0,
                COUNT(DISTINCT CASE WHEN a.activity_week = sc.signup_week + INTERVAL '1 week' THEN sc.creator_id END) as week_1,
                COUNT(DISTINCT CASE WHEN a.activity_week = sc.signup_week + INTERVAL '2 weeks' THEN sc.creator_id END) as week_2,
                COUNT(DISTINCT CASE WHEN a.activity_week = sc.signup_week + INTERVAL '3 weeks' THEN sc.creator_id END) as week_3,
                COUNT(DISTINCT CASE WHEN a.activity_week = sc.signup_week + INTERVAL '4 weeks' THEN sc.creator_id END) as week_4
            FROM signup_cohorts sc
            LEFT JOIN activity a ON sc.creator_id = a.creator_id
            GROUP BY sc.signup_week
            ORDER BY sc.signup_week DESC
        """)

        cohorts = []
        for row in cursor.fetchall():
            total = row['total_users'] or 1  # Avoid division by zero
            cohorts.append({
                'signup_week': str(row['signup_week'].date()) if row['signup_week'] else None,
                'total_users': row['total_users'],
                'week_0': row['week_0'],
                'week_0_pct': round((row['week_0'] / total) * 100, 1),
                'week_1': row['week_1'],
                'week_1_pct': round((row['week_1'] / total) * 100, 1),
                'week_2': row['week_2'],
                'week_2_pct': round((row['week_2'] / total) * 100, 1),
                'week_3': row['week_3'],
                'week_3_pct': round((row['week_3'] / total) * 100, 1),
                'week_4': row['week_4'],
                'week_4_pct': round((row['week_4'] / total) * 100, 1)
            })

        conn.close()

        return jsonify({
            'cohorts': cohorts
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# UNLOCKS BY BRAND (Most Popular Brands)
# ============================================================================

@admin_reports_bp.route('/popular-brands', methods=['GET'])
@admin_required
def get_popular_brands():
    """
    Get most popular brands by saves and pitches from creator_pipeline

    Query params:
        limit: Number of brands (default 20)
        days: Time period (default 30)

    Returns:
        {
            "brands": [
                {"brand_id": 1, "brand_name": "...", "save_count": 50, "pitch_count": 20},
                ...
            ]
        }
    """
    try:
        limit = int(request.args.get('limit', 20))
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get popular brands from creator_pipeline (saves + pitches)
        cursor.execute("""
            SELECT
                cp.brand_id,
                pb.brand_name,
                pb.category,
                COUNT(*) as save_count,
                COUNT(cp.pitched_at) as pitch_count,
                COUNT(DISTINCT cp.creator_id) as unique_users
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE DATE(cp.created_at) >= %s
            GROUP BY cp.brand_id, pb.brand_name, pb.category
            ORDER BY save_count DESC
            LIMIT %s
        """, (start_date, limit))

        brands = cursor.fetchall()

        conn.close()

        return jsonify({
            'brands': brands,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# BRAND ANALYTICS - Comprehensive Brand Performance
# ============================================================================

@admin_reports_bp.route('/brand-analytics', methods=['GET'])
@admin_required
def get_brand_analytics():
    """
    Get comprehensive brand analytics

    Query params:
        days: Time period (default 30)
        limit: Number of top brands (default 20)

    Returns:
        {
            "overview": {
                "total_brands": 500,
                "brands_with_form": 200,
                "brands_with_email": 350,
                "total_unlocks": 5000
            },
            "top_unlocked_brands": [...],
            "top_brands_with_form": [...],
            "top_brands_email_only": [...],
            "unlocks_by_category": {...},
            "recent_unlocks": [...]
        }
    """
    try:
        days = int(request.args.get('days', 30))
        limit = int(request.args.get('limit', 20))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Overview stats
        cursor.execute("SELECT COUNT(*) as count FROM pr_brands WHERE COALESCE(status, 'published') = 'published'")
        total_brands = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM pr_brands
            WHERE COALESCE(status, 'published') = 'published'
            AND application_form_url IS NOT NULL AND application_form_url != ''
        """)
        brands_with_form = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM pr_brands
            WHERE COALESCE(status, 'published') = 'published'
            AND contact_email IS NOT NULL AND contact_email != ''
        """)
        brands_with_email = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM brand_unlocks
            WHERE DATE(unlocked_at) >= %s
        """, (start_date,))
        total_unlocks_period = cursor.fetchone()['count']

        # Top unlocked brands (all)
        cursor.execute("""
            SELECT
                bu.brand_id,
                pb.brand_name,
                pb.slug,
                pb.category,
                pb.application_form_url,
                pb.contact_email,
                CASE
                    WHEN pb.application_form_url IS NOT NULL AND pb.application_form_url != '' THEN 'form'
                    WHEN pb.contact_email IS NOT NULL AND pb.contact_email != '' THEN 'email'
                    ELSE 'none'
                END as contact_type,
                COUNT(*) as unlock_count,
                COUNT(DISTINCT bu.creator_id) as unique_users,
                MAX(bu.unlocked_at) as last_unlock
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE DATE(bu.unlocked_at) >= %s
            GROUP BY bu.brand_id, pb.brand_name, pb.slug, pb.category,
                     pb.application_form_url, pb.contact_email
            ORDER BY unlock_count DESC
            LIMIT %s
        """, (start_date, limit))
        top_unlocked = cursor.fetchall()

        # Top brands with application forms
        cursor.execute("""
            SELECT
                bu.brand_id,
                pb.brand_name,
                pb.slug,
                pb.category,
                pb.application_form_url,
                COUNT(*) as unlock_count,
                COUNT(DISTINCT bu.creator_id) as unique_users
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE DATE(bu.unlocked_at) >= %s
            AND pb.application_form_url IS NOT NULL
            AND pb.application_form_url != ''
            GROUP BY bu.brand_id, pb.brand_name, pb.slug, pb.category, pb.application_form_url
            ORDER BY unlock_count DESC
            LIMIT %s
        """, (start_date, limit))
        top_with_form = cursor.fetchall()

        # Top brands with email only (no form)
        cursor.execute("""
            SELECT
                bu.brand_id,
                pb.brand_name,
                pb.slug,
                pb.category,
                pb.contact_email,
                COUNT(*) as unlock_count,
                COUNT(DISTINCT bu.creator_id) as unique_users
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE DATE(bu.unlocked_at) >= %s
            AND (pb.application_form_url IS NULL OR pb.application_form_url = '')
            AND pb.contact_email IS NOT NULL
            AND pb.contact_email != ''
            GROUP BY bu.brand_id, pb.brand_name, pb.slug, pb.category, pb.contact_email
            ORDER BY unlock_count DESC
            LIMIT %s
        """, (start_date, limit))
        top_email_only = cursor.fetchall()

        # Unlocks by category
        cursor.execute("""
            SELECT
                pb.category,
                COUNT(*) as unlock_count,
                COUNT(DISTINCT bu.creator_id) as unique_users,
                COUNT(DISTINCT bu.brand_id) as brands_unlocked
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE DATE(bu.unlocked_at) >= %s
            GROUP BY pb.category
            ORDER BY unlock_count DESC
        """, (start_date,))
        unlocks_by_category = cursor.fetchall()

        # Recent unlocks (last 50)
        cursor.execute("""
            SELECT
                bu.id,
                bu.unlocked_at,
                pb.brand_name,
                pb.category,
                u.email as user_email,
                c.username
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            JOIN creators c ON bu.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            ORDER BY bu.unlocked_at DESC
            LIMIT 50
        """)
        recent_unlocks = cursor.fetchall()

        # Convert timestamps
        for unlock in recent_unlocks:
            unlock['unlocked_at'] = str(unlock['unlocked_at'])

        for brand in top_unlocked:
            brand['last_unlock'] = str(brand['last_unlock']) if brand['last_unlock'] else None

        conn.close()

        return jsonify({
            'overview': {
                'total_brands': total_brands,
                'brands_with_form': brands_with_form,
                'brands_with_email': brands_with_email,
                'total_unlocks_period': total_unlocks_period
            },
            'top_unlocked_brands': top_unlocked,
            'top_brands_with_form': top_with_form,
            'top_brands_email_only': top_email_only,
            'unlocks_by_category': unlocks_by_category,
            'recent_unlocks': recent_unlocks,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# PITCH ANALYTICS - AI Pitch Credit Usage KPIs
# ============================================================================

@admin_reports_bp.route('/pitch-analytics', methods=['GET'])
@admin_required
def get_pitch_analytics():
    """
    Get comprehensive AI pitch/contact analytics

    Query params:
        days: Time period (default 30)

    Returns:
        {
            "today": {
                "pitches_today": 15,
                "unique_users_today": 8,
                "change": 3,
                "top_users": [...],
                "top_brands": [...]
            },
            "quota": {
                "at_limit": 5
            },
            "period": {
                "total_pitches": 500
            },
            "daily": [...],
            "top_users": [...],
            "top_brands": [...],
            "recent_pitches": [...],
            "users_at_limit": [...]
        }
    """
    try:
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ================== TODAY'S STATS ==================

        # Pitches in last 24 hours (pitched_at is set when user contacts a brand)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE pitched_at >= NOW() - INTERVAL '24 hours'
        """)
        pitches_today = cursor.fetchone()['count']

        # Pitches 24-48 hours ago (for comparison)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE pitched_at >= NOW() - INTERVAL '48 hours'
            AND pitched_at < NOW() - INTERVAL '24 hours'
        """)
        pitches_yesterday = cursor.fetchone()['count']

        # Unique users who pitched in last 24 hours
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE pitched_at >= NOW() - INTERVAL '24 hours'
        """)
        unique_users_today = cursor.fetchone()['count']

        # Top pitch users today
        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                COALESCE(c.subscription_tier, 'free') as tier,
                COUNT(*) as pitches_today,
                (
                    SELECT COUNT(*) FROM creator_pipeline cp2
                    WHERE cp2.creator_id = c.id
                    AND cp2.pitched_at >= DATE_TRUNC('week', NOW())
                ) as pitches_this_week
            FROM creator_pipeline cp
            JOIN creators c ON cp.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE cp.pitched_at >= NOW() - INTERVAL '24 hours'
            GROUP BY c.id, u.email, c.username, c.subscription_tier
            ORDER BY pitches_today DESC
            LIMIT 10
        """)
        top_users_today = cursor.fetchall()

        # Top pitched brands today
        cursor.execute("""
            SELECT
                pb.id as brand_id,
                pb.brand_name,
                pb.category,
                CASE
                    WHEN pb.contact_email IS NOT NULL AND pb.contact_email != '' THEN 'email'
                    WHEN pb.application_form_url IS NOT NULL AND pb.application_form_url != '' THEN 'form'
                    ELSE 'unknown'
                END as contact_type,
                COUNT(*) as pitch_count
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.pitched_at >= NOW() - INTERVAL '24 hours'
            GROUP BY pb.id, pb.brand_name, pb.category, pb.contact_email, pb.application_form_url
            ORDER BY pitch_count DESC
            LIMIT 10
        """)
        top_brands_today = cursor.fetchall()

        # ================== QUOTA / LIMIT STATS ==================

        # Free users at weekly pitch limit (3 pitches/week)
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
            AND (
                SELECT COUNT(*) FROM creator_pipeline cp
                WHERE cp.creator_id = c.id
                AND cp.pitched_at >= DATE_TRUNC('week', NOW())
            ) >= 3
        """)
        users_at_limit = cursor.fetchone()['count']

        # ================== PERIOD STATS ==================

        # Total pitches in period
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM creator_pipeline
            WHERE pitched_at >= %s
        """, (start_date,))
        total_pitches_period = cursor.fetchone()['count']

        # ================== DAILY TREND ==================

        cursor.execute("""
            SELECT
                DATE(pitched_at) as date,
                COUNT(*) as pitch_count,
                COUNT(DISTINCT creator_id) as unique_users
            FROM creator_pipeline
            WHERE DATE(pitched_at) >= %s
            AND pitched_at IS NOT NULL
            GROUP BY DATE(pitched_at)
            ORDER BY date ASC
        """, (start_date,))
        daily_data = [
            {
                'date': str(row['date']),
                'pitch_count': row['pitch_count'],
                'unique_users': row['unique_users']
            }
            for row in cursor.fetchall()
        ]

        # ================== TOP USERS (PERIOD) ==================

        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                COALESCE(c.subscription_tier, 'free') as tier,
                (
                    SELECT COUNT(*) FROM creator_pipeline cp2
                    WHERE cp2.creator_id = c.id
                    AND cp2.pitched_at >= DATE_TRUNC('week', NOW())
                ) as pitches_this_week,
                COUNT(*) as total_pitches,
                MAX(cp.pitched_at) as last_pitch_at
            FROM creator_pipeline cp
            JOIN creators c ON cp.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE cp.pitched_at >= %s
            GROUP BY c.id, u.email, c.username, c.subscription_tier
            ORDER BY total_pitches DESC
            LIMIT 20
        """, (start_date,))
        top_users_period = []
        for row in cursor.fetchall():
            top_users_period.append({
                'creator_id': row['creator_id'],
                'email': row['email'],
                'username': row['username'],
                'tier': row['tier'],
                'pitches_this_week': row['pitches_this_week'],
                'total_pitches': row['total_pitches'],
                'last_pitch_at': str(row['last_pitch_at']) if row['last_pitch_at'] else None
            })

        # ================== TOP BRANDS (PERIOD) ==================

        cursor.execute("""
            SELECT
                pb.id as brand_id,
                pb.brand_name,
                pb.category,
                CASE
                    WHEN pb.contact_email IS NOT NULL AND pb.contact_email != '' THEN 'email'
                    WHEN pb.application_form_url IS NOT NULL AND pb.application_form_url != '' THEN 'form'
                    ELSE 'unknown'
                END as contact_type,
                COUNT(*) as pitch_count,
                COUNT(DISTINCT cp.creator_id) as unique_users
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.pitched_at >= %s
            GROUP BY pb.id, pb.brand_name, pb.category, pb.contact_email, pb.application_form_url
            ORDER BY pitch_count DESC
            LIMIT 20
        """, (start_date,))
        top_brands_period = cursor.fetchall()

        # ================== RECENT PITCHES ==================

        cursor.execute("""
            SELECT
                cp.id,
                cp.pitched_at,
                pb.brand_name,
                CASE
                    WHEN pb.contact_email IS NOT NULL AND pb.contact_email != '' THEN 'email'
                    WHEN pb.application_form_url IS NOT NULL AND pb.application_form_url != '' THEN 'form'
                    ELSE 'unknown'
                END as contact_type,
                u.email,
                c.username
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            JOIN creators c ON cp.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE cp.pitched_at IS NOT NULL
            ORDER BY cp.pitched_at DESC
            LIMIT 20
        """)
        recent_pitches = []
        for row in cursor.fetchall():
            recent_pitches.append({
                'id': row['id'],
                'pitched_at': str(row['pitched_at']),
                'brand_name': row['brand_name'],
                'contact_type': row['contact_type'],
                'email': row['email'],
                'username': row['username']
            })

        # ================== USERS AT LIMIT ==================

        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                (
                    SELECT COUNT(*) FROM creator_pipeline cp2
                    WHERE cp2.creator_id = c.id
                    AND cp2.pitched_at >= DATE_TRUNC('week', NOW())
                ) as pitches_this_week,
                (
                    SELECT COUNT(*) FROM creator_pipeline cp3
                    WHERE cp3.creator_id = c.id
                    AND cp3.pitched_at IS NOT NULL
                ) as total_pitches,
                (
                    SELECT MAX(cp4.pitched_at) FROM creator_pipeline cp4
                    WHERE cp4.creator_id = c.id
                ) as last_pitch_at
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
            AND (
                SELECT COUNT(*) FROM creator_pipeline cp
                WHERE cp.creator_id = c.id
                AND cp.pitched_at >= DATE_TRUNC('week', NOW())
            ) >= 3
            ORDER BY last_pitch_at DESC
            LIMIT 20
        """)
        users_at_limit_list = []
        for row in cursor.fetchall():
            users_at_limit_list.append({
                'creator_id': row['creator_id'],
                'email': row['email'],
                'username': row['username'],
                'pitches_this_week': row['pitches_this_week'],
                'total_pitches': row['total_pitches'],
                'last_pitch_at': str(row['last_pitch_at']) if row['last_pitch_at'] else None
            })

        conn.close()

        return jsonify({
            'today': {
                'pitches_today': pitches_today,
                'unique_users_today': unique_users_today,
                'change': pitches_today - pitches_yesterday,
                'top_users': top_users_today,
                'top_brands': top_brands_today
            },
            'quota': {
                'at_limit': users_at_limit
            },
            'period': {
                'total_pitches': total_pitches_period
            },
            'daily': daily_data,
            'top_users': top_users_period,
            'top_brands': top_brands_period,
            'recent_pitches': recent_pitches,
            'users_at_limit': users_at_limit_list,
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENGAGEMENT ANALYTICS - User Journey & Activation Metrics
# ============================================================================

@admin_reports_bp.route('/engagement', methods=['GET'])
@admin_required
def get_engagement():
    """
    Get user engagement and activation analytics

    Query params:
        days: Time period (default 30)

    Returns:
        {
            "activation": {
                "signups": 100,
                "saved_brand": 60,
                "pitched_brand": 30,
                "activated_rate": 60%
            },
            "engagement": {
                "avg_saves_per_user": 5.2,
                "avg_pitches_per_user": 2.1,
                "power_users": 15
            },
            "conversion_funnel": {...},
            "daily_engagement": [...],
            "user_segments": {...}
        }
    """
    try:
        days = int(request.args.get('days', 30))
        start_date = datetime.now().date() - timedelta(days=days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ================== ACTIVATION METRICS ==================

        # Total signups in period
        cursor.execute("""
            SELECT COUNT(*) as count FROM creators
            WHERE DATE(created_at) >= %s
        """, (start_date,))
        total_signups = cursor.fetchone()['count']

        # Users who saved at least 1 brand
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN creator_pipeline cp ON c.id = cp.creator_id
            WHERE DATE(c.created_at) >= %s
        """, (start_date,))
        users_saved = cursor.fetchone()['count']

        # Users who pitched at least 1 brand
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN creator_pipeline cp ON c.id = cp.creator_id
            WHERE DATE(c.created_at) >= %s
            AND cp.pitched_at IS NOT NULL
        """, (start_date,))
        users_pitched = cursor.fetchone()['count']

        # ================== ENGAGEMENT METRICS ==================

        # Average saves per active user
        cursor.execute("""
            SELECT
                COUNT(*) as total_saves,
                COUNT(DISTINCT creator_id) as active_users
            FROM creator_pipeline
            WHERE DATE(created_at) >= %s
        """, (start_date,))
        save_stats = cursor.fetchone()
        avg_saves = round(save_stats['total_saves'] / max(save_stats['active_users'], 1), 1)

        # Average pitches per pitching user
        cursor.execute("""
            SELECT
                COUNT(*) as total_pitches,
                COUNT(DISTINCT creator_id) as pitching_users
            FROM creator_pipeline
            WHERE pitched_at IS NOT NULL
            AND DATE(pitched_at) >= %s
        """, (start_date,))
        pitch_stats = cursor.fetchone()
        avg_pitches = round(pitch_stats['total_pitches'] / max(pitch_stats['pitching_users'], 1), 1)

        # Power users (5+ pitches in period)
        cursor.execute("""
            SELECT COUNT(*) as count FROM (
                SELECT creator_id, COUNT(*) as pitch_count
                FROM creator_pipeline
                WHERE pitched_at IS NOT NULL AND DATE(pitched_at) >= %s
                GROUP BY creator_id
                HAVING COUNT(*) >= 5
            ) as power_users
        """, (start_date,))
        power_users = cursor.fetchone()['count']

        # ================== USER SEGMENTS ==================

        # Segment users by activity level
        cursor.execute("""
            WITH user_activity AS (
                SELECT
                    c.id,
                    COALESCE(c.subscription_tier, 'free') as tier,
                    COUNT(cp.id) as save_count,
                    COUNT(cp.pitched_at) as pitch_count
                FROM creators c
                LEFT JOIN creator_pipeline cp ON c.id = cp.creator_id
                    AND DATE(cp.created_at) >= %s
                WHERE DATE(c.created_at) >= %s
                GROUP BY c.id, c.subscription_tier
            )
            SELECT
                CASE
                    WHEN pitch_count >= 5 THEN 'power_user'
                    WHEN pitch_count >= 1 THEN 'engaged'
                    WHEN save_count >= 1 THEN 'exploring'
                    ELSE 'inactive'
                END as segment,
                COUNT(*) as count,
                COUNT(CASE WHEN tier IN ('pro', 'elite') THEN 1 END) as paid
            FROM user_activity
            GROUP BY segment
        """, (start_date, start_date))
        segments = {row['segment']: {'count': row['count'], 'paid': row['paid']} for row in cursor.fetchall()}

        # ================== DAILY ENGAGEMENT TREND ==================

        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(DISTINCT creator_id) as active_users,
                COUNT(*) as total_saves,
                COUNT(pitched_at) as total_pitches
            FROM creator_pipeline
            WHERE DATE(created_at) >= %s
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (start_date,))
        daily_engagement = [
            {
                'date': str(row['date']),
                'active_users': row['active_users'],
                'saves': row['total_saves'],
                'pitches': row['total_pitches']
            }
            for row in cursor.fetchall()
        ]

        # ================== CONVERSION FUNNEL ==================

        cursor.execute("""
            SELECT
                stage,
                COUNT(*) as count
            FROM creator_pipeline
            WHERE DATE(created_at) >= %s
            GROUP BY stage
        """, (start_date,))
        stages = {row['stage']: row['count'] for row in cursor.fetchall()}

        # Normalize stage names
        if 'interested' in stages:
            stages['saved'] = stages.pop('interested')

        # Time to first action
        cursor.execute("""
            SELECT
                AVG(EXTRACT(EPOCH FROM (first_save - signup)) / 3600) as avg_hours_to_save,
                AVG(EXTRACT(EPOCH FROM (first_pitch - signup)) / 3600) as avg_hours_to_pitch
            FROM (
                SELECT
                    c.id,
                    c.created_at as signup,
                    MIN(cp.created_at) as first_save,
                    MIN(cp.pitched_at) as first_pitch
                FROM creators c
                LEFT JOIN creator_pipeline cp ON c.id = cp.creator_id
                WHERE DATE(c.created_at) >= %s
                GROUP BY c.id, c.created_at
            ) as user_times
        """, (start_date,))
        time_to_action = cursor.fetchone()

        conn.close()

        activation_rate = round((users_saved / max(total_signups, 1)) * 100, 1)
        pitch_rate = round((users_pitched / max(total_signups, 1)) * 100, 1)

        return jsonify({
            'activation': {
                'signups': total_signups,
                'saved_brand': users_saved,
                'pitched_brand': users_pitched,
                'activation_rate': activation_rate,
                'pitch_rate': pitch_rate
            },
            'engagement': {
                'avg_saves_per_user': avg_saves,
                'avg_pitches_per_user': avg_pitches,
                'power_users': power_users,
                'total_active_users': save_stats['active_users'],
                'total_pitching_users': pitch_stats['pitching_users']
            },
            'user_segments': segments,
            'daily_engagement': daily_engagement,
            'conversion_funnel': stages,
            'time_to_action': {
                'avg_hours_to_save': round(time_to_action['avg_hours_to_save'] or 0, 1),
                'avg_hours_to_pitch': round(time_to_action['avg_hours_to_pitch'] or 0, 1)
            },
            'days': days
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# FOUNDER DASHBOARD - Consolidated KPIs for adminsimple.html
# ============================================================================

@admin_reports_bp.route('/founder-dashboard', methods=['GET'])
@admin_required
def get_founder_dashboard():
    """
    Get consolidated founder dashboard data matching adminsimple.html design

    Query Params:
        period: '7d' (default), '14d', '30d', '90d', '180d', 'all'
        start_date: Optional custom start date (YYYY-MM-DD)
        end_date: Optional custom end date (YYYY-MM-DD)

    Returns:
        {
            "mrr": {...},
            "at_limit_users": [...],
            "health": {...},
            "funnel": {...},
            "this_month": {...},
            "period": {...}  # info about selected period
        }
    """
    try:
        # Parse period parameter
        period = request.args.get('period', '7d')
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        # Calculate date range based on period
        now = datetime.now()
        end_date = now

        if start_date_str and end_date_str:
            # Custom date range
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            period_label = f"{start_date_str} to {end_date_str}"
            period_days = (end_date - start_date).days + 1
        elif period == '14d':
            start_date = now - timedelta(days=14)
            period_label = 'Last 14 days'
            period_days = 14
        elif period == '30d':
            start_date = now - timedelta(days=30)
            period_label = 'Last 30 days'
            period_days = 30
        elif period == '90d':
            start_date = now - timedelta(days=90)
            period_label = 'Last 90 days'
            period_days = 90
        elif period == '180d':
            start_date = now - timedelta(days=180)
            period_label = 'Last 180 days'
            period_days = 180
        elif period == 'all':
            start_date = datetime(2020, 1, 1)  # Far back enough
            period_label = 'All time'
            period_days = (now - start_date).days
        else:  # Default to 7d
            start_date = now - timedelta(days=7)
            period_label = 'Last 7 days'
            period_days = 7

        # For comparison, calculate previous period
        prev_end_date = start_date
        prev_start_date = start_date - timedelta(days=period_days)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ================== MRR CALCULATION ==================
        # Pro = $19/month (only tier currently available)
        cursor.execute("""
            SELECT
                COALESCE(subscription_tier, 'free') as tier,
                COUNT(*) as count
            FROM creators
            GROUP BY COALESCE(subscription_tier, 'free')
        """)
        tier_counts = {row['tier']: row['count'] for row in cursor.fetchall()}

        pro_count_db = tier_counts.get('pro', 0)
        free_count = tier_counts.get('free', 0)

        # MRR — prefer live Stripe active subs; fall back to DB tier count × $19
        pro_price = 19
        mrr_source = 'database'
        from utils.stripe_mrr import fetch_stripe_mrr

        stripe_mrr = fetch_stripe_mrr()
        if stripe_mrr:
            current_mrr = int(stripe_mrr['mrr_dollars'])
            pro_count = stripe_mrr['active_subscriptions']
            mrr_source = 'stripe'
        else:
            pro_count = pro_count_db
            current_mrr = pro_count * pro_price

        total_paid_subs = pro_count
        goal_mrr = 1000
        progress_pct = min(round((current_mrr / goal_mrr) * 100, 1), 100)

        # Conversion rate (paid / total)
        total_creators = free_count + pro_count
        conversion_rate = round((total_paid_subs / max(total_creators, 1)) * 100, 2)

        # Need X more subs to hit goal
        subs_needed = max(0, int((goal_mrr - current_mrr) / pro_price))

        # ================== AT-LIMIT USERS (Hot Leads) ==================
        # Free users who maxed 3 pitches/month AND hit limit 7+ days ago (optimal nudge timing)
        # Support at_limit_limit query param for pagination (default 10, max 200)
        at_limit_limit = min(int(request.args.get('at_limit_limit', 10)), 200)
        cursor.execute("""
            WITH user_pitch_counts AS (
                SELECT
                    c.id as creator_id,
                    u.email,
                    c.username,
                    c.followers_count,
                    c.niche,
                    COUNT(*) as pitches_this_month,
                    MAX(cp.pitched_at) as last_pitch_at
                FROM creators c
                JOIN users u ON c.user_id = u.id
                JOIN creator_pipeline cp ON c.id = cp.creator_id
                WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
                AND cp.pitched_at >= DATE_TRUNC('month', NOW())
                AND (c.last_any_email_sent IS NULL OR c.last_any_email_sent < NOW() - INTERVAL '48 hours')
                AND u.unsubscribed_at IS NULL
                GROUP BY c.id, u.email, c.username, c.followers_count, c.niche
                HAVING COUNT(*) >= 3
            )
            SELECT *,
                EXTRACT(DAY FROM NOW() - last_pitch_at)::INT as days_ago
            FROM user_pitch_counts
            WHERE EXTRACT(DAY FROM NOW() - last_pitch_at) >= 7
            ORDER BY last_pitch_at ASC
            LIMIT %s
        """, (at_limit_limit,))
        at_limit_users = []
        for row in cursor.fetchall():
            days_since = None
            if row['last_pitch_at']:
                days_since = (datetime.now() - row['last_pitch_at']).days
            at_limit_users.append({
                'creator_id': row['creator_id'],
                'email': row['email'],
                'username': row['username'],
                'followers': row['followers_count'],
                'niche': row['niche'],
                'pitches_used': row['pitches_this_month'],
                'hit_limit_at': row['last_pitch_at'].strftime('%b %d') if row['last_pitch_at'] else None,
                'days_since_limit': days_since,
                'needs_followup': days_since >= 7 if days_since is not None else False
            })

        # Count of users at limit (7+ days ago, excluding recently emailed and unsubscribed)
        cursor.execute("""
            SELECT COUNT(*) as count FROM (
                SELECT cp.creator_id, MAX(cp.pitched_at) as last_pitch_at
                FROM creator_pipeline cp
                JOIN creators c ON cp.creator_id = c.id
                JOIN users u ON c.user_id = u.id
                WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
                AND cp.pitched_at >= DATE_TRUNC('month', NOW())
                AND (c.last_any_email_sent IS NULL OR c.last_any_email_sent < NOW() - INTERVAL '48 hours')
                AND u.unsubscribed_at IS NULL
                GROUP BY cp.creator_id
                HAVING COUNT(*) >= 3 AND MAX(cp.pitched_at) <= NOW() - INTERVAL '7 days'
            ) as at_limit
        """)
        at_limit_count = cursor.fetchone()['count']

        # Users near limit (2/3 pitches this month)
        cursor.execute("""
            SELECT COUNT(*) as count FROM (
                SELECT creator_id
                FROM creator_pipeline cp
                JOIN creators c ON cp.creator_id = c.id
                WHERE (c.subscription_tier = 'free' OR c.subscription_tier IS NULL)
                AND cp.pitched_at >= DATE_TRUNC('month', NOW())
                GROUP BY cp.creator_id
                HAVING COUNT(*) = 2
            ) as near_limit
        """)
        near_limit_count = cursor.fetchone()['count']

        # ================== HEALTH METRICS (Dynamic Period) ==================
        # Sparkline data for signups, pitches, and active creators based on selected period

        # Signups by day (selected period)
        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(*) as count
            FROM creators
            WHERE created_at >= %s AND created_at <= %s
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (start_date, end_date))
        signup_daily = [{'date': str(row['date']), 'count': row['count']} for row in cursor.fetchall()]

        # Current period vs previous period signups
        cursor.execute("""
            SELECT COUNT(*) as count FROM creators
            WHERE created_at >= %s AND created_at <= %s
        """, (start_date, end_date))
        signups_this_period = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM creators
            WHERE created_at >= %s AND created_at < %s
        """, (prev_start_date, prev_end_date))
        signups_last_period = cursor.fetchone()['count']

        # Pitches by day (selected period)
        cursor.execute("""
            SELECT
                DATE(pitched_at) as date,
                COUNT(*) as count
            FROM creator_pipeline
            WHERE pitched_at >= %s AND pitched_at <= %s
            AND pitched_at IS NOT NULL
            GROUP BY DATE(pitched_at)
            ORDER BY date ASC
        """, (start_date, end_date))
        pitches_daily = [{'date': str(row['date']), 'count': row['count']} for row in cursor.fetchall()]

        # Current period vs previous period pitches
        cursor.execute("""
            SELECT COUNT(*) as count FROM creator_pipeline
            WHERE pitched_at >= %s AND pitched_at <= %s
        """, (start_date, end_date))
        pitches_this_period = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM creator_pipeline
            WHERE pitched_at >= %s AND pitched_at < %s
        """, (prev_start_date, prev_end_date))
        pitches_last_period = cursor.fetchone()['count']

        # Active creators by day (selected period)
        cursor.execute("""
            SELECT
                DATE(created_at) as date,
                COUNT(DISTINCT creator_id) as count
            FROM creator_pipeline
            WHERE created_at >= %s AND created_at <= %s
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (start_date, end_date))
        active_daily = [{'date': str(row['date']), 'count': row['count']} for row in cursor.fetchall()]

        # Current period vs previous period active creators
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
            WHERE created_at >= %s AND created_at <= %s
        """, (start_date, end_date))
        active_this_period = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
            WHERE created_at >= %s AND created_at < %s
        """, (prev_start_date, prev_end_date))
        active_last_period = cursor.fetchone()['count']

        # ================== CREATOR ACTIVATION FUNNEL (All Time) ==================
        cursor.execute("SELECT COUNT(*) as count FROM creators")
        total_signups = cursor.fetchone()['count']

        # Users who saved at least 1 brand
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
        """)
        saved_brand = cursor.fetchone()['count']

        # Users who pitched at least 1 brand
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
            WHERE pitched_at IS NOT NULL
        """)
        sent_pitch = cursor.fetchone()['count']

        # Users who pitched 2+ brands
        cursor.execute("""
            SELECT COUNT(*) as count FROM (
                SELECT creator_id FROM creator_pipeline
                WHERE pitched_at IS NOT NULL
                GROUP BY creator_id
                HAVING COUNT(*) >= 2
            ) as multi_pitch
        """)
        pitched_multiple = cursor.fetchone()['count']

        # Users who got a package (success stage)
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
            WHERE stage = 'success'
        """)
        got_package = cursor.fetchone()['count']

        # ================== THIS MONTH STATS ==================
        cursor.execute("""
            SELECT COUNT(*) as count FROM creators
            WHERE created_at >= DATE_TRUNC('month', NOW())
        """)
        signups_this_month = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM creator_pipeline
            WHERE pitched_at >= DATE_TRUNC('month', NOW())
        """)
        pitches_this_month = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count FROM creator_pipeline
            WHERE pitched_at IS NOT NULL
        """)
        unique_pitch_users = cursor.fetchone()['count']

        # ================== TOP BRANDS BY PITCHES ==================
        # Most pitched brands by creators - useful for partnership insights
        cursor.execute("""
            SELECT
                pb.id as brand_id,
                pb.brand_name,
                pb.category,
                pb.logo_url,
                COUNT(*) as saves,
                SUM(CASE WHEN cp.pitched_at IS NOT NULL THEN 1 ELSE 0 END) as pitch_count,
                SUM(CASE WHEN cp.stage = 'replied' THEN 1 ELSE 0 END) as replies,
                SUM(CASE WHEN cp.stage = 'deal_closed' THEN 1 ELSE 0 END) as deals
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            GROUP BY pb.id, pb.brand_name, pb.category, pb.logo_url
            ORDER BY saves DESC
            LIMIT 15
        """)
        top_brands = []
        for row in cursor.fetchall():
            reply_rate = round((row['replies'] / row['pitch_count']) * 100, 1) if row['pitch_count'] > 0 else 0
            top_brands.append({
                'brand_id': row['brand_id'],
                'brand_name': row['brand_name'],
                'category': row['category'],
                'logo_url': row['logo_url'],
                'saves': row['saves'],
                'pitch_count': row['pitch_count'],
                'replies': row['replies'],
                'deals': row['deals'],
                'reply_rate': reply_rate
            })

        conn.close()

        # ================== TRAFFIC from GA4 ==================
        traffic = None
        bust_cache = request.args.get('bust') == '1'
        try:
            from utils.ga4 import get_traffic_data
            traffic_raw = get_traffic_data(bust_cache=bust_cache)
            if traffic_raw:
                # Visitor → signup rate: combine GA4 visitors with DB signups
                visitors = traffic_raw["visitors_this_week"]
                visitor_signup_rate = round(
                    (signups_this_period / visitors * 100), 1
                ) if visitors else 0

                import time as _time
                fetched_at = traffic_raw.get("fetched_at") or int(_time.time())
                age_minutes = round((_time.time() - fetched_at) / 60)
                traffic = {
                    **traffic_raw,
                    "visitor_signup_rate": visitor_signup_rate,
                    "signups_this_week": signups_this_period,
                    "connected": True,
                    "cache_age_minutes": age_minutes,
                    "cache_ttl_minutes": 15,
                }
        except Exception as e:
            print(f"[GA4] Error fetching traffic data: {e}")
            traffic = {"connected": False, "error": str(e)}

        return jsonify({
            'mrr': {
                'current': current_mrr,
                'goal': goal_mrr,
                'progress_pct': progress_pct,
                'pro_count': pro_count,
                'pro_count_db': pro_count_db,
                'total_paid': total_paid_subs,
                'pro_price': pro_price,
                'subs_needed': subs_needed,
                'conversion_rate': conversion_rate,
                'source': mrr_source,
                'stripe_live': bool(stripe_mrr and stripe_mrr.get('live_mode')),
            },
            'at_limit_users': at_limit_users,
            'at_limit_count': at_limit_count,
            'near_limit_count': near_limit_count,
            'top_brands': top_brands,
            'health': {
                'signups': {
                    'this_week': signups_this_period,  # Keep key name for frontend compat
                    'last_week': signups_last_period,
                    'change': signups_this_period - signups_last_period,
                    'daily': signup_daily
                },
                'pitches': {
                    'this_week': pitches_this_period,
                    'last_week': pitches_last_period,
                    'change': pitches_this_period - pitches_last_period,
                    'daily': pitches_daily
                },
                'active_creators': {
                    'this_week': active_this_period,
                    'last_week': active_last_period,
                    'change': active_this_period - active_last_period,
                    'daily': active_daily
                }
            },
            'period': {
                'key': period,
                'label': period_label,
                'days': period_days,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d'),
            },
            'traffic': traffic,
            'funnel': {
                'signed_up': total_signups,
                'saved_brand': saved_brand,
                'sent_pitch': sent_pitch,
                'pitched_multiple': pitched_multiple,
                'got_package': got_package
            },
            'this_month': {
                'signups': signups_this_month,
                'total_signups': total_signups,
                'pitches': pitches_this_month,
                'unique_pitch_users': unique_pitch_users
            }
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# SEND NUDGE EMAIL - For at-limit users
# ============================================================================

@admin_reports_bp.route('/send-nudge', methods=['POST'])
@admin_required
def send_nudge_email():
    """
    Send a nudge email to an at-limit user encouraging them to upgrade.
    Respects the global 24-hour email cooldown to avoid email fatigue.

    Request body:
        {
            "creator_id": 123,
            "email": "user@example.com"
        }

    Returns:
        {"success": true, "message": "Nudge sent"}
        or
        {"success": false, "reason": "cooldown"} if user received email recently
    """
    try:
        import os
        from jinja2 import Environment, FileSystemLoader
        from email_cron_routes import send_template_email

        data = request.get_json()
        creator_id = data.get('creator_id')
        email = data.get('email')

        if not creator_id or not email:
            return jsonify({'error': 'Missing creator_id or email'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check global 24-hour cooldown and unsubscribe status
        GLOBAL_EMAIL_COOLDOWN_HOURS = 24
        FREE_PITCH_LIMIT = 3
        cursor.execute("""
            SELECT c.username, u.first_name, c.last_any_email_sent,
                u.unsubscribed_at,
                (
                    SELECT COUNT(*)::int
                    FROM creator_pipeline cp
                    WHERE cp.creator_id = c.id
                      AND cp.pitched_at >= DATE_TRUNC('month', NOW())
                ) AS pitches_this_month
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        result = cursor.fetchone()

        if not result:
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404

        # Check if user has unsubscribed from emails
        if result.get('unsubscribed_at'):
            conn.close()
            return jsonify({
                'success': False,
                'reason': 'unsubscribed',
                'message': 'User has unsubscribed from emails'
            }), 200

        # Use first_name for greeting, fallback to username, then 'there'
        first_name = result.get('first_name')
        username = result.get('username')
        if first_name:
            display_name = first_name.split()[0]
        elif username:
            display_name = username.split()[0]
        else:
            display_name = 'there'
        pitches_this_month = result.get('pitches_this_month') or FREE_PITCH_LIMIT

        # Cooldown check in SQL (last_any_email_sent is TIMESTAMPTZ)
        cursor.execute("""
            SELECT
                (last_any_email_sent IS NOT NULL
                 AND last_any_email_sent >= NOW() - INTERVAL '1 hour' * %s) AS on_cooldown,
                GREATEST(
                    1,
                    CEIL(EXTRACT(EPOCH FROM (
                        last_any_email_sent + INTERVAL '1 hour' * %s - NOW()
                    )) / 3600)
                )::int AS hours_until_ok
            FROM creators
            WHERE id = %s
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS, GLOBAL_EMAIL_COOLDOWN_HOURS, creator_id))
        cooldown_row = cursor.fetchone()

        if cooldown_row and cooldown_row.get('on_cooldown'):
            hours_until_ok = cooldown_row.get('hours_until_ok') or 1
            conn.close()
            return jsonify({
                'success': False,
                'reason': 'cooldown',
                'message': f'User received an email recently. Try again in ~{hours_until_ok}h.'
            }), 200

        frontend_url = os.getenv('FRONTEND_URL', 'https://newcollab.co').rstrip('/')
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        if not smtp_username or not smtp_password:
            print(f"[NUDGE] SMTP not configured - would send to {email}")
            conn.close()
            return jsonify({
                'success': False,
                'reason': 'smtp_not_configured',
                'message': 'Email service not configured'
            }), 200

        # ── Fetch user's actual pitches this month with brand info ──
        cursor.execute("""
            SELECT
                cp.id,
                cp.stage,
                cp.pitched_at,
                cp.followup_sent_at,
                EXTRACT(DAY FROM NOW() - cp.pitched_at)::INT AS days_since,
                pb.brand_name,
                pb.category
            FROM creator_pipeline cp
            JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.creator_id = %s
              AND cp.pitched_at >= DATE_TRUNC('month', NOW())
              AND cp.stage != 'archived'
            ORDER BY cp.pitched_at ASC
            LIMIT 3
        """, (creator_id,))
        pitch_rows = cursor.fetchall()

        # Build pitches list with status logic
        pitches = []
        # Brand colors - predefined palette for variety
        brand_colors = ['#B5002D', '#2E7D4F', '#1A1A2E', '#6D28D9', '#E85D75', '#1D4ED8', '#C8102E', '#4A7C59']
        for i, row in enumerate(pitch_rows):
            days_since = row.get('days_since') or 0
            stage = row.get('stage') or 'pitched'
            brand_name = row.get('brand_name') or 'Brand'

            # Determine status and next action based on stage and days
            if stage in ('replied', 'won', 'success', 'received'):
                status = 'replied'
                next_action_copy = 'Log reply + track PR value'
                next_action_sub = 'build your deal history'
            elif days_since >= 7:
                status = 'follow_up_due'
                next_action_copy = 'Send follow-up email'
                next_action_sub = 'custom drafted for this brand, ready to send'
            else:
                status = 'waiting'
                days_until = max(1, 7 - days_since)
                next_action_copy = f'Follow-up in {days_until} day{"s" if days_until != 1 else ""}'
                next_action_sub = "we'll draft it automatically"

            pitches.append({
                'brand_name': brand_name,
                'brand_initial': brand_name[:2].upper(),
                'brand_color': brand_colors[i % len(brand_colors)],
                'days_since': days_since,
                'status': status,
                'next_action_copy': next_action_copy,
                'next_action_sub': next_action_sub,
            })

        # ── Get creator's niche ──
        cursor.execute("SELECT niche FROM creators WHERE id = %s", (creator_id,))
        niche_row = cursor.fetchone()
        user_niche = niche_row.get('niche') if niche_row else None
        niche_label = user_niche.replace('_', ' ').title() if user_niche else 'your niche'

        # ── Fetch teaser brands (from user's niche, not yet pitched) ──
        teaser_brands = []
        if user_niche:
            pitched_brand_ids = [row.get('id') for row in pitch_rows if row.get('id')]
            # Get 2 high-response-rate brands in same category/niche
            cursor.execute("""
                SELECT
                    brand_name,
                    category,
                    COALESCE(response_rate, 0) AS response_rate
                FROM pr_brands
                WHERE category = %s
                  AND id NOT IN (
                      SELECT brand_id FROM creator_pipeline WHERE creator_id = %s
                  )
                  AND COALESCE(status, 'published') = 'published'
                ORDER BY response_rate DESC NULLS LAST
                LIMIT 2
            """, (user_niche, creator_id))
            teaser_rows = cursor.fetchall()

            teaser_colors = ['#E85D75', '#6D28D9', '#1D4ED8', '#C8102E']
            for j, tr in enumerate(teaser_rows):
                teaser_brands.append({
                    'name': tr.get('brand_name'),
                    'initial': (tr.get('brand_name') or 'BR')[:2].upper(),
                    'logo_color': teaser_colors[j % len(teaser_colors)],
                    'reply_rate': int(tr.get('response_rate') or 0) if tr.get('response_rate') else None,
                })

        templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        jinja_env = Environment(loader=FileSystemLoader(templates_dir))
        message_html = jinja_env.get_template('upgrade_nudge_email_body.html').render(
            display_name=display_name,
            pitch_limit=FREE_PITCH_LIMIT,
            pitches_this_month=pitches_this_month,
            pitches=pitches,
            teaser_brands=teaser_brands,
            niche_label=niche_label,
        )

        # Use first brand name in subject if available for higher open rate
        first_brand = pitches[0]['brand_name'] if pitches else None
        if first_brand:
            subject = f"{first_brand} hasn't replied yet — here's what to do"
        else:
            subject = f"{display_name}, your 5 free contacts are out — here's what's next"

        email_context = {
            'subject': subject,
            'preheader': f"You've used your 5 free contacts. Here's the status of each one.",
            'message': message_html,
            'action_url': f'{frontend_url}/creator/dashboard/settings?upgrade=pro&ref=quota_email&utm_source=email&utm_medium=trigger&utm_campaign=quota_hit',
            'action_text': 'Unlock Pro — $19/month →',
        }

        success, smtp_error = send_template_email(
            to_email=email,
            template_name='conversion_email.html',
            subject=subject,
            context=email_context,
        )

        if not success:
            conn.close()
            print(f"[NUDGE] SMTP error sending to {email}: {smtp_error}")
            return jsonify({
                'success': False,
                'reason': 'smtp_error',
                'message': smtp_error or 'Failed to send email',
            }), 200

        cursor.execute("""
            UPDATE creators
            SET last_any_email_sent = NOW()
            WHERE id = %s
        """, (creator_id,))
        conn.commit()
        print(f"[NUDGE] Sent upgrade nudge to {email} (creator_id: {creator_id})")
        conn.close()
        return jsonify({
            'success': True,
            'message': f'Nudge sent to {email}',
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
