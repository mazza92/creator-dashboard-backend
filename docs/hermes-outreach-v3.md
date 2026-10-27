# Hermes brand outreach v3: scan-led

Replaces the "free PR campaign, zero fee" mission. The goal is **paid brands** ($299/mo Pro). The first gifted campaign is free; say so, and say Pro exists.

Every email carries real numbers from a verified competitor's Brand Scan. Hermes never writes numbers, competitor names or claims itself. The backend renders every email.

## The offer (what you can say)
- See any brand's TikTok creators, the hooks they reuse and their top videos: free, at newcollab.co/brands/scan.
- Your first gifted campaign is free: you pick the creators, ship product, and get the videos back to run as ads.
- Newcollab Pro is $299/mo: a new campaign every month, unlimited Brand Scans, the full applicant list.

## Safety rules
- **Test mode is the default.** Until `OUTREACH_V3_LIVE=1` is set on the backend, every send goes to `team@newcollab.co` (`OUTREACH_TEST_EMAIL`), whatever you ask. The subject reads `[TEST to <real recipient>]` and a QA footer lists every number with its source.
- **Test before live.** With `OUTREACH_V3_REQUIRE_TEST=1` (the default), a live send is refused until that same brand has had a test send.
- If `prepare` returns `no_evidence`, skip the brand. Never fall back to a generic email in v3.
- Numbers are re-measured at send time. If the competitor no longer passes the checks, the send is refused.

## How a competitor is chosen
1. Gemini, with Google Search when available, proposes direct competitors: same kind of product, same customer, similar price. Brands the radar sees seeding creators in the niche are given as hints only.
2. Each candidate's Brand Scan must show at least 5 creators and 5 videos, and at least 50% of matches must @mention or hashtag the brand. This rules out namesakes found only through a caption word.
3. A second, strict model call reads the candidate's real captions and confirms both that the videos are about that brand and that it sells the same kind of product (confidence 0.7 or higher).
4. The pick is the passing candidate with the most creators. Every rejected candidate keeps its reason.

## Daily loop (admin token, `X-Admin-Token`)
1. `GET /api/admin/brand-pipeline/queue/new?limit=20`: prospects (same queue as v2).
2. Find contacts as before: `POST /api/admin/brand-pipeline/contact`.
3. `POST /api/admin/outreach-v3/prepare` `{"brand_ids": [..up to 10]}`. Statuses:
   - `ready`: verified competitor, email can render.
   - `scanning`: a TikTok scan is running. Call prepare again in 1 to 2 minutes; scans give up after 12 minutes.
   - `no_evidence`: skip the brand. `tried` says why.
4. `GET /api/admin/outreach-v3/preview/<brand_id>?step=0`: the email text plus `facts` (claim, value, source link).
5. `POST /api/admin/outreach-v3/send` `{"brand_id": id, "step": 0, "test": true}`: the test lands in team@newcollab.co.
6. Once live is on: `{"brand_id": id, "step": 0, "test": false}`.
7. Follow-ups: `GET /api/admin/outreach-v3/queue/followups` gives `{brand_id, step}` items. Send each with `{"brand_id", "step", "test": false}`.
   - Step 1 (day 3): their own creators, only if their scan has evidence. Otherwise the free-campaign email.
   - Step 2 (day 7): the free campaign, with Pro at $299/mo stated.
   - Step 3 (day 14): the last email, with the booking link.
8. Replies still go through `POST /api/admin/brand-pipeline/inbound`. v3 replies are held as drafts for Mazza.
9. `GET /api/admin/outreach-v3/report`: prepare statuses, tests sent, and the funnel (contacted, replied, scan_open, signed_up, campaign, paid) with MRR.

## Attribution
Links carry `oref=<ref_code>`:
- `scan_open`: the scan page is opened.
- `signed_up`: a workspace signup (the code is kept in the `nc_oref` cookie for 30 days).
- `campaign`: the first campaign.
- `paid`: the Stripe subscription.

Test sends use `oref=test-...` and are never credited.

## Costs
Competitor scans come from the store first. An Apify scan starts only for the best 2 unproven candidates, and its results are shared across prospects for 3 days. All scans count toward `BRAND_SCAN_DAILY_CAP_USD`.
