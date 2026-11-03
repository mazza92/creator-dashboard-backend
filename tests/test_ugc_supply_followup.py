from routes import ugc_supply as us


def test_followup_copy_is_short_personal_and_keeps_the_signup_link():
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert "@jaye.ugc" in html and us.SIGNUP_URL in html
    assert "—" not in html and "{{" not in html


def test_outreach_queue_skips_existing_members():
    assert "FROM users u" in us.NOT_A_MEMBER_SQL and us.MAX_EMAILS_PER_LEAD == 2


def test_first_email_pitches_the_assistant_without_overclaiming():
    html = us.personalize(us.default_onboarding_html(), {"handle": "jaye.ugc"})
    assert us.personalize(us.DEFAULT_SUBJECT, {"handle": "jaye.ugc"}) == "A pitching assistant for @jaye.ugc"
    assert "on autopilot" in html and "your own Gmail" in html and us.SIGNUP_URL in html
    for claim in ("negotiat", "contract", "guarantee", "free"):
        assert claim not in html.lower()
    assert "—" not in html and "{{" not in html
