Two pitches are now genuinely good. Two are still failing for the same reason. Let me break it down honestly.

---

**Pitch 2 (Unseen Sunscreen) — done:**
- Line 1: Specific product claim (no-white-cast formula), audience signal (my comments ask for it), context (sunny-day routine) — exactly right
- Line 3: Real scene, visual, believable — morning run, no reapplication, result shown
- Line 4: "a sample of the SPF" — natural, short form

**Pitch 1 (Hydro-Stars) — close:**
- Line 1: "the overnight fix that actually pulls out what my followers want to see" — slightly awkward ending but has a real product claim
- Line 3: Excellent — patches on, before, 20 minutes after
- Line 4: "a pack of the patches" — natural

---

**Pitches 3 and 4 (both fragrance) — still failing, same two causes:**

**Cause A — Wrong product data:**
"Luxury Clean Fragrances" is a category string, not a product name. No prompt fix will make "Your Luxury Clean Fragrances has the aesthetic" read well — the subject is wrong before the AI starts writing. This brand needs `hero_product` enriched.

**Cause B — Wrong scene logic for fragrance:**
Both Pitch 3 and 4 have identical Line 3:
```
"I'd show the [fragrance] in use — the texture, the application, the visible result."
```
Fragrance has no texture to show, no visible application, no visible result. The model is applying sunscreen/skincare logic to a scent product. "The visible result" of a perfume mist is nothing — it's invisible on camera.

---

## Fix Brief — Fragrance / Sensory Products

### Fix 1 — Add fragrance to the product type logic in Line 3

Extend the ingestibles logic with a sensory/fragrance category:

```
For fragrance, perfume, candle, and scent products:
Do NOT describe "texture", "application", or "visible result" — fragrance has none of these on camera.
Instead, describe the aesthetic or ritual context the product lives in.

Good: "I'd build a getting-ready moment around the [mist] — bottle on the counter, 
the spritz before leaving, the mood it sets."
Good: "I'd show the [perfume] as part of a morning shelf moment — 
what it sits next to, why it's the one I reach for."
Good: "I'd dedicate a video to the [fragrance] layered into a routine — 
what it pairs with, who it's for."
Bad: "I'd show the texture, the application, the visible result." (fragrance has none of these)
Bad: "I'd show the perfume in use." (use means nothing for fragrance — be specific about the scene)
```

### Fix 2 — Add one more banned Line 1 template

"is the kind of product that shows real results on camera" (Pitch 3) is a mutated version of the banned phrase "has the kind of results I actually want to show on camera." Add to HARD RULES:

```
- Never: "is the kind of product that shows real results on camera"
- Never: any Line 1 where the main claim is about wanting to film it, not about what the product is
```

### Fix 3 — Line 4 grammar for fragrance

Add to the LINE 4 unit guide:
```
For fragrance/perfume/mist → "a bottle" or "one to try"
Never: "one of the perfume" or "one of the fragrance" (grammatically wrong)
```

### Fix 4 — "Luxury Clean Fragrances" data fix (no prompt fix possible)

This needs enrichment. Until the brand has a real `hero_product`, every pitch will have an awkward subject line. Deprioritise brands where `hero_product` is null or matches the `category` field exactly — they should be lower in the For You feed until enriched.

Quick filter to check:
```python
# Brands where hero_product is effectively just a category string
# (hero_product == category or hero_product contains common category words)
problematic = Brand.query.filter(
    (Brand.hero_product == Brand.category) |
    Brand.hero_product.is_(None)
).all()
```

---

### State of the pitch quality now

| Brand type | Line 1 | Line 3 | Status |
|---|---|---|---|
| Topical (sunscreen, patches) | Good | Good | Ready |
| Supplements/ingestibles | Improving | Needs Fix 4 from last brief | Partially done |
| Fragrance/scent | Template | Wrong logic | Needs Fix 1-3 above |
| Brands without hero_product | Broken | N/A | Data fix required |

The model now writes good pitches for anything with a visible before/after or a demonstrable surface result. Fragrance and sensory products need their own content logic. Everything without a real `hero_product` needs data before the prompt can help.