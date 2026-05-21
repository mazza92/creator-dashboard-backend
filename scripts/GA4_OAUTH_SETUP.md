# GA4 OAuth setup (when gcloud says "This app is blocked")

Google blocks gcloud's shared OAuth app on some accounts. Use **your own** OAuth client in project `auth-app-feed3`.

## 1. OAuth consent screen (~3 min)

1. Open: https://console.cloud.google.com/apis/credentials/consent?project=auth-app-feed3
2. **User type**: External (or Internal if you use Google Workspace for @newcollab.co only)
3. App name: `NewCollab Admin`
4. User support email: your email
5. **Scopes** → Add:
   - `.../auth/analytics.readonly`
   - `.../auth/analytics.manage.users`
6. **Test users** (required while app is in "Testing"): add the Gmail you sign in with (GA4 admin)
7. Save

## 2. Desktop OAuth client (~2 min)

1. Open: https://console.cloud.google.com/apis/credentials?project=auth-app-feed3
2. **+ Create credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `NewCollab GA4 local`
5. **Download JSON**
6. Save as:
   ```
   creator_dashboard/secrets/gcp-oauth-client.json
   ```

## 3. Enable APIs

- [Analytics Data API](https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com?project=auth-app-feed3)
- [Analytics Admin API](https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com?project=auth-app-feed3)

## 4. Sign in (your app, not gcloud)

```powershell
cd C:\Users\maher\Desktop\creator_dashboard
python scripts/ga4_oauth_login.py
```

Sign in with **team@newcollab.co** (or whichever account is GA4 Administrator).

## 5. Grant service account access to GA4

```powershell
python scripts/grant_ga4_access.py
```

## 6. Local Flask / dashboard

In `.env`:

```env
GA4_USE_USER_CREDENTIALS=true
```

Restart Flask. Traffic should load in Admin Reports.

## 7. Test

```powershell
python -c "from dotenv import load_dotenv; load_dotenv(); from utils.ga4 import get_traffic_data; d=get_traffic_data(bust_cache=True); print(d)"
```
