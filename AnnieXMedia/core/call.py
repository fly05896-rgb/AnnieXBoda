# Authored By Certified Coders © 2026
# File Path: AnnieXMedia/core/call.py
# System: PyTgCalls v3.0.0 & NTgCalls v3.0.5 Ultra-Fast Zero-Jitter Call Controller
# Fixes: Queue Protection + Shared Userbot Sessions + FastMediaStream + Zero Stutter Audio

import asyncio
import logging
import os
import traceback
from asyncio import Lock
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Union

import pytgcalls.ffmpeg as _pytg_ffmpeg
from ntgcalls import TelegramServerError
from pyrogram import enums, errors
from pyrogram.errors import ChatAdminRequired
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto
from pytgcalls import PyTgCalls, filters
from pytgcalls.exceptions import (
    NoActiveGroupCall,
    NoAudioSourceFound,
    NoVideoSourceFound,
)
from pytgcalls.types import (
    Call as TgCallData,
    ChatUpdate,
    GroupCallConfig,
    GroupCallParticipant,
    MediaStream,
    StreamEnded,
    Update,
    VideoQuality,
)
from pytgcalls.types.raw import AudioParameters

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
from AnnieXMedia.utils.formatters import check_duration, seconds_to_min, speed_converter
from AnnieXMedia.utils.inline.play import stream_markup
from AnnieXMedia.utils.stream.autoclear import auto_clean
from AnnieXMedia.utils.thumbnails import get_thumb

# ==========================================================
# إزالة (-reconnect_at_eof 1) من أوامر ffmpeg فقط ليغلق فور انتهاء الأغنية
# وينتقل للأغنية التالية في قائمة الانتظار بدون تعليق
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

# التردد الأصلي لمحرك WebRTC Opus في تيليجرام و ntgcalls هو 48000Hz Stereo (2 Channels)
NATIVE_OPUS_AUDIO = AudioParameters(bitrate=48000, channels=2)


class FastMediaStream(MediaStream):
    """
    يتخطى فحص ffprobe البطيء في pytgcalls ليبدأ ضخ الصوت فوراً (~150ms)
    مع الحفاظ على استقرار أنبوب الـ PCM المرسل إلى ntgcalls ThreadedReader.
    """
    async def check_stream(self):
        if (
            self._video_flags & MediaStream.Flags.IGNORE
            and self._audio_path
            and not str(self._audio_path).startswith(("http://", "https://"))
            and os.path.exists(str(self._audio_path))
        ):
            return
        if (
            self._video_flags & MediaStream.Flags.IGNORE
            and self._audio_path
            and str(self._audio_path).startswith(("http://", "https://"))
        ):
            return
        await super().check_stream()


def _build_stream(
    link: str,
    video: bool = False,
    additional_ffmpeg_parameters: str = "",
    headers: dict = None,
) -> MediaStream:
    s_link = str(link)
    is_local = os.path.exists(s_link)
    is_http = s_link.startswith(("http://", "https://"))

    # فلتر تنعيم ومزامنة الساعة الرقمية لمنع أي تقطيع (Jitter / Underrun) في ntgcalls
    af_chain = "aresample=osr=48000:ochl=stereo:async=1000:first_pts=0"

    if is_local:
        base_ff = (
            "-nostdin -threads 4 -thread_queue_size 4096 "
            "-probesize 64k -analyzeduration 0"
        )
    elif is_http:
        # بدون -fflags +nobuffer حتى يحتفظ ffmpeg بالـ Read-Ahead Buffer في الرامات (50GB RAM)
        base_ff = (
            "-nostdin -threads 4 -thread_queue_size 4096 "
            "-probesize 64k -analyzeduration 0 "
            "-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 "
            "-reconnect_on_http_error 4xx,5xx -reconnect_delay_max 2 "
            "-rw_timeout 10000000"
        )
    else:
        base_ff = "-nostdin -threads 4 -thread_queue_size 4096 -probesize 64k -analyzeduration 0"

    if additional_ffmpeg_parameters:
        extra = additional_ffmpeg_parameters.strip()
        if "-af " in extra or "-filter:a" in extra:
            ffmpeg_params = f"{base_ff} {extra}"
        else:
            ffmpeg_params = f"{base_ff} {extra} -_-mid -af {af_chain}"
    else:
        ffmpeg_params = f"{base_ff} -_-mid -af {af_chain}"

    # عدم تمرير headers إذا كان الفيديو مفعلاً حتى لا يرفض ffprobe صيغة -headers بدون CRLF
    effective_headers = None if video else headers

    return FastMediaStream(
        media_path=s_link,
        audio_parameters=NATIVE_OPUS_AUDIO,
        video_parameters=VideoQuality.HD_720p,
        audio_flags=MediaStream.Flags.REQUIRED,
        video_flags=MediaStream.Flags.REQUIRED if video else MediaStream.Flags.IGNORE,
        ffmpeg_parameters=ffmpeg_params,
        headers=effective_headers,
    )


async def _clear_(chat_id: int) -> None:
    popped = db.pop(chat_id, None)
    if popped:
        with suppress(Exception):
            await auto_clean(popped)
    db[chat_id] = []
    with suppress(Exception):
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        await set_loop(chat_id, 0)


class Call:
    def __init__(self):
        # استخدام جلسات userbot المشتركة لمنع تعارض الجلسات المزدوجة (AuthKeyDuplicated)
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

    def _get_lock(self, chat_id: int) -> Lock:
        return self.get_lock(chat_id)

    def is_joining(self, chat_id: int) -> bool:
        return chat_id in self._joining_chats

    async def is_truly_playing(self, chat_id: int) -> bool:
        """
        فحص دقيق ومتكامل لحالة التشغيل الفعلية لمنع تخطي الأغنية الشغالة عند إضافة أغنية ثانية:
        1. إذا كان المساعد في مرحلة الانضمام حالياً (_joining_chats)، يرجع True فوراً لحماية الأغنية الأولى.
        2. إذا كانت قائمة الانتظار فارغة، يرجع False.
        3. يفحص حالة call_info.capture (إرسال الصوت/الفيديو من البوت للمكالمة في NTgCalls).
        """
        if self.is_joining(chat_id):
            return True

        queue = db.get(chat_id)
        if not queue:
            self.active_calls.discard(chat_id)
            return False

        if not await is_active_chat(chat_id) and chat_id not in self.active_calls:
            return False

        try:
            assistant = await group_assistant(self, chat_id)
            if not assistant:
                return False

            ntg_calls = await assistant.calls
            call_info = ntg_calls.get(chat_id)

            if call_info is None:
                # إذا لم يسجل NTgCalls المكالمة بعد ولكن تم تشغيلها منذ ثوانٍ قليلة
                if len(queue) >= 1 and int(queue[0].get("played") or 0) <= 5 and chat_id in self.active_calls:
                    return True
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                return False

            # في سورس ntgcalls.cpp: capture هو بث البوت للمكالمة (PLAYING / PAUSED)، أما playback فهو للتسجيل فقط
            if call_info.capture == TgCallData.Status.IDLE:
                if len(queue) >= 1 and int(queue[0].get("played") or 0) <= 5 and chat_id in self.active_calls:
                    return True
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                return False

            if len(queue) == 1:
                dur_sec = int(queue[0].get("seconds") or 0)
                played_sec = int(queue[0].get("played") or 0)
                if dur_sec > 0 and played_sec >= dur_sec + 8:
                    await _clear_(chat_id)
                    self.active_calls.discard(chat_id)
                    return False

            return True
        except Exception:
            return bool(queue and (chat_id in self.active_calls or await is_active_chat(chat_id)))

    async def check_and_advance(self, chat_id: int) -> None:
        """حارس تلقائي يستدعيه seeker.py عند انتهاء وقت الأغنية لضمان الانتقال للتالي في الطابور"""
        if self.is_joining(chat_id):
            return
        current_time = asyncio.get_running_loop().time()
        if chat_id in self._stream_end_cache and (current_time - self._stream_end_cache[chat_id] < 3.0):
            return
        self._stream_end_cache[chat_id] = current_time
        with suppress(Exception):
            assistant = await group_assistant(self, chat_id)
            if assistant:
                await self.change_stream(assistant, chat_id)

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
        return str(round(sum(pings) / len(pings), 2)) if pings else "0.0"

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
            self._joining_chats.discard(chat_id)
            with suppress(Exception):
                await assistant.leave_call(chat_id, close=False)

    async def stop_stream_force(self, chat_id: int) -> None:
        for client in (self.one, self.two, self.three, self.four, self.five):
            if client:
                with suppress(Exception):
                    await client.leave_call(chat_id, close=False)
        await _clear_(chat_id)
        self.active_calls.discard(chat_id)
        self._joining_chats.discard(chat_id)

    async def force_stop_stream(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            with suppress(Exception):
                check = db.get(chat_id)
                if check:
                    check.pop(0)
            await _clear_(chat_id)
            self.active_calls.discard(chat_id)
            self._joining_chats.discard(chat_id)
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

    async def seek_stream(
        self,
        chat_id: int,
        file_path: str,
        to_seek: str | int,
        duration: str | int,
        mode: str,
    ) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            is_video = mode == "video"
            ffmpeg_seek = f"-ss {to_seek} -to {duration}"
            headers = YouTube.get_stream_headers(file_path) if str(file_path).startswith("http") else None
            stream = _build_stream(
                file_path,
                video=is_video,
                additional_ffmpeg_parameters=ffmpeg_seek,
                headers=headers,
            )
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
            playing[0]["seconds"] = new_total_secs
            playing[0]["dur"] = seconds_to_min(new_total_secs)
            playing[0]["played"] = new_played
            playing[0]["speed_path"] = out_file
            playing[0]["speed"] = spd

            ffmpeg_opts = f"-ss {seconds_to_min(new_played)}" if new_played > 0 else ""
            stream = _build_stream(out_file, video=is_video, additional_ffmpeg_parameters=ffmpeg_opts)
            await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
        headers: dict = None,
    ) -> None:
        async with self.get_lock(chat_id):
            assistant = await group_assistant(self, chat_id)
            is_vid = bool(video)
            eff_headers = headers or (YouTube.get_stream_headers(link) if str(link).startswith("http") else None)
            stream = _build_stream(link, video=is_vid, headers=eff_headers)
            try:
                await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
            except (NoAudioSourceFound, NoVideoSourceFound):
                q = db.get(chat_id)
                vid = q[0].get("vidid") if q else None
                recovered = await YouTube.recover_stream(link, videoid=vid, video=is_vid)
                if recovered:
                    rec_headers = YouTube.get_stream_headers(recovered)
                    stream = _build_stream(recovered, video=is_vid, headers=rec_headers)
                    await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                else:
                    raise
            self.active_calls.add(chat_id)
            await add_active_chat(chat_id)
            if is_vid:
                await add_active_video_chat(chat_id)
            else:
                await remove_active_video_chat(chat_id)

    async def stream_call(self, link: str) -> None:
        assistant = await group_assistant(self, config.LOGGER_ID)
        stream = _build_stream(link, video=True)
        await assistant.play(config.LOGGER_ID, stream, config=GroupCallConfig(auto_start=True))
        await asyncio.sleep(0.5)
        with suppress(Exception):
            await assistant.leave_call(config.LOGGER_ID, close=False)

    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
        headers: dict = None,
    ) -> None:
        async with self.get_lock(chat_id):
            self._joining_chats.add(chat_id)
            self.active_calls.add(chat_id)
            await add_active_chat(chat_id)
            try:
                assistant = await group_assistant(self, chat_id)
                language = await get_lang(chat_id)
                _ = get_string(language)

                is_vid = bool(video)
                eff_headers = headers or (YouTube.get_stream_headers(link) if str(link).startswith("http") else None)
                stream = _build_stream(link, video=is_vid, headers=eff_headers)

                max_retries = 2
                for attempt in range(max_retries):
                    try:
                        await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                        break
                    except (NoAudioSourceFound, NoVideoSourceFound):
                        if attempt < max_retries - 1:
                            q = db.get(chat_id)
                            vid = q[0].get("vidid") if q else None
                            recovered = await YouTube.recover_stream(link, videoid=vid, video=is_vid)
                            if recovered:
                                link = recovered
                                stream = _build_stream(
                                    link,
                                    video=is_vid,
                                    headers=YouTube.get_stream_headers(link),
                                )
                                continue
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        raise AssistantErr(_["call_10"])
                    except (NoActiveGroupCall, ChatAdminRequired, errors.GroupcallForbidden):
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        raise AssistantErr(_["call_8"])
                    except TelegramServerError:
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        raise AssistantErr(_["call_10"])
                    except Exception as e:
                        err_str = str(e).lower()
                        if attempt < max_retries - 1 and (
                            "group call not found" in err_str
                            or "cannot be initialized" in err_str
                            or "groupcall_invalid" in err_str
                        ):
                            await asyncio.sleep(0.8)
                            continue
                        self.active_calls.discard(chat_id)
                        await remove_active_chat(chat_id)
                        LOGGER(__name__).error(f"join_call error in {chat_id}: {e}")
                        raise AssistantErr(_["call_10"])

                await music_on(chat_id)
                if is_vid:
                    await add_active_video_chat(chat_id)
                else:
                    await remove_active_video_chat(chat_id)

                if await is_autoend():
                    counter[chat_id] = {}
                    with suppress(Exception):
                        users = len(await assistant.get_participants(chat_id))
                        if users == 1:
                            autoend[chat_id] = datetime.now() + timedelta(minutes=1)
            finally:
                async def _release_join_flag(cid: int):
                    await asyncio.sleep(2.0)
                    self._joining_chats.discard(cid)

                asyncio.create_task(_release_join_flag(chat_id))

    @capture_internal_err
    async def change_stream(self, client: PyTgCalls, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            check = db.get(chat_id)
            if not check:
                await _clear_(chat_id)
                self.active_calls.discard(chat_id)
                with suppress(Exception):
                    await client.leave_call(chat_id, close=False)
                return

            popped = None
            loop_count = await get_loop(chat_id)
            try:
                if loop_count == 0:
                    popped = check.pop(0)
                else:
                    loop_count = loop_count - 1
                    await set_loop(chat_id, loop_count)
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
                    await client.leave_call(chat_id, close=False)
                return

            queued = check[0].get("file")
            language = await get_lang(chat_id)
            _ = get_string(language)
            title = (check[0].get("title") or "").title()
            user = check[0].get("by")
            original_chat_id = check[0].get("chat_id")
            streamtype = check[0].get("streamtype")
            videoid = check[0].get("vidid")
            duration_str = check[0].get("dur")
            check[0]["played"] = 0

            exis = check[0].get("old_dur")
            if exis:
                check[0]["dur"] = exis
                check[0]["seconds"] = check[0]["old_second"]
                check[0]["speed_path"] = None
                check[0]["speed"] = 1.0

            is_video = str(streamtype) == "video"

            # تحضير الأغنية التي تليها في الطابور مسبقاً في الكاش (0ms عند الانتقال القادم)
            if len(check) > 1 and check[1].get("vidid"):
                next_vid = str(check[1].get("vidid"))
                if len(next_vid) == 11:
                    asyncio.create_task(YouTube._innertube_player(next_vid))

            if "live_" in str(queued):
                n, link = await YouTube.video(videoid, True)
                if n == 0 or not link:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                stream = _build_stream(link, video=is_video, headers=YouTube.get_stream_headers(link))
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                run = await self._send_photo_with_retry(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23],
                        duration_str,
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                if run and db.get(chat_id):
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"

            elif "vid_" in str(queued):
                mystic = await app.send_message(original_chat_id, _["call_7"])
                try:
                    res = await YouTube.download(
                        videoid,
                        mystic,
                        videoid=True,
                        video=is_video,
                    )
                    file_path = res[0] if isinstance(res, tuple) else res
                except Exception:
                    await mystic.edit_text(_["call_6"], disable_web_page_preview=True)
                    return
                if not file_path:
                    await mystic.edit_text(_["call_6"], disable_web_page_preview=True)
                    return
                stream = _build_stream(
                    file_path,
                    video=is_video,
                    headers=YouTube.get_stream_headers(file_path),
                )
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    recovered = await YouTube.recover_stream(file_path, videoid=str(videoid), video=is_video)
                    if recovered:
                        stream = _build_stream(
                            recovered,
                            video=is_video,
                            headers=YouTube.get_stream_headers(recovered),
                        )
                        await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                    else:
                        await app.send_message(original_chat_id, text=_["call_6"])
                        return
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                with suppress(Exception):
                    await mystic.delete()
                run = await self._send_photo_with_retry(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23],
                        duration_str,
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                if run and db.get(chat_id):
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

            elif "index_" in str(queued):
                stream = _build_stream(videoid, video=is_video)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                button = stream_markup(_, chat_id)
                run = await self._send_photo_with_retry(
                    chat_id=original_chat_id,
                    photo=config.STREAM_IMG_URL,
                    caption=_["stream_2"].format(user),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                if run and db.get(chat_id):
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"

            else:
                eff_headers = YouTube.get_stream_headers(queued) if str(queued).startswith("http") else None
                stream = _build_stream(queued, video=is_video, headers=eff_headers)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                if videoid == "telegram":
                    button = stream_markup(_, chat_id)
                    run = await self._send_photo_with_retry(
                        chat_id=original_chat_id,
                        photo=config.TELEGRAM_AUDIO_URL if str(streamtype) == "audio" else config.TELEGRAM_VIDEO_URL,
                        caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], duration_str, user),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    if run and db.get(chat_id):
                        db[chat_id][0]["mystic"] = run
                        db[chat_id][0]["markup"] = "tg"
                elif videoid == "soundcloud":
                    button = stream_markup(_, chat_id)
                    run = await self._send_photo_with_retry(
                        chat_id=original_chat_id,
                        photo=config.SOUNCLOUD_IMG_URL,
                        caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], duration_str, user),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    if run and db.get(chat_id):
                        db[chat_id][0]["mystic"] = run
                        db[chat_id][0]["markup"] = "tg"
                else:
                    img = await get_thumb(videoid)
                    button = stream_markup(_, chat_id)
                    run = await self._send_photo_with_retry(
                        chat_id=original_chat_id,
                        photo=img,
                        caption=_["stream_1"].format(
                            f"https://t.me/{app.username}?start=info_{videoid}",
                            title[:23],
                            duration_str,
                            user,
                        ),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    if run and db.get(chat_id):
                        db[chat_id][0]["mystic"] = run
                        db[chat_id][0]["markup"] = "stream"

    # مترادف متوافق في حال استدعى أي ملف خارجي self.play(client, chat_id)
    play = change_stream

    async def start(self) -> None:
        LOGGER(__name__).info("Starting PyTgCalls Clients (PyTgCalls v3.0.0 & NTgCalls v3.0.5)...")
        if config.STRING1 and self.userbot1:
            self.one = PyTgCalls(self.userbot1, cache_duration=100)
            await self.one.start()
        if config.STRING2 and self.userbot2:
            self.two = PyTgCalls(self.userbot2, cache_duration=100)
            await self.two.start()
        if config.STRING3 and self.userbot3:
            self.three = PyTgCalls(self.userbot3, cache_duration=100)
            await self.three.start()
        if config.STRING4 and self.userbot4:
            self.four = PyTgCalls(self.userbot4, cache_duration=100)
            await self.four.start()
        if config.STRING5 and self.userbot5:
            self.five = PyTgCalls(self.userbot5, cache_duration=100)
            await self.five.start()

    async def decorators(self) -> None:
        assistants = list(filter(None, [self.one, self.two, self.three, self.four, self.five]))

        for assistant in assistants:
            @assistant.on_update(filters.stream_end())
            async def stream_end_handler(client: PyTgCalls, update: Update):
                if isinstance(update, StreamEnded):
                    if update.stream_type not in (StreamEnded.Type.AUDIO, StreamEnded.Type.VIDEO):
                        return
                    chat_id = update.chat_id
                    current_time = asyncio.get_running_loop().time()
                    if chat_id in self._stream_end_cache:
                        if current_time - self._stream_end_cache[chat_id] < 2.5:
                            return
                    self._stream_end_cache[chat_id] = current_time
                    self._stream_end_cache = {
                        cid: t for cid, t in self._stream_end_cache.items() if current_time - t < 6.0
                    }
                    await self.change_stream(client, chat_id)

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


StreamController = Call()
