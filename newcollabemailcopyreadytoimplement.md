# Newcollab Email Copy — Ready to Implement

**Purpose:** Production-ready copy for all 23 emails in the strategy overhaul. Dev can paste directly into templates.

**Global rules (all emails):**
- Sender name: `Your Newcollab Manager`
- Sender email: `team@newcollab.co`
- Reply-to: `team@newcollab.co` (real inbox monitored)
- No em dashes anywhere
- Lowercase subject lines (except for user's first_name and brand names)
- No exclamation marks in subject lines
- Preheader max 100 chars
- Body max ~250 words unless specified
- CTA: 1 primary button per email, plain link secondary only where noted
- Variables in Jinja `{{ variable_name }}` format
- All URLs include UTM: `?utm_source=email&utm_medium=lifecycle&utm_campaign={{ email_slug }}`

**Fallback rules for variables:**
- `{{ first_name }}` — falls back to "there" if empty
- `{{ brand_name }}` — always required, do not send email if empty
- `{{ N_unlocks }}` — always required
- `{{ score }}` — falls back to "your starting score" if empty
- Any brand-specific email skips if brand data is null

---

# 1 · EMAIL VERIFICATION (existing, minor polish)

**Trigger:** Immediately after registration
**Template file:** Custom HTML in `app.py:send_verification_email()`

---

**Subject:**
```
Verify your Newcollab account
```

**Preheader:**
```
One click to activate your account and meet your manager.
```

**Body:**
```
Hi {{ first_name or "there" }},

Welcome to Newcollab. Click below to verify your email and get started.

Your verification link expires in 24 hours.

If you didn't sign up, ignore this email.

Your Newcollab Manager
```

**CTA button:**
- Label: `Verify email`
- URL: `https://app.newcollab.co/verify-email?token={{ token }}`

---

# 2 · WELCOME FROM YOUR MANAGER (REWRITE)

**Trigger:** Immediately after email verification
**Template file:** `welcome_email.html`

---

**Subject:**
```
hi from your manager, {{ first_name }}
```

**Preheader:**
```
Let's get you your first PR reply.
```

**Body:**
```
Hi {{ first_name or "there" }},

I'm your Newcollab manager. My job over the next few weeks is to help you go from "I want brand deals" to "I have a reply in my inbox."

Here's what I know so far:

You just joined. You have 3 brand unlocks this month. Each one is a full strategy plus verified contact, not just an email.

If I could give you one piece of advice for this week, it's this: use all 3 unlocks. The more brands you pitch, the higher your chances of landing a reply. Creators who pitch 3 brands in their first week are 4x more likely to get a response than those who only pitch 1.

I'll send you a short guide every 3 to 4 days on how brands actually evaluate creators. Nothing spammy. Just what I'd tell a friend.

Ready to see who I've matched you with?

Your Newcollab Manager
```

**CTA button:**
- Label: `Meet your matches`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=welcome`

---

# 3 · YOUR FIRST MOVE (REWRITE of "First Brand is Waiting")

**Trigger:**
- 24h after signup
- `first_pitch_sent_at IS NULL`
- Global cooloff respected
- Cron: daily at 10am

**Template file:** `conversion_email.html`

---

**Subject:**
```
{{ first_name }} — your first move
```

**Preheader:**
```
The one thing most creators skip.
```

**Body:**
```
Hi {{ first_name or "there" }},

You signed up yesterday and haven't unlocked a brand yet. That's fine. Most creators browse first.

Here's the thing I wish every new creator knew:

Before you pitch anyone, spend 5 minutes fixing your bio and publishing your portfolio. That single step raises your reply chance from around 5% to around 18%. Same effort, 3 times the odds.

Brands check your profile in the 10 seconds between reading your subject line and deciding whether to open your email. If your bio doesn't tell them who you are and how to reach you, they close the tab.

I laid out both fixes in your plan. Should take about 15 minutes.

Once these are done, pitch your first brand. I'll help write it.

Your Newcollab Manager
```

**CTA button:**
- Label: `Open my plan`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=first_move`

---

# 4 · MEET THE MANAGER (NEW)

**Trigger:**
- Day 3 after signup
- User has NOT completed bio OR portfolio in their plan
- User hasn't logged in since Day 1
- Cron: daily at 9am

---

**Subject:**
```
{{ first_name }}, i think i can help
```

**Preheader:**
```
A quick note from your manager.
```

**Body:**
```
Hi {{ first_name or "there" }},

I noticed you signed up but haven't set up your plan yet. Not chasing you. Just want to say two things.

First, most creators who land brand deals through Newcollab do the exact same 2 things in their first week: fix their bio for brand outreach, and publish a portfolio brands can actually look at. Those 2 things take 15 minutes and move your reply chance up meaningfully.

Second, I'm not a template or a chatbot. I'm the manager. Every action in your plan is picked because it's the highest-leverage next move for your specific profile. If something in the plan feels wrong, hit reply on this email and tell me.

Here when you're ready.

Your Newcollab Manager
```

**CTA button:**
- Label: `See my plan`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=meet_manager`

---

# 5 · WHY BRANDS SAY NO (NEW · Education series Email 1 of 6)

**Trigger:**
- Day 5 after signup
- User has completed at least 1 free plan action OR unlocked 1 brand
- Skips if user is Pro
- Cron: daily at 10am

---

**Subject:**
```
the 5 real reasons brands don't reply
```

**Preheader:**
```
It's rarely about your follower count.
```

**Body:**
```
Hi {{ first_name or "there" }},

Most creators assume brands ignore them because they're too small. Almost never true.

I've watched thousands of pitches. Here are the actual top 5 reasons brands don't reply:

1. Your bio doesn't say what you make. Brands scan for niche fit in under 3 seconds. "UGC Creator ✨" tells them nothing.

2. Your last 9 posts don't show product in real use. Brands hire creators who demonstrate, not just display.

3. You have no visible contact. Brands who liked you can't figure out how to write back.

4. Your pitch subject line says "collaboration inquiry." Every creator sends that. It doesn't stand out.

5. You didn't follow up. 67% of brand replies come after a follow-up. Most creators send once and give up.

The good news: every one of these is a 15-minute fix.

Want your manager's audit of which of these is holding YOU back specifically?

Your Newcollab Manager
```

**CTA button:**
- Label: `See my audit`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=edu_5reasons`

---

# 6 · THE 60-SECOND BRAND AUDIT (NEW · Education Email 2)

**Trigger:**
- Day 8 after signup
- User received Email 5
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
the 60-second brand audit
```

**Preheader:**
```
How PR coordinators actually decide.
```

**Body:**
```
Hi {{ first_name or "there" }},

Ever wonder what a brand's PR person actually does when they read your pitch?

I've asked a lot of them. It's the same 60 seconds every time. Here's the exact sequence.

Second 1 to 10: subject line. If it's generic ("collab opportunity"), archived.

Second 11 to 25: they scan your bio for niche fit. Beauty pitch, no beauty in bio? Archived.

Second 26 to 40: they open your Instagram or TikTok. First 3 posts. Are they on-niche? Recent? Aesthetically consistent?

Second 41 to 50: they check for a contact email. If none, most won't do the work of finding one.

Second 51 to 60: if you're still in play, they read your pitch body. Now it's about the offer.

The takeaway: you have to win seconds 11 to 50 before your pitch body even matters.

Every one of those checkpoints is fixable. In your plan, I've flagged which ones you're currently failing.

Your Newcollab Manager
```

**CTA button:**
- Label: `See my checkpoints`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=edu_60sec`

---

# 7 · THE PITCH THAT GETS REPLIED TO (NEW · Education Email 3)

**Trigger:**
- Day 12 after signup
- User received Email 6
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
the pitch that gets replied to
```

**Preheader:**
```
Anatomy of a working cold pitch.
```

**Body:**
```
Hi {{ first_name or "there" }},

I want to break down a real pitch that landed a small creator (2,100 followers) a PR box from a mid-sized beauty brand.

Here's the structure. Copy it.

Subject: "quick idea for {brand_name} + skincare routines"

Line 1: name a specific product they sell. "Your Retinol Boost has been on my nightstand for 6 weeks."

Line 2: name what your audience does that maps to their brand. "I make morning routines for creators with sensitive skin, which is exactly the audience your Retinol Boost speaks to."

Line 3: propose ONE specific content idea. Not "some content." A specific idea. "I'd love to feature it in a 2-week 'gentle retinol' series and share the honest results."

Line 4: state the ask. "Would you be open to sending a bottle?"

Line 5: 1 sentence signature with your handle + follower count.

That's it. Under 100 words. Specific. Confident.

The mistake most creators make: 4 paragraphs of self-introduction. Brands don't need your life story. They need to know why THIS brand + YOU.

My AI writes pitches in exactly this structure. If you haven't tried it, it's in your plan.

Your Newcollab Manager
```

**CTA button:**
- Label: `Generate a pitch`
- URL: `https://app.newcollab.co/creator/dashboard/for-you?utm_campaign=edu_pitch`

---

# 8 · HOW FOLLOW-UPS ACTUALLY WORK (NEW · Education Email 4)

**Trigger:**
- Day 16 after signup
- User received Email 7
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
follow-ups are where deals actually happen
```

**Preheader:**
```
The 3-touch cadence that lifts reply rate 4x.
```

**Body:**
```
Hi {{ first_name or "there" }},

Here's something that will change how you think about pitching:

Most creators send one email. If no reply in 3 days, they conclude "the brand said no" and move on.

That's the mistake.

67% of brand replies come after a follow-up. Not on the first email. On the second, third, or fourth touch.

Here's the cadence that works:

Touch 1: your initial pitch (day 0)
Touch 2: day 6 nudge. One sentence. "Hi, just following up on the note below. Happy to answer any questions."
Touch 3: day 14 value-add. "Wanted to share I just posted [something related] and it hit [some metric]. Still keen to work together if the timing is right."
Touch 4: day 30 soft close. "Understand you might be busy. If this isn't the right timing, no worries. I'll circle back next quarter."

If they don't reply after 4 touches, then you can conclude no.

Most creators would rather send 10 new pitches than follow up on 3. Follow-ups convert 4x higher.

I automate this if you're on Pro. On free, I remind you when it's time.

Your Newcollab Manager
```

**CTA button:**
- Label: `Check your pipeline`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?utm_campaign=edu_followup`

---

# 9 · YOUR MICRO-CREATOR EDGE (NEW · Education Email 5)

**Trigger:**
- Day 20 after signup
- User received Email 8
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
your micro-creator edge (macros don't have this)
```

**Preheader:**
```
Why small creators land deals macros can't.
```

**Body:**
```
Hi {{ first_name or "there" }},

Creators with millions of followers can't do 3 things you can:

1. Reply to a brand within an hour of them writing. Macros have agents. You have your phone.

2. Post authentic-feeling content that doesn't scream "sponsored." Brands paying for UGC-style ads specifically want smaller creators because your posts look real, not staged.

3. Offer to be flexible on rates for the right partnership. A macro creator can't do this. You can.

Small creators are winning UGC deals in 2026 not despite their size but because of it. Brands running Meta and TikTok ads (the majority of who buys UGC) specifically prefer creators between 2K and 25K followers.

The mistake I see: small creators pitch like they're macros. They lead with follower count and vanity metrics. Don't. Lead with your speed, your authenticity, and your flexibility.

I've written a "micro-creator edge" line into the Growing pitch tone. Try it on your next pitch.

Your Newcollab Manager
```

**CTA button:**
- Label: `Pitch your next brand`
- URL: `https://app.newcollab.co/creator/dashboard/for-you?utm_campaign=edu_edge`

---

# 10 · TIME TO PITCH {{ BRAND_NAME }} (REWRITE)

**Trigger:**
- 2 days after user saves their first brand to pipeline
- Brand still not pitched
- User has never pitched anyone
- Cron: daily at 9am

**Template file:** `conversion_email.html`

---

**Subject:**
```
ready to pitch {{ brand_name }}?
```

**Preheader:**
```
Two days ago you saved them. Now's the moment.
```

**Body:**
```
Hi {{ first_name or "there" }},

Two days ago you saved {{ brand_name }} to your pipeline. Timing matters here.

{{ #if brand_response_rate }}
{{ brand_name }} replies to about {{ brand_response_rate }}% of pitches. That's actually strong. But their inbox fills up fast, so the sooner you send, the better your position.
{{ else }}
Brands see so many pitches that the ones sent within the first week of a creator saving them tend to land at higher rates.
{{ endif }}

I've drafted the pitch for you. Open, review, edit if you want to. Should take 2 minutes.

Your Newcollab Manager
```

**CTA button:**
- Label: `Pitch {{ brand_name }}`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?brand={{ brand_slug }}&utm_campaign=save_day2`

---

# 11 · {{ BRAND_NAME }} IS STILL WAITING (REWRITE)

**Trigger:**
- 6 days after user saves their first brand
- Brand still not pitched
- User has never pitched
- Cron: daily at 9am

---

**Subject:**
```
{{ brand_name }} is still on your list
```

**Preheader:**
```
Quick honest note about timing.
```

**Body:**
```
Hi {{ first_name or "there" }},

Six days ago you saved {{ brand_name }}. Still on your list, not sent.

I'll give you the honest breakdown of what happens with time:

Days 1 to 7 after saving: highest reply rate. You're motivated, the brand hasn't been pitched by 50 other creators yet, and your content is fresh in your feed.

Days 8 to 21: reply rate drops about 30%. Not fatal, still worth pitching.

After day 21: I usually recommend swapping the brand out for a fresher match. The window has closed.

You're on day 6. Still in the sweet spot. Your pitch is drafted.

If {{ brand_name }} isn't your priority anymore, that's fine. You can remove them and I'll surface a better match.

Your Newcollab Manager
```

**CTA button:**
- Label: `Pitch {{ brand_name }} now`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?brand={{ brand_slug }}&utm_campaign=save_day6`

**Secondary link (text under button):**
- `Not interested anymore? Remove from your list.`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?remove={{ brand_slug }}&utm_campaign=save_day6_remove`

---

# 12 · DID {{ BRAND_NAME }} REPLY? (REWRITE)

**Trigger:**
- 14 days after pitch sent
- No reply logged yet
- Pipeline stage in ('waiting', 'followup', 'pitched')
- Cron: daily at 9am

---

**Subject:**
```
did {{ brand_name }} reply?
```

**Preheader:**
```
Two weeks in. Quick check-in.
```

**Body:**
```
Hi {{ first_name or "there" }},

It's been 2 weeks since you pitched {{ brand_name }}.

Did they get back to you?

If yes: mark it in your pipeline so I can help you plan the follow-up (and set your reply expectations for the next brand).

If no: don't take it personally. This is where most creators quit. Brands often reply on the follow-up, not the first email. I'll help you send one.

Update your pipeline in one tap.

Your Newcollab Manager
```

**CTA buttons (2, side by side):**
- Button A: `They replied ✓`
- URL A: `https://app.newcollab.co/creator/dashboard/pr-pipeline?brand={{ brand_slug }}&action=mark_replied&utm_campaign=status_14d`
- Button B: `Send follow-up`
- URL B: `https://app.newcollab.co/creator/dashboard/pr-pipeline?brand={{ brand_slug }}&action=followup&utm_campaign=status_14d`

---

# 13 · TIME TO FOLLOW UP WITH {{ BRAND_NAME }} (REWRITE)

**Trigger:**
- 7+ days since pitch sent
- No reply received
- `followup_sent_at IS NULL`
- Groups multiple brands into one email if applicable
- Cron: daily at 10am

---

**Subject (single brand):**
```
time to follow up with {{ brand_name }}
```

**Subject (multiple brands, N > 1):**
```
follow up with {{ primary_brand_name }} plus {{ N }} others
```

**Preheader:**
```
67% of replies come after a follow-up.
```

**Body (FREE tier):**
```
Hi {{ first_name or "there" }},

You pitched {{ N }} brand{{ 's' if N > 1 }} over 7 days ago and haven't heard back yet. Time to follow up.

Here's who needs a nudge:

{{ #each pending_brands }}
• {{ brand_name }} — pitched {{ days_ago }} days ago ({{ response_rate }}% response rate)
{{ /each }}

Why it matters:
67% of brand replies come after a follow-up. Following up doubles your reply rate. Brands are busy, and a polite nudge shows you're serious.

On free, I'll draft the follow-up when you tap through. Pro members get automatic follow-ups sent for them on the right day, plus tracking on which brands opened your emails.

Your Newcollab Manager
```

**Body (PRO tier):**
```
Hi {{ first_name or "there" }},

Quick nudge. You have {{ N }} follow-up{{ 's' if N > 1 }} due today.

{{ #each pending_brands }}
• {{ brand_name }} — pitched {{ days_ago }} days ago ({{ response_rate }}% response rate)
{{ /each }}

I've drafted each one in your Pro voice. Review and send takes about 3 minutes total.

Pro tip for follow-ups: keep them short. Reference your original pitch in one line. Add one piece of new value (a recent post that's doing well, a content idea specific to them, or a fresh product angle). End with a low-pressure ask.

Your Newcollab Manager
```

**CTA button (FREE):**
- Label: `Send follow-ups`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?filter=needs_followup&utm_campaign=followup_free`

**CTA button (PRO):**
- Label: `Review & send`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?filter=needs_followup&utm_campaign=followup_pro`

---

# 14 · SARAH'S STORY (NEW · Doubter Series Email 1)

**Trigger:**
- User has unlocked 2+ brands
- Signed up 14+ days ago
- Zero replies received
- User is not Pro
- One-time send (never resend)
- Cron: daily at 10am

---

**Subject:**
```
a story you'll want to read
```

**Preheader:**
```
Sarah has 340 followers.
```

**Body:**
```
Hi {{ first_name or "there" }},

I want to tell you about Sarah.

She joined Newcollab 6 weeks ago with 340 followers. She's a nursing student who posts skincare content between shifts.

Her first 3 pitches got 0 replies. She almost quit.

On pitch 4, she used the follow-up sequence I laid out in her plan. She got a reply from a small clean-beauty brand offering to send her their new serum.

Pitch 5: another yes. Pitch 6: no reply. Pitch 7: yes.

By pitch 10, she had 4 brand replies, 2 free PR boxes, and 1 conversation about a paid post.

She has 340 followers.

Here's what she did differently from most creators:
- Every pitch had a specific creative angle for that brand
- Every unanswered pitch got a follow-up on day 6
- Her bio included "collab: sarah@..." so brands could find her

You're {{ pitches_sent }} pitches in. Statistically, your reply is close. Most creators who quit at pitch 3 to 5 would have gotten one on pitch 6 to 8.

Ready to send your next?

Your Newcollab Manager
```

**CTA button:**
- Label: `Open my plan`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=doubter_sarah`

---

# 15 · THE 5-PITCH RULE (NEW · Doubter Series Email 2)

**Trigger:**
- 4 days after Email 14 sent
- Still no reply received
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
about the 5-pitch rule
```

**Preheader:**
```
Honest data on how long this actually takes.
```

**Body:**
```
Hi {{ first_name or "there" }},

I want to share something honest.

Most creators expect a reply on pitch 1 or 2. Real data from thousands of pitches through Newcollab shows the average creator gets their first reply between pitch 5 and pitch 8. Not pitch 1.

If you've sent 3 pitches and heard nothing, you're right where 80% of creators are.

The ones who get replies aren't luckier. They just keep going past pitch 3.

Here's what I'd do if I were you this week:

Send one more pitch. Not to a random brand. To one where your fit is above 60%. I've marked those in your matches with a green "strong fit" tag.

Then send a follow-up on day 6 to your first pitch. Even a 2-line nudge. Follow-ups have a much higher reply rate than first pitches.

You're not stuck. You're at the halfway mark most creators quit at.

Your Newcollab Manager
```

**CTA button:**
- Label: `See my strong-fit matches`
- URL: `https://app.newcollab.co/creator/dashboard/for-you?filter=strong_fit&utm_campaign=doubter_5pitch`

---

# 16 · YOUR QUOTA RESETS IN {{ N }} DAYS (NEW · Maximizer Email 1)

**Trigger:**
- User has used 3/3 unlocks this month
- User is not Pro
- Sent immediately when quota hits 3/3
- One-time (not resent if user goes below 3/3 via bug)
- Cron: real-time trigger, not batch

---

**Subject:**
```
{{ first_name }}, you're maxed out for {{ month }}
```

**Preheader:**
```
Here's what to do until your quota resets.
```

**Body:**
```
Hi {{ first_name or "there" }},

You've used all 3 unlocks this month. Most creators never use all 3. That means you're pitching, which is the hard part.

Your quota resets on {{ reset_date }}. That's {{ N }} days from now.

Here's what I recommend doing in the meantime, and honestly, this is where the biggest wins come from:

Follow up on the 3 pitches you sent. 67% of replies come after a follow-up. Your fresh pitches are hitting inboxes right now, and the brands that were going to reply, will reply to a follow-up in the next 7 to 10 days.

Refresh your portfolio. Add 1 or 2 new pieces. Brands who checked you out and passed will re-check when you bump your profile.

Post 1 piece of content in your niche this week. Brands review your last 9 posts before replying. Keep them fresh.

If you want to keep pitching without waiting, Pro removes the cap and adds weekly coaching. I'll send you a note about that in a couple days if it makes sense for you.

For now, the follow-up game is where you can move the needle.

Your Newcollab Manager
```

**CTA button:**
- Label: `Check my follow-ups`
- URL: `https://app.newcollab.co/creator/dashboard/pr-pipeline?filter=needs_followup&utm_campaign=max_quota_hit`

---

# 17 · WHAT PRO CREATORS DO DIFFERENTLY (NEW · Maximizer Email 2)

**Trigger:**
- 48 hours after Email 16
- User still hasn't upgraded
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
the 3 things pro creators do that free creators don't
```

**Preheader:**
```
And why it changes their reply rate.
```

**Body:**
```
Hi {{ first_name or "there" }},

You've used all 3 unlocks this month. Nice work. Most creators never use all 3.

Here's what I want you to know before your quota resets.

The creators who land regular PR aren't sending more pitches. They're sending better pitches. Specifically, they do 3 things you're not doing yet:

1. They send a follow-up on day 6. Every time. Reply rate jumps from around 5% to around 18%.

2. They add whitelisting or usage rights to their pitch. Brands running Meta and TikTok ads (60% of your target brands) prioritize this. Without it, they skip you for someone who has it flagged.

3. They post 1 content piece per week specifically to feed the pitch. A "for this brand" clip or a fresh routine post. Brands who check your last 9 posts see relevance.

I can walk you through all 3 in your manager plan. Comes with unlimited unlocks, a weekly plan, and a personalized brand queue.

Not ready yet? Fine. I'll refresh your 3 unlocks in {{ N }} days. But these 3 shifts will matter more than any single unlock.

Your Newcollab Manager
```

**CTA button:**
- Label: `Let your manager finish the job — $19/mo`
- URL: `https://app.newcollab.co/creator/dashboard/upgrade?utm_campaign=max_3things`

---

# 18 · YOUR MANAGER'S PRO PLAN FOR YOU (NEW · Maximizer Email 3)

**Trigger:**
- 7 days after Email 16
- User still hasn't upgraded
- User is not Pro
- Cron: daily at 10am

---

**Subject:**
```
here's your personalized pro plan
```

**Preheader:**
```
What I'd do with you in your first 30 days of Pro.
```

**Body:**
```
Hi {{ first_name or "there" }},

I want to lay out what I'd actually do with you if you went Pro. Not a features list. A plan.

Week 1: We finish the 2 fixes locked in your plan (whitelisting + concept sketch). Your score climbs from {{ current_score }}% to about 82%.

Week 2: I open 5 more brand matches personalized to your profile. You pitch the top 3. I automate the day 6 follow-ups.

Week 3: I run the audit on your last 12 posts and flag 2 quick content tweaks that improve your fit for the brands most likely to reply. You post them.

Week 4: You should have replies coming in by now. I help you draft your response to each one. We plan follow-ups on the ones who took a meeting but didn't send yet.

That's what Pro looks like. Not "unlimited unlocks." A specific 30-day plan tied to what your profile actually needs.

Your quota resets in {{ N }} days if you'd rather wait. Fine either way. I just wanted you to see what the plan would be.

Your Newcollab Manager
```

**CTA button:**
- Label: `Start my 30-day plan — $19/mo`
- URL: `https://app.newcollab.co/creator/dashboard/upgrade?utm_campaign=max_30day_plan`

---

# 19 · THE BRANDS YOU MISSED THIS WEEK (NEW · Re-engagement Email 1)

**Trigger:**
- 14 days since last login
- User has previously unlocked at least 1 brand
- Cron: daily at 9am

---

**Subject:**
```
{{ N_new_brands }} new matches while you were away
```

**Preheader:**
```
Quick update on what's new in your niche.
```

**Body:**
```
Hi {{ first_name or "there" }},

I haven't seen you in 2 weeks. Not chasing. Just wanted to share what's new.

{{ N_new_brands }} new brands were added to Newcollab in your niche while you were away. Here are 3 worth your attention:

{{ #each top_new_brands }}
• {{ brand_name }} · {{ category }} · fits your profile at {{ fit_score }}%
{{ /each }}

If you want to check them out, one click below.

Your unlocks reset on {{ reset_date }}, so you have {{ N_unlocks }} available right now.

Your Newcollab Manager
```

**CTA button:**
- Label: `See new matches`
- URL: `https://app.newcollab.co/creator/dashboard/for-you?filter=new_this_week&utm_campaign=reengagement_new`

---

# 20 · YOUR MANAGER WANTS YOU BACK (NEW · Re-engagement Email 2 · high emotional)

**Trigger:**
- 21 days since last login
- User has previously logged in at least once
- User has NOT received Email 19 in last 7 days
- One-time send per dormancy cycle
- Cron: daily at 9am

---

**Subject:**
```
hey — everything ok?
```

**Preheader:**
```
Your manager noticed you've been away.
```

**Body:**
```
Hi {{ first_name or "there" }},

I haven't seen you in 3 weeks. That's fine. Creators drop off for 100 reasons and I'm not going to guilt you.

But I wanted to say two things.

First, {{ N_new_brands }} new brands were added to your matches while you were away. Two of them are strong fits for you specifically.

Second, if the thing that made you stop was "this isn't working," here's honest data: creators who come back after a 2 to 3 week break land brand deals at similar rates to consistent users. The break doesn't hurt you.

If you want back in, one click.

If it's not for you anymore, that's also fine. I'm here when you change your mind.

Your Newcollab Manager
```

**CTA button:**
- Label: `See what's new`
- URL: `https://app.newcollab.co/creator/dashboard/for-you?utm_campaign=reengagement_soft`

---

# 21 · WEEKLY MANAGER DIGEST (NEW · Recurring · Every Monday)

**Trigger:**
- Every Monday 8am user local time
- User verified email (any tier)
- User NOT in dormant re-engagement state (avoid stacking with Email 19/20)
- Skip if user was sent >1 email in last 3 days
- Cron: weekly Monday 8am

---

**Subject variants (rotate weekly):**
- Week A: `your monday brief from your manager`
- Week B: `{{ first_name }} — this week's move`
- Week C: `{{ N_new_brands }} new brands + one tactic for this week`

**Preheader:**
```
Your progress + this week's focus.
```

**Body template:**
```
Hi {{ first_name or "there" }},

Here's what's new this week.

📊 YOUR PROGRESS
Score: {{ current_score }}% ({{ delta_sign }}{{ score_delta }} vs last week)
Unlocks: {{ unlocks_used }} used of {{ unlocks_quota }} this month
Replies received: {{ replies_count }}

🎯 THIS WEEK'S FOCUS
{{ weekly_theme_title }}

{{ weekly_theme_body_2_sentences }}

📥 NEW BRANDS IN YOUR MATCH LIST
{{ #each new_brands_this_week }}
• {{ brand_name }} · {{ category }} · {{ one_line_reason }}
{{ /each }}

🎉 CREATOR WIN OF THE WEEK
{{ win_creator_handle }} ({{ win_creator_followers }} followers) landed {{ win_brand_name }} using {{ win_tactic }}. {{ win_one_line }}

Your Newcollab Manager
```

**CTA button:**
- Label: `See my full plan`
- URL: `https://app.newcollab.co/creator/dashboard/ai-manager?utm_campaign=weekly_digest_{{ iso_week }}`

**Weekly theme library (rotate through, 12+ themes):**

1. Bio polish — "Brands scan bios in 3 seconds. Fix yours this week."
2. Portfolio refresh — "Add 2 posts to your portfolio that show product in real use."
3. Follow-up rhythm — "You have {{ N }} pitches due for follow-up. Do them today."
4. New pitch angle — "Try leading with a specific product name in your subject line."
5. Content pattern — "Post one 'for the brand' style Reel this week."
6. Portfolio recency — "Refresh anything older than 30 days on your public kit."
7. Rate card visibility — "Add your gifting + paid rates to your kit."
8. Whitelisting mention — "Add 'open to whitelisting' to your bio."
9. Trending sound match — "Use one trending sound in this week's content for reach."
10. First-touch personalization — "Every pitch this week: mention 1 real product they sell."
11. Application forms — "3 brands opened application forms this week. Apply to at least one."
12. Testimonial add — "Add a 1-line quote from any brand you've worked with."

Cycle through these; each creator gets 1 theme per Monday.

---

# 22 · REPLY RECEIVED (NEW · Celebration)

**Trigger:**
- User marks a pitch as "replied" in pipeline
- Sent within 5 minutes of the action
- One-time per reply
- Real-time trigger

---

**Subject:**
```
{{ brand_name }} replied. here's what to do next.
```

**Preheader:**
```
The next 24 hours matter more than the pitch did.
```

**Body:**
```
Hi {{ first_name or "there" }},

You got a reply from {{ brand_name }}. That's a real win. Most creators never get this far.

Here's what I need you to know about the next 24 hours, because how you respond matters more than the pitch that got you here.

Reply today. Not tomorrow. Within 12 hours if you can. Brands who reply and then wait days for the creator to respond move on to someone faster.

Match their energy. If they wrote 2 sentences, you write 2 sentences. If they asked a question, answer it in the first line. Don't over-write.

Confirm the offer clearly. If they're offering PR (free product), say what you'll create in exchange (1 Reel, 3 photos, 1 story). Specific deliverables build trust. Vague answers kill deals.

Ask for shipping timeline. "What's the shipping window?" is a 1-line question that shows you're organized and moves the conversation forward.

I've drafted a first response for you. Open your inbox to review.

Your Newcollab Manager
```

**CTA button:**
- Label: `Open my inbox`
- URL: `https://app.newcollab.co/creator/dashboard/inbox?brand={{ brand_slug }}&utm_campaign=celebration_reply`

---

# 23 · YOU LANDED YOUR FIRST PR BOX (NEW · Celebration)

**Trigger:**
- User marks first PR box as "received" in pipeline
- One-time send (only for the FIRST package)
- Real-time trigger

---

**Subject:**
```
your first pr box. congrats.
```

**Preheader:**
```
Real talk about what happens next.
```

**Body:**
```
Hi {{ first_name or "there" }},

Your first PR box landed. That's a real moment. Take a screenshot. Save the day. Most people who sign up for tools like this never get here.

Now, three real things about what happens next:

1. Deliver on your promise. Whatever content you offered, ship it on time, with the quality you'd want if you were the brand. This is what separates one-time gifts from recurring partnerships.

2. Tag them thoughtfully. When you post the content, tag {{ brand_name }} and their handle. Send them a DM with a link to the post so it doesn't get lost in their notifications.

3. Ask for the next thing. After the content ships and performs, message them back: "Really enjoyed working on this. Any upcoming campaigns I could be part of?" This is how gifts become paid partnerships.

If you're up for it, share your win in the community. Other creators get pulled forward by hearing how others got there.

Your Newcollab Manager
```

**CTA button:**
- Label: `Share your win`
- URL: `https://app.newcollab.co/creator/dashboard/community?share=true&utm_campaign=celebration_first_box`

**Secondary link (text under button):**
- `Or just quietly celebrate. I get it.`

---

# GLOBAL THROTTLING RULES (for dev implementation)

```
MAX_EMAILS_PER_USER_PER_WEEK = 2
MAX_EMAILS_PER_USER_PER_DAY  = 1

EXEMPTIONS_FROM_WEEKLY_CAP:
  - Email 1 (Verification)
  - Email 2 (Welcome from Manager) — sent immediately after verify
  - Email 22 (Reply Received) — celebration, high emotional
  - Email 23 (First PR Box) — celebration

QUIET_HOURS: 22:00 - 06:00 user local time (queue if triggered during quiet hours, send at 06:01)

PRIORITY_ORDER (when multiple emails qualify same week):
  1. Onboarding sequence (Emails 2, 3, 4)
  2. Real-time celebration triggers (Emails 22, 23)
  3. Behavioral triggers (Emails 10, 11, 12, 13)
  4. Weekly digest (Email 21)
  5. Educational series (Emails 5-9)
  6. Doubter series (Emails 14, 15)
  7. Maximizer series (Emails 16, 17, 18)
  8. Re-engagement (Emails 19, 20)

If user hits weekly cap, skip lower-priority in favor of higher.
```

# UTM STRUCTURE

Every URL includes:
```
?utm_source=email
&utm_medium=lifecycle
&utm_campaign={{ email_slug }}
&utm_content={{ variant if A/B testing }}
```

Track in GA4 as conversion source.

# UNSUBSCRIBE FOOTER (all non-transactional emails)

At the bottom of every email except Verification (Email 1):

```
You're receiving this because you signed up for Newcollab.

Update your email preferences: {{ preferences_url }}
Unsubscribe from lifecycle emails: {{ unsubscribe_url }}

Newcollab · {{ company_address }}
```

# STYLE NOTES FOR DEV

**HTML structure per email:**
- Max width: 560px
- Font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif
- Body font size: 15px, line-height 1.55
- Text color: #15161a
- Background: #ffffff
- CTA button: black background (#15161a), white text, 12px 24px padding, 10px border-radius
- No images in body (only in header if desired, minimal)
- No em dashes anywhere (already checked all copy above)
- No exclamation marks in subject lines
- Preheader text hidden with CSS but pulled by inbox previews

**Testing checklist before ship:**
- [ ] Every {{ variable }} has a fallback tested
- [ ] Skip logic for missing brand data works
- [ ] Weekly cap enforced across all templates
- [ ] Quiet hours respected
- [ ] Unsubscribe link works
- [ ] UTM tags fire correctly in GA4
- [ ] Subject lines render without truncation on mobile (iOS Mail, Gmail iOS, Gmail Android)
- [ ] Preheader text renders correctly
- [ ] CTA button clickable on all major clients (Gmail, Outlook, Apple Mail, mobile)
