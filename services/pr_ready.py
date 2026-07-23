"""
PR-Ready orchestration — thin layer over existing scrape + kit + Gemini mentor data.

No new tables. Reuses:
- creator_profile_data (CreatorProfileScraper)
- portfolio_posts / creators.kit_* (PortfolioBuilder)
- brand_readiness_signals + Ideal UGC Profile checklist (ai_depth_generator)
- Gemini HTTP pattern (creator_profile_scraper.run_text_analysis)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from media_proxy_routes import proxy_media_urls

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_PR_READY_MODEL", "gemini-2.5-flash")

_COLLAB_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


def _extract_collab_email(*texts: Any) -> Optional[str]:
    """
    Prefer the creator's own collab email over MGMT / agency addresses.
    Skips lines that look like management contacts when multiple emails exist.
    """
    fallback: Optional[str] = None
    for text in texts:
        if not text:
            continue
        for line in str(text).replace("\\n", "\n").splitlines():
            m = _COLLAB_EMAIL_RE.search(line)
            if not m:
                continue
            email = m.group(0)
            if re.search(r"\b(mgmt|management|agency|manager)\b", line, re.I):
                if not fallback:
                    fallback = email
                continue
            return email
        if not fallback:
            m = _COLLAB_EMAIL_RE.search(str(text))
            if m:
                fallback = m.group(0)
    return fallback


def _as_dict(value: Any) -> Dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


def _as_list(value: Any) -> List:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


# Free diagnosis can look strong; Ready is reserved for Pro-backed assets.
FREE_SCORE_CAP = 68
READY_SCORE_THRESHOLD = 85
ALMOST_SCORE_THRESHOLD = 55
NOT_YET_SCORE_THRESHOLD = 30
# Milestone shown on AI Manager — Campaign Ready is the next tangible bar after free setup.
CAMPAIGN_READY_THRESHOLD = 65
# Score = profile foundation (evidence) + checklist completions (actions).
# Never show 0% when a real scrape/bio/posts exist — that reads as a broken meter.
FOUNDATION_SCORE_MAX = 30
CHECKLIST_SCORE_MAX = 70


def _foundation_hireability(
    *,
    bio: str,
    portfolio_count: int,
    post_count: int,
    product_hits: int,
    shows_products: bool,
    mention_count: int,
    follower_count: int,
    has_email: bool,
) -> int:
    """Soft baseline from live profile evidence (not checklist completion)."""
    pts = 0
    bio_len = len((bio or "").strip())
    if bio_len >= 12:
        pts += 8
    if bio_len >= 35:
        pts += 4
    if has_email:
        pts += 3
    if portfolio_count >= 3:
        pts += 6
    if portfolio_count >= 6:
        pts += 3
    if post_count >= 1:
        pts += 3
    if shows_products or product_hits >= 1:
        pts += 5
    if mention_count >= 1:
        pts += 3
    if follower_count >= 100:
        pts += 2
    if follower_count >= 1000:
        pts += 2
    return int(min(FOUNDATION_SCORE_MAX, pts))


# Bento-style free meters (match pr_crm FREE_UNLOCK_LIMIT / pitch limits)
FREE_BRAND_UNLOCK_LIMIT = 3
FREE_PITCH_LIMIT = 3
FREE_KIT_POST_LIMIT_DISPLAY = 3
PRO_KIT_POST_LIMIT_DISPLAY = 9


def build_monetization_plan(
    *,
    is_pro: bool,
    unlock_balance: Optional[Dict] = None,
    pitches_used: int = 0,
    brands_matched: Optional[int] = None,
    kit_post_count: int = 0,
    score_capped: bool = False,
) -> Dict[str, Any]:
    """
    Bento-style plan surface: hard quota meter + tools unlocked catalog.
    Free forever with visible walls; Pro removes meters and unlocks the catalog.
    """
    unlock_balance = unlock_balance or {}
    if is_pro or unlock_balance.get("is_unlimited"):
        unlock_meter = {
            "id": "brand_unlocks",
            "label": "Brand PR unlocks",
            "used": None,
            "limit": None,
            "remaining": None,
            "unlimited": True,
            "period": "month",
        }
    else:
        limit = int(unlock_balance.get("limit") or FREE_BRAND_UNLOCK_LIMIT)
        used = int(unlock_balance.get("used") or 0)
        remaining = unlock_balance.get("remaining")
        if remaining is None:
            remaining = max(0, limit - used)
        unlock_meter = {
            "id": "brand_unlocks",
            "label": "Brand PR unlocks",
            "used": used,
            "limit": limit,
            "remaining": int(remaining),
            "unlimited": False,
            "period": "month",
        }

    pitch_limit = None if is_pro else FREE_PITCH_LIMIT
    pitch_meter = {
        "id": "pitches",
        "label": "Pitches this month",
        "used": int(pitches_used or 0) if not is_pro else None,
        "limit": pitch_limit,
        "remaining": None
        if is_pro
        else max(0, FREE_PITCH_LIMIT - int(pitches_used or 0)),
        "unlimited": bool(is_pro),
        "period": "month",
    }

    # Scarce free surface (Bento): diagnosis + 1 rewrite + draft kit + 1 hook.
    # Gate coaching evidence, engagement stats, tracking, volume, and pitching power.
    free_tools = [
        {"id": "diagnosis", "label": "Capped readiness score", "unlocked": True},
        {"id": "bio_rewrite", "label": "1 AI bio rewrite", "unlocked": True},
        {
            "id": "auto_kit_draft",
            "label": f"Draft portfolio ({FREE_KIT_POST_LIMIT_DISPLAY} posts, no stats)",
            "unlocked": True,
        },
        {"id": "sample_hook", "label": "1 sample UGC hook", "unlocked": True},
    ]
    pro_tools = [
        {
            "id": "fix_coaching",
            "label": "Full fix coaching + evidence",
            "unlocked": bool(is_pro),
        },
        {
            "id": "engagement_stats",
            "label": "Post engagement stats on portfolio",
            "unlocked": bool(is_pro),
        },
        {
            "id": "ready_score",
            "label": "Uncapped Ready score",
            "unlocked": bool(is_pro),
        },
        {"id": "kit_views", "label": "Portfolio view tracking", "unlocked": bool(is_pro)},
        {
            "id": "weekly_hooks",
            "label": "Weekly UGC hooks pack",
            "unlocked": bool(is_pro),
        },
        {
            "id": "kit_9",
            "label": f"{PRO_KIT_POST_LIMIT_DISPLAY}-post auto-portfolio",
            "unlocked": bool(is_pro),
        },
        {
            "id": "unlimited_unlocks",
            "label": "Unlimited Brand PR unlocks",
            "unlocked": bool(is_pro),
        },
        {
            "id": "pitch_pack",
            "label": "Full pitch pack + rate tips",
            "unlocked": bool(is_pro),
        },
    ]
    tools = free_tools + pro_tools
    unlocked_count = sum(1 for t in tools if t.get("unlocked"))

    return {
        "plan": "pro" if is_pro else "free",
        "price": "$19/mo",
        "pitch": "Free shows the score. Pro unlocks coaching, stats, and pitching power.",
        "primary_meter": unlock_meter,
        "secondary_meter": pitch_meter,
        "tools": tools,
        # Opaque "4 of 12 tools" erodes trust — prefer remaining unlock framing only.
        "tools_unlocked": unlocked_count,
        "tools_total": len(tools),
        "show_tools_counter": False,
        "inventory": {
            "brands_matched": brands_matched,
            "kit_posts": int(kit_post_count or 0),
            "kit_post_limit": None
            if is_pro
            else FREE_KIT_POST_LIMIT_DISPLAY,
        },
        "score_capped": bool(score_capped and not is_pro),
        "upgrade_headline": "Upgrade to unlock access",
        "locked_tools": [t for t in tools if not t.get("unlocked")],
    }


def compute_pr_ready_score(
    scrape: Optional[Dict],
    kit_status: Optional[Dict] = None,
    *,
    is_pro: bool = False,
    creator_bio: Optional[str] = None,
    creator_profile: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Brand-agnostic readiness score from scrape signals + kit completeness.

    Free users get an honest diagnosis, but score/status are capped so Ready
    requires Pro-backed assets (live kit with tracking + weekly hooks path).

    creator_bio: optional saved rewrite on creators.bio — preferred over thin scrape bio.
    creator_profile: optional creators row (rates, kit_tagline) for Pro milestone checks.
    """
    scrape = scrape or {}
    kit_status = kit_status or {}
    creator_profile = creator_profile or {}
    signals = _as_dict(scrape.get("brand_readiness_signals"))
    brands_tagged = _as_list(scrape.get("brands_already_tagged")) or _as_list(
        signals.get("brands_already_tagged")
    )
    caption_mentions = _as_list(scrape.get("caption_mentions"))
    captions = _as_list(scrape.get("recent_captions"))
    if not captions:
        captions = [
            (p.get("caption") or "")
            for p in _as_list(scrape.get("recent_posts"))
            if isinstance(p, dict)
        ]

    product_hits = _caption_product_hits(captions)
    mention_handles = caption_mentions or _extract_caption_mentions(
        captions, scrape.get("handle") or ""
    )

    content_format = _as_dict(scrape.get("content_format_breakdown"))
    product_content = (
        int(content_format.get("product_close_ups") or 0)
        + int(content_format.get("grwm_routine") or 0)
        + int(content_format.get("before_after") or 0)
    )

    shows_products = (
        bool(signals.get("shows_products_in_use"))
        or product_content > 0
        or product_hits >= 2
    )

    scrape_bio = (scrape.get("raw_bio") or "").strip()
    saved_bio = (creator_bio or "").strip()
    # Prefer the saved rewrite when it's richer than the scrape bio
    if saved_bio and (
        len(saved_bio) > len(scrape_bio)
        or _bio_looks_professional(saved_bio, "@" in saved_bio)
    ):
        bio = saved_bio
    else:
        bio = scrape_bio

    extracted = _extract_collab_email(
        bio,
        scrape.get("collab_email_extracted"),
        scrape.get("biography"),
        scrape_bio,
        saved_bio,
    )
    has_email = bool(
        scrape.get("has_collab_email")
        or scrape.get("collab_email_extracted")
        or extracted
    )
    post_count = int(kit_status.get("post_count") or 0)
    kit_ok = bool(kit_status.get("is_published")) and post_count >= 3
    # One win: niche-first bio + public PR email (merged former #1 + #2)
    bio_pack_ok = _bio_looks_professional(bio, has_email) and has_email
    # Empty scrape bio (no rewrite saved) = critical manager priority
    bio_empty = bool(scrape.get("bio_empty")) or (
        not scrape_bio and not (saved_bio and len(saved_bio.strip()) >= 8)
    )

    bio_preview = (scrape_bio or bio)[:140] if (scrape_bio or bio) else ""
    if bio_empty and not bio_pack_ok:
        bio_item = {
            "id": "bio",
            "title": "Add a bio — brands skip blank profiles",
            "label": "Bio missing (critical)",
            "done": False,
            "critical": True,
            "impact": 5,
            "time_minutes": 3,
            "tip": "Write a niche-first bio and add a public PR email brands can copy.",
            "why": (
                "Your profile has no bio. Brands decide in seconds — a blank bio looks "
                "inactive or incomplete, so they skip you before opening your content."
            ),
            "current": "Empty bio on your social profile",
            "optimized_bio": None,
            "needs_email": True,
            "email": "",
            "cta": "Write my bio",
            "cta_action": "rewrite_bio",
            "free": True,
            "value": "action",
            "evidence": None,
        }
    else:
        bio_item = {
            "id": "bio",
            "title": "Optimize your bio for brand outreach",
            "label": "Pro bio + PR email",
            "done": bio_pack_ok,
            "critical": False,
            "impact": 5,
            "time_minutes": 3,
            "tip": "Niche + proof + a public PR email brands can copy into outreach tools.",
            "why": (
                "PR teams skim in ~3 seconds — and many agencies export emails into CRMs. "
                "A niche-first bio without a public email still gets skipped."
            ),
            "current": bio_preview or "No usable bio found on your social profile",
            "optimized_bio": bio if bio_pack_ok else (saved_bio if saved_bio and saved_bio != scrape_bio else None),
            "needs_email": not has_email,
            "email": extracted or "",
            "cta": "Optimize my bio",
            "cta_action": "rewrite_bio",
            "free": True,
            "value": "action",
            "evidence": extracted if extracted else None,
        }

    portfolio_count = max(
        post_count,
        len(_as_list(scrape.get("recent_posts"))),
        len(_as_list(scrape.get("recent_post_thumbnails"))),
    )

    kit_tagline = str(creator_profile.get("kit_tagline") or "").strip()
    rights_blob = " ".join(
        [
            bio,
            kit_tagline,
            str(creator_profile.get("bio") or ""),
        ]
    ).lower()
    whitelist_ok = bool(
        re.search(
            r"\b(whitelist|whitelisting|usage rights?|spark ads?|partnership ads?|"
            r"paid usage|60[\s-]?day usage|ad rights?)\b",
            rights_blob,
            re.I,
        )
    )
    # Brand-specific concept: stored flag / pitch proof when built later
    concept_ok = bool(creator_profile.get("has_brand_concept"))

    # Manager-style checklist: outcome titles + impact/time + brand-ops why.
    checklist = [
        bio_item,
        {
            "id": "kit",
            "title": "Publish your portfolio",
            "label": "Published portfolio (3+ posts)",
            "done": kit_ok,
            "impact": 4,
            "time_minutes": 5,
            "tip": "One click builds and publishes your portfolio from real posts so brands can open your link.",
            "why": (
                "When a brand likes your content, the next click is usually your portfolio. "
                "No published portfolio = friction — they move to the next creator."
            ),
            "current": (
                f"{post_count} portfolio posts · "
                f"{'published' if kit_status.get('is_published') else 'not published'}"
            ),
            "cta": "Build my portfolio" if not kit_ok else "Rebuild portfolio",
            "cta_action": "auto_kit",
            "free": True,
            "value": "action",
        },
        {
            "id": "shows_products",
            "title": "Prove you can sell a product",
            "label": "Shows products in real use",
            "done": shows_products,
            "impact": 5,
            "time_minutes": 20,
            "tip": "Film a 20-second first-impressions clip with a product you already own.",
            "why": (
                "Brands don't buy aesthetics — they buy confidence you'll show their product "
                "naturally. Application footage signals campaign-ready; flat lays don't."
            ),
            "why_blocks": None,  # filled below from live scrape evidence
            "fix_steps": (
                "1) Film a 20s first-impressions clip with a product you own. "
                "2) Show hands/application, not just the bottle. "
                "3) Post with a caption that names the product benefit."
            ),
            "current": (
                f"{product_hits} product-use captions found"
                if product_hits
                else "Mostly lifestyle / selfie content — little product-in-use proof"
            ),
            "missing": [
                "application / demo videos",
                "unboxings",
                "first impressions",
                "voiceover reviews",
            ],
            "suggested_shoot": (
                "Film a 20-second first impressions video with a product you already own."
            ),
            "cta": "Film a product demo",
            "cta_action": "pitch_pack",
            "free": False,
            "value": "coaching",
            "evidence": f"{product_hits} product-use captions" if product_hits else None,
        },
        {
            "id": "whitelisting",
            "title": "Add whitelisting + usage rights",
            "label": "Whitelisting / paid usage stated",
            "done": whitelist_ok,
            "impact": 5,
            "time_minutes": 10,
            "tip": "State whitelisting (Spark / Partnership Ads) and paid usage windows on your portfolio.",
            "why": (
                "Brands running Meta/TikTok ads need creators who allow whitelisting or explicit "
                "usage rights. Without this signal, they skip you for someone who's flagged it."
            ),
            "why_blocks": (
                "Brands running Meta/TikTok ads (most UGC buyers) need creators who allow "
                "whitelisting or explicit usage rights. Without this on your portfolio, they "
                "skip you for someone who's flagged it. This is the single most "
                "invisible-yet-important signal."
            ),
            "fix_steps": (
                "1) Add a line: 'Whitelisting / Spark Ads available'. "
                "2) State a paid usage window (e.g. 60-day organic + paid). "
                "3) Optional: list a whitelist rate so budget brands self-qualify."
            ),
            "current": (
                "Whitelisting / usage rights mentioned on profile"
                if whitelist_ok
                else "No whitelisting or usage-rights signal found on bio / portfolio"
            ),
            "cta": "See fix — $19/mo",
            "cta_action": "upgrade",
            "free": False,
            "value": "coaching",
            "evidence": "Whitelisting language found" if whitelist_ok else None,
        },
        {
            "id": "brand_concept",
            "title": 'Sketch a "for this brand" concept',
            "label": "Brand-specific concept clip",
            "done": concept_ok,
            "impact": 5,
            "time_minutes": 15,
            "tip": "Attach a 30s concept for a target brand — not just past work.",
            "why": (
                "Brands hire creators who show strategic thinking, not just execution. "
                "A concept clip attached to your pitch outperforms a cold portfolio link."
            ),
            "why_blocks": (
                "Brands hire creators who show strategic thinking, not just execution. "
                "A 30-second concept clip attached to your pitch outperforms "
                '"here\'s my portfolio" by 3–5× on reply rate. Most creators never do this.'
            ),
            "fix_steps": (
                "1) Pick one target brand from For You. "
                "2) Film a 20–30s 'here's how I'd feature your product' concept. "
                "3) Attach it (or a board) to your next pitch."
            ),
            "current": (
                "Brand-specific concept ready"
                if concept_ok
                else "No brand-specific concept attached yet — pitches look generic"
            ),
            "cta": "See fix — $19/mo",
            "cta_action": "upgrade",
            "free": False,
            "value": "coaching",
            "evidence": None,
        },
    ]

    # Peek-reveal: specific problem statements from live scrape
    for item in checklist:
        if item["id"] == "shows_products" and not item["done"]:
            n = portfolio_count or len(_as_list(scrape.get("recent_posts"))) or 0
            item["why_blocks"] = (
                f"Your last {max(n, 1)} posts show lifestyle / close-ups, but little product "
                f"IN USE. Brands hire creators who demonstrate — not just display."
                if product_hits < 2
                else (
                    f"Only {product_hits} product-use cues found. Brands want clearer "
                    "application / unboxing proof before gifting."
                )
            )
        elif item.get("why_blocks") is None and item.get("why"):
            item["why_blocks"] = item["why"]

    done_count = sum(1 for item in checklist if item["done"])
    n_boxes = max(len(checklist), 1)
    follower_count = int(
        scrape.get("follower_count")
        or scrape.get("followers")
        or creator_profile.get("followers_count")
        or 0
    )
    foundation = _foundation_hireability(
        bio=bio,
        portfolio_count=portfolio_count,
        post_count=post_count,
        product_hits=product_hits,
        shows_products=shows_products,
        mention_count=len(mention_handles),
        follower_count=follower_count,
        has_email=has_email,
    )
    checklist_pts = (done_count / n_boxes) * CHECKLIST_SCORE_MAX
    raw_score = int(round(min(100, foundation + checklist_pts)))

    # Honest projection: Free = foundation + finish Included fixes; Pro = 100.
    if is_pro:
        projected_raw = 100
    else:
        free_extra = sum(1 for item in checklist if not item["done"] and item.get("free"))
        projected_checklist = ((done_count + free_extra) / n_boxes) * CHECKLIST_SCORE_MAX
        projected_raw = int(round(min(100, foundation + projected_checklist)))

    if raw_score >= READY_SCORE_THRESHOLD:
        status = "ready"
    elif raw_score >= ALMOST_SCORE_THRESHOLD:
        status = "almost"
    elif raw_score >= NOT_YET_SCORE_THRESHOLD:
        status = "not_yet"
    else:
        status = "build_first"

    score = raw_score
    score_capped = False
    if not is_pro:
        if score > FREE_SCORE_CAP:
            score = FREE_SCORE_CAP
            score_capped = True
        if status == "ready":
            status = "almost"
            score_capped = True

    fixes = [item for item in checklist if not item["done"]]
    if not is_pro:
        # Critical empty-bio (and similar) always surface first
        fixes.sort(
            key=lambda f: (
                0 if f.get("critical") else 1,
                0 if f.get("free") else 1,
                -int(f.get("impact") or 0),
            )
        )
    else:
        fixes.sort(key=lambda f: (0 if f.get("critical") else 1, -int(f.get("impact") or 0)))

    free_open = [f for f in fixes if f.get("free")]
    pro_open = [f for f in fixes if not f.get("free")]
    free_runway_done = (not is_pro) and len(free_open) == 0 and any(
        c.get("free") and c.get("done") for c in checklist
    )

    # Full checklist done projection (what Pro coaching unlocks next)
    remaining_all = len(fixes)
    pro_projected = int(
        round(min(100, foundation + ((done_count + remaining_all) / n_boxes) * CHECKLIST_SCORE_MAX))
    )
    if free_runway_done:
        # Free ceiling hit — potential shown should be the Pro path, not a flat line
        projected_raw = pro_projected
        next_gain = max(0, projected_raw - raw_score)

    data_quality = _assessment_data_quality(scrape, captions, mention_handles, product_hits)

    next_gain = max(0, projected_raw - raw_score)
    top_gap = next((f for f in fixes if f.get("value") != "pro"), fixes[0] if fixes else None)
    # After free runway, the honest "top gap" is the first Pro peek — but CTA is pitch, not upgrade-only
    if free_runway_done and pro_open:
        top_gap = pro_open[0]
    points_per_box = int(round(CHECKLIST_SCORE_MAX / n_boxes))
    effort_minutes = sum(int(f.get("time_minutes") or 0) for f in fixes[:4])
    pro_gain = points_per_box * len(pro_open) if pro_open else max(0, pro_projected - score)

    # Score climb for left-column motivator
    climb_steps = []
    cumulative = 0
    if free_runway_done and pro_open:
        for i, item in enumerate(pro_open[:3]):
            pts = points_per_box
            cumulative += pts
            climb_steps.append(
                {
                    "n": i + 1,
                    "id": item["id"],
                    "title": item.get("title") or item.get("label"),
                    "points": pts,
                    "cumulative": cumulative,
                    "locked": True,
                }
            )
    else:
        for i, item in enumerate(fixes):
            if not is_pro and not item.get("free"):
                continue
            pts = points_per_box
            cumulative += pts
            climb_steps.append(
                {
                    "n": i + 1,
                    "id": item["id"],
                    "title": item.get("title") or item.get("label"),
                    "points": pts,
                    "cumulative": cumulative,
                    "locked": False,
                }
            )
            if len(climb_steps) >= 2:
                break
    week_gain = int(climb_steps[-1]["cumulative"]) if climb_steps else 0
    week_score = min(100 if is_pro or free_runway_done else FREE_SCORE_CAP, int(score) + week_gain)

    shoot_by_niche = {
        "skincare": "GRWM using one favorite product",
        "beauty": "GRWM using one favorite product",
        "fashion": "Outfit try-on with natural light + brand tag",
        "fitness": "20-second product-in-use demo mid-workout",
        "food": "First-bite review with voiceover",
    }
    niche_key = str(scrape.get("primary_niche") or "").lower()
    recommended_shoot = shoot_by_niche.get(niche_key) or (
        "First impressions with a product you already own"
    )

    climb_potential = projected_raw if not is_pro else 100
    if free_runway_done:
        climb_potential = pro_projected

    manager = {
        "greeting": (
            "Setup done. Here's the fork."
            if free_runway_done
            else "Here's what I'd focus on today."
        ),
        "path_line": (
            f"Free setup done. Pitch brands with your portfolio — or unlock +{pro_gain} with Pro coaching."
            if free_runway_done
            else "Here's your path to your first brand deal."
        ),
        "milestone": f"Campaign Ready ({CAMPAIGN_READY_THRESHOLD}% score)",
        "milestone_threshold": CAMPAIGN_READY_THRESHOLD,
        "milestone_points_needed": max(0, CAMPAIGN_READY_THRESHOLD - int(score)),
        "effort_minutes": effort_minutes or 15,
        "priority": None,
        "briefing": None,
        "free_runway_done": free_runway_done,
        "pro_projected_score": pro_projected,
        "pro_gain": pro_gain,
        "next_move": None,
        "score_climb": {
            "current": score,
            "potential": climb_potential,
            "steps": climb_steps,
            "week_gain": week_gain,
            "week_score": week_score,
            "marker_label": "free ceiling" if free_runway_done else "you're here",
            "mode": "pro_path" if free_runway_done else "free_path",
            "title": (
                "Next score climb (Pro)"
                if free_runway_done
                else "This week's projected score climb"
            ),
        },
    }
    if free_runway_done:
        manager["next_move"] = {
            "title": "What's next",
            "body": (
                "You've used the free setup wins. Don't stall here — pitch a matched brand "
                "with your new portfolio, or unlock the coaching path for the remaining gaps."
            ),
            "primary_cta": "Pitch a matched brand",
            "primary_action": "pitch_brands",
            "secondary_cta": f"Unlock +{pro_gain} coaching path",
            "secondary_action": "upgrade",
            "pro_gain": pro_gain,
        }
        manager["briefing"] = {
            "priority": "Pitch a brand with your new portfolio",
            "priority_note": (
                "Brands reply to outreach, not to unfinished setup. "
                "Use a Brand PR unlock on your strongest match."
            ),
            "recommended_shoot": (
                f"When you're ready for the next +{points_per_box} points: "
                f"{pro_open[0].get('suggested_shoot') or recommended_shoot}"
                if pro_open
                else recommended_shoot
            ),
            "shoot_minutes": int((pro_open[0].get("time_minutes") if pro_open else 18) or 18),
            "estimated_score_gain": points_per_box if pro_open else 0,
            "mode": "pitch_first",
        }
        manager["priority"] = {
            "id": "pitch_brands",
            "title": "Pitch a matched brand",
            "headline": "If you only do one thing today, pitch a brand with your new portfolio.",
            "why_short": "Setup without outreach is unfinished. Your portfolio is ready to send.",
            "cta": "Pitch a matched brand",
            "cta_action": "pitch_brands",
            "impact": 5,
            "time_minutes": 5,
            "score_gain": 0,
        }
    elif top_gap:
        gap_title = top_gap.get("title") or top_gap.get("label") or "top gap"
        is_critical = bool(top_gap.get("critical"))
        manager["priority"] = {
            "id": top_gap["id"],
            "title": gap_title,
            "headline": (
                f"Critical: {gap_title}"
                if is_critical
                else f"If you only fix one thing today, make it your {gap_title.lower()}."
            ),
            "why_short": (top_gap.get("why") or "")[:220],
            "cta": top_gap.get("cta") or "Start this fix",
            "cta_action": top_gap.get("cta_action") or "rewrite_bio",
            "impact": top_gap.get("impact") or 3,
            "time_minutes": top_gap.get("time_minutes") or 5,
            "score_gain": points_per_box,
            "critical": is_critical,
        }
        manager["briefing"] = {
            "priority": gap_title,
            "priority_note": (
                "Critical gap — fix this before pitching brands."
                if is_critical
                else "Highest ROI for brand replies today."
            ),
            "recommended_shoot": top_gap.get("suggested_shoot") or recommended_shoot,
            "shoot_minutes": int(top_gap.get("time_minutes") or 18),
            "estimated_score_gain": points_per_box,
        }

    return {
        "score": score,
        "raw_score": raw_score,
        "score_capped": score_capped,
        "free_score_cap": None if is_pro else FREE_SCORE_CAP,
        "status": status,
        "score_label": "Hireability score",
        "score_promise": (
            "how ready your profile is for PR gifting, UGC campaigns, and paid outreach"
        ),
        "projected_score": projected_raw,
        "projected_gain": next_gain,
        "top_gap_id": (top_gap or {}).get("id"),
        "boxes_checked": done_count,
        "boxes_total": len(checklist),
        "checklist": checklist,
        "fixes": fixes,
        "manager": manager,
        "manager_bar": build_manager_bar(
            score=score,
            checklist=checklist,
            manager=manager,
            is_pro=is_pro,
        ),
        "data_quality": data_quality,
    }


def build_manager_bar(
    *,
    score: int,
    checklist: Optional[List[Dict]] = None,
    manager: Optional[Dict] = None,
    is_pro: bool = False,
    days_since_score_change: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    Slim payload for the For You persistent manager score bar (routing Mech 1).
    Returns None when the bar should not render (Pro power-user / maxed score).
    """
    checklist = checklist or []
    manager = manager or {}
    score = int(score or 0)

    if is_pro and score >= 85:
        return None
    if score >= 100:
        return None

    open_items = [c for c in checklist if not c.get("done")]
    fixes_remaining = len(open_items)
    open_ids = {c.get("id") for c in open_items}
    setup_incomplete = ("bio" in open_ids) or ("kit" in open_ids)
    climb = manager.get("score_climb") or {}
    week_gain = int(climb.get("week_gain") or 0)

    if setup_incomplete:
        state = "setup_incomplete"
        est = 45 if ("bio" in open_ids and "kit" in open_ids) else 20
    elif score >= CAMPAIGN_READY_THRESHOLD and fixes_remaining == 0:
        state = "campaign_ready"
        est = 0
    elif days_since_score_change >= 7 and week_gain <= 0:
        state = "stalled"
        est = 0
    elif week_gain > 0:
        state = "climbed"
        est = 0
    else:
        state = "waiting"
        est = 0

    return {
        "state": state,
        "score": score,
        "score_delta_last_7d": max(0, week_gain),
        "fixes_remaining": fixes_remaining,
        "days_since_score_change": int(days_since_score_change or 0),
        "setup_incomplete_time_estimate_min": est,
        "campaign_ready": score >= CAMPAIGN_READY_THRESHOLD,
    }


_PRODUCT_USE_RE = re.compile(
    r"\b(apply|applying|swatch|swatching|trying|tried|review|reviewing|unbox|unboxing|"
    r"using|used|wore|wearing|demo|testing|tested|grwm|get ready|routine|tutorial|"
    r"first impression|haul)\b",
    re.I,
)
_MENTION_RE = re.compile(r"@([A-Za-z0-9._]{2,30})")


def _caption_product_hits(captions: List) -> int:
    hits = 0
    for c in captions:
        if isinstance(c, str) and _PRODUCT_USE_RE.search(c):
            hits += 1
    return hits


def _extract_caption_mentions(captions: List, handle: str = "") -> List[str]:
    own = (handle or "").lstrip("@").lower()
    found: List[str] = []
    seen = set()
    for c in captions:
        if not isinstance(c, str):
            continue
        for m in _MENTION_RE.findall(c):
            key = m.lower()
            if key == own or key in seen:
                continue
            # Skip very generic IG handles
            if key in {"instagram", "meta", "reels", "tiktok"}:
                continue
            seen.add(key)
            found.append(m)
    return found[:20]


def _bio_looks_professional(bio: str, has_email: bool) -> bool:
    if len(bio) < 35:
        return False
    lower = bio.lower()
    if lower.startswith("instagram creator"):
        return False
    has_contact = has_email or "@" in bio or "collab" in lower or "pr" in lower or "email" in lower
    has_niche_cue = bool(
        re.search(
            r"\b(ugc|creator|content|beauty|skincare|makeup|fashion|fitness|tech|ai|"
            r"lifestyle|food|travel|review|creator)\b",
            lower,
        )
    )
    return has_contact and (has_niche_cue or len(bio) >= 60)


def _assessment_data_quality(
    scrape: Dict, captions: List, mentions: List[str], product_hits: int
) -> Dict[str, Any]:
    posts = _as_list(scrape.get("recent_posts"))
    thumbs = _as_list(scrape.get("recent_post_thumbnails"))
    caption_len = sum(len(c) for c in captions if isinstance(c, str))
    eng = 0
    for p in posts:
        if not isinstance(p, dict):
            continue
        if int(p.get("likes") or 0) or int(p.get("views") or 0) or int(p.get("comments") or 0):
            eng += 1
    raw_bio = (scrape.get("raw_bio") or "").strip().lower()
    synthetic_bio = raw_bio.startswith("instagram creator")
    coverage = "strong"
    if synthetic_bio or len(thumbs) < 3 or caption_len < 80:
        coverage = "thin"
    elif eng < 2:
        coverage = "partial"
    note = "Assessment grounded in your recent posts, captions, and bio."
    if coverage == "thin":
        note = (
            "Profile data looks incomplete (few posts/captions or placeholder bio). "
            "Re-run social scrape after your posts load — score improves with real content."
        )
    elif coverage == "partial":
        note = "Assessment uses your live posts + bio. Refresh scrape after you post for a fresher score."
    return {
        "coverage": coverage,
        "posts_with_engagement": eng,
        "caption_chars": caption_len,
        "mentions_found": len(mentions),
        "product_caption_hits": product_hits,
        "incomplete_scrape": coverage == "thin",
        "note": note,
    }


def _call_gemini_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, json=payload, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def rewrite_bio(scrape: Dict, collab_email: Optional[str] = None) -> Dict[str, Any]:
    """Restructure the creator's existing scraped bio into a winning UGC/PR format."""
    niche = scrape.get("primary_niche") or "creator"
    themes = _as_list(scrape.get("content_themes"))[:6]
    followers = int(scrape.get("follower_count") or 0)
    handle = scrape.get("handle") or "creator"
    full_name = (scrape.get("full_name") or "").strip()
    raw_bio = (scrape.get("raw_bio") or "").strip()
    email = (
        (collab_email or "").strip()
        or (scrape.get("collab_email_extracted") or "").strip()
        or (_extract_collab_email(raw_bio) or "")
    )
    # Normalize mailto: prefix only (never use str.lstrip — it strips a char set)
    if email.lower().startswith("mailto:"):
        email = email[7:].strip()
    platform = (scrape.get("primary_platform") or "instagram").lower()
    aesthetic = scrape.get("aesthetic") or {}
    if isinstance(aesthetic, dict):
        vibe = aesthetic.get("overall_vibe") or aesthetic.get("vibe") or ""
    else:
        vibe = ""

    # Captions often carry identity when IG meta bio was empty / display-name only
    caption_bits = []
    for c in _as_list(scrape.get("recent_captions"))[:4]:
        text = re.sub(r"\s+", " ", str(c or "")).strip()
        if text:
            caption_bits.append(text[:120])
    for p in _as_list(scrape.get("recent_posts"))[:3]:
        if not isinstance(p, dict):
            continue
        text = re.sub(r"\s+", " ", str(p.get("caption") or "")).strip()
        if text and text not in caption_bits:
            caption_bits.append(text[:120])

    email_rule = (
        f"Collab email (MUST appear in bio exactly): {email}"
        if email
        else (
            "Collab email: none on file — write a niche-first bio with "
            "'UGC & PR → email@yoursite.com' placeholder ONLY if no email is provided; "
            "prefer leaving a clear PR CTA without inventing a real address."
        )
    )

    system = """You are NewCollab's UGC/PR bio editor. Brands and PR teams decide in ~3 seconds
whether a micro creator is pitchable. Your job: restructure THIS creator's CURRENT bio
into the highest-converting UGC/PR bio layout — using only their real details.

WHAT WINNING MICRO-UGC BIOS DO (gift-first + paid UGC / brand deals):
PR scanners look for, in order:
1) WHO — clear identity (name or role cue) + niche in plain English
2) PROOF — life/trust signal from THEIR bio (mum of 3, city/region, full-time UGC,
   specialty, audience). This beats vague "lifestyle creator" fluff.
3) OPEN FOR WORK — UGC / PR / collabs signal
4) EMAIL IN PLAIN TEXT — copy-pasteable. Never hide behind DM-only if an email exists.
Location matters for shipping/PR lists when present. Follower flex is optional and only
if they already state it — never invent.

Pick the ONE structure that best packs those 4 beats from THEIR material:
A) Scan-line (most common winner for micro UGC)
   "{identity/niche} · {1 proof from their bio} | UGC & PR → {email}"
B) Who + who-you-help + email
   "{identity}. {niche} UGC for {audience/brands they imply}. {email}"
C) First-person specialist
   "I create {what they actually make} · {niche} UGC | {email}"
D) Trust stack
   "{life/trust cue} · {niche} UGC | {region if present} | {email}"

Return JSON only:
{
  "bio": "string max 150 chars",
  "tagline": "string max 60 chars — kit headline from THEIR positioning",
  "template_used": "A|B|C|D",
  "why": "one short sentence TO THE CREATOR (you/your): what brand-scan beats you kept and why this layout wins replies. Never she/he/her/his."
}

HARD RULES — preserve the person, maximize brand-scan:
- CURRENT BIO is the only source of truth. Edit/reorder/compress — do not invent a new persona.
- Keep the highest-value details when compressing (priority): collab email → niche/UGC role →
  strongest trust cue (e.g. mum of 3, full-time) → location → age/emoji personality.
- If a collab email is provided OR in CURRENT BIO, it MUST appear exactly (prefer 💌/UGC email
  over MGMT). Never replace email with "DM".
- FORBIDDEN unless already in their bio/name/themes/captions: "product-in-use",
  "realistic product-in-use", "honest reviews", "GRWM", "soft launch", "Built our home",
  "for Home & Lifestyle brands" (unless they said that), or other stock filler.
- Do not invent brand names, follower counts, or credentials.
- Max 150 chars (hard). Prefer ~110–140. If over length, cut lowest-priority words first —
  email never gets cut.
- Max 1 emoji (prefer one they already use). No hashtags. No "link in bio".
- Tagline = short kit headline from THEIR positioning.
- "why" speaks to the creator ("you/your")."""

    user = f"""Handle: @{handle}
Display name: {full_name or 'n/a'}
Platform: {platform}
Followers: {followers}
Detected niche (hint only — prefer wording from their bio): {niche}
Content themes (hint only): {themes or ['n/a']}
Aesthetic/vibe (hint only): {vibe or 'n/a'}
{email_rule}

CURRENT BIO (rewrite FROM this — keep their highest-value brand-scan details):
{raw_bio or '(empty — use display name + niche + caption cues only)'}

Recent caption cues (optional flavor only, do not invent claims):
{chr(10).join(f'- {c}' for c in caption_bits) if caption_bits else '- (none)'}

Goal: the bio a PR manager would shortlist for a gift/UGC outreach list in 3 seconds.
"""
    result = _call_gemini_json(system, user)
    bio = (result.get("bio") or "").strip()
    tagline = (result.get("tagline") or "").strip()[:80]
    why = _why_to_second_person((result.get("why") or "").strip()[:240])
    template_used = (result.get("template_used") or "").strip().upper()[:1]
    if not bio:
        raise ValueError("Empty bio rewrite")
    final_email = email or _extract_collab_email(bio) or ""
    bio = _ensure_email_preserved_in_bio(bio, final_email)
    return {
        "bio": bio,
        "tagline": tagline,
        "why": why,
        "template_used": template_used if template_used in ("A", "B", "C", "D") else None,
        "email": final_email or _extract_collab_email(bio) or "",
        "needs_email": not bool(final_email or _extract_collab_email(bio)),
    }


def _ensure_email_preserved_in_bio(bio: str, email: str, max_len: int = 150) -> str:
    """Instagram bio max is 150 — never truncate the collab email (even mid-address)."""
    text = (bio or "").strip()
    email = (email or "").strip()
    # Never use str.lstrip("mailto:") — that strips a character *set* and eats
    # leading t/m/a/i/l/o from addresses like team@… → eam@…
    if email.lower().startswith("mailto:"):
        email = email[7:].strip()
    if not email:
        return text[:max_len]

    # Strip any existing emails / broken email tails so we can re-attach the full one
    cleaned = _COLLAB_EMAIL_RE.sub("", text)
    cleaned = re.sub(r"(?:UGC\s*&\s*PR\s*)[→\->:]+\s*$", "UGC & PR", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*[|·→\->:]+\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    if re.search(r"ugc\s*&\s*pr\s*$", cleaned, re.I):
        suffix = f" → {email}"
    else:
        suffix = f" | {email}"

    # Always keep the full email; only trim the body ahead of it
    room = max_len - len(suffix)
    if room < 12:
        return email if len(email) <= max_len else email[:max_len]

    body = cleaned[:room].rstrip(" |·-→")
    if len(body) + len(suffix) > max_len:
        body = body[: max(0, max_len - len(suffix))].rstrip(" |·-→")
    out = f"{body}{suffix}".strip()
    if email.lower() not in out.lower() or len(out) > max_len:
        body = cleaned[: max(0, max_len - len(suffix))].rstrip(" |·-→")
        out = f"{body}{suffix}".strip()
    # Never slice the full string — that cuts into the email at the end
    if len(out) > max_len:
        body = body[: max(0, max_len - len(suffix))].rstrip(" |·-→")
        out = f"{body}{suffix}".strip()
    return out


def _why_to_second_person(why: str) -> str:
    """Soft-fix bio 'why' copy speaks to the creator, not about them."""
    if not why:
        return why
    out = why
    replacements = (
        (r"\bher niche\b", "your niche"),
        (r"\bhis niche\b", "your niche"),
        (r"\btheir niche\b", "your niche"),
        (r"\bher UGC\b", "your UGC"),
        (r"\bhis UGC\b", "your UGC"),
        (r"\bher skill\b", "your skill"),
        (r"\bshowcases her\b", "showcases your"),
        (r"\bshowcases his\b", "showcases your"),
        (r"\bstates her\b", "states your"),
        (r"\bstates his\b", "states your"),
        (r"\bfor her\b", "for you"),
        (r"\bfor him\b", "for you"),
        (r"\bshe\b", "you"),
        (r"\bhe\b", "you"),
        (r"\bher\b", "your"),
        (r"\bhis\b", "your"),
    )
    for pat, repl in replacements:
        out = re.sub(pat, repl, out, flags=re.I)
    # Fix grammar artifacts from she→you ("you is" → "you are")
    out = re.sub(r"\byou is\b", "you are", out, flags=re.I)
    out = re.sub(r"\byour are\b", "you are", out, flags=re.I)
    return out[:200]


def generate_pitch_pack(scrape: Dict) -> Dict[str, Any]:
    """Weekly portfolio / shoot plan + UGC hooks (manager briefing)."""
    niche = scrape.get("primary_niche") or "beauty"
    themes = _as_list(scrape.get("content_themes"))[:8]
    handle = scrape.get("handle") or "creator"
    followers = int(scrape.get("follower_count") or 0)
    gaps = _as_list(scrape.get("content_gaps"))[:5]

    system = """You are the NewCollab talent manager.
Build THIS WEEK's portfolio shoot plan for an aspiring micro-creator.

Return JSON only:
{
  "weekly_plan": [
    {
      "day": "Monday",
      "format": "Product routine / GRWM / POV / Voiceover review / Trend remix",
      "brief": "one concrete shoot instruction (what to film)",
      "caption_starter": "I've been testing...",
      "minutes": 18
    }
  ],
  "ugc_hooks": ["hook 1", "hook 2", "hook 3", "hook 4"],
  "rate_card_tips": ["tip 1", "tip 2", "tip 3"],
  "focus": "one sentence: the portfolio gap this week closes"
}

Rules:
- Exactly 4 shoots across the week (Mon / Tue / Thu / Sat preferred)
- Each shoot must be filmable with products they already own
- Prefer product-in-use, talking-to-camera, B-roll, voiceover — not flat lays
- caption_starter under 12 words
- minutes between 12 and 25
- Sound like a manager assigning work, not generic advice"""

    user = f"""Handle: @{handle}
Followers: {followers}
Niche: {niche}
Themes: {themes}
Content gaps: {gaps}
"""
    result = _call_gemini_json(system, user)
    plan = result.get("weekly_plan") or []
    hooks = result.get("ugc_hooks") or []
    tips = result.get("rate_card_tips") or []
    focus = (result.get("focus") or "").strip()

    days = ["Monday", "Tuesday", "Thursday", "Saturday"]
    formats = ["Product routine", "POV try-on", "Voiceover review", "Trend remix"]
    normalized = []
    for i, day in enumerate(days):
        row = plan[i] if i < len(plan) and isinstance(plan[i], dict) else {}
        hook = hooks[i] if i < len(hooks) else ""
        normalized.append(
            {
                "day": row.get("day") or day,
                "format": row.get("format") or formats[i],
                "brief": (row.get("brief") or hook or f"Film a {formats[i].lower()} for {niche}").strip(),
                "caption_starter": (row.get("caption_starter") or "I've been testing…").strip()[:80],
                "minutes": int(row.get("minutes") or (15 + i * 2)),
            }
        )
    if not hooks:
        hooks = [p["brief"] for p in normalized]

    return {
        "weekly_plan": normalized,
        "ugc_hooks": hooks[:6],
        "rate_card_tips": tips[:4],
        "focus": focus or f"Build product-proof {niche} portfolio this week",
        "niche": niche,
        "pitches": result.get("pitches") or [],
    }


def ensure_recent_posts_column(conn) -> None:
    """Idempotent schema patch so scrape stats can persist."""
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(
            "ALTER TABLE creator_profile_data ADD COLUMN IF NOT EXISTS recent_posts JSONB DEFAULT '[]'::jsonb"
        )
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[pr-ready] ensure recent_posts column: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


def _posts_have_engagement(posts: List[Dict[str, Any]]) -> bool:
    return any(
        int(p.get("views") or 0) > 0
        or int(p.get("likes") or 0) > 0
        or int(p.get("comments") or 0) > 0
        for p in posts
    )


def _shortcode_from_url(url: Optional[str]) -> str:
    if not url:
        return ""
    m = re.search(r"/(?:p|reel|tv)/([^/?#]+)", str(url))
    return m.group(1) if m else ""


def _rehost_thumbnail_to_storage(image_url: str, creator_id: int) -> Optional[str]:
    """
    Download a social CDN thumb and upload to Supabase so kit images don't expire.
    Returns public URL or None on failure.
    """
    if not image_url or not str(image_url).startswith("http"):
        return None
    # Already our storage — keep as-is
    if "supabase" in image_url.lower() or "/storage/v1/object/public/" in image_url:
        return image_url

    supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_KEY") or ""
    bucket = os.getenv("SUPABASE_BUCKET", "creators")
    if not supabase_url or not supabase_key:
        return None

    try:
        from media_proxy_routes import is_social_cdn_url
        if not is_social_cdn_url(image_url):
            # Still try — some embeds return fbcdn hosts we allow elsewhere
            pass
    except Exception:
        pass

    try:
        resp = requests.get(
            image_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
            },
            timeout=14,
        )
        if resp.status_code != 200 or not resp.content:
            return None
        content_type = resp.headers.get("Content-Type") or "image/jpeg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"
        else:
            ext = "jpg"
            content_type = "image/jpeg"

        import uuid as _uuid

        filename = f"portfolio/{creator_id}/auto_{_uuid.uuid4().hex[:16]}.{ext}"
        upload = requests.post(
            f"{supabase_url}/storage/v1/object/{bucket}/{filename}",
            headers={
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            data=resp.content,
            timeout=20,
        )
        if upload.status_code not in (200, 201):
            print(f"[pr-ready] thumb rehost upload {upload.status_code}: {upload.text[:160]}")
            return None
        return f"{supabase_url}/storage/v1/object/public/{bucket}/{filename}"
    except Exception as e:
        print(f"[pr-ready] thumb rehost failed: {e}")
        return None


def rehost_kit_post_thumbnails(posts: List[Dict[str, Any]], creator_id: int) -> int:
    """Mutate posts in place: replace CDN thumbs with durable storage URLs."""
    updated = 0
    for p in posts:
        if not isinstance(p, dict):
            continue
        src = (p.get("thumbnail_url") or "").strip()
        if not src:
            continue
        hosted = _rehost_thumbnail_to_storage(src, creator_id)
        if hosted and hosted != src:
            p["thumbnail_url"] = hosted
            updated += 1
    if updated:
        print(f"[pr-ready] rehosted {updated} kit thumbnails to storage")
    return updated


def enrich_scrape_posts_via_embeds(scrape: Dict, *, max_posts: int = 9) -> int:
    """
    When imginn/APIs are blocked, pull likes/comments/views (+ fresh thumb)
    from public /p/{code}/embed/captioned/ for posts that have a shortcode/URL.
    Mutates scrape['recent_posts'] in place. Returns number of posts updated.
    """
    recent = _as_list(scrape.get("recent_posts"))
    if not recent:
        return 0

    try:
        from services.inhouse_social_scraper import _ig_fetch_embed_post_meta, _session
    except Exception as e:
        print(f"[pr-ready] embed enrich import failed: {e}")
        return 0

    session = _session()
    updated = 0
    for p in recent[:max_posts]:
        if not isinstance(p, dict):
            continue
        has_stats = (
            int(p.get("likes") or 0) > 0
            or int(p.get("comments") or 0) > 0
            or int(p.get("views") or 0) > 0
        )
        has_thumb = bool(p.get("thumbnail_url"))
        thumb_durable = "supabase" in str(p.get("thumbnail_url") or "").lower()
        code = _shortcode_from_url(p.get("post_url")) or str(
            p.get("shortCode") or p.get("shortcode") or ""
        ).strip()
        if not code:
            continue
        # Skip only when we already have stats + a durable thumb
        if has_stats and has_thumb and thumb_durable:
            continue
        try:
            extra = _ig_fetch_embed_post_meta(session, code)
        except Exception as e:
            print(f"[pr-ready] embed enrich /{code}: {e}")
            continue
        if not extra:
            continue
        changed = False
        if extra.get("likesCount") and not int(p.get("likes") or 0):
            p["likes"] = int(extra["likesCount"])
            changed = True
        if extra.get("commentsCount") and not int(p.get("comments") or 0):
            p["comments"] = int(extra["commentsCount"])
            changed = True
        if extra.get("videoViewCount") and not int(p.get("views") or 0):
            p["views"] = int(extra["videoViewCount"])
            changed = True
        # Always refresh thumb when embed returns one — CDN URLs expire quickly
        if extra.get("displayUrl"):
            p["thumbnail_url"] = extra["displayUrl"]
            changed = True
        if not p.get("post_url"):
            p["post_url"] = f"https://www.instagram.com/reel/{code}/"
            changed = True
        if changed:
            updated += 1

    if updated:
        scrape["recent_posts"] = recent
        # Keep thumbnail list in sync for UI previews
        thumbs = [p.get("thumbnail_url") for p in recent if p.get("thumbnail_url")]
        if thumbs:
            scrape["recent_post_thumbnails"] = thumbs
        print(f"[pr-ready] embed-enriched engagement/thumbs on {updated} posts")
    return updated


def recover_engagement_from_user_feed(scrape: Dict, *, limit: int = 9) -> int:
    """
    One mobile user_feed pass when onboarding saved thumbs but dropped per-post stats.
    Mutates scrape['recent_posts'] (+ thumbnails). Returns posts recovered.
    """
    platform = (scrape.get("primary_platform") or "instagram").lower()
    handle = (scrape.get("handle") or "").lstrip("@").strip()
    if platform != "instagram" or not handle:
        return 0
    try:
        from services.inhouse_social_scraper import (
            _ig_lookup_user_id,
            _ig_posts_from_user_feed,
            _ig_warm_session,
            _session,
        )
    except Exception as e:
        print(f"[pr-ready] feed recover import failed: {e}")
        return 0

    try:
        session = _session()
        _ig_warm_session(session, handle)
        pk = _ig_lookup_user_id(handle)
        if not pk:
            print(f"[pr-ready] feed recover: no pk for @{handle}")
            return 0
        feed_posts = _ig_posts_from_user_feed(session, pk, handle, limit)
        if not feed_posts:
            return 0
        recent: List[Dict[str, Any]] = []
        for p in feed_posts:
            if not isinstance(p, dict):
                continue
            code = (p.get("shortCode") or "").strip()
            thumb = p.get("displayUrl") or ""
            if not thumb and not code:
                continue
            recent.append(
                {
                    "thumbnail_url": thumb,
                    "post_url": f"https://www.instagram.com/p/{code}/" if code else None,
                    "shortCode": code,
                    "likes": int(p.get("likesCount") or 0),
                    "comments": int(p.get("commentsCount") or 0),
                    "views": int(p.get("videoViewCount") or 0),
                    "shares": 0,
                    "saves": 0,
                    "caption": (p.get("caption") or "")[:500],
                    "timestamp": p.get("timestamp") or "",
                }
            )
        if not recent:
            return 0
        scrape["recent_posts"] = recent
        thumbs = [p["thumbnail_url"] for p in recent if p.get("thumbnail_url")]
        if thumbs:
            scrape["recent_post_thumbnails"] = thumbs
        print(f"[pr-ready] recovered engagement from user_feed ({len(recent)} posts)")
        return len(recent)
    except Exception as e:
        print(f"[pr-ready] feed recover failed: {e}")
        return 0


def merge_previous_post_engagement(scrape: Dict, previous: Optional[Dict]) -> int:
    """Copy likes/views/comments from a prior scrape when the live pass returned zeros."""
    if not previous:
        return 0
    prev_posts = _as_list(previous.get("recent_posts"))
    if not prev_posts:
        return 0
    by_url = {
        (p.get("post_url") or ""): p
        for p in prev_posts
        if isinstance(p, dict) and p.get("post_url")
    }
    by_thumb = {
        (p.get("thumbnail_url") or ""): p
        for p in prev_posts
        if isinstance(p, dict) and p.get("thumbnail_url")
    }
    recent = _as_list(scrape.get("recent_posts"))
    merged = 0
    for p in recent:
        if not isinstance(p, dict):
            continue
        if int(p.get("likes") or 0) or int(p.get("views") or 0) or int(p.get("comments") or 0):
            continue
        src = by_url.get(p.get("post_url") or "") or by_thumb.get(p.get("thumbnail_url") or "")
        if not src:
            continue
        for key in ("likes", "comments", "views", "shares", "saves"):
            if int(src.get(key) or 0) > 0 and not int(p.get(key) or 0):
                p[key] = int(src[key])
        if not p.get("post_url") and src.get("post_url"):
            p["post_url"] = src["post_url"]
        if int(p.get("likes") or 0) or int(p.get("views") or 0) or int(p.get("comments") or 0):
            merged += 1
    if merged:
        scrape["recent_posts"] = recent
        print(f"[pr-ready] restored engagement on {merged} posts from last scrape")
    return merged


def _recent_posts_for_kit(scrape: Dict) -> List[Dict[str, Any]]:
    """Normalize scrape recent_posts (with stats) or fall back to thumbnails only."""
    recent = _as_list(scrape.get("recent_posts"))
    posts: List[Dict[str, Any]] = []
    for p in recent[:9]:
        if not isinstance(p, dict):
            continue
        thumb = p.get("thumbnail_url") or p.get("thumb") or ""
        if not thumb:
            continue
        posts.append(
            {
                "thumbnail_url": thumb,
                "post_url": p.get("post_url") or None,
                "views": int(p.get("views") or 0),
                "likes": int(p.get("likes") or 0),
                "comments": int(p.get("comments") or 0),
                "shares": int(p.get("shares") or 0),
                "saves": int(p.get("saves") or 0),
            }
        )
    if posts:
        return posts
    for thumb in _as_list(scrape.get("recent_post_thumbnails"))[:9]:
        if thumb:
            posts.append(
                {
                    "thumbnail_url": thumb,
                    "post_url": None,
                    "views": 0,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0,
                }
            )
    return posts


FREE_KIT_POST_LIMIT = 3
PRO_AUTO_KIT_POST_LIMIT = 9


def _post_engagement_score(post: Dict[str, Any]) -> int:
    return (
        int(post.get("views") or 0)
        + int(post.get("likes") or 0)
        + int(post.get("comments") or 0) * 5
        + int(post.get("shares") or 0) * 3
        + int(post.get("saves") or 0) * 3
    )


def auto_fill_kit_from_scrape(
    conn,
    creator_id: int,
    scrape: Dict,
    *,
    publish: bool = False,
    rewritten_bio: Optional[str] = None,
    tagline: Optional[str] = None,
    max_posts: Optional[int] = None,
    is_pro: bool = False,
) -> Dict[str, Any]:
    """
    Fill portfolio_posts + creators kit fields from scrape posts + stats.

    Free: top N posts by engagement (default 3); can auto-publish when requested
    (AI Manager "Build my portfolio" path).
    Pro: up to 9 posts; can publish when requested.
    """
    from psycopg2.extras import RealDictCursor

    ensure_recent_posts_column(conn)

    limit = max_posts
    if limit is None:
        limit = PRO_AUTO_KIT_POST_LIMIT if is_pro else FREE_KIT_POST_LIMIT
    limit = max(1, min(int(limit), PRO_AUTO_KIT_POST_LIMIT))

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    platform = (scrape.get("primary_platform") or "instagram").lower()
    if platform not in ("instagram", "tiktok", "youtube"):
        platform = "instagram"
    post_type = "tiktok" if platform == "tiktok" else "reel"

    # Best-performing posts first — draft kit should look valuable, not random
    all_posts = _recent_posts_for_kit(scrape)
    all_posts = sorted(all_posts, key=_post_engagement_score, reverse=True)
    posts = all_posts[:limit]
    # Durable thumbs so public kit doesn't show black boxes after CDN expiry
    rehost_kit_post_thumbnails(posts, creator_id)
    thumbs = [p["thumbnail_url"] for p in posts]

    cursor.execute(
        """
        SELECT id, thumbnail_url, post_url, views, likes, comments, shares, saves, display_order
        FROM portfolio_posts WHERE creator_id = %s
        ORDER BY display_order ASC NULLS LAST, id ASC
        """,
        (creator_id,),
    )
    existing_list = list(cursor.fetchall())
    by_thumb = {row["thumbnail_url"]: row for row in existing_list if row.get("thumbnail_url")}
    by_url = {row["post_url"]: row for row in existing_list if row.get("post_url")}

    inserted = 0
    updated_stats = 0
    trimmed = 0

    def _apply_stats(row_id: int, post: Dict[str, Any], thumb: str) -> None:
        nonlocal updated_stats
        cursor.execute(
            """
            UPDATE portfolio_posts SET
                views = %s, likes = %s, comments = %s, shares = %s, saves = %s,
                thumbnail_url = COALESCE(NULLIF(%s, ''), thumbnail_url),
                post_url = COALESCE(NULLIF(%s, ''), post_url),
                collab_type = COALESCE(collab_type, 'organic')
            WHERE id = %s
            """,
            (
                post["views"],
                post["likes"],
                post["comments"],
                post["shares"],
                post["saves"],
                thumb,
                post.get("post_url") or "",
                row_id,
            ),
        )
        updated_stats += 1

    matched_ids = set()
    # Prefer positional update when kit already has posts (CDN thumbs rotate every scrape)
    if existing_list and posts:
        for idx, post in enumerate(posts):
            if idx >= len(existing_list):
                break
            row = existing_list[idx]
            matched_ids.add(row["id"])
            _apply_stats(row["id"], post, post["thumbnail_url"])

    for idx, post in enumerate(posts):
        if idx < len(existing_list) and existing_list[idx]["id"] in matched_ids:
            continue

        # Free: stop inserting once we already have the cap
        cursor.execute(
            "SELECT COUNT(*) AS c FROM portfolio_posts WHERE creator_id = %s",
            (creator_id,),
        )
        current_count = int(cursor.fetchone()["c"] or 0)
        if not is_pro and current_count >= FREE_KIT_POST_LIMIT and not matched_ids:
            break

        thumb = post["thumbnail_url"]
        post_url = post.get("post_url") or ""
        row = None
        if post_url and post_url in by_url and by_url[post_url]["id"] not in matched_ids:
            row = by_url[post_url]
        elif thumb in by_thumb and by_thumb[thumb].get("id") not in matched_ids:
            row = by_thumb[thumb]

        if row:
            matched_ids.add(row["id"])
            if post["views"] or post["likes"] or post["comments"] or post["shares"] or post["saves"]:
                _apply_stats(row["id"], post, thumb)
            continue

        if not is_pro and current_count >= FREE_KIT_POST_LIMIT:
            continue

        cursor.execute(
            """
            INSERT INTO portfolio_posts (
                creator_id, post_url, platform, post_type, brand_name,
                collab_type, views, likes, comments, shares, saves,
                thumbnail_url, display_order, is_featured
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                creator_id,
                post.get("post_url"),
                platform,
                post_type,
                None,
                "organic",
                post["views"],
                post["likes"],
                post["comments"],
                post["shares"],
                post["saves"],
                thumb,
                idx,
                idx < 3,
            ),
        )
        new_id = cursor.fetchone()["id"]
        inserted += 1
        matched_ids.add(new_id)
        by_thumb[thumb] = {"id": new_id, "thumbnail_url": thumb, "post_url": post.get("post_url")}

    # Free tier: enforce max 3 posts — keep highest-engagement rows
    if not is_pro:
        cursor.execute(
            """
            SELECT id, COALESCE(views,0) AS views, COALESCE(likes,0) AS likes,
                   COALESCE(comments,0) AS comments, COALESCE(shares,0) AS shares,
                   COALESCE(saves,0) AS saves
            FROM portfolio_posts WHERE creator_id = %s
            """,
            (creator_id,),
        )
        all_rows = list(cursor.fetchall())
        if len(all_rows) > FREE_KIT_POST_LIMIT:
            ranked = sorted(
                all_rows,
                key=lambda r: (
                    int(r["views"] or 0)
                    + int(r["likes"] or 0)
                    + int(r["comments"] or 0) * 5
                    + int(r["shares"] or 0) * 3
                    + int(r["saves"] or 0) * 3
                ),
                reverse=True,
            )
            keep_ids = {r["id"] for r in ranked[:FREE_KIT_POST_LIMIT]}
            drop_ids = [r["id"] for r in ranked if r["id"] not in keep_ids]
            if drop_ids:
                cursor.execute(
                    "DELETE FROM portfolio_posts WHERE creator_id = %s AND id = ANY(%s)",
                    (creator_id, drop_ids),
                )
                trimmed = len(drop_ids)
            for order, rid in enumerate(r["id"] for r in ranked[:FREE_KIT_POST_LIMIT]):
                cursor.execute(
                    "UPDATE portfolio_posts SET display_order = %s, is_featured = %s WHERE id = %s",
                    (order, order < 3, rid),
                )

    bio = rewritten_bio or scrape.get("raw_bio") or ""
    kit_tagline = tagline or (bio[:60] if bio else None)
    followers = int(scrape.get("follower_count") or 0)

    # Update creator profile fields used by kit / public pages
    cursor.execute(
        """
        UPDATE creators SET
            bio = COALESCE(NULLIF(%s, ''), bio),
            kit_tagline = COALESCE(%s, kit_tagline),
            followers_count = CASE WHEN %s > 0 THEN %s ELSE followers_count END,
            niche = COALESCE(NULLIF(%s, ''), niche),
            kit_published = CASE WHEN %s THEN TRUE ELSE kit_published END
        WHERE id = %s
        """,
        (
            bio[:500] if bio else "",
            kit_tagline,
            followers,
            followers,
            scrape.get("primary_niche"),
            bool(publish),
            creator_id,
        ),
    )

    cursor.execute(
        "SELECT COUNT(*) AS post_count FROM portfolio_posts WHERE creator_id = %s",
        (creator_id,),
    )
    post_count = int(cursor.fetchone()["post_count"] or 0)

    # Auto-publish when we now meet the 3-post bar and caller asked to publish
    published = False
    if publish and post_count >= 3:
        cursor.execute(
            "UPDATE creators SET kit_published = TRUE WHERE id = %s",
            (creator_id,),
        )
        published = True

    conn.commit()
    cursor.close()

    return {
        "inserted_posts": inserted,
        "updated_stats": updated_stats,
        "trimmed_posts": trimmed,
        "post_count": post_count,
        "platform": platform,
        "published": published,
        "max_posts": limit,
        "is_pro": is_pro,
        "thumbnails": proxy_media_urls(thumbs[:6]),
    }


# Success story: real creators with a visible OUTCOME only.
# Identity without a win (e.g. 32 followers, no brands) actively hurts paywall trust.
_MIN_STORY_FOLLOWERS_SHOW = 100
_MIN_STORY_FOLLOWERS_SOLO = 1000  # kit-only proof without pipeline wins


def _format_follower_count(n: Optional[int]) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "K"
    return str(n)


# Onboarding region IDs → short paywall labels (CreatorOnboarding REGIONS).
_REGION_ID_LABELS = {
    "US": "US",
    "USA": "US",
    "UK": "UK",
    "GB": "UK",
    "Canada": "Canada",
    "CA": "Canada",
    "Europe": "Europe",
    "EU": "Europe",
    "LATAM": "LATAM",
    "Latin America": "LATAM",
    "MENA": "MENA",
    "Middle East & Africa": "MENA",
    "Middle East": "MENA",
    "Asia": "Asia",
    "Asia Pacific": "Asia",
    "APAC": "Asia",
    "North America": "North America",
    "South America": "LATAM",
    "Africa": "Africa",
    "Oceania": "Oceania",
}

_SKIP_REGION_LABELS = {
    "global",
    "worldwide",
    "international",
    "anywhere",
}

_ISO_COUNTRY = {
    "IN": "India",
    "US": "US",
    "GB": "UK",
    "UK": "UK",
    "AU": "Australia",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "BR": "Brazil",
    "MX": "Mexico",
    "NG": "Nigeria",
    "ZA": "South Africa",
    "PH": "Philippines",
    "ID": "Indonesia",
    "SG": "Singapore",
    "AE": "UAE",
    "IE": "Ireland",
    "NZ": "New Zealand",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "PL": "Poland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "PT": "Portugal",
    "JP": "Japan",
    "KR": "South Korea",
}


def _parse_regions_list(regions: Any) -> List[Any]:
    regions_raw = regions
    if isinstance(regions_raw, str):
        try:
            regions_raw = json.loads(regions_raw)
        except Exception:
            # Comma-separated fallback
            parts = [p.strip() for p in regions_raw.split(",") if p.strip()]
            return parts
    if isinstance(regions_raw, list):
        return regions_raw
    return []


def _normalize_region_token(raw: str) -> str:
    """Map country / onboarding region IDs to a short display label."""
    if not raw:
        return ""
    cleaned = " ".join(str(raw).strip().split())
    if not cleaned:
        return ""
    low = cleaned.lower()
    if low in _SKIP_REGION_LABELS:
        return ""
    # Exact onboarding / ISO keys
    if cleaned in _REGION_ID_LABELS:
        return _REGION_ID_LABELS[cleaned]
    if cleaned.upper() in _REGION_ID_LABELS:
        return _REGION_ID_LABELS[cleaned.upper()]
    # Case-insensitive region map
    for key, label in _REGION_ID_LABELS.items():
        if key.lower() == low:
            return label
    if len(cleaned) <= 3 and cleaned.upper() in _ISO_COUNTRY:
        return _ISO_COUNTRY[cleaned.upper()]
    if cleaned.upper() in ("UK", "UAE"):
        return cleaned.upper()
    if cleaned.upper() == "USA":
        return "US"
    return cleaned.title()


def _region_label(country: Any, regions: Any) -> str:
    """
    Prefer users.country when present; else first creators.regions entry.
    Supports onboarding IDs (US, Europe, LATAM, …) and ISO country codes.
    """
    if country and str(country).strip():
        label = _normalize_region_token(str(country).strip())
        if label:
            return label

    for item in _parse_regions_list(regions):
        if isinstance(item, dict):
            raw = str(
                item.get("country")
                or item.get("id")
                or item.get("value")
                or item.get("name")
                or item.get("label")
                or ""
            ).strip()
        else:
            raw = str(item).strip()
        label = _normalize_region_token(raw)
        if label:
            return label
    return ""


def _joined_months_ago(created: Any) -> Optional[int]:
    if created is None:
        return None
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if getattr(created, "tzinfo", None) is None:
            created_aware = created.replace(tzinfo=timezone.utc)
        else:
            created_aware = created
        days = max(1, (now - created_aware).days)
        return max(1, days // 30)
    except Exception:
        return None


def _story_score_climb(
    conn,
    creator_id: int,
    creator_row: Dict[str, Any],
) -> tuple:
    """
    Real score trajectory for paywall proof.
    current = live hireability; starting = same scrape with free setup undone
    (bio email / published kit), so the climb is grounded in this creator's data —
    never a hardcoded 24→78.
    Returns (starting, current) or (None, None) if climb isn't credible.
    """
    try:
        from psycopg2.extras import RealDictCursor
        from pr_crm_routes import check_media_kit_complete
        from services.creator_profile_scraper import CreatorProfileScraper

        user_id = creator_row.get("user_id")
        if not user_id:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT user_id FROM creators WHERE id = %s", (creator_id,))
            row = cur.fetchone()
            cur.close()
            user_id = (row or {}).get("user_id")
        if not user_id:
            return None, None

        scrape = CreatorProfileScraper(conn).get_creator_profile(int(user_id))
        if not scrape:
            return None, None

        _c, _m, kit_status = check_media_kit_complete(creator_id)

        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute(
                """
                SELECT id, user_id, bio, niche, kit_tagline, kit_published, kit_slug,
                       social_handle, followers_count, subscription_tier
                FROM creators WHERE id = %s
                """,
                (creator_id,),
            )
            profile = dict(cur.fetchone() or creator_row)
        finally:
            cur.close()

        tier = (profile.get("subscription_tier") or "free")
        is_pro = tier in ("pro", "elite")
        current_report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=profile.get("bio"),
            creator_profile=profile,
        )
        current = int(current_report.get("score") or 0)
        if current < 65:
            return None, None

        baseline_kit = dict(kit_status or {})
        baseline_kit["is_published"] = False
        baseline_kit["post_count"] = min(int(baseline_kit.get("post_count") or 0), 2)
        baseline_bio = (profile.get("bio") or scrape.get("raw_bio") or "").strip()
        import re as _re

        baseline_bio = _re.sub(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            "",
            baseline_bio,
        ).strip()
        baseline_profile = dict(profile)
        baseline_profile["bio"] = baseline_bio
        start_report = compute_pr_ready_score(
            scrape,
            baseline_kit,
            is_pro=False,
            creator_bio=baseline_bio,
            creator_profile=baseline_profile,
        )
        starting = int(start_report.get("score") or 0)
        if starting >= current or (current - starting) < 15:
            return None, None
        return starting, current
    except Exception as err:
        print(f"[pr-ready] story score climb skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None, None


def fetch_success_story(conn, *, exclude_creator_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Paywall social proof — only return a creator with a real outcome.
    Prefer country-specific + ≤4 months + score climb when available,
    but never hide a real win just because country/timeline/climb is imperfect.
    """
    if conn is None:
        return None
    from psycopg2.extras import RealDictCursor

    cursor = None
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        params: List[Any] = []
        exclude_sql = ""
        if exclude_creator_id:
            exclude_sql = "AND c.id <> %s"
            params.append(int(exclude_creator_id))

        # Prefer creators with brand wins; fall back to strong published portfolios.
        # Followers: coalesce creators + social verification + scrape profile.
        cursor.execute(
            f"""
            SELECT c.id, c.user_id, c.username, c.social_handle, c.image_profile,
                   c.followers_count, c.social_follower_count, c.regions,
                   c.kit_published, c.created_at,
                   u.country,
                   cpd.follower_count AS scrape_followers,
                   COALESCE(
                       NULLIF(c.followers_count, 0),
                       NULLIF(c.social_follower_count, 0),
                       NULLIF(cpd.follower_count, 0),
                       0
                   )::int AS resolved_followers,
                   COALESCE(w.win_count, 0) AS win_count
            FROM creators c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN creator_profile_data cpd ON cpd.user_id = c.user_id
            LEFT JOIN (
                SELECT creator_id, COUNT(*)::int AS win_count
                FROM creator_pipeline
                WHERE stage IN ('success', 'responded')
                GROUP BY creator_id
            ) w ON w.creator_id = c.id
            WHERE COALESCE(NULLIF(TRIM(c.image_profile), ''), NULL) IS NOT NULL
              AND (
                    COALESCE(NULLIF(TRIM(c.social_handle), ''), NULL) IS NOT NULL
                 OR COALESCE(NULLIF(TRIM(c.username), ''), NULL) IS NOT NULL
              )
              AND (
                    COALESCE(w.win_count, 0) >= 1
                    OR (
                        COALESCE(c.kit_published, FALSE) = TRUE
                        AND COALESCE(
                            NULLIF(c.followers_count, 0),
                            NULLIF(c.social_follower_count, 0),
                            NULLIF(cpd.follower_count, 0),
                            0
                        ) >= %s
                    )
              )
              {exclude_sql}
            ORDER BY COALESCE(w.win_count, 0) DESC,
                     COALESCE(
                         NULLIF(c.followers_count, 0),
                         NULLIF(c.social_follower_count, 0),
                         NULLIF(cpd.follower_count, 0),
                         0
                     ) DESC,
                     RANDOM()
            LIMIT 16
            """,
            [ _MIN_STORY_FOLLOWERS_SOLO ] + params,
        )
        candidates = cursor.fetchall() or []
        if not candidates:
            return None

        scored_candidates: List[Dict[str, Any]] = []
        for row in candidates:
            handle = (row.get("social_handle") or row.get("username") or "").lstrip("@").strip()
            if not handle:
                continue

            # Country is preferred; continents become blank (not a hard reject).
            country = _region_label(row.get("country"), row.get("regions"))
            months = _joined_months_ago(row.get("created_at"))

            brands: List[str] = []
            try:
                cursor.execute(
                    """
                    SELECT DISTINCT pb.brand_name AS brand_name
                    FROM creator_pipeline cp
                    JOIN pr_brands pb ON pb.id = cp.brand_id
                    WHERE cp.creator_id = %s
                      AND cp.stage IN ('success', 'responded')
                      AND NULLIF(TRIM(pb.brand_name), '') IS NOT NULL
                    ORDER BY brand_name
                    LIMIT 3
                    """,
                    (row["id"],),
                )
                brands = [
                    str(r["brand_name"]).strip()
                    for r in (cursor.fetchall() or [])
                    if r.get("brand_name")
                ]
            except Exception as brand_err:
                print(f"[pr-ready] success story brands skipped: {brand_err}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                brands = []

            followers_n = int(
                row.get("resolved_followers")
                or row.get("followers_count")
                or row.get("social_follower_count")
                or row.get("scrape_followers")
                or 0
            )
            kit_ok = bool(row.get("kit_published"))
            has_outcome = bool(brands) or (kit_ok and followers_n >= _MIN_STORY_FOLLOWERS_SOLO)
            if not has_outcome:
                continue
            if not brands and followers_n < _MIN_STORY_FOLLOWERS_SOLO:
                continue

            scored_candidates.append(
                {
                    "row": row,
                    "handle": handle,
                    "country": country,
                    "months": months,
                    "brands": brands,
                    "starting_score": None,
                    "current_score": None,
                    "has_climb": False,
                    "followers_n": followers_n,
                }
            )

        if not scored_candidates:
            return None

        # Prefer: country + ≤4 months + wins, then any real outcome
        def _rank(c: Dict[str, Any]) -> tuple:
            months = c.get("months") or 99
            return (
                0 if c.get("country") else 1,
                0 if months <= 4 else 1,
                months,
                -int(c["row"].get("win_count") or 0),
                -int(c.get("followers_n") or 0),
            )

        scored_candidates.sort(key=_rank)

        # Score climb only for top shortlist (expensive; never required to show card)
        for cand in scored_candidates[:3]:
            starting_score, current_score = _story_score_climb(
                conn, int(cand["row"]["id"]), dict(cand["row"])
            )
            if starting_score is not None and current_score is not None:
                cand["starting_score"] = starting_score
                cand["current_score"] = current_score
                cand["has_climb"] = True

        # Re-rank: climb + country first when available
        scored_candidates.sort(
            key=lambda c: (
                0 if c.get("has_climb") else 1,
                0 if c.get("country") else 1,
                0 if (c.get("months") or 99) <= 4 else 1,
                c.get("months") or 99,
                -int(c["row"].get("win_count") or 0),
            )
        )

        best = scored_candidates[0]
        chosen = best["row"]
        brands = best["brands"]
        handle = best["handle"]
        country = best["country"]
        months = best["months"]
        followers_n = best["followers_n"]
        starting_score = best["starting_score"]
        current_score = best["current_score"]

        followers_label = (
            _format_follower_count(followers_n)
            if followers_n >= _MIN_STORY_FOLLOWERS_SHOW
            else ""
        )
        win_count = int(chosen.get("win_count") or len(brands) or 0)

        joined_label = ""
        if months is not None:
            if months <= 1:
                joined_label = "joined 1 month ago"
            else:
                joined_label = f"joined {months} months ago"
        else:
            created = chosen.get("created_at")
            if created is not None:
                try:
                    from datetime import datetime, timezone

                    now = datetime.now(timezone.utc)
                    if getattr(created, "tzinfo", None) is None:
                        created_aware = created.replace(tzinfo=timezone.utc)
                    else:
                        created_aware = created
                    days = max(1, (now - created_aware).days)
                    if days < 45:
                        weeks = max(1, days // 7)
                        joined_label = f"joined {weeks} week{'s' if weeks != 1 else ''} ago"
                except Exception:
                    joined_label = ""

        outcome_line = ""
        if brands:
            if win_count >= 2:
                outcome_line = f"Landed {win_count} brand replies · {', '.join(brands[:2])}"
            else:
                outcome_line = f"Landed {', '.join(brands)}"
        elif chosen.get("kit_published"):
            outcome_line = "Published portfolio + pitching brands on Newcollab"

        return {
            "creator_id": chosen["id"],
            "name": f"@{handle}",
            "handle": handle,
            "country": country,
            "joined_label": joined_label,
            "avatar_url": chosen.get("image_profile") or "",
            "avatar_emoji": None,
            "brands_landed": brands,
            "outcome_line": outcome_line,
            "follower_current": followers_label,
            "follower_start": None,
            "starting_score": starting_score,
            "current_score": current_score,
            "kit_published": bool(chosen.get("kit_published")),
            "quote": None,
            "is_real": True,
            "has_outcome": True,
        }
    except Exception as e:
        print(f"[pr-ready] fetch_success_story error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if cursor:
            cursor.close()


def pick_success_story(seed: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """No fictional fallback — hide the card when DB has no outcome story."""
    return None


def score_brand_readiness(
    scrape: Optional[Dict],
    brand: Dict[str, Any],
    *,
    checklist: Optional[List[Dict]] = None,
    creator_bio: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Per-brand hireability peek (Fix 6).
    Universal gaps + category-specific content needs — no invented benchmarks.
    """
    scrape = scrape or {}
    brand = brand or {}
    name = brand.get("name") or brand.get("brand_name") or "Brand"
    category = (brand.get("category") or "").strip().lower()
    base = int(brand.get("match_score") or brand.get("score") or 50)

    checklist = checklist or []
    open_ids = {c["id"] for c in checklist if not c.get("done")}

    niche = str(scrape.get("primary_niche") or "").lower()
    themes = " ".join(str(x).lower() for x in _as_list(scrape.get("content_themes")))
    captions = " ".join(
        str(c).lower()
        for c in (_as_list(scrape.get("recent_captions")) or [])[:8]
        if isinstance(c, str)
    )
    blob = f"{niche} {themes} {captions} {(creator_bio or '').lower()}"

    score = max(18, min(92, base))
    needs: List[Dict[str, Any]] = []
    free_lift_pts = 0

    def add_need(text: str, done: bool, weight: int = 0):
        nonlocal score, free_lift_pts
        needs.append({"text": text, "done": done, "weight": weight})
        if not done:
            score = max(12, score - weight)
            free_lift_pts += weight

    add_need("Bio optimized", "bio" not in open_ids, 8)
    add_need("Publish portfolio", "kit" not in open_ids, 8)

    # Category-specific third need
    cat = category
    if any(k in cat for k in ("beauty", "makeup", "cosmetic")) or "makeup" in blob:
        has = bool(re.search(r"\b(makeup|mascara|lipstick|foundation|glam)\b", blob))
        add_need("Post 1 makeup Reel this week", has, 6)
    elif any(k in cat for k in ("skincare", "skin", "derm")) or "skincare" in blob:
        has = bool(re.search(r"\b(skincare|serum|moisturizer|routine|spf)\b", blob))
        add_need("Add 1 skincare routine post", has, 6)
    elif any(k in cat for k in ("wellness", "health", "supplement", "juice")):
        has = bool(re.search(r"\b(wellness|adaptogen|ritual|supplement|juice)\b", blob))
        add_need("Wellness / wellness-adjacent content", has, 8)
    elif any(k in cat for k in ("fashion", "apparel", "clothing", "streetwear")):
        has = bool(re.search(r"\b(outfit|fashion|grwm|try.?on|streetwear|ootd)\b", blob))
        add_need("Fashion / GRWM content in last posts", has, 6)
    elif any(k in cat for k in ("fitness", "active", "gym")):
        has = bool(re.search(r"\b(fitness|workout|gym|train)\b", blob))
        add_need("Fitness product-in-use content", has, 6)
    else:
        add_need(
            "Product-in-use proof on profile",
            "shows_products" not in open_ids,
            6,
        )

    # Keep top 3 needs, prefer incomplete first then complete
    needs = sorted(needs, key=lambda n: (n["done"],))[:3]
    score = int(max(12, min(95, score)))
    lift_free = int(min(95, score + free_lift_pts))
    # Soft Pro ceiling — shown as context only, never the primary CTA number
    lift_pro = int(min(95, lift_free + 12))
    if score >= 60:
        tier = "high"
    elif score >= 40:
        tier = "medium"
    else:
        tier = "low"
    open_need_count = sum(1 for n in needs if not n.get("done"))

    return {
        "id": brand.get("id") or brand.get("brand_id"),
        "name": name,
        "category": brand.get("category") or category or "brand",
        "score": score,
        "needs": [{"text": n["text"], "done": n["done"]} for n in needs],
        "user_fit_score": score,
        "user_fit_lift_potential_free": lift_free,
        "user_fit_lift_potential_pro": lift_pro,
        "user_fit_tier": tier,
        "user_fit_open_fixes": open_need_count,
    }


def scrape_summary(scrape: Optional[Dict]) -> Optional[Dict[str, Any]]:
    if not scrape:
        return None
    thumbs = _as_list(scrape.get("recent_post_thumbnails"))
    recent_posts = []
    for p in _recent_posts_for_kit(scrape)[:6]:
        proxied = proxy_media_urls([p["thumbnail_url"]])
        recent_posts.append(
            {
                "thumbnail_url": (proxied[0] if proxied else p["thumbnail_url"]),
                "views": p["views"],
                "likes": p["likes"],
                "comments": p["comments"],
                "shares": p["shares"],
                "saves": p["saves"],
                "post_url": p.get("post_url"),
            }
        )
    return {
        "handle": scrape.get("handle"),
        "platform": scrape.get("primary_platform"),
        "follower_count": scrape.get("follower_count") or 0,
        "engagement_rate": scrape.get("engagement_rate") or 0,
        "raw_bio": scrape.get("raw_bio") or "",
        "primary_niche": scrape.get("primary_niche"),
        "content_themes": _as_list(scrape.get("content_themes"))[:8],
        "content_gaps": _as_list(scrape.get("content_gaps"))[:6],
        "recent_thumbnails": proxy_media_urls(thumbs[:9]),
        "recent_posts": recent_posts,
        "caption_mentions": _as_list(scrape.get("caption_mentions"))[:12],
        "has_collab_email": bool(scrape.get("has_collab_email")),
        "scraped_at": str(scrape.get("scraped_at") or ""),
        "posting_cadence_per_week": scrape.get("posting_cadence_per_week") or 0,
    }
