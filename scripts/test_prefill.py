"""
Pre-generate sentences for today's word buffer for all users and store them in sentence_cache.
Runs immediately — bypasses the midnight schedule.

Usage:
    python scripts/test_prefill.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import ALLOWED_CHAT_IDS
from core.scheduler import _prefill_sentence_cache
from core.vocab import init_db

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    if not ALLOWED_CHAT_IDS:
        print("ERROR: CHAT_IDS is not set in .env")
        return
    init_db()
    await _prefill_sentence_cache()


if __name__ == "__main__":
    asyncio.run(main())
