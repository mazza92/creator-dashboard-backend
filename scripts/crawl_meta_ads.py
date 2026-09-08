#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
CLI runner for Meta Ads Library → Shopify brand discovery pipeline.

Usage:
    # Daily quota: 50 outreach-ready Shopify brands
    python scripts/crawl_meta_ads.py --daily -o daily_brands.json

    # Custom keywords
    python scripts/crawl_meta_ads.py --keywords skincare "clean beauty" --quota 50 -o brands.json

    # Attach to logged-in Chrome if Ad Library captcha-blocks headless
    python scripts/crawl_meta_ads.py --daily --cdp http://127.0.0.1:9222 -o daily_brands.json

    # Discovery only
    python scripts/crawl_meta_ads.py --keywords skincare --discover-only -o ads.json
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.brand_db_writer import insert_brands_to_pr_brands, insert_from_json
from services.meta_ads_library_scraper import (
    DEFAULT_DAILY_KEYWORDS,
    discover_ads,
    discover_and_enrich,
)


def _print_brands(brands):
    qualified = [b for b in brands if b.get("qualified")]
    print(f"\n=== {len(brands)} Shopify brands ({len(qualified)} qualified) ===")
    for brand in brands:
        q = "YES" if brand.get("qualified") else "no "
        print(
            f"  [Q:{q}] {brand['brand_name']} - {brand['website']}"
        )
        if brand.get("contact_email"):
            print(f"      email: {brand['contact_email']}")
        if brand.get("instagram_handle"):
            print(f"      IG: @{brand['instagram_handle']}")
        if brand.get("hero_product"):
            print(f"      SKU: {brand['hero_product']}")
        if brand.get("creator_platform"):
            print(f"      creator platform: {brand['creator_platform']}")


def main():
    parser = argparse.ArgumentParser(
        description="Discover Shopify DTC brands via Meta Ads Library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        help="Search keywords (default: daily DTC set when --daily)",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Use the default beauty/DTC keyword set and stop at --quota qualified brands",
    )
    parser.add_argument(
        "--quota",
        type=int,
        default=50,
        help="Stop after N qualified brands (Shopify + email or Instagram). Default: 50",
    )
    parser.add_argument(
        "--country",
        default="US",
        help="ISO country code (default: US). Examples: GB, CA, AU",
    )
    parser.add_argument(
        "--max-ads",
        type=int,
        default=40,
        help="Max unique landing domains per keyword (default: 40)",
    )
    parser.add_argument(
        "--max-scroll",
        type=int,
        default=8,
        help="Ad Library scroll passes per keyword (default: 8)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Show the browser (useful for captchas)",
    )
    parser.add_argument(
        "--cdp",
        metavar="URL",
        help="Attach to existing Chrome via CDP (e.g. http://127.0.0.1:9222)",
    )
    parser.add_argument(
        "--debug",
        metavar="FILE",
        help="Save discovered landing domains to JSON (before Shopify filter)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Save enriched brands to JSON file",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only discover ads, skip Shopify enrichment",
    )
    parser.add_argument(
        "--save-to-db",
        action="store_true",
        help="Save enriched brands to pr_brands as status=draft",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview database inserts without writing",
    )
    parser.add_argument(
        "--load-json",
        metavar="FILE",
        help="Load enriched brands from JSON and insert to database",
    )

    args = parser.parse_args()
    headless = not args.headful
    keywords = args.keywords or (list(DEFAULT_DAILY_KEYWORDS) if args.daily else None)

    if args.load_json:
        if not args.save_to_db and not args.dry_run:
            print("ERROR: --load-json requires --save-to-db or --dry-run")
            return
        stats = insert_from_json(args.load_json, dry_run=args.dry_run)
        print(
            f"\nDatabase insert complete: {stats['inserted']} inserted, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )
        return

    if not keywords:
        print("ERROR: pass --keywords ... or --daily")
        return

    if args.discover_only:
        print("[Mode] Discovery only (no enrichment)")
        all_ads = []
        seen = set()
        for keyword in keywords:
            print(f"\n=== Searching for '{keyword}' ===")
            ads = discover_ads(
                keyword,
                country=args.country,
                max_ads=args.max_ads,
                max_scroll=args.max_scroll,
                headless=headless,
                cdp_url=args.cdp,
            )
            for ad in ads:
                domain = ad.get("shopify_domain")
                if domain and domain not in seen:
                    seen.add(domain)
                    all_ads.append(ad)
            print(f"Found {len(ads)} unique domains for '{keyword}'")
            if len(all_ads) >= args.quota * 3:
                break

        if args.debug:
            with open(args.debug, "w", encoding="utf-8") as f:
                json.dump(all_ads, f, indent=2, ensure_ascii=False)
            print(f"\nSaved debug → {args.debug}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(all_ads, f, indent=2, ensure_ascii=False)
            print(f"Saved {len(all_ads)} ads → {args.output}")
        else:
            print(f"\n=== {len(all_ads)} total unique landing domains ===")
            for ad in all_ads[:15]:
                print(f"  - {ad.get('advertiser_name')}: {ad.get('landing_url')}")
            if len(all_ads) > 15:
                print(f"  ... and {len(all_ads) - 15} more")
    else:
        print(
            f"[Mode] Full pipeline (quota {args.quota} qualified, "
            f"{len(keywords)} keywords)"
        )
        brands = discover_and_enrich(
            keywords,
            country=args.country,
            max_ads_per_keyword=args.max_ads,
            max_scroll=args.max_scroll,
            quota=args.quota,
            headless=headless,
            cdp_url=args.cdp,
            debug_dump=args.debug,
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(brands, f, indent=2, ensure_ascii=False)
            print(f"\nSaved {len(brands)} brands → {args.output}")
        else:
            _print_brands(brands)

        if args.save_to_db or args.dry_run:
            print(f"\nInserting {len(brands)} brands to pr_brands...")
            stats = insert_brands_to_pr_brands(brands, dry_run=args.dry_run)
            print(
                f"Database insert {'preview' if args.dry_run else 'complete'}: "
                f"{stats['inserted']} inserted, {stats['skipped']} skipped, "
                f"{stats['errors']} errors"
            )

    print("\nDone!")


if __name__ == "__main__":
    main()
