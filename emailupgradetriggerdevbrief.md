# Email — Upgrade Trigger (Quota Hit)
**Template:** `/home/user/email-upgrade-trigger.html` (Jinja2)  
**Trigger:** User sends their 3rd free pitch of the month  
**Goal:** Convert free → Pro by showing their real pitch statuses + locked next actions

---

## 1. When to Send

**Do NOT fire immediately.** Wait 7 days after the 3rd pitch so status cards show "Follow up due" (amber) rather than "Waiting" (blue). Amber cards create urgency — the user sees overdue actions they can't take without Pro.

On the 3rd pitch, **schedule** the email for 7 days later. Run a daily cron that picks up pending sends.

```python
# In the route that handles pitch creation (e.g. POST /api/pitch)
# After saving the pitch to DB:

from datetime import datetime, timezone, timedelta

def check_and_schedule_quota_email(user_id):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    pitch_count = db.session.query(Pitch).filter(
        Pitch.user_id == user_id,
        Pitch.created_at >= month_start,
        Pitch.deleted_at.is_(None)
    ).count()

    if pitch_count == 3:
        user = db.session.get(User, user_id)
        if not user.is_pro and user.quota_email_send_at is None:
            user.quota_email_send_at = now + timedelta(days=7)
            db.session.commit()
```

```python
# Daily cron job (run once per day, e.g. via APScheduler or Celery beat)

def cron_send_pending_quota_emails():
    now = datetime.now(timezone.utc)
    current_month = now.strftime('%Y-%m')

    pending_users = db.session.query(User).filter(
        User.quota_email_send_at <= now,
        User.quota_email_send_at.isnot(None),
        User.quota_email_sent_month != current_month,
        User.is_pro == False,
    ).all()

    for user in pending_users:
        send_quota_upgrade_email(user.id)
        user.quota_email_send_at = None  # clear scheduled send
```

**New DB column required:**
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_email_send_at TIMESTAMPTZ;
```

**Why 7 days?** Most brands take 5–10 days to reply. At day 7, all 3 pitch cards will show "Follow up due" (amber) — a visible, locked action the user can't complete without Pro. Sending at day 0, all cards are blue "Waiting" which reads as passive and reduces urgency.

**De-duplication:** Add a `quota_email_sent_month` column (VARCHAR `YYYY-MM`) to the `users` table. Only send if `quota_email_sent_month != current_month`. Update it after sending.

```sql
ALTER TABLE users ADD COLUMN quota_email_sent_month VARCHAR(7);
```

```python
def send_quota_upgrade_email(user_id):
    current_month = datetime.now().strftime('%Y-%m')
    user = db.session.get(User, user_id)

    if user.quota_email_sent_month == current_month:
        return  # already sent this month

    # ... build context and send ...

    user.quota_email_sent_month = current_month
    db.session.commit()
```

---

## 2. Data to Pull for Template

```python
def build_quota_email_context(user_id):
    user = db.session.get(User, user_id)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── User's pitches this month (up to 3) ──────────────────
    pitches_raw = db.session.query(Pitch).join(Brand).filter(
        Pitch.user_id == user_id,
        Pitch.created_at >= month_start,
        Pitch.deleted_at.is_(None)
    ).order_by(Pitch.created_at.asc()).limit(3).all()  # oldest first = most urgent card leads

    pitches = []
    for p in pitches_raw:
        days_since = (now - p.created_at).days
        status, badge_style, next_action_icon, next_action_copy, next_action_sub = \
            resolve_pitch_display(p, days_since)

        pitches.append({
            'brand_name':       p.brand.name,
            'brand_initial':    p.brand.name[:2].upper(),
            'brand_color':      p.brand.logo_color or '#4A4A4A',
            'category':         p.brand.category,
            'days_since':       days_since,
            'status':           status,
            'badge_style':      badge_style,  # inline CSS string for badge
            'next_action_icon': next_action_icon,
            'next_action_copy': next_action_copy,
            'next_action_sub':  next_action_sub,
        })

    # ── For You teaser brands (not yet pitched by this user) ─
    pitched_brand_ids = [p.brand_id for p in pitches_raw]
    user_niche = (user.niches or [''])[0]  # primary niche

    teaser_brands_raw = db.session.query(Brand).filter(
        Brand.id.notin_(pitched_brand_ids),
        Brand.niches.contains([user_niche]),
        Brand.reply_rate >= 30,
        Brand.is_active == True
    ).order_by(Brand.reply_rate.desc()).limit(2).all()

    teaser_brands = [{
        'name':         b.name,
        'initial':      b.name[:2].upper(),
        'logo_color':   b.logo_color or '#4A4A4A',
        'reply_rate':   b.reply_rate,
        'hot_label':    'responding now' if b.reply_rate >= 45 else 'hot this week',
    } for b in teaser_brands_raw]

    # ── Niche label ───────────────────────────────────────────
    niche_label = user_niche.title() if user_niche else 'your niche'

    # ── Upgrade URL with UTM ──────────────────────────────────
    upgrade_url = (
        f"https://newcollab.co/register?plan=pro"
        f"&ref=quota_email&user_id={user.id}"
        f"&utm_source=email&utm_medium=trigger&utm_campaign=quota_hit"
    )

    return {
        'user':          {'first_name': user.first_name or 'there'},
        'pitches':       pitches,
        'teaser_brands': teaser_brands,
        'niche_label':   niche_label,
        'upgrade_url':   upgrade_url,
        'unsubscribe_url': f"https://newcollab.co/unsubscribe?token={user.unsubscribe_token}",
    }
```

---

## 3. Pitch Status + Next Action Logic

```python
def resolve_pitch_display(pitch, days_since):
    """
    Returns (status, badge_style, next_action_icon, next_action_copy, next_action_sub)
    based on pitch.status and days since sent.
    """

    # ── Replied ──────────────────────────────────────────────
    if pitch.status == 'replied':
        return (
            'replied',
            'background:#ECFDF5;color:#059669',           # green badge
            '🔒',
            'Log reply + track PR value in your dashboard',
            'Package confirmed = log the value and build your collab history',
        )

    # ── Won / Package coming ──────────────────────────────────
    if pitch.status == 'won':
        return (
            'won',
            'background:#FFF1F3;color:#E11D48',
            '🔒',
            'Mark package received + log $PR value',
            'Track the value of every deal you land — all in one place',
        )

    # ── Waiting — follow up overdue (>= 7 days) ──────────────
    if days_since >= 7:
        return (
            'follow_up_due',
            'background:#FFFBEB;color:#92400E',           # amber badge
            '🔒',
            f'Send follow-up email — custom drafted for this brand, ready to send',
            f'"Hi [brand], just following up on my pitch from last week..."',
        )

    # ── Waiting — follow up not yet due (< 7 days) ───────────
    days_until_followup = 7 - days_since
    return (
        'waiting',
        'background:#EFF6FF;color:#2563EB',               # blue badge
        '🔒',
        f'Follow-up reminder in {days_until_followup} day{"s" if days_until_followup != 1 else ""} — we\'ll draft it for you',
        'Most brands reply after the second touch. Yours will be ready.',
    )
```

---

## 4. Sending the Email

Use your existing email provider (SendGrid / Resend / SMTP). Render the Jinja2 template server-side:

```python
from flask import render_template
from your_email_module import send_email   # your existing email helper

def send_quota_upgrade_email(user_id):
    current_month = datetime.now().strftime('%Y-%m')
    user = db.session.get(User, user_id)

    if user.quota_email_sent_month == current_month:
        return

    ctx = build_quota_email_context(user_id)

    html_body = render_template('emails/quota_upgrade.html', **ctx)

    send_email(
        to=user.email,
        subject=f"{ctx['user']['first_name']}, your 3 free pitches are out — here's what's next",
        html=html_body,
        from_name='The Newcollab team',
        from_email='team@newcollab.co',
        reply_to='team@newcollab.co',
    )

    user.quota_email_sent_month = current_month
    db.session.commit()
```

**Template file location:**  
Copy `email-upgrade-trigger.html` to `templates/emails/quota_upgrade.html` in your Flask app.

---

## 5. Subject Line Options (A/B test these)

| Variant | Subject line |
|---|---|
| A (current best) | `[first_name], your 3 free pitches are out — here's what's next` |
| B | `Rhode Skin hasn't replied yet — here's what to do` *(uses first brand name)* |
| C | `your follow-up email is ready — you just can't send it yet` |
| D | `3 pitches out. 2 locked brands waiting. Here's the plan.` |

Variant B and C will outperform A/D if your email provider supports dynamic subject lines via merge tags.

For variant B:
```python
first_pitch_brand = pitches[0]['brand_name'] if pitches else 'your brand'
subject = f"{first_pitch_brand} hasn't replied yet — here's what to do"
```

---

## 6. DB Changes Required

```sql
-- De-duplication column
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS quota_email_sent_month VARCHAR(7);

-- Scheduled send time (set to +7 days on 3rd pitch, cleared after send)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS quota_email_send_at TIMESTAMPTZ;

-- Brand logo color
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS logo_color VARCHAR(7) DEFAULT '#4A4A4A';
```

**Populate `logo_color` for top brands** (add to your brand seed/admin):

| Brand | logo_color |
|---|---|
| Rhode Skin | #B5002D |
| Anua | #2E7D4F |
| Oh Polly | #1A1A2E |
| Fenty Beauty | #C8102E |
| Glow Recipe | #E85D75 |
| Princess Polly | #6D28D9 |
| Nopalera | #4A7C59 |
| Aura Bora | #1D4ED8 |

---

## 7. Email Client Compatibility Notes

The template uses **table-based layout** and **inline styles** for maximum compatibility.

- Gmail (web + app): fully supported
- Apple Mail: fully supported
- Outlook 2016+: supported (no CSS Grid/Flex used)
- Dark mode: the `#0F0F0F` header and Pro block render correctly in dark mode

**Do not** add `display:flex`, CSS Grid, or external stylesheets to this template.  
**Do** inline all critical styles — the `<style>` block in `<head>` is only for the dev preview wrapper and resets.

If using SendGrid, use their Inliner tool or `css_inliner` Python package before sending.

---

## 8. Quick Test Checklist

- [ ] Email fires only on the 3rd pitch (not the 1st or 2nd)
- [ ] Email does not fire if user is already Pro
- [ ] Email does not fire twice in the same calendar month
- [ ] Pitch cards show real brand names, days, and statuses
- [ ] Teaser brands are not brands the user has already pitched
- [ ] Upgrade URL includes `user_id` and UTM params
- [ ] Unsubscribe link works
- [ ] Subject line is personalised with first name
- [ ] Reply-to is set to founder email (not noreply)
- [ ] Test in Gmail + Apple Mail before shipping
