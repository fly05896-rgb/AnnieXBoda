# Authored By Certified Coders © 2026
# System: Song Plugin (Final Version - Live Cut-Through Stream Piping to Telegram)
# Features: Zero-Disk RAM Streaming (512KB Instant Pump + 10MB Parallel Range Blocks) + Full Album Art & Artist Metadata 🎵

import os
import re
import math
import socket
import asyncio
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from typing import Callable, Optional

import aiohttp
from pyrogram import Client, StopTransmission, enums, filters, raw
from pyrogram.connection.transport.tcp.tcp import TCP
from pyrogram.session import Session
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ForceReply,
)

# استيرادات AnnieXMedia
import config
from config import (
    BANNED_USERS,
    SONG_DOWNLOAD_DURATION,
    SONG_DOWNLOAD_DURATION_LIMIT,
    OWNER_ID,
    LOGGER_ID,
)
from AnnieXMedia import app
from AnnieXMedia.platforms import YouTube, SongDownloader
from AnnieXMedia.utils.inline.song import song_markup
from AnnieXMedia.utils.database import get_config, set_config, get_cached_file, cache_file, get_lang
from strings import get_string
from AnnieXMedia.utils.stream.stream import stream

SUDO_USERS = OWNER_ID if isinstance(OWNER_ID, list) else [OWNER_ID]
DEFAULT_LIST_LIMIT = 10
OWNER_USERNAME_LINK = "https://t.me/S_G0C7"

# ── كاش فوري في الرامات بجانب كاش قاعدة البيانات ──
_RAM_FILE_CACHE: dict[str, str] = {}
_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_FAST_CRYPTO_POOL = ThreadPoolExecutor(max_workers=8)


class _StreamedTelegramFile:
    """كائن يمرر الـ InputFile المرفوع في الرامات مباشرة لدوال send_audio / send_video في كوري جرام بدون إعادة رفع"""
    def __init__(self, input_file, name: str):
        self.input_file = input_file
        self.name = name


async def _get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(
            limit=64,
            limit_per_host=32,
            ttl_dns_cache=3600,
            use_dns_cache=True,
            keepalive_timeout=300,
            force_close=False,
        )
        timeout = aiohttp.ClientTimeout(total=180, connect=5)
        _HTTP_SESSION = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _HTTP_SESSION


def _patch_kurigram_engine():
    if getattr(Client, "_cutthrough_patched", False):
        return

    # 1. رفع عدد خيوط التشفير في Kurigram من 1 إلى 8 لتشفير أجزاء الملف بالتوازي
    Session.CRYPTO_EXECUTOR_WORKERS = 8

    # 2. تفعيل TCP_NODELAY وتكبير حجم الـ Socket Buffer إلى 4MB لضخ الأجزاء بدون أي تأخير
    _orig_keepalive = TCP._enable_keepalive

    @staticmethod
    def _turbo_socket_opts(sock: socket.socket) -> None:
        _orig_keepalive(sock)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except Exception:
            pass

    TCP._enable_keepalive = _turbo_socket_opts

    # 3. تعديل save_file ليقبل الملفات المضخوخة مسبقاً في الرامات (_StreamedTelegramFile) ويرفع أي ملف محلي بـ SaveBigFilePart
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

        if isinstance(path, (raw.types.InputFile, raw.types.InputFileBig)):
            return path

        if hasattr(path, "input_file"):
            return path.input_file

        async with self.save_file_semaphore:
            part_size = 512 * 1024
            if isinstance(path, (str, os.PathLike)):
                fp = open(path, "rb")
                close_fp = True
            else:
                fp = path
                close_fp = False

            try:
                file_name = os.path.basename(str(getattr(fp, "name", "file.mp4")))
                fp.seek(0, os.SEEK_END)
                file_size = fp.tell()
                fp.seek(0)

                if file_size == 0:
                    raise ValueError("File size equals to 0 B")

                file_total_parts = int(math.ceil(file_size / part_size))
                is_missing_part = file_id is not None
                file_id = file_id or self.rnd_id()

                dc_id = await self.storage.dc_id()
                session = await self.get_session(dc_id, is_media=False)
                try:
                    if session.connection and session.connection.protocol:
                        session.connection.protocol.crypto_executor = _FAST_CRYPTO_POOL
                except Exception:
                    pass

                if file_total_parts == 1 and not is_missing_part:
                    chunk = fp.read(part_size)
                    md5_sum = md5(chunk).hexdigest()
                    await session.invoke(
                        raw.functions.upload.SaveFilePart(
                            file_id=file_id, file_part=0, bytes=chunk
                        )
                    )
                    return raw.types.InputFile(
                        id=file_id, parts=1, name=file_name, md5_checksum=md5_sum
                    )

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

                workers_count = min(12, max(2, file_total_parts))
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
                    name=file_name,
                )
            except StopTransmission:
                raise
            finally:
                if close_fp:
                    fp.close()

    Client.save_file = _fast_save_file
    Client._cutthrough_patched = True


_patch_kurigram_engine()


async def _pump_thumb_in_ram(client: Client, thumbnail_url: Optional[str], vidid: Optional[str]) -> Optional[_StreamedTelegramFile]:
    """سحب صورة بروفايل الأغنية في الرامات ورفعها لتيليجرام مباشرة بدون لمس الهارد"""
    url = thumbnail_url or (f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg" if vidid else None)
    if not url:
        return None
    try:
        http = await _get_http_session()
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            if not data or len(data) > 512 * 1024:
                return None

        dc_id = await client.storage.dc_id()
        session = await client.get_session(dc_id, is_media=False)
        file_id = client.rnd_id()
        md5_sum = md5(data).hexdigest()
        await session.invoke(
            raw.functions.upload.SaveFilePart(file_id=file_id, file_part=0, bytes=data)
        )
        inp = raw.types.InputFile(id=file_id, parts=1, name="thumb.jpg", md5_checksum=md5_sum)
        return _StreamedTelegramFile(inp, "thumb.jpg")
    except Exception:
        return None


async def _live_cutthrough_pump(
    client: Client,
    stream_info: dict,
    file_name: str,
) -> Optional[_StreamedTelegramFile]:
    """
    الضخ المباشر الحي (Cut-Through Streaming):
    يسحب البث من رابط يوتيوب المباشر على شكل بلوكات 10MB متوازية،
    وفور وصول أول 512KB في الرامات يضخها في نفس اللحظة إلى سيرفر تيليجرام أثناء نزول باقي الأجزاء!
    """
    direct_url = stream_info.get("direct_url")
    if not direct_url or not stream_info.get("can_direct_pump"):
        return None

    headers = dict(stream_info.get("headers") or {})
    headers["Accept-Encoding"] = "identity"

    part_size = 512 * 1024          # 512 KB (حجم قطعة تيليجرام القياسي)
    block_parts = 20                # 20 قطعة = 10 MiB بالظبط
    block_size = part_size * block_parts  # 10,485,760 bytes (10 MiB)

    http = await _get_http_session()
    dc_id = await client.storage.dc_id()
    tg_session = await client.get_session(dc_id, is_media=False)
    try:
        if tg_session.connection and tg_session.connection.protocol:
            tg_session.connection.protocol.crypto_executor = _FAST_CRYPTO_POOL
    except Exception:
        pass

    file_id = client.rnd_id()
    upload_sem = asyncio.Semaphore(16)
    upload_tasks: list[asyncio.Task] = []
    failures: list[Exception] = []

    async def _push_part_to_tg(part_idx: int, total_parts: int, data: bytes):
        async with upload_sem:
            if failures:
                return
            try:
                if total_parts == 1:
                    await tg_session.invoke(
                        raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=0,
                            bytes=data,
                        )
                    )
                else:
                    await tg_session.invoke(
                        raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=part_idx,
                            file_total_parts=total_parts,
                            bytes=data,
                        )
                    )
            except Exception as e:
                failures.append(e)

    async def _stream_10mb_block(block_idx: int, start_byte: int, end_byte: int, total_parts: int, first_chunk_holder: list):
        req_headers = dict(headers)
        req_headers["Range"] = f"bytes={start_byte}-{end_byte}"
        base_part = block_idx * block_parts
        part_offset = 0
        buf = bytearray()

        async with http.get(direct_url, headers=req_headers) as resp:
            if resp.status not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status}")

            # قراءة البث فور وصول أول كيلوبايت وتجميع 512KB ثم ضخها فوراً لتيليجرام بدون انتظار باقي البلوك
            async for raw_slice in resp.content.iter_chunked(65536):
                if failures:
                    return
                buf.extend(raw_slice)
                while len(buf) >= part_size:
                    chunk_bytes = bytes(buf[:part_size])
                    del buf[:part_size]
                    if total_parts == 1 and base_part + part_offset == 0:
                        first_chunk_holder.append(chunk_bytes)
                    t = asyncio.create_task(
                        _push_part_to_tg(base_part + part_offset, total_parts, chunk_bytes)
                    )
                    upload_tasks.append(t)
                    part_offset += 1

            if buf and not failures:
                chunk_bytes = bytes(buf)
                if total_parts == 1 and base_part + part_offset == 0:
                    first_chunk_holder.append(chunk_bytes)
                t = asyncio.create_task(
                    _push_part_to_tg(base_part + part_offset, total_parts, chunk_bytes)
                )
                upload_tasks.append(t)

    try:
        # معرفة الحجم الدقيق بالبايت من clen أو أول بايت
        total_size = int(stream_info.get("filesize") or 0)
        if not total_size:
            probe_headers = dict(headers)
            probe_headers["Range"] = "bytes=0-0"
            async with http.get(direct_url, headers=probe_headers) as probe:
                cr = probe.headers.get("Content-Range", "")
                if "/" in cr:
                    total_size = int(cr.split("/")[-1])
                elif probe.headers.get("Content-Length"):
                    total_size = int(probe.headers["Content-Length"])

        if total_size <= 0:
            return None

        total_parts = int(math.ceil(total_size / part_size))
        total_blocks = int(math.ceil(total_size / block_size))
        first_chunk_holder: list[bytes] = []

        # تشغيل جميع بلوكات الـ 10MB بالتوازي بحيث يتم السحب والضخ لتيليجرام في نفس اللحظة!
        block_sem = asyncio.Semaphore(6)

        async def _run_block(b_idx: int):
            async with block_sem:
                s_byte = b_idx * block_size
                e_byte = min(s_byte + block_size - 1, total_size - 1)
                await _stream_10mb_block(b_idx, s_byte, e_byte, total_parts, first_chunk_holder)

        await asyncio.gather(*[_run_block(b) for b in range(total_blocks)])
        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        if failures:
            return None

        if total_parts == 1 and first_chunk_holder:
            md5_sum = md5(first_chunk_holder[0]).hexdigest()
            inp = raw.types.InputFile(
                id=file_id, parts=1, name=file_name, md5_checksum=md5_sum
            )
        else:
            inp = raw.types.InputFileBig(
                id=file_id, parts=total_parts, name=file_name
            )
        return _StreamedTelegramFile(inp, file_name)
    except Exception:
        return None


def clean_title(title: str) -> str:
    title = re.sub(r'\[.*?\]', '', title)
    title = re.sub(r'\(.*?\)', '', title)
    bad_words = [
        "Official Video", "Official Audio", "Lyrics", "Video",
        "Music Video", "HD", "HQ", "4K", "ft.", "feat.",
        "Live", "Performance", "with Lyrics"
    ]
    for word in bad_words:
        title = title.replace(word, "").replace(word.lower(), "").replace(word.upper(), "")
    return title.strip()

@app.on_message(filters.command(["رفع الجودة", "ارفع الجودة"], prefixes="") & filters.user(SUDO_USERS))
async def enable_hq_cmd(client, message):
    SongDownloader.enable_quality()
    await message.reply_text("تم تفعيل الجودة العالية (HQ).")

@app.on_message(filters.command(["قفل الجودة", "اقفل الجودة"], prefixes="") & filters.user(SUDO_USERS))
async def disable_hq_cmd(client, message):
    SongDownloader.disable_quality()
    await message.reply_text("تم تعطيل الجودة العالية.")

@app.on_message(filters.command(["قفل التنزيل"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def lock_download(client, message):
    await set_config("download_locked", True)
    await message.reply_text("تم تعطيل التنزيل.")

@app.on_message(filters.command(["فتح التنزيل"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def unlock_download(client, message):
    await set_config("download_locked", False)
    await message.reply_text("تم تفعيل التنزيل.")

@app.on_message(filters.command(["قفل كيب البحث"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def lock_buttons(client, message):
    await set_config("buttons_locked", True)
    await message.reply_text("تم تعطيل أزرار البحث.")

@app.on_message(filters.command(["تفعيل كيب البحث"], prefixes=["", "/"]) & filters.user(SUDO_USERS))
async def unlock_buttons(client, message):
    await set_config("buttons_locked", False)
    await message.reply_text("تم تفعيل أزرار البحث.")

def _build_list_keyboard(results: list, user_id: int) -> InlineKeyboardMarkup:
    keyboard = []
    for i, r in enumerate(results[:DEFAULT_LIST_LIMIT], start=1):
        clean_t = clean_title(r.get("title", "Unknown"))[:40]
        keyboard.append([InlineKeyboardButton(text=f"{i}. {clean_t}", callback_data=f"list_select {r.get('vidid', '')}|{user_id}")])
    keyboard.append([
        InlineKeyboardButton(text="المالك", url=OWNER_USERNAME_LINK),
        InlineKeyboardButton(text="إغلاق", callback_data="list_close"),
    ])
    return InlineKeyboardMarkup(keyboard)

# ==========================================================
# معالج ابعتلي / هات (تم إصلاح استخراج كلمة فيديو)
# ==========================================================
@app.on_message(filters.regex(r"^/?(ابعتلي|هات|هاتلي|تنزيل|تحميل|song)(\s+.+)?$") & filters.group & ~BANNED_USERS)
async def smart_song_handler(client, message: Message):
    if await get_config("download_locked") and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("عذراً، التنزيل متوقف حالياً.")

    match = re.match(r"^/?(ابعتلي|هات|هاتلي|تنزيل|تحميل|song)(\s+.+)?$", message.text)
    if not match: return

    query = match.group(2)
    if not query or query.strip() == "":
        return await message.reply_text("يرجى كتابة الاسم.")

    query = query.strip()
    is_video_request = False
    
    # 🔴 التصليح: البحث الدقيق عن كلمة فيديو لإرسال الفيديو
    words = query.split()
    for kw in ["فيديو", "video", "فيد"]:
        if kw in words:
            is_video_request = True
            words.remove(kw)
            
    query = " ".join(words).strip()
    if not query:
        return await message.reply_text("يرجى كتابة اسم الأغنية.")

    url = await YouTube.url(message)
    if not url and ("http" in query): url = query

    if url:
        return await direct_download_handler(client, message, url, is_video_request, show_play_btn=False)

    if await get_config("buttons_locked"):
        return await direct_download_handler(client, message, query, is_video_request, show_play_btn=False)

    mystic = await message.reply_text("جاري البحث...")
    try:
        stream_info = await SongDownloader.extract_stream(query, is_video=is_video_request)
        if not stream_info:
            return await mystic.edit_text("لم يتم العثور على نتائج.")
        title = stream_info["title"]
        duration_min = stream_info["duration_min"]
        duration_sec = stream_info["duration_sec"]
        thumbnail = stream_info["thumbnail"]
        vidid = stream_info["vidid"]
    except: return await mystic.edit_text("لم يتم العثور على نتائج.")

    if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
        return await mystic.edit_text(f"مدة المقطع أكبر من {SONG_DOWNLOAD_DURATION} دقيقة.")

    await mystic.delete()
    return await message.reply_photo(
        thumbnail,
        caption=f"العنوان: {title}\nالمدة: {duration_min}\n\nاختر طريقة التحميل:",
        reply_markup=InlineKeyboardMarkup(song_markup(None, vidid)),
    )

# ==========================================================
# أمر يوت (الضخ المباشر في خطوة واحدة بدون بحث مزدوج)
# ==========================================================
@app.on_message(filters.command(["يوت", "yut"], prefixes=["", "/"]) & ~BANNED_USERS)
async def yut_command(client, message):
    if await get_config("download_locked") and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("التنزيل مغلق حالياً.")
    if len(message.command) < 2: return await message.reply_text("اكتب الاسم.")
    
    query = message.text.split(None, 1)[1]
    is_video = False
    
    # 🔴 التصليح: البحث الدقيق عن كلمة فيديو لإرسال الفيديو
    words = query.split()
    for kw in ["فيديو", "video", "فيد"]:
        if kw in words:
            is_video = True
            words.remove(kw)
            
    query = " ".join(words).strip()
    if not query:
        return await message.reply_text("اكتب الاسم.")
    
    if "http" in query:
        return await direct_download_handler(client, message, query.split()[0], is_video, show_play_btn=False)

    return await direct_download_handler(client, message, query, is_video, show_play_btn=False)

# ==========================================================
# أمر ليست
# ==========================================================
@app.on_message(filters.command(["ليست", "لست"], prefixes=["", "/"]) & ~BANNED_USERS)
async def list_command(client, message: Message):
    if await get_config("download_locked") and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("التنزيل مغلق حالياً.")

    query = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else None
    if not query:
        try:
            response = await client.ask(message.chat.id, "ارسل اسـم الفنان الان .", user_id=message.from_user.id, timeout=30, reply_markup=ForceReply(selective=True))
            query = response.text
        except: return 

    if "http" in query and ("list=" in query or "playlist" in query):
        await message.reply_text("تم الكشف عن بلاي ليست.. جاري المعالجة.")
        async def _process_playlist():
            try:
                limit = getattr(SongDownloader, "playlist_limit", DEFAULT_LIST_LIMIT) or DEFAULT_LIST_LIMIT
                ids = await YouTube.playlist(query, limit, message.from_user.id)
                if not ids: return
                for vid in ids[:limit]:
                    try:
                        await direct_download_handler(client, message, f"https://www.youtube.com/watch?v={vid}", False, show_play_btn=False)
                        await asyncio.sleep(1)
                    except: continue
            except: return
        asyncio.create_task(_process_playlist())
        return

    mystic = await message.reply_text("جاري البحث...")
    try:
        limit = getattr(SongDownloader, "playlist_limit", DEFAULT_LIST_LIMIT) or DEFAULT_LIST_LIMIT
        results = await YouTube.search(query, limit=int(limit))
    except: results = []

    if not results: return await mystic.edit_text("لم يتم العثور على نتائج.")
    await mystic.delete()
    return await message.reply_text("اخـتار من الـقـائمة التالية.\n\n                                       ـ", reply_markup=_build_list_keyboard(results, message.from_user.id))

# ==========================================================
# معالج الضخ المباشر الفعلي للرفع
# ==========================================================
async def direct_download_handler(client, message, url_or_query, is_video_force=False, show_play_btn=False):
    mystic = await message.reply_text("جاري المعالجة...")
    try:
        # 1. استخراج الرابط المباشر + تفاصيل الأغنية في خطوة واحدة فقط (0.15 ثانية)
        stream_info = await SongDownloader.extract_stream(url_or_query, is_video=is_video_force)
        if stream_info:
            title = stream_info["title"]
            duration_sec = stream_info["duration_sec"]
            thumbnail_url = stream_info["thumbnail"]
            vidid = stream_info["vidid"]
            url = f"https://www.youtube.com/watch?v={vidid}"
        else:
            try:
                details = await YouTube.details(url_or_query)
                title, duration_sec, thumbnail_url, vidid = details[0], details[2], details[3], details[4] if details else ("Unknown", 0, None, None)
                url = f"https://www.youtube.com/watch?v={vidid}" if vidid else url_or_query
            except:
                return await mystic.edit_text("لم يتم العثور على نتائج.")

        if not vidid or title == "Unknown":
            return await mystic.edit_text("لم يتم العثور على نتائج.")

        clean_full_title = clean_title(title)
        artist = clean_full_title.split("-", 1)[0].strip() if "-" in clean_full_title else clean_full_title
        song = clean_full_title.split("-", 1)[1].strip() if "-" in clean_full_title else clean_full_title
        caption = f"الطلب: {message.from_user.mention}\nالعنوان: {song}"

        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="- تـشغيل الان.", callback_data=f"force_play {vidid}")]]) if show_play_btn and vidid else None
        cache_key = f"{vidid}|{'video' if is_video_force else 'audio'}"
        cached = _RAM_FILE_CACHE.get(cache_key) or await get_cached_file(cache_key)

        if cached:
            _RAM_FILE_CACHE[cache_key] = cached
            await mystic.edit_text("جاري الرفع.")
            try:
                if is_video_force: await client.send_video(message.chat.id, video=cached, caption=caption, reply_markup=reply_markup, reply_to_message_id=message.id)
                else: await client.send_audio(message.chat.id, audio=cached, caption=caption, reply_markup=reply_markup, reply_to_message_id=message.id)
                return await mystic.delete()
            except: pass

        await mystic.edit_text("جاري الضخ والرفع...")
        file_id = None
        ext = ".mp4" if is_video_force else ".m4a"
        virtual_name = f"{song[:40] or vidid}{ext}"

        # 2. الضخ المباشر الحي في الرامات (أول 512KB تترفع لتليجرام فور نزولها من يوتيوب بالتوازي مع صورة البروفايل!)
        if stream_info and stream_info.get("can_direct_pump"):
            thumb_stream_task = asyncio.create_task(_pump_thumb_in_ram(client, thumbnail_url, vidid))
            media_stream_task = asyncio.create_task(_live_cutthrough_pump(client, stream_info, virtual_name))
            streamed_thumb, streamed_media = await asyncio.gather(thumb_stream_task, media_stream_task)

            if streamed_media:
                try:
                    if is_video_force:
                        sent = await client.send_video(
                            message.chat.id,
                            video=streamed_media,
                            caption=caption,
                            duration=int(duration_sec or 0),
                            thumb=streamed_thumb,
                            file_name=virtual_name,
                            supports_streaming=True,
                            reply_markup=reply_markup,
                            reply_to_message_id=message.id,
                        )
                        if sent and sent.video:
                            file_id = sent.video.file_id
                    else:
                        sent = await client.send_audio(
                            message.chat.id,
                            audio=streamed_media,
                            caption=caption,
                            duration=int(duration_sec or 0),
                            title=song,
                            performer=artist,
                            thumb=streamed_thumb,
                            file_name=virtual_name,
                            reply_markup=reply_markup,
                            reply_to_message_id=message.id,
                        )
                        if sent and sent.audio:
                            file_id = sent.audio.file_id
                except Exception:
                    file_id = None

        # 3. مسار احتياطي تلقائي في حال احتاج الملف لمعالجة ffmpeg في /dev/shm
        path = None
        is_direct = False
        if not file_id:
            thumb_task = asyncio.create_task(_pump_thumb_in_ram(client, thumbnail_url, vidid))
            dl_task = asyncio.create_task(SongDownloader.download(url, is_video=is_video_force))
            streamed_thumb, (path, is_direct) = await asyncio.gather(thumb_task, dl_task)

            if not path:
                return await mystic.edit_text("فشل التحميل.")

            if not is_video_force and not path.endswith((".m4a", ".mp3")):
                try:
                    new_path = path.rsplit(".", 1)[0] + ".m4a"
                    os.rename(path, new_path)
                    path = new_path
                except: pass

            try:
                if is_video_force:
                    sent = await client.send_video(
                        message.chat.id,
                        video=path,
                        caption=caption,
                        duration=int(duration_sec or 0),
                        thumb=streamed_thumb,
                        supports_streaming=True,
                        reply_markup=reply_markup,
                        reply_to_message_id=message.id,
                    )
                    if sent and sent.video:
                        file_id = sent.video.file_id
                else:
                    sent = await client.send_audio(
                        message.chat.id,
                        audio=path,
                        caption=caption,
                        duration=int(duration_sec or 0),
                        title=song,
                        performer=artist,
                        thumb=streamed_thumb,
                        reply_markup=reply_markup,
                        reply_to_message_id=message.id,
                    )
                    if sent and sent.audio:
                        file_id = sent.audio.file_id
            except: pass

        await mystic.delete()

        # 4. حفظ الـ file_id وإرساله لجروب السجل في الخلفية بدون تعطيل المستخدم!
        async def _bg_log_and_cleanup(fid, fpath, direct_flag):
            try:
                if fid:
                    _RAM_FILE_CACHE[cache_key] = fid
                    await cache_file(cache_key, fid)
                    if LOGGER_ID:
                        if is_video_force:
                            await client.send_video(LOGGER_ID, video=fid, caption=f"ID: `{vidid}`\n{clean_full_title}")
                        else:
                            await client.send_audio(LOGGER_ID, audio=fid, caption=f"ID: `{vidid}`\n{clean_full_title}")
            except Exception:
                pass
            finally:
                try:
                    if not direct_flag and fpath and os.path.exists(fpath):
                        os.remove(fpath)
                except Exception:
                    pass

        asyncio.create_task(_bg_log_and_cleanup(file_id, path, is_direct))

    except Exception as e: await mystic.edit_text(f"خطأ: {e}")

# ==========================================================
# Callbacks
# ==========================================================
@app.on_callback_query(filters.regex(pattern=r"list_select") & ~BANNED_USERS)
async def list_select_cb(client, query):
    try:
        vidid, uid = query.data.split(None, 1)[1].split("|", 1)
        if query.from_user.id != int(uid): return await query.answer("مش ليك.", show_alert=True)
        await query.answer("جاري التحضير...", show_alert=False)
        await direct_download_handler(client, query.message, f"https://www.youtube.com/watch?v={vidid}", False, show_play_btn=True)
    except: await query.message.reply_text("فشل.")

@app.on_callback_query(filters.regex(pattern=r"force_play") & ~BANNED_USERS)
async def force_play_cb(client, query):
    try: vidid = query.data.split()[1]
    except: return
    chat_id, user_id, user_name = query.message.chat.id, query.from_user.id, query.from_user.first_name
    await query.answer("جاري التشغيل...", show_alert=False)
    try:
        language = await get_lang(chat_id)
        details, _ = await YouTube.track(vidid, videoid=vidid)
        await stream(get_string(language), query.message, user_id, details, chat_id, user_name, chat_id, video=False, streamtype="custom", forceplay=True)
    except Exception as e: await query.message.reply_text(f"فشل التشغيل: {e}")

@app.on_callback_query(filters.regex(pattern=r"list_close") & ~BANNED_USERS)
async def list_close_cb(client, query):
    try: await query.message.edit_reply_markup(None)
    except: pass
    
@app.on_callback_query(filters.regex(pattern=r"song_back") & ~BANNED_USERS)
async def songs_back_helper(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS: return
    return await query.edit_message_reply_markup(InlineKeyboardMarkup(song_markup(None, query.data.split("|")[1])))

@app.on_callback_query(filters.regex(pattern=r"song_download") & ~BANNED_USERS)
async def song_download_cb(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS: return
    try: await query.answer("جاري...", show_alert=True)
    except: pass
    data = query.data.split("|")
    # 🔴 التصليح: تحديد إن الزر المنداس عليه هو فيديو ولا صوت بشكل سليم
    is_video = True if data[1].strip() == "video" else False
    await direct_download_handler(client, query.message, f"https://www.youtube.com/watch?v={data[2]}", is_video_force=is_video, show_play_btn=False)

@app.on_callback_query(filters.regex(pattern=r"song_helper") & ~BANNED_USERS)
async def song_helper_cb(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS: return
    try: await query.answer("جاري...", show_alert=True)
    except: pass
    await direct_download_handler(client, query.message, f"https://www.youtube.com/watch?v={query.data.split('|')[1]}", not ("audio" in query.data), False)
