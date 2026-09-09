# UGC Creator Acquisition Flow (Hermes)

Instructions for the **creator-onboarding** Hermes agent.
Goal: find Hannah-shaped TikTok UGC creators → store in `ugc_supply` → cold email them to **sign up and apply for gifted PR** on Newcollab.

**Do not invent SMTP.** All email goes through the backend API (`/api/admin/ugc-supply/outreach/*`, Resend).
**Do not mix these rows with `pr_brands` or brand gifted-UGC billing.**
**Do not rewrite the email.** Omit `subject` / `html_content` so the locked template below is used.

---

## Pipeline overview (2× daily)

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ 1. TikTok UGC   │ --> │ 2. ugc_supply    │ --> │ 3. for-outreach │ --> │ 4. Cold email    │
│ crawler         │     │ draft + tag      │     │ (qualified)     │     │ via backend API  │
└─────────────────┘     └──────────────────┘     └─────────────────┘     └──────────────────┘
```

| Step | What | Where |
|------|------|--------|
| 1 | Crawl UGC + niche + public email + 1k followers | `scripts/crawl_tiktok_ugc.py --daily --save-to-db` |
| 2 | Insert `status=draft` `qualified=true` notes `[UGC_SUPPLY_OUTREACH]` | `ugc_supply` (production Supabase) |
| 3 | List ready creators | `GET /api/admin/ugc-supply/for-outreach` |
| 4 | Send signup / apply emails | `POST /api/admin/ugc-supply/outreach/bulk` |

**Cron cadence:** 2 runs/day (suggested: **09:30 UTC** and **17:30 UTC**, offset from brand Meta Ads).
Each run: crawl (quota 50) → send ready leads (cap 25).

No Hunter step. These creators already have a public email in bio.

---

## Identity & paths (HARD RULES)

- Runtime user on VPS: `hermes`
- Creator-onboarding profile: `/home/hermes/.hermes/profiles/creator-onboarding/`
- Backend API: `https://api.newcollab.co`
- Admin UI: `https://app.newcollab.co/admin/ugc-supply`
- Admin token env: `NEWCOLLAB_ADMIN_TOKEN` (fallback `pr-hunter-admin-2026`)
- Signup link in every email: `https://app.newcollab.co/register/creator`
- **Never** use `/home/mazza/` paths
- **Never** send mail via SMTP / smtplib / Gmail from Hermes
- **Never** write these rows into `pr_brands`
- **Never** invent follower counts, brand counts, or SKUs
- **Never** promise perpetual copyright / “yours forever”

### Backend repo (crawler + API)

```text
/home/hermes/apps/creator_dashboard/
```

| File | Role |
|------|------|
| `scripts/crawl_tiktok_ugc.py` | CLI: discover + enrich + optional DB insert |
| `services/tiktok_ugc_profile_scraper.py` | SerpAPI + TikTok enrich + qualify |
| `services/tiktok_ugc_lead_writer.py` | Inserts drafts with `[UGC_SUPPLY_OUTREACH]` |
| `routes/ugc_supply.py` | Admin list / send / stats |
| `.env` | `DATABASE_URL`, `SERPAPI_API_KEY` |

Send requires `RESEND_API_KEY` on the API host (Vercel `appbackend`).

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
```

---

## Lead identity

| Field | Value |
|-------|--------|
| table | `ugc_supply` (never `pr_brands`) |
| `status` | `draft` until emailed → `contacted` |
| `source` | `tiktok_ugc_search` |
| `qualified` | `true` (UGC + niche + public email + ≥1,000 followers) |
| `notes` | starts with **`[UGC_SUPPLY_OUTREACH]`** |
| `profile_url` | `https://www.tiktok.com/@{handle}` |

Location is extracted when present but **not required**.

---

## Locked email (do not rewrite)

Invite / match — not an unpaid-work brief. Do **not** list deliverables (1 organic + 1 UGC) or “just product”; that triggers “I only do fixed rate.”
Objective: **sign up + apply if interested.**

**Subject:** `PR / gifting campaigns — @{{handle}}`

```
Hey,

Saw your TikTok (@{{handle}}).

We have brands running PR / gifting campaigns, and your profile could be a good match.

If you're interested, you can sign up and apply here:
https://app.newcollab.co/register/creator

Worth a look?

Mazza
Founder, Newcollab
```

Hermes sends by **omitting** `subject` and `html_content`. The backend fills this template.
Tokens: `{{handle}}`, `{{niche}}`, `{{signup_url}}`, `{{display_name}}`, `{{profile_url}}`, `{{followers}}`.

Do not add 6-month reuse, Pro pricing, brand-count claims, or a rate/deliverable breakdown in this first email.

---

## Step 1 — Crawl (TikTok UGC → ugc_supply)

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
python scripts/crawl_tiktok_ugc.py --daily --save-to-db --quota 50 --max-handles 400 \
  -o /home/hermes/apps/creator_dashboard/logs/tiktok_ugc_$(date -u +%Y%m%d_%H%M).json
```

What this does:

1. SerpAPI Google + Bing for UGC + niche + gmail / beacons / collab queries
2. Enriches TikTok profiles (display name, bio, email, followers)
3. Qualifies: **UGC + niche + public email + ≥1,000 followers**
4. Inserts into `ugc_supply` as **`draft`** with notes `[UGC_SUPPLY_OUTREACH] … Batch YYYY-MM-DD`
5. Dedupes by handle (`ON CONFLICT DO NOTHING`)

Do **not** pass `--ignore-seen` on the daily cron (that re-scrapes old handles). Use it only for a one-off recovery.

Env:

```bash
DATABASE_URL=postgresql://...
SERPAPI_API_KEY=...
```

Expected runtime: ~8–15 minutes for quota 50.

---

## Step 2 — Confirm DB write

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
  SELECT COUNT(*) AS n FROM ugc_supply
  WHERE qualified
    AND notes ILIKE '%UGC_SUPPLY_OUTREACH%'
    AND created_at::date = CURRENT_DATE
""")
print("today's UGC_SUPPLY qualified:", cur.fetchone()["n"])
conn.close()
PY
```

If count is 0 → crawl failed (SerpAPI / TikTok scrape / DB). Fix before sending.

Admin spot-check: `https://app.newcollab.co/admin/ugc-supply` → Ready to email.

---

## Step 3 — Cold email (backend API only)

### Bootstrap (every run)

```python
import os
import requests

BASE = "https://api.newcollab.co/api/admin/ugc-supply"
H = {
    "X-Admin-Token": os.environ.get("NEWCOLLAB_ADMIN_TOKEN", "pr-hunter-admin-2026"),
    "Content-Type": "application/json",
}

ready = requests.get(f"{BASE}/for-outreach", params={"limit": 25}, headers=H, timeout=30)
ready.raise_for_status()
payload = ready.json()
rows = payload.get("supply") or payload.get("leads") or []
ids = [row["id"] for row in rows]
print("ready", len(ids), "of", payload["total"])
```

`for-outreach` returns qualified creators with a public email, status `draft`/`review`, never contacted.

### Send

```python
MAX_SEND = 25
ids = ids[:MAX_SEND]

if ids:
    sent = requests.post(
        f"{BASE}/outreach/bulk",
        headers=H,
        json={"lead_ids": ids, "delay_seconds": 0.5},
        timeout=120,
    )
    sent.raise_for_status()
    print(sent.json())
```

Rules:

- **Only** `POST /api/admin/ugc-supply/outreach/send` or `/outreach/bulk`
- **Do not** pass `subject` or `html_content` (locked template)
- Cap **≤ 25** emails per cron run
- Already-contacted rows are skipped unless `allow_followup: true` (do not set this on the daily send)
- If send returns `success: False` → log error, do not invent another channel
- After send, backend sets `status=contacted`

### Stats / replies

```python
print(requests.get(f"{BASE}/outreach/stats", headers=H, timeout=30).json())
```

Mark a reply or skip:

```python
requests.patch(
    f"{BASE}/{lead_id}",
    headers=H,
    json={"status": "skipped", "last_response_status": "not_interested"},
    timeout=30,
)
```

---

## Suggested cron (2× daily)

`/home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
export $(grep -v '^#' .env | xargs -d '\n')

STAMP=$(date -u +%Y%m%d_%H%M)
LOG_DIR=/home/hermes/apps/creator_dashboard/logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/ugc_supply_acq_$STAMP.log"

exec >>"$LOG" 2>&1
echo "===== START $STAMP ====="

echo "[1/2] crawl + draft insert"
python scripts/crawl_tiktok_ugc.py --daily --save-to-db --quota 50 --max-handles 400 \
  -o "$LOG_DIR/tiktok_ugc_$STAMP.json"

echo "[2/2] outreach send via Hermes / API"
# Prefer a creator-onboarding Hermes turn, or:
python /home/hermes/.hermes/profiles/creator-onboarding/skills/run_ugc_supply_send.py || true

echo "===== DONE $STAMP ====="
```

Crontab (as user `hermes`):

```cron
# UGC creator acquisition — 2x daily UTC (offset from Meta Ads 09:00 / 17:00)
30 9 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
30 17 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
```

---

## Hermes agent checklist (per run)

1. Confirm `GET /api/admin/ugc-supply/outreach/stats` works  
2. Confirm crawl log shows inserts / or today’s `UGC_SUPPLY_OUTREACH` count > 0  
3. `GET /for-outreach?limit=25`  
4. Send ≤ 25 via `/outreach/bulk` with **no custom body**  
5. Report to Mahery: crawled N / qualified M / sent K / errors  

---

## Failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| 0 seeds | SerpAPI key / credits / timeout | Check `SERPAPI_API_KEY`; retry |
| 0 qualified | Email-in-bio is scarce | Normal; raise `--max-handles`, do not loosen email or 1k floor |
| Inserts all skipped | Handle already in `ugc_supply` | Normal; do not `--ignore-seen` on cron |
| `for-outreach` empty | All emailed or none qualified | Wait for next crawl; do not follow up same day |
| Outreach send failed | Missing `RESEND_API_KEY` on Vercel | Fix API host env; do not SMTP |
| Creators appear on `/admin/brands` | Wrong table | Bug — these must stay in `ugc_supply` |

---

## Files Mahery should sync to the VPS

- `scripts/crawl_tiktok_ugc.py`
- `services/tiktok_ugc_profile_scraper.py`
- `services/tiktok_ugc_lead_writer.py`
- `routes/ugc_supply.py`
- This file: `UGC_SUPPLY_ACQUISITION_FLOW.md`

---

## Out of scope

- Writing to `pr_brands`
- Paid UGC / Creator Pro pitch on first email
- Listing deliverables (1 organic + 1 UGC) or “just product” on first email
- Promising perpetual copyright or 6-month reuse in the cold email
- Inventing emails, follower counts, or brand counts
- Sending via Resend/Gmail from the Hermes process directly

---

## One-liner for Hermes memory

> Twice daily: `crawl_tiktok_ugc.py --daily --save-to-db --quota 50` → `GET /api/admin/ugc-supply/for-outreach` → `POST /outreach/bulk` max 25 (no custom email; locked match-invite template). Never touch `pr_brands`.
