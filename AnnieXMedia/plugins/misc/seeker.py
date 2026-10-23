# Authored By Certified Coders © 2026
# System: Seeker Timer & Auto-Advance Watchdog (Prevents Stuck Ended Songs)

import asyncio
from contextlib import suppress

from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import get_active_chats, is_music_playing


async def timer():
    while not await asyncio.sleep(1):
        active_chats = list(await get_active_chats())
        for chat_id in active_chats:
            with suppress(Exception):
                if not await is_music_playing(chat_id):
                    continue
                playing = db.get(chat_id)
                if not playing:
                    continue
                duration = int(playing[0].get("seconds") or 0)
                if duration == 0:
                    continue

                db[chat_id][0]["played"] += 1

                # 🔥 إذا انتهى وقت الأغنية الفعلي (+4 ثوانٍ سماحية) ولم ينطلق حدث StreamEnded،
                # يقوم الحارس بنقل الطابور للأغنية التالية أو تنظيف الكول تلقائياً!
                if db[chat_id][0]["played"] >= duration + 4:
                    asyncio.create_task(StreamController.check_and_advance(chat_id))


asyncio.create_task(timer())
