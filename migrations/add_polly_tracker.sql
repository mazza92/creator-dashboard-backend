-- Polly task tracker + timeline. Integer PKs to match creators.id / pr_brands.id.

CREATE TABLE IF NOT EXISTS polly_tasks (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER REFERENCES pr_brands(id) ON DELETE SET NULL,
    parent_task_id INTEGER REFERENCES polly_tasks(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    snoozed_until TIMESTAMPTZ,
    outcome TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    polly_last_nudge_at TIMESTAMPTZ,
    polly_nudge_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_polly_tasks_creator_status
    ON polly_tasks (creator_id, status);
CREATE INDEX IF NOT EXISTS idx_polly_tasks_due
    ON polly_tasks (due_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_polly_tasks_brand
    ON polly_tasks (brand_id) WHERE status IN ('pending', 'in_progress');

CREATE TABLE IF NOT EXISTS polly_timeline_events (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER REFERENCES pr_brands(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES polly_tasks(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    event_label TEXT NOT NULL,
    event_icon TEXT,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    polly_notes TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_polly_timeline_creator_brand
    ON polly_timeline_events (creator_id, brand_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_polly_timeline_creator
    ON polly_timeline_events (creator_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS polly_intent_logs (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    user_message TEXT NOT NULL,
    detected_intent TEXT NOT NULL,
    detected_brand_id INTEGER REFERENCES pr_brands(id) ON DELETE SET NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    action_taken TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
