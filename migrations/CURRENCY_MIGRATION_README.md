# Currency Migration Guide

This guide explains how to migrate existing ad slots to use the correct currency based on the creator's country.

## Problem

Existing ad slots were created before the currency feature was implemented, so they all default to EUR. We need to update them based on each creator's country.

## Solution

We provide two migration steps:

1. **Add the currency column** (if not already done)
2. **Update existing ad slots** with currency based on creator's country

## Step 1: Add Currency Column

Run the SQL migration to add the currency column:

```bash
# For PostgreSQL
psql -d your_database_name -f migrations/add_currency_to_sponsor_drafts.sql

# Or execute the SQL directly in your database client
```

This will:
- Add a `currency` column to `sponsor_drafts` table
- Set default value to 'EUR' for backward compatibility
- Update existing rows to 'EUR'

## Step 2: Update Existing Ad Slots

Run the Python migration script to update ad slots based on creator's country:

```bash
# Make sure you're in the project root directory
cd /path/to/creator_dashboard

# Activate your virtual environment (if using one)
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Run the migration script
python migrations/update_existing_ad_slots_currency.py
```

This script will:
- Find all ad slots with NULL or EUR currency
- Look up each creator's country from the users table
- Map country to appropriate currency (see mapping below)
- Update ad slots with the correct currency
- Show a summary of updates

## Country to Currency Mapping

The script maps countries to currencies as follows:

| Country/Region | Currency |
|----------------|----------|
| United States | USD |
| Canada | CAD |
| United Kingdom | GBP |
| Switzerland | CHF |
| Sweden | SEK |
| Norway | NOK |
| Denmark | DKK |
| Australia | AUD |
| Japan | JPY |
| European Union countries | EUR |
| All other countries | EUR (default) |

## Supported Currencies

The following currencies are supported:
- EUR (Euro)
- USD (US Dollar)
- GBP (British Pound)
- CAD (Canadian Dollar)
- AUD (Australian Dollar)
- JPY (Japanese Yen)
- CHF (Swiss Franc)
- SEK (Swedish Krona)
- NOK (Norwegian Krone)
- DKK (Danish Krone)

## What If a Creator Has No Country Set?

If a creator's country is NULL or not found in the mapping, the ad slot will remain as EUR (the default).

## Verification

After running the migration, you can verify the results:

```sql
-- Check currency distribution
SELECT currency, COUNT(*) as count
FROM sponsor_drafts
GROUP BY currency
ORDER BY count DESC;

-- Check ad slots by country and currency
SELECT 
    u.country,
    sd.currency,
    COUNT(*) as count
FROM sponsor_drafts sd
JOIN creators c ON sd.creator_id = c.id
JOIN users u ON c.user_id = u.id
GROUP BY u.country, sd.currency
ORDER BY u.country, sd.currency;
```

## Manual Updates

If you need to manually update specific ad slots:

```sql
-- Update a specific ad slot
UPDATE sponsor_drafts 
SET currency = 'USD' 
WHERE id = <ad_slot_id>;

-- Update all ad slots for a specific creator
UPDATE sponsor_drafts 
SET currency = 'USD' 
WHERE creator_id = <creator_id>;
```

## Rollback

If you need to rollback (set all back to EUR):

```sql
UPDATE sponsor_drafts 
SET currency = 'EUR';
```

## Notes

- The migration script is idempotent - you can run it multiple times safely
- It only updates ad slots where currency is NULL or 'EUR'
- New ad slots created after this migration will use the currency selected during creation
- The script preserves ad slots that already have a non-EUR currency set

