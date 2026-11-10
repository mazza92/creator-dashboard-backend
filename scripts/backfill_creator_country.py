"""One-off: set creators.country for existing creators when it is unambiguous.

Sources, in order:
- users.country holding a served country's name or code ("United States", "UK", "France")
- creators.regions that is exactly ["US"], ["UK"] or ["Canada"]
When both are present and disagree the creator is skipped. Nothing is ever set to 'OTHER', and
creators.regions is only filled when it is empty (an existing regions list is left as the creator chose it).

Dry run by default:  python scripts/backfill_creator_country.py
Apply:               python scripts/backfill_creator_country.py --apply
"""
import os
import sys
from collections import Counter
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.creator_country import (  # noqa: E402
    OTHER,
    _as_list,
    country_from_regions,
    ensure_schema,
    normalize_code,
    regions_json,
)


def decide(row: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(code or None, reason) for one creator row with user_country, regions, country."""
    if str(row.get("country") or "").strip():
        return None, "already set"
    from_user = normalize_code(row.get("user_country"))
    if from_user == OTHER:
        from_user = None
    from_regions = country_from_regions(row.get("regions"))
    if from_user and from_regions and from_user != from_regions:
        return None, "users.country and regions disagree"
    if from_user:
        return from_user, "from users.country"
    if from_regions:
        return from_regions, "from regions"
    if str(row.get("user_country") or "").strip():
        return None, "users.country not a served country"
    return None, "no signal"


def main(apply: bool) -> None:
    from dotenv import load_dotenv
    import psycopg2
    from psycopg2.extras import RealDictCursor

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    cur = conn.cursor()
    if apply:
        ensure_schema(cur)
        conn.commit()
    has_country = True
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'creators' AND column_name = 'country'")
    if not cur.fetchone():
        has_country = False  # dry run before the column exists: every creator counts as unset
    cur.execute(f"""SELECT c.id, c.regions::text AS regions, u.country AS user_country,
                           {'c.country' if has_country else 'NULL'} AS country
                    FROM creators c JOIN users u ON u.id = c.user_id""")
    stats, examples = Counter(), []
    for r in cur.fetchall():
        code, reason = decide(r)
        stats[reason] += 1
        if not code:
            continue
        stats[f"set {code}"] += 1
        if len(examples) < 10:
            examples.append((r["id"], r["user_country"], r["regions"], code))
        if apply:
            if _as_list(r["regions"]):
                cur.execute("UPDATE creators SET country = %s WHERE id = %s AND country IS NULL", (code, r["id"]))
            else:
                cur.execute("UPDATE creators SET country = %s, regions = %s WHERE id = %s AND country IS NULL",
                            (code, regions_json(code), r["id"]))
    if apply:
        conn.commit()
    print(("APPLIED" if apply else "DRY RUN"), dict(stats))
    for e in examples:
        print("  id=%s users.country=%r regions=%s -> %s" % e)
    conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
