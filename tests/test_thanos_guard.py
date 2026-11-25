"""Guard: Thanos is the only creator-brand matching engine (Nov 2026 decision).

Ten separate systems used to pick brands for creators (Polly suggestions, first matches, Autopilot, the weekly
plan, For You, the hotlist, the daily hot brand, Radar, Monday picks, emails), each with its own reach rule,
reply-tier sort and fallbacks. They were replaced by services/thanos. This test fails if one comes back.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"tests", "scripts", "migrations", "venv", ".venv", "node_modules", ".git", "__pycache__"}

# Rankers that were deleted: defining or calling them again means a second matching engine.
RETIRED = [
    r"\bdef _rank_by_reach\b", r"\bdef _directory_topup\b", r"\bdef _rank_for_you_with_mentor\b",
    r"\bdef _apply_mentor_fit_to_for_you\b", r"\bdef rank_matches_deep\b", r"\bdef rerank_by_fit\b",
    r"\bdef _fallback_brand_pool\b", r"\bdef _fetch_brands_by_category\b", r"\bdef proven_niche_brands\b",
    r"\b_for_you_pool\(", r"\bflatten_for_you\(",
]
# Ordering creators' brand lists by the stale response_rate column, or at random.
SQL_RANKING = [
    r"ORDER BY[^;\"]{0,200}response_rate",
    r"FROM pr_brands[^;\"]{0,600}ORDER BY[^;\"]{0,80}RANDOM\(\)",
]


def _py_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT)
        if rel.startswith(os.path.join("services", "thanos")):
            continue
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_retired_rankers():
    hits = []
    for path in _py_files():
        src = open(path, encoding="utf-8", errors="ignore").read()
        for pattern in RETIRED:
            if re.search(pattern, src):
                hits.append(f"{os.path.relpath(path, ROOT)}: {pattern}")
    assert not hits, "Old brand matching is back (use services.thanos.rank):\n" + "\n".join(hits)


def test_no_sql_ranking_by_response_rate_or_random():
    hits = []
    for path in _py_files():
        src = open(path, encoding="utf-8", errors="ignore").read()
        for pattern in SQL_RANKING:
            for m in re.finditer(pattern, src, re.S | re.I):
                hits.append(f"{os.path.relpath(path, ROOT)}: {m.group(0)[:80]!r}")
    assert not hits, "Brand lists must be ordered by Thanos:\n" + "\n".join(hits)


def test_thanos_is_the_api():
    import services.thanos as thanos
    for name in ("rank", "score_brands", "load_profile", "signals_changed", "pool_health"):
        assert hasattr(thanos, name)
