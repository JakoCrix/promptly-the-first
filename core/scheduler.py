import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.config import DAILY_SLOTS, MIN_RETIRED_FOR_WEAVING, WINDOW_END_HOUR, WINDOW_START_HOUR
from core.persistence import load_today_schedule, save_schedule
from core.vocab import get_allowed_chat_ids, get_user_schedule_settings

log = logging.getLogger(__name__)

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


def generate_daily_timestamps(
    reference_date: datetime | None = None,
    *,
    start_hour: int | None = None,
    end_hour: int | None = None,
    daily_slots: int | None = None,
) -> list[datetime]:
    """
    Return daily_slots datetimes spread across [start_hour, end_hour) on the same
    calendar day as reference_date (defaults to today in Melbourne time).

    Uses stratified sampling: divides the window into daily_slots equal buckets and
    picks one random second within each, guaranteeing even distribution.
    Timestamps are sorted ascending so notifications arrive in a natural order.
    """
    if reference_date is None:
        reference_date = datetime.now(tz=MELBOURNE_TZ)

    _start = start_hour  if start_hour  is not None else WINDOW_START_HOUR
    _end   = end_hour    if end_hour    is not None else WINDOW_END_HOUR
    _slots = daily_slots if daily_slots is not None else DAILY_SLOTS

    day_start = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)

    window_open  = _start * 3600
    window_close = _end   * 3600
    window_seconds = window_close - window_open

    if _slots > window_seconds:
        raise ValueError(
            f"daily_slots ({_slots}) exceeds available window seconds ({window_seconds}). "
            "Reduce daily_slots or widen the window."
        )

    bucket_size = window_seconds / _slots
    offsets = []
    for i in range(_slots):
        b_start = int(window_open + i * bucket_size)
        b_end   = int(window_open + (i + 1) * bucket_size)
        offsets.append(random.randint(b_start, b_end - 1) if b_end > b_start else b_start)
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


def _schedule_user_day(app, send_card_fn, chat_id: int, slots: list[datetime], fired: set[datetime]) -> None:
    now = datetime.now(tz=MELBOURNE_TZ)
    app.bot_data.setdefault("today_slots", {})[chat_id] = slots
    app.bot_data.setdefault("today_fired", {})[chat_id] = set(fired)
    for ts in slots:
        if ts not in fired and ts > now:
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
    from core.vocab import get_retired_words, get_topic_description, pick_n_words, purge_old_cached_sentences, store_cached_sentence, store_pooled_sentence

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
                    word["word"], word["topic"], description, word.get("definition"), retired,
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
            store_pooled_sentence(word["topic"], word["id"], raw)
            log.debug("cached '%s': %.60s", word["word"], sentence)

    tasks = []
    allowed = get_allowed_chat_ids()
    for chat_id in allowed:
        _, _, user_slots = get_user_schedule_settings(chat_id)
        for word in pick_n_words(chat_id, user_slots * 2):
            description = get_topic_description(word["topic"])
            retired = get_retired_words(chat_id, word["topic"])
            highlight_retired = retired if len(retired) >= MIN_RETIRED_FOR_WEAVING else []
            tasks.append(_generate_and_store(chat_id, word, description, retired, highlight_retired))

    await asyncio.gather(*tasks)
    log.info("sentence cache prefilled for %d chat(s)", len(allowed))


async def _startup_prefill(context) -> None:
    await _prefill_sentence_cache()


async def _midnight_callback(context) -> None:
    send_card_fn = context.application.bot_data["send_card_fn"]
    now = datetime.now(tz=MELBOURNE_TZ)
    for chat_id in get_allowed_chat_ids():
        start, end, count = get_user_schedule_settings(chat_id)
        slots = generate_daily_timestamps(now, start_hour=start, end_hour=end, daily_slots=count)
        fired: set[datetime] = set()
        save_schedule(chat_id, slots, fired)
        _schedule_user_day(context.application, send_card_fn, chat_id, slots, fired)
    await _prefill_sentence_cache()


def wire_new_user(app, send_card_fn, chat_id: int) -> None:
    """Generate and register today's schedule for a newly registered user."""
    now = datetime.now(tz=MELBOURNE_TZ)
    start, end, count = get_user_schedule_settings(chat_id)
    slots = generate_daily_timestamps(now, start_hour=start, end_hour=end, daily_slots=count)
    fired: set[datetime] = set()
    save_schedule(chat_id, slots, fired)
    _schedule_user_day(app, send_card_fn, chat_id, slots, fired)


def wire_scheduler(app, send_card_fn) -> None:
    app.bot_data["send_card_fn"] = send_card_fn
    app.bot_data["today_slots"] = {}
    app.bot_data["today_fired"] = {}

    now = datetime.now(tz=MELBOURNE_TZ)
    for chat_id in get_allowed_chat_ids():
        slots, fired = load_today_schedule(chat_id)
        if slots is None:
            start, end, count = get_user_schedule_settings(chat_id)
            slots = generate_daily_timestamps(now, start_hour=start, end_hour=end, daily_slots=count)
            fired = set()
            save_schedule(chat_id, slots, fired)
        _schedule_user_day(app, send_card_fn, chat_id, slots, fired)

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
