# -*- coding: utf-8 -*-
"""
Lifecycle Email Routes
API endpoints for lifecycle email system including cron jobs,
admin controls, and action triggers.
"""
import os
import traceback
from flask import Blueprint, request, jsonify
from functools import wraps

# Track import errors for debugging
_import_error = None
try:
    from lifecycle_email_engine import (
        process_daily_lifecycle_emails,
        process_weekly_digest,
        trigger_welcome_email,
        trigger_reply_celebration,
        trigger_first_pr_box,
        trigger_quota_hit,
        get_all_feature_flags,
        set_feature_flag,
        is_feature_enabled,
        get_eligible_emails,
        update_creator_state,
        send_lifecycle_email,
        build_email_context,
        get_db_connection
    )
except Exception as e:
    _import_error = traceback.format_exc()
    # Define stubs so blueprint can still load
    process_daily_lifecycle_emails = None
    process_weekly_digest = None
    trigger_welcome_email = None
    trigger_reply_celebration = None
    trigger_first_pr_box = None
    trigger_quota_hit = None
    get_all_feature_flags = None
    set_feature_flag = None
    is_feature_enabled = None
    get_eligible_emails = None
    update_creator_state = None
    send_lifecycle_email = None
    build_email_context = None
    get_db_connection = None

lifecycle_email_bp = Blueprint('lifecycle_email', __name__)

# Admin token for cron jobs
CRON_TOKEN = os.getenv('CRON_SECRET', 'lifecycle-cron-secret-2026')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'pr-hunter-admin-2026')


@lifecycle_email_bp.route('/api/lifecycle-email/health', methods=['GET'])
def lifecycle_health():
    """Health check endpoint to diagnose import/setup issues."""
    return jsonify({
        'status': 'ok' if _import_error is None else 'import_error',
        'import_error': _import_error,
        'functions_loaded': {
            'process_weekly_digest': process_weekly_digest is not None,
            'process_daily_lifecycle_emails': process_daily_lifecycle_emails is not None,
            'trigger_welcome_email': trigger_welcome_email is not None,
        }
    })


def require_cron_auth(f):
    """Decorator to require cron authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Cron-Token') or request.args.get('cron_token')
        print(f"[CRON AUTH] Received token: '{token}', Expected: '{CRON_TOKEN}', Headers: {dict(request.headers)}")
        if token != CRON_TOKEN:
            return jsonify({'error': 'Unauthorized', 'debug': f'received={token}, expected_len={len(CRON_TOKEN)}'}), 401
        return f(*args, **kwargs)
    return decorated


def require_admin_auth(f):
    """Decorator to require admin authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Admin-Token') or request.args.get('admin_token')
        if token != ADMIN_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


# ============================================
# CRON JOB ENDPOINTS
# ============================================

@lifecycle_email_bp.route('/api/lifecycle-email/cron/daily', methods=['POST'])
@require_cron_auth
def cron_daily_emails():
    """
    Process daily lifecycle emails.
    Should be called by cron every day at 10am.
    """
    try:
        batch_size = request.json.get('batch_size', 100) if request.json else 100
        stats = process_daily_lifecycle_emails(batch_size=batch_size)
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/cron/weekly-digest', methods=['POST'])
@require_cron_auth
def cron_weekly_digest():
    """
    Process weekly digest emails.
    Should be called by cron every Monday at 8am.
    """
    from datetime import datetime

    # Check for import errors first
    if _import_error:
        return jsonify({
            'success': False,
            'error': 'Import error in lifecycle_email_engine',
            'traceback': _import_error
        }), 500

    if process_weekly_digest is None:
        return jsonify({
            'success': False,
            'error': 'process_weekly_digest function not loaded'
        }), 500

    # Quick sanity check - it's not Monday, skip
    if datetime.now().weekday() != 0:
        return jsonify({
            'success': True,
            'skipped': True,
            'reason': 'Not Monday',
            'current_day': datetime.now().strftime('%A')
        })

    try:
        batch_size = request.json.get('batch_size', 100) if request.json else 100
        stats = process_weekly_digest(batch_size=batch_size)
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        error_trace = traceback.format_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': error_trace
        }), 500


# ============================================
# ACTION TRIGGER ENDPOINTS
# ============================================

@lifecycle_email_bp.route('/api/lifecycle-email/trigger/welcome', methods=['POST'])
def trigger_welcome():
    """
    Trigger welcome email after email verification.
    Called internally after user verifies email.
    """
    try:
        data = request.json
        creator_id = data.get('creator_id')
        email = data.get('email')
        first_name = data.get('first_name')

        if not creator_id or not email:
            return jsonify({'error': 'Missing creator_id or email'}), 400

        success, message = trigger_welcome_email(creator_id, email, first_name)
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/trigger/reply', methods=['POST'])
def trigger_reply():
    """
    Trigger celebration email when creator marks a reply.
    Called when pipeline status is updated to 'replied'.
    """
    try:
        data = request.json
        creator_id = data.get('creator_id')
        brand_id = data.get('brand_id')

        if not creator_id or not brand_id:
            return jsonify({'error': 'Missing creator_id or brand_id'}), 400

        success, message = trigger_reply_celebration(creator_id, brand_id)
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/trigger/pr-box', methods=['POST'])
def trigger_pr_box():
    """
    Trigger celebration email when creator marks first PR box received.
    """
    try:
        data = request.json
        creator_id = data.get('creator_id')
        brand_id = data.get('brand_id')

        if not creator_id or not brand_id:
            return jsonify({'error': 'Missing creator_id or brand_id'}), 400

        success, message = trigger_first_pr_box(creator_id, brand_id)
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/trigger/quota-hit', methods=['POST'])
def trigger_quota():
    """
    Trigger maximizer email when creator hits 3/3 unlocks.
    Called when unlock count reaches 3.
    """
    try:
        data = request.json
        creator_id = data.get('creator_id')

        if not creator_id:
            return jsonify({'error': 'Missing creator_id'}), 400

        success, message = trigger_quota_hit(creator_id)
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ============================================
# ADMIN ENDPOINTS
# ============================================

@lifecycle_email_bp.route('/api/lifecycle-email/admin/feature-flags', methods=['GET'])
@require_admin_auth
def get_feature_flags():
    """Get all feature flags."""
    try:
        flags = get_all_feature_flags()
        return jsonify({
            'success': True,
            'flags': flags
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/admin/feature-flags', methods=['POST'])
@require_admin_auth
def update_feature_flag():
    """Update a feature flag."""
    try:
        data = request.json
        flag_name = data.get('flag_name')
        enabled = data.get('enabled')

        if flag_name is None or enabled is None:
            return jsonify({'error': 'Missing flag_name or enabled'}), 400

        success = set_feature_flag(flag_name, enabled)
        return jsonify({
            'success': success,
            'flag_name': flag_name,
            'enabled': enabled
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/admin/preview/<template_slug>', methods=['GET'])
@require_admin_auth
def preview_template(template_slug):
    """Preview a lifecycle email template with test data."""
    try:
        from jinja2 import Environment, FileSystemLoader
        import os

        # Load template
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        jinja_env = Environment(loader=FileSystemLoader(template_dir))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT body_template_file, subject_template, preheader_template
            FROM lifecycle_email_templates WHERE slug = %s
        """, (template_slug,))
        template_info = cursor.fetchone()
        conn.close()

        if not template_info:
            return jsonify({'error': 'Template not found'}), 404

        # Test context
        test_context = {
            'first_name': 'Test User',
            'preheader': template_info[2] or '',
            'subject': template_info[1],
            'cta_url': 'https://app.newcollab.co/creator/dashboard/ai-manager',
            'preferences_url': '#',
            'unsubscribe_url': '#',
            'brand_name': 'Test Brand',
            'brand_response_rate': 15,
            'current_score': 67,
            'score_delta': 5,
            'unlocks_used': 2,
            'unlocks_quota': 3,
            'replies_count': 1,
            'reset_date': 'August 1',
            'days_until_reset': 5,
            'month': 'July',
            'pitches_sent': 3,
            'is_pro': False,
            'pending_count': 2,
            'pending_brands': [
                {'name': 'Brand A', 'days_ago': 7, 'response_rate': 12},
                {'name': 'Brand B', 'days_ago': 10, 'response_rate': 8},
            ],
            'new_brands_count': 6,
            'top_new_brands': [
                {'name': 'New Brand 1', 'category': 'Beauty', 'fit_score': 85},
                {'name': 'New Brand 2', 'category': 'Skincare', 'fit_score': 72},
            ],
            'unlocks_available': 3,
            'weekly_theme_title': 'Bio polish',
            'weekly_theme_body': 'Brands scan bios in 3 seconds. This week, audit yours.',
            'new_brands': [
                {'name': 'Brand X', 'category': 'Beauty', 'reason': 'New this week'},
            ],
            'win_story': {
                'handle': '@testcreator',
                'followers': '2.4K',
                'brand': 'Summer Fridays',
                'tactic': 'follow-up on day 6',
                'summary': 'Landed a PR serum set.'
            }
        }

        template = jinja_env.get_template(template_info[0])
        html = template.render(**test_context)

        return html, 200, {'Content-Type': 'text/html'}
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/admin/test-send', methods=['POST'])
@require_admin_auth
def test_send_email():
    """Send a test lifecycle email to a specific address."""
    from lifecycle_email_engine import render_template, _send_smtp, FRONTEND_URL

    try:
        data = request.json
        template_slug = data.get('template_slug')
        to_email = data.get('to_email')
        skip_checks = data.get('skip_checks', False)

        if not template_slug or not to_email:
            return jsonify({'error': 'Missing template_slug or to_email'}), 400

        # For skip_checks mode, render and send directly without DB lookups
        if skip_checks:
            # Map template slugs to files
            template_map = {
                'weekly_digest': '21_weekly_digest.html',
                'welcome_manager': '02_welcome_manager.html',
                'celebration_first_box': '23_celebration_first_box.html',
            }

            template_file = template_map.get(template_slug, f'{template_slug}.html')

            # Build test context with sample data
            context = {
                'first_name': data.get('first_name', 'Test'),
                'current_score': 75,
                'score_delta': 5,
                'unlocks_used': 1,
                'unlocks_quota': 3,
                'weekly_theme_title': 'Bio polish',
                'weekly_theme_body': 'Brands scan bios in 3 seconds. This week, audit yours.',
                'new_brands': [
                    {'name': 'Glossier', 'category': 'Beauty', 'reason': 'New this week'},
                    {'name': 'Rare Beauty', 'category': 'Beauty', 'reason': 'Matches your niche'},
                ],
                'win_story': None,
                'cta_url': f"{FRONTEND_URL}/creator/dashboard/pr-ready",
                'preferences_url': f"{FRONTEND_URL}/creator/dashboard/settings",
                'unsubscribe_url': f"{FRONTEND_URL}/unsubscribe",
                'preheader': 'Your weekly digest from Newcollab',
            }

            # Render template
            html_content = render_template(f'lifecycle/{template_file}', context)

            # Subject mapping
            subject_map = {
                'weekly_digest': 'your monday brief from your manager',
                'welcome_manager': 'your newcollab manager just arrived',
                'celebration_first_box': 'you got your first PR box',
            }
            subject = subject_map.get(template_slug, f'Test: {template_slug}')

            # Send directly via SMTP
            success, result = _send_smtp(to_email, subject, html_content)

            return jsonify({
                'success': success,
                'message': result if success else f"SMTP error: {result}",
                'mode': 'skip_checks'
            })

        # Normal mode - use full send_lifecycle_email flow
        creator_id = data.get('creator_id', 1)
        context = build_email_context(creator_id, template_slug)
        context['first_name'] = data.get('first_name', 'Test')

        success, message = send_lifecycle_email(
            to_email=to_email,
            template_slug=template_slug,
            context=context,
            creator_id=creator_id,
            dedup_key=f"test_{template_slug}_{to_email}_{os.urandom(4).hex()}"
        )

        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/admin/creator-state/<int:creator_id>', methods=['GET'])
@require_admin_auth
def get_creator_state(creator_id):
    """Get lifecycle state and eligible emails for a creator."""
    try:
        state = update_creator_state(creator_id)
        eligible = get_eligible_emails(creator_id)

        return jsonify({
            'success': True,
            'creator_id': creator_id,
            'lifecycle_state': state,
            'eligible_emails': [
                {
                    'slug': e['slug'],
                    'name': e['name'],
                    'priority': e['priority'],
                    'category': e['category']
                } for e in eligible
            ]
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/lifecycle-email/admin/stats', methods=['GET'])
@require_admin_auth
def get_email_stats():
    """Get lifecycle email statistics."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Sends by template (last 7 days)
        cursor.execute("""
            SELECT template_slug, COUNT(*) as send_count,
                   SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as success_count
            FROM lifecycle_email_sends
            WHERE sent_at >= NOW() - INTERVAL '7 days'
            GROUP BY template_slug
            ORDER BY send_count DESC
        """)
        by_template = [
            {'slug': r[0], 'sent': r[1], 'success': r[2]}
            for r in cursor.fetchall()
        ]

        # Sends by day (last 7 days)
        cursor.execute("""
            SELECT DATE(sent_at) as send_date, COUNT(*) as send_count
            FROM lifecycle_email_sends
            WHERE sent_at >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(sent_at)
            ORDER BY send_date DESC
        """)
        by_day = [
            {'date': str(r[0]), 'count': r[1]}
            for r in cursor.fetchall()
        ]

        # State distribution
        cursor.execute("""
            SELECT lifecycle_state, COUNT(*) as creator_count
            FROM creators
            WHERE lifecycle_state IS NOT NULL
            GROUP BY lifecycle_state
            ORDER BY creator_count DESC
        """)
        by_state = [
            {'state': r[0], 'count': r[1]}
            for r in cursor.fetchall()
        ]

        # Feature flag status
        flags = get_all_feature_flags()

        conn.close()

        return jsonify({
            'success': True,
            'sends_by_template': by_template,
            'sends_by_day': by_day,
            'creators_by_state': by_state,
            'feature_flags': flags
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ============================================
# EMAIL PREFERENCES (User-facing)
# ============================================

@lifecycle_email_bp.route('/api/email/preferences/<int:creator_id>', methods=['GET'])
def get_email_preferences(creator_id):
    """Get email preferences for a creator."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT receive_onboarding, receive_education, receive_behavioral,
                   receive_weekly_digest, receive_celebration, receive_reengagement,
                   receive_pro_nudges, unsubscribed_all, timezone
            FROM email_preferences WHERE creator_id = %s
        """, (creator_id,))
        prefs = cursor.fetchone()
        conn.close()

        if not prefs:
            # Return defaults
            return jsonify({
                'success': True,
                'preferences': {
                    'receive_onboarding': True,
                    'receive_education': True,
                    'receive_behavioral': True,
                    'receive_weekly_digest': True,
                    'receive_celebration': True,
                    'receive_reengagement': True,
                    'receive_pro_nudges': True,
                    'unsubscribed_all': False,
                    'timezone': 'America/New_York'
                }
            })

        return jsonify({
            'success': True,
            'preferences': {
                'receive_onboarding': prefs[0],
                'receive_education': prefs[1],
                'receive_behavioral': prefs[2],
                'receive_weekly_digest': prefs[3],
                'receive_celebration': prefs[4],
                'receive_reengagement': prefs[5],
                'receive_pro_nudges': prefs[6],
                'unsubscribed_all': prefs[7],
                'timezone': prefs[8]
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/email/preferences/<int:creator_id>', methods=['POST'])
def update_email_preferences(creator_id):
    """Update email preferences for a creator."""
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()

        # Upsert preferences
        cursor.execute("""
            INSERT INTO email_preferences (creator_id, receive_onboarding, receive_education,
                receive_behavioral, receive_weekly_digest, receive_celebration,
                receive_reengagement, receive_pro_nudges, timezone, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                receive_onboarding = EXCLUDED.receive_onboarding,
                receive_education = EXCLUDED.receive_education,
                receive_behavioral = EXCLUDED.receive_behavioral,
                receive_weekly_digest = EXCLUDED.receive_weekly_digest,
                receive_celebration = EXCLUDED.receive_celebration,
                receive_reengagement = EXCLUDED.receive_reengagement,
                receive_pro_nudges = EXCLUDED.receive_pro_nudges,
                timezone = EXCLUDED.timezone,
                updated_at = NOW()
        """, (
            creator_id,
            data.get('receive_onboarding', True),
            data.get('receive_education', True),
            data.get('receive_behavioral', True),
            data.get('receive_weekly_digest', True),
            data.get('receive_celebration', True),
            data.get('receive_reengagement', True),
            data.get('receive_pro_nudges', True),
            data.get('timezone', 'America/New_York')
        ))

        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@lifecycle_email_bp.route('/api/email/unsubscribe/<int:creator_id>', methods=['GET', 'POST'])
def unsubscribe(creator_id):
    """Unsubscribe a creator from all lifecycle emails."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO email_preferences (creator_id, unsubscribed_all, unsubscribed_at)
            VALUES (%s, true, NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                unsubscribed_all = true,
                unsubscribed_at = NOW()
        """, (creator_id,))

        conn.commit()
        conn.close()

        # Return a simple HTML page for GET requests
        if request.method == 'GET':
            return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Unsubscribed - Newcollab</title>
                <style>
                    body { font-family: -apple-system, sans-serif; max-width: 500px; margin: 100px auto; text-align: center; }
                    h1 { color: #15161a; }
                    p { color: #6b7280; }
                </style>
            </head>
            <body>
                <h1>You've been unsubscribed</h1>
                <p>You won't receive any more emails from your Newcollab manager.</p>
                <p>Changed your mind? <a href="https://app.newcollab.co/creator/dashboard/settings">Update your preferences</a></p>
            </body>
            </html>
            """, 200, {'Content-Type': 'text/html'}

        return jsonify({'success': True, 'message': 'Unsubscribed'})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
