"""
Bootstrap script for Promptly — safe to re-run on any DB state.

Run after every cloud deploy to ensure:
  1. All DB tables exist with the current schema (migrations included).
  2. Corpus word lists are populated for all languages.
  3. Pronunciations generated for Japanese and Korean (Gemini, resumable).
  4. Pre-generated sentence CSVs loaded into the word_sentences bank.

Usage:
    python data/_bootstrap_db.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.vocab import _get_conn, init_db

ROOT = Path(__file__).parent.parent

# (topic_prefix, importer_script, extra_args)
# Chinese uses --confirm to delete+reimport, but step2_corpus only calls this
# when chinese_hsk has zero rows. Do NOT call import_chinese.py --confirm
# outside of step2_corpus — it will wipe existing corpus and user_progress rows.
CORPUS_IMPORTERS = [
    ("chinese_hsk",   "scripts/corpora/import_chinese.py",  ["--confirm"]),
    ("english_ngsl",  "scripts/corpora/import_english.py",  []),
    ("japanese_jlpt", "scripts/corpora/import_japanese.py", []),
    ("korean_topik",  "scripts/corpora/import_korean.py",   []),
    ("spanish_pcic",  "scripts/corpora/import_spanish.py",  []),
]

PRONUNCIATION_IMPORT = "scripts/pronunciation/import_from_csv.py"


def _run(script: str, args: list[str] | None = None) -> bool:
    result = subprocess.run([sys.executable, str(ROOT / script)] + (args or []))
    if result.returncode != 0:
        print(f"  WARNING: {script} exited with code {result.returncode}")
        return False
    return True


def step1_schema() -> None:
    print("=== Step 1: Schema ===")
    init_db()
    print("All tables verified / migrated.\n")


def step2_corpus() -> None:
    print("=== Step 2: Corpus word lists ===")
    conn = _get_conn()
    try:
        for prefix, script, args in CORPUS_IMPORTERS:
            count = conn.execute(
                "SELECT COUNT(*) FROM corpus WHERE topic LIKE ?", (f"{prefix}%",)
            ).fetchone()[0]
            if count > 0:
                print(f"  {prefix}: {count} rows — skipping.")
                continue
            print(f"  {prefix}: empty — running importer...")
            _run(script, args)
    finally:
        conn.close()
    print()


def step3_pronunciations() -> None:
    print("=== Step 3: Pronunciations ===")
    _run(PRONUNCIATION_IMPORT)
    print()


def step4_sentences() -> None:
    print("=== Step 4: Sentence bank ===")
    _run("scripts/sentences/import_from_csv.py")
    print()


if __name__ == "__main__":
    step1_schema()
    step2_corpus()
    step3_pronunciations()
    step4_sentences()
    print("Bootstrap complete.")
