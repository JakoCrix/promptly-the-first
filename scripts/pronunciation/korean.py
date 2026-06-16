"""
Generate and store Revised Romanization for the Korean TOPIK corpus.

For each word in korean_topik_* topics with no pronunciation set:
  - Calls Gemini in batches to get Revised Romanization of Hangul
  - UPDATEs corpus.pronunciation in the DB
  - Appends to CSV backup at data/pronunciations/korean.csv

Resumable: words that already have a pronunciation value are skipped.

Usage:
    python scripts/pronunciation/korean.py
    python scripts/pronunciation/korean.py --topic korean_topik_1
    python scripts/pronunciation/korean.py --batch 10 --delay 0.3
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

OUTPUT_CSV = os.path.join(PROJECT_ROOT, "data", "pronunciations", "korean.csv")
CSV_FIELDNAMES = ["topic", "word_id", "word", "meaning", "romanization"]

genai.configure(api_key=GOOGLE_API_KEY)
_MODEL = genai.GenerativeModel(GEMINI_MODEL)


def _fetch_words(topic_filter: str | None) -> list[dict]:
    conn = _get_conn()
    try:
        if topic_filter:
            rows = conn.execute(
                "SELECT topic, word_id, word, definition FROM corpus "
                "WHERE topic = ? AND pronunciation IS NULL ORDER BY frequency_rank",
                (topic_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT topic, word_id, word, definition FROM corpus "
                "WHERE topic LIKE 'korean_topik_%' AND pronunciation IS NULL "
                "ORDER BY topic, frequency_rank",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_romanization_batch(words: list[dict]) -> list[str | None]:
    """Call Gemini once for a batch of words. Returns a list of romanizations (or None on failure)."""
    lines = []
    for i, w in enumerate(words, 1):
        meaning = w.get("definition") or ""
        meaning_part = f" (meaning: {meaning})" if meaning else ""
        lines.append(f"{i}. {w['word']}{meaning_part}")

    prompt = (
        f"Give the Revised Romanization of Korean (국어의 로마자 표기법) for each of these "
        f"{len(words)} words, in the same numbered order:\n"
        + "\n".join(lines)
        + "\n\nReturn only the romanization, numbered 1–"
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


def _store_one(topic: str, word_id: str, romanization: str, word: str, meaning: str, csv_writer, csv_file) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE corpus SET pronunciation = ? WHERE topic = ? AND word_id = ?",
            (romanization, topic, word_id),
        )
        conn.commit()
    finally:
        conn.close()
    csv_writer.writerow({"topic": topic, "word_id": word_id, "word": word, "meaning": meaning, "romanization": romanization})
    csv_file.flush()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Generate Revised Romanization for Korean TOPIK corpus")
    parser.add_argument("--topic", default=None, help="Limit to one topic (e.g. korean_topik_1)")
    parser.add_argument("--batch", type=int, default=10, help="Words per API call (default: 10)")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between API calls (default: 0.3)")
    args = parser.parse_args()

    if args.topic and not args.topic.startswith("korean_topik_"):
        print(f"ERROR: --topic must be a korean_topik_* topic, got '{args.topic}'")
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

            results = _get_romanization_batch(batch)
            batch_ok = 0
            for word, romanization in zip(batch, results):
                if romanization:
                    meaning = word.get("definition") or ""
                    _store_one(word["topic"], word["word_id"], romanization, word["word"], meaning, writer, f)
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
