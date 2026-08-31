"""Free monthly pack quota: 3 distinct PR brands per calendar month (DB local time)."""

FREE_UNLOCK_LIMIT = 3

# Distinct PR brands delivered this month. Ignore legacy `brands` ids that are
# not in pr_brands so the old public-directory unlock cannot steal pack credits.
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
    ) delivered
'''

ALREADY_DELIVERED_SQL = '''
    SELECT 1 FROM brand_unlocks
    WHERE creator_id = %s AND brand_id = %s
    UNION ALL
    SELECT 1 FROM pr_packages
    WHERE creator_id = %s AND brand_id = %s
    LIMIT 1
'''


def usage_from_delivered(delivered, pack_credits=0):
    """used / free remaining / total remaining for the quota bar."""
    used = min(FREE_UNLOCK_LIMIT, max(0, int(delivered or 0)))
    remaining_free = max(0, FREE_UNLOCK_LIMIT - used)
    remaining = remaining_free + max(0, int(pack_credits or 0))
    return used, remaining_free, remaining


def count_delivered_unlocks_this_month(cursor, creator_id):
    """Distinct PR brands this calendar month with a pack or unlock row."""
    cursor.execute(DELIVERED_THIS_MONTH_SQL, (creator_id, creator_id))
    row = cursor.fetchone() or {}
    if isinstance(row, dict):
        return int(row.get('n') or 0)
    return int(row[0] if row else 0)


def brand_already_delivered(cursor, creator_id, brand_id):
    """True if this creator already has a pack or unlock row for the brand."""
    cursor.execute(
        ALREADY_DELIVERED_SQL,
        (creator_id, brand_id, creator_id, brand_id),
    )
    return cursor.fetchone() is not None


def free_unlock_usage(cursor, creator_id, unlocks_remaining=None, pack_credits=0, unlocks_reset_at=None):
    """Quota bar is packs delivered this calendar month, not the remaining counter."""
    delivered = count_delivered_unlocks_this_month(cursor, creator_id)
    used, _remaining_free, remaining = usage_from_delivered(delivered, pack_credits)
    return used, remaining


def sync_free_unlock_remaining(cursor, creator_id, pack_credits=0):
    """Write unlocks_remaining to match packs delivered this month."""
    used, remaining = free_unlock_usage(cursor, creator_id, pack_credits=pack_credits)
    remaining_free = max(0, FREE_UNLOCK_LIMIT - used)
    cursor.execute(
        '''
        UPDATE creators
        SET unlocks_remaining = %s,
            unlocks_reset_at = date_trunc('month', NOW()) + interval '1 month'
        WHERE id = %s
          AND COALESCE(unlocks_tier, 'free') <> 'pro'
          AND COALESCE(subscription_tier, 'free') NOT IN ('pro', 'elite')
          AND unlocks_remaining IS DISTINCT FROM %s
        ''',
        (remaining_free, creator_id, remaining_free),
    )
    return used, remaining
