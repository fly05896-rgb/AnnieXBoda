# Authored By Certified Coders © 2026
# System: Main Entry Point (100% Cookie-Free Mode)

import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path

if os.environ.get("DEBUG_NTG") == "1":
    logging.basicConfig(
        format="[%(levelname) 4s/%(asctime)s] %(name)s: %(message)s",
    )
    logging.getLogger("ntgcalls").setLevel(logging.DEBUG)
    print("🚨 تم تفعيل وضع الـ DEBUG لمكتبة ntgcalls بناءً على طلبك من الكونسول 🚨")

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import idle

sys.path.insert(0, os.getcwd())

import config
from config import BANNED_USERS
from AnnieXMedia import LOGGER, app, userbot
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import sudo
from AnnieXMedia.plugins import ALL_MODULES
from AnnieXMedia.utils.database import get_banned_users, get_gbanned


async def init():
    LOGGER("AnnieXMedia").info("🚀 Starting Annie Music Bot (100% Cookie-Free Mode)...")

    if not any([config.STRING1, config.STRING2, config.STRING3, config.STRING4, config.STRING5]):
        LOGGER(__name__).error("❌ Assistant session not filled, please fill a Pyrogram session...")
        sys.exit()

    # مسح أي ملف كوكيز قديم من السيرفر لضمان عدم استخدامه نهائياً
    try:
        legacy_cookie = Path("AnnieXMedia/assets/cookies.txt")
        if legacy_cookie.exists():
            legacy_cookie.unlink()
    except Exception:
        pass

    await sudo()
    try:
        users = await get_gbanned()
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await get_banned_users()
        for user_id in users:
            BANNED_USERS.add(user_id)
    except Exception:
        pass

    await app.start()
    for all_module in ALL_MODULES:
        importlib.import_module("AnnieXMedia.plugins" + all_module)
    LOGGER("AnnieXMedia.plugins").info("✅ Modules Loaded Successfully.")

    await userbot.start()
    await StreamController.start()

    await StreamController.decorators()
    LOGGER("AnnieXMedia").info("✅ Annie Music Bot Started Successfully (Zero-Cookies Engine).")

    await idle()

    await app.stop()
    await userbot.stop()
    LOGGER("AnnieXMedia").info("🛑 Stopping Annie Music Bot...")


if __name__ == "__main__":
    try:
        loop.run_until_complete(init())
    except KeyboardInterrupt:
        LOGGER("AnnieXMedia").info("🛑 Bot process killed by user (Ctrl+C).")
    except Exception as e:
        LOGGER("AnnieXMedia").error(f"⚠️ Fatal Error Occurred: {e}")
