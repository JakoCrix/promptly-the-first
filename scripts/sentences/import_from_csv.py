"""
Import pre-generated sentences from CSV backups into the word_sentences table.

Reads all CSV files from data/word_sentences/*.csv and inserts any sentences
not already present in the DB. Safe to re-run — uses INSERT OR IGNORE logic
(skips duplicates based on topic + word_id + sentence).

Usage:
    python scripts/sentences/import_from_csv.py
"""
import csv
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, PROJECT_ROOT)

from core.vocab import _get_conn, init_db

CSV_DIR = os.path.join(PROJECT_ROOT, "data", "word_sentences")


def main() -> None:
    init_db()
    csv_files = sorted(Path(CSV_DIR).glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {CSV_DIR}")
        return

    conn = _get_conn()
    try:
        total_inserted = total_skipped = 0

        for csv_path in csv_files:
            inserted = skipped = 0
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    topic = row.get("topic", "").strip()
                    word_id = row.get("word_id", "").strip()
                    sentence = row.get("sentence", "").strip()
                    if not (topic and word_id and sentence):
                        skipped += 1
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM word_sentences WHERE topic = ? AND word_id = ? AND sentence = ?",
                        (topic, word_id, sentence),
                    ).fetchone()
                    if existing:
                        skipped += 1
                    else:
                        conn.execute(
                            "INSERT INTO word_sentences (topic, word_id, sentence) VALUES (?, ?, ?)",
                            (topic, word_id, sentence),
                        )
                        inserted += 1

            conn.commit()
            print(f"{csv_path.name}: {inserted} inserted, {skipped} skipped")
            total_inserted += inserted
            total_skipped += skipped

    finally:
        conn.close()

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped.")


if __name__ == "__main__":
    main()
