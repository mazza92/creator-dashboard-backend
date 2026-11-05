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
