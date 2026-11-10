"""Permanent creator account deletion (GDPR / privacy right to erasure)."""

import hashlib
import logging
import os

from psycopg2 import sql

logger = logging.getLogger(__name__)

CONFIRMATION_PHRASE = "DELETE"
SKIP_TABLES = frozenset({"account_deletion_log", "users", "creators"})
CREATOR_ID_COLUMNS = ("creator_id", "supporter_id", "target_id")
USER_ID_COLUMNS = ("user_id",)
EMAIL_PURGE_TABLES = (
    "email_campaign_recipients",
    "email_logs",
    "pr_email_reminders",
)
MAX_DELETE_PASSES = 8


def hash_email(email):
    if not email:
        return ""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


# Why people leave, asked before deletion (Nov 2026: 19 deletions in 30 days, none with a reason; 4 were paying
# Pro members who skipped the cancel flow by deleting). Pro gets the questions the cancel flow already learns from;
# free creators get a shorter list about why the free plan didn't work for them.
PRO_REASONS = (
    ("no_replies", "Brands didn't reply"),
    ("too_expensive", "Too expensive for me"),
    ("not_using", "I wasn't using it"),
    ("found_alternative", "I found another way to get deals"),
    ("privacy", "Privacy: I don't want my data here"),
    ("other", "Something else"),
)
FREE_REASONS = (
    ("not_approved", "My profile wasn't approved"),
    ("no_brands_for_me", "No brands for my niche or country"),
    ("not_useful", "I didn't find it useful"),
    ("too_many_emails", "Too many emails"),
    ("privacy", "Privacy: I don't want my data here"),
    ("other", "Something else"),
)
ALL_REASONS = frozenset(k for k, _ in PRO_REASONS + FREE_REASONS)
FEEDBACK_MAX = 1000


def deletion_gate(cursor, creator_id):
    """What has to happen before this creator can delete: a paying Pro member cancels through the cancel flow
    first (reason + offers, like every subscription app), then can delete."""
    out = {"plan": "free", "must_cancel_first": False, "cancel_scheduled": False, "pro_ends_at": None,
           "reasons": [{"id": k, "label": v} for k, v in FREE_REASONS]}
    if not creator_id:
        return out
    cursor.execute(
        """SELECT subscription_tier, subscription_status, subscription_started_at, subscription_ends_at,
                  stripe_subscription_id
           FROM creators WHERE id = %s""",
        (creator_id,),
    )
    row = cursor.fetchone()
    if not row:
        return out
    row = dict(row) if not isinstance(row, dict) else row
    tier = (row.get("subscription_tier") or "free").lower()
    status = row.get("subscription_status") or ""
    if tier != "pro" or status not in ("active", "trialing", "past_due") or not row.get("stripe_subscription_id"):
        return out
    out["plan"] = "pro"
    out["reasons"] = [{"id": k, "label": v} for k, v in PRO_REASONS]
    ends = row.get("subscription_ends_at")
    out["pro_ends_at"] = ends.isoformat() if ends else None
    scheduled = False
    try:
        cursor.execute("SAVEPOINT del_gate")
        cursor.execute(
            """SELECT 1 FROM creator_retention_events
               WHERE creator_id = %s AND outcome = 'canceled'
                 AND created_at >= COALESCE(%s, '-infinity'::timestamptz)
               LIMIT 1""",
            (creator_id, row.get("subscription_started_at")),
        )
        scheduled = cursor.fetchone() is not None
        cursor.execute("RELEASE SAVEPOINT del_gate")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT del_gate")
    out["cancel_scheduled"] = scheduled
    out["must_cancel_first"] = not scheduled
    return out


def validate_deletion_request(body, account_email):
    """Return an error string, or None if the confirmation is valid."""
    payload = body or {}
    confirmation = str(payload.get("confirmation") or "").strip().upper()
    typed_email = str(payload.get("email") or "").strip().lower()
    expected_email = str(account_email or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()

    if reason not in ALL_REASONS:
        return "Tell us why you're leaving before deleting your account."
    if confirmation != CONFIRMATION_PHRASE:
        return "Type DELETE to confirm you want to permanently erase this account."
    if not expected_email or typed_email != expected_email:
        return "The email you typed does not match this account."
    return None


def ensure_account_deletion_schema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS account_deletion_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            creator_id INTEGER,
            email_hash TEXT NOT NULL,
            role TEXT,
            deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    from services.db_ddl import ensure_columns
    ensure_columns(cursor, """ALTER TABLE account_deletion_log ADD COLUMN IF NOT EXISTS reason TEXT,
                              ADD COLUMN IF NOT EXISTS feedback TEXT, ADD COLUMN IF NOT EXISTS plan TEXT""")


def cancel_stripe_billing(customer_id, subscription_id):
    """Cancel billing immediately, then remove the Stripe customer if possible."""
    secret = os.getenv("STRIPE_SECRET_KEY")
    if not secret or (not customer_id and not subscription_id):
        return

    import stripe

    stripe.api_key = secret

    if subscription_id:
        try:
            stripe.Subscription.cancel(subscription_id)
        except Exception:
            try:
                stripe.Subscription.delete(subscription_id)
            except Exception as exc:
                logger.warning("Could not cancel Stripe subscription %s: %s", subscription_id, exc)

    if customer_id:
        try:
            stripe.Customer.delete(customer_id)
        except Exception as exc:
            logger.warning("Could not delete Stripe customer %s: %s", customer_id, exc)


def _row_value(row, key, index):
    if isinstance(row, dict):
        return row[key]
    return row[index]


def _tables_with_columns(cursor, columns):
    cursor.execute(
        """
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
          AND c.column_name = ANY(%s)
        ORDER BY c.table_name
        """,
        (list(columns),),
    )
    return [
        (_row_value(row, "table_name", 0), _row_value(row, "column_name", 1))
        for row in cursor.fetchall()
    ]


def _try_delete(cursor, table, column, value):
    if not value or table in SKIP_TABLES:
        return 0
    cursor.execute("SAVEPOINT account_del")
    try:
        cursor.execute(
            sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                sql.Identifier(table),
                sql.Identifier(column),
            ),
            (value,),
        )
        deleted = cursor.rowcount or 0
        cursor.execute("RELEASE SAVEPOINT account_del")
        return deleted
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT account_del")
        return 0


def _try_anonymize_email(cursor, table, email):
    if not email or table in SKIP_TABLES:
        return
    cursor.execute("SAVEPOINT account_email")
    try:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = 'email'
            """,
            (table,),
        )
        if not cursor.fetchone():
            cursor.execute("RELEASE SAVEPOINT account_email")
            return
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}
                SET email = 'deleted-' || id::text || '@invalid.local'
                WHERE LOWER(email) = LOWER(%s)
                """
            ).format(sql.Identifier(table)),
            (email,),
        )
        extra = []
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = ANY(%s)
            """,
            (table, ["first_name", "username", "user_id", "creator_id"]),
        )
        extra = [row["column_name"] for row in cursor.fetchall()]
        if extra:
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = NULL").format(sql.Identifier(col)) for col in extra
            )
            cursor.execute(
                sql.SQL("UPDATE {} SET {} WHERE LOWER(email) = LOWER(%s)").format(
                    sql.Identifier(table),
                    assignments,
                ),
                (email,),
            )
        cursor.execute("RELEASE SAVEPOINT account_email")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT account_email")


def purge_creator_account(cursor, user_id, creator_id, email, role="creator", reason=None, feedback=None,
                          plan=None):
    """Delete the creator, user, and related personal data. Caller commits."""
    ensure_account_deletion_schema(cursor)

    creator_targets = _tables_with_columns(cursor, CREATOR_ID_COLUMNS)
    user_targets = _tables_with_columns(cursor, USER_ID_COLUMNS)

    for _ in range(MAX_DELETE_PASSES):
        deleted_any = 0
        if creator_id:
            for table, column in creator_targets:
                deleted_any += _try_delete(cursor, table, column, creator_id)
        for table, column in user_targets:
            deleted_any += _try_delete(cursor, table, column, user_id)
        if deleted_any == 0:
            break

    for table in EMAIL_PURGE_TABLES:
        _try_anonymize_email(cursor, table, email)

    if creator_id:
        cursor.execute("DELETE FROM creators WHERE id = %s", (creator_id,))
    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))

    cursor.execute(
        """
        INSERT INTO account_deletion_log (user_id, creator_id, email_hash, role, reason, feedback, plan)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, creator_id, hash_email(email), role, reason,
         (str(feedback).strip()[:FEEDBACK_MAX] or None) if feedback else None, plan),
    )
