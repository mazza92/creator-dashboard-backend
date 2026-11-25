"""Thanos: Newcollab's one creator-brand matching engine.

Every feature that chooses brands for a creator (Polly suggestions, first matches, Autopilot, the weekly plan,
For You, the hotlist, Monday check-in picks, "brands that answer" alternatives, lifecycle emails) calls
`rank()`. Nothing else in the app ranks brands for creators (tests/test_thanos_guard.py keeps it that way).

    from services.thanos import rank
    ranking = rank(conn, creator_id, surface="polly", n=6)
    ranking.brands   # cards, best first, each with reply_chance / reply_band / reply_reason
    ranking.level    # 0 = their niche ... 5 = follow-ups (the ladder never leaves a creator with nothing)

After recording a pitch, a reply, a roster pick, a kit open or a bounce, call `signals_changed()`.
"""

from services.thanos.engine import SURFACES, Ranking, neighbours, rank, rank_for_niche, score_brands  # noqa: F401
from services.thanos.health import pool_health  # noqa: F401
from services.thanos.profile import CreatorProfile, load_profile  # noqa: F401
from services.thanos.signals import invalidate as signals_changed  # noqa: F401
