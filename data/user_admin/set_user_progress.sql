-- Set mastery scores for all words in a topic for a specific user.
-- Words at mastery_score >= 10 are retired (excluded from card selection).
-- Change the values in the temp table below before running.
--
-- Usage:
--   sqlite3 data/promptly.db < data/user_admin/set_user_progress.sql
--
-- To find available topics:
--   SELECT DISTINCT topic FROM corpus ORDER BY topic;

-- ── Set target user and topic here ──

CREATE TEMP TABLE _cfg(chat_id INTEGER, topic TEXT, mastery_score INTEGER);
INSERT INTO _cfg VALUES (
    5419998958,    -- Andrew: 5419998958 
    'chinese_hsk1',
    10             -- 10 = fully retired; 0 = reset to unseen
);

-- Upsert user_progress for every word in the target topic:
INSERT OR REPLACE INTO user_progress (chat_id, topic, word_id, mastery_score, last_seen)
SELECT
    (SELECT chat_id FROM _cfg),
    c.topic,
    c.word_id,
    (SELECT mastery_score FROM _cfg),
    datetime('now')
FROM corpus c
WHERE c.topic = (SELECT topic FROM _cfg);

-- Confirm:
SELECT
    (SELECT mastery_score FROM _cfg) AS mastery_set,
    COUNT(*) AS words_updated
FROM user_progress
WHERE chat_id = (SELECT chat_id FROM _cfg)
  AND topic   = (SELECT topic  FROM _cfg);

DROP TABLE _cfg;
