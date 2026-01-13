"""
Seed 100+ Brands into PR CRM Database
Comprehensive brand list for creator outreach
"""

import psycopg2
import os
import json
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

# Comprehensive brand list
brands = [
    # ==================== AUSTRALIAN BEAUTY BRANDS ====================
    ('Mecca', 'https://www.mecca.com.au', 'Beauty', ['makeup', 'skincare', 'haircare'], ['Australia'], 3000, ['instagram', 'tiktok'], 'pr@mecca.com.au', '@mecca', None, True, 'https://www.mecca.com.au/influencer', ['makeup', 'skincare'], 38, 7, False, 'Australia\'s largest beauty retailer. Polished media kits preferred.'),
    ('Frank Body', 'https://frankbody.com', 'Beauty', ['skincare', 'body care'], ['Australia', 'US', 'UK'], 5000, ['instagram', 'tiktok'], 'hello@frankbody.com', '@frank_bod', '@frank_bod', False, None, ['body scrubs', 'skincare'], 45, 5, False, 'Coffee scrub brand. Loves authentic, unfiltered content.'),
    ('Bondi Sands', 'https://bondisands.com', 'Beauty', ['tanning', 'skincare'], ['Australia', 'US', 'UK'], 10000, ['instagram', 'tiktok'], 'pr@bondisands.com', '@bondisands', '@bondisands', True, 'https://bondisands.com/pages/ambassador', ['self-tanner', 'gradual tan'], 52, 4, False, 'Self-tanning brand. Beach/summer aesthetic preferred.'),
    ('Naked Sundays', 'https://nakedsundays.com', 'Beauty', ['skincare', 'sunscreen'], ['Australia'], 5000, ['instagram', 'tiktok'], 'pr@nakedsundays.com', '@nakedsundays', '@nakedsundays', False, None, ['sunscreen', 'SPF'], 40, 6, False, 'Clean sunscreen brand. Eco-conscious creators.'),
    ('Sand & Sky', 'https://sandandsky.com', 'Beauty', ['skincare'], ['Australia', 'US'], 8000, ['instagram', 'tiktok'], 'pr@sandandsky.com', '@sandandskyaus', '@sandandsky', True, 'https://sandandsky.com/pages/influencer', ['masks', 'serums'], 48, 5, False, 'Australian pink clay masks. Skincare enthusiasts.'),
    ('BYS Cosmetics', 'https://byscosmetics.com.au', 'Beauty', ['makeup'], ['Australia'], 3000, ['instagram'], 'info@byscosmetics.com.au', '@byscosmetics', None, False, None, ['makeup', 'lipstick'], 35, 8, False, 'Affordable makeup. Entry-level friendly.'),

    # ==================== AUSTRALIAN FASHION BRANDS ====================
    ('Princess Polly', 'https://www.princesspolly.com', 'Fashion', ['fashion', 'streetwear'], ['Australia', 'US'], 15000, ['instagram', 'tiktok'], 'influencers@princesspolly.com', '@princesspolly', '@princesspolly', True, 'https://www.princesspolly.com/pages/influencer', ['dresses', 'tops'], 35, 10, False, 'Gen-Z fashion. OOTD content.'),
    ('Showpo', 'https://www.showpo.com', 'Fashion', ['fashion', 'party wear'], ['Australia', 'US'], 8000, ['instagram'], 'pr@showpo.com', '@showpo', None, False, None, ['dresses', 'playsuits'], 40, 6, False, 'Affordable fast fashion. Quick responses.'),
    ('Beginning Boutique', 'https://beginningboutique.com', 'Fashion', ['fashion'], ['Australia'], 10000, ['instagram'], 'pr@beginningboutique.com', '@beginningboutique', None, False, None, ['dresses', 'tops'], 32, 9, False, 'Trendy fashion. Young creators.'),
    ('Pepper Mayo', 'https://www.peppermayo.com', 'Fashion', ['fashion'], ['Australia'], 12000, ['instagram', 'tiktok'], 'pr@peppermayo.com', '@peppermayo', None, False, None, ['dresses', 'sets'], 38, 7, False, 'Feminine fashion. Styling content.'),

    # ==================== US BEAUTY BRANDS ====================
    ('Glossier', 'https://www.glossier.com', 'Beauty', ['skincare', 'makeup'], ['US', 'UK', 'Canada'], 5000, ['instagram', 'tiktok'], 'pr@glossier.com', '@glossier', '@glossier', True, 'https://www.glossier.com/influencer', ['skincare', 'makeup'], 42, 7, True, 'Cult beauty brand. Minimal aesthetic.'),
    ('ColourPop', 'https://colourpop.com', 'Beauty', ['makeup'], ['US', 'Global'], 3000, ['instagram', 'tiktok', 'youtube'], 'pr@colourpop.com', '@colourpopcosmetics', '@colourpop', True, 'https://colourpop.com/pages/pr', ['eyeshadow', 'lipstick'], 65, 3, False, 'Very responsive. Affordable makeup.'),
    ('Fenty Beauty', 'https://fentybeauty.com', 'Beauty', ['makeup', 'skincare'], ['US', 'UK', 'Australia'], 10000, ['instagram', 'tiktok'], 'influencer@fentybeauty.com', '@fentybeauty', '@fentybeauty', False, None, ['foundation', 'highlighter'], 38, 10, True, 'Rihanna\'s brand. High-value PR.'),
    ('The Ordinary', 'https://theordinary.com', 'Beauty', ['skincare'], ['Global'], 5000, ['instagram', 'tiktok', 'youtube'], 'pr@theordinary.com', '@theordinary', '@theordinary', True, 'https://theordinary.com/en-us/influencer', ['serums', 'treatments'], 50, 5, False, 'Science-based skincare. Educational content.'),
    ('Rare Beauty', 'https://rarebeauty.com', 'Beauty', ['makeup'], ['US', 'Canada'], 8000, ['instagram', 'tiktok'], 'pr@rarebeauty.com', '@rarebeauty', '@rarebeauty', False, None, ['blush', 'lipstick'], 44, 8, True, 'Selena Gomez brand. Mental health focus.'),
    ('Drunk Elephant', 'https://www.drunkelephant.com', 'Beauty', ['skincare'], ['US', 'Global'], 10000, ['instagram', 'tiktok'], 'pr@drunkelephant.com', '@drunkelephant', '@drunkelephant', False, None, ['serums', 'moisturizers'], 40, 9, True, 'Clean skincare. Premium brand.'),
    ('Glow Recipe', 'https://www.glowrecipe.com', 'Beauty', ['skincare'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@glowrecipe.com', '@glowrecipe', '@glowrecipe', True, 'https://www.glowrecipe.com/pages/influencer', ['masks', 'serums'], 46, 6, False, 'Fruit-based skincare. K-beauty inspired.'),
    ('Tower 28', 'https://www.tower28beauty.com', 'Beauty', ['makeup'], ['US'], 5000, ['instagram', 'tiktok'], 'pr@tower28beauty.com', '@tower28beauty', '@tower28beauty', False, None, ['blush', 'lip gloss'], 48, 5, False, 'Clean makeup. Sensitive skin friendly.'),
    ('Kosas', 'https://kosas.com', 'Beauty', ['makeup', 'skincare'], ['US', 'Canada'], 7000, ['instagram', 'tiktok'], 'pr@kosas.com', '@kosas', '@kosas', False, None, ['foundation', 'lipstick'], 42, 7, True, 'Clean beauty. Natural makeup looks.'),
    ('Ilia Beauty', 'https://iliabeauty.com', 'Beauty', ['makeup', 'skincare'], ['US'], 10000, ['instagram'], 'pr@iliabeauty.com', '@iliabeauty', None, False, None, ['foundation', 'lipstick'], 38, 10, True, 'Clean beauty. Skin-first makeup.'),
    ('Tula Skincare', 'https://www.tula.com', 'Beauty', ['skincare'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@tula.com', '@tula', '@tula', True, 'https://www.tula.com/pages/ambassador', ['moisturizers', 'cleansers'], 44, 6, False, 'Probiotic skincare. Wellness creators.'),
    ('Tatcha', 'https://www.tatcha.com', 'Beauty', ['skincare'], ['US'], 15000, ['instagram'], 'pr@tatcha.com', '@tatcha', None, False, None, ['cleansers', 'moisturizers'], 35, 12, True, 'Japanese beauty rituals. Premium skincare.'),
    ('Youth To The People', 'https://www.youthtothepeople.com', 'Beauty', ['skincare'], ['US'], 10000, ['instagram', 'tiktok'], 'pr@youthtothepeople.com', '@youthtothepeople', '@yttp', False, None, ['cleansers', 'serums'], 40, 8, False, 'Vegan skincare. Superfood ingredients.'),
    ('Summer Fridays', 'https://www.summerfridays.com', 'Beauty', ['skincare'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@summerfridays.com', '@summerfridays', '@summerfridays', False, None, ['masks', 'lip balm'], 46, 6, False, 'Minimal skincare. Weekend vibes.'),
    ('Peach & Lily', 'https://www.peachandlily.com', 'Beauty', ['skincare'], ['US'], 7000, ['instagram'], 'pr@peachandlily.com', '@peachandlily', None, True, 'https://www.peachandlily.com/pages/influencer', ['serums', 'masks'], 42, 7, False, 'K-beauty curated. Glass skin focus.'),

    # ==================== US FASHION BRANDS ====================
    ('Gymshark', 'https://www.gymshark.com', 'Fashion', ['activewear', 'fitness'], ['Global'], 15000, ['instagram', 'tiktok', 'youtube'], 'influencer@gymshark.com', '@gymshark', '@gymshark', True, 'https://www.gymshark.com/pages/athletes', ['leggings', 'sports bras'], 30, 14, True, 'Fitness brand. Gym content.'),
    ('ASOS', 'https://www.asos.com', 'Fashion', ['fashion'], ['UK', 'US', 'Australia'], 10000, ['instagram', 'tiktok'], 'influencermarketing@asos.com', '@asos', '@asos', True, 'https://www.asos.com/discover/insiders/', ['dresses', 'tops'], 28, 12, False, 'Global fashion retailer.'),
    ('Revolve', 'https://www.revolve.com', 'Fashion', ['fashion', 'luxury'], ['US'], 20000, ['instagram'], 'influencer@revolve.com', '@revolve', None, True, 'https://www.revolve.com/r/Influencer/', ['dresses', 'designer'], 25, 15, True, 'High-end fashion. Major influencer program.'),
    ('PrettyLittleThing', 'https://www.prettylittlething.com', 'Fashion', ['fashion'], ['UK', 'US'], 12000, ['instagram', 'tiktok'], 'pr@prettylittlething.com', '@prettylittlething', '@plt', True, 'https://www.prettylittlething.com/plt-shape', ['dresses', 'bodysuits'], 35, 10, False, 'Fast fashion. Trendy styles.'),
    ('Nasty Gal', 'https://www.nastygal.com', 'Fashion', ['fashion', 'vintage'], ['US', 'UK'], 10000, ['instagram'], 'pr@nastygal.com', '@nastygal', None, False, None, ['dresses', 'vintage'], 32, 11, False, 'Edgy fashion. Vintage vibes.'),
    ('Meshki', 'https://www.meshki.us', 'Fashion', ['fashion'], ['US', 'Australia'], 15000, ['instagram', 'tiktok'], 'pr@meshki.us', '@meshki', '@meshki', False, None, ['dresses', 'bodysuits'], 38, 9, False, 'Bodycon fashion. Night-out looks.'),
    ('Oh Polly', 'https://www.ohpolly.com', 'Fashion', ['fashion'], ['UK', 'US'], 12000, ['instagram', 'tiktok'], 'pr@ohpolly.com', '@ohpolly', '@ohpolly', False, None, ['dresses', 'co-ords'], 36, 10, False, 'Party fashion. Going-out content.'),
    ('Lounge Underwear', 'https://www.loungeunderwear.com', 'Fashion', ['lingerie', 'loungewear'], ['UK', 'US'], 10000, ['instagram', 'tiktok'], 'pr@loungeunderwear.com', '@loungeunderwear', '@lounge', True, 'https://www.loungeunderwear.com/pages/ambassador', ['lingerie', 'loungewear'], 40, 8, False, 'Lingerie & loungewear. Cozy content.'),
    ('Alo Yoga', 'https://www.aloyoga.com', 'Fashion', ['activewear', 'yoga'], ['US'], 15000, ['instagram'], 'influencer@aloyoga.com', '@aloyoga', None, False, None, ['leggings', 'sports bras'], 32, 12, True, 'Luxury activewear. Yoga/wellness.'),
    ('Lululemon', 'https://www.lululemon.com', 'Fashion', ['activewear'], ['Global'], 20000, ['instagram'], 'community@lululemon.com', '@lululemon', None, True, 'https://www.lululemon.com/en-us/ambassadors', ['leggings', 'tops'], 25, 14, True, 'Premium activewear. Ambassador program.'),

    # ==================== GAMING/TECH BRANDS ====================
    ('Razer', 'https://www.razer.com', 'Tech', ['gaming', 'peripherals'], ['Global'], 5000, ['twitch', 'youtube', 'tiktok'], 'influencer@razer.com', '@razer', '@razer', True, 'https://www.razer.com/ambassadors', ['keyboards', 'mice'], 35, 10, False, 'Gaming peripherals. Streamer focused.'),
    ('HyperX', 'https://www.hyperx.com', 'Tech', ['gaming', 'audio'], ['Global'], 3000, ['twitch', 'youtube'], 'sponsorships@hyperx.com', '@hyperx', '@hyperx', False, None, ['headsets', 'keyboards'], 42, 7, False, 'Gaming headsets. Responsive to streamers.'),
    ('SteelSeries', 'https://steelseries.com', 'Tech', ['gaming', 'peripherals'], ['Global'], 5000, ['twitch', 'youtube'], 'partnerships@steelseries.com', '@steelseries', '@steelseries', True, 'https://steelseries.com/sponsorships', ['headsets', 'keyboards'], 38, 9, False, 'Pro gaming gear.'),
    ('Logitech G', 'https://www.logitechg.com', 'Tech', ['gaming'], ['Global'], 8000, ['twitch', 'youtube'], 'gaming-influencer@logitech.com', '@logitechg', '@logitechg', True, 'https://www.logitechg.com/en-us/sponsorship', ['keyboards', 'mice'], 33, 11, False, 'Gaming peripherals. Established brand.'),
    ('Corsair', 'https://www.corsair.com', 'Tech', ['gaming', 'PC'], ['Global'], 10000, ['twitch', 'youtube'], 'influencer@corsair.com', '@corsair', '@corsair', True, 'https://www.corsair.com/us/en/sponsorship', ['keyboards', 'headsets'], 30, 12, False, 'PC gaming. Streaming setups.'),
    ('Secretlab', 'https://secretlab.co', 'Tech', ['gaming', 'chairs'], ['Global'], 5000, ['twitch', 'youtube'], 'partnerships@secretlab.sg', '@secretlabchairs', '@secretlab', False, None, ['gaming chairs'], 40, 8, False, 'Gaming chairs. Streamer favorite.'),
    ('GFUEL', 'https://gfuel.com', 'Gaming', ['energy drinks', 'gaming'], ['US', 'Global'], 5000, ['twitch', 'youtube', 'tiktok'], 'sponsorships@gfuel.com', '@gfuelenergy', '@gfuel', True, 'https://gfuel.com/pages/sponsorship', ['energy drinks'], 45, 6, False, 'Gaming energy drinks. Very active.'),
    ('Elgato', 'https://www.elgato.com', 'Tech', ['streaming', 'content creation'], ['Global'], 3000, ['twitch', 'youtube'], 'influencer@elgato.com', '@elgato', '@elgato', False, None, ['stream decks', 'capture cards'], 38, 9, False, 'Streaming equipment. Content creators.'),
    ('Blue Microphones', 'https://www.bluemic.com', 'Tech', ['audio', 'streaming'], ['US'], 5000, ['twitch', 'youtube'], 'pr@bluemic.com', '@bluemicrophones', None, False, None, ['microphones'], 35, 10, False, 'Streaming microphones.'),

    # ==================== FOOD/BEVERAGE BRANDS ====================
    ('Liquid I.V.', 'https://www.liquid-iv.com', 'Food', ['hydration', 'wellness'], ['US'], 8000, ['instagram', 'tiktok'], 'partnerships@liquid-iv.com', '@liquidiv', '@liquidiv', True, 'https://www.liquid-iv.com/pages/ambassador', ['hydration packets'], 42, 7, False, 'Hydration brand. Wellness creators.'),
    ('OLIPOP', 'https://drinkolipop.com', 'Food', ['beverages', 'wellness'], ['US'], 10000, ['instagram', 'tiktok'], 'partnerships@drinkolipop.com', '@drinkolipop', '@drinkolipop', False, None, ['soda', 'prebiotic drinks'], 40, 8, False, 'Healthy soda. Food/wellness creators.'),
    ('Magic Spoon', 'https://magicspoon.com', 'Food', ['food', 'breakfast'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@magicspoon.com', '@magicspoon', '@magicspoon', False, None, ['cereal'], 38, 9, False, 'Keto cereal. Health/fitness creators.'),
    ('Chomps', 'https://chomps.com', 'Food', ['snacks', 'protein'], ['US'], 5000, ['instagram'], 'partnerships@chomps.com', '@chomps', None, False, None, ['meat sticks'], 35, 10, False, 'Protein snacks. Fitness creators.'),

    # ==================== HOME/LIFESTYLE BRANDS ====================
    ('Pela Case', 'https://pelacase.com', 'Lifestyle', ['sustainability', 'tech accessories'], ['US', 'Canada', 'Global'], 3000, ['instagram', 'tiktok'], 'influencer@pelacase.com', '@pela', '@pela', False, None, ['phone cases'], 55, 4, False, 'Compostable phone cases. Eco creators.'),
    ('PopSockets', 'https://www.popsockets.com', 'Lifestyle', ['tech accessories'], ['Global'], 5000, ['instagram', 'tiktok'], 'partnerships@popsockets.com', '@popsockets', '@popsockets', True, 'https://www.popsockets.com/pages/influencer', ['phone grips'], 40, 7, False, 'Phone accessories. Fun designs.'),
    ('Casetify', 'https://www.casetify.com', 'Lifestyle', ['tech accessories'], ['Global'], 8000, ['instagram', 'tiktok'], 'influencer@casetify.com', '@casetify', '@casetify', True, 'https://www.casetify.com/pages/co-lab', ['phone cases'], 38, 9, False, 'Custom phone cases. Design collabs.'),

    # ==================== HAIRCARE BRANDS ====================
    ('Olaplex', 'https://olaplex.com', 'Beauty', ['haircare'], ['Global'], 10000, ['instagram', 'tiktok'], 'pr@olaplex.com', '@olaplex', '@olaplex', False, None, ['hair treatments'], 40, 8, True, 'Professional haircare. Salon quality.'),
    ('K18', 'https://k18hair.com', 'Beauty', ['haircare'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@k18hair.com', '@k18hair', '@k18hair', False, None, ['hair masks'], 45, 6, False, 'Hair repair. Science-based.'),
    ('Briogeo', 'https://briogeohair.com', 'Beauty', ['haircare'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@briogeohair.com', '@briogeohair', '@briogeo', False, None, ['shampoo', 'conditioner'], 42, 7, False, 'Clean haircare. Curly hair friendly.'),
    ('Amika', 'https://www.amika.com', 'Beauty', ['haircare'], ['US'], 7000, ['instagram'], 'pr@amika.com', '@loveamika', None, False, None, ['styling products'], 38, 9, False, 'Fun haircare. Colorful packaging.'),
    ('Verb', 'https://verbproducts.com', 'Beauty', ['haircare'], ['US'], 5000, ['instagram'], 'pr@verbproducts.com', '@verbproducts', None, False, None, ['shampoo', 'styling'], 40, 8, False, 'Affordable haircare. Salon quality.'),

    # ==================== SUSTAINABLE/ECO BRANDS ====================
    ('Girlfriend Collective', 'https://girlfriend.com', 'Fashion', ['activewear', 'sustainability'], ['US'], 10000, ['instagram'], 'partnerships@girlfriend.com', '@girlfriend', None, False, None, ['leggings', 'sports bras'], 35, 10, False, 'Sustainable activewear. Eco creators.'),
    ('Reformation', 'https://www.thereformation.com', 'Fashion', ['fashion', 'sustainability'], ['US'], 20000, ['instagram'], 'influencer@thereformation.com', '@reformation', None, False, None, ['dresses', 'jeans'], 28, 14, True, 'Sustainable fashion. Premium brand.'),
    ('Patagonia', 'https://www.patagonia.com', 'Fashion', ['outdoor', 'sustainability'], ['Global'], 25000, ['instagram'], 'ambassador@patagonia.com', '@patagonia', None, True, 'https://www.patagonia.com/ambassadors/', ['jackets', 'outdoor gear'], 20, 20, True, 'Outdoor brand. Environmental focus.'),
    ('Allbirds', 'https://www.allbirds.com', 'Fashion', ['shoes', 'sustainability'], ['US'], 15000, ['instagram'], 'partnerships@allbirds.com', '@allbirds', None, False, None, ['sneakers'], 30, 12, True, 'Sustainable shoes. Comfort focus.'),

    # ==================== JEWELRY/ACCESSORIES ====================
    ('Mejuri', 'https://mejuri.com', 'Accessories', ['jewelry'], ['US', 'Canada'], 10000, ['instagram', 'tiktok'], 'partnerships@mejuri.com', '@mejuri', '@mejuri', False, None, ['jewelry', 'rings'], 38, 9, False, 'Fine jewelry. Everyday luxury.'),
    ('Missoma', 'https://www.missoma.com', 'Accessories', ['jewelry'], ['UK', 'US'], 12000, ['instagram'], 'pr@missoma.com', '@missoma', None, False, None, ['jewelry', 'necklaces'], 35, 10, False, 'Layering jewelry. Trendy pieces.'),
    ('Ana Luisa', 'https://www.analuisa.com', 'Accessories', ['jewelry'], ['US'], 8000, ['instagram', 'tiktok'], 'partnerships@analuisa.com', '@analuisa_ny', '@analuisa', False, None, ['jewelry'], 40, 8, False, 'Affordable jewelry. Sustainable.'),

    # ==================== WELLNESS/SUPPLEMENTS ====================
    ('Ritual', 'https://ritual.com', 'Wellness', ['supplements', 'vitamins'], ['US'], 10000, ['instagram'], 'partnerships@ritual.com', '@ritual', None, False, None, ['multivitamins'], 35, 10, False, 'Essential vitamins. Transparent ingredients.'),
    ('Sakara', 'https://www.sakara.com', 'Wellness', ['food', 'wellness'], ['US'], 15000, ['instagram'], 'partnerships@sakara.com', '@sakaralife', None, False, None, ['meal delivery'], 30, 12, True, 'Wellness meal delivery. Premium.'),
    ('Olly', 'https://olly.com', 'Wellness', ['supplements', 'vitamins'], ['US'], 8000, ['instagram', 'tiktok'], 'pr@olly.com', '@ollynutrition', '@olly', True, 'https://olly.com/pages/ambassador', ['gummies', 'supplements'], 42, 7, False, 'Vitamin gummies. Fun wellness.'),

    # ==================== SKINCARE DEVICES ====================
    ('NuFACE', 'https://www.mynuface.com', 'Beauty', ['skincare devices'], ['US'], 12000, ['instagram', 'tiktok'], 'pr@mynuface.com', '@mynuface', '@mynuface', False, None, ['microcurrent devices'], 38, 9, True, 'Facial toning. Beauty tech.'),
    ('Solawave', 'https://solawave.co', 'Beauty', ['skincare devices'], ['US'], 8000, ['instagram', 'tiktok'], 'partnerships@solawave.co', '@solawave', '@solawave', False, None, ['red light therapy'], 45, 6, False, 'Affordable beauty devices. Skincare tech.'),
    ('Foreo', 'https://www.foreo.com', 'Beauty', ['skincare devices'], ['Global'], 10000, ['instagram'], 'influencer@foreo.com', '@foreo', None, False, None, ['cleansing devices'], 35, 10, False, 'Facial cleansing. Beauty tech.'),
]

print(f'Seeding {len(brands)} brands into database...')

inserted = 0
skipped = 0

for brand_data in brands:
    try:
        cursor.execute('''
            INSERT INTO pr_brands (
                brand_name, website, category, niches, regions, min_followers, platforms,
                contact_email, instagram_handle, tiktok_handle, has_application_form,
                application_form_url, product_types, response_rate, avg_response_time_days,
                is_premium, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (brand_name) DO NOTHING
        ''', (
            brand_data[0],  # brand_name
            brand_data[1],  # website
            brand_data[2],  # category
            json.dumps(brand_data[3]),  # niches
            json.dumps(brand_data[4]),  # regions
            brand_data[5],  # min_followers
            json.dumps(brand_data[6]),  # platforms
            brand_data[7],  # contact_email
            brand_data[8],  # instagram_handle
            brand_data[9],  # tiktok_handle
            brand_data[10],  # has_application_form
            brand_data[11],  # application_form_url
            json.dumps(brand_data[12]),  # product_types
            brand_data[13],  # response_rate
            brand_data[14],  # avg_response_time_days
            brand_data[15],  # is_premium
            brand_data[16],  # notes
        ))

        if cursor.rowcount > 0:
            inserted += 1
            print(f'Added: {brand_data[0]}')
        else:
            skipped += 1
            print(f'Skipped (duplicate): {brand_data[0]}')

    except Exception as e:
        print(f'Error adding {brand_data[0]}: {str(e)}')

conn.commit()

# Get final stats
cursor.execute('SELECT COUNT(*) FROM pr_brands')
total = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM pr_brands WHERE is_premium = true')
premium = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM pr_brands WHERE has_application_form = true')
has_form = cursor.fetchone()[0]

cursor.execute('SELECT category, COUNT(*) as count FROM pr_brands GROUP BY category ORDER BY count DESC')
by_category = cursor.fetchall()

print(f'\n========== SEEDING COMPLETE ==========')
print(f'Inserted: {inserted} brands')
print(f'Skipped: {skipped} brands')
print(f'Total in database: {total} brands')
print(f'Premium brands: {premium}')
print(f'Brands with application forms: {has_form}')
print(f'\nBrands by category:')
for cat in by_category:
    print(f'  {cat[0]}: {cat[1]} brands')

cursor.close()
conn.close()

print('\nReady to build the frontend!')
