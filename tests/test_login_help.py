import bcrypt

from services.login_help import GOOGLE_PASSWORD_PREFIX, is_google_only_password


def test_google_placeholder_detected():
    assert is_google_only_password(GOOGLE_PASSWORD_PREFIX + 'abc123')
    assert is_google_only_password((GOOGLE_PASSWORD_PREFIX + 'abc').encode())


def test_real_hash_and_empty_are_not_google():
    real = bcrypt.hashpw(b'secret', bcrypt.gensalt(4)).decode()
    assert not is_google_only_password(real)
    assert not is_google_only_password(None)
    assert not is_google_only_password('')
