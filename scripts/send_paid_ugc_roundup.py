"""Weekly Paid UGC radar email from Polly, free creators only (Pro gets Polly's 48h UGC roundup).

    py -3 scripts/send_paid_ugc_roundup.py --preview radar.html     # render only, no DB writes
    py -3 scripts/send_paid_ugc_roundup.py --count                  # how many would get it
    py -3 scripts/send_paid_ugc_roundup.py --test you@example.com   # one test send
    py -3 scripts/send_paid_ugc_roundup.py --send [--limit 200]     # the weekly send

Skips unsubscribed and waitlisted creators, and anyone already sent this ISO week.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from psycopg2.extras import RealDictCursor  # noqa: E402

from lifecycle_email_engine import get_db_connection  # noqa: E402
from public_routes import make_unsubscribe_token  # noqa: E402
from services.paid_ugc_roundup import (  # noqa: E402
    BACKEND_URL,
    ROUNDUP_SLUG,
    live_board_cards,
    roundup_context,
    roundup_email,
)
from services.polly_alerts import _default_send  # noqa: E402
from services.waitlist_gate import WAITLIST_EXCLUDE_SQL  # noqa: E402

SEND_PAUSE_SECONDS = 0.6

_AUDIENCE_SQL = f"""
    SELECT c.id AS creator_id, c.user_id, u.email, u.first_name
    FROM creators c
    JOIN users u ON u.id = c.user_id
    WHERE u.email IS NOT NULL AND u.email LIKE '%%@%%'
      AND u.unsubscribed_at IS NULL
      AND COALESCE(c.subscription_tier, 'free') = 'free'
      AND {WAITLIST_EXCLUDE_SQL}
      AND NOT EXISTS (
            SELECT 1 FROM lifecycle_email_sends s
            WHERE s.creator_id = c.id AND s.dedup_key = %s AND s.status = 'sent'
          )
    ORDER BY c.id
"""


def week_key(now=None) -> str:
    year, week, _ = (now or datetime.now(timezone.utc)).isocalendar()
    return f"{ROUNDUP_SLUG}:{year}-W{week:02d}"


def first_name(raw) -> str:
    return (raw or '').strip().split(' ')[0]


def unsubscribe_link(user_id) -> str:
    return f"{BACKEND_URL}/api/public/unsubscribe?uid={user_id}&token={make_unsubscribe_token(str(user_id))}"


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', metavar='FILE')
    mode.add_argument('--count', action='store_true')
    mode.add_argument('--test', metavar='EMAIL')
    mode.add_argument('--send', action='store_true')
    parser.add_argument('--limit', type=int, default=0, help='max recipients this run (0 = all)')
    parser.add_argument('--min-offers', type=int, default=5, help='skip the send if fewer live offers')
    args = parser.parse_args()

    conn = get_db_connection()
    conn.set_session(readonly=bool(args.preview or args.count))
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ctx = roundup_context(live_board_cards(cur))
        key = week_key()
        print(f"offers live={ctx['total']} new_this_week={ctx['new_this_week']} top_pay={ctx['top_pay']} week={key}")

        if args.preview:
            mail = roundup_email('Alex', ctx, is_pro=False, unsubscribe_url='#')
            with open(args.preview, 'w', encoding='utf-8') as fh:
                fh.write(mail['html'])
            print(f"subject: {mail['subject']}\npreview written to {args.preview}")
            return

        cur.execute(_AUDIENCE_SQL, (key,))
        audience = cur.fetchall()
        if args.count:
            print(f"eligible free creators this week: {len(audience)}")
            return

        if ctx['total'] < args.min_offers:
            print(f"only {ctx['total']} live offers, below --min-offers {args.min_offers}; not sending")
            return

        if args.test:
            mail = roundup_email('there', ctx, is_pro=False)
            result = _default_send(args.test, f"[TEST] {mail['subject']}", mail['html'], '', ROUNDUP_SLUG)
            print('test sent' if result.get('success') else f"test failed: {result.get('error')}")
            return

        recipients = audience[: args.limit] if args.limit else audience
        sent = failed = 0
        for row in recipients:
            unsub = unsubscribe_link(row['user_id'])
            mail = roundup_email(first_name(row.get('first_name')), ctx, False, unsub)
            result = _default_send(row['email'], mail['subject'], mail['html'], unsub, ROUNDUP_SLUG)
            ok = bool(result.get('success'))
            cur.execute(
                """
                INSERT INTO lifecycle_email_sends
                (creator_id, template_slug, email_address, subject_rendered,
                 provider_message_id, status, error_message, dedup_key, utm_campaign)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row['creator_id'], ROUNDUP_SLUG, row['email'], mail['subject'],
                    result.get('message_id') if ok else None, 'sent' if ok else 'failed',
                    None if ok else str(result.get('error'))[:500], key, ROUNDUP_SLUG,
                ),
            )
            conn.commit()
            sent += ok
            failed += not ok
            time.sleep(SEND_PAUSE_SECONDS)
        print(f"done: sent={sent} failed={failed} remaining={len(audience) - len(recipients)}")
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    main()
