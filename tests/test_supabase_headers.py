from services.supabase_headers import supabase_auth_headers


def test_new_secret_key_goes_in_apikey_only():
    assert supabase_auth_headers("sb_secret_abc") == {"apikey": "sb_secret_abc"}


def test_legacy_jwt_key_keeps_bearer():
    assert supabase_auth_headers("eyJx.y.z") == {"apikey": "eyJx.y.z", "Authorization": "Bearer eyJx.y.z"}
