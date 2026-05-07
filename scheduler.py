import random
from datetime import datetime, time, timedelta

from config import ALLOWED_CHAT_IDS, DAILY_SLOTS, WINDOW_END_HOUR, WINDOW_START_HOUR


def generate_daily_timestamps(reference_date: datetime | None = None) -> list[datetime]:
    """
    Return DAILY_SLOTS unique datetimes spread across [WINDOW_START_HOUR, WINDOW_END_HOUR)
    on the same calendar day as reference_date (defaults to today).

    Timestamps are sorted ascending so notifications arrive in a natural order.
    """
    if reference_date is None:
        reference_date = datetime.now()

    day_start = reference_date.replace(hour=0, minute=0, second=0, microsecond=0)

    window_open = WINDOW_START_HOUR * 3600   # seconds since midnight
    window_close = WINDOW_END_HOUR * 3600

    offsets = random.sample(range(window_open, window_close), DAILY_SLOTS)
    offsets.sort()

    return [day_start + timedelta(seconds=s) for s in offsets]


async def _midnight_callback(context) -> None:
    send_card_fn = context.application.bot_data["send_card_fn"]
    now = datetime.now()
    slots = generate_daily_timestamps(now)
    for ts in slots:
        for chat_id in ALLOWED_CHAT_IDS:
            context.job_queue.run_once(
                send_card_fn,
                when=ts,
                chat_id=chat_id,
                name=f"card_{chat_id}_{ts.isoformat()}",
            )


def wire_scheduler(app, send_card_fn) -> None:
    app.bot_data["send_card_fn"] = send_card_fn

    now = datetime.now()
    for ts in generate_daily_timestamps(now):
        if ts > now:
            for chat_id in ALLOWED_CHAT_IDS:
                app.job_queue.run_once(
                    send_card_fn,
                    when=ts,
                    chat_id=chat_id,
                    name=f"card_{chat_id}_{ts.isoformat()}",
                )

    app.job_queue.run_daily(
        _midnight_callback,
        time=time(0, 0, 0),
        name="midnight_regen",
    )


# Quick sanity check: python scheduler.py
if __name__ == "__main__":
    slots = generate_daily_timestamps()
    print(f"Today's {len(slots)} notification slots:")
    for ts in slots:
        print(f"  {ts.strftime('%H:%M:%S')}")
