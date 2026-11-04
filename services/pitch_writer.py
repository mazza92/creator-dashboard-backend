"""Short, brand-specific pitches in the creator's voice, checked before anything is sent.

Every pitch follows one proven cold-pitch structure and stays readable in under 30 seconds:

    Hi {Brand} team,

    {opener}      1-2 sentences, written for this brand: their product + one concrete video idea (Gemini)
    {proof}       one line, real numbers only, weak stats left out (code)
    {posts}       up to 2 posts the creator chose (code)
    {ask}         the gifted / paid ask line already on the pitch, kept as is (code)

    {close}
    {first name}
    {portfolio link}

Gemini only writes the opener and may not use numbers, so it can't invent stats. A second Gemini pass
judges the whole email "send-ready" alongside hard checks here. Any failure -> one rewrite -> a tested
template opener. If anything breaks, the original pitch is returned untouched.

Follow-ups (day 4, day 10) are short and add something new: a second idea, then a smaller ask.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional

MAX_WORDS = 120          # whole pitch, links not counted
MAX_OPENER_WORDS = 50
MAX_FOLLOWUP_WORDS = 50
MAX_SUBJECT_WORDS = 8
STRONG_ENGAGEMENT = 3.0  # % below which engagement is left out
STRUCTURES = ("idea_first", "fan_first", "results_first")

TONES: Dict[str, Dict[str, str]] = {
    "brief": {"label": "Brief & direct", "emoji": "✂️",
              "guide": "Short, plain sentences. Friendly but to the point. No filler.",
              "example": "Quick note: love the brand, already have two content ideas. Working with creators this season?"},
    "warm": {"label": "Warm & relatable", "emoji": "🏡",
             "guide": "Warm, personal, conversational. Everyday-life details, never gushing.",
             "example": "Between school runs and a never-quiet kitchen, your cleanser earned a real spot in my routine."},
    "upbeat": {"label": "Upbeat", "emoji": "⚡",
               "guide": "Energetic and positive, one exclamation mark at most.",
               "example": "I already have so many fun ideas for your next drop. It feels like such a natural fit!"},
    "playful": {"label": "Playful", "emoji": "😄",
                "guide": "Light humour, one playful touch, still clear about the idea.",
                "example": "Fair warning: my dog photobombs every shoot. Brands keep coming back for round two."},
    "professional": {"label": "Professional", "emoji": "💼",
                     "guide": "Polished and businesslike, no slang, no emojis.",
                     "example": "I'd like to propose a collaboration built around your launch and short-form goals."},
    "data": {"label": "Data-driven", "emoji": "📊",
             "guide": "Concrete and outcome-focused. Talk about what the video will make viewers do.",
             "example": "My last product Reel reached 180K views and sent 2,400 people to the brand's site."},
}
DEFAULT_TONE = "brief"

IDEA_BANK: Dict[str, List[str]] = {
    "skincare": ["a 15-second texture + first-use video", "a 'my night routine' video with it as the first step",
                 "a '3 products I'd actually rebuy' video with it as the pick"],
    "beauty": ["a get-ready-with-me video built around it", "a 'one product, three looks' video",
               "a 15-second before/after reveal"],
    "haircare": ["a wash-day routine video with it as the hero step", "a 15-second before/after on second-day hair",
                 "a 'how I get this volume' tutorial"],
    "wellness": ["a 'my morning reset' video with it in the routine", "an honest week-one check-in video",
                 "a 'what's in my bag' video featuring it"],
    "home": ["a 15-second 'room reset' video featuring it", "a 'small upgrades that changed my space' video",
             "a cosy evening routine video with it in shot"],
    "food": ["a quick 'what I eat in a day' video featuring it", "a 30-second recipe with it as the star",
             "a first-taste reaction video"],
    "fashion": ["a 'one piece, three outfits' video", "a try-on haul with it as the hero piece",
                "a 'how I style it for the week' video"],
    "fitness": ["a 'what I take to the gym' video with it", "a workout routine video featuring it",
                "a 15-second before/after-workout clip"],
    "pet": ["a day-in-the-life video with my pet and it", "a first-reaction unboxing with my pet",
            "a 'pet routine' video featuring it"],
    "tech": ["a 30-second 'how I use it every day' video", "a desk setup video featuring it",
             "a quick unboxing + first-impressions video"],
}
DEFAULT_IDEAS = ["a 15-second unboxing + first-impression video", "a 'how I use it every day' video",
                 "a '3 things I love about it' video"]

CLOSES = {"brief": "Worth a try?", "warm": "Would love to make this for you.", "upbeat": "Want to give it a go?",
          "playful": "Up for a little test video?", "professional": "Would this be of interest?",
          "data": "Open to a test video?"}

_URL = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|co|net|io|app)/\S*", re.I)
_PLACEHOLDER = re.compile(r"\[[A-Z ,_]+\]|\{\w+\}|lorem|TODO|XX", re.I)
# Stat-like numbers the opener may not use (durations like "15-second" or "3 products" are fine).
_STAT = re.compile(r"(?i)\d[\d,.]*\s*(?:k\b|m\b|%|percent|views?|followers?|likes?|saves?|clicks?|sales|x\b|times)"
                   r"|\d{1,3}(?:,\d{3})+|\b\d{4,}\b")
# The offer line, also when an older pitch has it mid-paragraph ("... for 3 months. No fee. Just product ...").
_ASK = re.compile(r"(?im)(?:\bno fee\.[^\n]*|^rate:[^\n]*$)")
MIN_PROOF_VIEWS = 1000  # below this, view counts read as a weakness: say nothing


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def profile_from_notes(notes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The creator's pitch profile (set in the Pro setup), with defaults."""
    raw = dict((notes or {}).get("pitch_profile") or {})
    chat_tone = ((notes or {}).get("prefs") or {}).get("tone")  # older Polly chat preference
    tone = raw.get("tone") if raw.get("tone") in TONES else ("professional" if chat_tone == "professional" else DEFAULT_TONE)
    structure = raw.get("structure") if raw.get("structure") in STRUCTURES else ""
    rules = [str(r).strip()[:120] for r in (raw.get("style_rules") or []) if str(r).strip()][:6]
    posts = [str(p).strip() for p in (raw.get("proof_posts") or []) if str(p).strip().startswith("http")][:2]
    return {
        "tone": tone,
        "voice": "sample" if raw.get("voice") == "sample" and rules else "preset",
        "style_rules": rules,
        "structure": structure,
        "proof_posts": posts,
        "win": " ".join(str(raw.get("win") or "").split())[:160],
        "setup_done_at": raw.get("setup_done_at"),
    }


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def words(text: str) -> int:
    return len(_URL.sub(" ", str(text or "")).split())


def fmt_count(n: Any) -> str:
    try:
        v = int(float(n))
    except (TypeError, ValueError):
        return ""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1000:
        return f"{v / 1000:.1f}K".replace(".0K", "K")
    return str(v) if v > 0 else ""


def _engagement_pct(raw: Any) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw or ""))
    return float(m.group(1)) if m else 0.0


def _count(v: Any) -> int:
    s = str(v or "").strip().upper().replace(",", "")
    mult = 1000 if s.endswith("K") else 1_000_000 if s.endswith("M") else 1
    try:
        return int(float(s.rstrip("KM") or 0) * mult)
    except ValueError:
        return 0


def proof_line(facts: Dict[str, Any]) -> str:
    """One line of real proof. Engagement only when it's strong; nothing when stats are off."""
    lead = facts.get("lead_metric") or ""
    niche = facts.get("niche") or ""
    what = f"My {niche} videos" if niche else "My videos"
    parts = []
    views, followers = facts.get("avg_views"), facts.get("followers")
    eng = _engagement_pct(facts.get("engagement"))
    if lead == "none":
        pass
    elif lead == "followers" and followers:
        parts.append(f"I have {followers} followers on {facts.get('platform') or 'my page'}.")
    elif lead == "engagement" and eng >= STRONG_ENGAGEMENT:
        parts.append(f"{what} get {eng:g}% engagement.")
    elif views and (facts.get("avg_views_n") or 0) >= MIN_PROOF_VIEWS:
        parts.append(f"{what} average {views} views.")
    elif followers and _count(followers) >= MIN_PROOF_VIEWS:
        parts.append(f"I have {followers} followers on {facts.get('platform') or 'my page'}.")
    if facts.get("win"):
        win = facts["win"].rstrip(".")
        parts.append(win[0].upper() + win[1:] + ".")
    return " ".join(parts)


def ideas_for(niche: str) -> List[str]:
    key = str(niche or "").lower()
    for k, v in IDEA_BANK.items():
        if k in key:
            return v
    return DEFAULT_IDEAS


def _pick(seq: List[str], seed: str, offset: int = 0) -> str:
    if not seq:
        return ""
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return seq[(h + offset) % len(seq)]


def subject_options(brand: str, product: str, first: str) -> List[Dict[str, str]]:
    p = product if product and product != "PR sample" and not product.startswith("your ") else ""
    out = []
    if p and first:
        out.append({"id": "brand_x_name", "text": f"{brand} × {first}: an idea for {p}"})
    if p:
        out.append({"id": "video_idea", "text": f"Video idea for {p}"})
    out.append({"id": "ugc_for_brand", "text": f"UGC for {brand}?"})
    return [o for o in out if len(o["text"].split()) <= MAX_SUBJECT_WORDS] or [{"id": "ugc_for_brand", "text": f"UGC for {brand}?"}]


def pick_subject(brand: str, product: str, first: str, seed: str) -> Dict[str, str]:
    """Rotates subject variants per pitch so we can see which one wins (variant id goes on the meta)."""
    return {**_pick_dict(subject_options(brand, product, first), seed)}


def _pick_dict(seq: List[Dict[str, str]], seed: str) -> Dict[str, str]:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return seq[h % len(seq)]


def _place(part: str) -> str:
    # "NEW YORK" -> "New York"; short codes like USA or UK stay as they are.
    letters = re.sub(r"[^A-Za-z]", "", part)
    return part.title() if part.isupper() and len(letters) > 3 else part


def find_ask(body: str) -> str:
    m = _ASK.search(body or "")
    if not m:
        return ""
    line = m.group(0).strip()
    # Ship-to typed in capitals ("NEW YORK") reads like shouting in a pitch.
    head, sep, place = line.partition("shipping to ")
    return head + sep + ", ".join(_place(p) for p in place.split(", ")) if sep else line


def find_kit(body: str) -> str:
    m = re.search(r"https?://(?:www\.)?newcollab\.co/kit/\S+", body or "")
    return m.group(0).rstrip(".,)") if m else ""


# ---------------------------------------------------------------------------
# Gemini (opener + send-ready review)
# ---------------------------------------------------------------------------

def gemini_json(system: str, user: str, max_tokens: int = 400) -> Dict[str, Any]:
    try:
        from services.brand_pipeline import _gemini_json
    except Exception:
        return {}
    # No thinking: these are short JSON answers, and thinking tokens would eat the budget and cut the JSON off.
    return _gemini_json(system, user, max_tokens=max(max_tokens, 600), thinking_budget=0)


OPENER_SYSTEM = (
    "You write the first 1-2 sentences of a cold pitch email from a UGC creator to a brand. "
    "Return JSON {\"opener\": \"...\"}. Rules: at most 45 words. Name the product exactly as given and propose ONE "
    "concrete short-form video idea for it (the format and what the viewer sees). First person, in the tone given. "
    "Follow the creator's style rules if any, but keep it clear to a stranger at a brand (proper capitalisation, "
    "no slang a brand could misread). NEVER use stats, view or follower counts, percentages or past results (a video length like 15-second is fine). Do not claim you "
    "already use or own the product. No greeting, no sign-off, no links, no hashtags, at most one emoji. "
    "Open naturally and vary it; don't start with 'I envision' or 'Imagine'."
)

REVIEW_SYSTEM = (
    "You are the last safety check on a short cold pitch from a UGC creator to a brand. The format is fixed and "
    "proven: 'Hi <Brand> team,', a 1-2 sentence video idea, 'Recent posts:' links, a one-line offer (e.g. 'No fee. "
    "Just product + shipping to ...' or 'Rate: ...'), a short close question, the creator's first name and a "
    "portfolio link. Brand and product names are written the way the brand writes them (lowercase is fine), and the "
    "creator's handle can differ from their first name. Short and direct is the goal: do NOT judge style, tone, "
    "length, the close, the sign-off or the offer line. Return JSON {\"send_ready\": true|false, \"issues\": [...]}. "
    "Set send_ready false ONLY for a real problem: a broken or nonsensical sentence, leftover template text or "
    "placeholders, the idea is about a different product or brand, invented facts or numbers, or something rude, "
    "spammy or misleading. Otherwise send_ready is true and issues is empty."
)


def write_opener(facts: Dict[str, Any], profile: Dict[str, Any], llm: Callable = gemini_json,
                 feedback: str = "") -> str:
    tone = TONES[profile["tone"]]
    payload = {
        "brand": facts["brand"], "product": facts["product"], "brand_category": facts.get("category") or "",
        "creator_niche": facts.get("niche") or "", "platform": facts.get("platform") or "",
        "structure": facts["structure"],
        "structure_hint": {
            "idea_first": "Open with the video idea itself.",
            "fan_first": "Open with why this product fits your content and audience, then the idea.",
            "results_first": "Open with the idea framed around what it will make viewers do.",
        }[facts["structure"]],
        "tone": tone["label"], "tone_guide": tone["guide"], "tone_example": tone["example"],
        "style_rules": profile.get("style_rules") or [],
        "idea_suggestions": facts.get("ideas") or [],
    }
    if feedback:
        payload["fix_these_issues"] = feedback
    out = llm(OPENER_SYSTEM, json.dumps(payload), max_tokens=300) or {}
    return " ".join(str(out.get("opener") or "").split())


def fallback_opener(facts: Dict[str, Any]) -> str:
    idea = (facts.get("ideas") or DEFAULT_IDEAS)[0]
    product = facts["product"]
    if facts["structure"] == "fan_first":
        return f"{product} fits right into the kind of content my audience saves. I'd love to make {idea} for it."
    return f"I have an idea for {product}: {idea}, short and easy to reuse in your ads."


def opener_issues(opener: str, facts: Dict[str, Any]) -> List[str]:
    issues = []
    if not opener:
        return ["empty opener"]
    if words(opener) > MAX_OPENER_WORDS:
        issues.append("opener too long")
    product = facts.get("product") or ""
    if product and product != "PR sample" and product.lower() not in opener.lower():
        issues.append("opener must name the product")
    stripped = opener.replace(product, "")
    if _STAT.search(stripped):
        issues.append("no stats or results in the opener")
    if _URL.search(opener) or "#" in opener:
        issues.append("no links or hashtags")
    if _PLACEHOLDER.search(opener):
        issues.append("template text left in")
    if re.search(r"(?i)\b(i'?ve (been )?using|i use|i love using|my favou?rite)\b", opener):
        issues.append("don't claim personal use")
    return issues


def body_issues(subject: str, body: str, facts: Dict[str, Any]) -> List[str]:
    issues = []
    if words(body) > MAX_WORDS:
        issues.append(f"pitch is {words(body)} words (max {MAX_WORDS})")
    if len(subject.split()) > MAX_SUBJECT_WORDS:
        issues.append("subject too long")
    if _PLACEHOLDER.search(body) or _PLACEHOLDER.search(subject):
        issues.append("template text left in")
    if not body.startswith("Hi "):
        issues.append("missing greeting")
    if facts.get("first") and not re.search(rf"(?m)^{re.escape(facts['first'])}$", body):
        issues.append("missing sign-off name")
    if not facts.get("ask") or facts["ask"] not in body:
        issues.append("missing ask")
    return issues


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

def assemble(opener: str, facts: Dict[str, Any]) -> str:
    proof = proof_line(facts)
    first = [opener, proof] if facts["structure"] != "results_first" or not proof else [proof, opener]
    lines = [f"Hi {facts['brand']} team,", "", " ".join(p for p in first if p)]
    posts = facts.get("posts") or []
    if posts:
        lines += ["", "Recent posts:"] + posts[:2]
    if facts.get("ask"):
        lines += ["", facts["ask"]]
    lines += ["", facts.get("close") or "Worth a try?"]
    if facts.get("first"):
        lines.append(facts["first"])
    if facts.get("kit"):
        lines.append(facts["kit"])
    return "\n".join(lines)


def _signoff(pitch_body: str, first_name: str) -> str:
    if first_name:
        return first_name.strip().split()[0].capitalize()
    last = [ln for ln in (pitch_body or "").strip().splitlines() if ln.strip()]
    return last[-1].strip() if last else ""


def clean_brand(name: str) -> str:
    """'Nutrisense (US)' -> 'Nutrisense': the region tag is for our catalog, not the greeting."""
    return re.sub(r"\s*\((?:US|UK|EU|CA|AU|USA|Global|International)\)\s*$", "", str(name or "").strip(), flags=re.I)


def clean_product(product: str) -> str:
    """A real product name stays; a category ('Lighting fixtures and home hardware') reads as 'your ...'."""
    p = " ".join(str(product or "").split())
    if not p or p.lower() in ("pr sample", "product", "products"):
        return "your products"
    words_ = p.split()
    looks_category = (" and " in p or " & " in p) and sum(w[:1].islower() for w in words_) >= len(words_) / 2
    return f"your {p[0].lower()}{p[1:]}" if looks_category else p


def build_facts(pitch: Dict[str, Any], *, brand: Dict[str, Any], profile: Dict[str, Any],
                first_name: str = "", stats: Optional[Dict[str, Any]] = None,
                prefs: Optional[Dict[str, Any]] = None, seed: str = "") -> Dict[str, Any]:
    stats = stats or {}
    prefs = prefs or {}
    body = str(pitch.get("body") or "")
    brand_name = clean_brand(brand.get("brand_name") or brand.get("name") or pitch.get("brand_name") or "")
    product = clean_product(pitch.get("product_name") or brand.get("hero_product") or "")
    niche = (pitch.get("niche") or (pitch.get("creator_stats") or {}).get("niche") or "").strip()
    ideas = ideas_for(niche or brand.get("category") or "")
    lead = str(prefs.get("lead_metric") or "").lower()
    engagement = pitch.get("engagement") if prefs.get("show_engagement_rate", True) is not False else ""
    posts = profile.get("proof_posts") or stats.get("top_posts") or []
    structure = profile.get("structure") or (
        "results_first" if (stats.get("avg_views_n") or 0) >= 20000 or profile.get("win") else "idea_first")
    return {
        "brand": brand_name or "there",
        "product": product,
        "category": brand.get("category") or "",
        "niche": niche,
        "platform": pitch.get("platform") or "",
        "followers": pitch.get("followers") or "",
        "engagement": engagement,
        "avg_views": stats.get("avg_views") or "",
        "avg_views_n": stats.get("avg_views_n") or 0,
        "lead_metric": "none" if lead in ("none", "no_stats") else lead,
        "win": profile.get("win") or "",
        "posts": list(dict.fromkeys(posts))[:2],
        "ask": find_ask(body),
        "kit": find_kit(body) or pitch.get("media_kit_url") or "",
        "first": _signoff(body, first_name),
        "close": CLOSES.get(profile["tone"], "Worth a try?"),
        "ideas": [_pick(ideas, seed, i) for i in range(len(ideas))][:3],
        "structure": structure,
    }


def creator_stats(conn, creator_id: int) -> Dict[str, Any]:
    """Median views and the 2 best posts from the last scrape."""
    try:
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """SELECT d.recent_posts FROM creators c JOIN creator_profile_data d ON d.user_id = c.user_id
               WHERE c.id = %s""",
            (creator_id,),
        )
        row = cur.fetchone() or {}
        posts = row.get("recent_posts") or []
        if isinstance(posts, str):
            posts = json.loads(posts or "[]")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    scored = []
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        try:
            v = int(float(p.get("views") or p.get("playCount") or 0))
        except (TypeError, ValueError):
            v = 0
        url = str(p.get("post_url") or p.get("url") or "").split("?")[0]
        scored.append((v, url))
    views = sorted(v for v, _ in scored if v > 0)
    if not views:
        return {}
    median = views[len(views) // 2]
    top = [u for v, u in sorted(scored, reverse=True) if u.startswith("http")][:2]
    return {"avg_views": fmt_count(median), "avg_views_n": median, "top_posts": top}


def finalize_pitch(pitch: Optional[Dict[str, Any]], *, brand: Dict[str, Any], notes: Optional[Dict[str, Any]],
                   first_name: str = "", stats: Optional[Dict[str, Any]] = None, seed: str = "",
                   llm: Callable = gemini_json, review: bool = True) -> Optional[Dict[str, Any]]:
    """The send-ready version of a drafted pitch. Returns the original pitch if anything goes wrong."""
    if not pitch or not pitch.get("body") or pitch.get("is_followup") or pitch.get("is_reply"):
        return pitch
    try:
        from services.polly_prefs import get_prefs

        if get_prefs(notes or {}).get("custom_template"):
            return pitch  # the creator wrote their own template: keep it
        profile = profile_from_notes(notes)
        facts = build_facts(pitch, brand=brand, profile=profile, first_name=first_name, stats=stats,
                            prefs=get_prefs(notes or {}), seed=seed or f"{brand.get('id')}")
        subject = pick_subject(facts["brand"], facts["product"], facts["first"], seed or str(brand.get("id")))
        source = "gemini"
        opener = write_opener(facts, profile, llm)
        problems = opener_issues(opener, facts)
        if problems:
            opener = write_opener(facts, profile, llm, feedback="; ".join(problems))
            problems = opener_issues(opener, facts)
        if problems:
            opener, source = fallback_opener(facts), "template"
        body = assemble(opener, facts)
        issues = body_issues(subject["text"], body, facts)
        verdict = {}
        if not issues and review and source == "gemini":
            verdict = llm(REVIEW_SYSTEM, json.dumps({"subject": subject["text"], "body": body}), max_tokens=200) or {}
            if verdict and verdict.get("send_ready") is False:
                opener = fallback_opener(facts)
                body, source = assemble(opener, facts), "template"
                issues = body_issues(subject["text"], body, facts)
        if issues:
            return pitch
        out = dict(pitch)
        out["subject"] = subject["text"]
        out["body"] = body
        out["pitch_meta"] = {
            "writer": "v2", "source": source, "structure": facts["structure"], "tone": profile["tone"],
            "subject_variant": subject["id"], "product": facts["product"], "ideas": facts["ideas"],
            "words": words(body), "review": verdict.get("issues") if verdict else None,
        }
        if out.get("email"):
            from services.polly import build_mailto
            out["mailto"] = build_mailto(out["email"], out["subject"], body)
        return out
    except Exception as err:
        print(f"[pitch_writer] kept original pitch: {err}")
        return pitch


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

def compose_followup_v2(first_name: str, brand_name: str, subject: str, wave: int,
                        meta: Optional[Dict[str, Any]] = None, paid: bool = False) -> Dict[str, str]:
    """Day 4: a second idea. Day 10: a smaller, easier ask. Both under 50 words."""
    meta = meta or {}
    name = (brand_name or "your team").strip()
    subj = (subject or f"{name} collab").strip()
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    product = meta.get("product") if meta.get("product") and meta.get("product") != "PR sample" else "it"
    product = "them" if product.startswith("your ") else product
    ideas = meta.get("ideas") or DEFAULT_IDEAS
    sign = (first_name or "").strip()
    if wave >= 2:
        middle = (
            "Last note from me. If a full collab isn't open right now, I'm happy to start smaller: "
            + ("one test video at a lower rate." if paid else "2 raw UGC clips you can use in ads, no posting needed.")
        )
    else:
        alt = ideas[1] if len(ideas) > 1 else ideas[0]
        middle = f"One more idea in case the first wasn't right: {alt} for {product}. Happy to send a quick concept."
    body = f"Hi {name} team,\n\n{middle}\n\n" + (sign or "Thanks!")
    return {"subject": subj, "body": body}


# ---------------------------------------------------------------------------
# Write like me
# ---------------------------------------------------------------------------

STYLE_SYSTEM = (
    "A UGC creator pasted a sample of their own writing. Describe their writing style as 3-5 short rules another "
    "writer could follow for a brand pitch email, keeping it professional enough for a brand inbox (no rules that "
    "would make it unclear, all-lowercase or full of slang). Return JSON {\"rules\": [\"...\"], \"summary\": \"3-6 "
    "word label\"}."
)


def style_from_sample(sample: str, llm: Callable = gemini_json) -> Dict[str, Any]:
    text = " ".join(str(sample or "").split())[:1500]
    if words(text) < 12:
        return {"ok": False, "error": "Paste at least a couple of sentences you wrote."}
    out = llm(STYLE_SYSTEM, json.dumps({"sample": text}), max_tokens=300) or {}
    rules = [" ".join(str(r).split())[:120] for r in (out.get("rules") or []) if str(r).strip()][:5]
    if not rules:
        return {"ok": False, "error": "I couldn't read a style from that. Try a caption or an email you wrote."}
    return {"ok": True, "rules": rules, "summary": str(out.get("summary") or "")[:60]}


def finalize_for(conn, creator_id: int, brand_id: Any, pitch: Optional[Dict[str, Any]],
                 notes: Optional[Dict[str, Any]], first_name: str = "",
                 posts: Optional[List[str]] = None, llm: Callable = gemini_json,
                 angle: int = 0) -> Optional[Dict[str, Any]]:
    """finalize_pitch with the brand row and the creator's stats loaded. Never raises."""
    try:
        from psycopg2.extras import RealDictCursor

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, brand_name, hero_product, category FROM pr_brands WHERE id = %s", (brand_id,))
        brand = dict(cur.fetchone() or {"id": brand_id, "brand_name": (pitch or {}).get("brand_name") or ""})
        stats = creator_stats(conn, creator_id)
        if posts:
            stats = {**stats, "top_posts": [p for p in posts if str(p).startswith("http")][:2]}
        return finalize_pitch(pitch, brand=brand, notes=notes, first_name=first_name, stats=stats,
                              seed=f"{creator_id}:{brand_id}" + (f":{angle}" if angle else ""), llm=llm)
    except Exception as err:
        print(f"[pitch_writer] finalize skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return pitch
