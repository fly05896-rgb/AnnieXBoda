import sys
import os
import asyncio
import logging

# 🛠️ إضافة المطور: تشتغل فقط عند الطلب من الكونسول
if os.environ.get("DEBUG_NTG") == "1":
    logging.basicConfig(
        format="[%(levelname) 4s/%(asctime)s] %(name)s: %(message)s",
    )
    logging.getLogger('ntgcalls').setLevel(logging.DEBUG)
    print("🚨 تم تفعيل وضع الـ DEBUG لمكتبة ntgcalls بناءً على طلبك من الكونسول 🚨")

# 🚀 الطريقة الصحيحة لمنع تعارض الـ Event Loop مع MongoDB و Pyromod
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import importlib
from pyrogram import idle
from pytgcalls.exceptions import NoActiveGroupCall

sys.path.insert(0, os.getcwd())

import config
from AnnieXMedia import LOGGER, app, userbot
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import sudo
from AnnieXMedia.plugins import ALL_MODULES
from AnnieXMedia.utils.database import get_banned_users, get_gbanned
from AnnieXMedia.utils.cookie_handler import fetch_and_store_cookies
from config import BANNED_USERS


async def init():
    LOGGER("AnnieXMedia").info("🚀 Starting Annie Music Bot with Synchronized Event Loop...")

    if not any([config.STRING1, config.STRING2, config.STRING3, config.STRING4, config.STRING5]):
        LOGGER(__name__).error("❌ Assistant session not filled, please fill a Pyrogram session...")
        sys.exit()

    try:
        await fetch_and_store_cookies()
        LOGGER("AnnieXMedia").info("✅ YouTube Cookies Loaded Successfully.")
    except Exception as e:
        LOGGER("AnnieXMedia").warning(f"⚠️ Cookie Error: {e}")

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
    LOGGER("AnnieXMedia").info("✅ Annie Music Bot Started Successfully.")
    
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
