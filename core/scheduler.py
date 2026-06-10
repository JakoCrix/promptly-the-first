import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.config import ALLOWED_CHAT_IDS, DAILY_SLOTS, MIN_RETIRED_FOR_WEAVING, WINDOW_END_HOUR, WINDOW_START_HOUR
from core.persistence import load_today_schedule, save_schedule

log = logging.getLogger(__name__)

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


def generate_daily_timestamps(reference_date: datetime | None = None) -> list[datetime]:
    """
    Return DAILY_SLOTS unique datetimes spread across [WINDOW_START_HOUR, WINDOW_END_HOUR)
    on the same calendar day as reference_date (defaults to today in Melbourne time).

    Timestamps are sorted ascending so notifications arrive in a natural order.
    """
    if reference_date is None:
        reference_date = datetime.now(tz=MELBOURNE_TZ)

    day_start = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)

    window_open = WINDOW_START_HOUR * 3600   # seconds since midnight
    window_close = WINDOW_END_HOUR * 3600

    window_seconds = window_close - window_open
    if DAILY_SLOTS > window_seconds:
        raise ValueError(
            f"DAILY_SLOTS ({DAILY_SLOTS}) exceeds available window seconds ({window_seconds}). "
            "Reduce DAILY_SLOTS or widen WINDOW_START/END_HOUR in config."
        )
    offsets = random.sample(range(window_open, window_close), DAILY_SLOTS)
    offsets.sort()

    return [day_start + timedelta(seconds=s) for s in offsets]


def get_schedule_state(
    today_slots: list[datetime],
    fired_times: set[datetime],
) -> list[dict]:
    """Return fired/pending state for each slot.

    Each dict: {"time": datetime, "pending": bool}
    A slot is pending if it is in the future and not in fired_times.
    """
    now = datetime.now(tz=MELBOURNE_TZ)
    return [
        {"time": ts, "pending": ts > now and ts not in fired_times}
        for ts in sorted(today_slots)
    ]


def _schedule_day(app, send_card_fn, slots: list[datetime], fired: set[datetime]) -> None:
    now = datetime.now(tz=MELBOURNE_TZ)
    app.bot_data["today_slots"] = slots
    app.bot_data["today_fired"] = fired
    for ts in slots:
        if ts not in fired and ts > now:
            for chat_id in ALLOWED_CHAT_IDS:
                app.job_queue.run_once(
                    send_card_fn,
                    when=ts,
                    chat_id=chat_id,
                    name=f"card_{chat_id}_{ts.isoformat()}",
                    data=ts,
                )


async def _prefill_sentence_cache() -> None:
    # Imported here to avoid circular imports at module load time.
    from core.suggest import format_sentence_words, generate_sentence
    from core.vocab import get_retired_words, get_topic_description, pick_n_words, purge_old_cached_sentences, store_cached_sentence

    now = datetime.now(MELBOURNE_TZ)
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    deleted = purge_old_cached_sentences(cutoff)
    if deleted:
        log.info("purged %d stale sentence_cache row(s) older than %s", deleted, cutoff)

    async def _generate_and_store(chat_id: int, word: dict, description: str, retired: list, highlight_retired: list) -> None:
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    generate_sentence,
                    word["word"], word["topic"], description, word.get("hint"), retired,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            log.warning("sentence prefill timed out for '%s' (>15 s)", word["word"])
            return
        except Exception as exc:
            log.warning("sentence prefill failed for '%s': %s: %s", word["word"], type(exc).__name__, exc)
            return
        if raw:
            sentence = format_sentence_words(raw, word["word"], highlight_retired)
            store_cached_sentence(chat_id, word["topic"], word["id"], sentence, today)
            log.debug("cached '%s': %.60s", word["word"], sentence)

    tasks = []
    target = DAILY_SLOTS * 2
    for chat_id in ALLOWED_CHAT_IDS:
        for word in pick_n_words(chat_id, target):
            description = get_topic_description(word["topic"])
            retired = get_retired_words(chat_id, word["topic"])
            highlight_retired = retired if len(retired) >= MIN_RETIRED_FOR_WEAVING else []
            tasks.append(_generate_and_store(chat_id, word, description, retired, highlight_retired))

    await asyncio.gather(*tasks)
    log.info("sentence cache prefilled for %d chat(s)", len(ALLOWED_CHAT_IDS))


async def _startup_prefill(context) -> None:
    await _prefill_sentence_cache()


async def _midnight_callback(context) -> None:
    send_card_fn = context.application.bot_data["send_card_fn"]
    now = datetime.now(tz=MELBOURNE_TZ)
    slots = generate_daily_timestamps(now)
    fired: set[datetime] = set()
    save_schedule(slots, fired)
    await _prefill_sentence_cache()
    _schedule_day(context.application, send_card_fn, slots, fired)


def wire_scheduler(app, send_card_fn) -> None:
    app.bot_data["send_card_fn"] = send_card_fn

    now = datetime.now(tz=MELBOURNE_TZ)
    slots, fired = load_today_schedule()
    if slots is None:
        slots = generate_daily_timestamps(now)
        fired = set()
        save_schedule(slots, fired)

    _schedule_day(app, send_card_fn, slots, fired)

    app.job_queue.run_once(_startup_prefill, when=5)
    app.job_queue.run_daily(
        _midnight_callback,
        time=time(0, 0, 0, tzinfo=MELBOURNE_TZ),
        name="midnight_regen",
    )


# Quick sanity check: python scheduler.py
if __name__ == "__main__":
    slots = generate_daily_timestamps()
    print(f"Today's {len(slots)} notification slots (Melbourne time):")
    for ts in slots:
        print(f"  {ts.strftime('%H:%M:%S %Z')}")
