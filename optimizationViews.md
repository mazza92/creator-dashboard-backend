# "Who Viewed Your Kit" + PR-Ready Badge — Pro Conversion Brief

**Goal:** Replace "unlimited pitches" as the headline Pro pitch with "see which brands are already looking at you." Sell curiosity about something real that already happened, not a promise about the future.

**Core principle:** This is not a vanity-traffic counter. Every kit link sent in a pitch becomes uniquely tokenized, so a "view" means a specific brand opened a specific creator's kit after receiving their pitch. Free users see that it happened. Pro users see who, when, and how often — which also becomes the trigger for follow-up nudges.

---

## Why This Works (the comment-section insight)

TikTok comments under creator PR videos: "how did you get the PR package," "have you emailed the brand?", "follow for follow please." Nobody asks how to write a better email — they want proof the door opens for people like them. That's envy and hope, not a skills gap.

You already have the seed of this mechanic live: the For You page banner *"2 people viewed your kit this week → Upgrade to see who's checking you out."* It's currently a dismissible footnote. It should be the headline.

---

## Block 1 — Data Model

```python
# New model
class KitView(db.Model):
    __tablename__ = 'kit_views'
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('creators.id'), nullable=False, index=True)
    brand_id = db.Column(db.Integer, db.ForeignKey('brands.id'), nullable=True)  # null if untracked/organic visit
    pitch_id = db.Column(db.Integer, db.ForeignKey('pitches.id'), nullable=True)  # which pitch led to this view
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)
    ip_hash = db.Column(db.String(64))  # sha256(ip + date) — for same-day dedupe only, never store raw IP
    view_count = db.Column(db.Integer, default=1)  # increments on repeat views same brand/creator
```

```sql
CREATE TABLE kit_views (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id),
    brand_id INTEGER REFERENCES brands(id),
    pitch_id INTEGER REFERENCES pitches(id),
    viewed_at TIMESTAMP DEFAULT NOW(),
    ip_hash VARCHAR(64),
    view_count INTEGER DEFAULT 1
);
CREATE INDEX idx_kit_views_creator ON kit_views(creator_id);
```

---

## Block 2 — Tokenize the Kit Link at Pitch Send Time

**Find** wherever the pitch email/template inserts the kit link (currently likely a static `newcollab.co/kit/{username}`).

**Replace with a per-pitch token:**

```python
import hashlib

def generate_kit_token(pitch_id):
    # Short, unguessable, deterministic per pitch
    raw = f"{pitch_id}-{current_app.config['SECRET_KEY']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]

def get_tracked_kit_url(creator, pitch):
    token = generate_kit_token(pitch.id)
    # Store token -> pitch_id mapping at send time
    pitch.kit_token = token
    db.session.commit()
    return f"https://newcollab.co/kit/{creator.username}?ref={token}"
```

```python
# Add to Pitch model
kit_token = db.Column(db.String(10), unique=True, index=True)
```

Use `get_tracked_kit_url()` everywhere the kit link is inserted into outbound pitch content — email body, subject if linked, etc.

---

## Block 3 — View Tracking on Kit Page Load

**Find** the route serving `/kit/<username>`.

```python
@app.route('/kit/<username>')
def view_kit(username):
    creator = Creator.query.filter_by(username=username).first_or_404()
    ref_token = request.args.get('ref')

    if ref_token:
        pitch = Pitch.query.filter_by(kit_token=ref_token).first()
        if pitch:
            ip_hash = hashlib.sha256(
                f"{request.remote_addr}-{date.today()}".encode()
            ).hexdigest()

            existing = KitView.query.filter_by(
                pitch_id=pitch.id, ip_hash=ip_hash
            ).first()

            if existing:
                existing.view_count += 1
            else:
                db.session.add(KitView(
                    creator_id=creator.id,
                    brand_id=pitch.brand_id,
                    pitch_id=pitch.id,
                    ip_hash=ip_hash
                ))
            db.session.commit()

            # Trigger notification — see Block 4
            notify_kit_view(creator, pitch.brand_id, is_repeat=bool(existing))

    return render_template('kit.html', creator=creator)
```

Dedupe by `(pitch_id, ip_hash)` per day so a refresh doesn't inflate counts — but increment `view_count` on genuine repeat visits (different day), since a second visit is a stronger signal worth surfacing to Pro users.

---

## Block 4 — Real-Time Notification Trigger

```python
def notify_kit_view(creator, brand_id, is_repeat):
    brand = Brand.query.get(brand_id) if brand_id else None

    if creator.subscription_tier == 'pro':
        send_push(creator.id,
            title="👀 Your kit was just viewed",
            body=f"{brand.name} viewed your kit{' again' if is_repeat else ''}." +
                 (" They haven't replied yet — want to bump your profile?" if is_repeat else "")
        )
    else:
        # Free users get the tease, never the name
        send_push(creator.id,
            title="👀 A brand just viewed your kit",
            body="Upgrade to see who's checking you out →"
        )
```

Fire this synchronously or via a lightweight background job — the value of this feature is entirely in the immediacy. A notification that arrives a day late loses the emotional spike that drives the upgrade.

---

## Block 5 — Frontend Banner (Free vs Pro)

**Free tier — gated teaser (replace the current dismissible banner):**

```jsx
<KitViewBanner>
  <BlurredAvatarStack count={weeklyViewCount} />
  <span>{weeklyViewCount} brands viewed your kit this week</span>
  <UpgradeButton>Upgrade to see who's checking you out →</UpgradeButton>
</KitViewBanner>
```

**Pro tier — full reveal, becomes the dashboard headline:**

```jsx
<KitViewList>
  {kitViews.map(view => (
    <KitViewRow key={view.id}>
      <BrandLogo src={view.brand.logo} />
      <span>{view.brand.name}</span>
      <span className="timestamp">{formatRelativeTime(view.viewed_at)}</span>
      {view.view_count > 1 && <Badge>Viewed {view.view_count}x</Badge>}
      {!view.has_replied && (
        <BumpButton onClick={() => openBumpModal(view)}>
          Bump your profile →
        </BumpButton>
      )}
    </KitViewRow>
  ))}
</KitViewList>
```

Move this to the **top of the For You page**, above the brand match cards. This is now the primary hook, not a footnote.

---

## Block 6 — "PR-Ready Verified" Badge

No new schema needed — just gate on existing `subscription_tier`.

```jsx
// On kit.html / public kit page
{creator.subscription_tier === 'pro' && (
  <VerifiedBadge title="Verified by Newcollab — actively pitching brands">
    ✓ PR-Ready Verified
  </VerifiedBadge>
)}
```

Place it directly next to the creator's name/avatar on their public kit page — this is what brands see when they click through, and it's what the creator sees on their own preview. Costs nothing to build, isn't gated by follower count, and answers the exact anxiety in "can't get to 1k followers" comments: status that doesn't require scale.

---

## Block 7 — Upsell Copy

**Banner (free, in-app):**
```
🔥 3 brands viewed your kit this week
Upgrade to see who's checking you out →
```

**Push notification (free):**
```
👀 A brand just viewed your kit
Upgrade to see who →
```

**Push notification (Pro, repeat view — ties into Bump Your Profile):**
```
👀 Everbella viewed your kit again — still no reply.
Want us to bump your profile in their inbox?
```

**Upgrade modal headline (replace "Send unlimited pitches"):**
```
Brands are already looking at you.
See exactly who — and follow up before they forget.
```

---

## Implementation Order

| Block | Task | Time |
|-------|------|------|
| 1 | KitView model + migration | 20 min |
| 2 | Tokenize kit links at pitch send | 30 min |
| 3 | View tracking on kit page route | 30 min |
| 4 | Push notification trigger | 30 min |
| 5 | Frontend banner — free teaser + Pro reveal | 1.5 hours |
| 6 | PR-Ready badge on kit page | 15 min |
| 7 | Upsell copy + modal swap | 20 min |

**Total: ~4 hours**

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Upgrade hook | "Send more pitches" (utility) | "See who's watching you" (curiosity/status) |
| Data shown | Vague footnote, dismissible | Headline of dashboard, real tracked opens |
| Follow-up timing | Manual, 7 days stale | Real-time trigger on repeat view — feeds Bump Your Profile |
| Status for low-follower creators | None — followers are the only credibility signal | PR-Ready badge, independent of follower count |
| Conversion moment | End of month, generic limit hit | Real-time, emotionally charged (brand just looked at me) |

This reuses infrastructure you already have (kit pages, pitch sending, the existing banner) — it's a re-pointing of the upsell trigger and one cheap cosmetic badge, not new product surface area.
