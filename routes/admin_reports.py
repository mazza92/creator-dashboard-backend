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

        # Active users based on brand_unlocks table
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Active today
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM brand_unlocks
            WHERE DATE(unlocked_at) = %s
        """, (today,))
        active_today = cursor.fetchone()['count']

        # Active last 7 days
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM brand_unlocks
            WHERE DATE(unlocked_at) >= %s
        """, (week_ago,))
        active_7d = cursor.fetchone()['count']

        # Active last 30 days
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM brand_unlocks
            WHERE DATE(unlocked_at) >= %s
        """, (month_ago,))
        active_30d = cursor.fetchone()['count']

        # Total unlocks
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

        conn.close()

        return jsonify({
            'total_users': total_users,
            'total_creators': total_creators,
            'active_today': active_today,
            'active_7d': active_7d,
            'active_30d': active_30d,
            'total_unlocks': total_unlocks,
            'total_pipeline_saves': total_pipeline,
            'subscription_breakdown': subscription_breakdown
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

        # Active users in last 24 hours (unique creators who unlocked)
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM brand_unlocks
            WHERE unlocked_at >= NOW() - INTERVAL '24 hours'
        """)
        active_users_today = cursor.fetchone()['count']

        # Active users 24-48 hours ago
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_id) as count
            FROM brand_unlocks
            WHERE unlocked_at >= NOW() - INTERVAL '48 hours'
            AND unlocked_at < NOW() - INTERVAL '24 hours'
        """)
        active_users_yesterday = cursor.fetchone()['count']

        # Total unlocks in last 24 hours
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM brand_unlocks
            WHERE unlocked_at >= NOW() - INTERVAL '24 hours'
        """)
        unlocks_today = cursor.fetchone()['count']

        # Total unlocks 24-48 hours ago
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM brand_unlocks
            WHERE unlocked_at >= NOW() - INTERVAL '48 hours'
            AND unlocked_at < NOW() - INTERVAL '24 hours'
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

        # Top 5 brands unlocked in last 24 hours
        cursor.execute("""
            SELECT
                pb.brand_name,
                pb.category,
                COUNT(*) as unlock_count
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE bu.unlocked_at >= NOW() - INTERVAL '24 hours'
            GROUP BY pb.id, pb.brand_name, pb.category
            ORDER BY unlock_count DESC
            LIMIT 5
        """)
        top_brands_today = cursor.fetchall()

        # Most active users in last 24 hours
        cursor.execute("""
            SELECT
                c.id as creator_id,
                u.email,
                c.username,
                COALESCE(c.subscription_tier, 'free') as tier,
                COUNT(*) as unlocks_today
            FROM brand_unlocks bu
            JOIN creators c ON bu.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE bu.unlocked_at >= NOW() - INTERVAL '24 hours'
            GROUP BY c.id, u.email, c.username, c.subscription_tier
            ORDER BY unlocks_today DESC
            LIMIT 10
        """)
        most_active_today = cursor.fetchall()

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

        # DAU based on brand_unlocks activity
        cursor.execute("""
            SELECT
                DATE(unlocked_at) as date,
                COUNT(DISTINCT creator_id) as active_users
            FROM brand_unlocks
            WHERE DATE(unlocked_at) >= %s
            GROUP BY DATE(unlocked_at)
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
    Get top users by activity

    Query params:
        limit: Number of users (default 20)
        metric: 'unlocks' or 'pipeline' (default 'unlocks')

    Returns:
        {
            "users": [
                {"creator_id": 1, "email": "...", "count": 50, "tier": "pro"},
                ...
            ]
        }
    """
    try:
        limit = int(request.args.get('limit', 20))
        metric = request.args.get('metric', 'unlocks')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        if metric == 'unlocks':
            cursor.execute("""
                SELECT
                    bu.creator_id,
                    u.email,
                    c.username,
                    COALESCE(c.subscription_tier, 'free') as tier,
                    COUNT(*) as count,
                    MAX(bu.unlocked_at) as last_activity
                FROM brand_unlocks bu
                JOIN creators c ON bu.creator_id = c.id
                JOIN users u ON c.user_id = u.id
                GROUP BY bu.creator_id, u.email, c.username, c.subscription_tier
                ORDER BY count DESC
                LIMIT %s
            """, (limit,))
        else:
            cursor.execute("""
                SELECT
                    cp.creator_id,
                    u.email,
                    c.username,
                    COALESCE(c.subscription_tier, 'free') as tier,
                    COUNT(*) as count,
                    MAX(cp.created_at) as last_activity
                FROM creator_pipeline cp
                JOIN creators c ON cp.creator_id = c.id
                JOIN users u ON c.user_id = u.id
                GROUP BY cp.creator_id, u.email, c.username, c.subscription_tier
                ORDER BY count DESC
                LIMIT %s
            """, (limit,))

        users = cursor.fetchall()

        # Convert datetime to string
        users_data = []
        for user in users:
            users_data.append({
                'creator_id': user['creator_id'],
                'email': user['email'],
                'username': user['username'],
                'tier': user['tier'],
                'count': user['count'],
                'last_activity': str(user['last_activity']) if user['last_activity'] else None
            })

        conn.close()

        return jsonify({
            'users': users_data,
            'metric': metric
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
    Get retention cohort data

    Returns:
        {
            "cohorts": [
                {
                    "signup_week": "2026-W01",
                    "total_users": 50,
                    "week_1": 30,
                    "week_2": 20,
                    "week_3": 15,
                    "week_4": 12
                },
                ...
            ]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get signup cohorts from last 8 weeks
        cursor.execute("""
            WITH signup_cohorts AS (
                SELECT
                    id as creator_id,
                    DATE_TRUNC('week', created_at) as signup_week
                FROM creators
                WHERE created_at >= NOW() - INTERVAL '8 weeks'
            ),
            activity AS (
                SELECT
                    creator_id,
                    DATE_TRUNC('week', unlocked_at) as activity_week
                FROM brand_unlocks
                WHERE unlocked_at >= NOW() - INTERVAL '12 weeks'
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
            cohorts.append({
                'signup_week': str(row['signup_week'].date()) if row['signup_week'] else None,
                'total_users': row['total_users'],
                'week_0': row['week_0'],
                'week_1': row['week_1'],
                'week_2': row['week_2'],
                'week_3': row['week_3'],
                'week_4': row['week_4']
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
    Get most popular brands by unlock count

    Query params:
        limit: Number of brands (default 20)
        days: Time period (default 30)

    Returns:
        {
            "brands": [
                {"brand_id": 1, "brand_name": "...", "unlock_count": 50},
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

        cursor.execute("""
            SELECT
                bu.brand_id,
                pb.brand_name,
                pb.category,
                COUNT(*) as unlock_count,
                COUNT(DISTINCT bu.creator_id) as unique_users
            FROM brand_unlocks bu
            JOIN pr_brands pb ON bu.brand_id = pb.id
            WHERE DATE(bu.unlocked_at) >= %s
            GROUP BY bu.brand_id, pb.brand_name, pb.category
            ORDER BY unlock_count DESC
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
