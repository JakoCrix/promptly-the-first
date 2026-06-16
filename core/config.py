import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_CHAT_IDS = [int(x) for x in os.environ.get("CHAT_IDS", "").split(",") if x]
INVITE_CODE = os.environ.get("INVITE_CODE", "")
VOCAB_TOPIC = os.environ.get("VOCAB_TOPIC", "vocab")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Minimum retired words needed before asking Gemini to weave them into a sentence.
# Below this, the prompt stays single-word to avoid thin or awkward context.
MIN_RETIRED_FOR_WEAVING = 3

WINDOW_START_HOUR = 9    # notifications begin at 09:00
WINDOW_END_HOUR = 23     # notifications end at 23:00
DAILY_SLOTS = 10
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "promptly.db")

# Words at or above this mastery score are retired (excluded from selection)
RETIREMENT_THRESHOLD = 10

# Synthetic elapsed seconds assigned to words never seen before (7 days)
# Gives unseen words a high initial weight without special-casing them
NEVER_SEEN_SECONDS = 7 * 24 * 3600

def require_bot_token() -> str:
    """Call in bot.py at startup — not at import time, so data scripts work without a token."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set — add it to your .env file")
    return BOT_TOKEN
