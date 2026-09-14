"""Public media-kit helpers — no Flask/DB imports so tests stay hermetic."""

from __future__ import annotations

import json
import re

_PLATFORM_URLS = {
    'instagram': 'https://instagram.com/{handle}',
    'ig': 'https://instagram.com/{handle}',
    'tiktok': 'https://tiktok.com/@{handle}',
    'youtube': 'https://youtube.com/@{handle}',
    'yt': 'https://youtube.com/@{handle}',
    'twitter': 'https://x.com/{handle}',
    'x': 'https://x.com/{handle}',
    'linkedin': 'https://linkedin.com/in/{handle}',
}

_PLATFORM_CANON = {
    'ig': 'instagram',
    'tik tok': 'tiktok',
    'yt': 'youtube',
    'x': 'twitter',
}


def _as_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except (TypeError, ValueError):
            return []
    return []


def parse_kit_niches(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        joined = ','.join(str(n) for n in raw)
        if joined.strip().startswith('[') or any('"' in str(n) for n in raw):
            try:
                parsed = json.loads(joined if joined.strip().startswith('[') else f'[{joined}]')
                if isinstance(parsed, list):
                    return [str(n).strip() for n in parsed if str(n).strip()]
            except (TypeError, ValueError):
                pass
        return [str(n).strip().strip('"[]') for n in raw if str(n).strip().strip('"[]')]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(n).strip() for n in parsed if str(n).strip()]
            except (TypeError, ValueError):
                pass
        return [part.strip().strip('"[]') for part in text.split(',') if part.strip().strip('"[]')]
    return [str(raw).strip()] if str(raw).strip() else []


def _canon_platform(raw):
    key = (raw or '').strip().lower()
    return _PLATFORM_CANON.get(key, key)


def _handle_from_url(url):
    if not url:
        return ''
    text = str(url).strip()
    if not text.startswith('http'):
        return text.lstrip('@').strip('/')
    match = re.search(r'(?:instagram\.com|tiktok\.com|youtube\.com|x\.com|twitter\.com|linkedin\.com/in)/@?([^/?#]+)', text, re.I)
    return (match.group(1) if match else '').lstrip('@')


def _social_url(platform, handle=None, url=None):
    if url and str(url).startswith(('http://', 'https://')):
        return str(url).strip()
    h = (handle or '').strip().lstrip('@')
    if not h and url:
        h = _handle_from_url(url)
    if not h:
        return None
    tmpl = _PLATFORM_URLS.get(platform)
    if not tmpl:
        return None
    return tmpl.format(handle=h)


def build_public_socials(social_links, social_handle=None, social_platform=None, username=None):
    """Return {socials: {platform: url}, social_profiles: [{platform, handle, url, followers}]}."""
    profiles = []
    seen = set()

    def add(platform, handle=None, url=None, followers=None):
        platform = _canon_platform(platform)
        if not platform:
            return
        href = _social_url(platform, handle=handle, url=url)
        h = (handle or '').strip().lstrip('@') or _handle_from_url(href or url or '')
        if not href and not h:
            return
        key = (platform, (h or '').lower() or href)
        if key in seen:
            return
        seen.add(key)
        item = {
            'platform': platform,
            'handle': f'@{h}' if h else None,
            'url': href,
        }
        if followers not in (None, ''):
            try:
                item['followers'] = int(followers)
            except (TypeError, ValueError):
                pass
        profiles.append(item)

    for link in _as_list(social_links):
        if not isinstance(link, dict):
            continue
        add(
            link.get('platform'),
            handle=link.get('handle'),
            url=link.get('url') or link.get('profile_url'),
            followers=link.get('followersCount') or link.get('followers'),
        )

    add(social_platform, handle=social_handle)

    if not profiles:
        fallback_handle = (social_handle or username or '').strip().lstrip('@')
        if fallback_handle:
            add(social_platform or 'instagram', handle=fallback_handle)

    socials = {}
    for item in profiles:
        if item.get('url') and item['platform'] not in socials:
            socials[item['platform']] = item['url']
    return socials, profiles


def serialize_public_recent_posts(raw_items, thumbnails=None, default_platform=None, limit=9):
    """Public-safe recent work from scrape/OAuth payloads. Never includes tokens."""
    out = []
    seen = set()
    for post in _as_list(raw_items):
        if not isinstance(post, dict):
            continue
        thumb = (
            post.get('cover_image_url')
            or post.get('thumbnail_url')
            or post.get('thumb')
            or post.get('display_url')
            or post.get('displayUrl')
            or ''
        )
        url = (
            post.get('share_url')
            or post.get('post_url')
            or post.get('url')
            or post.get('webVideoUrl')
            or post.get('permalink')
            or ''
        )
        if not thumb and not url:
            continue
        key = url or thumb
        if key in seen:
            continue
        seen.add(key)
        likes = post.get('likes')
        if likes is None:
            likes = post.get('like_count')
        views = post.get('views')
        if views is None:
            views = post.get('view_count') or post.get('play_count')
        comments = post.get('comments')
        if comments is None:
            comments = post.get('comment_count')
        platform = _canon_platform(post.get('platform') or default_platform) or None
        item = {
            'id': post.get('id'),
            'post_url': url or None,
            'platform': platform,
            'post_type': post.get('post_type') or ('tiktok' if platform == 'tiktok' else 'reel'),
            'brand_name': None,
            'collab_type': 'own',
            'thumbnail_url': thumb or None,
            'source': 'scrape',
        }
        for key_name, value in (('likes', likes), ('views', views), ('comments', comments)):
            try:
                if value not in (None, ''):
                    item[key_name] = int(value)
            except (TypeError, ValueError):
                pass
        out.append(item)
        if len(out) >= limit:
            return out

    for thumb in _as_list(thumbnails):
        if not thumb or thumb in seen:
            continue
        seen.add(thumb)
        out.append({
            'id': None,
            'post_url': None,
            'platform': _canon_platform(default_platform) or None,
            'post_type': 'reel',
            'brand_name': None,
            'collab_type': 'own',
            'thumbnail_url': thumb,
            'source': 'scrape',
        })
        if len(out) >= limit:
            break
    return out


def serialize_tiktok_picker_videos(*sources, handle='', limit=40):
    """Kit-studio picker rows from Login Kit / scrape payloads. No tokens."""
    handle = str(handle or '').strip().lstrip('@')
    out = []
    seen = set()
    cap = max(1, min(int(limit or 40), 60))
    for source in sources:
        for post in _as_list(source):
            if not isinstance(post, dict):
                continue
            vid = str(post.get('id') or post.get('shortCode') or post.get('videoId') or '').strip()
            url = (
                post.get('share_url')
                or post.get('post_url')
                or post.get('url')
                or post.get('webVideoUrl')
                or ''
            )
            url = str(url or '').strip()
            if not url and vid:
                url = f'https://www.tiktok.com/@{handle}/video/{vid}' if handle else f'https://www.tiktok.com/@/video/{vid}'
            if not url:
                continue
            key = f"id:{vid}" if vid else url.lower()
            if key in seen:
                continue
            seen.add(key)
            cover = (
                post.get('cover_image_url')
                or post.get('thumbnail_url')
                or post.get('coverUrl')
                or ((post.get('videoMeta') or {}).get('coverUrl') if isinstance(post.get('videoMeta'), dict) else '')
                or ''
            )
            title = str(post.get('title') or post.get('text') or post.get('caption') or '').strip()[:80]
            description = str(post.get('description') or post.get('caption') or post.get('text') or '').strip()[:200]
            item = {
                'id': vid or None,
                'url': url,
                'title': title,
                'description': description,
                'cover_url': str(cover or '').strip() or None,
            }
            for key_name, value in (
                ('likes', post.get('likes') if post.get('likes') is not None else post.get('like_count') or post.get('diggCount')),
                ('views', post.get('views') if post.get('views') is not None else post.get('view_count') or post.get('playCount')),
                ('comments', post.get('comments') if post.get('comments') is not None else post.get('comment_count') or post.get('commentCount')),
            ):
                try:
                    if value not in (None, ''):
                        item[key_name] = int(value)
                except (TypeError, ValueError):
                    pass
            out.append(item)
            if len(out) >= cap:
                return out
    return out


def social_kit_stats(creator=None, scrape=None, oauth_videos=None, handle=''):
    """Likes / video count / avg views / display name from Login Kit + scrape."""
    creator = creator or {}
    scrape = scrape or {}
    likes = int(creator.get('total_likes') or scrape.get('like_count') or 0)
    video_count = int(
        creator.get('social_media_count')
        or creator.get('total_posts')
        or scrape.get('post_count')
        or 0
    )
    picker = serialize_tiktok_picker_videos(
        oauth_videos,
        scrape.get('recent_posts'),
        handle=handle or creator.get('social_handle') or '',
        limit=40,
    )
    view_vals = [int(v.get('views') or 0) for v in picker if int(v.get('views') or 0) > 0]
    avg_views = int(round(sum(view_vals) / len(view_vals))) if view_vals else 0
    display_name = (
        str(scrape.get('full_name') or '').strip()
        or str(creator.get('display_name') or '').strip()
        or str(creator.get('first_name') or '').strip()
    )
    try:
        engagement = float(creator.get('engagement_rate') or 0) or float(scrape.get('engagement_rate') or 0)
    except (TypeError, ValueError):
        engagement = 0.0
    return {
        'likes_count': likes,
        'video_count': video_count,
        'avg_views': avg_views,
        'display_name': display_name or None,
        'engagement_rate': engagement,
        'tiktok_videos': picker,
    }
