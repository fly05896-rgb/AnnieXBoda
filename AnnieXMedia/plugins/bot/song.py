import asyncio
import functools
import inspect
import io
import math
import os
import re
from contextlib import suppress
from hashlib import md5

from pyrogram import Client, filters, raw, StopTransmission
from pyrogram.types import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import (
    BANNED_USERS,
    LOGGER_ID,
    OWNER_ID,
    SONG_DOWNLOAD_DURATION,
    SONG_DOWNLOAD_DURATION_LIMIT,
)
from strings import get_string
from AnnieXMedia import app
from AnnieXMedia.platforms import SongDownloader, YouTube
from AnnieXMedia.utils.database import (
    cache_file,
    get_cached_file,
    get_config,
    get_lang,
    set_config,
)
from AnnieXMedia.utils.inline.song import song_markup
from AnnieXMedia.utils.stream.stream import stream

SUDO_USERS = OWNER_ID if isinstance(OWNER_ID, list) else [OWNER_ID]
DEFAULT_LIST_LIMIT = 10
OWNER_USERNAME_LINK = "https://t.me/S_G0C7"


# ==========================================================
# 🚀 تسريع رفع الملفات في Kurigram 2.2.26 (4 مسارات متوازية للملفات < 10MB)
# ==========================================================
def _patch_kurigram_fast_upload() -> None:
    if getattr(Client, "_fast_upload_patched", False):
        return

    async def _fast_save_file(
        self: Client,
        path,
        file_id: int | None = None,
        file_part: int = 0,
        progress=None,
        progress_args: tuple = (),
    ):
        async with self.save_file_semaphore:
            if path is None:
                return None

            failures: list[Exception] = []

            async def worker(session):
                while True:
                    data = await queue.get()
                    if data is None:
                        return
                    try:
                        await session.invoke(data)
                    except Exception as e:
                        failures.append(e)

            part_size = 512 * 1024

            if isinstance(path, (str, os.PathLike)):
                fp = open(path, "rb")
            elif isinstance(path, io.IOBase):
                fp = path
            else:
                raise ValueError("Invalid file.")

            file_name = getattr(fp, "name", "file.jpg")
            fp.seek(0, os.SEEK_END)
            file_size = fp.tell()
            fp.seek(0)

            if file_size == 0:
                if isinstance(path, (str, os.PathLike)):
                    fp.close()
                raise ValueError("File size equals to 0 B")

            file_total_parts = int(math.ceil(file_size / part_size))
            is_big = file_size > 10 * 1024 * 1024
            # استخدام 4 مسارات متوازية حتى للملفات الصوتية والفيديوهات الصغيرة لتقليص وقت الرفع لربع الوقت
            workers_count = min(4, max(1, file_total_parts))
            is_missing_part = file_id is not None
            file_id = file_id or self.rnd_id()
            md5_sum = md5() if not is_big and not is_missing_part else None
            dc_id = await self.storage.dc_id()

            session = await self.get_session(dc_id, is_media=True)
            workers = [asyncio.create_task(worker(session)) for _ in range(workers_count)]
            queue = asyncio.Queue(8)

            try:
                fp.seek(part_size * file_part)
                while True:
                    chunk = fp.read(part_size)
                    if not chunk:
                        if not is_big and not is_missing_part:
                            md5_sum = "".join([hex(i)[2:].zfill(2) for i in md5_sum.digest()])
                        break

                    if is_big:
                        rpc = raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            file_total_parts=file_total_parts,
                            bytes=chunk,
                        )
                    else:
                        rpc = raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=file_part,
                            bytes=chunk,
                        )

                    await queue.put(rpc)

                    if is_missing_part:
                        break

                    if not is_big and not is_missing_part:
                        md5_sum.update(chunk)

                    file_part += 1

                    if progress:
                        func = functools.partial(
                            progress,
                            min(file_part * part_size, file_size),
                            file_size,
                            *progress_args,
                        )
                        if inspect.iscoroutinefunction(progress):
                            await func()
                        else:
                            await asyncio.get_running_loop().run_in_executor(self.executor, func)
            except StopTransmission:
                raise
            finally:
                for _ in workers:
                    await queue.put(None)
                await asyncio.gather(*workers)
                if isinstance(path, (str, os.PathLike)):
                    fp.close()

            if failures:
                raise failures[0]
            if is_missing_part:
                return None
            if is_big:
                return raw.types.InputFileBig(id=file_id, parts=file_total_parts, name=file_name)
            return raw.types.InputFile(
                id=file_id, parts=file_total_parts, name=file_name, md5_checksum=md5_sum
            )

    Client.save_file = _fast_save_file
    Client._fast_upload_patched = True


_patch_kurigram_fast_upload()


def clean_title(title: str) -> str:
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"\(.*?\)", "", title)
    bad_words = [
        "Official Video", "Official Audio", "Lyrics", "Video",
        "Music Video", "HD", "HQ", "4K", "ft.", "feat.",
        "Live", "Performance", "with Lyrics",
    ]
    for word in bad_words:
        title = title.replace(word, "").replace(word.lower(), "").replace(word.upper(), "")
    return title.strip() or "Track"


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
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {clean_t}",
                    callback_data=f"list_select {r.get('vidid', '')}|{user_id}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(text="المالك", url=OWNER_USERNAME_LINK),
            InlineKeyboardButton(text="إغلاق", callback_data="list_close"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# معالج ابعتلي / هات / تنزيل / تحميل / song
# ==========================================================
@app.on_message(
    filters.regex(r"^/?(ابعتلي|هات|هاتلي|تنزيل|تحميل|song)(\s+.+)?$")
    & filters.group
    & ~BANNED_USERS
)
async def smart_song_handler(client, message: Message):
    if await get_config("download_locked") and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("عذراً، التنزيل متوقف حالياً.")

    match = re.match(r"^/?(ابعتلي|هات|هاتلي|تنزيل|تحميل|song)(\s+.+)?$", message.text)
    if not match:
        return

    query = match.group(2)
    if not query or query.strip() == "":
        return await message.reply_text("يرجى كتابة الاسم.")

    query = query.strip()
    is_video_request = False

    words = query.split()
    for kw in ["فيديو", "video", "فيد"]:
        if kw in words:
            is_video_request = True
            words.remove(kw)

    query = " ".join(words).strip()
    if not query:
        return await message.reply_text("يرجى كتابة اسم الأغنية.")

    url = await YouTube.url(message)
    if not url and ("http" in query):
        url = query.split()[0]

    if url:
        return await direct_download_handler(
            client, message, url, is_video_request, show_play_btn=False
        )

    mystic = await message.reply_text("⚡ جاري التحضير...")
    try:
        details = await YouTube.details(query)
        title, duration_min, duration_sec, thumbnail, vidid = details
    except Exception:
        return await mystic.edit_text("لم يتم العثور على نتائج.")

    if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
        return await mystic.edit_text(f"مدة المقطع أكبر من {SONG_DOWNLOAD_DURATION} دقيقة.")

    if await get_config("buttons_locked"):
        yt_link = f"https://www.youtube.com/watch?v={vidid}"
        return await direct_download_handler(
            client,
            message,
            yt_link,
            is_video_request,
            show_play_btn=False,
            pre_details=details,
            mystic_msg=mystic,
        )
    else:
        with suppress(Exception):
            await mystic.delete()
        return await message.reply_photo(
            thumbnail,
            caption=f"العنوان: {title}\nالمدة: {duration_min}\n\nاختر طريقة التحميل:",
            reply_markup=InlineKeyboardMarkup(song_markup(None, vidid)),
        )


# ==========================================================
# أمر يوت (يرفع مباشرة في أسرع وقت بدون تكرار البحث)
# ==========================================================
@app.on_message(filters.command(["يوت", "yut"], prefixes=["", "/"]) & ~BANNED_USERS)
async def yut_command(client, message: Message):
    if await get_config("download_locked") and message.from_user.id not in SUDO_USERS:
        return await message.reply_text("التنزيل مغلق حالياً.")
    if len(message.command) < 2:
        return await message.reply_text("اكتب الاسم.")

    query = message.text.split(None, 1)[1]
    is_video = False

    words = query.split()
    for kw in ["فيديو", "video", "فيد"]:
        if kw in words:
            is_video = True
            words.remove(kw)

    query = " ".join(words).strip()
    if not query:
        return await message.reply_text("اكتب الاسم.")

    if "http" in query:
        return await direct_download_handler(
            client, message, query.split()[0], is_video, show_play_btn=False
        )

    mystic = await message.reply_text("⚡ جاري الرفع...")
    try:
        details = await YouTube.details(query)
        if details and details[4]:
            link = f"https://www.youtube.com/watch?v={details[4]}"
            await direct_download_handler(
                client,
                message,
                link,
                is_video,
                show_play_btn=False,
                pre_details=details,
                mystic_msg=mystic,
            )
        else:
            await mystic.edit_text("لم يتم العثور على نتائج.")
    except Exception:
        await mystic.edit_text("حدث خطأ.")


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
            response = await client.ask(
                message.chat.id,
                "ارسل اسـم الفنان الان .",
                user_id=message.from_user.id,
                timeout=30,
                reply_markup=ForceReply(selective=True),
            )
            query = response.text
        except Exception:
            return

    if "http" in query and ("list=" in query or "playlist" in query):
        await message.reply_text("تم الكشف عن بلاي ليست.. جاري المعالجة.")

        async def _process_playlist():
            try:
                limit = getattr(SongDownloader, "playlist_limit", DEFAULT_LIST_LIMIT) or DEFAULT_LIST_LIMIT
                ids = await YouTube.playlist(query, limit, message.from_user.id)
                if not ids:
                    return
                for vid in ids[:limit]:
                    try:
                        await direct_download_handler(
                            client,
                            message,
                            f"https://www.youtube.com/watch?v={vid}",
                            False,
                            show_play_btn=False,
                        )
                        await asyncio.sleep(0.5)
                    except Exception:
                        continue
            except Exception:
                return

        asyncio.create_task(_process_playlist())
        return

    mystic = await message.reply_text("جاري البحث...")
    try:
        limit = getattr(SongDownloader, "playlist_limit", DEFAULT_LIST_LIMIT) or DEFAULT_LIST_LIMIT
        results = await YouTube.search(query, limit=int(limit))
    except Exception:
        results = []

    if not results:
        return await mystic.edit_text("لم يتم العثور على نتائج.")
    with suppress(Exception):
        await mystic.delete()
    return await message.reply_text(
        "اخـتار من الـقـائمة التالية.\n\n                                       ـ",
        reply_markup=_build_list_keyboard(results, message.from_user.id),
    )


# ==========================================================
# 🚀 معالج التحميل والرفع الفوري (يرسل للجروب أولاً ثم للسجل في الخلفية)
# ==========================================================
async def direct_download_handler(
    client: Client,
    message: Message,
    url: str,
    is_video_force: bool = False,
    show_play_btn: bool = False,
    pre_details: tuple | None = None,
    mystic_msg: Message | None = None,
):
    mystic = mystic_msg or await message.reply_text("⚡ جاري الرفع...")
    try:
        if pre_details:
            title, _, duration_sec, thumbnail_url, vidid = pre_details
        else:
            try:
                details = await YouTube.details(url)
                title, duration_sec, thumbnail_url, vidid = (
                    details[0],
                    details[2],
                    details[3],
                    details[4],
                )
            except Exception:
                title, duration_sec, thumbnail_url, vidid = "Unknown", 0, None, None

        clean_full_title = clean_title(title)
        artist = (
            clean_full_title.split("-", 1)[0].strip()
            if "-" in clean_full_title
            else clean_full_title
        )
        song = (
            clean_full_title.split("-", 1)[1].strip()
            if "-" in clean_full_title
            else clean_full_title
        )
        user_mention = message.from_user.mention if message.from_user else "مستخدم"
        caption = f"الطلب: {user_mention}\nالعنوان: {song}"

        reply_markup = (
            InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="- تـشغيل الان.", callback_data=f"force_play {vidid}")]]
            )
            if show_play_btn and vidid
            else None
        )
        cache_key = f"{vidid}|{'video' if is_video_force else 'audio'}"

        # 1. فحص الكاش أولاً (إرسال فوري في 0.2 ثانية بدون تنزيل أو رفع!)
        cached = await get_cached_file(cache_key) if vidid else None
        if cached:
            try:
                if is_video_force:
                    await client.send_video(
                        message.chat.id,
                        video=cached,
                        caption=caption,
                        reply_markup=reply_markup,
                        reply_to_message_id=message.id,
                    )
                else:
                    await client.send_audio(
                        message.chat.id,
                        audio=cached,
                        caption=caption,
                        reply_markup=reply_markup,
                        reply_to_message_id=message.id,
                    )
                with suppress(Exception):
                    await mystic.delete()
                return
            except Exception:
                pass

        # 2. استخدام صورة مصغرة خفيفة (mqdefault ~15KB) وتحميلها بالتوازي مع الملف في نفس اللحظة
        fast_thumb_url = (
            f"https://i.ytimg.com/vi/{vidid}/mqdefault.jpg" if vidid else thumbnail_url
        )
        dl_task = SongDownloader.download(url, is_video=is_video_force)
        thumb_task = (
            YouTube.download_thumb(fast_thumb_url)
            if fast_thumb_url
            else asyncio.sleep(0, result=None)
        )

        (path, is_direct), thumb_path = await asyncio.gather(dl_task, thumb_task)

        if not path:
            return await mystic.edit_text("فشل التحميل.")

        if not is_video_force and not path.endswith((".m4a", ".mp3")):
            with suppress(Exception):
                new_path = path.rsplit(".", 1)[0] + ".m4a"
                os.rename(path, new_path)
                path = new_path

        # 3. إرسال الملف مباشرة لجروب العضو أولاً (بدون انتظار LOGGER_ID نهائياً!)
        sent_msg = None
        if is_video_force:
            sent_msg = await client.send_video(
                message.chat.id,
                video=path,
                caption=caption,
                duration=int(duration_sec or 0),
                thumb=thumb_path,
                supports_streaming=True,
                reply_markup=reply_markup,
                reply_to_message_id=message.id,
            )
        else:
            sent_msg = await client.send_audio(
                message.chat.id,
                audio=path,
                caption=caption,
                duration=int(duration_sec or 0),
                title=song,
                performer=artist,
                thumb=thumb_path,
                reply_markup=reply_markup,
                reply_to_message_id=message.id,
            )

        with suppress(Exception):
            await mystic.delete()

        # 4. حفظ الـ file_id وإرساله لجروب السجل وتنظيف الملفات في الخلفية (0 ثانية انتظار للعضو!)
        async def _background_cache_and_cleanup():
            try:
                file_id = None
                if sent_msg:
                    if is_video_force and getattr(sent_msg, "video", None):
                        file_id = sent_msg.video.file_id
                    elif not is_video_force and getattr(sent_msg, "audio", None):
                        file_id = sent_msg.audio.file_id
                    elif getattr(sent_msg, "document", None):
                        file_id = sent_msg.document.file_id

                if file_id and vidid:
                    await cache_file(cache_key, file_id)
                    with suppress(Exception):
                        if is_video_force:
                            await client.send_video(
                                LOGGER_ID,
                                video=file_id,
                                caption=f"ID: `{vidid}`\n{clean_full_title}",
                            )
                        else:
                            await client.send_audio(
                                LOGGER_ID,
                                audio=file_id,
                                caption=f"ID: `{vidid}`\n{clean_full_title}",
                            )
            finally:
                with suppress(Exception):
                    if not is_direct and path and os.path.exists(path):
                        os.remove(path)
                with suppress(Exception):
                    if thumb_path and os.path.exists(thumb_path):
                        os.remove(thumb_path)

        asyncio.create_task(_background_cache_and_cleanup())

    except Exception as e:
        with suppress(Exception):
            await mystic.edit_text(f"خطأ: {e}")


# ==========================================================
# Callbacks
# ==========================================================
@app.on_callback_query(filters.regex(pattern=r"list_select") & ~BANNED_USERS)
async def list_select_cb(client, query):
    try:
        vidid, uid = query.data.split(None, 1)[1].split("|", 1)
        if query.from_user.id != int(uid):
            return await query.answer("مش ليك.", show_alert=True)
        await query.answer("جاري التحضير...", show_alert=False)
        await direct_download_handler(
            client,
            query.message,
            f"https://www.youtube.com/watch?v={vidid}",
            False,
            show_play_btn=True,
        )
    except Exception:
        await query.message.reply_text("فشل.")


@app.on_callback_query(filters.regex(pattern=r"force_play") & ~BANNED_USERS)
async def force_play_cb(client, query):
    try:
        vidid = query.data.split()[1]
    except Exception:
        return
    chat_id, user_id, user_name = (
        query.message.chat.id,
        query.from_user.id,
        query.from_user.first_name,
    )
    await query.answer("جاري التشغيل...", show_alert=False)
    try:
        language = await get_lang(chat_id)
        details, _ = await YouTube.track(vidid, videoid=vidid)
        await stream(
            get_string(language),
            query.message,
            user_id,
            details,
            chat_id,
            user_name,
            chat_id,
            video=False,
            streamtype="custom",
            forceplay=True,
        )
    except Exception as e:
        await query.message.reply_text(f"فشل التشغيل: {e}")


@app.on_callback_query(filters.regex(pattern=r"list_close") & ~BANNED_USERS)
async def list_close_cb(client, query):
    with suppress(Exception):
        await query.message.edit_reply_markup(None)


@app.on_callback_query(filters.regex(pattern=r"song_back") & ~BANNED_USERS)
async def songs_back_helper(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS:
        return
    return await query.edit_message_reply_markup(
        InlineKeyboardMarkup(song_markup(None, query.data.split("|")[1]))
    )


@app.on_callback_query(filters.regex(pattern=r"song_download") & ~BANNED_USERS)
async def song_download_cb(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS:
        return
    with suppress(Exception):
        await query.answer("جاري الرفع...", show_alert=False)
    data = query.data.split("|")
    is_video = data[1].strip() == "video"
    await direct_download_handler(
        client,
        query.message,
        f"https://www.youtube.com/watch?v={data[2]}",
        is_video_force=is_video,
        show_play_btn=False,
    )


@app.on_callback_query(filters.regex(pattern=r"song_helper") & ~BANNED_USERS)
async def song_helper_cb(client, query):
    if await get_config("download_locked") and query.from_user.id not in SUDO_USERS:
        return
    with suppress(Exception):
        await query.answer("جاري الرفع...", show_alert=False)
    await direct_download_handler(
        client,
        query.message,
        f"https://www.youtube.com/watch?v={query.data.split('|')[1]}",
        not ("audio" in query.data),
        False,
    )
