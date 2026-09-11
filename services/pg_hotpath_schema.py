"""Catalog checks so request handlers never ALTER hot tables.

ADD COLUMN / CREATE INDEX / ALTER COLUMN take ACCESS EXCLUSIVE (or Share)
locks even when the object already exists. Concurrent apply + list + count
requests then deadlock pr_brands vs creators.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def _value(row: Any, key: str, index: int = 0):
    if row is None:
        return None
    if isinstance(row, dict):
        if key in row:
            return row[key]
        return next(iter(row.values()))
    return row[index]


def public_table_exists(cursor, table: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        (table,),
    )
    return cursor.fetchone() is not None


def public_column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table, column),
    )
    return cursor.fetchone() is not None


def public_columns_exist(cursor, table: str, columns: Sequence[str]) -> bool:
    wanted = tuple(columns)
    if not wanted:
        return True
    cursor.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = ANY(%s)
        """,
        (table, list(wanted)),
    )
    return int(_value(cursor.fetchone(), "n") or 0) >= len(wanted)


def public_column_is_nullable(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table, column),
    )
    row = cursor.fetchone()
    if not row:
        return False
    return str(_value(row, "is_nullable")).upper() == "YES"


def public_index_exists(cursor, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = %s
        LIMIT 1
        """,
        (index_name,),
    )
    return cursor.fetchone() is not None


def public_constraint_exists(cursor, constraint_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conname = %s
        LIMIT 1
        """,
        (constraint_name,),
    )
    return cursor.fetchone() is not None


def columns_ready(
    cursor,
    pairs: Iterable[tuple[str, str]],
) -> bool:
    pairs = list(pairs)
    if not pairs:
        return True
    clauses = []
    params = []
    for table, column in pairs:
        clauses.append("(table_name = %s AND column_name = %s)")
        params.extend([table, column])
    cursor.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND ({" OR ".join(clauses)})
        """,
        params,
    )
    return int(_value(cursor.fetchone(), "n") or 0) >= len(pairs)


def tables_ready(cursor, tables: Sequence[str]) -> bool:
    wanted = tuple(tables)
    if not wanted:
        return True
    cursor.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (list(wanted),),
    )
    return int(_value(cursor.fetchone(), "n") or 0) >= len(wanted)
