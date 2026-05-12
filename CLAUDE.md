# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Activate the virtual environment first (Windows)
.venv\Scripts\activate

# Run the bot
python bot.py
```

## Environment setup

Create a `.env` file at the project root:

```
BOT_TOKEN=<telegram-bot-token>
CHAT_IDS=<comma-separated-chat-ids>
```

`VOCAB_TOPIC` is a legacy env var — it is only used by the migration script to glob old JSON files and can be omitted for new setups.

## Manual test scripts

```bash
python scripts/test_connection.py          # verify token + chat IDs, sends a test message
python scripts/test_card.py                # force-send a random card and wait for button press
python scripts/test_card.py <word_id>      # force-send a specific word (e.g. "pangolin", "ni_hao")
python scripts/migrate_to_sqlite.py        # one-shot: import JSON vocab + seed all built-in topics
python scheduler.py                        # print today's generated slots (quick sanity check)
```

## Architecture

No automated tests. All verification is manual via the scripts above.

**Entry point:** `bot.py` — builds the PTB `Application`, registers handlers, calls `init_db()` then `wire_scheduler()` in `post_init`, then runs polling.

**Module responsibilities:**

| Module | Role |
|---|---|
| `config.py` | Loads `.env`, exposes all tuneable constants (`DAILY_SLOTS`, `WINDOW_START/END_HOUR`, `RETIREMENT_THRESHOLD`, `NEVER_SEEN_SECONDS`) |
| `vocab.py` | All SQLite I/O for vocab data; owns `_get_conn()`, `init_db()`, and the word selection algorithm |
| `persistence.py` | Schedule I/O only — reads/writes the `schedule` table; imports `_get_conn` from `vocab` |
| `scheduler.py` | Generates daily slot timestamps, registers PTB `job_queue` jobs, schedules midnight regeneration |

**Database:** `data/promptly.db` — a single SQLite file for all data.

**Schema summary:**

| Table | Purpose | Key columns |
|---|---|---|
| `words` | Vocab per user per topic | PK: `(chat_id, topic, word_id)` |
| `history` | Every Known/Forgot event | FK → words; `UNIQUE (chat_id, topic, word_id, timestamp, result)` |
| `user_settings` | Each user's active topic | PK: `chat_id` |
| `schedule` | Today's notification slots | PK: `(date, slot_time)`; `fired` flag |

**Word selection (`vocab.pick_word`):** Weighted random — `weight = (1 / (mastery_score + 1)) * elapsed_seconds`. Unseen words get `NEVER_SEEN_SECONDS` (7 days) so they always surface early. Words with `mastery_score >= RETIREMENT_THRESHOLD` (10) are excluded. Topic is resolved from `user_settings`; if no row exists, falls back to the first topic alphabetically.

**Callback data format:** `known:{chat_id}:{topic}:{word_id}` — topic and word_id are embedded so `feedback_handler` can route to the correct DB row without a session lookup. Word IDs are always ASCII (pinyin for Chinese, English for other topics) to stay within Telegram's 64-byte callback_data limit.

**Scheduling:** On startup, `wire_scheduler` loads today's persisted schedule from the DB (or generates a fresh one). It registers a PTB `run_once` job for each unfired future slot, and a `run_daily` job at midnight Melbourne time to regenerate. All times are Melbourne-aware (`ZoneInfo("Australia/Melbourne")`).

**Multi-topic:** Each user independently tracks mastery per topic. Switch via `/topic <name>` in Telegram (stored in `user_settings`). Built-in seed topics are `zoo_animals`, `chinese`, and `vegetables` — defined in `scripts/migrate_to_sqlite.py:SEED_DATA` and seeded for all `ALLOWED_CHAT_IDS` when the migration script is run.

## Adding a new topic

Add an entry to `SEED_DATA` in `scripts/migrate_to_sqlite.py` and re-run the script. `INSERT OR IGNORE` means existing data is never overwritten. Word IDs must be ASCII.

## Constraints

- Telegram-only UI. No web dashboard, no external DB, no images or audio.
- `ALLOWED_CHAT_IDS` in `.env` is the allowlist — a handful of users max.
- stdlib `sqlite3` only — no ORM, no new packages. Per-call `sqlite3.connect()` is intentional.
- `persistence.py` imports `_get_conn` from `vocab.py` (private function, cross-module) — known design debt, acceptable at this scale.
