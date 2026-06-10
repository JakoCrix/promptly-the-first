# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Activate the virtual environment (Windows)
.venv\Scripts\activate

# Run the bot
python bot.py

# Run the Streamlit dashboard (separate process)
streamlit run dashboard/app.py
```

## Environment setup

Create a `.env` file at the project root:

```
BOT_TOKEN=<telegram-bot-token>
CHAT_IDS=<comma-separated-chat-ids>
GOOGLE_API_KEY=<google-gemini-api-key>
```

`VOCAB_TOPIC` is a legacy env var used only by the migration script to glob old JSON files — omit for new setups.

## Manual test scripts

```bash
python scripts/test_connection.py          # verify token + chat IDs, sends a test message
python scripts/test_card.py                # force-send a random card and wait for button press
python scripts/test_card.py <word_id>      # force-send a specific word (e.g. "pangolin", "ni_hao")
python scripts/test_suggest.py             # test Gemini suggestions + send first card via Telegram
python scripts/test_suggest.py <topic>     # same but override the topic (e.g. "zoo_animals")
python scripts/migrate_to_sqlite.py        # one-shot: import JSON vocab + seed all built-in topics
python scripts/import_hsk.py              # download and import HSK 3.0 levels 1–7 into the corpus
python -m core.scheduler                   # print today's generated slots (quick sanity check)
```

## Architecture

No automated tests. All verification is manual via the scripts above.

**Entry point:** `bot.py` — builds the PTB `Application`, registers handlers, calls `init_db()` then `wire_scheduler()` in `post_init`, then runs polling.

**Module responsibilities:**

| Module | Role |
|---|---|
| `core/config.py` | Loads `.env`, exposes all tuneable constants (`DAILY_SLOTS`, `WINDOW_START/END_HOUR`, `RETIREMENT_THRESHOLD`, `NEVER_SEEN_SECONDS`) |
| `core/vocab.py` | All SQLite I/O for vocab data; owns `_get_conn()`, `init_db()`, and the word selection algorithm |
| `core/persistence.py` | Schedule I/O only — reads/writes the `schedule` table; imports `_get_conn` from `core/vocab` |
| `core/scheduler.py` | Generates daily slot timestamps, registers PTB `job_queue` jobs, schedules midnight regeneration |
| `core/suggest.py` | Calls Google Gemini (gemini-2.5-flash) to generate word suggestions for a given topic |
| `dashboard/app.py` | Streamlit UI — two tabs: Vocabulary Navigator (browse/edit/delete words, bulk mastery adjustments) and Word Discovery Pool (review and approve candidate words) |
| `dashboard/db.py` | Dashboard-only DB helpers; queries `corpus` + `user_progress`; exposes `add_to_decks()`, bulk mastery/delete operations, and `update_word_corpus()` (edits word text and hint) |

**Database:** `data/promptly.db` — a single SQLite file, gitignored (runtime data).

**Schema summary:**

| Table | Purpose | Key columns |
|---|---|---|
| `corpus` | Shared word definitions, one row per word | PK: `(topic, word_id)`; columns: `word`, `hint` |
| `user_progress` | Per-user mastery tracking | PK: `(chat_id, topic, word_id)`; FK → corpus; columns: `mastery_score`, `last_seen` |
| `history` | Every Known/Forgot event | FK → user_progress; `UNIQUE (chat_id, topic, word_id, timestamp, result)` |
| `user_settings` | Each user's active topic | PK: `chat_id` |
| `schedule` | Today's notification slots | PK: `(date, slot_time)`; `fired` flag |
| `topics` | Per-topic description used as Gemini context | PK: `topic` |
| `word_pool` | Candidate words staged for review in the dashboard | created by `dashboard/db.py:init_word_pool()` |

The `hint` column on `corpus` stores tone-marked pinyin + first English meaning for HSK words (e.g. `ài hào — to like; to be fond of`); `NULL` for non-HSK words. `init_db()` contains a one-time migration that moves any old `words` table rows into `corpus` and `user_progress`.

**Word selection (`core/vocab.pick_word`):** Weighted random — `weight = (1 / (mastery_score + 1)) * elapsed_seconds`. Unseen words (no `user_progress` row) get `NEVER_SEEN_SECONDS` (7 days) so they always surface early. Words with `mastery_score >= RETIREMENT_THRESHOLD` (10) are excluded. Topic is resolved from `user_settings`; if no row exists, falls back to the first topic in `corpus` alphabetically. `list_topics()` returns all topics present in `corpus` (global, not per-user).

**Slugify:** `core/vocab.slugify(text)` converts arbitrary text to an ASCII slug safe for Telegram `callback_data`: `re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")`. Used when inserting new words (`insert_word`) and when building suggestion card callback data in `bot.py`.

**Callback data format:** `known:{chat_id}:{topic}:{word_id}` — topic and word_id are embedded so `feedback_handler` can route to the correct DB row without a session lookup. Word IDs are always ASCII (pinyin for Chinese, English for other topics) to stay within Telegram's 64-byte callback_data limit. The `/suggest` flow uses `suggest_y` / `suggest_n` as the action prefix with the same `:{chat_id}:{topic}:{word_id}` suffix.

**Scheduling:** On startup, `wire_scheduler` loads today's persisted schedule from the DB (or generates a fresh one). It registers a PTB `run_once` job for each unfired future slot, and a `run_daily` job at midnight Melbourne time to regenerate. All times are Melbourne-aware (`ZoneInfo("Australia/Melbourne")`).

**Multi-topic:** Each user independently tracks mastery per topic. Switch via `/topic <name>` in Telegram (stored in `user_settings`). Built-in seed topics (`zoo_animals`, `vegetables`) are defined in `scripts/migrate_to_sqlite.py:SEED_DATA` and seeded for all `ALLOWED_CHAT_IDS` when the migration script is run. HSK topics (`chinese_hsk1` … `chinese_hsk7`) are populated by `scripts/import_hsk.py`.

**AI suggestions (`/suggest`):** Admin-only command. Fetches the active topic's description from the `topics` table, samples up to 15 existing words from `corpus`, and sends both as context to Gemini. Returns `SUGGEST_BATCH_SIZE` (currently 3) suggestions as inline Yes/No cards. Pending suggestions are held in `context.bot_data["pending"][chat_id]` (in-memory). Approved words are inserted into `corpus` via `core/vocab.insert_word()`.

**Card delivery (`send_card`):** Before sending, calls `generate_sentence()` (gemini-2.5-flash) with a 5-second `asyncio.wait_for` timeout. If the call times out or raises, the card is sent without a sentence — the word alone is never suppressed. The `hint` field (if set) is shown on the card as an italic line between the word and the sentence.

**Mastery updates (`record_feedback`):** `known` → `mastery_score + 1`; `forgot` → `max(0, mastery_score - 1)`. Deleting a word via the dashboard removes it from both `corpus` and `user_progress` but does NOT cascade-delete its `history` rows.

**Bot commands:** `/start` — welcome message; `/schedule` — show today's pending/fired slots in Melbourne time; `/topic [name]` — view or switch active topic; `/suggest` — trigger AI word suggestions for the active topic.

## Adding a new topic

**Custom topic:** Add an entry to `SEED_DATA` and `TOPIC_DESCRIPTIONS` in `scripts/migrate_to_sqlite.py` and re-run the script. `INSERT OR IGNORE` means existing data is never overwritten.

**HSK-style bulk import:** Use `scripts/import_hsk.py` as a template — it fetches JSON from GitHub and bulk-inserts into `corpus` with topic names like `chinese_hsk1`. Safe to re-run.

Word IDs must be ASCII (use `core/vocab.slugify()` for non-ASCII source text). For Chinese, the word ID is slugified numeric pinyin; the `word` field stores the simplified character(s).

## Constraints

- `ALLOWED_CHAT_IDS` in `.env` is the allowlist — a handful of users max.
- `stdlib sqlite3` only — no ORM, no new packages without good reason. Per-call `sqlite3.connect()` is intentional.
- `core/persistence.py` imports `_get_conn` from `core/vocab.py` (private cross-module import) — known design debt, acceptable at this scale.
- `dashboard/db.py` has its own `_get_conn()` copy — the dashboard is intentionally decoupled from the bot's core module.
- All scripts prepend the project root to `sys.path` so `core.*` imports resolve correctly.
