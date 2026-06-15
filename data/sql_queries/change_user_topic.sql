-- Change the active topic (dictionary) for a user.
-- Replace the values below before running.
--
-- Usage:
--   sqlite3 data/promptly.db < data/user_admin/change_user_topic.sql
--
-- To find chat IDs and current topics:
--   SELECT chat_id, active_topic FROM user_settings;
--
-- To list available topics:
--   SELECT topic FROM topics ORDER BY topic;

INSERT OR REPLACE INTO user_settings (chat_id, active_topic)
VALUES (
    5419998958,   -- e.g. 123456789
    'chinese_hsk2'    -- e.g. chinese_hsk1
);

-- Confirm the change:
SELECT chat_id, active_topic FROM user_settings WHERE chat_id = 5419998958;
