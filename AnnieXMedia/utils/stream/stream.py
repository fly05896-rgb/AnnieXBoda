# Authored By Certified Coders © 2026
# System: Stream Controller (Logic & Queue Bridge)
# Updated: Python 3.14 Native, 10-Core Fly.io API Compatible, PEP 604

import asyncio
import contextlib
import traceback
from random import randint

from pyrogram.types import InlineKeyboardMarkup

import config
from AnnieXMedia import Carbon, YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import (
    add_active_video_chat,
    remove_active_video_chat,
    is_active_chat,
)
from AnnieXMedia.utils.exceptions import AssistantErr
from AnnieXMedia.utils.inline import aq_markup, close_markup, stream_markup
from AnnieXMedia.utils.inline.custom import custom_markup
from AnnieXMedia.utils.pastebin import ANNIEBIN
from AnnieXMedia.utils.stream.queue import put_queue, put_queue_index
from AnnieXMedia.utils.errors import capture_internal_err


async def safe_delete(message) -> None:
    """Safely deletes a message without throwing errors."""
    with contextlib.suppress(Exception):
        if message:
            await message.delete()


async def _sync_video_state(chat_id: int, is_video: bool) -> None:
    """Syncs the video chat state in the database."""
    with contextlib.suppress(Exception):
        if is_video:
            await add_active_video_chat(chat_id)
        else:
            await remove_active_video_chat(chat_id)


@capture_internal_err
async def stream(
    _,
    mystic,
    user_id: int,
    result: dict | str | list,
    chat_id: int,
    user_name: str,
    original_chat_id: int,
    video: bool | str | None = None,
    streamtype: str | None = None,
    spotify: bool | str | None = None,
    forceplay: bool | str | None = None,
) -> None:
    
    if not result:
        return

    forceplay = bool(forceplay)
    is_video = bool(video)

    if forceplay:
        await StreamController.force_stop_stream(chat_id)

    def get_download_id(vid: str) -> str:
        return f"{vid}_v" if is_video else str(vid)

    # ==========================
    # Structural Pattern Matching (Python 3.10+ / Highly Optimized in 3.13+)
    # ==========================
    match streamtype:
        
        # --------------------------
        # 1. CUSTOM MODE (Inline File Playback)
        # --------------------------
        case "custom":
            link = result.get("link", "")
            vidid = result.get("vidid", "")
            title = result.get("title", "").title()
            duration_min = result.get("duration_min", "00:00")

            is_active = await is_active_chat(chat_id)

            # 🔴 التحديث: إضافة احترام الطابور (Queue) لوضع الملفات المرفوعة
            if is_active and not forceplay:
                await put_queue(
                    chat_id,
                    original_chat_id,
                    f"vid_{vidid}",
                    title,
                    duration_min,
                    user_name,
                    vidid,
                    user_id,
                    "video" if is_video else "audio",
                )
                position = len(db.get(chat_id)) - 1
                button = aq_markup(_, chat_id)
                try:
                    await mystic.edit_text(
                        text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                except Exception:
                    await safe_delete(mystic)
                    await app.send_message(
                        original_chat_id,
                        text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                return

            if not forceplay:
                db[chat_id] = []

            try:
                # 🚀 استلام قيمة واحدة (رابط مباشر) من سيرفر Fly.io
                file_path = await YouTube.download(
                    vidid, None, video=is_video, videoid=get_download_id(vidid)
                )
                direct = True
            except Exception as e:
                print(f"\n🚨 [CUSTOM MODE] Download Error: {type(e).__name__}")
                traceback.print_exc()
                return await app.send_message(original_chat_id, text=_["play_14"])

            if not file_path:
                return await app.send_message(original_chat_id, text=_["play_14"])

            try:
                await StreamController.join_call(chat_id, original_chat_id, file_path, video=is_video)
            except AssistantErr as e:
                return await app.send_message(original_chat_id, text=str(e))
            except Exception as e:
                print(f"\n🚨 [CUSTOM MODE] Join Call Error: {type(e).__name__}")
                traceback.print_exc()
                return await app.send_message(original_chat_id, text=f"Error: [{type(e).__name__}] {e}")

            await put_queue(
                chat_id,
                original_chat_id,
                file_path if direct else f"vid_{vidid}",
                title,
                duration_min,
                user_name,
                vidid,
                user_id,
                "video" if is_video else "audio",
                forceplay=True,
            )

            button = custom_markup(_, chat_id, vidid)
            try:
                await mystic.edit_reply_markup(reply_markup=button)
            except Exception:
                await app.send_message(original_chat_id, text="✅ ᴘʟᴀʏɪɴɢ", reply_markup=button)

            if db.get(chat_id):
                db[chat_id][0]["mystic"] = mystic
                db[chat_id][0]["markup"] = "custom"

            await _sync_video_state(chat_id, is_video)
            return

        # --------------------------
        # 2. PLAYLIST MODE
        # --------------------------
        case "playlist":
            msg = f"{_['play_19']}\n\n"
            count = 0
            
            # 🔴 التحديث: منع تكرار تفريغ الطابور أثناء تحميل القائمة لمعالجة ثغرة الـ Race Condition
            is_active = await is_active_chat(chat_id)
            if not is_active and not forceplay:
                db[chat_id] = []

            for search in result:
                if count == config.PLAYLIST_FETCH_LIMIT:
                    break

                try:
                    title, duration_min, duration_sec, thumbnail, vidid = await YouTube.details(search, videoid=search)
                except Exception as e:
                    print(f"\n🚨 [PLAYLIST MODE] Fetch Details Error: {type(e).__name__}")
                    traceback.print_exc()
                    continue

                if str(duration_min) == "None" or duration_min is None:
                    continue
                if duration_sec and duration_sec > config.DURATION_LIMIT:
                    continue

                if await is_active_chat(chat_id):
                    await put_queue(
                        chat_id,
                        original_chat_id,
                        f"vid_{vidid}",
                        title,
                        duration_min,
                        user_name,
                        vidid,
                        user_id,
                        "video" if is_video else "audio",
                    )
                    position = len(db.get(chat_id)) - 1
                    count += 1
                    msg += f"{count}. {title[:70]}\n{_['play_20']} {position}\n\n"
                    continue

                try:
                    # 🚀 استلام قيمة واحدة للرابط المباشر في قوائم التشغيل
                    file_path = await YouTube.download(
                        vidid, mystic, video=is_video, videoid=get_download_id(vidid)
                    )
                    direct = True
                    if not file_path:
                        continue
                    await StreamController.join_call(chat_id, original_chat_id, file_path, video=is_video)
                except Exception as e:
                    print(f"\n🚨 [PLAYLIST MODE] Download/Join Error: {type(e).__name__}")
                    traceback.print_exc()
                    continue

                await put_queue(
                    chat_id,
                    original_chat_id,
                    file_path if direct else f"vid_{vidid}",
                    title,
                    duration_min,
                    user_name,
                    vidid,
                    user_id,
                    "video" if is_video else "audio",
                    forceplay=True,
                )

                img = f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg" if vidid else config.YOUTUBE_IMG_URL
                button = stream_markup(_, chat_id)
                await safe_delete(mystic)

                run = await app.send_photo(
                    original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{vidid}",
                        title[:23],
                        duration_min,
                        user_name,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                if db.get(chat_id):
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

                count += 1

            if count == 0:
                return

            link = await ANNIEBIN(msg)
            try:
                playlist_photo = await Carbon.generate(msg, randint(100, 10000000))
            except Exception:
                playlist_photo = config.PLAYLIST_IMG_URL

            upl = close_markup(_)
            final_position = len(db.get(chat_id) or []) - 1
            return await app.send_photo(
                original_chat_id,
                photo=playlist_photo,
                caption=_["play_21"].format(final_position, link),
                reply_markup=upl,
            )

        # --------------------------
        # 3. UNIFIED MEDIA MODE (YT, SC, TG, LIVE, INDEX)
        # --------------------------
        case _:
            is_index = (streamtype == "index")
            is_live = (streamtype == "live")

            if is_index:
                link = file_path = vidid = result
                title = "ɪɴᴅᴇx ᴏʀ ᴍ3ᴜ8 ʟɪɴᴋ"
                duration_min = "00:00"
            else:
                link = result.get("link", "")
                vidid = result.get("vidid", "")
                title = result.get("title", "").title()
                duration_min = "Live Track" if is_live else result.get("duration_min", result.get("dur", "00:00"))
                file_path = result.get("path", result.get("filepath", ""))

            # Path determination based on streamtype
            match streamtype:
                case "youtube":
                    q_path = f"vid_{vidid}"
                case "live":
                    q_path = f"live_{vidid}"
                case "index":
                    q_path = "index_url"
                case _:
                    q_path = file_path

            is_active = await is_active_chat(chat_id)

            # --- ACTION 1: ADD TO QUEUE ---
            if is_active and not forceplay:
                if is_index:
                    await put_queue_index(
                        chat_id,
                        original_chat_id,
                        q_path,
                        title,
                        duration_min,
                        user_name,
                        link,
                        "video" if is_video else "audio",
                    )
                else:
                    await put_queue(
                        chat_id,
                        original_chat_id,
                        q_path,
                        title,
                        duration_min,
                        user_name,
                        vidid,
                        user_id,
                        "video" if is_video else "audio",
                    )

                position = len(db.get(chat_id)) - 1
                button = aq_markup(_, chat_id)

                if is_index:
                    try:
                        await mystic.edit_text(
                            text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                            reply_markup=InlineKeyboardMarkup(button),
                        )
                    except Exception:
                        await safe_delete(mystic)
                        await app.send_message(
                            original_chat_id,
                            text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                            reply_markup=InlineKeyboardMarkup(button),
                        )
                else:
                    await safe_delete(mystic)
                    await app.send_message(
                        original_chat_id,
                        text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                return

            # --- ACTION 2: PLAY IMMEDIATELY ---
            if not forceplay:
                db[chat_id] = []

            play_path = file_path
            try:
                match streamtype:
                    case "youtube":
                        # 🚀 استلام الرابط المباشر فقط من السيرفر الصاروخي
                        play_path = await YouTube.download(
                            vidid, mystic, video=is_video, videoid=get_download_id(vidid)
                        )
                        direct = True
                    case "live":
                        n, play_path = await YouTube.video(link)
                        direct = True
                        if n == 0:
                            raise AssistantErr(_["str_3"])
                    case "index":
                        play_path = link
                        direct = True
                    case _:
                        direct = False

                if not play_path:
                    raise AssistantErr(_["play_14"])

                await StreamController.join_call(chat_id, original_chat_id, play_path, video=is_video)
            except AssistantErr as e:
                await safe_delete(mystic)
                return await app.send_message(original_chat_id, text=str(e))
            except Exception as e:
                print(f"\n🚨🚨🚨 FATAL STREAM ENGINE ERROR (Type: {type(e).__name__}) 🚨🚨🚨")
                traceback.print_exc()
                await safe_delete(mystic)
                return await app.send_message(original_chat_id, text=f"Error: [{type(e).__name__}] {str(e)}")

            if is_index:
                await put_queue_index(
                    chat_id,
                    original_chat_id,
                    q_path,
                    title,
                    duration_min,
                    user_name,
                    link,
                    "video" if is_video else "audio",
                    forceplay=True,
                )
            else:
                await put_queue(
                    chat_id,
                    original_chat_id,
                    q_path,
                    title,
                    duration_min,
                    user_name,
                    vidid,
                    user_id,
                    "video" if is_video else "audio",
                    forceplay=True,
                )

            await _sync_video_state(chat_id, is_video)

            await safe_delete(mystic)
            button = stream_markup(_, chat_id)

            match streamtype:
                case "soundcloud":
                    photo = config.SOUNDCLOUD_IMG_URL
                    caption = _["stream_1"].format(config.SUPPORT_CHAT, title[:23], duration_min, user_name)
                case "telegram":
                    photo = config.TELEGRAM_VIDEO_URL if is_video else config.TELEGRAM_AUDIO_URL
                    caption = _["stream_1"].format(link, title[:23], duration_min, user_name)
                case "index":
                    photo = config.STREAM_IMG_URL
                    caption = _["stream_2"].format(user_name)
                case _:
                    photo = f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg" if vidid else config.YOUTUBE_IMG_URL
                    caption = _["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{vidid}",
                        title[:23],
                        duration_min,
                        user_name,
                    )

            run = await app.send_photo(
                original_chat_id,
                photo=photo,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(button),
            )

            if db.get(chat_id):
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg" if streamtype in ["soundcloud", "telegram", "live", "index"] else "stream"
