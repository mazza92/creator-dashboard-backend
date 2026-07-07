current pitch below, it does not work on all brands, we need to refine it, give me brief:

Hi,

Your Self-tanning foams and gradual tan products keeps coming up in my comments. My followers want to see how I'd use it.

I create beauty content on TikTok (27.5K followers, 9.3100% engagement, beauty enthusiasts who trust creator recommendations over ads).

I'd show how it fits into a real getting-ready moment.

Would you be open to sending a sample?

https://newcollab.co/kit/socialcontentking

SOcial

exAmple:
Hi,

Your Eva II Vibrator keeps coming up in my comments. My followers want to see how I'd use it.

I create tech & gadgets content on TikTok (27.5K followers, 9.3100% engagement, tech & gadgets enthusiasts aged 20-35).

I'd show how the bra fits into a real moment, not a staged demo.

Would you be open to sending a sample?

https://newcollab.co/kit/socialcontentking

SOcial


These two pitches expose several distinct bugs — some in the prompt, some in the code. Let me map them all before writing the brief.

**Problems I see:**

**Pitch 1 (self-tanning):**
- "Self-tanning foams and gradual tan products" — this is the `brand.category` field being used as fallback for a missing `hero_product`. It's not a product name, it's a category dump.
- "keeps coming up in my comments. My followers want to see how I'd use it." — two-sentence Line 1, both generic. The prompt is not enforcing a specific observation.
- "I'd show how it fits into a real getting-ready moment" — no product named, vague scene.
- "a sample" — ask doesn't name the product.
- `9.3100%` — code formatting bug.
- `SOcial` — sign-off is the username.

**Pitch 2 (Eva II Vibrator):**
- "how the **bra** fits" — AI hallucinated a different product entirely. The prompt isn't forcing it to stay anchored to the hero product passed in.
- Wrong niche match — "tech & gadgets" creator pitching a vibrator.
- Same `9.3100%`, same `SOcial`.

---

Here's the brief:

---

## Pitch Generator Bug Fix Brief


### Bug 1 — Sign-off is the username (`SOcial`, `mer`)

**Root cause:** `get_creator_first_name()` not yet implemented. The prompt is receiving the creator's handle or display name directly.

**Fix — add this helper to your utility file:**
```python
def get_creator_first_name(creator):
    if creator.first_name:
        return creator.first_name
    # Single-word display_name is almost always a handle — skip it
    if creator.display_name and ' ' in creator.display_name:
        return creator.display_name.split()[0].capitalize()
    return ""
```

**Fix — update the sign-off line in your prompt:**
```python
# Find:
sign off with creator's real first name only

# The prompt variable must use:
- First name: {get_creator_first_name(creator) or "[name]"}
```

If `get_creator_first_name()` returns `""`, the pitch omits the sign-off entirely rather than inserting a username.

---

### Bug 2 — Hero product fallback dumps the category string

**Root cause:** `brand.hero_product or brand.category` — when `hero_product` is null, `brand.category` is a long descriptor like "Self-tanning foams and gradual tan products", not a usable product name.

**Fix — add a smarter fallback:**
```python
def get_hero_product(brand):
    if brand.hero_product:
        return brand.hero_product
    # category is a type descriptor, not a name — use the brand name instead
    return f"{brand.name} products"
```

**Replace all occurrences of** `brand.hero_product or brand.category` **with** `get_hero_product(brand)` in the prompt.

This means Line 1 becomes "Your St. Tropez products" rather than "Your Self-tanning foams and gradual tan products", which at least reads naturally until the brand gets enriched.

**Longer fix:** Run the enrichment script on any brand that still has no `hero_product` before they appear on the For You feed. Brands without a hero product should be deprioritised in the matching queue.

---

### Bug 3 — AI hallucinates a different product in Line 3 ("the bra")

**Root cause:** The model is drifting from the hero product mid-generation. The current prompt doesn't explicitly anchor Line 3 to the exact product name from the BRAND section.

**Add to the LINE 3 instructions in the prompt:**
```
LINE 3 — Content idea:
One sentence. You MUST name {get_hero_product(brand)} in this sentence.
Do not reference any product other than {get_hero_product(brand)}.
If you cannot think of a specific scene for this product, describe the moment
someone first opens the packaging and uses it for the first time.
State the format ({creator.primary_format or 'short-form video'}).
```

**Add to HARD RULES:**
```
- Never reference any product in Line 3 other than the hero product named in the BRAND section above
- If you are uncertain what the product does, describe the unboxing or first-use moment
```

---

### Bug 4 — Line 1 is two generic sentences

**Root cause:** "keeps coming up in my comments. My followers want to see how I'd use it." — the prompt isn't banning this pattern, and it currently appears in the `Good` examples (similar phrasing).

**Update LINE 1 instructions — replace the Good/Bad examples with:**
```
LINE 1 — Brand hook:
One sentence. Specific observation about {get_hero_product(brand)} by name.
The sentence must be about the product — not about your audience's reaction to it.

Good: "Your No.04 Bois de Balincourt candle is the one my followers keep screenshotting from my shelf content."
Good: "The [hero product] formula is unlike anything I've tried — my comments go to it every time I mention clean alternatives."
Bad: "Your [product] keeps coming up in my comments." (weak — says nothing about the product itself)
Bad: "My followers want to see how I'd use it." (vague — says nothing about why the product is interesting)
Bad: "I've been eyeing your products."
Bad: "I came across your brand."

Never: two sentences in Line 1.
Never: make Line 1 about your followers' reaction before saying anything specific about the product.
```

---

### Bug 5 — Niche mismatch (Eva II Vibrator shown to tech & gadgets creator)

**Root cause:** Upstream brand categorisation. The brand is filed under a category that maps to tech & gadgets in `NICHE_BRAND_CATEGORY_MAP`.

**Fix — in your brand categorisation or niche map:**
```python
# Add a wellness/adult category that does NOT map to tech & gadgets
# In NICHE_BRAND_CATEGORY_MAP, adult/intimacy brands should only surface to:
# - beauty, wellness, lifestyle niches
# Never to: tech, food, fitness, parenting

NICHE_BRAND_CATEGORY_MAP = {
    ...
    "wellness": ["wellness", "beauty", "lifestyle", "health"],
    "intimacy": ["beauty", "lifestyle", "wellness"],  # never tech or food
    ...
}
```

Also add a brand-level flag if needed:
```python
# On the Brand model:
sensitive_category = db.Column(db.Boolean, default=False)

# In the matching query — exclude sensitive brands from non-lifestyle niches:
if brand.sensitive_category and creator.niche not in ['beauty', 'wellness', 'lifestyle']:
    continue
```

---

### Bug 6 — Ask doesn't name the product ("Would you be open to sending a sample?")

This is already in the hard rules but the model is ignoring it. **Reinforce in the LINE 4 prompt instruction:**

```
LINE 4 — Ask:
One sentence. You MUST name the product.
Do not write "a sample" alone — write "a sample of the [hero product]" or name a unit.

Good: "Would you be open to sending a bottle?"
Good: "Would you be open to sending a sample of the [hero product]?"
Bad: "Would you be open to sending a sample?" (product unnamed — model failed)
Bad: "Would you be open to sending product?" (no article — grammatically wrong)
```

---

### Implementation Order

| # | Bug | Where | Time |
|---|-----|--------|------|
| 1 | Sign-off username | Add helper, update prompt | 15 min |
| 2 | Hero product fallback | Add `get_hero_product()` helper | 15 min |
| 3 | AI hallucinating wrong product | Prompt — LINE 3 + hard rule | 10 min |
| 4 | Line 1 is two generic sentences | Prompt — LINE 1 good/bad examples | 10 min |
| 5 | Niche mismatch (sensitive brands) | NICHE_BRAND_CATEGORY_MAP | 20 min |
| 6 | Ask doesn't name product | Prompt — LINE 4 reinforcement | 5 min |

**Total: ~1.5 hours**

---

Bugs 1, 2, 3 are the most visible — `SOcial`, and "Self-tanning foams" and immediately distrusts the tool. Fix those three first.