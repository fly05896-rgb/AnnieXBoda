# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/core/call.py
# System: PyTgCalls V3.0.0 & NTgCalls V3.0.5 Core Call Controller
# Features: Zero-Probe FastMediaStream (~154ms First Audio Pump) + Fixed Capture Queue Check + Non-Blocking AutoEnd

import asyncio
import logging
import os
import traceback
from asyncio import Lock
from contextlib import suppress
from datetime import datetime, timedelta

import pytgcalls.ffmpeg as _pytg_ffmpeg
from pyrogram import enums, errors
from pyrogram.errors import ChatAdminRequired
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto

from pytgcalls import PyTgCalls, filters
from pytgcalls.exceptions import (
    NoActiveGroupCall,
    NoAudioSourceFound,
    NoVideoSourceFound,
    NotInCallError,
)
from pytgcalls.types import (
    AudioQuality,
    Call as TgCallData,
    ChatUpdate,
    GroupCallConfig,
    GroupCallParticipant,
    MediaStream,
    StreamEnded,
    Update,
    VideoQuality,
)

import config
from strings import get_string
from AnnieXMedia import LOGGER, YouTube, app, userbot
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import (
    add_active_chat,
    add_active_video_chat,
    get_lang,
    get_loop,
    group_assistant,
    is_active_chat,
    is_autoend,
    music_on,
    remove_active_chat,
    remove_active_video_chat,
    set_loop,
)
from AnnieXMedia.utils.errors import capture_internal_err
from AnnieXMedia.utils.exceptions import AssistantErr
from AnnieXMedia.utils.stream.autoclear import auto_clean
from AnnieXMedia.utils.thumbnails import get_thumb


# ==========================================================
# إزالة (-reconnect_at_eof 1) من أوامر ffmpeg لمنع التعليق عند نهاية الأغنية
# ==========================================================
if not getattr(_pytg_ffmpeg, "_annie_eof_patched", False):
    _orig_build_cmd = _pytg_ffmpeg.build_command

    def _patched_build_command(*args, **kwargs) -> list[str]:
        cmd = _orig_build_cmd(*args, **kwargs)
        if not cmd or cmd[0] != "ffmpeg":
            return cmd
        cleaned: list[str] = []
        skip_next = False
        for token in cmd:
            if skip_next:
                skip_next = False
                continue
            if token == "-reconnect_at_eof":
                skip_next = True
                continue
            cleaned.append(token)
        return cleaned

    _pytg_ffmpeg.build_command = _patched_build_command
    try:
        import pytgcalls.types.stream.media_stream as _ms_mod
        _ms_mod.build_command = _patched_build_command
    except Exception:
        pass
    _pytg_ffmpeg._annie_eof_patched = True


class FastMediaStream(MediaStream):
    """
    Zero-Probe MediaStream:
    يتخطى تشغيل 3 عمليات (ffprobe -h full + ffprobe -i url + ffmpeg -h full) قبل بدء الصوت،
    مما يمنع قطع الاتصال الأول مع googlevideo ويضخ أول ثانية صوت في ~154ms فقط!
    """
    async def check_stream(self):
        return


class PyTgCallsErrorFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if "UpdateGroupCall" in msg:
            return False
        if "Connection with chat id" in msg and "not found" in msg:
            return False
        return True


logging.getLogger("pyrogram.dispatcher").addFilter(PyTgCallsErrorFilter())

autoend = {}
counter = {}


def _build_stream(path: str, video: bool = False, ffmpeg_opts: str = "") -> MediaStream:
    """
    بناء مجرى الصوت والفيديو بتقنية Zero-Probe الفورية:
    - تمرير audio_path=path حتى يتم تجهيز أمر ffmpeg فوراً داخل __init__ بدون انتظار ffprobe
    - استخدام -probesize 32k -analyzeduration 0 للصوت لضخ الصوت خلال 150ms
    """
    path = str(path)
    if video:
        base_flags = "-probesize 256k -analyzeduration 400000 -fflags +fastseek+flush_packets "
    else:
        base_flags = "-probesize 32k -analyzeduration 0 -fflags +nobuffer+fastseek+flush_packets "

    final_ffmpeg = (base_flags + ffmpeg_opts).strip()

    return FastMediaStream(
        media_path=path,
        audio_path=path,
        audio_parameters=AudioQuality.HIGH,
        video_parameters=VideoQuality.HD_720p,
        audio_flags=MediaStream.Flags.REQUIRED,
        video_flags=MediaStream.Flags.REQUIRED if video else MediaStream.Flags.IGNORE,
        ffmpeg_parameters=final_ffmpeg if final_ffmpeg else None,
    )


async def _clear_(chat_id: int) -> None:
    popped = db.pop(chat_id, None)
    if popped:
        await auto_clean(popped)
    db[chat_id] = []

    with suppress(Exception):
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        await set_loop(chat_id, 0)


class Call:
    def __init__(self):
        self.userbot1 = getattr(userbot, "one", None)
        self.userbot2 = getattr(userbot, "two", None)
        self.userbot3 = getattr(userbot, "three", None)
        self.userbot4 = getattr(userbot, "four", None)
        self.userbot5 = getattr(userbot, "five", None)

        self.one: PyTgCalls | None = None
        self.two: PyTgCalls | None = None
        self.three: PyTgCalls | None = None
        self.four: PyTgCalls | None = None
        self.five: PyTgCalls | None = None

        self.active_calls: set[int] = set()
        self.chat_locks: dict[int, Lock] = {}
        self._stream_end_cache: dict[int, float] = {}
        self._joining_chats: set[int] = set()

    def get_lock(self, chat_id: int) -> Lock:
        if chat_id not in self.chat_locks:
            self.chat_locks[chat_id] = Lock()
        return self.chat_locks[chat_id]

    def is_joining(self, chat_id: int) -> bool:
        return chat_id in self._joining_chats

    async def is_truly_playing(self, chat_id: int) -> bool:
        """
        فحص حقيقي من داخل NTgCalls:
        في NTgCalls، البث الصوتي/المرئي الخارج للجمهور يُخزن في (call_info.capture) وليس (playback).
        إذا كان capture في حالة ACTIVE أو PAUSED فالأغنية شغالة فعلياً ويتم الإضافة للطابور.
        """
        if not await is_active_chat(chat_id):
            return False

        queue = db.get(chat_id)
        if not queue:
            await _clear_(chat_id)
            self.active_calls.discard(chat_id)
            return False

        if self.is_joining(chat_id):
            return True

        try:
            assistant = await group_assistant(self, chat_id)
            if not assistant:
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                return False

            ntg_calls = await assistant.calls
            call_info = ntg_calls.get(chat_id)

            # 🔥 فحص capture (البث الخارج من البوت للمكالمة) وليس playback (التسجيل)
            if call_info is None or call_info.capture == TgCallData.Status.IDLE:
                if not self.is_joining(chat_id):
                    await _clear_(chat_id)
                    self.active_calls.discard(chat_id)
                    return False
        except Exception:
            pass

        return True

    async def check_and_advance(self, chat_id: int) -> None:
        if self.is_joining(chat_id):
            return
        current_time = asyncio.get_running_loop().time()
        if chat_id in self._stream_end_cache and (current_time - self._stream_end_cache[chat_id] < 3.0):
            return
        self._stream_end_cache[chat_id] = current_time
        with suppress(Exception):
            assistant = await group_assistant(self, chat_id)
            if assistant:
                await self.play(assistant, chat_id)

    async def _edit_media_with_retry(self, message, media_obj: InputMediaPhoto, reply_markup):
        try:
            return await message.edit_media(media=media_obj, reply_markup=reply_markup)
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            with suppress(Exception):
                return await message.edit_media(media=media_obj, reply_markup=reply_markup)
        except Exception:
            return None

    async def _send_photo_with_retry(self, chat_id: int, photo, caption: str, reply_markup):
        try:
            return await app.send_photo(chat_id=chat_id, photo=photo, caption=caption, reply_markup=reply_markup)
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            with suppress(Exception):
                return await app.send_photo(chat_id=chat_id, photo=photo, caption=caption, reply_markup=reply_markup)
        except Exception:
            return None

    async def ping(self) -> str:
        pings = []
        for client in (self.one, self.two, self.three, self.four, self.five):
            if client:
                with suppress(Exception):
                    val = client.ping
                    if asyncio.iscoroutine(val):
                        val = await val
                    pings.append(float(val))
        if not pings:
            return "0.0"
        return str(round(sum(pings) / len(pings), 2))

    async def pause_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            await assistant.pause(chat_id)

    async def resume_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            await assistant.resume(chat_id)

    async def mute_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            await assistant.mute(chat_id)

    async def unmute_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            await assistant.unmute(chat_id)

    async def stop_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            await _clear_(chat_id)
            self.active_calls.discard(chat_id)
            with suppress(Exception):
                await assistant.leave_call(chat_id, close=False)

    async def force_stop_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            with suppress(Exception):
                check = db.get(chat_id)
                if check:
                    check.pop(0)

            await _clear_(chat_id)
            self.active_calls.discard(chat_id)

            with suppress(Exception):
                await assistant.leave_call(chat_id, close=False)

    async def change_volume_call(self, chat_id: int, volume: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            try:
                await assistant.change_volume_call(chat_id, volume)
            except Exception as e:
                LOGGER(__name__).error(f"Failed to change volume for {chat_id}: {e}")
                raise AssistantErr(f"Failed to change volume: {e}")

    async def seek_stream(self, chat_id: int, file_path: str, to_seek: str | int, duration: str | int, mode: str) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            ffmpeg_opts = f"-ss {to_seek} "
            is_video = mode == "video"
            stream = _build_stream(file_path, video=is_video, ffmpeg_opts=ffmpeg_opts)
            await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))

    async def speedup_stream(self, chat_id: int, file_path: str, speed: float | str, playing: list) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            spd = float(speed)
            is_video = str(playing[0].get("streamtype")) == "video"
            vidid = playing[0].get("vidid")

            target_input = file_path
            if str(file_path).startswith("vid_") or not os.path.exists(str(file_path)):
                direct = await YouTube.get_direct_link(
                    f"https://www.youtube.com/watch?v={vidid}" if vidid else str(file_path),
                    prefer_audio=not is_video,
                )
                if direct:
                    target_input = direct

            os.makedirs("downloads", exist_ok=True)
            ext = "mp4" if is_video else "m4a"
            out_file = f"downloads/speed_{chat_id}_{spd}.{ext}"

            if not os.path.exists(out_file):
                setpts = round(1.0 / spd, 4)
                if is_video:
                    cmd = [
                        "ffmpeg", "-y", "-i", str(target_input),
                        "-filter:v", f"setpts={setpts}*PTS",
                        "-filter:a", f"atempo={spd}",
                        "-preset", "ultrafast", out_file,
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y", "-i", str(target_input),
                        "-vn", "-filter:a", f"atempo={spd}",
                        out_file,
                    ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate()

            played_secs = int(playing[0].get("played", 0))
            new_played = int(played_secs / spd) if spd > 0 else 0
            old_seconds = int(playing[0].get("old_second") or playing[0].get("seconds") or 0)
            if not playing[0].get("old_dur"):
                playing[0]["old_dur"] = playing[0].get("dur")
                playing[0]["old_second"] = old_seconds

            new_total_secs = int(old_seconds / spd) if old_seconds and spd > 0 else old_seconds
            from AnnieXMedia.utils.formatters import seconds_to_min
            playing[0]["seconds"] = new_total_secs
            playing[0]["dur"] = seconds_to_min(new_total_secs)
            playing[0]["played"] = new_played
            playing[0]["speed_path"] = out_file
            playing[0]["speed"] = spd

            ffmpeg_opts = f"-ss {seconds_to_min(new_played)} " if new_played > 0 else ""
            stream = _build_stream(out_file, video=is_video, ffmpeg_opts=ffmpeg_opts)
            await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))

    async def skip_stream(self, chat_id: int, link: str, video: bool = False) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            stream = _build_stream(link, video=video)
            try:
                await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
            except (NoAudioSourceFound, NoVideoSourceFound):
                q = db.get(chat_id)
                vid = q[0].get("vidid") if q else None
                recovered = await YouTube.recover_stream(link, videoid=vid, video=video)
                if recovered:
                    stream = _build_stream(recovered, video=video)
                    await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                else:
                    raise
            self.active_calls.add(chat_id)
            await add_active_chat(chat_id)
            if video:
                await add_active_video_chat(chat_id)
            else:
                await remove_active_video_chat(chat_id)

    async def _check_autoend_bg(self, assistant: PyTgCalls, chat_id: int) -> None:
        with suppress(Exception):
            if await is_autoend():
                counter[chat_id] = {}
                users = len(await assistant.get_participants(chat_id))
                if users == 1:
                    autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link: str,
        video: bool = False,
        image: str | None = None,
    ) -> None:
        async with self.get_lock(chat_id):
            self._joining_chats.add(chat_id)
            try:
                assistant, lang = await asyncio.gather(
                    group_assistant(self, chat_id),
                    get_lang(chat_id),
                )
                _ = get_string(lang)

                if chat_id != original_chat_id:
                    with suppress(Exception):
                        chat = await app.get_chat(chat_id)
                        if chat.type == enums.ChatType.CHANNEL:
                            assistant_member = await app.get_chat_member(chat_id, assistant.me.id)
                            if assistant_member.status == enums.ChatMemberStatus.BANNED:
                                raise AssistantErr("❌ Assistant is banned in this channel.")

                stream = _build_stream(link, video=video)

                self.active_calls.add(chat_id)
                await add_active_chat(chat_id)

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                        break
                    except (NoAudioSourceFound, NoVideoSourceFound) as media_err:
                        if attempt < max_retries - 1:
                            q = db.get(chat_id)
                            vid = q[0].get("vidid") if q else None
                            recovered = await YouTube.recover_stream(link, videoid=vid, video=video)
                            if recovered:
                                link = recovered
                                stream = _build_stream(link, video=video)
                                continue
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        raise AssistantErr(f"Error: {media_err}")
                    except (NoActiveGroupCall, ChatAdminRequired, errors.GroupcallForbidden):
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.8)
                            continue
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        raise AssistantErr(_["call_8"])
                    except Exception as e:
                        err_str = str(e).lower()
                        if attempt < max_retries - 1 and (
                            "group call not found" in err_str
                            or "cannot be initialized" in err_str
                            or "groupcall_invalid" in err_str
                        ):
                            await asyncio.sleep(0.9)
                            continue
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        traceback.print_exc()
                        raise AssistantErr(f"Error: {e}")

                await music_on(chat_id)
                if video:
                    await add_active_video_chat(chat_id)
                else:
                    await remove_active_video_chat(chat_id)

                asyncio.create_task(self._check_autoend_bg(assistant, chat_id))
            finally:
                async def _release_join_flag(cid: int):
                    await asyncio.sleep(2.5)
                    self._joining_chats.discard(cid)

                asyncio.create_task(_release_join_flag(chat_id))

    async def start(self) -> None:
        LOGGER(__name__).info("Starting PyTgCalls Clients (PyTgCalls v3.0.0 & NTgCalls v3.0.5)...")
        if self.userbot1:
            self.one = PyTgCalls(self.userbot1)
        if self.userbot2:
            self.two = PyTgCalls(self.userbot2)
        if self.userbot3:
            self.three = PyTgCalls(self.userbot3)
        if self.userbot4:
            self.four = PyTgCalls(self.userbot4)
        if self.userbot5:
            self.five = PyTgCalls(self.userbot5)

        if self.one and config.STRING1:
            await self.one.start()
        if self.two and config.STRING2:
            await self.two.start()
        if self.three and config.STRING3:
            await self.three.start()
        if self.four and config.STRING4:
            await self.four.start()
        if self.five and config.STRING5:
            await self.five.start()

    async def decorators(self) -> None:
        assistants = list(filter(None, [self.one, self.two, self.three, self.four, self.five]))

        for assistant in assistants:
            @assistant.on_update(filters.stream_end())
            async def stream_end_handler(client: PyTgCalls, update: Update):
                if isinstance(update, StreamEnded):
                    chat_id = update.chat_id

                    current_time = asyncio.get_running_loop().time()
                    if chat_id in self._stream_end_cache:
                        if current_time - self._stream_end_cache[chat_id] < 2.5:
                            return
                    self._stream_end_cache[chat_id] = current_time
                    self._stream_end_cache = {
                        cid: t for cid, t in self._stream_end_cache.items() if current_time - t < 6.0
                    }

                    LOGGER(__name__).info(f"Stream ended for chat {chat_id}")
                    await self.play(client, chat_id)

            @assistant.on_update(
                filters.chat_update(
                    ChatUpdate.Status.LEFT_CALL
                    | ChatUpdate.Status.CLOSED_VOICE_CHAT
                    | ChatUpdate.Status.KICKED
                )
            )
            async def left_call_handler(client: PyTgCalls, update: Update):
                if not self.is_joining(update.chat_id):
                    await self.stop_stream(update.chat_id)

            @assistant.on_update(
                filters.call_participant(
                    GroupCallParticipant.Action.KICKED | GroupCallParticipant.Action.LEFT
                )
                & filters.me
            )
            async def kicked_handler(client: PyTgCalls, update: Update):
                if not self.is_joining(update.chat_id):
                    await self.stop_stream(update.chat_id)

    @capture_internal_err
    async def play(self, client: PyTgCalls, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            check = db.get(chat_id)
            if not check:
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                with suppress(Exception):
                    await client.leave_call(chat_id, close=False)
                return

            popped = None
            loop = await get_loop(chat_id)
            try:
                if loop == 0:
                    popped = check.pop(0)
                else:
                    loop = loop - 1
                    await set_loop(chat_id, loop)

                if popped:
                    await auto_clean(popped)

                if not check:
                    if popped and popped.get("mystic"):
                        with suppress(Exception):
                            await popped.get("mystic").delete()

                    await _clear_(chat_id)
                    self.active_calls.discard(chat_id)
                    with suppress(Exception):
                        await client.leave_call(chat_id, close=False)
                    return
            except Exception:
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                with suppress(Exception):
                    return await client.leave_call(chat_id, close=False)
                return

            queued = check[0].get("file")
            title = (check[0].get("title") or "").title()
            user = check[0].get("by")
            original_chat_id = check[0].get("chat_id")
            streamtype = check[0].get("streamtype")
            videoid = check[0].get("vidid")
            duration_str = check[0].get("dur")

            is_video = str(streamtype) == "video"
            check[0]["played"] = 0

            thumb_task = asyncio.create_task(get_thumb(videoid)) if videoid else None

            if len(check) > 1 and check[1].get("vidid"):
                next_vid = check[1].get("vidid")
                if len(str(next_vid)) == 11:
                    asyncio.create_task(YouTube._innertube_player(str(next_vid)))

            final_link = queued
            needs_yt_resolve = bool(
                videoid
                and (
                    str(queued).startswith("vid_")
                    or str(queued).startswith("live_")
                    or str(queued).startswith("http")
                    or str(streamtype) in ("youtube", "live")
                )
            )

            if needs_yt_resolve:
                try:
                    direct = await YouTube.get_direct_link(
                        f"https://www.youtube.com/watch?v={videoid}",
                        prefer_audio=not is_video,
                    )
                    if direct:
                        final_link = direct
                    else:
                        raise RuntimeError("Direct link extraction returned None")
                except Exception as e:
                    LOGGER(__name__).error(f"Queue URL Fetch Error: {e}")
                    if thumb_task:
                        thumb_task.cancel()
                    return await self.play(client, chat_id)

            stream = _build_stream(final_link, video=is_video)

            try:
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    if videoid:
                        fresh_link = await YouTube.recover_stream(
                            final_link,
                            videoid=str(videoid),
                            video=is_video,
                        )
                        if fresh_link:
                            stream = _build_stream(fresh_link, video=is_video)
                            await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                        else:
                            raise
                    else:
                        raise

                if is_video:
                    await add_active_video_chat(chat_id)
                else:
                    await remove_active_video_chat(chat_id)

                img = await thumb_task if thumb_task else config.YOUTUBE_IMG_URL
                from AnnieXMedia.utils.inline import stream_markup

                lang_code = await get_lang(chat_id)
                _ = get_string(lang_code)
                button = stream_markup(_, chat_id)

                with suppress(Exception):
                    if popped and popped.get("mystic"):
                        asyncio.create_task(popped.get("mystic").delete())
                    elif db[chat_id][0].get("mystic"):
                        asyncio.create_task(db[chat_id][0].get("mystic").delete())

                caption_text = _["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23],
                    duration_str,
                    user,
                )

                run = await self._send_photo_with_retry(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=caption_text,
                    reply_markup=InlineKeyboardMarkup(button),
                )

                if run and db.get(chat_id):
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

            except Exception as e:
                if thumb_task and not thumb_task.done():
                    thumb_task.cancel()
                traceback.print_exc()
                LOGGER(__name__).error(f"Queue Play Error: {e}")
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                with suppress(Exception):
                    await client.leave_call(chat_id, close=False)
                with suppress(Exception):
                    await app.send_message(original_chat_id, "❌ Failed to switch stream.")


StreamController = Call()
