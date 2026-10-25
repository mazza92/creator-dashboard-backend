"""
TikTok Events API (server-side) for NewCollab.

Sends conversion events to the newcollab TikTok pixel so ads can optimize on
real Pro subscriptions, even when the browser pixel is blocked or the user
closes the tab. The browser pixel sends the same event with the same event_id,
so TikTok counts each conversion once.

Env vars:
  TIKTOK_EVENTS_TOKEN  Access token from TikTok Events Manager
                       (newcollabPixel > Settings > Events API > Generate Access Token).
                       TIKTOK_ACCESS_TOKEN is accepted as a fallback name.
  TIKTOK_PIXEL_ID      Pixel code (defaults to D8RDMRBC77U5P88O6G70).
"""
import hashlib
import os
import time

import requests

TIKTOK_PIXEL_ID = os.getenv('TIKTOK_PIXEL_ID', 'D8RDMRBC77U5P88O6G70')
TIKTOK_EVENTS_API_URL = 'https://business-api.tiktok.com/open_api/v1.3/event/track/'


def _token():
    return os.getenv('TIKTOK_EVENTS_TOKEN') or os.getenv('TIKTOK_ACCESS_TOKEN')


def _sha256(value):
    if not value:
        return None
    return hashlib.sha256(str(value).strip().lower().encode()).hexdigest()


def send_event(
    event: str,
    event_id: str,
    email: str = None,
    external_id: str = None,
    value: float = None,
    currency: str = 'USD',
    content_id: str = None,
    content_name: str = None,
    ttclid: str = None,
    ttp: str = None,
    client_ip: str = None,
    client_user_agent: str = None,
    page_url: str = None,
):
    """Send one standard event (e.g. CompletePayment, CompleteRegistration).

    Never raises: tracking must not break checkout or signup.
    """
    token = _token()
    if not token:
        print('[TIKTOK EVENTS] TIKTOK_EVENTS_TOKEN not set, skipping')
        return {'skipped': 'no_token'}

    user = {
        'email': _sha256(email),
        'external_id': _sha256(external_id),
        'ttclid': ttclid or None,
        'ttp': ttp or None,
        'ip': client_ip or None,
        'user_agent': client_user_agent or None,
    }
    data = {
        'event': event,
        'event_id': str(event_id),
        'event_time': int(time.time()),
        'user': {k: v for k, v in user.items() if v},
    }
    if page_url:
        data['page'] = {'url': page_url}

    properties = {}
    if value is not None:
        properties['value'] = float(value)
        properties['currency'] = (currency or 'USD').upper()
    if content_id:
        properties['content_id'] = content_id
        properties['content_type'] = 'product'
    if content_name:
        properties['content_name'] = content_name
    if properties:
        data['properties'] = properties

    payload = {
        'event_source': 'web',
        'event_source_id': TIKTOK_PIXEL_ID,
        'data': [data],
    }

    try:
        res = requests.post(
            TIKTOK_EVENTS_API_URL,
            headers={'Access-Token': token, 'Content-Type': 'application/json'},
            json=payload,
            timeout=10,
        )
        body = res.json() if res.content else {}
        if res.status_code == 200 and body.get('code') == 0:
            print(f'[TIKTOK EVENTS] {event} sent (event_id={event_id})')
        else:
            print(f'[TIKTOK EVENTS] {event} rejected: {res.status_code} {body}')
        return body
    except Exception as err:
        print(f'[TIKTOK EVENTS] {event} failed: {err}')
        return {'error': str(err)}
