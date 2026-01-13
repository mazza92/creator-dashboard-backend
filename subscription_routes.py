"""
Subscription Routes for Creator PR CRM
Handles Stripe subscription checkout and management
"""

from flask import Blueprint, request, jsonify, session
import stripe
import os
from datetime import datetime
from psycopg2.extras import RealDictCursor

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
    """Get creator ID from session"""
    return session.get('creator_id') or session.get('user_id')

def check_subscription_limits(creator_id, action_type):
    """
    Check if creator can perform action based on subscription tier
    action_type: 'save_brand' or 'send_pitch'
    Returns: (allowed: bool, message: str, current_count: int, limit: int)
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute('''
        SELECT subscription_tier, brands_saved_count, pitches_sent_this_month
        FROM creators
        WHERE id = %s
    ''', (creator_id,))
    creator = cursor.fetchone()
    cursor.close()
    conn.close()

    if not creator:
        return False, "Creator not found", 0, 0

    tier = creator['subscription_tier'] or 'free'

    # NEW LIMITS STRUCTURE - Taste of success model with 5 free contacts
    FREE_CONTACT_LIMIT = 10  # Free users get 10 brand contacts
    PRO_CONTACT_LIMIT = 20  # Pro: 20 brand contacts per month
    # Elite: unlimited

    if tier == 'free':
        # UNLIMITED: Save/browse brands - let free users explore everything
        if action_type == 'save_brand':
            return True, "", 0, -1  # Unlimited saves for free tier

        # UPDATED: Free tier gets 5 free pitches to taste success
        if action_type == 'send_pitch':
            count = creator['pitches_sent_this_month'] or 0
            if count >= FREE_CONTACT_LIMIT:
                return False, f"Free tier limit reached: {FREE_CONTACT_LIMIT} brand contacts used. Upgrade to Pro for 20 contacts/month + pitch templates.", count, FREE_CONTACT_LIMIT
            return True, "", count, FREE_CONTACT_LIMIT

    elif tier == 'pro':
        # Pro tier: Unlimited saves, limited pitches
        if action_type == 'save_brand':
            return True, "", 0, -1

        if action_type == 'send_pitch':
            count = creator['pitches_sent_this_month'] or 0
            if count >= PRO_CONTACT_LIMIT:
                return False, f"Pro tier limit reached: {PRO_CONTACT_LIMIT} pitches per month. Upgrade to Elite for unlimited.", count, PRO_CONTACT_LIMIT
            return True, "", count, PRO_CONTACT_LIMIT

    # Elite: unlimited everything
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

@subscription_bp.route('/create-checkout', methods=['POST'])
def create_checkout_session():
    """Create Stripe Checkout session for subscription"""
    try:
        creator_id = get_creator_id_from_session()
        if not creator_id:
            return jsonify({'error': 'Not authenticated'}), 401

        tier = request.json.get('tier')  # 'pro' or 'elite'

        if tier not in ['pro', 'elite']:
            return jsonify({'error': 'Invalid tier'}), 400

        # Get creator email from users table
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT u.email, c.username as name
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        # Get price ID from environment
        price_id = os.getenv('STRIPE_PRICE_ID_PRO') if tier == 'pro' else os.getenv('STRIPE_PRICE_ID_ELITE')

        if not price_id:
            return jsonify({'error': 'Price ID not configured. Please set STRIPE_PRICE_ID_PRO and STRIPE_PRICE_ID_ELITE in environment variables.'}), 500

        # Create Stripe Checkout Session
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')

        checkout_session = stripe.checkout.Session.create(
            customer_email=creator['email'],
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f"{frontend_url}/creator/dashboard/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/creator/dashboard/subscription/cancel",
            metadata={
                'creator_id': str(creator_id),
                'tier': tier,
                'creator_name': creator.get('name', '')
            },
            allow_promotion_codes=True,  # Allow discount codes
        )

        print(f"✅ Created checkout session for creator {creator_id} - {tier} tier")

        return jsonify({
            'checkout_url': checkout_session.url,
            'session_id': checkout_session.id
        })

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

        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')

        portal_session = stripe.billing_portal.Session.create(
            customer=creator['stripe_customer_id'],
            return_url=f"{frontend_url}/creator/dashboard/settings",
        )

        return jsonify({'portal_url': portal_session.url})

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

        if checkout_session.payment_status != 'paid':
            return jsonify({'error': 'Payment not completed'}), 400

        # Extract metadata
        tier = checkout_session.metadata.get('tier')
        subscription_id = checkout_session.subscription
        customer_id = checkout_session.customer

        print(f"✅ Confirming checkout for creator {creator_id} - {tier} tier")

        # Update database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE creators
            SET subscription_tier = %s,
                subscription_status = 'active',
                stripe_subscription_id = %s,
                stripe_customer_id = %s,
                subscription_started_at = NOW()
            WHERE id = %s
        ''', (tier, subscription_id, customer_id, creator_id))
        conn.commit()
        cursor.close()
        conn.close()

        print(f"✅ Activated {tier} subscription for creator {creator_id}")

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
                pitches_sent_this_month
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        return jsonify({
            'tier': creator.get('subscription_tier', 'free'),
            'status': creator.get('subscription_status', 'inactive'),
            'started_at': creator.get('subscription_started_at').isoformat() if creator.get('subscription_started_at') else None,
            'ends_at': creator.get('subscription_ends_at').isoformat() if creator.get('subscription_ends_at') else None,
            'brands_saved_count': creator.get('brands_saved_count', 0),
            'pitches_sent_this_month': creator.get('pitches_sent_this_month', 0)
        })

    except Exception as e:
        print(f"❌ Error getting subscription status: {e}")
        return jsonify({'error': str(e)}), 500

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
            creator_id = session['metadata'].get('creator_id')
            tier = session['metadata'].get('tier')
            subscription_id = session.get('subscription')
            customer_id = session.get('customer')

            print(f"✅ Checkout completed for creator {creator_id} - {tier} tier")

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE creators
                SET subscription_tier = %s,
                    subscription_status = 'active',
                    stripe_subscription_id = %s,
                    stripe_customer_id = %s,
                    subscription_started_at = NOW()
                WHERE id = %s
            ''', (tier, subscription_id, customer_id, creator_id))
            conn.commit()
            cursor.close()
            conn.close()

            print(f"✅ Updated creator {creator_id} to {tier} tier")

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
            subscription = event['data']['object']
            subscription_id = subscription['id']
            status = subscription['status']

            print(f"🔄 Subscription {subscription_id} updated to {status}")

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE creators
                SET subscription_status = %s
                WHERE stripe_subscription_id = %s
            ''', (status, subscription_id))
            conn.commit()
            cursor.close()
            conn.close()

        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
