"""Which brands actually answer creators, from what creators logged on Newcollab.

Evidence (per brand, distinct creators):
- pitched: pipeline rows that were sent (pitched_at / send_confirmed / post-send stage)
- replied: pipeline replies, or Polly "brand replied" timeline events
- selected: gifted-roster applications the brand picked (ships / posted)

Tiers:
- "replies": picked creators from a roster, or >=5% of senders heard back
- "cold": 15+ creators pitched, under 3% heard back, never picked anyone
- None: not enough signal either way

Free credits should not be spent on cold brands without a warning, and every
recommendation list sorts replies first and cold last.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional

COLD_MIN_PITCHED = 15
COLD_MAX_RATE = 0.03
REPLIES_MIN_RATE = 0.05
CACHE_TTL_SECONDS = 15 * 60

TIER_RANK = {"replies": 0, None: 1, "cold": 2}

_CACHE: Dict[str, Any] = {"at": 0.0, "stats": {}}
_LOCK = threading.Lock()

_REPLIED_STAGES = ("replied", "won", "received", "responded", "success")
_SENT_STAGES = ("pitched", "waiting", "followup") + _REPLIED_STAGES

_PIPELINE_SQL = f"""
    SELECT brand_id,
           COUNT(DISTINCT creator_id) FILTER (
               WHERE pitched_at IS NOT NULL OR send_confirmed IS TRUE
                  OR stage IN ({",".join(f"'{s}'" for s in _SENT_STAGES)})
           ) AS pitched,
           COUNT(DISTINCT creator_id) FILTER (
               WHERE replied_at IS NOT NULL
                  OR stage IN ({",".join(f"'{s}'" for s in _REPLIED_STAGES)})
           ) AS replied
    FROM creator_pipeline
    WHERE brand_id IS NOT NULL
    GROUP BY brand_id
"""

_EVENTS_SQL = """
    SELECT brand_id, COUNT(DISTINCT creator_id) AS replied
    FROM polly_timeline_events
    WHERE brand_id IS NOT NULL
      AND event_type IN ('brand_replied_interested', 'brand_replied_question', 'brand_replied_rejected')
    GROUP BY brand_id
"""

_APPS_SQL = """
    SELECT brand_id, COUNT(DISTINCT creator_id) AS selected
    FROM brand_pr_applications
    WHERE brand_id IS NOT NULL AND status IN ('ships', 'posted')
    GROUP BY brand_id
"""


def _safe_rows(cursor, sql: str) -> List[Dict[str, Any]]:
    """Run one query without poisoning the caller's transaction if a table is missing."""
    try:
        cursor.execute("SAVEPOINT brand_reply_signal")
    except Exception:
        return []
    try:
        cursor.execute(sql)
        rows = [dict(r) if not isinstance(r, dict) else r for r in cursor.fetchall()]
        cursor.execute("RELEASE SAVEPOINT brand_reply_signal")
        return rows
    except Exception as err:
        print(f"[ReplySignal] query skipped: {err}")
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT brand_reply_signal")
        except Exception:
            pass
        return []


def _row_get(row: Any, key: str, idx: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[idx]
    except Exception:
        return None


def build_stats(pipeline_rows: Iterable, event_rows: Iterable, app_rows: Iterable) -> Dict[int, Dict[str, int]]:
    stats: Dict[int, Dict[str, int]] = {}

    def slot(bid) -> Optional[Dict[str, int]]:
        try:
            key = int(bid)
        except (TypeError, ValueError):
            return None
        return stats.setdefault(key, {"pitched": 0, "replied": 0, "selected": 0})

    for row in pipeline_rows or []:
        s = slot(_row_get(row, "brand_id", 0))
        if s is None:
            continue
        s["pitched"] = int(_row_get(row, "pitched", 1) or 0)
        s["replied"] = max(s["replied"], int(_row_get(row, "replied", 2) or 0))
    for row in event_rows or []:
        s = slot(_row_get(row, "brand_id", 0))
        if s is None:
            continue
        s["replied"] = max(s["replied"], int(_row_get(row, "replied", 1) or 0))
    for row in app_rows or []:
        s = slot(_row_get(row, "brand_id", 0))
        if s is None:
            continue
        s["selected"] = int(_row_get(row, "selected", 1) or 0)
    return stats


def load_stats(cursor, force: bool = False) -> Dict[int, Dict[str, int]]:
    now = time.time()
    if not force and _CACHE["stats"] and now - _CACHE["at"] < CACHE_TTL_SECONDS:
        return _CACHE["stats"]
    with _LOCK:
        if not force and _CACHE["stats"] and time.time() - _CACHE["at"] < CACHE_TTL_SECONDS:
            return _CACHE["stats"]
        stats = build_stats(
            _safe_rows(cursor, _PIPELINE_SQL),
            _safe_rows(cursor, _EVENTS_SQL),
            _safe_rows(cursor, _APPS_SQL),
        )
        _CACHE["stats"] = stats
        _CACHE["at"] = time.time()
        return stats


def classify(stat: Optional[Dict[str, int]]) -> Optional[str]:
    if not stat:
        return None
    pitched = int(stat.get("pitched") or 0)
    replied = int(stat.get("replied") or 0)
    selected = int(stat.get("selected") or 0)
    if selected >= 1:
        return "replies"
    rate = (replied / pitched) if pitched else 0.0
    if replied >= 1 and pitched and rate >= REPLIES_MIN_RATE:
        return "replies"
    if pitched >= COLD_MIN_PITCHED and rate < COLD_MAX_RATE:
        return "cold"
    return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def signal_for(stat: Optional[Dict[str, int]]) -> Optional[Dict[str, Any]]:
    tier = classify(stat)
    if not tier:
        return None
    pitched = int(stat.get("pitched") or 0)
    replied = int(stat.get("replied") or 0)
    selected = int(stat.get("selected") or 0)
    if tier == "replies":
        if selected:
            detail = f"Picked {_plural(selected, 'Newcollab creator')} for gifted PR"
        else:
            detail = f"{_plural(replied, 'creator')} heard back after pitching"
        label = "Replies to creators"
    else:
        heard = "none" if not replied else str(replied)
        detail = f"{pitched} creators pitched on Newcollab, {heard} heard back"
        label = "Rarely replies"
    return {
        "tier": tier,
        "label": label,
        "detail": detail,
        "pitched": pitched,
        "replied": replied,
        "selected": selected,
    }


def brand_signal(cursor, brand_id: Any) -> Optional[Dict[str, Any]]:
    try:
        bid = int(brand_id)
    except (TypeError, ValueError):
        return None
    return signal_for(load_stats(cursor).get(bid))


def tier_of(brand: Dict[str, Any]) -> Optional[str]:
    sig = (brand or {}).get("reply_signal") or {}
    return sig.get("tier") if isinstance(sig, dict) else None


def annotate(brands: Optional[List[Dict[str, Any]]], stats: Dict[int, Dict[str, int]]) -> List[Dict[str, Any]]:
    out = []
    for raw in brands or []:
        b = dict(raw) if not isinstance(raw, dict) else raw
        try:
            bid = int(b.get("id") or b.get("brand_id"))
        except (TypeError, ValueError):
            bid = None
        sig = signal_for(stats.get(bid)) if bid is not None else None
        if sig:
            b["reply_signal"] = sig
        else:
            b.pop("reply_signal", None)
        out.append(b)
    return out


def sort_by_signal(brands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable: replies first, cold last, otherwise keep the caller's order."""
    return sorted(brands, key=lambda b: TIER_RANK.get(tier_of(b), 1))


def rerank_by_fit(brands: List[Dict[str, Any]], bonus: int = 12, penalty: int = 30) -> List[Dict[str, Any]]:
    """Blend with match_score so a strong fit can still beat an unknown brand, but cold sinks."""
    def adjusted(pair):
        idx, b = pair
        score = float(b.get("match_score") or 0)
        tier = tier_of(b)
        if tier == "replies":
            score += bonus
        elif tier == "cold":
            score -= penalty
        return (-score, idx)

    return [b for _, b in sorted(enumerate(brands), key=adjusted)]


def annotate_and_sort(cursor, brands, fit_aware: bool = False) -> List[Dict[str, Any]]:
    rows = annotate(brands, load_stats(cursor))
    return rerank_by_fit(rows) if fit_aware else sort_by_signal(rows)


def id_sets(cursor) -> Dict[str, List[int]]:
    """Brand ids per tier, for SQL ORDER BY (b.id = ANY(%s))."""
    replies, cold = [], []
    for bid, stat in load_stats(cursor).items():
        tier = classify(stat)
        if tier == "replies":
            replies.append(bid)
        elif tier == "cold":
            cold.append(bid)
    return {"replies": replies or [0], "cold": cold or [0]}


def responsive_alternatives(cursor, category: str = "", exclude_ids: Optional[List[int]] = None, limit: int = 3, min_follower_cap: Optional[int] = None) -> List[Dict[str, Any]]:
    """Same-category brands with reply evidence first, never cold ones."""
    sets = id_sets(cursor)
    exclude = [int(x) for x in (exclude_ids or []) if str(x).isdigit()] or [0]
    params: List[Any] = [sets["replies"], exclude, sets["cold"]]
    cat_clause = ""
    if category:
        cat_clause = "AND LOWER(b.category) = LOWER(%s)"
        params.append(category)
    cap_clause = ""
    if min_follower_cap:
        cap_clause = "AND (b.min_followers IS NULL OR b.min_followers <= %s)"
        params.append(min_follower_cap)
    params += [sets["replies"], limit]
    sql = f"""
        SELECT b.id, b.slug, b.brand_name AS name, b.logo_url AS logo, b.category,
               b.response_rate, b.micro_friendly, (b.id = ANY(%s)) AS has_replies
        FROM pr_brands b
        WHERE b.slug IS NOT NULL
          AND COALESCE(b.status, 'published') = 'published'
          AND b.id != ALL(%s)
          AND b.id != ALL(%s)
          {cat_clause}
          {cap_clause}
        ORDER BY (b.id = ANY(%s)) DESC, COALESCE(b.micro_friendly, FALSE) DESC,
                 COALESCE(b.response_rate, 0) DESC, b.id DESC
        LIMIT %s
    """
    try:
        cursor.execute("SAVEPOINT brand_reply_alt")
        cursor.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.execute("RELEASE SAVEPOINT brand_reply_alt")
    except Exception as err:
        print(f"[ReplySignal] alternatives skipped: {err}")
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT brand_reply_alt")
        except Exception:
            pass
        return []
    return annotate(rows, load_stats(cursor))
