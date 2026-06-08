# -*- coding: utf-8 -*-
import os
import sys
import sqlite3

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DB_PATH, ALLOWED_CHAT_IDS, RETIREMENT_THRESHOLD

EDITABLE_FIELDS = {"word"}

_WORDS_COLUMNS = ["chat_id", "topic", "word_id", "word", "hint", "mastery_score", "last_seen"]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_word_pool() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS word_pool (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                topic   TEXT NOT NULL,
                word_id TEXT NOT NULL,
                word    TEXT NOT NULL
            )
        """)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(word_pool)")}
        if "sentence" in cols:
            conn.execute("ALTER TABLE word_pool DROP COLUMN sentence")
        conn.commit()
    finally:
        conn.close()


def get_all_words_df() -> pd.DataFrame:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT up.chat_id, c.topic, c.word_id, c.word, c.hint, "
            "COALESCE(up.mastery_score, 0) AS mastery_score, up.last_seen "
            "FROM corpus c "
            "LEFT JOIN user_progress up ON c.topic = up.topic AND c.word_id = up.word_id "
            "ORDER BY c.topic, c.word_id"
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows], columns=_WORDS_COLUMNS)
    finally:
        conn.close()


def get_word_counts(chat_id: int | None = None) -> dict:
    conn = _get_conn()
    try:
        if chat_id is not None:
            row = conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM corpus) AS total, "
                "(SELECT COUNT(*) FROM user_progress "
                " WHERE chat_id = ? AND mastery_score >= ?) AS retired",
                (chat_id, RETIREMENT_THRESHOLD),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM corpus) AS total, "
                "(SELECT COUNT(*) FROM user_progress WHERE mastery_score >= ?) AS retired",
                (RETIREMENT_THRESHOLD,),
            ).fetchone()
        return {"total": row["total"] or 0, "retired": row["retired"] or 0}
    finally:
        conn.close()


def update_word_entry(chat_id: int, topic: str, word_id: str, word: str) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE corpus SET word = ? WHERE topic = ? AND word_id = ?",
            (word, topic, word_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_word_pool() -> list:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, topic, word_id, word FROM word_pool ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_to_decks(pool_id: int, topic: str, word_id: str, word: str) -> tuple:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO corpus (topic, word_id, word) VALUES (?, ?, ?)",
            (topic, word_id, word),
        )
        inserted = cur.rowcount
        skipped = 1 - inserted
        conn.execute("DELETE FROM word_pool WHERE id = ?", (pool_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted, skipped


def delete_word(chat_id: int, topic: str, word_id: str) -> int:
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM user_progress WHERE chat_id = ? AND topic = ? AND word_id = ?",
            (chat_id, topic, word_id),
        )
        cur = conn.execute(
            "DELETE FROM corpus WHERE topic = ? AND word_id = ?",
            (topic, word_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def reject_from_pool(pool_id: int) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM word_pool WHERE id = ?", (pool_id,))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()
