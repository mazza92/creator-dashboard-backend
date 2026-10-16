"""
Fetch live MRR from Stripe active subscriptions (Pro price).
Falls back to None if Stripe is not configured or the API call fails.
"""
import os


def fetch_trial_cancel_flags():
    """{subscription_id: True if the trialist already cancelled}, or None when Stripe is unavailable."""
    secret_key = os.getenv('STRIPE_SECRET_KEY')
    if not secret_key or not secret_key.startswith('sk_'):
        return None
    try:
        import stripe
        stripe.api_key = secret_key
        return {
            sub['id']: bool(sub.get('cancel_at_period_end') or sub.get('cancel_at'))
            for sub in stripe.Subscription.list(status='trialing', limit=100).auto_paging_iter()
        }
    except Exception as e:
        print(f'[MRR] Stripe trial lookup failed: {e}')
        return None


def fetch_stripe_mrr():
    """
    Returns dict with mrr_dollars, active_subscriptions, source='stripe',
    or None on missing config / API error.
    """
    secret_key = os.getenv('STRIPE_SECRET_KEY')

    # Monthly, yearly, and the old $12 price for grandfathered subscribers
    pro_price_ids = {
        price for price in (os.getenv('STRIPE_PRICE_ID_PRO'), os.getenv('STRIPE_PRICE_ID_PRO_ANNUAL')) if price
    }
    # Old $12 price ID (grandfathered subscribers)
    pro_price_ids.add('price_1TN7n9EYev1UAuLgQcLAr73g')

    if not secret_key or not secret_key.startswith('sk_'):
        return None

    try:
        import stripe
    except ImportError:
        print('[MRR] stripe package not installed')
        return None

    stripe.api_key = secret_key

    try:
        mrr_cents = 0
        active_subscriptions = 0

        for sub in stripe.Subscription.list(
            status='active',
            limit=100,
            expand=['data.items.data.price'],
        ).auto_paging_iter():
            matched = False
            for item in sub.get('items', {}).get('data', []):
                price = item.get('price') or {}
                price_id = price.get('id')
                # Count subscriptions on any Pro price (current or grandfathered)
                if pro_price_ids and price_id not in pro_price_ids:
                    continue

                amount = price.get('unit_amount') or 0
                recurring = price.get('recurring') or {}
                interval = recurring.get('interval', 'month')
                qty = item.get('quantity') or 1

                if interval == 'month':
                    mrr_cents += amount * qty
                elif interval == 'year':
                    mrr_cents += int((amount * qty) / 12)
                matched = True

            if matched:
                active_subscriptions += 1

        return {
            'mrr_dollars': round(mrr_cents / 100, 2),
            'active_subscriptions': active_subscriptions,
            'source': 'stripe',
            'live_mode': secret_key.startswith('sk_live_'),
        }
    except Exception as e:
        print(f'[MRR] Stripe API error: {e}')
        return None
