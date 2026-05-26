import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from core.config import ALLOWED_CHAT_IDS, BOT_TOKEN
from core.persistence import mark_slot_fired
from core.scheduler import MELBOURNE_TZ, get_schedule_state, wire_scheduler
from core.suggest import fetch_suggestions
from core.vocab import get_active_topic, get_topic_description, get_word_sample, init_db, insert_word, list_topics, pick_word, record_feedback, set_active_topic, _slugify


async def send_card(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    word = pick_word(chat_id)
    if word is None:
        return
    text = f"<b>{word['word']}</b>\n\n{word['sentence']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Known", callback_data=f"known:{chat_id}:{word['topic']}:{word['id']}"),
        InlineKeyboardButton("❌ Forgot", callback_data=f"forgot:{chat_id}:{word['topic']}:{word['id']}"),
    ]])
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    ts: datetime = context.job.data
    context.bot_data.setdefault("today_fired", set()).add(ts)
    try:
        mark_slot_fired(ts)
    except Exception:
        logging.exception("Failed to persist fired slot %s — in-memory state is current", ts)


async def start_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    await update.message.reply_text(
        "👋 Welcome to Promptly!\n\n"
        "I'll send you vocabulary prompts throughout the day — just tap ✅ Known or ❌ Forgot on each one.\n\n"
        "Commands:\n"
        "/schedule — see today's prompt times and which have already fired\n"
        "/topic — see or switch your active vocabulary topic. Type /topic <name> to switch to a different topic.\n"
        "/suggest — get AI-generated word suggestions for your active topic",
        parse_mode=None,
    )


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        return

    today_slots = context.bot_data.get("today_slots")
    if not today_slots:
        await update.message.reply_text("No schedule available yet.")
        return

    fired_times = context.bot_data.get("today_fired", set())
    schedule = get_schedule_state(today_slots, fired_times)

    lines = []
    for entry in schedule:
        t = entry["time"].astimezone(MELBOURNE_TZ)
        symbol = "⏳" if entry["pending"] else "✅"
        lines.append(f"{symbol} {t.strftime('%H:%M')}")

    header = f"Today's schedule ({len(schedule)} slots, Melbourne time):"
    await update.message.reply_text(header + "\n" + "\n".join(lines))


async def topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        return

    available = list_topics(chat_id)

    if not context.args:
        current = get_active_topic(chat_id) or (available[0] if available else "none")
        topics_str = ", ".join(available) if available else "none"
        await update.message.reply_text(
            f"Active topic: {current}\nAvailable: {topics_str}"
        )
        return

    requested = context.args[0]
    match = next((t for t in available if t.lower() == requested.lower()), None)
    if match is None:
        topics_str = ", ".join(available) if available else "none"
        await update.message.reply_text(
            f"Topic '{requested}' not found. Available: {topics_str}"
        )
        return

    set_active_topic(chat_id, match)
    await update.message.reply_text(f"Switched to topic: {match}")


async def feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 3)
    if len(parts) != 4:
        return
    result, chat_id_str, topic, word_id = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return
    if chat_id not in ALLOWED_CHAT_IDS or update.effective_chat.id != chat_id:
        return
    record_feedback(chat_id, topic, word_id, result)
    await query.edit_message_reply_markup(reply_markup=None)


async def _send_next_suggestion(
    bot,
    chat_id: int,
    topic: str,
    bot_data: dict,
) -> None:
    pending = bot_data.get("pending", {}).get(chat_id, [])
    if not pending:
        await bot.send_message(chat_id=chat_id, text="All done! No more suggestions.")
        return

    item = pending[0]
    word, sentence, word_id = item["word"], item["sentence"], item["word_id"]
    text = (
        f"💡 Suggested word: <b>{word}</b>\n"
        f'"{sentence}"\n\n'
        f"Add to <i>{topic}</i>?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"suggest_y:{chat_id}:{topic}:{word_id}"),
        InlineKeyboardButton("❌ No",  callback_data=f"suggest_n:{chat_id}:{topic}:{word_id}"),
    ]])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML")


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        return

    topic = get_active_topic(chat_id)
    if topic is None:
        await update.message.reply_text("No active topic set. Use /topic <name> to choose one.")
        return

    await update.message.reply_text(f"Fetching suggestions for <i>{topic}</i>…", parse_mode="HTML")

    description = get_topic_description(topic)
    sample = get_word_sample(chat_id, topic)
    existing_words = [w["word"] for w in sample]

    suggestions = fetch_suggestions(topic, description, existing_words)
    if not suggestions:
        await update.message.reply_text("Couldn't get suggestions right now. Check your GOOGLE_API_KEY and try again.")
        return

    pending_list = [
        {"word": word, "sentence": sentence, "word_id": _slugify(word)}
        for word, sentence in suggestions
    ]
    context.bot_data.setdefault("pending", {})[chat_id] = pending_list
    await _send_next_suggestion(context.bot, chat_id, topic, context.bot_data)


async def suggest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 3)
    if len(parts) != 4:
        return
    action, chat_id_str, topic, word_id = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return
    if chat_id not in ALLOWED_CHAT_IDS or update.effective_chat.id != chat_id:
        return

    pending = context.bot_data.get("pending", {}).get(chat_id, [])
    if not pending or pending[0]["word_id"] != word_id:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    item = pending.pop(0)

    if action == "suggest_y":
        added = insert_word(chat_id, topic, item["word"], item["sentence"])
        status = "✅ Added!" if added else "Already exists."
    else:
        status = "❌ Skipped."

    await query.edit_message_text(
        f"{status}\n\n<i>{item['word']}</i>: {item['sentence']}",
        parse_mode="HTML",
    )
    await _send_next_suggestion(context.bot, chat_id, topic, context.bot_data)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async def post_init(app):
        init_db()
        wire_scheduler(app, send_card)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("topic", topic_command))
    app.add_handler(CommandHandler("suggest", suggest_command))
    app.add_handler(
        CallbackQueryHandler(feedback_handler, pattern=r"^(known|forgot):\d+:[^:]+:.+$")
    )
    app.add_handler(
        CallbackQueryHandler(suggest_callback, pattern=r"^suggest_[yn]:")
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
