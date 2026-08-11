-- ============================================================
-- SendSMS - schema check (READ ONLY, changes nothing)
-- ============================================================
--
-- Tells you whether migrate_existing_db.sql still needs to be run.
-- Safe to run any time, on a live database, as often as you like.
--
--   psql "$DATABASE_URL" -f scripts/check_schema.sql
--
-- Every row should say OK. Any MISSING row means run the migration.
-- ============================================================

\echo ''
\echo '=== SendSMS schema check ==='
\echo ''

SELECT
    item,
    CASE WHEN present THEN 'OK' ELSE '>>> MISSING - run the migration' END AS status
FROM (
    SELECT 'campaigns.allow_weekends' AS item, EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='campaigns' AND column_name='allow_weekends') AS present
    UNION ALL
    SELECT 'campaigns.message_body', EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='campaigns' AND column_name='message_body')
    UNION ALL
    SELECT 'campaigns.scheduled_start_at  (scheduling)', EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='campaigns' AND column_name='scheduled_start_at')
    UNION ALL
    SELECT 'messages.is_auto_reply  (auto-reply)', EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='messages' AND column_name='is_auto_reply')
    UNION ALL
    SELECT 'auto_reply_rules table  (auto-reply)', EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name='auto_reply_rules')
    UNION ALL
    SELECT 'uq_conversation_contact index', EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname='uq_conversation_contact')
) t
ORDER BY present, item;

\echo ''
\echo '=== Duplicate conversation threads (want: 0 rows) ==='
\echo ''

SELECT contact_id, COUNT(*) AS threads
FROM conversations
GROUP BY contact_id
HAVING COUNT(*) > 1;

\echo ''
\echo '=== Row counts (sanity check before/after) ==='
\echo ''

SELECT 'contacts'      AS table_name, COUNT(*) AS row_count FROM contacts
UNION ALL SELECT 'conversations', COUNT(*) FROM conversations
UNION ALL SELECT 'messages',      COUNT(*) FROM messages
UNION ALL SELECT 'campaigns',     COUNT(*) FROM campaigns;

\echo ''
