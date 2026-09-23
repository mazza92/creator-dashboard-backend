# UGC Creator Acquisition Flow (Hermes)

Instructions for the **creator-onboarding** Hermes agent.
Goal: find Hannah-shaped TikTok UGC creators → store in `ugc_supply` → cold email them to **sign up and apply for gifted PR** on Newcollab.

**Do not invent SMTP.** All email goes through the backend API (`/api/admin/ugc-supply/outreach/*`, Resend).
**Do not mix these rows with `pr_brands` or brand gifted-UGC billing.**
**Do not rewrite the email.** Omit `subject` / `html_content` so the locked template below is used.

---

## STOP — crawl command (the only one)

Do **not** write a scraper. Do **not** INSERT rows yourself. Do **not** invent emails.

If `scripts/crawl_tiktok_ugc.py` is missing: `git pull origin main`. Then run **exactly**:

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
export PYTHONUNBUFFERED=1
mkdir -p logs
# If VPS IP is TikTok-blocked, set one of these in .env before crawling:
#   TIKTOK_SHOP_PROXY=http://USER:PASS@host:port
#   IG_PROXY=http://USER:PASS@host:port
python -u scripts/crawl_tiktok_ugc.py --daily --save-to-db \
  --quota 20 --max-handles 500 --no-serp --workers 2 \
  -o logs/tiktok_ugc_$(date -u +%Y%m%d_%H%M).json
```

Success looks like:

```
[TikTokUGC] Q @handle email=real@gmail.com niche=beauty followers=1234
[TikTokUGC] done: N profiles, 15 qualified
Database insert: 15 inserted, 0 skipped, 0 errors
```

Qualified rows are inserted **after each enrich batch**, not only at the end. If cron hits GNU `timeout` (`crawl_exit=124`), already-qualified creators must still be in `ugc_supply`. `inserted=0` with `qualified>0` in the progress lines means the crawler never flushed — that is a bug, not “no creators found.”

If logs show `[InHouse/TT] embed status=400` / `profile html empty` / `No TikTok data` for almost every handle: **TikTok is blocking this server IP**. Waiting alone often fails while 5×/day crawls keep hammering. Fix:

1. Set `TIKTOK_SHOP_PROXY` or `IG_PROXY` (residential) in `/home/hermes/apps/creator_dashboard/.env`
2. Confirm crawl logs print `[InHouse/TT] proxy enabled host=...` or `playwright proxy http://...`
3. If you also see `SSL: CERTIFICATE_VERIFY_FAILED` / `self-signed certificate` through the proxy, the HTTP scraper must disable TLS verify on that session (shipped). Confirm logs print `TLS verify disabled (proxy MITM / self-signed)`.
4. Rerun the crawl command above (prefer `--workers 2` with a working proxy)
5. Until proxy works: set `UGC_SKIP_CRAWL=1` for cron, or pause the crawl — empty enrich still hammers TikTok

`example@example.com` = you did not run this script. Stop and rerun the command above.

Or: `bash scripts/run_ugc_supply_crawl.sh 15`

---

## Pipeline overview (5× daily)

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

**Cron cadence:** 5 runs/day at **07:30 / 10:30 / 13:30 / 16:30 / 19:30 UTC** (offset from Meta Ads 09:00 / 17:00).
Each run: crawl quota **20** qualified → send ≤20. Daily objective: **100 new creator prospects**.

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
- **Never** invent follower counts, brand counts, SKUs, or emails (`example@example.com` is not a lead)
- **Never** write custom inserts. Only `scripts/crawl_tiktok_ugc.py --save-to-db` or `POST /ingest` with crawler JSON
- Qualified means a **real** public email. No email → not qualified. Do not invent a TikTok API.
- **Never** promise perpetual copyright / “yours forever”

### Backend repo (crawler + API)

```text
/home/hermes/apps/creator_dashboard/
```

| File | Role |
|------|------|
| `scripts/crawl_tiktok_ugc.py` | CLI: discover + enrich + optional DB insert |
| `services/tiktok_ugc_profile_scraper.py` | In-house TikTok hashtag/search + enrich + qualify |
| `services/tiktok_ugc_lead_writer.py` | Inserts drafts with `[UGC_SUPPLY_OUTREACH]` |
| `routes/ugc_supply.py` | Admin list / send / stats |
| `.env` | `DATABASE_URL`, `TIKTOK_SHOP_PROXY` (optional `UGC_USE_SERPAPI=1`) |

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
  --no-serp -o /home/hermes/apps/creator_dashboard/logs/tiktok_ugc_$(date -u +%Y%m%d_%H%M).json
```

What this does:

1. In-house TikTok hashtag + user-search pages (same proxy session as profile scrape)
2. Enriches TikTok profiles (display name, bio, email, followers)
3. Qualifies: **UGC + niche + public email + ≥1,000 followers**
4. Inserts into `ugc_supply` as **`draft`** with notes `[UGC_SUPPLY_OUTREACH] … Batch YYYY-MM-DD`
5. Dedupes by handle (`ON CONFLICT DO NOTHING`)

Do **not** pass `--ignore-seen` on the daily cron (that re-scrapes old handles). Use it only for a one-off recovery.

SerpAPI is **off** unless you pass `--serp` or set `UGC_USE_SERPAPI=1`. Free SerpAPI 429s were burning the crawl before any TikTok enrich.

Env:

```bash
DATABASE_URL=postgresql://...
TIKTOK_SHOP_PROXY=http://USER:PASS@host:port   # recommended on VPS
# UGC_USE_SERPAPI=1   # only if you have paid SerpAPI credits
```

Expected runtime: ~8–15 minutes for quota 50. Cron wraps the crawl in `timeout --kill-after=60 2400` (40 minutes). Do not treat `crawl_exit=124` as “zero creators” — check `RESULT_CRAWL inserted=` and whether `Database insert:` printed before the timeout.

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

If count is 0 → crawl failed (TikTok scrape / proxy / DB). Fix before sending.

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

## Suggested cron (5× daily)

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
  --no-serp -o "$LOG_DIR/tiktok_ugc_$STAMP.json"

echo "[2/2] outreach send via Hermes / API"
# Prefer a creator-onboarding Hermes turn, or:
python /home/hermes/.hermes/profiles/creator-onboarding/skills/run_ugc_supply_send.py || true

echo "===== DONE $STAMP ====="
```

Crontab (as user `hermes`):

```cron
# UGC creator acquisition — 5x daily UTC (offset from Meta Ads 09:00 / 17:00)
# Invoke this script directly. Do not wrap it in a Hermes chat/terminal with a 300s timeout.
# If a Hermes cron job must own it: HERMES_CRON_SCRIPT_TIMEOUT=2400
30 7 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
30 10 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
30 13 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
30 16 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
30 19 * * * /home/hermes/apps/creator_dashboard/scripts/run_ugc_supply_acquisition.sh
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
| 0 seeds / 2 chrome handles from 350k tag HTML | Tag/search SSR is an empty app shell | In-house search + challenge APIs, then Playwright XHR; do not turn SerpAPI back on |
| Hermes / terminal kill at 300s | Wrapper timeout, not TikTok | Call `scripts/run_ugc_supply_acquisition.sh` from crontab; set `HERMES_CRON_SCRIPT_TIMEOUT=2400` |
| `emails_sent: 254` vs `sent=0` | Lifetime `/outreach/stats`, not this run | Trust `RESULT inserted=` and `sent=` |
| 0 profiles after 2 seeds | Those handles already in `_tiktok_ugc_state.json` | Expected; need more unique discovery seeds |
| 0 seeds | TikTok blocking VPS / empty hashtag HTML | Set residential `TIKTOK_SHOP_PROXY`; confirm `[InHouse/TT] proxy enabled`; do not turn SerpAPI back on for free-tier 429s |
| 0 profiles / embed `400` / empty HTML | TikTok blocking VPS IP | Set `TIKTOK_SHOP_PROXY` or `IG_PROXY` (residential); confirm proxy log line; `--workers 1`; pause crawl until fixed |
| 0 qualified | Email-in-bio is scarce | Normal; raise `--max-handles`, do not loosen email or 1k floor |
| Inserts all skipped | Handle already in `ugc_supply` | Normal; do not `--ignore-seen` on cron |
| `for-outreach` empty | All emailed or none qualified | Wait for next crawl; do not follow up same day |
| Outreach send failed | Missing `RESEND_API_KEY` on Vercel | Fix API host env; do not SMTP |
| Creators appear on `/admin/brands` | Wrong table | Bug — these must stay in `ugc_supply` |

---

## Files Mahery should sync to the VPS

- `scripts/crawl_tiktok_ugc.py`
- `scripts/run_ugc_supply_acquisition.sh`
- `services/tiktok_ugc_profile_scraper.py`
- `services/tiktok_ugc_lead_writer.py`
- `services/inhouse_social_scraper.py` (TikTok HTTP + Playwright proxy)
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

> 5× daily: `crawl_tiktok_ugc.py --daily --save-to-db --quota 20 --no-serp` → `GET /api/admin/ugc-supply/for-outreach` → `POST /outreach/bulk` max 20 (locked match-invite template). Never touch `pr_brands`. Never SerpAPI on the free plan.
