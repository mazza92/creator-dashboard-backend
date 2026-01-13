# Sync Niche to PR Wishlist

## Overview

This feature automatically populates a creator's `pr_wishlist` from their `niche` field. This ensures creators don't need to manually set PR preferences if they already have a niche defined.

**Important**: This feature **ONLY READS** from the `niche` column and **NEVER MODIFIES** it. The `niche` column remains completely unchanged. We only convert/parse the niche value when writing to `pr_wishlist`.

## How It Works

1. **Automatic Sync**: When a creator's profile is created or updated with a `niche` value, the system automatically checks if their `pr_wishlist` is empty.
2. **If Empty**: If `pr_wishlist` is empty/null, it's automatically populated with the niche value.
3. **If Not Empty**: If the creator has already set PR preferences, the niche sync is skipped to preserve their choices.

## Implementation

### Backend Function

The `sync_niche_to_pr_wishlist(creator_id, niche, conn=None)` function:
- **READS** from the `niche` column (never modifies it)
- Handles niche in any format (JSON array string, single string, or list)
- Converts niche format to match pr_wishlist format requirements
- Checks if `pr_wishlist` is empty
- Populates `pr_wishlist` with the converted niche value if empty
- Supports both JSONB column and separate table approaches
- Only syncs if `pr_wishlist` is empty (doesn't overwrite existing preferences)

### Integration Points

The sync function is automatically called when:
1. **New Creator Profile Created**: When a creator completes onboarding with a niche
2. **Profile Updated**: When a creator updates their profile and niche changes

## Backfilling Existing Creators

### Option 1: SQL Migration (Recommended)

Run the SQL migration to backfill all existing creators:

```bash
psql -h your-host -U your-user -d your-database -f migrations/sync_niche_to_pr_wishlist.sql
```

### Option 2: Python Script

Run the Python backfill script:

```bash
cd creator_dashboard
python scripts/backfill_pr_wishlist_from_niche.py
```

## Verification

After running the backfill, verify the results:

```sql
SELECT 
  COUNT(*) as total_creators,
  COUNT(CASE WHEN niche IS NOT NULL AND niche != '' THEN 1 END) as creators_with_niche,
  COUNT(CASE WHEN pr_wishlist IS NOT NULL AND pr_wishlist != '[]'::jsonb THEN 1 END) as creators_with_pr_wishlist
FROM creators;
```

## Notes

- The sync only happens if `pr_wishlist` is empty
- Creators can still manually update their PR preferences at any time
- The sync preserves existing PR preferences if they've been set
- Works with both JSONB column and separate table implementations

