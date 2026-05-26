import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.config import ALLOWED_CHAT_IDS, DAILY_SLOTS, WINDOW_END_HOUR, WINDOW_START_HOUR
from core.persistence import load_today_schedule, save_schedule

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


async def _midnight_callback(context) -> None:
    send_card_fn = context.application.bot_data["send_card_fn"]
    now = datetime.now(tz=MELBOURNE_TZ)
    slots = generate_daily_timestamps(now)
    fired: set[datetime] = set()
    save_schedule(slots, fired)
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
