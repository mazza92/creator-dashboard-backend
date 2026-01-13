# Instagram Rate Limit Solution

## Problem

Instagram is blocking scraping requests with:
```
401 Unauthorized - "Please wait a few minutes before you try again."
```

This happens when making too many requests too quickly.

---

## Solution: Rate-Limited Scraper

I've created a new scraper with built-in rate limiting: `scripts/rate_limited_scraper.py`

### Features

✅ **Smart Delays**: 3-7 seconds between each request (random)
✅ **Extended Breaks**: 15-25 second pause every 10 brands
✅ **Retry Logic**: 3 attempts with exponential backoff (60s, 120s, 180s)
✅ **Auto-Recovery**: Detects rate limits and waits before retrying
✅ **Progress Tracking**: Shows time remaining and success rate

---

## How to Use

### Quick Start
```bash
# Scrape with rate limiting (recommended)
cd scripts
python rate_limited_scraper.py beauty

# Or all categories
python rate_limited_scraper.py all
```

### What It Does

1. **Before each request**: Waits 3-7 seconds (random)
2. **Every 10 brands**: Takes a 15-25 second break
3. **On rate limit**: Waits 60 seconds, then retries
4. **On second rate limit**: Waits 120 seconds, then retries
5. **On third rate limit**: Waits 180 seconds, then retries
6. **After 3 failures**: Skips brand and moves to next

---

## Expected Timeline

With rate limiting, scraping is slower but more reliable:

### Small Batch (10-20 brands)
- **Time**: 5-10 minutes
- **Success Rate**: 80-90%

### Medium Batch (50 brands)
- **Time**: 30-45 minutes
- **Success Rate**: 75-85%

### Large Batch (100 brands)
- **Time**: 1.5-2 hours
- **Success Rate**: 70-80%

### Full 580 Brands
- **Time**: 15-20 hours (run overnight or in batches)
- **Success Rate**: 65-75%
- **Expected Result**: 400-450 new brands

---

## Recommended Strategy

### Strategy 1: Run Overnight (Best)
```bash
# Start before bed, let it run all night
python scripts/rate_limited_scraper.py all
```

**Pros**: Hands-off, completes all brands
**Cons**: Takes 15-20 hours

### Strategy 2: Daily Batches (Safest)
```bash
# Day 1: Beauty (2-3 hours)
python scripts/rate_limited_scraper.py beauty

# Day 2: Fashion (2-3 hours)
python scripts/rate_limited_scraper.py fashion

# Day 3: Lifestyle (1-2 hours)
python scripts/rate_limited_scraper.py lifestyle
```

**Pros**: Avoids long rate limit blocks, can monitor progress
**Cons**: Takes 3 days

### Strategy 3: Smaller Batches (Most Conservative)
Create custom lists of 20-30 brands and run multiple times per day.

**Pros**: Minimal rate limiting risk
**Cons**: More manual work

---

## Output Example

```
============================================================
RATE-LIMITED PRODUCTION SCRAPER - BEAUTY
============================================================
Total brands to scrape: 230
Rate limiting: 3-7s between requests, 15-25s every 10 brands
Retries: 3 attempts with exponential backoff on rate limits
============================================================

SCRAPING IN PROGRESS
============================================================

[1/230] Processing: maybelline

=== Scraping brand: maybelline (FREE METHOD) ===
1. Scraping Instagram (free)...
   [OK] Found: Maybelline New York
   ✓ Success (ID: 150)

[2/230] Processing: loreal
   [SKIP] Brand already exists (ID: 45, Name: L'Oréal)

[10/230] Processing: morphebrushes
   [PAUSE] Taking a 18.3s break after 10 requests...

[15/230] Processing: colourpopcosmetics

=== Scraping brand: colourpopcosmetics (FREE METHOD) ===
1. Scraping Instagram (free)...
   [RATE LIMIT] Instagram rate limit hit. Waiting 60s...
   [RETRY] Attempting again (attempt 2/3)...
1. Scraping Instagram (free)...
   [OK] Found: ColourPop Cosmetics
   ✓ Success (ID: 155)

============================================================
PROGRESS UPDATE - 20/230 (8.7%)
============================================================
New: 15 | Skipped: 3 | Failed: 2
Elapsed: 8.5m | Est. Remaining: 85.0m
============================================================
```

---

## Tips for Success

### 1. Start Small
Test with beauty category first (smaller, better success rate):
```bash
python scripts/rate_limited_scraper.py beauty
```

### 2. Run During Off-Peak Hours
Instagram has less traffic at night (your timezone), which may help.

### 3. Monitor Progress
Check database periodically:
```bash
python check_supabase_brands.py
```

### 4. Don't Panic on Rate Limits
The scraper handles them automatically. You'll see:
```
[RATE LIMIT] Instagram rate limit hit. Waiting 60s...
```
This is normal and expected.

### 5. Resume Capability
If you stop the scraper, just run it again - it will skip brands that already exist.

---

## Alternative: Use Without Instagram

If rate limiting is too problematic, we can scrape without Instagram:

1. Get brand info from website only
2. Skip Instagram profile data
3. Much faster, no rate limits
4. But less brand information

Let me know if you want this option.

---

## Comparison

### Original Scraper (free_brand_scraper.py)
- ❌ No delays
- ❌ No retry logic
- ❌ Hits rate limits quickly
- ✅ Fast when it works

### Rate-Limited Scraper (rate_limited_scraper.py)
- ✅ 3-7s delays between requests
- ✅ Extended breaks every 10 brands
- ✅ 3 retry attempts with exponential backoff
- ✅ Auto-detects and handles rate limits
- ✅ Progress tracking
- ⚠️ Slower (but more reliable)

---

## Current Status

Your database has **82 brands**.

### Recommended Next Steps

1. **Test the rate-limited scraper**:
   ```bash
   cd scripts
   python rate_limited_scraper.py beauty
   ```

2. **Let it run for 2-3 hours** (will get ~50-80 new brands)

3. **Check results**:
   ```bash
   python ../check_supabase_brands.py
   ```

4. **If successful**, continue with more categories

5. **Target**: 500+ brands for production

---

## Files

- `scripts/rate_limited_scraper.py` - New rate-limited scraper
- `scripts/free_brand_scraper.py` - Original (backup)
- `brand_lists_500plus.py` - 580 brand handles
- `RATE_LIMIT_SOLUTION.md` - This guide

---

## Summary

Instagram rate limiting is **expected and normal** when scraping at scale. The solution is to:

1. Add delays between requests (3-7 seconds)
2. Take breaks every 10 brands (15-25 seconds)
3. Retry with exponential backoff on rate limits
4. Be patient - quality over speed

**New scraper**: `scripts/rate_limited_scraper.py`
**Expected time**: 15-20 hours for all 580 brands
**Expected result**: 400-450 new brands (65-75% success rate)

🐌 **Slow and steady wins the race!**
