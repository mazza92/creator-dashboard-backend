from routes import ugc_supply as us


def test_followup_copy_is_short_personal_and_keeps_the_signup_link():
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert us.SIGNUP_URL in html
    assert "—" not in html and "{{" not in html


def test_outreach_queue_skips_existing_members():
    assert "FROM users u" in us.NOT_A_MEMBER_SQL and us.MAX_EMAILS_PER_LEAD == 2


def _with_count(monkeypatch, n):
    monkeypatch.setitem(us._gig_count, "n", n)


def test_first_email_leads_with_the_paid_job_feed(monkeypatch):
    _with_count(monkeypatch, 105)
    html = us.personalize(us.default_onboarding_html(), {"handle": "jaye.ugc"})
    assert us.personalize(us.DEFAULT_SUBJECT, {"handle": "jaye.ugc"}) == "paid UGC jobs for @jaye.ugc"
    assert "105 new this week" in html and "SideShift" in html and "See this week's paid UGC jobs" in html
    assert "Free to look" in html and "free for 7 days" in html
    assert 'href="https://app.newcollab.co/register/creator?ref=polly_trial7"' in html
    for claim in ("negotiat", "contract", "guarantee", "free jobs"):
        assert claim not in html.lower()
    assert "\u2014" not in html and "{{" not in html


def test_small_feed_never_shows_a_tiny_number(monkeypatch):
    _with_count(monkeypatch, 4)
    subject = us.personalize("{{gig_count}} paid UGC jobs this week", {"handle": "x"})
    assert subject == "new paid UGC jobs this week"


def test_subject_variants_are_short_personal_and_stable(monkeypatch):
    _with_count(monkeypatch, 105)
    seen = set()
    for i in range(60):
        lead = {"id": i, "handle": "jaye.ugc"}
        subject = us.personalize(us.subject_for_lead(lead), lead)
        assert subject == us.personalize(us.subject_for_lead(lead), lead)  # same lead, same subject
        assert len(subject.split()) <= 6 and "free" not in subject.lower() and "(" not in subject
        seen.add(us.subject_for_lead(lead))
    assert seen == set(us.SUBJECT_VARIANTS)


def test_followup_points_back_to_the_feed(monkeypatch):
    _with_count(monkeypatch, 105)
    html = us.personalize(us.followup_html(), {"handle": "jaye.ugc"})
    assert "105 paid UGC jobs went up this week" in html and us.SIGNUP_URL in html
