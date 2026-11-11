#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Backfill Meta Ad Library signals (live ad count, first/last seen, Ad Library link, samples, UGC style) for brands
already in pr_brands with source='meta_ads_library', then re-run the quality gate for them.

Needs Playwright + Chromium, so it runs on a machine (a laptop or a worker box), not on Vercel. When the Ad Library
shows a login wall or captcha headless, attach to a logged-in Chrome with --cdp.

Usage:
    python scripts/backfill_meta_ad_signals.py --limit 100
    python scripts/backfill_meta_ad_signals.py --brand-ids 12 34 --headful
    python scripts/backfill_meta_ad_signals.py --cdp http://127.0.0.1:9222 --limit 300 --stale-days 7
    python scripts/backfill_meta_ad_signals.py --seed-notes-only     # no browser: keep the notes' ad sample only

Env: DATABASE_URL (same as scripts/crawl_meta_ads.py). GEMINI key optional (inconclusive UGC samples).
"""
import argparse
import os
import random
import sys
import time

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from services import meta_ads_signal as sig  # noqa: E402


def _domain(website):
    from urllib.parse import urlparse

    url = website or ""
    if url and not url.startswith("http"):
        url = "https://" + url
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def main():
    ap = argparse.ArgumentParser(description="Backfill Meta Ad Library signals for meta_ads_library brands")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--stale-days", type=int, default=7, help="Re-check brands checked longer ago than this")
    ap.add_argument("--brand-ids", nargs="+", type=int)
    ap.add_argument("--country", default="ALL")
    ap.add_argument("--max-scroll", type=int, default=4)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--cdp", metavar="URL")
    ap.add_argument("--no-llm", action="store_true", help="Heuristics only for UGC style")
    ap.add_argument("--no-gate", action="store_true", help="Skip re-running the quality gate afterwards")
    ap.add_argument("--seed-notes-only", action="store_true", help="No browser: seed samples from notes")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set")
        return 1
    conn = psycopg2.connect(db_url)
    sig.ensure_table(conn)
    seeded = sig.seed_from_notes(conn)
    print(f"[backfill] seeded {seeded} brands from notes")
    if args.seed_notes_only:
        return 0

    todo = sig.stale_brands(conn, older_than_days=args.stale_days, limit=args.limit, brand_ids=args.brand_ids)
    print(f"[backfill] {len(todo)} brands to check against the Ad Library")
    if not todo:
        return 0

    from playwright.sync_api import sync_playwright

    from services.meta_ads_library_scraper import _open_browser, _save_browser_state, fetch_brand_ads

    done = []
    with sync_playwright() as p:
        browser, context, owns = _open_browser(p, headless=not args.headful, cdp_url=args.cdp)
        page = context.new_page()
        try:
            for i, brand in enumerate(todo, 1):
                domain = _domain(brand.get("website"))
                name = brand.get("brand_name") or domain
                if not domain:
                    print(f"[backfill] {i}/{len(todo)} {name}: no website, skipped")
                    continue
                try:
                    got = fetch_brand_ads(page, name, domain, page_id=brand.get("page_id"), country=args.country,
                                          max_scroll=args.max_scroll)
                except Exception as err:
                    print(f"[backfill] {i}/{len(todo)} {name}: fetch failed: {err}")
                    continue
                cards = sig.normalize_cards(got["cards"])
                if got.get("page_id") is None:
                    cards = sig.cards_for_domain(cards, domain)
                if cards:
                    signals = sig.summarize(cards, results_count=got.get("results"), page_id=got.get("page_id"),
                                            query=got.get("query"), source="page" if got.get("page_id") else "keyword_search")
                else:
                    signals = sig.no_live_ads(int(brand["id"]), got.get("query"), got.get("page_id"))
                print(f"[backfill] {i}/{len(todo)} {name}: {signals['active_ad_count']} live ads, "
                      f"first {signals.get('ads_first_seen')}, page {got.get('page_id')}")
                if not args.dry_run:
                    saved = sig.save(conn, int(brand["id"]), signals, use_llm=not args.no_llm)
                    print(f"           ugc={saved.get('ugc_style')} ({saved.get('ugc_style_reason')})")
                    done.append(int(brand["id"]))
                time.sleep(random.uniform(1.5, 3.0))
            _save_browser_state(context)
        finally:
            if owns:
                browser.close()
            else:
                page.close()

    if done and not args.no_gate and not args.dry_run:
        from services.brand_quality_gate import run_gate

        out = run_gate(conn, brand_ids=done)
        print(f"[backfill] gate: {out}")
    conn.close()
    print(f"RESULT_AD_SIGNALS checked={len(done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
