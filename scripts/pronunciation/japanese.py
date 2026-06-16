"""
Generate and store romaji pronunciation for the Japanese JLPT corpus.

For each word in japanese_jlpt_* topics with no pronunciation set:
  - Extracts the kana reading from the existing definition field
  - Calls Gemini in batches to get Hepburn romaji transliterations
  - UPDATEs corpus.pronunciation in the DB
  - Appends to CSV backup at data/pronunciations/japanese.csv

Resumable: words that already have a pronunciation value are skipped.

Usage:
    python scripts/pronunciation/japanese.py
    python scripts/pronunciation/japanese.py --topic japanese_jlpt_n5
    python scripts/pronunciation/japanese.py --batch 10 --delay 0.3
"""

import argparse
import csv
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, PROJECT_ROOT)

import google.generativeai as genai

from core.config import GEMINI_MODEL, GOOGLE_API_KEY
from core.vocab import _get_conn, init_db

OUTPUT_CSV = os.path.join(PROJECT_ROOT, "data", "pronunciations", "japanese.csv")
CSV_FIELDNAMES = ["topic", "word_id", "word", "kana", "romaji"]

genai.configure(api_key=GOOGLE_API_KEY)
_MODEL = genai.GenerativeModel(GEMINI_MODEL)


def _fetch_words(topic_filter: str | None) -> list[dict]:
    """Fetch words that have kana in pronunciation but no romaji yet (no comma)."""
    conn = _get_conn()
    try:
        if topic_filter:
            rows = conn.execute(
                "SELECT topic, word_id, word, definition, pronunciation FROM corpus "
                "WHERE topic = ? AND (pronunciation IS NULL OR pronunciation NOT LIKE '%,%') "
                "ORDER BY frequency_rank",
                (topic_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT topic, word_id, word, definition, pronunciation FROM corpus "
                "WHERE topic LIKE 'japanese_jlpt_%' "
                "  AND (pronunciation IS NULL OR pronunciation NOT LIKE '%,%') "
                "ORDER BY topic, frequency_rank",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _kana_from_word(word: dict) -> str:
    """Get kana from pronunciation column (post-migration) or definition (pre-migration fallback)."""
    pron = word.get("pronunciation") or ""
    if pron and "," not in pron:
        return pron  # pronunciation holds kana only (post-migration, no romaji yet)
    definition = word.get("definition") or ""
    return definition.split(" — ")[0].strip() if " — " in definition else ""


def _get_romaji_batch(words: list[dict]) -> list[str | None]:
    """Call Gemini once for a batch of words. Returns a list of romaji strings (or None on failure)."""
    lines = []
    for i, w in enumerate(words, 1):
        kana = _kana_from_word(w)
        kana_part = f" (kana: {kana})" if kana else ""
        lines.append(f"{i}. {w['word']}{kana_part}")

    prompt = (
        f"Give the Hepburn romaji romanisation for each of these {len(words)} Japanese words, "
        f"in the same numbered order:\n"
        + "\n".join(lines)
        + "\n\nReturn only the romaji, numbered 1–"
        + str(len(words))
        + ", one per line. No explanations, no extra text."
    )
    try:
        response = _MODEL.generate_content(prompt)
        text = response.text.strip()
    except Exception as exc:
        print(f"  ERROR calling Gemini: {exc}")
        return [None] * len(words)

    results = [None] * len(words)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)[.\)]\s*(.+)$", line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(words):
                results[idx] = m.group(2).strip()
    return results


def _store_one(topic: str, word_id: str, romaji: str, word: str, kana: str, csv_writer, csv_file) -> None:
    pronunciation = f"{kana}, {romaji}" if kana else romaji
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE corpus SET pronunciation = ? WHERE topic = ? AND word_id = ?",
            (pronunciation, topic, word_id),
        )
        conn.commit()
    finally:
        conn.close()
    csv_writer.writerow({"topic": topic, "word_id": word_id, "word": word, "kana": kana, "romaji": romaji})
    csv_file.flush()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Generate romaji pronunciation for Japanese JLPT corpus")
    parser.add_argument("--topic", default=None, help="Limit to one topic (e.g. japanese_jlpt_n5)")
    parser.add_argument("--batch", type=int, default=10, help="Words per API call (default: 10)")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between API calls (default: 0.3)")
    args = parser.parse_args()

    if args.topic and not args.topic.startswith("japanese_jlpt_"):
        print(f"ERROR: --topic must be a japanese_jlpt_* topic, got '{args.topic}'")
        sys.exit(1)

    init_db()
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    words = _fetch_words(args.topic)
    total = len(words)
    print(f"Words to process: {total}")

    if not total:
        print("Nothing to do.")
        return

    csv_exists = os.path.exists(OUTPUT_CSV)
    errors = 0
    done = 0
    batch_num = 0

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not csv_exists:
            writer.writeheader()

        for i in range(0, total, args.batch):
            batch = words[i: i + args.batch]
            batch_num += 1
            print(f"[batch {batch_num}] words {i+1}–{min(i+len(batch), total)} of {total} ...", end=" ", flush=True)

            results = _get_romaji_batch(batch)
            batch_ok = 0
            for word, romaji in zip(batch, results):
                if romaji:
                    kana = _kana_from_word(word)
                    _store_one(word["topic"], word["word_id"], romaji, word["word"], kana, writer, f)
                    batch_ok += 1
                    done += 1
                else:
                    errors += 1
            print(f"OK {batch_ok}/{len(batch)}" if batch_ok == len(batch) else f"{batch_ok}/{len(batch)} stored, {len(batch)-batch_ok} skipped")

            if args.delay > 0 and i + args.batch < total:
                time.sleep(args.delay)

    print(f"\nDone. Stored: {done}, Skipped (retry next run): {errors}. Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
