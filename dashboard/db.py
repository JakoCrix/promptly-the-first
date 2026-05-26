# -*- coding: utf-8 -*-
import os
import sys
import sqlite3

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DB_PATH, ALLOWED_CHAT_IDS, RETIREMENT_THRESHOLD

EDITABLE_FIELDS = {"word", "sentence"}

_WORDS_COLUMNS = ["chat_id", "topic", "word_id", "word", "sentence", "mastery_score", "last_seen"]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_word_pool() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS word_pool (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                topic    TEXT NOT NULL,
                word_id  TEXT NOT NULL,
                word     TEXT NOT NULL,
                sentence TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def get_all_words_df() -> pd.DataFrame:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT chat_id, topic, word_id, word, sentence, mastery_score, last_seen "
            "FROM words ORDER BY topic, word_id"
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows], columns=_WORDS_COLUMNS)
    finally:
        conn.close()


def get_word_counts() -> dict:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN mastery_score >= ? THEN 1 ELSE 0 END) AS retired "
            "FROM words",
            (RETIREMENT_THRESHOLD,),
        ).fetchone()
        return {"total": row["total"] or 0, "retired": row["retired"] or 0}
    finally:
        conn.close()


def update_word_entry(chat_id: int, topic: str, word_id: str, word: str, sentence: str) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE words SET word = ?, sentence = ? WHERE chat_id = ? AND topic = ? AND word_id = ?",
            (word, sentence, chat_id, topic, word_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_word_pool() -> list:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, topic, word_id, word, sentence FROM word_pool ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_to_decks(pool_id: int, topic: str, word_id: str, word: str, sentence: str) -> tuple:
    conn = _get_conn()
    inserted = 0
    skipped = 0
    try:
        for chat_id in ALLOWED_CHAT_IDS:
            cur = conn.execute(
                "INSERT OR IGNORE INTO words "
                "(chat_id, topic, word_id, word, sentence, mastery_score, last_seen) "
                "VALUES (?, ?, ?, ?, ?, 0, NULL)",
                (chat_id, topic, word_id, word, sentence),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.execute("DELETE FROM word_pool WHERE id = ?", (pool_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted, skipped


def delete_word(topic: str, word_id: str) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM words WHERE topic = ? AND word_id = ?",
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
