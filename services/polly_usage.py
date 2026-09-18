"""Polly beta usage — lightweight events + admin snapshot.

Does not store message text. Outcomes still come from timeline / applies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_usage_events (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER,
            event TEXT NOT NULL,
            intent TEXT,
            llm TEXT,
            ok BOOLEAN NOT NULL DEFAULT TRUE,
            error TEXT,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_polly_usage_created
        ON polly_usage_events (created_at DESC)
        """
    )
    return cur


def log_usage(
    conn,
    creator_id: Optional[int],
    event: str,
    *,
    intent: Optional[str] = None,
    llm: Optional[str] = None,
    ok: bool = True,
    error: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if not conn or not event:
        return
    try:
        cur = _cursor(conn)
        cur.execute(
            """
            INSERT INTO polly_usage_events
                (creator_id, event, intent, llm, ok, error, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                creator_id,
                str(event)[:40],
                (intent or "")[:64] or None,
                (llm or "")[:32] or None,
                bool(ok),
                (error or "")[:180] or None,
                Json(meta or {}),
            ),
        )
        conn.commit()
    except Exception as err:
        print(f"[Polly usage] log skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass


def _table_exists(cursor, name: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (name,),
    )
    return bool(cursor.fetchone())


def _count_map(rows: List[Dict], key: str, val: str = "n") -> Dict[str, int]:
    out = {}
    for row in rows or []:
        name = str(row.get(key) or "unknown")
        out[name] = int(row.get(val) or 0)
    return out


def period_start(days: int) -> datetime:
    return utc_now() - timedelta(days=max(1, int(days or 7)))


def polly_beta_snapshot(cursor, days: int = 7) -> Dict[str, Any]:
    start = period_start(days)
    usage = {
        "openers": 0,
        "chatters": 0,
        "turns": 0,
        "errors": 0,
        "llm": {},
        "intents": {},
        "cost": {
            "usd": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "priced_turns": 0,
            "source": "gemini_anthropic_usage",
        },
    }
    if _table_exists(cursor, "polly_usage_events"):
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE event = 'open')::int AS opens,
                COUNT(DISTINCT creator_id) FILTER (WHERE event = 'open')::int AS openers,
                COUNT(*) FILTER (WHERE event = 'chat')::int AS turns,
                COUNT(DISTINCT creator_id) FILTER (WHERE event = 'chat')::int AS chatters,
                COUNT(*) FILTER (WHERE event = 'chat_error' OR ok = FALSE)::int AS errors
            FROM polly_usage_events
            WHERE created_at >= %s
            """,
            (start,),
        )
        row = cursor.fetchone() or {}
        usage["opens"] = int(row.get("opens") or 0)
        usage["openers"] = int(row.get("openers") or 0)
        usage["turns"] = int(row.get("turns") or 0)
        usage["chatters"] = int(row.get("chatters") or 0)
        usage["errors"] = int(row.get("errors") or 0)
        cursor.execute(
            """
            SELECT COALESCE(llm, 'none') AS llm, COUNT(*)::int AS n
            FROM polly_usage_events
            WHERE created_at >= %s AND event = 'chat'
            GROUP BY 1
            """,
            (start,),
        )
        usage["llm"] = _count_map(cursor.fetchall() or [], "llm")
        cursor.execute(
            """
            SELECT COALESCE(intent, 'chat') AS intent, COUNT(*)::int AS n
            FROM polly_usage_events
            WHERE created_at >= %s AND event = 'chat'
            GROUP BY 1
            ORDER BY n DESC
            LIMIT 12
            """,
            (start,),
        )
        usage["intents"] = _count_map(cursor.fetchall() or [], "intent")
        try:
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(NULLIF(meta->>'usd', '')::numeric), 0)::float AS usd,
                    COALESCE(SUM(NULLIF(meta->>'input_tokens', '')::int), 0)::int AS input_tokens,
                    COALESCE(SUM(NULLIF(meta->>'output_tokens', '')::int), 0)::int AS output_tokens,
                    COUNT(*) FILTER (
                        WHERE COALESCE(NULLIF(meta->>'usd', '')::numeric, 0) > 0
                           OR COALESCE(NULLIF(meta->>'input_tokens', '')::int, 0) > 0
                    )::int AS priced_turns
                FROM polly_usage_events
                WHERE created_at >= %s AND event = 'chat'
                """,
                (start,),
            )
            cost_row = cursor.fetchone() or {}
            usage["cost"] = {
                "usd": round(float(cost_row.get("usd") or 0), 6),
                "input_tokens": int(cost_row.get("input_tokens") or 0),
                "output_tokens": int(cost_row.get("output_tokens") or 0),
                "priced_turns": int(cost_row.get("priced_turns") or 0),
                "source": "gemini_anthropic_usage",
            }
        except Exception as err:
            print(f"[Polly usage] cost rollup skipped: {err}")
            try:
                cursor.connection.rollback()
            except Exception:
                pass

    threads = {"creators": 0, "active": 0}
    if _table_exists(cursor, "polly_threads"):
        cursor.execute("SELECT COUNT(*)::int AS n FROM polly_threads")
        threads["creators"] = int((cursor.fetchone() or {}).get("n") or 0)
        cursor.execute(
            """
            SELECT COUNT(*)::int AS n FROM polly_threads
            WHERE updated_at >= %s
              AND jsonb_typeof(messages) = 'array'
              AND jsonb_array_length(messages) > 0
            """,
            (start,),
        )
        threads["active"] = int((cursor.fetchone() or {}).get("n") or 0)

    outcomes = {
        "pitch_sent": 0,
        "pitch_drafted": 0,
        "applied": 0,
        "kit_views": 0,
        "checkins": 0,
    }
    if _table_exists(cursor, "polly_timeline_events"):
        cursor.execute(
            """
            SELECT event_type, COUNT(*)::int AS n
            FROM polly_timeline_events
            WHERE occurred_at >= %s
            GROUP BY 1
            """,
            (start,),
        )
        by_type = _count_map(cursor.fetchall() or [], "event_type")
        outcomes["pitch_sent"] = by_type.get("pitch_sent", 0)
        outcomes["pitch_drafted"] = by_type.get("pitch_drafted", 0)
        outcomes["applied"] = by_type.get("campaign_applied", 0)
        outcomes["kit_views"] = by_type.get("portfolio_viewed", 0)
        outcomes["checkins"] = (
            by_type.get("email_bounced", 0)
            + by_type.get("pitch_not_sent", 0)
            + by_type.get("brand_replied_rejected", 0)
            + by_type.get("brand_replied_interested", 0)
        )

    applies = {"total": 0, "creators": 0}
    if _table_exists(cursor, "brand_pr_applications"):
        cursor.execute(
            """
            SELECT COUNT(*)::int AS n, COUNT(DISTINCT creator_id)::int AS creators
            FROM brand_pr_applications
            WHERE applied_at >= %s
            """,
            (start,),
        )
        row = cursor.fetchone() or {}
        applies["total"] = int(row.get("n") or 0)
        applies["creators"] = int(row.get("creators") or 0)

    pain = {}
    if _table_exists(cursor, "polly_threads"):
        cursor.execute(
            """
            SELECT COALESCE(notes->'active_pain'->>'code', 'none') AS code, COUNT(*)::int AS n
            FROM polly_threads
            WHERE notes->'active_pain'->>'status' = 'open'
            GROUP BY 1
            """
        )
        pain = _count_map(cursor.fetchall() or [], "code")

    turns = usage.get("turns") or 0
    errors = usage.get("errors") or 0
    return {
        "days": days,
        "reach": {
            "openers": usage.get("openers") or 0,
            "opens": usage.get("opens") or 0,
            "chatters": usage.get("chatters") or threads["active"],
            "threads": threads["creators"],
            "active_threads": threads["active"],
        },
        "chat": {
            "turns": turns,
            "errors": errors,
            "error_rate": round((errors / turns) * 100, 1) if turns else 0,
        },
        "llm": usage.get("llm") or {},
        "cost": usage.get("cost") or {
            "usd": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "priced_turns": 0,
            "source": "gemini_anthropic_usage",
        },
        "intents": usage.get("intents") or {},
        "outcomes": outcomes,
        "applies": applies,
        "pain": pain,
    }
