# Brand Scraper Usage Guide

## Quick Start

The scraper now has **expanded brand lists** and supports different categories.

### Basic Usage

```bash
# Scrape beauty brands (default - 47 brands)
python scripts/free_brand_scraper.py

# Or explicitly specify beauty
python scripts/free_brand_scraper.py beauty

# Scrape fashion brands (25 brands)
python scripts/free_brand_scraper.py fashion

# Scrape lifestyle brands (10 brands)
python scripts/free_brand_scraper.py lifestyle
```

---

## Brand Lists

### Beauty (47 brands)
**Top brands**: glossier, fentybeauty, kyliecosmetics, rarebeauty, milkmakeup, hudabeauty, patmcgrathreal, charlottetilbury, lauramercier, narsissist, anastasiabeverlyhills, benefitcosmetics, toofaced, urbandecaycosmetics, nyxcosmetics, maybelline, loreal, covergirl, elfcosmetics, physiciansformula, wetnwildbeauty, blackupispower, juviasplace, morphebrushes, colourpopcosmetics, bhcosmetics, makeuprevolution

**Skincare**: theordinary, cerave, cetaphil, larocheposay, neutrogena, drunkelephant, tatcha, glow_recipe, summerfriedaysinc, youthtothepeople, inisfree, laneige, korres, fresh, kiehlsus, clinique, esteelauder, shiseido, origins, skinceuticals

**Indie/DTC**: ilia, rmsbeauty, vapourbeauty, kjaerweis, ritueldefille, beautycounter, merit, jonesmroadbeauty, saiebeauty, victoriabeautybeckham

### Fashion (25 brands)
fashionnova, prettylittlething, boohoo, revolve, asos, zaful, shein, misguided, nastygal, dollskill, gymshark, fabletics, lululemon, alo, outdoorvoices, everlane, reformation, aritzia, freepeople, urbanoutfitters, zara, hm, mango, uniqlo, gap

### Lifestyle (10 brands)
amazon, target, walmart, nordstrom, sephora, ulta, bathandbodyworks, lushcosmetics, theBodyShop, spacenk

---

## Expected Output

### First Run (Most brands are new)
```
Category: BEAUTY
Starting free brand scraping...
Total brands to scrape: 47

[1/47] Processing: glossier
=== Scraping brand: glossier (FREE METHOD) ===
1. Scraping Instagram (free)...
   [OK] Found: Glossier
2. Fetching additional data from website...
   [OK] Contact: hello@glossier.com
   ✓ Success (ID: 123)

[2/47] Processing: fentybeauty
=== Scraping brand: fentybeauty (FREE METHOD) ===
1. Scraping Instagram (free)...
   [OK] Found: FENTY BEAUTY BY RIHANNA
...

============================================================
SCRAPING COMPLETE!
============================================================

Results:
  ✓ New brands created: 35
  ⊘ Already existed (skipped): 5
  ✗ Failed: 7
  📊 Success rate: 83.3%

Total brands in database: 117

🎉 Added 35 new brands! Total: 117
```

### Subsequent Runs (Most brands exist)
```
Category: BEAUTY
Starting free brand scraping...
Total brands to scrape: 47

[1/47] Processing: glossier
=== Scraping brand: glossier (FREE METHOD) ===
   [SKIP] Brand already exists (ID: 17, Name: Glossier)

[2/47] Processing: fentybeauty
=== Scraping brand: fentybeauty (FREE METHOD) ===
   [SKIP] Brand already exists (ID: 19, Name: FENTY BEAUTY BY RIHANNA)

[3/47] Processing: kyliecosmetics
...

============================================================
SCRAPING COMPLETE!
============================================================

Results:
  ✓ New brands created: 2
  ⊘ Already existed (skipped): 42
  ✗ Failed: 3

Total brands in database: 119

🎉 Added 2 new brands! Total: 119
```

---

## How It Works

1. **Duplicate Prevention**: Before scraping, checks if Instagram handle exists in database
2. **Skip Existing**: If brand exists, prints `[SKIP]` and moves to next brand
3. **Scrape New**: Only scrapes brands that don't exist yet
4. **Accurate Reporting**: Shows exactly how many were new vs skipped vs failed

---

## Adding More Brands

To add more brands to scrape, edit `scripts/free_brand_scraper.py`:

```python
beauty_brands = [
    # ... existing brands ...
    'your_new_brand_handle',  # Add here
]
```

Or create a new category:

```python
tech_brands = [
    'apple', 'samsung', 'google', 'microsoft', 'adobe'
]

# In the category selection:
elif category.lower() == 'tech':
    brands_to_scrape = tech_brands
```

---

## Tips

### Scraping Efficiently
- Run with `beauty` first (largest list)
- Then run `fashion`
- Then `lifestyle`
- This gives you ~82 brands total

### Checking Results
```bash
# See brands in database
python check_supabase_brands.py

# Check for duplicates
python check_duplicate_brands.py

# Test API endpoint
python test_brands_api.py
```

### If Scraping Fails
Common reasons:
- Instagram rate limiting (wait a bit, try again)
- Invalid Instagram handle (will show in Failed count)
- Network issues (temporary - retry)

---

## Summary

- ✅ **47 beauty brands** ready to scrape
- ✅ **25 fashion brands** ready to scrape
- ✅ **10 lifestyle brands** ready to scrape
- ✅ **Automatic duplicate prevention**
- ✅ **Clear reporting** (new/skipped/failed)
- ✅ **Easy to expand** with more brands

Now you have 82 brands to scrape instead of just 5!
