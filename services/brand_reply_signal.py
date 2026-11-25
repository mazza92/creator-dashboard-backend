"""Reply badge for brand cards and pages, read from Thanos's evidence.

Thanos (services.thanos) is the one place that defines what Newcollab has seen from a brand (signals.load) and
when an inbox is cold (model.is_cold). This module only turns that evidence into the old "reply_signal" badge
({tier, label, detail, pitched, replied, selected}) that pages and Polly cards still show:

- "replies": a creator heard back with interest or a question, or the brand picked someone from a roster
  (the brands Thanos's ladder treats as proven)
- "cold": model.is_cold (15+ creators pitched, under 3% heard anything, never picked anyone)
- None: not enough signal either way

It does not choose or order brands for a creator: that is services.thanos.rank.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _conn(cursor):
    """Thanos reads with its own cursor; callers here hand us theirs."""
    return getattr(cursor, "connection", None) or cursor


def from_thanos(stats) -> Dict[int, Dict[str, int]]:
    """Thanos per-brand evidence -> {brand_id: {pitched, replied, positive, selected}} (distinct creators)."""
    out: Dict[int, Dict[str, int]] = {}
    for bid, ev in (getattr(stats, "brand", None) or {}).items():
        out[int(bid)] = {
            "pitched": int(ev.sends),
            "replied": int(ev.positive + ev.rejected),
            "positive": int(ev.positive),
            "selected": int(ev.selected),
        }
    return out


def load_stats(cursor, force: bool = False) -> Dict[int, Dict[str, int]]:
    from services.thanos import signals

    return from_thanos(signals.load(_conn(cursor), force=force))


def _evidence(stat: Dict[str, Any]):
    from services.thanos.model import Evidence

    replied = int(stat.get("replied") or 0)
    positive = int(stat.get("positive", replied) or 0)
    return Evidence(sends=float(stat.get("pitched") or 0), positive=float(positive),
                    rejected=float(max(replied - positive, 0)), selected=float(stat.get("selected") or 0))


def classify(stat: Optional[Dict[str, int]]) -> Optional[str]:
    if not stat:
        return None
    from services.thanos.model import is_cold

    ev = _evidence(stat)
    if is_cold(ev):
        return "cold"
    if ev.positive + ev.selected > 0:
        return "replies"
    return None


def signal_for(stat: Optional[Dict[str, int]]) -> Optional[Dict[str, Any]]:
    tier = classify(stat)
    if not tier:
        return None
    pitched = int(stat.get("pitched") or 0)
    replied = int(stat.get("replied") or 0)
    selected = int(stat.get("selected") or 0)
    # A count of one reads as proof but is an anecdote: only quote counts from 2 up.
    if tier == "replies":
        if selected >= 2:
            detail = f"Picked {selected} Newcollab creators for gifted PR"
        elif selected:
            detail = "Picked a Newcollab creator for gifted PR"
        elif replied >= 2:
            detail = f"Answered {replied} Newcollab creators who pitched them"
        else:
            detail = "Has answered Newcollab creators who pitched them"
        label = "Replies to creators"
    else:
        heard = "none" if not replied else str(replied)
        detail = f"{pitched} creators pitched on Newcollab, {heard} heard back"
        label = "Long shot"
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


def id_sets(cursor) -> Dict[str, List[int]]:
    """Brand ids per tier, for SQL filters (b.id = ANY(%s))."""
    replies, cold = [], []
    for bid, stat in load_stats(cursor).items():
        tier = classify(stat)
        if tier == "replies":
            replies.append(bid)
        elif tier == "cold":
            cold.append(bid)
    return {"replies": replies or [0], "cold": cold or [0]}
