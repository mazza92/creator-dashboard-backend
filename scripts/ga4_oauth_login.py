#!/usr/bin/env python3
"""
Sign in with team@newcollab.co for GA4 API access.

Use this instead of gcloud when Google shows "This app is blocked"
(gcloud uses Google's shared OAuth client, which many accounts reject).

Prerequisites: secrets/gcp-oauth-client.json (Desktop OAuth client from GCP).
See scripts/GA4_OAUTH_SETUP.md

Usage:
  cd creator_dashboard
  python scripts/ga4_oauth_login.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from scripts.ga4_credentials import (
    MANAGE_USERS_SCOPE,
    READONLY_SCOPE,
    get_token_path,
    run_oauth_login,
)

load_dotenv()

# GA4 + Search Console scopes
GSC_READONLY_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
SCOPES = [MANAGE_USERS_SCOPE, READONLY_SCOPE, GSC_READONLY_SCOPE]


def main():
    print('Opening browser — sign in with your GA4 admin Google account')
    print('(e.g. the account behind team@newcollab.co)\n')
    run_oauth_login(SCOPES)
    print(f'\nSaved token to: {get_token_path()}')
    print('Next: python scripts/grant_ga4_access.py')


if __name__ == '__main__':
    main()
