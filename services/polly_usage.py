"""Polly beta usage — lightweight events + admin snapshot.

Does not store message text. Outcomes still come from timeline / applies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


_USAGE_TABLE_READY = False


def _cursor(conn):
    global _USAGE_TABLE_READY
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if _USAGE_TABLE_READY:
        return cur
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
    conn.commit()
    _USAGE_TABLE_READY = True
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


FUNNEL_STEPS = ("opened", "matches_shown", "pitch_or_apply", "paywall_shown", "upgraded")


def funnel_paywall_moment(
    moment: Optional[Dict[str, Any]],
    paywall: bool,
    task_chips: Optional[List[Dict]] = None,
    starters: Optional[List[Dict]] = None,
) -> Optional[Dict[str, Any]]:
    """Which upgrade prompt a chat reply showed, or None when it showed none."""
    if isinstance(moment, dict) and moment.get("moment"):
        return dict(moment)
    if paywall:
        return {"moment": "pitch"}
    if any((c or {}).get("action") == "unlock_pro" for c in (task_chips or [])):
        return {"moment": "chip"}
    if any((c or {}).get("action") == "unlock_pro" for c in (starters or [])):
        return {"moment": "starter"}
    return None


def polly_funnel(cursor, start: datetime) -> Dict[str, Any]:
    """Free creators who first opened Polly in the window, each step requiring the one before.

    Creators already on Pro at first open are counted separately, since they can't upgrade.
    Kit views sit beside the funnel: they depend on brands, not on the creator's next step.
    """
    empty = {
        "steps": [{"key": k, "creators": 0} for k in FUNNEL_STEPS],
        "already_pro": 0,
        "kit_viewed": 0,
        "paywall_moments": {},
        "upgrade_moments": {},
        "checkout_sources": {},
    }
    if not _table_exists(cursor, "polly_usage_events"):
        return empty
    has_timeline = _table_exists(cursor, "polly_timeline_events")
    has_apps = _table_exists(cursor, "brand_pr_applications")
    pitch_sql = "FALSE"
    kit_sql = "FALSE"
    if has_timeline:
        pitch_sql = (
            "EXISTS (SELECT 1 FROM polly_timeline_events t WHERE t.creator_id = o.creator_id "
            "AND t.event_type IN ('pitch_drafted','pitch_sent','campaign_applied') AND t.occurred_at >= o.first_open)"
        )
        kit_sql = (
            "EXISTS (SELECT 1 FROM polly_timeline_events t WHERE t.creator_id = o.creator_id "
            "AND t.event_type = 'portfolio_viewed' AND t.occurred_at >= o.first_open)"
        )
    if has_apps:
        apply_sql = (
            "EXISTS (SELECT 1 FROM brand_pr_applications a WHERE a.creator_id = o.creator_id "
            "AND a.applied_at >= o.first_open)"
        )
        pitch_sql = f"({pitch_sql} OR {apply_sql})"
    try:
        cursor.execute(
            f"""
            WITH o AS (
                SELECT creator_id, MIN(created_at) AS first_open
                FROM polly_usage_events
                WHERE event = 'open' AND created_at >= %s AND creator_id IS NOT NULL
                GROUP BY creator_id
            ),
            f AS (
                SELECT
                    o.creator_id,
                    EXISTS (SELECT 1 FROM polly_usage_events e WHERE e.creator_id = o.creator_id
                            AND e.event = 'matches_shown' AND e.created_at >= o.first_open) AS matches,
                    {pitch_sql} AS pitched,
                    {kit_sql} AS kit,
                    EXISTS (SELECT 1 FROM polly_usage_events e WHERE e.creator_id = o.creator_id
                            AND e.event = 'paywall_shown' AND e.created_at >= o.first_open) AS paywall,
                    COALESCE(c.unlocks_tier = 'pro' OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro', FALSE) AS pro_now,
                    c.subscription_started_at AS pro_since,
                    o.first_open
                FROM o
                LEFT JOIN creators c ON c.id = o.creator_id
            ),
            g AS (
                SELECT f.*,
                    (pro_now AND (pro_since IS NULL OR pro_since < first_open)) AS already_pro,
                    (pro_now AND pro_since IS NOT NULL AND pro_since >= first_open) AS upgraded_after
                FROM f
            )
            SELECT
                COUNT(*) FILTER (WHERE already_pro)::int AS already_pro,
                COUNT(*) FILTER (WHERE NOT already_pro)::int AS opened,
                COUNT(*) FILTER (WHERE NOT already_pro AND matches)::int AS matches_shown,
                COUNT(*) FILTER (WHERE NOT already_pro AND matches AND pitched)::int AS pitch_or_apply,
                COUNT(*) FILTER (WHERE NOT already_pro AND matches AND pitched AND paywall)::int AS paywall_shown,
                COUNT(*) FILTER (WHERE NOT already_pro AND matches AND pitched AND paywall AND upgraded_after)::int AS upgraded,
                COUNT(*) FILTER (WHERE upgraded_after)::int AS upgraded_any,
                COUNT(*) FILTER (WHERE NOT already_pro AND paywall)::int AS paywall_any,
                COUNT(*) FILTER (WHERE NOT already_pro AND kit)::int AS kit_viewed
            FROM g
            """,
            (start,),
        )
        row = cursor.fetchone() or {}
        cursor.execute(
            """
            WITH o AS (
                SELECT creator_id, MIN(created_at) AS first_open
                FROM polly_usage_events
                WHERE event = 'open' AND created_at >= %s AND creator_id IS NOT NULL
                GROUP BY creator_id
            )
            SELECT COALESCE(e.meta->>'moment', 'unknown') AS moment,
                   COUNT(DISTINCT e.creator_id)::int AS n
            FROM polly_usage_events e
            JOIN o ON o.creator_id = e.creator_id AND e.created_at >= o.first_open
            WHERE e.event = 'paywall_shown'
            GROUP BY 1
            """,
            (start,),
        )
        paywall_moments = _count_map(cursor.fetchall() or [], "moment")
        cursor.execute(
            """
            WITH o AS (
                SELECT creator_id, MIN(created_at) AS first_open
                FROM polly_usage_events
                WHERE event = 'open' AND created_at >= %s AND creator_id IS NOT NULL
                GROUP BY creator_id
            ),
            u AS (
                SELECT o.creator_id, c.subscription_started_at AS pro_since, o.first_open
                FROM o JOIN creators c ON c.id = o.creator_id
                WHERE (c.unlocks_tier = 'pro' OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro')
                  AND c.subscription_started_at IS NOT NULL
                  AND c.subscription_started_at >= o.first_open
            ),
            m AS (
                SELECT COALESCE((
                    SELECT e.meta->>'moment' FROM polly_usage_events e
                    WHERE e.creator_id = u.creator_id AND e.event = 'paywall_shown'
                      AND e.created_at >= u.first_open AND e.created_at <= u.pro_since
                    ORDER BY e.created_at DESC LIMIT 1
                ), 'no_polly_paywall') AS moment
                FROM u
            )
            SELECT moment, COUNT(*)::int AS n FROM m GROUP BY moment
            """,
            (start,),
        )
        upgrade_moments = _count_map(cursor.fetchall() or [], "moment")
        cursor.execute(
            """
            SELECT COALESCE(NULLIF(intent, ''), 'unknown') AS source,
                   COUNT(DISTINCT creator_id)::int AS n
            FROM polly_usage_events
            WHERE event = 'checkout_completed' AND created_at >= %s
            GROUP BY 1
            """,
            (start,),
        )
        checkout_sources = _count_map(cursor.fetchall() or [], "source")
    except Exception as err:
        print(f"[Polly usage] funnel skipped: {err}")
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return empty
    return {
        "steps": [{"key": k, "creators": int(row.get(k) or 0)} for k in FUNNEL_STEPS],
        "already_pro": int(row.get("already_pro") or 0),
        "upgraded_any": int(row.get("upgraded_any") or 0),
        "paywall_any": int(row.get("paywall_any") or 0),
        "kit_viewed": int(row.get("kit_viewed") or 0),
        "paywall_moments": paywall_moments,
        "upgrade_moments": upgrade_moments,
        "checkout_sources": checkout_sources,
    }


def autopilot_snapshot(cursor, start: datetime) -> Dict[str, Any]:
    """Who has Polly autopilot on, Gmail connected, and pitches actually sent."""
    empty = {
        "settings_rows": 0,
        "enabled": 0,
        "paused": 0,
        "gmail_live": 0,
        "ready": 0,
        "sent": 0,
        "sent_creators": 0,
        "queue": {},
        "creators": [],
    }
    if not _table_exists(cursor, "polly_autopilot"):
        return empty
    has_gmail = _table_exists(cursor, "polly_gmail_accounts")
    has_queue = _table_exists(cursor, "polly_autopilot_queue")
    try:
        gmail_join = ""
        gmail_live = "0"
        ready = "0"
        if has_gmail:
            gmail_join = (
                "LEFT JOIN polly_gmail_accounts g "
                "ON g.creator_id = a.creator_id AND g.revoked_at IS NULL"
            )
            gmail_live = "COUNT(*) FILTER (WHERE g.creator_id IS NOT NULL)"
            ready = "COUNT(*) FILTER (WHERE a.enabled AND g.creator_id IS NOT NULL)"
        cursor.execute(
            f"""
            SELECT
                COUNT(*)::int AS settings_rows,
                COUNT(*) FILTER (WHERE a.enabled)::int AS enabled,
                COUNT(*) FILTER (WHERE NOT a.enabled)::int AS paused,
                {gmail_live}::int AS gmail_live,
                {ready}::int AS ready
            FROM polly_autopilot a
            {gmail_join}
            """
        )
        counts = cursor.fetchone() or {}
        queue = {}
        sent = 0
        sent_creators = 0
        creators: List[Dict[str, Any]] = []
        if has_queue:
            cursor.execute(
                """
                SELECT status, COUNT(*)::int AS n
                FROM polly_autopilot_queue
                GROUP BY 1
                """
            )
            queue = _count_map(cursor.fetchall() or [], "status")
            cursor.execute(
                """
                SELECT COUNT(*)::int AS sent,
                       COUNT(DISTINCT creator_id)::int AS sent_creators
                FROM polly_autopilot_queue
                WHERE status = 'sent' AND sent_at >= %s
                """,
                (start,),
            )
            sent_row = cursor.fetchone() or {}
            sent = int(sent_row.get("sent") or 0)
            sent_creators = int(sent_row.get("sent_creators") or 0)
            gmail_select = "FALSE AS gmail"
            gmail_creator_join = ""
            if has_gmail:
                gmail_select = "(g.creator_id IS NOT NULL) AS gmail"
                gmail_creator_join = (
                    "LEFT JOIN polly_gmail_accounts g "
                    "ON g.creator_id = a.creator_id AND g.revoked_at IS NULL"
                )
            cursor.execute(
                f"""
                SELECT a.creator_id, c.username, a.enabled, a.monthly_target,
                       {gmail_select},
                       (c.unlocks_tier = 'pro'
                        OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro') AS pro,
                       COALESCE(q.drafts, 0)::int AS drafts,
                       COALESCE(q.approved, 0)::int AS approved,
                       COALESCE(q.sent, 0)::int AS sent
                FROM polly_autopilot a
                JOIN creators c ON c.id = a.creator_id
                {gmail_creator_join}
                LEFT JOIN (
                    SELECT creator_id,
                           COUNT(*) FILTER (WHERE status = 'draft') AS drafts,
                           COUNT(*) FILTER (WHERE status = 'approved') AS approved,
                           COUNT(*) FILTER (WHERE status = 'sent') AS sent
                    FROM polly_autopilot_queue
                    GROUP BY 1
                ) q ON q.creator_id = a.creator_id
                ORDER BY a.enabled DESC, a.updated_at DESC
                LIMIT 25
                """
            )
            for row in cursor.fetchall() or []:
                creators.append({
                    "creator_id": row.get("creator_id"),
                    "username": row.get("username") or "",
                    "enabled": bool(row.get("enabled")),
                    "gmail": bool(row.get("gmail")),
                    "pro": bool(row.get("pro")),
                    "monthly_target": int(row.get("monthly_target") or 0),
                    "drafts": int(row.get("drafts") or 0),
                    "approved": int(row.get("approved") or 0),
                    "sent": int(row.get("sent") or 0),
                })
        return {
            "settings_rows": int(counts.get("settings_rows") or 0),
            "enabled": int(counts.get("enabled") or 0),
            "paused": int(counts.get("paused") or 0),
            "gmail_live": int(counts.get("gmail_live") or 0),
            "ready": int(counts.get("ready") or 0),
            "sent": sent,
            "sent_creators": sent_creators,
            "queue": queue,
            "creators": creators,
        }
    except Exception as err:
        print(f"[Polly usage] autopilot snapshot skipped: {err}")
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return empty


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

    funnel = polly_funnel(cursor, start)
    autopilot = autopilot_snapshot(cursor, start)

    turns = usage.get("turns") or 0
    errors = usage.get("errors") or 0
    return {
        "funnel": funnel,
        "autopilot": autopilot,
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
