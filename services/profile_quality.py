"""Onboarding quality bar for brand-ready creator accounts.

Hard rejects, in order:
  1. 500+ followers (when a count was scraped)
  2. 12+ public posts
  3. A post in the last 30 days (only when we have a real date)

Visual Gemini review is advisory only. Instagram covers and UGC
thumbnails are too incomplete to reject creators on.
"""

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from urllib.parse import urlparse

import requests

MIN_FOLLOWERS = 500
MIN_POSTS = 12
MAX_LAST_POST_DAYS = 30
MIN_THUMBNAILS = 3
MIN_VISUAL_SCORE = 60
MAX_VISUAL_THUMBS = 6


class ProfileQualityError(ValueError):
    """Raised when a scraped profile is real but below the product bar."""

    def __init__(
        self,
        code: str,
        handle: str,
        follower_count: int = 0,
        post_count: int = 0,
        latest_post_days_ago=None,
        message: str = None,
    ):
        self.code = code
        self.handle = (handle or "").lstrip("@").strip()
        self.follower_count = int(follower_count or 0)
        self.post_count = int(post_count or 0)
        try:
            self.latest_post_days_ago = (
                int(latest_post_days_ago) if latest_post_days_ago is not None else None
            )
        except (TypeError, ValueError):
            self.latest_post_days_ago = None
        super().__init__(message or f"Account @{self.handle} failed quality check ({code})")


def _as_int(value, default=0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def fails_follower_floor(follower_count) -> bool:
    """True when we have a real count and it is below 500. 0 means unknown."""
    n = _as_int(follower_count)
    return n > 0 and n < MIN_FOLLOWERS


def raw_follower_count(raw_scrape: dict) -> int:
    if not raw_scrape:
        return 0
    return _as_int(
        raw_scrape.get("followersCount")
        or raw_scrape.get("followerCount")
        or raw_scrape.get("subscriberCount")
        or raw_scrape.get("follower_count")
    )


def visible_post_count(profile: dict) -> int:
    """Declared count, or how many posts we actually saw, whichever is higher."""
    if not profile:
        return 0
    declared = _as_int(
        profile.get("postsCount")
        or profile.get("videoCount")
        or profile.get("post_count")
    )
    visible = 0
    for key in ("latestPosts", "latestVideos", "recent_posts", "recent_post_thumbnails"):
        items = profile.get(key) or []
        if isinstance(items, list):
            visible = max(visible, len(items))
    return max(declared, visible)


def latest_post_age_days(profile: dict):
    days = profile.get("latest_post_days_ago")
    if days is None:
        return None
    try:
        n = int(days)
    except (TypeError, ValueError):
        return None
    if n >= 999:
        return None
    return n


def fails_post_floor(post_count) -> bool:
    return _as_int(post_count) < MIN_POSTS


def fails_recency(days_ago) -> bool:
    if days_ago is None:
        return True
    return _as_int(days_ago, 999) > MAX_LAST_POST_DAYS


def _thumb_headers(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    host = urlparse(url).netloc.lower()
    if any(token in host for token in ("tiktok", "muscdn", "byteoversea", "ibyteimg")):
        headers["Referer"] = "https://www.tiktok.com/"
    elif any(token in host for token in ("ytimg", "googleusercontent", "youtube")):
        headers["Referer"] = "https://www.youtube.com/"
    elif any(token in host for token in ("cdninstagram", "fbcdn", "instagram")):
        headers["Referer"] = "https://www.instagram.com/"
    return headers


def _download_one_thumb(url: str):
    try:
        resp = requests.get(
            url,
            timeout=6,
            headers=_thumb_headers(url),
        )
        if not resp.ok or not resp.content or len(resp.content) < 800:
            return None
        ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        if ctype not in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
            ctype = "image/jpeg"
        return ctype, resp.content
    except Exception:
        return None


def download_thumbnails(urls, limit: int = MAX_VISUAL_THUMBS):
    cleaned = [u for u in (urls or []) if isinstance(u, str) and u.startswith("http")][:limit]
    if not cleaned:
        return []
    out = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_download_one_thumb, url): url for url in cleaned}
        for fut in as_completed(futures):
            item = fut.result()
            if item:
                out.append(item)
    return out


def interpret_visual_review(parsed) -> dict:
    """Turn Gemini JSON into a pass/fail for brand-ready content.

    Face-forward GRWM, unboxing, hauls, and talk-to-camera UGC must pass.
    Fail only unusable footage, or a personal diary grid with no UGC intent.
    """
    if not isinstance(parsed, dict):
        return {"ok": True, "score": None, "reason": "skipped"}

    score = max(0, min(100, _as_int(parsed.get("score"), 0)))
    clear = parsed.get("clear_and_clean")
    ugc_style = parsed.get("ugc_style") is True
    has_product = parsed.get("has_product_or_ugc_focus") is True
    personal_only = parsed.get("personal_only") is True
    heavy_filters = parsed.get("heavy_filters") is True

    # Typical UGC: face to camera, GRWM, unboxing. Never reject these
    # just because the thumbnail is a face or makeup looks like a filter.
    if ugc_style or has_product:
        if clear is False and score < 40:
            return {"ok": False, "score": score, "reason": "low_quality"}
        return {"ok": True, "score": score, "reason": "ok"}

    if clear is False or score < 40:
        return {"ok": False, "score": min(score, 39), "reason": "low_quality"}

    if personal_only and not has_product:
        return {"ok": False, "score": score, "reason": "no_brand_focus"}
    if heavy_filters:
        return {"ok": False, "score": score, "reason": "heavy_filters"}
    return {"ok": True, "score": score, "reason": "ok"}


def assess_visual_clarity(thumbnail_urls, gemini_fn=None) -> dict:
    """
    Look at recent post thumbnails.

    Returns:
      ok: True when content looks brand-ready, or when we cannot see enough
          media to judge (Instagram CDN often blocks server downloads)
      score: 0-100 or None if skipped
      reason: not_enough_media | low_quality | heavy_filters | no_brand_focus | skipped | ok
    """
    images = download_thumbnails(thumbnail_urls)
    if len(images) < MIN_THUMBNAILS:
        print(
            f"[Quality] visual skipped: downloaded {len(images)}/{MIN_THUMBNAILS} "
            f"thumbs from {len(thumbnail_urls or [])} urls"
        )
        return {"ok": True, "score": None, "reason": "skipped"}

    if gemini_fn is None:
        gemini_fn = _gemini_visual_review
    try:
        review = gemini_fn(images)
    except Exception as exc:
        print(f"[Quality] visual Gemini skipped: {exc}")
        return {"ok": True, "score": None, "reason": "skipped"}

    if review is None:
        return {"ok": True, "score": None, "reason": "skipped"}
    if isinstance(review, dict) and "ok" in review:
        return review
    if isinstance(review, dict):
        return interpret_visual_review(review)
    score = _as_int(review, 0)
    if score < MIN_VISUAL_SCORE:
        return {"ok": False, "score": score, "reason": "low_quality"}
    return {"ok": True, "score": score, "reason": "ok"}


def _gemini_visual_review(images):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    parts = [
        {
            "text": (
                "You review creator thumbnails for brand PR / UGC recruiting. "
                "Return JSON only: "
                "{\"score\": 0-100, \"clear_and_clean\": true/false, "
                "\"ugc_style\": true/false, \"heavy_filters\": true/false, "
                "\"personal_only\": true/false, \"has_product_or_ugc_focus\": true/false}. "
                "ALWAYS PASS these formats (ugc_style true, personal_only false, score 70+): "
                "face-forward talk to camera, GRWM, get ready with me, unboxing, haul, "
                "tutorial, review, demo, makeup application, skincare routine, a creator "
                "holding or using a product. A face filling the frame is normal UGC. "
                "Real makeup, contour, ring lights, and beauty lighting are NOT filters. "
                "The product does not have to dominate the thumbnail. "
                "ONLY FAIL (ugc_style false, personal_only true, score under 40) when the "
                "grid is a personal diary/selfie dump with no creator or product intent, "
                "or footage that is grainy, dark, blurry, screenshots, memes, or spam. "
                "heavy_filters is Snapchat/AR/cartoon/dog-ear effects only. "
                "If it could be UGC, pass it."
            )
        }
    ]
    for ctype, blob in images[:MAX_VISUAL_THUMBS]:
        parts.append({
            "inline_data": {
                "mime_type": ctype,
                "data": base64.b64encode(blob).decode("ascii"),
            }
        })

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    resp = requests.post(
        url,
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 256,
                "responseMimeType": "application/json",
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text")
        or "{}"
    )
    parsed = json.loads(text)
    return interpret_visual_review(parsed)


def assert_onboarding_quality(profile: dict, handle: str, *, check_visual: bool = False) -> None:
    """Raise ProfileQualityError when the account should not continue onboarding."""
    followers = raw_follower_count(profile)
    posts = visible_post_count(profile)
    days_ago = latest_post_age_days(profile)

    if fails_follower_floor(followers):
        raise ProfileQualityError(
            "below_follower_min",
            handle,
            follower_count=followers,
            post_count=posts,
            latest_post_days_ago=days_ago,
            message=f"Account @{handle} has fewer than {MIN_FOLLOWERS} followers",
        )

    if fails_post_floor(posts):
        raise ProfileQualityError(
            "below_post_min",
            handle,
            follower_count=followers,
            post_count=posts,
            latest_post_days_ago=days_ago,
            message=f"Account @{handle} has fewer than {MIN_POSTS} posts",
        )

    if fails_recency(days_ago):
        raise ProfileQualityError(
            "inactive",
            handle,
            follower_count=followers,
            post_count=posts,
            latest_post_days_ago=days_ago if days_ago is not None else 999,
            message=f"Account @{handle} has no recent posts",
        )

    if check_visual:
        thumbs = profile.get("recent_post_thumbnails") or []
        visual = assess_visual_clarity(thumbs)
        print(
            f"[Quality] @{handle} visual ok={visual.get('ok')} "
            f"reason={visual.get('reason')} score={visual.get('score')} "
            f"thumbs={len(thumbs)} (advisory, not a reject)"
        )
