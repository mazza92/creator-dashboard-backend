from services import brand_workspace as ws


def test_legacy_brands_count_as_approved():
    assert ws.is_approved(None) and ws.is_approved("approved")
    assert not ws.is_approved("pending") and not ws.is_approved("rejected")


def test_clean_inputs():
    assert ws.clean_email(" Founder@Brand.CO ") == "founder@brand.co"
    assert ws.clean_email("nope") == ""
    assert ws.clean_website("glowrecipe.com") == "https://glowrecipe.com"
    assert ws.clean_website("http://x.co") == "http://x.co"
    assert ws.clean_text("  a   b  ", 10) == "a b"


def test_creator_ref_prefers_handle():
    assert ws.creator_ref({"handle": "@Mia", "preview_url": "https://x"}) == "@mia"
    assert ws.creator_ref({"preview_url": "https://newcollab.co/kit/mia"}) == "https://newcollab.co/kit/mia"
    assert ws.creator_ref({}) == ""


def test_host_strips_www_and_path():
    assert ws._host("https://www.glowrecipe.com/shop") == "glowrecipe.com"
