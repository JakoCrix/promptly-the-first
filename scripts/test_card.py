"""
Force-send a vocab card and listen for the button response.
Sends the full card format with ✅ Known / ❌ Forgot inline buttons,
waits for you to press one, updates the mastery score, then exits.

Usage:
    python scripts/test_card.py              # random word from active topic
    python scripts/test_card.py pangolin     # specific word by id (active topic)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes

from core.config import ALLOWED_CHAT_IDS, BOT_TOKEN
from core.vocab import get_active_topic, get_word, pick_word, record_feedback


def resolve_word(chat_id: int, word_id: str | None) -> dict | None:
    if word_id:
        topic = get_active_topic(chat_id)
        if topic is None:
            print(f"  ✗ No active topic set for chat {chat_id} — run /topic in Telegram first")
            return None
        word = get_word(chat_id, topic, word_id)
        if word is None:
            print(f"  ✗ Word '{word_id}' not found in topic '{topic}' for chat {chat_id}")
        return word
    word = pick_word(chat_id)
    if word is None:
        print(f"  ✗ No eligible words for chat {chat_id} (no active topic or all retired)")
    return word


async def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set in .env")
        return
    if not ALLOWED_CHAT_IDS:
        print("ERROR: CHAT_IDS is not set in .env")
        return

    word_id = sys.argv[1] if len(sys.argv) > 1 else None
    stop_event = asyncio.Event()

    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        result, chat_id_str, topic, wid = parts
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            return
        if chat_id not in ALLOWED_CHAT_IDS:
            return
        record_feedback(chat_id, topic, wid, result)
        await query.edit_message_reply_markup(reply_markup=None)
        label = "Known" if result == "known" else "Forgot"
        print(f"  ✓ '{wid}' marked as {label} — mastery updated")
        stop_event.set()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(known|forgot):\d+:[^:]+:.+$"))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        me = await app.bot.get_me()
        print(f"Connected as @{me.username}")

        sent = False
        for chat_id in ALLOWED_CHAT_IDS:
            word = resolve_word(chat_id, word_id)
            if word is None:
                continue
            text = f"<b>{word['word']}</b>"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Known", callback_data=f"known:{chat_id}:{word['topic']}:{word['id']}"),
                InlineKeyboardButton("❌ Forgot", callback_data=f"forgot:{chat_id}:{word['topic']}:{word['id']}"),
            ]])
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                print(f"  ✓ Sent '{word['word']}' to {chat_id} — press a button in Telegram")
                sent = True
            except TelegramError as e:
                print(f"  ✗ Failed for {chat_id}: {e}")

        if sent:
            print("Waiting for button press... (Ctrl+C to cancel)")
            try:
                await stop_event.wait()
            except asyncio.CancelledError:
                pass

        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
