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
        ("ni_hao",   "你好",  "你好！很高兴认识你。(Hello! Nice to meet you.)"),
        ("xie_xie",  "谢谢",  "谢谢你帮助我。(Thank you for helping me.)"),
        ("shui",     "水",    "我想喝一杯水。(I want to drink a glass of water.)"),
        ("shu",      "书",    "我正在读一本有趣的书。(I am reading an interesting book.)"),
        ("mao",      "猫",    "那只猫在窗台上睡觉。(That cat is sleeping on the windowsill.)"),
        ("gou",      "狗",    "那只狗跑得很快。(That dog runs very fast.)"),
        ("chi",      "吃",    "我每天早上吃早饭。(I eat breakfast every morning.)"),
        ("da",       "大",    "大象是世界上最大的陆地动物之一。(Elephants are among the largest land animals.)"),
        ("xiao",     "小",    "这只小猫非常可爱。(This little cat is very cute.)"),
        ("peng_you", "朋友",  "他是我最好的朋友。(He is my best friend.)"),
    ],
    "zoo_animals": [
        ("tapir",     "tapir",     "The tapir used its short, flexible snout to pluck leaves from low-hanging branches near the river's edge."),
        ("okapi",     "okapi",     "Resembling a horse with zebra-striped legs, the okapi is the giraffe's only living relative and was unknown to science until 1901."),
        ("binturong", "binturong", "The binturong hung by its prehensile tail in the canopy, filling the air around it with an inexplicable scent of warm popcorn."),
        ("pangolin",  "pangolin",  "When the pangolin sensed danger, it curled into a tight ball, its overlapping keratin scales forming a near-impenetrable armour."),
        ("mandrill",  "mandrill",  "The mandrill's vivid red and blue face markings grow brighter with age, advertising its dominance to rivals."),
        ("aardvark",  "aardvark",  "The aardvark uses its long, sticky tongue to consume tens of thousands of termites in a single night."),
        ("capybara",  "capybara",  "Despite being the world's largest rodent, the capybara is remarkably docile and is often seen grooming birds perched on its back."),
        ("axolotl",   "axolotl",   "The axolotl never undergoes metamorphosis, retaining its feathery external gills and larval features throughout its entire life."),
        ("quokka",    "quokka",    "The quokka's perpetual upturned mouth has earned it the reputation of being the world's happiest animal."),
        ("cassowary", "cassowary", "Despite its vivid blue neck and red wattles, the cassowary is considered one of the most dangerous birds alive, capable of delivering a lethal kick."),
    ],
    "vegetables": [
        ("artichoke", "artichoke", "The artichoke's edible heart is hidden beneath dozens of tough, spiky leaves."),
        ("asparagus", "asparagus", "Fresh asparagus spears snap cleanly at their natural breaking point when bent."),
        ("beetroot",  "beetroot",  "The deep purple beetroot bled its vivid colour into everything else on the plate."),
        ("courgette", "courgette", "She sliced the courgette into thin ribbons and tossed them with olive oil and lemon."),
        ("fennel",    "fennel",    "The fennel bulb has a mild anise flavour that mellows considerably when roasted."),
        ("kohlrabi",  "kohlrabi",  "Kohlrabi looks like a spaceship landed in the vegetable patch, with leaves shooting in all directions."),
        ("leek",      "leek",      "A classic vichyssoise is made from leeks, potatoes, cream, and patience."),
        ("parsnip",   "parsnip",   "Roasted parsnips caramelise at the edges and develop a sweet, nutty flavour."),
        ("celeriac",  "celeriac",  "Beneath its gnarled exterior, celeriac has a mild celery flavour and creamy texture when cooked."),
        ("radicchio", "radicchio", "The bitter radicchio wilted into silky ribbons when tossed through the warm pasta."),
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
                "(chat_id, topic, word_id, word, sentence, mastery_score, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, topic, word["id"], word["word"], word["sentence"],
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
            for word_id, word, sentence in words:
                conn.execute(
                    "INSERT OR IGNORE INTO words "
                    "(chat_id, topic, word_id, word, sentence, mastery_score, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, 0, NULL)",
                    (chat_id, topic, word_id, word, sentence),
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
