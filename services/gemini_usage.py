"""Ledger of Gemini calls this app makes.

Google AI Studio is the invoice for every key on the project. The API key
cannot read that page. This table is the calls Newcollab itself logged,
priced from the token counts Gemini returns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_TABLE_READY = False


def _meta_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        meta = raw.get("usageMetadata") or raw.get("usage_metadata") or {}
        return meta if isinstance(meta, dict) else {}
    meta = getattr(raw, "usage_metadata", None)
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    return {
        "promptTokenCount": getattr(meta, "prompt_token_count", None),
        "candidatesTokenCount": getattr(meta, "candidates_token_count", None),
        "thoughtsTokenCount": getattr(meta, "thoughts_token_count", None),
        "cachedContentTokenCount": getattr(meta, "cached_content_token_count", None),
    }


def _pick(meta: Dict[str, Any], *names: str) -> int:
    for name in names:
        if meta.get(name) is not None:
            try:
                return max(0, int(meta.get(name) or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def priced_from_response(model: Optional[str], raw: Any) -> Dict[str, Any]:
    """Token price for one Gemini response. Empty when the response has no usage."""
    from services.polly_llm_cost import usage_from_gemini

    meta = _meta_dict(raw)
    if not meta:
        return {}
    normalized = {
        "usageMetadata": {
            "promptTokenCount": _pick(meta, "promptTokenCount", "prompt_token_count"),
            "candidatesTokenCount": _pick(meta, "candidatesTokenCount", "candidates_token_count"),
            "thoughtsTokenCount": _pick(meta, "thoughtsTokenCount", "thoughts_token_count"),
            "cachedContentTokenCount": _pick(meta, "cachedContentTokenCount", "cached_content_token_count"),
        }
    }
    priced = usage_from_gemini(normalized, model or "")
    if not (priced.get("input_tokens") or priced.get("output_tokens") or priced.get("usd")):
        return {}
    return priced


def _ensure(cursor, conn) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gemini_api_usage (
            id SERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            model TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            usd NUMERIC NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gemini_api_usage_created
        ON gemini_api_usage (created_at DESC)
        """
    )
    conn.commit()
    _TABLE_READY = True


def record_gemini_usage(source: str, model: Optional[str], raw: Any) -> None:
    """Store one call. Never raises into the caller."""
    priced = priced_from_response(model, raw)
    if not priced:
        return
    conn = None
    try:
        from pr_crm_routes import get_db_connection
        from psycopg2.extras import RealDictCursor

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure(cursor, conn)
        cursor.execute(
            """
            INSERT INTO gemini_api_usage
                (source, model, input_tokens, output_tokens, cached_tokens, usd)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(source or "other")[:40],
                (model or "")[:80] or None,
                int(priced.get("input_tokens") or 0),
                int(priced.get("output_tokens") or 0),
                int(priced.get("cached_tokens") or 0),
                float(priced.get("usd") or 0),
            ),
        )
        conn.commit()
    except Exception as err:
        print(f"[Gemini usage] log skipped: {err}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def gemini_spend(cursor, start) -> Dict[str, Any]:
    """Calls logged in gemini_api_usage since start. Empty when the table is missing."""
    empty = {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "sources": []}
    try:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'gemini_api_usage'
            """
        )
        if not cursor.fetchone():
            return empty
        cursor.execute(
            """
            SELECT source,
                   COUNT(*)::int AS calls,
                   COALESCE(SUM(usd), 0)::float AS usd,
                   COALESCE(SUM(input_tokens), 0)::int AS input_tokens,
                   COALESCE(SUM(output_tokens), 0)::int AS output_tokens
            FROM gemini_api_usage
            WHERE created_at >= %s
            GROUP BY 1
            ORDER BY usd DESC
            """,
            (start,),
        )
        sources: List[Dict[str, Any]] = []
        usd = calls = inp = out = 0
        for row in cursor.fetchall() or []:
            item = {
                "source": row.get("source") or "other",
                "calls": int(row.get("calls") or 0),
                "usd": round(float(row.get("usd") or 0), 6),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
            }
            sources.append(item)
            usd += item["usd"]
            calls += item["calls"]
            inp += item["input_tokens"]
            out += item["output_tokens"]
        return {
            "usd": round(usd, 6),
            "calls": calls,
            "input_tokens": inp,
            "output_tokens": out,
            "sources": sources,
        }
    except Exception as err:
        print(f"[Gemini usage] spend skipped: {err}")
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return empty
