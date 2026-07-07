# Newcollab — Automated Email Flow
**Goal:** Move users through signup → first pitch → quota hit → Pro upgrade  
**Rule:** Max 1 email/day per user. Max 3/week. Always check current state before sending.

---

## Flow Map

```
SIGNUP
  └─ Stage 1: Activation (no pitch sent yet)
       └─ Stage 2: First pitch sent
            └─ Stage 3: Pitch 2 sent (2/3)
                 └─ Stage 4: Quota hit (3/3)
                      └─ Stage 5: Follow-up overdue
                           └─ Stage 6: Upgrade → PRO

PARALLEL: Re-engagement (inactive users)
PARALLEL: Package won (any stage)
MANUAL:   Weekly broadcast (with suppression rules)
```

---

## Global Rules (apply before every send)

```python
def can_send_email(user, email_type):
    if user.is_pro:
        # Pros only receive: follow-up digest, package win, weekly broadcast
        if email_type not in ['follow_up_digest', 'package_win', 'broadcast']:
            return False

    if emails_sent_today(user) >= 1:
        return False  # max 1/day

    if emails_sent_this_week(user) >= 3:
        return False  # max 3/week

    if user.unsubscribed:
        return False

    return True
```

**Priority order when two emails compete for the same day:**
1. Upgrade trigger
2. Follow-up digest
3. Activation / nudge
4. Weekly broadcast

---

## Stage 1 — Activation
**Segment:** Signed up, 0 pitches sent  
**Goal:** Get them to send their first pitch

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 1.1 | Signup | +1 day | `[first_name], your first brand is waiting` | pitch_count >= 1 |
| 1.2 | Signup | +4 days | `3 [niche] creators landed PR packages this week` | pitch_count >= 1 |
| 1.3 | Signup | +10 days | `Still here — one brand in your feed is responding now` | pitch_count >= 1 |

**Exit:** Cancel all remaining Stage 1 emails the moment `pitch_count >= 1`  
**Never:** Send 1.2 or 1.3 if user already pitched. Check state at send time, not schedule time.

```python
def send_stage_1(user, email_num):
    if user.pitch_count >= 1:
        return  # already activated — stop entire stage
    send(user, f"stage_1_{email_num}")
```

---

## Stage 2 — Saved brand, never pitched
**Segment:** Saved ≥1 brand, pitch_count = 0  
**Goal:** Remove friction between saving and sending

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 2.1 | Brand saved | +2 days | `[Brand name]'s PR inbox is open — your pitch is ready` | pitch_count >= 1 |
| 2.2 | Brand saved | +6 days | `Last nudge on [Brand name] — here's the direct contact` | pitch_count >= 1 |

**Note:** Only send 2.1 for the first saved brand. If user saves multiple brands without pitching, do not send one email per brand — use the first saved brand only.

---

## Stage 3 — First pitch sent (pitch_count = 1)
**Segment:** Just sent their first pitch  
**Goal:** Build momentum, push to pitch 2-3

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 3.1 | Pitch sent (first) | Immediate | `Your pitch to [Brand] is on its way` | — (confirmation, always send) |
| 3.2 | Pitch 1 sent | +3 days | `[Brand] hasn't replied yet — that's normal. Here's what to do while you wait.` | pitch_count >= 3 |

**3.2 content:** Short reassurance + "while you wait, pitch 2 more brands — here are 3 responding in your niche this week." Drives pitch 2 and 3 before quota runs out.

---

## Stage 4 — Pre-quota warning (pitch_count = 2)
**Segment:** Sent 2 pitches, 1 free slot left  
**Goal:** Prime them for the upgrade before they hit the wall

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 4.1 | 2nd pitch sent | +1 day | `1 free pitch left this month — here's what Pro unlocks` | is_pro = true |

**Tone:** Light, not pushy. Show the 3 Pro features (outreach emails, media kit, creator assistant). One soft CTA. This primes the upgrade so the paywall modal doesn't feel like a surprise.

---

## Stage 5 — Quota hit (pitch_count = 3)
**Segment:** Hit free limit, not Pro  
**Goal:** Convert to Pro  
**Key rule:** Do NOT send immediately. Wait 7 days so pitch cards show "Follow up due" (amber) not "Waiting" (blue).

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 5.1 | 3rd pitch sent | +7 days | `[Brand name] hasn't replied yet — here's what to do` | is_pro = true |

**Implementation:**
```python
# On 3rd pitch creation:
if pitch_count == 3 and not user.is_pro:
    user.quota_email_send_at = now + timedelta(days=7)
    db.session.commit()

# Daily cron:
pending = User.query.filter(
    User.quota_email_send_at <= now,
    User.quota_email_sent_month != current_month,
    User.is_pro == False
).all()
for user in pending:
    send_quota_upgrade_email(user.id)
    user.quota_email_send_at = None
    user.quota_email_sent_month = current_month
```

**Template:** `email-quota-v2.html` (built — shows 3 real pitch cards + 2 teaser brands)  
**De-duplication:** `quota_email_sent_month VARCHAR(7)` — one send per calendar month max

---

## Stage 6 — Follow-up digest
**Segment:** Any user with pitches >= 7 days old, no reply logged  
**Goal:** Keep users engaged, remind them of locked follow-up feature  

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 6.1 | Pitch >= 7 days old | Daily check | `Time to follow up with [Brand] — and [N] others` | pitch replied OR user upgrades |

**Critical:** ONE email per user per day listing ALL overdue follow-ups. Never one email per brand.

```python
# Daily cron — runs once per day
def send_followup_digest():
    users_with_overdue = db.session.query(User).join(Pitch).filter(
        Pitch.created_at <= now - timedelta(days=7),
        Pitch.status == 'waiting',
        Pitch.follow_up_sent == False,
        User.is_pro == False
    ).distinct().all()

    for user in users_with_overdue:
        overdue_pitches = [p for p in user.pitches if is_overdue(p)]
        if len(overdue_pitches) == 1:
            subject = f"Time to follow up with {overdue_pitches[0].brand.name}"
        else:
            subject = f"Time to follow up with {overdue_pitches[0].brand.name} — and {len(overdue_pitches)-1} others"
        send_digest(user, overdue_pitches, subject)
```

**For Pro users:** Send the same digest but CTA is "Send follow-up" not "Unlock to follow up"

---

## Stage 7 — Package won
**Segment:** User marks package as received  
**Goal:** Celebrate, collect testimonial, drive referral

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 7.1 | Package marked received | Immediate | `You landed it. [Brand name] is sending you a package 🎉` | — |
| 7.2 | Package marked received | +7 days | `Quick question about your [Brand name] package` | — |

**7.2 content:** Ask for a 2-sentence quote about the experience. These become testimonials in the paywall modal and landing page. Personal email tone, reply-to founder.

---

## Stage 8 — Re-engagement
**Segment:** Signed up, active before, gone quiet  
**Goal:** Bring back users before they fully churn

| # | Trigger | Delay | Subject | Exit condition |
|---|---|---|---|---|
| 8.1 | No login for 14 days | — | `[Brand in their niche] is responding to pitches this week` | any login |
| 8.2 | No login for 30 days | — | `Your saved brands are still waiting` | any login |
| 8.3 | No login for 60 days | — | `Should we keep your account active?` | any login OR unsubscribe |

**8.3** is a soft sunset email. Low volume, but cleans your list and re-activates a % of dormant users.

---

## Manual Weekly Broadcast
**What:** New brands added to directory that week  
**Sent by:** Founder manually  
**Frequency:** Weekly

**Suppression list — exclude these users every time:**
```python
suppress = User.query.filter(
    or_(
        # In upgrade window (hit quota in last 7 days)
        User.quota_email_send_at >= now - timedelta(days=7),
        # Just received upgrade trigger email
        User.quota_email_sent_month == current_month
    )
).all()
```

These users have one job: upgrade. Don't distract them with new brand discovery until they've converted or 7 days have passed.

---

## Full Sequence for a Typical User

```
Day 0    Signup                    → 3.1 Welcome / pitch confirmation (if pitched)
Day 1    (no pitch yet)            → 1.1 "Your first brand is waiting"
Day 3    Brand saved               → (Stage 1 cancelled) 2.1 fires in 2 days
Day 5    (saved, not pitched)      → 2.1 "[Brand] PR inbox is open"
Day 7    First pitch sent          → 3.1 Confirmation (immediate)
Day 10   (pitch 1, no reply)       → 3.2 "Still waiting — pitch 2 more while you wait"
Day 12   Pitch 2 sent              → (stage 3 cancelled)
Day 13   (2/3 pitches)             → 4.1 "1 free pitch left — here's what Pro unlocks"
Day 15   Pitch 3 sent              → quota_email_send_at = Day 22
Day 22   (7 days after pitch 3)    → 5.1 Upgrade trigger (amber cards)
Day 22   (pitches overdue)         → 6.1 Follow-up digest (but 5.1 takes priority today)
Day 23   (if not upgraded)         → 6.1 Follow-up digest
```

---

## DB Columns Required

```sql
-- Users table
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS quota_email_sent_month VARCHAR(7),
  ADD COLUMN IF NOT EXISTS quota_email_send_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_email_sent_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS emails_sent_this_week INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reengagement_stage INT DEFAULT 0;

-- Pitches table
ALTER TABLE pitches
  ADD COLUMN IF NOT EXISTS follow_up_sent BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS follow_up_sent_at TIMESTAMPTZ;
```

---

## Quick Test Checklist

- [ ] Stage 1 stops immediately when pitch_count >= 1
- [ ] Stage 2 only uses first saved brand (not one email per brand)
- [ ] Stage 5 fires on day 7, not day 0
- [ ] Stage 5 does not fire if user already upgraded
- [ ] Stage 6 sends ONE email listing all overdue brands (not one per brand)
- [ ] Stage 6 and Stage 5 never fire on the same day (Stage 5 wins)
- [ ] Weekly broadcast excludes users in upgrade window
- [ ] Max 1 email per day enforced globally
- [ ] All emails check is_pro before sending upgrade CTAs
- [ ] Package won email fires on status change, not on a timer
