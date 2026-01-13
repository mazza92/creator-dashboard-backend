# External Cron Setup

## PR Packages Introduction for Existing Creators

### Quick Start Guide

📖 **For detailed step-by-step instructions, see:** [`PR_INTRO_CAMPAIGN_GUIDE.md`](./PR_INTRO_CAMPAIGN_GUIDE.md)

### One-Time Campaign Endpoint

**Endpoint:** `POST /api/cron/send-pr-introduction-to-existing-creators`

**Purpose:** Send PR Packages introduction email to all existing creators who haven't set up PR preferences yet.

**Authentication:** Optional - set `CRON_SECRET` environment variable and send it in `X-Cron-Secret` header

**How it works:**
- Finds all verified creators who:
  - Haven't received PR introduction email yet
  - Don't have PR preferences set up (empty or null `pr_wishlist`)
- Sends introduction email in batches of 100
- Marks each creator as sent to prevent duplicates
- Processes up to 100 creators per call (run multiple times if needed)

**Quick Example:**
```bash
# 1. First, run the migration (see PR_INTRO_CAMPAIGN_GUIDE.md)
# 2. Then send to creators:
curl -X POST https://api.newcollab.co/api/cron/send-pr-introduction-to-existing-creators \
  -H "Content-Type: application/json" \
  -H "X-Cron-Secret: your-secret-key"
```

**Response:**
```json
{
  "success": true,
  "total_found": 50,
  "sent": 48,
  "errors": 2,
  "message": "Sent PR Packages introduction to 48 creators"
}
```

**Best Practice:**
1. ✅ Run the database migration first (see guide)
2. ✅ Test locally before production
3. ✅ Run this endpoint once to send to all existing creators
4. ✅ Run it periodically (weekly) to catch any new creators who missed it
5. ✅ New creators automatically get the email 2 days after creating their first ad slot

### Database Migration

Run the migration to add the tracking flag:
```sql
-- See: migrations/add_pr_introduction_sent_flag.sql
-- Or follow the detailed guide: PR_INTRO_CAMPAIGN_GUIDE.md
```

---

## PR Package Email Reminders

The system includes automated email reminders for PR package collaborations:

### Reminder Types

1. **Product Received Check** (7 days after shipping)
   - Sent to creators 7 days after a brand ships a PR package
   - Reminds creators to confirm product receipt if they haven't already
   - Only sent if package status is still 'shipped'

2. **Start Content Reminder** (48 hours after product received)
   - Sent to creators 48 hours after they confirm product receipt
   - Encourages creators to start creating content
   - Only sent if status is still 'product_received' or 'content_in_progress'

### Cron Endpoint

**Endpoint:** `POST /api/cron/process-pr-reminders`

**Authentication:** Optional - set `CRON_SECRET` environment variable and send it in `X-Cron-Secret` header

**Frequency:** Recommended to run every hour

**Example Cron Job:**
```bash
# Run every hour
0 * * * * curl -X POST https://your-domain.com/api/cron/process-pr-reminders \
  -H "X-Cron-Secret: your-secret-key"
```

### Database Migration

Run the migration to create the reminders table:
```sql
-- See: migrations/create_pr_email_reminders_table.sql
```

### How It Works

1. When a brand ships a package → reminder scheduled for 7 days later
2. When a creator confirms receipt → reminder scheduled for 48 hours later
3. Cron job processes due reminders hourly
4. Reminders are automatically cancelled if status has changed (e.g., product already received)

---

## Onboarding Reminders

## Overview
The onboarding reminder system has been modified to work with Vercel's serverless architecture using external cron services instead of APScheduler.

## Changes Made
- ✅ Removed APScheduler dependencies
- ✅ Removed local scheduler initialization
- ✅ Updated API endpoint for external cron
- ✅ Removed APScheduler from requirements.txt

## Setup Instructions

### Option 1: Cron-job.org (Recommended)

1. **Go to [cron-job.org](https://cron-job.org)**
2. **Create a free account**
3. **Add a new cron job:**
   - **URL**: `https://api.newcollab.co/api/send-onboarding-reminders`
   - **Method**: `POST`
                  - **Schedule**: `*/30 * * * *` (every 30 minutes)
   - **Headers**: `Content-Type: application/json`
   - **Body**: `{}`

### Option 2: GitHub Actions (Alternative)

Create `.github/workflows/onboarding-reminders.yml`:

```yaml
name: Onboarding Reminders

on:
  schedule:
                    - cron: '*/30 * * * *'  # Every 30 minutes

jobs:
  send-reminders:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Onboarding Reminders
        run: |
          curl -X POST \
            -H "Content-Type: application/json" \
            -d '{}' \
            https://api.newcollab.co/api/send-onboarding-reminders
```

### Option 3: Vercel Cron (Vercel Pro)

Add to `vercel.json`:

```json
{
  "crons": [
    {
      "path": "/api/send-onboarding-reminders",
                        "schedule": "*/30 * * * *"
    }
  ]
}
```

## Testing

### Test the Endpoint Locally:
```bash
curl -X POST http://localhost:5000/api/send-onboarding-reminders \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Test the Endpoint in Production:
```bash
curl -X POST https://api.newcollab.co/api/send-onboarding-reminders \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Expected Response
```json
{
  "message": "Onboarding reminders processed successfully"
}
```

## Monitoring
- Check Vercel logs for cron execution
- Monitor email delivery in your SMTP logs
- Use the `/api/check-incomplete-profiles` endpoint to verify users

## Benefits
- ✅ Works with Vercel serverless architecture
- ✅ No additional dependencies
- ✅ Reliable external service
- ✅ Easy to monitor and debug
- ✅ Free tier available on most services 