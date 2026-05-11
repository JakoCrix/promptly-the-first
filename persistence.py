import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import DATA_DIR

_MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
_SCHEDULE_FILE = Path(DATA_DIR) / "schedule.json"


def load_today_schedule() -> tuple[list[datetime], set[datetime]] | tuple[None, None]:
    """Return (slots, fired) for today, or (None, None) if file is missing or from a different day."""
    try:
        data = json.loads(_SCHEDULE_FILE.read_text())
    except FileNotFoundError:
        return None, None
    except json.JSONDecodeError:
        logging.warning("schedule.json is malformed — regenerating today's schedule")
        return None, None
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    if data.get("date") != today:
        return None, None
    slots = [datetime.fromisoformat(s) for s in data["slots"]]
    fired = {datetime.fromisoformat(s) for s in data.get("fired", [])}
    return slots, fired


def save_schedule(slots: list[datetime], fired: set[datetime]) -> None:
    today = datetime.now(tz=_MELBOURNE_TZ).date().isoformat()
    payload = json.dumps({
        "date": today,
        "slots": [ts.isoformat() for ts in slots],
        "fired": [ts.isoformat() for ts in fired],
    }, indent=2)
    tmp = str(_SCHEDULE_FILE) + ".tmp"
    with open(tmp, "w") as f:
        f.write(payload)
    os.replace(tmp, _SCHEDULE_FILE)


def mark_slot_fired(ts: datetime) -> None:
    """Add ts to the fired set on disk. No-op if the file is missing or from a different day."""
    slots, fired = load_today_schedule()
    if slots is None:
        return
    fired.add(ts)
    save_schedule(slots, fired)
