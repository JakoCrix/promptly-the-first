"""
Verify bot token and chat IDs are configured correctly.
Sends a plain text message to every ALLOWED_CHAT_ID.

Usage:
    python scripts/test_connection.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Bot
from telegram.error import TelegramError

from core.config import ALLOWED_CHAT_IDS, BOT_TOKEN


async def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set in .env")
        return
    if not ALLOWED_CHAT_IDS:
        print("ERROR: CHAT_IDS is not set in .env")
        return

    async with Bot(BOT_TOKEN) as bot:
        me = await bot.get_me()
        print(f"Connected as @{me.username} ({me.full_name})")

        for chat_id in ALLOWED_CHAT_IDS:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="<b>Promptly test</b>\n\nBot is connected and can reach this chat.",
                    parse_mode="HTML",
                )
                print(f"  ✓ Sent to {chat_id}")
            except TelegramError as e:
                print(f"  ✗ Failed for {chat_id}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
