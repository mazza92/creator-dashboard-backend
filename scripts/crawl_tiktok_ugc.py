#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Find TikTok profiles: UGC + niche + email + 1k+ followers (location optional).

Volume defaults: 1000 qualified / 2000 enriched.

Usage:
    python scripts/crawl_tiktok_ugc.py --daily -o daily_tiktok_ugc.json
    python scripts/crawl_tiktok_ugc.py --handles hannahs.ugccorner
    python scripts/crawl_tiktok_ugc.py --daily --save-to-db --quota 1000
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.tiktok_ugc_lead_writer import insert_leads
from services.tiktok_ugc_profile_scraper import (
    DEFAULT_MAX_HANDLES,
    DEFAULT_NICHES,
    DEFAULT_QUOTA,
    DEFAULT_SERP_PAGES,
    DEFAULT_WORKERS,
    MIN_FOLLOWERS,
    default_search_queries,
    discover_and_enrich,
    enrich_handle,
)


def _print(records):
    qualified = [r for r in records if r.get("qualified")]
    print(f"\n=== {len(records)} profiles ({len(qualified)} qualified) ===")
    for rec in records:
        q = "YES" if rec.get("qualified") else "no "
        print(f"  [Q:{q}] @{rec.get('handle')}  {rec.get('display_name') or ''}")
        print(
            f"      ugc={rec.get('has_ugc')} niche={rec.get('niche')} "
            f"followers={rec.get('followers')} email={rec.get('contact_email')}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Discover TikTok UGC creators (UGC + niche + email + 1k followers)",
    )
    parser.add_argument("--daily", action="store_true", help="Full volume query set")
    parser.add_argument("--queries", nargs="+", help="Custom search queries")
    parser.add_argument("--niches", nargs="+", default=None, help="Niche tokens to require")
    parser.add_argument("--handles", nargs="+", help="Skip search; enrich these TikTok handles")
    parser.add_argument(
        "--quota",
        type=int,
        default=DEFAULT_QUOTA,
        help=f"Stop after N qualified profiles (default {DEFAULT_QUOTA})",
    )
    parser.add_argument(
        "--min-followers",
        type=int,
        default=MIN_FOLLOWERS,
        help=f"Minimum follower count (default {MIN_FOLLOWERS})",
    )
    parser.add_argument(
        "--max-handles",
        type=int,
        default=DEFAULT_MAX_HANDLES,
        help=f"Max profiles to enrich (default {DEFAULT_MAX_HANDLES})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel TikTok scrapes (default {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--serp-pages",
        type=int,
        default=DEFAULT_SERP_PAGES,
        help=f"Google/Bing result pages per engine (default {DEFAULT_SERP_PAGES})",
    )
    parser.add_argument(
        "--ignore-seen",
        action="store_true",
        help="Re-enrich handles already in the local seen file",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Do not follow @mentions from bios/captions",
    )
    parser.add_argument("--output", "-o", metavar="FILE")
    parser.add_argument("--save-to-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-unqualified",
        action="store_true",
        help="Also insert profiles missing a field (still tagged as draft)",
    )
    args = parser.parse_args()

    niches = args.niches or list(DEFAULT_NICHES)

    if args.handles:
        records = []
        for handle in args.handles:
            rec = enrich_handle(
                handle.lstrip("@"),
                niches=niches,
                min_followers=args.min_followers,
            )
            if rec:
                records.append(rec)
    else:
        queries = args.queries or (default_search_queries(niches) if args.daily else None)
        if not queries:
            print("ERROR: pass --daily, --queries, or --handles")
            return
        records = discover_and_enrich(
            queries,
            quota=args.quota,
            max_handles=args.max_handles,
            niches=niches,
            min_followers=args.min_followers,
            workers=args.workers,
            serp_pages=args.serp_pages,
            ignore_seen=args.ignore_seen,
            expand_graph=not args.no_graph,
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(records)} profiles -> {args.output}")
    else:
        _print(records)

    if args.save_to_db or args.dry_run:
        stats = insert_leads(
            records,
            dry_run=args.dry_run,
            only_qualified=not args.include_unqualified,
        )
        print(
            f"Database {'preview' if args.dry_run else 'insert'}: "
            f"{stats['inserted']} inserted, {stats['skipped']} skipped, "
            f"{stats['errors']} errors"
        )


if __name__ == "__main__":
    main()
