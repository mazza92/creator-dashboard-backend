# Database Migration Instructions

## Add Onboarding Email Sequence Columns

Your users table needs 5 new columns to track which onboarding emails have been sent. 

### Option 1: Run the Migration Script (Recommended)

1. **Connect to your PostgreSQL database:**
   ```bash
   psql -h your_host -U your_user -d your_database
   ```

2. **Run the migration script:**
   ```bash
   \i migrations/add_onboarding_email_columns_to_existing_db.sql
   ```
   
   Or if running from command line:
   ```bash
   psql -h your_host -U your_user -d your_database -f migrations/add_onboarding_email_columns_to_existing_db.sql
   ```

3. **Verify the columns were added:**
   ```sql
   SELECT column_name, data_type 
   FROM information_schema.columns 
   WHERE table_name = 'users' 
   AND column_name LIKE 'onboarding_email%'
   ORDER BY column_name;
   ```

### Option 2: Let the Application Create Columns Automatically

The application will automatically create the columns when it first sends an email. However, this is slower and doesn't create indexes upfront.

**Note:** The automatic creation only happens when the first email is sent, so it's better to run the migration first.

---

## What Gets Added

The migration adds these 5 columns to your `users` table:

1. `onboarding_email_1_sent_at` - Timestamp when Welcome email was sent
2. `onboarding_email_2_sent_at` - Timestamp when Value Focus email was sent
3. `onboarding_email_3_sent_at` - Timestamp when Social Proof email was sent
4. `onboarding_email_4_sent_at` - Timestamp when Support email was sent
5. `onboarding_email_5_sent_at` - Timestamp when Last Chance email was sent

All columns are `TIMESTAMP` type and can be `NULL` (NULL means the email hasn't been sent yet).

---

## Verify Migration Success

After running the migration, verify with:

```sql
-- Check if all columns exist
SELECT 
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns 
WHERE table_name = 'users' 
AND column_name LIKE 'onboarding_email%'
ORDER BY column_name;
```

Expected output:
```
      column_name              | data_type | is_nullable 
------------------------------+-----------+-------------
 onboarding_email_1_sent_at  | timestamp | YES
 onboarding_email_2_sent_at   | timestamp | YES
 onboarding_email_3_sent_at  | timestamp | YES
 onboarding_email_4_sent_at  | timestamp | YES
 onboarding_email_5_sent_at  | timestamp | YES
```

---

## Troubleshooting

### Issue: "relation users does not exist"
**Solution:** Make sure you're connected to the correct database.

### Issue: "permission denied"
**Solution:** You need ALTER TABLE permissions. Run as a database owner or superuser.

### Issue: Columns already exist
**Solution:** The script is idempotent - it won't fail if columns already exist. It's safe to run multiple times.

---

## Next Steps

After running the migration:

1. ✅ **Test the email sequence** with a test account
2. ✅ **Monitor logs** to see emails being sent
3. ✅ **Check database** to see timestamps being updated
4. ✅ **Track metrics** on completion rates

The system will start working automatically once the columns are added!

