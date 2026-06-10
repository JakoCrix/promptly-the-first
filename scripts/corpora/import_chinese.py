"""
Import HSK 3.0 vocabulary (9 levels, ~11,092 words) into the corpus.

This script replaces any existing chinese_hsk* data. Run without --confirm first
to see a summary of what would change; add --confirm to execute the replacement.

Usage:
    python scripts/corpora/import_chinese.py            # dry-run
    python scripts/corpora/import_chinese.py --confirm  # delete old + import
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vocab import _get_conn, init_db, slugify
from scripts.corpora._base import bulk_insert, validate_word_id

_BASE_URL = (
    "https://raw.githubusercontent.com/drkameleon/complete-hsk-vocabulary"
    "/main/wordlists/exclusive/new/{level}.json"
)
_LEVELS = range(1, 8)  # HSK 3.0: source has 7 files; file 7 covers levels 7–9 combined

_DESCRIPTIONS = {
    **{f"chinese_hsk_{i}": f"Mandarin Chinese — HSK 3.0 Level {i}." for i in range(1, 7)},
    "chinese_hsk_7": "Mandarin Chinese — HSK 3.0 Levels 7–9 (advanced, combined in source dataset).",
}


def _fetch_level(level: int) -> list[dict]:
    url = _BASE_URL.format(level=level)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _build_row(entry: dict, topic: str, rank: int) -> tuple | None:
    try:
        word = entry["simplified"]
        forms = entry.get("forms", [])
        f0 = forms[0] if forms else {}
        transcriptions = f0.get("transcriptions", {})
        pinyin_numeric = transcriptions.get("numeric", "")
        pinyin_toned = transcriptions.get("toned", "")
        meanings = f0.get("meanings", [])
        gloss = meanings[0] if meanings else ""
    except (KeyError, IndexError, TypeError):
        return None

    word_id = slugify(pinyin_numeric)
    if not word_id:
        return None
    if not validate_word_id(topic, word_id):
        print(f"  SKIP (callback overflow): {word!r}  word_id={word_id!r}")
        return None

    hint = None
    if pinyin_toned and gloss:
        hint = f"{pinyin_toned} — {gloss}"
    elif pinyin_toned:
        hint = pinyin_toned
    elif gloss:
        hint = gloss

    return (topic, word_id, word, hint, rank)


def _purge_old_data(conn) -> tuple[int, int]:
    cur = conn.execute("DELETE FROM user_progress WHERE topic LIKE 'chinese_hsk%'")
    progress_deleted = cur.rowcount
    cur = conn.execute("DELETE FROM corpus WHERE topic LIKE 'chinese_hsk%'")
    corpus_deleted = cur.rowcount
    conn.execute("DELETE FROM topics WHERE topic LIKE 'chinese_hsk%'")
    # Migrate user_settings from old naming (chinese_hsk1) to new (chinese_hsk_1).
    for i in range(1, 8):
        conn.execute(
            "UPDATE user_settings SET active_topic = ? WHERE active_topic = ?",
            (f"chinese_hsk_{i}", f"chinese_hsk{i}"),
        )
    conn.commit()
    return corpus_deleted, progress_deleted


def main() -> None:
    confirm = "--confirm" in sys.argv
    init_db()
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM corpus WHERE topic LIKE 'chinese_hsk%'"
        ).fetchone()[0]

        if existing and not confirm:
            print(f"Found {existing} existing chinese_hsk* corpus rows.")
            print("Run with --confirm to delete old data and re-import HSK 3.0.")
            print("WARNING: this will also clear user_progress for those topics.")
            return

        if existing and confirm:
            corpus_del, progress_del = _purge_old_data(conn)
            print(
                f"Purged {corpus_del} corpus rows and {progress_del} user_progress rows "
                f"for chinese_hsk* topics."
            )

        total_inserted = total_skipped = 0
        for level in _LEVELS:
            topic = f"chinese_hsk_{level}"
            print(f"  Level {level}: fetching...", end=" ", flush=True)
            try:
                entries = _fetch_level(level)
            except Exception as exc:
                print(f"FAILED ({exc})")
                continue

            rows = []
            seen_ids: set[str] = set()
            for rank, entry in enumerate(entries, start=1):
                row = _build_row(entry, topic, rank)
                if row is None:
                    continue
                word_id = row[1]
                if word_id in seen_ids:
                    continue
                seen_ids.add(word_id)
                rows.append(row)

            inserted, skipped = bulk_insert(conn, rows, topic, _DESCRIPTIONS[topic])
            total_inserted += inserted
            total_skipped += skipped
            print(f"{inserted} inserted, {skipped} skipped")

    finally:
        conn.close()

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped.")


if __name__ == "__main__":
    main()
