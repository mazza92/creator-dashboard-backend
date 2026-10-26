"""Deep answers for the brand workspace: plan TikTok searches for a question, then write a real answer.

  plan()        One question -> 10-14 TikTok searches that each cover a different angle of it.
  pool()        The videos those searches returned (from apify_query_cache), with their creators.
  synthesize()  The model reads the pool and writes the answer: summary, sections of hooks / formats /
                creators, a shortlist and follow-up questions. Only video ids and handles from the pool are kept.

The model is Gemini (same key the radar uses). Without it, plan() falls back to the niche's query bank and
synthesize() returns None, so callers show the plain data view instead.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

PLAN_QUERIES = 12
POOL_VIDEOS = 160
POOL_WINDOW = timedelta(days=180)
SECTION_KINDS = ("hooks", "formats", "creators", "brands", "takeaways")

Writer = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _gemini(prompt: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    import requests

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not configured")
    model = os.getenv("BRAND_INSIGHT_MODEL", "gemini-2.5-flash")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": opts.get("temperature", 0.5), "responseMimeType": "application/json",
                                   "thinkingConfig": {"thinkingBudget": opts.get("thinking", 0)}}},
        timeout=opts.get("timeout", 25),
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        from services.gemini_usage import record_gemini_usage

        record_gemini_usage(opts.get("source", "brand_insight"), model, data)
    except Exception:
        pass
    return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])


# ----- plan ----------------------------------------------------------------------------------------

_PLAN_PROMPT = """You plan TikTok searches to answer a brand's question about creator content.

Brand: {brand}
Brand niche: {niche}
Question: {question}

Write {n} TikTok search queries whose results, read together, answer the question well.
- Each covers a different angle, sub-niche, product type, problem or video format (for skincare: tretinoin
  update, sunscreen review, drugstore routine, acne journey, skincare empties, korean skincare haul), so the
  results barely overlap.
- Write them the way creators caption videos: 2 to 6 words, lowercase, no # symbols, no brand names
  (unless the question is about a specific brand).
- If the question is about gifted or sponsored content, a few may add "pr package", "gifted" or "sent me".
- If it names a country or city, work it into a few queries naturally.

Return JSON: {{"queries": ["..."], "label": "3 to 6 word description of what you're searching for"}}"""


def _clean(raw: Any) -> str:
    q = " ".join(str(raw or "").lower().replace("#", " ").split())
    return q if 1 <= len(q.split()) <= 8 and len(q) <= 70 else ""


def plan(question: str, niche: str, brand: str = "", n: int = PLAN_QUERIES, write: Optional[Writer] = None,
         fallback: Optional[List[str]] = None) -> Dict[str, Any]:
    """{"queries": [...], "label": str, "ai": bool}."""
    write = write or _gemini
    try:
        reply = write(_PLAN_PROMPT.format(brand=brand or "not given", niche=niche.replace("_", " "),
                                          question=question, n=n), {"temperature": 0.7, "source": "brand_insight_plan"})
        queries = list(dict.fromkeys(q for q in (_clean(x) for x in (reply or {}).get("queries") or []) if q))[:n]
        if len(queries) >= 4:
            return {"queries": queries, "label": str(reply.get("label") or "")[:60], "ai": True}
    except Exception as err:
        print(f"[insight-agent] plan skipped: {type(err).__name__}: {err}")
    return {"queries": list(dict.fromkeys(fallback or []))[:n], "label": "", "ai": False}


# ----- pool ----------------------------------------------------------------------------------------

def pool(conn, keys: List[str], niche_rows: List[Dict[str, Any]] = (), now: Optional[datetime] = None,
         limit: int = POOL_VIDEOS) -> Dict[str, Any]:
    """Videos returned by these searches (plus any niche rows passed in), newest window, most viewed first,
    and the creators behind them."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ids: List[str] = []
    if keys:
        cur.execute("SELECT video_ids FROM apify_query_cache WHERE cache_key = ANY(%s)", (list(keys),))
        for r in cur.fetchall():
            ids.extend(r["video_ids"] or [])
    ids = list(dict.fromkeys(ids + [str(v["tiktok_id"]) for v in niche_rows or []]))
    videos: List[Dict[str, Any]] = []
    if ids:
        cur.execute("""
            SELECT v.tiktok_id, v.url, v.caption, v.hashtags, v.mentions, v.views, v.likes, v.created_at, v.cover_url,
                   v.is_ad, v.is_sponsored, v.creator_handle, v.creator_followers, v.creator_region
            FROM tiktok_videos v WHERE v.tiktok_id = ANY(%s) AND (v.created_at IS NULL OR v.created_at > %s)
        """, (ids, now - POOL_WINDOW))
        videos = sorted((dict(r) for r in cur.fetchall()), key=lambda v: -int(v.get("views") or 0))[:limit]
    handles = list(dict.fromkeys(v["creator_handle"] for v in videos if v.get("creator_handle")))
    creators: Dict[str, Dict[str, Any]] = {}
    if handles:
        cur.execute("SELECT handle, followers, region, bio FROM tiktok_creators WHERE handle = ANY(%s)", (handles,))
        creators = {r["handle"]: dict(r) for r in cur.fetchall()}
    for v in videos:
        c = creators.setdefault(v["creator_handle"], {"handle": v["creator_handle"], "followers": v.get("creator_followers"),
                                                       "region": v.get("creator_region"), "bio": ""})
        c["videos"] = c.get("videos", 0) + 1
        c["views"] = c.get("views", 0) + int(v.get("views") or 0)
    return {"videos": videos, "creators": creators}


# ----- synthesize ----------------------------------------------------------------------------------

_ANSWER_PROMPT = """You are a sharp creator-marketing strategist. A brand asked a question; answer it from the
TikTok videos below (real, recent posts we just pulled). Be specific and qualitative, the way a great analyst
would: name the patterns, say why they work, point to the exact videos and creators.

Brand: {brand}
Niche: {niche}
Question: {question}

VIDEOS (id | creator | followers | region | views | likes | age | flags | caption)
{videos}

CREATORS (handle | followers | region | videos here | total views | bio)
{creators}

Write the answer as JSON:
{{
  "headline": "short answer title, under 9 words",
  "summary": "2 to 3 sentences answering directly. Use **bold** for the key numbers or names.",
  "sections": [
    {{
      "kind": "hooks | formats | creators | brands | takeaways",
      "title": "section heading",
      "intro": "one sentence",
      "items": [
        {{
          "title": "for hooks: the opening line pattern in quotes; for creators: their @handle; otherwise a short name",
          "text": "1 to 2 sentences: what it is and why it works / why this creator fits",
          "tag": "short label: a location the creator states, a format, or a number like '6 videos'",
          "handle": "creator handle without @ when the item is about one creator, else empty",
          "videos": ["ids of 1 to 3 videos from the list that show it"]
        }}
      ]
    }}
  ],
  "shortlist": "2 to 4 sentences: who to start with and what to brief them on. Use **bold** for handles.",
  "followups": [{{"label": "emoji + 3 to 6 words", "prompt": "a full follow-up question the brand could ask next"}}]
}}
Rules:
- 2 to 4 sections that fit the question, 3 to 8 items each. Lead with what the question asks for.
- A creators section lists creators from CREATORS only, under 100K followers unless the question says otherwise.
- Only use video ids and handles that appear above. Never invent numbers; only cite counts you can see.
- Plain words, short sentences, no em dashes. 3 or 4 followups.
- If the videos don't answer the question well, say so in the summary and give the closest useful answer."""


def _age_days(when: Any, now: datetime) -> str:
    if not isinstance(when, datetime):
        return "?"
    return f"{max(0, (now - when).days)}d"


def _line(s: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _video_lines(videos: List[Dict[str, Any]], now: datetime) -> str:
    out = []
    for v in videos:
        flags = ",".join(f for f, on in (("ad", v.get("is_ad")), ("sponsored", v.get("is_sponsored"))) if on) or "-"
        out.append(" | ".join([str(v["tiktok_id"]), "@" + str(v.get("creator_handle") or ""),
                               str(v.get("creator_followers") or 0), str(v.get("creator_region") or "-"),
                               str(v.get("views") or 0), str(v.get("likes") or 0), _age_days(v.get("created_at"), now),
                               flags, _line(v.get("caption"), 220)]))
    return "\n".join(out)


def _creator_lines(creators: Dict[str, Dict[str, Any]], limit: int = 70) -> str:
    rows = sorted(creators.values(), key=lambda c: -int(c.get("views") or 0))[:limit]
    return "\n".join(" | ".join(["@" + c["handle"], str(c.get("followers") or 0), str(c.get("region") or "-"),
                                 str(c.get("videos") or 0), str(c.get("views") or 0), _line(c.get("bio"), 160)])
                     for c in rows)


def _text(s: Any, n: int) -> str:
    return _line(s, n).replace("—", ",").replace("–", "-")


def clean_answer(raw: Dict[str, Any], video_ids: set, handles: set) -> Optional[Dict[str, Any]]:
    """Keep only well-formed parts, and only videos / handles that exist in the pool."""
    if not isinstance(raw, dict):
        return None
    sections = []
    for s in raw.get("sections") or []:
        if not isinstance(s, dict):
            continue
        items = []
        for it in s.get("items") or []:
            if not isinstance(it, dict) or not _text(it.get("title"), 160):
                continue
            handle = _line(it.get("handle"), 60).lstrip("@").lower()
            if not handle:
                m = re.match(r"^@([\w.]+)$", _line(it.get("title"), 60))
                handle = m.group(1).lower() if m else ""
            items.append({"title": _text(it.get("title"), 160), "text": _text(it.get("text"), 400),
                          "tag": _text(it.get("tag"), 40), "handle": handle if handle in handles else "",
                          "videos": [str(x) for x in (it.get("videos") or []) if str(x) in video_ids][:3]})
        if items:
            kind = str(s.get("kind") or "").strip().lower()
            sections.append({"kind": kind if kind in SECTION_KINDS else "takeaways", "title": _text(s.get("title"), 80),
                             "intro": _text(s.get("intro"), 240), "items": items[:8]})
    summary = _text(raw.get("summary"), 700)
    if not sections or not summary:
        return None
    followups = [{"label": _text(f.get("label"), 60), "prompt": _text(f.get("prompt"), 300)}
                 for f in raw.get("followups") or [] if isinstance(f, dict) and f.get("prompt")][:4]
    return {"headline": _text(raw.get("headline"), 90), "summary": summary, "sections": sections[:4],
            "shortlist": _text(raw.get("shortlist"), 700), "followups": followups}


def synthesize(question: str, niche: str, brand: str, data: Dict[str, Any], write: Optional[Writer] = None,
               now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    videos = data.get("videos") or []
    if len(videos) < 3:
        return None
    now = now or datetime.now(timezone.utc)
    write = write or _gemini
    prompt = _ANSWER_PROMPT.format(brand=brand or "not given", niche=niche.replace("_", " "), question=question,
                                   videos=_video_lines(videos, now), creators=_creator_lines(data.get("creators") or {}))
    try:
        raw = write(prompt, {"temperature": 0.4, "thinking": 1024, "timeout": 50, "source": "brand_insight_answer"})
    except Exception as err:
        print(f"[insight-agent] answer skipped: {type(err).__name__}: {err}")
        return None
    return clean_answer(raw, {str(v["tiktok_id"]) for v in videos}, set((data.get("creators") or {}).keys()))
