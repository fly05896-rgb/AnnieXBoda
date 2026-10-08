# Authored By Certified Coders © 2026
# System: Skip Handler (Optimized for PyTgCalls v3.0 & Zero-Crash Logic)
# Optimized for Python 3.13: Pattern Matching & Suppress

from contextlib import suppress
from pyrogram import filters
# تمت إضافة LinkPreviewOptions للإصدارات الحديثة
from pyrogram.types import InlineKeyboardMarkup, Message, LinkPreviewOptions

import config
from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import get_loop
from AnnieXMedia.utils.decorators import AdminRightsCheck
from AnnieXMedia.utils.inline import close_markup, stream_markup
from AnnieXMedia.utils.stream.autoclear import auto_clean
from AnnieXMedia.utils.thumbnails import get_thumb
from config import BANNED_USERS


@app.on_message(
    filters.command(["skip", "cskip", "next", "cnext", "تخطي", "التالي"], prefixes=["", "/", "!", "."]) 
    & filters.group 
    & ~BANNED_USERS
)
@AdminRightsCheck
async def skip(cli, message: Message, _, chat_id: int):
    # -----------------------
    # Early Check: If no active call, return 'call_8'
    # -----------------------
    # 🚀 بايثون 3.13: استخدام suppress للتخطي الصامت للأخطاء بدلاً من try/except
    with suppress(Exception):
        if chat_id not in StreamController.active_calls:
            return await message.reply_text(_["call_8"])

    # -------------------------------------------------------
    # 1. Multi-Skip Logic (e.g., /skip 3)
    # -------------------------------------------------------
    if len(message.command) > 1:
        loop = await get_loop(chat_id)
        if loop != 0:
            return await message.reply_text(_["admin_8"])
        
        state = message.text.split(None, 1)[1].strip()
        if state.isnumeric():
            state = int(state)
            check = db.get(chat_id)
            if check:
                count = len(check)
                if count > 2:
                    count = count - 1
                    if 1 <= state <= count:
                        for x in range(state):
                            popped = None
                            try:
                                popped = check.pop(0)
                            except Exception:
                                return await message.reply_text(_["admin_12"])
                            if popped:
                                await auto_clean(popped)
                            if not check:
                                with suppress(Exception):
                                    await message.reply_text(
                                        text=_["admin_6"].format(message.from_user.mention, message.chat.title),
                                        reply_markup=close_markup(_),
                                    )
                                    await StreamController.stop_stream(chat_id)
                                break
                    else:
                        return await message.reply_text(_["admin_11"].format(count))
                else:
                    return await message.reply_text(_["admin_10"])
            else:
                return await message.reply_text(_["queue_2"])
        else:
            return await message.reply_text(_["admin_9"])
    
    # -------------------------------------------------------
    # 2. Standard Skip (Next Track)
    # -------------------------------------------------------
    else:
        check = db.get(chat_id)
        popped = None
        try:
            if check:
                popped = check.pop(0)
            if popped:
                await auto_clean(popped)
            if not check:
                await message.reply_text(
                    text=_["admin_6"].format(message.from_user.mention, message.chat.title),
                    reply_markup=close_markup(_),
                )
                with suppress(Exception):
                    return await StreamController.stop_stream(chat_id)
        except Exception:
            with suppress(Exception):
                await message.reply_text(
                    text=_["admin_6"].format(message.from_user.mention, message.chat.title),
                    reply_markup=close_markup(_),
                )
                return await StreamController.stop_stream(chat_id)
    
    if not check:
        return
    
    # Prepare Next Track Data
    queued = check[0]["file"]
    title = (check[0]["title"]).title()
    user = check[0]["by"]
    streamtype = check[0]["streamtype"]
    videoid = check[0]["vidid"]
    status = True if str(streamtype) == "video" else False
    
    db[chat_id][0]["played"] = 0
    exis = (check[0]).get("old_dur")
    if exis:
        db[chat_id][0]["dur"] = exis
        db[chat_id][0]["seconds"] = check[0]["old_second"]
        db[chat_id][0]["speed_path"] = None
        db[chat_id][0]["speed"] = 1.0
        
    # -------------------------------------------------------
    # 3. Stream Switching Logic (🚀 Python 3.13 Match-Case)
    # -------------------------------------------------------
    match queued:
        # [A] Live Stream
        case _ if "live_" in queued:
            n, link = await YouTube.video(videoid, True)
            if n == 0 or not link:
                return await message.reply_text(_["admin_7"].format(title))
            try:
                await StreamController.skip_stream(chat_id, link, video=status)
            except Exception:
                return await message.reply_text(_["call_6"])
            
            button = stream_markup(_, chat_id)
            img = await get_thumb(videoid)
            run = await message.reply_photo(
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23], check[0]["dur"], user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
            
        # [B] YouTube Video / Audio Downloaded
        case _ if "vid_" in queued:
            # تم التحديث لحل الكراش باستخدام LinkPreviewOptions بدلاً من disable_web_page_preview
            mystic = await message.reply_text(
                _["call_7"], 
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            try:
                # 🔥 بناء الرابط كامل لمنع الكراش
                full_url = f"https://www.youtube.com/watch?v={videoid}"
                
                # استلام المسار فقط
                file_path = await YouTube.download(
                    link=full_url,
                    mystic=mystic,
                    video=status,
                )
            except Exception as e:
                print(f"Skip Command Download Error: {e}")
                return await mystic.edit_text(_["call_6"])
            
            if not file_path:
                return await mystic.edit_text(_["call_6"])
                
            try:
                await StreamController.skip_stream(chat_id, file_path, video=status)
            except Exception as e:
                print(f"Skip Command Stream Error: {e}")
                return await mystic.edit_text(_["call_6"])
            
            button = stream_markup(_, chat_id)
            img = await get_thumb(videoid)
            run = await message.reply_photo(
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23], check[0]["dur"], user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
            with suppress(Exception):
                await mystic.delete()
                
        # [C] Index Link / M3U8
        case _ if "index_" in queued:
            try:
                await StreamController.skip_stream(chat_id, videoid, video=status)
            except Exception:
                return await message.reply_text(_["call_6"])
            
            button = stream_markup(_, chat_id)
            run = await message.reply_photo(
                photo=config.STREAM_IMG_URL,
                caption=_["stream_2"].format(user),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
            
        # [D] Telegram Audio / SoundCloud / Direct Link
        case _:
            try:
                await StreamController.skip_stream(chat_id, queued, video=status)
            except Exception:
                return await message.reply_text(_["call_6"])
            
            button = stream_markup(_, chat_id)
            if videoid == "telegram":
                run = await message.reply_photo(
                    photo=config.TELEGRAM_AUDIO_URL if str(streamtype) == "audio" else config.TELEGRAM_VIDEO_URL,
                    caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
                
            elif videoid == "soundcloud":
                run = await message.reply_photo(
                    photo=config.SOUNDCLOUD_IMG_URL if str(streamtype) == "audio" else config.TELEGRAM_VIDEO_URL,
                    caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
                
            else:
                img = await get_thumb(videoid)
                run = await message.reply_photo(
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23], check[0]["dur"], user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"
