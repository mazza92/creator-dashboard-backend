"""Load the creator's Newcollab kit so Polly can review the real page."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

KIT_EDITOR_PATH = "/creator/dashboard/my-kit"
KIT_PUBLIC_HOST = "https://newcollab.co/kit"
MIN_ABOUT_CHARS = 150
MIN_POSTS = 3


def public_kit_url(slug: Optional[str]) -> Optional[str]:
    clean = re.sub(r"[^a-zA-Z0-9._-]", "", str(slug or "").strip().lstrip("@").lower())
    if not clean:
        return None
    return f"{KIT_PUBLIC_HOST}/{clean}"


def _as_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _bio_kit_state(bio: str, slug: Optional[str], username: Optional[str]) -> Dict[str, bool]:
    text = (bio or "").lower()
    targets = [s for s in (slug, username) if s]
    has_exact = False
    for item in targets:
        token = str(item).strip().lstrip("@").lower()
        if token and f"newcollab.co/kit/{token}" in text:
            has_exact = True
            break
    has_generic = "newcollab.co" in text and not has_exact
    return {
        "bio_has_kit_url": has_exact,
        "bio_has_generic_newcollab": has_generic,
        "bio_missing_kit_url": not has_exact,
    }


def _post_line(post: Dict[str, Any], index: int) -> str:
    platform = (post.get("platform") or "post").strip()
    brand = (post.get("brand_name") or "").strip()
    title = (post.get("title") or "").strip()
    url = (post.get("url") or post.get("post_url") or "").strip()
    views = post.get("views")
    likes = post.get("likes")
    bits = [f"{index}. {platform}"]
    if brand:
        bits.append(f"for {brand}")
    if title:
        bits.append(f"— {title[:80]}")
    stats = []
    try:
        if views not in (None, "", 0, "0"):
            stats.append(f"{int(views)} views")
    except (TypeError, ValueError):
        pass
    try:
        if likes not in (None, "", 0, "0"):
            stats.append(f"{int(likes)} likes")
    except (TypeError, ValueError):
        pass
    if stats:
        bits.append(f"({', '.join(stats)})")
    elif url:
        bits.append("(no stats saved)")
    return " ".join(bits)


def load_kit_snapshot(conn, creator_id: int, scrape: Optional[Dict] = None) -> Dict[str, Any]:
    """DB snapshot of My Kit — same facts the public page uses. Never invents posts."""
    empty = {
        "found": False,
        "published": False,
        "slug": None,
        "url": None,
        "editor_path": KIT_EDITOR_PATH,
        "gaps": ["I couldn't load My Kit yet — open it and we can go through it together."],
        "posts": [],
        "bio_has_kit_url": False,
        "bio_has_generic_newcollab": False,
    }
    if not conn or not creator_id:
        return empty
    scrape = scrape or {}
    try:
        from psycopg2.extras import RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT
                c.kit_published, c.kit_published_at, c.kit_slug, c.kit_tagline,
                c.kit_layout, c.kit_theme, c.username, c.bio, c.social_handle,
                c.social_platform, c.rates_reel, c.rates_tiktok, c.rates_photo, c.rates_gifted
            FROM creators c
            WHERE c.id = %s
            """,
            (creator_id,),
        )
        creator = cursor.fetchone()
        if not creator:
            return empty

        posts = []
        try:
            cursor.execute(
                """
                SELECT platform, post_type, brand_name, collab_type, views, likes,
                       comments, post_url, display_order, is_featured
                FROM portfolio_posts
                WHERE creator_id = %s
                ORDER BY display_order ASC, created_at DESC
                LIMIT 12
                """,
                (creator_id,),
            )
            posts = [dict(row) for row in cursor.fetchall()]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            posts = []

        theme = _as_dict(creator.get("kit_theme"))
        examples = _as_list(theme.get("example_posts"))
        if not examples:
            examples = [{"url": u} for u in _as_list(theme.get("examples")) if u]
        example_count = len([row for row in examples if (row.get("url") if isinstance(row, dict) else row)])
        post_count = len(posts) or example_count

        slug = (creator.get("kit_slug") or creator.get("username") or "").strip() or None
        url = public_kit_url(slug)
        about = (theme.get("about") or creator.get("bio") or "").strip()
        headline = (theme.get("headline") or creator.get("kit_tagline") or "").strip()
        display_name = (theme.get("display_name") or "").strip()
        has_email = bool((theme.get("email") or "").strip())
        rates = {
            "reel": creator.get("rates_reel"),
            "tiktok": creator.get("rates_tiktok"),
            "photo": creator.get("rates_photo"),
            "gifted": creator.get("rates_gifted"),
        }
        has_rate = any(v not in (None, "", 0, "0") for v in rates.values())
        services = [
            (row.get("title") or "").strip()
            for row in _as_list(theme.get("services"))
            if isinstance(row, dict) and (row.get("title") or "").strip()
        ]
        logos = _as_list(theme.get("brand_logos"))
        testimonials = _as_list(theme.get("testimonials"))
        tiktok_bio = (scrape.get("raw_bio") or scrape.get("bio") or "").strip()
        bio_state = _bio_kit_state(tiktok_bio, slug, creator.get("username"))

        gaps = []
        published = bool(creator.get("kit_published"))
        if not published:
            gaps.append("Kit is not published — brands cannot open it yet.")
        if not display_name:
            gaps.append("Display name on the kit is empty.")
        if len(about) < MIN_ABOUT_CHARS:
            gaps.append(f"About section is thin ({len(about)} chars) — brands want ~{MIN_ABOUT_CHARS}+ on who you shoot for.")
        if not has_email:
            gaps.append("No brand-contact email on the kit.")
        if post_count < 1:
            gaps.append("No work on the kit yet — add at least one TikTok/IG/YouTube post.")
        elif post_count < MIN_POSTS:
            gaps.append(f"Only {post_count} piece(s) on the kit — first three posts are what brands actually watch.")
        if not has_rate:
            gaps.append("No rates on the kit.")
        if not headline:
            gaps.append("No headline / tagline above the fold.")
        if bio_state["bio_has_generic_newcollab"]:
            gaps.append("TikTok bio links to newcollab.co, not the live kit URL.")
        elif bio_state["bio_missing_kit_url"]:
            gaps.append("TikTok bio does not include the live kit URL.")

        snapshot = {
            "found": True,
            "published": published,
            "slug": slug,
            "url": url,
            "editor_path": KIT_EDITOR_PATH,
            "display_name": display_name or None,
            "headline": headline or None,
            "about": about[:600] if about else None,
            "about_chars": len(about),
            "has_contact_email": has_email,
            "layout": creator.get("kit_layout") or "editorial",
            "post_count": post_count,
            "portfolio_post_count": len(posts),
            "example_post_count": example_count,
            "has_rates": has_rate,
            "rates": {k: v for k, v in rates.items() if v not in (None, "", 0, "0")},
            "services": services[:8],
            "brand_logo_count": len(logos),
            "testimonial_count": len(testimonials),
            "tiktok_bio": tiktok_bio[:280] if tiktok_bio else None,
            "gaps": gaps,
            "posts": posts[:8],
            "example_posts": examples[:8],
            **bio_state,
        }
        return snapshot
    except Exception as err:
        print(f"[Polly] kit snapshot skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return empty


def kit_context(snapshot: Optional[Dict[str, Any]]) -> str:
    kit = snapshot or {}
    if not kit.get("found"):
        return (
            "NEWCOLLAB KIT (ground truth):\n"
            "Could not load My Kit. Do not invent a portfolio. "
            "Tell them to tap My portfolio to build the Newcollab kit. "
            "Never paste the editor path. Never coach Linktree or a generic landing page."
        )
    lines = [
        "NEWCOLLAB KIT (this is THEIR media kit — you have already opened it. Speak from these facts only.):",
        f"Published: {'yes' if kit.get('published') else 'NO — still a draft'}",
        "Editor: in-app My Kit — the UI shows a My portfolio button. Do not paste a path.",
    ]
    if kit.get("url"):
        lines.append(f"Live URL brands should open: {kit['url']}")
    if kit.get("display_name"):
        lines.append(f"Name on kit: {kit['display_name']}")
    if kit.get("headline"):
        lines.append(f"Headline: {kit['headline']}")
    if kit.get("about"):
        lines.append(f"About ({kit.get('about_chars') or 0} chars): {kit['about'][:400]}")
    lines.append("Brand contact email on kit: " + ("present" if kit.get("has_contact_email") else "missing"))
    if kit.get("has_rates"):
        rate_bits = [f"{k}={v}" for k, v in (kit.get("rates") or {}).items()]
        lines.append("Rates on kit: " + ", ".join(rate_bits))
    else:
        lines.append("Rates on kit: none")
    if kit.get("services"):
        lines.append("Offers: " + ", ".join(kit["services"]))
    lines.append(f"Work on kit: {kit.get('post_count') or 0} piece(s)")
    for i, post in enumerate(kit.get("posts") or [], 1):
        lines.append(_post_line(post, i))
    if not kit.get("posts"):
        for i, row in enumerate(kit.get("example_posts") or [], 1):
            if isinstance(row, dict) and row.get("url"):
                title = (row.get("title") or "").strip()
                lines.append(f"{i}. example {title or row.get('url')}")
    if kit.get("tiktok_bio"):
        lines.append(f"Current TikTok/social bio: {kit['tiktok_bio']}")
    if kit.get("bio_has_kit_url"):
        lines.append("Bio already includes the live kit URL.")
    elif kit.get("bio_has_generic_newcollab"):
        lines.append("Bio links to newcollab.co generally — NOT the /kit/slug URL. Suggest the exact kit URL if they have a bio link slot.")
    else:
        lines.append("Bio does not include the kit URL yet. Not a blocker — many micros cannot add a bio link until the platform unlocks it.")
    if kit.get("gaps"):
        lines.append("Gaps to fix: " + " | ".join(kit["gaps"]))
    lines.append(
        "Coaching rule: Newcollab kit, not Linktree. Publish when you can. Offer the exact "
        "/kit/slug URL for bio because brands click it from the pitch and we track views. "
        "If they cannot add a bio link yet (low followers), say that's fine and keep pitching. "
        "Never treat a missing bio link as an immediate no or a reason to pause mentoring."
    )
    return "\n".join(lines)


def kit_reply_grounded(say: Optional[str], snapshot: Optional[Dict[str, Any]] = None) -> bool:
    low = (say or "").lower()
    if not low or "linktree" in low:
        return False
    url = str((snapshot or {}).get("url") or "").lower()
    return "my kit" in low or "newcollab.co/kit/" in low or (bool(url) and url in low)


def kit_actions(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    kit = snapshot or {}
    actions = [{
        "label": "My portfolio",
        "href": KIT_EDITOR_PATH,
        "external": False,
    }]
    if kit.get("published") and kit.get("url"):
        actions.append({
            "label": "View live kit",
            "href": kit["url"],
            "external": True,
        })
    return actions


_KIT_EDITOR_PATH_RE = re.compile(
    r"(?i)(?:here's the link:?\s*)?(?:https?://[^\s)]+)?/?creator/dashboard/my-kit"
)


def strip_kit_editor_paths(text: Optional[str] = None) -> str:
    """Drop raw /creator/dashboard/my-kit — the My portfolio button carries that."""
    s = text or ""
    s = re.sub(r"(?i)_{1,2}\s*/?creator/dashboard/my-kit\s*_{1,2}", "", s)
    s = _KIT_EDITOR_PATH_RE.sub("", s)
    s = re.sub(r"(?i)here'?s the link:?\s*", "", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def persona_kit_review(snapshot: Optional[Dict[str, Any]] = None, first_name: Optional[str] = None) -> str:
    kit = snapshot or {}
    name = (first_name or "").strip()
    hey = f"Right {name}," if name else "Right,"
    url = kit.get("url") or f"{KIT_PUBLIC_HOST}/yourname"
    if not kit.get("found"):
        return (
            f"{hey} I need **My Kit** open in front of me before I can coach this properly.\n\n"
            "That's your Newcollab media kit — the page brands actually click from a pitch. "
            f"Go to **My Kit**, build it there, then come back and I'll review the live page.\n\n"
            "When it's published, if they have a bio link slot, the kit URL belongs there "
            f"(__{url}__) — not the Newcollab homepage. If they don't (common on smaller accounts), "
            "that's fine; brands still get the kit from the pitch and we can see who viewed it."
        )

    gaps = kit.get("gaps") or []
    if not kit.get("published"):
        missing = gaps[:4] or ["It's still a draft."]
        steps = "\n".join(f"{i}. {item}" for i, item in enumerate(missing, 1))
        return (
            f"{hey} I opened **My Kit** — this is the Newcollab page brands review, not a random portfolio.\n\n"
            "It's **not live yet**, so a PR team has nothing to click.\n\n"
            f"**Fix these, then hit publish:**\n{steps}\n\n"
            "Tap **My portfolio**, make those edits, publish. If IG/TikTok already lets you add a "
            "bio link, paste this so brands land on the kit and we can track views:\n\n"
            f"__{url}__\n\n"
            "If you don't have a bio link slot yet, skip it — keep pitching. The kit URL is already in the email."
        )

    notes = []
    if kit.get("post_count", 0) < MIN_POSTS:
        notes.append(
            f"You've got **{kit.get('post_count') or 0}** piece(s) on the page. "
            "The first three are the whole review — add your strongest TikToks next."
        )
    elif kit.get("posts"):
        top = _post_line(kit["posts"][0], 1)
        notes.append(f"Lead piece right now: *{top}*. Make sure that's actually your strongest work.")
    if not kit.get("has_rates"):
        notes.append("No rates on the kit. Brands who want to pay bounce when they have to ask.")
    if (kit.get("about_chars") or 0) < MIN_ABOUT_CHARS:
        notes.append("The about blurb is too thin — who you are, who you shoot for, how you work.")
    if kit.get("bio_has_generic_newcollab"):
        notes.append(
            "Your TikTok bio currently points at *newcollab.co*, not the kit. "
            "Brands land on the homepage and bounce."
        )
    elif kit.get("bio_missing_kit_url"):
        notes.append(
            "If you have a bio link slot, paste the kit URL there — brands click through from the pitch "
            "and we can see who viewed it. If IG/TikTok hasn't unlocked links yet, that's fine."
        )
    if not notes:
        notes.append("Solid start. Next job is keeping the first three posts as your best work, with a number on each.")

    body = "\n\n".join(f"{i}. {n}" for i, n in enumerate(notes[:4], 1))
    bio_line = (
        "Bio already has the kit URL — nice. Keep it as the only link."
        if kit.get("bio_has_kit_url")
        else f"If you can add a link in bio, paste this (link field, not a comment). Brands click it and we track kit views. No slot yet? Skip — keep pitching:\n\n__{url}__"
    )
    return (
        f"{hey} I looked at your live kit: **{url}**\n\n"
        f"{body}\n\n"
        f"{bio_line}\n\n"
        "Tweak it in **My Kit**, then ping me and I'll re-check. 🎯"
    )
