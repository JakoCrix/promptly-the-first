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
        "/topic — browse and switch your active vocabulary topic\n"
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


_TOPIC_FLAGS = {
    "chinese": "🇨🇳",
    "english": "🇬🇧",
    "japanese": "🇯🇵",
    "korean": "🇰🇷",
    "spanish": "🇪🇸",
}

_MISC_TOPIC_LABELS = {
    "vegetables": "🥦 Vegetables",
    "zoo_animals": "🦁 Zoo Animals",
}

_LANG_PREFIXES = frozenset(_TOPIC_FLAGS)


def _topic_label(topic: str) -> str:
    if topic in _MISC_TOPIC_LABELS:
        return _MISC_TOPIC_LABELS[topic]
    parts = topic.split("_")
    lang = parts[0]
    flag = _TOPIC_FLAGS.get(lang, "")
    if topic == "english_ngsl":
        return f"{flag} NGSL"
    if lang == "chinese":       # chinese_hsk_1
        return f"{flag} HSK {parts[-1]}"
    if lang == "japanese":      # japanese_jlpt_n5
        return f"{flag} {parts[-1].upper()}"
    if lang == "korean":        # korean_topik_1
        return f"{flag} TOPIK {parts[-1]}"
    if lang == "spanish":       # spanish_pcic_a1
        return f"{flag} {parts[-1].upper()}"
    return topic.replace("_", " ").title()


def _build_topic_keyboard(topics: list[str], active: str | None, chat_id: int) -> InlineKeyboardMarkup:
    lang_topics = [t for t in topics if t.split("_")[0] in _LANG_PREFIXES]
    other_topics = [t for t in topics if t.split("_")[0] not in _LANG_PREFIXES]

    groups: dict[str, list[str]] = {}
    for t in lang_topics:
        lang = t.split("_")[0]
        if lang not in groups:
            groups[lang] = []
        groups[lang].append(t)

    rows = []
    for group in groups.values():
        for i in range(0, len(group), 4):
            rows.append([
                InlineKeyboardButton(
                    ("✓ " if t == active else "") + _topic_label(t),
                    callback_data=f"topic:{chat_id}:{t}",
                )
                for t in group[i:i + 4]
            ])

    if other_topics:
        others_label = "✓ ⋯ Others" if active in other_topics else "⋯ Others"
        rows.append([InlineKeyboardButton(others_label, callback_data=f"topic_others:{chat_id}")])

    return InlineKeyboardMarkup(rows)


def _build_others_keyboard(other_topics: list[str], active: str | None, chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(other_topics), 3):
        rows.append([
            InlineKeyboardButton(
                ("✓ " if t == active else "") + _topic_label(t),
                callback_data=f"topic:{chat_id}:{t}",
            )
            for t in other_topics[i:i + 3]
        ])
    rows.append([InlineKeyboardButton("← Back", callback_data=f"topic_back:{chat_id}")])
    return InlineKeyboardMarkup(rows)


async def topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_registered(chat_id):
        return

    available = list_topics(chat_id)
    current = get_active_topic(chat_id) or (available[0] if available else None)

    if not context.args:
        if not available:
            await update.message.reply_text("No topics available yet.")
            return
        keyboard = _build_topic_keyboard(available, current, chat_id)
        active_label = _topic_label(current) if current else "none"
        await update.message.reply_text(
            f"Active: <b>{active_label}</b>\n\nChoose a topic:",
            reply_markup=keyboard,
            parse_mode="HTML",
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
    await update.message.reply_text(f"Switched to: <b>{_topic_label(match)}</b>", parse_mode="HTML")


async def topic_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer()
        return
    _, chat_id_str, topic = parts
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await query.answer()
        return
    if not is_registered(chat_id) or update.effective_chat.id != chat_id:
        await query.answer()
        return

    available = list_topics(chat_id)
    if topic not in available:
        await query.answer("Topic not found.", show_alert=True)
        return

    set_active_topic(chat_id, topic)
    label = _topic_label(topic)
    await query.answer(f"Switched to {label}")

    keyboard = _build_topic_keyboard(available, topic, chat_id)
    await query.edit_message_text(
        f"Active: <b>{label}</b>\n\nChoose a topic:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def topic_others_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        chat_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if not is_registered(chat_id) or update.effective_chat.id != chat_id:
        await query.answer()
        return

    await query.answer()
    available = list_topics(chat_id)
    current = get_active_topic(chat_id)
    other_topics = [t for t in available if t.split("_")[0] not in _LANG_PREFIXES]
    keyboard = _build_others_keyboard(other_topics, current, chat_id)
    await query.edit_message_text("Choose a topic:", reply_markup=keyboard)


async def topic_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        chat_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if not is_registered(chat_id) or update.effective_chat.id != chat_id:
        await query.answer()
        return

    await query.answer()
    available = list_topics(chat_id)
    current = get_active_topic(chat_id)
    keyboard = _build_topic_keyboard(available, current, chat_id)
    active_label = _topic_label(current) if current else "none"
    await query.edit_message_text(
        f"Active: <b>{active_label}</b>\n\nChoose a topic:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


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
        CallbackQueryHandler(topic_others_handler, pattern=r"^topic_others:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(topic_back_handler, pattern=r"^topic_back:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(topic_select_handler, pattern=r"^topic:\d+:.+$")
    )
    app.add_handler(
        CallbackQueryHandler(feedback_handler, pattern=r"^(known|forgot):\d+:[^:]+:.+$")
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
