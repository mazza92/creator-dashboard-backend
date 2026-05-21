#!/usr/bin/env python3
"""
Grant the GA4 service account Viewer access on your property.

Auth: uses secrets/ga4-user-token.json from ga4_oauth_login.py
      (NOT gcloud — avoids "This app is blocked").

Prerequisites:
  1. secrets/gcp-oauth-client.json (Desktop OAuth client)
  2. python scripts/ga4_oauth_login.py  (signed in as GA4 admin)
  3. Google Analytics Admin API enabled on auth-app-feed3

Usage:
  python scripts/grant_ga4_access.py
"""
import json
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.ga4_credentials import MANAGE_USERS_SCOPE, load_user_credentials

load_dotenv()

PROPERTY_ID = os.getenv('GA4_PROPERTY_ID') or os.getenv('GA4_PROPERTY')
SA_PATH = os.getenv(
    'GA4_SERVICE_ACCOUNT_PATH',
    os.path.join(ROOT, 'secrets', 'ga4-service-account.json'),
)


def _service_account_email():
    sa_json = os.getenv('GA4_SERVICE_ACCOUNT_JSON')
    if sa_json:
        return json.loads(sa_json).get('client_email')
    if os.path.isfile(SA_PATH):
        with open(SA_PATH, encoding='utf-8') as f:
            return json.load(f).get('client_email')
    return None


def main():
    if not PROPERTY_ID:
        print('Missing GA4_PROPERTY_ID in .env')
        sys.exit(1)

    sa_email = _service_account_email()

    if not sa_email:
        print('No client_email in service account JSON')
        sys.exit(1)

    creds = load_user_credentials([MANAGE_USERS_SCOPE])
    if not creds:
        print(
            'No OAuth token found. Run first:\n'
            '  python scripts/ga4_oauth_login.py\n'
            'See scripts/GA4_OAUTH_SETUP.md'
        )
        sys.exit(1)

    try:
        import requests
    except ImportError as e:
        print(f'Missing dependency: {e}')
        sys.exit(1)

    print(f'Property: {PROPERTY_ID}')
    print(f'Service account: {sa_email}')

    url = (
        f'https://analyticsadmin.googleapis.com/v1alpha/'
        f'properties/{PROPERTY_ID}/accessBindings'
    )
    body = {
        'user': sa_email,
        'roles': ['predefinedRoles/viewer'],
    }
    headers = {
        'Authorization': f'Bearer {creds.token}',
        'Content-Type': 'application/json',
    }

    resp = requests.post(url, headers=headers, json=body, timeout=30)

    if resp.status_code == 200:
        print('Success — Viewer access granted.')
        print(resp.json().get('name', ''))
        return

    if resp.status_code == 409 or 'already exists' in resp.text.lower():
        print('Access already exists for this service account.')
        return

    print(f'Failed ({resp.status_code}): {resp.text}')
    if resp.status_code == 403:
        print(
            '\n403 usually means:\n'
            '  - Signed-in user is not GA4 Administrator on this property\n'
            '  - Analytics Admin API not enabled\n'
            '  - Re-run: python scripts/ga4_oauth_login.py with the correct Google account\n'
        )
    sys.exit(1)


if __name__ == '__main__':
    main()
