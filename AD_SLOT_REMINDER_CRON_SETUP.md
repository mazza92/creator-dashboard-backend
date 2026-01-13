# External Cron Setup for Ad Slot Reminders

## Overview
The ad slot reminder system is designed to send weekly reminders to creators who haven't published any ad slot opportunities (sponsor_drafts). This system works with Vercel's serverless architecture using external cron services.

## System Rules
- ✅ **Target**: Verified creators with no `sponsor_drafts` entries
- ✅ **Frequency**: Once per week (7-day cooldown)
- ✅ **Prevents spam**: Uses `reminder_sent_at` timestamp tracking
- ✅ **Smart filtering**: Only sends to eligible creators

## Setup Instructions

### Option 1: Cron-job.org (Recommended)

1. **Go to [cron-job.org](https://cron-job.org)**
2. **Create a free account** (if you don't have one)
3. **Add a new cron job:**
   - **URL**: `https://api.newcollab.co/api/send-ad-slot-reminders`
   - **Method**: `POST`
   - **Schedule**: `0 9 * * 1` (Every Monday at 9 AM)
   - **Headers**: `Content-Type: application/json`
   - **Body**: `{}`

### Option 2: GitHub Actions (Alternative)

Create `.github/workflows/ad-slot-reminders.yml`:

```yaml
name: Ad Slot Reminders

on:
  schedule:
    - cron: '0 9 * * 1'  # Every Monday at 9 AM

jobs:
  send-reminders:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Ad Slot Reminders
        run: |
                     curl -X POST \
             -H "Content-Type: application/json" \
             -d '{}' \
             https://api.newcollab.co/api/send-ad-slot-reminders
```

### Option 3: Vercel Cron (Vercel Pro)

Add to `vercel.json`:

```json
{
  "crons": [
    {
      "path": "/api/send-ad-slot-reminders",
      "schedule": "0 9 * * 1"
    }
  ]
}
```

## Testing

### Test the Endpoint Locally:
```bash
curl -X POST http://localhost:5000/api/send-ad-slot-reminders \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Test the Endpoint in Production:
```bash
curl -X POST https://api.newcollab.co/api/send-ad-slot-reminders \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Test Individual Email:
```bash
curl -X POST https://api.newcollab.co/api/test-ad-slot-reminder \
  -H "Content-Type: application/json" \
  -d '{"test_email": "team@newcollab.co", "test_role": "creator"}'
```

### Check Eligible Creators:
```bash
curl -X GET https://appbackend-dd18gyi8t-mazza92s-projects.vercel.app/api/check-creators-without-ad-slots
```

## Expected Response
```json
{
  "success": true,
  "message": "Ad slot reminders sent: X successful, Y failed",
  "sent": 5,
  "failed": 0
}
```

## Email Content

**Subject:**
```
Don't miss out: Publish your ad slots to receive bids
```

**Body:**
```
Hi [Creator Name],

We noticed you haven't published any ad slot opportunities yet. Don't miss out on potential brand collaborations!

[3-step visual guide with screenshots]
```

## Monitoring

### Check Vercel Logs:
- Monitor cron execution in Vercel dashboard
- Check for any errors in function logs

### Monitor Email Delivery:
- Check SMTP logs for delivery status
- Monitor bounce rates and engagement

### Verify System Status:
```bash
# Check how many creators need reminders
curl -X GET https://api.newcollab.co/api/check-creators-without-ad-slots
```

## SQL Query Logic

The system uses this query to find eligible creators:

```sql
SELECT DISTINCT 
    u.id as user_id,
    u.email,
    u.first_name,
    u.last_name,
    u.username,
    c.id as creator_id
FROM users u
JOIN creators c ON u.id = c.user_id
WHERE u.role = 'creator' 
AND u.is_verified = true
AND u.id NOT IN (
    SELECT DISTINCT creator_id 
    FROM sponsor_drafts 
    WHERE creator_id IS NOT NULL
)
AND (
    u.reminder_sent_at IS NULL 
    OR u.reminder_sent_at < NOW() - INTERVAL '7 days'
)
```

## Benefits
- ✅ **Weekly frequency** prevents spam
- ✅ **Smart targeting** only verified creators
- ✅ **7-day cooldown** prevents over-messaging
- ✅ **Beautiful email design** with 3-step guide
- ✅ **Easy monitoring** with test endpoints
- ✅ **Production ready** with error handling

## Troubleshooting

### Reset Reminder for Testing:
```bash
curl -X POST https://api.newcollab.co/api/reset-reminder/team@newcollab.co
```

### Check Database Column:
```bash
curl -X GET https://api.newcollab.co/check-db-column
```

### Common Issues:
1. **No emails sent**: Check if creators have published ad slots
2. **Duplicate emails**: Verify 7-day cooldown is working
3. **Template errors**: Check SMTP configuration
4. **Cron not running**: Verify cron-job.org setup 