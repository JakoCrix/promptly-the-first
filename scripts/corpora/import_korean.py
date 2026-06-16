"""
Import Korean vocabulary into the corpus across 6 TOPIK-approximate levels.

Source: jemdiggity/hanja-wordlist "Korean Vocab 6000" (frequency-ordered).
Columns: # (rank), Korean, English.
~3,474 entries; levels assigned by frequency band.

TOPIK-approximate level bands:
  korean_topik_1 : ranks   1–500   (beginner core)
  korean_topik_2 : ranks 501–1000
  korean_topik_3 : ranks 1001–1800
  korean_topik_4 : ranks 1801–2500
  korean_topik_5 : ranks 2501–3000
  korean_topik_6 : ranks 3001+   (advanced)

word_id: k_{rank:04d} — unique, ASCII, fits within callback limit.
definition: English gloss from the source list.

Usage:
    python scripts/corpora/import_korean.py
"""
import csv
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db
from scripts.corpora._base import bulk_insert, validate_word_id

_KO_URL = (
    "https://raw.githubusercontent.com/jemdiggity/hanja-wordlist/master/"
    "Korean%20Vocab%206000%20-%20Sheet1.tsv"
)

_LEVEL_BANDS = [
    (1,    500,  "korean_topik_1"),
    (501,  1000, "korean_topik_2"),
    (1001, 1800, "korean_topik_3"),
    (1801, 2500, "korean_topik_4"),
    (2501, 3000, "korean_topik_5"),
    (3001, 9999, "korean_topik_6"),
]

_DESCRIPTIONS = {
    "korean_topik_1": "Korean TOPIK-approximate Level 1 — beginner core vocabulary (ranks 1–500).",
    "korean_topik_2": "Korean TOPIK-approximate Level 2 — elementary vocabulary (ranks 501–1000).",
    "korean_topik_3": "Korean TOPIK-approximate Level 3 — intermediate vocabulary (ranks 1001–1800).",
    "korean_topik_4": "Korean TOPIK-approximate Level 4 — upper-intermediate vocabulary (ranks 1801–2500).",
    "korean_topik_5": "Korean TOPIK-approximate Level 5 — advanced vocabulary (ranks 2501–3000).",
    "korean_topik_6": "Korean TOPIK-approximate Level 6 — high-advanced vocabulary (ranks 3001+).",
}


def _topic_for_rank(rank: int) -> str:
    for lo, hi, topic in _LEVEL_BANDS:
        if lo <= rank <= hi:
            return topic
    return "korean_topik_6"


def main() -> None:
    init_db()
    conn = _get_conn()

    print("Fetching Korean vocab list...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(_KO_URL, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    print("done.")

    # Bucket rows by topic before inserting.
    topic_rows: dict[str, list[tuple]] = {t: [] for _, _, t in _LEVEL_BANDS}
    reader = csv.reader(io.StringIO(content), delimiter="\t")
    next(reader, None)  # skip header: "#\tKorean\tEnglish"

    for row in reader:
        if len(row) < 3:
            continue
        rank_str, korean, english = row[0].strip(), row[1].strip(), row[2].strip()
        if not rank_str.isdigit() or not korean:
            continue
        rank = int(rank_str)
        english = english or ""
        topic = _topic_for_rank(rank)
        word_id = f"k_{rank:04d}"
        if not validate_word_id(topic, word_id):
            print(f"  SKIP (callback overflow): rank={rank}")
            continue
        definition = english if english else None
        topic_rows[topic].append((topic, word_id, korean, definition, rank, None))

    try:
        total_inserted = total_skipped = 0
        for _, _, topic in _LEVEL_BANDS:
            rows = topic_rows[topic]
            if not rows:
                print(f"  {topic}: no data")
                continue
            inserted, skipped = bulk_insert(conn, rows, topic, _DESCRIPTIONS[topic])
            total_inserted += inserted
            total_skipped += skipped
            print(f"  {topic}: {inserted} inserted, {skipped} skipped")
    finally:
        conn.close()

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped.")


if __name__ == "__main__":
    main()
