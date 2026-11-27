"""Start choice: newly approved creators choose between a 7-day Autopilot trial and 3 free pitches.

Nov 2026: 29% of approved creators sent a pitch and 7.2% went Pro; 3 free pitches give about a 7% chance of a brand
reply, while trials that ended converted 10 of 20. So every new approval sees a screen on their first dashboard
visit: "Polly runs your brand outreach for you, free for 7 days" (card up front, $0 today) or "Do it yourself"
(3 free pitches). This is the production flow, not a split test: the comparison is the two choices (paid Pro by
day 14 for trial pickers vs free pickers), recorded in creator_start_choice.

- Creators approved on or after LAUNCH_AT (Nov 24 2026, 18:00 UTC), never paid, with the FLAG on (kill switch).
- Recorded on their first eligible visit; the choice is saved when they pick.
- The free week is for creators who picked it. "Do it yourself" creators get the normal paid upgrade at every
  paywall (no trial), so the comparison stays clean and the 3 free pitches are the free offer they chose.

Version 2, matches first (Nov 26 2026): the choice screen asked for a plan before showing a single brand, and 5 of
the first 21 creators left without choosing. Now no screen comes first: Polly's welcome leads with their matches,
writes the pitch for their top match right away, and the choice happens on that pitch: "Use autopilot" (or
"Start my free week") is the 7-day trial; sending a pitch themselves is the manual path, and from then on every
paywall offers the paid plan directly. Arm 'matches_first'; creators who saw version 1 and never chose move to it.
No cardless trial: the card stays at checkout.
"""

from datetime import datetime, timezone

FLAG = 'start_choice_v1'
LAUNCH_AT = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)  # went live at the 18:03 UTC deploy
CHOICES = ('trial', 'free')
# Everyone eligible is in the 'choice' group; the column stays so the table reads the same if a holdout returns.
ARM = 'choice'
ARM_V2 = 'matches_first'

_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS creator_start_choice (
        creator_id INTEGER PRIMARY KEY,
        arm TEXT NOT NULL,
        choice TEXT,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        chosen_at TIMESTAMPTZ
    )
"""


def arm_for(creator_id) -> str:
    """Every eligible creator starts with their matches (version 2, Nov 26 2026)."""
    return ARM_V2


def flag_on() -> bool:
    try:
        from lifecycle_email_engine import is_feature_enabled
        return bool(is_feature_enabled(FLAG))
    except Exception:  # noqa: BLE001
        return False


def _one(row, key, idx=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[idx]


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_test(cursor, creator_id) -> bool:
    """Approved since launch and never paid: the population both arms are drawn from."""
    cursor.execute(
        """
        SELECT approval_status, approved_at, stripe_subscription_id, stripe_customer_id, subscription_started_at
        FROM creators WHERE id = %s
        """,
        (creator_id,),
    )
    row = cursor.fetchone()
    if not row:
        return False
    get = (lambda k, i: row.get(k)) if isinstance(row, dict) else (lambda k, i: row[i])
    approved_at = _aware(get('approved_at', 1))
    return (
        get('approval_status', 0) in ('approved', 'pro_approved')
        and approved_at is not None and approved_at >= LAUNCH_AT
        and not get('stripe_subscription_id', 2) and not get('stripe_customer_id', 3)
        and not get('subscription_started_at', 4)
    )


def _recorded(cursor, creator_id):
    cursor.execute("SELECT to_regclass('public.creator_start_choice') AS t")
    if not _one(cursor.fetchone(), 't'):
        return None
    cursor.execute("SELECT arm, choice FROM creator_start_choice WHERE creator_id = %s", (creator_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return {'arm': _one(row, 'arm', 0), 'choice': _one(row, 'choice', 1)}


def status(conn, creator_id, flag=None) -> dict:
    """{'show': bool, 'arm': str|None}. Records the arm the first time an eligible creator is seen."""
    cursor = conn.cursor()
    rec = _recorded(cursor, creator_id)
    if rec:
        if rec['arm'] == ARM and not rec['choice']:
            # Saw the version 1 screen and left without choosing: they get the matches-first journey instead.
            cursor.execute("UPDATE creator_start_choice SET arm = %s WHERE creator_id = %s AND choice IS NULL",
                           (ARM_V2, creator_id))
            conn.commit()
            return {'show': False, 'arm': ARM_V2}
        return {'show': False, 'arm': rec['arm']}
    if not (flag_on() if flag is None else flag) or not _in_test(cursor, creator_id):
        return {'show': False, 'arm': None}
    arm = arm_for(creator_id)
    cursor.execute(_TABLE_SQL)
    cursor.execute(
        "INSERT INTO creator_start_choice (creator_id, arm) VALUES (%s, %s) ON CONFLICT (creator_id) DO NOTHING",
        (creator_id, arm),
    )
    conn.commit()
    return {'show': arm == ARM, 'arm': arm}


def record_choice(conn, creator_id, choice) -> bool:
    """Saves 'trial' or 'free' for a creator in the choice arm. False when they aren't in it."""
    if choice not in CHOICES:
        return False
    cursor = conn.cursor()
    rec = _recorded(cursor, creator_id)
    # Version 2 records the choice made on the welcome flow's last step ("Let Polly handle the outreach" or
    # "I'll pitch manually"): manual = the paid plan at every paywall from then on, never the free week.
    if not rec or rec['arm'] not in (ARM, ARM_V2):
        return False
    cursor.execute(
        "UPDATE creator_start_choice SET choice = %s, chosen_at = NOW() WHERE creator_id = %s",
        (choice, creator_id),
    )
    conn.commit()
    return True


def trial_eligible(cursor, creator_id) -> bool:
    """Checkout offers the free week to new creators who never paid: everyone on the matches-first journey, and
    version 1 creators unless they picked free."""
    rec = _recorded(cursor, creator_id)
    if not rec or rec['choice'] == 'free':
        return False
    return rec['arm'] in (ARM, ARM_V2) and _in_test(cursor, creator_id)


def mark_pitched_manually(conn, creator_id) -> bool:
    """Matches first: a creator who sends a pitch themselves (Send email, Gmail, the Brands flow) before starting
    the free week chose to pitch manually. From then on, like "Do it yourself" in version 1, every paywall
    (out of free pitches included) offers the paid plan, not the free week. True when it changed something."""
    cursor = conn.cursor()
    rec = _recorded(cursor, creator_id)
    if not rec or rec['arm'] != ARM_V2 or rec['choice'] or not _in_test(cursor, creator_id):
        return False
    cursor.execute(
        "UPDATE creator_start_choice SET choice = %s, chosen_at = NOW() WHERE creator_id = %s AND choice IS NULL",
        ('free', creator_id),
    )
    conn.commit()
    return True


def journey(cursor, creator_id):
    """'matches_first' for version 2 creators, else their version 1 choice ('trial' / 'free') or None."""
    rec = _recorded(cursor, creator_id)
    if not rec:
        return None
    if rec['arm'] == ARM_V2:
        return ARM_V2  # their pick on the welcome flow's last step is picked() (the flow stays the same)
    return rec['choice']


def picked(cursor, creator_id):
    """What they chose on the welcome flow ('trial' / 'free'), None before they chose."""
    rec = _recorded(cursor, creator_id)
    return rec['choice'] if rec else None


def picked_free(cursor, creator_id) -> bool:
    """Chose "Do it yourself" (3 free pitches): every paywall offers the paid upgrade, never the free week."""
    rec = _recorded(cursor, creator_id)
    return bool(rec and rec['choice'] == 'free')
