# Supabase Migration Guide

## Quick Start: Add Onboarding Email Columns

### Step 1: Open Supabase SQL Editor

1. Go to your Supabase project dashboard
2. Click on **"SQL Editor"** in the left sidebar
3. Click **"New query"**

### Step 2: Run the Migration

1. Copy the entire contents of `migrations/supabase_add_onboarding_email_columns.sql`
2. Paste it into the SQL Editor
3. Click **"Run"** (or press `Ctrl+Enter` / `Cmd+Enter`)

### Step 3: Verify Success

After running, you should see:
- ✅ Success messages in the output
- ✅ A result table showing 5 columns (email_1 through email_5)

---

## What Gets Added

The migration adds these 5 columns to your `users` table:

| Column Name | Type | Purpose |
|------------|------|---------|
| `onboarding_email_1_sent_at` | TIMESTAMP | When Welcome email was sent |
| `onboarding_email_2_sent_at` | TIMESTAMP | When Value Focus email was sent |
| `onboarding_email_3_sent_at` | TIMESTAMP | When Social Proof email was sent |
| `onboarding_email_4_sent_at` | TIMESTAMP | When Support email was sent |
| `onboarding_email_5_sent_at` | TIMESTAMP | When Last Chance email was sent |

All columns are nullable (`NULL` means the email hasn't been sent yet).

---

## Verification Query

Run this query to check if columns exist:

```sql
SELECT 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns 
WHERE table_name = 'users' 
AND column_name LIKE 'onboarding_email%'
ORDER BY column_name;
```

**Expected Result:** 5 rows

---

## Test the System

After migration, test with a query:

```sql
-- Check how many creators need emails
SELECT 
    COUNT(*) as total_incomplete,
    COUNT(CASE WHEN onboarding_email_1_sent_at IS NULL THEN 1 END) as needs_email_1,
    COUNT(CASE WHEN onboarding_email_2_sent_at IS NULL THEN 1 END) as needs_email_2,
    COUNT(CASE WHEN onboarding_email_3_sent_at IS NULL THEN 1 END) as needs_email_3
FROM users u
LEFT JOIN creators c ON u.id = c.user_id
WHERE u.role = 'creator'
AND u.is_verified = true
AND c.id IS NULL;
```

---

## Troubleshooting

### Issue: "permission denied"
**Solution:** Make sure you're running as the database owner or have ALTER TABLE permissions.

### Issue: Columns already exist
**Solution:** The script is idempotent - it won't fail. It's safe to run multiple times.

### Issue: No output shown
**Solution:** Check the "Messages" tab in Supabase SQL Editor for notices.

---

## Next Steps

After running the migration:

1. ✅ **Deploy your updated backend code** (the email sequence system)
2. ✅ **Test with a test account** (create a new creator account)
3. ✅ **Monitor logs** to see emails being sent
4. ✅ **Check database** to see timestamps being updated

The system will start working automatically once the columns are added!

---

## Rollback (If Needed)

If you need to remove the columns (not recommended, but possible):

```sql
ALTER TABLE users DROP COLUMN IF EXISTS onboarding_email_1_sent_at;
ALTER TABLE users DROP COLUMN IF EXISTS onboarding_email_2_sent_at;
ALTER TABLE users DROP COLUMN IF EXISTS onboarding_email_3_sent_at;
ALTER TABLE users DROP COLUMN IF EXISTS onboarding_email_4_sent_at;
ALTER TABLE users DROP COLUMN IF EXISTS onboarding_email_5_sent_at;
```

---

**Last Updated:** November 2025

