# Creator Pool — Full Integration Brief

**Goal:** Ship the Creator Pool ("give to get" follow exchange) inside the existing app without regressing current flows. It must feel like a natural extension of For You, Inbox, and the pitch generator — not a bolted-on side feature.

**Core principle:** Reuse existing UI patterns, existing nav structure, existing notification/email infrastructure. No new design language, no new sending infrastructure, minimal new screens.

---

## Matching Algorithm — Who Shows Up In the Queue

**Why this matters beyond UX:** platform distribution (TikTok FYP, Instagram Explore) is driven by engagement rate, not follower count. A follower with no interest in the niche rarely engages, which can hurt reach rather than help it. Matching by niche/region also avoids the actual bot-detection signature — platforms flag velocity and incoherence (follow bursts from topically/geographically unrelated accounts), not the fact that a follow originated from a referral link. Niche-matched growth looks like normal organic discovery; your existing 3/day cap already throttles velocity, which matters more to detection systems than the matching itself.

**Matching priority (cheap — reuses fields already on the Creator model):**

```python
NICHE_ADJACENCY = {
    'beauty': ['skincare', 'haircare', 'fashion'],
    'skincare': ['beauty', 'wellness'],
    'fitness': ['wellness', 'nutrition', 'lifestyle'],
    'fashion': ['beauty', 'lifestyle'],
    # extend per category map already used for brand matching
}

def get_pool_matches(creator, limit=10):
    base = Creator.query.join(PoolCredit).filter(
        Creator.id != creator.id,
        (PoolCredit.balance > 0) | (Creator.subscription_tier == 'pro')
    )

    same_niche = base.filter(Creator.niche == creator.niche)
    adjacent = base.filter(Creator.niche.in_(NICHE_ADJACENCY.get(creator.niche, [])))
    same_region = base.filter(Creator.region == creator.region)

    # Combine in priority order, dedupe, cap at limit
    ranked = dedupe_preserve_order(same_niche, adjacent, same_region)
    return ranked[:limit]
```

Don't make this exclusive — if there aren't enough same-niche/region matches to fill the queue, fall back to follower-tier-only matching rather than showing an empty Pool. Quality-first, but never block the loop entirely.

---

## Block 1 — Navigation: Add "Pool" Tab

**Find** the main nav component rendering `For You | Discover | Inbox | My Kit`.

**Insert** a new tab between Inbox and My Kit:

```jsx
<Nav>
  <NavItem active={tab==='foryou'}>For You {forYouBadge > 0 && <Badge>{forYouBadge}</Badge>}</NavItem>
  <NavItem active={tab==='discover'}>Discover</NavItem>
  <NavItem active={tab==='inbox'}>Inbox {inboxBadge > 0 && <Badge>{inboxBadge}</Badge>}</NavItem>
  <NavItem active={tab==='pool'}>Pool {poolBadge > 0 && <Badge color="violet">{poolBadge}</Badge>}</NavItem>
  <NavItem active={tab==='mykit'}>My Kit {isNewFeature && <NewTag>NEW</NewTag>}</NavItem>
</Nav>
```

`poolBadge` = count of new followers received since last Pool tab visit. Use the same badge component/style already used on Inbox (red pill with white number) but in violet (`--violet: #7C3AED`) to visually distinguish "growth" activity from "brand reply" activity.

---

## Block 2 — For You Page: Replace the Social Proof Strip

**Find** the component rendering:
`"Creators with under 10K followers got PR from brands like these this month"` (avatar stack + text, sits between the kit-view banner and the Matches/Opportunities tabs).

**Replace with:**

```jsx
<PoolPromoBanner>
  <AvatarStack users={activePoolMembers} />
  <BannerText>
    <Title>Grow your following & engagement with the community</Title>
    <Subtitle>Active Pool members get 2x more profile views from brands</Subtitle>
  </BannerText>
  <JoinPoolButton onClick={() => navigate('/pool')}>Join Creator Pool</JoinPoolButton>
</PoolPromoBanner>
```

Keep the exact visual treatment of the current strip (white card, avatar stack, single-line CTA) — same component shell, swapped content and destination. `activePoolMembers` should pull from creators who gave a support in the last 7 days, not a static/placeholder list (this is the same trust issue you flagged with the "~5d avg reply" and "Summer campaigns" placeholder bugs — don't ship another fake-feeling banner).

Do not remove this banner for users who've already joined the Pool — instead swap its content to their own stats: *"You've grown +12 followers this month from the Pool — keep it up →"* This keeps the slot doing conversion work for both new and existing Pool members instead of going stale.

---

## Block 3 — Pitch Generator Modal: Pool Nudge

**Find** the pitch modal component (the one showing PITCH / "Open in email app" / "Media kit attached").

**Insert** a nudge directly below the "Media kit attached" row, shown only if the creator has not given a Pool support in the last 7 days:

```jsx
{!hasRecentPoolActivity && (
  <PoolNudge>
    💡 Active Pool members get 2x more profile views from brands.
    <NudgeLink onClick={() => navigate('/pool')}>Join the Pool →</NudgeLink>
  </PoolNudge>
)}
```

**Second nudge moment — after a pitch is sent**, in the confirmation/success state (before the modal closes):

```
✓ Pitch sent to Withings
While you wait for a reply, grow your following — [Join the Pool →]
```

This is the highest-attention moment in the entire app (they just took the desired action) — use it to cross-sell Pool engagement instead of letting the modal just close.

Do not show the nudge to creators already active in the Pool — repeating a CTA someone has already acted on reads as broken, not encouraging.

---

## Block 4 — Optimized Follow Flow (kill every extra step)

This is the part most likely to leak users if it's clumsy. Target: tap → native app opens with profile loaded → tap Follow → return to Newcollab automatically prompts confirmation. No manual navigation back to a list.

**Deep link generation:**

```python
def get_social_deep_link(platform, username):
    if platform == 'tiktok':
        return {
            'app': f"tiktok://user?username={username}",
            'web': f"https://www.tiktok.com/@{username}"
        }
    if platform == 'instagram':
        return {
            'app': f"instagram://user?username={username}",
            'web': f"https://instagram.com/{username}"
        }
```

**Frontend — app-link-with-fallback pattern (standard across Linktree, social apps):**

```js
function openFollowFlow(platform, username, targetCreatorId) {
  const { app, web } = getDeepLinks(platform, username);
  const start = Date.now();

  window.location.href = app;
  setTimeout(() => {
    if (Date.now() - start < 1600) window.location.href = web; // app not installed
  }, 1200);

  const onReturn = () => {
    if (document.visibilityState === 'visible') {
      showFollowConfirmToast(targetCreatorId);
      document.removeEventListener('visibilitychange', onReturn);
    }
  };
  document.addEventListener('visibilitychange', onReturn);
}
```

**On return**, auto-surface a toast instead of requiring navigation:

```jsx
<ConfirmToast>
  Did you follow @{username}?
  <Yes onClick={() => confirmSupport(targetCreatorId)}>Yes, +1 credit</Yes>
  <No onClick={() => dismiss()}>Not yet</No>
</ConfirmToast>
```

This removes the two biggest friction points in a naive version: (1) manually finding your way back to the right card in a list, (2) a separate "mark as done" tap that feels like extra admin work instead of a natural continuation.

---

## Block 5 — UX Patterns to Replicate

- **Single-card queue, not a scrolling list** (Tinder/Bumble pattern). One creator shown at a time with Follow / Skip — reduces decision fatigue and creates a faster sense of progress than a static list of 10 cards.
- **Daily/weekly streak indicator** (Duolingo pattern) — "3-day support streak" shown at the top of the Pool tab, reinforcing the decay mechanic from the credit system without needing extra copy.
- **Avatar stack social proof** (Instagram "Suggested for you" pattern) — already used elsewhere in your app (the kit-view teaser), reuse the same component for the Pool banner in Block 2 rather than building a new one.

---

## Block 6 — Email & Push Notification Triggers

**Trigger condition:** a `PoolSupport` row is confirmed where `target_id = creator.id`.

**Push (immediate, if push enabled):**
```
🎉 @username just followed you from the Pool!
```

**Email (daily digest, not per-follow — avoid notification fatigue):**

Batch all confirmed supports received in the last 24h, send once via existing transactional template wrapper (same header/footer/branding used for pitch confirmations).

```python
def send_pool_digest_email(creator):
    new_supporters = PoolSupport.query.filter(
        PoolSupport.target_id == creator.id,
        PoolSupport.confirmed_at >= datetime.utcnow() - timedelta(days=1)
    ).all()

    if not new_supporters:
        return

    render_email(
        template='existing_transactional_template',  # reuse current header/footer
        subject=f"You got {len(new_supporters)} new follower(s) from the Pool 🎉",
        body=render_pool_digest_body(new_supporters)
    )
```

**Email body (plain, matches existing tone — no new visual system):**

```
Hi {first_name},

{count} creators followed you from the Pool this week:

{for each: avatar/name + niche tag}

Keep your streak going — support 3 more to stay visible.

[Open Pool →]
```

Send at a fixed time (e.g. 9am local) rather than real-time per follow — this turns a passive notification into a daily reason to reopen the app, same job your existing nudge emails already do.

---

## Block 7 — Data Model

```python
class PoolSupport(db.Model):
    __tablename__ = 'pool_supports'
    id = db.Column(db.Integer, primary_key=True)
    supporter_id = db.Column(db.Integer, db.ForeignKey('creators.id'), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey('creators.id'), nullable=False)
    platform = db.Column(db.String(20))
    confirmed_at = db.Column(db.DateTime, default=datetime.utcnow)

class PoolCredit(db.Model):
    __tablename__ = 'pool_credits'
    creator_id = db.Column(db.Integer, db.ForeignKey('creators.id'), primary_key=True)
    balance = db.Column(db.Integer, default=0)
    week_start = db.Column(db.Date)
```

```sql
CREATE TABLE pool_supports (
    id SERIAL PRIMARY KEY,
    supporter_id INTEGER NOT NULL REFERENCES creators(id),
    target_id INTEGER NOT NULL REFERENCES creators(id),
    platform VARCHAR(20),
    confirmed_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE pool_credits (
    creator_id INTEGER PRIMARY KEY REFERENCES creators(id),
    balance INTEGER DEFAULT 0,
    week_start DATE
);
```

Visibility rule in the feed query: only show creators where `PoolCredit.balance > 0` (or `subscription_tier = 'pro'`, who bypass the balance check per Block 6 of the earlier brief).

---

## Implementation Order

| Block | Task | Time |
|-------|------|------|
| 7 | PoolSupport + PoolCredit models + migration | 30 min |
| 1 | Add Pool nav tab + badge logic | 30 min |
| 4 | Deep-link follow flow + confirm toast | 1.5 hours |
| 2 | Replace For You social proof banner | 45 min |
| 3 | Pitch modal nudge (pre-send + post-send) | 45 min |
| 6 | Push trigger + daily digest email | 1 hour |
| 5 | Single-card queue UI + streak indicator | 1.5 hours |

**Total: ~6.5 hours**

---

## What NOT to Change

- Don't touch `FREE_MONTHLY_LIMIT` or the existing pitch-credit deduction logic — Pool credits are a separate balance, separate table, separate cap.
- Don't remove the kit-view teaser banner from Block 2 of the previous brief — it should sit alongside the Pool banner, not replace it. Only the *social proof strip* is being swapped, not the kit-view feature.
- Don't send pool-follow emails per-event — batch into the daily digest or you'll train users to ignore notifications from the app entirely.
