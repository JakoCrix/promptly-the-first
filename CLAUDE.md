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
INVITE_CODE=<invite-code-for-self-registration>
GEMINI_MODEL=gemini-2.5-flash
```

`CHAT_IDS` seeds the `users` table on startup so existing users aren't prompted to re-register. `INVITE_CODE` is required for `/register` to work. `GEMINI_MODEL` defaults to `gemini-2.5-flash` if omitted.

## Manual test scripts

```bash
python scripts/test_card.py                               # force-send a random card and wait for button press
python scripts/test_card.py <word_id>                     # force-send a specific word (e.g. "pangolin", "ni_hao")
python -m core.scheduler                                  # print today's generated slots (quick sanity check)

# Corpus importers (scripts/corpora/) — safe to re-run (INSERT OR IGNORE)
python scripts/corpora/import_chinese.py            # dry-run: show existing chinese_hsk* row count
python scripts/corpora/import_chinese.py --confirm  # delete old chinese_hsk* and import HSK 3.0 (9 levels)
python scripts/corpora/import_english.py            # import NGSL 1.01 (~2,800 words, english_ngsl)
python scripts/corpora/import_japanese.py           # import JLPT N5–N1 (5 topics)
python scripts/corpora/import_korean.py             # import Korean freq list across 6 TOPIK-approx levels
python scripts/corpora/import_spanish.py            # import Spanish freq list across 6 PCIC-approx levels

# Sentence bank builders (scripts/sentences/) — resumable, safe to re-run
python scripts/sentences/chinese.py                       # generate sentences for all chinese_hsk_* topics
python scripts/sentences/chinese.py --topic chinese_hsk_1 # limit to one topic
python scripts/sentences/chinese.py --count 10 --delay 0.5 # sentences per word, API delay in seconds

# Pronunciation builders (scripts/pronunciation/) — resumable, safe to re-run
# Chinese pronunciation (toned pinyin) is populated by import_chinese.py --confirm (no AI needed)
python scripts/pronunciation/japanese.py                  # generate Hepburn romaji for all japanese_jlpt_* topics
python scripts/pronunciation/japanese.py --topic japanese_jlpt_n5 # limit to one topic
python scripts/pronunciation/korean.py                    # generate Revised Romanization for all korean_topik_* topics
python scripts/pronunciation/korean.py --topic korean_topik_1     # limit to one topic
python scripts/sentences/english.py                       # generate sentences for english_* topics
python scripts/sentences/japanese.py                      # generate sentences for japanese_jlpt_* topics
python scripts/sentences/korean.py                        # generate sentences for korean_topik_* topics
python scripts/sentences/spanish.py                       # generate sentences for spanish_pcic_* topics
```

## Architecture

No automated tests. All verification is manual via the scripts above.

**Entry point:** `bot.py` — builds the PTB `Application`, registers handlers, calls `init_db()` then `wire_scheduler()` in `post_init`, then runs polling.

**Module responsibilities:**

| Module | Role |
|---|---|
| `core/config.py` | Loads `.env`, exposes all tuneable constants (`DAILY_SLOTS`, `WINDOW_START/END_HOUR`, `RETIREMENT_THRESHOLD`, `NEVER_SEEN_SECONDS`, `MIN_RETIRED_FOR_WEAVING`) |
| `core/vocab.py` | All SQLite I/O for vocab data; owns `_get_conn()`, `init_db()`, and the word selection algorithm |
| `core/persistence.py` | Schedule I/O only — reads/writes the `schedule` table; imports `_get_conn` from `core/vocab` |
| `core/scheduler.py` | Generates daily slot timestamps, registers PTB `job_queue` jobs, schedules midnight regeneration, prefills `sentence_cache` at startup and midnight |
| `core/suggest.py` | Calls Google Gemini (gemini-2.5-flash) to generate example sentences (`generate_sentence`) and apply HTML formatting (`format_sentence_words`) |
| `dashboard/app.py` | Streamlit UI — two tabs: Vocabulary Navigator (browse/edit/delete words, bulk mastery adjustments) and Word Discovery Pool (review and approve candidate words) |
| `dashboard/db.py` | Dashboard-only DB helpers; queries `corpus` + `user_progress`; exposes `add_to_decks()`, bulk mastery/delete operations, and `update_word_corpus()` (edits word text and hint) |

**Database:** `data/promptly.db` — a single SQLite file, gitignored (runtime data).

**Schema summary:**

| Table | Purpose | Key columns |
|---|---|---|
| `corpus` | Shared word definitions, one row per word | PK: `(topic, word_id)`; columns: `word`, `hint`, `frequency_rank`, `pronunciation` |
| `user_progress` | Per-user mastery tracking | PK: `(chat_id, topic, word_id)`; FK → corpus; columns: `mastery_score`, `last_seen` |
| `history` | Every Known/Forgot event | FK → user_progress; `UNIQUE (chat_id, topic, word_id, timestamp, result)` |
| `user_settings` | Each user's active topic | PK: `chat_id` |
| `users` | Registered users | PK: `chat_id`; `registered_at` ISO timestamp |
| `schedule` | Today's notification slots | PK: `(chat_id, date, slot_time)`; `fired` flag |
| `sentence_cache` | Per-user per-day prefilled sentences | PK: `(chat_id, topic, word_id, generated_for)`; purged after 7 days |
| `word_sentences` | Permanent corpus-wide sentence bank | PK: `id`; indexed on `(topic, word_id)`; capped at 10 per word |
| `topics` | Per-topic description used as Gemini context | PK: `topic` |
| `word_pool` | Candidate words staged for review in the dashboard | created by `dashboard/db.py:init_word_pool()` |

The `hint` column on `corpus` stores the English gloss for HSK/JLPT/Korean words; `NULL` for Spanish and English entries. The `pronunciation` column stores romanization: toned pinyin for Chinese (e.g. `ài hào`), Hepburn romaji for Japanese (e.g. `ane`), Revised Romanization for Korean; `NULL` for Spanish and English. Both columns are shown on vocab cards. `init_db()` contains a one-time migration that moves any old `words` table rows into `corpus` and `user_progress`.

**Word selection (`core/vocab.pick_word`):** Weighted random — `weight = (1 / (mastery_score + 1)) * elapsed_seconds`. Unseen words (no `user_progress` row) get `NEVER_SEEN_SECONDS` (7 days) so they always surface early. Words with `mastery_score >= RETIREMENT_THRESHOLD` (10) are excluded. Topic is resolved from `user_settings`; if no row exists, falls back to the first topic in `corpus` alphabetically. `list_topics(chat_id)` returns all topics present in `corpus` (global — the query does not filter by user).

**Slugify:** `core/vocab.slugify(text)` converts arbitrary text to an ASCII slug safe for Telegram `callback_data`: `re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")`. Used when inserting new words (`insert_word`) and must be used for any non-ASCII word IDs.

**Callback data format:** `known:{chat_id}:{topic}:{word_id}` (or `forgot:…`) — topic and word_id are embedded so `feedback_handler` can route to the correct DB row without a session lookup. Word IDs are always ASCII (pinyin for Chinese, English for other topics) to stay within Telegram's 64-byte callback_data limit. `insert_word` enforces this limit at insert time.

**Scheduling:** On startup, `wire_scheduler` loads today's persisted schedule from the DB per user (or generates a fresh one). It registers a PTB `run_once` job for each unfired future slot, and a `run_daily` job at midnight Melbourne time to regenerate. All times are Melbourne-aware (`ZoneInfo("Australia/Melbourne")`). When a new user registers via `/register`, `wire_new_user` generates and registers their schedule immediately for the remainder of the day.

**Sentence resolution (three-tier cascade):** At startup and every midnight, `_prefill_sentence_cache` pre-generates `DAILY_SLOTS * 2` sentences per user. Each generated sentence is stored in both `sentence_cache` (per-user/per-day) and `word_sentences` (permanent bank, capped at 10 per word). `send_card` resolves a sentence with this cascade:
1. `sentence_cache` — today's prefilled sentence for this user
2. `word_sentences` — any previously banked sentence for this word (formatted on the fly, then promoted into `sentence_cache`)
3. Live Gemini call — 15-second `asyncio.wait_for` timeout; card is sent without a sentence if this fails

`format_sentence_words` wraps the main word in `<u><b>…</b></u>` and any weaved-in retired words in `<u>…</u>` (HTML parse mode). The `hint` field (if set) is shown as an `<i>italic</i>` line between the word and the sentence. Retired words are only woven in when `len(retired) >= MIN_RETIRED_FOR_WEAVING` (3).

**Sentence bank scripts (`scripts/sentences/`):** One script per language; each calls Gemini once per word and writes to `word_sentences` (and a CSV backup at `data/word_sentences/<lang>.csv`). Resumable — words with `>= count` existing rows are skipped. All scripts accept `--topic`, `--count`, and `--delay` flags.

**Mastery updates (`record_feedback`):** `known` → `mastery_score + 1`; `forgot` → `max(0, mastery_score - 1)`. Deleting a word via the dashboard removes it from both `corpus` and `user_progress` but does NOT cascade-delete its `history` rows.

**Bot commands:** `/start` — welcome message; `/register <code>` — self-register with invite code; `/schedule` — show today's pending/fired slots in Melbourne time; `/topic [name]` — view or switch active topic; `/test [word_id]` — send 3 weighted-random cards right now, or a specific word if `word_id` given.

## Corpus pipeline (Stage 1 languages)

Import scripts live in `scripts/corpora/`. Each script is self-contained and safe to re-run (`INSERT OR IGNORE`). Shared utilities are in `scripts/corpora/_base.py` (re-exports `slugify` and `_get_conn` from `core.vocab`; provides `validate_word_id`, `bulk_insert`, and `normalize_slug` for accent-bearing scripts).

**Topic naming convention:** `{language}_{list}_{level}`

| Language | Topics | Source |
|---|---|---|
| Chinese | `chinese_hsk_1` … `chinese_hsk_9` | drkameleon/complete-hsk-vocabulary (GitHub JSON) |
| English | `english_ngsl` | koba-ninkigumi/ngsl NGSL-1.01.csv (CC BY-SA 4.0) |
| Japanese | `japanese_jlpt_n5` … `japanese_jlpt_n1` | jamsinclair/open-anki-jlpt-decks (MIT) |
| Korean | `korean_topik_1` … `korean_topik_6` | jemdiggity/hanja-wordlist Korean Vocab 6000 TSV |
| Spanish | `spanish_pcic_a1` … `spanish_pcic_c2` | doozan/spanish_data frequency.csv (CC-BY-4.0) |

**word_id rules:**
- Chinese: `slugify(numeric_pinyin)` (ASCII pinyin, e.g. `ai4_hao4`)
- English/Japanese: `slugify(english_meaning)` with dedup counter on collision
- Korean: `k_{rank:04d}` (sequential — hangul can't be slugified)
- Spanish: `es_{rank:05d}` (sequential — accented chars drop from slugify)

**`frequency_rank` column:** nullable `INTEGER` on `corpus`, populated by all Stage 1 importers as the word's position within its source list. Added via a one-time migration in `init_db()`.

**Chinese full-replace:** `import_chinese.py` requires `--confirm` to delete existing `chinese_hsk*` rows (corpus + user_progress) before re-importing. Dry-run without the flag shows current row counts.

## Adding a new topic

Use one of the existing `scripts/corpora/` importers as a template. Insert rows directly into `corpus` with `INSERT OR IGNORE`, set a `topics` row for the Gemini description, and follow the topic naming convention above.

Word IDs must be ASCII (use `core/vocab.slugify()` for non-ASCII source text). For Chinese, the word ID is slugified numeric pinyin; the `word` field stores the simplified character(s).

## Constraints

- The `users` table is the authoritative user registry. `CHAT_IDS` in `.env` only seeds it at startup for backwards compatibility — new users join via `/register <invite_code>`.
- `stdlib sqlite3` only — no ORM, no new packages without good reason. Per-call `sqlite3.connect()` is intentional.
- `core/persistence.py` imports `_get_conn` from `core/vocab.py` (private cross-module import) — known design debt, acceptable at this scale.
- `dashboard/db.py` has its own `_get_conn()` copy — the dashboard is intentionally decoupled from the bot's core module.
- All scripts prepend the project root to `sys.path` so `core.*` imports resolve correctly.
