"""
Migration script to update existing ad slots with currency based on creator's country.
This script maps countries to their primary currencies and updates sponsor_drafts accordingly.

Run this script after adding the currency column to the sponsor_drafts table.
"""

import sys
import os

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_db_connection
from psycopg2.extras import RealDictCursor

# Country to Currency mapping
COUNTRY_TO_CURRENCY = {
    # North America
    'united states': 'USD', 'usa': 'USD', 'united states of america': 'USD',
    'canada': 'CAD',
    'mexico': 'MXN',  # Note: MXN not in supported list, will default to USD
    
    # Europe
    'france': 'EUR', 'french': 'EUR',
    'germany': 'EUR', 'deutschland': 'EUR',
    'spain': 'EUR', 'españa': 'EUR',
    'italy': 'EUR', 'italia': 'EUR',
    'netherlands': 'EUR', 'holland': 'EUR',
    'belgium': 'EUR',
    'austria': 'EUR', 'österreich': 'EUR',
    'finland': 'EUR', 'suomi': 'EUR',
    'portugal': 'EUR',
    'ireland': 'EUR', 'éire': 'EUR',
    'greece': 'EUR', 'ελλάδα': 'EUR',
    'united kingdom': 'GBP', 'uk': 'GBP', 'britain': 'GBP', 'great britain': 'GBP',
    'switzerland': 'CHF', 'schweiz': 'CHF', 'suisse': 'CHF',
    'sweden': 'SEK', 'sverige': 'SEK',
    'norway': 'NOK', 'norge': 'NOK',
    'denmark': 'DKK', 'danmark': 'DKK',
    
    # Asia Pacific
    'australia': 'AUD',
    'new zealand': 'NZD',  # Note: NZD not in supported list, will default to AUD
    'japan': 'JPY', '日本': 'JPY',
    
    # Default to EUR for other countries
}

# Supported currencies (from currency.js)
SUPPORTED_CURRENCIES = ['EUR', 'USD', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'SEK', 'NOK', 'DKK']


def normalize_country(country_name):
    """Normalize country name for matching."""
    if not country_name:
        return None
    return country_name.strip().lower()


def get_currency_for_country(country_name):
    """
    Get currency for a country name.
    Returns a supported currency or defaults to EUR.
    """
    if not country_name:
        return 'EUR'
    
    normalized = normalize_country(country_name)
    
    # Direct lookup
    if normalized in COUNTRY_TO_CURRENCY:
        currency = COUNTRY_TO_CURRENCY[normalized]
        # Ensure it's a supported currency
        if currency in SUPPORTED_CURRENCIES:
            return currency
    
    # Try partial matching
    for key, currency in COUNTRY_TO_CURRENCY.items():
        if key in normalized or normalized in key:
            if currency in SUPPORTED_CURRENCIES:
                return currency
    
    # Default to EUR
    return 'EUR'


def update_ad_slots_currency():
    """
    Update all ad slots (sponsor_drafts) with currency based on creator's country.
    Only updates rows where currency is NULL or 'EUR' (assuming EUR is the default).
    """
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if currency column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'sponsor_drafts' 
            AND column_name = 'currency'
        """)
        
        if not cursor.fetchone():
            print("❌ Currency column does not exist in sponsor_drafts table.")
            print("Please run the SQL migration first: migrations/add_currency_to_sponsor_drafts.sql")
            return
        
        # Get all ad slots with their creator's country
        cursor.execute("""
            SELECT 
                sd.id,
                sd.currency,
                u.country
            FROM sponsor_drafts sd
            JOIN creators c ON sd.creator_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE sd.currency IS NULL 
               OR sd.currency = 'EUR'  -- Update EUR defaults too
        """)
        
        ad_slots = cursor.fetchall()
        print(f"📊 Found {len(ad_slots)} ad slots to update")
        
        updated_count = 0
        skipped_count = 0
        
        for slot in ad_slots:
            slot_id = slot['id']
            current_currency = slot['currency']
            country = slot['country']
            
            # Determine currency based on country
            new_currency = get_currency_for_country(country)
            
            # Only update if currency would change
            if current_currency != new_currency:
                cursor.execute("""
                    UPDATE sponsor_drafts 
                    SET currency = %s 
                    WHERE id = %s
                """, (new_currency, slot_id))
                
                updated_count += 1
                print(f"  ✓ Updated ad slot {slot_id}: {current_currency or 'NULL'} → {new_currency} (country: {country or 'Unknown'})")
            else:
                skipped_count += 1
        
        conn.commit()
        
        print(f"\n✅ Migration complete!")
        print(f"   Updated: {updated_count} ad slots")
        print(f"   Skipped: {skipped_count} ad slots (already correct)")
        
        # Show summary by currency
        cursor.execute("""
            SELECT currency, COUNT(*) as count
            FROM sponsor_drafts
            GROUP BY currency
            ORDER BY count DESC
        """)
        
        summary = cursor.fetchall()
        print(f"\n📈 Currency distribution:")
        for row in summary:
            print(f"   {row['currency']}: {row['count']} ad slots")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error during migration: {str(e)}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == '__main__':
    print("🚀 Starting ad slot currency migration...")
    print("=" * 60)
    update_ad_slots_currency()
    print("=" * 60)
    print("✨ Done!")

