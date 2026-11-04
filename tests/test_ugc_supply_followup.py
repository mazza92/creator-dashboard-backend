from routes import ugc_supply as us


def test_followup_copy_is_short_personal_and_keeps_the_signup_link():
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert us.SIGNUP_URL in html
    assert "—" not in html and "{{" not in html


def test_outreach_queue_skips_existing_members():
    assert "FROM users u" in us.NOT_A_MEMBER_SQL and us.MAX_EMAILS_PER_LEAD == 2


def test_first_email_offers_the_free_week_without_overclaiming():
    html = us.personalize(us.default_onboarding_html(), {"handle": "jaye.ugc"})
    subject = us.personalize(us.DEFAULT_SUBJECT, {"handle": "jaye.ugc"})
    assert subject == "PR & gifting for @jaye.ugc"
    assert "free for 7 days" in html and "from your Gmail" in html
    assert 'href="https://app.newcollab.co/register/creator?ref=polly_trial7"' in html
    for claim in ("negotiat", "contract", "guarantee"):
        assert claim not in html.lower()
    assert "\u2014" not in html and "{{" not in html


def test_subject_variants_are_short_personal_and_stable():
    seen = set()
    for i in range(60):
        lead = {"id": i, "handle": "jaye.ugc"}
        subject = us.personalize(us.subject_for_lead(lead), lead)
        assert subject == us.personalize(us.subject_for_lead(lead), lead)  # same lead, same subject
        assert len(subject.split()) <= 5 and "free" not in subject.lower() and "(" not in subject
        seen.add(us.subject_for_lead(lead))
    assert seen == set(us.SUBJECT_VARIANTS)


def test_followup_keeps_the_free_week():
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert "7 free days" in html and us.SIGNUP_URL in html
