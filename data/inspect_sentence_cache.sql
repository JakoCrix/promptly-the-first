-- Explore the sentence_cache table.
-- Usage:
--   sqlite3 data/promptly.db < data/inspect_sentence_cache.sql

-- Summary: how many cached sentences per user per date
SELECT
    chat_id,
    generated_for,
    COUNT(*) AS sentence_count
FROM sentence_cache
GROUP BY chat_id, generated_for
ORDER BY generated_for DESC, chat_id;

-- ── Full listing for today ────────────────────────────────────────────────────
SELECT
    sc.chat_id,
    sc.topic,
    sc.word_id,
    c.word,
    sc.generated_for,
    sc.sentence
FROM sentence_cache sc
JOIN corpus c ON sc.topic = c.topic AND sc.word_id = c.word_id
WHERE sc.generated_for = DATE('now')
ORDER BY sc.chat_id, sc.topic, sc.word_id;
