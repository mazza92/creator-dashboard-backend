# Shopify DTC Brand Acquisition Flow (Hermes)

Instructions for the **brand-acquisition** Hermes agent.
Goal: discover Shopify brands running Meta ads → enrich emails → cold outreach for gifted PR / UGC.

**Do not invent SMTP.** All email goes through the NewCollab backend API (`brand_outreach_api.py`), same as existing brand outreach.

---

## Pipeline overview (2× daily)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ 1. Meta Ads     │ --> │ 2. pr_brands     │ --> │ 3. Hunter       │ --> │ 4. Cold email    │
│ Shopify scraper │     │ draft + tag      │     │ enrich/verify   │     │ via backend API  │
└─────────────────┘     └──────────────────┘     └─────────────────┘     └──────────────────┘
```

| Step | What | Where |
|------|------|--------|
| 1 | Scrape Meta Ad Library → Shopify DTC | `creator_dashboard` Python scripts |
| 2 | Insert `status=draft` + `[META_ADS_OUTREACH]` notes | `pr_brands` (production Supabase) |
| 3 | Hunter domain-search + verify | `hunter_draft_enricher.py` |
| 4 | Send UGC outreach emails | `brand_outreach_api.send_ugc_outreach_email` |

**Cron cadence:** 2 runs/day (suggested: **09:00 UTC** and **17:00 UTC**).
Each run: scrape (quota 50) → Hunter → outreach send (cap per run).

---

## Identity & paths (HARD RULES)

- Runtime user on VPS: `hermes`
- Brand-acquisition profile: `/home/hermes/.hermes/profiles/brand-acquisition/`
- Skills: `/home/hermes/.hermes/profiles/brand-acquisition/skills/`
- Backend API: `https://api.newcollab.co`
- Admin token env: `NEWCOLLAB_ADMIN_TOKEN` (fallback `pr-hunter-admin-2026`)
- **Never** use `/home/mazza/` paths
- **Never** send mail via SMTP / smtplib / Gmail app passwords from Hermes

### Backend repo (scraper + Hunter)

Clone / sync the Flask backend (`creator_dashboard`) on the VPS, e.g.:

```text
/home/hermes/apps/creator_dashboard/
```

Required inside that repo:

| File | Role |
|------|------|
| `scripts/crawl_meta_ads.py` | CLI entry for daily scrape + DB insert |
| `services/meta_ads_library_scraper.py` | Meta Ads → Shopify discovery + enrichment |
| `services/brand_db_writer.py` | Inserts drafts with `[META_ADS_OUTREACH]` |
| `scripts/hunter_draft_enricher.py` | Hunter enrich/verify for those drafts |
| `.env` | `DATABASE_URL`, `HUNTER_API_KEY`, optional `META_ADS_PROXY` |

Venv:

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate   # or: . venv/bin/activate
playwright install chromium   # once
```

---

## Lead identity (how to find these brands)

Every scraped lead is tagged so you and Hermes can filter them:

| Field | Value |
|-------|--------|
| `status` | `draft` |
| `source` | `meta_ads_library` |
| `notes` | starts with **`[META_ADS_OUTREACH]`** |

After Hunter:

| Field | Value |
|-------|--------|
| `notes` | also contains **`ENRICHED BY HUNTER (YYYY-MM-DD)`** |
| `email_status` | `valid` / `catch-all` / `risky` / … |
| `email_verification_source` | `hunter` (when `--verify`) |

### SQL filters

```sql
-- All Meta Ads scrape leads (drafts)
SELECT id, brand_name, website, contact_email, instagram_handle, email_status, notes
FROM pr_brands
WHERE status = 'draft'
  AND source = 'meta_ads_library'
  AND notes ILIKE '%META_ADS_OUTREACH%'
ORDER BY id DESC;

-- Ready to email (verified-enough + not yet contacted)
SELECT b.id, b.brand_name, b.contact_email, b.website, b.hero_product
FROM pr_brands b
LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
WHERE b.status = 'draft'
  AND b.source = 'meta_ads_library'
  AND b.notes ILIKE '%META_ADS_OUTREACH%'
  AND b.contact_email IS NOT NULL AND TRIM(b.contact_email) <> ''
  AND COALESCE(b.email_status, '') IN ('valid', 'catch-all', 'risky', 'unverified', '')
  AND (bo.brand_id IS NULL OR COALESCE(bo.last_response_status, '') = '')
ORDER BY b.id DESC
LIMIT 40;
```

**Important:** `/api/admin/email/brands-for-outreach` only returns `status=published`.
For this flow, **do not** use `get_brands_for_outreach()` alone.
Select Meta Ads drafts via SQL (python + `DATABASE_URL`) or a small helper, then send by `brand_id` with `send_ugc_outreach_email(brand_id)`.
The send endpoint accepts drafts as long as `contact_email` is set.

---

## Step 1 — Scrape (Meta Ads → Shopify)

### Command (production)

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
python scripts/crawl_meta_ads.py --daily --quota 50 --save-to-db \
  -o /home/hermes/apps/creator_dashboard/logs/meta_ads_$(date -u +%Y%m%d_%H%M).json
```

What this does:

1. Searches Meta Ad Library (active ads, `keyword_unordered`) across the default DTC keyword set
2. Unwraps `l.facebook.com/l.php` landing URLs
3. Keeps Shopify stores (`/products.json` / CDN signals)
4. Stops at **50 qualified** brands (Shopify + email **or** Instagram from the storefront)
5. Inserts into `pr_brands` as **`draft`** with notes `[META_ADS_OUTREACH] … Batch YYYY-MM-DD`
6. Dedupes by website domain + brand name (skips existing)

Optional if Meta captcha-blocks headless Chromium:

```bash
# Start Chrome with remote debugging once, then:
python scripts/crawl_meta_ads.py --daily --quota 50 --save-to-db \
  --cdp http://127.0.0.1:9222 \
  -o /home/hermes/apps/creator_dashboard/logs/meta_ads_$(date -u +%Y%m%d_%H%M).json
```

Env:

```bash
# required
DATABASE_URL=postgresql://...
# optional
META_ADS_PROXY=http://user:pass@host:port
# or IG_PROXY as fallback
```

Expected runtime: ~8–15 minutes for quota 50.

---

## Step 2 — Confirm DB write

After scrape, spot-check:

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
python - <<'PY'
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
from psycopg2.extras import RealDictCursor
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("""
  SELECT COUNT(*) AS n FROM pr_brands
  WHERE status='draft' AND source='meta_ads_library'
    AND notes ILIKE '%META_ADS_OUTREACH%'
    AND discovered_at::date = CURRENT_DATE
""")
print("today's META_ADS drafts:", cur.fetchone()["n"])
conn.close()
PY
```

If count is 0 → scrape failed (captcha / Playwright / DB). Fix before Hunter/outreach.

---

## Step 3 — Hunter enrich + verify

Use the Meta Ads mode (no logo/cover required):

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
python scripts/hunter_draft_enricher.py \
  --meta-ads-outreach \
  --verify \
  --replace \
  --limit 80
```

What this does:

- Selects `draft` + `source=meta_ads_library` + notes `META_ADS_OUTREACH`
- Hunter Domain Search (PR / influencer / partnerships priority)
- Hunter Email Verifier (`--verify`)
- Writes `contact_email`, `email_status`, `email_quality_score`, `email_verified_at`
- Appends `ENRICHED BY HUNTER (YYYY-MM-DD)` to `notes` (keeps `[META_ADS_OUTREACH]`)

Env:

```bash
HUNTER_API_KEY=...
DATABASE_URL=...
```

**Send policy after Hunter:**

- Prefer `email_status IN ('valid', 'catch-all')`
- Allow `risky` only if score ≥ 50 (verifier already gates this)
- Skip `invalid`
- Brands with Instagram but still no email after Hunter → **do not email**; leave for manual IG / next batch

---

## Step 4 — Cold email (backend API only)

### Bootstrap (every run)

```python
import sys
sys.path.insert(0, "/home/hermes/.hermes/profiles/brand-acquisition/skills")
from brand_outreach_api import (
    test_connection,
    send_ugc_outreach_email,
    send_bulk_ugc_outreach,
    get_outreach_stats,
)
print(test_connection())
```

### Pull ready Meta Ads leads (python + DB)

```python
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load backend .env for DATABASE_URL (or set in brand-acquisition .env)
load_dotenv("/home/hermes/apps/creator_dashboard/.env")

def fetch_meta_ads_outreach_ready(limit=25):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT b.id, b.brand_name, b.contact_email, b.email_status, b.website
        FROM pr_brands b
        LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
        WHERE b.status = 'draft'
          AND b.source = 'meta_ads_library'
          AND b.notes ILIKE %s
          AND b.contact_email IS NOT NULL AND TRIM(b.contact_email) <> ''
          AND COALESCE(b.email_status, 'unverified') IN ('valid', 'catch-all', 'risky', 'unverified')
          AND COALESCE(b.email_status, '') <> 'invalid'
          AND (bo.brand_id IS NULL OR COALESCE(bo.last_contacted_at::text, '') = '')
        ORDER BY
          CASE b.email_status
            WHEN 'valid' THEN 0
            WHEN 'catch-all' THEN 1
            WHEN 'risky' THEN 2
            ELSE 3
          END,
          b.id DESC
        LIMIT %s
        """,
        ("%META_ADS_OUTREACH%", limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

ready = fetch_meta_ads_outreach_ready(limit=25)
brand_ids = [r["id"] for r in ready]
print(len(brand_ids), "ready", brand_ids[:10])
```

### Send

```python
# Cap per cron run — do NOT blast hundreds
MAX_SEND = 25
ids = brand_ids[:MAX_SEND]

# Optional: preview one
# from brand_outreach_api import render_outreach_email
# print(render_outreach_email(ids[0]))

results = send_bulk_ugc_outreach(brand_ids=ids, delay_seconds=2.0)
print(results)
```

Rules:

- **Only** `send_ugc_outreach_email` / `send_bulk_ugc_outreach`
- Cap **≤ 25** emails per cron run (adjust with Mahery if volume grows)
- `delay_seconds ≥ 1.5` (prefer 2.0)
- If send returns `success: False` → log error, do not invent another channel
- Duplicate protection is already on the backend (`brand_outreach_tracking`)

Pitch product (what the email should sell): gifted product → 1 organic + 1 UGC file, 6‑month usage — same as roster / For Brands gifted PR. Use the existing UGC render path; do not invent follower counts or fake SKUs.

---

## Suggested cron (2× daily)

Put a shell wrapper on the VPS, e.g. `/home/hermes/apps/creator_dashboard/scripts/run_meta_ads_acquisition.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
export $(grep -v '^#' .env | xargs -d '\n')

STAMP=$(date -u +%Y%m%d_%H%M)
LOG_DIR=/home/hermes/apps/creator_dashboard/logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/meta_ads_acq_$STAMP.log"

exec >>"$LOG" 2>&1
echo "===== START $STAMP ====="

echo "[1/3] scrape + draft insert"
python scripts/crawl_meta_ads.py --daily --quota 50 --save-to-db \
  -o "$LOG_DIR/meta_ads_$STAMP.json"

echo "[2/3] hunter enrich/verify"
python scripts/hunter_draft_enricher.py --meta-ads-outreach --verify --replace --limit 80

echo "[3/3] outreach send via Hermes skill / API"
# Prefer invoking the brand-acquisition Hermes turn here, OR a small python
# that imports brand_outreach_api and sends ≤25 ready leads (see Step 4).
# Example if a local runner exists:
#   hermes -p brand-acquisition run --prompt "Run META_ADS_OUTREACH send: fetch ready leads, send_bulk_ugc_outreach max 25"
python /home/hermes/.hermes/profiles/brand-acquisition/skills/run_meta_ads_outreach_send.py || true

echo "===== DONE $STAMP ====="
```

Crontab (as user `hermes`):

```cron
# Shopify Meta Ads acquisition — 2x daily UTC
0 9 * * * /home/hermes/apps/creator_dashboard/scripts/run_meta_ads_acquisition.sh
0 17 * * * /home/hermes/apps/creator_dashboard/scripts/run_meta_ads_acquisition.sh
```

Make executable: `chmod +x .../run_meta_ads_acquisition.sh`

---

## Hermes agent checklist (per run)

1. Confirm backend `test_connection()` OK  
2. Confirm scrape log shows inserts / or today’s `META_ADS_OUTREACH` count > 0  
3. Run Hunter `--meta-ads-outreach --verify --replace`  
4. Fetch ready IDs (email + not contacted)  
5. Send ≤ 25 via `send_bulk_ugc_outreach`  
6. Report to Mahery: scraped N / hunter enriched M / sent K / errors  

---

## Failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| 0 ads discovered | Meta captcha / bad selectors | `--cdp` logged-in Chrome; check proxy |
| Playwright missing browser | Fresh venv | `playwright install chromium` |
| Inserts all skipped | Already in `pr_brands` | Normal; widen keywords / quota |
| Hunter finds nothing | New DTC / private domain | Keep IG-only for later; don’t invent emails |
| Outreach “brand not found” | Wrong API base / token | Check `NEWCOLLAB_API_BASE` + admin token |
| `brands-for-outreach` empty for these leads | Endpoint is **published-only** | Use SQL filter in Step 4, not that list endpoint |
| Duplicate outreach blocked | Already contacted | Expected; use follow-up sequence only when intentional |

---

## Files Mahery should sync to the VPS

From local `creator_dashboard` (Windows path for reference):

- `scripts/crawl_meta_ads.py`
- `services/meta_ads_library_scraper.py`
- `services/brand_db_writer.py`
- `scripts/hunter_draft_enricher.py` (includes `--meta-ads-outreach`)
- `META_ADS_SHOPIFY_PIPELINE.md` (scraper details)
- This file: `META_ADS_SHOPIFY_ACQUISITION_FLOW.md`

Brand-acquisition Hermes already has:

- `skills/brand_outreach_api.py`
- `SOUL.md` (email = API only)

---

## Out of scope (do not build in this cron)

- Shopify App Store plugin
- Promoting Meta Ads drafts to `published` directory listings (keep as draft outreach leads unless Mahery asks)
- Inventing emails / follower counts
- Sending via Resend/Gmail from Hermes process directly

---

## One-liner for Hermes memory

> Twice daily: `crawl_meta_ads.py --daily --save-to-db` → `hunter_draft_enricher.py --meta-ads-outreach --verify --replace` → SQL-select `[META_ADS_OUTREACH]` drafts with good emails → `send_bulk_ugc_outreach` (max 25) via backend API.
