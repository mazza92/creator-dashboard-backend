"""
Fetch live MRR from Stripe active subscriptions (Pro price).
Falls back to None if Stripe is not configured or the API call fails.
"""
import os


def fetch_stripe_mrr():
    """
    Returns dict with mrr_dollars, active_subscriptions, source='stripe',
    or None on missing config / API error.
    """
    secret_key = os.getenv('STRIPE_SECRET_KEY')

    # Support both old $12 and new $19 price IDs for grandfathered subscribers
    pro_price_ids = set()
    current_price = os.getenv('STRIPE_PRICE_ID_PRO')
    if current_price:
        pro_price_ids.add(current_price)
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
