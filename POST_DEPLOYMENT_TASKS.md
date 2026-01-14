# Post-Deployment Tasks

## ✅ Completed Fixes

### Frontend (Deployed)
1. ✅ Fixed public directory hero title overlapping with header
2. ✅ Fixed brand cards showing '0' for minFollowers
3. ✅ Fixed brand page showing '0' in requirements section
4. ✅ Added proper spacing between visit website button and category badge

### Backend (Deployed)
1. ✅ Created complete email flow system with 3 cron endpoints
2. ✅ Implemented smart email logic with spam prevention
3. ✅ Added email tracking database fields

---

## 🔧 Required Post-Deployment Steps

### 1. Run Database Migrations

Execute these migrations in your production database:

```bash
# SSH into your database or use psql
psql $DATABASE_URL

# Run migrations
\i migrations/add_last_reminder_sent.sql
\i migrations/add_email_tracking_fields.sql
```

**Or run SQL directly:**

```sql
-- Migration 1: Onboarding reminders tracking
ALTER TABLE creators ADD COLUMN IF NOT EXISTS last_reminder_sent TIMESTAMP DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_creators_last_reminder_sent ON creators(last_reminder_sent);

-- Migration 2: New brands notification tracking
ALTER TABLE creators ADD COLUMN IF NOT EXISTS last_new_brands_email_sent TIMESTAMP DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_creators_last_new_brands_email ON creators(last_new_brands_email_sent);

-- Migration 3: PR reminders tracking table
CREATE TABLE IF NOT EXISTS pr_email_reminders (
    id SERIAL PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES pr_packages(id) ON DELETE CASCADE,
    reminder_type VARCHAR(50) NOT NULL,
    sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_package ON pr_email_reminders(package_id);
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_type ON pr_email_reminders(reminder_type);
```

### 2. Update Cron Job Configuration

The onboarding reminders endpoint has moved from:
- ❌ OLD: `https://api.newcollab.co/api/send-onboarding-reminders`
- ✅ NEW: `https://api.newcollab.co/api/cron/send-onboarding-reminders`

**Update your cron-job.org configuration:**

1. Go to [cron-job.org](https://cron-job.org)
2. Find the existing "onboarding reminders" job
3. Update the URL to: `https://api.newcollab.co/api/cron/send-onboarding-reminders`
4. Save and re-enable the job

### 3. Create New Cron Jobs

Add these two new cron jobs:

#### A. New Brands Notification
- **URL**: `https://api.newcollab.co/api/cron/send-new-brands-notification`
- **Method**: POST
- **Schedule**: `0 9 * * *` (Daily at 9 AM)
- **Headers**: `Content-Type: application/json`
- **Description**: Notify creators about new brands added in last 24 hours

#### B. PR Package Reminders
- **URL**: `https://api.newcollab.co/api/cron/process-pr-reminders`
- **Method**: POST
- **Schedule**: `0 * * * *` (Every hour)
- **Headers**: `Content-Type: application/json`
- **Description**: Send PR package follow-up reminders

---

## 📧 Email Flow Summary

### 1. Onboarding Reminders
- **Trigger**: 24 hours after signup with incomplete profile
- **Cooldown**: 7 days between reminders
- **Criteria**: Missing instagram_handle or niche
- **Schedule**: Every 30 minutes

### 2. New Brands Notifications
- **Trigger**: New brands added to directory in last 24 hours
- **Cooldown**: 7 days per creator
- **Criteria**: Active creators (logged in within 30 days) with completed profiles
- **Schedule**: Daily at 9 AM
- **Limit**: 100 creators per run

### 3. PR Package Reminders
- **Trigger A**: 7 days after brand ships package (receipt confirmation)
- **Trigger B**: 48 hours after creator confirms receipt (start content)
- **Schedule**: Every hour
- **Tracking**: pr_email_reminders table prevents duplicate sends

---

## 🧪 Testing Endpoints

### Test Onboarding Reminders
```bash
curl -X POST https://api.newcollab.co/api/cron/send-onboarding-reminders \
  -H "Content-Type: application/json"
```

### Test New Brands Notification
```bash
curl -X POST https://api.newcollab.co/api/cron/send-new-brands-notification \
  -H "Content-Type: application/json"
```

### Test PR Reminders
```bash
curl -X POST https://api.newcollab.co/api/cron/process-pr-reminders \
  -H "Content-Type: application/json"
```

**Expected Response (All endpoints):**
```json
{
  "success": true,
  "message": "...",
  "sent": 0,
  "errors": 0
}
```

---

## 🔍 Monitoring

### Check Email Logs
Monitor your SMTP logs or email service dashboard to verify emails are being sent.

### Database Queries

**Check onboarding reminders sent:**
```sql
SELECT COUNT(*) as incomplete_profiles,
       COUNT(last_reminder_sent) as reminders_sent
FROM creators
WHERE instagram_handle IS NULL OR instagram_handle = ''
  OR niche IS NULL OR niche = '';
```

**Check new brands notifications:**
```sql
SELECT COUNT(DISTINCT id) as creators_eligible
FROM creators
WHERE instagram_handle IS NOT NULL
  AND last_new_brands_email_sent < NOW() - INTERVAL '7 days';
```

**Check PR reminders:**
```sql
SELECT COUNT(*) as reminders_sent,
       reminder_type
FROM pr_email_reminders
WHERE sent_at > NOW() - INTERVAL '7 days'
GROUP BY reminder_type;
```

---

## 🚨 Troubleshooting

### 404 Error on Cron Endpoint
- Verify deployment completed on Vercel
- Check backend logs for Python errors
- Ensure blueprint is registered in app.py

### No Emails Being Sent
- Verify SMTP environment variables are set
- Check email template exists: `templates/onboarding_reminder.html`
- Review backend logs for email send errors

### Database Errors
- Ensure migrations have been run
- Check column exists: `\d creators` in psql
- Verify pr_email_reminders table exists

---

## ✨ Benefits of New Email System

1. **Spam Prevention**: 7-day cooldowns prevent overwhelming users
2. **Tracking**: All email sends recorded with timestamps
3. **Engagement**: New brands notification increases platform usage
4. **Retention**: PR reminders keep creators engaged through fulfillment
5. **Scalability**: Batched processing (50-100 per run) prevents timeouts
6. **Maintainability**: Centralized email logic in email_cron_routes.py

---

## 📝 Next Steps (Optional Enhancements)

1. Add unsubscribe functionality per email type
2. Create email preference center for creators
3. Add A/B testing for email subject lines
4. Implement email open/click tracking
5. Add weekly digest email with brand highlights
6. Create brand-side emails for PR package updates
