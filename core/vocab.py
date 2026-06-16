import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from core.config import ALLOWED_CHAT_IDS, DAILY_SLOTS, DATA_DIR, DB_PATH, INVITE_CODE, NEVER_SEEN_SECONDS, RETIREMENT_THRESHOLD, WINDOW_END_HOUR, WINDOW_START_HOUR


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":            row["word_id"],
        "topic":         row["topic"],
        "word":          row["word"],
        "definition":    row["definition"],
        "pronunciation": row["pronunciation"],
        "mastery_score": row["mastery_score"],
        "last_seen":     row["last_seen"],
    }


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _get_conn()
    try:
        # Snapshot BEFORE executescript so migration guards reflect pre-existing state.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS corpus (
                topic      TEXT NOT NULL,
                word_id    TEXT NOT NULL,
                word       TEXT NOT NULL,
                definition TEXT,
                PRIMARY KEY (topic, word_id)
            );

            CREATE TABLE IF NOT EXISTS user_progress (
                chat_id       INTEGER NOT NULL,
                topic         TEXT    NOT NULL,
                word_id       TEXT    NOT NULL,
                mastery_score INTEGER NOT NULL DEFAULT 0,
                last_seen     TEXT,
                PRIMARY KEY (chat_id, topic, word_id),
                FOREIGN KEY (topic, word_id) REFERENCES corpus (topic, word_id)
            );

            CREATE TABLE IF NOT EXISTS history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   INTEGER NOT NULL,
                topic     TEXT    NOT NULL,
                word_id   TEXT    NOT NULL,
                timestamp TEXT    NOT NULL,
                result    TEXT    NOT NULL,
                UNIQUE (chat_id, topic, word_id, timestamp, result),
                FOREIGN KEY (chat_id, topic, word_id) REFERENCES user_progress (chat_id, topic, word_id)
            );

            CREATE INDEX IF NOT EXISTS idx_history_chat_word
                ON history (chat_id, topic, word_id);

            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id           INTEGER PRIMARY KEY,
                active_topic      TEXT    NOT NULL,
                window_start_hour INTEGER,
                window_end_hour   INTEGER,
                daily_slots       INTEGER
            );

            CREATE TABLE IF NOT EXISTS schedule (
                chat_id   INTEGER NOT NULL,
                date      TEXT    NOT NULL,
                slot_time TEXT    NOT NULL,
                fired     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, date, slot_time)
            );

            CREATE TABLE IF NOT EXISTS topics (
                topic       TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS users (
                chat_id       INTEGER PRIMARY KEY,
                registered_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sentence_cache (
                chat_id       INTEGER NOT NULL,
                topic         TEXT    NOT NULL,
                word_id       TEXT    NOT NULL,
                generated_for TEXT    NOT NULL,
                sentence      TEXT    NOT NULL,
                PRIMARY KEY (chat_id, topic, word_id, generated_for)
            );

            CREATE TABLE IF NOT EXISTS word_sentences (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                topic    TEXT NOT NULL,
                word_id  TEXT NOT NULL,
                sentence TEXT NOT NULL,
                UNIQUE (topic, word_id, sentence)
            );
            CREATE INDEX IF NOT EXISTS idx_word_sentences_lookup
                ON word_sentences (topic, word_id);
        """)
        conn.commit()

        # Seed users table from ALLOWED_CHAT_IDS so existing users aren't affected.
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        for cid in ALLOWED_CHAT_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO users (chat_id, registered_at) VALUES (?, ?)",
                (cid, now_iso),
            )
        conn.commit()

        # One-time migrations — guards use the pre-executescript snapshot above.
        # SQLite FK enforcement is OFF by default, so DROP TABLE words succeeds even though
        # history rows may reference it. History rows are re-anchored to user_progress after migration.
        if "words" in tables:
            word_cols = {r[1] for r in conn.execute("PRAGMA table_info(words)")}
            if "hint" not in word_cols:
                conn.execute("ALTER TABLE words ADD COLUMN hint TEXT")
                conn.commit()
            conn.executescript("""
                INSERT OR IGNORE INTO corpus (topic, word_id, word, definition)
                    SELECT DISTINCT topic, word_id, word, hint FROM words;
                INSERT OR IGNORE INTO user_progress (chat_id, topic, word_id, mastery_score, last_seen)
                    SELECT chat_id, topic, word_id, mastery_score, last_seen FROM words;
                DROP TABLE words;
            """)
            conn.commit()

        # Migrate schedule table to per-user schema (add chat_id to PK).
        # Schedule data is ephemeral — drop and recreate if the old schema is detected.
        if "schedule" in tables:
            sched_cols = {r[1] for r in conn.execute("PRAGMA table_info(schedule)")}
            if "chat_id" not in sched_cols:
                conn.executescript("""
                    DROP TABLE schedule;
                    CREATE TABLE schedule (
                        chat_id   INTEGER NOT NULL,
                        date      TEXT    NOT NULL,
                        slot_time TEXT    NOT NULL,
                        fired     INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (chat_id, date, slot_time)
                    );
                """)
                conn.commit()

        # Migrate sentence_cache if generated_for is not part of the primary key.
        # The table is pure ephemeral cache so dropping it is safe.
        if "sentence_cache" in tables:
            old_pk_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(sentence_cache)") if r[5] > 0
            }
            if "generated_for" not in old_pk_cols:
                conn.executescript("""
                    DROP TABLE sentence_cache;
                    CREATE TABLE sentence_cache (
                        chat_id       INTEGER NOT NULL,
                        topic         TEXT    NOT NULL,
                        word_id       TEXT    NOT NULL,
                        generated_for TEXT    NOT NULL,
                        sentence      TEXT    NOT NULL,
                        PRIMARY KEY (chat_id, topic, word_id, generated_for)
                    );
                """)
                conn.commit()

        # Add frequency_rank to corpus for ordered word lists (Stage 1 corpus ingestion).
        if "corpus" in tables:
            corpus_cols = {r[1] for r in conn.execute("PRAGMA table_info(corpus)")}
            if "frequency_rank" not in corpus_cols:
                conn.execute("ALTER TABLE corpus ADD COLUMN frequency_rank INTEGER")
                conn.commit()
            if "pronunciation" not in corpus_cols:
                conn.execute("ALTER TABLE corpus ADD COLUMN pronunciation TEXT")
                conn.commit()
            if "hint" in corpus_cols and "definition" not in corpus_cols:
                conn.execute("ALTER TABLE corpus RENAME COLUMN hint TO definition")
                conn.commit()

        # Add UNIQUE constraint to word_sentences to allow INSERT OR IGNORE dedup.
        # SQLite can't ADD CONSTRAINT, so rebuild the table if the index is missing.
        if "word_sentences" in tables:
            existing_idx = {r[1] for r in conn.execute("PRAGMA index_list(word_sentences)")}
            if "uq_word_sentences" not in existing_idx:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS word_sentences_new (
                        id       INTEGER PRIMARY KEY AUTOINCREMENT,
                        topic    TEXT NOT NULL,
                        word_id  TEXT NOT NULL,
                        sentence TEXT NOT NULL,
                        UNIQUE (topic, word_id, sentence)
                    );
                    INSERT OR IGNORE INTO word_sentences_new (id, topic, word_id, sentence)
                        SELECT id, topic, word_id, sentence FROM word_sentences;
                    DROP TABLE word_sentences;
                    ALTER TABLE word_sentences_new RENAME TO word_sentences;
                    CREATE INDEX IF NOT EXISTS idx_word_sentences_lookup
                        ON word_sentences (topic, word_id);
                """)
                conn.commit()

        # Add per-user schedule settings to user_settings (one-time migration).
        if "user_settings" in tables:
            us_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_settings)")}
            for col in ("window_start_hour", "window_end_hour", "daily_slots"):
                if col not in us_cols:
                    conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} INTEGER")
            conn.commit()
    finally:
        conn.close()


def get_active_topic(chat_id: int) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT active_topic FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return row["active_topic"] if row else None
    finally:
        conn.close()


def set_active_topic(chat_id: int, topic: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO user_settings (chat_id, active_topic) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET active_topic = excluded.active_topic",
            (chat_id, topic),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_schedule_settings(chat_id: int) -> tuple[int, int, int]:
    """Return (start_hour, end_hour, daily_slots) for this user, falling back to config defaults for any NULL."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT window_start_hour, window_end_hour, daily_slots FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return (WINDOW_START_HOUR, WINDOW_END_HOUR, DAILY_SLOTS)
        start = row["window_start_hour"] if row["window_start_hour"] is not None else WINDOW_START_HOUR
        end   = row["window_end_hour"]   if row["window_end_hour"]   is not None else WINDOW_END_HOUR
        count = row["daily_slots"]       if row["daily_slots"]       is not None else DAILY_SLOTS
        return (start, end, count)
    finally:
        conn.close()


def set_user_schedule_settings(chat_id: int, start: int, end: int, count: int) -> None:
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT active_topic FROM user_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE user_settings "
                "SET window_start_hour = ?, window_end_hour = ?, daily_slots = ? "
                "WHERE chat_id = ?",
                (start, end, count, chat_id),
            )
        else:
            # No user_settings row means the user hasn't picked a topic yet.
            # This shouldn't occur in normal flow (settings require prior registration),
            # so skip the INSERT rather than planting a fake active_topic.
            log.warning(
                "set_user_schedule_settings: no user_settings row for chat_id=%s — settings not persisted",
                chat_id,
            )
        conn.commit()
    finally:
        conn.close()


def list_topics(chat_id: int) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM corpus ORDER BY topic",
        ).fetchall()
        return [r["topic"] for r in rows]
    finally:
        conn.close()


def find_word(chat_id: int, word_id: str) -> dict | None:
    """Find a word by word_id, preferring the user's active topic to avoid cross-topic collisions."""
    conn = _get_conn()
    try:
        active_topic = _resolve_topic(conn, chat_id)
        if active_topic:
            row = conn.execute(
                "SELECT c.word_id, c.topic, c.word, c.definition, c.pronunciation, "
                "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
                "FROM corpus c "
                "LEFT JOIN user_progress up "
                "    ON c.topic = up.topic AND c.word_id = up.word_id AND up.chat_id = ? "
                "WHERE c.topic = ? AND c.word_id = ?",
                (chat_id, active_topic, word_id),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        row = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.definition, c.pronunciation, "
            "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
            "FROM corpus c "
            "LEFT JOIN user_progress up "
            "    ON c.topic = up.topic AND c.word_id = up.word_id AND up.chat_id = ? "
            "WHERE c.word_id = ? LIMIT 1",
            (chat_id, word_id),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_word(chat_id: int, topic: str, word_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.definition, c.pronunciation, "
            "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
            "FROM corpus c "
            "LEFT JOIN user_progress up "
            "    ON c.topic = up.topic AND c.word_id = up.word_id AND up.chat_id = ? "
            "WHERE c.topic = ? AND c.word_id = ?",
            (chat_id, topic, word_id),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def _resolve_topic(conn: sqlite3.Connection, chat_id: int) -> str | None:
    row = conn.execute(
        "SELECT active_topic FROM user_settings WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row:
        return row["active_topic"]
    first = conn.execute(
        "SELECT topic FROM corpus ORDER BY topic LIMIT 1",
    ).fetchone()
    return first["topic"] if first else None


def _elapsed_seconds(word: dict, now: datetime) -> float:
    if word["last_seen"] is None:
        return float(NEVER_SEEN_SECONDS)
    delta = now - datetime.fromisoformat(word["last_seen"])
    return max(0.0, delta.total_seconds())


def _weight(word: dict, now: datetime) -> float:
    return (1.0 / (word["mastery_score"] + 1)) * _elapsed_seconds(word, now)


def pick_word(chat_id: int) -> dict | None:
    conn = _get_conn()
    try:
        topic = _resolve_topic(conn, chat_id)
        if topic is None:
            return None

        rows = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.definition, c.pronunciation, "
            "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
            "FROM corpus c "
            "LEFT JOIN user_progress up "
            "    ON c.topic = up.topic AND c.word_id = up.word_id AND up.chat_id = ? "
            "WHERE c.topic = ? "
            "  AND COALESCE(up.mastery_score, 0) < ?",
            (chat_id, topic, RETIREMENT_THRESHOLD),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None
    eligible = [_row_to_dict(r) for r in rows]
    now = datetime.now()
    weights = [_weight(w, now) for w in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]


def pick_n_words(chat_id: int, n: int) -> list[dict]:
    """Return up to n distinct eligible words sampled without replacement, by weight."""
    conn = _get_conn()
    try:
        topic = _resolve_topic(conn, chat_id)
        if topic is None:
            return []

        rows = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.definition, c.pronunciation, "
            "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
            "FROM corpus c "
            "LEFT JOIN user_progress up "
            "    ON c.topic = up.topic AND c.word_id = up.word_id AND up.chat_id = ? "
            "WHERE c.topic = ? "
            "  AND COALESCE(up.mastery_score, 0) < ?",
            (chat_id, topic, RETIREMENT_THRESHOLD),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []
    pool = [_row_to_dict(r) for r in rows]
    now = datetime.now()
    pool_weights = [_weight(w, now) for w in pool]
    n = min(n, len(pool))
    selected = []
    while len(selected) < n:
        idx = random.choices(range(len(pool)), weights=pool_weights, k=1)[0]
        selected.append(pool.pop(idx))
        pool_weights.pop(idx)
    return selected


def get_retired_words(chat_id: int, topic: str, limit: int = 10) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT c.word FROM corpus c "
            "JOIN user_progress up ON c.topic = up.topic AND c.word_id = up.word_id "
            "WHERE up.chat_id = ? AND c.topic = ? AND up.mastery_score >= ? "
            "ORDER BY RANDOM() LIMIT ?",
            (chat_id, topic, RETIREMENT_THRESHOLD, limit),
        ).fetchall()
        return [r["word"] for r in rows]
    finally:
        conn.close()


def get_cached_sentence(chat_id: int, topic: str, word_id: str, date: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT sentence FROM sentence_cache "
            "WHERE chat_id = ? AND topic = ? AND word_id = ? AND generated_for = ?",
            (chat_id, topic, word_id, date),
        ).fetchone()
        return row["sentence"] if row else None
    finally:
        conn.close()


def purge_old_cached_sentences(before_date: str) -> int:
    """Delete sentence_cache rows older than before_date (YYYY-MM-DD). Returns deleted row count."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM sentence_cache WHERE generated_for < ?",
            (before_date,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def store_cached_sentence(chat_id: int, topic: str, word_id: str, sentence: str, date: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sentence_cache (chat_id, topic, word_id, sentence, generated_for) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, topic, word_id, sentence, date),
        )
        conn.commit()
    finally:
        conn.close()


_POOLED_SENTENCE_CAP = 10


def store_pooled_sentence(topic: str, word_id: str, sentence: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO word_sentences (topic, word_id, sentence) VALUES (?, ?, ?)",
            (topic, word_id, sentence),
        )
        conn.execute(
            """
            DELETE FROM word_sentences
            WHERE topic = ? AND word_id = ?
              AND id NOT IN (
                  SELECT id FROM word_sentences
                  WHERE topic = ? AND word_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (topic, word_id, topic, word_id, _POOLED_SENTENCE_CAP),
        )
        conn.commit()
    finally:
        conn.close()


def get_pooled_sentence(topic: str, word_id: str) -> str | None:
    """Return a random pre-generated sentence from the word_sentences bank, or None if empty."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT sentence FROM word_sentences WHERE topic = ? AND word_id = ? ORDER BY RANDOM() LIMIT 1",
            (topic, word_id),
        ).fetchone()
        return row["sentence"] if row else None
    finally:
        conn.close()


def slugify(text: str) -> str:
    """Convert arbitrary text to an ASCII slug safe for Telegram callback_data."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")


def get_topic_description(topic: str) -> str:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT description FROM topics WHERE topic = ?",
            (topic,),
        ).fetchone()
        return row["description"] if row else ""
    finally:
        conn.close()


def get_word_sample(chat_id: int, topic: str, n: int = 15) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT word_id, word FROM corpus WHERE topic = ? ORDER BY RANDOM() LIMIT ?",
            (topic, n),
        ).fetchall()
        return [{"id": r["word_id"], "word": r["word"]} for r in rows]
    finally:
        conn.close()


_MAX_CALLBACK_BYTES = 64

def insert_word(chat_id: int, topic: str, word: str) -> bool:
    word_id = slugify(word)
    # "forgot" is the longer of the two action prefixes — use it for the worst-case check.
    if len(f"forgot:{chat_id}:{topic}:{word_id}".encode()) > _MAX_CALLBACK_BYTES:
        log.warning(
            "insert_word: callback_data would exceed 64 bytes for word_id='%s' topic='%s' — skipping",
            word_id, topic,
        )
        return False
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO corpus (topic, word_id, word) VALUES (?, ?, ?)",
            (topic, word_id, word),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_allowed_chat_ids() -> list[int]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT chat_id FROM users").fetchall()
        return [r["chat_id"] for r in rows]
    finally:
        conn.close()


def is_registered(chat_id: int) -> bool:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def register_user(chat_id: int, code: str) -> bool:
    """Register a new user if code matches INVITE_CODE. Returns True if newly registered."""
    if not INVITE_CODE or code != INVITE_CODE:
        return False
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO users (chat_id, registered_at) VALUES (?, ?)",
            (chat_id, datetime.now(tz=timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def record_feedback(chat_id: int, topic: str, word_id: str, result: str) -> bool:
    conn = _get_conn()
    try:
        if not conn.execute(
            "SELECT 1 FROM corpus WHERE topic = ? AND word_id = ?",
            (topic, word_id),
        ).fetchone():
            return False

        row = conn.execute(
            "SELECT mastery_score FROM user_progress "
            "WHERE chat_id = ? AND topic = ? AND word_id = ?",
            (chat_id, topic, word_id),
        ).fetchone()
        current = row["mastery_score"] if row else 0

        if result == "known":
            new_score = current + 1
        elif result == "forgot":
            new_score = max(0, current - 1)
        else:
            new_score = current

        now = datetime.now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO user_progress "
            "(chat_id, topic, word_id, mastery_score, last_seen) VALUES (?, ?, ?, ?, ?)",
            (chat_id, topic, word_id, new_score, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO history (chat_id, topic, word_id, timestamp, result) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, topic, word_id, now, result),
        )
        conn.commit()
        # user_progress upsert always succeeds at this point; return True unconditionally.
        # The history insert may silently deduplicate a retry — that is acceptable.
        return True
    finally:
        conn.close()
