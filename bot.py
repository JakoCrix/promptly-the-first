# python bot.py
import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from core.config import BOT_TOKEN, MIN_RETIRED_FOR_WEAVING
from core.persistence import mark_slot_fired
from core.scheduler import MELBOURNE_TZ, get_schedule_state, wire_new_user, wire_scheduler
from core.suggest import format_sentence_words, generate_sentence
from core.vocab import get_active_topic, get_cached_sentence, get_retired_words, get_topic_description, get_word, init_db, is_registered, list_topics, pick_n_words, pick_word, record_feedback, register_user, set_active_topic, store_cached_sentence


async def send_card(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    word = pick_word(chat_id)
    if word is None:
        return

    today = datetime.now(MELBOURNE_TZ).strftime("%Y-%m-%d")
    sentence = get_cached_sentence(chat_id, word["topic"], word["id"], today)
    if sentence is None:
        retired = get_retired_words(chat_id, word["topic"])
        highlight_retired = retired if len(retired) >= MIN_RETIRED_FOR_WEAVING else []
        try:
            description = get_topic_description(word["topic"])
            raw = await asyncio.wait_for(
                asyncio.to_thread(generate_sentence, word["word"], word["topic"], description, word.get("hint"), retired),
                timeout=15.0,
            )
            if raw:
                sentence = format_sentence_words(raw, word["word"], highlight_retired)
                store_cached_sentence(chat_id, word["topic"], word["id"], sentence, today)
        except asyncio.TimeoutError:
            logging.debug("generate_sentence timed out for '%s'", word["word"])
        except Exception:
            logging.warning("generate_sentence failed for '%s'", word["word"], exc_info=True)

    hint_line = f"\n<i>{word['hint']}</i>" if word.get("hint") else ""
    text = f"<b>{word['word']}</b>{hint_line}" + (f"\n\n{sentence}" if sentence else "")
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
    context.bot_data.setdefault("today_fired", {}).setdefault(chat_id, set()).add(ts)
    try:
        mark_slot_fired(chat_id, ts)
    except Exception:
        logging.exception("Failed to persist fired slot %s for chat %s — in-memory state is current", ts, chat_id)


async def start_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_registered(update.effective_chat.id):
        await update.message.reply_text(
            "👋 Hi! To join Promptly, use /register <invite code>."
        )
        return
    await update.message.reply_text(
        "👋 Welcome to Promptly!\n\n"
        "I'll send you vocabulary prompts throughout the day — just tap ✅ Known or ❌ Forgot on each one.\n\n"
        "Commands:\n"
        "/schedule — see today's prompt times and which have already fired\n"
        "/topic — see or switch your active vocabulary topic. Type /topic <name> to switch to a different topic.\n"
        "/test — send a random card right now. Type /test <word_id> to test a specific word.",
        parse_mode=None,
    )


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_registered(chat_id):
        return

    today_slots = context.bot_data.get("today_slots", {}).get(chat_id)
    if not today_slots:
        await update.message.reply_text("No schedule available yet.")
        return

    fired_times = context.bot_data.get("today_fired", {}).get(chat_id, set())
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
    if not is_registered(chat_id):
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


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_registered(chat_id):
        return

    if context.args:
        topic = get_active_topic(chat_id)
        if topic is None:
            await update.message.reply_text("No active topic set. Use /topic <name> to choose one.")
            return
        word = get_word(chat_id, topic, context.args[0])
        if word is None:
            await update.message.reply_text(f"Word '{context.args[0]}' not found in topic '{topic}'.")
            return
        words = [word]
    else:
        words = pick_n_words(chat_id, 3)
        if not words:
            await update.message.reply_text("No eligible words (all retired, or no active topic).")
            return

    today = datetime.now(MELBOURNE_TZ).strftime("%Y-%m-%d")
    for word in words:
        sentence = get_cached_sentence(chat_id, word["topic"], word["id"], today)
        if sentence is None:
            retired = get_retired_words(chat_id, word["topic"])
            highlight_retired = retired if len(retired) >= MIN_RETIRED_FOR_WEAVING else []
            try:
                description = get_topic_description(word["topic"])
                raw = await asyncio.wait_for(
                    asyncio.to_thread(generate_sentence, word["word"], word["topic"], description, word.get("hint"), retired),
                    timeout=15.0,
                )
                if raw:
                    sentence = format_sentence_words(raw, word["word"], highlight_retired)
                    store_cached_sentence(chat_id, word["topic"], word["id"], sentence, today)
            except asyncio.TimeoutError:
                logging.debug("generate_sentence timed out for '%s'", word["word"])
            except Exception:
                logging.warning("generate_sentence failed for '%s'", word["word"], exc_info=True)

        hint_line = f"\n<i>{word['hint']}</i>" if word.get("hint") else ""
        text = f"<b>{word['word']}</b>{hint_line}" + (f"\n\n{sentence}" if sentence else "")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Known", callback_data=f"known:{chat_id}:{word['topic']}:{word['id']}"),
            InlineKeyboardButton("❌ Forgot", callback_data=f"forgot:{chat_id}:{word['topic']}:{word['id']}"),
        ]])
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if is_registered(chat_id):
        await update.message.reply_text("You're already registered!")
        return
    if not context.args:
        await update.message.reply_text("Usage: /register <invite code>")
        return
    if register_user(chat_id, context.args[0]):
        wire_new_user(context.application, send_card, chat_id)
        await update.message.reply_text(
            "You're registered! Your first vocabulary cards are scheduled for today."
        )
    else:
        await update.message.reply_text("Invalid invite code.")


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
    if not is_registered(chat_id) or update.effective_chat.id != chat_id:
        return
    record_feedback(chat_id, topic, word_id, result)
    await query.edit_message_reply_markup(reply_markup=None)



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
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("topic", topic_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(
        CallbackQueryHandler(feedback_handler, pattern=r"^(known|forgot):\d+:[^:]+:.+$")
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
