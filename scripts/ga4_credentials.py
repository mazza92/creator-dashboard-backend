"""Load Google user credentials for GA4 (OAuth — shared with seo-assistant)."""
import os

MANAGE_USERS_SCOPE = 'https://www.googleapis.com/auth/analytics.manage.users'
READONLY_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'

DEFAULT_TOKEN_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'secrets', 'ga4-user-token.json'
)
# seo-assistant already has a working GA4 OAuth token for property 499594920
SEO_ASSISTANT_TOKEN = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'seo-assistant', 'tokens', 'ga4_token.json')
)
DEFAULT_CLIENT_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'secrets', 'gcp-oauth-client.json'
)


def get_token_path():
    """Resolve token file: env > seo-assistant > local secrets."""
    for candidate in (
        os.getenv('GA4_TOKEN_PATH'),
        os.getenv('GA4_OAUTH_TOKEN_PATH'),
        SEO_ASSISTANT_TOKEN if os.path.isfile(SEO_ASSISTANT_TOKEN) else None,
        DEFAULT_TOKEN_PATH,
    ):
        if candidate and os.path.isfile(candidate):
            return os.path.normpath(candidate)
    return os.path.normpath(
        os.getenv('GA4_TOKEN_PATH')
        or os.getenv('GA4_OAUTH_TOKEN_PATH')
        or DEFAULT_TOKEN_PATH
    )


def get_client_secrets_path():
    return os.getenv('GA4_OAUTH_CLIENT_PATH', DEFAULT_CLIENT_PATH)


def load_user_credentials(scopes):
    """Load saved OAuth token; refresh if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    token_path = get_token_path()
    if not os.path.isfile(token_path):
        return None

    # Use scopes stored in the token file for refresh (avoids invalid_scope errors)
    creds = Credentials.from_authorized_user_file(token_path)
    if scopes and not creds.has_scopes(scopes):
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_user_credentials(creds)
    return creds


def save_user_credentials(creds):
    token_path = get_token_path()
    parent = os.path.dirname(os.path.abspath(token_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(token_path, 'w', encoding='utf-8') as f:
        f.write(creds.to_json())


def run_oauth_login(scopes):
    """Browser login using YOUR OAuth client (avoids blocked gcloud app)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_path = get_client_secrets_path()
    if not os.path.isfile(client_path):
        raise FileNotFoundError(
            f'Missing OAuth client JSON: {client_path}\n'
            'Create a Desktop OAuth client in GCP (see scripts/GA4_OAUTH_SETUP.md).'
        )

    flow = InstalledAppFlow.from_client_secrets_file(client_path, scopes)
    creds = flow.run_local_server(port=0, prompt='consent')
    save_user_credentials(creds)
    return creds
