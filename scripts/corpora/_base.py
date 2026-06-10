"""Shared utilities for corpus import scripts."""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, slugify  # noqa: E402

_MAX_CALLBACK_BYTES = 64


def normalize_slug(text: str) -> str:
    """Strip diacritics then slugify — needed for accented scripts (Spanish, etc.)."""
    nfd = unicodedata.normalize("NFD", text.lower())
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return slugify(ascii_text)


def validate_word_id(topic: str, word_id: str) -> bool:
    """Return False if the worst-case callback_data would exceed Telegram's 64-byte limit."""
    worst_case = f"forgot:999999999999:{topic}:{word_id}"
    return len(worst_case.encode()) <= _MAX_CALLBACK_BYTES


def bulk_insert(conn, rows: list[tuple], topic: str, description: str) -> tuple[int, int]:
    """INSERT OR IGNORE rows into corpus and seed the topics table.

    Each row must be: (topic, word_id, word, hint, frequency_rank)
    Returns (inserted, skipped).
    """
    inserted = skipped = 0
    for row in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO corpus (topic, word_id, word, hint, frequency_rank)"
            " VALUES (?, ?, ?, ?, ?)",
            row,
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    conn.execute(
        "INSERT OR IGNORE INTO topics (topic, description) VALUES (?, ?)",
        (topic, description),
    )
    conn.commit()
    return inserted, skipped
