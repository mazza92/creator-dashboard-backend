"""Competitor finder for scan-led outreach (pipeline v3). A competitor is only used when it is verified three ways:

  1. Proposed: Gemini (with Google Search grounding when available) names direct competitors of the prospect:
     same kind of product, same customer, similar price. Brands the radar already sees seeding creators in the
     niche are offered as hints, but the model must only keep real competitors.
  2. Measured: the competitor's Brand Scan (the same report the email links to) clears evidence gates: enough
     creators and videos, and most matches come from @mentions / hashtags rather than a loose caption word.
  3. Checked: a second model call reads the competitor's real video captions next to the prospect's product and
     confirms the videos are about that brand (not a namesake) and that it sells the same kind of product.

Anything that fails is recorded with the reason, so a human can see why a brand was skipped.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from services import brand_scan as scan

MIN_CREATORS = 5
MIN_VIDEOS = 5
MIN_STRONG_SHARE = 0.5
MIN_CONFIDENCE = 0.7
MAX_CANDIDATES = 8

Writer = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _gemini(prompt: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    """JSON from Gemini. opts.grounded adds Google Search (JSON mode can't be combined with it, so the reply is
    parsed out of plain text)."""
    import requests

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not configured")
    model = os.getenv("OUTREACH_COMPETITOR_MODEL", "gemini-2.5-flash")
    config: Dict[str, Any] = {"temperature": opts.get("temperature", 0.2),
                              "thinkingConfig": {"thinkingBudget": opts.get("thinking", 512)}}
    body: Dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    if opts.get("grounded"):
        body["tools"] = [{"google_search": {}}]
    else:
        config["responseMimeType"] = "application/json"
    resp = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                         json=body, timeout=opts.get("timeout", 40))
    resp.raise_for_status()
    data = resp.json()
    try:
        from services.gemini_usage import record_gemini_usage

        record_gemini_usage(opts.get("source", "outreach_competitors"), model, data)
    except Exception:
        pass
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return parse_json(text)


def parse_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


# ----- 1. propose ------------------------------------------------------------------------------------

_PROPOSE = """You are a DTC market analyst. List the direct competitors of this brand.

Brand: {name}
Website: {website}
Category: {category}
What they sell: {product}

A direct competitor sells the same kind of product (not just the same broad category) to the same customer, at
a similar price point, and is a real consumer brand that creators post about on TikTok. Prefer independent DTC
brands of a similar size or a bit bigger. Exclude retailers, marketplaces, the brand itself and its parent.

Brands we see seeding TikTok creators in this niche (only use one if it truly is a direct competitor):
{hints}

Return only JSON: {{"product": "what {name} sells, in under 12 words", "competitors": [{{"name": "Brand",
"website": "domain.com", "why": "under 15 words: what they sell that overlaps"}}]}}
List up to {n}, best match first. If you aren't sure what {name} sells, return an empty list."""


def propose(prospect: Dict[str, Any], hints: List[str], write: Optional[Writer] = None) -> Dict[str, Any]:
    write = write or _gemini
    prompt = _PROPOSE.format(name=prospect.get("brand_name") or "", website=prospect.get("website") or "unknown",
                             category=prospect.get("category") or "unknown",
                             product=(prospect.get("hero_product") or prospect.get("description") or "unknown")[:300],
                             hints=", ".join(hints[:20]) or "none", n=MAX_CANDIDATES)
    reply: Dict[str, Any] = {}
    for grounded in (True, False):
        try:
            reply = write(prompt, {"grounded": grounded, "source": "outreach_competitors"}) or {}
            break
        except Exception as err:
            print(f"[competitors] propose ({'grounded' if grounded else 'plain'}) failed: {type(err).__name__}: {err}")
    own = scan.brand_key(prospect.get("brand_name") or "")
    out, seen = [], set()
    for c in reply.get("competitors") or []:
        if not isinstance(c, dict):
            continue
        name = " ".join(str(c.get("name") or "").split())[:60]
        key = scan.brand_key(name)
        if len(key) < 3 or key == own or key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "website": str(c.get("website") or "")[:120], "why": str(c.get("why") or "")[:160]})
    return {"product": str(reply.get("product") or "")[:120], "candidates": out[:MAX_CANDIDATES]}


# ----- 2. measure ------------------------------------------------------------------------------------

def strong_share(videos: List[Dict[str, Any]], target: Dict[str, Any]) -> float:
    """Share of matched videos that @mention the brand or use its hashtag (not only a caption word)."""
    if not videos:
        return 0.0
    tags = set(scan.hashtags_for(target))
    handles = set(target["handles"])
    strong = sum(1 for v in videos if set(v.get("mentions") or []) & handles or set(v.get("hashtags") or []) & tags)
    return round(strong / len(videos), 2)


def measure(conn, name: str) -> Dict[str, Any]:
    """The numbers the competitor's scan report shows, from the store (no Apify)."""
    target = scan.resolve(conn, name)
    if not target:
        return {"ok": False, "reason": "not a usable brand name"}
    videos = sorted(scan.matched_videos(conn, target), key=lambda v: -int(v.get("views") or 0))
    st = scan.stats(videos, target["key"])
    hooks = scan.group_hooks(videos)
    share = strong_share(videos, target)
    top_hook = None
    if hooks and hooks[0]["creators"] >= 2:
        h = hooks[0]
        top_hook = {"text": clean_hook(h["videos"][0].get("caption")), "videos": len(h["videos"]),
                    "creators": h["creators"]}
    out = {"key": target["key"], "name": target["name"], "videos": st["videos"], "creators": st["creators"],
           "views": st["views"], "views_label": scan._k(st["views"]),
           "median_followers": st["median_followers"], "strong_share": share, "top_hook": top_hook,
           "captions": [scan.hook_text(v.get("caption"))[:160] for v in videos[:12] if v.get("caption")]}
    if st["creators"] < MIN_CREATORS or st["videos"] < MIN_VIDEOS:
        return {**out, "ok": False, "reason": f"thin data ({st['creators']} creators, {st['videos']} videos)"}
    if share < MIN_STRONG_SHARE:
        return {**out, "ok": False, "reason": f"matches are mostly caption words ({int(share * 100)}% tagged)"}
    return {**out, "ok": True}


_TRAILING = {"with", "for", "from", "by", "and", "the", "a", "an", "to", "my", "of", "on", "in", "at", "x", "ft"}


def clean_hook(caption) -> str:
    """The quoted opener: first 8 words of the caption without tags, never ending on 'with' / 'for' etc."""
    words = scan.hook_text(caption).split()[:8]
    while words and words[-1].lower().strip(",.!?") in _TRAILING:
        words.pop()
    return " ".join(words).strip(" ,.")


# ----- 3. check --------------------------------------------------------------------------------------

_CHECK = """Check a competitor pick before it goes in a sales email. Be strict: a wrong pick embarrasses us.

Prospect: {prospect} ({website}), sells: {product}
Proposed competitor: {competitor}

Recent TikTok captions from videos our system matched to "{competitor}":
{captions}

Answer:
1. same_product: does {competitor} sell the same kind of product as the prospect (not just the same broad
   category)? Use what you know about the brand and the captions.
2. videos_about_brand: are these captions actually about the brand {competitor} (not a common word, a
   different company with a similar name, or unrelated content)?
Return only JSON: {{"same_product": true/false, "videos_about_brand": true/false, "confidence": 0.0-1.0,
"reason": "under 20 words"}}"""


def check(prospect: Dict[str, Any], product: str, cand: Dict[str, Any], measured: Dict[str, Any],
          write: Optional[Writer] = None) -> Dict[str, Any]:
    write = write or _gemini
    captions = "\n".join(f"- {c}" for c in measured.get("captions") or []) or "- (none)"
    try:
        reply = write(_CHECK.format(prospect=prospect.get("brand_name"), website=prospect.get("website") or "",
                                    product=product or prospect.get("category") or "unknown",
                                    competitor=measured.get("name") or cand["name"], captions=captions),
                      {"grounded": False, "temperature": 0, "source": "outreach_competitor_check"}) or {}
    except Exception as err:
        return {"ok": False, "reason": f"check unavailable ({type(err).__name__})"}
    conf = float(reply.get("confidence") or 0)
    ok = bool(reply.get("same_product")) and bool(reply.get("videos_about_brand")) and conf >= MIN_CONFIDENCE
    return {"ok": ok, "confidence": conf, "same_product": bool(reply.get("same_product")),
            "videos_about_brand": bool(reply.get("videos_about_brand")),
            "reason": str(reply.get("reason") or "")[:200]}


def evaluate(conn, prospect: Dict[str, Any], product: str, candidates: List[Dict[str, Any]],
             write: Optional[Writer] = None) -> Dict[str, Any]:
    """Measure + check every candidate from the store. Returns {"pick": {...} | None, "tried": [...]}, the pick
    being the verified candidate with the most creators."""
    tried, passed = [], []
    for cand in candidates:
        m = measure(conn, cand["name"])
        row = {"name": cand["name"], "why": cand.get("why"), "measured": {k: v for k, v in m.items() if k != "captions"}}
        if not m.get("ok"):
            row["verdict"] = {"ok": False, "reason": m.get("reason")}
            tried.append(row)
            continue
        verdict = check(prospect, product, cand, m, write=write)
        row["verdict"] = verdict
        tried.append(row)
        if verdict["ok"]:
            passed.append({**cand, **{k: v for k, v in m.items() if k != "captions"}, "verdict": verdict})
    passed.sort(key=lambda c: (-c["creators"], -c["videos"]))
    return {"pick": passed[0] if passed else None, "tried": tried}
