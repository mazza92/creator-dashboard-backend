"""Transactional waitlist emails — joined, approved, rejected.

Uses the same welcome_email.html shell as resume-onboarding so branding stays consistent.
Lifecycle product emails (unlocks, matches) stay off until the creator is approved.
"""
from html import escape

WAITLIST_APP_URL = 'https://app.newcollab.co'


def waitlist_email_context(kind, name, reason=None, as_pro=False):
    """Pure template context. Safe to unit-test without SMTP."""
    safe_name = escape((name or 'there').strip() or 'there')
    if kind == 'joined':
        return {
            'subject': "You're on the Newcollab waitlist",
            'preheader': 'We review every profile by hand. You will get an email when you are in.',
            'action_url': f'{WAITLIST_APP_URL}/creator/waitlist',
            'action_text': 'Check your waitlist',
            'message': (
                f'<p>Hi {safe_name},</p>'
                f'<p>Your creator profile is in the review queue. We look at every application by hand '
                f'so brands only hear from real, active creators.</p>'
                f'<p>Typical wait is about a day. We will email you the moment you are approved — nothing to do until then.</p>'
                f'<p>Pro members skip the line and get in instantly.</p>'
            ),
        }
    if kind == 'approved':
        opener = (
            'Pro unlocked instant access. Your profile is live.'
            if as_pro
            else 'Your creator profile has been approved.'
        )
        return {
            'subject': "You're in — start browsing brands",
            'preheader': 'Your Newcollab profile is approved.',
            'action_url': f'{WAITLIST_APP_URL}/creator/dashboard/for-you',
            'action_text': 'See your brand matches',
            'message': (
                f'<p>Hi {safe_name},</p>'
                f'<p>{opener}</p>'
                f'<p>You can now browse gifting brands, pitch matches for your niche, and track follow-ups in one place.</p>'
            ),
        }
    if kind == 'rejected':
        safe_reason = escape((reason or '').strip()) or 'Your profile does not meet our current quality bar.'
        return {
            'subject': 'Update on your Newcollab application',
            'preheader': 'We could not approve your profile this time.',
            'action_url': 'mailto:team@newcollab.co?subject=Creator%20application',
            'action_text': 'Email the team',
            'message': (
                f'<p>Hi {safe_name},</p>'
                f'<p>Thank you for applying. After reviewing your profile, we are unable to approve your creator account right now.</p>'
                f'<p><strong>Reason:</strong> {safe_reason}</p>'
                f'<p>This keeps matches high-quality for brands. Keep posting, and reach out if you have questions.</p>'
            ),
        }
    raise ValueError(f'Unknown waitlist email kind: {kind}')


def send_waitlist_email(kind, email, name, user_id=None, reason=None, as_pro=False):
    """Send a waitlist transactional email. Returns True on success."""
    if not email:
        return False
    ctx = waitlist_email_context(kind, name, reason=reason, as_pro=as_pro)
    try:
        from email_cron_routes import send_template_email
        success, err = send_template_email(
            to_email=email,
            template_name='welcome_email.html',
            subject=ctx['subject'],
            context={
                'user_id': user_id,
                'preheader': ctx['preheader'],
                'subject': ctx['subject'],
                'message': ctx['message'],
                'action_url': ctx['action_url'],
                'action_text': ctx['action_text'],
            },
        )
        if not success:
            print(f'Waitlist {kind} email failed for {email}: {err}')
        return bool(success)
    except Exception as e:
        print(f'Waitlist {kind} email error for {email}: {e}')
        return False
