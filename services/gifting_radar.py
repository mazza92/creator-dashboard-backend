"""Gifting Radar (beta): brands publicly gifting or paying creators this creator's size.

Database first: every search warms the shared index, so a niche searched in the last 48h
answers instantly. Otherwise one async Apify run fills only the stale queries, and the
chat card polls until it's ingested. Polly only renders what this module returns.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import radar_source as source
from services.radar_extract import (
    MAX_CREATOR_FOLLOWERS,
    brand_key,
    brand_signals,
    display_name,
    keep_video,
    key_variants,
    radar_niche,
    video_niches,
)

DAILY_CAP_USD = float(os.getenv("RADAR_DAILY_CAP_USD", "3"))
SEARCHES_PER_DAY = {"free": 1, "pro": 3}
PER_QUERY = 20
SHARED_HASHTAGS = ("prpackage", "gifted")
HASHTAG_TTL = timedelta(hours=24)
QUERY_TTL = timedelta(days=3)
FRESH_FOR = timedelta(hours=48)
INSTANT_MIN = 5
PAGE = 7
RUN_TIMEOUT = timedelta(minutes=4)
MIN_CREATORS = 2

CAVEAT = ("Gifting is based on public posts tagged #gifted, #prpackage or similar. "
          "It's a strong signal, not a guarantee they'll pick you.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----- query plan ---------------------------------------------------------------

def query_plan(niche: str) -> List[Dict[str, Any]]:
    queries = [f"{niche} pr unboxing", f"gifted {niche}", f"{niche} pr haul", f"new {niche} pr package",
               f"small creator {niche} pr"]
    plan = [{"key": f"hashtag:{t}", "type": "hashtag", "query": t, "ttl": HASHTAG_TTL} for t in SHARED_HASHTAGS]
    plan += [{"key": f"search:{q}", "type": "search", "query": q, "ttl": QUERY_TTL} for q in queries]
    return plan


def stale_queries(conn, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT cache_key FROM apify_query_cache WHERE cache_key = ANY(%s) AND expires_at > NOW()",
                ([p["key"] for p in plan],))
    fresh = {r[0] for r in cur.fetchall()}
    return [p for p in plan if p["key"] not in fresh]


# ----- limits ---------------------------------------------------------------------

def searches_today(conn, creator_id: int) -> int:
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM discovery_runs
                   WHERE creator_id = %s AND served_from <> 'database'
                     AND created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
                (creator_id,))
    return int(cur.fetchone()[0] or 0)


def spend_today(conn) -> float:
    cur = conn.cursor()
    cur.execute("""SELECT COALESCE(SUM(CASE WHEN status IN ('running', 'ingesting')
                                            THEN COALESCE((params->>'est_cost')::numeric, 0)
                                            ELSE cost_usd END), 0)
                   FROM discovery_runs
                   WHERE created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""")
    return float(cur.fetchone()[0] or 0)


def last_topup(conn, niche: str) -> Optional[datetime]:
    cur = conn.cursor()
    cur.execute("""SELECT MAX(finished_at) FROM discovery_runs
                   WHERE niche = %s AND status = 'done' AND served_from <> 'database'""", (niche,))
    row = cur.fetchone()
    return row[0] if row else None


# ----- directory resolution ---------------------------------------------------------

def _domain_key(url: Any) -> str:
    host = str(url or "").lower()
    host = host.split("//")[-1].split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return brand_key(host.split(".")[0]) if host else ""


def load_directory(conn) -> Dict[str, Dict[str, Any]]:
    """brand_key -> directory row, keyed by TikTok handle, Instagram handle, name and website.

    Drafts are included so the team sees the link; cards only offer a pitch for published rows."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, brand_name AS name, slug, logo_url, category, contact_email,
                          application_form_url, tiktok_handle, instagram_handle, website, regions
                   FROM pr_brands WHERE COALESCE(status, 'published') IN ('published', 'draft')
                   ORDER BY (COALESCE(status, 'published') = 'published') DESC, id""")
    out: Dict[str, Dict[str, Any]] = {}
    for row in cur.fetchall():
        row = dict(row)
        for raw in (row.get("tiktok_handle"), row.get("instagram_handle"), row.get("name")):
            key = brand_key(raw)
            if len(key) >= 3:
                out.setdefault(key, row)
        dkey = _domain_key(row.get("website"))
        if len(dkey) >= 4:
            out.setdefault(dkey, row)
    return out


def match_directory(key: str, directory: Dict[str, Dict[str, Any]]):
    """(canonical key, directory row or {}) trying run-together country/store tails."""
    for variant in key_variants(key):
        if variant in directory:
            return variant, directory[variant]
    return key, {}


def _upsert_brand(cur, sig: Dict[str, Any], directory: Dict[str, Dict[str, Any]], seen_at) -> int:
    key, row = match_directory(sig["key"], directory)
    cur.execute("""
        INSERT INTO discovered_brands (brand_key, brand_id, name, tiktok_handle, last_signal_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (brand_key) DO UPDATE SET
          brand_id = COALESCE(discovered_brands.brand_id, EXCLUDED.brand_id),
          name = CASE WHEN discovered_brands.brand_id IS NULL AND EXCLUDED.brand_id IS NOT NULL
                      THEN EXCLUDED.name ELSE discovered_brands.name END,
          tiktok_handle = COALESCE(discovered_brands.tiktok_handle, EXCLUDED.tiktok_handle),
          last_signal_at = GREATEST(discovered_brands.last_signal_at, EXCLUDED.last_signal_at)
        RETURNING id
    """, (key, row.get("id"), row.get("name") or display_name(sig["name"]), sig.get("handle") or None, seen_at))
    return int(cur.fetchone()[0])


def relink(conn, directory: Optional[Dict[str, Dict[str, Any]]] = None, *, commit: bool = True) -> int:
    """Link discovered brands that the directory gained (or that a better match rule now finds)."""
    directory = directory if directory is not None else load_directory(conn)
    cur = conn.cursor()
    cur.execute("SELECT id, brand_key FROM discovered_brands WHERE brand_id IS NULL")
    linked = 0
    for bid, key in cur.fetchall():
        canon, row = match_directory(key, directory)
        if row:
            cur.execute("""
                UPDATE discovered_brands SET brand_id = %s, name = %s,
                  brand_key = CASE WHEN EXISTS (SELECT 1 FROM discovered_brands d WHERE d.brand_key = %s)
                                   THEN brand_key ELSE %s END
                WHERE id = %s
            """, (row["id"], row["name"], canon, canon, bid))
            linked += 1
    if commit:
        conn.commit()
    return linked


# ----- ingest -----------------------------------------------------------------------

def ingest(conn, items: List[Dict[str, Any]], niche: str, plan: List[Dict[str, Any]],
           cost_usd: float, now: Optional[datetime] = None) -> Dict[str, int]:
    """Store every fetched video, then signals for the small-creator ones. Returns counts."""
    now = now or _now()
    videos = [v for v in (source.normalize(i) for i in items or []) if v]
    directory = load_directory(conn)
    cur = conn.cursor()
    kept = signals = 0
    for v in videos:
        c = v["creator"]
        cur.execute("""
            INSERT INTO tiktok_videos (tiktok_id, url, caption, hashtags, mentions, views, likes, created_at,
                                       cover_url, is_ad, is_sponsored, language, creator_handle,
                                       creator_followers, creator_region, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (tiktok_id) DO UPDATE SET views = EXCLUDED.views, likes = EXCLUDED.likes,
              cover_url = EXCLUDED.cover_url, creator_followers = EXCLUDED.creator_followers, fetched_at = NOW()
        """, (v["tiktok_id"], v["url"], v["caption"], v["hashtags"], v["mentions"], v["views"], v["likes"],
              v["created_at"], v["cover_url"], v["is_ad"], v["is_sponsored"], v["language"], c["handle"],
              c["followers"], c["region"]))
        cur.execute("""
            INSERT INTO tiktok_creators (handle, followers, region, bio, fetched_at) VALUES (%s,%s,%s,%s,NOW())
            ON CONFLICT (handle) DO UPDATE SET followers = EXCLUDED.followers, bio = EXCLUDED.bio, fetched_at = NOW()
        """, (c["handle"], c["followers"], c["region"], c["bio"]))
        if not keep_video(v, now):
            continue
        kept += 1
        niches = list(dict.fromkeys(([niche] if v.get("query") else []) + video_niches(v))) or ["general"]
        for sig in brand_signals(v, directory.keys()):
            bid = _upsert_brand(cur, sig, directory, v["created_at"])
            for kind in sig["types"]:
                for n in niches:
                    cur.execute("""INSERT INTO brand_signals (discovered_brand_id, tiktok_id, signal_type, niche)
                                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (bid, v["tiktok_id"], kind, n))
                    signals += cur.rowcount
    share = round(cost_usd / max(1, len(plan)), 4)
    for p in plan:
        if p["type"] == "search":
            ids = [v["tiktok_id"] for v in videos if v.get("query") == p["query"]]
        else:
            ids = [v["tiktok_id"] for v in videos if not v.get("query") and p["query"] in v["hashtags"]]
        cur.execute("""
            INSERT INTO apify_query_cache (cache_key, query_type, query, video_ids, result_count, cost_usd,
                                           fetched_at, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s)
            ON CONFLICT (cache_key) DO UPDATE SET video_ids = EXCLUDED.video_ids,
              result_count = EXCLUDED.result_count, cost_usd = EXCLUDED.cost_usd, fetched_at = NOW(),
              expires_at = EXCLUDED.expires_at
        """, (p["key"], p["type"], p["query"], ids, len(ids), share, now + p["ttl"]))
    relink(conn, directory, commit=False)
    conn.commit()
    return {"fetched": len(videos), "kept": kept, "signals": signals}


# ----- ranking + cards -------------------------------------------------------------------

def _stats_rows(conn, niche: str, signal_type: str = "any") -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    having = "" if signal_type in ("", "any", None) else "AND BOOL_OR(s.signal_type = %(kind)s)"
    cur.execute(f"""
        SELECT db.id, db.name, db.brand_key, db.brand_id, db.tiktok_handle, db.status,
               COUNT(DISTINCT v.creator_handle) AS creators_60d,
               COUNT(DISTINCT v.creator_handle) FILTER (WHERE v.created_at > NOW() - INTERVAL '30 days') AS creators_30d,
               COUNT(DISTINCT v.creator_handle) FILTER (WHERE v.created_at > NOW() - INTERVAL '14 days') AS creators_14d,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.creator_followers) AS median_followers,
               MAX(v.creator_followers) AS max_followers,
               MAX(v.created_at) AS last_signal_at,
               ARRAY_AGG(DISTINCT s.signal_type) AS signal_types
        FROM brand_signals s
        JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        JOIN discovered_brands db ON db.id = s.discovered_brand_id
        WHERE s.niche = %(niche)s AND v.created_at > NOW() - INTERVAL '60 days'
          AND v.creator_followers <= %(cap)s AND db.status <> 'rejected'
        GROUP BY db.id
        HAVING COUNT(DISTINCT v.creator_handle) >= %(min)s {having}
    """, {"niche": niche, "cap": MAX_CREATOR_FOLLOWERS, "min": MIN_CREATORS, "kind": signal_type})
    return [dict(r) for r in cur.fetchall()]


def _directory_rows(conn, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, brand_name AS name, slug, logo_url, category, contact_email,
                          application_form_url, regions
                   FROM pr_brands WHERE id = ANY(%s) AND slug IS NOT NULL
                     AND COALESCE(status, 'published') = 'published'""", (ids,))
    return {int(r["id"]): dict(r) for r in cur.fetchall()}


def _examples(conn, brand_ids: List[int], niche: str) -> Dict[int, List[Dict[str, Any]]]:
    if not brand_ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT s.discovered_brand_id AS bid, v.tiktok_id, v.url, v.cover_url, v.views,
               v.creator_handle, v.creator_followers, v.created_at
        FROM brand_signals s JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE s.discovered_brand_id = ANY(%s) AND s.niche = %s
          AND v.created_at > NOW() - INTERVAL '60 days' AND v.creator_followers <= %s
    """, (brand_ids, niche, MAX_CREATOR_FOLLOWERS))
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in cur.fetchall():
        out.setdefault(int(r["bid"]), []).append(dict(r))
    return out


def score(row: Dict[str, Any], followers: int, region_match: bool, has_contact: bool) -> float:
    median = float(row.get("median_followers") or 0)
    near = bool(followers) and median and 0.5 * followers <= median <= 1.5 * followers
    return (int(row.get("creators_60d") or 0) * 3 + int(row.get("creators_14d") or 0) * 2
            + (3 if near else 0) + (2 if region_match else 0) + (2 if has_contact else 0))


def _k(n: float) -> str:
    n = float(n or 0)
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(int(n))


def _age(when: Optional[datetime], now: datetime) -> str:
    if not when:
        return ""
    days = max(0, (now - when).days)
    return "today" if days == 0 else f"{days}d"


def evidence_line(row: Dict[str, Any]) -> str:
    size = "10K" if int(row.get("max_followers") or 0) <= 10_000 else "25K"
    recent = int(row.get("creators_30d") or 0)
    if recent >= MIN_CREATORS:
        count, window = recent, "last 30 days"
    else:
        count, window = int(row.get("creators_60d") or 0), "last 60 days"
    return (f"{count} creators under {size} · {window} · median "
            f"{_k(row.get('median_followers'))} followers")


_BADGES = {"gifted": "🎁 Gifting", "paid": "💸 Paid", "affiliate": "🛍 TikTok Shop / affiliate"}


def pick_examples(videos: List[Dict[str, Any]], followers: int, limit: int = 3) -> List[Dict[str, Any]]:
    """Top videos from creators closest to the user's size, one per creator."""
    def closeness(v):
        f = max(1, int(v.get("creator_followers") or 1))
        return abs(math.log(f / max(1, followers))) if followers else 0

    out, handles = [], set()
    for v in sorted(videos, key=lambda v: (round(closeness(v), 1), -int(v.get("views") or 0))):
        if v["creator_handle"] in handles:
            continue
        handles.add(v["creator_handle"])
        out.append(v)
        if len(out) >= limit:
            break
    return out


def _thumb(cover: Optional[str], url: str) -> str:
    try:
        from media_proxy_routes import to_proxied_media_url
        return to_proxied_media_url(cover, post_url=url) if cover else ""
    except Exception:
        return cover or ""


def build_card(row: Dict[str, Any], directory: Dict[str, Any], videos: List[Dict[str, Any]], *,
               followers: int, country: Optional[str], now: datetime) -> Dict[str, Any]:
    from services.brand_shipping import country_name, ships_to_country

    types = [t for t in ("gifted", "paid", "affiliate") if t in (row.get("signal_types") or [])]
    ships = ships_to_country(directory.get("regions"), country) if directory and country else None
    contact = ("pitch" if directory.get("contact_email") else
               "apply" if directory.get("application_form_url") else "find_contact")
    examples = pick_examples(videos, followers)
    return {
        "id": f"radar-{row['id']}",
        "discovered_brand_id": int(row["id"]),
        "brand_id": directory.get("id") or None,
        "name": directory.get("name") or display_name(row["name"]),
        "slug": directory.get("slug") or None,
        "logo": directory.get("logo_url") or None,
        "category": directory.get("category") or None,
        "badges": [_BADGES[t] for t in types],
        "signal_types": types,
        "headline": "gifting small creators now" if int(row.get("creators_14d") or 0) else "gifting small creators recently",
        "evidence": evidence_line(row),
        "ships": ("Ships to you" if ships else f"Doesn't list {country_name(country)}" if ships is False
                  else "Shipping not confirmed") if country else None,
        "contact": contact,
        "application_form_url": directory.get("application_form_url") if contact == "apply" else None,
        "in_directory": bool(directory),
        "evidence_data": {
            "creators_30d": int(row.get("creators_30d") or 0),
            "creators_60d": int(row.get("creators_60d") or 0),
            "median_followers": int(row.get("median_followers") or 0),
            "max_followers": int(row.get("max_followers") or 0),
            "last_signal_at": row["last_signal_at"].isoformat() if row.get("last_signal_at") else None,
        },
        "videos": [{
            "url": v["url"], "handle": v["creator_handle"], "followers": _k(v["creator_followers"]),
            "views": _k(v["views"]), "age": _age(v.get("created_at"), now), "thumb": _thumb(v.get("cover_url"), v["url"]),
        } for v in examples],
    }


def ranked(conn, ctx: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Every qualifying brand for this creator, best first (cards)."""
    from services.brand_shipping import ships_to_country

    now = now or _now()
    rows = _stats_rows(conn, ctx["niche"], ctx.get("signal_type") or "any")
    directory = _directory_rows(conn, [int(r["brand_id"]) for r in rows if r.get("brand_id")])
    skip_ids = {int(i) for i in ctx.get("exclude_brand_ids") or [] if str(i).isdigit()}
    avoid = {str(c).lower() for c in ctx.get("avoid_categories") or []}
    followers = int(ctx.get("followers") or 0)
    country = ctx.get("country")
    scored = []
    for r in rows:
        d = directory.get(int(r["brand_id"])) if r.get("brand_id") else {}
        d = d or {}
        if d.get("id") and int(d["id"]) in skip_ids:
            continue
        if d.get("category") and str(d["category"]).lower() in avoid:
            continue
        ships = ships_to_country(d.get("regions"), country) if d and country else None
        if ships is False:
            continue
        scored.append((score(r, followers, bool(ships), bool(d.get("contact_email"))), r, d))
    scored.sort(key=lambda x: (-x[0], -(x[1]["last_signal_at"] or now).timestamp()))
    videos = _examples(conn, [int(r["id"]) for _, r, _ in scored[:40]], ctx["niche"])
    return [build_card(r, d, videos.get(int(r["id"]), []), followers=followers, country=country, now=now)
            for _, r, d in scored[:40]]


def envelope(cards: List[Dict[str, Any]], ctx: Dict[str, Any], *, run_id: Optional[int], served_from: str,
             note: str = "") -> Dict[str, Any]:
    offset = int(ctx.get("offset") or 0)
    page = cards[offset:offset + PAGE]
    niche = ctx["niche"]
    if len(page) >= 3:
        status = "found"
        biggest = max(int((b.get("evidence_data") or {}).get("max_followers") or 0) for b in page)
        under = "10K" if biggest <= 10_000 else "25K"
        intro = (f"I found {len(page)} brands that sent PR to {niche} creators under {under} followers in the "
                 "last 60 days. They're ranked by how often they work with creators your size, not by brand size.")
    elif page:
        status = "weak"
        intro = (f"Only {len(page)} brand{'s' if len(page) != 1 else ''} came up for {niche} this week. "
                 "Want me to widen the search?")
    else:
        status = "none"
        intro = (f"No brands came up gifting {niche} creators your size yet. "
                 "Want me to try a broader niche?")
    return {"status": status, "run_id": run_id, "niche": niche, "brands": page, "total": len(cards),
            "has_more": len(cards) > offset + PAGE, "offset": offset, "served_from": served_from,
            "title": f"Brands gifting {niche} creators like you", "intro": intro,
            "caveat": CAVEAT if page else "", "note": note}


# ----- search + poll ----------------------------------------------------------------------

def _record(conn, ctx: Dict[str, Any], *, status: str, served_from: str, queries=None, run_ids=None,
            params=None, brands: int = 0) -> int:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discovery_runs (creator_id, niche, region, status, served_from, cache_hit, apify_run_ids,
                                    queries, params, brands_returned, finished_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, CASE WHEN %s = 'done' THEN NOW() END)
        RETURNING id
    """, (ctx.get("creator_id"), ctx["niche"], ctx.get("country"), status, served_from,
          served_from == "database", list(run_ids or []), json.dumps(queries or []), json.dumps(params or {}),
          brands, status))
    run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def _inflight(conn, niche: str) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("""SELECT id FROM discovery_runs WHERE niche = %s AND status IN ('running', 'ingesting')
                     AND created_at > NOW() - %s ORDER BY id DESC LIMIT 1""", (niche, RUN_TIMEOUT))
    row = cur.fetchone()
    return int(row[0]) if row else None


def search(conn, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ctx: creator_id, niche, followers, country, is_pro, exclude_brand_ids, avoid_categories,
    signal_type, offset. Returns an envelope, or {"status": "searching", "run_id"}."""
    niche = radar_niche(ctx.get("niche"))
    if not niche:
        return {"status": "needs_niche"}
    ctx = {**ctx, "niche": niche}
    if niche in {str(c).lower() for c in ctx.get("avoid_categories") or []}:
        return {"status": "excluded_category", "niche": niche}
    finalize_stale(conn)
    cards = ranked(conn, ctx)
    fresh_at = last_topup(conn, niche)
    fresh = bool(fresh_at) and _now() - fresh_at < FRESH_FOR
    if (len(cards) >= INSTANT_MIN and fresh) or int(ctx.get("offset") or 0) > 0:
        run_id = _record(conn, ctx, status="done", served_from="database",
                         brands=len(cards[:PAGE]))
        return envelope(cards, ctx, run_id=run_id, served_from="database")
    running = _inflight(conn, niche)
    if running:
        return searching(running, niche, age_s=0)
    tier = "pro" if ctx.get("is_pro") else "free"
    plan = stale_queries(conn, query_plan(niche))
    if not plan:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return envelope(cards, ctx, run_id=run_id, served_from="database")
    if searches_today(conn, ctx["creator_id"]) >= SEARCHES_PER_DAY[tier]:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return envelope(cards, ctx, run_id=run_id, served_from="database", note="daily_limit")
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if spend_today(conn) + est > DAILY_CAP_USD:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return envelope(cards, ctx, run_id=run_id, served_from="database", note="budget")
    try:
        apify_id = source.start_run([p["query"] for p in plan if p["type"] == "hashtag"],
                                    [p["query"] for p in plan if p["type"] == "search"], PER_QUERY)
    except source.SourceUnavailable as err:
        print(f"[radar] start failed: {err}")
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return envelope(cards, ctx, run_id=run_id, served_from="database", note="source_down")
    served = "partial_topup" if cards or len(plan) < len(query_plan(niche)) else "full_apify"
    run_id = _record(conn, ctx, status="running", served_from=served, run_ids=[apify_id],
                     queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                     params={"est_cost": est, "per_query": PER_QUERY})
    print(f"[radar] run {run_id} niche={niche} queries={len(plan)} est=${est}")
    return searching(run_id, niche, age_s=0)


def status_line(niche: str, age_s: float, items: int = 0, ingesting: bool = False) -> str:
    if ingesting:
        return "Pulling the best examples…"
    if items:
        return f"Found {items} posts. Checking which brands work with creators your size…"
    return f"Searching TikTok for gifted and PR posts in {niche}…"


def searching(run_id: int, niche: str, age_s: float, items: int = 0, ingesting: bool = False) -> Dict[str, Any]:
    return {"status": "searching", "run_id": run_id, "niche": niche,
            "line": status_line(niche, age_s, items, ingesting),
            "title": f"Brands gifting {niche} creators like you"}


def _load_run(conn, run_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM discovery_runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _finish(conn, run: Dict[str, Any]) -> Dict[str, Any]:
    """Claim a finished Apify run and ingest it once. Returns {"done": bool, "items": n}."""
    state = source.run_state(run["apify_run_ids"][0])
    age = _now() - run["created_at"]
    if not state["finished"]:
        if age > RUN_TIMEOUT:
            cur = conn.cursor()
            cur.execute("UPDATE discovery_runs SET status = 'failed', error = 'timeout', finished_at = NOW() "
                        "WHERE id = %s AND status = 'running'", (run["id"],))
            conn.commit()
            return {"done": True, "failed": True}
        return {"done": False, "items": state["items_so_far"]}
    cur = conn.cursor()
    cur.execute("UPDATE discovery_runs SET status = 'ingesting' WHERE id = %s AND status = 'running' RETURNING id",
                (run["id"],))
    claimed = cur.fetchone()
    conn.commit()
    if not claimed:
        return {"done": False, "ingesting": True}
    try:
        items = source.fetch_items(state["dataset_id"]) if state["ok"] and state["dataset_id"] else []
        plan = [{**q, "ttl": HASHTAG_TTL if q["type"] == "hashtag" else QUERY_TTL} for q in run["queries"] or []]
        counts = ingest(conn, items, run["niche"], plan, state["cost_usd"])
        cur = conn.cursor()
        cur.execute("""UPDATE discovery_runs SET status = %s, cost_usd = %s, videos_fetched = %s,
                         duration_ms = %s, finished_at = NOW(), error = %s WHERE id = %s""",
                    ("done" if state["ok"] else "failed", state["cost_usd"], counts["fetched"],
                     int(age.total_seconds() * 1000), None if state["ok"] else state["status"], run["id"]))
        conn.commit()
        print(f"[radar] run {run['id']} ingested {counts} cost=${state['cost_usd']:.3f}")
        return {"done": True}
    except Exception as err:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("UPDATE discovery_runs SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                    (str(err)[:300], run["id"]))
        conn.commit()
        print(f"[radar] ingest failed run={run['id']}: {err}")
        return {"done": True, "failed": True}


def poll(conn, run_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    run = _load_run(conn, run_id)
    if not run:
        return {"status": "not_found"}
    niche = run["niche"]
    ctx = {**ctx, "niche": niche}
    if run["status"] in ("running", "ingesting"):
        age = (_now() - run["created_at"]).total_seconds()
        if run["status"] == "ingesting" and age > (RUN_TIMEOUT + timedelta(minutes=2)).total_seconds():
            cur = conn.cursor()
            cur.execute("UPDATE discovery_runs SET status = 'failed', error = 'ingest stalled', finished_at = NOW() "
                        "WHERE id = %s AND status = 'ingesting'", (run_id,))
            conn.commit()
            run = _load_run(conn, run_id) or run
        elif run["status"] == "ingesting":
            return searching(run_id, niche, age, ingesting=True)
        if run["status"] == "running":
            try:
                step = _finish(conn, run)
            except source.SourceUnavailable as err:
                print(f"[radar] poll failed run={run_id}: {err}")
                return searching(run_id, niche, age)
            if not step.get("done"):
                return searching(run_id, niche, age, items=step.get("items") or 0,
                                 ingesting=bool(step.get("ingesting")))
            run = _load_run(conn, run_id) or run
    cards = ranked(conn, ctx)
    if run.get("creator_id") == ctx.get("creator_id") and not run.get("brands_returned"):
        cur = conn.cursor()
        cur.execute("UPDATE discovery_runs SET brands_returned = %s WHERE id = %s", (len(cards[:PAGE]), run_id))
        conn.commit()
    note = "source_down" if run["status"] == "failed" else ""
    return envelope(cards, ctx, run_id=run_id, served_from=run["served_from"], note=note)


def finalize_stale(conn, limit: int = 1) -> None:
    """Ingest runs nobody polled to the end (tab closed), so paid results aren't lost."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT * FROM discovery_runs WHERE status = 'running' AND created_at < NOW() - INTERVAL '45 seconds'
                   ORDER BY id LIMIT %s""", (limit,))
    for run in [dict(r) for r in cur.fetchall()]:
        try:
            _finish(conn, run)
        except Exception as err:
            conn.rollback()
            print(f"[radar] finalize skipped run={run['id']}: {err}")


def request_contact(conn, discovered_brand_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("""UPDATE discovered_brands SET contact_requests = contact_requests + 1,
                     status = CASE WHEN status = 'new' THEN 'enriching' ELSE status END
                   WHERE id = %s RETURNING id""", (discovered_brand_id,))
    ok = bool(cur.fetchone())
    conn.commit()
    return ok


def evidence_pitch_line(brand_name: str, niche: str, evidence: Dict[str, Any]) -> str:
    """One natural line for the pitch. Never names the example creators."""
    recent = int((evidence or {}).get("creators_30d") or 0)
    when = "this month" if recent >= MIN_CREATORS else "recently"
    return (f"I've seen {brand_name} sending PR to {niche} creators {when}, and I'd love to be part "
            "of that next wave.")
