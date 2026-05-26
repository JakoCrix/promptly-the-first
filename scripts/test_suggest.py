"""
Test the LLM word-suggestion pipeline end-to-end.

Phase 1: calls fetch_suggestions() and prints the raw results (no Telegram needed).
Phase 2: sends the first suggestion as a real inline card via Telegram and waits
         for a Yes / No button press, then inserts or skips accordingly.

Usage:
    python scripts/test_suggest.py              # active topic for first ALLOWED_CHAT_ID
    python scripts/test_suggest.py zoo_animals  # override topic
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes

from core.config import ALLOWED_CHAT_IDS, BOT_TOKEN, GOOGLE_API_KEY
from core.suggest import fetch_suggestions
from core.vocab import get_active_topic, get_topic_description, get_word_sample, init_db, insert_word, _slugify


async def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set in .env")
        return
    if not ALLOWED_CHAT_IDS:
        print("ERROR: CHAT_IDS is not set in .env")
        return
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_API_KEY is not set in .env")
        return

    init_db()
    chat_id = ALLOWED_CHAT_IDS[0]

    topic_arg = sys.argv[1] if len(sys.argv) > 1 else None
    topic = topic_arg or get_active_topic(chat_id)
    if topic is None:
        print(f"ERROR: No active topic for chat {chat_id}. Run /topic in Telegram or pass a topic as an argument.")
        return

    print(f"Topic: {topic}  |  Chat ID: {chat_id}")

    # --- Phase 1: raw Gemini call ---
    print("\n=== Phase 1: Fetching suggestions from Gemini ===")
    description = get_topic_description(topic)
    sample = get_word_sample(chat_id, topic)
    existing_words = [w["word"] for w in sample]

    print(f"  Description: {description or '(none — topic not in topics table)'}")
    print(f"  Existing word sample ({len(existing_words)}): {', '.join(existing_words[:10])}")
    print("  Calling Gemini...")

    suggestions = fetch_suggestions(topic, description, existing_words)
    if not suggestions:
        print("  ✗ No suggestions returned. Check your GOOGLE_API_KEY and network connection.")
        return

    print(f"  ✓ Got {len(suggestions)} suggestion(s):")
    for i, (word, sentence) in enumerate(suggestions, 1):
        print(f"    {i}. {word!r} — {sentence}")

    # --- Phase 2: send first suggestion card via Telegram ---
    print("\n=== Phase 2: Sending first suggestion card via Telegram ===")
    word, sentence = suggestions[0]
    word_id = _slugify(word)
    stop_event = asyncio.Event()
    result_holder: dict = {}

    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        action, cid_str, t, wid = parts
        try:
            cid = int(cid_str)
        except ValueError:
            return
        if cid not in ALLOWED_CHAT_IDS or wid != word_id:
            return

        if action == "suggest_y":
            added = insert_word(cid, t, word, sentence)
            result_holder["status"] = "Added!" if added else "Already exists — not inserted."
        else:
            result_holder["status"] = "Skipped."

        await query.edit_message_text(
            f"{'✅' if action == 'suggest_y' else '❌'} {result_holder['status']}\n\n"
            f"<i>{word}</i>: {sentence}",
            parse_mode="HTML",
        )
        stop_event.set()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^suggest_[yn]:"))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        me = await app.bot.get_me()
        print(f"  Connected as @{me.username}")

        text = (
            f"💡 Suggested word: <b>{word}</b>\n"
            f'"{sentence}"\n\n'
            f"Add to <i>{topic}</i>?"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"suggest_y:{chat_id}:{topic}:{word_id}"),
            InlineKeyboardButton("❌ No",  callback_data=f"suggest_n:{chat_id}:{topic}:{word_id}"),
        ]])
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            print(f"  ✓ Card sent to {chat_id} — press Yes or No in Telegram")
        except TelegramError as e:
            print(f"  ✗ Failed to send card: {e}")
            await app.updater.stop()
            await app.stop()
            return

        print("  Waiting for button press... (Ctrl+C to cancel)")
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass

        print(f"  ✓ Result: {result_holder.get('status', 'no response')}")

        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
