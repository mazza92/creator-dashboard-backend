"""Pro trial experiment: $19 now (control) vs 7-day free trial, with a 10% holdout.

Arms are assigned from the creator id, so a creator stays in one arm for the
whole test and nobody can opt into the trial from the client. The holdout gets
no free-limit email and never sees the trial; in-app they see the $19 offer.

Who gets the trial inside the trial arm (opt-out trial, card collected up front):
- never paid before (no Stripe subscription or customer): one trial per person;
- account at least 3 days old: half of Pro buys within 3 days of signup at $19,
  so the trial isn't spent on people already paying full price;
- at least 3 pitches delivered: they've used Polly enough to know the value.
"""

import hashlib

EXPERIMENT = 'quota_trial_v1'
FLAG = 'quota_trial_test_v1'
HOLDOUT_PCT = 10
TRIAL_PCT = 45
TRIAL_DAYS = 7
MIN_ACCOUNT_AGE_DAYS = 3
MIN_PITCHES = 3


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
    """Arm when the test is running, else 'control' (everyone gets the normal offer)."""
    return quota_trial_arm(creator_id) if experiment_on() else 'control'


def trial_eligible(cursor, creator_id):
    """Trial arm, never paid, account old enough, and has used Polly. Checkout enforces the same rule."""
    if active_arm(creator_id) != 'trial':
        return False
    cursor.execute(
        """
        SELECT 1
        FROM creators c
        WHERE c.id = %s
          AND c.stripe_subscription_id IS NULL
          AND c.stripe_customer_id IS NULL
          AND c.subscription_started_at IS NULL
          AND c.created_at <= (NOW() AT TIME ZONE 'UTC') - make_interval(days => %s)
        """,
        (creator_id, MIN_ACCOUNT_AGE_DAYS),
    )
    if cursor.fetchone() is None:
        return False
    from services.unlock_quota import count_delivered_unlocks_all_time
    return count_delivered_unlocks_all_time(cursor, creator_id) >= MIN_PITCHES
