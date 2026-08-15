"""Map onboarding scrape failures to user-safe copy. Never leak proxy or session internals."""

from services.profile_quality import (
    MAX_LAST_POST_DAYS,
    MIN_FOLLOWERS,
    MIN_POSTS,
    ProfileQualityError,
)

_PLATFORM_URLS = {
    "instagram": "https://www.instagram.com/{handle}/",
    "tiktok": "https://www.tiktok.com/@{handle}",
    "youtube": "https://www.youtube.com/@{handle}",
}

_PLATFORM_NAMES = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube",
}


def _normalize_platform(platform: str) -> str:
    platform = (platform or "instagram").lower()
    return platform if platform in _PLATFORM_NAMES else "instagram"


def _audience_noun(platform: str) -> str:
    return "subscribers" if platform == "youtube" else "followers"


def _content_noun(platform: str) -> str:
    return "videos" if platform in ("tiktok", "youtube") else "posts"


def _profile_url(platform: str, handle: str) -> str:
    template = _PLATFORM_URLS.get(platform, _PLATFORM_URLS["instagram"])
    return template.format(handle=handle)


def _quality_payload(quality: ProfileQualityError, handle: str, platform: str):
    display = f"@{handle}" if handle else "this profile"
    platform_name = _PLATFORM_NAMES.get(platform, "Instagram")
    audience = _audience_noun(platform)
    content = _content_noun(platform)
    code = quality.code
    counted = quality.follower_count or None
    posts = quality.post_count or None
    days = quality.latest_post_days_ago

    if code == "below_follower_min":
        counted_bit = (
            f" We counted {counted} {audience} on {display}."
            if counted
            else f" {display} is under {MIN_FOLLOWERS}."
        )
        return {
            "error_code": "below_follower_min",
            "error": f"You need at least {MIN_FOLLOWERS} {audience}",
            "message": (
                f"Brands on Newcollab work with creators who already have an audience."
                f"{counted_bit} Come back when you reach {MIN_FOLLOWERS}."
            ),
            "tips": [
                f"Keep posting until you reach {MIN_FOLLOWERS} {audience}",
                "Confirm we scanned the right public account",
                "Then tap Continue again",
            ],
        }

    if code == "below_post_min":
        seen = f" We found {posts} {content} on {display}." if posts else ""
        return {
            "error_code": "below_post_min",
            "error": f"You need at least {MIN_POSTS} {content}",
            "message": (
                f"Brands want to see a real content grid, not a new or empty profile."
                f"{seen} Publish until you have {MIN_POSTS} public {content}, then come back."
            ),
            "tips": [
                f"Post {MIN_POSTS} public {content} on {platform_name}",
                "Keep the account public so we can see the grid",
                "Then tap Continue again",
            ],
        }

    if code == "inactive":
        age_bit = (
            f" The latest {content[:-1] if content.endswith('s') else content} we found on {display} is {days} days old."
            if days and days < 999
            else f" We could not find a {content[:-1] if content.endswith('s') else content} from the last {MAX_LAST_POST_DAYS} days on {display}."
        )
        return {
            "error_code": "inactive",
            "error": f"Your profile needs a recent {content[:-1] if content.endswith('s') else content}",
            "message": (
                f"Brands look for creators who post regularly."
                f"{age_bit} Share something new, then come back."
            ),
            "tips": [
                f"Post at least once every {MAX_LAST_POST_DAYS} days",
                "Keep the new post public",
                "Then tap Continue again",
            ],
        }

    return {
        "error_code": "below_content_quality",
        "error": "Your content needs to look brand-ready",
        "message": (
            f"Brands look for UGC they could reuse: GRWM, unboxing, demos, or clear "
            f"face-to-camera clips. The recent {content} on {display} look too much like "
            f"a personal dump, or are too dark, blurry, or filtered to use."
        ),
        "tips": [
            "GRWM, unboxing, and talking to camera are welcome",
            "Skip Snapchat-style filters, memes, and dark blurry clips",
            "Then tap Continue again",
        ],
    }


def onboarding_scrape_user_error(exc: Exception, handle: str, platform: str) -> dict:
    """
    Convert scraper exceptions into a payload the onboarding UI can render.

    error_code:
      private | not_found | incomplete | unavailable
      below_follower_min | below_post_min | inactive | below_content_quality
    """
    handle = (handle or "").lstrip("@").strip()
    platform = _normalize_platform(platform)
    platform_name = _PLATFORM_NAMES[platform]
    content = _content_noun(platform)
    msg = str(exc or "").lower()
    profile_url = _profile_url(platform, handle) if handle else None
    display = f"@{handle}" if handle else "this profile"

    default_tips = [
        "Double-check the username spelling",
        f"Make sure the {platform_name} account is public, not private",
        "Have at least one public post so we can confirm it is you",
    ]

    payload = {
        "success": False,
        "error_code": "unavailable",
        "error": f"We could not verify {display}",
        "message": "Check the username and that the account is public, then try again.",
        "tips": default_tips,
        "profile_url": profile_url,
        "is_private": False,
    }

    if isinstance(exc, ProfileQualityError):
        extra = _quality_payload(exc, handle, platform)
        payload.update(extra)
        payload["follower_count"] = exc.follower_count or None
        payload["post_count"] = exc.post_count or None
        payload["min_followers"] = MIN_FOLLOWERS
        payload["min_posts"] = MIN_POSTS
        payload["is_private"] = False
        return payload

    if "fewer than 500" in msg or "below_follower" in msg:
        payload.update(_quality_payload(
            ProfileQualityError("below_follower_min", handle),
            handle,
            platform,
        ))
        payload["min_followers"] = MIN_FOLLOWERS
        return payload

    if "fewer than 12" in msg or "below_post" in msg:
        payload.update(_quality_payload(
            ProfileQualityError("below_post_min", handle),
            handle,
            platform,
        ))
        payload["min_posts"] = MIN_POSTS
        return payload

    if "private" in msg:
        payload.update({
            "error_code": "private",
            "error": f"{display} looks private",
            "message": f"Switch {platform_name} to a public account, then try again.",
            "tips": [
                f"Open Settings on {platform_name} and set the account to Public",
                "Confirm the profile opens while logged out",
                "Then tap Continue again",
            ],
            "is_private": True,
        })
        return payload

    if "invalid" in msg and "username" in msg:
        payload.update({
            "error_code": "not_found",
            "error": "That username does not look valid",
            "message": (
                "Use the handle only. Letters, numbers, periods, underscores"
                + (", and hyphens." if platform == "youtube" else ".")
            ),
            "tips": [
                "Do not paste the full profile URL",
                "Remove spaces and extra symbols",
            ],
        })
        return payload

    if "not found" in msg:
        payload.update({
            "error_code": "not_found",
            "error": f"We could not find {display} on {platform_name}",
            "message": "Check the spelling, then open the profile to confirm it exists.",
            "tips": [
                "Remove extra dots, spaces, or letters",
                f"Paste only the username, not the full {platform_name} URL",
                "Open the profile link below to confirm it loads",
            ],
        })
        return payload

    if (
        "latest_post" in msg
        or "missing latest" in msg
        or "incomplete" in msg
        or "thin" in msg
    ):
        payload.update({
            "error_code": "incomplete",
            "error": f"We could not fully verify {display}",
            "message": (
                f"Public {platform_name} profiles need to be public "
                "and have at least one post."
            ),
            "tips": [
                "Double-check the username spelling",
                "Make sure the account is public, not private",
                f"Publish at least one public {'video' if content == 'videos' else 'photo or Reel'}, then try again",
            ],
        })
        return payload

    if "429" in msg or "rate-limit" in msg or "rate limit" in msg:
        payload.update({
            "error_code": "unavailable",
            "error": f"{platform_name} is busy right now",
            "message": "Wait about a minute, then try again. Your username is saved.",
            "tips": default_tips,
        })
        return payload

    if "no instagram data" in msg or "no tiktok data" in msg or "no youtube data" in msg:
        payload.update({
            "error_code": "not_found",
            "error": f"We could not load {display}",
            "message": "Confirm the username is correct and the account is public.",
            "tips": default_tips,
        })
        return payload

    return payload
