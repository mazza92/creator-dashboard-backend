"""Persist Polly threads per creator so leaving Directory doesn't wipe chat."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

_MAX_MESSAGES = 80


def ensure_polly_thread_table(cursor, conn) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_threads (
            creator_id INTEGER PRIMARY KEY,
            messages JSONB NOT NULL DEFAULT '[]'::jsonb,
            suggested_brands JSONB NOT NULL DEFAULT '[]'::jsonb,
            notes JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.commit()


def _clean_message(msg: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(msg, dict):
        return None
    role = (msg.get("role") or "").strip().lower()
    if role not in ("user", "assistant"):
        return None
    content = msg.get("content")
    if content is None:
        content = ""
    out = {
        "id": msg.get("id"),
        "role": role,
        "content": str(content)[:8000],
    }
    brands = msg.get("brands")
    if isinstance(brands, list) and brands:
        out["brands"] = brands[:8]
    pitch = msg.get("pitch")
    if isinstance(pitch, dict) and pitch:
        out["pitch"] = pitch
    kit_actions = msg.get("kit_actions")
    if isinstance(kit_actions, list) and kit_actions:
        out["kit_actions"] = kit_actions[:4]
    task_chips = msg.get("task_chips")
    if isinstance(task_chips, list) and task_chips:
        out["task_chips"] = task_chips[:6]
    kind = msg.get("kind")
    if kind in ("nudge", "brief", "alert"):
        out["kind"] = kind
        if msg.get("brief"):
            out["brief"] = msg.get("brief")
    return out


def sanitize_thread(messages: Optional[List], suggested: Optional[List] = None) -> Dict[str, Any]:
    cleaned = []
    for msg in messages or []:
        item = _clean_message(msg)
        if item:
            cleaned.append(item)
    brands = []
    for row in suggested or []:
        if isinstance(row, dict) and row.get("id") and row.get("name"):
            brands.append(row)
    return {
        "messages": cleaned[-_MAX_MESSAGES:],
        "suggested_brands": brands[:12],
    }


def load_thread(conn, creator_id: int) -> Dict[str, Any]:
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    ensure_polly_thread_table(cursor, conn)
    cursor.execute(
        "SELECT messages, suggested_brands, notes FROM polly_threads WHERE creator_id = %s",
        (creator_id,),
    )
    row = cursor.fetchone()
    if not row:
        return {"messages": [], "suggested_brands": [], "notes": {}}
    messages = row.get("messages") or []
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except Exception:
            messages = []
    brands = row.get("suggested_brands") or []
    if isinstance(brands, str):
        try:
            brands = json.loads(brands)
        except Exception:
            brands = []
    notes = row.get("notes") or {}
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except Exception:
            notes = {}
    cleaned = sanitize_thread(messages, brands)
    cleaned["notes"] = notes if isinstance(notes, dict) else {}
    return cleaned


def save_thread(
    conn,
    creator_id: int,
    messages: Optional[List] = None,
    suggested: Optional[List] = None,
    notes: Optional[Dict] = None,
) -> Dict[str, Any]:
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    ensure_polly_thread_table(cursor, conn)
    payload = sanitize_thread(messages, suggested)
    if notes is None:
        cursor.execute(
            """
            INSERT INTO polly_threads (creator_id, messages, suggested_brands, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                messages = EXCLUDED.messages,
                suggested_brands = EXCLUDED.suggested_brands,
                updated_at = NOW()
            """,
            (creator_id, Json(payload["messages"]), Json(payload["suggested_brands"])),
        )
    else:
        cursor.execute(
            """
            INSERT INTO polly_threads (creator_id, messages, suggested_brands, notes, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (creator_id) DO UPDATE SET
                messages = EXCLUDED.messages,
                suggested_brands = EXCLUDED.suggested_brands,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            """,
            (
                creator_id,
                Json(payload["messages"]),
                Json(payload["suggested_brands"]),
                Json(notes if isinstance(notes, dict) else {}),
            ),
        )
    conn.commit()
    payload["notes"] = notes if isinstance(notes, dict) else {}
    return payload
