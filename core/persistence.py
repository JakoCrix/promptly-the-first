import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from core.vocab import _get_conn

_MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


def load_today_schedule() -> tuple[list[datetime], set[datetime]] | tuple[None, None]:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT slot_time, fired FROM schedule WHERE date = ?",
            (today,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None, None

    slots = [datetime.fromisoformat(r["slot_time"]) for r in rows]
    fired = {datetime.fromisoformat(r["slot_time"]) for r in rows if r["fired"]}
    return slots, fired


def save_schedule(slots: list[datetime], fired: set[datetime]) -> None:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    fired_set = set(fired)
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM schedule WHERE date = ?", (today,))
        conn.executemany(
            "INSERT INTO schedule (date, slot_time, fired) VALUES (?, ?, ?)",
            [(today, ts.isoformat(), 1 if ts in fired_set else 0) for ts in slots],
        )
        conn.commit()
    finally:
        conn.close()


def mark_slot_fired(ts: datetime) -> None:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE schedule SET fired = 1 WHERE date = ? AND slot_time = ?",
            (today, ts.isoformat()),
        )
        conn.commit()
    except Exception:
        logging.exception("Failed to mark slot fired in DB: %s", ts)
    finally:
        conn.close()
