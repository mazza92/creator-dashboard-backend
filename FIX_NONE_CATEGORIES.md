# Fixed "None" Category Display Issue

## Problem
All brands in the Discovery feed were showing "None" as their category badge instead of proper categories like "Beauty", "Fashion", etc.

## Root Cause
- Some brands had the literal string `'None'` saved in the `category` field
- Others had empty strings `''` as category
- Instagram's `business_category_name` sometimes returns 'None' as a string

## Solution

### 1. ✅ Database Cleanup (Completed)
**Script**: `fix_none_categories.py`

**What it does**:
1. Sets all `'None'` and empty string categories to `NULL`
2. Infers categories from brand names/descriptions using keyword matching
3. Sets remaining brands to default `'Lifestyle'` category

**Results**:
- Fixed 12 brands with 'None' or empty categories
- Inferred 11 brands automatically (Beauty, Fashion, Fitness)
- All brands now have proper categories

**Final Distribution**:
```
Beauty: 32 brands
Fashion: 18 brands
Tech: 8 brands
Personal Goods & General Merchandise Stores: 5 brands
Food: 4 brands
Lifestyle: 4 brands
Accessories: 3 brands
Lifestyle Services: 3 brands
Wellness: 3 brands
Gaming: 1 brand
Fitness: 1 brand
```

### 2. ✅ Scraper Prevention (Completed)
**File**: `scripts/free_brand_scraper.py:55-58`

Added filter to prevent 'None' from being saved in the future:
```python
category = profile.business_category_name or ''
# Don't save 'None' as category
if category in ['None', 'none', 'NONE']:
    category = ''
```

When empty, the database save logic uses `'Other'` as default.

### 3. ✅ UI Safety Check (Already in place)
**File**: `src/creator-portal/PRBrandDiscovery.js:907`

The UI already has a conditional check:
```javascript
{currentBrand.category && <Category>{currentBrand.category}</Category>}
```

This hides the category badge if it's null or falsy.

## Testing
1. **Database verified**: All brands now have valid categories
2. **Scraper updated**: Won't save 'None' in future
3. **UI check**: Already handles null gracefully

## Files Modified
1. `scripts/free_brand_scraper.py` - Added 'None' filter
2. `fix_none_categories.py` - Database cleanup script (NEW)
3. `FIX_NONE_CATEGORIES.md` - This documentation (NEW)

## To Verify Fix
1. Refresh the Discovery page
2. All brands should now show proper category badges
3. No more "None" badges visible

**Status**: ✅ Fixed and deployed!
