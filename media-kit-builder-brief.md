# Media Kit + Portfolio Builder — Dev Brief
**New files:** `src/creator-portal/PortfolioBuilder.js` · `src/pages/kit/[username].js` (or equivalent public route)  
**Approach:** Two self-contained components. Builder lives inside the creator dashboard. Public kit is a standalone page at `newcollab.co/kit/:username`.  
**Mobile first:** All layouts designed 375px up. Bento grid collapses to single column below 768px.

---

## Architecture overview

```
Creator dashboard (authenticated)          Public URL (unauthenticated)
──────────────────────────────             ──────────────────────────────
PortfolioBuilder.js                        PublicMediaKit.js
  ├── Step 1: URL input / detect           Route: /kit/:username
  ├── Step 2: Brand + collab type
  ├── Step 3: Stats + preview card         Bento grid layout
  ├── Empty state → Guide flow               ├── Header (dark)
  └── My Kit dashboard                       ├── Stats strip
       ├── Post grid                         ├── Post grid
       ├── Kit views tracker                 ├── Rate card
       └── Share / edit                      └── CTA
```

---

## Phase 1 — Database

### New table: `portfolio_posts`

```sql
CREATE TABLE IF NOT EXISTS portfolio_posts (
  id                  SERIAL PRIMARY KEY,
  creator_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  post_url            TEXT,
  platform            VARCHAR(20) NOT NULL,          -- instagram | tiktok | youtube
  post_type           VARCHAR(20) NOT NULL,          -- reel | photo | tiktok | youtube | story
  brand_name          VARCHAR(200),
  collab_type         VARCHAR(20) DEFAULT 'organic', -- gifted | paid | organic | own
  views               INTEGER DEFAULT 0,
  likes               INTEGER DEFAULT 0,
  comments            INTEGER DEFAULT 0,
  shares              INTEGER DEFAULT 0,
  thumbnail_url       TEXT,
  display_order       INTEGER DEFAULT 0,
  is_featured         BOOLEAN DEFAULT false,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_portfolio_posts_creator ON portfolio_posts(creator_id);
```

### New table: `kit_views`

```sql
CREATE TABLE IF NOT EXISTS kit_views (
  id            SERIAL PRIMARY KEY,
  creator_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  viewer_ip     VARCHAR(45),
  viewer_ua     TEXT,
  referrer      TEXT,
  viewed_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_kit_views_creator ON kit_views(creator_id, viewed_at DESC);
```

### Add columns to `users` (or `creators`) table

```sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS kit_tagline      TEXT,
  ADD COLUMN IF NOT EXISTS kit_published    BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS kit_slug         VARCHAR(100) UNIQUE,
  ADD COLUMN IF NOT EXISTS rates_reel       INTEGER,
  ADD COLUMN IF NOT EXISTS rates_tiktok     INTEGER,
  ADD COLUMN IF NOT EXISTS rates_photo      INTEGER,
  ADD COLUMN IF NOT EXISTS rates_gifted     BOOLEAN DEFAULT true;
```

> `kit_slug` defaults to username. Set on first publish if not already set.

---

## Phase 2 — Backend API

### 2A. Portfolio posts CRUD

```python
# GET /api/portfolio/posts
# Returns all posts for the authenticated creator, ordered by display_order
@app.route('/api/portfolio/posts', methods=['GET'])
@login_required
def get_portfolio_posts():
    posts = PortfolioPost.query.filter_by(
        creator_id=current_user.id
    ).order_by(PortfolioPost.display_order.asc()).all()
    return jsonify([serialize_post(p) for p in posts])


# POST /api/portfolio/posts
# Creates a new post entry
@app.route('/api/portfolio/posts', methods=['POST'])
@login_required
def create_portfolio_post():
    data = request.get_json()
    post = PortfolioPost(
        creator_id   = current_user.id,
        post_url     = data.get('post_url'),
        platform     = data.get('platform'),
        post_type    = data.get('post_type'),
        brand_name   = data.get('brand_name'),
        collab_type  = data.get('collab_type', 'organic'),
        views        = int(data.get('views', 0)),
        likes        = int(data.get('likes', 0)),
        comments     = int(data.get('comments', 0)),
        shares       = int(data.get('shares', 0)),
        thumbnail_url= data.get('thumbnail_url'),
        display_order= data.get('display_order', 0),
        is_featured  = data.get('is_featured', False),
    )
    db.session.add(post)
    db.session.commit()
    return jsonify(serialize_post(post)), 201


# PATCH /api/portfolio/posts/:id
@app.route('/api/portfolio/posts/<int:post_id>', methods=['PATCH'])
@login_required
def update_portfolio_post(post_id):
    post = PortfolioPost.query.filter_by(
        id=post_id, creator_id=current_user.id
    ).first_or_404()
    data = request.get_json()
    for field in ['brand_name','collab_type','views','likes','comments','shares',
                  'thumbnail_url','display_order','is_featured','post_type']:
        if field in data:
            setattr(post, field, data[field])
    db.session.commit()
    return jsonify(serialize_post(post))


# DELETE /api/portfolio/posts/:id
@app.route('/api/portfolio/posts/<int:post_id>', methods=['DELETE'])
@login_required
def delete_portfolio_post(post_id):
    post = PortfolioPost.query.filter_by(
        id=post_id, creator_id=current_user.id
    ).first_or_404()
    db.session.delete(post)
    db.session.commit()
    return jsonify({'ok': True})


def serialize_post(p):
    return {
        'id':           p.id,
        'post_url':     p.post_url,
        'platform':     p.platform,
        'post_type':    p.post_type,
        'brand_name':   p.brand_name,
        'collab_type':  p.collab_type,
        'views':        p.views,
        'likes':        p.likes,
        'comments':     p.comments,
        'shares':       p.shares,
        'thumbnail_url': p.thumbnail_url,
        'display_order': p.display_order,
        'is_featured':  p.is_featured,
        'created_at':   p.created_at.isoformat() if p.created_at else None,
    }
```

### 2B. Kit settings save

```python
# PATCH /api/portfolio/settings
@app.route('/api/portfolio/settings', methods=['PATCH'])
@login_required
def update_kit_settings():
    data = request.get_json()
    if 'kit_tagline' in data:
        current_user.kit_tagline = data['kit_tagline']
    if 'rates_reel' in data:
        current_user.rates_reel = data['rates_reel']
    if 'rates_tiktok' in data:
        current_user.rates_tiktok = data['rates_tiktok']
    if 'rates_photo' in data:
        current_user.rates_photo = data['rates_photo']
    if 'rates_gifted' in data:
        current_user.rates_gifted = data['rates_gifted']
    if data.get('publish'):
        current_user.kit_published = True
        if not current_user.kit_slug:
            current_user.kit_slug = current_user.username
    db.session.commit()
    return jsonify({'ok': True})
```

### 2C. Public kit endpoint

```python
# GET /api/public/kit/:slug  (no auth required)
@app.route('/api/public/kit/<slug>', methods=['GET'])
def get_public_kit(slug):
    creator = User.query.filter_by(kit_slug=slug, kit_published=True).first()
    if not creator:
        creator = User.query.filter_by(username=slug, kit_published=True).first()
    if not creator:
        return jsonify({'error': 'Kit not found'}), 404

    posts = PortfolioPost.query.filter_by(
        creator_id=creator.id
    ).order_by(PortfolioPost.display_order.asc()).all()

    # Log the view (fire and forget)
    try:
        view = KitView(
            creator_id = creator.id,
            viewer_ip  = request.remote_addr,
            viewer_ua  = request.headers.get('User-Agent', '')[:500],
            referrer   = request.headers.get('Referer', '')[:500],
        )
        db.session.add(view)
        db.session.commit()
    except Exception:
        pass

    return jsonify({
        'username':         creator.username,
        'first_name':       creator.first_name,
        'avatar_url':       creator.profile_picture,
        'tagline':          creator.kit_tagline,
        'niches':           creator.niches or [],
        'follower_count':   creator.follower_count,
        'engagement_rate':  float(creator.engagement_rate or 0),
        'country':          creator.country,
        'rates_reel':       creator.rates_reel,
        'rates_tiktok':     creator.rates_tiktok,
        'rates_photo':      creator.rates_photo,
        'rates_gifted':     creator.rates_gifted,
        'posts':            [serialize_post(p) for p in posts],
    })
```

### 2D. Kit views tracker

```python
# GET /api/portfolio/views
@app.route('/api/portfolio/views', methods=['GET'])
@login_required
def get_kit_views():
    week_ago = datetime.now() - timedelta(days=7)
    total_week = KitView.query.filter(
        KitView.creator_id == current_user.id,
        KitView.viewed_at >= week_ago
    ).count()

    recent = KitView.query.filter_by(
        creator_id=current_user.id
    ).order_by(KitView.viewed_at.desc()).limit(10).all()

    return jsonify({
        'views_this_week': total_week,
        'recent': [{
            'id':        v.id,
            'viewed_at': v.viewed_at.isoformat(),
            'referrer':  v.referrer or '',
        } for v in recent]
    })
```

### 2E. URL platform detection (simple, no scraping needed)

```python
# POST /api/portfolio/detect-url
@app.route('/api/portfolio/detect-url', methods=['POST'])
@login_required
def detect_url():
    url = request.get_json().get('url', '')
    platform = 'instagram'
    post_type = 'post'

    if 'tiktok.com' in url:
        platform = 'tiktok'
        post_type = 'tiktok'
    elif 'youtube.com' in url or 'youtu.be' in url:
        platform = 'youtube'
        post_type = 'youtube'
    elif 'instagram.com' in url:
        platform = 'instagram'
        post_type = 'reel' if '/reel/' in url else 'photo'

    return jsonify({'platform': platform, 'post_type': post_type})
```

---

## Phase 3 — Frontend: PortfolioBuilder.js

**File:** `src/creator-portal/PortfolioBuilder.js`  
**New file — no find/replace needed.**

```jsx
import React, { useState, useEffect, useRef } from 'react';
import styled, { keyframes } from 'styled-components';
import { motion, AnimatePresence } from 'framer-motion';
import { message } from 'antd';
import api from '../config/api';

// ── Niche product suggestions ──────────────────────────────────
const NICHE_PRODUCTS = {
  beauty:   [{ emoji: '🧴', name: 'Moisturizer' }, { emoji: '💄', name: 'Lip product' }, { emoji: '🌸', name: 'Toner' }],
  skincare: [{ emoji: '☀️', name: 'SPF serum' }, { emoji: '💆', name: 'Face mask' }, { emoji: '🧴', name: 'Cleanser' }],
  fitness:  [{ emoji: '💪', name: 'Protein powder' }, { emoji: '🏃', name: 'Activewear' }, { emoji: '⚡', name: 'Pre-workout' }],
  wellness: [{ emoji: '🌿', name: 'Supplements' }, { emoji: '🧘', name: 'Wellness tool' }, { emoji: '💊', name: 'Vitamins' }],
  food:     [{ emoji: '🍳', name: 'Kitchen tool' }, { emoji: '🥗', name: 'Snack product' }, { emoji: '☕', name: 'Coffee/drinks' }],
  fashion:  [{ emoji: '👗', name: 'Outfit piece' }, { emoji: '👟', name: 'Shoes' }, { emoji: '👜', name: 'Accessory' }],
  default:  [{ emoji: '📦', name: 'Product you use' }, { emoji: '⭐', name: 'Favourite item' }, { emoji: '🛒', name: 'Recent purchase' }],
};

const SCRIPT_HOOKS = {
  beauty:   '"I tested this for two weeks and I finally have thoughts..."',
  skincare: '"This is what my skin actually looks like before I start..."',
  fitness:  '"Three weeks in. Here is what actually changed..."',
  wellness: '"I was skeptical. Here is what happened after 14 days..."',
  default:  '"I have been using this every day and I need to talk about it..."',
};

const PLATFORMS = [
  { id: 'instagram', label: 'Instagram', types: ['reel', 'photo', 'story'] },
  { id: 'tiktok',    label: 'TikTok',    types: ['tiktok'] },
  { id: 'youtube',   label: 'YouTube',   types: ['youtube', 'short'] },
];

const POST_TYPE_LABELS = {
  reel: 'Reel', photo: 'Photo post', story: 'Story',
  tiktok: 'TikTok video', youtube: 'YouTube video', short: 'YouTube Short',
};

const COLLAB_TYPES = [
  { id: 'gifted',  label: 'Gifted' },
  { id: 'paid',    label: 'Paid' },
  { id: 'organic', label: 'Organic' },
  { id: 'own',     label: 'My own content' },
];

const formatNumber = (n) => {
  if (!n) return null;
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return n.toString();
};

// ── Component ──────────────────────────────────────────────────
const PortfolioBuilder = ({ currentUser }) => {
  const [view, setView]           = useState('dashboard'); // dashboard | add | guide
  const [step, setStep]           = useState(1);
  const [posts, setPosts]         = useState([]);
  const [loading, setLoading]     = useState(true);
  const [kitViews, setKitViews]   = useState({ views_this_week: 0, recent: [] });
  const [saving, setSaving]       = useState(false);
  const [showRates, setShowRates] = useState(false);

  // Step form state
  const [postUrl, setPostUrl]       = useState('');
  const [platform, setPlatform]     = useState('instagram');
  const [postType, setPostType]     = useState('reel');
  const [brandName, setBrandName]   = useState('');
  const [collabType, setCollabType] = useState('gifted');
  const [stats, setStats]           = useState({ views: '', likes: '', comments: '', shares: '' });
  const [urlDetecting, setUrlDetecting] = useState(false);

  const niche = (currentUser?.niches?.[0] || 'default').toLowerCase();

  useEffect(() => {
    fetchPosts();
    fetchKitViews();
  }, []);

  const fetchPosts = async () => {
    try {
      const res = await api.get('/api/portfolio/posts');
      setPosts(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchKitViews = async () => {
    try {
      const res = await api.get('/api/portfolio/views');
      setKitViews(res.data);
    } catch (e) { /* silent */ }
  };

  const handleUrlChange = async (url) => {
    setPostUrl(url);
    if (!url.includes('http')) return;
    setUrlDetecting(true);
    try {
      const res = await api.post('/api/portfolio/detect-url', { url });
      setPlatform(res.data.platform);
      setPostType(res.data.post_type);
    } catch (e) { /* silent */ }
    finally { setUrlDetecting(false); }
  };

  const resetForm = () => {
    setPostUrl(''); setPlatform('instagram'); setPostType('reel');
    setBrandName(''); setCollabType('gifted');
    setStats({ views: '', likes: '', comments: '', shares: '' });
    setStep(1);
  };

  const handleSavePost = async () => {
    if (!platform || !postType) {
      message.error('Select a platform and post type');
      return;
    }
    setSaving(true);
    try {
      await api.post('/api/portfolio/posts', {
        post_url:    postUrl,
        platform,
        post_type:   postType,
        brand_name:  brandName || null,
        collab_type: collabType,
        views:       parseInt(stats.views.replace(/[^0-9]/g, '')) || 0,
        likes:       parseInt(stats.likes.replace(/[^0-9]/g, '')) || 0,
        comments:    parseInt(stats.comments.replace(/[^0-9]/g, '')) || 0,
        shares:      parseInt(stats.shares.replace(/[^0-9]/g, '')) || 0,
        display_order: posts.length,
      });
      await fetchPosts();
      message.success('Added to your portfolio');
      resetForm();
      setView('dashboard');
    } catch (e) {
      message.error('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    try {
      await api.patch('/api/portfolio/settings', { publish: true });
      message.success('Kit published');
      fetchPosts();
    } catch (e) {
      message.error('Failed to publish');
    }
  };

  const handleDeletePost = async (postId) => {
    try {
      await api.delete(`/api/portfolio/posts/${postId}`);
      setPosts(p => p.filter(post => post.id !== postId));
    } catch (e) {
      message.error('Failed to remove');
    }
  };

  const kitUrl = `newcollab.co/kit/${currentUser?.username || ''}`;

  // ── VIEWS ───────────────────────────────────────────────────

  if (view === 'guide') return (
    <GuideView
      niche={niche}
      onBack={() => setView('dashboard')}
      onDone={() => { setView('add'); setStep(1); }}
    />
  );

  if (view === 'add') return (
    <AddPostView
      step={step}
      setStep={setStep}
      postUrl={postUrl}
      setPostUrl={handleUrlChange}
      platform={platform}
      setPlatform={p => { setPlatform(p); setPostType(PLATFORMS.find(x => x.id === p)?.types[0] || 'post'); }}
      postType={postType}
      setPostType={setPostType}
      brandName={brandName}
      setBrandName={setBrandName}
      collabType={collabType}
      setCollabType={setCollabType}
      stats={stats}
      setStats={setStats}
      urlDetecting={urlDetecting}
      saving={saving}
      onSave={handleSavePost}
      onBack={() => { setView('dashboard'); resetForm(); }}
    />
  );

  // Dashboard view
  return (
    <Container>
      <DashHeader>
        <DashTitle>My Kit</DashTitle>
        <DashActions>
          {posts.length > 0 && (
            <PreviewBtn onClick={handlePublish}>
              Publish kit
            </PreviewBtn>
          )}
          <ShareChip onClick={() => {
            navigator.clipboard.writeText(`https://${kitUrl}`);
            message.success('Link copied');
          }}>
            {kitUrl}
          </ShareChip>
        </DashActions>
      </DashHeader>

      {/* Kit view stats */}
      {kitViews.views_this_week > 0 && (
        <ViewsCard>
          <ViewsLeft>
            <ViewsNum>{kitViews.views_this_week}</ViewsNum>
            <ViewsLabel>kit views this week</ViewsLabel>
          </ViewsLeft>
          <ViewsList>
            {kitViews.recent.slice(0, 2).map((v, i) => (
              <ViewRow key={v.id}>
                <ViewDot style={{ background: i === 0 ? '#10B981' : '#7C3AED' }} />
                <ViewText>
                  {v.referrer
                    ? `Via ${new URL(v.referrer).hostname.replace('www.', '')}`
                    : 'Direct visit'}
                  <ViewTime>
                    {Math.round((Date.now() - new Date(v.viewed_at)) / 3600000)}h ago
                  </ViewTime>
                </ViewText>
              </ViewRow>
            ))}
          </ViewsList>
        </ViewsCard>
      )}

      {/* Empty state */}
      {posts.length === 0 && !loading && (
        <EmptyState>
          <EmptyTitle>Build your portfolio</EmptyTitle>
          <EmptyBody>
            Add posts you have created — branded or organic. Each one builds your case for a collab.
          </EmptyBody>
          <EmptyActions>
            <PrimaryBtn onClick={() => setView('add')}>Paste a post URL</PrimaryBtn>
            <SecondaryBtn onClick={() => setView('guide')}>Starting from scratch</SecondaryBtn>
          </EmptyActions>
        </EmptyState>
      )}

      {/* Post grid */}
      {posts.length > 0 && (
        <>
          <SectionLabel>Your posts ({posts.length})</SectionLabel>
          <PostGrid>
            {posts.map(post => (
              <PostThumb key={post.id}>
                <PostThumbImg niche={niche}>
                  <PostThumbEmoji>{getPlatformEmoji(post.platform)}</PostThumbEmoji>
                  <PostThumbBadge>{POST_TYPE_LABELS[post.post_type] || post.post_type}</PostThumbBadge>
                </PostThumbImg>
                <PostThumbInfo>
                  <PostThumbBrand>{post.brand_name || 'My own content'}</PostThumbBrand>
                  <PostThumbMeta>
                    {post.collab_type}
                    {post.views > 0 && ` · ${formatNumber(post.views)} views`}
                  </PostThumbMeta>
                </PostThumbInfo>
                <PostThumbDelete onClick={() => handleDeletePost(post.id)}>✕</PostThumbDelete>
              </PostThumb>
            ))}
            <AddPostCard onClick={() => { resetForm(); setView('add'); }}>
              <AddPostIcon>+</AddPostIcon>
              <AddPostLabel>Add post</AddPostLabel>
            </AddPostCard>
          </PostGrid>

          {/* Rate card */}
          <RatesSection>
            <RatesSectionHeader onClick={() => setShowRates(!showRates)}>
              <SectionLabel style={{ margin: 0 }}>Packages & Rates</SectionLabel>
              <ToggleIcon>{showRates ? '▲' : '▼'}</ToggleIcon>
            </RatesSectionHeader>
            {showRates && <RatesEditor currentUser={currentUser} />}
          </RatesSection>
        </>
      )}
    </Container>
  );
};

// ── Add post flow ──────────────────────────────────────────────
const AddPostView = ({
  step, setStep, postUrl, setPostUrl, platform, setPlatform,
  postType, setPostType, brandName, setBrandName, collabType,
  setCollabType, stats, setStats, urlDetecting, saving, onSave, onBack
}) => {
  return (
    <Container>
      <StepNav>
        <BackBtn onClick={step === 1 ? onBack : () => setStep(s => s - 1)}>← Back</BackBtn>
        <StepTitle>Add content</StepTitle>
        <StepCount>{step} / 3</StepCount>
      </StepNav>

      <ProgressWrap>
        <ProgressBar>
          <ProgressFill style={{ width: `${(step / 3) * 100}%` }} />
        </ProgressBar>
      </ProgressWrap>

      <AnimatePresence mode="wait">
        {step === 1 && (
          <StepPanel key="s1" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}>
            <StepQ>Where's the post?</StepQ>
            <StepSub>Paste a link from Instagram, TikTok, or YouTube. We detect the platform automatically.</StepSub>

            <UrlInputWrap active={!!postUrl}>
              <UrlInner>
                <UrlIcon>🔗</UrlIcon>
                <UrlInput
                  value={postUrl}
                  onChange={e => setPostUrl(e.target.value)}
                  placeholder="Paste your post URL here"
                />
                {urlDetecting && <UrlSpinner>...</UrlSpinner>}
              </UrlInner>
              {postUrl && platform && (
                <UrlDetected>
                  <UrlDot />
                  <UrlDetectedText>{platform.charAt(0).toUpperCase() + platform.slice(1)} detected</UrlDetectedText>
                </UrlDetected>
              )}
            </UrlInputWrap>

            <FieldLabel style={{ marginTop: 20, marginBottom: 10 }}>Platform</FieldLabel>
            <PlatformGrid>
              {PLATFORMS.map(p => (
                <PlatformChip
                  key={p.id}
                  selected={platform === p.id}
                  onClick={() => setPlatform(p.id)}
                >
                  <PlatformEmoji>{getPlatformEmoji(p.id)}</PlatformEmoji>
                  <PlatformLabel>{p.label}</PlatformLabel>
                </PlatformChip>
              ))}
            </PlatformGrid>

            <FieldLabel style={{ marginTop: 16, marginBottom: 10 }}>Post type</FieldLabel>
            <TypePills>
              {(PLATFORMS.find(p => p.id === platform)?.types || []).map(t => (
                <TypePill key={t} selected={postType === t} onClick={() => setPostType(t)}>
                  {POST_TYPE_LABELS[t] || t}
                </TypePill>
              ))}
            </TypePills>

            <PrimaryBtn style={{ marginTop: 24 }} onClick={() => setStep(2)}>Next</PrimaryBtn>
          </StepPanel>
        )}

        {step === 2 && (
          <StepPanel key="s2" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}>
            <StepQ>Brand and collab type</StepQ>
            <StepSub>No brand? Select "My own content" — original posts count too.</StepSub>

            <FieldLabel>Brand name</FieldLabel>
            <TextInput
              value={brandName}
              onChange={e => setBrandName(e.target.value)}
              placeholder="e.g. Kopari Beauty"
            />

            <FieldLabel style={{ marginTop: 16, marginBottom: 10 }}>Collab type</FieldLabel>
            <CollabPills>
              {COLLAB_TYPES.map(c => (
                <CollabPill key={c.id} selected={collabType === c.id} onClick={() => setCollabType(c.id)}>
                  {c.label}
                </CollabPill>
              ))}
            </CollabPills>

            <PrimaryBtn style={{ marginTop: 24 }} onClick={() => setStep(3)}>Next</PrimaryBtn>
          </StepPanel>
        )}

        {step === 3 && (
          <StepPanel key="s3" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }}>
            <StepQ>Engagement numbers</StepQ>
            <StepSub>Add what you can. These are shown to brands as proof of performance. All optional.</StepSub>

            <StatGrid>
              {[
                { key: 'views',    label: 'Views' },
                { key: 'likes',    label: 'Likes' },
                { key: 'comments', label: 'Comments' },
                { key: 'shares',   label: 'Shares' },
              ].map(({ key, label }) => (
                <StatInputWrap key={key}>
                  <StatInputLabel>{label}</StatInputLabel>
                  <StatInput
                    value={stats[key]}
                    onChange={e => setStats(s => ({ ...s, [key]: e.target.value }))}
                    placeholder="0"
                  />
                </StatInputWrap>
              ))}
            </StatGrid>

            {/* Preview card */}
            <PreviewLabel>Preview</PreviewLabel>
            <PostPreviewCard>
              <PostPreviewImg niche="default">
                <PostPreviewEmoji>{getPlatformEmoji(platform)}</PostPreviewEmoji>
                <PostPreviewBadge>{POST_TYPE_LABELS[postType] || postType}</PostPreviewBadge>
              </PostPreviewImg>
              <PostPreviewBody>
                <PostPreviewBrand>
                  {brandName || 'My own content'}
                  <CollabChip type={collabType}>{collabType}</CollabChip>
                </PostPreviewBrand>
                <PostPreviewStats>
                  {stats.views && <span>{formatNumber(parseInt(stats.views.replace(/\D/g,'')))} views</span>}
                  {stats.likes && <span>{formatNumber(parseInt(stats.likes.replace(/\D/g,'')))} likes</span>}
                  {stats.comments && <span>{formatNumber(parseInt(stats.comments.replace(/\D/g,'')))} comments</span>}
                </PostPreviewStats>
              </PostPreviewBody>
            </PostPreviewCard>

            <PrimaryBtn style={{ marginTop: 16 }} onClick={onSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save to my portfolio'}
            </PrimaryBtn>
            <SecondaryBtn style={{ marginTop: 8 }} onClick={() => setStep(2)}>Edit details</SecondaryBtn>
          </StepPanel>
        )}
      </AnimatePresence>
    </Container>
  );
};

// ── Guide view (no content yet) ────────────────────────────────
const GuideView = ({ niche, onBack, onDone }) => {
  const products = NICHE_PRODUCTS[niche] || NICHE_PRODUCTS.default;
  const hook = SCRIPT_HOOKS[niche] || SCRIPT_HOOKS.default;
  const [selected, setSelected] = useState(null);

  return (
    <Container>
      <StepNav>
        <BackBtn onClick={onBack}>← Back</BackBtn>
        <StepTitle>Create your first post</StepTitle>
        <div />
      </StepNav>

      <GuideDark>
        <GuideEyebrow>No posts yet? No problem.</GuideEyebrow>
        <GuideTitle>Create your first collab-worthy post today</GuideTitle>
        <GuideSub>Based on your niche. Takes about 20 minutes to film.</GuideSub>
      </GuideDark>

      <GuideSection>
        <GuideSectionLabel>Products you probably own</GuideSectionLabel>
        <ProductScroll>
          {products.map((p, i) => (
            <ProductCard key={i} selected={selected === i} onClick={() => setSelected(i)}>
              <ProductEmoji>{p.emoji}</ProductEmoji>
              <ProductName>{p.name}</ProductName>
            </ProductCard>
          ))}
        </ProductScroll>
      </GuideSection>

      <GuideSection>
        <GuideSectionLabel>What to film — 3 shots</GuideSectionLabel>
        {[
          { n: 1, title: 'The before', body: 'Show the product or your starting point. Natural light. Keep it real. 4 seconds.' },
          { n: 2, title: 'The use', body: 'Apply, wear, or use it. No need to be perfect. The authenticity is the point. 8 seconds.' },
          { n: 3, title: 'Your reaction', body: 'Talk to camera. Tell them one thing you noticed. This is where the comments come from. 10 seconds.' },
        ].map(s => (
          <SequenceItem key={s.n}>
            <SeqNum>{s.n}</SeqNum>
            <SeqBody>
              <SeqTitle>{s.title}</SeqTitle>
              <SeqText>{s.body}</SeqText>
            </SeqBody>
          </SequenceItem>
        ))}
      </GuideSection>

      <GuideSection>
        <GuideSectionLabel>Hook to open with</GuideSectionLabel>
        <ScriptCard>
          <ScriptLabel>First 2 seconds</ScriptLabel>
          <ScriptText>{hook}</ScriptText>
        </ScriptCard>
      </GuideSection>

      <GuideSection style={{ paddingBottom: 32 }}>
        <PrimaryBtn onClick={onDone}>I filmed it — add to my kit</PrimaryBtn>
      </GuideSection>
    </Container>
  );
};

// ── Rates editor ───────────────────────────────────────────────
const RatesEditor = ({ currentUser }) => {
  const [reel, setReel]     = useState(currentUser?.rates_reel || '');
  const [tiktok, setTiktok] = useState(currentUser?.rates_tiktok || '');
  const [photo, setPhoto]   = useState(currentUser?.rates_photo || '');
  const [gifted, setGifted] = useState(currentUser?.rates_gifted !== false);
  const [saved, setSaved]   = useState(false);

  const save = async () => {
    try {
      await api.patch('/api/portfolio/settings', {
        rates_reel: reel ? parseInt(reel) : null,
        rates_tiktok: tiktok ? parseInt(tiktok) : null,
        rates_photo: photo ? parseInt(photo) : null,
        rates_gifted: gifted,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) { message.error('Failed to save rates'); }
  };

  return (
    <RatesWrap>
      {[
        { label: 'Instagram Reel', val: reel, set: setReel },
        { label: 'TikTok video',   val: tiktok, set: setTiktok },
        { label: 'Photo post',     val: photo, set: setPhoto },
      ].map(({ label, val, set }) => (
        <RateRow key={label}>
          <RateLabel>{label}</RateLabel>
          <RateInput
            value={val}
            onChange={e => set(e.target.value)}
            placeholder="from $..."
          />
        </RateRow>
      ))}
      <RateRow>
        <RateLabel>Open to gifted collabs</RateLabel>
        <GiftedToggle active={gifted} onClick={() => setGifted(!gifted)}>
          {gifted ? 'Yes' : 'No'}
        </GiftedToggle>
      </RateRow>
      <PrimaryBtn onClick={save} style={{ marginTop: 12 }}>
        {saved ? 'Saved' : 'Save rates'}
      </PrimaryBtn>
    </RatesWrap>
  );
};

// ── Helpers ────────────────────────────────────────────────────
const getPlatformEmoji = (platform) => {
  if (platform === 'tiktok') return '▶';
  if (platform === 'youtube') return '▶';
  return '📸';
};

// ── Styled components ──────────────────────────────────────────
const shimmer = keyframes`0%{background-position:200% 0}100%{background-position:-200% 0}`;

const Container = styled.div`
  max-width: 640px;
  margin: 0 auto;
  padding-bottom: 80px;
`;

const DashHeader = styled.div`
  padding: 20px 16px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
`;

const DashTitle = styled.h1`
  font-size: 22px;
  font-weight: 900;
  color: #0F0F0F;
`;

const DashActions = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
`;

const PreviewBtn = styled.button`
  background: #0F0F0F;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  padding: 8px 16px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
`;

const ShareChip = styled.button`
  background: #F3F4F6;
  color: #6B7280;
  font-size: 11.5px;
  font-weight: 500;
  padding: 7px 12px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  @media (max-width: 380px) { max-width: 130px; }
`;

const ViewsCard = styled.div`
  margin: 0 16px 16px;
  background: #fff;
  border-radius: 16px;
  padding: 14px;
  border: 1.5px solid #E5E7EB;
  display: flex;
  align-items: center;
  gap: 16px;
  @media (max-width: 480px) { flex-direction: column; align-items: flex-start; gap: 10px; }
`;

const ViewsLeft = styled.div`
  flex-shrink: 0;
`;

const ViewsNum = styled.div`
  font-size: 28px;
  font-weight: 900;
  color: #0F0F0F;
  line-height: 1;
`;

const ViewsLabel = styled.div`
  font-size: 11px;
  color: #9CA3AF;
  margin-top: 3px;
`;

const ViewsList = styled.div`
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
`;

const ViewRow = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
`;

const ViewDot = styled.div`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
`;

const ViewText = styled.div`
  font-size: 12px;
  color: #374151;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
`;

const ViewTime = styled.span`
  color: #9CA3AF;
`;

const EmptyState = styled.div`
  margin: 16px 16px;
  border: 2px dashed #E5E7EB;
  border-radius: 18px;
  padding: 32px 20px;
  text-align: center;
`;

const EmptyTitle = styled.div`
  font-size: 17px;
  font-weight: 800;
  color: #0F0F0F;
  margin-bottom: 8px;
`;

const EmptyBody = styled.div`
  font-size: 13px;
  color: #6B7280;
  line-height: 1.5;
  margin-bottom: 20px;
`;

const EmptyActions = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 280px;
  margin: 0 auto;
`;

const SectionLabel = styled.div`
  font-size: 11px;
  font-weight: 800;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  padding: 12px 16px 8px;
`;

const PostGrid = styled.div`
  padding: 0 16px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  @media (max-width: 480px) { grid-template-columns: repeat(2, 1fr); }
`;

const PostThumb = styled.div`
  position: relative;
  background: #fff;
  border-radius: 14px;
  overflow: hidden;
  border: 1.5px solid #F3F4F6;
`;

const PostThumbImg = styled.div`
  padding-top: 100%;
  position: relative;
  background: ${p =>
    p.niche === 'beauty' || p.niche === 'skincare' ? 'linear-gradient(135deg,#FDF2F8,#F5F3FF)' :
    p.niche === 'fitness' || p.niche === 'wellness' ? 'linear-gradient(135deg,#F0FDF4,#ECFDF5)' :
    p.niche === 'food' ? 'linear-gradient(135deg,#FFFBEB,#FEF3C7)' :
    'linear-gradient(135deg,#EFF6FF,#F5F3FF)'
  };
`;

const PostThumbEmoji = styled.div`
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
`;

const PostThumbBadge = styled.div`
  position: absolute;
  bottom: 6px;
  left: 6px;
  background: rgba(0,0,0,0.6);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 5px;
`;

const PostThumbInfo = styled.div`
  padding: 8px 8px 9px;
`;

const PostThumbBrand = styled.div`
  font-size: 11.5px;
  font-weight: 700;
  color: #0F0F0F;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`;

const PostThumbMeta = styled.div`
  font-size: 10px;
  color: #9CA3AF;
  margin-top: 2px;
  text-transform: capitalize;
`;

const PostThumbDelete = styled.button`
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(0,0,0,0.5);
  color: #fff;
  font-size: 10px;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
`;

const AddPostCard = styled.div`
  border: 2px dashed #E5E7EB;
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  cursor: pointer;
  transition: border-color 0.15s;
  &:hover { border-color: #7C3AED; }
`;

const AddPostIcon = styled.div`
  font-size: 24px;
  color: #9CA3AF;
  margin-bottom: 4px;
`;

const AddPostLabel = styled.div`
  font-size: 11px;
  font-weight: 600;
  color: #9CA3AF;
`;

const RatesSection = styled.div`
  margin: 20px 16px 0;
`;

const RatesSectionHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
`;

const ToggleIcon = styled.span`
  font-size: 11px;
  color: #9CA3AF;
`;

const RatesWrap = styled.div`
  background: #fff;
  border: 1.5px solid #E5E7EB;
  border-radius: 14px;
  padding: 16px;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 10px;
`;

const RateRow = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
`;

const RateLabel = styled.div`
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  flex: 1;
`;

const RateInput = styled.input`
  width: 110px;
  padding: 8px 10px;
  border: 1.5px solid #E5E7EB;
  border-radius: 10px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  text-align: right;
  &:focus { border-color: #7C3AED; }
`;

const GiftedToggle = styled.button`
  padding: 7px 16px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
  border: 1.5px solid ${p => p.active ? '#0F0F0F' : '#E5E7EB'};
  background: ${p => p.active ? '#0F0F0F' : '#fff'};
  color: ${p => p.active ? '#fff' : '#9CA3AF'};
  cursor: pointer;
`;

// ── Step-flow styles ───────────────────────────────────────────

const StepNav = styled.div`
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #F3F4F6;
  background: #fff;
`;

const BackBtn = styled.button`
  font-size: 13px;
  font-weight: 600;
  color: #6B7280;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
`;

const StepTitle = styled.div`
  font-size: 14px;
  font-weight: 800;
  color: #0F0F0F;
`;

const StepCount = styled.div`
  font-size: 12px;
  font-weight: 700;
  color: #9CA3AF;
`;

const ProgressWrap = styled.div`
  padding: 0 16px;
  background: #fff;
  border-bottom: 1px solid #F3F4F6;
  padding-bottom: 12px;
`;

const ProgressBar = styled.div`
  height: 3px;
  background: #F3F4F6;
  border-radius: 2px;
  overflow: hidden;
  margin-top: 10px;
`;

const ProgressFill = styled.div`
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, #7C3AED, #E11D48);
  transition: width 0.3s ease;
`;

const StepPanel = styled(motion.div)`
  padding: 24px 16px 20px;
`;

const StepQ = styled.div`
  font-size: 20px;
  font-weight: 900;
  color: #0F0F0F;
  line-height: 1.2;
  margin-bottom: 6px;
`;

const StepSub = styled.div`
  font-size: 13px;
  color: #6B7280;
  line-height: 1.45;
  margin-bottom: 20px;
`;

const FieldLabel = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 8px;
`;

const UrlInputWrap = styled.div`
  border: 2px solid ${p => p.active ? '#7C3AED' : '#E5E7EB'};
  border-radius: 14px;
  overflow: hidden;
  transition: border-color 0.2s;
`;

const UrlInner = styled.div`
  display: flex;
  align-items: center;
  padding: 13px 14px;
  gap: 10px;
`;

const UrlIcon = styled.div`
  font-size: 18px;
  flex-shrink: 0;
`;

const UrlInput = styled.input`
  flex: 1;
  font-size: 14px;
  color: #0F0F0F;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  &::placeholder { color: #D1D5DB; }
`;

const UrlSpinner = styled.div`
  font-size: 12px;
  color: #9CA3AF;
`;

const UrlDetected = styled.div`
  padding: 8px 14px 10px;
  background: #F0FDF4;
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #A7F3D0;
`;

const UrlDot = styled.div`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10B981;
  flex-shrink: 0;
`;

const UrlDetectedText = styled.div`
  font-size: 11.5px;
  font-weight: 600;
  color: #059669;
`;

const PlatformGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  @media (max-width: 480px) { grid-template-columns: repeat(3, 1fr); }
`;

const PlatformChip = styled.div`
  padding: 12px 8px;
  border: 2px solid ${p => p.selected ? '#0F0F0F' : '#E5E7EB'};
  background: ${p => p.selected ? '#0F0F0F' : '#fff'};
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.15s;
`;

const PlatformEmoji = styled.div`
  font-size: 22px;
`;

const PlatformLabel = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: ${p => p.selected ? '#fff' : '#374151'};
`;

// Fix: PlatformChip children inherit selected color
// When using styled-components, pass selected as prop to children if needed

const TypePills = styled.div`
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
`;

const TypePill = styled.button`
  padding: 8px 16px;
  border: 2px solid ${p => p.selected ? '#0F0F0F' : '#E5E7EB'};
  background: ${p => p.selected ? '#0F0F0F' : '#fff'};
  color: ${p => p.selected ? '#fff' : '#374151'};
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
`;

const TextInput = styled.input`
  width: 100%;
  padding: 13px 14px;
  border: 2px solid #E5E7EB;
  border-radius: 14px;
  font-size: 14px;
  color: #0F0F0F;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
  &:focus { border-color: #7C3AED; }
  &::placeholder { color: #D1D5DB; }
`;

const CollabPills = styled.div`
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
`;

const CollabPill = styled.button`
  padding: 9px 18px;
  border: 2px solid ${p => p.selected ? '#0F0F0F' : '#E5E7EB'};
  background: ${p => p.selected ? '#0F0F0F' : '#fff'};
  color: ${p => p.selected ? '#fff' : '#374151'};
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
`;

const StatGrid = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 16px;
`;

const StatInputWrap = styled.div`
  background: #F9FAFB;
  border: 1.5px solid #E5E7EB;
  border-radius: 12px;
  padding: 11px 13px;
`;

const StatInputLabel = styled.div`
  font-size: 10px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 4px;
`;

const StatInput = styled.input`
  font-size: 16px;
  font-weight: 700;
  color: #0F0F0F;
  border: none;
  outline: none;
  background: transparent;
  width: 100%;
  font-family: inherit;
  &::placeholder { color: #D1D5DB; font-weight: 400; font-size: 14px; }
`;

const PreviewLabel = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 8px;
`;

const PostPreviewCard = styled.div`
  border: 1.5px solid #E5E7EB;
  border-radius: 16px;
  overflow: hidden;
  background: #fff;
`;

const PostPreviewImg = styled.div`
  height: 100px;
  background: linear-gradient(135deg, #F5F3FF, #FDF2F8);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
`;

const PostPreviewEmoji = styled.div`
  font-size: 36px;
`;

const PostPreviewBadge = styled.div`
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: #0F0F0F;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 7px;
`;

const PostPreviewBody = styled.div`
  padding: 12px 13px;
`;

const PostPreviewBrand = styled.div`
  font-size: 13px;
  font-weight: 800;
  color: #0F0F0F;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
`;

const CollabChip = styled.span`
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 8px;
  text-transform: capitalize;
  background: ${p =>
    p.type === 'paid' ? '#D1FAE5' :
    p.type === 'gifted' ? '#EFF6FF' :
    '#F3F4F6'};
  color: ${p =>
    p.type === 'paid' ? '#065F46' :
    p.type === 'gifted' ? '#1D4ED8' :
    '#6B7280'};
`;

const PostPreviewStats = styled.div`
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #6B7280;
  span { font-weight: 600; }
`;

// ── Guide styles ───────────────────────────────────────────────

const GuideDark = styled.div`
  background: #0F0F0F;
  padding: 20px 16px 18px;
`;

const GuideEyebrow = styled.div`
  font-size: 10px;
  font-weight: 700;
  color: rgba(255,255,255,0.45);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
`;

const GuideTitle = styled.div`
  font-size: 19px;
  font-weight: 900;
  color: #fff;
  line-height: 1.2;
  margin-bottom: 4px;
`;

const GuideSub = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.5);
`;

const GuideSection = styled.div`
  padding: 16px 16px 0;
`;

const GuideSectionLabel = styled.div`
  font-size: 11px;
  font-weight: 800;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 10px;
`;

const ProductScroll = styled.div`
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
  -webkit-overflow-scrolling: touch;
  &::-webkit-scrollbar { display: none; }
`;

const ProductCard = styled.div`
  flex-shrink: 0;
  width: 88px;
  background: ${p => p.selected ? '#FFF1F2' : '#fff'};
  border: 2px solid ${p => p.selected ? '#E11D48' : '#E5E7EB'};
  border-radius: 14px;
  padding: 12px 8px;
  text-align: center;
  cursor: pointer;
  transition: all 0.15s;
`;

const ProductEmoji = styled.div`
  font-size: 26px;
  margin-bottom: 6px;
`;

const ProductName = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #0F0F0F;
  line-height: 1.3;
`;

const SequenceItem = styled.div`
  background: #fff;
  border-radius: 12px;
  padding: 12px 13px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1.5px solid #F3F4F6;
  margin-bottom: 8px;
`;

const SeqNum = styled.div`
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #0F0F0F;
  color: #fff;
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
`;

const SeqBody = styled.div``;

const SeqTitle = styled.div`
  font-size: 13px;
  font-weight: 700;
  color: #0F0F0F;
`;

const SeqText = styled.div`
  font-size: 12px;
  color: #6B7280;
  margin-top: 2px;
  line-height: 1.45;
`;

const ScriptCard = styled.div`
  background: #F5F3FF;
  border: 1.5px solid #DDD6FE;
  border-radius: 14px;
  padding: 14px;
`;

const ScriptLabel = styled.div`
  font-size: 10px;
  font-weight: 800;
  color: #7C3AED;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 8px;
`;

const ScriptText = styled.div`
  font-size: 13.5px;
  color: #374151;
  line-height: 1.6;
  font-style: italic;
`;

// ── Shared buttons ─────────────────────────────────────────────

const PrimaryBtn = styled.button`
  width: 100%;
  background: #0F0F0F;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  padding: 15px;
  border-radius: 14px;
  border: none;
  cursor: pointer;
  transition: opacity 0.15s;
  &:disabled { opacity: 0.5; cursor: not-allowed; }
  &:hover:not(:disabled) { opacity: 0.88; }
`;

const SecondaryBtn = styled.button`
  width: 100%;
  background: #F3F4F6;
  color: #374151;
  font-size: 13px;
  font-weight: 600;
  padding: 13px;
  border-radius: 14px;
  border: none;
  cursor: pointer;
  &:hover { background: #E5E7EB; }
`;

export default PortfolioBuilder;
```

---

## Phase 4 — Public kit page

**File:** Create at your public route — either `pages/kit/[username].js` (Next.js) or `src/pages/PublicMediaKit.js` (React Router), depending on your stack.

```jsx
import React, { useState, useEffect } from 'react';
import styled from 'styled-components';
import axios from 'axios';

const API_BASE = process.env.REACT_APP_API_URL || 'https://api.newcollab.co';

const formatNumber = (n) => {
  if (!n) return null;
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return n.toString();
};

const PLATFORM_LABEL = { instagram: 'IG', tiktok: 'TikTok', youtube: 'YT' };
const POST_TYPE_LABEL = {
  reel: 'Reel', photo: 'Photo', story: 'Story',
  tiktok: 'TikTok', youtube: 'Video', short: 'Short',
};
const COLLAB_COLOR = {
  paid:    { bg: '#D1FAE5', color: '#065F46' },
  gifted:  { bg: '#EFF6FF', color: '#1D4ED8' },
  organic: { bg: '#F3F4F6', color: '#6B7280' },
  own:     { bg: '#F3F4F6', color: '#6B7280' },
};

const PublicMediaKit = ({ username }) => {
  const [kit, setKit]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!username) return;
    axios.get(`${API_BASE}/api/public/kit/${username}`)
      .then(r => setKit(r.data))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [username]);

  if (loading) return <KitPage><KitLoading>Loading...</KitLoading></KitPage>;
  if (notFound) return <KitPage><KitLoading>Kit not found</KitLoading></KitPage>;
  if (!kit) return null;

  const { posts = [], rates_reel, rates_tiktok, rates_photo, rates_gifted } = kit;
  const hasRates = rates_reel || rates_tiktok || rates_photo;
  const brands = [...new Set(posts.filter(p => p.brand_name && p.collab_type !== 'own').map(p => p.brand_name))];

  return (
    <KitPage>
      <KitWrap>

        {/* Header */}
        <KitHeader>
          <KitHeaderInner>
            <KitAvatar>
              {kit.avatar_url
                ? <img src={kit.avatar_url} alt={kit.username} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                : <span>{(kit.first_name || kit.username || '?').charAt(0).toUpperCase()}</span>
              }
            </KitAvatar>
            <KitHeaderInfo>
              <KitName>{kit.first_name || kit.username}</KitName>
              {kit.tagline && <KitTagline>{kit.tagline}</KitTagline>}
              {kit.niches?.length > 0 && (
                <KitNicheTags>
                  {kit.niches.slice(0, 4).map(n => (
                    <KitNicheTag key={n}>{n}</KitNicheTag>
                  ))}
                </KitNicheTags>
              )}
            </KitHeaderInfo>
          </KitHeaderInner>
        </KitHeader>

        {/* Stats strip */}
        <KitStatsStrip>
          {kit.follower_count && (
            <KitStatItem>
              <KitStatVal>{formatNumber(kit.follower_count)}</KitStatVal>
              <KitStatSub>followers</KitStatSub>
            </KitStatItem>
          )}
          {kit.engagement_rate > 0 && (
            <KitStatItem>
              <KitStatVal>{kit.engagement_rate.toFixed(1)}%</KitStatVal>
              <KitStatSub>engagement</KitStatSub>
            </KitStatItem>
          )}
          {kit.country && (
            <KitStatItem>
              <KitStatVal>{kit.country}</KitStatVal>
              <KitStatSub>market</KitStatSub>
            </KitStatItem>
          )}
          {posts.length > 0 && (
            <KitStatItem>
              <KitStatVal>{posts.length}</KitStatVal>
              <KitStatSub>posts</KitStatSub>
            </KitStatItem>
          )}
        </KitStatsStrip>

        {/* Portfolio grid */}
        {posts.length > 0 && (
          <>
            <KitSectionLabel>Portfolio</KitSectionLabel>
            <KitGrid>
              {posts.map((post, i) => {
                const collab = COLLAB_COLOR[post.collab_type] || COLLAB_COLOR.own;
                const isFeatured = post.is_featured || i === 0;
                return (
                  <KitPostCard key={post.id} featured={isFeatured} href={post.post_url || undefined} target="_blank" rel="noopener noreferrer" as={post.post_url ? 'a' : 'div'}>
                    <KitPostImg featured={isFeatured} niche={kit.niches?.[0]?.toLowerCase()}>
                      <KitPostEmoji>{post.platform === 'tiktok' ? '▶' : post.platform === 'youtube' ? '▶' : '📸'}</KitPostEmoji>
                      <KitPostPlatformBadge platform={post.platform}>
                        {PLATFORM_LABEL[post.platform] || post.platform} · {POST_TYPE_LABEL[post.post_type] || post.post_type}
                      </KitPostPlatformBadge>
                    </KitPostImg>
                    <KitPostBody>
                      <KitPostBrandRow>
                        <KitPostBrand>{post.brand_name || 'Original content'}</KitPostBrand>
                        <KitPostCollabChip style={{ background: collab.bg, color: collab.color }}>
                          {post.collab_type === 'own' ? 'Original' : post.collab_type}
                        </KitPostCollabChip>
                      </KitPostBrandRow>
                      {(post.views || post.likes) > 0 && (
                        <KitPostStats>
                          {post.views > 0 && <span>{formatNumber(post.views)} views</span>}
                          {post.likes > 0 && <span>{formatNumber(post.likes)} likes</span>}
                          {post.comments > 0 && <span>{formatNumber(post.comments)} comments</span>}
                        </KitPostStats>
                      )}
                    </KitPostBody>
                  </KitPostCard>
                );
              })}
            </KitGrid>
          </>
        )}

        {/* Brands worked with */}
        {brands.length > 0 && (
          <>
            <KitSectionLabel>Brands worked with</KitSectionLabel>
            <BrandsRow>
              {brands.map(b => <BrandPill key={b}>{b}</BrandPill>)}
              {rates_gifted && <BrandPill open>Open to gifted</BrandPill>}
            </BrandsRow>
          </>
        )}

        {/* Rate card */}
        {hasRates && (
          <>
            <KitSectionLabel>Packages</KitSectionLabel>
            <RateCard>
              {rates_reel && (
                <RateCardRow>
                  <RateCardLabel>Instagram Reel + Story</RateCardLabel>
                  <RateCardPrice>from ${rates_reel.toLocaleString()}</RateCardPrice>
                </RateCardRow>
              )}
              {rates_tiktok && (
                <RateCardRow>
                  <RateCardLabel>TikTok video</RateCardLabel>
                  <RateCardPrice>from ${rates_tiktok.toLocaleString()}</RateCardPrice>
                </RateCardRow>
              )}
              {rates_photo && (
                <RateCardRow>
                  <RateCardLabel>Photo post</RateCardLabel>
                  <RateCardPrice>from ${rates_photo.toLocaleString()}</RateCardPrice>
                </RateCardRow>
              )}
              {rates_gifted && (
                <RateCardRow gifted>
                  <RateCardLabel>Gifted collabs</RateCardLabel>
                  <RateCardPrice style={{ color: '#059669' }}>Open</RateCardPrice>
                </RateCardRow>
              )}
            </RateCard>
          </>
        )}

        {/* CTA */}
        <KitCTA>
          <KitCTAText>
            <KitCTATitle>Interested in working together?</KitCTATitle>
            <KitCTASub>Usually responds within 48 hours</KitCTASub>
          </KitCTAText>
          <KitCTABtn href={`mailto:?subject=Collab with ${kit.username}`}>
            Get in touch
          </KitCTABtn>
        </KitCTA>

        <KitFooter>
          <KitFooterLeft>newcollab.co/kit/{kit.username}</KitFooterLeft>
          <KitFooterRight>Media kit by <KitBrand>Newcollab</KitBrand></KitFooterRight>
        </KitFooter>

      </KitWrap>
    </KitPage>
  );
};

// ── Public page styles ─────────────────────────────────────────

const KitPage = styled.div`
  min-height: 100vh;
  background: #F5F5F3;
  padding: 24px 16px 60px;
  @media (max-width: 480px) { padding: 0 0 60px; }
`;

const KitWrap = styled.div`
  max-width: 720px;
  margin: 0 auto;
  background: #fff;
  border-radius: 24px;
  overflow: hidden;
  box-shadow: 0 8px 40px rgba(0,0,0,0.08);
  @media (max-width: 480px) { border-radius: 0; box-shadow: none; }
`;

const KitLoading = styled.div`
  text-align: center;
  padding: 80px 20px;
  font-size: 14px;
  color: #9CA3AF;
`;

const KitHeader = styled.div`
  background: #0F0F0F;
  padding: 28px 24px 24px;
  @media (max-width: 480px) { padding: 24px 16px 20px; }
`;

const KitHeaderInner = styled.div`
  display: flex;
  align-items: flex-start;
  gap: 16px;
`;

const KitAvatar = styled.div`
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: linear-gradient(135deg, #7C3AED, #E11D48);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 900;
  color: #fff;
  flex-shrink: 0;
  border: 2px solid rgba(255,255,255,0.15);
  overflow: hidden;
  @media (max-width: 480px) { width: 52px; height: 52px; font-size: 20px; }
`;

const KitHeaderInfo = styled.div`
  flex: 1;
`;

const KitName = styled.div`
  font-size: 22px;
  font-weight: 900;
  color: #fff;
  margin-bottom: 4px;
  @media (max-width: 480px) { font-size: 18px; }
`;

const KitTagline = styled.div`
  font-size: 13px;
  color: rgba(255,255,255,0.6);
  line-height: 1.4;
  margin-bottom: 10px;
`;

const KitNicheTags = styled.div`
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
`;

const KitNicheTag = styled.span`
  background: rgba(255,255,255,0.1);
  color: rgba(255,255,255,0.8);
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 20px;
  text-transform: capitalize;
`;

const KitStatsStrip = styled.div`
  display: flex;
  border-bottom: 1px solid #F3F4F6;
  overflow-x: auto;
  &::-webkit-scrollbar { display: none; }
`;

const KitStatItem = styled.div`
  flex: 1;
  min-width: 80px;
  padding: 16px 12px;
  text-align: center;
  border-right: 1px solid #F3F4F6;
  &:last-child { border-right: none; }
`;

const KitStatVal = styled.div`
  font-size: 18px;
  font-weight: 900;
  color: #0F0F0F;
`;

const KitStatSub = styled.div`
  font-size: 10px;
  color: #9CA3AF;
  margin-top: 2px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
`;

const KitSectionLabel = styled.div`
  font-size: 10px;
  font-weight: 800;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 16px 20px 8px;
`;

const KitGrid = styled.div`
  padding: 0 16px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  @media (max-width: 600px) { grid-template-columns: repeat(2, 1fr); }
  @media (max-width: 380px) { grid-template-columns: 1fr 1fr; gap: 6px; padding: 0 12px; }
`;

const KitPostCard = styled.a`
  background: #F9FAFB;
  border-radius: 14px;
  overflow: hidden;
  border: 1.5px solid #F3F4F6;
  text-decoration: none;
  display: block;
  transition: transform 0.15s;
  &:hover { transform: translateY(-2px); }
  ${p => p.featured ? `grid-column: span 1;` : ''}
`;

const KitPostImg = styled.div`
  padding-top: 100%;
  position: relative;
  background: ${p =>
    p.niche === 'beauty' || p.niche === 'skincare' ? 'linear-gradient(135deg,#FDF2F8,#F5F3FF)' :
    p.niche === 'fitness' || p.niche === 'wellness' ? 'linear-gradient(135deg,#F0FDF4,#ECFDF5)' :
    p.niche === 'food' ? 'linear-gradient(135deg,#FFFBEB,#FEF3C7)' :
    'linear-gradient(135deg,#EFF6FF,#F5F3FF)'
  };
`;

const KitPostEmoji = styled.div`
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
`;

const KitPostPlatformBadge = styled.div`
  position: absolute;
  top: 6px;
  left: 6px;
  font-size: 9px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 6px;
  background: ${p => p.platform === 'tiktok' ? '#0F0F0F' : p.platform === 'youtube' ? '#EF4444' : '#E11D48'};
  color: #fff;
  text-transform: uppercase;
  letter-spacing: 0.2px;
`;

const KitPostBody = styled.div`
  padding: 8px 10px 10px;
`;

const KitPostBrandRow = styled.div`
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 4px;
`;

const KitPostBrand = styled.div`
  font-size: 11.5px;
  font-weight: 700;
  color: #0F0F0F;
`;

const KitPostCollabChip = styled.span`
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 6px;
  text-transform: capitalize;
`;

const KitPostStats = styled.div`
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 10px;
  color: #9CA3AF;
  font-weight: 500;
`;

const BrandsRow = styled.div`
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 0 20px 16px;
`;

const BrandPill = styled.div`
  padding: 6px 14px;
  background: ${p => p.open ? '#F0FDF4' : '#F9FAFB'};
  border: 1px solid ${p => p.open ? '#A7F3D0' : '#E5E7EB'};
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
  color: ${p => p.open ? '#059669' : '#374151'};
`;

const RateCard = styled.div`
  margin: 0 16px 16px;
  border: 1.5px solid #E5E7EB;
  border-radius: 16px;
  overflow: hidden;
`;

const RateCardRow = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px;
  border-bottom: 1px solid #F3F4F6;
  background: ${p => p.gifted ? '#F0FDF4' : '#fff'};
  &:last-child { border-bottom: none; }
`;

const RateCardLabel = styled.div`
  font-size: 13px;
  font-weight: 600;
  color: #374151;
`;

const RateCardPrice = styled.div`
  font-size: 14px;
  font-weight: 900;
  color: #0F0F0F;
`;

const KitCTA = styled.div`
  margin: 8px 16px 0;
  background: #0F0F0F;
  border-radius: 18px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  @media (max-width: 480px) { flex-direction: column; text-align: center; gap: 14px; margin: 8px 12px 0; }
`;

const KitCTAText = styled.div``;

const KitCTATitle = styled.div`
  font-size: 16px;
  font-weight: 900;
  color: #fff;
  margin-bottom: 3px;
`;

const KitCTASub = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.5);
`;

const KitCTABtn = styled.a`
  background: #fff;
  color: #0F0F0F;
  font-size: 13px;
  font-weight: 800;
  padding: 12px 22px;
  border-radius: 12px;
  border: none;
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
  flex-shrink: 0;
  display: inline-block;
  @media (max-width: 480px) { width: 100%; text-align: center; }
`;

const KitFooter = styled.div`
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid #F3F4F6;
  margin-top: 16px;
  @media (max-width: 480px) { flex-direction: column; gap: 4px; text-align: center; padding: 12px; }
`;

const KitFooterLeft = styled.div`
  font-size: 12px;
  color: #9CA3AF;
`;

const KitFooterRight = styled.div`
  font-size: 12px;
  color: #9CA3AF;
`;

const KitBrand = styled.span`
  color: #E11D48;
  font-weight: 800;
`;

export default PublicMediaKit;
```

---

## Phase 5 — Wire up in app routing

### Add route for public kit page

If using **React Router** (`App.js` or router config):

```jsx
import PublicMediaKit from './pages/PublicMediaKit';

// Add to your routes:
<Route path="/kit/:username" element={<PublicMediaKitWrapper />} />

// Wrapper to extract username param:
const PublicMediaKitWrapper = () => {
  const { username } = useParams();
  return <PublicMediaKit username={username} />;
};
```

If using **Next.js**, create `pages/kit/[username].js`:

```jsx
import PublicMediaKit from '../../src/components/PublicMediaKit';

export default function KitPage({ params }) {
  return <PublicMediaKit username={params.username} />;
}
```

### Wire `PortfolioBuilder` into the "My Kit" tab

In whatever component renders the My Kit tab content (the component that handles Discover / Saved / For You / My Kit navigation):

```jsx
import PortfolioBuilder from './PortfolioBuilder';

// Find where My Kit tab renders (currently likely empty or basic):
{activeTab === 'myKit' && (
  <PortfolioBuilder currentUser={currentUser} />
)}
```

---

## Checklist

### Phase 1 — Database
- [ ] `portfolio_posts` table created
- [ ] `kit_views` table created
- [ ] `users` table: `kit_tagline`, `kit_published`, `kit_slug`, `rates_*` columns added

### Phase 2 — Backend
- [ ] `GET /api/portfolio/posts` — returns creator's posts
- [ ] `POST /api/portfolio/posts` — creates a post entry
- [ ] `PATCH /api/portfolio/posts/:id` — updates post fields
- [ ] `DELETE /api/portfolio/posts/:id` — removes a post
- [ ] `PATCH /api/portfolio/settings` — saves tagline, rates, publish flag
- [ ] `GET /api/public/kit/:slug` — public, no auth, logs view
- [ ] `GET /api/portfolio/views` — creator's kit view stats
- [ ] `POST /api/portfolio/detect-url` — detects platform from URL

### Phase 3 — PortfolioBuilder.js
- [ ] `dashboard` view: post grid, views card, empty state, rates editor
- [ ] `add` view: 3-step flow with progress bar and AnimatePresence transitions
- [ ] `guide` view: niche-based product suggestions, 3 sequences, script hook
- [ ] URL detection calls `/api/portfolio/detect-url` on paste
- [ ] Stats input accepts formatted numbers (strips non-numeric on save)
- [ ] Post delete with optimistic update
- [ ] Rates editor saves independently
- [ ] Publish button sets `kit_published = true`
- [ ] Share chip copies public URL to clipboard

### Phase 4 — PublicMediaKit.js
- [ ] Fetches from `/api/public/kit/:username` (no auth)
- [ ] Logs kit view on every page load
- [ ] Stats strip shows follower count, ER, country, post count
- [ ] Portfolio grid: 3 cols desktop, 2 cols mobile (<600px)
- [ ] Brands row only renders if at least 1 branded post exists
- [ ] Rate card only renders if at least 1 rate is set
- [ ] CTA mailto link uses creator username in subject
- [ ] Full page renders correctly on 375px (iPhone SE)

### Phase 5 — Routing
- [ ] `/kit/:username` route added and tested
- [ ] `PortfolioBuilder` wired to My Kit tab
- [ ] `currentUser` prop passed with `niches`, `follower_count`, `username`, `rates_*`, `engagement_rate`

### Responsive checks
- [ ] 375px: Step flow single-column, buttons full-width
- [ ] 480px: Post grid switches to 2 columns
- [ ] 375px public kit: grid 2-col, CTA stacks vertically, header avatar smaller
- [ ] Product scroll horizontal on mobile (no wrapping)
- [ ] All touch targets ≥ 44px height
