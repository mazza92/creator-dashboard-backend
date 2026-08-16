"""Prevent accidental duplicate creator-to-brand outreach emails."""

from datetime import datetime, timedelta, timezone

# First-pitch mailto can fire again after the creator returns from Mail.app.
INITIAL_MAILTO_COOLDOWN = timedelta(hours=24)
# Follow-ups are allowed after the 7-day reminder, but not twice in a sitting.
FOLLOWUP_MAILTO_COOLDOWN = timedelta(hours=48)

INITIAL_BLOCK_MESSAGE = (
    'You already emailed this brand. Sending the same pitch again can look like spam. '
    'Follow up from your pipeline after a week if they have not replied.'
)
FOLLOWUP_BLOCK_MESSAGE = (
    'You already sent a follow-up to this brand recently. '
    'Wait before sending another so it does not look like spam.'
)


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_until(last_at, cooldown, now=None):
    last_at = _as_utc(last_at)
    if not last_at:
        return 0
    now = _as_utc(now) or datetime.now(timezone.utc)
    remaining = (last_at + cooldown) - now
    if remaining.total_seconds() <= 0:
        return 0
    return max(1, int(remaining.total_seconds() // 3600))


def duplicate_outreach_block(pipeline_row, is_followup=False, now=None):
    """Return a block dict if this send should not open mailto, else None.

    Fail-open when there is no pipeline row. Confirmed first pitches cannot be
    resent as a new first pitch. Unconfirmed mailto opens are cooled off so
    returning from iOS Mail cannot fire the same email again.
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    row = pipeline_row or {}
    mailto_at = _as_utc(row.get('mailto_opened_at'))
    pitched_at = _as_utc(row.get('pitched_at'))
    followup_at = _as_utc(row.get('followup_sent_at'))
    confirmed = bool(row.get('send_confirmed'))

    if is_followup:
        last_at = followup_at or mailto_at
        if last_at and now - last_at < FOLLOWUP_MAILTO_COOLDOWN:
            return {
                'code': 'duplicate_outreach',
                'error': FOLLOWUP_BLOCK_MESSAGE,
                'retry_after_hours': hours_until(last_at, FOLLOWUP_MAILTO_COOLDOWN, now=now),
                'can_followup': False,
            }
        return None

    if confirmed or pitched_at:
        return {
            'code': 'duplicate_outreach',
            'error': INITIAL_BLOCK_MESSAGE,
            'retry_after_hours': None,
            'can_followup': True,
        }

    if mailto_at and now - mailto_at < INITIAL_MAILTO_COOLDOWN:
        return {
            'code': 'duplicate_outreach',
            'error': INITIAL_BLOCK_MESSAGE,
            'retry_after_hours': hours_until(mailto_at, INITIAL_MAILTO_COOLDOWN, now=now),
            'can_followup': False,
        }

    return None
