"""Thanos reads inside the caller's transaction, so a failed read must never undo the caller's own writes:
every query runs in a savepoint and only that savepoint is rolled back."""

from __future__ import annotations

from typing import Any, List, Optional


def fetch(conn, sql: str, params: Any = None, cursor_factory=None) -> Optional[List[Any]]:
    """Rows, or None when the query failed (logged)."""
    cur = conn.cursor(cursor_factory=cursor_factory) if cursor_factory else conn.cursor()
    savepoint = not getattr(conn, "autocommit", False)
    try:
        if savepoint:
            cur.execute("SAVEPOINT thanos_read")
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        if savepoint:
            cur.execute("RELEASE SAVEPOINT thanos_read")
        return rows
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] query failed: {err}")
        try:
            if savepoint:
                cur.execute("ROLLBACK TO SAVEPOINT thanos_read")
        except Exception:  # noqa: BLE001
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None
