"""
Import pre-generated pronunciations from CSV backups into the corpus table.

Japanese (data/pronunciations/japanese.csv):
  - Columns: topic, word_id, word, kana, romaji
  - Sets pronunciation = "kana, romaji" for words that have kana-only pronunciation

Korean (data/pronunciations/korean.csv):
  - Columns: topic, word_id, word, meaning, romanization
  - Sets pronunciation = romanization for words with NULL pronunciation

Safe to re-run — skips words that already have a complete pronunciation.

Usage:
    python scripts/pronunciation/import_from_csv.py
"""
import csv
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, PROJECT_ROOT)

from core.vocab import _get_conn, init_db

DATA_DIR = Path(PROJECT_ROOT) / "data" / "pronunciations"


def _import_japanese(conn) -> tuple[int, int]:
    csv_path = DATA_DIR / "japanese.csv"
    if not csv_path.exists():
        print(f"  japanese.csv not found at {csv_path} — skipping.")
        return 0, 0

    inserted = skipped = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            topic = row["topic"].strip()
            word_id = row["word_id"].strip()
            kana = row["kana"].strip()
            romaji = row["romaji"].strip()
            if not (topic and word_id and romaji):
                skipped += 1
                continue

            current = conn.execute(
                "SELECT pronunciation FROM corpus WHERE topic = ? AND word_id = ?",
                (topic, word_id),
            ).fetchone()
            if current is None:
                skipped += 1
                continue
            pron = current[0]
            if pron and "," in pron:
                skipped += 1  # already has kana+romaji
                continue

            new_pron = f"{kana}, {romaji}" if kana else romaji
            conn.execute(
                "UPDATE corpus SET pronunciation = ? WHERE topic = ? AND word_id = ?",
                (new_pron, topic, word_id),
            )
            inserted += 1

    conn.commit()
    return inserted, skipped


def _import_korean(conn) -> tuple[int, int]:
    csv_path = DATA_DIR / "korean.csv"
    if not csv_path.exists():
        print(f"  korean.csv not found at {csv_path} — skipping.")
        return 0, 0

    inserted = skipped = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            topic = row["topic"].strip()
            word_id = row["word_id"].strip()
            romanization = row["romanization"].strip()
            if not (topic and word_id and romanization):
                skipped += 1
                continue

            current = conn.execute(
                "SELECT pronunciation FROM corpus WHERE topic = ? AND word_id = ?",
                (topic, word_id),
            ).fetchone()
            if current is None:
                skipped += 1
                continue
            if current[0]:
                skipped += 1  # already set
                continue

            conn.execute(
                "UPDATE corpus SET pronunciation = ? WHERE topic = ? AND word_id = ?",
                (romanization, topic, word_id),
            )
            inserted += 1

    conn.commit()
    return inserted, skipped


def main() -> None:
    init_db()
    conn = _get_conn()
    try:
        jp_inserted, jp_skipped = _import_japanese(conn)
        print(f"  japanese: {jp_inserted} updated, {jp_skipped} skipped")

        ko_inserted, ko_skipped = _import_korean(conn)
        print(f"  korean:   {ko_inserted} updated, {ko_skipped} skipped")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
