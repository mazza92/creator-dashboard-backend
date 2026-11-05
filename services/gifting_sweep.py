"""Daily TikTok gifting sweep: every niche scanned once a day, so the list of brands giving out boxes grows
without waiting for a creator to search.

Runs from an hourly cron. Each call:
1. ingests sweep (or creator) runs that finished since the last call;
2. drafts newly seen gifting brands that aren't in the directory yet (team reviews and publishes them);
3. starts the scan for the next niche not swept in the last day, within the daily budget;
4. recomputes which brands are gifting-confirmed (services.gifting_confirmed).

A niche takes one Apify run (~$0.6-0.9) using the fixed hashtag and search bank (no model call).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import gifting_radar as radar
from services.radar_extract import NICHE_WORDS, display_name

SWEPT_WITHIN_HOURS = 22
MIN_CREATORS_FOR_DRAFT = 2
DRAFT_WINDOW_DAYS = 60
SERVED_FROM = "sweep"


def daily_budget_usd() -> float:
    try:
        return float(os.getenv("GIFTING_SWEEP_DAILY_USD") or "15")
    except ValueError:
        return 15.0


def sweep_spend_today(conn) -> float:
    cur = conn.cursor()
    cur.execute("""SELECT COALESCE(SUM(COALESCE(cost_usd, 0)
                                       + CASE WHEN status IN ('running', 'ingesting')
                                              THEN COALESCE((params->>'est_cost')::numeric, 0) ELSE 0 END), 0)
                   FROM discovery_runs
                   WHERE served_from = %s
                     AND created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
                (SERVED_FROM,))
    return float(cur.fetchone()[0] or 0)


def _swept_recently(conn) -> set:
    cur = conn.cursor()
    cur.execute(f"""SELECT DISTINCT niche FROM discovery_runs
                    WHERE served_from <> 'database' AND status IN ('running', 'ingesting', 'done')
                      AND created_at > NOW() - INTERVAL '{SWEPT_WITHIN_HOURS} hours'""")
    return {r[0] for r in cur.fetchall()}


def next_niche(conn) -> Optional[str]:
    """The niche swept longest ago (never-swept first), skipping any scanned in the last day."""
    recent = _swept_recently(conn)
    cur = conn.cursor()
    cur.execute("""SELECT niche, MAX(created_at) FROM discovery_runs
                   WHERE served_from <> 'database' GROUP BY niche""")
    last = {r[0]: r[1] for r in cur.fetchall()}
    todo = [n for n in NICHE_WORDS if n not in recent]
    if not todo:
        return None
    todo.sort(key=lambda n: (last.get(n) is not None, last.get(n) or 0))
    return todo[0]


def start_niche(conn, niche: str, dry_run: bool = False) -> Dict[str, Any]:
    plan = radar.stale_queries(conn, radar.query_plan(niche))[: radar.ROUND_SIZE]
    if not plan:
        return {"niche": niche, "status": "fresh"}
    est = radar.source.estimate_cost(len(plan) * radar.PER_QUERY)
    if sweep_spend_today(conn) + est > daily_budget_usd():
        return {"niche": niche, "status": "budget", "est_cost": est}
    if dry_run:
        return {"niche": niche, "status": "would_start", "queries": len(plan), "est_cost": est}
    try:
        apify_id = radar._start_round(plan)
    except radar.source.SourceUnavailable as err:
        return {"niche": niche, "status": "source_down", "error": str(err)[:200]}
    run_id = radar._record(conn, {"creator_id": None, "niche": niche}, status="running", served_from=SERVED_FROM,
                           run_ids=[apify_id],
                           queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                           params={"est_cost": est, "per_query": radar.PER_QUERY, "round": 1, "sweep": True})
    return {"niche": niche, "status": "started", "run_id": run_id, "queries": len(plan), "est_cost": est}


def draft_new_brands(conn, dry_run: bool = False, limit: int = 40) -> int:
    """Gifting brands seen on several small creators that the directory doesn't have yet become drafts."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"""
        SELECT d.id, d.name, d.tiktok_handle, d.website,
               (SELECT s2.niche FROM brand_signals s2 WHERE s2.discovered_brand_id = d.id
                GROUP BY s2.niche ORDER BY COUNT(*) DESC LIMIT 1) AS niche
        FROM discovered_brands d
        JOIN brand_signals s ON s.discovered_brand_id = d.id AND s.signal_type = 'gifted'
        JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE d.brand_id IS NULL AND d.last_signal_at > NOW() - INTERVAL '{DRAFT_WINDOW_DAYS} days'
        GROUP BY d.id
        HAVING COUNT(DISTINCT v.creator_handle) >= %s
        ORDER BY COUNT(DISTINCT v.creator_handle) DESC, d.last_signal_at DESC
        LIMIT %s
    """, (MIN_CREATORS_FOR_DRAFT, limit))
    rows = [dict(r) for r in cur.fetchall()]
    if dry_run or not rows:
        return len(rows)
    made = 0
    by_niche: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_niche.setdefault(r.get("niche") or "", []).append(
            {"discovered_brand_id": r["id"], "brand_id": None, "name": display_name(r["name"])})
    for niche, cards in by_niche.items():
        made += radar.save_drafts(conn, cards, niche)
    return made


def run(conn, dry_run: bool = False) -> Dict[str, Any]:
    from services.gifting_confirmed import refresh

    out: Dict[str, Any] = {}
    if not dry_run:
        radar.finalize_stale(conn, limit=2)
    out["drafted"] = draft_new_brands(conn, dry_run=dry_run)
    niche = next_niche(conn)
    out["scan"] = start_niche(conn, niche, dry_run=dry_run) if niche else {"status": "all_swept_today"}
    if not dry_run:
        from services.gifting_confirmed import hide_unread_requests
        out["confirmed"] = refresh(conn)
        out["requests_taken_off"] = hide_unread_requests(conn)
    out["spend_today_usd"] = round(sweep_spend_today(conn), 2)
    return out
