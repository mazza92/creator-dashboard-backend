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

        # Find incomplete profiles
        cursor.execute("""
            SELECT c.id, u.email, c.username, c.created_at, c.last_reminder_sent
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.email_verified = true
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
            LIMIT 50
        """)

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
                    subject="Complete your NewCollab profile to unlock 229+ PR brands",
                    context=context
                )

                if success:
                    # Update last_reminder_sent timestamp
                    cursor.execute("""
                        UPDATE creators
                        SET last_reminder_sent = NOW()
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

        # Find active creators who should be notified
        # Active = logged in within last 30 days, email verified, has completed profile
        cursor.execute("""
            SELECT DISTINCT c.id, u.email, c.username, c.last_new_brands_email_sent
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.email_verified = true
              AND c.instagram_handle IS NOT NULL
              AND c.instagram_handle != ''
              AND u.last_login > NOW() - INTERVAL '30 days'
              AND (
                c.last_new_brands_email_sent IS NULL
                OR c.last_new_brands_email_sent < NOW() - INTERVAL '7 days'
              )
            LIMIT 100
        """)

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
                    # Update last_new_brands_email_sent timestamp
                    cursor.execute("""
                        UPDATE creators
                        SET last_new_brands_email_sent = NOW()
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

        # 1. Find packages shipped 7 days ago that haven't been received
        cursor.execute("""
            SELECT
                pr.id as package_id,
                pr.status,
                pr.shipped_date,
                pr.brand_id,
                pr.creator_id,
                b.brand_name,
                c.username as creator_name,
                u.email as creator_email
            FROM pr_packages pr
            JOIN brands b ON pr.brand_id = b.id
            JOIN creators c ON pr.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE pr.status = 'shipped'
              AND pr.shipped_date < NOW() - INTERVAL '7 days'
              AND pr.shipped_date > NOW() - INTERVAL '8 days'
              AND NOT EXISTS (
                SELECT 1 FROM pr_email_reminders
                WHERE package_id = pr.id
                AND reminder_type = 'product_received_check'
              )
            LIMIT 50
        """)

        shipped_packages = cursor.fetchall()

        for package in shipped_packages:
            try:
                context = {
                    'message': f"<p>Hey {package['creator_name']}! 👋</p><p>It's been a week since {package['brand_name']} shipped your PR package. Have you received it yet?</p><p>Please confirm receipt so the brand knows their package arrived safely.</p>",
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/creator/dashboard/pr-pipeline",
                    'action_text': "Confirm Receipt",
                    'user_id': package['creator_id'],
                    'email_header_title': "Did your PR package arrive?",
                    'email_header_subtitle': f"Update from {package['brand_name']}"
                }

                success, error = send_template_email(
                    to_email=package['creator_email'],
                    template_name='onboarding_reminder.html',
                    subject=f"Did you receive your PR package from {package['brand_name']}?",
                    context=context
                )

                if success:
                    # Record reminder sent
                    cursor.execute("""
                        INSERT INTO pr_email_reminders (package_id, reminder_type, sent_at)
                        VALUES (%s, 'product_received_check', NOW())
                    """, (package['package_id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent receipt reminder to {package['creator_email']}")
                else:
                    errors.append(f"Error sending to {package['creator_email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing package {package['package_id']}: {str(e)}")
                continue

        # 2. Find packages received 48 hours ago that haven't started content
        cursor.execute("""
            SELECT
                pr.id as package_id,
                pr.status,
                pr.received_date,
                pr.brand_id,
                pr.creator_id,
                b.brand_name,
                c.username as creator_name,
                u.email as creator_email
            FROM pr_packages pr
            JOIN brands b ON pr.brand_id = b.id
            JOIN creators c ON pr.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE pr.status IN ('product_received', 'content_in_progress')
              AND pr.received_date < NOW() - INTERVAL '48 hours'
              AND pr.received_date > NOW() - INTERVAL '72 hours'
              AND NOT EXISTS (
                SELECT 1 FROM pr_email_reminders
                WHERE package_id = pr.id
                AND reminder_type = 'start_content'
              )
            LIMIT 50
        """)

        received_packages = cursor.fetchall()

        for package in received_packages:
            try:
                context = {
                    'message': f"<p>Hey {package['creator_name']}! 👋</p><p>Now that you've received your PR package from {package['brand_name']}, it's time to create some amazing content!</p><p>Brands love seeing content within 2-3 days of receipt. Let's keep that momentum going! 🚀</p>",
                    'action_url': f"{os.getenv('FRONTEND_URL', 'https://newcollab.co')}/creator/dashboard/pr-pipeline",
                    'action_text': "Update Status",
                    'user_id': package['creator_id'],
                    'email_header_title': "Ready to create content?",
                    'email_header_subtitle': f"Your PR package from {package['brand_name']} is waiting!"
                }

                success, error = send_template_email(
                    to_email=package['creator_email'],
                    template_name='onboarding_reminder.html',
                    subject=f"Time to create content for {package['brand_name']}! 🎥",
                    context=context
                )

                if success:
                    # Record reminder sent
                    cursor.execute("""
                        INSERT INTO pr_email_reminders (package_id, reminder_type, sent_at)
                        VALUES (%s, 'start_content', NOW())
                    """, (package['package_id'],))
                    conn.commit()

                    sent_count += 1
                    print(f"✅ Sent content reminder to {package['creator_email']}")
                else:
                    errors.append(f"Error sending to {package['creator_email']}: {error}")

            except Exception as e:
                errors.append(f"Error processing package {package['package_id']}: {str(e)}")
                continue

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Processed {len(shipped_packages) + len(received_packages)} PR reminders',
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
