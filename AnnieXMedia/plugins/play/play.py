# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/plugins/play/play.py
# System: Play Command Handler (0ms RAM Search-Lock Cache + Parallel Reply & YouTube Track Pipeline)

import asyncio
import random
import string
from contextlib import suppress

from pyrogram import filters
from pyrogram.errors import FloodWait, RandomIdDuplicate
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message

import config
from config import AYU, BANNED_USERS, OWNER_ID, lyrical
from AnnieXMedia import Apple, Resso, SoundCloud, Spotify, Telegram, YouTube, app
from AnnieXMedia.core.mongo import mongodb
from AnnieXMedia.utils import time_to_seconds
from AnnieXMedia.utils.channelplay import get_channeplayCB
from AnnieXMedia.utils.decorators.language import languageCB
from AnnieXMedia.utils.decorators.play import PlayWrapper
from AnnieXMedia.utils.errors import capture_callback_err, capture_err
from AnnieXMedia.utils.exceptions import AssistantErr
from AnnieXMedia.utils.formatters import formats
from AnnieXMedia.utils.inline import (
    botplaylist_markup,
    livestream_markup,
    playlist_markup,
    slider_markup,
)
from AnnieXMedia.utils.logger import play_logs
from AnnieXMedia.utils.stream.stream import stream

# ==========================================================
# إعدادات قاعدة البيانات المشتركة مع كاش الرام الفوري (0ms بدل 789ms)
# ==========================================================

SUDO_USERS = OWNER_ID if isinstance(OWNER_ID, list) else [OWNER_ID]
songdb = mongodb.song_settings
_SEARCH_LOCKED_CACHE: bool | None = None


async def get_search_state() -> bool:
    global _SEARCH_LOCKED_CACHE
    if _SEARCH_LOCKED_CACHE is not None:
        return _SEARCH_LOCKED_CACHE
    try:
        data = await songdb.find_one({"_id": "song_config"})
        _SEARCH_LOCKED_CACHE = bool(data.get("search_locked", False)) if data else False
        return _SEARCH_LOCKED_CACHE
    except Exception:
        _SEARCH_LOCKED_CACHE = False
        return False


async def set_search_state(locked: bool):
    global _SEARCH_LOCKED_CACHE
    _SEARCH_LOCKED_CACHE = bool(locked)
    with suppress(Exception):
        await songdb.update_one({"_id": "song_config"}, {"$set": {"search_locked": locked}}, upsert=True)


async def _safe_delete_msg(msg):
    with suppress(Exception):
        if msg:
            await msg.delete()


async def _send_wait_msg(message: Message, wait_text: str):
    try:
        return await message.reply_text(wait_text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await message.reply_text(wait_text)
    except RandomIdDuplicate:
        return await app.send_message(message.chat.id, wait_text)


# ==========================================================
# أوامر التحكم (أدمن المطور)
# ==========================================================

@app.on_message(filters.command(["قفل البحث", "تعطيل البحث"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def lock_search_cmd(client, message: Message):
    await set_search_state(True)
    await message.reply_text("**تم قفل البحث بنجاح .**")


@app.on_message(filters.command(["فتح البحث", "تفعيل البحث"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def unlock_search_cmd(client, message: Message):
    await set_search_state(False)
    await message.reply_text("**تم فتح البحث بنجاح .**")


# ==========================================================
# كود التشغيل الرئيسي (The Main Engine)
# ==========================================================

@app.on_message(
    filters.command(
        [
            "play", "vplay", "cplay", "cvplay", "playforce", "vplayforce", "cplayforce", "cvplayforce",
            "تشغيل", "شغل", "فيد", "فيديو",
        ],
        prefixes=["", "/", "!", "#"],
    )
    & filters.group
    & ~BANNED_USERS
)
@PlayWrapper
@capture_err
async def play_command(
    client,
    message: Message,
    _,
    chat_id,
    video,
    channel,
    playmode,
    url,
    fplay,
):
    is_locked = await get_search_state()
    if is_locked and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("- البحـث مغلـق .")

    command = message.command[0] if message.command else ""
    if command in ["فيد", "فيديو", "vplay", "cvplay", "vplayforce", "cvplayforce"]:
        video = True

    wait_text = _["play_2"].format(channel) if channel else random.choice(AYU)
    mystic_task = asyncio.create_task(_send_wait_msg(message, wait_text))

    plist_id = None
    plist_type = None
    spotify = None
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    internal_type = None
    details = None
    track_id = None
    img = None
    cap = None
    log_label = "Youtube Track"

    # ==========================
    # A. الرد على ملف صوتي (Audio Reply)
    # ==========================
    audio_telegram = (
        (message.reply_to_message.audio or message.reply_to_message.voice)
        if message.reply_to_message
        else None
    )
    if audio_telegram:
        mystic = await mystic_task
        if audio_telegram.file_size > config.TG_AUDIO_FILESIZE_LIMIT:
            return await mystic.edit_text(_["play_5"])
        if audio_telegram.duration > config.DURATION_LIMIT:
            return await mystic.edit_text(_["play_6"].format(config.DURATION_LIMIT_MIN, app.mention))

        file_path = await Telegram.get_filepath(audio=audio_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(audio_telegram, audio=True)
            dur = await Telegram.get_duration(audio_telegram, file_path)
            details = {"title": file_name, "link": message_link, "path": file_path, "dur": dur}

            try:
                await stream(
                    _, mystic, user_id, details, chat_id, user_name,
                    message.chat.id, video=False, streamtype="telegram", forceplay=bool(fplay),
                )
            except Exception as e:
                await _safe_delete_msg(mystic)
                return await app.send_message(chat_id, _["general_2"].format(type(e).__name__))

            asyncio.create_task(play_logs(message, streamtype="Telegram [Audio]", query=message.reply_to_message.caption or "—"))
            return await _safe_delete_msg(mystic)
        return

    # ==========================
    # B. الرد على فيديو (Video Reply)
    # ==========================
    video_telegram = (
        (message.reply_to_message.video or message.reply_to_message.document)
        if message.reply_to_message
        else None
    )
    if video_telegram:
        mystic = await mystic_task
        if message.reply_to_message.document:
            try:
                ext = (video_telegram.file_name or "").split(".")[-1]
                if ext.lower() not in formats:
                    return await mystic.edit_text(_["play_7"].format(" | ".join(formats)))
            except Exception:
                return await mystic.edit_text(_["play_7"].format(" | ".join(formats)))

        if video_telegram.file_size > config.TG_VIDEO_FILESIZE_LIMIT:
            return await mystic.edit_text(_["play_8"])

        file_path = await Telegram.get_filepath(video=video_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(video_telegram)
            dur = await Telegram.get_duration(video_telegram, file_path)
            details = {"title": file_name, "link": message_link, "path": file_path, "dur": dur}

            try:
                await stream(
                    _, mystic, user_id, details, chat_id, user_name,
                    message.chat.id, video=True, streamtype="telegram", forceplay=bool(fplay),
                )
            except Exception as e:
                await _safe_delete_msg(mystic)
                return await app.send_message(chat_id, _["general_2"].format(type(e).__name__))

            asyncio.create_task(play_logs(message, streamtype="Telegram [Video]", query=message.reply_to_message.caption or "—"))
            return await _safe_delete_msg(mystic)
        return

    # ==========================
    # C. الروابط (URLs: YT, Spotify, Apple, etc.)
    # ==========================
    if url:
        if await YouTube.exists(url):
            match url:
                case _ if "playlist" in url or ("list=" in url and "watch?v=" not in url):
                    try:
                        details, mystic = await asyncio.gather(
                            YouTube.playlist(url, config.PLAYLIST_FETCH_LIMIT, user_id),
                            mystic_task,
                        )
                    except Exception as e:
                        mystic = await mystic_task
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")

                    if not details:
                        return await mystic.edit_text(_["play_3"])

                    plist_type = "yt"
                    plist_id = (url.split("="))[1].split("&")[0] if "&" in url else (url.split("="))[1]
                    img = config.PLAYLIST_IMG_URL
                    cap = _["play_9"]
                    internal_type = "playlist"
                    log_label = "Youtube playlist"
                case _:
                    try:
                        (details, track_id), mystic = await asyncio.gather(
                            YouTube.track(url),
                            mystic_task,
                        )
                    except Exception as e:
                        mystic = await mystic_task
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")

                    if not details or details.get("title") == "Unknown":
                        return await mystic.edit_text(_["play_3"])

                    img = details.get("thumb", config.YOUTUBE_IMG_URL)
                    cap = _["play_10"].format(details["title"], details["duration_min"])
                    internal_type = "youtube"
                    log_label = "Youtube Track"

        elif await Spotify.valid(url):
            mystic = await mystic_task
            spotify = True
            if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
                return await mystic.edit_text("» Spotify not supported yet.")

            match url:
                case _ if "track" in url:
                    try:
                        details, track_id = await Spotify.track(url)
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    img = details.get("thumb", config.SPOTIFY_ARTIST_IMG_URL)
                    cap = _["play_10"].format(details["title"], details["duration_min"])
                    internal_type = "youtube"
                    log_label = "Spotify Track"
                case _ if "playlist" in url:
                    try:
                        details, plist_id = await Spotify.playlist(url)
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    plist_type = "spplay"
                    img = config.SPOTIFY_PLAYLIST_IMG_URL
                    cap = _["play_11"].format(app.mention, message.from_user.mention)
                    internal_type = "playlist"
                    log_label = "Spotify playlist"
                case _ if "album" in url:
                    try:
                        details, plist_id = await Spotify.album(url)
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    plist_type = "spalbum"
                    img = config.SPOTIFY_ALBUM_IMG_URL
                    cap = _["play_11"].format(app.mention, message.from_user.mention)
                    internal_type = "playlist"
                    log_label = "Spotify album"
                case _ if "artist" in url:
                    try:
                        details, plist_id = await Spotify.artist(url)
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    plist_type = "spartist"
                    img = config.SPOTIFY_ARTIST_IMG_URL
                    cap = _["play_11"].format(app.mention, message.from_user.first_name)
                    internal_type = "playlist"
                    log_label = "Spotify artist"
                case _:
                    return await mystic.edit_text(_["play_15"])

        elif await Apple.valid(url):
            mystic = await mystic_task
            match url:
                case _ if "album" in url or "/song/" in url or "?i=" in url:
                    try:
                        res = await Apple.track(url)
                        if not res or not isinstance(res, tuple):
                            return await mystic.edit_text(_["play_3"])
                        details, track_id = res
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    img = details.get("thumb", config.PLAYLIST_IMG_URL)
                    cap = _["play_10"].format(details["title"], details["duration_min"])
                    internal_type = "youtube"
                    log_label = "Apple Music"
                case _ if "playlist" in url:
                    spotify = True
                    try:
                        res = await Apple.playlist(url)
                        if not res or not isinstance(res, tuple):
                            return await mystic.edit_text(_["play_3"])
                        details, plist_id = res
                    except Exception as e:
                        return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
                    if not details:
                        return await mystic.edit_text(_["play_3"])
                    plist_type = "apple"
                    img = config.PLAYLIST_IMG_URL
                    cap = _["play_12"].format(app.mention, message.from_user.mention)
                    internal_type = "playlist"
                    log_label = "Apple Music playlist"
                case _:
                    return await mystic.edit_text(_["play_3"])

        elif await Resso.valid(url):
            mystic = await mystic_task
            try:
                res = await Resso.track(url)
                if not res or not isinstance(res, tuple):
                    return await mystic.edit_text(_["play_3"])
                details, track_id = res
            except Exception as e:
                return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
            if not details:
                return await mystic.edit_text(_["play_3"])
            img = details.get("thumb", config.PLAYLIST_IMG_URL)
            cap = _["play_10"].format(details["title"], details["duration_min"])
            internal_type = "youtube"
            log_label = "Resso"

        elif await SoundCloud.valid(url):
            mystic = await mystic_task
            try:
                sc_res = await SoundCloud.download(url)
                if not sc_res or not isinstance(sc_res, tuple):
                    return await mystic.edit_text(_["play_3"])
                details, track_path = sc_res
            except Exception as e:
                return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")
            if details["duration_sec"] > config.DURATION_LIMIT:
                return await mystic.edit_text(_["play_6"].format(config.DURATION_LIMIT_MIN, app.mention))

            try:
                await stream(
                    _, mystic, user_id, details, chat_id, user_name,
                    message.chat.id, streamtype="soundcloud", forceplay=bool(fplay),
                )
            except Exception as e:
                await _safe_delete_msg(mystic)
                return await app.send_message(chat_id, _["general_2"].format(type(e).__name__))

            asyncio.create_task(play_logs(message, streamtype="Soundcloud"))
            return await _safe_delete_msg(mystic)

        else:
            mystic = await mystic_task
            try:
                await stream(
                    _, mystic, user_id, url, chat_id, user_name,
                    message.chat.id, video=bool(video), streamtype="index", forceplay=bool(fplay),
                )
                asyncio.create_task(play_logs(message, streamtype="M3U8 or Index Link"))
                return
            except Exception as e:
                await _safe_delete_msg(mystic)
                return await app.send_message(chat_id, _["general_2"].format(type(e).__name__))

    # ==========================
    # D. البحث السريع بالتوازي مع إرسال رسالة الانتظار (يوفر 300ms كاملة!)
    # ==========================
    else:
        if len(message.command) < 2:
            mystic = await mystic_task
            buttons = botplaylist_markup(_)
            return await mystic.edit_text(_["play_18"], reply_markup=InlineKeyboardMarkup(buttons))

        query = message.text.split(None, 1)[1]
        if "-v" in query:
            query = query.replace("-v", "").strip()

        try:
            (details, track_id), mystic = await asyncio.gather(
                YouTube.track(query),
                mystic_task,
            )
        except Exception as e:
            mystic = await mystic_task
            return await mystic.edit_text(f"{_['play_3']}\nʀᴇᴀsᴏɴ: {e}")

        if not details or details.get("title") == "Unknown":
            return await mystic.edit_text(_["play_3"])

        internal_type = "youtube"
        log_label = "Youtube Track"

    # ==========================
    # التنفيذ النهائي (Final Execution)
    # ==========================
    match str(playmode):
        case "Direct":
            if not plist_type:
                if details.get("duration_min") and str(details.get("duration_min")).lower() != "live":
                    duration_sec = time_to_seconds(details["duration_min"])
                    if duration_sec and duration_sec > config.DURATION_LIMIT:
                        return await mystic.edit_text(_["play_6"].format(config.DURATION_LIMIT_MIN, app.mention))

                    try:
                        await stream(
                            _, mystic, user_id, details, chat_id, user_name, message.chat.id,
                            video=bool(video), streamtype=internal_type, spotify=spotify, forceplay=bool(fplay),
                        )
                    except AssistantErr as e:
                        await _safe_delete_msg(mystic)
                        return await app.send_message(chat_id, str(e))
                    except Exception as e:
                        await _safe_delete_msg(mystic)
                        return await app.send_message(chat_id, _["general_2"].format(type(e).__name__))

                    asyncio.create_task(_safe_delete_msg(mystic))
                    asyncio.create_task(play_logs(message, streamtype=log_label))
                    return

                else:
                    buttons = livestream_markup(
                        _, track_id, user_id,
                        "v" if video else "a",
                        "c" if channel else "g",
                        "f" if fplay else "d",
                    )
                    stream_img = details.get("thumb")
                    if not stream_img or not str(stream_img).startswith("http"):
                        stream_img = config.YOUTUBE_IMG_URL

                    asyncio.create_task(_safe_delete_msg(mystic))
                    return await message.reply_photo(
                        photo=stream_img,
                        caption=_["play_13"],
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )

            else:
                ran_hash = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
                lyrical[ran_hash] = plist_id
                buttons = playlist_markup(
                    _, ran_hash, user_id, plist_type,
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
                asyncio.create_task(_safe_delete_msg(mystic))

                final_thumb = config.PLAYLIST_IMG_URL
                with suppress(Exception):
                    if isinstance(details, dict) and details.get("thumb"):
                        final_thumb = details["thumb"]
                    elif isinstance(details, list) and len(details) > 0 and isinstance(details[0], dict) and details[0].get("thumb"):
                        final_thumb = details[0]["thumb"]
                    elif img and isinstance(img, str) and img.startswith("http") and "apple.com" not in img:
                        final_thumb = img

                if not final_thumb or not str(final_thumb).startswith("http"):
                    final_thumb = config.PLAYLIST_IMG_URL

                await message.reply_photo(
                    photo=final_thumb,
                    caption=cap,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                plist_label_map = {
                    "yt": "Youtube playlist",
                    "spplay": "Spotify playlist",
                    "spalbum": "Spotify album",
                    "spartist": "Spotify artist",
                    "apple": "Apple Music playlist",
                }
                asyncio.create_task(play_logs(message, streamtype=plist_label_map.get(plist_type, "Playlist")))
                return

        case _:
            buttons = slider_markup(
                _, track_id, user_id, query, 0,
                "c" if channel else "g",
                "f" if fplay else "d",
            )
            asyncio.create_task(_safe_delete_msg(mystic))

            slide_thumb = details.get("thumb")
            if not slide_thumb or not str(slide_thumb).startswith("http"):
                slide_thumb = config.YOUTUBE_IMG_URL

            await message.reply_photo(
                photo=slide_thumb,
                caption=_["play_10"].format(details["title"].title(), details["duration_min"]),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            asyncio.create_task(play_logs(message, streamtype="Searched on YouTube"))
            return


# ==========================
# معالجات الأزرار (Callbacks)
# ==========================

@app.on_callback_query(filters.regex("MusicStream") & ~BANNED_USERS)
@languageCB
@capture_callback_err
async def play_music_cb(client, CallbackQuery, _):
    mystic = None
    try:
        callback_data = CallbackQuery.data.split(None, 1)[1]
        vidid, user_id, mode, cplay, fplay = callback_data.split("|")

        if CallbackQuery.from_user.id != int(user_id):
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)

        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
        user_name = CallbackQuery.from_user.first_name
        asyncio.create_task(CallbackQuery.message.delete())
        await CallbackQuery.answer()

        wait_text = _["play_2"].format(channel) if channel else random.choice(AYU)
        (details, track_id), mystic = await asyncio.gather(
            YouTube.track(vidid, videoid=vidid),
            _send_wait_msg(CallbackQuery.message, wait_text),
        )

        if details.get("duration_min") and str(details.get("duration_min")).lower() != "live":
            duration_sec = time_to_seconds(details["duration_min"])
            if duration_sec and duration_sec > config.DURATION_LIMIT:
                return await mystic.edit_text(_["play_6"].format(config.DURATION_LIMIT_MIN, app.mention))

            video = mode == "v"
            forceplay = fplay == "f"

            await stream(
                _, mystic, CallbackQuery.from_user.id, details, chat_id, user_name,
                CallbackQuery.message.chat.id, bool(video), streamtype="youtube", forceplay=bool(forceplay),
            )
            asyncio.create_task(_safe_delete_msg(mystic))
        else:
            buttons = livestream_markup(
                _, track_id, CallbackQuery.from_user.id, mode,
                "c" if cplay == "c" else "g",
                "f" if fplay else "d",
            )
            return await mystic.edit_text(_["play_13"], reply_markup=InlineKeyboardMarkup(buttons))

    except Exception as e:
        await _safe_delete_msg(mystic)
        err = _["general_2"].format(type(e).__name__)
        return await CallbackQuery.message.reply_text(err)


@app.on_callback_query(filters.regex("AnonymousAdmin") & ~BANNED_USERS)
@capture_callback_err
async def anonymous_check(client, CallbackQuery):
    with suppress(Exception):
        await CallbackQuery.answer(
            "» ʀᴇᴠᴇʀᴛ ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ ᴀᴄᴄᴏᴜɴᴛ :\n\n"
            "ᴏᴘᴇɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs.\n"
            "-> ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs\n-> ᴄʟɪᴄᴋ ᴏɴ ʏᴏᴜʀ ɴᴀᴍᴇ\n"
            "-> ᴜɴᴄʜᴇᴄᴋ ᴀɴᴏɴʏᴍᴏᴜs ᴀᴅᴍɪɴ ᴘᴇʀᴍɪssɪᴏɴs.",
            show_alert=True,
        )


@app.on_callback_query(filters.regex("AnniePlaylists") & ~BANNED_USERS)
@languageCB
@capture_callback_err
async def play_playlists_command(client, CallbackQuery, _):
    mystic = None
    try:
        callback_data = CallbackQuery.data.split(None, 1)[1]
        videoid, user_id, ptype, mode, cplay, fplay = callback_data.split("|")

        if CallbackQuery.from_user.id != int(user_id):
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)

        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
        user_name = CallbackQuery.from_user.first_name
        asyncio.create_task(CallbackQuery.message.delete())
        await CallbackQuery.answer()

        wait_text = _["play_2"].format(channel) if channel else random.choice(AYU)
        mystic = await _send_wait_msg(CallbackQuery.message, wait_text)

        videoid = lyrical.get(videoid)
        video = mode == "v"
        forceplay = fplay == "f"
        spotify = True

        match ptype:
            case "yt":
                spotify = False
                result = await YouTube.playlist("", config.PLAYLIST_FETCH_LIMIT, CallbackQuery.from_user.id, videoid=videoid)
                internal_type = "playlist"
                log_label = "Youtube playlist"
            case "spplay":
                result, _ = await Spotify.playlist(videoid)
                internal_type = "playlist"
                log_label = "Spotify playlist"
            case "spalbum":
                result, _ = await Spotify.album(videoid)
                internal_type = "playlist"
                log_label = "Spotify album"
            case "spartist":
                result, _ = await Spotify.artist(videoid)
                internal_type = "playlist"
                log_label = "Spotify artist"
            case "apple":
                result, _ = await Apple.playlist(videoid, True)
                internal_type = "playlist"
                log_label = "Apple Music playlist"
            case _:
                return

        await stream(
            _, mystic, CallbackQuery.from_user.id, result, chat_id, user_name,
            CallbackQuery.message.chat.id, bool(video), streamtype=internal_type, spotify=spotify, forceplay=bool(forceplay),
        )
        asyncio.create_task(play_logs(CallbackQuery.message, streamtype=log_label))
        asyncio.create_task(_safe_delete_msg(mystic))

    except Exception as e:
        await _safe_delete_msg(mystic)
        err = _["general_2"].format(type(e).__name__)
        return await CallbackQuery.message.reply_text(err)


@app.on_callback_query(filters.regex("slider") & ~BANNED_USERS)
@languageCB
@capture_callback_err
async def slider_queries(client, CallbackQuery, _):
    with suppress(Exception):
        callback_data = CallbackQuery.data.split(None, 1)[1]
        what, rtype, query, user_id, cplay, fplay = callback_data.split("|")

        if CallbackQuery.from_user.id != int(user_id):
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)

        rtype = int(rtype)
        query_type = (rtype + 1) if what == "F" else (rtype - 1)

        if query_type > 9:
            query_type = 0
        if query_type < 0:
            query_type = 9

        title, duration_min, thumbnail, vidid = await YouTube.slider(query, query_type)
        buttons = slider_markup(_, vidid, user_id, query, query_type, cplay, fplay)

        if not thumbnail or not str(thumbnail).startswith("http"):
            thumbnail = config.YOUTUBE_IMG_URL

        med = InputMediaPhoto(media=thumbnail, caption=_["play_10"].format(title.title(), duration_min))

        await CallbackQuery.edit_message_media(media=med, reply_markup=InlineKeyboardMarkup(buttons))
        await CallbackQuery.answer(_["playcb_2"])
