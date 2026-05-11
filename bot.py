import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from config import ALLOWED_CHAT_IDS, BOT_TOKEN
from persistence import mark_slot_fired
from scheduler import MELBOURNE_TZ, get_schedule_state, wire_scheduler
from vocab import pick_word, record_feedback


async def send_card(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    word = pick_word(chat_id)
    if word is None:
        return
    text = f"<b>{word['word']}</b>\n\n{word['sentence']}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Known", callback_data=f"known:{chat_id}:{word['id']}"),
        InlineKeyboardButton("❌ Forgot", callback_data=f"forgot:{chat_id}:{word['id']}"),
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
        "/schedule — see today's prompt times and which have already fired",
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


async def feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    result, chat_id_str, word_id = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        return
    if chat_id not in ALLOWED_CHAT_IDS:
        return
    record_feedback(chat_id, word_id, result)
    await query.edit_message_reply_markup(reply_markup=None)


def main() -> None:
    async def post_init(app):
        wire_scheduler(app, send_card)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(
        CallbackQueryHandler(feedback_handler, pattern=r"^(known|forgot):\d+:.+$")
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
