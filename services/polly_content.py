"""Polly as a content co-pilot: stats from their real posts, then plans, hooks, scripts,
captions and post reviews grounded in those stats. Never invents numbers."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

TASKS = ("weekly_plan", "hooks", "script", "caption", "review", "ideas")
MIN_POSTS = 3


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def _when(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _hook(caption: str) -> str:
    """First line / sentence of a caption, without hashtags."""
    text = re.sub(r"#\w+", "", caption or "").strip()
    first = re.split(r"[\n.!?]", text, maxsplit=1)[0].strip()
    return first[:90]


def posts_from_scrape(scrape: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    posts = []
    for p in _as_list((scrape or {}).get("recent_posts")):
        if not isinstance(p, dict):
            continue
        caption = str(p.get("caption") or "")
        posts.append({
            "caption": caption,
            "hook": _hook(caption),
            "views": _int(p.get("views")),
            "likes": _int(p.get("likes")),
            "comments": _int(p.get("comments")),
            "shares": _int(p.get("shares")),
            "saves": _int(p.get("saves")),
            "url": p.get("post_url") or p.get("url") or None,
            "at": _when(p.get("timestamp") or p.get("posted_at")),
        })
    posts.sort(key=lambda x: x["at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return posts


def _interactions(p: Dict[str, Any]) -> int:
    return p["likes"] + p["comments"] + p["shares"] + p["saves"]


def _post_view(p: Dict[str, Any], typical: float, interactions: bool = True) -> Dict[str, Any]:
    out = {"hook": p["hook"] or "(no caption)", "views": p["views"], "url": p["url"]}
    if interactions:
        out.update(likes=p["likes"], comments=p["comments"])
    if p["at"]:
        out["posted"] = p["at"].date().isoformat()
    if typical and p["views"]:
        out["vs_typical"] = round(p["views"] / typical, 1)
    return out


def content_stats(scrape: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """What their recent posts actually show. Empty-ish dict (enough=False) when the scrape is thin."""
    now = now or datetime.now(timezone.utc)
    posts = posts_from_scrape(scrape)
    out: Dict[str, Any] = {"posts_analysed": len(posts), "enough": len(posts) >= MIN_POSTS}
    if not posts:
        return out
    has_interactions = any(_interactions(p) for p in posts)
    out["likes_comments_synced"] = has_interactions
    viewed = [p for p in posts if p["views"]]
    if viewed:
        views = [p["views"] for p in viewed]
        typical = float(median(views))
        out["avg_views"] = round(sum(views) / len(views))
        out["median_views"] = round(typical)
        best = max(viewed, key=lambda p: p["views"])
        worst = min(viewed, key=lambda p: p["views"])
        out["best_post"] = _post_view(best, typical, has_interactions)
        if worst is not best:
            out["weakest_post"] = _post_view(worst, typical, has_interactions)
        if has_interactions:
            rates = [_interactions(p) / p["views"] for p in viewed]
            out["engagement_per_view_pct"] = round(100 * sum(rates) / len(rates), 1)
        top = sorted(viewed, key=lambda p: p["views"], reverse=True)[:3]
        out["top_hooks"] = [p["hook"] for p in top if p["hook"]]
        if len(viewed) >= 4:
            half = len(viewed) // 2
            recent = median([p["views"] for p in viewed[:half]])
            older = median([p["views"] for p in viewed[half:]])
            if older:
                change = (recent - older) / older
                out["trend"] = ("up" if change > 0.2 else "down" if change < -0.2 else "flat")
                out["trend_pct"] = round(100 * change)
    dated = [p["at"] for p in posts if p["at"]]
    if dated:
        out["days_since_last_post"] = max(0, (now - max(dated)).days)
        if len(dated) >= 2:
            span_days = max(1.0, (max(dated) - min(dated)).total_seconds() / 86400)
            out["posts_per_week"] = round(7 * (len(dated) - 1) / span_days, 1)
    tags = Counter(t.lower() for p in posts for t in re.findall(r"#(\w+)", p["caption"]))
    if tags:
        out["top_hashtags"] = [t for t, _ in tags.most_common(6)]
    asks = sum(1 for p in posts if "?" in p["caption"])
    out["captions_with_question"] = asks
    return out


def niche_summary(scrape: Optional[Dict[str, Any]], creator: Optional[Dict[str, Any]],
                  kit: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    scrape, creator, kit = scrape or {}, creator or {}, kit or {}
    secondary = scrape.get("secondary_niches") or creator.get("creator_niches") or []
    if isinstance(secondary, str):
        secondary = [s.strip() for s in secondary.split(",") if s.strip()]
    themes = _as_list(scrape.get("content_themes"))
    return {
        "primary_niche": scrape.get("primary_niche") or creator.get("niche") or None,
        "secondary_niches": [str(s) for s in secondary[:5] if s],
        "content_themes": [str(t) for t in themes[:6] if t],
        "kit_headline": kit.get("headline") or None,
        "platform": scrape.get("primary_platform") or scrape.get("platform") or None,
    }


def stats_brief(stats: Dict[str, Any]) -> str:
    """Two short lines a reply can quote without inventing anything."""
    if not stats.get("posts_analysed"):
        return "I can't see any of your recent posts yet, so I'm not quoting numbers."
    bits = [f"Across your last {stats['posts_analysed']} posts"]
    if stats.get("median_views"):
        bits.append(f"a typical post gets {stats['median_views']:,} views")
    if stats.get("posts_per_week") is not None:
        bits.append(f"you post about {stats['posts_per_week']}x a week")
    line = ", ".join(bits) + "."
    best = stats.get("best_post")
    if best and best.get("vs_typical") and best["vs_typical"] >= 1.5:
        line += f" Your best (\"{best['hook']}\") did {best['vs_typical']}x your usual."
    return line


_SYSTEM = """You are Polly, a creator's content manager at Newcollab. You help micro-creators grow and land
brand deals. Write in a friendly, direct, manager voice. English only.

Hard rules:
- Use ONLY the numbers in STATS. Never invent views, followers, percentages or results. If STATS is thin, say
  the advice is general and don't quote numbers.
- likes_comments_synced=false means we couldn't read likes or comments, NOT that there were none. Never say a
  post got no likes or no engagement; judge on views and the caption only.
- Build on what already works for THEM (their best hooks, themes, niche). Be specific to their niche.
- Everything must be filmable by one person with a phone and things they own (or the product named).
- No hashtag walls: at most 5 hashtags, and only when asked for a caption.
- Return JSON only, in the shape given for the task."""

_SHAPES = {
    "weekly_plan": """{"focus": "one sentence: what this week's content fixes or builds on, from STATS",
 "posts": [{"day": "Monday", "format": "GRWM / POV / tutorial / review / talking head / trend",
            "idea": "what to film, concrete", "hook": "first line on screen or spoken, under 12 words",
            "why": "one short reason tied to their stats or niche"}],
 "cadence_tip": "one line about posting frequency, from STATS"}
Exactly 4 posts unless they asked for a different number.""",
    "hooks": """{"hooks": ["hook under 12 words", "..."], "pattern": "one line: what their best hooks have in common"}
6 hooks.""",
    "script": """{"title": "short", "hook": "0-3s line", "beats": [{"time": "0-3s", "say": "...", "show": "..."}],
 "cta": "closing line", "length_seconds": 30}
4-6 beats. Keep it under 45 seconds unless asked.""",
    "caption": """{"captions": [{"caption": "full caption", "hashtags": ["max 5"]}]}
3 options, different angles.""",
    "review": """{"verdict": "one sentence", "worked": ["..."], "fix": ["specific change", "..."],
 "try_next": "one concrete next post built from this one"}
Ground every point in the post's numbers vs their typical numbers.""",
    "ideas": """{"ideas": [{"idea": "what to film", "hook": "under 12 words", "format": "..."}]}
6 ideas.""",
}


def _task_prompt(task: str, *, stats: Dict[str, Any], niche: Dict[str, Any], args: Dict[str, Any],
                 post: Optional[Dict[str, Any]]) -> str:
    want = {k: args.get(k) for k in ("topic", "product", "brand", "draft", "goal", "count") if args.get(k)}
    lines = [
        f"TASK: {task}",
        f"ASK: {json.dumps(want, ensure_ascii=False) if want else 'none beyond the task'}",
        f"NICHE: {json.dumps(niche, ensure_ascii=False)}",
        f"STATS: {json.dumps(stats, ensure_ascii=False, default=str)}",
    ]
    if post:
        lines.append(f"POST TO REVIEW: {json.dumps(post, ensure_ascii=False, default=str)}")
    lines.append(f"RETURN JSON SHAPED LIKE:\n{_SHAPES[task]}")
    return "\n".join(lines)


def pick_post(posts: List[Dict[str, Any]], which: str) -> Optional[Dict[str, Any]]:
    """'latest' / 'best' / 'worst' / a URL / a 1-based index."""
    if not posts:
        return None
    which = (which or "latest").strip().lower()
    viewed = [p for p in posts if p["views"]] or posts
    if which in ("best", "top"):
        return max(viewed, key=lambda p: p["views"])
    if which in ("worst", "weakest", "lowest"):
        return min(viewed, key=lambda p: p["views"])
    if which.startswith("http"):
        return next((p for p in posts if p["url"] and p["url"].rstrip("/") == which.rstrip("/")), None)
    if which.isdigit() and 1 <= int(which) <= len(posts):
        return posts[int(which) - 1]
    return posts[0]


def _fallback(task: str, stats: Dict[str, Any], niche: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """No model: honest, generic structure plus their real top hooks."""
    topic = args.get("product") or args.get("topic") or niche.get("primary_niche") or "your niche"
    hooks = stats.get("top_hooks") or []
    if task == "hooks":
        return {"hooks": hooks[:3] + [f"I tried {topic} for a week so you don't have to",
                                     f"The {topic} mistake everyone makes",
                                     f"3 things I wish I knew about {topic}"],
                "pattern": "Your best posts open with a clear promise in the first line." if hooks else ""}
    if task == "weekly_plan":
        days = ["Monday", "Tuesday", "Thursday", "Saturday"]
        formats = ["Tutorial", "POV", "Honest review", "Talking head"]
        return {"focus": f"Four {topic} posts this week, built on what already gets you views.",
                "posts": [{"day": d, "format": f, "idea": f"{f} about {topic}",
                           "hook": (hooks[i] if i < len(hooks) else ""), "why": ""}
                          for i, (d, f) in enumerate(zip(days, formats))],
                "cadence_tip": ""}
    return {}


def run_task(task: str, scrape: Optional[Dict[str, Any]], creator: Optional[Dict[str, Any]],
             kit: Optional[Dict[str, Any]], args: Dict[str, Any], *, llm=None) -> Dict[str, Any]:
    if task not in TASKS:
        return {"ok": False, "error": f"task must be one of {list(TASKS)}"}
    stats = content_stats(scrape)
    niche = niche_summary(scrape, creator, kit)
    post = None
    if task == "review":
        pasted = str(args.get("draft") or "").strip()
        found = pick_post(posts_from_scrape(scrape), str(args.get("post") or "latest"))
        if found:
            post = _post_view(found, float(stats.get("median_views") or 0),
                              bool(stats.get("likes_comments_synced")))
            post["caption"] = found["caption"][:600]
        elif pasted:
            post = {"caption": pasted[:1200], "note": "Pasted by the creator; no stats for it."}
        else:
            return {"ok": True, "status": "no_post", "task": task,
                    "explain": "I can't see that post. Ask them to paste the caption or the link."}
    if llm is None:
        from services.pr_ready import _call_gemini_json as llm
    try:
        content = llm(_SYSTEM, _task_prompt(task, stats=stats, niche=niche, args=args, post=post))
        if not isinstance(content, dict) or not content:
            raise ValueError("empty content")
        source = "model"
    except Exception as err:
        print(f"[Polly content] {task} model failed: {err}")
        content = _fallback(task, stats, niche, args)
        source = "fallback"
        if not content:
            return {"ok": False, "status": "unavailable", "task": task,
                    "explain": "Writing that needs a moment. Ask them to try again shortly."}
    out = {"ok": True, "status": "written", "task": task, "content": content, "source": source,
           "grounded_in": stats_brief(stats)}
    if post:
        out["post"] = {k: post.get(k) for k in ("hook", "views", "vs_typical", "posted", "url") if post.get(k)}
    return out


_ASKS = (
    ("review", re.compile(r"(?i)\b(review|feedback on|rate|roast|critique)\b.{0,30}\b(post|video|reel|tiktok|caption)\b"
                          r"|\bwhy did (my|this|that) (last )?(post|video|reel)\b")),
    ("weekly_plan", re.compile(r"(?i)\b(content|posting)\s+(plan|calendar|schedule)\b"
                               r"|\bwhat should i (post|film)\b(?!.{0,20}\bfor\s+\w+\s+brand)")),
    ("script", re.compile(r"(?i)\b(write|give|need)\b.{0,20}\bscript\b")),
    ("hooks", re.compile(r"(?i)\bhooks?\b")),
    ("caption", re.compile(r"(?i)\b(write|give|need)\b.{0,20}\bcaptions?\b")),
    ("ideas", re.compile(r"(?i)\b(content|video|post|reel|tiktok)\s+ideas?\b")),
)
_STATS_ASK = re.compile(
    r"(?i)\bwhat'?s my niche\b|\bmy niche\?|\b(views|reach|engagement)\s+(are|is|have|has)?\s*(down|dropp|low|tank)"
    r"|\bhow (often|many times) should i post\b|\bwhat'?s working\b|\bhow (am i|are my posts) doing\b"
    r"|\bgrow (my|on)\b"
)


def content_ask(text: str):
    """Keyword route for the no-model fallback: (tool, args) or None."""
    text = text or ""
    for task, rx in _ASKS:
        if rx.search(text):
            return ("content_assist", {"task": task, "topic": text[:200]})
    if _STATS_ASK.search(text):
        return ("get_content_stats", {})
    return None


def render(result: Dict[str, Any]) -> str:
    """Markdown for the template reply when the model can't write the turn."""
    task, c = result.get("task"), result.get("content") or {}
    if task == "weekly_plan":
        rows = [f"- **{p.get('day')}** · {p.get('format')}: {p.get('idea')}"
                + (f" Hook: \"{p['hook']}\"" if p.get("hook") else "") for p in c.get("posts") or []]
        return "\n".join([f"**This week:** {c.get('focus') or ''}".strip(), "", *rows])
    if task in ("hooks", "ideas"):
        items = c.get("hooks") or [i.get("hook") or i.get("idea") for i in c.get("ideas") or []]
        return "\n".join(["Hooks to try:", *[f"- {h}" for h in items if h]])
    if task == "script":
        beats = [f"- **{b.get('time')}** {b.get('say')}" for b in c.get("beats") or []]
        return "\n".join([f"**{c.get('title') or 'Script'}**", f"Hook: {c.get('hook') or ''}", *beats,
                          f"CTA: {c.get('cta') or ''}"])
    if task == "caption":
        caps = [f"- {x.get('caption')}" for x in c.get("captions") or []]
        return "\n".join(["Caption options:", *caps])
    if task == "review":
        parts = [c.get("verdict") or ""]
        if c.get("fix"):
            parts += ["", "**Change next time:**", *[f"- {f}" for f in c["fix"]]]
        if c.get("try_next"):
            parts += ["", f"**Next post:** {c['try_next']}"]
        return "\n".join(parts).strip()
    return "Here you go."
