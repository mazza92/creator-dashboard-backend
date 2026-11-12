"""Free monthly pack quota: 3 distinct PR brands per calendar month (DB local time)."""

FREE_UNLOCK_LIMIT = 3

# Distinct PR brands delivered this month. Ignore legacy `brands` ids that are
# not in pr_brands so the old public-directory unlock cannot steal pack credits.
# One credit = one PR brand delivered. Same event as pre-pivot unlocks
# (3 free credits == 3 free unlocks). UNION so Brand PR applies still count
# if an unlock row is missing; the same brand is not counted twice.
CREDIT_USAGE_SQL = """
    SELECT creator_id, brand_id, MIN(used_at) AS used_at
    FROM (
        SELECT bu.creator_id, bu.brand_id, bu.unlocked_at AS used_at
        FROM brand_unlocks bu
        WHERE EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION ALL
        SELECT pp.creator_id, pp.brand_id, pp.generated_at AS used_at
        FROM pr_packages pp
        WHERE pp.generated_at IS NOT NULL
        UNION ALL
        SELECT a.creator_id, a.brand_id, a.applied_at AS used_at
        FROM brand_pr_applications a
        WHERE a.applied_at IS NOT NULL
    ) raw
    GROUP BY creator_id, brand_id
"""

DELIVERED_THIS_MONTH_SQL = '''
    SELECT COUNT(*) AS n FROM (
        SELECT bu.brand_id
        FROM brand_unlocks bu
        WHERE bu.creator_id = %s
          AND bu.unlocked_at >= date_trunc('month', NOW())
          AND EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION
        SELECT pp.brand_id
        FROM pr_packages pp
        WHERE pp.creator_id = %s
          AND pp.generated_at IS NOT NULL
          AND pp.generated_at >= date_trunc('month', NOW())
        UNION
        SELECT a.brand_id
        FROM brand_pr_applications a
        WHERE a.creator_id = %s
          AND a.applied_at >= date_trunc('month', NOW())
    ) delivered
'''

ALREADY_DELIVERED_SQL = '''
    SELECT 1 FROM brand_unlocks
    WHERE creator_id = %s AND brand_id = %s
    UNION ALL
    SELECT 1 FROM pr_packages
    WHERE creator_id = %s AND brand_id = %s
    UNION ALL
    SELECT 1 FROM brand_pr_applications
    WHERE creator_id = %s AND brand_id = %s
    LIMIT 1
'''

# Distinct PR brands ever delivered (admin hot-lead / at-limit metrics).
DELIVERED_ALL_TIME_BY_CREATOR_SQL = '''
    SELECT creator_id, COUNT(*) AS delivered FROM (
        SELECT bu.creator_id, bu.brand_id
        FROM brand_unlocks bu
        WHERE EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION
        SELECT pp.creator_id, pp.brand_id
        FROM pr_packages pp
        WHERE pp.generated_at IS NOT NULL
        UNION
        SELECT a.creator_id, a.brand_id
        FROM brand_pr_applications a
    ) delivered
    GROUP BY creator_id
'''

DELIVERED_ALL_TIME_COUNT_SQL = '''
    SELECT COUNT(*) AS n FROM (
        SELECT bu.brand_id
        FROM brand_unlocks bu
        WHERE bu.creator_id = %s
          AND EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION
        SELECT pp.brand_id
        FROM pr_packages pp
        WHERE pp.creator_id = %s
          AND pp.generated_at IS NOT NULL
        UNION
        SELECT a.brand_id
        FROM brand_pr_applications a
        WHERE a.creator_id = %s
    ) delivered
'''

# Founder-report at-limit: distinct PR brands delivered in the current calendar month.
DELIVERED_THIS_MONTH_BY_CREATOR_SQL = '''
    SELECT creator_id, COUNT(*) AS delivered FROM (
        SELECT bu.creator_id, bu.brand_id
        FROM brand_unlocks bu
        WHERE bu.unlocked_at >= date_trunc('month', NOW())
          AND bu.unlocked_at < date_trunc('month', NOW()) + interval '1 month'
          AND EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION
        SELECT pp.creator_id, pp.brand_id
        FROM pr_packages pp
        WHERE pp.generated_at IS NOT NULL
          AND pp.generated_at >= date_trunc('month', NOW())
          AND pp.generated_at < date_trunc('month', NOW()) + interval '1 month'
        UNION
        SELECT a.creator_id, a.brand_id
        FROM brand_pr_applications a
        WHERE a.applied_at >= date_trunc('month', NOW())
          AND a.applied_at < date_trunc('month', NOW()) + interval '1 month'
    ) delivered
    GROUP BY creator_id
'''

DELIVERED_LAST_MONTH_BY_CREATOR_SQL = '''
    SELECT creator_id, COUNT(*) AS delivered FROM (
        SELECT bu.creator_id, bu.brand_id
        FROM brand_unlocks bu
        WHERE bu.unlocked_at >= date_trunc('month', NOW()) - interval '1 month'
          AND bu.unlocked_at < date_trunc('month', NOW())
          AND EXISTS (SELECT 1 FROM pr_brands pb WHERE pb.id = bu.brand_id)
        UNION
        SELECT pp.creator_id, pp.brand_id
        FROM pr_packages pp
        WHERE pp.generated_at IS NOT NULL
          AND pp.generated_at >= date_trunc('month', NOW()) - interval '1 month'
          AND pp.generated_at < date_trunc('month', NOW())
        UNION
        SELECT a.creator_id, a.brand_id
        FROM brand_pr_applications a
        WHERE a.applied_at >= date_trunc('month', NOW()) - interval '1 month'
          AND a.applied_at < date_trunc('month', NOW())
    ) delivered
    GROUP BY creator_id
'''


def usage_from_delivered(delivered, limit=FREE_UNLOCK_LIMIT):
    """used / free remaining / total remaining for the quota bar."""
    used = min(limit, max(0, int(delivered or 0)))
    remaining_free = max(0, limit - used)
    return used, remaining_free, remaining_free


def free_limit(cursor, creator_id):
    """Free credits a month for this creator: FREE_UNLOCK_LIMIT unless overridden in creator_credit_overrides
    (e.g. 0 after a refunded Pro cancel, so a refund never turns into free credits)."""
    cursor.execute("SELECT to_regclass('public.creator_credit_overrides') IS NOT NULL AS ok")
    row = cursor.fetchone() or {}
    if not (row.get("ok") if isinstance(row, dict) else row[0]):
        return FREE_UNLOCK_LIMIT
    cursor.execute("SELECT free_limit FROM creator_credit_overrides WHERE creator_id = %s", (creator_id,))
    row = cursor.fetchone()
    if not row:
        return FREE_UNLOCK_LIMIT
    value = row.get("free_limit") if isinstance(row, dict) else row[0]
    return max(0, int(value)) if value is not None else FREE_UNLOCK_LIMIT


def count_delivered_unlocks_all_time(cursor, creator_id):
    """Distinct PR brands ever delivered for one creator."""
    cursor.execute(DELIVERED_ALL_TIME_COUNT_SQL, (creator_id, creator_id, creator_id))
    row = cursor.fetchone() or {}
    if isinstance(row, dict):
        return int(row.get('n') or 0)
    return int(row[0] if row else 0)


def count_delivered_unlocks_this_month(cursor, creator_id):
    """Distinct PR brands this calendar month with a pack or unlock row, minus pitches written but not sent yet
    (credit_pending_send): a free pitch is spent when it's sent, not when Polly writes it."""
    cursor.execute(DELIVERED_THIS_MONTH_SQL, (creator_id, creator_id, creator_id))
    row = cursor.fetchone() or {}
    n = int(row.get('n') or 0) if isinstance(row, dict) else int(row[0] if row else 0)
    return max(0, n - pending_send_count(cursor, creator_id))


# --- Written, not sent yet -------------------------------------------------------------------------------------
# Free creators: Polly writes a batch of pitches (which creates the unlock / package rows that count as credits),
# but the credit is only spent when a pitch is sent (Gmail) or taken out (Open email / Copy). Until then the row
# below cancels it out of the monthly count. Unsent ones never come back as charges.

PENDING_DDL = """CREATE TABLE IF NOT EXISTS credit_pending_send (
    creator_id INTEGER NOT NULL, brand_id INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (creator_id, brand_id))"""
PENDING_FRESH_DAYS = 7


def _pending_ready(cursor):
    cursor.execute("SELECT to_regclass('public.credit_pending_send') IS NOT NULL AS ok")
    row = cursor.fetchone() or {}
    return bool(row.get("ok") if isinstance(row, dict) else row[0])


def pending_send_count(cursor, creator_id, fresh_only=False):
    """Pitches written this month and not sent yet (fresh_only: written in the last 7 days)."""
    try:
        if not _pending_ready(cursor):
            return 0
        extra = f" AND created_at > NOW() - INTERVAL '{PENDING_FRESH_DAYS} days'" if fresh_only else ""
        cursor.execute("SELECT COUNT(*) AS n FROM credit_pending_send WHERE creator_id = %s "
                       "AND created_at >= date_trunc('month', NOW())" + extra, (creator_id,))
        row = cursor.fetchone() or {}
        return int(row.get("n") or 0) if isinstance(row, dict) else int(row[0] if row else 0)
    except Exception:
        return 0


def pending_send_ids(cursor, creator_id):
    if not _pending_ready(cursor):
        return set()
    cursor.execute("SELECT brand_id FROM credit_pending_send WHERE creator_id = %s "
                   "AND created_at >= date_trunc('month', NOW())", (creator_id,))
    return {int(r.get("brand_id") if isinstance(r, dict) else r[0]) for r in cursor.fetchall()}


def mark_pending_send(cursor, creator_id, brand_ids):
    cursor.execute(PENDING_DDL)
    for bid in brand_ids or []:
        cursor.execute("INSERT INTO credit_pending_send (creator_id, brand_id) VALUES (%s, %s) "
                       "ON CONFLICT (creator_id, brand_id) DO UPDATE SET created_at = NOW()", (creator_id, int(bid)))


def charge_pending(cursor, creator_id, brand_ids):
    """The pitch left Polly (sent or taken out): its credit now counts."""
    if not brand_ids or not _pending_ready(cursor):
        return 0
    cursor.execute("DELETE FROM credit_pending_send WHERE creator_id = %s AND brand_id = ANY(%s)",
                   (creator_id, [int(b) for b in brand_ids]))
    return cursor.rowcount


def brand_already_delivered(cursor, creator_id, brand_id):
    """True if this creator already has a pack or unlock row for the brand."""
    cursor.execute(
        ALREADY_DELIVERED_SQL,
        (creator_id, brand_id, creator_id, brand_id, creator_id, brand_id),
    )
    return cursor.fetchone() is not None


def free_unlock_usage(cursor, creator_id, unlocks_remaining=None, unlocks_reset_at=None):
    """Quota bar is packs delivered this calendar month, not the remaining counter."""
    delivered = count_delivered_unlocks_this_month(cursor, creator_id)
    used, _remaining_free, remaining = usage_from_delivered(delivered, free_limit(cursor, creator_id))
    return used, remaining


def sync_free_unlock_remaining(cursor, creator_id):
    """Write unlocks_remaining to match packs delivered this month."""
    used, remaining = free_unlock_usage(cursor, creator_id)
    remaining_free = max(0, free_limit(cursor, creator_id) - used)
    cursor.execute(
        '''
        UPDATE creators
        SET unlocks_remaining = %s,
            unlocks_reset_at = date_trunc('month', NOW()) + interval '1 month'
        WHERE id = %s
          AND COALESCE(unlocks_tier, 'free') <> 'pro'
          AND COALESCE(subscription_tier, 'free') <> 'pro'
          AND unlocks_remaining IS DISTINCT FROM %s
        ''',
        (remaining_free, creator_id, remaining_free),
    )
    return used, remaining
