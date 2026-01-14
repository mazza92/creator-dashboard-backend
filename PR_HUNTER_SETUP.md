# PR Hunter Setup Guide

## Overview
The PR Hunter is an automated brand discovery and enrichment engine that finds PR contacts and validates their emails before presenting them for manual approval.

## Prerequisites
- Python 3.9+
- PostgreSQL database
- Redis server (for Celery task queue)
- API keys for external services

## 1. Database Migration

Run the SQL migration to create the `brand_candidates` table:

```bash
psql -U your_username -d your_database -f migrations/create_brand_candidates_table.sql
```

Or manually execute the SQL file in your database client.

## 2. Install Python Dependencies

Add these to your `requirements.txt`:

```txt
celery==5.3.4
redis==5.0.1
requests==2.31.0
```

Install:

```bash
pip install -r requirements.txt
```

## 3. Environment Variables

Add these API keys to your `.env` file:

```env
# SerpApi (Google Search) - https://serpapi.com/
SERPAPI_API_KEY=your_serpapi_key_here

# Hunter.io (Email Finding) - https://hunter.io/
HUNTER_API_KEY=your_hunter_api_key_here

# NeverBounce (Email Verification) - https://neverbounce.com/
NEVERBOUNCE_API_KEY=your_neverbounce_api_key_here

# Clearbit (Logo Fetching) - Free for logos
CLEARBIT_API_KEY=optional

# Redis for Celery
REDIS_URL=redis://localhost:6379/0
```

### API Service Costs (Monthly)
- **SerpApi**: ~$50/mo (100 searches/day)
- **Hunter.io**: ~$49/mo (1,000 searches)
- **NeverBounce**: Pay-as-you-go (~$0.003/email)
- **Total**: ~$100-150/mo

### Free Tier Testing
For testing, you can use free tiers:
- SerpApi: 100 searches/mo free
- Hunter.io: 25 searches/mo free
- NeverBounce: 250 verifications free

## 4. Start Redis Server

### macOS (Homebrew):
```bash
brew install redis
brew services start redis
```

### Ubuntu/Debian:
```bash
sudo apt-get install redis-server
sudo systemctl start redis-server
```

### Windows:
Download from https://github.com/microsoftarchive/redis/releases

## 5. Start Celery Worker

In your project directory:

```bash
# Start Celery worker in background
celery -A tasks.pr_hunter_tasks worker --loglevel=info

# Or run in foreground for debugging
celery -A tasks.pr_hunter_tasks worker --loglevel=debug
```

For production, use a process manager like `supervisor` or `systemd`.

## 6. Register Flask Blueprint

In your `app.py`, add:

```python
from routes.admin_pr_hunter import admin_pr_hunter_bp

# Register blueprint
app.register_blueprint(admin_pr_hunter_bp)
```

## 7. Add React Component to Admin Dashboard

Update your admin routes (e.g., `App.js`):

```javascript
import PRHunter from './brand-portal/PRHunter';

// Inside your admin routes
<Route path='/brand/dashboard/pr-hunter' element={<PRHunter />} />
```

Add to your admin navigation menu:

```javascript
<Menu.Item key="pr-hunter" icon={<SearchOutlined />}>
  <Link to="/brand/dashboard/pr-hunter">PR Hunter</Link>
</Menu.Item>
```

## 8. Test the System

### Test Discovery (Manual):
```python
from services.pr_hunter import PRHunterService

service = PRHunterService()
brands = service.search_google_for_brands("K-Beauty", max_results=5)
print(f"Found {len(brands)} brands")
```

### Test Full Pipeline (Celery):
```python
from tasks.pr_hunter_tasks import run_pr_hunt

# Trigger task
result = run_pr_hunt.delay("K-Beauty", 10)
print(f"Task ID: {result.id}")

# Wait for result
print(result.get(timeout=300))
```

### Test via API:
```bash
# Start a hunt
curl -X POST http://localhost:5000/api/admin/pr-hunt/start \
  -H "Content-Type: application/json" \
  -d '{"keyword": "K-Beauty", "max_results": 10}' \
  --cookie "your_session_cookie"

# Check status
curl http://localhost:5000/api/admin/pr-hunt/status/task_id_here \
  --cookie "your_session_cookie"

# Get candidates
curl http://localhost:5000/api/admin/candidates \
  --cookie "your_session_cookie"
```

## 9. Using the Admin UI

1. Navigate to `/brand/dashboard/pr-hunter`
2. Click "Start New Hunt"
3. Enter a keyword (e.g., "Clean Beauty", "K-Beauty")
4. Select max results (recommended: 50)
5. Click "Start Hunt"
6. Wait for the hunt to complete (typically 5-15 minutes)
7. Review candidates in the table:
   - ✅ Green badges = Verified emails (95-100 score)
   - ⚠️ Yellow badges = Catch-all or lower scores
   - Edit names/emails by clicking "Edit"
8. Select candidates to approve
9. Click "Approve Selected" to move them to live brands table

## 10. Quality Gate Logic

The system automatically filters out:
- Generic emails (info@, contact@, support@)
- Invalid emails (failed SMTP check)
- Missing PR manager names
- Low verification scores (<50)

Candidates with catch-all emails are flagged but not auto-rejected.

## 11. Workflow

```
Discovery → Enrichment → Verification → Quality Gate → Staging Table → Admin Review → Live Brands
```

### Discovery
- Searches Google via SerpApi
- Finds brands on TikTok, Instagram, and listicles
- Extracts domain and basic info

### Enrichment
- Searches LinkedIn for PR manager
- Finds email via Hunter.io
- Fetches logo from Clearbit

### Verification
- Validates email deliverability via NeverBounce
- Assigns confidence score (0-100)
- Detects catch-all domains

### Quality Gate
- Filters out low-quality candidates
- Only shows high-probability winners

### Staging Table
- Saves to `brand_candidates`
- Prevents duplicates
- Tracks source and timestamps

### Admin Review
- Manual approval required
- Can edit names/emails
- Can reject or re-verify

### Live Brands
- Approved candidates moved to `brands` table
- Creates slug, sets visibility
- Ready for public directory

## 12. Troubleshooting

### Celery Not Starting
```bash
# Check Redis connection
redis-cli ping
# Should return "PONG"

# Check Celery logs
celery -A tasks.pr_hunter_tasks worker --loglevel=debug
```

### API Keys Not Working
```bash
# Check environment variables
echo $SERPAPI_API_KEY
echo $HUNTER_API_KEY
echo $NEVERBOUNCE_API_KEY

# Or in Python
import os
print(os.getenv('SERPAPI_API_KEY'))
```

### Database Connection Errors
```bash
# Check database connection
psql -U your_username -d your_database -c "SELECT * FROM brand_candidates LIMIT 1;"
```

### Low Quality Results
Adjust the quality gate in `services/pr_hunter.py`:

```python
def quality_gate(self, candidate):
    # Lower the score threshold for testing
    if score < 25:  # Was 50
        return False, f"Low verification score: {score}"
```

## 13. Production Deployment

### Celery with Supervisor (Ubuntu)

Create `/etc/supervisor/conf.d/celery.conf`:

```ini
[program:celery]
command=/path/to/venv/bin/celery -A tasks.pr_hunter_tasks worker --loglevel=info
directory=/path/to/project
user=your_user
numprocs=1
stdout_logfile=/var/log/celery/worker.log
stderr_logfile=/var/log/celery/worker.log
autostart=true
autorestart=true
startsecs=10
stopwaitsecs=600
```

Start:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start celery
```

### Celery with Systemd

Create `/etc/systemd/system/celery.service`:

```ini
[Unit]
Description=Celery Service
After=network.target

[Service]
Type=forking
User=your_user
Group=your_group
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/celery -A tasks.pr_hunter_tasks worker --detach
ExecStop=/path/to/venv/bin/celery -A tasks.pr_hunter_tasks control shutdown

[Install]
WantedBy=multi-user.target
```

Start:
```bash
sudo systemctl daemon-reload
sudo systemctl start celery
sudo systemctl enable celery
```

## 14. Rate Limiting & Optimization

The service includes built-in rate limiting (1 second between API calls). To optimize:

### Batch Processing
Process candidates in batches of 10:

```python
from services.pr_hunter import chunk_list

for batch in chunk_list(brands, 10):
    # Process batch
    pass
```

### Reduce API Calls
Use Hunter.io's domain search instead of email finder when possible.

### Cache Results
Cache LinkedIn searches for 24 hours to avoid duplicate API calls.

## 15. Monitoring

### Check Celery Status
```bash
celery -A tasks.pr_hunter_tasks inspect active
celery -A tasks.pr_hunter_tasks inspect stats
```

### Monitor Queue
```bash
# Check queue length
redis-cli LLEN celery
```

### View Logs
```bash
tail -f /var/log/celery/worker.log
```

## 16. Advanced Configuration

### Custom Discovery Sources
Add your own discovery strategies in `services/pr_hunter.py`:

```python
def search_instagram_hashtags(self, hashtag):
    # Your custom Instagram scraping logic
    pass
```

### Custom Enrichment Services
Replace Hunter.io with Apollo.io or RocketReach:

```python
def _find_email_apollo(self, first_name, last_name, domain):
    # Apollo.io integration
    pass
```

### Custom Verification
Use ZeroBounce or EmailListVerify instead of NeverBounce.

## Support

For issues or questions:
1. Check logs: `/var/log/celery/worker.log`
2. Verify API keys are valid
3. Test each service individually
4. Check database connections

## Next Steps

1. Run the database migration
2. Configure API keys
3. Start Redis and Celery
4. Test with a small hunt (10 results)
5. Review quality of results
6. Adjust quality gate as needed
7. Scale up to production hunts (50-100 results)
