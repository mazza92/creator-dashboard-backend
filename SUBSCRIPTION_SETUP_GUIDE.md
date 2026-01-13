# Subscription System Setup Guide

This guide will help you set up the Stripe subscription system for the Creator PR CRM.

## 1. Create Stripe Products (5 minutes)

### Option A: Using Stripe Dashboard (Recommended)

1. **Go to Stripe Dashboard**: https://dashboard.stripe.com
2. **Navigate to Products**: Click "Products" in the left sidebar
3. **Create Pro Product**:
   - Click "+ Add product"
   - Name: "Creator Pro"
   - Description: "Unlimited brand saves, pitches, and premium features"
   - Pricing: $19.00 USD
   - Billing period: Monthly
   - Click "Save product"
   - **Copy the Price ID** (starts with `price_...`)

4. **Create Elite Product**:
   - Click "+ Add product"
   - Name: "Creator Elite"
   - Description: "Everything in Pro plus AI pitch writing and personal coaching"
   - Pricing: $49.00 USD
   - Billing period: Monthly
   - Click "Save product"
   - **Copy the Price ID** (starts with `price_...`)

### Option B: Using Stripe CLI

```bash
# Create Pro product
stripe products create \
  --name="Creator Pro" \
  --description="Unlimited brand saves, pitches, and premium features"

# Create Pro price
stripe prices create \
  --product=prod_xxx \
  --unit-amount=1900 \
  --currency=usd \
  --recurring[interval]=month

# Create Elite product
stripe products create \
  --name="Creator Elite" \
  --description="Everything in Pro plus AI and coaching"

# Create Elite price
stripe prices create \
  --product=prod_yyy \
  --unit-amount=4900 \
  --currency=usd \
  --recurring[interval]=month
```

## 2. Configure Environment Variables

Add these to your `.env` file:

```bash
# Stripe Subscription Configuration
STRIPE_PRICE_ID_PRO=price_xxxxxxxxxxxxx        # From step 1
STRIPE_PRICE_ID_ELITE=price_xxxxxxxxxxxxx      # From step 1

# Stripe Webhook Secret (will configure in step 3)
STRIPE_WEBHOOK_SECRET_SUBSCRIPTION=whsec_xxxxxxxxxxxxx

# Frontend URL (for redirect after checkout)
FRONTEND_URL=http://localhost:3000             # Change to your production URL
```

**Note**: You already have `STRIPE_SECRET_KEY` from your existing Stripe Connect setup. The same key works for subscriptions!

## 3. Set Up Stripe Webhook

### Development (Local Testing with Stripe CLI)

1. **Install Stripe CLI**: https://stripe.com/docs/stripe-cli
2. **Login to Stripe**:
   ```bash
   stripe login
   ```

3. **Forward webhooks to your local server**:
   ```bash
   stripe listen --forward-to localhost:5000/api/subscription/webhook
   ```

4. **Copy the webhook secret** (starts with `whsec_`) and add to `.env`:
   ```bash
   STRIPE_WEBHOOK_SECRET_SUBSCRIPTION=whsec_xxxxxxxxxxxxx
   ```

### Production (Vercel/Server)

1. **Go to Stripe Dashboard**: https://dashboard.stripe.com/webhooks
2. **Click "+ Add endpoint"**
3. **Endpoint URL**: `https://yourdomain.com/api/subscription/webhook`
4. **Events to send**: Select these events:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `customer.subscription.updated`
5. **Click "Add endpoint"**
6. **Reveal webhook signing secret** and add to `.env`:
   ```bash
   STRIPE_WEBHOOK_SECRET_SUBSCRIPTION=whsec_xxxxxxxxxxxxx
   ```

## 4. Database Schema (Already Done!)

Your database already has the necessary columns from the PR CRM migration:

```sql
-- These columns already exist in creators table:
subscription_tier VARCHAR(50) DEFAULT 'free'
subscription_status VARCHAR(50) DEFAULT 'inactive'
stripe_subscription_id VARCHAR(255)
stripe_customer_id VARCHAR(255)
subscription_started_at TIMESTAMP
subscription_ends_at TIMESTAMP
brands_saved_count INT DEFAULT 0
pitches_sent_this_month INT DEFAULT 0
```

✅ No additional migration needed!

## 5. Test the Flow

### Test in Development Mode

1. **Start your backend**:
   ```bash
   python app.py
   ```

2. **Start Stripe webhook forwarding** (in another terminal):
   ```bash
   stripe listen --forward-to localhost:5000/api/subscription/webhook
   ```

3. **Start your frontend**:
   ```bash
   npm start
   ```

4. **Test the upgrade flow**:
   - Go to Discover page
   - Save 5 brands (free limit)
   - On the 6th save, upgrade modal should appear
   - Click "Upgrade to Pro"
   - Use test card: `4242 4242 4242 4242`
   - Expiry: Any future date
   - CVC: Any 3 digits
   - Zip: Any 5 digits

5. **Verify**:
   - Check database: `subscription_tier` should be 'pro'
   - Try saving more brands → should work without limit
   - Check Stripe Dashboard → subscription should appear

## 6. Go Live

1. **Switch to Live Mode** in Stripe Dashboard (toggle in top-left)

2. **Create LIVE products**:
   - Repeat step 1 in Live mode
   - Get new LIVE price IDs

3. **Update `.env` with LIVE credentials**:
   ```bash
   STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxxx
   STRIPE_PRICE_ID_PRO=price_xxxxxxxxxxxxx         # LIVE price ID
   STRIPE_PRICE_ID_ELITE=price_xxxxxxxxxxxxx       # LIVE price ID
   STRIPE_WEBHOOK_SECRET_SUBSCRIPTION=whsec_xxxxxxxxxxxxx  # LIVE webhook secret
   FRONTEND_URL=https://your-production-domain.com
   ```

4. **Set up LIVE webhook**:
   - Follow Production steps in Step 3
   - Use your production URL

5. **Deploy to production**:
   - Deploy backend with new `.env` variables
   - Deploy frontend (no changes needed)

## 7. Monitoring & Management

### Check Subscription Status

```bash
curl -X GET http://localhost:5000/api/subscription/status \
  -H "Cookie: session=..." \
  -H "Content-Type: application/json"
```

### Stripe Customer Portal

Users can manage their subscription at:
```
POST /api/subscription/portal
```

This creates a session where users can:
- Update payment method
- Cancel subscription
- View invoices
- Update billing info

## 8. Usage Limits

Current limits are defined in `subscription_routes.py`:

```python
FREE_BRAND_LIMIT = 5      # Free tier: 5 brands saved
FREE_PITCH_LIMIT = 3      # Free tier: 3 pitches per month
```

To change limits, edit these values and restart the server.

## 9. Troubleshooting

### "Price ID not configured" error
- Make sure `STRIPE_PRICE_ID_PRO` and `STRIPE_PRICE_ID_ELITE` are set in `.env`
- Restart your Flask server after changing `.env`

### Webhook not working
- Check webhook secret is correct
- In development, make sure `stripe listen` is running
- Check Flask logs for webhook errors

### Upgrade modal not showing
- Check browser console for JavaScript errors
- Verify backend is returning 403 with `upgrade_required: true`
- Check network tab in DevTools

### Subscription not activating
- Check Stripe webhook logs in Dashboard
- Verify webhook endpoint is accessible
- Check Flask logs for database errors

## 10. Next Steps

After basic subscription is working:

1. **Add usage indicators**: Show "X/5 brands saved" in UI
2. **Add pricing page**: Dedicated page explaining tiers
3. **Add success page**: `/creator/dashboard/subscription/success`
4. **Email notifications**: Welcome email on Pro upgrade
5. **Analytics**: Track conversion rates, churn, MRR

## Support

- Stripe Documentation: https://stripe.com/docs/billing/subscriptions/overview
- Stripe Testing: https://stripe.com/docs/testing
- This codebase: `subscription_routes.py` and `UpgradeModal.js`
