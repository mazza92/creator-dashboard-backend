"""Free-limit email experiment: $19 now (control) vs 7-day free trial, with a no-email holdout.

Arms are assigned from the creator id, so a creator stays in one arm for the
whole test and nobody can opt into the trial from the client.
"""

import hashlib

EXPERIMENT = 'quota_trial_v1'
FLAG = 'quota_trial_test_v1'
HOLDOUT_PCT = 10
TRIAL_PCT = 45
TRIAL_DAYS = 7
TRIAL_OFFER_WINDOW_DAYS = 30


def quota_trial_arm(creator_id):
    """'holdout' (10%), 'trial' (45%) or 'control' (45%)."""
    bucket = int(hashlib.sha256(f'{EXPERIMENT}:{int(creator_id)}'.encode()).hexdigest(), 16) % 100
    if bucket < HOLDOUT_PCT:
        return 'holdout'
    if bucket < HOLDOUT_PCT + TRIAL_PCT:
        return 'trial'
    return 'control'


def experiment_on():
    from lifecycle_email_engine import is_feature_enabled
    return is_feature_enabled(FLAG)


def active_arm(creator_id):
    """Arm when the test is running, else 'control' (everyone gets the normal email)."""
    return quota_trial_arm(creator_id) if experiment_on() else 'control'


def trial_eligible(cursor, creator_id):
    """Trial arm, was sent the trial email recently, and has never subscribed before."""
    if active_arm(creator_id) != 'trial':
        return False
    cursor.execute(
        """
        SELECT 1
        FROM creators c
        WHERE c.id = %s
          AND c.stripe_subscription_id IS NULL
          AND c.subscription_started_at IS NULL
          AND EXISTS (
              SELECT 1 FROM lifecycle_email_sends s
              WHERE s.creator_id = c.id
                AND s.template_slug = 'max_quota_hit'
                AND s.utm_campaign = 'max_quota_hit_trial'
                AND s.status = 'sent'
                AND s.sent_at >= NOW() - make_interval(days => %s)
          )
        """,
        (creator_id, TRIAL_OFFER_WINDOW_DAYS),
    )
    return cursor.fetchone() is not None
