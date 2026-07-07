These 5 pitches reveal what the model does when it runs out of ideas — it falls back on 3 templates it keeps recycling. Let me map exactly what's still broken.

---

**Problem 1 — Same Line 1 template used in 3 of 5 pitches:**
```
"The [product] formula stands out from what I normally see in this space."
```
Pitch 2, 3, and 4 are word-for-word identical in structure. This phrase is not banned and the model found it as a safe fallback.

**Problem 2 — Two other weak Line 1 templates:**
```
"caught my attention because it fits perfectly with my content style." (Pitch 1)
"is exactly the kind of product my fitness & wellness content features." (Pitch 5)
```
Both are about the creator, not the product. Neither says anything specific.

**Problem 3 — "Unboxing" fallback used as primary template:**
```
"I'd film my first time unboxing and trying the [product]." (Pitch 1, 5)
```
This was added as a fallback instruction when the model has no product knowledge. It's now the default Line 3 for anything the model isn't confident about. Just as generic as "a short-form video this month."

**Problem 4 — Product name repeated 3+ times despite the rule:**
Pitch 5: "Hydro-Stars hydrocolloid pimple patches" appears in Line 1, Line 3, and Line 4. The rule exists but the model ignores it when the product name is long.

**Problem 5 — Sign-off `mer` still appearing:**
`get_creator_first_name()` not yet implemented. "mer" is a single-word display_name — the helper would return `""` but the code is still passing the raw username to the prompt.

**Problem 6 — Model has no brand context beyond the product name:**
It can't write a specific Line 1 about "Unseen Sunscreen SPF 50" because it only knows the name, not what makes it distinctive. It defaults to "formula stands out." The fix is adding `brand.description` to the prompt context.

---

## Fix Brief

### Fix 1 — Add 4 banned Line 1 phrases to HARD RULES

In the `pitch_prompt`, add to HARD RULES:
```
- Never: "[product] formula stands out from what I normally see in this space"
- Never: "caught my attention because it fits perfectly with my content style"
- Never: "is exactly the kind of product my [niche] content features"
- Never: "I've been looking for a brand like yours"
- Line 1 must say something specific about the product itself — what it does, how it looks, what it contains, why it's distinctive. If you only know the product name, reference its most obvious benefit or use-case.
```

And rewrite the LINE 1 bad examples to include the new patterns:
```
Bad: "The [product] formula stands out from what I normally see in this space." (says nothing about why)
Bad: "[Product] caught my attention because it fits my content style." (about the creator, not the product)
Bad: "Your [product] is exactly the kind of thing my audience loves." (vague, template-sounding)
```

---

### Fix 2 — Kill the unboxing default in Line 3

The current prompt says: *"If you cannot think of a specific scene, describe the unboxing or first-use moment."* — remove this. It's become the primary output.

**Replace the LINE 3 fallback instruction with:**
```
LINE 3 — Content idea:
One sentence. Name the product once (if not yet mentioned in this line, use a shortened form).
Describe a specific real-life scene — not a filming technique, not a content format label.
Ask: what does the viewer SEE in this video?

Good: "I'd show the first application — skin before, skin 20 minutes after, the texture mid-blend."
Good: "I'd film my morning run with [product] already on — no reapplication, no white cast, just the result."
Good: "I'd build a 60-second routine around [product] — how it fits between cleanse and SPF."
Bad: "I'd film my first time unboxing and trying [product]." (unboxing is not a scene — it's a format label)
Bad: "I'd show how [product] fits into a real moment." (no visual, no specific detail)
Bad: "I'd create an authentic review." (not a scene)

Never use "unboxing" as the content idea. If you genuinely cannot think of a specific scene, 
describe the moment the result is visible — after use, not during opening.
```

---

### Fix 3 — Product name repetition in long product names

The current rule ("after first mention, use 'it' or shortened form") is being ignored for long product names. Make the instruction mechanical:

```
PRODUCT NAME RULE:
- Use the full hero product name ONCE in the entire email — either in Line 1 or Line 3, not both.
- After the first use, refer to it as "it", "the product", or the first word of the name only.
- Example: "Hydro-Stars hydrocolloid pimple patches" → first use full name in Line 1, then "the patches" or "them" in Line 3, "a pack" in Line 4.
- Example: "Unseen Sunscreen SPF 50" → "Unseen" after first mention, or "the SPF".
```

---

### Fix 4 — Add `brand.description` to the prompt context

This is what gives the model something real to write Line 1 from. Without it, every Line 1 is fabricated.

```python
# In the BRAND section of pitch_prompt, add:
- Description: {brand.description or ''}
```

And update LINE 1 instruction:
```
Use brand.description to anchor Line 1 to something real and specific about this product.
If description is empty, reference the product's most obvious function or benefit from its name.
"Unseen Sunscreen SPF 50" → invisible formula, no white cast angle.
"Hydro-Stars hydrocolloid pimple patches" → the patch shape, the overnight result.
```

If `brand.description` is empty for most brands, this is also the trigger to prioritise the enrichment script. Until descriptions are populated, Line 1 will always be templated.

---

### Fix 5 — Sign-off `mer` (implement `get_creator_first_name`)

This is code, not prompt. **Find where you build the pitch prompt in your Flask route — likely something like:**
```python
# Find:
sign_off = creator.username  # or creator.display_name or however it's currently passed
```

**Add this helper and replace:**
```python
def get_creator_first_name(creator):
    if creator.first_name:
        return creator.first_name
    if creator.display_name and ' ' in creator.display_name:
        return creator.display_name.split()[0].capitalize()
    return ""

# In your prompt:
sign_off = get_creator_first_name(creator)
# Pass to prompt as:
- Sign-off name: {sign_off if sign_off else "[omit sign-off line entirely]"}
```

Add to HARD RULES:
```
- If sign-off name is empty, omit the sign-off line entirely — never use a username or handle
```

---

### Priority order

| # | Fix | Impact | Time |
|---|-----|--------|------|
| 1 | Add `brand.description` to prompt context | Fixes Line 1 root cause | 10 min |
| 2 | Ban 4 template Line 1 phrases | Stops recycling | 10 min |
| 3 | Rewrite Line 3 fallback, ban unboxing | Fixes Line 3 root cause | 10 min |
| 4 | Make product name repetition mechanical | Fixes long name repetition | 5 min |
| 5 | Implement `get_creator_first_name()` | Fixes sign-off | 15 min |

Fix 1 and 2 together. Fix 3 immediately after. Fix 5 is a code change, do it once.

The model keeps templating because it has no real knowledge of the brand — `brand.description` is the single most important input missing from the prompt right now.