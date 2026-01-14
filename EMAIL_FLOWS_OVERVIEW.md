# Email Flows Overview

Complete documentation of all automated emails sent to users, their triggers, and schedules.

---

## 📧 Email Templates

All email templates are located in: `templates/`

| Template File | Used For | Layout |
|--------------|----------|---------|
| `welcome_email.html` | New user registration | Custom branded layout |
| `onboarding_reminder.html` | All reminder emails | Flexible gradient header, reusable |
| `email_template.html` | Generic notifications | Standard layout |
| `email_template.txt` | Plain text fallback | Text only |
| `bio_url_reminder.html` | (Legacy) Bio URL reminders | - |

---

## 📬 Active Email Flows

### 1. Welcome Email (Immediate)

**When Sent**: Immediately upon user registration

**Trigger**: User completes signup form and verifies email

**Template**: `welcome_email.html`

**Subject**: "Welcome to NewCollab! Your creator journey starts here 🚀"

**Content**:
- Welcome message
- Account verification confirmation
- 229+ PR brands available to browse
- "Next: Browse brands & unlock 5 contacts today (FREE)"
- Quick access buttons to directory
- Social proof and stats

**Code Location**: `app.py` - registration endpoints

**Frequency**: Once per user, at signup

**Can Unsubscribe**: No (transactional)

---

### 2. Onboarding Reminder (Automated)

**When Sent**: 24 hours after signup IF profile incomplete

**Trigger Conditions**:
- User registered 24+ hours ago
- Email is verified
- Missing `instagram_handle` OR `niche`
- No reminder sent in last 7 days

**Template**: `onboarding_reminder.html`

**Subject**: "Complete your NewCollab profile to unlock 229+ PR brands"

**Content**:
- "We noticed you started creating your profile but haven't finished yet!"
- Urgency section: "Don't Miss Out! 229+ brands are actively accepting PR applications"
- Benefits: Access to brands, direct PR emails, 5 FREE unlocks/day, $500+ product value
- Stats: 229+ brands, 1,000+ creators, $500+ avg package value
- CTA: "Complete Your Profile" → links to `/creator/dashboard/profile`

**Endpoint**: `/api/cron/send-onboarding-reminders`

**Schedule**: Every 30 minutes

**Cooldown**: 7 days between reminders (spam prevention)

**Batching**: 50 users per run

**Database Tracking**: `creators.last_reminder_sent`

**Can Unsubscribe**: No (encouraged action)

---

### 3. New Brands Notification (Daily)

**When Sent**: Daily at 9 AM, IF new brands added

**Trigger Conditions**:
- Brands added to directory in last 24 hours
- `is_public = true` on brand
- User is "active" (logged in within 30 days)
- User has completed profile (has `instagram_handle`)
- No notification sent in last 7 days

**Template**: `onboarding_reminder.html` (reused with custom content)

**Subject**: "🎉 {X} new PR brands just added to NewCollab!"

**Content**:
- "Hey {username}! We just added {X} new brands that might be perfect for you:"
- Brand cards with:
  - Logo
  - Brand name
  - Category
  - "View Brand →" link
- CTA: "Browse New Brands" → links to `/directory`

**Endpoint**: `/api/cron/send-new-brands-notification`

**Schedule**: Daily at 9:00 AM (`0 9 * * *`)

**Cooldown**: 7 days per creator

**Batching**: 100 creators per run

**Database Tracking**: `creators.last_new_brands_email_sent`

**Can Unsubscribe**: Yes (marketing email)

---

### 4. PR Package Receipt Check (Automated)

**When Sent**: 7 days after brand ships package

**Trigger Conditions**:
- PR offer status = `'shipped'`
- Shipped 7 days ago (between 7-8 days)
- No reminder already sent for this offer
- Creator email verified

**Template**: `onboarding_reminder.html`

**Subject**: "Did you receive your PR package from {Brand Name}?"

**Content**:
- "Hey {creator_name}! It's been a week since {brand_name} shipped your PR package."
- "Have you received it yet?"
- "Please confirm receipt so the brand knows their package arrived safely."
- CTA: "Confirm Receipt" → links to `/creator/dashboard/pr-pipeline`

**Endpoint**: `/api/cron/process-pr-reminders`

**Schedule**: Every hour (`0 * * * *`)

**Cooldown**: One-time per offer (won't send again)

**Batching**: 50 offers per run

**Database Tracking**: `pr_email_reminders` table with `reminder_type = 'product_received_check'`

**Can Unsubscribe**: No (transactional - related to active collaboration)

---

### 5. PR Content Creation Reminder (Automated)

**When Sent**: 48 hours after creator confirms receipt

**Trigger Conditions**:
- PR offer status = `'product_received'` OR `'content_in_progress'`
- Product received 48-72 hours ago
- No reminder already sent for this offer
- Creator email verified

**Template**: `onboarding_reminder.html`

**Subject**: "Time to create content for {Brand Name}! 🎥"

**Content**:
- "Hey {creator_name}! Now that you've received your PR package from {brand_name}, it's time to create some amazing content!"
- "Brands love seeing content within 2-3 days of receipt."
- "Let's keep that momentum going! 🚀"
- CTA: "Update Status" → links to `/creator/dashboard/pr-pipeline`

**Endpoint**: `/api/cron/process-pr-reminders`

**Schedule**: Every hour (`0 * * * *`)

**Cooldown**: One-time per offer

**Batching**: 50 offers per run

**Database Tracking**: `pr_email_reminders` table with `reminder_type = 'start_content'`

**Can Unsubscribe**: No (transactional - related to active collaboration)

---

## 📊 Email Flow Timeline

### New User Journey

```
Day 0 (Signup)
├─ [Immediate] Welcome Email
│
Day 1 (If incomplete profile)
├─ [+24h] Onboarding Reminder #1
│
Day 8 (If still incomplete)
├─ [+7 days] Onboarding Reminder #2
│
Day 15 (If still incomplete)
├─ [+7 days] Onboarding Reminder #3
└─ ... (continues every 7 days until profile completed)
```

### Active User Journey

```
Daily 9:00 AM
├─ [Check] New brands added yesterday?
│   └─ [Yes] Send notification (if no email in last 7 days)
│   └─ [No] Skip
```

### PR Package Journey

```
Day 0 - Brand sends PR offer
├─ Creator accepts offer
│
Day X - Brand ships package
├─ Status: 'shipped'
│
Day X+7 - Receipt Check
├─ [Email] "Did you receive your package?"
│
Day X+7 (+2h) - Creator confirms receipt
├─ Status: 'product_received'
│
Day X+9 - Content Reminder
├─ [Email] "Time to create content!"
│
Day X+12 - Creator submits content
└─ Status: 'content_submitted' → No more emails
```

---

## 🔧 Cron Job Configuration

### Required Cron Jobs on cron-job.org

| Name | URL | Method | Schedule | Frequency |
|------|-----|--------|----------|-----------|
| Onboarding Reminders | `https://api.newcollab.co/api/cron/send-onboarding-reminders` | POST | `*/30 * * * *` | Every 30 min |
| New Brands Notification | `https://api.newcollab.co/api/cron/send-new-brands-notification` | POST | `0 9 * * *` | Daily at 9 AM |
| PR Package Reminders | `https://api.newcollab.co/api/cron/process-pr-reminders` | POST | `0 * * * *` | Every hour |

### Headers for All Endpoints
```
Content-Type: application/json
```

---

## 🛡️ Spam Prevention

All automated emails include spam prevention:

| Email Type | Prevention Method | Cooldown Period |
|------------|-------------------|-----------------|
| Onboarding Reminder | `last_reminder_sent` timestamp | 7 days |
| New Brands Notification | `last_new_brands_email_sent` timestamp | 7 days |
| PR Receipt Check | `pr_email_reminders` record | One-time only |
| PR Content Reminder | `pr_email_reminders` record | One-time only |

---

## 📈 Email Analytics Queries

### Check Onboarding Reminders Sent (Last 7 Days)
```sql
SELECT COUNT(*) as total_incomplete,
       COUNT(CASE WHEN last_reminder_sent > NOW() - INTERVAL '7 days' THEN 1 END) as reminded_last_7d
FROM creators
WHERE instagram_handle IS NULL OR instagram_handle = ''
   OR niche IS NULL OR niche = '';
```

### Check New Brands Notifications Sent (Last 30 Days)
```sql
SELECT COUNT(*) as total_eligible,
       COUNT(CASE WHEN last_new_brands_email_sent > NOW() - INTERVAL '30 days' THEN 1 END) as notified_last_30d
FROM creators
WHERE instagram_handle IS NOT NULL
  AND instagram_handle != '';
```

### Check PR Reminders Sent (Last 7 Days)
```sql
SELECT reminder_type,
       COUNT(*) as emails_sent,
       COUNT(DISTINCT offer_id) as unique_offers
FROM pr_email_reminders
WHERE sent_at > NOW() - INTERVAL '7 days'
GROUP BY reminder_type;
```

### Email Send Rate (Last 24 Hours)
```sql
-- Onboarding reminders
SELECT COUNT(*) as onboarding_emails
FROM creators
WHERE last_reminder_sent > NOW() - INTERVAL '24 hours';

-- New brands notifications
SELECT COUNT(*) as new_brands_emails
FROM creators
WHERE last_new_brands_email_sent > NOW() - INTERVAL '24 hours';

-- PR reminders
SELECT COUNT(*) as pr_emails
FROM pr_email_reminders
WHERE sent_at > NOW() - INTERVAL '24 hours';
```

---

## 🎯 Email Performance Targets

| Metric | Target | Current Check |
|--------|--------|---------------|
| Onboarding completion rate | 60%+ | Check incomplete profiles 7+ days old |
| New brands click-through rate | 20%+ | Track link clicks (future) |
| PR receipt confirmation | 80%+ | Check offers stuck in 'shipped' status |
| PR content submission | 70%+ | Check offers stuck in 'product_received' status |

---

## ⚠️ Important Notes

1. **Transactional vs Marketing**:
   - Welcome, PR reminders = Transactional (cannot opt-out)
   - Onboarding, New brands = Marketing (should have unsubscribe)

2. **Email Rate Limits**:
   - Batching prevents overwhelming SMTP server
   - 50-100 emails per cron run
   - Runs spread throughout day

3. **Unsubscribe Functionality**:
   - Currently NOT implemented
   - Should be added for marketing emails
   - Keep transactional emails mandatory

4. **Email Testing**:
   - Test endpoints manually before enabling cron
   - Monitor logs for send failures
   - Check spam folder for deliverability

5. **SMTP Configuration**:
   - Requires: `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
   - Sender name: `EMAIL_SENDER_NAME` (default: "NewCollab")
   - Uses TLS encryption

---

## 🔮 Future Email Enhancements

1. **Weekly Digest Email**:
   - Summary of new brands from past week
   - Creator's saved brands activity
   - Platform statistics

2. **Re-engagement Campaign**:
   - Target creators inactive 30+ days
   - Highlight new features
   - "We miss you" messaging

3. **Brand Match Suggestions**:
   - AI-powered brand recommendations
   - Based on creator niche and audience
   - Weekly personalized list

4. **Collaboration Milestones**:
   - "You've completed 5 PR packages!"
   - Achievement badges
   - Encourage continued engagement

5. **Email Preferences Center**:
   - Let users choose email frequency
   - Opt-in/out of specific email types
   - Custom notification settings

---

## 📝 Testing Checklist

Before enabling each cron job:

- [ ] Run migration to add tracking columns
- [ ] Test endpoint manually with curl
- [ ] Verify email template renders correctly
- [ ] Check SMTP credentials are set
- [ ] Confirm database queries return expected data
- [ ] Enable cron job with correct schedule
- [ ] Monitor first few runs for errors
- [ ] Check recipient spam folders
- [ ] Verify unsubscribe links work (for marketing emails)
- [ ] Monitor delivery rates in SMTP logs
