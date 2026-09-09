# -*- coding: utf-8 -*-
"""Insert UGC supply (creator prospects) into ugc_supply. Separate from pr_brands."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

OUTREACH_TAG = "UGC_SUPPLY_OUTREACH"
SOURCE = "tiktok_ugc_search"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ugc_supply (
    id SERIAL PRIMARY KEY,
    handle VARCHAR(64) NOT NULL UNIQUE,
    display_name TEXT,
    bio TEXT,
    contact_email TEXT,
    niche VARCHAR(64),
    location TEXT,
    followers INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    bio_link TEXT,
    avatar_url TEXT,
    profile_url TEXT,
    source VARCHAR(64) NOT NULL DEFAULT 'tiktok_ugc_search',
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    qualified BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_OUTREACH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ugc_supply_outreach_tracking (
    lead_id INTEGER PRIMARY KEY REFERENCES ugc_supply(id) ON DELETE CASCADE,
    outreach_count INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TIMESTAMPTZ,
    last_subject TEXT,
    last_response_status VARCHAR(64),
    response_notes TEXT,
    last_response_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS ugc_supply_outreach_log (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES ugc_supply(id) ON DELETE CASCADE,
    email_sent_to TEXT,
    subject TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'sent',
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    message_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ugc_supply_qualified
    ON ugc_supply (qualified, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ugc_supply_email
    ON ugc_supply (contact_email)
    WHERE contact_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ugc_supply_outreach_log_lead
    ON ugc_supply_outreach_log (lead_id, sent_at DESC);
"""


def ensure_schema(cursor) -> None:
    cursor.execute(_SCHEMA_SQL)
    for stmt in [s.strip() for s in _OUTREACH_SCHEMA_SQL.split(";") if s.strip()]:
        cursor.execute(stmt)


def _db():
    import psycopg2

    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def build_note(rec: Dict, *, batch_date: str) -> str:
    bits = [
        f"[{OUTREACH_TAG}]",
        "TikTok UGC creator: UGC + niche + email + 1k followers (location optional).",
        f"Batch {batch_date}.",
    ]
    if rec.get("qualified"):
        bits.append("Qualified.")
    else:
        missing = []
        if not rec.get("has_ugc"):
            missing.append("ugc")
        if not rec.get("niche"):
            missing.append("niche")
        if not rec.get("contact_email"):
            missing.append("email")
        if not rec.get("has_min_followers"):
            missing.append("followers")
        if missing:
            bits.append("Missing: " + ", ".join(missing) + ".")
    if rec.get("niche"):
        bits.append(f"Niche: {rec['niche']}.")
    if rec.get("location"):
        bits.append(f"Location: {rec['location']}.")
    return " ".join(bits)


def insert_leads(
    records: List[Dict],
    *,
    dry_run: bool = False,
    only_qualified: bool = True,
    batch_date: Optional[str] = None,
) -> Dict[str, int]:
    batch_date = batch_date or datetime.now(timezone.utc).date().isoformat()
    rows = [r for r in records if r.get("handle")]
    if only_qualified:
        rows = [r for r in rows if r.get("qualified")]
    stats = {"inserted": 0, "skipped": 0, "errors": 0, "considered": len(rows)}
    if dry_run:
        stats["inserted"] = len(rows)
        return stats

    conn = _db()
    try:
        cur = conn.cursor()
        ensure_schema(cur)
        for rec in rows:
            handle = (rec.get("handle") or "").lstrip("@").strip().lower()
            try:
                cur.execute(
                    """
                    INSERT INTO ugc_supply (
                        handle, display_name, bio, contact_email, niche, location,
                        followers, likes, video_count, bio_link, avatar_url,
                        profile_url, source, status, qualified, notes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, 'draft', %s, %s
                    )
                    ON CONFLICT (handle) DO NOTHING
                    """,
                    (
                        handle,
                        rec.get("display_name") or None,
                        rec.get("bio") or None,
                        rec.get("contact_email") or None,
                        rec.get("niche") or None,
                        rec.get("location") or None,
                        int(rec.get("followers") or 0),
                        int(rec.get("likes") or 0),
                        int(rec.get("video_count") or 0),
                        rec.get("bio_link") or None,
                        rec.get("avatar_url") or None,
                        rec.get("profile_url") or f"https://www.tiktok.com/@{handle}",
                        rec.get("source") or SOURCE,
                        bool(rec.get("qualified")),
                        build_note(rec, batch_date=batch_date),
                    ),
                )
                if cur.rowcount:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                print(f"[TikTokUGC] insert error @{handle}: {exc}")
                stats["errors"] += 1
                conn.rollback()
                cur = conn.cursor()
                ensure_schema(cur)
                continue
        conn.commit()
    finally:
        conn.close()
    return stats
