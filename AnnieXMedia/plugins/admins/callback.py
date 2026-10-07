# Authored By Certified Coders © 2026
# Module: Playback Callback & Control
# Optimized for Python 3.13: Pattern Matching, PEP 604, and Suppress

import asyncio
import random
from contextlib import suppress

from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup

from config import (
    BANNED_USERS,
    lyrical,
    SOUNCLOUD_IMG_URL,
    STREAM_IMG_URL,
    SUPPORT_CHAT,
    TELEGRAM_AUDIO_URL,
    TELEGRAM_VIDEO_URL,
)
from strings import get_string
from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import (
    get_active_chats,
    get_assistant,
    get_lang,
    is_active_chat,
    is_music_playing,
    music_off,
    music_on,
    set_loop,
)
from AnnieXMedia.utils.decorators import ActualAdminCB, languageCB
from AnnieXMedia.utils.formatters import seconds_to_min
from AnnieXMedia.utils.inline import close_markup, stream_markup, stream_markup_timer
from AnnieXMedia.utils.stream.autoclear import auto_clean
from AnnieXMedia.utils.thumbnails import get_thumb

checker = {}

# 🚀 بايثون 3.13: كتابة الأنواع الحديثة (Modern Typing) 
def parse_chat_info(chat_info: str) -> tuple[int, str | None]:
    if "_" in chat_info:
        parts = chat_info.split("_")
        return int(parts[0]), parts[1]
    return int(chat_info), None


@app.on_callback_query(filters.regex("unban_assistant"))
async def unban_assistant(_, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    userbot = await get_assistant(chat_id)
    try:
        await app.unban_chat_member(chat_id, userbot.id)
        await callback.answer(
            "تم الغاء حظر الحساب المساعد بنجاح\nيمكنك الان تشغيل الاغاني",
            show_alert=True,
        )
    except Exception:
        await callback.answer(
            "فشل الغاء حظر المساعد لانني لا املك صلاحية الحظر\nيرجى اعطائي صلاحية الحظر حتى اتمكن من فك الحظر عن المساعد",
            show_alert=True,
        )


@app.on_callback_query(filters.regex("stream_admin") & ~BANNED_USERS)
@languageCB
async def manage_callback(client, callback: CallbackQuery, _):
    data = callback.data.strip().split(None, 1)[1]
    command, chat_info = data.split("|", 1)
    chat_id, counter = parse_chat_info(chat_info)
    
    if not await is_active_chat(chat_id):
        return await callback.answer(_["general_5"], show_alert=True)
        
    user_mention = callback.from_user.mention
    
    # 🚀 بايثون 3.13: المطابقة الهيكلية السريعة (Pattern Matching) بدلاً من if/elif
    match command:
        case "Pause":
            if not await is_music_playing(chat_id):
                return await callback.answer(_["admin_1"], show_alert=True)
            await callback.answer()
            await music_off(chat_id)
            await StreamController.pause_stream(chat_id)
            await callback.message.reply_text(_["admin_2"].format(user_mention), reply_markup=close_markup(_))

        case "Resume":
            if await is_music_playing(chat_id):
                return await callback.answer(_["admin_3"], show_alert=True)
            await callback.answer()
            await music_on(chat_id)
            await StreamController.resume_stream(chat_id)
            await callback.message.reply_text(_["admin_4"].format(user_mention), reply_markup=close_markup(_))

        case "Stop" | "End": # دمج الأوامر المتشابهة بسهولة!
            await callback.answer()
            await StreamController.stop_stream(chat_id)
            await set_loop(chat_id, 0)
            await callback.message.reply_text(_["admin_5"].format(user_mention), reply_markup=close_markup(_))
            with suppress(Exception):
                await callback.message.delete()

        case "Loop":
            await callback.answer()
            await set_loop(chat_id, 3)
            await callback.message.reply_text(_["admin_41"].format(user_mention, 3))

        case "Shuffle":
            playlist = db.get(chat_id)
            if not playlist:
                return await callback.answer(_["admin_42"], show_alert=True)
            try:
                popped = playlist.pop(0)
            except Exception:
                return await callback.answer(_["admin_43"], show_alert=True)
            if not playlist:
                playlist.insert(0, popped)
                return await callback.answer(_["admin_43"], show_alert=True)
            await callback.answer()
            random.shuffle(playlist)
            playlist.insert(0, popped)
            await callback.message.reply_text(_["admin_44"].format(user_mention))

        case "Skip" | "Replay":
            await handle_skip_replay(callback, _, chat_id, command, user_mention)

        case _: # (Default fallback) لأي أمر آخر مثل التقديم والتأخير (Seek)
            await handle_seek(callback, _, chat_id, command, user_mention)


async def handle_skip_replay(callback: CallbackQuery, _, chat_id: int, command: str, user_mention: str):
    playlist = db.get(chat_id)

    if not playlist or len(playlist) == 0:
        return await callback.answer(_["queue_2"], show_alert=True)

    if command == "Skip":
        text_msg = f"تم تخطي الاغنية\nبواسطة : {user_mention}"
        try:
            popped = playlist.pop(0)
            if popped:
                await auto_clean(popped)
            if not playlist:
                await callback.edit_message_text(text_msg)
                await callback.message.reply_text(
                    _["admin_6"].format(user_mention, callback.message.chat.title),
                    reply_markup=close_markup(_)
                )
                return await StreamController.stop_stream(chat_id)
        except Exception:
            await callback.edit_message_text(text_msg)
            await callback.message.reply_text(
                _["admin_6"].format(user_mention, callback.message.chat.title),
                reply_markup=close_markup(_)
            )
            return await StreamController.stop_stream(chat_id)
    else:
        text_msg = f"تم إعادة التشغيل\nبواسطة : {user_mention}"

    await callback.answer()

    if len(playlist) == 0:
        return await callback.answer(_["queue_2"], show_alert=True)

    current_track = playlist[0]
    queued = current_track["file"]
    title = current_track["title"].title()
    user = current_track["by"]
    duration = current_track["dur"]
    streamtype = current_track["streamtype"]
    videoid = current_track["vidid"]
    status = str(streamtype) == "video"

    db[chat_id][0]["played"] = 0
    if current_track.get("old_dur"):
        db[chat_id][0]["dur"] = current_track["old_dur"]
        db[chat_id][0]["seconds"] = current_track["old_second"]
        db[chat_id][0]["speed_path"] = None
        db[chat_id][0]["speed"] = 1.0

    # 🚀 بايثون 3.13: المطابقة الهيكلية مع الشروط (Pattern Matching with Guards)
    match queued:
        case _ if "live_" in queued:
            n, new_link = await YouTube.video(videoid, True)
            if n == 0:
                return await callback.message.reply_text(_["admin_7"].format(title), reply_markup=close_markup(_))
            
            try:
                await StreamController.skip_stream(chat_id, new_link, video=status)
            except Exception:
                return await callback.message.reply_text(_["call_6"])
                
            buttons = stream_markup(_, chat_id)
            img = await get_thumb(videoid)
            run = await callback.message.reply_photo(
                photo=img,
                caption=_["stream_1"].format(f"https://t.me/{app.username}?start=info_{videoid}", title[:23], duration, user),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
            await callback.edit_message_text(text_msg, reply_markup=close_markup(_))

        # 🔥 الحل الجذري للكراش بعد التحديثات + نظام 3.13 
        case _ if "vid_" in queued:
            # تمت إزالة disable_web_page_preview=True لتوافق Pyrogram الإصدارات الحديثة
            mystic = await callback.message.reply_text(_["call_7"])
            try:
                # 1. بناء الرابط كاملاً
                full_url = f"https://www.youtube.com/watch?v={videoid}"
                # 2. استلام قيمة واحدة (المسار) بدون Unpacking error
                file_path = await YouTube.download(full_url, mystic, video=status)
            except Exception as e:
                print(f"Callback Download Error: {e}")
                return await mystic.edit_text(_["call_6"])
            
            if not file_path:
                return await mystic.edit_text(_["call_6"])
                
            try:
                await StreamController.skip_stream(chat_id, file_path, video=status)
            except Exception as e:
                print(f"Callback Skip Stream Error: {e}")
                return await mystic.edit_text(_["call_6"])
                
            buttons = stream_markup(_, chat_id)
            img = await get_thumb(videoid)
            run = await callback.message.reply_photo(
                photo=img,
                caption=_["stream_1"].format(f"https://t.me/{app.username}?start=info_{videoid}", title[:23], duration, user),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
            await callback.edit_message_text(text_msg, reply_markup=close_markup(_))
            with suppress(Exception):
                await mystic.delete()

        case _ if "index_" in queued:
            try:
                await StreamController.skip_stream(chat_id, videoid, video=status)
            except Exception:
                return await callback.message.reply_text(_["call_6"])
            buttons = stream_markup(_, chat_id)
            run = await callback.message.reply_photo(
                photo=STREAM_IMG_URL,
                caption=_["stream_2"].format(user),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
            await callback.edit_message_text(text_msg, reply_markup=close_markup(_))

        case _:
            try:
                await StreamController.skip_stream(chat_id, queued, video=status)
            except Exception:
                return await callback.message.reply_text(_["call_6"])
                
            if videoid == "telegram":
                buttons = stream_markup(_, chat_id)
                run = await callback.message.reply_photo(
                    photo=(TELEGRAM_AUDIO_URL if str(streamtype) == "audio" else TELEGRAM_VIDEO_URL),
                    caption=_["stream_1"].format(SUPPORT_CHAT, title[:23], duration, user),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            elif videoid == "soundcloud":
                buttons = stream_markup(_, chat_id)
                run = await callback.message.reply_photo(
                    photo=(SOUNCLOUD_IMG_URL if str(streamtype) == "audio" else TELEGRAM_VIDEO_URL),
                    caption=_["stream_1"].format(SUPPORT_CHAT, title[:23], duration, user),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            else:
                buttons = stream_markup(_, chat_id)
                img = await get_thumb(videoid)
                run = await callback.message.reply_photo(
                    photo=img,
                    caption=_["stream_1"].format(f"https://t.me/{app.username}?start=info_{videoid}", title[:23], duration, user),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"
            await callback.edit_message_text(text_msg, reply_markup=close_markup(_))


async def handle_seek(callback: CallbackQuery, _, chat_id: int, command: str, user_mention: str):
    try:
        cmd_int = int(command)
    except ValueError:
        return await callback.answer("عذراً، أمر التقديم أو التأخير غير صالح.", show_alert=True)

    playing = db.get(chat_id)
    if not playing or len(playing) == 0:
        return await callback.answer(_["queue_2"], show_alert=True)
        
    duration_seconds = int(playing[0]["seconds"])
    if duration_seconds == 0:
        return await callback.answer(_["admin_22"], show_alert=True)
        
    file_path = playing[0]["file"]
    if "index_" in file_path or "live_" in file_path:
        return await callback.answer(_["admin_22"], show_alert=True)
        
    duration_played = int(playing[0]["played"])
    duration_to_skip = 10 if cmd_int in [1, 2] else 30
    duration = playing[0]["dur"]
    
    if cmd_int in [1, 3]:
        if (duration_played - duration_to_skip) <= 10:
            bet = seconds_to_min(duration_played)
            return await callback.answer(
                f"لا يمكن الترجيع لان المدة تتجاوز الحد\n\nتم تشغيل : {bet} دقيقة من اصل {duration} دقيقة",
                show_alert=True
            )
        to_seek = duration_played - duration_to_skip + 1
    else:
        if (duration_seconds - (duration_played + duration_to_skip)) <= 10:
            bet = seconds_to_min(duration_played)
            return await callback.answer(
                f"لا يمكن التقديم لان المدة تتجاوز الحد\n\nتم تشغيل : {bet} دقيقة من اصل {duration} دقيقة",
                show_alert=True
            )
        to_seek = duration_played + duration_to_skip + 1
        
    await callback.answer()
    mystic = await callback.message.reply_text(_["admin_24"])
    
    if "vid_" in file_path:
        n, file_path = await YouTube.video(playing[0]["vidid"], True)
        if n == 0:
            return await mystic.edit_text(_["admin_22"])
    
    try:
        streamtype = playing[0]["streamtype"]
        mode = "video" if streamtype == "video" else "audio"
        await StreamController.seek_stream(
            chat_id,
            file_path,
            seconds_to_min(to_seek),
            duration,
            mode, 
        )
    except Exception:
        return await mystic.edit_text(_["admin_26"])
        
    if cmd_int in [1, 3]:
        db[chat_id][0]["played"] -= duration_to_skip
    else:
        db[chat_id][0]["played"] += duration_to_skip
        
    seek_message = _["admin_25"].format(seconds_to_min(to_seek))
    await mystic.edit_text(f"{seek_message}\n\nبواسطة : {user_mention}")


async def markup_timer():
    while True:
        await asyncio.sleep(6)
        active_chats = await get_active_chats()
        for chat_id in active_chats:
            try:
                if not await is_music_playing(chat_id):
                    continue
                playing = db.get(chat_id)
                if not playing:
                    continue
                duration_seconds = int(playing[0]["seconds"])
                if duration_seconds == 0:
                    continue
                mystic = playing[0].get("mystic")
                if not mystic:
                    continue
                if chat_id in checker and mystic.id in checker[chat_id]:
                    if checker[chat_id][mystic.id] is False:
                        continue
                
                # 🚀 بايثون 3.13: تحديد اللغة بشكل أنظف مع توفير معالجة Exception
                language = await get_lang(chat_id) if hasattr(get_lang, "__call__") else "ar"
                _lang = get_string(language) if get_string else None
                if not _lang:
                    _lang = get_string("ar")
                    
                with suppress(Exception):
                    buttons = stream_markup_timer(
                        _lang,
                        chat_id,
                        seconds_to_min(playing[0]["played"]),
                        playing[0]["dur"],
                    )
                    await mystic.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
            except Exception:
                continue

# التأكد من عمل المؤقت بدون أخطاء في الـ Event Loop
try:
    asyncio.create_task(markup_timer())
except RuntimeError:
    pass


@app.on_callback_query(filters.regex("close") & ~BANNED_USERS)
async def close_menu(_, query: CallbackQuery):
    # بايثون 3.13: استخدام Suppress للتعامل الصامت والأسرع مع الأخطاء (بدلًا من try/except)
    with suppress(Exception):
        await query.answer()
        await query.message.delete()
        msg = await query.message.reply_text(f"تم الاغلاق بواسطة : {query.from_user.mention}")
        await asyncio.sleep(2)
        await msg.delete()


@app.on_callback_query(filters.regex("stop_downloading") & ~BANNED_USERS)
@ActualAdminCB
async def stop_download(_, query: CallbackQuery, _lang):
    task = lyrical.get(query.message.id)
    if not task:
        return await query.answer(_lang["tg_4"], show_alert=True)
    if task.done() or task.cancelled():
        return await query.answer(_lang["tg_5"], show_alert=True)
    try:
        task.cancel()
        lyrical.pop(query.message.id, None)
        await query.answer(_lang["tg_6"], show_alert=True)
        return await query.edit_message_text(_lang["tg_7"].format(query.from_user.mention))
    except Exception:
        return await query.answer(_lang["tg_8"], show_alert=True)
