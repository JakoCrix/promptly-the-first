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
        "mastery_score": row["mastery_score"],
        "last_seen":     row["last_seen"],
    }


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS words (
                chat_id       INTEGER NOT NULL,
                topic         TEXT    NOT NULL,
                word_id       TEXT    NOT NULL,
                word          TEXT    NOT NULL,
                mastery_score INTEGER NOT NULL DEFAULT 0,
                last_seen     TEXT,
                PRIMARY KEY (chat_id, topic, word_id)
            );

            CREATE TABLE IF NOT EXISTS history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id   INTEGER NOT NULL,
                topic     TEXT    NOT NULL,
                word_id   TEXT    NOT NULL,
                timestamp TEXT    NOT NULL,
                result    TEXT    NOT NULL,
                UNIQUE (chat_id, topic, word_id, timestamp, result),
                FOREIGN KEY (chat_id, topic, word_id) REFERENCES words (chat_id, topic, word_id)
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
        cols = {row[1] for row in conn.execute("PRAGMA table_info(words)")}
        if "sentence" in cols:
            conn.execute("ALTER TABLE words DROP COLUMN sentence")
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
            "SELECT DISTINCT topic FROM words WHERE chat_id = ? ORDER BY topic",
            (chat_id,),
        ).fetchall()
        return [r["topic"] for r in rows]
    finally:
        conn.close()


def get_word(chat_id: int, topic: str, word_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT word_id, topic, word, mastery_score, last_seen "
            "FROM words WHERE chat_id = ? AND topic = ? AND word_id = ?",
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
                "SELECT topic FROM words WHERE chat_id = ? ORDER BY topic LIMIT 1",
                (chat_id,),
            ).fetchone()
            if first is None:
                return None
            topic = first["topic"]

        rows = conn.execute(
            "SELECT word_id, topic, word, mastery_score, last_seen "
            "FROM words WHERE chat_id = ? AND topic = ? AND mastery_score < ?",
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


def _slugify(text: str) -> str:
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
            "SELECT word_id, word FROM words WHERE chat_id = ? AND topic = ? ORDER BY RANDOM() LIMIT ?",
            (chat_id, topic, n),
        ).fetchall()
        return [{"id": r["word_id"], "word": r["word"]} for r in rows]
    finally:
        conn.close()


def insert_word(chat_id: int, topic: str, word: str) -> bool:
    word_id = _slugify(word)
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO words (chat_id, topic, word_id, word, mastery_score, last_seen) "
            "VALUES (?, ?, ?, ?, 0, NULL)",
            (chat_id, topic, word_id, word),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def record_feedback(chat_id: int, topic: str, word_id: str, result: str) -> bool:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT mastery_score FROM words WHERE chat_id = ? AND topic = ? AND word_id = ?",
            (chat_id, topic, word_id),
        ).fetchone()
        if row is None:
            return False

        current = row["mastery_score"]
        if result == "known":
            new_score = current + 1
        elif result == "forgot":
            new_score = max(0, current - 1)
        else:
            new_score = current

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE words SET mastery_score = ?, last_seen = ? "
            "WHERE chat_id = ? AND topic = ? AND word_id = ?",
            (new_score, now, chat_id, topic, word_id),
        )
        cur = conn.execute(
            "INSERT OR IGNORE INTO history (chat_id, topic, word_id, timestamp, result) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, topic, word_id, now, result),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()
