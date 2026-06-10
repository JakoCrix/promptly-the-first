from datetime import datetime
from zoneinfo import ZoneInfo

from core.vocab import _get_conn

_MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


def load_today_schedule(chat_id: int) -> tuple[list[datetime], set[datetime]] | tuple[None, None]:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT slot_time, fired FROM schedule WHERE chat_id = ? AND date = ?",
            (chat_id, today),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None, None

    slots = [datetime.fromisoformat(r["slot_time"]) for r in rows]
    fired = {datetime.fromisoformat(r["slot_time"]) for r in rows if r["fired"]}
    return slots, fired


def save_schedule(chat_id: int, slots: list[datetime], fired: set[datetime]) -> None:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    fired_set = set(fired)
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM schedule WHERE chat_id = ? AND date = ?", (chat_id, today))
        conn.executemany(
            "INSERT INTO schedule (chat_id, date, slot_time, fired) VALUES (?, ?, ?, ?)",
            [(chat_id, today, ts.isoformat(), 1 if ts in fired_set else 0) for ts in slots],
        )
        conn.commit()
    finally:
        conn.close()


def mark_slot_fired(chat_id: int, ts: datetime) -> None:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE schedule SET fired = 1 WHERE chat_id = ? AND date = ? AND slot_time = ?",
            (chat_id, today, ts.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
