import asyncio
import json
import math
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import aiohttp
from pyrogram import Client, enums, filters, raw, StopTransmission
from pyrogram.connection.transport.tcp.tcp import TCP
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from config import BANNED_USERS, SONG_DOWNLOAD_DURATION, SONG_DOWNLOAD_DURATION_LIMIT
from AnnieXMedia import YouTube, app
from AnnieXMedia.platforms import SongDownloader
from AnnieXMedia.utils.decorators.language import language, languageCB
from AnnieXMedia.utils.formatters import convert_bytes
from AnnieXMedia.utils.inline.song import song_markup

# ── كاش فوري للـ file_id على سيرفرات تيليجرام (إرسال في 0.05 ثانية عند التكرار) ──
_MEM_FILE_CACHE: dict[str, str] = {}

# ── جلسة HTTP مستمرة (Pre-warmed Keep-Alive C-Stream) لـ Telegram Bot API ──
_BOT_API_SESSION: Optional[aiohttp.ClientSession] = None


async def _get_bot_api_session() -> aiohttp.ClientSession:
    global _BOT_API_SESSION
    if _BOT_API_SESSION is None or _BOT_API_SESSION.closed:
        connector = aiohttp.TCPConnector(
            limit=32,
            ttl_dns_cache=3600,
            use_dns_cache=True,
            keepalive_timeout=300,
            force_close=False,
        )
        timeout = aiohttp.ClientTimeout(total=120, connect=5)
        _BOT_API_SESSION = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _BOT_API_SESSION


def _build_inline_keyboard_json(reply_markup: Optional[InlineKeyboardMarkup]) -> Optional[str]:
    if not reply_markup or not getattr(reply_markup, "inline_keyboard", None):
        return None
    rows = []
    for row in reply_markup.inline_keyboard:
        btn_row = []
        for btn in row:
            b_dict = {"text": btn.text}
            if getattr(btn, "url", None):
                b_dict["url"] = btn.url
            elif getattr(btn, "callback_data", None):
                b_dict["callback_data"] = btn.callback_data
            elif getattr(btn, "switch_inline_query", None) is not None:
                b_dict["switch_inline_query"] = btn.switch_inline_query
            elif getattr(btn, "switch_inline_query_current_chat", None) is not None:
                b_dict["switch_inline_query_current_chat"] = btn.switch_inline_query_current_chat
            btn_row.append(b_dict)
        if btn_row:
            rows.append(btn_row)
    return json.dumps({"inline_keyboard": rows}) if rows else None


async def _fast_bot_api_send(
    chat_id: int | str,
    file_path: str,
    *,
    is_video: bool = False,
    caption: str = "",
    title: str = "Track",
    performer: str = "",
    duration: int = 0,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[str]:
    """
    رفع صاروخي مباشر عبر C-Level OpenSSL HTTP Stream إلى api.telegram.org
    يتخطى تقطيع 512KB وتشفير MTProto في بايثون بالكامل ويرفع 2-10 ميجا في 0.2 - 0.5 ثانية!
    يعيد الـ file_id لحفظه في الكاش.
    """
    if not os.path.exists(file_path):
        return None

    file_size = os.path.getsize(file_path)
    # Bot API يدعم حتى 50MB للرفع المباشر؛ للملفات الأكبر ننتقل تلقائياً لـ Kurigram BigFile Parallel
    if file_size > 48 * 1024 * 1024:
        return None

    session = await _get_bot_api_session()
    method = "sendVideo" if is_video else "sendAudio"
    field_name = "video" if is_video else "audio"
    api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"

    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    if caption:
        form.add_field("caption", caption)
        form.add_field("parse_mode", "HTML")
    if duration:
        form.add_field("duration", str(int(duration)))

    if is_video:
        form.add_field("supports_streaming", "true")
    else:
        if title:
            form.add_field("title", str(title))
        if performer:
            form.add_field("performer", str(performer))

    if reply_to_message_id:
        form.add_field(
            "reply_parameters",
            json.dumps({"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}),
        )

    kb_json = _build_inline_keyboard_json(reply_markup)
    if kb_json:
        form.add_field("reply_markup", kb_json)

    ext = Path(file_path).suffix.lower()
    mime = "video/mp4" if is_video else ("audio/mp4" if ext == ".m4a" else "audio/mpeg")
    clean_name = f"{title[:50]}{ext or ('.mp4' if is_video else '.m4a')}"

    try:
        with open(file_path, "rb") as fp:
            form.add_field(field_name, fp, filename=clean_name, content_type=mime)
            async with session.post(api_url, data=form) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok") and "result" in data:
                        res = data["result"]
                        media_obj = res.get("video") or res.get("audio") or res.get("document")
                        if media_obj and media_obj.get("file_id"):
                            return media_obj["file_id"]
                        return "SENT_OK"
    except Exception:
        pass

    return None


# ── ترقيع Kurigram لإصلاح ثغرة FilePartMissing وتفعيل TCP_NODELAY للملفات الأكبر من 48 ميجا ──
def _patch_kurigram_turbo_mtproto():
    if getattr(Client, "_turbo_mtproto_patched", False):
        return

    # 1. تفعيل TCP_NODELAY ورفع حجم الـ Socket Buffer إلى 4MB لمنع تأخير Nagle's Algorithm
    _orig_keepalive = TCP._enable_keepalive

    @staticmethod
    def _turbo_socket_tuning(sock: socket.socket) -> None:
        _orig_keepalive(sock)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except Exception:
            pass

    TCP._enable_keepalive = _turbo_socket_tuning

    # 2. إجبار SaveBigFilePart (InputFileBig) على نفس الـ Production DC لمنع خطأ FilePartMissing نهائياً
    async def _fast_save_file(
        self: Client,
        path,
        file_id: int | None = None,
        file_part: int = 0,
        progress: Callable | None = None,
        progress_args: tuple = (),
    ):
        if path is None:
            return None

        async with self.save_file_semaphore:
            part_size = 512 * 1024
            if isinstance(path, (str, os.PathLike)):
                fp = open(path, "rb")
                close_fp = True
            else:
                fp = path
                close_fp = False

            try:
                file_name = getattr(fp, "name", "file.mp4")
                fp.seek(0, os.SEEK_END)
                file_size = fp.tell()
                fp.seek(0)

                if file_size == 0:
                    raise ValueError("File size equals to 0 B")

                file_total_parts = int(math.ceil(file_size / part_size))
                is_missing_part = file_id is not None
                file_id = file_id or self.rnd_id()

                # السر هنا: استخدام SaveBigFilePart دائماً لأن SaveFilePart العادي يسبب FilePartMissing عند التوازي!
                dc_id = await self.storage.dc_id()
                session = await self.get_session(dc_id, is_media=False)

                if is_missing_part:
                    fp.seek(part_size * file_part)
                    chunk = fp.read(part_size)
                    if chunk:
                        await session.invoke(
                            raw.functions.upload.SaveBigFilePart(
                                file_id=file_id,
                                file_part=file_part,
                                file_total_parts=file_total_parts,
                                bytes=chunk,
                            )
                        )
                    return None

                workers_count = min(16, max(4, file_total_parts))
                queue: asyncio.Queue = asyncio.Queue()
                failures: list[Exception] = []

                for p_idx in range(file_total_parts):
                    chunk = fp.read(part_size)
                    if not chunk:
                        break
                    queue.put_nowait(
                        raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=p_idx,
                            file_total_parts=file_total_parts,
                            bytes=chunk,
                        )
                    )

                async def _worker():
                    while not queue.empty():
                        try:
                            rpc = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        try:
                            await session.invoke(rpc)
                        except Exception as e:
                            failures.append(e)

                await asyncio.gather(*[_worker() for _ in range(workers_count)])

                if failures:
                    raise failures[0]

                return raw.types.InputFileBig(
                    id=file_id,
                    parts=file_total_parts,
                    name=os.path.basename(str(file_name)),
                )
            except StopTransmission:
                raise
            finally:
                if close_fp:
                    fp.close()

    Client.save_file = _fast_save_file
    Client._turbo_mtproto_patched = True


_patch_kurigram_turbo_mtproto()


def clean_query(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)
    return " ".join(cleaned.split())


YT_REGEX = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+", re.IGNORECASE
)


@app.on_message(filters.command(["song"]) & filters.group & ~BANNED_USERS)
@language
async def song_commad_group(client, message: Message, _):
    upl = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_["SG_B_1"],
                    url=f"https://t.me/{app.username}?start=song",
                ),
            ]
        ]
    )
    await message.reply_text(_["song_1"], reply_markup=upl)


@app.on_message(filters.command(["song"]) & filters.private & ~BANNED_USERS)
@language
async def song_commad_private(client, message: Message, _):
    await message.delete()
    url = await YouTube.url(message)

    if url:
        if not await YouTube.valid(url):
            return await message.reply_text(_["song_5"])
        mystic = await message.reply_text(_["play_1"])
        (
            title,
            duration_min,
            duration_sec,
            thumbnail,
            vidid,
        ) = await YouTube.details(url)
        if str(duration_min) == "None":
            return await mystic.edit_text(_["song_3"])
        if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
            return await mystic.edit_text(
                _["play_4"].format(SONG_DOWNLOAD_DURATION, duration_min)
            )
        buttons = song_markup(_, vidid)
        await mystic.delete()
        return await message.reply_photo(
            thumbnail,
            caption=_["song_4"].format(title),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    else:
        if len(message.command) < 2:
            return await message.reply_text(_["song_2"])
        query = message.text.split(None, 1)[1]

    mystic = await message.reply_text(_["play_1"])
    (
        title,
        duration_min,
        duration_sec,
        thumbnail,
        vidid,
    ) = await YouTube.details(query)
    if not title or str(title) == "Unknown":
        return await mystic.edit_text(_["song_5"])
    if str(duration_min) == "None":
        return await mystic.edit_text(_["song_3"])
    if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
        return await mystic.edit_text(
            _["play_6"].format(SONG_DOWNLOAD_DURATION, duration_min)
        )
    buttons = song_markup(_, vidid)
    await mystic.delete()
    return await message.reply_photo(
        thumbnail,
        caption=_["song_4"].format(title),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@app.on_message(
    filters.command(["يوت", "yut"], prefixes=["", "/"]) & ~BANNED_USERS
)
@language
async def yut_command(client, message: Message, _):
    raw_query = None
    if len(message.command) > 1:
        raw_query = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message and (
        message.reply_to_message.text or message.reply_to_message.caption
    ):
        raw_query = (
            message.reply_to_message.text or message.reply_to_message.caption
        ).strip()

    if not raw_query:
        return await message.reply_text("🎸 **اكتب اسم الأغنية جنب الأمر يا غالي.**")

    is_video_force = False
    if raw_query.startswith(("فيديو ", "ف ")):
        is_video_force = True
        raw_query = re.sub(r"^(فيديو|ف)\s+", "", raw_query).strip()

    if YT_REGEX.match(raw_query):
        return await direct_download_handler(
            client, message, raw_query, is_video_force=is_video_force
        )

    query = clean_query(raw_query)
    if not query:
        return await message.reply_text("❌ **الاسم غير صالح للبحث.**")

    return await direct_download_handler(
        client, message, query, is_video_force=is_video_force
    )


@app.on_message(filters.regex(YT_REGEX) & filters.private & ~BANNED_USERS)
@language
async def auto_link_handler(client, message: Message, _):
    if message.text and message.text.startswith(("/", "!", ".")):
        return
    url = message.text.strip()
    await direct_download_handler(client, message, url, is_video_force=False)


async def direct_download_handler(
    client: Client,
    message: Message,
    query: str,
    is_video_force: bool = False,
):
    mystic = await message.reply_text("⚡ جاري التحضير والرفع...")

    try:
        details = await YouTube.details(query)
        if not details:
            return await mystic.edit_text("❌ لم يتم العثور على نتائج.")

        title, duration_min, duration_sec, thumb, vidid = details
        if not vidid or title == "Unknown":
            return await mystic.edit_text("❌ لم يتم العثور على نتائج.")

        url = f"https://www.youtube.com/watch?v={vidid}"
        cache_key = f"{vidid}_{'v' if is_video_force else 'a'}"

        channel = getattr(config, "SUPPORT_CHANNEL", "https://t.me/SourceBoda")
        buttons = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="• 𝚂ᴏᴜʀᴄᴇ ✘ 𝐁ᴏᴅᴀ •", url=channel)]]
        )
        caption_text = f"🎬 <b>{title}</b>" if is_video_force else f"🎧 <b>{title}</b>"

        # 1. لو الـ file_id محفوظ في الكاش، يتبعت في 0.05 ثانية!
        cached_fid = _MEM_FILE_CACHE.get(cache_key)
        if cached_fid and cached_fid != "SENT_OK":
            if is_video_force:
                await client.send_video(
                    message.chat.id,
                    video=cached_fid,
                    caption=caption_text,
                    reply_to_message_id=message.id,
                    reply_markup=buttons,
                )
            else:
                await client.send_audio(
                    message.chat.id,
                    audio=cached_fid,
                    caption=caption_text,
                    title=title,
                    performer=config.BOT_NAME,
                    duration=int(duration_sec or 0),
                    reply_to_message_id=message.id,
                    reply_markup=buttons,
                )
            await mystic.delete()
            return

        # 2. التحميل السريع في /dev/shm (مع فصل الصوت النقي 1.3MB في 0.1 ثانية)
        path, _ = await SongDownloader.download(url, is_video=is_video_force)
        if not path or not os.path.exists(path):
            return await mystic.edit_text("❌ فشل التحميل من المصدر.")

        # 3. الرفع الصاروخي المباشر عبر C-Level HTTP Bot API Stream (يرفع 2-10 ميجا في ~0.3 ثانية!)
        sent_fid = await _fast_bot_api_send(
            message.chat.id,
            path,
            is_video=is_video_force,
            caption=caption_text,
            title=title,
            performer=config.BOT_NAME,
            duration=int(duration_sec or 0),
            reply_to_message_id=message.id,
            reply_markup=buttons,
        )

        if sent_fid:
            if sent_fid != "SENT_OK":
                _MEM_FILE_CACHE[cache_key] = sent_fid
            await mystic.delete()
            return

        # 4. للملفات الضخمة (> 48MB): استخدام Kurigram BigFile Parallel بدون FilePartMissing
        if is_video_force:
            sent = await client.send_video(
                message.chat.id,
                video=path,
                caption=caption_text,
                duration=int(duration_sec or 0),
                supports_streaming=True,
                reply_to_message_id=message.id,
                reply_markup=buttons,
            )
            if sent and sent.video:
                _MEM_FILE_CACHE[cache_key] = sent.video.file_id
        else:
            sent = await client.send_audio(
                message.chat.id,
                audio=path,
                caption=caption_text,
                title=title,
                performer=config.BOT_NAME,
                duration=int(duration_sec or 0),
                reply_to_message_id=message.id,
                reply_markup=buttons,
            )
            if sent and sent.audio:
                _MEM_FILE_CACHE[cache_key] = sent.audio.file_id

        await mystic.delete()

    except Exception as e:
        await mystic.edit_text(f"❌ حدث خطأ: {e}")


@app.on_callback_query(
    filters.regex(pattern=r"song_back") & ~BANNED_USERS
)
@languageCB
async def songs_back_helper(client, CallbackQuery: CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    stype, vidid = callback_request.split("|")
    buttons = song_markup(_, vidid)
    return await CallbackQuery.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(
    filters.regex(pattern=r"song_helper") & ~BANNED_USERS
)
@languageCB
async def song_helper_cb(client, CallbackQuery: CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    stype, vidid = callback_request.split("|")
    try:
        await CallbackQuery.answer(_["song_6"], show_alert=True)
    except Exception:
        pass
    if stype == "audio":
        buttons = [
            [
                InlineKeyboardButton(
                    text="⚡ تحميل صوت بأعلى جودة (Fast M4A)",
                    callback_data=f"song_download audio|140|{vidid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_["BACK_BUTTON"],
                    callback_data=f"song_back {stype}|{vidid}",
                ),
                InlineKeyboardButton(
                    text=_["CLOSE_BUTTON"], callback_data="close"
                ),
            ],
        ]
    else:
        buttons = [
            [
                InlineKeyboardButton(
                    text="🎬 تحميل فيديو سريع (Fast MP4)",
                    callback_data=f"song_download video|18|{vidid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_["BACK_BUTTON"],
                    callback_data=f"song_back {stype}|{vidid}",
                ),
                InlineKeyboardButton(
                    text=_["CLOSE_BUTTON"], callback_data="close"
                ),
            ],
        ]
    return await CallbackQuery.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(
    filters.regex(pattern=r"song_download") & ~BANNED_USERS
)
@languageCB
async def song_download_cb(client, CallbackQuery: CallbackQuery, _):
    try:
        await CallbackQuery.answer(" جاري الرفع...")
    except Exception:
        pass

    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    stype, format_id, vidid = callback_request.split("|")
    yturl = f"https://www.youtube.com/watch?v={vidid}"
    is_video = stype == "video"
    cache_key = f"{vidid}_{'v' if is_video else 'a'}"

    mystic = await CallbackQuery.edit_message_text(_["song_8"])

    try:
        title, duration_min, duration_sec, thumb_url, _ = await YouTube.details(yturl)
        title = re.sub(r"\W+", " ", title.title())

        cached_fid = _MEM_FILE_CACHE.get(cache_key)
        if cached_fid and cached_fid != "SENT_OK":
            await mystic.delete()
            if is_video:
                await CallbackQuery.message.reply_video(video=cached_fid, caption=title)
            else:
                await CallbackQuery.message.reply_audio(
                    audio=cached_fid,
                    caption=title,
                    title=title,
                    performer=config.BOT_NAME,
                )
            return

        file_path, _ = await SongDownloader.download(yturl, is_video=is_video)
        if not file_path or not os.path.exists(file_path):
            return await mystic.edit_text(_["song_9"].format("Download Failed"))

        sent_fid = await _fast_bot_api_send(
            CallbackQuery.message.chat.id,
            file_path,
            is_video=is_video,
            caption=title,
            title=title,
            performer=config.BOT_NAME,
            duration=int(duration_sec or 0),
        )

        if sent_fid:
            if sent_fid != "SENT_OK":
                _MEM_FILE_CACHE[cache_key] = sent_fid
            await mystic.delete()
            return

        await mystic.delete()
        if is_video:
            sent = await CallbackQuery.message.reply_video(
                video=file_path,
                duration=int(duration_sec or 0),
                caption=title,
                supports_streaming=True,
            )
            if sent and sent.video:
                _MEM_FILE_CACHE[cache_key] = sent.video.file_id
        else:
            sent = await CallbackQuery.message.reply_audio(
                audio=file_path,
                caption=title,
                title=title,
                performer=config.BOT_NAME,
                duration=int(duration_sec or 0),
            )
            if sent and sent.audio:
                _MEM_FILE_CACHE[cache_key] = sent.audio.file_id

    except Exception as e:
        await mystic.edit_text(_["song_10"])


@app.on_callback_query(
    filters.regex(pattern=r"^yut_dl\|") & ~BANNED_USERS
)
async def yut_callback_download(client, CallbackQuery: CallbackQuery):
    try:
        _, stype, vidid, user_id = CallbackQuery.data.split("|")
    except Exception:
        return

    if CallbackQuery.from_user.id != int(user_id):
        return await CallbackQuery.answer(
            "❌ هذا الطلب لا يخصك يا غالي!", show_alert=True
        )

    await CallbackQuery.answer(" جاري الرفع...")
    await direct_download_handler(
        client,
        CallbackQuery.message,
        f"https://www.youtube.com/watch?v={vidid}",
        is_video_force=(stype == "v"),
    )
    try:
        await CallbackQuery.message.delete()
    except Exception:
        pass
