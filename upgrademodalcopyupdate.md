# UpgradeModal — Copy Update
**File:** `src/creator-portal/UpgradeModal.js`

---

## 1. Title

**Find:**
```
"You've Used Your Free Pitches!"
```
**Replace with:**
```
"You're on a roll. Keep going."
```

---

## 2. Subtitle

**Find:**
```
`You've sent ${currentCount} brand pitches this week.`
```
**Replace with:**
```
`You've sent ${currentCount} pitches this month. Don't stop now.`
```

**Find:**
```
`You have ${limit - currentCount} free pitches left this week.`
```
**Replace with:**
```
`You have ${limit - currentCount} free pitches left this month.`
```

---

## 3. Remove reset banner

**Delete this block entirely:**
```
{currentCount >= limit && (
  <DailyResetNote>
    ⏰ Resets next month, or upgrade now for unlimited pitches!
  </DailyResetNote>
)}
```

## 4. OK
---

## 5. Remove "Impulse Buy Pricing" badge

**Delete this line entirely:**
```
<Badge>Impulse Buy Pricing</Badge>
```

---

## 6. Price tagline

**Find:**
```
Less than a Spotify subscription!
```
**Replace with:**
```
One gifted package covers your Pro for the year.
```

---

## 7. Feature 1

**Find:**
```
<strong>Unlimited Brand Pitches</strong> (No weekly limits!)
```
**Replace with:**
```
<strong>Unlimited pitches.</strong> Every brand, every month.
```

---

## 8. Feature 2

**Find:**
```
Personalized emails that brands actually read
```
**Replace with:**
```
Custom outreach emails written for each brand
```

---

## 9. Feature 3

**Find:**
```
Direct access to PR manager emails
```
**Replace with:**
```
Direct PR manager contacts for 500+ brands
```

---

## 10. Feature 4

**Find:**
```
Pitch templates proven to get responses
```
**Replace with:**
```
Your media kit and portfolio in one shareable link
```

---

## 11. Feature 5

**Find:**
```
Priority support
```
**Replace with:**
```
Personal creator assistant for follow-ups and deal negotiations
```

---

## 12. Value prop box

**Find:**
```
💡 One PR package could get you $500 worth of free products. $12/month is a steal!
```
**Replace with:**
```
💡 Most creators land their first gifted package within 30 days. That covers your $12 back in product.
```

---

## 13. CTA button

**Find:**
```
'Upgrade Now for $12/month'
```
**Replace with:**
```
'Unlock Pro for $12/month'
```
