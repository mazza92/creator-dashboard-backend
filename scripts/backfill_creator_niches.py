"""One-off: make every creator's niche consistent (Settings ids in creator_niches + niche + Polly notes).

Dry run by default:  python scripts/backfill_creator_niches.py
Apply:               python scripts/backfill_creator_niches.py --apply
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from services.creator_niches import recorded_niches  # noqa: E402


def main(apply: bool) -> None:
    conn = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("""SELECT c.id, c.niche, c.creator_niches, t.notes -> 'niche' AS polly_niche
                   FROM creators c LEFT JOIN polly_threads t ON t.creator_id = c.id""")
    stats, examples = Counter(), []
    for r in cur.fetchall():
        want = recorded_niches(r)
        if not want:
            stats["no usable niche (left as is)"] += 1
            continue
        cols = list(r["creator_niches"] or []) != want or r["niche"] != json.dumps(want)
        polly = r["polly_niche"] is not None and r["polly_niche"] != want
        if not cols and not polly:
            stats["already consistent"] += 1
            continue
        stats["columns fixed" if cols else "polly copy only"] += 1
        if polly:
            stats["polly copy fixed"] += 1
        if len(examples) < 8 and (r["creator_niches"] or []) != want:
            examples.append((r["id"], r["creator_niches"], r["niche"], r["polly_niche"], want))
        if apply:
            cur.execute("UPDATE creators SET creator_niches = %s, niche = %s WHERE id = %s",
                        (want, json.dumps(want), r["id"]))
            if polly:
                cur.execute("UPDATE polly_threads SET notes = jsonb_set(notes, '{niche}', %s::jsonb) "
                            "WHERE creator_id = %s", (json.dumps(want), r["id"]))
    if apply:
        conn.commit()
    print(("APPLIED" if apply else "DRY RUN"), dict(stats))
    for e in examples:
        print("  id=%s creator_niches=%s niche=%s polly=%s -> %s" % e)
    conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
