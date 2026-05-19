"""
Email Cron Routes
Handles automated email campaigns and reminders
"""

from flask import Blueprint, jsonify, request
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor
from jinja2 import Environment, FileSystemLoader
import psycopg2

email_cron_bp = Blueprint('email_cron', __name__, url_prefix='/api/cron')

# =============================================================================
# GLOBAL EMAIL COOL-OFF SETTINGS
# Prevents users from receiving multiple emails on the same day
# =============================================================================
GLOBAL_EMAIL_COOLDOWN_HOURS = 24  # Minimum hours between ANY emails to same user


def check_global_cooloff(cursor, creator_id: int) -> bool:
    """
    Check if a creator can receive an email based on global cool-off.
    Returns True if they CAN receive an email (cooloff passed).
    Returns False if they should NOT receive an email (too recent).
    """
    cursor.execute("""
        SELECT last_any_email_sent
        FROM creators
        WHERE id = %s
    """, (creator_id,))
    result = cursor.fetchone()

    if not result or not result.get('last_any_email_sent'):
        return True  # Never sent, can send

    last_sent = result['last_any_email_sent']
    cooloff_threshold = datetime.now() - timedelta(hours=GLOBAL_EMAIL_COOLDOWN_HOURS)

    return last_sent < cooloff_threshold


def mark_email_sent(cursor, conn, creator_id: int, email_type: str = None):
    """
    Update the global last_any_email_sent timestamp after sending an email.
    Also updates the specific email type timestamp if provided.
    """
    cursor.execute("""
        UPDATE creators
        SET last_any_email_sent = NOW()
        WHERE id = %s
    """, (creator_id,))
    conn.commit()


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def send_template_email(to_email, template_name, subject, context):
    """
    Send an email using a Jinja2 template

    Args:
        to_email: Recipient email address
        template_name: Name of the template file (e.g., 'onboarding_reminder.html')
        subject: Email subject line
        context: Dictionary of variables to pass to the template

    Returns:
        Tuple of (success: bool, error_message: str or None)
    """
    try:
        env = Environment(loader=FileSystemLoader('templates'))
        template = env.get_template(template_name)

        html_content = template.render(**context)

        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')
        sender_name = os.getenv('EMAIL_SENDER_NAME', 'NewCollab')

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
        return False, str(e)


@email_cron_bp.route('/send-onboarding-reminders', methods=['POST'])
def send_onboarding_reminders():
    """
    Send onboarding reminder emails to incomplete profiles
    Finds creators who registered but haven't completed their profile
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Find incomplete profiles (with global cooloff check)
        cursor.execute("""
            SELECT c.id, u.email, c.username, c.created_at, c.last_reminder_sent
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.created_at < NOW() - INTERVAL '24 hours'
              AND (
                c.instagram_handle IS NULL
                OR c.instagram_handle = ''
                OR c.niche IS NULL
                OR c.niche = ''
              )
              AND (
                c.last_reminder_sent IS NULL
                OR c.last_reminder_sent < NOW() - INTERVAL '7 days'
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 50
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        incomplete_profiles = cursor.fetchall()

        sent_count = 0
        errors = []

        for profile in incomplete_profiles:
            try:
                context = {
                    'message': "We noticed you started creating your profile but haven't finished yet!",
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/creator/dashboard/profile",
                    'action_text': "Complete Your Profile",
                    'user_id': profile['id']
                }

                success, error = send_template_email(
                    to_email=profile['email'],
                    template_name='onboarding_reminder.html',
                    subject="Complete your NewCollab profile to unlock 300+ PR brands",
                    context=context
                )

                if success:
                    # Update last_reminder_sent and global timestamp
                    cursor.execute("""
                        UPDATE creators
                        SET last_reminder_sent = NOW(),
                            last_any_email_sent = NOW()
                        WHERE id = %s
                    """, (profile['id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent onboarding reminder to {profile['email']}")
                else:
                    errors.append(f"Error sending to {profile['email']}: {error}")
                    print(f"❌ Error sending to {profile['email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing {profile['email']}: {str(e)}")
                print(f"❌ Error processing {profile['email']}: {str(e)}")
                continue

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Processed {len(incomplete_profiles)} incomplete profiles',
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_onboarding_reminders: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@email_cron_bp.route('/send-new-brands-notification', methods=['POST'])
def send_new_brands_notification():
    """
    Send notification to creators when new brands are added to the directory
    Finds brands added in the last 24 hours and notifies active creators
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Find brands added in the last 24 hours
        cursor.execute("""
            SELECT id, brand_name, slug, category, logo_url, created_at
            FROM brands
            WHERE created_at > NOW() - INTERVAL '24 hours'
              AND is_public = true
            ORDER BY created_at DESC
            LIMIT 10
        """)

        new_brands = cursor.fetchall()

        if not new_brands:
            return jsonify({
                'success': True,
                'message': 'No new brands to notify about',
                'sent': 0
            }), 200

        # Find active creators who should be notified (with global cooloff)
        # Active = logged in within last 30 days, email verified, has completed profile
        cursor.execute("""
            SELECT DISTINCT c.id, u.email, c.username, c.last_new_brands_email_sent
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.instagram_handle IS NOT NULL
              AND c.instagram_handle != ''
              AND u.last_login > NOW() - INTERVAL '30 days'
              AND (
                c.last_new_brands_email_sent IS NULL
                OR c.last_new_brands_email_sent < NOW() - INTERVAL '7 days'
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 100
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        creators = cursor.fetchall()

        sent_count = 0
        errors = []

        # Build brands list HTML for email
        brands_html = ""
        for brand in new_brands:
            brands_html += f"""
            <div style="margin-bottom: 20px; padding: 16px; background-color: #f9fafb; border-radius: 8px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    {'<img src="' + brand['logo_url'] + '" style="width: 48px; height: 48px; border-radius: 8px; object-fit: cover;">' if brand['logo_url'] else ''}
                    <div>
                        <h4 style="margin: 0; color: #1f2937; font-size: 16px; font-weight: 600;">{brand['brand_name']}</h4>
                        <p style="margin: 4px 0 0; color: #6b7280; font-size: 14px;">{brand['category'].replace('_', ' ').title()}</p>
                    </div>
                </div>
                <a href="https://newcollab.co/brand/{brand['slug']}" style="display: inline-block; margin-top: 12px; color: #667eea; text-decoration: none; font-weight: 500; font-size: 14px;">View Brand →</a>
            </div>
            """

        for creator in creators:
            try:
                context = {
                    'message': f"<p>Hey {creator['username'] or 'there'}! 👋</p><p>We just added {len(new_brands)} new brand{'s' if len(new_brands) > 1 else ''} to the directory that might be perfect for you:</p>" + brands_html,
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/directory",
                    'action_text': "Browse New Brands",
                    'user_id': creator['id'],
                    'email_header_title': f"{len(new_brands)} New Brand{'s' if len(new_brands) > 1 else ''} Just Added!",
                    'email_header_subtitle': "Check them out before other creators do"
                }

                success, error = send_template_email(
                    to_email=creator['email'],
                    template_name='onboarding_reminder.html',  # Reuse this template as it's flexible
                    subject=f"🎉 {len(new_brands)} new PR brands just added to NewCollab!",
                    context=context
                )

                if success:
                    # Update last_new_brands_email_sent and global timestamp
                    cursor.execute("""
                        UPDATE creators
                        SET last_new_brands_email_sent = NOW(),
                            last_any_email_sent = NOW()
                        WHERE id = %s
                    """, (creator['id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent new brands notification to {creator['email']}")
                else:
                    errors.append(f"Error sending to {creator['email']}: {error}")
                    print(f"❌ Error sending to {creator['email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing {creator['email']}: {str(e)}")
                print(f"❌ Error processing {creator['email']}: {str(e)}")
                continue

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Notified {sent_count} creators about {len(new_brands)} new brands',
            'brands_count': len(new_brands),
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_new_brands_notification: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@email_cron_bp.route('/process-pr-reminders', methods=['POST'])
def process_pr_reminders():
    """
    Process PR package reminders
    - 7 days after shipping: remind creator to confirm receipt
    - 48 hours after receipt: remind to start creating content
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        sent_count = 0
        errors = []

        # 1. Find offers shipped 7 days ago that haven't been received
        cursor.execute("""
            SELECT
                pr.id as offer_id,
                pr.status,
                pr.shipped_at,
                pr.brand_id,
                pr.creator_id,
                b.brand_name,
                c.username as creator_name,
                u.email as creator_email
            FROM pr_offers pr
            JOIN brands b ON pr.brand_id = b.id
            JOIN creators c ON pr.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE pr.status = 'shipped'
              AND pr.shipped_at < NOW() - INTERVAL '7 days'
              AND pr.shipped_at > NOW() - INTERVAL '8 days'
              AND NOT EXISTS (
                SELECT 1 FROM pr_email_reminders
                WHERE offer_id = pr.id
                AND reminder_type = 'product_received_check'
              )
            LIMIT 50
        """)

        shipped_offers = cursor.fetchall()

        for offer in shipped_offers:
            try:
                context = {
                    'message': f"<p>Hey {offer['creator_name']}! 👋</p><p>It's been a week since {offer['brand_name']} shipped your PR package. Have you received it yet?</p><p>Please confirm receipt so the brand knows their package arrived safely.</p>",
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/creator/dashboard/pr-pipeline",
                    'action_text': "Confirm Receipt",
                    'user_id': offer['creator_id'],
                    'email_header_title': "Did your PR package arrive?",
                    'email_header_subtitle': f"Update from {offer['brand_name']}"
                }

                success, error = send_template_email(
                    to_email=offer['creator_email'],
                    template_name='onboarding_reminder.html',
                    subject=f"Did you receive your PR package from {offer['brand_name']}?",
                    context=context
                )

                if success:
                    # Record reminder sent
                    cursor.execute("""
                        INSERT INTO pr_email_reminders (offer_id, reminder_type, sent_at)
                        VALUES (%s, 'product_received_check', NOW())
                    """, (offer['offer_id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent receipt reminder to {offer['creator_email']}")
                else:
                    errors.append(f"Error sending to {offer['creator_email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing offer {offer['offer_id']}: {str(e)}")
                continue

        # 2. Find offers received 48 hours ago that haven't started content
        cursor.execute("""
            SELECT
                pr.id as offer_id,
                pr.status,
                pr.product_received_at,
                pr.brand_id,
                pr.creator_id,
                b.brand_name,
                c.username as creator_name,
                u.email as creator_email
            FROM pr_offers pr
            JOIN brands b ON pr.brand_id = b.id
            JOIN creators c ON pr.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE pr.status IN ('product_received', 'content_in_progress')
              AND pr.product_received_at < NOW() - INTERVAL '48 hours'
              AND pr.product_received_at > NOW() - INTERVAL '72 hours'
              AND NOT EXISTS (
                SELECT 1 FROM pr_email_reminders
                WHERE offer_id = pr.id
                AND reminder_type = 'start_content'
              )
            LIMIT 50
        """)

        received_offers = cursor.fetchall()

        for offer in received_offers:
            try:
                context = {
                    'message': f"<p>Hey {offer['creator_name']}! 👋</p><p>Now that you've received your PR package from {offer['brand_name']}, it's time to create some amazing content!</p><p>Brands love seeing content within 2-3 days of receipt. Let's keep that momentum going! 🚀</p>",
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/creator/dashboard/pr-pipeline",
                    'action_text': "Update Status",
                    'user_id': offer['creator_id'],
                    'email_header_title': "Ready to create content?",
                    'email_header_subtitle': f"Your PR package from {offer['brand_name']} is waiting!"
                }

                success, error = send_template_email(
                    to_email=offer['creator_email'],
                    template_name='onboarding_reminder.html',
                    subject=f"Time to create content for {offer['brand_name']}! 🎥",
                    context=context
                )

                if success:
                    # Record reminder sent
                    cursor.execute("""
                        INSERT INTO pr_email_reminders (offer_id, reminder_type, sent_at)
                        VALUES (%s, 'start_content', NOW())
                    """, (offer['offer_id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent content reminder to {offer['creator_email']}")
                else:
                    errors.append(f"Error sending to {offer['creator_email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing offer {offer['offer_id']}: {str(e)}")
                continue

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Processed {len(shipped_offers) + len(received_offers)} PR reminders',
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in process_pr_reminders: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================================================
# EMAIL CONVERSION SEQUENCE
# 5 behavioral emails to convert free users to Creator Pro ($12/month)
# =============================================================================

@email_cron_bp.route('/send-first-pitch-nudge', methods=['POST'])
def send_first_pitch_nudge():
    """
    Email 1: First Pitch Nudge
    Target: Signed up 24h+ ago, never sent a pitch, email verified
    Cron: Daily at 10am UTC
    Add ?test=true to only send to team@newcollab.co
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">You signed up but haven't reached out to a brand yet. Totally normal. Most people browse first.</p>
                <p style="margin: 0 0 16px;">But here's the thing: creators with smaller followings are landing PR packages every week through Newcollab. The brands in our directory actually want to hear from you.</p>
                <p style="margin: 0 0 16px;">Pick one brand. Send one pitch. See what happens.</p>
                <p style="margin: 0;">We write the pitch for you, so it takes about 2 minutes.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Browse brands',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Your first brand is waiting',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Global cooloff: only select creators who haven't received ANY email in 24h
        cursor.execute("""
            SELECT c.id, u.email, c.username
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.first_pitch_sent_at IS NULL
              AND c.created_at < NOW() - INTERVAL '24 hours'
              AND (
                c.last_reminder_sent IS NULL
                OR c.last_reminder_sent < NOW() - INTERVAL '48 hours'
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            ORDER BY c.created_at ASC
            LIMIT 50
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        creators = cursor.fetchall()
        sent_count = 0
        errors = []

        for creator in creators:
            name = creator['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">You signed up but haven't reached out to a brand yet. Totally normal. Most people browse first.</p>
                    <p style="margin: 0 0 16px;">But here's the thing: creators with smaller followings are landing PR packages every week through Newcollab. The brands in our directory actually want to hear from you.</p>
                    <p style="margin: 0 0 16px;">Pick one brand. Send one pitch. See what happens.</p>
                    <p style="margin: 0;">We write the pitch for you, so it takes about 2 minutes.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Browse brands',
                'user_id': creator['id']
            }

            success, error = send_template_email(
                to_email=creator['email'],
                template_name='conversion_email.html',
                subject='Your first brand is waiting',
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_reminder_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (creator['id'],))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent first pitch nudge to {creator['email']}")
            else:
                errors.append(f"{creator['email']}: {error}")
                print(f"❌ Error sending to {creator['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_first_pitch_nudge: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/send-limit-warning', methods=['POST'])
def send_limit_warning():
    """
    Email 2: Limit Warning (1 contact left)
    Target: Free tier, pitches_sent_this_week = 2, warning not sent this month
    Cron: Daily at 11am UTC
    Add ?test=true to only send to team@newcollab.co
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">Quick heads up: you've used 2 of your 3 free brand contacts this month. One left.</p>
                <p style="margin: 0 0 16px;">Once it's gone, you'll need to wait until next month to pitch more brands.</p>
                <p style="margin: 0 0 16px;">If you want to keep going without limits, Pro is $12/month. You get unlimited pitches, unlimited brand contacts, and full access to every PR contact in the directory.</p>
                <p style="margin: 0;">No pressure either way. Just wanted to give you a heads up before it resets.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Go Pro for $12/mo',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] 1 free contact left this month',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        cursor.execute("""
            SELECT c.id, u.email, c.username
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.subscription_tier = 'free'
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.pitches_sent_this_week = 2
              AND (
                c.last_limit_warning_sent IS NULL
                OR c.last_limit_warning_sent < %s
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 100
        """, (month_start, GLOBAL_EMAIL_COOLDOWN_HOURS))

        creators = cursor.fetchall()
        sent_count = 0
        errors = []

        for creator in creators:
            name = creator['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">Quick heads up: you've used 2 of your 3 free brand contacts this month. One left.</p>
                    <p style="margin: 0 0 16px;">Once it's gone, you'll need to wait until next month to pitch more brands.</p>
                    <p style="margin: 0 0 16px;">If you want to keep going without limits, Pro is $12/month. You get unlimited pitches, unlimited brand contacts, and full access to every PR contact in the directory.</p>
                    <p style="margin: 0;">No pressure either way. Just wanted to give you a heads up before it resets.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Go Pro for $12/mo',
                'user_id': creator['id']
            }

            success, error = send_template_email(
                to_email=creator['email'],
                template_name='conversion_email.html',
                subject='1 free contact left this month',
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_limit_warning_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (creator['id'],))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent limit warning to {creator['email']}")
            else:
                errors.append(f"{creator['email']}: {error}")
                print(f"❌ Error sending to {creator['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_limit_warning: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/send-limit-reached', methods=['POST'])
def send_limit_reached():
    """
    Email 3: Limit Reached (hard upgrade push)
    Target: Free tier, pitches_sent_this_week >= 3, upgrade email not sent this month
    Cron: Daily at 11:30am UTC
    Note: Highest-intent moment — user is actively trying to pitch
    Add ?test=true to only send to team@newcollab.co
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">You've used all 3 of your free brand contacts this month.</p>
                <p style="margin: 0 0 16px;">That actually says something good about you. You're out there pitching, which is exactly how creators land PR packages.</p>
                <p style="margin: 0 0 16px;">Your contacts will reset next month. But if you don't want to wait, Pro removes the limit entirely. $12/month, unlimited pitches, cancel anytime.</p>
                <p style="margin: 0;">Either way, nice work reaching out to brands. Most people never get this far.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Upgrade to Pro',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject="[TEST] You've hit your free limit",
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        cursor.execute("""
            SELECT c.id, u.email, c.username
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.subscription_tier = 'free'
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.pitches_sent_this_week >= 3
              AND (
                c.last_upgrade_email_sent IS NULL
                OR c.last_upgrade_email_sent < %s
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 100
        """, (month_start, GLOBAL_EMAIL_COOLDOWN_HOURS))

        creators = cursor.fetchall()
        sent_count = 0
        errors = []

        for creator in creators:
            name = creator['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">You've used all 3 of your free brand contacts this month.</p>
                    <p style="margin: 0 0 16px;">That actually says something good about you. You're out there pitching, which is exactly how creators land PR packages.</p>
                    <p style="margin: 0 0 16px;">Your contacts will reset next month. But if you don't want to wait, Pro removes the limit entirely. $12/month, unlimited pitches, cancel anytime.</p>
                    <p style="margin: 0;">Either way, nice work reaching out to brands. Most people never get this far.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Upgrade to Pro',
                'user_id': creator['id']
            }

            success, error = send_template_email(
                to_email=creator['email'],
                template_name='conversion_email.html',
                subject="You've hit your free limit",
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_upgrade_email_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (creator['id'],))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent limit reached email to {creator['email']}")
            else:
                errors.append(f"{creator['email']}: {error}")
                print(f"❌ Error sending to {creator['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_limit_reached: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/send-reengagement', methods=['POST'])
def send_reengagement():
    """
    Email 4: Re-engagement (never pitched, gone quiet)
    Target: Registered 7+ days ago, never sent a pitch, no re-engagement email in 14 days
    Cron: Weekly (Mondays) at 9am UTC
    Add ?test=true to only send to team@newcollab.co
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">You haven't reached out to a brand yet, and that's totally fine. A lot of people take their time.</p>
                <p style="margin: 0 0 16px;">Just wanted to remind you that the brands in our directory work with creators of all sizes. We're talking 500-5k followers landing free products regularly.</p>
                <p style="margin: 0 0 16px;">You still have 3 free contacts available. We handle the pitch writing, so it's really just picking a brand and hitting send.</p>
                <p style="margin: 0;">Whenever you're ready.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Browse brands',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Still have 3 free contacts',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT c.id, u.email, c.username
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.first_pitch_sent_at IS NULL
              AND c.created_at < NOW() - INTERVAL '7 days'
              AND (
                c.last_reengagement_sent IS NULL
                OR c.last_reengagement_sent < NOW() - INTERVAL '14 days'
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            ORDER BY c.created_at ASC
            LIMIT 50
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        creators = cursor.fetchall()
        sent_count = 0
        errors = []

        for creator in creators:
            name = creator['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">You haven't reached out to a brand yet, and that's totally fine. A lot of people take their time.</p>
                    <p style="margin: 0 0 16px;">Just wanted to remind you that the brands in our directory work with creators of all sizes. We're talking 500-5k followers landing free products regularly.</p>
                    <p style="margin: 0 0 16px;">You still have 3 free contacts available. We handle the pitch writing, so it's really just picking a brand and hitting send.</p>
                    <p style="margin: 0;">Whenever you're ready.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Browse brands',
                'user_id': creator['id']
            }

            success, error = send_template_email(
                to_email=creator['email'],
                template_name='conversion_email.html',
                subject='Still have 3 free contacts',
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_reengagement_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (creator['id'],))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent re-engagement email to {creator['email']}")
            else:
                errors.append(f"{creator['email']}: {error}")
                print(f"❌ Error sending to {creator['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_reengagement: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/send-monthly-reset', methods=['POST'])
def send_monthly_reset():
    """
    Email 5: Monthly Reset
    Target: Free users who hit the limit last month
    Cron: 1st of each month at 9am UTC
    Add ?test=true to only send to team@newcollab.co
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">Your 3 free contacts just reset for the month.</p>
                <p style="margin: 0 0 16px;">You used all of them last month, which is great. Most people don't even try. But you actually pitched brands, and that's how you land PR packages.</p>
                <p style="margin: 0 0 16px;">If you want to skip the monthly limit, Pro is $12/month for unlimited contacts. But either way, you've got 3 fresh ones ready to go.</p>
                <p style="margin: 0;">Good luck this month.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Start pitching',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Your contacts just reset',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        last_month_start = (datetime.now().replace(day=1) - timedelta(days=1)).replace(day=1)

        cursor.execute("""
            SELECT c.id, u.email, c.username
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.subscription_tier = 'free'
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND c.pitches_sent_this_week >= 3
              AND (
                c.last_monthly_reset_sent IS NULL
                OR c.last_monthly_reset_sent < %s
              )
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 100
        """, (last_month_start, GLOBAL_EMAIL_COOLDOWN_HOURS))

        creators = cursor.fetchall()
        sent_count = 0
        errors = []

        for creator in creators:
            name = creator['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">Your 3 free contacts just reset for the month.</p>
                    <p style="margin: 0 0 16px;">You used all of them last month, which is great. Most people don't even try. But you actually pitched brands, and that's how you land PR packages.</p>
                    <p style="margin: 0 0 16px;">If you want to skip the monthly limit, Pro is $12/month for unlimited contacts. But either way, you've got 3 fresh ones ready to go.</p>
                    <p style="margin: 0;">Good luck this month.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Start pitching',
                'user_id': creator['id']
            }

            success, error = send_template_email(
                to_email=creator['email'],
                template_name='conversion_email.html',
                subject='Your contacts just reset',
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_monthly_reset_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (creator['id'],))
                conn.commit()
                sent_count += 1
                print(f"✅ Sent monthly reset email to {creator['email']}")
            else:
                errors.append(f"{creator['email']}: {error}")
                print(f"❌ Error sending to {creator['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in send_monthly_reset: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# PR PIPELINE EMAIL CRON ENDPOINTS
# ============================================

@email_cron_bp.route('/pipeline-followup-nudge', methods=['POST'])
def pipeline_followup_nudge():
    """
    Day 7 - Follow-up Nudge (Free + Pro)
    Sends to ALL users who have a brand in 'waiting' or 'followup' stage
    pitched exactly 7 days ago and have not already received this nudge.
    Cron: Daily at 09:00
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">You pitched <strong>Test Brand</strong> 7 days ago. Brands that receive a follow-up are 2x more likely to respond.</p>
                <p style="margin: 0 0 16px;">Test Brand has a <strong>45% response rate</strong> - worth the nudge.</p>
                <p style="margin: 0;">Open your pipeline and send a follow-up to double your chances.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
            'action_text': 'Send Follow-up',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Time to follow up with Test Brand',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                cp.id AS pipeline_id,
                cp.followup_count,
                c.id AS creator_id, u.email, c.username,
                pb.brand_name, pb.response_rate
            FROM creator_pipeline cp
            JOIN creators c ON c.id = cp.creator_id
            JOIN users u ON u.id = c.user_id
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage IN ('waiting', 'followup', 'pitched')
              AND (cp.send_confirmed = TRUE OR cp.pitched_at IS NOT NULL)
              AND cp.pitched_at::date = (NOW() - INTERVAL '7 days')::date
              AND cp.followup_sent_at IS NULL
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 200
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        rows = cursor.fetchall()
        sent_count = 0
        errors = []

        for row in rows:
            name = row['username'] or 'there'
            response_rate = row['response_rate'] or 'unknown'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">You pitched <strong>{row['brand_name']}</strong> 7 days ago. Brands that receive a follow-up are 2x more likely to respond.</p>
                    <p style="margin: 0 0 16px;">{row['brand_name']} has a <strong>{response_rate}% response rate</strong> - worth the nudge.</p>
                    <p style="margin: 0;">Open your pipeline and send a follow-up to double your chances.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
                'action_text': 'Send Follow-up',
                'user_id': row['creator_id']
            }

            success, error = send_template_email(
                to_email=row['email'],
                template_name='conversion_email.html',
                subject=f"Time to follow up with {row['brand_name']}",
                context=context
            )

            if success:
                cursor.execute("""
                    UPDATE creator_pipeline SET followup_sent_at = NOW() WHERE id = %s
                """, (row['pipeline_id'],))
                mark_email_sent(cursor, conn, row['creator_id'], 'pipeline_followup_nudge')
                sent_count += 1
                print(f"✅ Sent pipeline follow-up nudge to {row['email']}")
            else:
                errors.append(f"{row['email']}: {error}")
                print(f"❌ Error sending to {row['email']}: {error}")

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in pipeline_followup_nudge: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/pipeline-status-check', methods=['POST'])
def pipeline_status_check():
    """
    Day 14 - Status Check (Free + Pro)
    Sends to users who pitched 14 days ago with no reply logged yet.
    Goal: bring them back to update their pipeline status.
    Cron: Daily at 09:00
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">It's been 2 weeks since you pitched <strong>Test Brand</strong>.</p>
                <p style="margin: 0 0 16px;">Did they get back to you?</p>
                <p style="margin: 0;">Tap "They Replied" or "Send Follow-up" to keep your pipeline accurate.</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
            'action_text': 'Update Pipeline',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Did Test Brand reply? Update your pipeline',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT cp.id AS pipeline_id, c.id AS creator_id, u.email, c.username, pb.brand_name
            FROM creator_pipeline cp
            JOIN creators c ON c.id = cp.creator_id
            JOIN users u ON u.id = c.user_id
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage IN ('waiting', 'followup', 'pitched')
              AND (cp.send_confirmed = TRUE OR cp.pitched_at IS NOT NULL)
              AND cp.pitched_at::date = (NOW() - INTERVAL '14 days')::date
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 200
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        rows = cursor.fetchall()
        sent_count = 0
        errors = []

        for row in rows:
            name = row['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">It's been 2 weeks since you pitched <strong>{row['brand_name']}</strong>.</p>
                    <p style="margin: 0 0 16px;">Did they get back to you?</p>
                    <p style="margin: 0;">Tap "They Replied" or "Send Follow-up" to keep your pipeline accurate.</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
                'action_text': 'Update Pipeline',
                'user_id': row['creator_id']
            }

            success, error = send_template_email(
                to_email=row['email'],
                template_name='conversion_email.html',
                subject=f"Did {row['brand_name']} reply? Update your pipeline",
                context=context
            )

            if success:
                mark_email_sent(cursor, conn, row['creator_id'], 'pipeline_status_check')
                sent_count += 1
                print(f"✅ Sent pipeline status check to {row['email']}")
            else:
                errors.append(f"{row['email']}: {error}")
                print(f"❌ Error sending to {row['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in pipeline_status_check: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/pipeline-discover-nudge', methods=['POST'])
def pipeline_discover_nudge():
    """
    Day 21 - Discover New Brands (Pro only)
    Sends to Pro users 21 days after pitching with no response.
    Suggests 3 similar brands with higher response rates -> drives back to Discover.
    Cron: Daily at 09:00
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">It's been 3 weeks since your <strong>Test Brand</strong> pitch. Some brands are just slow - but here are 3 in the same category with higher response rates:</p>
                <ul style="margin: 0 0 16px; padding-left: 20px;">
                    <li><strong>Brand A</strong> - 65% response rate</li>
                    <li><strong>Brand B</strong> - 55% response rate</li>
                    <li><strong>Brand C</strong> - 50% response rate</li>
                </ul>
                <p style="margin: 0;">Check them out and keep the momentum going!</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
            'action_text': 'Discover These Brands',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Test Brand may be slow - here are 3 better options',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                cp.id AS pipeline_id, c.id AS creator_id, u.email, c.username,
                pb.brand_name, pb.category
            FROM creator_pipeline cp
            JOIN creators c ON c.id = cp.creator_id
            JOIN users u ON u.id = c.user_id
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage IN ('waiting', 'followup', 'pitched')
              AND (cp.send_confirmed = TRUE OR cp.pitched_at IS NOT NULL)
              AND cp.pitched_at::date = (NOW() - INTERVAL '21 days')::date
              AND c.subscription_tier IN ('pro', 'elite')
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 200
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        rows = cursor.fetchall()
        sent_count = 0
        errors = []

        for row in rows:
            # Fetch 3 alternative brands in same category with higher response rate
            cursor.execute("""
                SELECT brand_name, response_rate, slug
                FROM pr_brands
                WHERE category = %s
                  AND response_rate > 35
                  AND id != (SELECT brand_id FROM creator_pipeline WHERE id = %s)
                ORDER BY response_rate DESC LIMIT 3
            """, (row['category'], row['pipeline_id']))
            alternatives = cursor.fetchall()

            alt_html = ""
            for alt in alternatives:
                alt_html += f"<li><strong>{alt['brand_name']}</strong> - {alt['response_rate']}% response rate</li>"

            if not alt_html:
                alt_html = "<li>Check Discover for new options in your niche</li>"

            name = row['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">It's been 3 weeks since your <strong>{row['brand_name']}</strong> pitch. Some brands are just slow - but here are 3 in the same category with higher response rates:</p>
                    <ul style="margin: 0 0 16px; padding-left: 20px;">{alt_html}</ul>
                    <p style="margin: 0;">Check them out and keep the momentum going!</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-brands",
                'action_text': 'Discover These Brands',
                'user_id': row['creator_id']
            }

            success, error = send_template_email(
                to_email=row['email'],
                template_name='conversion_email.html',
                subject=f"{row['brand_name']} may be slow - here are 3 better options",
                context=context
            )

            if success:
                mark_email_sent(cursor, conn, row['creator_id'], 'pipeline_discover_nudge')
                sent_count += 1
                print(f"✅ Sent pipeline discover nudge to {row['email']}")
            else:
                errors.append(f"{row['email']}: {error}")
                print(f"❌ Error sending to {row['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in pipeline_discover_nudge: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/pipeline-package-nudge', methods=['POST'])
def pipeline_package_nudge():
    """
    Package Arrival Nudge
    Sends when expected_delivery date is reached and stage is still 'won'.
    Invites creator back to mark package as received.
    Cron: Daily at 09:00
    """
    test_mode = request.args.get('test', '').lower() == 'true'
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    if test_mode:
        context = {
            'message': """
                <p style="margin: 0 0 16px;">Hey there,</p>
                <p style="margin: 0 0 16px;">Your collab with <strong>Test Brand</strong> was confirmed and your package should be arriving around now.</p>
                <p style="margin: 0 0 16px;">Once it arrives, mark it as received in your pipeline.</p>
                <p style="margin: 0;">Don't forget to log it - your PR value stat depends on it!</p>
            """,
            'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
            'action_text': 'Mark as Received',
            'user_id': 0
        }
        success, error = send_template_email(
            to_email=TEST_EMAIL,
            template_name='conversion_email.html',
            subject='[TEST] Your Test Brand package should be arriving!',
            context=context
        )
        return jsonify({
            'success': success,
            'test_mode': True,
            'sent_to': TEST_EMAIL,
            'error': error if not success else None
        }), 200 if success else 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT cp.id AS pipeline_id, c.id AS creator_id, u.email, c.username, pb.brand_name
            FROM creator_pipeline cp
            JOIN creators c ON c.id = cp.creator_id
            JOIN users u ON u.id = c.user_id
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage IN ('won', 'success')
              AND cp.expected_delivery <= NOW()::date
              AND cp.received_at IS NULL
              AND u.is_verified = true
              AND u.unsubscribed_at IS NULL
              AND (
                c.last_any_email_sent IS NULL
                OR c.last_any_email_sent < NOW() - INTERVAL '%s hours'
              )
            LIMIT 200
        """, (GLOBAL_EMAIL_COOLDOWN_HOURS,))

        rows = cursor.fetchall()
        sent_count = 0
        errors = []

        for row in rows:
            name = row['username'] or 'there'
            context = {
                'message': f"""
                    <p style="margin: 0 0 16px;">Hey {name},</p>
                    <p style="margin: 0 0 16px;">Your collab with <strong>{row['brand_name']}</strong> was confirmed and your package should be arriving around now.</p>
                    <p style="margin: 0 0 16px;">Once it arrives, mark it as received in your pipeline.</p>
                    <p style="margin: 0;">Don't forget to log it - your PR value stat depends on it!</p>
                """,
                'action_url': f"{APP_URL}/creator/dashboard/pr-pipeline",
                'action_text': 'Mark as Received',
                'user_id': row['creator_id']
            }

            success, error = send_template_email(
                to_email=row['email'],
                template_name='conversion_email.html',
                subject=f"Your {row['brand_name']} package should be arriving!",
                context=context
            )

            if success:
                mark_email_sent(cursor, conn, row['creator_id'], 'pipeline_package_nudge')
                sent_count += 1
                print(f"✅ Sent pipeline package nudge to {row['email']}")
            else:
                errors.append(f"{row['email']}: {error}")
                print(f"❌ Error sending to {row['email']}: {error}")

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'errors': len(errors),
            'error_details': errors[:5]
        }), 200

    except Exception as e:
        print(f"❌ Error in pipeline_package_nudge: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@email_cron_bp.route('/pipeline-confirm-send-test', methods=['POST'])
def pipeline_confirm_send_test():
    """
    Test-only endpoint for the pitch confirmation email sent after a creator
    confirms they contacted a brand (confirm-send flow).
    Always sends to team@newcollab.co.
    """
    TEST_EMAIL = 'team@newcollab.co'
    APP_URL = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')

    subject = '[TEST] ✓ You contacted Test Brand Co.!'
    context = {
        'subject': subject,
        'preheader': "Step 1 done — you just reached out to Test Brand Co. Here's what to do next.",
        'message': """
            <p style="margin: 0 0 16px;">Hey Test Creator,</p>
            <p style="margin: 0 0 16px;">You just contacted <strong>Test Brand Co.</strong> — that's step 1 complete! 🎉</p>
            <p style="margin: 0 0 12px;">Here's what happens next:</p>
            <ul style="text-align: left; margin: 0 0 16px; padding-left: 20px; color: #1d1d1f;">
                <li style="margin-bottom: 6px;">We'll remind you to follow up in 7 days if you haven't heard back</li>
                <li style="margin-bottom: 6px;">Most brands respond within 1–2 weeks</li>
                <li style="margin-bottom: 6px;">Track your pipeline and log any replies in your dashboard</li>
            </ul>
            <p style="margin: 0; color: #059669; font-weight: 600;">Keep the momentum going — the more brands you contact, the more packages you'll land! 📦</p>
        """,
        'action_url': f'{APP_URL}/creator/dashboard/pr-pipeline',
        'action_text': 'Track My Pipeline',
    }

    success, error = send_template_email(
        to_email=TEST_EMAIL,
        template_name='conversion_email.html',
        subject=subject,
        context=context
    )

    return jsonify({
        'success': success,
        'test_mode': True,
        'sent_to': TEST_EMAIL,
        'error': error if not success else None
    }), 200 if success else 500
