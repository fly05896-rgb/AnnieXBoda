# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/plugins/AzanSystem/az_utils.py
# System: Azan Maestro Engine (Original Arabic Audio Track Enforced & Old Dubbed Cache Purged)

import asyncio
import functools
import glob
import logging
import os
import random
import re
from contextlib import suppress
from datetime import datetime
from typing import Any, Dict, Optional

import aiohttp
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import enums
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    UserAlreadyParticipant,
    UserNotParticipant,
)

# --- [ Internal Imports ] ---
from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import (
    add_active_chat,
    get_assistant,
    music_on,
    remove_active_video_chat,
)

# --- [ Configuration & Local DB ] ---
from .az_conf import (
    CURRENT_DUA_STICKER,
    CURRENT_RESOURCES,
    DEVS,
    MORNING_DUAS,
    NIGHT_DUAS,
    azan_logs_db,
    local_cache,
    resources_db,
    settings_db,
)

# --- [ Logging Setup ] ---
logging.basicConfig(
    format="%(asctime)s - [AzanEngine] - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Azan_Maestro_Stable")

# --- [ Constants ] ---
CAIRO_TZ = pytz.timezone("Africa/Cairo")
scheduler = AsyncIOScheduler(timezone=CAIRO_TZ)

# مسح أي ملفات أذان قديمة مدبلجة بالإنجليزية تم حفظها سابقاً في مجلد downloads
for _old_dubbed_file in glob.glob("downloads/azan_[A-Za-z]*_*.m4a"):
    if not os.path.basename(_old_dubbed_file).startswith("azan_orig_"):
        with suppress(Exception):
            os.remove(_old_dubbed_file)


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
    if not url:
        return None
    s = str(url).strip()
    if len(s) == 11 and not s.startswith("http") and " " not in s:
        return s
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([0-9A-Za-z_-]{11})", s)
    return match.group(1) if match else None


async def check_rights(user_id: int, chat_id: int) -> bool:
    if user_id in DEVS:
        return True
    try:
        mem = await app.get_chat_member(chat_id, user_id)
        return mem.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
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
                "prayers": {k: True for k in CURRENT_RESOURCES.keys()},
            }
            await settings_db.insert_one(doc)
        local_cache[chat_id] = doc
        return doc
    except Exception:
        return {}


async def update_doc(chat_id: int, key: str, value, sub_key: str = None):
    with suppress(Exception):
        if sub_key:
            await settings_db.update_one(
                {"chat_id": chat_id}, {"$set": {f"prayers.{sub_key}": value}}, upsert=True
            )
            if chat_id in local_cache:
                local_cache[chat_id].setdefault("prayers", {})[sub_key] = value
        else:
            await settings_db.update_one(
                {"chat_id": chat_id}, {"$set": {key: value}}, upsert=True
            )
            if chat_id in local_cache:
                local_cache[chat_id][key] = value


@retry_operation(max_retries=3)
async def load_resources():
    try:
        stored_res = await resources_db.find_one({"type": "azan_data"})
        if stored_res:
            for k, v in stored_res.get("data", {}).items():
                if k in CURRENT_RESOURCES and isinstance(v, dict):
                    CURRENT_RESOURCES[k].update(v)

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

async def prepare_assistant_membership(chat_id: int) -> bool:
    try:
        userbot = await get_assistant(chat_id)
        if not userbot:
            return False
        ass_id = getattr(userbot, "id", None) or (await userbot.get_me()).id
        ass_username = getattr(userbot, "username", None)

        try:
            member = await app.get_chat_member(chat_id, ass_id)
            if member.status not in (
                enums.ChatMemberStatus.BANNED,
                enums.ChatMemberStatus.RESTRICTED,
                enums.ChatMemberStatus.LEFT,
            ):
                return True
            if member.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
                with suppress(Exception):
                    await app.unban_chat_member(chat_id, ass_id)
        except UserNotParticipant:
            pass
        except Exception:
            return True

        if ass_username:
            with suppress(UserAlreadyParticipant, Exception):
                await app.add_chat_members(chat_id, ass_username)
                return True

        try:
            try:
                invite_link = await app.export_chat_invite_link(chat_id)
            except Exception:
                chat = await app.get_chat(chat_id)
                invite_link = f"https://t.me/{chat.username}" if chat.username else None

            if invite_link:
                if str(invite_link).startswith("https://t.me/+"):
                    invite_link = str(invite_link).replace("https://t.me/+", "https://t.me/joinchat/")
                with suppress(UserAlreadyParticipant):
                    await userbot.join_chat(str(invite_link))
                return True
        except Exception:
            return False
        return True
    except Exception as e:
        logger.error(f"Assistant Prep Error: {e}")
        return False


# ==================================================================
# [SECTION 3] Direct Stream & Local Cache Engine (100% Cookie-Free)
# ==================================================================

async def _cache_url_to_disk_bg(direct_url: str, target_path: str) -> None:
    try:
        if os.path.exists(target_path) and os.path.getsize(target_path) > 10240:
            return
        tmp_path = f"{target_path}.part"
        headers = YouTube.get_stream_headers(direct_url)
        session = await YouTube._get_session()
        async with session.get(direct_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status in (200, 206):
                data = await resp.read()
                if len(data) > 10240:
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    os.replace(tmp_path, target_path)
                    logger.info(f"Cached Original Azan audio on disk: {target_path}")
    except Exception as e:
        logger.debug(f"Background Azan cache skipped: {e}")


async def resolve_azan_media(prayer_key: str, play_target: Optional[str] = None) -> Optional[str]:
    """
    يستخرج المسار الصوتي العربي الأصلي للأذان (بدون الدبلجة الإنجليزية التلقائية)،
    ويدعم روابط youtu.be/watch?v=... و youtube.com/watch?v=... وملفات تيليجرام.
    """
    os.makedirs("downloads", exist_ok=True)
    res = CURRENT_RESOURCES.get(prayer_key) or {}
    raw_source = str(play_target or res.get("link") or "").strip()
    vidid = str(res.get("vidid") or "").strip()

    if not raw_source and not vidid:
        return None

    # 1. ملف محلي محفوظ مسبقاً
    if os.path.exists(raw_source) and os.path.getsize(raw_source) > 10240:
        return os.path.abspath(raw_source)

    # 2. ملف صوتي مرفوع على تيليجرام (file_id)
    is_tg_file = (
        vidid == "TelegramFile"
        or (not raw_source.startswith(("http://", "https://", "/")) and len(raw_source) > 20)
    )
    if is_tg_file:
        tg_cache_path = os.path.abspath(f"downloads/azan_tg_{prayer_key}_{raw_source[:12]}.ogg")
        if os.path.exists(tg_cache_path) and os.path.getsize(tg_cache_path) > 1024:
            return tg_cache_path
        try:
            downloaded = await app.download_media(raw_source, file_name=tg_cache_path)
            if downloaded and os.path.exists(downloaded):
                return os.path.abspath(downloaded)
        except Exception as e:
            logger.error(f"Failed to download custom Telegram Azan file_id: {e}")
            return None

    # 3. استخراج الـ Video ID من vidid أو الرابط، وحفظ النسخة العربية الأصلية باسم azan_orig_
    extracted_vid = (vidid if len(vidid) == 11 else None) or extract_vidid(raw_source)
    if extracted_vid and len(extracted_vid) == 11:
        old_dubbed = os.path.abspath(f"downloads/azan_{prayer_key}_{extracted_vid}.m4a")
        if os.path.exists(old_dubbed):
            with suppress(Exception):
                os.remove(old_dubbed)

        local_yt_cache = os.path.abspath(f"downloads/azan_orig_{prayer_key}_{extracted_vid}.m4a")
        if os.path.exists(local_yt_cache) and os.path.getsize(local_yt_cache) > 10240:
            return local_yt_cache

        YouTube.invalidate_cache(extracted_vid)
        res_dl = await YouTube.download(
            extracted_vid,
            None,
            video=False,
            videoid=True,
        )
        direct_url = res_dl[0] if isinstance(res_dl, tuple) else res_dl
        if direct_url:
            asyncio.create_task(_cache_url_to_disk_bg(direct_url, local_yt_cache))
            return direct_url

    # 4. رابط صوتي خارجي مباشر
    if raw_source.startswith(("http://", "https://")):
        return raw_source

    return None


async def _direct_join_call(chat_id: int, playable_path: str) -> bool:
    try:
        await StreamController.join_call(chat_id, chat_id, playable_path, video=False)
        return True
    except Exception as e1:
        logger.debug(f"join_call fallback to skip_stream for {chat_id}: {e1}")
        try:
            await StreamController.skip_stream(chat_id, playable_path, video=False)
            return True
        except Exception as e2:
            logger.error(f"Azan Call Join Failed in {chat_id}: {e2}")
            return False


async def start_azan_stream(
    chat_id: int,
    prayer_key: str,
    play_target: str = None,
    force_test: bool = False,
):
    if not force_test:
        doc = await get_chat_doc(chat_id)
        if not doc.get("azan_active", True) or not doc.get("prayers", {}).get(prayer_key, True):
            return

    try:
        res = CURRENT_RESOURCES.get(prayer_key)
        if not res:
            return

        playable_file = await resolve_azan_media(prayer_key, play_target)
        if not playable_file:
            if force_test:
                with suppress(Exception):
                    await app.send_message(chat_id, "❌ تعذر استخراج الرابط الصوتي للأذان.")
            return

        await prepare_assistant_membership(chat_id)

        db[chat_id] = [
            {
                "vidid": res.get("vidid") or "adhan_local",
                "title": f"أذان {res.get('name')}",
                "duration": "04:00",
                "streamtype": "audio",
                "by": "AzanSystem",
                "user_id": 777,
                "chat_id": chat_id,
                "file": playable_file,
                "markup": "adhan",
                "mystic": None,
                "seconds": 240,
                "played": 0,
                "dur": "04:00",
                "speed": 1.0,
                "stream_mode": "local",
            }
        ]

        await add_active_chat(chat_id)
        await remove_active_video_chat(chat_id)
        await music_on(chat_id)

        success = await _direct_join_call(chat_id, playable_file)

        if res.get("sticker"):
            with suppress(Exception):
                await app.send_sticker(chat_id, res["sticker"])

        caption = f"🕌 حان الان موعد اذان {res.get('name', '')} بالتوقيت المحلي لمدينة القاهرة."
        with suppress(Exception):
            await app.send_message(chat_id, caption)

        if not success and force_test:
            with suppress(Exception):
                await app.send_message(
                    chat_id,
                    "⚠️ تم إرسال تنبيه الأذان، ولكن تعذر دخول المكالمة الصوتية (تأكد أن المكالمة مفتوحة وأن المساعد مشرف).",
                )

        if not force_test and success:
            with suppress(Exception):
                now = datetime.now(CAIRO_TZ)
                log_key = f"{chat_id}_{now.strftime('%Y-%m-%d_%H:%M')}"
                await azan_logs_db.insert_one(
                    {
                        "chat_id": chat_id,
                        "key": log_key,
                        "prayer_key": prayer_key,
                        "time": now.strftime("%I:%M %p"),
                    }
                )

    except Exception as e:
        logger.error(f"Azan Stream Failed {chat_id}: {e}")
        if force_test:
            with suppress(Exception):
                await app.send_message(chat_id, f"خطأ: {e}")


# ==================================================================
# [SECTION 4] Broadcaster & Scheduler
# ==================================================================

async def broadcast_azan(prayer_key: str):
    logger.info(f"STARTING AZAN BROADCAST: {prayer_key}")
    res = CURRENT_RESOURCES.get(prayer_key)
    if not res:
        return

    playable_media = await resolve_azan_media(prayer_key)
    if not playable_media:
        logger.error(f"Failed to resolve Azan media for {prayer_key}")
        return

    tasks = []
    async for doc in settings_db.find({"azan_active": True}):
        if c_id := doc.get("chat_id"):
            tasks.append(start_azan_stream(c_id, prayer_key, playable_media))

    if tasks:
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
        session = await YouTube._get_session()
        url = "https://api.aladhan.com/v1/timingsByCity"
        params = {"city": "Cairo", "country": "Egypt", "method": "5"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["data"]["timings"]
    except Exception as e:
        logger.error(f"get_azan_times error: {e}")
    return None


async def update_scheduler():
    await load_resources()
    times = await get_azan_times()
    if not times:
        return

    for job in scheduler.get_jobs():
        if str(job.id).startswith("azan_"):
            job.remove()

    for key in CURRENT_RESOURCES.keys():
        if key in times:
            t_str = times[key].split(" ")[0]
            try:
                h, m = map(int, t_str.split(":"))
                scheduler.add_job(
                    broadcast_azan,
                    "cron",
                    hour=h,
                    minute=m,
                    args=[key],
                    id=f"azan_{key}",
                    replace_existing=True,
                )
            except Exception:
                continue
    logger.info("Scheduler Updated with new times.")


def init_azan_scheduler():
    if not scheduler.running:
        scheduler.add_job(update_scheduler, "cron", hour=0, minute=5, id="azan_daily_update", replace_existing=True)
        scheduler.add_job(
            send_duas_batch,
            "cron",
            hour=7,
            minute=0,
            args=[MORNING_DUAS, "dua_active", "أذكار الصباح"],
            id="azan_morning_duas",
            replace_existing=True,
        )
        scheduler.add_job(
            send_duas_batch,
            "cron",
            hour=20,
            minute=0,
            args=[NIGHT_DUAS, "night_dua_active", "أذكار المساء"],
            id="azan_night_duas",
            replace_existing=True,
        )
        scheduler.start()

        with suppress(Exception):
            loop = asyncio.get_running_loop()
            loop.create_task(update_scheduler())
