-- Reset all mastery and history for a specific user.
-- Leaves the user registered and their active topic setting intact.
-- Change the chat ID in the temp table below before running.
--
-- Usage:
--   sqlite3 data/promptly.db < data/user_admin/reset_user_progress.sql
--
-- To find chat IDs:
--   SELECT chat_id, registered_at FROM users;

-- ── Set the target chat ID here ──

CREATE TEMP TABLE _cfg(chat_id INTEGER);
INSERT INTO _cfg VALUES (5419998958); 
-- Andrew: 5419998958
-- Joy: 8543874547

DELETE FROM history        WHERE chat_id = (SELECT chat_id FROM _cfg);
DELETE FROM user_progress  WHERE chat_id = (SELECT chat_id FROM _cfg);
DELETE FROM sentence_cache WHERE chat_id = (SELECT chat_id FROM _cfg);
DELETE FROM schedule       WHERE chat_id = (SELECT chat_id FROM _cfg);

-- Confirm all cleared:
SELECT 'history rows remaining:',       COUNT(*) FROM history       WHERE chat_id = (SELECT chat_id FROM _cfg);
SELECT 'user_progress rows remaining:', COUNT(*) FROM user_progress WHERE chat_id = (SELECT chat_id FROM _cfg);

DROP TABLE _cfg;
