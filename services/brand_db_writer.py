# -*- coding: utf-8 -*-
"""
Database writer for enriched brand records → pr_brands table.

Inserts Shopify brand discoveries from Meta Ads Library into the pr_brands
table with status='draft' for admin / Hermes outreach.

Dedupes by website domain and brand_name. Tags notes with META_ADS_OUTREACH
so scrape leads are easy to filter.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

OUTREACH_TAG = "META_ADS_OUTREACH"
SOURCE = "meta_ads_library"


def _normalize_domain(url: str) -> Optional[str]:
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower().removeprefix("www.")
        return domain if domain else None
    except Exception:
        return None


def create_slug(brand_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (brand_name or "").lower()).strip("-")
    return slug or f"brand-{int(time.time())}"


def build_outreach_note(brand: Dict, *, batch_date: str) -> str:
    """Stable note Hermes / humans can filter on."""
    bits = [
        f"[{OUTREACH_TAG}]",
        "Shopify DTC lead from Meta Ads Library.",
        "Running paid Meta ads — gifted PR / UGC outreach target.",
        f"Batch {batch_date}.",
    ]
    if brand.get("qualified"):
        bits.append("Qualified: has email or Instagram.")
    else:
        bits.append("Shopify-only: no public email/IG yet.")
    if brand.get("hero_product"):
        bits.append(f"Hero SKU: {brand['hero_product']}.")
    if brand.get("has_creator_program"):
        platform = brand.get("creator_platform") or brand.get("creator_program_url") or "yes"
        bits.append(f"Creator program: {platform}.")
    if brand.get("ad_creative_sample"):
        sample = re.sub(r"\s+", " ", str(brand["ad_creative_sample"])).strip()[:160]
        if sample:
            bits.append(f"Ad sample: {sample}")
    return " ".join(bits)


def insert_brands_to_pr_brands(
    brands: List[Dict],
    *,
    dry_run: bool = False,
    dedupe_by_domain: bool = True,
    only_qualified: bool = False,
    batch_date: Optional[str] = None,
) -> Dict[str, int]:
    """
    Insert enriched Meta Ads brands as status='draft' with META_ADS_OUTREACH notes.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        print("[BrandDB] ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        return {"inserted": 0, "skipped": 0, "errors": len(brands)}

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[BrandDB] ERROR: DATABASE_URL not found in environment")
        return {"inserted": 0, "skipped": 0, "errors": len(brands)}

    batch = batch_date or datetime.now(timezone.utc).date().isoformat()
    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    if dry_run:
        print("[BrandDB] DRY RUN MODE - no database writes will be performed")
        print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        existing_domains = set()
        existing_names = set()
        cursor.execute(
            "SELECT brand_name, website FROM pr_brands WHERE website IS NOT NULL OR brand_name IS NOT NULL"
        )
        for row in cursor.fetchall():
            name = (row.get("brand_name") or "").strip().lower()
            if name:
                existing_names.add(name)
            domain = _normalize_domain(row.get("website") or "")
            if domain:
                existing_domains.add(domain)
        print(
            f"[BrandDB] Loaded {len(existing_domains)} domains / "
            f"{len(existing_names)} names for dedupe"
        )

        for brand in brands:
            try:
                if only_qualified and not brand.get("qualified"):
                    stats["skipped"] += 1
                    continue

                website = brand.get("website")
                brand_name = (brand.get("brand_name") or "").strip()
                if not website or not brand_name:
                    print(f"[BrandDB] Skipping — missing name/website: {brand_name or website}")
                    stats["skipped"] += 1
                    continue

                domain = _normalize_domain(website)
                if dedupe_by_domain and domain and domain in existing_domains:
                    print(f"[BrandDB] Skipping {brand_name} — domain {domain} exists")
                    stats["skipped"] += 1
                    continue
                if brand_name.lower() in existing_names:
                    print(f"[BrandDB] Skipping {brand_name} — name already exists")
                    stats["skipped"] += 1
                    continue

                notes = build_outreach_note(brand, batch_date=batch)
                slug = create_slug(brand_name)
                cursor.execute("SELECT id FROM pr_brands WHERE slug = %s", (slug,))
                if cursor.fetchone():
                    slug = f"{slug}-{int(time.time())}"

                record = {
                    "brand_name": brand_name,
                    "slug": slug,
                    "website": website,
                    "description": brand.get("description"),
                    "contact_email": brand.get("contact_email"),
                    "instagram_handle": brand.get("instagram_handle"),
                    "tiktok_handle": brand.get("tiktok_handle"),
                    "category": "beauty",
                    "status": "draft",
                    "micro_friendly": bool(brand.get("micro_friendly")),
                    "hero_product": brand.get("hero_product"),
                    "source": SOURCE,
                    "source_url": website,
                    "discovered_at": datetime.now(timezone.utc),
                    "verified_contact": bool(brand.get("contact_email")),
                    "accepting_pr": True,
                    "has_application_form": False,
                    "application_method": "DIRECT_LINK",
                    "notes": notes,
                }

                columns = [k for k, v in record.items() if v is not None]
                values = [record[k] for k in columns]
                placeholders = ", ".join(["%s"] * len(values))
                columns_str = ", ".join(columns)
                insert_sql = f"""
                    INSERT INTO pr_brands ({columns_str}, created_at, updated_at)
                    VALUES ({placeholders}, NOW(), NOW())
                    RETURNING id
                """

                if dry_run:
                    print(f"\n[DRY RUN] Would insert: {brand_name}")
                    print(f"  Website: {website}")
                    print(f"  Email: {record.get('contact_email')}")
                    print(f"  IG: @{record.get('instagram_handle')}" if record.get("instagram_handle") else "  IG: None")
                    print(f"  Notes: {notes[:120]}...")
                    stats["inserted"] += 1
                else:
                    cursor.execute("SAVEPOINT brand_insert")
                    cursor.execute(insert_sql, values)
                    result = cursor.fetchone()
                    cursor.execute("RELEASE SAVEPOINT brand_insert")
                    brand_id = result["id"] if result else None
                    print(f"[BrandDB] ✓ Inserted {brand_name} (ID: {brand_id})")
                    stats["inserted"] += 1
                    if domain:
                        existing_domains.add(domain)
                    existing_names.add(brand_name.lower())

            except Exception as e:
                print(f"[BrandDB] ERROR inserting {brand.get('brand_name', 'unknown')}: {e}")
                stats["errors"] += 1
                if conn and not dry_run:
                    try:
                        cursor.execute("ROLLBACK TO SAVEPOINT brand_insert")
                    except Exception:
                        conn.rollback()
                continue

        if not dry_run:
            conn.commit()
            print("\n[BrandDB] Transaction committed")

    except Exception as e:
        print(f"[BrandDB] Database connection error: {e}")
        if conn:
            conn.rollback()
        stats["errors"] = len(brands)
    finally:
        if conn:
            conn.close()

    print("\n" + "=" * 60)
    print(
        f"[BrandDB] SUMMARY: {stats['inserted']} inserted, "
        f"{stats['skipped']} skipped, {stats['errors']} errors"
    )
    print(f"[BrandDB] Filter later with: notes ILIKE '%{OUTREACH_TAG}%' AND source = '{SOURCE}'")
    return stats


def insert_from_json(json_file: str, **kwargs) -> Dict[str, int]:
    import json

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            brands = json.load(f)

        if not isinstance(brands, list):
            print(f"[BrandDB] ERROR: {json_file} does not contain a list of brands")
            return {"inserted": 0, "skipped": 0, "errors": 0}

        print(f"[BrandDB] Loaded {len(brands)} brands from {json_file}")
        return insert_brands_to_pr_brands(brands, **kwargs)

    except FileNotFoundError:
        print(f"[BrandDB] ERROR: File not found: {json_file}")
        return {"inserted": 0, "skipped": 0, "errors": 0}
    except json.JSONDecodeError as e:
        print(f"[BrandDB] ERROR: Invalid JSON in {json_file}: {e}")
        return {"inserted": 0, "skipped": 0, "errors": 0}
