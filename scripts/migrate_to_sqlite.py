"""
One-shot migration: import all data/*_{chat_id}.json vocab files into
data/promptly.db, seed built-in topic dictionaries for all users,
and set each user's active topic.

Usage:
    python scripts/migrate_to_sqlite.py
"""
import glob
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import ALLOWED_CHAT_IDS, DATA_DIR, DB_PATH
from core.vocab import init_db, set_active_topic

# Built-in seed dictionaries — seeded for every user in ALLOWED_CHAT_IDS.
# Word IDs must be ASCII (they appear in Telegram callback_data).
SEED_DATA = {
    "chinese": [
        ("ni_hao",   "你好"),
        ("xie_xie",  "谢谢"),
        ("shui",     "水"),
        ("shu",      "书"),
        ("mao",      "猫"),
        ("gou",      "狗"),
        ("chi",      "吃"),
        ("da",       "大"),
        ("xiao",     "小"),
        ("peng_you", "朋友"),
    ],
    "zoo_animals": [
        ("tapir",     "tapir"),
        ("okapi",     "okapi"),
        ("binturong", "binturong"),
        ("pangolin",  "pangolin"),
        ("mandrill",  "mandrill"),
        ("aardvark",  "aardvark"),
        ("capybara",  "capybara"),
        ("axolotl",   "axolotl"),
        ("quokka",    "quokka"),
        ("cassowary", "cassowary"),
    ],
    "vegetables": [
        ("artichoke", "artichoke"),
        ("asparagus", "asparagus"),
        ("beetroot",  "beetroot"),
        ("courgette", "courgette"),
        ("fennel",    "fennel"),
        ("kohlrabi",  "kohlrabi"),
        ("leek",      "leek"),
        ("parsnip",   "parsnip"),
        ("celeriac",  "celeriac"),
        ("radicchio", "radicchio"),
    ],
}


def migrate_vocab_files(conn: sqlite3.Connection) -> tuple[list[str], dict[int, str]]:
    """Import all data/*_{chat_id}.json files. Returns (migrated_paths, {chat_id: first_topic})."""
    # Glob all JSON files — the regex below filters to {topic}_{chat_id}.json only.
    all_json = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))

    migrated_paths = []
    first_topic_per_user: dict[int, str] = {}
    total_words = 0
    total_history = 0

    for path in all_json:
        filename = os.path.basename(path)
        match = re.search(r"^(.+)_(\d+)\.json$", filename)
        if not match:
            continue  # skip schedule.json and any other non-vocab files

        topic = match.group(1)
        chat_id = int(match.group(2))

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        words = data.get("words", [])
        n_words = 0
        n_history = 0

        for word in words:
            conn.execute(
                "INSERT OR REPLACE INTO words "
                "(chat_id, topic, word_id, word, mastery_score, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, topic, word["id"], word["word"],
                 word["mastery_score"], word["last_seen"]),
            )
            n_words += 1

            for entry in word.get("history", []):
                conn.execute(
                    "INSERT OR IGNORE INTO history "
                    "(chat_id, topic, word_id, timestamp, result) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, topic, word["id"], entry["timestamp"], entry["result"]),
                )
                n_history += 1

        conn.commit()
        print(f"  Migrated {filename}: {n_words} words, {n_history} history rows (topic: {topic})")
        total_words += n_words
        total_history += n_history
        migrated_paths.append(path)

        if chat_id not in first_topic_per_user:
            first_topic_per_user[chat_id] = topic

    if migrated_paths:
        print(f"  Total: {total_words} words, {total_history} history rows from {len(migrated_paths)} file(s)")
    else:
        print("  No vocab JSON files found in data/ — nothing to migrate from JSON.")

    return migrated_paths, first_topic_per_user


TOPIC_DESCRIPTIONS = {
    "zoo_animals": "Common animals found in a zoo, suitable for intermediate English vocabulary.",
    "chinese":     "Everyday Mandarin Chinese words and phrases, written in pinyin with English translations.",
    "vegetables":  "Common vegetables, suitable for beginner English vocabulary.",
}


def seed_topics(conn: sqlite3.Connection) -> None:
    """Seed built-in dictionaries for every user in ALLOWED_CHAT_IDS."""
    if not ALLOWED_CHAT_IDS:
        print("  No ALLOWED_CHAT_IDS configured — skipping seed")
        return

    for topic, words in SEED_DATA.items():
        for chat_id in ALLOWED_CHAT_IDS:
            for word_id, word in words:
                conn.execute(
                    "INSERT OR IGNORE INTO words "
                    "(chat_id, topic, word_id, word, mastery_score, last_seen) "
                    "VALUES (?, ?, ?, ?, 0, NULL)",
                    (chat_id, topic, word_id, word),
                )
        conn.commit()
        print(f"  Seeded '{topic}': {len(words)} words for {len(ALLOWED_CHAT_IDS)} user(s)")

    for topic, description in TOPIC_DESCRIPTIONS.items():
        conn.execute(
            "INSERT OR IGNORE INTO topics (topic, description) VALUES (?, ?)",
            (topic, description),
        )
    conn.commit()
    print(f"  Seeded descriptions for {len(TOPIC_DESCRIPTIONS)} topic(s)")


def set_active_topics(first_topic_per_user: dict[int, str]) -> None:
    for chat_id, topic in first_topic_per_user.items():
        set_active_topic(chat_id, topic)
        print(f"  Set active topic for chat {chat_id} → {topic}")


def main() -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    try:
        print("=== Migrating vocab JSON files ===")
        migrated_paths, first_topic_per_user = migrate_vocab_files(conn)

        print("\n=== Seeding built-in dictionaries ===")
        seed_topics(conn)

        if first_topic_per_user:
            print("\n=== Setting active topics ===")
            set_active_topics(first_topic_per_user)
    finally:
        conn.close()

    print(f"\nDatabase: {DB_PATH}")

    schedule_json = os.path.join(DATA_DIR, "schedule.json")
    files_to_delete = migrated_paths[:]
    if os.path.exists(schedule_json):
        files_to_delete.append(schedule_json)

    if not files_to_delete:
        print("No files to delete.")
        return

    print("\nFiles to delete:")
    for f in files_to_delete:
        print(f"  {os.path.basename(f)}")

    answer = input("\nDelete these files? [y/N]: ").strip().lower()
    if answer == "y":
        for f in files_to_delete:
            os.remove(f)
            print(f"  Deleted {os.path.basename(f)}")
    else:
        print("Files kept.")


if __name__ == "__main__":
    main()
