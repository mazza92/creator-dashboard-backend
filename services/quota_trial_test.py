"""Pro trial at the free limit: $19 first, the free week only if they didn't buy.

Ran as an experiment from Oct 14 (45% trial at the limit, 45% $19, 10% holdout). By Nov 4, 110 creators had hit
the limit: the trial arm started more (8% vs 6%) but $19 at the limit left more paying (3 active vs 0 confirmed).
So since Nov 4 everyone sees $19 at the limit, and the free week opens LIMIT_TO_TRIAL_DAYS later for those
who didn't buy (opt-out trial, card collected up front):
- never paid before (no Stripe subscription or customer): one trial per person;
- account at least 3 days old, at least 3 pitches delivered: they've used Polly enough to know the value;
- hit the free limit at least LIMIT_TO_TRIAL_DAYS ago.
The arm helpers stay so older reports can still be read.
"""

import hashlib

EXPERIMENT = 'quota_trial_v1'
FLAG = 'quota_trial_test_v1'
HOLDOUT_PCT = 10
TRIAL_PCT = 45
TRIAL_DAYS = 7
MIN_ACCOUNT_AGE_DAYS = 3
MIN_PITCHES = 3
LIMIT_TO_TRIAL_DAYS = 4

# When a creator hit the free limit: the limit email, or Polly's paywall follow-up when that went instead.
LIMIT_HIT_SQL = """
    SELECT MIN(at) AS hit FROM (
        SELECT MIN(s.sent_at) AS at FROM lifecycle_email_sends s
        WHERE s.creator_id = %(cid)s AND s.template_slug = 'max_quota_hit'
        UNION ALL
        SELECT MIN(l.sent_at) FROM polly_email_log l
        WHERE l.creator_id = %(cid)s AND l.kind = 'paywall_followup'
    ) x
"""


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


def outreach_trial_eligible(cursor, creator_id):
    """Creators our UGC supply cold email offered 7 days free: their account email is one we emailed, and
    they never paid. No arm, account-age or pitch rules, so the offer holds on the day they sign up."""
    cursor.execute(
        """
        SELECT 1
        FROM creators c
        JOIN users u ON u.id = c.user_id
        JOIN ugc_supply l ON LOWER(TRIM(l.contact_email)) = LOWER(TRIM(u.email))
        JOIN ugc_supply_outreach_tracking t ON t.lead_id = l.id
        WHERE c.id = %s
          AND c.stripe_subscription_id IS NULL
          AND c.stripe_customer_id IS NULL
          AND c.subscription_started_at IS NULL
        LIMIT 1
        """,
        (creator_id,),
    )
    return cursor.fetchone() is not None


def checkout_trial_eligible(cursor, creator_id):
    """What the paywall offers and checkout honours: a trial-offer link at signup, the cold-email address
    match, else the experiment's rule."""
    from services.creator_offers import has_trial_offer
    return (has_trial_offer(cursor, creator_id) or outreach_trial_eligible(cursor, creator_id)
            or trial_eligible(cursor, creator_id))


def limit_hit_at(cursor, creator_id):
    cursor.execute(LIMIT_HIT_SQL, {"cid": int(creator_id)})
    row = cursor.fetchone()
    return (row.get("hit") if isinstance(row, dict) else row[0]) if row else None


def trial_eligible(cursor, creator_id, now=None):
    """Never paid, account old enough, has used Polly, and hit the free limit LIMIT_TO_TRIAL_DAYS+ ago."""
    from datetime import datetime, timedelta, timezone

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
    hit = limit_hit_at(cursor, creator_id)
    if not hit:
        return False
    if hit.tzinfo is None:
        hit = hit.replace(tzinfo=timezone.utc)
    if (now or datetime.now(timezone.utc)) - hit < timedelta(days=LIMIT_TO_TRIAL_DAYS):
        return False
    from services.unlock_quota import count_delivered_unlocks_all_time
    return count_delivered_unlocks_all_time(cursor, creator_id) >= MIN_PITCHES
