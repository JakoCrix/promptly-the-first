"""
Generate and store example sentences for the English NGSL corpus.

For each word in english_* topics:
  - Generates N sentences via Gemini (one API call per word)
  - Writes immediately to the word_sentences DB table
  - Appends to CSV as a backup (data/word_sentences/english.csv)

Resumable: words already having >= count sentences in the DB are skipped.

Usage:
    python scripts/sentences/english.py
    python scripts/sentences/english.py --topic english_ngsl
    python scripts/sentences/english.py --count 10 --delay 0.5
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

OUTPUT_CSV = os.path.join(PROJECT_ROOT, "data", "word_sentences", "english.csv")
CSV_FIELDNAMES = ["topic", "word_id", "word", "hint", "sentence"]

genai.configure(api_key=GOOGLE_API_KEY)
_MODEL = genai.GenerativeModel(GEMINI_MODEL)


def _load_done(min_count: int) -> set[tuple[str, str]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT topic, word_id FROM word_sentences "
            "GROUP BY topic, word_id HAVING COUNT(*) >= ?",
            (min_count,),
        ).fetchall()
        return {(r["topic"], r["word_id"]) for r in rows}
    finally:
        conn.close()


def _fetch_words(topic_filter: str | None) -> list[dict]:
    conn = _get_conn()
    try:
        if topic_filter:
            rows = conn.execute(
                "SELECT topic, word_id, word, hint FROM corpus "
                "WHERE topic = ? ORDER BY frequency_rank",
                (topic_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT topic, word_id, word, hint FROM corpus "
                "WHERE topic LIKE 'english_%' ORDER BY topic, frequency_rank",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _generate_sentences(word: str, hint: str | None, count: int) -> list[str]:
    prompt = (
        f'Generate exactly {count} natural example sentences using the English word "{word}".\n'
        f"Each sentence should be distinct and useful for an English language learner.\n"
        f"Number them 1–{count}, one per line. Return only the sentences — no extra labels, no quotes, no blank lines."
    )
    try:
        response = _MODEL.generate_content(prompt)
        text = response.text.strip()
    except Exception as exc:
        print(f"  ERROR calling Gemini: {exc}")
        return []

    sentences = []
    for line in text.splitlines():
        line = re.sub(r"^\d+[\.\)]\s*", "", line.strip()).strip()
        if line:
            sentences.append(line)
    return sentences[:count]


def _store(word: dict, sentences: list[str], csv_writer, csv_file) -> None:
    conn = _get_conn()
    try:
        for sentence in sentences:
            conn.execute(
                "INSERT OR IGNORE INTO word_sentences (topic, word_id, sentence) VALUES (?, ?, ?)",
                (word["topic"], word["word_id"], sentence),
            )
            csv_writer.writerow({
                "topic":    word["topic"],
                "word_id":  word["word_id"],
                "word":     word["word"],
                "hint":     word.get("hint") or "",
                "sentence": sentence,
            })
        conn.commit()
    finally:
        conn.close()
    csv_file.flush()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Generate sentences for the English NGSL corpus")
    parser.add_argument("--topic", default=None, help="Limit to one topic (e.g. english_ngsl)")
    parser.add_argument("--count", type=int, default=10, help="Sentences per word (default: 10)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls (default: 0.5)")
    args = parser.parse_args()

    init_db()
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    done = _load_done(args.count)
    words = _fetch_words(args.topic)
    to_process = [w for w in words if (w["topic"], w["word_id"]) not in done]

    print(f"Words total: {len(words)} | Already done: {len(done)} | To process: {len(to_process)}")

    if not to_process:
        print("Nothing to do.")
        return

    csv_exists = os.path.exists(OUTPUT_CSV)
    errors = 0

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not csv_exists:
            writer.writeheader()

        for i, word in enumerate(to_process, 1):
            print(f"[{i}/{len(to_process)}] {word['topic']} / {word['word_id']} ({word['word']}) ...", end=" ", flush=True)
            sentences = _generate_sentences(word["word"], word.get("hint"), args.count)
            if not sentences:
                print("SKIPPED (no output)")
                errors += 1
            else:
                _store(word, sentences, writer, f)
                print(f"OK ({len(sentences)} sentences)")

            if args.delay > 0 and i < len(to_process):
                time.sleep(args.delay)

    print(f"\nDone. Errors: {errors}. Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
