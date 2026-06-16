"""
One-time migration: split kana out of Japanese definition into the pronunciation column.

Before: definition = "あう — to meet, to see"   pronunciation = "au"  (or NULL)
After:  definition = "to meet, to see"           pronunciation = "あう, au" (or "あう")

Safe to re-run — words already migrated (definition contains no ' — ') are skipped.

Usage:
    python scripts/pronunciation/migrate_japanese_hint.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    init_db()
    conn = _get_conn()
    try:
        # Words that already have romaji: pronunciation becomes "kana, romaji"
        cur = conn.execute("""
            UPDATE corpus
            SET
                pronunciation = SUBSTR(definition, 1, INSTR(definition, ' — ') - 1) || ', ' || pronunciation,
                definition          = SUBSTR(definition, INSTR(definition, ' — ') + 3)
            WHERE topic LIKE 'japanese_jlpt_%'
              AND definition LIKE '% — %'
              AND pronunciation IS NOT NULL
        """)
        with_romaji = cur.rowcount

        # Words without romaji yet: move kana to pronunciation, clean definition
        cur = conn.execute("""
            UPDATE corpus
            SET
                pronunciation = SUBSTR(definition, 1, INSTR(definition, ' — ') - 1),
                definition          = SUBSTR(definition, INSTR(definition, ' — ') + 3)
            WHERE topic LIKE 'japanese_jlpt_%'
              AND definition LIKE '% — %'
              AND pronunciation IS NULL
        """)
        without_romaji = cur.rowcount

        conn.commit()
        print(f"Migrated {with_romaji} words (kana + romaji) and {without_romaji} words (kana only).")
        print("Run scripts/pronunciation/japanese.py to fill in remaining romaji.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
