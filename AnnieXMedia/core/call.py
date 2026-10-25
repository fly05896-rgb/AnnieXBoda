import asyncio
import os
from datetime import datetime, timedelta
from typing import Union

from ntgcalls import TelegramServerError
from pyrogram import Client
from pyrogram.errors import FloodWait, ChatAdminRequired
from pyrogram.types import InlineKeyboardMarkup
from pytgcalls import PyTgCalls
from pytgcalls.exceptions import NoActiveGroupCall, NoAudioSourceFound, NoVideoSourceFound
from pytgcalls.types import AudioQuality, GroupCallConfig, MediaStream, StreamEnded, Update, VideoQuality
from pytgcalls.types.raw import AudioParameters

import config
from strings import get_string
from AnnieXMedia import LOGGER, YouTube, app
from AnnieXMedia.misc import db
from AnnieXMedia.utils.database import (
    add_active_chat,
    add_active_video_chat,
    get_lang,
    get_loop,
    group_assistant,
    is_autoend,
    music_on,
    remove_active_chat,
    remove_active_video_chat,
    set_loop,
)
from AnnieXMedia.utils.exceptions import AssistantErr
from AnnieXMedia.utils.formatters import check_duration, seconds_to_min, speed_converter
from AnnieXMedia.utils.inline.play import stream_markup
from AnnieXMedia.utils.stream.autoclear import auto_clean
from AnnieXMedia.utils.thumbnails import get_thumb

autoend = {}
counter = {}

loop = asyncio.get_event_loop_policy().get_event_loop()

# التردد الأصلي لمحرك WebRTC Opus في تيليجرام و ntgcalls هو 48000Hz Stereo (2 Channels)
# استخدام 48000Hz يمنع WebRTC من عمل Resampling إضافي ويعطي أنقى صوت ممكن بدون تشويه
NATIVE_OPUS_AUDIO = AudioParameters(bitrate=48000, channels=2)


class FastMediaStream(MediaStream):
    """
    يتخطى فحص ffprobe البطيء في pytgcalls ليبدأ ضخ الصوت فوراً (أقل من 0.2 ثانية)،
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
            and ("googlevideo.com" in str(self._audio_path) or str(self._audio_path).startswith(("http://", "https://")))
        ):
            return
        await super().check_stream()


def _build_stream(
    link: str,
    video: bool = False,
    additional_ffmpeg_parameters: str = "",
    headers: dict = None,
) -> MediaStream:
    is_local = os.path.exists(str(link))
    is_http = str(link).startswith(("http://", "https://"))

    # فلاتر تنعيم ومزامنة الصوت لمنع أي تقطيع (Jitter / Underrun) في ntgcalls ThreadedReader:
    # aresample=async=1000:first_pts=0 يعوض أي تأخير لحظي في الشبكة بنعومة تامة بدون طقطقة
    af_chain = "aresample=osr=48000:ochl=stereo:async=1000:first_pts=0"

    if is_local:
        # ملف محلي على السيرفر: قراءة متعددة المسارات بأقصى سرعة مع مخزن قراءة كبير في الرام
        base_ff = (
            "-nostdin -threads 4 -thread_queue_size 4096 "
            "-probesize 64k -analyzeduration 0"
        )
    elif is_http:
        # بث شبكي مباشر (googlevideo.com أو راديو):
        # بدون -fflags +nobuffer حتى يحتفظ ffmpeg بالـ Read-Ahead Buffer في الرامات (50GB RAM)
        # مع تفعيل إعادة الاتصال التلقائي وطابور 4096 حزمة لمنع جفاف الـ Pipe نهائياً
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
        # إذا كان هناك فلاتر سرعة (atempo) في extra، ندمج معها فلتر المزامنة
        if "-af " in extra or "-filter:a" in extra:
            ffmpeg_params = f"{base_ff} {extra}"
        else:
            ffmpeg_params = f"{base_ff} {extra} -_-end {af_chain_arg(af_chain)}"
    else:
        ffmpeg_params = f"{base_ff} -_-mid -af {af_chain}"

    return FastMediaStream(
        media_path=link,
        audio_parameters=NATIVE_OPUS_AUDIO,
        video_parameters=VideoQuality.HD_720p,
        audio_flags=MediaStream.Flags.REQUIRED,
        video_flags=MediaStream.Flags.REQUIRED if video else MediaStream.Flags.IGNORE,
        ffmpeg_parameters=ffmpeg_params,
        headers=headers,
    )


def af_chain_arg(af_chain: str) -> str:
    return f"-af {af_chain}"


async def _clear_(chat_id: int) -> None:
    popped = db.pop(chat_id, None)
    if popped:
        await auto_clean(popped)
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)
    await set_loop(chat_id, 0)


class Call:
    def __init__(self):
        self.userbot1 = Client(
            name="AnnieXAss1",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING1),
            workers=16,
            max_concurrent_transmissions=10,
        ) if config.STRING1 else None
        self.one = PyTgCalls(self.userbot1, cache_duration=100) if self.userbot1 else None

        self.userbot2 = Client(
            name="AnnieXAss2",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING2),
            workers=16,
            max_concurrent_transmissions=10,
        ) if config.STRING2 else None
        self.two = PyTgCalls(self.userbot2, cache_duration=100) if self.userbot2 else None

        self.userbot3 = Client(
            name="AnnieXAss3",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING3),
            workers=16,
            max_concurrent_transmissions=10,
        ) if config.STRING3 else None
        self.three = PyTgCalls(self.userbot3, cache_duration=100) if self.userbot3 else None

        self.userbot4 = Client(
            name="AnnieXAss4",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING4),
            workers=16,
            max_concurrent_transmissions=10,
        ) if config.STRING4 else None
        self.four = PyTgCalls(self.userbot4, cache_duration=100) if self.userbot4 else None

        self.userbot5 = Client(
            name="AnnieXAss5",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING5),
            workers=16,
            max_concurrent_transmissions=10,
        ) if config.STRING5 else None
        self.five = PyTgCalls(self.userbot5, cache_duration=100) if self.userbot5 else None

        self._ locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def is_truly_playing(self, chat_id: int) -> bool:
        """
        يتحقق من حالة البث الفعلية داخل محرك NTgCalls.
        ملاحظة هامة من سورس ntgcalls.cpp:
        - call_info.capture يمثل حالة إرسال الصوت/الفيديو من البوت إلى المكالمة (Microphone / Camera).
        - call_info.playback يمثل حالة تسجيل الصوت القادم من المكالمة (RecordStream) ويكون دائماً IDLE أثناء التشغيل العادي!
        لذلك يجب فحص call_info.capture وليس call_info.playback.
        """
        queue = db.get(chat_id)
        if not queue:
            return False
        try:
            assistant = await group_assistant(self, chat_id)
            calls = await assistant.calls
            call_info = calls.get(chat_id)
            if call_info is None:
                return False
            from pytgcalls.types import Call as TgCallData
            if call_info.capture == TgCallData.Status.IDLE:
                return False
            return True
        except Exception:
            return bool(queue)

    async def pause_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        await assistant.pause(chat_id)

    async def resume_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        await assistant.resume(chat_id)

    async def mute_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        await assistant.mute(chat_id)

    async def unmute_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        await assistant.unmute(chat_id)

    async def stop_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        try:
            await _clear_(chat_id)
            await assistant.leave_call(chat_id)
        except Exception:
            pass

    async def stop_stream_force(self, chat_id: int) -> None:
        for client in [self.one, self.two, self.three, self.four, self.five]:
            if client:
                try:
                    await client.leave_call(chat_id)
                except Exception:
                    pass
        try:
            await _clear_(chat_id)
        except Exception:
            pass

    async def speedup_stream(self, chat_id: int, file_path: str, speed: float, playing: list) -> None:
        assistant = await group_assistant(self, chat_id)
        if str(speed) != "1.0":
            base = os.path.basename(file_path)
            chatdir = os.path.join(os.getcwd(), "playback", str(speed))
            if not os.path.isdir(chatdir):
                os.makedirs(chatdir, exist_ok=True)
            out = os.path.join(chatdir, base)
            if not os.path.isfile(out):
                vs = str(2.0 / float(speed))
                proc = await asyncio.create_subprocess_shell(
                    f'ffmpeg -y -i "{file_path}" -filter:v setpts={vs}*PTS -filter:a atempo={speed} "{out}"',
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
        else:
            out = file_path

        dur = await loop.run_in_executor(None, check_duration, out)
        dur = int(dur)
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)

        is_video = playing[0]["streamtype"] == "video"
        ffmpeg_Seek = f"-ss {played} -to {duration}"
        stream = _build_stream(out, video=is_video, additional_ffmpeg_parameters=ffmpeg_Seek)

        if db[chat_id][0]["file"] == file_path:
            await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
        else:
            raise AssistantErr("Umm")

        if str(db[chat_id][0]["file"]) == str(file_path):
            exis = (playing[0]).get("old_dur")
            if not exis:
                db[chat_id][0]["old_dur"] = db[chat_id][0]["dur"]
                db[chat_id][0]["old_second"] = db[chat_id][0]["seconds"]
            db[chat_id][0]["played"] = con_seconds
            db[chat_id][0]["dur"] = duration
            db[chat_id][0]["seconds"] = dur
            db[chat_id][0]["speed_path"] = out
            db[chat_id][0]["speed"] = speed

    async def force_stop_stream(self, chat_id: int) -> None:
        assistant = await group_assistant(self, chat_id)
        try:
            check = db.get(chat_id)
            if check:
                check.pop(0)
        except Exception:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        try:
            await assistant.leave_call(chat_id)
        except Exception:
            pass

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
        headers: dict = None,
    ) -> None:
        assistant = await group_assistant(self, chat_id)
        stream = _build_stream(link, video=bool(video), headers=headers)
        await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))

    async def seek_stream(self, chat_id: int, file_path: str, to_seek: str, duration: str, mode: str) -> None:
        assistant = await group_assistant(self, chat_id)
        is_video = mode == "video"
        ffmpeg_Seek = f"-ss {to_seek} -to {duration}"
        stream = _build_stream(file_path, video=is_video, additional_ffmpeg_parameters=ffmpeg_Seek)
        await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))

    async def stream_call(self, link: str) -> None:
        assistant = await group_assistant(self, config.LOGGER_ID)
        stream = _build_stream(link, video=True)
        await assistant.play(config.LOGGER_ID, stream, config=GroupCallConfig(auto_start=True))
        await asyncio.sleep(0.5)
        try:
            await assistant.leave_call(config.LOGGER_ID)
        except Exception:
            pass

    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
        headers: dict = None,
    ) -> None:
        assistant = await group_assistant(self, chat_id)
        language = await get_lang(chat_id)
        _ = get_string(language)

        stream = _build_stream(link, video=bool(video), headers=headers)

        try:
            await assistant.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
        except NoActiveGroupCall:
            raise AssistantErr(_["call_8"])
        except ChatAdminRequired:
            raise AssistantErr(_["call_8"])
        except (NoAudioSourceFound, NoVideoSourceFound):
            raise AssistantErr(_["call_10"])
        except TelegramServerError:
            raise AssistantErr(_["call_10"])
        except Exception as e:
            LOGGER(__name__).error(f"join_call error in {chat_id}: {e}")
            raise AssistantErr(_["call_10"])

        await add_active_chat(chat_id)
        await music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            users = len(await assistant.get_participants(chat_id))
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    async def change_stream(self, client: PyTgCalls, chat_id: int) -> None:
        lock = self._get_lock(chat_id)
        if lock.locked():
            return
        async with lock:
            check = db.get(chat_id)
            popped = None
            loop_count = await get_loop(chat_id)
            try:
                if loop_count == 0:
                    popped = check.pop(0)
                else:
                    loop_count = loop_count - 1
                    await set_loop(chat_id, loop_count)
                await auto_clean(popped)
                if not check:
                    await _clear_(chat_id)
                    try:
                        await client.leave_call(chat_id)
                    except Exception:
                        pass
                    return
            except Exception:
                try:
                    await _clear_(chat_id)
                    await client.leave_call(chat_id)
                except Exception:
                    pass
                return

            queued = check[0]["file"]
            language = await get_lang(chat_id)
            _ = get_string(language)
            title = (check[0]["title"]).title()
            user = check[0]["by"]
            original_chat_id = check[0]["chat_id"]
            streamtype = check[0]["streamtype"]
            videoid = check[0]["vidid"]
            db[chat_id][0]["played"] = 0

            exis = (check[0]).get("old_dur")
            if exis:
                db[chat_id][0]["dur"] = exis
                db[chat_id][0]["seconds"] = check[0]["old_second"]
                db[chat_id][0]["speed_path"] = None
                db[chat_id][0]["speed"] = 1.0

            video = True if str(streamtype) == "video" else False

            if "live_" in queued:
                n, link = await YouTube.video(videoid, True)
                if n == 0:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                stream = _build_stream(link, video=video, headers=YouTube.stream_headers)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            elif "vid_" in queued:
                mystic = await app.send_message(original_chat_id, _["call_7"])
                try:
                    file_path, direct = await YouTube.download(
                        videoid,
                        mystic,
                        videoid=True,
                        video=video,
                    )
                except Exception:
                    await mystic.edit_text(_["call_6"], disable_web_page_preview=True)
                    return
                if not file_path:
                    await mystic.edit_text(_["call_6"], disable_web_page_preview=True)
                    return
                stream = _build_stream(file_path, video=video, headers=YouTube.stream_headers)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                await mystic.delete()
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"

            elif "index_" in queued:
                stream = _build_stream(videoid, video=video)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                button = stream_markup(_, chat_id)
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=config.STREAM_IMG_URL,
                    caption=_["stream_2"].format(user),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            else:
                stream = _build_stream(queued, video=video, headers=YouTube.stream_headers)
                try:
                    await client.play(chat_id, stream, config=GroupCallConfig(auto_start=True))
                except Exception:
                    await app.send_message(original_chat_id, text=_["call_6"])
                    return
                if videoid == "telegram":
                    button = stream_markup(_, chat_id)
                    run = await app.send_photo(
                        chat_id=original_chat_id,
                        photo=config.TELEGRAM_AUDIO_URL if str(streamtype) == "audio" else config.TELEGRAM_VIDEO_URL,
                        caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                elif videoid == "soundcloud":
                    button = stream_markup(_, chat_id)
                    run = await app.send_photo(
                        chat_id=original_chat_id,
                        photo=config.SOUNCLOUD_IMG_URL,
                        caption=_["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                else:
                    img = await get_thumb(videoid)
                    button = stream_markup(_, chat_id)
                    run = await app.send_photo(
                        chat_id=original_chat_id,
                        photo=img,
                        caption=_["stream_1"].format(
                            f"https://t.me/{app.username}?start=info_{videoid}",
                            title[:23],
                            check[0]["dur"],
                            user,
                        ),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

    async def ping(self) -> str:
        pings = []
        for client in [self.one, self.two, self.three, self.four, self.five]:
            if client:
                pings.append(client.ping)
        return str(round(sum(pings) / len(pings), 3)) if pings else "0.0"

    async def start(self) -> None:
        LOGGER(__name__).info("Starting PyTgCalls Clients...")
        if config.STRING1 and self.one:
            await self.one.start()
        if config.STRING2 and self.two:
            await self.two.start()
        if config.STRING3 and self.three:
            await self.three.start()
        if config.STRING4 and self.four:
            await self.four.start()
        if config.STRING5 and self.five:
            await self.five.start()

    async def decorators(self) -> None:
        for client in [self.one, self.two, self.three, self.four, self.five]:
            if not client:
                continue

            @client.on_update()
            async def stream_services_handler(cli: PyTgCalls, update: Update) -> None:
                if isinstance(update, StreamEnded):
                    if update.stream_type in (StreamEnded.Type.AUDIO, StreamEnded.Type.VIDEO):
                        await self.change_stream(cli, update.chat_id)


StreamController = Call()
