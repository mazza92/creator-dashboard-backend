"""Brand Scan: the public brand-side tool. Any brand in, the TikTok creators, hooks and videos behind it out.

Runs on the Gifting Radar's store: tiktok_videos first, then one Apify run of the brand's hashtags and
name when the store is thin. A scan is a discovery_runs row with niche 'scan:<brand_key>', so the
radar's claim/ingest/cost code, daily spend cap and stale-run finalizer all apply unchanged.
"""

from __future__ import annotations

import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import gifting_radar as radar
from services import radar_source as source
from services.radar_extract import (
    GENERIC,
    RETAILERS,
    brand_key,
    display_name,
    key_variants,
    radar_niche,
    signal_types,
    video_niches,
)

WINDOW = timedelta(days=180)
FRESH_FOR = timedelta(hours=72)
READY_MIN = 8
PLENTY = 24
PER_QUERY = 20
TAG_SUFFIXES = ("partner", "partners", "ambassador", "creator", "ugc", "pr", "gifted")
SMALL_FOLLOWERS = 50_000
FREE = {"top_videos": 24, "hooks": 8, "creators": 6, "matches": 6}  # only the creators list is gated
FULL = {"top_videos": 24, "hooks": 8, "creators": 60, "matches": 6}
DAILY_CAP_USD = float(os.getenv("BRAND_SCAN_DAILY_CAP_USD") or "10")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----- target -------------------------------------------------------------------------

def resolve(conn, raw: Any, directory: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """'Glow Recipe' / '@glowrecipe' / 'glowrecipe.com' -> {key, name, handles, logo_url, category, brand_id}."""
    text = " ".join(str(raw or "").split())[:80]
    if not text:
        return None
    looks_domain = "." in text and " " not in text and not text.startswith("@")
    key = radar._domain_key(text) if looks_domain else brand_key(text)
    if len(key) < 3 or key in GENERIC:
        return None
    directory = directory if directory is not None else radar.load_directory(conn)
    canon, row = radar.match_directory(key, directory)
    handle = brand_key(row.get("tiktok_handle")) if row.get("tiktok_handle") else ""
    if looks_domain:
        name = canon.title()
    else:
        name = display_name(text.lstrip("@"))
        if name == name.lower():
            name = name.title() if " " in name else name
    name = row.get("name") or name
    handles = list(dict.fromkeys([k for k in key_variants(canon) + key_variants(key) + [handle] if k]))
    return {
        "key": canon,
        "name": name,
        "handles": handles,
        "logo_url": row.get("logo_url") or None,
        "category": row.get("category") or "",
        "brand_id": row.get("id"),
    }


def scan_niche(target: Dict[str, Any]) -> str:
    return f"scan:{target['key']}"


def hashtags_for(target: Dict[str, Any]) -> List[str]:
    key = target["key"]
    return [key] + [key + s for s in TAG_SUFFIXES]


# ----- matching -------------------------------------------------------------------------

def _word_re(name: str):
    return re.compile(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])")


def is_match(video: Dict[str, Any], target: Dict[str, Any]) -> bool:
    """A post about the brand, by someone other than the brand or a retailer."""
    creator = str(video.get("creator_handle") or "").lower()
    ckey = brand_key(creator)
    if creator in target["handles"] or ckey in target["handles"] or ckey in RETAILERS:
        return False
    if set(video.get("mentions") or []) & set(target["handles"]):
        return True
    if set(video.get("hashtags") or []) & set(hashtags_for(target)):
        return True
    name = target["name"]
    return len(name) >= 4 and bool(_word_re(name).search(str(video.get("caption") or "").lower()))


def matched_videos(conn, target: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or _now()
    like = f"%{target['name']}%" if len(target["name"]) >= 4 else None
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT tiktok_id, url, caption, hashtags, mentions, views, likes, created_at, cover_url,
               is_ad, is_sponsored, creator_handle, creator_followers
        FROM tiktok_videos
        WHERE created_at > %s
          AND (mentions && %s OR hashtags && %s OR (%s::text IS NOT NULL AND caption ILIKE %s))
        ORDER BY views DESC NULLS LAST
        LIMIT 500
    """, (now - WINDOW, target["handles"], hashtags_for(target), like, like))
    return [dict(r) for r in cur.fetchall() if is_match(dict(r), target)]


# ----- report pieces (pure) ----------------------------------------------------------------

_STRIP = re.compile(r"https?://\S+|[#@][\w.]+")
_KEEP = re.compile(r"[^a-z0-9\s'’?!,.]")


def hook_text(caption: Any) -> str:
    """First line of a caption without hashtags, mentions, links or emoji, lowercased."""
    first = str(caption or "").strip().split("\n")[0]
    text = _KEEP.sub(" ", _STRIP.sub(" ", first.lower()))
    return " ".join(text.replace("’", "'").split()).strip(" ,.")


def hook_key(caption: Any) -> str:
    words = re.sub(r"[?!,.]", "", hook_text(caption)).split()
    return " ".join(words[:6]) if len(words) >= 3 else ""


def short_hook(caption: Any, words: int = 10) -> str:
    parts = hook_text(caption).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def post_type(video: Dict[str, Any], key: str) -> str:
    types = signal_types(video, key)
    return types[0] if types else "organic"


def _k(n: Any) -> str:
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(int(n))


def _age(when: Optional[datetime], now: datetime) -> str:
    if not isinstance(when, datetime):
        return ""
    days = max(0, (now - when).days)
    return "today" if days == 0 else f"{days}d ago"


def video_card(v: Dict[str, Any], key: str, now: datetime, thumb=None) -> Dict[str, Any]:
    thumb = thumb or radar._thumb
    return {
        "id": v["tiktok_id"],
        "url": v["url"],
        "thumb": thumb(v.get("cover_url"), v["url"]) or None,
        "views": int(v.get("views") or 0),
        "views_label": _k(v.get("views")),
        "likes": int(v.get("likes") or 0),
        "creator": f"@{v.get('creator_handle')}",
        "followers": int(v.get("creator_followers") or 0),
        "followers_label": _k(v.get("creator_followers")),
        "age": _age(v.get("created_at"), now),
        "hook": short_hook(v.get("caption")),
        "type": post_type(v, key),
    }


def group_hooks(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Captions that open the same way, used by 2+ creators: a brand's creator script."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in videos:
        key = hook_key(v.get("caption"))
        if key:
            groups[key].append(v)
    out = []
    for key, vids in groups.items():
        creators = {v.get("creator_handle") for v in vids}
        if len(creators) < 2:
            continue
        vids = sorted(vids, key=lambda v: -int(v.get("views") or 0))
        out.append({"key": key, "videos": vids, "creators": len(creators),
                    "views": sum(int(v.get("views") or 0) for v in vids)})
    return sorted(out, key=lambda g: (-g["creators"], -g["views"]))


def creator_rows(videos: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    by: Dict[str, Dict[str, Any]] = {}
    for v in videos:
        h = v.get("creator_handle")
        row = by.setdefault(h, {"handle": f"@{h}", "url": f"https://www.tiktok.com/@{h}",
                                "followers": int(v.get("creator_followers") or 0), "videos": 0,
                                "best_views": 0, "types": []})
        row["videos"] += 1
        row["best_views"] = max(row["best_views"], int(v.get("views") or 0))
        t = post_type(v, key)
        if t not in row["types"]:
            row["types"].append(t)
    rows = sorted(by.values(), key=lambda r: (-r["best_views"], -r["videos"]))
    for r in rows:
        r["followers_label"] = _k(r["followers"])
        r["best_views_label"] = _k(r["best_views"])
    return rows


def stats(videos: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    followers = [int(v.get("creator_followers") or 0) for v in videos if v.get("creator_followers")]
    creators = {v.get("creator_handle") for v in videos}
    per_creator = {}
    for v in videos:
        per_creator[v.get("creator_handle")] = int(v.get("creator_followers") or 0)
    types = Counter(post_type(v, key) for v in videos)
    n = max(1, len(videos))
    return {
        "videos": len(videos),
        "creators": len(creators),
        "views": sum(int(v.get("views") or 0) for v in videos),
        "median_followers": int(statistics.median(followers)) if followers else 0,
        "small_creator_share": round(sum(1 for f in per_creator.values() if 0 < f <= SMALL_FOLLOWERS)
                                     / max(1, len(per_creator)), 2),
        "mix": {t: round(types.get(t, 0) / n, 2) for t in ("gifted", "paid", "affiliate", "organic")},
    }


def guess_niche(target: Dict[str, Any], videos: List[Dict[str, Any]]) -> str:
    niche = radar_niche(target.get("category"))
    if niche:
        return niche
    counts = Counter(n for v in videos for n in video_niches(v) if n != "general")
    return counts.most_common(1)[0][0] if counts else ""


def summary(name: str, st: Dict[str, Any], hooks: List[Dict[str, Any]]) -> str:
    if not st["videos"]:
        return (f"No public TikTok creator posts about **{name}** in the last 6 months yet. "
                "That usually means the brand isn't running a creator program, which is an opening for you.")
    creators = st["creators"]
    lines = [f"**{name}** has **{creators} creator{'s' if creators != 1 else ''}** posting about it on TikTok "
             f"in the last 6 months, across **{st['videos']} videos** and **{_k(st['views'])} views**."]
    if st["median_followers"]:
        size = "small" if st["median_followers"] <= SMALL_FOLLOWERS else "mid-size"
        lines.append(f"Most are {size} creators (median **{_k(st['median_followers'])} followers**).")
    mix = {k: v for k, v in st["mix"].items() if k != "organic" and v >= 0.15}
    if mix:
        top = max(mix, key=mix.get)
        label = {"gifted": "gifted", "paid": "paid partnerships", "affiliate": "TikTok Shop or affiliate"}[top]
        lines.append(f"**{round(mix[top] * 100)}%** of posts are {label}.")
    if hooks:
        h = hooks[0]
        lines.append(f"The top repeated hook, “{short_hook(h['videos'][0].get('caption'), 8)}”, ran on "
                     f"**{len(h['videos'])} videos** from {h['creators']} creators with **{_k(h['views'])} views**.")
    return " ".join(lines)


def build_report(target: Dict[str, Any], videos: List[Dict[str, Any]], *, matches: Optional[Dict[str, Any]],
                 locked: bool, now: Optional[datetime] = None, thumb=None, scan_id: Optional[int] = None,
                 note: str = "") -> Dict[str, Any]:
    now = now or _now()
    key = target["key"]
    videos = sorted(videos, key=lambda v: -int(v.get("views") or 0))
    st = stats(videos, key)
    hooks = group_hooks(videos)
    creators = creator_rows(videos, key)
    caps = FREE if locked else FULL
    hook_cards = [{
        "hook": short_hook(g["videos"][0].get("caption")),
        "videos": len(g["videos"]), "creators": g["creators"], "views": g["views"], "views_label": _k(g["views"]),
        "examples": [video_card(v, key, now, thumb) for v in g["videos"][:3]],
    } for g in hooks[:caps["hooks"]]]
    report = {
        "status": "ready" if videos else "empty",
        "scan_id": scan_id,
        "brand": {"key": key, "name": target["name"], "logo_url": target.get("logo_url"),
                  "niche": (matches or {}).get("niche") or ""},
        "summary": summary(target["name"], st, hooks),
        "stats": {**st, "views_label": _k(st["views"]), "median_followers_label": _k(st["median_followers"])},
        "top_videos": [video_card(v, key, now, thumb) for v in videos[:caps["top_videos"]]],
        "hooks": hook_cards,
        "creators": creators[:caps["creators"]],
        "matches": None,
        "locked": locked,
        "unlock": {"creators": max(0, min(len(creators), FULL["creators"]) - FREE["creators"]) if locked else 0},
    }
    if matches:
        report["matches"] = {**matches, "creators": (matches.get("creators") or [])[:caps["matches"]]}
    if note:
        report["note"] = note
    return report


# ----- creator matches -------------------------------------------------------------------

def signup_url(niche: str = "") -> str:
    """'Try your first campaign free': the brand signup (/for-brands), which lands in the brand workspace."""
    from urllib.parse import urlencode

    from services.creator_directory_search import frontend_base

    query = {"ref": "brand_scan", "utm_source": "brand_scan", "utm_medium": "tool", "utm_campaign": "brand_scan"}
    if niche:
        query["niche"] = niche
    return f"{frontend_base()}/for-brands?{urlencode(query)}"


def creator_matches(niche: str, conn=None, redis_client=None) -> Dict[str, Any]:
    from services.creator_directory_search import search_creators

    query = niche or "lifestyle"
    try:
        found = search_creators({"niche": query, "platform": "tiktok", "limit": FULL["matches"]},
                                redis_client=redis_client, conn=conn, landing=True)
    except Exception as err:
        print(f"[brand-scan] creator matches skipped: {type(err).__name__}: {err}")
        if conn is not None:
            conn.rollback()  # keep the connection usable for the lead insert that follows
        found = {}
    return {
        "niche": niche,
        "total": int(found.get("total_creators_found") or 0),
        "creators": found.get("creators") or [],
        "cta_url": signup_url(niche),
    }


# ----- runs -----------------------------------------------------------------------------

def _last_done(conn, niche: str) -> Optional[datetime]:
    cur = conn.cursor()
    cur.execute("""SELECT MAX(finished_at) FROM discovery_runs
                   WHERE niche = %s AND status = 'done' AND served_from <> 'database'""", (niche,))
    row = cur.fetchone()
    return row[0] if row else None


def query_plan(target: Dict[str, Any]) -> List[Dict[str, Any]]:
    key, name = target["key"], target["name"]
    tags = [key, f"{key}partner", f"{key}ambassador"]
    searches = list(dict.fromkeys([name.lower(), f"{name.lower()} review"]))
    plan = [{"key": f"hashtag:{t}", "type": "hashtag", "query": t, "ttl": radar.HASHTAG_TTL} for t in tags]
    plan += [{"key": f"search:{q}", "type": "search", "query": q, "ttl": radar.QUERY_TTL} for q in searches]
    return plan


def _respond(conn, target, videos, *, scan_id, redis_client=None, locked=True, note=""):
    """locked: the viewer isn't an approved brand, so the competitor's creators list stops at FREE["creators"]."""
    niche = guess_niche(target, videos)
    matches = creator_matches(niche, conn=conn, redis_client=redis_client)
    return build_report(target, videos, matches=matches, locked=locked, scan_id=scan_id, note=note)


def searching(scan_id: int, target: Dict[str, Any], items: int = 0, ingesting: bool = False) -> Dict[str, Any]:
    name = target["name"]
    if ingesting:
        step, line = 3, "Grouping creators and hooks, matching Newcollab creators…"
    elif items:
        step, line = 2, f"Found {items} posts about {name}. Pulling the best videos…"
    else:
        step, line = 1, f"Searching TikTok for creator posts about {name}…"
    return {"status": "searching", "scan_id": scan_id, "brand": {"key": target["key"], "name": name},
            "step": step, "line": line, "items": items}


def start(conn, raw: Any, *, can_start: bool = True, redis_client=None, ref: str = "",
          locked: bool = True) -> Dict[str, Any]:
    """Report from the store, or kick off one Apify run and return {"status": "searching"}.

    The returned "started" flag tells the caller an Apify run was paid for (for per-IP limits)."""
    target = resolve(conn, raw)
    if not target:
        return {"status": "invalid"}
    niche = scan_niche(target)
    radar.finalize_stale(conn)
    videos = matched_videos(conn, target)
    fresh_at = _last_done(conn, niche)
    fresh = bool(fresh_at) and _now() - fresh_at < FRESH_FOR
    ctx = {"niche": niche, "creator_id": None}
    params = {"kind": "brand_scan", "brand": target["name"], "raw": str(raw)[:80], "ref": ref[:40]}

    def from_db(note=""):
        run_id = radar._record(conn, ctx, status="done", served_from="database", params=params,
                               brands=len(videos))
        return _respond(conn, target, videos, scan_id=run_id, redis_client=redis_client, note=note, locked=locked)

    if (len(videos) >= READY_MIN and fresh) or len(videos) >= PLENTY:
        return from_db()
    running = radar._inflight(conn, niche)
    if running:
        return searching(running, target)
    plan = radar.stale_queries(conn, query_plan(target))
    if not plan:
        return from_db()
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if not can_start or radar.spend_today(conn) + est > DAILY_CAP_USD:
        return from_db(note="limit")
    try:
        apify_id = source.start_run([p["query"] for p in plan if p["type"] == "hashtag"],
                                    [p["query"] for p in plan if p["type"] == "search"], PER_QUERY)
    except Exception as err:  # SourceUnavailable, or a network error/timeout talking to Apify
        print(f"[brand-scan] start failed: {type(err).__name__}: {err}")
        return from_db(note="source_down")
    run_id = radar._record(conn, ctx, status="running", served_from="partial_topup" if videos else "full_apify",
                           run_ids=[apify_id], queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                           params={**params, "est_cost": est, "per_query": PER_QUERY, "round": 1})
    print(f"[brand-scan] run {run_id} brand={target['key']} queries={len(plan)} est=${est}")
    return {**searching(run_id, target), "started": True}


def poll(conn, scan_id: int, *, redis_client=None, locked: bool = True) -> Dict[str, Any]:
    run = radar._load_run(conn, scan_id)
    if not run or (run.get("params") or {}).get("kind") != "brand_scan":
        return {"status": "not_found"}
    target = resolve(conn, (run.get("params") or {}).get("brand") or run["niche"].split(":", 1)[-1])
    if not target:
        return {"status": "not_found"}
    note = ""
    if run["status"] in ("running", "ingesting"):
        age = _now() - radar._round_started(run)
        if run["status"] == "ingesting":
            if age < radar.RUN_TIMEOUT + timedelta(minutes=2):
                return searching(scan_id, target, ingesting=True)
        else:
            try:
                step = radar._finish(conn, run)
            except Exception as err:  # Apify unreachable: keep polling, the stale-run finalizer catches up
                conn.rollback()
                print(f"[brand-scan] poll failed run={scan_id}: {type(err).__name__}: {err}")
                return searching(scan_id, target)
            if not step.get("done"):
                return searching(scan_id, target, items=step.get("items") or 0,
                                 ingesting=bool(step.get("ingesting")))
            if step.get("failed"):
                note = "source_down"
    elif run["status"] == "failed":
        note = "source_down"
    return _respond(conn, target, matched_videos(conn, target), scan_id=scan_id, redis_client=redis_client,
                    note=note, locked=locked)


def full_report(conn, raw: Any, *, redis_client=None) -> Dict[str, Any]:
    target = resolve(conn, raw)
    if not target:
        return {"status": "invalid"}
    return _respond(conn, target, matched_videos(conn, target), scan_id=None, redis_client=redis_client,
                    locked=False)


# ----- leads ------------------------------------------------------------------------------

def clean_email(raw: Any) -> str:
    email = str(raw or "").strip().lower()[:254]
    return email if EMAIL_RE.match(email) else ""


def ensure_leads_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_scan_leads (
          id SERIAL PRIMARY KEY,
          email TEXT NOT NULL,
          scanned_brand TEXT NOT NULL,
          website TEXT,
          ref TEXT,
          ip TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS brand_scan_leads_email_idx ON brand_scan_leads (lower(email))")
    cur.execute("ALTER TABLE brand_scan_leads ENABLE ROW LEVEL SECURITY")
    conn.commit()


def save_lead(conn, email: str, brand: str, website: str = "", ref: str = "", ip: str = "") -> int:
    ensure_leads_table(conn)
    cur = conn.cursor()
    cur.execute("""INSERT INTO brand_scan_leads (email, scanned_brand, website, ref, ip)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (email, str(brand)[:80], str(website or "")[:200] or None, str(ref or "")[:40] or None,
                 str(ip or "")[:64] or None))
    lead_id = int(cur.fetchone()[0])
    conn.commit()
    return lead_id


def recently_sent(conn, email: str, brand: str, within: timedelta = timedelta(hours=24)) -> bool:
    """One report email per address and brand per day, however often they unlock it."""
    ensure_leads_table(conn)
    cur = conn.cursor()
    cur.execute("""SELECT 1 FROM brand_scan_leads WHERE lower(email) = %s AND lower(scanned_brand) = lower(%s)
                     AND created_at > NOW() - %s LIMIT 1""", (email, str(brand)[:80], within))
    return cur.fetchone() is not None


SCAN_PAGE = "https://newcollab.co/brands/scan"


def _md_html(text: str) -> str:
    from html import escape

    parts = escape(str(text or "")).split("**")
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))


def report_email(report: Dict[str, Any]) -> Dict[str, str]:
    """Subject + HTML for the full report sent after an unlock. Links only, no images, so it lands in the inbox."""
    from html import escape
    from urllib.parse import urlencode

    brand = (report.get("brand") or {}).get("name") or "this brand"
    st = report.get("stats") or {}
    scan_link = f"{SCAN_PAGE}?{urlencode({'b': brand, 'ref': 'email'})}"
    matches = report.get("matches") or {}
    cta = matches.get("cta_url") or "https://newcollab.co/brands/pr-packages"
    cta = cta + ("&" if "?" in cta else "?") + "utm_medium=email"
    p = 'style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#2b2d33"'
    h = 'style="margin:26px 0 10px;font-size:15px;color:#111214"'
    li = 'style="margin:0 0 8px;font-size:14px;line-height:1.5;color:#2b2d33"'
    rows = [f"<p {p}>{_md_html(report.get('summary'))}</p>"]
    if st.get("videos"):
        rows.append(f"<p {p}><b>{st.get('creators', 0)}</b> creators · <b>{st.get('videos', 0)}</b> videos · "
                    f"<b>{escape(str(st.get('views_label') or ''))}</b> views · median "
                    f"<b>{escape(str(st.get('median_followers_label') or ''))}</b> followers</p>")
    videos = report.get("top_videos") or []
    if videos:
        rows.append(f"<h3 {h}>Top videos</h3><ul style=\"padding-left:18px;margin:0\">")
        for v in videos[:5]:
            rows.append(f"<li {li}><a href=\"{escape(v['url'])}\" style=\"color:#e8395f\">{escape(v.get('hook') or 'Video')}</a>"
                        f" · {escape(v.get('creator') or '')} · {escape(v.get('views_label') or '')} views</li>")
        rows.append("</ul>")
    hooks = report.get("hooks") or []
    if hooks:
        rows.append(f"<h3 {h}>Hooks that repeat</h3><ul style=\"padding-left:18px;margin:0\">")
        for hk in hooks[:3]:
            rows.append(f"<li {li}>“{escape(hk['hook'])}”: {hk['videos']} videos from {hk['creators']} creators, "
                        f"{escape(hk.get('views_label') or '')} views</li>")
        rows.append("</ul>")
    creators = report.get("creators") or []
    if creators:
        names = ", ".join(escape(c["handle"]) for c in creators[:8])
        more = len(creators) - 8
        rows.append(f"<h3 {h}>Creators posting</h3><p {p}>{names}{f' and {more} more' if more > 0 else ''}.</p>")
    total = int(matches.get("total") or 0)
    niche = f"{matches.get('niche')} " if matches.get("niche") else ""
    bridge = (f"{total} vetted {niche}creators on Newcollab fit this profile and are open to gifting your brand."
              if total else f"Get vetted {niche}creators making content like this for your brand.")
    rows.append(
        f"<div style=\"margin:28px 0 8px;padding:20px;border-radius:14px;background:#fef2f4\">"
        f"<p style=\"margin:0 0 6px;font-size:16px;font-weight:700;color:#111214\">{escape(bridge)}</p>"
        f"<p style=\"margin:0 0 16px;font-size:14px;color:#3f424a\">Pick creators, ship your product, get 5 to 10 "
        f"ad-ready videos back with 6-month ad rights. Your first campaign is free.</p>"
        f"<a href=\"{escape(cta)}\" style=\"display:inline-block;background:#e8395f;color:#fff;text-decoration:none;"
        f"font-weight:600;font-size:15px;padding:12px 18px;border-radius:10px\">Try your first campaign free</a></div>")
    rows.append(f"<p style=\"margin:18px 0 0;font-size:13px;color:#6b6f78\">See the full interactive report: "
                f"<a href=\"{escape(scan_link)}\" style=\"color:#6b6f78\">{escape(scan_link)}</a><br>"
                "Reply to this email if you want us to set up the campaign with you.</p>")
    html = ("<div style=\"max-width:600px;margin:0 auto;padding:24px;font-family:-apple-system,Segoe UI,Roboto,"
            "Helvetica,Arial,sans-serif\">" + "".join(rows) + "</div>")
    owner = f"{brand}'" if brand.lower().endswith("s") else f"{brand}'s"
    return {"subject": f"Your Brand Scan: {owner} TikTok creators", "html": html}


def send_report(email: str, report: Dict[str, Any]) -> bool:
    from services.resend_mail import send_resend_email

    msg = report_email(report)
    try:
        out = send_resend_email(email, msg["subject"], msg["html"], tags=[{"name": "type", "value": "brand_scan_report"}],
                                reply_to=(os.getenv("BRAND_SCAN_NOTIFY_EMAIL") or "team@newcollab.co").strip())
        return bool((out or {}).get("success"))
    except Exception as err:
        print(f"[brand-scan] report email skipped: {err}")
        return False


def notify_lead(email: str, brand: str, website: str = "", ref: str = "", report: Optional[Dict] = None) -> None:
    from html import escape

    from services.resend_mail import send_resend_email

    to = (os.getenv("BRAND_SCAN_NOTIFY_EMAIL") or "team@newcollab.co").strip()
    st = (report or {}).get("stats") or {}
    html = (f"<p><b>{escape(email)}</b> unlocked a Brand Scan of <b>{escape(brand)}</b>.</p>"
            f"<p>Website: {escape(website or '-')}<br>Source: {escape(ref or '-')}<br>"
            f"Scan: {st.get('creators', 0)} creators, {st.get('videos', 0)} videos</p>"
            "<p>Reply within 2 hours with their first free campaign.</p>")
    try:
        send_resend_email(to, f"New Brand Scan lead: {email} scanned {brand}", html, tags=[{"name": "type",
                                                                                          "value": "brand_scan_lead"}])
    except Exception as err:
        print(f"[brand-scan] lead notify skipped: {err}")
