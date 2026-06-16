"""
Import community JLPT N5–N1 vocabulary into the corpus.

Source: jamsinclair/open-anki-jlpt-decks (MIT licence).
Columns: Expression (kanji/kana), Reading (hiragana), Meaning (English), Tags, GUID.
word_id is derived from the English meaning (hiragana is non-ASCII and can't be slugified).

Topics: japanese_jlpt_n5 … japanese_jlpt_n1

Usage:
    python scripts/corpora/import_japanese.py
"""
import csv
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db, slugify
from scripts.corpora._base import _MAX_CALLBACK_BYTES, bulk_insert

_BASE_URL = (
    "https://raw.githubusercontent.com/jamsinclair/open-anki-jlpt-decks"
    "/main/src/{level}.csv"
)
_LEVELS = ["n5", "n4", "n3", "n2", "n1"]

_DESCRIPTIONS = {
    "japanese_jlpt_n5": "Japanese JLPT N5 — beginner (~800 words).",
    "japanese_jlpt_n4": "Japanese JLPT N4 — elementary (~1,500 words at this level).",
    "japanese_jlpt_n3": "Japanese JLPT N3 — intermediate (~1,500 words at this level).",
    "japanese_jlpt_n2": "Japanese JLPT N2 — upper-intermediate (~1,700 words at this level).",
    "japanese_jlpt_n1": "Japanese JLPT N1 — advanced (~2,000+ words at this level).",
}


def _fetch_level(level: str) -> list[dict]:
    url = _BASE_URL.format(level=level)
    with urllib.request.urlopen(url, timeout=30) as resp:
        content = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def _build_row(entry: dict, topic: str, rank: int, seen_ids: set[str]) -> tuple | None:
    word = (entry.get("expression") or "").strip()
    reading = (entry.get("reading") or "").strip()
    meaning = (entry.get("meaning") or "").strip()
    if not word or not meaning:
        return None

    # Hiragana reading is non-ASCII; derive word_id from English meaning instead.
    # Truncate so worst-case callback_data never exceeds 64 bytes.
    prefix_len = len(f"forgot:999999999999:{topic}:".encode())
    max_id_len = _MAX_CALLBACK_BYTES - prefix_len
    base_id = slugify(meaning)[:max_id_len]
    if not base_id:
        return None

    # Deduplicate within topic.
    word_id = base_id
    counter = 2
    while word_id in seen_ids:
        suffix = f"_{counter}"
        word_id = base_id[: max_id_len - len(suffix)] + suffix
        counter += 1

    seen_ids.add(word_id)
    definition = meaning if meaning else None
    pronunciation = reading if reading else None
    return (topic, word_id, word, definition, rank, pronunciation)


def main() -> None:
    init_db()
    conn = _get_conn()

    try:
        total_inserted = total_skipped = 0
        for level in _LEVELS:
            topic = f"japanese_jlpt_{level}"
            print(f"  {topic}: fetching...", end=" ", flush=True)
            try:
                entries = _fetch_level(level)
            except Exception as exc:
                print(f"FAILED ({exc})")
                continue

            rows = []
            seen_ids: set[str] = set()
            for rank, entry in enumerate(entries, start=1):
                row = _build_row(entry, topic, rank, seen_ids)
                if row:
                    rows.append(row)

            inserted, skipped = bulk_insert(conn, rows, topic, _DESCRIPTIONS[topic])
            total_inserted += inserted
            total_skipped += skipped
            print(f"{inserted} inserted, {skipped} skipped")

    finally:
        conn.close()

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped.")


if __name__ == "__main__":
    main()
