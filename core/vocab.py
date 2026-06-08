import os
import random
import re
import sqlite3
from datetime import datetime

from core.config import DATA_DIR, DB_PATH, NEVER_SEEN_SECONDS, RETIREMENT_THRESHOLD


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":            row["word_id"],
        "topic":         row["topic"],
        "word":          row["word"],
        "hint":          row["hint"],
        "mastery_score": row["mastery_score"],
        "last_seen":     row["last_seen"],
    }


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS corpus (
                topic   TEXT NOT NULL,
                word_id TEXT NOT NULL,
                word    TEXT NOT NULL,
                hint    TEXT,
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
                chat_id      INTEGER PRIMARY KEY,
                active_topic TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule (
                date      TEXT    NOT NULL,
                slot_time TEXT    NOT NULL,
                fired     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (date, slot_time)
            );

            CREATE TABLE IF NOT EXISTS topics (
                topic       TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT ''
            );
        """)
        conn.commit()

        # One-time migration: move data from old `words` table into corpus + user_progress.
        # SQLite FK enforcement is OFF by default, so DROP TABLE words succeeds even though
        # history rows may reference it. History rows are re-anchored to user_progress after migration.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "words" in tables:
            word_cols = {r[1] for r in conn.execute("PRAGMA table_info(words)")}
            if "hint" not in word_cols:
                conn.execute("ALTER TABLE words ADD COLUMN hint TEXT")
                conn.commit()
            conn.executescript("""
                INSERT OR IGNORE INTO corpus (topic, word_id, word, hint)
                    SELECT DISTINCT topic, word_id, word, hint FROM words;
                INSERT OR IGNORE INTO user_progress (chat_id, topic, word_id, mastery_score, last_seen)
                    SELECT chat_id, topic, word_id, mastery_score, last_seen FROM words;
                DROP TABLE words;
            """)
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
            "INSERT OR REPLACE INTO user_settings (chat_id, active_topic) VALUES (?, ?)",
            (chat_id, topic),
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


def get_word(chat_id: int, topic: str, word_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.hint, "
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
        row = conn.execute(
            "SELECT active_topic FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        topic = row["active_topic"] if row else None

        if topic is None:
            first = conn.execute(
                "SELECT topic FROM corpus ORDER BY topic LIMIT 1",
            ).fetchone()
            if first is None:
                return None
            topic = first["topic"]

        rows = conn.execute(
            "SELECT c.word_id, c.topic, c.word, c.hint, "
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


def insert_word(chat_id: int, topic: str, word: str) -> bool:
    word_id = slugify(word)
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
