"""
Subscription Routes for Creator PR CRM
Handles Stripe subscription checkout and management
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import get_jwt_identity
import stripe
import os
import re
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from psycopg2.extras import RealDictCursor
import json

# GA4 Measurement Protocol configuration
GA4_MEASUREMENT_ID = os.getenv('GA4_MEASUREMENT_ID', 'G-XXXXXXXXXX')  # e.g., G-ABC123XYZ
GA4_API_SECRET = os.getenv('GA4_API_SECRET')  # From GA4 Admin > Data Streams > Measurement Protocol API secrets


def send_ga4_event(client_id, event_name, params=None):
    """
    Send server-side event to GA4 using Measurement Protocol.
    Used for tracking revenue events that happen on the backend (e.g., Stripe webhooks).
    """
    if not GA4_API_SECRET:
        print(f"⚠️  GA4_API_SECRET not set, skipping {event_name} event")
        return False

    url = f"https://www.google-analytics.com/mp/collect?measurement_id={GA4_MEASUREMENT_ID}&api_secret={GA4_API_SECRET}"

    payload = {
        "client_id": client_id,
        "events": [{
            "name": event_name,
            "params": params or {}
        }]
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 204:
            print(f"✅ GA4 event sent: {event_name}")
            return True
        else:
            print(f"⚠️  GA4 event failed ({response.status_code}): {event_name}")
            return False
    except Exception as e:
        print(f"❌ Error sending GA4 event: {e}")
        return False


def send_pro_subscriber_notification(creator_data, tier, amount, interval):
    """
    Send internal email notification to team@newcollab.co when a new Pro subscriber signs up.
    """
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        if not smtp_username or not smtp_password:
            print("⚠️  SMTP credentials not set, skipping Pro subscriber notification")
            return False

        to_email = 'team@newcollab.co'

        # Format amount (convert from cents)
        amount_formatted = f"${amount / 100:.2f}" if amount else "N/A"

        # Build email content
        subject = f"🎉 New Pro Subscriber: @{creator_data.get('username', 'Unknown')}"

        html_content = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; max-width: 600px;">
            <h2 style="color: #10b981; margin-bottom: 20px;">🎉 New Pro Subscriber!</h2>

            <div style="background: #f9fafb; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280; width: 120px;">Creator:</td>
                        <td style="padding: 8px 0; font-weight: 600;">@{creator_data.get('username', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Email:</td>
                        <td style="padding: 8px 0;">{creator_data.get('email', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Tier:</td>
                        <td style="padding: 8px 0; font-weight: 600; color: #7c3aed;">{tier.upper()}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Billing:</td>
                        <td style="padding: 8px 0;">{interval.capitalize()}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Amount:</td>
                        <td style="padding: 8px 0; font-weight: 600; color: #10b981;">{amount_formatted}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Followers:</td>
                        <td style="padding: 8px 0;">{creator_data.get('followers', 'N/A'):,}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Creator ID:</td>
                        <td style="padding: 8px 0;">#{creator_data.get('id', 'N/A')}</td>
                    </tr>
                </table>
            </div>

            <p style="color: #6b7280; font-size: 13px;">
                Converted at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}
            </p>
        </body>
        </html>
        """

        text_content = f"""
New Pro Subscriber!

Creator: @{creator_data.get('username', 'N/A')}
Email: {creator_data.get('email', 'N/A')}
Tier: {tier.upper()}
Billing: {interval.capitalize()}
Amount: {amount_formatted}
Followers: {creator_data.get('followers', 'N/A')}
Creator ID: #{creator_data.get('id', 'N/A')}

Converted at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}
        """

        msg = MIMEMultipart('alternative')
        msg['From'] = f"Newcollab <{smtp_username}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_username, to_email, msg.as_string())
        server.quit()

        print(f"✅ Pro subscriber notification sent for @{creator_data.get('username')}")
        return True

    except Exception as e:
        print(f"❌ Error sending Pro subscriber notification: {e}")
        return False


def _send_creator_resend(to_email, subject, html):
    if not to_email:
        return False
    try:
        from services.resend_mail import send_resend_email
        result = send_resend_email(to_email, subject, html)
        ok = bool(result.get('success'))
        if not ok:
            print(f"[retention] Resend failed: {result.get('error')}")
        return ok
    except Exception as exc:
        print(f"[retention] email failed: {exc}")
        return False


def _creator_by_stripe(cursor, subscription_id=None, customer_id=None):
    if subscription_id:
        cursor.execute(
            '''
            SELECT c.id, c.username, u.email, c.stripe_customer_id, c.stripe_subscription_id
            FROM creators c
            JOIN users u ON u.id = c.user_id
            WHERE c.stripe_subscription_id = %s
            ''',
            (subscription_id,),
        )
        row = cursor.fetchone()
        if row:
            return row
    if customer_id:
        cursor.execute(
            '''
            SELECT c.id, c.username, u.email, c.stripe_customer_id, c.stripe_subscription_id
            FROM creators c
            JOIN users u ON u.id = c.user_id
            WHERE c.stripe_customer_id = %s
            ORDER BY c.id DESC
            LIMIT 1
            ''',
            (customer_id,),
        )
        return cursor.fetchone()
    return None


subscription_bp = Blueprint('subscription', __name__, url_prefix='/api/subscription')

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

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
    """Get creator ID from session or JWT"""
    # Try session first
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id

    # Try JWT if session doesn't have it
    try:
        user_id = get_jwt_identity()
        if user_id:
            # Fetch creator_id from user_id
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

def check_subscription_limits(creator_id, action_type):
    """
    Check if creator can perform action based on subscription tier
    action_type: 'save_brand' or 'send_pitch'
    Returns: (allowed: bool, message: str, current_count: int, limit: int)
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute('''
        SELECT subscription_tier, brands_saved_count, pitches_sent_this_week
        FROM creators
        WHERE id = %s
    ''', (creator_id,))
    creator = cursor.fetchone()
    cursor.close()
    conn.close()

    if not creator:
        return False, "Creator not found", 0, 0

    tier = creator['subscription_tier'] or 'free'

    # Free users get 3 unlocks/contacts per MONTH (not week)
    FREE_MONTHLY_LIMIT = 3
    # Pro: unlimited

    if tier == 'free':
        # UNLIMITED: Save/browse brands - let free users explore everything
        if action_type == 'save_brand':
            return True, "", 0, -1  # Unlimited saves for free tier

        # DEPRECATED: New system uses unlocks_remaining in creators table
        if action_type == 'send_pitch':
            count = creator['pitches_sent_this_week'] or 0
            if count >= FREE_MONTHLY_LIMIT:
                return False, f"You've used all your free applications this month. Go Pro and Polly runs your week: pitches from your Gmail, follow-ups, and the reply that turns a yes into paid. Applications are unlimited.", count, FREE_MONTHLY_LIMIT
            return True, "", count, FREE_MONTHLY_LIMIT

    # Pro: unlimited everything
    return True, "", 0, -1  # -1 means unlimited

@subscription_bp.route('/check-limits', methods=['POST'])
def check_limits():
    """Check if user can perform action"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        action_type = request.json.get('action_type')  # 'save_brand' or 'send_pitch'

        allowed, message, current, limit = check_subscription_limits(creator_id, action_type)

        return jsonify({
            'allowed': allowed,
            'message': message,
            'current_count': current,
            'limit': limit,
            'upgrade_required': not allowed
        })
    except Exception as e:
        print(f"Error checking limits: {e}")
        return jsonify({'error': str(e)}), 500

def _log_checkout_source(conn, creator_id, metadata, session_id=None):
    """Which screen sold Pro (polly, directory, ...). Webhook and confirm both call this."""
    try:
        meta = dict(metadata or {})
        cid = int(creator_id)
    except (TypeError, ValueError):
        return
    try:
        from services.polly_usage import log_usage
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM polly_usage_events
            WHERE creator_id = %s AND event = 'checkout_completed' AND meta->>'session_id' = %s
            """,
            (cid, str(session_id or '')),
        )
        if cur.fetchone():
            return
        log_usage(conn, cid, 'checkout_completed', intent=meta.get('source') or 'unknown', meta={
            'source': meta.get('source') or 'unknown',
            'interval': meta.get('interval'),
            'session_id': str(session_id or ''),
        })
    except Exception as err:
        print(f"[subscription] checkout source log skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass


@subscription_bp.route('/create-checkout', methods=['POST'])
def create_checkout_session():
    """Create Stripe Checkout session for subscription"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        body = request.json or {}
        tier = body.get('tier')  # 'pro'
        interval = body.get('interval', 'monthly')  # 'monthly' or 'yearly'
        offer = (body.get('offer') or '').strip().lower()

        if tier != 'pro':
            return jsonify({'error': 'Invalid tier'}), 400

        if interval not in ['monthly', 'yearly']:
            return jsonify({'error': 'Invalid interval'}), 400

        # Get creator email from users table
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT u.email, c.username as name,
                   c.subscription_tier, c.subscription_status,
                   c.stripe_subscription_id, c.stripe_customer_id
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        from services.subscription_retention import (
            get_or_create_retention_coupon,
            qualifies_for_winback_coupon,
            stripe_checkout_customer_kwargs,
            stripe_checkout_promo_kwargs,
        )

        apply_winback = (
            offer == 'winback'
            and tier == 'pro'
            and qualifies_for_winback_coupon(creator)
        )
        if apply_winback:
            interval = 'monthly'

        # Get price ID from environment based on interval
        if interval == 'yearly':
            price_id = os.getenv('STRIPE_PRICE_ID_PRO_ANNUAL')
            if not price_id:
                # Fallback to monthly if annual not configured
                price_id = os.getenv('STRIPE_PRICE_ID_PRO')
                interval = 'monthly'
        else:
            price_id = os.getenv('STRIPE_PRICE_ID_PRO')

        if not price_id:
            return jsonify({'error': 'Price ID not configured. Please set STRIPE_PRICE_ID_PRO in environment variables.'}), 500

        # Create Stripe Checkout Session
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000').rstrip('/')
        coupon_id = get_or_create_retention_coupon(stripe) if apply_winback else None
        metadata = {
            'creator_id': str(creator_id),
            'tier': tier,
            'interval': interval,
            'creator_name': creator.get('name', ''),
        }
        if apply_winback:
            metadata['offer'] = 'winback'
        source = re.sub(r'[^a-z0-9_]', '', str(body.get('source') or '').lower())[:40]
        if source:
            metadata['source'] = source

        from services.checkout_recovery import checkout_recovery_kwargs

        promo_kwargs = stripe_checkout_promo_kwargs(offer if apply_winback else None, creator, coupon_id)
        checkout_session = stripe.checkout.Session.create(
            **checkout_recovery_kwargs(promo_codes='discounts' not in promo_kwargs),
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f"{frontend_url}/creator/dashboard/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/creator/dashboard/subscription/cancel",
            metadata=metadata,
            **stripe_checkout_customer_kwargs(offer if apply_winback else None, creator),
            **promo_kwargs,
        )

        print(f"✅ Created checkout session for creator {creator_id} - {tier} tier")

        return jsonify({
            'checkout_url': checkout_session.url,
            'session_id': checkout_session.id
        })

    except stripe.error.InvalidRequestError as e:
        print(f"❌ Stripe InvalidRequestError: {e}")
        # Check for specific Stripe account issues
        error_message = str(e)
        if 'cannot currently make live charges' in error_message.lower():
            return jsonify({
                'error': 'Payment processing is temporarily unavailable. Our payment system is being set up. Please try again later or contact support.',
                'code': 'stripe_account_pending'
            }), 503
        return jsonify({'error': error_message}), 400
    except Exception as e:
        print(f"❌ Error creating checkout session: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@subscription_bp.route('/portal', methods=['POST'])
def create_portal_session():
    """Create Stripe Customer Portal session for managing subscription"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        # Get creator's Stripe customer ID
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT stripe_customer_id FROM creators WHERE id = %s', (creator_id,))
        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator or not creator.get('stripe_customer_id'):
            return jsonify({'error': 'No active subscription found'}), 404

        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000').rstrip('/')

        from services.subscription_retention import get_retention_portal_configuration_id

        portal_kwargs = {
            'customer': creator['stripe_customer_id'],
            'return_url': f"{frontend_url}/creator/dashboard/settings",
        }
        portal_config_id = get_retention_portal_configuration_id(stripe)
        if portal_config_id:
            portal_kwargs['configuration'] = portal_config_id

        portal_session = stripe.billing_portal.Session.create(**portal_kwargs)

        return jsonify({'portal_url': portal_session.url})

    except stripe.error.InvalidRequestError as e:
        error_message = str(e)
        print(f"❌ Stripe InvalidRequestError in portal: {e}")

        # Handle test mode customer ID used with live mode keys
        if 'No such customer' in error_message:
            # Clear invalid customer data and reset to free tier
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE creators
                    SET stripe_customer_id = NULL,
                        stripe_subscription_id = NULL,
                        subscription_tier = 'free',
                        subscription_status = 'inactive'
                    WHERE id = %s
                ''', (creator_id,))
                conn.commit()
                cursor.close()
                conn.close()
                print(f"✅ Cleared invalid test-mode Stripe data for creator {creator_id}")
            except Exception as db_error:
                print(f"❌ Error clearing Stripe data: {db_error}")

            return jsonify({
                'error': 'Your subscription data was from a test environment and has been reset. Please subscribe again to access premium features.',
                'code': 'customer_not_found'
            }), 400

        return jsonify({'error': error_message}), 400
    except Exception as e:
        print(f"❌ Error creating portal session: {e}")
        return jsonify({'error': str(e)}), 500

@subscription_bp.route('/confirm-checkout', methods=['POST'])
def confirm_checkout():
    """Confirm and activate subscription from checkout session"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        session_id = request.json.get('session_id')
        if not session_id:
            return jsonify({'error': 'Missing session_id'}), 400

        # Retrieve the checkout session from Stripe
        checkout_session = stripe.checkout.Session.retrieve(session_id)

        payment_ok = checkout_session.payment_status in ('paid', 'no_payment_required')
        session_complete = checkout_session.status == 'complete'
        if not (payment_ok or (session_complete and checkout_session.subscription)):
            return jsonify({'error': 'Payment not completed'}), 400

        # Extract metadata
        tier = checkout_session.metadata.get('tier')
        if tier != 'pro':
            return jsonify({'error': 'Not a subscription checkout'}), 400
        interval = checkout_session.metadata.get('interval', 'monthly')
        subscription_id = checkout_session.subscription
        customer_id = checkout_session.customer

        print(f"✅ Confirming checkout for creator {creator_id} - {tier} tier ({interval})")

        # Update database
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get user email for Meta CAPI
        cursor.execute('''
            SELECT u.email FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        user_row = cursor.fetchone()
        user_email = user_row['email'] if user_row else None

        is_trial = (checkout_session.metadata or {}).get('trial') == '7'
        sub_status = 'trialing' if is_trial else 'active'

        cursor.execute('''
            UPDATE creators
            SET subscription_tier = %s,
                subscription_status = %s,
                stripe_subscription_id = %s,
                stripe_customer_id = %s,
                subscription_started_at = NOW()
            WHERE id = %s
        ''', (tier, sub_status, subscription_id, customer_id, creator_id))
        conn.commit()
        cursor.close()
        _log_checkout_source(conn, creator_id, checkout_session.metadata, session_id)
        conn.close()

        print(f"✅ Activated {tier} subscription for creator {creator_id}")

        # Send Meta Conversions API Purchase event (server-side)
        try:
            from services.meta_capi import send_purchase_event
            value = 152 if interval == 'yearly' else 19
            content_id = f"subscription_{tier}_{interval}"
            content_name = f"NewCollab {tier.title()}" + (" Annual" if interval == 'yearly' else "")

            send_purchase_event(
                email=user_email,
                value=value,
                content_name=content_name,
                content_id=content_id,
                event_id=session_id,  # Dedup with browser pixel
                client_ip=request.remote_addr,
                client_user_agent=request.headers.get('User-Agent'),
            )
        except Exception as capi_err:
            print(f"[META CAPI] Failed to send purchase event: {capi_err}")

        return jsonify({
            'success': True,
            'tier': tier,
            'status': 'active'
        })

    except Exception as e:
        print(f"❌ Error confirming checkout: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@subscription_bp.route('/status', methods=['GET'])
def get_subscription_status():
    """Get current subscription status for logged-in creator"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT
                subscription_tier,
                subscription_status,
                subscription_started_at,
                subscription_ends_at,
                brands_saved_count,
                pitches_sent_this_week,
                last_pitch_reset,
                daily_unlocks_used,
                last_unlock_date
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        from datetime import date
        today = date.today()
        month_start = today.replace(day=1)

        # Check if pitch counter needs reset (new month)
        pitches_used = creator.get('pitches_sent_this_week', 0) if creator else 0
        last_pitch_reset = creator.get('last_pitch_reset') if creator else None
        if creator and (last_pitch_reset is None or last_pitch_reset < month_start):
            pitches_used = 0

        # Check if unlock counter needs reset (new month)
        last_unlock = creator.get('last_unlock_date') if creator else None
        monthly_unlocks = creator.get('daily_unlocks_used', 0) if creator else 0
        if creator and (last_unlock is None or last_unlock < month_start):
            monthly_unlocks = 0

        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        ends_at = creator.get('subscription_ends_at')
        status_value = creator.get('subscription_status', 'inactive')
        cancel_scheduled = bool(
            ends_at
            and status_value in ('active', 'trialing', 'past_due')
            and (creator.get('subscription_tier') or 'free') != 'free'
        )

        return jsonify({
            'tier': creator.get('subscription_tier', 'free'),
            'status': status_value,
            'started_at': creator.get('subscription_started_at').isoformat() if creator.get('subscription_started_at') else None,
            'ends_at': ends_at.isoformat() if ends_at else None,
            'cancel_scheduled': cancel_scheduled,
            'brands_saved_count': creator.get('brands_saved_count', 0),
            'pitches_sent_this_week': pitches_used,  # Monthly reset, keeping field name for compatibility
            'daily_unlocks_used': monthly_unlocks  # Monthly reset, keeping field name for compatibility
        })

    except Exception as e:
        print(f"❌ Error getting subscription status: {e}")
        return jsonify({'error': str(e)}), 500


def _creator_billing_row(cursor, creator_id):
    cursor.execute(
        '''
        SELECT c.id, c.username, c.stripe_subscription_id, c.stripe_customer_id,
               c.subscription_tier, c.subscription_status, u.email
        FROM creators c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        ''',
        (creator_id,),
    )
    return cursor.fetchone()


def _require_paid_creator():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return None, (jsonify({'error': 'Not authenticated'}), 401)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    from services.subscription_retention import ensure_cancel_retention_schema
    ensure_cancel_retention_schema(conn)
    creator = _creator_billing_row(cursor, creator_id)
    if not creator:
        cursor.close()
        conn.close()
        return None, (jsonify({'error': 'Creator not found'}), 404)
    if (creator.get('subscription_tier') or 'free') != 'pro':
        cursor.close()
        conn.close()
        return None, (jsonify({'error': 'No paid subscription to cancel'}), 400)
    if not creator.get('stripe_subscription_id'):
        cursor.close()
        conn.close()
        return None, (jsonify({'error': 'No Stripe subscription on this account'}), 400)
    return (conn, cursor, creator), None


def _unstrike_cancel(subscription_id):
    return stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)


@subscription_bp.route('/cancel-flow/reason', methods=['POST'])
def cancel_flow_reason():
    """Log why they want to leave and return the matching save offer."""
    packed, err = _require_paid_creator()
    if err:
        return err
    conn, cursor, creator = packed
    try:
        from services.subscription_retention import (
            CANCEL_REASONS,
            HOLD_MONTHS,
            log_retention_event,
            offer_for_reason,
            subscription_price_snapshot,
        )

        reason = ((request.json or {}).get('reason') or '').strip()
        if reason not in CANCEL_REASONS:
            return jsonify({'error': 'Pick a cancel reason'}), 400

        sub = stripe.Subscription.retrieve(
            creator['stripe_subscription_id'],
            expand=['items.data.price'],
        )
        amount, interval = subscription_price_snapshot(sub)
        offer = offer_for_reason(reason, amount, interval)
        log_retention_event(cursor, creator['id'], reason, offer, 'viewed_offer')
        conn.commit()
        return jsonify({
            'reason': reason,
            'offer': offer,
            'hold_months': HOLD_MONTHS,
            'hold_price': 12,
            'resume_price': 19,
        })
    except Exception as e:
        print(f"[retention] cancel-flow reason failed: {e}")
        return jsonify({'error': 'Could not start cancel flow'}), 500
    finally:
        cursor.close()
        conn.close()


@subscription_bp.route('/cancel-flow/accept', methods=['POST'])
def cancel_flow_accept():
    """Accept talent-manager help or the $12/3-month hold instead of canceling."""
    packed, err = _require_paid_creator()
    if err:
        return err
    conn, cursor, creator = packed
    try:
        from services.subscription_retention import (
            get_or_create_retention_coupon,
            log_retention_event,
            offer_for_reason,
            price_hold_creator_email_html,
            subscription_price_snapshot,
            talent_manager_creator_email_html,
            talent_manager_team_email_html,
        )

        body = request.json or {}
        reason = (body.get('reason') or '').strip()
        offer = (body.get('offer') or '').strip()
        if offer not in ('talent_manager', 'price_hold'):
            return jsonify({'error': 'Unknown offer'}), 400

        sub = stripe.Subscription.retrieve(
            creator['stripe_subscription_id'],
            expand=['items.data.price'],
        )
        amount, interval = subscription_price_snapshot(sub)
        expected = offer_for_reason(reason, amount, interval)
        if expected != offer:
            return jsonify({'error': 'That save offer is not available for this reason'}), 400

        if getattr(sub, 'cancel_at_period_end', False) or (
            hasattr(sub, 'get') and sub.get('cancel_at_period_end')
        ):
            _unstrike_cancel(creator['stripe_subscription_id'])

        if offer == 'price_hold':
            coupon_id = get_or_create_retention_coupon(stripe)
            stripe.Subscription.modify(
                creator['stripe_subscription_id'],
                coupon=coupon_id,
                cancel_at_period_end=False,
            )

        cursor.execute(
            '''
            UPDATE creators
            SET subscription_status = 'active',
                subscription_ends_at = NULL
            WHERE id = %s
            ''',
            (creator['id'],),
        )
        log_retention_event(cursor, creator['id'], reason, offer, 'accepted')
        conn.commit()

        name = creator.get('username')
        email = creator.get('email')
        if offer == 'talent_manager':
            _send_creator_resend(
                email,
                'A talent manager is on your first collab',
                talent_manager_creator_email_html(name),
            )
            _send_creator_resend(
                'team@newcollab.co',
                f"Talent manager save: @{name or creator['id']}",
                talent_manager_team_email_html(creator),
            )
            return jsonify({
                'success': True,
                'offer': offer,
                'message': 'A talent manager will help you land the first collab. We will email you this week.',
            })

        _send_creator_resend(
            email,
            'Pro is $12/month for the next 3 months',
            price_hold_creator_email_html(name),
        )
        return jsonify({
            'success': True,
            'offer': offer,
            'message': 'Next 3 months of Pro are $12. Then it returns to $19.',
        })
    except Exception as e:
        print(f"[retention] cancel-flow accept failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Could not apply that offer'}), 500
    finally:
        cursor.close()
        conn.close()


@subscription_bp.route('/cancel-flow/confirm', methods=['POST'])
def cancel_flow_confirm():
    """Cancel at period end after they decline save offers (or had none)."""
    packed, err = _require_paid_creator()
    if err:
        return err
    conn, cursor, creator = packed
    try:
        from services.subscription_retention import (
            log_retention_event,
            period_end_datetime,
        )

        reason = ((request.json or {}).get('reason') or '').strip() or 'other'
        sub = stripe.Subscription.modify(
            creator['stripe_subscription_id'],
            cancel_at_period_end=True,
        )
        ends_at = period_end_datetime(sub)
        cursor.execute(
            '''
            UPDATE creators
            SET subscription_ends_at = COALESCE(%s, subscription_ends_at)
            WHERE id = %s
            ''',
            (ends_at, creator['id']),
        )
        log_retention_event(cursor, creator['id'], reason, None, 'canceled')
        conn.commit()
        return jsonify({
            'success': True,
            'cancel_scheduled': True,
            'ends_at': ends_at.isoformat() if ends_at else None,
            'message': 'Pro stays on until the end of the period you already paid for.',
        })
    except Exception as e:
        print(f"[retention] cancel-flow confirm failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Could not schedule cancellation'}), 500
    finally:
        cursor.close()
        conn.close()


@subscription_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events for subscriptions"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    try:
        webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET_SUBSCRIPTION')
        if not webhook_secret:
            print("⚠️  STRIPE_WEBHOOK_SECRET_SUBSCRIPTION not set, skipping signature verification")
            event = stripe.Event.construct_from(request.json, stripe.api_key)
        else:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
    except Exception as e:
        print(f"❌ Webhook signature verification failed: {e}")
        return jsonify({'error': str(e)}), 400

    print(f"📨 Received Stripe webhook: {event['type']}")

    try:
        # Handle successful checkout
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            meta = session.get('metadata') or {}
            if meta.get('product') == 'brand_gifted_ugc' or not meta.get('tier'):
                print("Skipping non-subscription checkout in subscription webhook")
                return jsonify({'success': True}), 200

            creator_id = meta.get('creator_id')
            tier = meta.get('tier')
            subscription_id = session.get('subscription')
            customer_id = session.get('customer')
            amount_total = session.get('amount_total', 0)  # In cents
            is_trial = meta.get('trial') == '7' or (amount_total or 0) == 0
            sub_status = 'trialing' if is_trial else 'active'

            print(f"✅ Checkout completed for creator {creator_id} - {tier} ({sub_status})")

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Update subscription and fetch creator data for notification
            cursor.execute('''
                UPDATE creators
                SET subscription_tier = %s,
                    subscription_status = %s,
                    stripe_subscription_id = %s,
                    stripe_customer_id = %s,
                    subscription_started_at = NOW()
                WHERE id = %s
                RETURNING id, username, followers_count
            ''', (tier, sub_status, subscription_id, customer_id, creator_id))
            updated_creator = cursor.fetchone()

            # Fetch email from users table
            cursor.execute('''
                SELECT u.email FROM users u
                JOIN creators c ON c.user_id = u.id
                WHERE c.id = %s
            ''', (creator_id,))
            user_row = cursor.fetchone()

            # AUTO-APPROVE PRO SUBSCRIBERS (Skip the waitlist)
            if tier == 'pro':
                cursor.execute("""
                    UPDATE creators
                    SET approval_status = 'pro_approved',
                        approved_at = NOW(),
                        approved_by = NULL
                    WHERE id = %s AND approval_status = 'pending'
                    RETURNING id
                """, (creator_id,))

                was_pending = cursor.fetchone()

                if was_pending:
                    # Log to audit table
                    cursor.execute("""
                        INSERT INTO creator_approval_audit
                        (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
                        VALUES (%s, NULL, 'pending', 'pro_approved', %s, %s)
                    """, (
                        creator_id,
                        f'Auto-approved via {tier.upper()} subscription',
                        json.dumps({
                            "auto_approved": True,
                            "tier": tier,
                            "subscription_id": subscription_id
                        })
                    ))
                    print(f"⚡ Creator {creator_id} auto-approved via {tier.upper()} subscription!")
                    if user_row and user_row.get('email'):
                        from waitlist_emails import send_waitlist_email
                        send_waitlist_email(
                            'approved',
                            email=user_row.get('email'),
                            name=updated_creator.get('username') if updated_creator else 'there',
                            as_pro=True,
                        )

            conn.commit()
            cursor.close()
            _log_checkout_source(conn, creator_id, meta, session.get('id'))
            conn.close()

            print(f"✅ Updated creator {creator_id} to {tier} tier")

            # Send GA4 pro_upgrade event for revenue attribution
            send_ga4_event(
                client_id=customer_id or f"creator_{creator_id}",
                event_name="pro_upgrade",
                params={
                    "tier": tier,
                    "creator_id": str(creator_id),
                    "currency": "USD",
                    "value": amount_total / 100,  # Convert cents to dollars
                    "transaction_id": subscription_id or session.get('id'),
                }
            )

            # Send internal notification to team@newcollab.co
            interval = session['metadata'].get('interval', 'monthly')
            creator_data = {
                'id': creator_id,
                'username': updated_creator.get('username') if updated_creator else 'Unknown',
                'email': user_row.get('email') if user_row else 'Unknown',
                'followers': updated_creator.get('followers_count', 0) if updated_creator else 0
            }
            send_pro_subscriber_notification(creator_data, tier, amount_total, interval)

            if user_row and user_row.get('email') and not is_trial:
                from services.subscription_retention import activation_email_html
                _send_creator_resend(
                    user_row.get('email'),
                    'Pro is on — send 5 applications this week',
                    activation_email_html(
                        updated_creator.get('username') if updated_creator else None
                    ),
                )

        elif event['type'] == 'checkout.session.expired':
            from services.checkout_recovery import handle_expired_session

            conn = get_db_connection()
            try:
                outcome = handle_expired_session(conn, event['data']['object'])
                print(f"[retention] checkout expired: {outcome}")
            finally:
                conn.close()

        # Handle subscription deleted/canceled
        elif event['type'] == 'customer.subscription.deleted':
            subscription = event['data']['object']
            subscription_id = subscription['id']

            print(f"❌ Subscription {subscription_id} canceled")

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE creators
                SET subscription_tier = 'free',
                    subscription_status = 'canceled',
                    subscription_ends_at = NOW()
                WHERE stripe_subscription_id = %s
            ''', (subscription_id,))
            conn.commit()
            cursor.close()
            conn.close()

            print(f"✅ Downgraded creator to free tier")

        # Handle subscription updated
        elif event['type'] == 'customer.subscription.updated':
            from services.subscription_retention import (
                period_end_datetime,
                should_downgrade_on_status,
            )

            subscription = event['data']['object']
            subscription_id = subscription['id']
            status = subscription['status']
            ends_at = period_end_datetime(subscription)

            print(f"🔄 Subscription {subscription_id} updated to {status}")

            conn = get_db_connection()
            cursor = conn.cursor()
            if should_downgrade_on_status(status):
                cursor.execute('''
                    UPDATE creators
                    SET subscription_tier = 'free',
                        subscription_status = %s,
                        subscription_ends_at = NOW()
                    WHERE stripe_subscription_id = %s
                ''', (status, subscription_id))
            else:
                cursor.execute('''
                    UPDATE creators
                    SET subscription_status = %s,
                        subscription_ends_at = COALESCE(%s, subscription_ends_at)
                    WHERE stripe_subscription_id = %s
                ''', (status, ends_at, subscription_id))
            conn.commit()
            cursor.close()
            conn.close()

        elif event['type'] in ('invoice.payment_failed', 'invoice.payment_action_required'):
            from services.subscription_retention import (
                action_required_email_html,
                dunning_email_html,
                dunning_key_for,
                invoice_amount_label,
                invoice_customer_id,
                invoice_subscription_id,
            )

            invoice = event['data']['object']
            sub_id = invoice_subscription_id(invoice)
            cust_id = invoice_customer_id(invoice)
            key = dunning_key_for(event['type'], invoice)

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            creator = _creator_by_stripe(cursor, sub_id, cust_id)
            if creator and event['type'] == 'invoice.payment_failed':
                cursor.execute(
                    '''
                    UPDATE creators
                    SET subscription_status = 'past_due'
                    WHERE id = %s AND subscription_tier = 'pro'
                    ''',
                    (creator['id'],),
                )
                conn.commit()
            if creator and key and creator.get('email'):
                label = invoice_amount_label(invoice)
                pay_url = invoice.get('hosted_invoice_url')
                if key == 'action_required':
                    subject = 'Confirm your Pro payment'
                    html = action_required_email_html(creator.get('username'), label, pay_url)
                elif key == 'dunning_final':
                    subject = 'Your Pro has switched off'
                    html = dunning_email_html(creator.get('username'), label, pay_url, final=True)
                else:
                    subject = 'Your Pro payment didn\'t go through'
                    html = dunning_email_html(creator.get('username'), label, pay_url)
                if _send_creator_resend(creator['email'], subject, html):
                    try:
                        stripe.Invoice.modify(invoice['id'], metadata={key: '1'})
                    except Exception as meta_err:
                        print(f"[retention] could not stamp invoice metadata: {meta_err}")
            cursor.close()
            conn.close()

        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
