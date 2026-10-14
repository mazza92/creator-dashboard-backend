"""Resend delivery webhook: records lifecycle email opens and clicks."""

import base64
import hashlib
import hmac
import json
import os
import time

from flask import Blueprint, jsonify, request

resend_webhooks_bp = Blueprint('resend_webhooks', __name__)

SIGNATURE_TOLERANCE_SECONDS = 300


def _secret_bytes():
    secret = (os.getenv('RESEND_WEBHOOK_SECRET') or '').strip()
    if not secret:
        return None
    if secret.startswith('whsec_'):
        secret = secret[len('whsec_'):]
    try:
        return base64.b64decode(secret)
    except (ValueError, TypeError):
        return None


def verify_signature(payload, msg_id, timestamp, signature_header, secret, now=None):
    """Svix-style check used by Resend: HMAC-SHA256 over "id.timestamp.body"."""
    if not (secret and msg_id and timestamp and signature_header):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    body = payload.decode('utf-8') if isinstance(payload, bytes) else payload
    signed = f'{msg_id}.{timestamp}.{body}'.encode('utf-8')
    expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        version, _, sig = part.partition(',')
        if version == 'v1' and hmac.compare_digest(sig, expected):
            return True
    return False


def record_engagement(cursor, event_type, email_id, link=None):
    """Stamp first open/click on the matching lifecycle send. Returns rows updated."""
    if event_type == 'email.opened':
        cursor.execute("""
            UPDATE lifecycle_email_sends
            SET opened_at = COALESCE(opened_at, NOW())
            WHERE provider_message_id = %s
        """, (email_id,))
    elif event_type == 'email.clicked':
        cursor.execute("""
            UPDATE lifecycle_email_sends
            SET opened_at = COALESCE(opened_at, NOW()),
                clicked_at = COALESCE(clicked_at, NOW()),
                clicked_url = COALESCE(clicked_url, %s)
            WHERE provider_message_id = %s
        """, ((link or '')[:2000] or None, email_id))
    else:
        return 0
    return cursor.rowcount


@resend_webhooks_bp.route('/api/webhooks/resend', methods=['POST'])
def resend_webhook():
    secret = _secret_bytes()
    if not secret:
        return jsonify({'error': 'webhook not configured'}), 503

    payload = request.get_data()
    if not verify_signature(
        payload,
        request.headers.get('svix-id'),
        request.headers.get('svix-timestamp'),
        request.headers.get('svix-signature'),
        secret,
    ):
        return jsonify({'error': 'invalid signature'}), 401

    try:
        event = json.loads(payload or b'{}')
    except ValueError:
        return jsonify({'error': 'invalid json'}), 400

    event_type = event.get('type')
    data = event.get('data') or {}
    email_id = data.get('email_id')
    if event_type not in ('email.opened', 'email.clicked') or not email_id:
        return jsonify({'ok': True, 'ignored': True}), 200

    from lifecycle_email_engine import get_db_connection

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        updated = record_engagement(
            cursor, event_type, email_id, (data.get('click') or {}).get('link')
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f'[RESEND WEBHOOK] {event_type} {email_id}: {exc}')
        return jsonify({'error': 'update failed'}), 500
    finally:
        conn.close()
    return jsonify({'ok': True, 'updated': updated}), 200
