"""Runtime 'make sure this index exists' without the lock.

``CREATE INDEX IF NOT EXISTS`` takes a SHARE lock on the table *before* it checks whether the index exists. On a
busy table that waits behind every uncommitted write, and requests that call each other (Polly's chat turn calls
the pitch writer) can end up waiting on themselves until the statement timeout: a 140s first Polly turn on
Nov 5. Look the index up first (no lock); only build it when it's really missing.
"""

import re

_NAME = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_\.\"]+)", re.I)


def ensure_index(cursor, ddl: str) -> None:
    m = _NAME.search(ddl or "")
    if m:
        name = m.group(1).strip('"')
        cursor.execute("SELECT to_regclass(%s) AS reg", (name if "." in name else f"public.{name}",))
        row = cursor.fetchone()
        exists = (row.get("reg") if isinstance(row, dict) else (row[0] if row else None)) if row else None
        if exists:
            return
    cursor.execute(ddl)


# ``ALTER TABLE t ADD COLUMN IF NOT EXISTS c`` takes the table's ACCESS EXCLUSIVE lock *before* checking whether the
# column exists. Queued behind any open reader, it then blocks every later reader of that table until it times
# out: Polly's first brand matches took 72-117s on Nov 6 while a paid-gigs request waited to "add" listing_brief to
# opportunities. Look the columns up first (no lock) and only alter when one is really missing.
_ADD_COL = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([A-Za-z0-9_\.\"]+)((?:\s*,?\s*ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
    r"[A-Za-z0-9_\"]+[^,;]*)+)", re.I)
_COL_NAME = re.compile(r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_\"]+)", re.I)


def _missing(cursor, table: str, column: str) -> bool:
    """Resolve the table the way the ALTER would (search_path), then look the column up in the catalog."""
    cursor.execute(
        "SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s AND NOT attisdropped",
        (table, column.strip('"')))
    return cursor.fetchone() is None


def ensure_columns(cursor, ddl: str, params=None) -> None:
    """Run DDL holding ``ADD COLUMN IF NOT EXISTS`` only when a column it adds is missing.

    Other statements in the same block (CREATE TABLE IF NOT EXISTS, CREATE INDEX…) still run."""
    statements = [s for s in (ddl or "").split(";") if s.strip()]
    keep = []
    for stmt in statements:
        m = _ADD_COL.search(stmt)
        if m and not any(_missing(cursor, m.group(1), c) for c in _COL_NAME.findall(m.group(2))):
            continue
        keep.append(stmt)
    if not keep:
        return
    sql = ";".join(keep)
    if params is None:
        cursor.execute(sql)
    else:
        cursor.execute(sql, params)
