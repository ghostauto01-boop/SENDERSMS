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
-- Duplicates are threads for the SAME contact on the SAME channel: since the
-- email release a contact legitimately has one SMS thread and one email
-- thread, and those two must never be merged. (Run this after deploying --
-- the app adds the channel column on startup.) For each duplicate group we
-- keep the OLDEST thread (lowest id) and move every message from the newer
-- duplicates onto it, so no chat history is lost.
-- ------------------------------------------------------------

-- Move messages from duplicate threads onto the surviving thread.
UPDATE messages m
SET conversation_id = keeper.keep_id
FROM (
    SELECT contact_id, COALESCE(channel, 'sms') AS channel, MIN(id) AS keep_id
    FROM conversations
    GROUP BY contact_id, COALESCE(channel, 'sms')
) AS keeper
JOIN conversations dup
    ON dup.contact_id = keeper.contact_id
   AND COALESCE(dup.channel, 'sms') = keeper.channel
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
    SELECT contact_id, COALESCE(channel, 'sms') AS channel, MIN(id) AS keep_id
    FROM conversations
    GROUP BY contact_id, COALESCE(channel, 'sms')
) AS keeper
WHERE c.contact_id = keeper.contact_id
  AND COALESCE(c.channel, 'sms') = keeper.channel
  AND c.id <> keeper.keep_id;


-- ------------------------------------------------------------
-- 3. Stop duplicates from coming back
--
-- The key was WIDENED for the email channel: one thread per contact PER
-- CHANNEL. The old single-column index has to go first, otherwise a contact
-- can never have both an SMS and an email thread (the insert fails with a
-- duplicate-key error that looks like an inbox bug).
-- ------------------------------------------------------------
DROP INDEX IF EXISTS uq_conversation_contact;
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_contact_channel
    ON conversations (contact_id, channel);

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

-- ============================================================
-- Email threading, attachments and bulk-send headers
-- ============================================================
-- Same additive changes the startup auto-repair makes. Run this by hand when
-- you want the indexes and the strict NOT NULL constraints applied up front
-- instead of waiting for the first request that touches the column.

BEGIN;

-- Every outgoing email gets an RFC Message-ID so the reply can quote it, and
-- each reply records the id it answers. Both are indexed because inbound mail
-- looks conversations up by them.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS rfc_message_id VARCHAR(255);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS in_reply_to VARCHAR(255);
-- JSON: [{name, content_type, size, content(base64)}]. Stored so a retry, or a
-- scheduled send that fires hours later, still has the files.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachments TEXT;
-- TRUE for campaign/audience mail: adds List-Unsubscribe + List-Unsubscribe-Post.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS bulk_send BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_messages_rfc_message_id ON messages (rfc_message_id);
CREATE INDEX IF NOT EXISTS ix_messages_in_reply_to ON messages (in_reply_to);

-- Templates and campaigns can carry attachments too.
ALTER TABLE templates ADD COLUMN IF NOT EXISTS attachments TEXT;
ALTER TABLE templates ADD COLUMN IF NOT EXISTS include_unsubscribe BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS attachments TEXT;

ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS attachments TEXT;

-- CC/BCC on a message and on a scheduled email, so a queued or time-delayed
-- send still copies the same people.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS cc_addresses TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS bcc_addresses TEXT;
ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS cc_addresses TEXT;
ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS bcc_addresses TEXT;

COMMIT;

-- ============================================================
-- Email, phone, or both — contacts are no longer phone-only
-- ============================================================
-- A contact now needs a phone number OR an email address. Earlier releases
-- declared contacts.phone_number NOT NULL, which silently rejected every
-- email-only import row, so this has to be relaxed on an existing database
-- before the importer can store one.
--
-- The startup auto-repair applies this on its own (schema_repair.py); run the
-- statements below by hand when you want the change applied up front, or when
-- the auto-repair could not (a restricted DB role, for example).

BEGIN;

-- Email-only contacts store NULL here. Safe and instant on PostgreSQL: it only
-- drops a constraint, no row is read or rewritten.
ALTER TABLE contacts ALTER COLUMN phone_number DROP NOT NULL;
ALTER TABLE contacts ALTER COLUMN country DROP NOT NULL;
ALTER TABLE contacts ALTER COLUMN country DROP DEFAULT;

-- Multiple reply-to targets: the legacy reply_to column stays as the primary
-- address sent to Brevo; this JSON column keeps every routing address.
ALTER TABLE email_accounts ADD COLUMN IF NOT EXISTS reply_to_addresses TEXT;
UPDATE email_accounts
SET reply_to_addresses = json_build_array(reply_to)::text
WHERE reply_to IS NOT NULL AND reply_to_addresses IS NULL;

-- Where an address came from and whether it was ever confirmed. A pattern
-- guess is stored with email_verified = FALSE and email_source = 'inferred',
-- so it can be reviewed but never silently treated as a real address.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_source VARCHAR(30);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_confidence INTEGER;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_enriched_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_enrichment_note VARCHAR(255);

-- Dedupe key for an email-only contact. NOT unique on purpose: an existing
-- database may already hold the same address on two contacts, and the ALTER
-- would abort the whole migration. Uniqueness is enforced by the application
-- against lower(email); resolve duplicates first if you want the index:
--
--   SELECT lower(email), COUNT(*) FROM contacts
--   WHERE email IS NOT NULL AND email <> ''
--   GROUP BY 1 HAVING COUNT(*) > 1;
--
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_lower VARCHAR(255);

-- Backfill the dedupe key for addresses that predate it, so an old contact is
-- still found by an email-only re-import. Idempotent: only fills blanks.
UPDATE contacts
   SET email_lower = lower(trim(email))
 WHERE email IS NOT NULL AND trim(email) <> ''
   AND (email_lower IS NULL OR email_lower = '');

CREATE INDEX IF NOT EXISTS ix_contacts_email_lower    ON contacts (email_lower);
CREATE INDEX IF NOT EXISTS ix_contacts_email          ON contacts (email);
CREATE INDEX IF NOT EXISTS ix_contacts_email_verified ON contacts (email_verified);

COMMIT;

-- ============================================================
-- Import progress, private diagnostics, and privacy quarantine
-- ============================================================
BEGIN;

CREATE TABLE IF NOT EXISTS contact_import_jobs (
    id VARCHAR(36) PRIMARY KEY,
    owner_id INTEGER,
    file_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    total_rows INTEGER NOT NULL DEFAULT 0,
    processed_rows INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    merged INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    content TEXT,
    mapping_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    auto_tag_rules_json TEXT NOT NULL DEFAULT '[]',
    list_id INTEGER,
    skip_duplicates INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_contact_import_jobs_owner_id ON contact_import_jobs (owner_id);
CREATE INDEX IF NOT EXISTS ix_contact_import_jobs_status ON contact_import_jobs (status);
CREATE INDEX IF NOT EXISTS ix_contact_import_jobs_created_at ON contact_import_jobs (created_at);

CREATE TABLE IF NOT EXISTS system_error_records (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    code VARCHAR(80) NOT NULL,
    exception_type VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    traceback TEXT NOT NULL,
    method VARCHAR(12) NOT NULL,
    path VARCHAR(1000) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_system_error_records_request_id ON system_error_records (request_id);
CREATE INDEX IF NOT EXISTS ix_system_error_records_code ON system_error_records (code);
CREATE INDEX IF NOT EXISTS ix_system_error_records_created_at ON system_error_records (created_at);

CREATE TABLE IF NOT EXISTS quarantined_sms (
    id SERIAL PRIMARY KEY,
    sender VARCHAR(40) NOT NULL,
    body TEXT NOT NULL,
    reason VARCHAR(60) NOT NULL,
    provider_message_id VARCHAR(255),
    device_id VARCHAR(120),
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_quarantined_sms_sender ON quarantined_sms (sender);
CREATE INDEX IF NOT EXISTS ix_quarantined_sms_reason ON quarantined_sms (reason);
CREATE INDEX IF NOT EXISTS ix_quarantined_sms_received_at ON quarantined_sms (received_at);

COMMIT;
