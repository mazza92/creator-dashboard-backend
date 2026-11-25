"""Thanos profile: everything about a creator that matching reads, resolved once per ranking.

Niche truth, in order: the niches saved on the creator record (Settings / onboarding), then what they told Polly,
then the signup niche. The scrape (bio, themes, primary niche) sharpens the fit score and may add its lane only
when it agrees with those niches (services.thanos.niches).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from services.thanos.model import size_band


@dataclass
class CreatorProfile:
    creator_id: int
    user_id: Optional[int] = None
    followers: int = 0
    country: str = ""
    interests: List[str] = field(default_factory=list)     # niches the creator picked
    categories: List[str] = field(default_factory=list)    # directory categories they match (core lane)
    roster_categories: Set[str] = field(default_factory=set)
    excluded_categories: List[str] = field(default_factory=list)
    fit_profile: Dict[str, Any] = field(default_factory=dict)
    scrape: Dict[str, Any] = field(default_factory=dict)
    notes: Dict[str, Any] = field(default_factory=dict)
    pitched_ids: Set[int] = field(default_factory=set)     # sent, applied, queued: never suggested again
    saved_ids: Set[int] = field(default_factory=set)       # in their list but not sent
    repitch_ids: Set[int] = field(default_factory=set)     # sent 60+ days ago, no reply, no "no"

    @property
    def band(self) -> str:
        return size_band(self.followers)


def _rows(conn, sql: str, params) -> List[Any]:
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall() or []
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] profile query skipped: {err}")
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


def _val(row, key, idx):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[idx]


def _parse_niches(raw) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(n).strip().lower() for n in raw if str(n).strip()]
    text = str(raw)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(n).strip().lower() for n in parsed if str(n).strip()]
        return [str(parsed).strip().lower()] if str(parsed).strip() else []
    except (ValueError, TypeError):
        return [n.strip().lower() for n in text.split(",") if n.strip()]


def _ids(rows) -> Set[int]:
    out = set()
    for r in rows:
        try:
            out.add(int(_val(r, "brand_id", 0)))
        except (TypeError, ValueError):
            continue
    return out


def load_profile(conn, creator_id: int, notes: Optional[Dict] = None, scrape: Optional[Dict] = None) -> CreatorProfile:
    from services.thanos import niches as N

    creator_id = int(creator_id)
    row = (_rows(conn, """
        SELECT c.user_id, c.niche, c.creator_niches, c.creator_followers, c.followers_count, c.country,
               mk.total_followers
        FROM creators c LEFT JOIN media_kits mk ON mk.creator_id = c.id
        WHERE c.id = %s LIMIT 1
    """, (creator_id,)) or [None])[0]
    prof = CreatorProfile(creator_id=creator_id)
    if row is not None:
        prof.user_id = _val(row, "user_id", 0)
        for key, idx in (("creator_followers", 3), ("total_followers", 6), ("followers_count", 4)):
            try:
                value = int(_val(row, key, idx) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                prof.followers = value
                break
        prof.country = str(_val(row, "country", 5) or "").strip().upper()

    if notes is None:
        try:
            from services.polly_memory import load_thread
            notes = load_thread(conn, creator_id).get("notes") or {}
        except Exception as err:  # noqa: BLE001
            print(f"[Thanos] notes skipped: {err}")
            notes = {}
    prof.notes = notes or {}

    recorded = _parse_niches(_val(row, "creator_niches", 2)) if row is not None else []
    stated: List[str] = []
    try:
        from services.polly_discovery import stated_niches
        stated = stated_niches(prof.notes)
    except Exception:  # noqa: BLE001
        stated = []
    signup = _parse_niches(_val(row, "niche", 1)) if row is not None else []
    prof.interests = recorded or stated or signup

    if scrape is None and prof.user_id:
        try:
            from services.creator_profile_scraper import CreatorProfileScraper
            scrape = CreatorProfileScraper(conn).get_creator_profile(prof.user_id) or {}
        except Exception as err:  # noqa: BLE001
            print(f"[Thanos] scrape skipped: {err}")
            scrape = {}
    prof.scrape = scrape or {}

    prof.categories = sorted(set(N._build_for_you_category_pool(prof.scrape or None, prof.interests) or []))
    prof.roster_categories = set(N._roster_category_pool(prof.scrape or None, prof.interests) or [])
    prof.excluded_categories = N.excluded_categories_for(prof.interests)
    try:
        prof.fit_profile = N._prepare_for_you_profile(dict(prof.scrape or {}), prof.interests, prof.followers) or {}
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] fit profile skipped: {err}")
        prof.fit_profile = N._minimal_fit_profile_from_niches(prof.interests, prof.followers)

    sent = _ids(_rows(conn, """
        SELECT brand_id FROM creator_pipeline
        WHERE creator_id = %s AND brand_id IS NOT NULL AND COALESCE(stage, '') <> 'saved'
        UNION SELECT brand_id FROM brand_pr_applications WHERE creator_id = %s AND brand_id IS NOT NULL
        UNION SELECT brand_id FROM polly_autopilot_queue
              WHERE creator_id = %s AND brand_id IS NOT NULL AND status <> 'skipped'
        UNION SELECT brand_id FROM polly_timeline_events
              WHERE creator_id = %s AND brand_id IS NOT NULL
                AND event_type IN ('pitch_sent', 'brand_replied_rejected')
    """, (creator_id, creator_id, creator_id, creator_id)))
    try:
        from services.polly import pitched_id_set
        sent |= pitched_id_set(prof.notes)
    except Exception:  # noqa: BLE001
        pass
    prof.pitched_ids = sent
    prof.saved_ids = _ids(_rows(conn, """
        SELECT brand_id FROM creator_pipeline
        WHERE creator_id = %s AND brand_id IS NOT NULL AND stage = 'saved'
    """, (creator_id,))) - sent
    prof.repitch_ids = _ids(_rows(conn, """
        SELECT e.brand_id FROM polly_timeline_events e
        WHERE e.creator_id = %s AND e.brand_id IS NOT NULL AND e.event_type = 'pitch_sent'
        GROUP BY e.brand_id
        HAVING MAX(e.occurred_at) < NOW() - INTERVAL '60 days'
           AND NOT EXISTS (
               SELECT 1 FROM polly_timeline_events r
               WHERE r.creator_id = %s AND r.brand_id = e.brand_id
                 AND r.event_type IN ('brand_replied_interested', 'brand_replied_question',
                                      'brand_replied_rejected', 'email_bounced'))
    """, (creator_id, creator_id)))
    return prof
