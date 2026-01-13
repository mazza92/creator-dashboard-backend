# How to Restart Flask Server

The PR CRM routes have been added to `app.py`, but Flask needs to be restarted to load them.

## Steps to Restart:

### Option 1: Quick Restart (Recommended)
1. Find the terminal/command prompt running Flask
2. Press `Ctrl+C` to stop the server
3. Run the Flask server again:
   ```bash
   python app.py
   ```
   OR
   ```bash
   flask run
   ```

### Option 2: If Flask is Running as a Service
1. Stop the service (method depends on how you're running it)
2. Start it again

### Option 3: Auto-reload (if configured)
If you have Flask's auto-reload enabled (`FLASK_DEBUG=1`), simply save the `app.py` file again to trigger a reload.

## Verify Routes are Loaded

After restarting, you should see these routes in the Flask startup logs:

```
PR CRM routes registered
```

And these endpoints should be available:
- GET /api/pr-crm/brands
- GET /api/pr-crm/brands/<id>
- GET /api/pr-crm/brands/categories
- GET /api/pr-crm/pipeline
- POST /api/pr-crm/pipeline/save
- PATCH /api/pr-crm/pipeline/<id>/update-stage
- DELETE /api/pr-crm/pipeline/<id>
- GET /api/pr-crm/templates
- POST /api/pr-crm/templates/<id>/render
- GET /api/pr-crm/analytics

## Test the API

After restarting, test with:
```bash
curl http://localhost:5000/api/pr-crm/brands?limit=5
```

Or open in browser:
```
http://localhost:5000/api/pr-crm/brands?limit=5
```

## Current Status

✅ Frontend: Running on port 3000
❌ Backend: Needs restart to load PR CRM routes

Once you restart Flask, the PR CRM system will be fully functional!
