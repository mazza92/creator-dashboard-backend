"""Brand workspace prompt library: niche-level insights for a brand, answered from the Gifting Radar store.

Each prompt is its own query, not a brand scan:
  gifting_brands  Brands seeding small creators in the brand's niche right now (who to watch / copy).
  niche_hooks     Opening lines several creators reuse across the niche (scripts to brief creators on).
  niche_creators  Small creators posting gifted / PR content in the niche, plus vetted Newcollab matches.

Database first. When the niche hasn't been topped up recently, one Apify run of the niche's PR hashtags and
searches fills the store. It's recorded as a normal radar run for that niche, so the creator-side Radar
benefits from it too and nothing is paid twice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import brand_scan as scan
from services import gifting_radar as radar
from services import radar_source as source
from services.radar_extract import MAX_CREATOR_FOLLOWERS, brand_key, radar_niche
from services.radar_queries import SHARED_HASHTAGS, niche_queries

KINDS = ("gifting_brands", "niche_hooks", "niche_creators")
WINDOW = timedelta(days=60)
FRESH_FOR = timedelta(hours=72)
MIN_VIDEOS = 25
PER_QUERY = 15
SEARCHES = 6
TITLES = {
    "gifting_brands": "Brands seeding creators in {niche}",
    "niche_hooks": "Hooks creators reuse in {niche}",
    "niche_creators": "Creators posting gifted {niche} content",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Brand categories the radar has no niche for, mapped to the closest one it indexes.
CATEGORY_NICHE = {"luxury": "fashion", "jewelry": "fashion", "activewear": "fitness", "supplements": "wellness",
                  "sustainable": "home", "lifestyle": "home", "travel": "home", "gaming": "tech", "beauty": "makeup"}


def niche_for(category: Any) -> str:
    text = str(category or "").strip().lower()
    return CATEGORY_NICHE.get(text) or radar_niche(text) or ""


def insight_key(kind: str, niche: str) -> str:
    return f"insight:{kind}:{niche}"


# ----- data ----------------------------------------------------------------------------------

def niche_videos(conn, niche: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Recent small-creator posts filed under this niche by the radar (any signal), one row per video."""
    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (v.tiktok_id) v.tiktok_id, v.url, v.caption, v.hashtags, v.mentions, v.views, v.likes,
               v.created_at, v.cover_url, v.is_ad, v.is_sponsored, v.creator_handle, v.creator_followers
        FROM brand_signals s JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE s.niche = %s AND v.created_at > %s AND v.creator_followers <= %s
        ORDER BY v.tiktok_id
    """, (niche, now - WINDOW, MAX_CREATOR_FOLLOWERS))
    return sorted((dict(r) for r in cur.fetchall()), key=lambda v: -int(v.get("views") or 0))


def gifting_brands(conn, niche: str, exclude_key: str = "", now: Optional[datetime] = None,
                   thumb=None, limit: int = 12) -> List[Dict[str, Any]]:
    """Brands with the most distinct small creators posting their gifted / paid product in the niche."""
    now = now or _now()
    rows = [r for r in radar._stats_rows(conn, niche) if brand_key(r.get("brand_key")) != exclude_key]
    rows.sort(key=lambda r: (-int(r.get("creators_30d") or 0), -int(r.get("creators_60d") or 0)))
    rows = rows[:limit]
    examples = radar._examples(conn, [int(r["id"]) for r in rows], niche)
    out = []
    for r in rows:
        vids = sorted(examples.get(int(r["id"]), []), key=lambda v: -int(v.get("views") or 0))
        seen, picks = set(), []
        for v in vids:
            if v["creator_handle"] in seen:
                continue
            seen.add(v["creator_handle"])
            picks.append(scan.video_card({**v, "caption": "", "hashtags": [], "mentions": []}, r["brand_key"], now, thumb))
            if len(picks) == 3:
                break
        types = [t for t in (r.get("signal_types") or []) if t]
        out.append({
            "name": r["name"],
            "key": r["brand_key"],
            "creators_60d": int(r.get("creators_60d") or 0),
            "creators_30d": int(r.get("creators_30d") or 0),
            "median_followers_label": scan._k(r.get("median_followers")),
            "types": types,
            "last_post": scan._age(r.get("last_signal_at"), now),
            "examples": picks,
        })
    return out


def build(kind: str, conn, niche: str, *, brand_name: str = "", now: Optional[datetime] = None, thumb=None,
          matches=None, scan_id: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    now = now or _now()
    label = niche.replace("_", " ")
    out: Dict[str, Any] = {"status": "ready", "kind": kind, "scan_id": scan_id, "niche": niche,
                           "title": TITLES[kind].format(niche=label), "insight": True}
    own = brand_key(brand_name)
    if kind == "gifting_brands":
        brands = gifting_brands(conn, niche, own, now, thumb)
        out["brands"] = brands
        if brands:
            top = brands[0]
            out["summary"] = (f"**{len(brands)} brands** are seeding small {label} creators right now. "
                              f"**{top['name']}** leads with **{top['creators_30d'] or top['creators_60d']} creators** "
                              f"posting in the last {'30' if top['creators_30d'] else '60'} days. Scan any of them to "
                              "see their full creator list and hooks.")
        else:
            out["status"] = "empty"
            out["summary"] = f"No brands seeding {label} creators in our index yet. Check back shortly."
    else:
        videos = niche_videos(conn, niche, now)
        if kind == "niche_hooks":
            groups = scan.group_hooks(videos)[:10]
            out["hooks"] = [{
                "hook": scan.short_hook(g["videos"][0].get("caption")), "videos": len(g["videos"]),
                "creators": g["creators"], "views": g["views"], "views_label": scan._k(g["views"]),
                "examples": [scan.video_card(v, "", now, thumb) for v in g["videos"][:3]],
            } for g in groups]
            out["top_videos"] = [scan.video_card(v, "", now, thumb) for v in videos[:12]]
            if groups:
                h = out["hooks"][0]
                out["summary"] = (f"Across **{len(videos)} recent {label} creator posts**, **{len(groups)} opening "
                                  f"lines** repeat. The most used, “{h['hook']}”, ran on **{h['videos']} videos** "
                                  f"from {h['creators']} creators. Brief your creators on the ones that fit you.")
            else:
                out["status"] = "empty" if not videos else "ready"
                out["summary"] = (f"No repeated hooks yet across {len(videos)} recent {label} posts. Here are the "
                                  "top videos instead." if videos else f"No recent {label} creator posts indexed yet.")
        else:
            creators = scan.creator_rows(videos, "")[:30]
            out["creators"] = creators
            out["top_videos"] = [scan.video_card(v, "", now, thumb) for v in videos[:12]]
            out["matches"] = matches
            if creators:
                out["summary"] = (f"**{len(creators)} small creators** posted gifted or PR {label} content in the "
                                  "last 60 days. Below are the ones getting the most views, then vetted Newcollab "
                                  "creators who are open to gifting your brand.")
            else:
                out["status"] = "empty"
                out["summary"] = f"No recent {label} creator posts indexed yet. Your Newcollab matches are below."
    if note:
        out["note"] = note
    return out


# ----- runs ------------------------------------------------------------------------------------

def query_plan(niche: str) -> List[Dict[str, Any]]:
    bank = niche_queries(niche)
    tags = list(dict.fromkeys(list(SHARED_HASHTAGS) + bank["hashtags"]))
    plan = [{"key": f"hashtag:{t}", "type": "hashtag", "query": t, "ttl": radar.HASHTAG_TTL} for t in tags]
    plan += [{"key": f"search:{q}", "type": "search", "query": q, "ttl": radar.QUERY_TTL}
             for q in bank["searches"][:SEARCHES]]
    return plan


def _respond(kind, conn, niche, *, brand_name, redis_client, scan_id=None, note=""):
    try:
        if kind == "gifting_brands":
            rows = radar._stats_rows(conn, niche)
            ex = radar._examples(conn, [int(r["id"]) for r in rows[:12]], niche)
            scan.persist_covers(conn, sorted((v for vs in ex.values() for v in vs), key=lambda v: -int(v.get("views") or 0)))
        else:
            scan.persist_covers(conn, niche_videos(conn, niche))
    except Exception as err:
        conn.rollback()
        print(f"[brand-insight] cover persist skipped: {type(err).__name__}: {err}")
    matches = scan.creator_matches(niche, conn=conn, redis_client=redis_client) if kind == "niche_creators" else None
    return build(kind, conn, niche, brand_name=brand_name, matches=matches, scan_id=scan_id, note=note)


def searching(run_id: int, niche: str, kind: str, items: int = 0, ingesting: bool = False) -> Dict[str, Any]:
    label = niche.replace("_", " ")
    if ingesting:
        step, line = 3, "Grouping brands, hooks and creators…"
    elif items:
        step, line = 2, f"Found {items} {label} creator posts. Reading them…"
    else:
        step, line = 1, f"Searching TikTok for gifted and PR {label} posts…"
    return {"status": "searching", "scan_id": run_id, "kind": kind, "niche": niche, "step": step, "line": line,
            "items": items, "insight": True}


def start(conn, kind: str, niche: str, *, brand_name: str = "", redis_client=None,
          can_start: bool = True) -> Dict[str, Any]:
    if kind not in KINDS or not niche:
        return {"status": "invalid"}
    radar.finalize_stale(conn)
    count = len(niche_videos(conn, niche))
    fresh_at = radar.last_topup(conn, niche)
    fresh = bool(fresh_at) and _now() - fresh_at < FRESH_FOR
    if (count >= MIN_VIDEOS and fresh) or count >= MIN_VIDEOS * 4:
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client)
    running = radar._inflight(conn, niche)
    if running:
        return searching(running, niche, kind)
    plan = radar.stale_queries(conn, query_plan(niche))
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if not plan or not can_start or radar.spend_today(conn) + est > scan.DAILY_CAP_USD:
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client,
                        note="" if not plan else "limit")
    try:
        apify_id = source.start_run([p["query"] for p in plan if p["type"] == "hashtag"],
                                    [p["query"] for p in plan if p["type"] == "search"], PER_QUERY)
    except Exception as err:
        print(f"[brand-insight] start failed: {type(err).__name__}: {err}")
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client, note="source_down")
    run_id = radar._record(conn, {"niche": niche, "creator_id": None}, status="running",
                           served_from="partial_topup" if count else "full_apify", run_ids=[apify_id],
                           queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                           params={"kind": "brand_insight", "insight": kind, "est_cost": est, "per_query": PER_QUERY,
                                   "round": 1, "brand": brand_name[:80]})
    print(f"[brand-insight] run {run_id} niche={niche} kind={kind} queries={len(plan)} est=${est}")
    return {**searching(run_id, niche, kind), "started": True}


def poll(conn, run_id: int, kind: str, *, brand_name: str = "", redis_client=None) -> Dict[str, Any]:
    run = radar._load_run(conn, run_id)
    # Insight runs, or a creator-side radar run of the same niche that was already in flight. Never brand scans.
    if not run or not run.get("niche") or str(run["niche"]).startswith("scan:"):
        return {"status": "not_found"}
    niche = run["niche"]
    kind = kind if kind in KINDS else (run.get("params") or {}).get("insight") or "gifting_brands"
    note = ""
    if run["status"] in ("running", "ingesting"):
        age = _now() - radar._round_started(run)
        if run["status"] == "ingesting":
            if age < radar.RUN_TIMEOUT + timedelta(minutes=2):
                return searching(run_id, niche, kind, ingesting=True)
        else:
            try:
                step = radar._finish(conn, run)
            except Exception as err:
                conn.rollback()
                print(f"[brand-insight] poll failed run={run_id}: {type(err).__name__}: {err}")
                return searching(run_id, niche, kind)
            if not step.get("done"):
                return searching(run_id, niche, kind, items=step.get("items") or 0, ingesting=bool(step.get("ingesting")))
            if step.get("failed"):
                note = "source_down"
    elif run["status"] == "failed":
        note = "source_down"
    return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client, scan_id=run_id, note=note)
