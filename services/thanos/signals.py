"""Thanos signals: what every pitch on Newcollab has produced, per brand and per creator-size band.

Sources (distinct creators per brand, last 365 days):
- polly_timeline_events: pitch_sent, brand_replied_interested / _question / _rejected, portfolio_viewed (the brand
  opened the creator's kit through a tracked link), email_bounced
- creator_pipeline: pitches sent and replies logged outside Polly
- brand_pr_applications: creators a brand picked from a gifted roster (ships / posted)

One query, cached for CACHE_SECONDS. Anything that records an outcome calls invalidate() so the next ranking
sees it: a brand that answers one creator moves up for similar creators within minutes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

from services.thanos.model import Evidence

CACHE_SECONDS = 120
LOOKBACK_DAYS = 365
RECENT_DAYS = 14

_BAND_SQL = """
    CASE
        WHEN f < 1000 THEN 'nano'
        WHEN f < 10000 THEN 'micro'
        WHEN f < 50000 THEN 'mid'
        WHEN f < 250000 THEN 'macro'
        ELSE 'mega'
    END
"""

STATS_SQL = f"""
    WITH raw AS (
        SELECT creator_id, brand_id,
               event_type = 'pitch_sent' AS sent,
               event_type = 'pitch_sent' AND occurred_at > NOW() - INTERVAL '{RECENT_DAYS} days' AS recent,
               event_type IN ('brand_replied_interested', 'brand_replied_question') AS pos,
               event_type = 'brand_replied_rejected' AS rej,
               event_type = 'portfolio_viewed' AS opened,
               event_type = 'email_bounced' AS bounced,
               FALSE AS picked
        FROM polly_timeline_events
        WHERE brand_id IS NOT NULL AND creator_id IS NOT NULL
          AND occurred_at > NOW() - INTERVAL '{LOOKBACK_DAYS} days'
          AND event_type IN ('pitch_sent', 'brand_replied_interested', 'brand_replied_question',
                             'brand_replied_rejected', 'portfolio_viewed', 'email_bounced')
        UNION ALL
        SELECT creator_id, brand_id,
               (pitched_at IS NOT NULL OR send_confirmed IS TRUE
                OR stage IN ('pitched', 'waiting', 'followup', 'replied', 'won', 'received', 'responded', 'success')),
               COALESCE(pitched_at, updated_at) > NOW() - INTERVAL '{RECENT_DAYS} days'
                AND (pitched_at IS NOT NULL OR send_confirmed IS TRUE),
               (replied_at IS NOT NULL OR stage IN ('replied', 'won', 'received', 'responded', 'success')),
               FALSE, FALSE, FALSE, FALSE
        FROM creator_pipeline
        WHERE brand_id IS NOT NULL AND creator_id IS NOT NULL
          AND COALESCE(pitched_at, updated_at, created_at) > NOW() - INTERVAL '{LOOKBACK_DAYS} days'
        UNION ALL
        SELECT creator_id, brand_id, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, TRUE
        FROM brand_pr_applications
        WHERE brand_id IS NOT NULL AND creator_id IS NOT NULL AND status IN ('ships', 'posted')
    ),
    per AS (
        SELECT creator_id, brand_id, BOOL_OR(sent) AS sent, BOOL_OR(recent) AS recent, BOOL_OR(pos) AS pos,
               BOOL_OR(rej) AS rej, BOOL_OR(opened) AS opened, BOOL_OR(bounced) AS bounced,
               BOOL_OR(picked) AS picked
        FROM raw GROUP BY creator_id, brand_id
    )
    SELECT per.brand_id,
           LOWER(COALESCE(b.category, '')) AS category,
           (SELECT {_BAND_SQL} FROM (SELECT COALESCE(NULLIF(c.creator_followers, 0), c.followers_count, 0) AS f) x)
               AS band,
           COUNT(*) FILTER (WHERE per.sent) AS sends,
           COUNT(*) FILTER (WHERE per.pos) AS positive,
           COUNT(*) FILTER (WHERE per.rej AND NOT per.pos) AS rejected,
           COUNT(*) FILTER (WHERE per.picked) AS selected,
           COUNT(*) FILTER (WHERE per.opened AND NOT per.pos AND NOT per.rej) AS kit_opens,
           COUNT(*) FILTER (WHERE per.bounced) AS bounces,
           COUNT(*) FILTER (WHERE per.recent) AS recent_load
    FROM per
    JOIN pr_brands b ON b.id = per.brand_id
    LEFT JOIN creators c ON c.id = per.creator_id
    GROUP BY 1, 2, 3
"""

_FIELDS = ("sends", "positive", "rejected", "selected", "kit_opens", "bounces", "recent_load")


@dataclass
class Stats:
    total: Evidence = field(default_factory=Evidence)
    brand: Dict[int, Evidence] = field(default_factory=dict)
    brand_band: Dict[Tuple[int, str], Evidence] = field(default_factory=dict)
    category_band: Dict[Tuple[str, str], Evidence] = field(default_factory=dict)
    category: Dict[str, Evidence] = field(default_factory=dict)
    loaded_at: float = 0.0

    def for_brand(self, brand_id) -> Evidence:
        try:
            return self.brand.get(int(brand_id)) or Evidence()
        except (TypeError, ValueError):
            return Evidence()

    def for_brand_band(self, brand_id, band: str) -> Evidence:
        try:
            return self.brand_band.get((int(brand_id), band)) or Evidence()
        except (TypeError, ValueError):
            return Evidence()

    def for_category_band(self, category: str, band: str) -> Evidence:
        key = (str(category or "").lower(), band)
        return self.category_band.get(key) or self.category.get(key[0]) or Evidence()


def _get(row, key, idx):
    return row.get(key) if isinstance(row, dict) else row[idx]


def build(rows) -> Stats:
    stats = Stats(loaded_at=time.time())
    for row in rows or []:
        try:
            bid = int(_get(row, "brand_id", 0))
        except (TypeError, ValueError):
            continue
        category = str(_get(row, "category", 1) or "").lower()
        band = str(_get(row, "band", 2) or "micro")
        ev = Evidence(**{f: float(_get(row, f, 3 + i) or 0) for i, f in enumerate(_FIELDS)})
        stats.brand_band[(bid, band)] = stats.brand_band.get((bid, band), Evidence()).add(ev)
        stats.brand[bid] = stats.brand.get(bid, Evidence()).add(ev)
        stats.category_band[(category, band)] = stats.category_band.get((category, band), Evidence()).add(ev)
        stats.category[category] = stats.category.get(category, Evidence()).add(ev)
        stats.total = stats.total.add(ev)
    return stats


_CACHE: Dict[str, Stats] = {}
_LOCK = threading.Lock()


def invalidate() -> None:
    """Call after recording a pitch, reply, roster pick, kit open or bounce."""
    with _LOCK:
        _CACHE.pop("stats", None)


def load(conn, force: bool = False) -> Stats:
    """Never raises: with no data, every brand falls back to the global prior."""
    now = time.time()
    with _LOCK:
        cached = _CACHE.get("stats")
        if cached and not force and now - cached.loaded_at < CACHE_SECONDS:
            return cached
    from services.thanos.db import fetch
    try:
        rows = fetch(conn, STATS_SQL)
    except Exception as err:  # noqa: BLE001  (no usable connection)
        print(f"[Thanos] signals load failed, using priors: {err}")
        rows = None
    if rows is None:
        with _LOCK:
            return _CACHE.get("stats") or Stats(loaded_at=now)
    stats = build(rows)
    with _LOCK:
        _CACHE["stats"] = stats
    return stats
