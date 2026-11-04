from social_verification_routes import legacy_instagram_handle


def test_kit_handle_wins_over_signup_links():
    kit = {"social_handle": "lynnae_beauty", "social_platform": "instagram",
           "social_profiles": [{"platform": "instagram", "handle": "lynnae_beauty"}]}
    links = '[{"platform":"","url":""},{"platform":"Instagram","url":"https://instagram.com/lexi_ebeautyy"}]'
    assert legacy_instagram_handle(kit, links) == "lynnae_beauty"


def test_signup_links_used_when_no_kit():
    links = '[{"platform":"Instagram","url":"https://www.instagram.com/Lexi_ebeautyy/?hl=en"}]'
    assert legacy_instagram_handle({}, links) == "Lexi_ebeautyy"
    assert legacy_instagram_handle("{}", '[{"url":"https://instagram.com/reel/DeITQ"}]') == ""


def test_junk_ignored():
    assert legacy_instagram_handle(None, None) == ""
    assert legacy_instagram_handle({"social_platform": "tiktok", "social_handle": "x"}, "not json") == ""
