"""Send creator campaigns through Resend. Brand outreach still uses Gmail."""

import os
import re

import requests

RESEND_API_URL = 'https://api.resend.com/emails'
DEFAULT_FROM_EMAIL = 'team@newcollab.co'
DEFAULT_FROM_NAME = 'Newcollab'
_UNSUBSCRIBE_HREF_RE = re.compile(
    r'href=["\'](https?://[^"\']+/api/public/unsubscribe[^"\']+)["\']',
    re.IGNORECASE,
)


def resend_configured():
    return bool((os.getenv('RESEND_API_KEY') or '').strip())


def campaign_from_header():
    from_email = (
        os.getenv('RESEND_FROM_EMAIL')
        or os.getenv('SMTP_USERNAME')
        or DEFAULT_FROM_EMAIL
    ).strip()
    from_name = (
        os.getenv('RESEND_FROM_NAME')
        or os.getenv('EMAIL_SENDER_NAME')
        or DEFAULT_FROM_NAME
    ).strip()
    return f'{from_name} <{from_email}>'


def extract_unsubscribe_url(html_content):
    if not html_content:
        return None
    match = _UNSUBSCRIBE_HREF_RE.search(html_content)
    return match.group(1) if match else None


def send_resend_email(
    to_email,
    subject,
    html_content,
    unsubscribe_url=None,
    tags=None,
):
    """
    Send one email via Resend.

    Returns:
        dict: success, message_id, error, retryable
    """
    api_key = (os.getenv('RESEND_API_KEY') or '').strip()
    if not api_key:
        return {
            'success': False,
            'message_id': None,
            'error': 'RESEND_API_KEY not set',
            'retryable': False,
        }

    to_email = (to_email or '').strip()
    if not to_email:
        return {
            'success': False,
            'message_id': None,
            'error': 'Missing recipient',
            'retryable': False,
        }

    payload = {
        'from': campaign_from_header(),
        'to': [to_email],
        'subject': subject,
        'html': html_content,
    }

    unsub = unsubscribe_url or extract_unsubscribe_url(html_content)
    if unsub:
        payload['headers'] = {
            'List-Unsubscribe': f'<{unsub}>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
        }

    if tags:
        payload['tags'] = tags

    try:
        response = requests.post(
            RESEND_API_URL,
            json=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        return {
            'success': False,
            'message_id': None,
            'error': str(exc)[:500],
            'retryable': True,
        }

    if response.status_code in (200, 201):
        data = {}
        try:
            data = response.json() or {}
        except ValueError:
            data = {}
        return {
            'success': True,
            'message_id': data.get('id'),
            'error': None,
            'retryable': False,
        }

    retryable = response.status_code == 429 or response.status_code >= 500
    error_text = response.text[:500] if response.text else f'HTTP {response.status_code}'
    try:
        body = response.json() or {}
        message = (body.get('message') or body.get('error') or error_text)
        if isinstance(message, dict):
            message = message.get('message') or error_text
        error_text = str(message)[:500]
    except ValueError:
        pass

    return {
        'success': False,
        'message_id': None,
        'error': error_text,
        'retryable': retryable,
    }
