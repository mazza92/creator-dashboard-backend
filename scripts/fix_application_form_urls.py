"""
Fix missing application_form_url for brands that should have them
Run this to update existing brands with application form URLs
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT', 5432),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)

cursor = conn.cursor()

# Brands that should have application_form_url
# Format: (brand_name, application_form_url)
brands_with_forms = [
    ('Mecca', 'https://www.mecca.com.au/influencer'),
    ('Bondi Sands', 'https://bondisands.com/pages/ambassador'),
    ('Sand & Sky', 'https://sandandsky.com/pages/influencer'),
    ('Princess Polly', 'https://www.princesspolly.com/pages/influencer'),
    ('Glossier', 'https://www.glossier.com/influencer'),
    ('ColourPop', 'https://colourpop.com/pages/pr'),
    ('The Ordinary', 'https://theordinary.com/en-us/influencer'),
    ('Glow Recipe', 'https://www.glowrecipe.com/pages/influencer'),
    ('Tula Skincare', 'https://www.tula.com/pages/ambassador'),
    ('Peach & Lily', 'https://www.peachandlily.com/pages/influencer'),
    ('Gymshark', 'https://www.gymshark.com/pages/athletes'),
    ('ASOS', 'https://www.asos.com/discover/insiders/'),
    ('Revolve', 'https://www.revolve.com/r/Influencer/'),
    ('PrettyLittleThing', 'https://www.prettylittlething.com/plt-shape'),
    ('Lounge Underwear', 'https://www.loungeunderwear.com/pages/ambassador'),
    ('Lululemon', 'https://www.lululemon.com/en-us/ambassadors'),
    ('Razer', 'https://www.razer.com/ambassadors'),
    ('SteelSeries', 'https://steelseries.com/sponsorships'),
    ('Logitech G', 'https://www.logitechg.com/en-us/sponsorship'),
    ('Corsair', 'https://www.corsair.com/us/en/sponsorship'),
    ('GFUEL', 'https://gfuel.com/pages/sponsorship'),
    ('Liquid I.V.', 'https://www.liquid-iv.com/pages/ambassador'),
    ('PopSockets', 'https://www.popsockets.com/pages/influencer'),
    ('Casetify', 'https://www.casetify.com/pages/co-lab'),
    ('Patagonia', 'https://www.patagonia.com/ambassadors/'),
    ('Olly', 'https://olly.com/pages/ambassador'),
]

print(f'Updating {len(brands_with_forms)} brands with application form URLs...\n')

updated = 0
not_found = 0

for brand_name, form_url in brands_with_forms:
    cursor.execute('''
        UPDATE pr_brands
        SET application_form_url = %s,
            has_application_form = true,
            updated_at = NOW()
        WHERE brand_name = %s
        RETURNING id, application_form_url
    ''', (form_url, brand_name))

    result = cursor.fetchone()
    if result:
        updated += 1
        print(f'[OK] Updated: {brand_name} -> {form_url}')
    else:
        not_found += 1
        print(f'[NOT FOUND] Not found: {brand_name}')

conn.commit()

# Show stats
print(f'\n--- Summary ---')
print(f'Updated: {updated}')
print(f'Not found: {not_found}')

# Show sample of brands with application forms
cursor.execute('''
    SELECT brand_name, application_form_url
    FROM pr_brands
    WHERE application_form_url IS NOT NULL
    ORDER BY brand_name
    LIMIT 10
''')
results = cursor.fetchall()
print(f'\n--- Sample brands with application forms ---')
for brand_name, url in results:
    print(f'{brand_name}: {url}')

cursor.close()
conn.close()

print('\nDone!')
