"""Auth headers for direct Supabase Storage calls.

New Supabase keys (sb_secret_... / sb_publishable_...) go in the `apikey` header and are not JWTs, so they must not
be sent as `Authorization: Bearer`. Legacy JWT keys (eyJ...) still get the Bearer header too.
"""


def supabase_auth_headers(key: str) -> dict:
    key = (key or "").strip()
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers
