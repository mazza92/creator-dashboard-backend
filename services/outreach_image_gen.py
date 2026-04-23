"""
outreach_image_gen.py — AI UGC image generation for brand outreach emails.

Generates realistic "creator + product in hand" lifestyle images via OpenAI
Images API (DALL-E 3). Results are cached in Postgres so each brand/vertical
combo is generated ONCE per 30 days (zero cost on cache hit).

No extra Python deps — uses only `requests` (already in requirements.txt).
Add OPENAI_API_KEY to .env to enable. Without it the service returns None
and the email gracefully skips the AI hero (falls back to placeholder text).
"""

import hashlib
import logging
import os

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET_OUTREACH = os.getenv("SUPABASE_BUCKET_OUTREACH", "outreach-ugc")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Bump to force-regenerate all cached images on next run
TEMPLATE_VERSION = "v1"


# ═══════════════════════════════════════════════════════════════════════════
# VERTICAL STYLE MAP
# One entry per category. Drives every generated image.
# ═══════════════════════════════════════════════════════════════════════════

VERTICAL_STYLE_MAP = {
    "beauty": {
        "pose": "smiling creator holding cream jar or lipstick near cheek, looking directly at camera",
        "environment": "bright bathroom or vanity mirror background, warm natural light",
        "product_interaction": "product clearly visible in hand, label facing camera",
        "model_type": "young woman, natural makeup, glowing skin",
        "caption_tone": "routine reveal",
        "negative_extras": "no studio white background, no flat lay, no product floating",
    },
    "skincare": {
        "pose": "creator applying or holding skincare product near face, visible healthy skin texture",
        "environment": "clean bathroom shelf, natural daylight or soft ring light",
        "product_interaction": "serum or moisturiser bottle held up, fingertip touching product",
        "model_type": "diverse person, dewy skin, minimal makeup",
        "caption_tone": "skin transformation / routine",
        "negative_extras": "no clinical white background, no 3D render, no blank skin",
    },
    "haircare": {
        "pose": "creator running fingers through hair, product in other hand near shoulder",
        "environment": "bedroom or bathroom mirror, diffused natural light",
        "product_interaction": "shampoo or hair mask bottle visible, product near hair",
        "model_type": "person with expressive healthy hair, relaxed candid expression",
        "caption_tone": "hair transformation / wash day",
        "negative_extras": "no studio backdrop, no mannequin head, no wig",
    },
    "makeup": {
        "pose": "creator mid-application with makeup product near eye or lips, small smile",
        "environment": "ring-lit vanity, warm home setting",
        "product_interaction": "eyeshadow palette or mascara wand clearly visible in hand",
        "model_type": "bold expressive makeup look, playful expression",
        "caption_tone": "GRWM / look reveal",
        "negative_extras": "no catalog white background, no invisible product",
    },
    "fitness": {
        "pose": "creator post-workout holding supplement shaker or fitness product, energised expression",
        "environment": "home gym or gym floor, natural sport lighting",
        "product_interaction": "product held up to camera, slight post-workout authenticity",
        "model_type": "athletic person in workout clothes, motivated expression",
        "caption_tone": "pre/post workout / performance review",
        "negative_extras": "no glossy commercial look, no studio, no CGI muscles",
    },
    "activewear": {
        "pose": "creator wearing the activewear, mirror selfie or outdoor stretch shot",
        "environment": "gym mirror, park, or clean outdoor background",
        "product_interaction": "outfit featured prominently, full-body or mid-stretch highlighting fit",
        "model_type": "athletic person, confident posture, natural expression",
        "caption_tone": "OOTD / fit reveal / workout outfit",
        "negative_extras": "no mannequin, no catalog flat lay",
    },
    "fashion": {
        "pose": "creator wearing featured fashion item, full-body mirror selfie or street style shot",
        "environment": "apartment hallway mirror, café terrace, or clean urban street",
        "product_interaction": "clothing or accessory featured prominently, natural styling context",
        "model_type": "stylish person, relaxed cool attitude, natural pose",
        "caption_tone": "OOTD / styling tip / haul",
        "negative_extras": "no catalog white background, no stiff fashion model pose",
    },
    "food": {
        "pose": "creator at kitchen table holding food/drink product, genuine smile and excitement",
        "environment": "cozy kitchen counter or café table, warm morning light",
        "product_interaction": "food or drink in hand, steam or texture visible for realism",
        "model_type": "approachable friendly person, casual outfit, excited about food",
        "caption_tone": "taste review / recipe hack / morning ritual",
        "negative_extras": "no sterile food studio, no floating product, no unappetising extreme close-up",
    },
    "wellness": {
        "pose": "creator in calm home setting holding wellness product, peaceful expression",
        "environment": "bright living room or yoga mat area, morning natural light",
        "product_interaction": "product clearly in hand or being consumed, warm atmosphere",
        "model_type": "calm person, minimal makeup, relaxed expression",
        "caption_tone": "morning ritual / self-care routine",
        "negative_extras": "no fake medical imagery, no dramatic before/after claim",
    },
    "supplements": {
        "pose": "creator post-workout or morning routine holding supplement bottle or shaker",
        "environment": "gym bag area, kitchen counter, or outdoor morning bench",
        "product_interaction": "supplement bottle held up, label clearly visible",
        "model_type": "healthy athletic person, energetic post-workout look",
        "caption_tone": "daily stack / routine results",
        "negative_extras": "no medical claims in scene, no clinical lab setting",
    },
    "pet": {
        "pose": "pet owner and pet both in frame, owner holding pet product near happy pet",
        "environment": "living room floor, backyard grass, or park bench",
        "product_interaction": "pet engaging with product (sniffing treat, eyeing toy)",
        "model_type": "happy pet owner, warm candid expression, pet as co-star",
        "caption_tone": "my dog/cat approved / favourite pet find",
        "negative_extras": "no isolated product, no veterinary clinical look, no stock poses",
    },
    "home": {
        "pose": "creator in home environment holding or using home product, satisfied expression",
        "environment": "living room, kitchen, or bedroom with product in real decor context",
        "product_interaction": "creator using product in natural home setting",
        "model_type": "relatable homeowner, casual comfortable outfit, genuine expression",
        "caption_tone": "home find / organisation tip / before-after",
        "negative_extras": "no staged catalogue interior, no isolated product packshot",
    },
    "tech": {
        "pose": "creator at desk or sofa actively using tech product, engaged focused expression",
        "environment": "home office desk, warm lamp light, natural background clutter",
        "product_interaction": "hands on device, screen glow, product clearly in use",
        "model_type": "young professional or student, focused and excited about product",
        "caption_tone": "desk setup / productivity hack / honest review",
        "negative_extras": "no clean-room render, no promotional hologram, no floating UI",
    },
    "gaming": {
        "pose": "creator at gaming setup holding controller or peripheral, hyped expression",
        "environment": "gaming desk with RGB lighting, monitor glow in background",
        "product_interaction": "gaming product prominently featured, hands actively using it",
        "model_type": "gamer with expressive reaction, casual hoodie",
        "caption_tone": "setup tour / reaction / game changer",
        "negative_extras": "no fake esports arena, no CGI render",
    },
    "travel": {
        "pose": "creator at travel location holding travel product (bag, bottle, organiser)",
        "environment": "airport terminal, hotel room, or outdoor scenic destination",
        "product_interaction": "travel product integrated in a real travel moment",
        "model_type": "adventurous traveller, candid expression, real travel context",
        "caption_tone": "travel essential / packing hack / must-have",
        "negative_extras": "no tourist-trap clichés, no stock travel photo pose",
    },
    "luxury": {
        "pose": "creator holding premium product near face, elegant but candid expression",
        "environment": "upscale home interior, neutral linen tones, soft indirect light",
        "product_interaction": "product displayed with care, detail visible, premium feel",
        "model_type": "refined poised person, minimal clean aesthetic, soft natural makeup",
        "caption_tone": "worthy investment / favourite splurge / unboxing",
        "negative_extras": "no cheap-looking background, no neon lights, no fast-fashion styling",
    },
    "sustainable": {
        "pose": "creator outdoors or bright natural home setting holding eco product",
        "environment": "outdoor garden, farmers market, or bright kitchen with plants",
        "product_interaction": "sustainable product held with care, eco-friendly packaging visible",
        "model_type": "eco-conscious person, natural look, earth-toned outfit",
        "caption_tone": "sustainable swap / zero-waste / planet-friendly find",
        "negative_extras": "no greenwashing visuals, no generic nature stock photo",
    },
    "baby": {
        "pose": "parent with baby or toddler, holding baby product, warm approachable smile",
        "environment": "nursery, playroom, or home kitchen, bright child-friendly space",
        "product_interaction": "baby product featured with child in safe happy scene",
        "model_type": "happy parent, gentle caring expression, casual home clothes",
        "caption_tone": "mum/dad review / must-have for parents / toddler approved",
        "negative_extras": "no unsafe child scenarios, no clinical medical setting",
    },
    "jewelry": {
        "pose": "creator wearing jewellery, showing wrist or neck detail, confident warm expression",
        "environment": "soft bedroom light, café window, or clean neutral wall background",
        "product_interaction": "jewellery clearly visible on body, natural styling context",
        "model_type": "stylish person, clean aesthetic, jewellery as hero accessory",
        "caption_tone": "accessories haul / daily wear / gift idea",
        "negative_extras": "no plain white product photo, no jewellery floating alone",
    },
    "lifestyle": {
        "pose": "creator in relaxed home setting holding brand product, genuine smile",
        "environment": "living room or kitchen, warm natural light",
        "product_interaction": "product clearly in hand, label visible",
        "model_type": "relatable person, casual outfit, authentic expression",
        "caption_tone": "honest review / daily find",
        "negative_extras": "no catalog white background, no placeholder imagery",
    },
    "other": {
        "pose": "creator casually holding brand product near face, genuine smile",
        "environment": "bright home interior, natural daylight",
        "product_interaction": "product visible in hand, label clearly facing camera",
        "model_type": "friendly diverse person, casual look, authentic expression",
        "caption_tone": "daily essential / quick honest review",
        "negative_extras": "no catalog white background, no placeholder, no wireframe UI",
    },
}


def _get_vertical_style(vertical: str) -> dict:
    v = (vertical or "other").lower().strip()
    return VERTICAL_STYLE_MAP.get(v) or VERTICAL_STYLE_MAP.get("lifestyle") or VERTICAL_STYLE_MAP["other"]


def _build_prompt(brand_name: str, vertical: str, niches: list = None,
                  description: str = "") -> str:
    """
    Build a DALL-E 3 prompt for a realistic UGC lifestyle photo.
    Intent: creator face visible + product in hand + authentic phone-camera vibe.
    """
    s = _get_vertical_style(vertical)

    niche_hint = ""
    if niches:
        safe = [n for n in niches if isinstance(n, str)][:2]
        if safe:
            niche_hint = f" Product niche: {', '.join(safe)}."

    brand_hint = ""
    if brand_name and brand_name.strip().lower() not in ("brand", "product", "item", ""):
        brand_hint = (
            f" The product belongs to the brand '{brand_name}' — "
            f"preserve recognisable packaging details and label."
        )

    prompt = (
        f"Authentic UGC lifestyle Instagram photo. "
        f"{s['model_type']}. "
        f"{s['pose']}. "
        f"{s['environment']}. "
        f"{s['product_interaction']}.{brand_hint}{niche_hint} "
        f"Shot on smartphone, slightly imperfect natural framing, real skin texture, "
        f"authentic candid energy, warm colour grading. "
        f"4:5 portrait ratio. "
        f"Absolutely NOT: {s['negative_extras']}, no text overlays, "
        f"no watermarks, no social media UI frames, no wireframe app mockups."
    )
    return prompt


def _make_cache_key(brand_id: int, vertical: str, brand_name: str) -> str:
    raw = f"{brand_id}:{vertical}:{TEMPLATE_VERSION}:{brand_name.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


# ── DB helpers ──────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _ensure_schema():
    ddl = """
        CREATE TABLE IF NOT EXISTS outreach_ugc_image_cache (
            id           SERIAL PRIMARY KEY,
            cache_key    TEXT UNIQUE NOT NULL,
            brand_id     INTEGER,
            vertical     TEXT,
            prompt       TEXT,
            image_url    TEXT NOT NULL,
            provider     TEXT DEFAULT 'openai_dalle3',
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 days'
        );
        CREATE INDEX IF NOT EXISTS idx_ugc_cache_key ON outreach_ugc_image_cache(cache_key);

        CREATE TABLE IF NOT EXISTS outreach_showcase_creators (
            id             SERIAL PRIMARY KEY,
            vertical       TEXT NOT NULL,
            sort_order     INTEGER DEFAULT 0,
            display_name   TEXT NOT NULL,
            handle         TEXT NOT NULL,
            one_liner      TEXT,
            content_style  TEXT,
            follower_range TEXT DEFAULT '10K-50K',
            active         BOOLEAN DEFAULT true,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_showcase_vertical
            ON outreach_showcase_creators(vertical, active, sort_order);
    """
    try:
        conn = _db()
        conn.cursor().execute(ddl)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[outreach_image_gen] schema init: {e}")


def _seed_default_creators():
    """Seed default curated showcase creators once. Edit via DB anytime."""
    rows = [
        # (vertical, sort_order, display_name, handle, one_liner, content_style, follower_range)
        ("beauty",      1, "Sarah K.",          "@sarahkskin",       "Found my HG moisturizer ✨",                 "Skincare & Makeup Tutorials",   "20K-80K"),
        ("beauty",      2, "Mia Laval",          "@mialaval",          "Honest beauty finds for real skin",          "Get Ready With Me",             "15K-50K"),
        ("beauty",      3, "Jasmine T.",         "@jasminetglow",      "Clean beauty obsessed 💚",                    "Product Reviews",               "8K-30K"),
        ("skincare",    1, "Lena Park",          "@lenaparkskin",      "Skincare nerd. Real results only.",          "Routine Videos",                "25K-100K"),
        ("skincare",    2, "Chloe V.",           "@chloevbeauty",      "Minimalist skincare routines",               "Before & After",                "10K-40K"),
        ("skincare",    3, "Anya M.",            "@anyamskin",         "Ingredient-focused reviews",                 "Skincare Deep Dives",           "5K-20K"),
        ("haircare",    1, "Zoe H.",             "@zoehairlove",       "Curly hair routines & finds",                "Wash Day Content",              "30K-120K"),
        ("haircare",    2, "Priya S.",           "@priyas_curls",      "Embracing my natural texture 🌿",             "Product Reviews",               "15K-60K"),
        ("haircare",    3, "Nina B.",            "@ninabhair",         "Hair care from roots to ends",               "Styling Tutorials",             "10K-35K"),
        ("makeup",      1, "Elise D.",           "@elisedmakeup",      "Bold looks on a budget 💄",                   "GRWM & Tutorials",              "25K-90K"),
        ("makeup",      2, "Tariq A.",           "@tariqamua",         "Inclusive beauty for all skin tones",        "Technique Videos",              "15K-60K"),
        ("makeup",      3, "Nora F.",            "@norafmakeup",       "Clean makeup, real reviews",                 "Product Comparisons",           "8K-30K"),
        ("fitness",     1, "Jake F.",            "@jakefitness",       "5AM club. Real workouts, real results.",     "Workout Vlogs",                 "40K-150K"),
        ("fitness",     2, "Tara W.",            "@taraworkout",       "Strength training for beginners 💪",          "Exercise Tutorials",            "20K-80K"),
        ("fitness",     3, "Marcus L.",          "@marcuslfit",        "Gym life + honest nutrition tips",           "Daily Fitness Vlogs",           "15K-50K"),
        ("activewear",  1, "Faye O.",            "@fayeoactive",       "Activewear that moves with you",             "Try-On Hauls",                  "25K-90K"),
        ("activewear",  2, "Diego M.",           "@diegomfit",         "Training gear, no fluff reviews",            "Workout Outfits",               "15K-60K"),
        ("activewear",  3, "Sasha V.",           "@sashavsweat",       "Activewear for all body types",              "Fit & Feel Reviews",            "10K-35K"),
        ("fashion",     1, "Léa M.",             "@leamstyle",         "Effortless everyday styling 🌸",              "OOTD & Hauls",                  "35K-120K"),
        ("fashion",     2, "Olivia N.",          "@olivianfashion",    "Thrift & trendy, always affordable",         "Fashion Hauls",                 "20K-70K"),
        ("fashion",     3, "Remy G.",            "@remygstyle",        "Minimalist wardrobe, maximal style",         "Capsule Wardrobe",              "10K-40K"),
        ("food",        1, "Clara T.",           "@claratcooks",       "Simple recipes, real ingredients",           "Recipe Videos",                 "30K-100K"),
        ("food",        2, "Dom P.",             "@dompfood",          "Street food & home cooking mashups",         "Food Reviews",                  "20K-80K"),
        ("food",        3, "Yuki H.",            "@yukihfoodlife",     "Mindful eating made delicious",              "Healthy Recipes",               "10K-45K"),
        ("wellness",    1, "Emma S.",            "@emmaswellness",     "Slow mornings & evidence-based wellness",    "Morning Routine Vlogs",         "25K-90K"),
        ("wellness",    2, "Kai R.",             "@kairwellbeing",     "Mental health & daily rituals",              "Wellness Vlogs",                "15K-55K"),
        ("wellness",    3, "Nadia B.",           "@nadiabalance",      "Holistic wellness on a budget",              "Self-Care Reviews",             "8K-30K"),
        ("supplements", 1, "Chris M.",           "@chrismsupps",       "What actually works in my stack",            "Stack Reviews",                 "20K-80K"),
        ("supplements", 2, "Lena R.",            "@lenarnutrition",    "Nutrition + supplementation simplified",     "Daily Nutrition",               "15K-55K"),
        ("supplements", 3, "Kai W.",             "@kaiwfitfuel",       "Pre-workout to recovery, reviewed",          "Supplement Vlogs",              "8K-30K"),
        ("pet",         1, "Sophie & Biscuit",   "@biscuitthelab",     "Life with a very spoiled Labrador 🐶",        "Pet Daily Vlogs",               "40K-200K"),
        ("pet",         2, "Tom & Mochi",        "@mochicat_life",     "Cat content that actually converts 🐱",       "Cat Lifestyle",                 "25K-100K"),
        ("pet",         3, "Ren L.",             "@renandpups",        "Dog gear & training honest reviews",         "Pet Product Reviews",           "10K-50K"),
        ("home",        1, "Alice D.",           "@alicedhome",        "Small flat, big style transformations",      "Home Decor Vlogs",              "30K-120K"),
        ("home",        2, "Ben C.",             "@bencorganise",      "Satisfying organisation & home finds",       "Before & After",                "20K-80K"),
        ("home",        3, "Mei Y.",             "@meiyinteriors",     "Budget interior refresh ideas",              "Home Tours",                    "10K-40K"),
        ("tech",        1, "Alex P.",            "@alexptech",         "Honest tech reviews, no fluff",              "Unboxing & Reviews",            "50K-200K"),
        ("tech",        2, "Sam T.",             "@samtechdesk",       "Desk setup & productivity tools",            "Setup Tours",                   "30K-100K"),
        ("tech",        3, "River N.",           "@riverntechlife",    "Tech for everyday life",                     "Product Deep Dives",            "10K-40K"),
        ("gaming",      1, "Kyle R.",            "@kylerplays",        "Honest gaming gear reviews",                 "Setup Tours & Reviews",         "40K-180K"),
        ("gaming",      2, "Zara G.",            "@zaragaminglife",    "Casual gamer, real recommendations",         "Gaming Lifestyle",              "20K-80K"),
        ("gaming",      3, "Nova M.",            "@novamgamer",        "Budget gaming setups that slap",             "Budget Build Guides",           "10K-40K"),
        ("travel",      1, "Leo V.",             "@leovtravels",       "Carry-on only traveller, real packing tips", "Travel Vlogs",                  "35K-150K"),
        ("travel",      2, "Hana S.",            "@hanastravellog",    "Solo travel essentials reviewed",            "Travel Reviews",                "20K-80K"),
        ("travel",      3, "Marco B.",           "@marcobonroute",     "Budget travel, premium finds",               "Travel Hauls",                  "10K-45K"),
        ("luxury",      1, "Camille F.",         "@camillefparis",     "Investment pieces worth every penny 💎",      "Luxury Unboxing",               "30K-150K"),
        ("luxury",      2, "Antoine R.",         "@antoinerlux",       "Timeless luxury, honest perspective",        "Brand Reviews",                 "15K-60K"),
        ("luxury",      3, "Vivienne L.",        "@viviennelstyle",    "Splurge-worthy finds curated weekly",        "Luxury Hauls",                  "10K-40K"),
        ("sustainable", 1, "Jess T.",            "@jesstecolife",      "Zero-waste swaps that actually work 🌱",      "Eco Swaps",                     "25K-90K"),
        ("sustainable", 2, "Finn E.",            "@finnecosub",        "Sustainable brands worth your money",        "Brand Reviews",                 "10K-40K"),
        ("sustainable", 3, "Mia G.",             "@miagsustain",       "Green beauty & home finds",                  "Eco Hauls",                     "8K-25K"),
        ("baby",        1, "Laura M.",           "@laurambabylife",    "Mum of 2, product tester extraordinaire",    "Baby Product Reviews",          "20K-80K"),
        ("baby",        2, "James & Lily",       "@jamesandlily",      "Dad-approved baby finds 👶",                  "Family Vlogs",                  "15K-60K"),
        ("baby",        3, "Emi P.",             "@emipparenthood",    "Honest parenting + product picks",           "Parenting Reviews",             "8K-30K"),
        ("jewelry",     1, "Ines C.",            "@inescjewels",       "Dainty everyday jewellery obsession",        "Jewellery Hauls",               "20K-75K"),
        ("jewelry",     2, "Nour S.",            "@noursaccessories",  "How to layer and style jewellery",           "Styling Guides",                "10K-40K"),
        ("jewelry",     3, "Ellie P.",           "@elliepjewelry",     "Gift guide & affordable finds",              "Gift Reviews",                  "8K-25K"),
        ("lifestyle",   1, "Anna W.",            "@annawlifestyle",    "Sharing what I actually love 💛",             "Day in My Life",                "20K-80K"),
        ("lifestyle",   2, "Jack B.",            "@jackbfinds",        "Real reviews from a real person",            "Product Reviews",               "10K-45K"),
        ("lifestyle",   3, "Yuki M.",            "@yukimfinds",        "Affordable favourites, weekly drops",        "Hauls & Reviews",               "8K-30K"),
        ("other",       1, "Anna W.",            "@annawlifestyle",    "Sharing what I actually love 💛",             "Day in My Life",                "20K-80K"),
        ("other",       2, "Jack B.",            "@jackbfinds",        "Real reviews from a real person",            "Product Reviews",               "10K-45K"),
        ("other",       3, "Yuki M.",            "@yukimfinds",        "Affordable favourites, weekly drops",        "Hauls & Reviews",               "8K-30K"),
    ]
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM outreach_showcase_creators")
        existing = cur.fetchone()
        if existing and existing["cnt"] > 0:
            conn.close()
            return
        for row in rows:
            cur.execute(
                """INSERT INTO outreach_showcase_creators
                   (vertical, sort_order, display_name, handle, one_liner, content_style, follower_range)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                row,
            )
        conn.commit()
        conn.close()
        logger.info("[outreach_image_gen] Seeded default showcase creators")
    except Exception as e:
        logger.warning(f"[outreach_image_gen] seed_default_creators: {e}")


# ── Generation + storage ────────────────────────────────────────────────────

def _generate_image_openai(prompt: str) -> bytes | None:
    """Call OpenAI DALL-E 3. Returns raw PNG bytes or None on failure."""
    if not OPENAI_API_KEY:
        logger.warning("[outreach_image_gen] OPENAI_API_KEY not set — skipping generation")
        return None
    try:
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "standard",   # use "hd" only after validating ROI
                "response_format": "url",
            },
            timeout=90,
        )
        resp.raise_for_status()
        temp_url = resp.json()["data"][0]["url"]
        img = requests.get(temp_url, timeout=30)
        img.raise_for_status()
        return img.content
    except Exception as e:
        logger.error(f"[outreach_image_gen] OpenAI error: {e}")
        return None


def _upload_to_supabase(image_bytes: bytes, filename: str) -> str | None:
    """Upload PNG bytes to Supabase and return stable public URL."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        bucket = SUPABASE_BUCKET_OUTREACH
        # Create bucket (idempotent — ignored if already exists)
        requests.post(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}",
                     "Content-Type": "application/json"},
            json={"id": bucket, "name": bucket, "public": True},
            timeout=10,
        )
        up = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/{bucket}/{filename}",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}",
                     "Content-Type": "image/png",
                     "x-upsert": "true"},
            data=image_bytes,
            timeout=30,
        )
        up.raise_for_status()
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{filename}"
    except Exception as e:
        logger.error(f"[outreach_image_gen] Supabase upload error: {e}")
        return None


def _lookup_cache(cache_key: str) -> str | None:
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute(
            "SELECT image_url FROM outreach_ugc_image_cache "
            "WHERE cache_key=%s AND expires_at > NOW() LIMIT 1",
            (cache_key,),
        )
        row = cur.fetchone()
        conn.close()
        return row["image_url"] if row else None
    except Exception:
        return None


def _save_cache(cache_key: str, brand_id: int, vertical: str, prompt: str, image_url: str):
    try:
        conn = _db()
        conn.cursor().execute(
            """INSERT INTO outreach_ugc_image_cache
               (cache_key, brand_id, vertical, prompt, image_url)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (cache_key) DO UPDATE
               SET image_url=EXCLUDED.image_url,
                   expires_at=NOW() + INTERVAL '30 days'""",
            (cache_key, brand_id, vertical, prompt, image_url),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[outreach_image_gen] cache save: {e}")


# ── Public API ──────────────────────────────────────────────────────────────

def get_showcase_creators(vertical: str, limit: int = 3) -> list:
    """Return curated creator list for this vertical, falling back to 'lifestyle'."""
    v = (vertical or "other").lower().strip()
    try:
        conn = _db()
        cur = conn.cursor()
        cur.execute(
            """SELECT display_name, handle, one_liner, content_style, follower_range
               FROM outreach_showcase_creators
               WHERE vertical=%s AND active=true
               ORDER BY sort_order ASC LIMIT %s""",
            (v, limit),
        )
        rows = cur.fetchall()
        if not rows:
            cur.execute(
                """SELECT display_name, handle, one_liner, content_style, follower_range
                   FROM outreach_showcase_creators
                   WHERE vertical='lifestyle' AND active=true
                   ORDER BY sort_order ASC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[outreach_image_gen] get_showcase_creators: {e}")
        return []


def generate_ugc_image(brand_id: int, brand_name: str, vertical: str,
                       niches: list = None, description: str = "",
                       force: bool = False) -> str | None:
    """
    Return a stable HTTPS URL for the UGC hero image for this brand.
    Calls OpenAI ONCE per brand/vertical combo (30-day cache).

    Returns:
        Public image URL or None if API key missing / generation failed.
    """
    _ensure_schema()
    cache_key = _make_cache_key(brand_id, vertical, brand_name)

    if not force:
        cached = _lookup_cache(cache_key)
        if cached:
            logger.info(f"[outreach_image_gen] cache HIT brand={brand_id} ({vertical})")
            return cached

    prompt = _build_prompt(brand_name, vertical, niches, description)
    logger.info(f"[outreach_image_gen] generating brand={brand_id} ({brand_name}, {vertical})")

    image_bytes = _generate_image_openai(prompt)
    if not image_bytes:
        return None

    filename = f"ugc-{cache_key}.png"
    url = _upload_to_supabase(image_bytes, filename)
    if not url:
        return None

    _save_cache(cache_key, brand_id, vertical, prompt, url)
    logger.info(f"[outreach_image_gen] stored → {url}")
    return url


def init():
    """Call once at app startup."""
    _ensure_schema()
    _seed_default_creators()
