-- ============================================================
-- SendSMS - one-time migration for databases created BEFORE
-- the "one thread per contact" fix.
-- ============================================================
--
-- WHO NEEDS THIS: anyone whose PostgreSQL database already existed and had
-- messages in it. A brand-new/empty database does NOT need this -- the app
-- builds the correct schema on first start.
--
-- WHAT IT DOES:
--   1. Adds the missing `allow_weekends` column to campaigns.
--   2. Merges duplicate conversation threads for the same contact.
--   3. Adds the unique index that stops duplicates coming back.
--
-- WHY: the app has no Alembic migrations; it only creates tables that do not
-- exist yet. New CONSTRAINTS and COLUMNS on existing tables are never applied
-- automatically, so this has to be run by hand -- once.
--
-- SAFETY: wrapped in a transaction. If any statement fails the whole thing
-- rolls back and your data is untouched. Running it twice is harmless.
--
-- BACK UP FIRST:
--   pg_dump "$DATABASE_URL" > backup-before-migration.sql
--
-- RUN IT:
--   psql "$DATABASE_URL" -f scripts/migrate_existing_db.sql
--
-- Note: use the plain postgres:// form of your URL here, not the
-- postgresql+asyncpg:// form the app uses.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Missing column on campaigns
-- ------------------------------------------------------------
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS allow_weekends BOOLEAN NOT NULL DEFAULT TRUE;


-- ------------------------------------------------------------
-- 2. Merge duplicate conversations
--
-- For each contact we keep the OLDEST thread (lowest id) and move every
-- message from the newer duplicates onto it, so no chat history is lost.
-- ------------------------------------------------------------

-- Move messages from duplicate threads onto the surviving thread.
UPDATE messages m
SET conversation_id = keeper.keep_id
FROM (
    SELECT contact_id, MIN(id) AS keep_id
    FROM conversations
    GROUP BY contact_id
) AS keeper
JOIN conversations dup
    ON dup.contact_id = keeper.contact_id
   AND dup.id <> keeper.keep_id
WHERE m.conversation_id = dup.id;

-- Recompute the counters on the surviving threads so the inbox shows
-- the correct message count after the merge.
UPDATE conversations c
SET message_count = stats.cnt,
    last_message_at = stats.last_at
FROM (
    SELECT conversation_id,
           COUNT(*)          AS cnt,
           MAX(created_at)   AS last_at
    FROM messages
    GROUP BY conversation_id
) AS stats
WHERE c.id = stats.conversation_id;

-- Delete the now-empty duplicate threads.
DELETE FROM conversations c
USING (
    SELECT contact_id, MIN(id) AS keep_id
    FROM conversations
    GROUP BY contact_id
) AS keeper
WHERE c.contact_id = keeper.contact_id
  AND c.id <> keeper.keep_id;


-- ------------------------------------------------------------
-- 3. Stop duplicates from coming back
--
-- This is what the application model now declares. Without it the database
-- will happily accept a second thread again.
-- ------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_contact
    ON conversations (contact_id);

-- ------------------------------------------------------------
-- 4. Inline campaign messages
--
-- Campaigns can now carry their own message text instead of requiring a saved
-- template. Existing campaigns keep using their template_id; this column is
-- simply NULL for them.
-- ------------------------------------------------------------
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS message_body TEXT;

-- ------------------------------------------------------------
-- 5. Campaign scheduling
--
-- The future time a campaign should launch by itself. Distinct from
-- scheduled_at, which only records when the campaign passed validation.
-- NULL means "start it manually", which is how every existing campaign
-- behaves, so this is a no-op for current data.
-- ------------------------------------------------------------
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS scheduled_start_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_campaigns_scheduled_start_at
    ON campaigns (scheduled_start_at);

-- ------------------------------------------------------------
-- 6. Auto-reply
--
-- User-defined rules for answering inbound SMS automatically. The table
-- starts empty and the feature is inert until the first rule is created.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auto_reply_rules (
    id                SERIAL PRIMARY KEY,
    name              VARCHAR(120) NOT NULL,
    keywords          TEXT,
    match_type        VARCHAR(20)  NOT NULL DEFAULT 'contains',
    reply_body        TEXT         NOT NULL,
    is_enabled        BOOLEAN      NOT NULL DEFAULT TRUE,
    priority          INTEGER      NOT NULL DEFAULT 100,
    cooldown_minutes  INTEGER      NOT NULL DEFAULT 240,
    stop_on_match     BOOLEAN      NOT NULL DEFAULT TRUE,
    times_triggered   INTEGER      NOT NULL DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Flags messages the autoresponder generated. Needed for the per-contact
-- cooldown; existing messages are correctly FALSE (a human or a campaign
-- sent them).
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS is_auto_reply BOOLEAN NOT NULL DEFAULT FALSE;

-- ------------------------------------------------------------
-- 7. Campaign attribution on conversations
--
-- Answers "which campaign is this lead from?" without replaying a thread's
-- whole message history on every inbox render.
--
--   campaign_id / ads_campaign_id           -> FIRST touch (the source)
--   last_campaign_id / last_ads_campaign_id -> LAST touch  (credits replies)
--
-- The classic and ads campaign systems live in different tables, so they get
-- separate columns; exactly one side is ever populated on a given row.
--
-- These start NULL for existing threads. The app self-heals them from the
-- messages table the first time a thread is listed or opened
-- (app/services/attribution.py:backfill_conversation), so this migration is
-- only about creating the columns and indexes up front -- no data backfill
-- is required here.
-- ------------------------------------------------------------
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS ads_campaign_id INTEGER;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS last_campaign_id INTEGER;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS last_ads_campaign_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_conversations_ads_campaign_id
    ON conversations (ads_campaign_id);
CREATE INDEX IF NOT EXISTS ix_conversations_last_campaign_id
    ON conversations (last_campaign_id);
CREATE INDEX IF NOT EXISTS ix_conversations_last_ads_campaign_id
    ON conversations (last_ads_campaign_id);

-- Messages sent by the SMS Ads Manager. The classic campaign_id column
-- already exists; this is its ads-side counterpart.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS ads_campaign_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_messages_ads_campaign_id
    ON messages (ads_campaign_id);

-- Optional one-off backfill. The app does this lazily per thread, but running
-- it here makes every badge correct immediately on a large existing inbox.
UPDATE conversations c
SET campaign_id = sub.campaign_id
FROM (
    SELECT DISTINCT ON (conversation_id) conversation_id, campaign_id
    FROM messages
    WHERE direction = 'outgoing' AND campaign_id IS NOT NULL
    ORDER BY conversation_id, created_at ASC, id ASC
) AS sub
WHERE c.id = sub.conversation_id
  AND c.campaign_id IS NULL
  AND c.ads_campaign_id IS NULL;

UPDATE conversations c
SET last_campaign_id = sub.campaign_id
FROM (
    SELECT DISTINCT ON (conversation_id) conversation_id, campaign_id
    FROM messages
    WHERE direction = 'outgoing' AND campaign_id IS NOT NULL
    ORDER BY conversation_id, created_at DESC, id DESC
) AS sub
WHERE c.id = sub.conversation_id
  AND c.last_campaign_id IS NULL
  AND c.last_ads_campaign_id IS NULL;

COMMIT;

-- ------------------------------------------------------------
-- Verify (should return zero rows):
--
--   SELECT contact_id, COUNT(*)
--   FROM conversations
--   GROUP BY contact_id
--   HAVING COUNT(*) > 1;
-- ------------------------------------------------------------
