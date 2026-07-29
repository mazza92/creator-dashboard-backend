"""
Meta Conversions API (CAPI) for server-side event tracking.
Sends Purchase events to Meta for better attribution and iOS 14+ tracking.
"""
import os
import time
import hashlib
import requests

# Meta Pixel ID and Access Token
META_PIXEL_ID = os.getenv('META_PIXEL_ID', '2008333943133119')
META_ACCESS_TOKEN = os.getenv('META_ACCESS_TOKEN', '')  # Set this in .env

# API endpoint
META_CAPI_URL = f"https://graph.facebook.com/v18.0/{META_PIXEL_ID}/events"


def _hash_value(value):
    """SHA256 hash a value for Meta CAPI (required for PII)."""
    if not value:
        return None
    return hashlib.sha256(str(value).lower().strip().encode()).hexdigest()


def send_purchase_event(
    email: str,
    value: float,
    currency: str = 'USD',
    content_name: str = 'NewCollab Pro',
    content_id: str = 'subscription_pro_monthly',
    event_id: str = None,
    client_ip: str = None,
    client_user_agent: str = None,
    fbc: str = None,  # Facebook click ID cookie
    fbp: str = None,  # Facebook browser ID cookie
):
    """
    Send a Purchase event to Meta Conversions API.

    Args:
        email: User's email (will be hashed)
        value: Purchase value in dollars
        currency: Currency code (default USD)
        content_name: Product name
        content_id: Product ID
        event_id: Unique event ID for deduplication (use Stripe session_id)
        client_ip: User's IP address
        client_user_agent: User's browser user agent
        fbc: _fbc cookie value (Facebook click ID)
        fbp: _fbp cookie value (Facebook browser ID)

    Returns:
        dict with success status and response
    """
    if not META_ACCESS_TOKEN:
        print("[META CAPI] No access token configured, skipping")
        return {'success': False, 'error': 'No access token'}

    # Build user data (hashed for privacy)
    user_data = {}
    if email:
        user_data['em'] = [_hash_value(email)]
    if client_ip:
        user_data['client_ip_address'] = client_ip
    if client_user_agent:
        user_data['client_user_agent'] = client_user_agent
    if fbc:
        user_data['fbc'] = fbc
    if fbp:
        user_data['fbp'] = fbp

    # Build event payload
    event_data = {
        'event_name': 'Purchase',
        'event_time': int(time.time()),
        'action_source': 'website',
        'user_data': user_data,
        'custom_data': {
            'currency': currency,
            'value': value,
            'content_type': 'product',
            'content_ids': [content_id],
            'content_name': content_name,
            'num_items': 1,
        },
    }

    # Add event_id for deduplication with browser pixel
    if event_id:
        event_data['event_id'] = event_id

    payload = {
        'data': [event_data],
        'access_token': META_ACCESS_TOKEN,
    }

    try:
        response = requests.post(
            META_CAPI_URL,
            json=payload,
            timeout=10
        )
        result = response.json()

        if response.status_code == 200:
            print(f"[META CAPI] Purchase event sent successfully: {value} {currency}")
            return {'success': True, 'response': result}
        else:
            print(f"[META CAPI] Error: {result}")
            return {'success': False, 'error': result}

    except Exception as e:
        print(f"[META CAPI] Request failed: {e}")
        return {'success': False, 'error': str(e)}


def send_initiate_checkout_event(
    email: str = None,
    value: float = 19,
    currency: str = 'USD',
    content_name: str = 'NewCollab Pro',
    content_id: str = 'subscription_pro_monthly',
    client_ip: str = None,
    client_user_agent: str = None,
):
    """Send an InitiateCheckout event to Meta CAPI."""
    if not META_ACCESS_TOKEN:
        return {'success': False, 'error': 'No access token'}

    user_data = {}
    if email:
        user_data['em'] = [_hash_value(email)]
    if client_ip:
        user_data['client_ip_address'] = client_ip
    if client_user_agent:
        user_data['client_user_agent'] = client_user_agent

    event_data = {
        'event_name': 'InitiateCheckout',
        'event_time': int(time.time()),
        'action_source': 'website',
        'user_data': user_data,
        'custom_data': {
            'currency': currency,
            'value': value,
            'content_type': 'product',
            'content_ids': [content_id],
            'content_name': content_name,
            'num_items': 1,
        },
    }

    payload = {
        'data': [event_data],
        'access_token': META_ACCESS_TOKEN,
    }

    try:
        response = requests.post(META_CAPI_URL, json=payload, timeout=10)
        result = response.json()

        if response.status_code == 200:
            print(f"[META CAPI] InitiateCheckout event sent: {value} {currency}")
            return {'success': True, 'response': result}
        else:
            print(f"[META CAPI] Error: {result}")
            return {'success': False, 'error': result}

    except Exception as e:
        print(f"[META CAPI] Request failed: {e}")
        return {'success': False, 'error': str(e)}
