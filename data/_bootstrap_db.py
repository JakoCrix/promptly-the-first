"""
Bootstrap script for Promptly — safe to re-run on any DB state.

Run after every cloud deploy to ensure:
  1. All DB tables exist with the current schema (migrations included).
  2. Corpus word lists are populated for all languages.
  3. Pre-generated sentence CSVs are loaded into the word_sentences bank.

Usage:
    python data/_bootstrap_db.py
"""
import csv
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.vocab import _get_conn, init_db

ROOT = Path(__file__).parent.parent

# (topic_prefix, importer_script, extra_args)
# NOTE: --confirm on the Chinese importer deletes all existing chinese_hsk* rows before
# re-importing. This is safe here ONLY because step2_corpus skips when count > 0.
# Do NOT remove the count guard without also removing --confirm.
CORPUS_IMPORTERS = [
    ("chinese_hsk",   "scripts/corpora/import_chinese.py",  ["--confirm"]),
    ("english_ngsl",  "scripts/corpora/import_english.py",  []),
    ("japanese_jlpt", "scripts/corpora/import_japanese.py", []),
    ("korean_topik",  "scripts/corpora/import_korean.py",   []),
    ("spanish_pcic",  "scripts/corpora/import_spanish.py",  []),
]

# Treat this many pre-existing sentences as "already ingested" and skip the CSV.
_SENTENCE_INGEST_THRESHOLD = 1000


def step1_schema() -> None:
    print("=== Step 1: Schema ===")
    init_db()
    print("All tables verified / migrated.\n")


def step2_corpus() -> None:
    print("=== Step 2: Corpus word lists ===")
    for prefix, script, args in CORPUS_IMPORTERS:
        conn = _get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM corpus WHERE topic LIKE ?", (f"{prefix}%",)
            ).fetchone()[0]
        finally:
            conn.close()

        if count > 0:
            print(f"  {prefix}: {count} rows — skipping.")
            continue

        print(f"  {prefix}: empty — running importer...")
        result = subprocess.run(
            [sys.executable, str(ROOT / script)] + args,
        )
        if result.returncode != 0:
            print(f"  WARNING: importer for {prefix} exited with code {result.returncode}")
    print()


def step3_sentences() -> None:
    print("=== Step 3: Sentence CSVs ===")
    csv_dir = ROOT / "data" / "word_sentences"
    if not csv_dir.exists():
        print("  data/word_sentences/ not found — skipping.\n")
        return

    conn = _get_conn()
    try:
        for csv_path in sorted(csv_dir.glob("*.csv")):
            # Infer topic prefix from filename: chinese.csv -> "chinese_"
            topic_prefix = csv_path.stem + "_"
            existing = conn.execute(
                "SELECT COUNT(*) FROM word_sentences WHERE topic LIKE ?",
                (f"{topic_prefix}%",),
            ).fetchone()[0]

            if existing >= _SENTENCE_INGEST_THRESHOLD:
                print(f"  {csv_path.name}: {existing} sentences already in DB — skipping.")
                continue

            with open(csv_path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                required = {"topic", "word_id", "sentence"}
                if reader.fieldnames and not required.issubset(set(reader.fieldnames)):
                    missing = required - set(reader.fieldnames)
                    print(f"  WARNING: {csv_path.name} missing columns {missing} — skipping.")
                    continue
                cursor = conn.executemany(
                    "INSERT INTO word_sentences (topic, word_id, sentence) VALUES (?, ?, ?)",
                    ((row["topic"], row["word_id"], row["sentence"]) for row in reader),
                )
            conn.commit()
            print(f"  {csv_path.name}: inserted {cursor.rowcount} sentences.")
    finally:
        conn.close()
    print()


if __name__ == "__main__":
    step1_schema()
    step2_corpus()
    step3_sentences()
    print("Bootstrap complete.")
