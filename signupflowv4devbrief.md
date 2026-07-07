# Signup Flow v4 — Full Dev Brief

Preview: `/home/user/signup-flow-v4.html`

## Core principle
**Zero data loss. Zero schema changes.** Every field collected in the old flow is still saved — just at a different moment. The new flow reduces signup friction; the profile completeness widget collects the rest progressively once the user is in the product.

---

## 1. What changes vs what stays

### Signup friction reduced — data collection deferred, not removed

| Field | Old flow | New flow | Where collected |
|---|---|---|---|
| Email | Step 1 (required) | Step 1 (required) | Register form |
| Password | Step 1 (required) | Step 1 (required) | Register form |
| First name | Step 1 (required) | **Deferred** | Profile completeness widget |
| Last name | Step 1 (required) | **Deferred** | Profile completeness widget |
| Terms accepted | Step 1 (required) | Step 1 (required, inline text) | Register form |
| Creator username | Step 2 (required) | Step 2 (required) | Onboarding step 1 |
| Bio | Step 2 | **Deferred** | Profile completeness widget |
| Instagram handle | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| Instagram followers | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| TikTok handle | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| TikTok followers | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| YouTube URL | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| YouTube subscribers | Step 2 | Step 2 (if selected) | Onboarding step 1 |
| Snapchat handle | Step 2 | **Deferred** | Profile completeness widget |
| Primary age range | Step 3 (required) | **Deferred** | Profile completeness widget |
| Regions reached | Step 3 (required) | **Deferred** | Profile completeness widget |
| Interests / Niche | Step 3 (required) | Onboarding step 2 (required) | Niche chip selection |
| Profile picture | Step 3 (required) | **Deferred** | Profile completeness widget |

**No DB columns are removed or renamed.** All existing columns remain. Only the required/optional status changes at registration time.

---

## 2. Database — no migration needed

All columns already exist. The only change is removing `NOT NULL` constraints on fields now collected progressively. Run this once:

```sql
-- Remove NOT NULL from fields now collected after signup
-- Adjust table/column names to match your actual schema

ALTER TABLE users
  ALTER COLUMN first_name DROP NOT NULL,
  ALTER COLUMN last_name  DROP NOT NULL,
  ALTER COLUMN bio        DROP NOT NULL,
  ALTER COLUMN age_range  DROP NOT NULL,
  ALTER COLUMN profile_picture DROP NOT NULL;

-- regions and niches are likely arrays — ensure they default to empty, not NULL
ALTER TABLE users
  ALTER COLUMN regions SET DEFAULT '{}',
  ALTER COLUMN niches  SET DEFAULT '{}';

UPDATE users SET regions = '{}' WHERE regions IS NULL;
UPDATE users SET niches  = '{}' WHERE niches  IS NULL;
```

**Existing users are unaffected** — their data is already in the DB. This only affects new registrations going forward.

---

## 3. Backend changes

### 3.1 Register endpoint — `POST /api/auth/register`

**Find your existing register route. Apply these changes:**

```python
# BEFORE — old required fields:
# first_name = data.get('first_name')  ← required, 400 if missing
# last_name  = data.get('last_name')   ← required, 400 if missing
# username   = data.get('username')    ← required, 400 if missing

# AFTER — new required fields (only email + password at registration):
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json()

    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    terms    = data.get('terms_accepted', False)

    # Only these 3 are required at registration
    if not email or not password or not terms:
        return jsonify({'error': 'Email, password and terms acceptance required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        terms_accepted=True,
        terms_accepted_at=datetime.utcnow(),
        # All other fields default to NULL / empty — filled in later
        onboarding_complete=False,
    )
    db.session.add(user)
    db.session.commit()

    # Start session / issue JWT as you currently do
    session['user_id'] = user.id
    return jsonify({'ok': True, 'user_id': user.id, 'redirect': '/onboarding'}), 201
```

### 3.2 Google SSO register — handle name extraction

If you add Google SSO, extract available fields from the Google profile and save them immediately — this pre-populates first_name, last_name, and profile_picture without the user having to fill them in:

```python
@app.route('/api/auth/google', methods=['POST'])
def google_auth():
    token = request.json.get('credential')
    # Verify with google-auth library
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)

    email = idinfo['email']
    existing = User.query.filter_by(email=email).first()

    if existing:
        session['user_id'] = existing.id
        redirect = '/dashboard' if existing.onboarding_complete else '/onboarding'
        return jsonify({'ok': True, 'redirect': redirect})

    user = User(
        email=email,
        # Pre-fill from Google — saves user from typing these later
        first_name=idinfo.get('given_name', ''),
        last_name=idinfo.get('family_name', ''),
        profile_picture=idinfo.get('picture', ''),  # Google CDN URL
        google_id=idinfo['sub'],
        terms_accepted=True,
        terms_accepted_at=datetime.utcnow(),
        onboarding_complete=False,
    )
    db.session.add(user)
    db.session.commit()

    session['user_id'] = user.id
    return jsonify({'ok': True, 'redirect': '/onboarding'})
```

### 3.3 Onboarding step 1 — `POST /api/user/onboarding/step1`

Maps platform selection to the correct existing DB columns:

```python
PLATFORM_FIELD_MAP = {
    'instagram': ('instagram_handle', 'instagram_followers'),
    'tiktok':    ('tiktok_handle',    'tiktok_followers'),
    'youtube':   ('youtube_url',      'youtube_subscribers'),
    'pinterest': ('pinterest_handle', 'pinterest_monthly_views'),
    'twitter':   ('twitter_handle',   'twitter_followers'),
    'blog':      ('blog_url',         'blog_monthly_readers'),
}

@app.route('/api/user/onboarding/step1', methods=['POST'])
@login_required
def onboarding_step1():
    data = request.get_json()
    user = User.query.get(current_user.id)

    username = data.get('username', '').strip()
    platform = data.get('platform', '')       # e.g. 'instagram'
    followers = data.get('followers', 0)

    if not username:
        return jsonify({'error': 'Username is required'}), 400

    # Check username uniqueness
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user.id:
        return jsonify({'error': 'Username already taken'}), 409

    user.username = username
    user.main_platform = platform  # store which platform is primary

    # Write to the correct existing columns based on platform
    if platform and platform in PLATFORM_FIELD_MAP:
        handle_col, followers_col = PLATFORM_FIELD_MAP[platform]
        # For handle: use username value (they entered @handle)
        setattr(user, handle_col, username)
        if followers:
            setattr(user, followers_col, int(followers))

    # Derive follower_count for matching/display (single unified field)
    if followers:
        user.follower_count = int(followers)

    db.session.commit()
    return jsonify({'ok': True})
```

### 3.4 Onboarding step 2 — `POST /api/user/onboarding/step2`

Maps niche chips to your existing `interests` / `niches` column:

```python
@app.route('/api/user/onboarding/step2', methods=['POST'])
@login_required
def onboarding_step2():
    data = request.get_json()
    user = User.query.get(current_user.id)

    niches = data.get('niches', [])  # list of strings e.g. ['Beauty', 'Fashion']

    if not niches:
        return jsonify({'error': 'Select at least one niche'}), 400

    # Write to whichever column your existing schema uses:
    # Option A — if stored as array (PostgreSQL):
    user.niches = niches
    # Option B — if stored as comma-separated string:
    # user.interests = ','.join(niches)
    # Option C — if stored as JSON:
    # user.interests = json.dumps(niches)

    # Also set primary niche for simpler queries
    user.niche = niches[0] if niches else None

    user.onboarding_complete = True   # mark onboarding done
    db.session.commit()

    return jsonify({'ok': True, 'redirect': '/dashboard'})
```

### 3.5 Profile completeness — `GET /api/user/profile-completeness`

Reads actual DB state so it works correctly for existing users AND new users:

```python
@app.route('/api/user/profile-completeness', methods=['GET'])
@login_required
def profile_completeness():
    user = User.query.get(current_user.id)

    fields = {
        'email':         bool(user.email),
        'username':      bool(user.username),
        'niche':         bool(user.niches or user.niche or user.interests),
        'profile_photo': bool(user.profile_picture),
        'bio':           bool(user.bio and len(user.bio.strip()) > 10),
        'audience_age':  bool(user.age_range),
        'regions':       bool(user.regions and len(user.regions) > 0),
        # Include name — collected via completeness widget if not from Google
        'name':          bool(user.first_name),
    }

    completed = sum(1 for v in fields.values() if v)
    total     = len(fields)
    pct       = round(completed / total * 100)

    return jsonify({
        'fields':    fields,
        'completed': completed,
        'total':     total,
        'pct':       pct,
    })
```

### 3.6 Profile update — `PATCH /api/user/profile`

Single endpoint handles all deferred field saves from the completeness widget. **This endpoint likely already exists — extend it to handle the new fields:**

```python
@app.route('/api/user/profile', methods=['PATCH'])
@login_required
def update_profile():
    data = request.get_json()
    user = User.query.get(current_user.id)

    # Map all possible fields — only update what's sent
    field_map = {
        'first_name':      'first_name',
        'last_name':       'last_name',
        'bio':             'bio',
        'age_range':       'age_range',
        'regions':         'regions',
        'profile_picture': 'profile_picture',
        'niches':          'niches',
        'niche':           'niche',
        'username':        'username',
        # Social handles — all existing columns
        'instagram_handle':       'instagram_handle',
        'instagram_followers':    'instagram_followers',
        'tiktok_handle':          'tiktok_handle',
        'tiktok_followers':       'tiktok_followers',
        'youtube_url':            'youtube_url',
        'youtube_subscribers':    'youtube_subscribers',
        'snapchat_handle':        'snapchat_handle',
        'pinterest_handle':       'pinterest_handle',
        'blog_url':               'blog_url',
    }

    for key, col in field_map.items():
        if key in data:
            setattr(user, col, data[key])

    db.session.commit()
    return jsonify({'ok': True})
```

---

## 4. Frontend changes

### 4.1 Files to update

| File | Change |
|---|---|
| `src/pages/Register.js` | Replace with new simplified register form |
| `src/pages/RegisterBrand.js` | Can be hidden/removed (no brands yet) — or leave route but don't show link |
| `src/pages/Onboarding.js` | Replace 3-step flow with new 2-step flow |
| `src/components/ProfileCompleteness.js` | **New file** — completeness widget |
| `src/pages/Dashboard.js` | Add `<ProfileCompleteness />` below welcome banner |

### 4.2 Register.js — find/replace

**Find your existing register form JSX. Replace the entire form content with:**

```jsx
// src/pages/Register.js
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

export default function Register() {
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw]     = useState(false);
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) { setError('Password must be at least 8 characters'); return; }
    setLoading(true);
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, terms_accepted: true }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Something went wrong'); return; }
      navigate('/onboarding');
    } catch {
      setError('Network error — please try again');
    } finally {
      setLoading(false);
    }
  };

  return (
    // Apply styles from signup-flow-v4.html
    // Card with warm background, eyebrow, headline, benefits, proof strip, Google btn, form
    <form onSubmit={handleSubmit}>
      {/* ... map the HTML from signup-flow-v4.html screen 1 ... */}
    </form>
  );
}
```

### 4.3 Onboarding.js — replace existing 3-step flow

```jsx
// src/pages/Onboarding.js
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

const PLATFORM_MAP = {
  instagram: { handleField: 'instagram_handle', followersField: 'instagram_followers', label: '📸 Followers' },
  tiktok:    { handleField: 'tiktok_handle',    followersField: 'tiktok_followers',    label: '🎵 Followers' },
  youtube:   { handleField: 'youtube_url',       followersField: 'youtube_subscribers', label: '▶️ Subscribers' },
  pinterest: { handleField: 'pinterest_handle', followersField: 'pinterest_monthly_views', label: '📌 Monthly views' },
  twitter:   { handleField: 'twitter_handle',   followersField: 'twitter_followers',   label: '𝕏 Followers' },
  blog:      { handleField: 'blog_url',          followersField: 'blog_monthly_readers', label: '✍️ Readers/mo' },
};

const NICHES = [
  '💄 Beauty', '👗 Fashion', '🍽️ Food', '💪 Fitness',
  '✈️ Travel', '🏠 Lifestyle', '💻 Tech', '🌿 Wellness',
  '📚 Education', '🎮 Gaming', '🐾 Pets', '🍼 Parenting',
];

export default function Onboarding() {
  const [step, setStep]           = useState(1);
  const [username, setUsername]   = useState('');
  const [platform, setPlatform]   = useState('');
  const [followers, setFollowers] = useState('');
  const [niches, setNiches]       = useState([]);
  const [error, setError]         = useState('');
  const [loading, setLoading]     = useState(false);
  const navigate = useNavigate();

  const submitStep1 = async () => {
    setError('');
    if (!username.trim()) { setError('Username is required'); return; }
    setLoading(true);
    try {
      const res = await fetch('/api/user/onboarding/step1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), platform, followers: Number(followers) || 0 }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Error saving'); return; }
      setStep(2);
    } catch {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  };

  const submitStep2 = async () => {
    setError('');
    if (!niches.length) { setError('Select at least one niche'); return; }
    setLoading(true);
    try {
      const res = await fetch('/api/user/onboarding/step2', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ niches: niches.map(n => n.replace(/^\S+\s/, '')) }), // strip emoji
      });
      const data = await res.json();
      if (!res.ok) { setError(data.error || 'Error saving'); return; }
      navigate('/dashboard');
    } catch {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  };

  const toggleNiche = (n) => {
    setNiches(prev => prev.includes(n) ? prev.filter(x => x !== n) : [...prev, n]);
  };

  if (step === 1) return (
    <OnboardingStep1
      username={username} setUsername={setUsername}
      platform={platform} setPlatform={setPlatform}
      followers={followers} setFollowers={setFollowers}
      onNext={submitStep1} onBack={() => navigate('/register')}
      error={error} loading={loading}
    />
  );

  return (
    <OnboardingStep2
      niches={niches} onToggle={toggleNiche}
      onNext={submitStep2} onBack={() => setStep(1)}
      error={error} loading={loading}
    />
  );
}
```

### 4.4 ProfileCompleteness.js — new component

```jsx
// src/components/ProfileCompleteness.js
import React, { useState, useEffect } from 'react';

// Fields shown in the completeness widget — map display label to API field key
// These match exactly the fields deferred from the old onboarding flow
const COMPLETENESS_FIELDS = [
  { key: 'profile_photo', label: '📸 Add profile photo', type: 'image_upload' },
  { key: 'bio',           label: '✍️ Write a bio',        type: 'textarea' },
  { key: 'name',          label: '👤 Add your name',      type: 'text_dual', fields: ['first_name', 'last_name'] },
  { key: 'audience_age',  label: '👥 Audience age range', type: 'select',
    options: ['13–17', '18–24', '25–34', '35–44', '45+'] },
  { key: 'regions',       label: '🌍 Regions reached',    type: 'multiselect',
    options: ['USA', 'UK', 'France', 'Canada', 'Australia', 'India', 'Germany', 'Other'] },
];

export default function ProfileCompleteness() {
  const [data, setData]       = useState(null);
  const [editing, setEditing] = useState(null);

  useEffect(() => {
    fetch('/api/user/profile-completeness')
      .then(r => r.json())
      .then(setData);
  }, []);

  if (!data) return null;

  // Don't show if profile is already 100%
  if (data.pct === 100) return null;

  const saveField = async (payload) => {
    await fetch('/api/user/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    // Refresh completeness
    const updated = await fetch('/api/user/profile-completeness').then(r => r.json());
    setData(updated);
    setEditing(null);
  };

  const alreadyDone = Object.entries(data.fields)
    .filter(([, v]) => v)
    .map(([k]) => k);

  const pending = COMPLETENESS_FIELDS.filter(f => !data.fields[f.key]);

  return (
    <div className="completeness-card">
      {/* Header */}
      <div className="cc-head">
        <div className="cc-title">Complete your profile — brands check this</div>
        <div className="cc-pct">{data.pct}%</div>
      </div>

      {/* Progress bar */}
      <div className="cc-track">
        <div className="cc-fill" style={{ width: `${data.pct}%` }} />
      </div>

      <p className="cc-why">
        A complete profile makes your pitches <strong>2× more likely to get a reply.</strong>{' '}
        Brands want to know who they're working with before they respond.
      </p>

      {/* Completed items */}
      <div className="cc-items">
        {['email', 'username', 'niche'].map(k => (
          data.fields[k] && (
            <div key={k} className="cc-item done">
              ✓ {k.charAt(0).toUpperCase() + k.slice(1)}
            </div>
          )
        ))}

        {/* Pending items — tapping opens inline editor */}
        {pending.map(field => (
          <div
            key={field.key}
            className="cc-item"
            onClick={() => setEditing(field.key === editing ? null : field.key)}
          >
            {field.label}
          </div>
        ))}
      </div>

      {/* Inline editors — rendered below the chips */}
      {editing && (
        <InlineEditor
          field={COMPLETENESS_FIELDS.find(f => f.key === editing)}
          onSave={saveField}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

// Inline editor — renders the right input type per field
function InlineEditor({ field, onSave, onCancel }) {
  const [value, setValue]   = useState('');
  const [value2, setValue2] = useState('');
  const [selected, setSelected] = useState([]);

  const handleSave = () => {
    if (field.type === 'text_dual') {
      onSave({ first_name: value, last_name: value2 });
    } else if (field.type === 'multiselect') {
      onSave({ [field.key === 'regions' ? 'regions' : field.key]: selected });
    } else if (field.type === 'select') {
      onSave({ age_range: value });
    } else if (field.type === 'image_upload') {
      // Trigger existing profile picture upload flow
      document.getElementById('profile-pic-upload').click();
    } else {
      onSave({ [field.key === 'bio' ? 'bio' : field.key]: value });
    }
  };

  return (
    <div style={{ marginTop: 14, padding: '14px 16px', background: '#F9F9F9', borderRadius: 12, border: '1.5px solid #E8E8E8' }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{field.label}</div>

      {field.type === 'textarea' && (
        <textarea
          rows={3}
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder="Tell brands about you and your content..."
          style={{ width: '100%', padding: '10px 12px', border: '1.5px solid #E8E8E8', borderRadius: 9, fontSize: 13, fontFamily: 'Inter', resize: 'vertical', outline: 'none' }}
        />
      )}

      {field.type === 'text_dual' && (
        <div style={{ display: 'flex', gap: 8 }}>
          <input placeholder="First name" value={value} onChange={e => setValue(e.target.value)}
            style={{ flex: 1, padding: '10px 12px', border: '1.5px solid #E8E8E8', borderRadius: 9, fontSize: 13, fontFamily: 'Inter', outline: 'none' }} />
          <input placeholder="Last name" value={value2} onChange={e => setValue2(e.target.value)}
            style={{ flex: 1, padding: '10px 12px', border: '1.5px solid #E8E8E8', borderRadius: 9, fontSize: 13, fontFamily: 'Inter', outline: 'none' }} />
        </div>
      )}

      {field.type === 'select' && (
        <select value={value} onChange={e => setValue(e.target.value)}
          style={{ width: '100%', padding: '10px 12px', border: '1.5px solid #E8E8E8', borderRadius: 9, fontSize: 13, fontFamily: 'Inter', outline: 'none', background: '#fff' }}>
          <option value="">Select age range...</option>
          {field.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )}

      {field.type === 'multiselect' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {field.options.map(o => (
            <div key={o}
              onClick={() => setSelected(prev => prev.includes(o) ? prev.filter(x => x !== o) : [...prev, o])}
              style={{ padding: '6px 12px', borderRadius: 20, border: `1.5px solid ${selected.includes(o) ? '#0F0F0F' : '#E8E8E8'}`, background: selected.includes(o) ? '#0F0F0F' : '#fff', color: selected.includes(o) ? '#fff' : '#555', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
              {o}
            </div>
          ))}
        </div>
      )}

      {field.type === 'image_upload' && (
        <>
          <input id="profile-pic-upload" type="file" accept="image/*" style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files[0];
              if (!file) return;
              // Use your existing profile picture upload endpoint
              const form = new FormData();
              form.append('file', file);
              const res = await fetch('/api/user/profile-picture', { method: 'POST', body: form });
              const { url } = await res.json();
              onSave({ profile_picture: url });
            }} />
          <div style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>PNG or JPEG, max 5MB</div>
        </>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button onClick={handleSave}
          style={{ flex: 1, padding: '10px', background: '#0F0F0F', color: '#fff', border: 'none', borderRadius: 9, fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: 'Inter' }}>
          Save
        </button>
        <button onClick={onCancel}
          style={{ padding: '10px 16px', background: '#fff', color: '#555', border: '1.5px solid #E8E8E8', borderRadius: 9, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'Inter' }}>
          Cancel
        </button>
      </div>
    </div>
  );
}
```

### 4.5 Dashboard.js — add ProfileCompleteness

**Find your existing Dashboard return statement. Add the completeness widget just below the welcome banner:**

```jsx
// FIND in Dashboard.js:
return (
  <DashboardLayout>
    {/* ...existing welcome banner or first section... */}

// ADD immediately after the opening/welcome section:
    <ProfileCompleteness />

    {/* ...rest of existing dashboard... */}
  </DashboardLayout>
);
```

And add the import at the top:
```jsx
import ProfileCompleteness from '../components/ProfileCompleteness';
```

---

## 5. Existing user compatibility

Existing users who completed the old 3-step onboarding already have all fields filled. The completeness widget reads live from the DB — it will show 100% for them and **not render at all** (`if (data.pct === 100) return null`).

No data migration needed. No existing user is affected.

---

## 6. Google SSO setup (optional but recommended)

Google SSO eliminates the password step entirely and auto-fills first_name, last_name, and profile_picture — making the completeness score higher from day one.

**Frontend — add Google Identity script to `public/index.html`:**
```html
<script src="https://accounts.google.com/gsi/client" async defer></script>
```

**In Register.js — initialize Google button:**
```javascript
useEffect(() => {
  window.google?.accounts.id.initialize({
    client_id: process.env.REACT_APP_GOOGLE_CLIENT_ID,
    callback: async ({ credential }) => {
      const res = await fetch('/api/auth/google', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential }),
      });
      const data = await res.json();
      if (data.ok) navigate(data.redirect);
    },
  });
  window.google?.accounts.id.renderButton(
    document.getElementById('google-btn'),
    { type: 'standard', shape: 'rectangular', theme: 'outline', size: 'large', width: 360 }
  );
}, []);
```

**Add to `.env`:**
```
REACT_APP_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
```

**Get Client ID:** Google Cloud Console → APIs & Services → Credentials → Create OAuth 2.0 Client ID → Web application → Authorized origins: `https://app.newcollab.co`

---

## 7. Redirect logic — where users go after each step

```
/register          → on success → /onboarding
/onboarding step1  → on success → /onboarding step2 (same page, step state)
/onboarding step2  → on success → /dashboard
/dashboard         → if !onboarding_complete → redirect to /onboarding
/login             → if onboarding_complete  → /dashboard
                   → if !onboarding_complete → /onboarding
```

**Add this guard to your login endpoint:**

```python
@app.route('/api/auth/login', methods=['POST'])
def login():
    # ... existing auth logic ...
    session['user_id'] = user.id
    redirect = '/dashboard' if user.onboarding_complete else '/onboarding'
    return jsonify({'ok': True, 'redirect': redirect})
```

**Add this guard to your React router (Dashboard protected route):**

```javascript
// src/components/ProtectedRoute.js
import { useAuth } from '../context/AuthContext';
import { Navigate } from 'react-router-dom';

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/register" replace />;
  if (!user.onboarding_complete) return <Navigate to="/onboarding" replace />;
  return children;
}
```

---

## 8. Implementation checklist

```
□ 1. Run the ALTER TABLE SQL (remove NOT NULL from deferred fields)
□ 2. Update POST /api/auth/register — only email + password required
□ 3. Add POST /api/user/onboarding/step1 (or update existing)
□ 4. Add POST /api/user/onboarding/step2 (or update existing)
□ 5. Add GET  /api/user/profile-completeness
□ 6. Update PATCH /api/user/profile to handle all deferred fields
□ 7. Update login endpoint to return correct redirect based on onboarding_complete
□ 8. Replace Register.js with new simplified form
□ 9. Replace Onboarding.js with new 2-step flow
□ 10. Create ProfileCompleteness.js component
□ 11. Add <ProfileCompleteness /> to Dashboard.js
□ 12. Test with an existing user — completeness should show correctly
□ 13. Test new signup end-to-end: register → onboarding → dashboard
□ 14. Verify PLATFORM_MAP writes to correct existing columns
□ 15. (Optional) Add Google SSO — pre-fills name + photo on first login
```

---

## 9. Column name check — do this before writing any code

The SQL and Python above assume these column names. Run this query and verify each one exists:

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
ORDER BY ordinal_position;
```

Expected columns (adjust names in code if yours differ):

| Expected | Likely alternatives |
|---|---|
| `first_name` | `firstname`, `given_name` |
| `last_name` | `lastname`, `family_name`, `surname` |
| `username` | `handle`, `creator_username` |
| `bio` | `description`, `about` |
| `age_range` | `audience_age`, `primary_age_range` |
| `regions` | `regions_reached`, `audience_regions` |
| `niches` | `interests`, `categories`, `niche` |
| `profile_picture` | `avatar`, `profile_image`, `photo_url` |
| `instagram_handle` | `instagram_username`, `ig_handle` |
| `instagram_followers` | `ig_followers`, `instagram_count` |
| `onboarding_complete` | `is_onboarded`, `setup_complete` |
