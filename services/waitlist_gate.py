"""Keep product/marketing emails away from creators still on the waitlist.

Waitlist notices themselves (joined / approved / rejected) go through
waitlist_emails.py and must not use this gate.
"""

from __future__ import annotations

from typing import Any, Optional

WAITLISTED_STATUSES = ("pending", "rejected")

WAITLIST_EXCLUDE_SQL = "COALESCE(c.approval_status, '') NOT IN ('pending', 'rejected')"


def is_waitlisted_status(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in WAITLISTED_STATUSES


def is_waitlisted_email(cursor: Any, email: Optional[str]) -> bool:
    """True when this address belongs to a creator who is pending or rejected."""
    if not email:
        return False
    cursor.execute(
        """
        SELECT 1
        FROM creators c JOIN users u ON u.id = c.user_id
        WHERE LOWER(u.email) = LOWER(%s)
          AND c.approval_status IN ('pending', 'rejected')
        LIMIT 1
        """,
        (email.strip(),),
    )
    return cursor.fetchone() is not None
