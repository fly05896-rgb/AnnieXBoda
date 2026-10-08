import asyncio
import aiohttp
import random
import logging
import pytz
import re
import os
import functools
import shlex  # للحماية من ثغرات الحقن
from contextlib import suppress # لمعالجة الأخطاء بصمت واحترافية
from datetime import datetime
from typing import Optional, Dict, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import enums
from pyrogram.errors import (
    FloodWait,
    UserNotParticipant,
    ChatAdminRequired,
    UserAlreadyParticipant
)

# --- [ Internal Imports ] ---
from AnnieXMedia import app, YouTube
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.utils.database import get_client, add_active_video_chat
from AnnieXMedia.misc import db
from AnnieXMedia.core.userbot import assistants

# --- [ Configuration & Local DB ] ---
from .az_conf import (
    settings_db,
    resources_db,
    azan_logs_db,
    local_cache,
    CURRENT_RESOURCES,
    CURRENT_DUA_STICKER,
    DEVS,
    MORNING_DUAS,
    NIGHT_DUAS
)

# --- [ Logging Setup ] ---
logging.basicConfig(
    format='%(asctime)s - [AzanEngine] - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("Azan_Maestro_Stable")

# --- [ Constants ] ---
CAIRO_TZ = pytz.timezone('Africa/Cairo')
scheduler = AsyncIOScheduler(timezone=CAIRO_TZ)

# ==================================================================
# [SECTION 1] Database & Rights Helpers
# ==================================================================

def retry_operation(max_retries=3, delay=2):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
            logger.error(f"Function {func.__name__} failed: {last_exc}")
            raise last_exc
        return wrapper
    return decorator

def extract_vidid(url: str) -> Optional[str]:
    if not url: return None
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None

async def check_rights(user_id: int, chat_id: int) -> bool:
    if user_id in DEVS: return True
    try:
        mem = await app.get_chat_member(chat_id, user_id)
        return mem.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception: 
        return False

async def get_chat_doc(chat_id: int) -> Dict[str, Any]:
    if chat_id in local_cache:
        return local_cache[chat_id]
    try:
        doc = await settings_db.find_one({"chat_id": chat_id})
        if not doc:
            doc = {
                "chat_id": chat_id,
                "azan_active": True,
                "dua_active": True,
                "night_dua_active": True,
                "prayers": {k: True for k in CURRENT_RESOURCES.keys()}
            }
            await settings_db.insert_one(doc)
        local_cache[chat_id] = doc
        return doc
    except Exception:
        return {}

async def update_doc(chat_id: int, key: str, value, sub_key: str = None):
    with suppress(Exception):
        if sub_key:
            await settings_db.update_one({"chat_id": chat_id}, {"$set": {f"prayers.{sub_key}": value}}, upsert=True)
            if chat_id in local_cache: local_cache[chat_id].setdefault("prayers", {})[sub_key] = value
        else:
            await settings_db.update_one({"chat_id": chat_id}, {"$set": {key: value}}, upsert=True)
            if chat_id in local_cache: local_cache[chat_id][key] = value

@retry_operation(max_retries=3)
async def load_resources():
    try:
        stored_res = await resources_db.find_one({"type": "azan_data"})
        if stored_res:
            for k, v in stored_res.get("data", {}).items():
                if k in CURRENT_RESOURCES: CURRENT_RESOURCES[k].update(v)
        
        dua_res = await resources_db.find_one({"type": "dua_sticker"})
        if dua_res:
            global CURRENT_DUA_STICKER
            CURRENT_DUA_STICKER = dua_res.get("sticker_id")
        logger.info("Resources Loaded.")
    except Exception as e:
        logger.error(f"Resource Load Error: {e}")

# ==================================================================
# [SECTION 2] Smart Assistant Prep
# ==================================================================

async def prepare_assistant_membership(chat_id: int):
    try:
        userbot = await get_client(random.choice(assistants))
        me = await userbot.get_me() # تأمين قراءة بيانات الحساب
        
        with suppress(UserAlreadyParticipant, Exception):
            await app.add_chat_members(chat_id, me.username)
            return True

        try:
            await userbot.get_chat_member(chat_id, me.id)
            return True 
        except UserNotParticipant:
            try:
                try:
                    invite_link = await app.export_chat_invite_link(chat_id)
                except Exception:
                    chat = await app.get_chat(chat_id)
                    invite_link = chat.username

                if invite_link:
                    if "+" in str(invite_link): 
                        await userbot.join_chat(invite_link)
                    else: 
                        await userbot.join_chat(str(invite_link))
                    await asyncio.sleep(1)
                    return True
            except Exception:
                return False
        except Exception:
            return True
    except Exception as e:
        logger.error(f"Assistant Prep Error: {e}")
        return False

# ==================================================================
# [SECTION 3] Direct Stream Engine
# ==================================================================

async def _download_locally_guaranteed(url: str, filename_prefix: str) -> str:
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    path = f"downloads/{filename_prefix}.mp3"
    
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    
    try:
        # حماية ضد Shell Injection باستخدام shlex.quote
        safe_url = shlex.quote(url)
        safe_path = shlex.quote(path)
        proc = await asyncio.create_subprocess_shell(
            f"yt-dlp -f 'bestaudio' -o {safe_path} {safe_url}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(path):
            return path
    except Exception: 
        pass

    try:
        if "youtube" not in url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        with open(path, 'wb') as f:
                            f.write(await resp.read())
                        return path
    except Exception as e:
        logger.error(f"Manual download failed: {e}")
    
    return url

async def _direct_join_call(chat_id, file_path):
    try:
        await StreamController.skip_stream(chat_id, file_path, video=False)
        return True
    except Exception:
        pass

    candidates = ["join_call", "join_stream", "join", "start_stream", "start_call"]
    for name in candidates:
        fn = getattr(StreamController, name, None)
        if callable(fn):
            try:
                res = fn(chat_id, chat_id, file_path, video=False)
                if asyncio.iscoroutine(res): await res
                return True
            except Exception:
                continue
    return False

async def start_azan_stream(chat_id: int, prayer_key: str, play_target: str = None, force_test: bool = False):
    if not force_test:
        doc = await get_chat_doc(chat_id)
        if not doc.get("azan_active", True) or not doc.get("prayers", {}).get(prayer_key, True):
            return

    try:
        res = CURRENT_RESOURCES.get(prayer_key)
        if not res: return
        
        final_file = play_target if play_target else res.get("link")
        if not final_file: return

        db[chat_id] = []
        await add_active_video_chat(chat_id)
        
        db[chat_id].append({
            "vidid": "adhan_local",
            "title": f"أذان {res.get('name')}",
            "duration": "04:00",
            "streamtype": "audio",
            "by": "AzanSystem",
            "user_id": 777,
            "chat_id": chat_id,
            "file": final_file, 
            "markup": "adhan", 
            "mystic": None,
            "seconds": 240,
            "played": 0,    
            "dur": "04:00",
            "speed": 1.0,
            "stream_mode": "local"
        })

        await prepare_assistant_membership(chat_id)
        success = await _direct_join_call(chat_id, final_file)
        
        if not success:
             return

        if res.get("sticker"):
            with suppress(Exception):
                await app.send_sticker(chat_id, res["sticker"])
        
        caption = f"🕌 حان الان موعد اذان {res.get('name', '')} بالتوقيت المحلي لمدينة القاهرة."
        with suppress(Exception):
            await app.send_message(chat_id, caption)

        if not force_test:
            with suppress(Exception):
                now = datetime.now(CAIRO_TZ)
                log_key = f"{chat_id}_{now.strftime('%Y-%m-%d_%H:%M')}"
                await azan_logs_db.insert_one({
                    "chat_id": chat_id,
                    "key": log_key,
                    "prayer_key": prayer_key,
                    "time": now.strftime("%I:%M %p")
                })

    except Exception as e:
        logger.error(f"Azan Stream Failed {chat_id}: {e}")
        if force_test: 
            with suppress(Exception):
                await app.send_message(chat_id, f"خطأ: {e}")

# ==================================================================
# [SECTION 4] Broadcaster & Scheduler (Optimized for Concurrency)
# ==================================================================

async def broadcast_azan(prayer_key: str):
    logger.info(f"STARTING AZAN BROADCAST: {prayer_key}")
    res = CURRENT_RESOURCES.get(prayer_key)
    if not res: return
    
    link = res["link"]
    local_file_path = link

    try:
        logger.info("Downloading Azan file locally...")
        local_file_path = await _download_locally_guaranteed(link, f"azan_{prayer_key}")
        logger.info(f"Broadcast File Ready: {local_file_path}")
    except Exception as e:
        logger.error(f"Download failed: {e}")

    # استخدام التزامن (Concurrency) لضمان دخول البوت كل المجموعات في نفس اللحظة
    tasks = []
    async for doc in settings_db.find({"azan_active": True}):
        if c_id := doc.get("chat_id"):
            tasks.append(start_azan_stream(c_id, prayer_key, local_file_path))
    
    if tasks:
        # ننفذ كل المهام معاً. لن ننتظر مجموعة تلو الأخرى.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        count = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"Azan Broadcast Finished for {count} chats simultaneously.")

async def send_duas_batch(dua_list, setting_key, title, target_chat_id=None):
    selected = random.sample(dua_list, min(4, len(dua_list)))
    text = f"<b>{title}</b>\n\n" + "\n\n".join([f"• {d} 🤍" for d in selected])
    text += "\n\n<b>تقبل الله منا ومنكم</b>"

    async def send_single_dua(c_id):
        with suppress(Exception):
            if CURRENT_DUA_STICKER:
                await app.send_sticker(c_id, CURRENT_DUA_STICKER)
            await app.send_message(c_id, text)

    if target_chat_id:
        await send_single_dua(target_chat_id)
        return

    # استخدام التزامن لإرسال الأذكار
    tasks = []
    async for entry in settings_db.find({setting_key: True}):
        if c_id := entry.get("chat_id"):
            tasks.append(send_single_dua(c_id))
            
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

# ==================================================================
# [SECTION 5] Init & Updates
# ==================================================================

async def get_azan_times() -> Optional[Dict[str, str]]:
    try:
        async with aiohttp.ClientSession() as session:
            url = "http://api.aladhan.com/v1/timingsByCity"
            params = {"city": "Cairo", "country": "Egypt", "method": "5"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["data"]["timings"]
    except Exception: 
        return None

async def update_scheduler():
    await load_resources()
    times = await get_azan_times()
    if not times: return
    
    for job in scheduler.get_jobs():
        if str(job.id).startswith("azan_"): job.remove()
        
    for key in CURRENT_RESOURCES.keys():
        if key in times:
            t_str = times[key].split(" ")[0]
            try:
                h, m = map(int, t_str.split(":"))
                scheduler.add_job(broadcast_azan, "cron", hour=h, minute=m, args=[key], id=f"azan_{key}")
            except Exception: 
                continue
    logger.info("Scheduler Updated with new times.")

def init_azan_scheduler():
    if not scheduler.running:
        scheduler.add_job(update_scheduler, "cron", hour=0, minute=5)
        scheduler.add_job(send_duas_batch, "cron", hour=7, minute=0, args=[MORNING_DUAS, "dua_active", "أذكار الصباح"])
        scheduler.add_job(send_duas_batch, "cron", hour=20, minute=0, args=[NIGHT_DUAS, "night_dua_active", "أذكار المساء"])
        scheduler.start()
        
        with suppress(Exception):
            asyncio.get_event_loop().create_task(update_scheduler())
