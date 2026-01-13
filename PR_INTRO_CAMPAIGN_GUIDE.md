# PR Packages Introduction Campaign - Step-by-Step Guide

## Overview
This guide will help you send the PR Packages introduction email to all existing creators who haven't set up their PR preferences yet.

---

## Step 1: Run the Database Migration

First, you need to add the tracking column to the `users` table.

### Option A: Using Supabase Dashboard
1. Go to your Supabase project dashboard
2. Navigate to **SQL Editor**
3. Copy and paste the contents of `migrations/add_pr_introduction_sent_flag.sql`:
   ```sql
   ALTER TABLE users 
   ADD COLUMN IF NOT EXISTS pr_packages_intro_sent_at TIMESTAMP;

   CREATE INDEX IF NOT EXISTS idx_users_pr_intro_sent ON users(pr_packages_intro_sent_at) WHERE pr_packages_intro_sent_at IS NULL;
   ```
4. Click **Run** to execute the migration
5. Verify it worked - you should see "Success. No rows returned"

### Option B: Using psql Command Line
```bash
psql -h your-db-host -U your-username -d your-database -f migrations/add_pr_introduction_sent_flag.sql
```

---

## Step 2: Test the Endpoint Locally (Optional but Recommended)

Before sending to all creators, test with a small batch:

### Test Locally:
```bash
# Make sure your Flask server is running on localhost:5000
curl -X POST http://localhost:5000/api/cron/send-pr-introduction-to-existing-creators \
  -H "Content-Type: application/json"
```

**Expected Response:**
```json
{
  "success": true,
  "total_found": 5,
  "sent": 5,
  "errors": 0,
  "message": "Sent PR Packages introduction to 5 creators"
}
```

---

## Step 3: Send to Production Creators

### Option A: Using cURL (One-time Manual Send)

```bash
# Replace with your actual domain and secret key
curl -X POST https://api.newcollab.co/api/cron/send-pr-introduction-to-existing-creators \
  -H "Content-Type: application/json" \
  -H "X-Cron-Secret: your-secret-key-here"
```

**Note:** 
- If you have more than 100 creators, you'll need to run this multiple times
- Each run processes up to 100 creators
- The endpoint automatically skips creators who already received the email

### Option B: Using Postman or Similar Tool

1. **Method:** POST
2. **URL:** `https://api.newcollab.co/api/cron/send-pr-introduction-to-existing-creators`
3. **Headers:**
   - `Content-Type: application/json`
   - `X-Cron-Secret: your-secret-key-here` (if you set CRON_SECRET)
4. **Body:** (empty or `{}`)
5. Click **Send**

### Option C: Using Python Script

Create a file `send_pr_intro.py`:

```python
import requests
import os

# Configuration
API_URL = "https://api.newcollab.co/api/cron/send-pr-introduction-to-existing-creators"
CRON_SECRET = os.getenv("CRON_SECRET", "your-secret-key-here")

headers = {
    "Content-Type": "application/json",
    "X-Cron-Secret": CRON_SECRET
}

# Send request
response = requests.post(API_URL, headers=headers, json={})

print(f"Status Code: {response.status_code}")
print(f"Response: {response.json()}")
```

Run it:
```bash
python send_pr_intro.py
```

---

## Step 4: Handle Large Creator Base (100+ creators)

If you have more than 100 creators, run the endpoint multiple times:

### Quick Script to Send to All:

```python
import requests
import time

API_URL = "https://api.newcollab.co/api/cron/send-pr-introduction-to-existing-creators"
CRON_SECRET = "your-secret-key-here"

headers = {
    "Content-Type": "application/json",
    "X-Cron-Secret": CRON_SECRET
}

total_sent = 0
batch = 1

while True:
    print(f"\n📧 Running batch {batch}...")
    response = requests.post(API_URL, headers=headers, json={})
    
    if response.status_code != 200:
        print(f"❌ Error: {response.text}")
        break
    
    data = response.json()
    sent = data.get('sent', 0)
    total_found = data.get('total_found', 0)
    total_sent += sent
    
    print(f"✅ Batch {batch}: Sent {sent} emails")
    print(f"📊 Total sent so far: {total_sent}")
    
    # If no more creators found, we're done
    if total_found == 0 or sent == 0:
        print(f"\n🎉 Campaign complete! Total emails sent: {total_sent}")
        break
    
    batch += 1
    # Wait 2 seconds between batches to avoid rate limiting
    time.sleep(2)
```

---

## Step 5: Verify Results

### Check How Many Creators Received the Email:

```sql
-- In Supabase SQL Editor
SELECT COUNT(*) as total_sent
FROM users
WHERE pr_packages_intro_sent_at IS NOT NULL;
```

### Check How Many Still Need It:

```sql
-- Creators who haven't received it yet and don't have preferences
SELECT COUNT(*) as still_need_intro
FROM users u
JOIN creators c ON u.id = c.user_id
WHERE u.role = 'creator'
AND u.is_verified = true
AND u.pr_packages_intro_sent_at IS NULL
AND (
    c.pr_wishlist IS NULL 
    OR c.pr_wishlist = '[]'::jsonb 
    OR c.pr_wishlist = 'null'::jsonb
);
```

---

## Step 6: Monitor Email Delivery

1. **Check your SMTP logs** for successful deliveries
2. **Check Flask application logs** for any errors:
   ```bash
   # If running locally, check terminal output
   # Look for lines like:
   # ✅ PR Packages introduction sent successfully to user@example.com
   ```

---

## Troubleshooting

### Issue: "Unauthorized" Error
**Solution:** Make sure you're sending the `X-Cron-Secret` header with the correct value that matches your `CRON_SECRET` environment variable.

### Issue: No Creators Found
**Possible reasons:**
- All creators already received the email
- All creators already have PR preferences set up
- Migration wasn't run (check if column exists)

**Check:**
```sql
-- Verify column exists
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'users' 
AND column_name = 'pr_packages_intro_sent_at';
```

### Issue: Some Emails Failed
- Check SMTP configuration
- Verify email addresses are valid
- Check application logs for specific error messages
- Failed emails won't be marked as sent, so you can retry

---

## Best Practices

1. ✅ **Test first** - Always test locally or with a small batch first
2. ✅ **Monitor closely** - Watch the first batch to ensure emails are sending correctly
3. ✅ **Check SMTP limits** - Make sure you don't exceed your email provider's rate limits
4. ✅ **Run in batches** - For large lists, process in batches with delays
5. ✅ **Verify results** - Check the database to confirm emails were sent
6. ✅ **Follow up** - Consider running weekly to catch any missed creators

---

## Next Steps After Campaign

1. **Monitor engagement** - Check how many creators set up preferences after receiving the email
2. **In-app prompts** - The dashboard already shows prompts for creators without preferences
3. **Automated for new creators** - New creators will automatically get the email 2 days after their first ad slot

---

## Quick Reference

**Endpoint:** `POST /api/cron/send-pr-introduction-to-existing-creators`

**Headers:**
- `Content-Type: application/json`
- `X-Cron-Secret: your-secret-key` (optional but recommended)

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

**Batch Size:** 100 creators per run

**Frequency:** Run multiple times until `total_found` is 0

