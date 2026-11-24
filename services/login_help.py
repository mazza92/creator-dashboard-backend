"""Login helpers for accounts created with Google sign-in.

Google sign-ups get a random placeholder in users.password (not a bcrypt hash), so typing any password can never
match. Instead of the generic error, the login page tells those people to use Continue with Google.
"""

GOOGLE_PASSWORD_PREFIX = 'GOOGLE_AUTH_'
GOOGLE_ACCOUNT_LOGIN_ERROR = 'This email signed up with Google. Use "Continue with Google" below.'


def is_google_only_password(stored):
    if isinstance(stored, bytes):
        stored = stored.decode('utf-8', 'ignore')
    return isinstance(stored, str) and stored.startswith(GOOGLE_PASSWORD_PREFIX)
