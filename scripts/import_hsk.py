"""
One-shot import: download HSK 3.0 vocabulary (levels 1-7) from GitHub and
insert into the corpus table in data/promptly.db.

Usage:
    python scripts/import_hsk.py

Safe to re-run: all inserts use INSERT OR IGNORE, so existing rows are
silently skipped and the counts reflect only new insertions.
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import DB_PATH
from core.vocab import slugify, init_db

LEVELS = list(range(1, 8))

_BASE_URL = (
    "https://raw.githubusercontent.com/drkameleon/complete-hsk-vocabulary"
    "/main/wordlists/exclusive/new/{}.json"
)

_DESCRIPTIONS = {
    1: "HSK 3.0 Level 1 — foundational Mandarin Chinese (~500 words)",
    2: "HSK 3.0 Level 2 — elementary Mandarin Chinese (~772 words)",
    3: "HSK 3.0 Level 3 — lower-intermediate Mandarin Chinese (~973 words)",
    4: "HSK 3.0 Level 4 — intermediate Mandarin Chinese (~1000 words)",
    5: "HSK 3.0 Level 5 — upper-intermediate Mandarin Chinese (~1071 words)",
    6: "HSK 3.0 Level 6 — advanced Mandarin Chinese (~1140 words)",
    7: "HSK 3.0 Level 7 — mastery-level Mandarin Chinese (5000+ words)",
}

# Maximum bytes available for word_id within the 64-byte Telegram callback_data
# budget. Format: "known:{chat_id}:{topic}:{word_id}" — 3 separating colons.
# chat_id ≤ 10 digits, longest topic = "chinese_hsk7" (12 chars).
_WORD_ID_MAX = 64 - len("known:") - 10 - len("chinese_hsk7") - 3  # = 33


def _fetch_level(level: int) -> list[dict]:
    url = _BASE_URL.format(level)
    print(f"  Downloading {url} …", end=" ", flush=True)
    with urllib.request.urlopen(url) as resp:
        data = json.load(resp)
    print(f"{len(data)} entries")
    return data


def _import_level(conn: sqlite3.Connection, level: int) -> None:
    topic = f"chinese_hsk{level}"
    entries = _fetch_level(level)

    inserted = 0
    skipped = 0
    warned = 0

    for entry in entries:
        simplified = entry.get("simplified", "").strip()
        forms = entry.get("forms", [])

        if not simplified or not forms:
            warned += 1
            continue

        form = forms[0]
        transcriptions = form.get("transcriptions", {})
        meanings = form.get("meanings", [])

        numeric_pinyin = transcriptions.get("numeric", "").strip()
        tone_pinyin = transcriptions.get("pinyin", "").strip()
        first_meaning = meanings[0].strip() if meanings else ""

        if not numeric_pinyin:
            warned += 1
            continue

        word_id = slugify(numeric_pinyin)

        if len(word_id.encode()) > _WORD_ID_MAX:
            print(f"    WARNING: word_id '{word_id}' exceeds {_WORD_ID_MAX} bytes — skipping")
            warned += 1
            continue

        hint = f"{tone_pinyin} — {first_meaning}" if tone_pinyin and first_meaning else None

        cur = conn.execute(
            "INSERT OR IGNORE INTO corpus (topic, word_id, word, hint) VALUES (?, ?, ?, ?)",
            (topic, word_id, simplified, hint),
        )
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    conn.execute(
        "INSERT OR IGNORE INTO topics (topic, description) VALUES (?, ?)",
        (topic, _DESCRIPTIONS[level]),
    )
    conn.commit()

    parts = [f"Level {level}: {inserted} inserted, {skipped} skipped"]
    if warned:
        parts.append(f"{warned} malformed entries skipped")
    print("  " + ", ".join(parts))


def main() -> None:
    print(f"Importing HSK 3.0 levels {LEVELS[0]}–{LEVELS[-1]} into corpus…\n")

    init_db()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    try:
        for level in LEVELS:
            _import_level(conn, level)
    finally:
        conn.close()

    print(f"\nDone. Database: {DB_PATH}")
    print("Switch to an HSK topic in Telegram with: /topic chinese_hsk1")


if __name__ == "__main__":
    main()
