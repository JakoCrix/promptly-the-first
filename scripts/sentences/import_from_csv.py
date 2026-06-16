"""
Import pre-generated sentences from CSV backups into the word_sentences table.

Reads all CSV files from data/word_sentences/*.csv and inserts any sentences
not already present in the DB. Uses INSERT OR IGNORE — safe to re-run.
Words already at 10 sentences are skipped to respect the sentence pool cap.

Usage:
    python scripts/sentences/import_from_csv.py
"""
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.vocab import _get_conn, init_db, _POOLED_SENTENCE_CAP

CSV_DIR = PROJECT_ROOT / "data" / "word_sentences"


def main() -> None:
    init_db()
    csv_files = sorted(CSV_DIR.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {CSV_DIR}")
        return

    conn = _get_conn()
    try:
        total_inserted = total_skipped = 0

        for csv_path in csv_files:
            inserted = skipped = 0
            try:
                with open(csv_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        topic = row.get("topic", "").strip()
                        word_id = row.get("word_id", "").strip()
                        sentence = row.get("sentence", "").strip()
                        if not (topic and word_id and sentence):
                            skipped += 1
                            continue

                        count = conn.execute(
                            "SELECT COUNT(*) FROM word_sentences WHERE topic = ? AND word_id = ?",
                            (topic, word_id),
                        ).fetchone()[0]
                        if count >= _POOLED_SENTENCE_CAP:
                            skipped += 1
                            continue

                        cur = conn.execute(
                            "INSERT OR IGNORE INTO word_sentences (topic, word_id, sentence)"
                            " VALUES (?, ?, ?)",
                            (topic, word_id, sentence),
                        )
                        if cur.rowcount:
                            inserted += 1
                        else:
                            skipped += 1

                conn.commit()
                print(f"{csv_path.name}: {inserted} inserted, {skipped} skipped")
            except Exception as exc:
                conn.rollback()
                print(f"  ERROR processing {csv_path.name}: {exc} — rolled back, skipping file.")

            total_inserted += inserted
            total_skipped += skipped

    finally:
        conn.close()

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped.")


if __name__ == "__main__":
    main()
