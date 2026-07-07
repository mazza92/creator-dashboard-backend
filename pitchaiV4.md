Real progress — Line 3 is working properly now on Pitches 1 and 3. The rest is clear pattern analysis.

---

**What's now working:**
- Pitch 1 Line 3: "patches going on, skin texture before, skin 20 minutes after" — specific, visual, believable
- Pitch 3 Line 3: "application, then 4 hours later, no shine, no pilling, under makeup" — exactly right
- Pitch 1 Line 4: "a pack" — natural, no product name repetition needed

**What still needs fixing:**

---

**Problem 1 — Two new Line 1 templates replacing the old ones:**

Pitches 1 and 4 both use:
```
"is something I'd genuinely use, not just feature."
```
Pitches 2 and 3 both use:
```
"has the kind of results I actually want to show on camera."
```

The model banned the old templates, found two new safe ones, and is repeating those. Both are still about the creator's enthusiasm, not the product. Both will read as generated to a PR manager who sees 50 pitches a week.

**Problem 2 — Product variants dumped into the pitch:**
```
"The Mental Performance Shot (Original, MAXX, FREE varieties)"
```
The hero_product field has variant info included. This is a data quality issue — the parenthetical shouldn't be in the pitch. Either clean the field or strip it before inserting into the prompt.

**Problem 3 — Line 4 not naming the product in 3 of 4 pitches:**
```
"Would you be open to sending a sample?"  ← Pitches 2, 3, 4
```
The rule exists but the model ignores it when it hasn't established a natural shortened name for the product earlier in the email.

**Problem 4 — Line 3 for supplements has no specific scene:**

Pitch 4 (Magnesium L-Threonate):
```
"I'd film a before-and-after with Magnesium, something my audience can actually see working."
```
You can't see a supplement working. The model doesn't know what "visible result" means for an ingestible. "Something my audience can actually see working" is a signal the model knows it's vague.

Pitch 2 (Mental Performance Shot):
```
"I'd show Mental in use, the texture, the application, the visible result."
```
"Mental" as a shortened name is strange. "The application" — you drink a shot, there's no application. The model is applying topical product logic to a drink.

**Problem 5 — Grammar: subject-verb agreement breaking on compound/plural product names:**
- "Your Hydro-Stars hydrocolloid pimple patches **is** something" → should be "are"
- "Your Magnesium L-Threonate and Liposomal Glutathione **is** something" → should be "are"

**Problem 6 — `mer` sign-off still not fixed in code**

---

## Fix Brief

### Fix 1 — Ban the two new Line 1 templates

Add to HARD RULES:
```
- Never: "is something I'd genuinely use, not just feature"
- Never: "has the kind of results I actually want to show on camera"
- Never: "is a product I'd actually reach for" or any variation of "I'd actually use/reach for/buy"
- Line 1 must make a claim about the product — not about the creator's desire to use it
```

The core LINE 1 rule needs tightening:
```
LINE 1 rule: Write a sentence where the SUBJECT is the product and the CLAIM is 
about what the product does, looks like, contains, or is known for.
The creator's opinion can be the ending — not the whole sentence.

Good: "The Hydro-Stars patches dissolve into skin by morning — they're the ones showing up in my skincare shelf posts."
Good: "The Unseen Sunscreen SPF 50 is the no-white-cast formula my comments ask for every time I post a sunny-day routine."
Bad: "The [product] is something I'd genuinely use." (creator enthusiasm, no product claim)
Bad: "The [product] has the kind of results I want to show on camera." (about the creator, not the product)
```

---

### Fix 2 — Strip product variant parentheticals before inserting into prompt

In your pitch generation route, clean the hero_product field before passing it to the prompt:

```python
import re

def clean_hero_product(hero_product):
    if not hero_product:
        return hero_product
    # Strip parenthetical variant info: "(Original, MAXX, FREE varieties)"
    return re.sub(r'\s*\([^)]*\)', '', hero_product).strip()

# Then in your prompt:
hero = clean_hero_product(get_hero_product(brand))
```

---

### Fix 3 — Enforce product naming in Line 4

The model only names the product in Line 4 when it has a natural short form from Line 1. Make it explicit:

```
LINE 4 rule:
You MUST reference the product. Use the shortest natural form established in this email.
If the product is a liquid/serum/cream → "a bottle"
If the product is a patch/sticker → "a pack"
If the product is a supplement/capsule → "a month's supply" or "a box"
If the product is a shot/drink → "a few to try" or "a box"
If the product is clothing/wearable → "a pair" or "one to try"
Default: "a sample of the [shortest product name]"
Never just: "a sample" with no product reference
```

---

### Fix 4 — Add supplement/ingestible logic to Line 3

This is a new content category. Sunscreen, patches, and fragrance all have visible application or before/after. Supplements, shots, and drinks do not.

Add to LINE 3 instructions:
```
For ingestible products (shots, supplements, capsules, drinks, powders):
Do NOT describe "application" or "visible results" — there are none.
Instead, describe the routine context: when the creator takes it, what else is in that moment, 
what they're about to do.

Good: "I'd film the morning stack — the Mental Performance Shot next to the water bottle, 
before the workout, as part of the 6am routine my audience follows."
Good: "I'd build a 60-second 'what I take every morning' video around the Magnesium — 
capsule count, timing, why I started taking it."
Bad: "I'd show the application and the visible result." (ingestibles have no application)
Bad: "I'd film a before-and-after." (supplements don't show results in one video)
```

---

### Fix 5 — Grammar rule for plural/compound product names

Add to HARD RULES:
```
- Always check subject-verb agreement when the product name is plural or compound:
  "patches are" not "patches is"
  "Magnesium L-Threonate and Glutathione are" not "is"
```

---

### Fix 6 — Implement `get_creator_first_name()` in code

Still showing `mer`. This is a code fix, not a prompt fix — the prompt change alone won't stop it. The function needs to be in the backend route:

```python
def get_creator_first_name(creator):
    if creator.first_name:
        return creator.first_name
    if creator.display_name and ' ' in creator.display_name:
        return creator.display_name.split()[0].capitalize()
    return ""
```

Pass `get_creator_first_name(creator)` to the prompt, not `creator.username` or `creator.display_name` directly. If it returns `""`, the prompt instruction is: omit the sign-off line entirely.

---

### Priority

| Fix | What it kills | Time |
|-----|--------------|------|
| 1 — Ban new Line 1 templates | Templates recurring | 5 min |
| 2 — Strip variant parentheticals | "(Original, MAXX, FREE)" | 5 min |
| 3 — Enforce Line 4 product naming | "a sample" with no product | 5 min |
| 4 — Supplement Line 3 logic | Wrong scene for ingestibles | 10 min |
| 5 — Grammar rule | "patches is" | 5 min |
| 6 — Sign-off code fix | `mer` | 15 min |

Line 3 is now the strongest part of the pitch. Line 1 is the remaining weak point — and it stays weak until Fix 1 is applied AND `brand.description` is populated for more brands.