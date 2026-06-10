"""
Import Spanish vocabulary into the corpus across 6 pseudo-CEFR levels.

Source: doozan/spanish_data frequency.csv (CC-BY-4.0).
Columns: count, spanish, pos, flags, usage.

Only content words (nouns, verbs, adjectives, adverbs) are imported; function words
(prepositions, articles, pronouns, conjunctions, etc.) are excluded since they have
no learning value as flashcard vocabulary.

CEFR-approximate level bands (filtered-rank after function word removal):
  spanish_pcic_a1 : filtered ranks   1–500
  spanish_pcic_a2 : filtered ranks 501–1500
  spanish_pcic_b1 : filtered ranks 1501–3000
  spanish_pcic_b2 : filtered ranks 3001–6000
  spanish_pcic_c1 : filtered ranks 6001–9000
  spanish_pcic_c2 : filtered ranks 9001+

word_id: es_{rank:05d} — unique ASCII key (Spanish has accented chars that slugify drops).
hint:    None — Spanish uses Latin script; the Gemini sentence provides context.
         Add English glosses via a later migration once a suitable open source is found.

Usage:
    python scripts/corpora/import_spanish.py
"""
import csv
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db
from scripts.corpora._base import bulk_insert, validate_word_id

_FREQ_URL = (
    "https://raw.githubusercontent.com/doozan/spanish_data/master/frequency.csv"
)

# Content POS tags to keep; everything else (prep, art, pron, conj, etc.) is excluded.
_CONTENT_POS = {"n", "v", "adj", "adv", "v/adj"}

_LEVEL_BANDS = [
    (1,    500,  "spanish_pcic_a1"),
    (501,  1500, "spanish_pcic_a2"),
    (1501, 3000, "spanish_pcic_b1"),
    (3001, 6000, "spanish_pcic_b2"),
    (6001, 9000, "spanish_pcic_c1"),
    (9001, 99999, "spanish_pcic_c2"),
]

_DESCRIPTIONS = {
    "spanish_pcic_a1": "Spanish PCIC-approximate A1 — highest-frequency content words (filtered ranks 1–500).",
    "spanish_pcic_a2": "Spanish PCIC-approximate A2 — frequent content words (filtered ranks 501–1500).",
    "spanish_pcic_b1": "Spanish PCIC-approximate B1 — intermediate vocabulary (filtered ranks 1501–3000).",
    "spanish_pcic_b2": "Spanish PCIC-approximate B2 — upper-intermediate vocabulary (filtered ranks 3001–6000).",
    "spanish_pcic_c1": "Spanish PCIC-approximate C1 — advanced vocabulary (filtered ranks 6001–9000).",
    "spanish_pcic_c2": "Spanish PCIC-approximate C2 — near-native vocabulary (filtered ranks 9001+).",
}


def _topic_for_rank(rank: int) -> str:
    for lo, hi, topic in _LEVEL_BANDS:
        if lo <= rank <= hi:
            return topic
    return "spanish_pcic_c2"


def main() -> None:
    init_db()
    conn = _get_conn()

    print("Fetching Spanish frequency data...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(_FREQ_URL, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    print("done.")

    topic_rows: dict[str, list[tuple]] = {t: [] for _, _, t in _LEVEL_BANDS}
    reader = csv.DictReader(io.StringIO(content))

    filtered_rank = 0
    for entry in reader:
        pos = (entry.get("pos") or "").strip().lower()
        flags = (entry.get("flags") or "").strip().upper()
        word = (entry.get("spanish") or "").strip()

        # Skip function words, duplicates (same lemma/different POS already counted), empty.
        if pos not in _CONTENT_POS or "DUPLICATE" in flags or not word:
            continue

        filtered_rank += 1
        topic = _topic_for_rank(filtered_rank)
        word_id = f"es_{filtered_rank:05d}"

        if not validate_word_id(topic, word_id):
            print(f"  SKIP (callback overflow): rank={filtered_rank}")
            continue

        topic_rows[topic].append((topic, word_id, word, None, filtered_rank))

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
    print("Note: hint=NULL for all Spanish entries. Add English glosses via a migration")
    print("once a suitable open-licensed Spanish-English word list is sourced.")


if __name__ == "__main__":
    main()
