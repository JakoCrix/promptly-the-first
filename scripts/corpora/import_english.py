"""
Import NGSL 1.01 (New General Service List) into the corpus as a single topic.

Source: koba-ninkigumi/ngsl on GitHub (CC BY-SA 4.0).
~2,800 high-frequency English words covering >92% of general texts.
Row order in the file = frequency rank (most frequent first).

Usage:
    python scripts/corpora/import_english.py
"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db, slugify
from scripts.corpora._base import bulk_insert, validate_word_id

_NGSL_URL = (
    "https://raw.githubusercontent.com/koba-ninkigumi/ngsl/master/NGSL-1.01.csv"
)
_TOPIC = "english_ngsl"
_DESCRIPTION = (
    "English — New General Service List (NGSL 1.01, CC BY-SA 4.0). "
    "~2,800 high-frequency words covering >92% of general English texts."
)


def main() -> None:
    init_db()

    print("Fetching NGSL data...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(_NGSL_URL, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    print("done.")

    conn = _get_conn()

    rows = []
    seen_ids: set[str] = set()
    rank = 0
    for line in content.splitlines():
        # File format: headword,variant1,variant2,... (no header row)
        parts = line.split(",")
        headword = parts[0].strip().lower()
        if not headword:
            continue
        rank += 1
        word_id = slugify(headword)
        if not word_id or word_id in seen_ids:
            continue
        if not validate_word_id(_TOPIC, word_id):
            print(f"  SKIP (callback overflow): {headword!r}")
            continue
        seen_ids.add(word_id)
        rows.append((_TOPIC, word_id, headword, None, rank))

    try:
        inserted, skipped = bulk_insert(conn, rows, _TOPIC, _DESCRIPTION)
    finally:
        conn.close()

    print(f"Done. {inserted} inserted, {skipped} skipped (of {len(rows)} processed).")


if __name__ == "__main__":
    main()
