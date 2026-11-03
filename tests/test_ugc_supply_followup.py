from routes import ugc_supply as us


def test_followup_copy_is_short_personal_and_keeps_the_signup_link():
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert "@jaye.ugc" in html and us.SIGNUP_URL in html
    assert "—" not in html and "{{" not in html


def test_outreach_queue_skips_existing_members():
    assert "FROM users u" in us.NOT_A_MEMBER_SQL and us.MAX_EMAILS_PER_LEAD == 2


def test_first_email_leads_with_paid_per_view():
    html = us.personalize(us.default_onboarding_html(), {"handle": "jaye.ugc"})
    assert us.personalize(us.DEFAULT_SUBJECT, {"handle": "jaye.ugc"}) == "Paid UGC for @jaye.ugc"
    assert "earn per 1,000 views" in html and "gifted product" in html and us.SIGNUP_URL in html
    assert "—" not in html and "{{" not in html
