# UGC Supply Acquisition Flow (Hermes)

Instructions for a **creator-onboarding** Hermes agent.
Goal: scrape Hannah-shaped TikTok UGC profiles → store in `ugc_supply` → bulk email them to sign up on Newcollab.

**Do not invent SMTP.** Send only through the backend API below (Resend).
**Do not mix these rows with `pr_brands` or brand gifted-UGC billing.**

---

## Pipeline (same shape as Meta Ads brands)

```
TikTok UGC crawler  →  ugc_supply (draft)  →  GET for-outreach  →  POST outreach/bulk
```

| Step | What | Where |
|------|------|--------|
| 1 | Crawl UGC + niche + email + 1k followers | `scripts/crawl_tiktok_ugc.py --daily --save-to-db` |
| 2 | Rows sit in `ugc_supply` `status=draft` `qualified=true` notes `[UGC_SUPPLY_OUTREACH]` | production Supabase |
| 3 | List ready creators | `GET /api/admin/ugc-supply/for-outreach` |
| 4 | Send onboarding emails | `POST /api/admin/ugc-supply/outreach/bulk` |

Admin UI: `https://app.newcollab.co/admin/ugc-supply`
Base URL: `https://api.newcollab.co`
Auth header: `X-Admin-Token: pr-hunter-admin-2026`

---

## API

### List (CRM)

`GET /api/admin/ugc-supply`

Query: `qualified=true` (default), `has_email=true` (default), `not_contacted=true|false`, `status`, `niche`, `min_followers`, `limit` (max 500), `offset`.

JSON includes both `supply` and `leads` (same array).

### Ready to email (use this)

`GET /api/admin/ugc-supply/for-outreach?limit=40`

Returns qualified creators with a public email, status `draft`/`review`, never contacted.

### Ingest crawler JSON (optional)

`POST /api/admin/ugc-supply/ingest`

```json
{ "only_qualified": true, "supply": [ { "handle": "...", "qualified": true, ... } ] }
```

`leads` / `records` are also accepted as the array key.

### Send one

`POST /api/admin/ugc-supply/outreach/send`

```json
{ "lead_id": 123 }
```

Omit `subject` / `html_content` to use the default Newcollab onboarding template.
Tokens: `{{display_name}}`, `{{handle}}`, `{{niche}}`, `{{signup_url}}`, `{{profile_url}}`, `{{followers}}`.

### Bulk send (cap 50 per call)

`POST /api/admin/ugc-supply/outreach/bulk`

```json
{ "lead_ids": [1, 2, 3], "delay_seconds": 0.5 }
```

Already-contacted creators are skipped unless `allow_followup: true`.

### Stats

`GET /api/admin/ugc-supply/outreach/stats`

### Mark reply / skip

`PATCH /api/admin/ugc-supply/<id>`

```json
{ "status": "skipped", "last_response_status": "not_interested" }
```

---

## Hermes loop (copy/paste)

```python
import os
import requests

BASE = "https://api.newcollab.co/api/admin/ugc-supply"
H = {"X-Admin-Token": os.environ["NEWCOLLAB_ADMIN_TOKEN"], "Content-Type": "application/json"}

ready = requests.get(f"{BASE}/for-outreach", params={"limit": 25}, headers=H, timeout=30)
ready.raise_for_status()
payload = ready.json()
rows = payload.get("supply") or payload.get("leads") or []
ids = [row["id"] for row in rows]
print("ready", len(ids), "of", payload["total"])

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

Suggested cap: **25–40 emails per run**, 2 runs/day. Do not dump 1k in one shot.

---

## Scrape → DB (VPS)

```bash
cd /home/hermes/apps/creator_dashboard
source venv/bin/activate
python scripts/crawl_tiktok_ugc.py --daily --save-to-db --quota 1000 --max-handles 2000 \
  --ignore-seen -o logs/tiktok_ugc_$(date -u +%Y%m%d_%H%M).json
```

Requires `DATABASE_URL` and `SERPAPI_API_KEY` in the backend `.env`.
Send requires `RESEND_API_KEY` on the API host (Vercel `appbackend`).

---

## Identity

| Field | Value |
|-------|--------|
| table | `ugc_supply` (never `pr_brands`) |
| API | `/api/admin/ugc-supply` |
| status | `draft` until emailed → `contacted` |
| notes | starts with `[UGC_SUPPLY_OUTREACH]` |
| signup link in emails | `https://app.newcollab.co/signup` |
