"""
Irresistible gifted-PR pitch — the format that got more brand replies.

Data upfront, concrete deliverable, direct ask, portfolio link.
Filled from creator/brand records. No LLM rewrite (keeps the proven structure).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional

from services.pitch_identity import resolve_pitch_identity

LOCATION_PLACEHOLDER = "[CITY, COUNTRY]"
GENERIC_PRODUCTS = {"your product", "pr sample", "product", "products"}
# Subject stays generic so long SKU names do not get cut off in the inbox.
IRRESISTIBLE_SUBJECT = "3 posts + 1 UGC file for a PR/gifting sample · gifted trial"

_ProofBuilder = Callable[..., dict]


def format_followers(count: Any) -> str:
    try:
        n = int(float(count or 0))
    except (TypeError, ValueError):
        n = 0
    if n >= 1_000_000:
        val = n / 1_000_000
        return f"{val:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        val = n / 1_000
        return f"{val:.1f}K".replace(".0K", "K")
    if n > 0:
        return str(n)
    return ""


def format_location(city: str = "", country: str = "") -> str:
    parts = [p.strip() for p in (city or "", country or "") if p and str(p).strip()]
    return ", ".join(parts) if parts else LOCATION_PLACEHOLDER


def _as_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else [str(parsed)]
        except Exception:
            return [raw]
    return []


def _as_dict(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def resolve_shipping(creator: dict, city: str = "", country: str = "") -> Dict[str, str]:
    addr = _as_dict(creator.get("shipping_address"))
    city = (city or addr.get("city") or "").strip()
    country = (country or addr.get("country") or creator.get("country") or "").strip()
    display = format_location(city, country)
    return {
        "city": city,
        "country": country,
        "display": display,
        "needs_location": not city or not country,
    }


def _clean_product(name: Any) -> str:
    if not name:
        return ""
    text = re.sub(r"\s*\([^)]*\)", "", str(name)).strip()
    return text


def resolve_product(brand: dict, creator: dict) -> str:
    sku = ""
    try:
        from services.gemini_pitch_generator_v2 import select_pitch_product
        sku = _clean_product(select_pitch_product(brand or {}, creator or {}))
    except Exception:
        sku = _clean_product(
            (brand or {}).get("hero_product")
            or (brand or {}).get("product_sku_name")
        )
    if not sku:
        return "PR sample"
    lower = sku.lower().strip()
    if lower in GENERIC_PRODUCTS or lower.endswith(" products"):
        return "PR sample"
    return sku


def resolve_platform(creator: dict) -> str:
    links = _as_list(creator.get("social_links"))
    best = ""
    best_followers = -1
    for link in links:
        if not isinstance(link, dict):
            continue
        plat = (link.get("platform") or "").strip().lower()
        if plat not in ("tiktok", "instagram", "youtube"):
            continue
        try:
            followers = int(float(link.get("followersCount") or link.get("followers") or 0))
        except (TypeError, ValueError):
            followers = 0
        if followers > best_followers:
            best_followers = followers
            best = plat
    if not best:
        platforms = _as_list(creator.get("platforms"))
        for p in platforms:
            name = (p if isinstance(p, str) else (p or {}).get("platform") or "").lower()
            if name in ("tiktok", "instagram", "youtube"):
                best = name
                break
    labels = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}
    return labels.get(best, "Instagram")


def resolve_niche(creator: dict, brand: dict) -> str:
    raw = creator.get("creator_niches") or creator.get("niches") or creator.get("niche")
    niches = [str(n).strip().strip("\"'[] ") for n in _as_list(raw) if n]
    if not niches and isinstance(raw, str) and raw.strip():
        niches = [raw.strip().strip("\"'[] ")]
    niche = next((n for n in niches if n and n.lower() not in ("null", "none")), "")
    if not niche:
        niche = (brand or {}).get("category") or "lifestyle"
    niche = str(niche).strip()
    if niche.startswith("[") and niche.endswith("]"):
        try:
            parsed = json.loads(niche)
            if isinstance(parsed, list) and parsed:
                niche = str(parsed[0])
        except Exception:
            niche = niche.strip("[]\"'").split(",")[0].strip()
    return niche.strip("\"'[] ") or "lifestyle"


def _parse_engagement_pct(raw: Any) -> float:
    if raw in (None, "", 0, "0"):
        return 0.0
    text = str(raw).strip().rstrip("%")
    try:
        rate = float(text)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0:
        return 0.0
    if rate > 100:
        return 100.0
    return rate


def resolve_engagement(creator: dict) -> str:
    """Pull a real % from creator row, marketplace field, scrape, or social_links."""
    creator = creator or {}
    candidates = [
        creator.get("engagement_rate"),
        creator.get("avg_engagement_rate"),
        creator.get("scraped_engagement_rate"),
        creator.get("media_kit_engagement"),
    ]
    for link in _as_list(creator.get("social_links")):
        if isinstance(link, dict):
            candidates.extend([
                link.get("engagementRate"),
                link.get("engagement_rate"),
                link.get("avgEngagement"),
            ])
    for raw in candidates:
        rate = _parse_engagement_pct(raw)
        if rate > 0:
            text = f"{rate:.1f}".rstrip("0").rstrip(".")
            return f"{text}%"
    try:
        views = float(creator.get("total_views") or 0)
        likes = float(creator.get("total_likes") or 0)
        comments = float(creator.get("total_comments") or 0)
        shares = float(creator.get("total_shares") or 0)
        if views > 0:
            rate = (likes + comments + shares) / views * 100
            if 0 < rate <= 100:
                text = f"{rate:.1f}".rstrip("0").rstrip(".")
                return f"{text}%"
    except (TypeError, ValueError):
        pass
    return ""


_PLATFORM_PROFILE_URL = {
    "instagram": "https://www.instagram.com/{handle}/",
    "tiktok": "https://www.tiktok.com/@{handle}",
    "youtube": "https://www.youtube.com/@{handle}",
}


def resolve_platform_profile_url(creator: dict, platform: str) -> str:
    """Public profile URL for the platform named in the intro line."""
    plat = (platform or "").strip().lower()
    for link in _as_list(creator.get("social_links")):
        if not isinstance(link, dict):
            continue
        if (link.get("platform") or "").strip().lower() != plat:
            continue
        url = str(link.get("url") or "").strip()
        if url.startswith("http"):
            return url
        handle = str(link.get("handle") or link.get("username") or "").strip().lstrip("@")
        template = _PLATFORM_PROFILE_URL.get(plat)
        if handle and template:
            return template.format(handle=handle)
    identity = resolve_pitch_identity(creator)
    handle = identity.get("handle") or ""
    template = _PLATFORM_PROFILE_URL.get(plat)
    if handle and template:
        return template.format(handle=handle)
    return ""


def format_platform_mention(platform: str, profile_url: str = "") -> str:
    """Plain-text 'Instagram (url)' so mail clients auto-link the profile."""
    if profile_url:
        return f"{platform} ({profile_url})"
    return platform


def resolve_demographic(creator: dict, niche: str) -> str:
    desc = (creator.get("audience_description") or "").strip()
    if desc:
        return desc.rstrip(".")
    age = (creator.get("primary_age_range") or creator.get("age_range") or "").strip()
    if age:
        return f"{age} {niche} fans"
    return f"{niche} fans"


def resolve_portfolio_link(creator: dict, proof: Optional[dict] = None) -> str:
    if proof and proof.get("url"):
        return proof["url"]
    identity = resolve_pitch_identity(creator)
    handle = identity.get("handle")
    if handle:
        return f"@{handle}"
    return ""


def generate_irresistible_pitch(
    brand: dict,
    creator: dict,
    *,
    city: str = "",
    country: str = "",
    proof_builder: Optional[_ProofBuilder] = None,
) -> dict:
    brand = brand or {}
    creator = creator or {}
    shipping = resolve_shipping(creator, city, country)
    product = resolve_product(brand, creator)
    platform = resolve_platform(creator)
    niche = resolve_niche(creator, brand)
    followers = format_followers(
        creator.get("creator_followers")
        or creator.get("media_kit_followers")
        or creator.get("followers_count")
        or creator.get("social_follower_count")
    )
    engagement = resolve_engagement(creator)
    demographic = resolve_demographic(creator, niche)
    brand_name = (brand.get("brand_name") or brand.get("name") or "there").strip()
    identity = resolve_pitch_identity(creator)
    signoff = identity.get("signoff_name") or identity.get("handle") or "there"
    profile_url = resolve_platform_profile_url(creator, platform)
    platform_mention = format_platform_mention(platform, profile_url)

    proof = {}
    if proof_builder:
        proof = proof_builder(
            creator,
            creator_id=creator.get("id") or creator.get("creator_id"),
            brand_id=brand.get("id"),
        ) or {}
    portfolio = resolve_portfolio_link(creator, proof)

    intro = f"I create {niche} content on {platform_mention}"
    if followers:
        intro += f" for {followers} followers"
    if engagement:
        intro += f" with {engagement} engagement"
    intro += f". My audience is {demographic}."

    box_line = f"Trade offer for a {product} PR box:"

    location_line = f"No fee. Just product + shipping to {shipping['display']}."

    portfolio_line = f"Recent work: {portfolio}" if portfolio else ""

    body_parts = [
        f"Hi {brand_name},",
        "",
        intro,
        "",
        box_line,
        "",
        f"• 3 organic posts to my {platform} within 21 days",
        "• 1 raw UGC video file (yours to run as paid ads, 6-month rights)",
        "• 30-day performance report (views, saves, CTR, DMs)",
        "",
        location_line,
        "",
    ]
    if portfolio_line:
        body_parts.extend([portfolio_line, ""])
    body_parts.extend(["Worth a look?", "", signoff])
    body = "\n".join(body_parts)

    subject = IRRESISTIBLE_SUBJECT

    return {
        "subject": subject,
        "body": body,
        "source": "irresistible_v1",
        "product_name": product,
        "platform": platform,
        "niche": niche,
        "followers": followers,
        "engagement": engagement,
        "shipping_city": shipping["city"],
        "shipping_country": shipping["country"],
        "location_display": shipping["display"],
        "needs_location": shipping["needs_location"],
        "kit_published": bool(creator.get("kit_published")),
        "media_kit_url": proof.get("url") if proof.get("kind") == "kit" else None,
        "kit_token": proof.get("kit_token"),
        "profile_url": profile_url or None,
        "creator_stats": {
            "followers": followers or None,
            "niche": niche,
            "platform": platform,
        },
    }


def apply_location_to_body(body: str, location_display: str, previous_display: str = "") -> str:
    """Swap the shipping destination in an already-generated pitch."""
    if not body:
        return body
    next_display = (location_display or LOCATION_PLACEHOLDER).strip() or LOCATION_PLACEHOLDER
    prev = (previous_display or "").strip()
    if prev and prev in body:
        return body.replace(prev, next_display)
    return re.sub(
        r"(shipping to )(.+?)(\.?)$",
        rf"\1{next_display}\3",
        body,
        count=1,
        flags=re.M,
    )


def pitch_to_html(plain: str) -> str:
    paras = [p.strip() for p in (plain or "").split("\n\n") if p.strip()]
    html = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras)
    return re.sub(
        r"(Instagram|TikTok|YouTube) \((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        html,
        count=1,
    )


def irresistible_package_slots(pitch: dict) -> dict:
    """Fill the three PR-package pitch columns with the same irresistible draft."""
    subject = pitch.get("subject") or ""
    body = pitch.get("body") or ""
    html = pitch_to_html(body)
    return {
        "pitch_short_subject": subject,
        "pitch_short_body_html": html,
        "pitch_short_body_plain": body,
        "pitch_growing_subject": subject,
        "pitch_growing_body_html": html,
        "pitch_growing_body_plain": body,
        "pitch_founder_subject": subject,
        "pitch_founder_body_html": html,
        "pitch_founder_body_plain": body,
        "generation_reasoning": "irresistible_v1",
    }

