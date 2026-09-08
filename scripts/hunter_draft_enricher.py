# -*- coding: utf-8 -*-
"""
Hunter.io Email Enrichment for Draft Brands

Enriches draft brands with PR/contact emails using Hunter.io API.
Prioritizes: influencer, pr, press, marketing, partnership, collaboration, affiliates
Marks enriched brands with 'ENRICHED BY HUNTER' in notes column.

Usage:
    python scripts/hunter_draft_enricher.py
    python scripts/hunter_draft_enricher.py --csv "C:\\Users\\maher\\Downloads\\brands-export-2026-08-25.csv" --replace --verify
"""
import csv
import os
import sys
import time
import argparse
import requests
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
HUNTER_API_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"

TIER_1 = (
    "influencer", "influencers", "creator", "creators", "ugc",
    "ambassador", "ambassadors",
    "partnership", "partnerships", "partner", "partners",
    "collab", "collabs", "collaborate", "collaboration",
    "marketing", "affiliate", "affiliates",
)
TIER_2 = ("pr", "press", "media")
TIER_3 = ("hello", "hi", "hey", "info", "contact", "support", "team", "social", "community")
BAD_LOCALS = (
    "shipping", "wholesale", "orders", "order", "billing", "accounts",
    "invoice", "accounting", "noreply", "no-reply", "donotreply",
    "webmaster", "postmaster", "bounce", "privacy", "legal", "careers",
    "jobs", "hr", "gdpr", "unsubscribe",
)


def get_email_priority(email_obj) -> int:
    """Return priority score for a Hunter email object (lower = better)."""
    email = (email_obj.get("value") or "").lower()
    local_part = email.split("@")[0]
    dept = (email_obj.get("department") or "").lower()
    position = (email_obj.get("position") or "").lower()
    kind = (email_obj.get("type") or "").lower()
    blob = f"{local_part} {position}"

    for i, pattern in enumerate(BAD_LOCALS):
        if local_part == pattern or local_part.startswith(pattern + ".") or local_part.startswith(pattern + "-"):
            return 900 + i
    for i, pattern in enumerate(TIER_1):
        if pattern in blob or dept == "marketing":
            return i
    for i, pattern in enumerate(TIER_2):
        if pattern in blob or dept == "communication":
            return 100 + i
    for i, pattern in enumerate(TIER_3):
        if pattern in local_part:
            return 200 + i
    if kind == "generic":
        return 300
    return 800


def email_priority_value(email: str) -> int:
    if not email:
        return 9999
    return get_email_priority({"value": email, "type": "generic"})


def hunter_email_count(domain: str, timeout: int = 10) -> int:
    """Free Hunter lookup: how many emails they have for this domain."""
    try:
        response = requests.get(
            "https://api.hunter.io/v2/email-count",
            params={"domain": domain, "api_key": HUNTER_API_KEY},
            timeout=timeout,
        )
        response.raise_for_status()
        data = (response.json().get("data") or {})
        return int(data.get("total") or 0)
    except Exception:
        return -1


def domain_from_website(website: str):
    if not website:
        return None
    raw = website if website.startswith("http") else f"https://{website}"
    try:
        parsed = urlparse(raw)
        return (parsed.hostname or "").lower().removeprefix("www.") or None
    except Exception:
        return None


def find_pr_candidates(domain: str, timeout: int = 15):
    """
    Return sorted Hunter email objects for a domain.
    Empty Domain Search does not consume credits.
    """
    if not domain or not HUNTER_API_KEY:
        return [], "skip"

    domain = domain.lower().removeprefix("http://").removeprefix("https://")
    domain = domain.split("/")[0].removeprefix("www.")

    try:
        params = {
            "domain": domain,
            "api_key": HUNTER_API_KEY,
            "limit": 100,
        }
        response = requests.get(HUNTER_API_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if data.get("errors"):
            error_msg = data["errors"][0].get("details", "Unknown error")
            print(f"  [!] API error: {error_msg}")
            return [], "error"

        emails = data.get("data", {}).get("emails") or []
        if not emails:
            return [], "none"

        emails_sorted = sorted(
            emails,
            key=lambda email_obj: (
                get_email_priority(email_obj),
                -(email_obj.get("confidence") or 0),
            ),
        )
        return emails_sorted, "ok"
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            return [], "none"
        print(f"  [!] HTTP error: {e.response.status_code if e.response is not None else e}")
        return [], "error"
    except requests.exceptions.RequestException as e:
        print(f"  [!] Request error: {str(e)[:50]}")
        return [], "error"
    except Exception as e:
        print(f"  [!] Error: {str(e)[:50]}")
        return [], "error"


def find_pr_email(domain: str, timeout: int = 15) -> tuple:
    """Back-compat: first usable candidate, or (None, 0, reason)."""
    known_count = hunter_email_count(domain, timeout=timeout)
    if known_count == 0:
        return None, 0, "empty"

    candidates, reason = find_pr_candidates(domain, timeout=timeout)
    if reason != "ok":
        return None, 0, reason

    for email_obj in candidates:
        confidence = email_obj.get("confidence", 0)
        value = email_obj.get("value")
        if value and confidence >= 30:
            return value, confidence, "ok"
    return None, 0, "low_confidence"


def verify_hunter_email(email: str, timeout: int = 30) -> dict:
    """
    Hunter Email Verifier. Maps to pr_brands.email_status values:
    valid, invalid, catch-all, risky, unverified
    """
    empty = {
        "ok": False,
        "status": "unverified",
        "score": 0,
        "result": None,
        "hunter_status": None,
    }
    if not email or not HUNTER_API_KEY:
        return empty

    try:
        response = None
        last_error = None
        for _attempt in range(2):
            try:
                response = requests.get(
                    HUNTER_VERIFY_URL,
                    params={"email": email, "api_key": HUNTER_API_KEY},
                    timeout=timeout,
                )
                response.raise_for_status()
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                time.sleep(1.5)
        if response is None:
            raise last_error or RuntimeError("verify failed")
        data = (response.json().get("data") or {})
        hunter_status = (data.get("status") or "").lower()
        result = (data.get("result") or "").lower()
        score = int(data.get("score") or 0)

        if hunter_status == "valid" or result == "deliverable":
            status = "valid"
            ok = True
        elif hunter_status == "accept_all" or (result == "risky" and data.get("accept_all")):
            status = "catch-all"
            ok = score >= 30
        elif hunter_status in ("invalid", "disposable") or result == "undeliverable":
            status = "invalid"
            ok = False
        elif hunter_status in ("unknown", "webmail") or result == "risky":
            status = "risky"
            ok = score >= 50
        else:
            status = "unverified"
            ok = score >= 70

        return {
            "ok": ok,
            "status": status,
            "score": score,
            "result": result,
            "hunter_status": hunter_status,
        }
    except Exception as e:
        print(f"  [!] Verify error: {str(e)[:60]}")
        return empty


def pick_working_email(candidates, max_verify=3):
    """Verify top Hunter candidates until one is deliverable enough to send."""
    tried = 0
    for email_obj in candidates:
        value = (email_obj.get("value") or "").strip()
        confidence = email_obj.get("confidence", 0)
        if not value or confidence < 30:
            continue
        local = value.split("@")[0].lower()
        if local in BAD_LOCALS and tried < max_verify:
            # Still verify later only if nothing better works
            continue
        verification = verify_hunter_email(value)
        tried += 1
        if verification["ok"]:
            return value, confidence, verification
        if tried >= max_verify:
            break

    # Fallback: verify a deprioritized email if nothing PR-shaped worked
    if tried < max_verify:
        for email_obj in candidates:
            value = (email_obj.get("value") or "").strip()
            confidence = email_obj.get("confidence", 0)
            if not value or confidence < 30:
                continue
            verification = verify_hunter_email(value)
            tried += 1
            if verification["ok"]:
                return value, confidence, verification
            if tried >= max_verify:
                break
    return None, 0, None


def connect_db(db_url):
    return psycopg2.connect(
        db_url,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def slugs_from_csv(csv_path: str):
    slugs = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            slug = (row.get("Slug") or "").strip()
            if slug:
                slugs.append(slug)
    return slugs


def append_hunter_note(existing_notes: str) -> str:
    hunter_note = f"ENRICHED BY HUNTER ({datetime.now().strftime('%Y-%m-%d')})"
    existing_notes = existing_notes or ""
    if "ENRICHED BY HUNTER" in existing_notes:
        return existing_notes
    return f"{hunter_note}\n{existing_notes}".strip() if existing_notes else hunter_note


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--notes-contains", type=str, default="HUNTER IMPORT 2026-08-16")
    parser.add_argument(
        "--meta-ads-outreach",
        action="store_true",
        help="Enrich META_ADS_OUTREACH drafts (no logo/cover required). Implies notes META_ADS_OUTREACH.",
    )
    parser.add_argument("--csv", type=str, help="Admin brands export CSV; enrich matching slugs")
    parser.add_argument("--replace", action="store_true", help="Replace existing emails if Hunter finds a better working one")
    parser.add_argument("--verify", action="store_true", help="Verify chosen emails with Hunter Email Verifier")
    parser.add_argument("--skip-count", action="store_true", help="Skip free email-count preflight")
    args = parser.parse_args()

    if args.meta_ads_outreach:
        args.notes_contains = "META_ADS_OUTREACH"

    print("=" * 60)
    print("Hunter.io Email Enrichment for Draft Brands")
    print("=" * 60)
    print("Priority: 1) influencer/creators/ambassadors/partnerships/marketing")
    print("          2) pr/press")
    print("          3) generic (hello/info/contact)")
    print("          4) other Hunter emails (personal fallback)")
    if args.meta_ads_outreach:
        print("Mode:     META_ADS_OUTREACH Shopify DTC leads")
    if args.verify:
        print("Verify:   Hunter Email Verifier (valid / catch-all / risky>=50)")
    print()

    if not HUNTER_API_KEY:
        print("ERROR: HUNTER_API_KEY not found")
        return

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not found")
        return

    conn = connect_db(db_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if args.csv:
        slugs = slugs_from_csv(args.csv)
        print(f"CSV: {args.csv}")
        print(f"CSV slugs: {len(slugs)}")
        cur.execute(
            """
            SELECT id, brand_name, website, notes, contact_email, slug
            FROM pr_brands
            WHERE slug = ANY(%s)
              AND website IS NOT NULL
            ORDER BY id DESC
            """,
            (slugs,),
        )
        brands = cur.fetchall()
        missing = set(slugs) - {b["slug"] for b in brands}
        print(f"Matched in DB: {len(brands)}")
        if missing:
            print(f"CSV slugs not in DB ({len(missing)}): {', '.join(sorted(missing)[:8])}")
        if args.limit and len(brands) > args.limit:
            brands = brands[: args.limit]
            print(f"Processing first {len(brands)} (--limit {args.limit})")
    elif args.meta_ads_outreach:
        # Shopify Meta Ads leads: drafts tagged META_ADS_OUTREACH.
        # Include rows that already have a scraped email so --verify/--replace can upgrade them.
        cur.execute(
            """
            SELECT id, brand_name, website, notes, contact_email, slug
            FROM pr_brands
            WHERE status = 'draft'
              AND website IS NOT NULL
              AND notes ILIKE %s
              AND COALESCE(source, '') = 'meta_ads_library'
              AND (
                    contact_email IS NULL
                 OR TRIM(contact_email) = ''
                 OR %s
              )
            ORDER BY id DESC
            LIMIT %s
            """,
            (f"%{args.notes_contains}%", args.replace or args.verify, args.limit),
        )
        brands = cur.fetchall()
        print(f"Found {len(brands)} META_ADS_OUTREACH draft brands to enrich/verify")
    else:
        cur.execute(
            """
            SELECT id, brand_name, website, notes, contact_email, slug
            FROM pr_brands
            WHERE status = 'draft'
              AND website IS NOT NULL
              AND notes ILIKE %s
              AND logo_url IS NOT NULL AND TRIM(logo_url) <> ''
              AND cover_image_url IS NOT NULL AND TRIM(cover_image_url) <> ''
              AND (contact_email IS NULL OR TRIM(contact_email) = '')
            ORDER BY id DESC
            LIMIT %s
            """,
            (f"%{args.notes_contains}%", args.limit),
        )
        brands = cur.fetchall()
        print(f"Found {len(brands)} draft brands with logo+cover and no email")

    print()

    try:
        acct = requests.get(
            "https://api.hunter.io/v2/account",
            params={"api_key": HUNTER_API_KEY},
            timeout=15,
        )
        acct.raise_for_status()
        searches = (acct.json().get("data") or {}).get("requests") or {}
        print(
            f"Hunter searches {((searches.get('searches') or {}).get('used', '?'))} / "
            f"{((searches.get('searches') or {}).get('available', '?'))} "
            f"(remaining {(searches.get('searches') or {}).get('remaining', '?')})"
        )
        print(
            f"Hunter verifies {((searches.get('verifications') or {}).get('used', '?'))} / "
            f"{((searches.get('verifications') or {}).get('available', '?'))} "
            f"(remaining {(searches.get('verifications') or {}).get('remaining', '?')})"
        )
        print()
    except Exception as exc:
        print(f"Could not read Hunter account quota: {exc}")
        print()

    enriched = 0
    upgraded = 0
    kept = 0
    skipped = 0
    failed = 0

    for i, brand in enumerate(brands):
        brand_id = brand["id"]
        name = brand["brand_name"] or f"Brand #{brand_id}"
        website = brand["website"]
        current_email = (brand.get("contact_email") or "").strip()
        domain = domain_from_website(website)

        if not domain:
            print(f"[{i+1}/{len(brands)}] {name}: No valid domain")
            skipped += 1
            continue

        print(f"[{i+1}/{len(brands)}] {name} ({domain})...", end=" ", flush=True)

        if current_email and not args.replace:
            print(f"[skip] already has {current_email}")
            kept += 1
            continue

        if not args.skip_count:
            known_count = hunter_email_count(domain)
            if known_count == 0:
                print("[--] Hunter has 0 emails")
                failed += 1
                continue

        candidates, reason = find_pr_candidates(domain)
        if reason != "ok":
            print(f"[--] No email found ({reason})")
            failed += 1
            if i < len(brands) - 1:
                time.sleep(0.4)
            continue

        if args.verify:
            email, confidence, verification = pick_working_email(candidates)
            if not email:
                print("[--] Hunter hits failed verification")
                failed += 1
                if i < len(brands) - 1:
                    time.sleep(0.6)
                continue
        else:
            email, confidence, verification = None, 0, None
            for email_obj in candidates:
                if (email_obj.get("confidence") or 0) >= 30 and email_obj.get("value"):
                    email = email_obj["value"]
                    confidence = email_obj.get("confidence") or 0
                    break
            if not email:
                print("[--] No email found")
                failed += 1
                continue

        new_email = email.strip().lower()
        old_email = current_email.lower()
        better = email_priority_value(new_email) < email_priority_value(old_email)

        if old_email and new_email == old_email:
            if args.verify and verification:
                cur.execute(
                    """
                    UPDATE pr_brands
                    SET email_status = %s,
                        email_quality_score = %s,
                        email_verified_at = NOW(),
                        email_verification_source = 'hunter',
                        notes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        verification["status"],
                        verification["score"],
                        append_hunter_note(brand.get("notes")),
                        brand_id,
                    ),
                )
                conn.commit()
            print(f"[same] {new_email} ({confidence}% / {verification['status'] if verification else 'unverified'})")
            kept += 1
            if i < len(brands) - 1:
                time.sleep(0.6)
            continue

        if old_email and not better:
            print(f"[keep] {old_email} (better than {new_email})")
            kept += 1
            if i < len(brands) - 1:
                time.sleep(0.6)
            continue

        status = (verification or {}).get("status") or "unverified"
        score = (verification or {}).get("score") or 0

        for attempt in range(2):
            try:
                cur.execute(
                    """
                    UPDATE pr_brands
                    SET contact_email = %s,
                        notes = %s,
                        email_status = %s,
                        email_quality_score = %s,
                        email_verified_at = CASE WHEN %s THEN NOW() ELSE email_verified_at END,
                        email_verification_source = CASE WHEN %s THEN 'hunter' ELSE email_verification_source END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        new_email,
                        append_hunter_note(brand.get("notes")),
                        status,
                        score,
                        bool(args.verify),
                        bool(args.verify),
                        brand_id,
                    ),
                )
                conn.commit()
                break
            except psycopg2.OperationalError:
                print("[!] DB reconnect...", end=" ", flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = connect_db(db_url)
                cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            print("[!!] DB write failed")
            failed += 1
            continue

        tag = "upgrade" if old_email and old_email != new_email else "ok"
        verify_bit = f" / {status} {score}" if verification else ""
        if old_email and old_email != new_email:
            print(f"[{tag}] {old_email} -> {new_email} ({confidence}%{verify_bit})")
            upgraded += 1
        else:
            print(f"[OK] {new_email} ({confidence}%{verify_bit})")
            enriched += 1

        if i < len(brands) - 1:
            time.sleep(0.6)

    conn.close()

    print()
    print("=" * 60)
    print("ENRICHMENT COMPLETE")
    print("=" * 60)
    print(f"  New emails: {enriched}")
    print(f"  Upgraded: {upgraded}")
    print(f"  Kept existing / same: {kept}")
    print(f"  No working email: {failed}")
    print(f"  Skipped (no domain): {skipped}")
    print(f"  Total processed: {len(brands)}")
    print()


if __name__ == "__main__":
    main()
