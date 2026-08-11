-- ============================================================
-- SendSMS - schema check for the NEON WEB CONSOLE (READ ONLY)
-- ============================================================
--
-- Same job as check_schema.sql, but with no psql-only \echo commands,
-- so it runs as-is in Neon's browser SQL Editor.
--
-- Everything is returned as ONE result table you can read at a glance.
-- Changes nothing. Safe to run any time, as often as you like.
-- ============================================================

SELECT * FROM (

    -- ---------- columns (auto-repaired by the app since v2) ----------
    SELECT 1 AS sort, 'COLUMN' AS kind,
           'campaigns.allow_weekends' AS item,
           CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='campaigns' AND column_name='allow_weekends')
                THEN 'OK' ELSE 'MISSING' END AS status
    UNION ALL
    SELECT 1, 'COLUMN', 'campaigns.message_body',
           CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='campaigns' AND column_name='message_body')
                THEN 'OK' ELSE 'MISSING' END
    UNION ALL
    SELECT 1, 'COLUMN', 'campaigns.scheduled_start_at',
           CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='campaigns' AND column_name='scheduled_start_at')
                THEN 'OK' ELSE 'MISSING' END
    UNION ALL
    SELECT 1, 'COLUMN', 'messages.is_auto_reply',
           CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='messages' AND column_name='is_auto_reply')
                THEN 'OK' ELSE 'MISSING' END
    UNION ALL
    SELECT 1, 'TABLE', 'auto_reply_rules',
           CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_name='auto_reply_rules')
                THEN 'OK' ELSE 'MISSING' END

    -- ---------- indexes (the app CANNOT add these - migration only) ----------
    UNION ALL
    SELECT 2, 'INDEX', 'uq_conversation_contact  (stops duplicate chat threads)',
           CASE WHEN EXISTS (SELECT 1 FROM pg_indexes
                WHERE indexname='uq_conversation_contact')
                THEN 'OK' ELSE 'MISSING - run the migration' END
    UNION ALL
    SELECT 2, 'INDEX', 'ix_campaigns_scheduled_start_at  (scheduling speed)',
           CASE WHEN EXISTS (SELECT 1 FROM pg_indexes
                WHERE indexname='ix_campaigns_scheduled_start_at')
                THEN 'OK' ELSE 'MISSING - run the migration' END

    -- ---------- data integrity (migration only) ----------
    UNION ALL
    SELECT 3, 'DATA', 'duplicate conversation threads (want 0)',
           COALESCE((
               SELECT CASE WHEN COUNT(*) = 0
                           THEN 'OK - none'
                           ELSE COUNT(*)::text || ' contact(s) with split threads - run the migration'
                      END
               FROM (SELECT contact_id FROM conversations
                     GROUP BY contact_id HAVING COUNT(*) > 1) d
           ), 'OK - none')

    -- ---------- row counts (compare before/after) ----------
    UNION ALL
    SELECT 4, 'COUNT', 'contacts',      (SELECT COUNT(*)::text FROM contacts)
    UNION ALL
    SELECT 4, 'COUNT', 'conversations', (SELECT COUNT(*)::text FROM conversations)
    UNION ALL
    SELECT 4, 'COUNT', 'messages',      (SELECT COUNT(*)::text FROM messages)
    UNION ALL
    SELECT 4, 'COUNT', 'campaigns',     (SELECT COUNT(*)::text FROM campaigns)

) report
ORDER BY sort, item;
