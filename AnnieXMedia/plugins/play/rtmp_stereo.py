import asyncio
import os
import random
import re
import time
from contextlib import suppress
from typing import Any, Dict, List

import aiofiles
from pyrogram import enums, filters, raw
from pyrogram.errors import ChatAdminRequired, UserNotParticipant
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from config import BANNED_USERS
from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.utils.database import get_assistant
from AnnieXMedia.utils.decorators import AdminRightsCheck, AdminActual

ANDROID_UA = "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip"
VISIONOS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)

COVER_DIR = "/dev/shm/rtmp_covers" if os.path.exists("/dev/shm") else "/tmp/rtmp_covers"
os.makedirs(COVER_DIR, exist_ok=True)

_rtmp_procs: Dict[int, asyncio.subprocess.Process] = {}
_rtmp_tasks: Dict[int, asyncio.Task] = {}
_rtmp_locks: Dict[int, asyncio.Lock] = {}
_rtmp_url_cache: Dict[int, str] = {}
_rtmp_queues: Dict[int, List[Dict[str, Any]]] = {}
_rtmp_state: Dict[int, Dict[str, Any]] = {}


def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _rtmp_locks:
        _rtmp_locks[chat_id] = asyncio.Lock()
    return _rtmp_locks[chat_id]


def _parse_dur_to_sec(dur_str: str) -> int:
    if not dur_str or str(dur_str).lower() == "live":
        return 0
    try:
        parts = [int(p) for p in str(dur_str).split(":") if p.isdigit()]
        return sum(p * (60**i) for i, p in enumerate(reversed(parts)))
    except Exception:
        return 0


def _sec_to_str(sec: int) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def _get_played_seconds(chat_id: int) -> int:
    st = _rtmp_state.get(chat_id)
    if not st:
        return 0
    if st.get("paused"):
        return int(st.get("offset", 0))
    return int(st.get("offset", 0) + (time.time() - st.get("start_ts", time.time())))


def _rtmp_buttons(chat_id: int, paused: bool = False) -> InlineKeyboardMarkup:
    pause_label = "استئناف" if paused else "توقف مؤقت"
    pause_cmd = "resume" if paused else "pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="-15 ث", callback_data=f"rtmp_ctl|rew15|{chat_id}"),
                InlineKeyboardButton(text=pause_label, callback_data=f"rtmp_ctl|{pause_cmd}|{chat_id}"),
                InlineKeyboardButton(text="+15 ث", callback_data=f"rtmp_ctl|fwd15|{chat_id}"),
            ],
            [
                InlineKeyboardButton(text="تخطي", callback_data=f"rtmp_ctl|skip|{chat_id}"),
                InlineKeyboardButton(text="ايقاف البث", callback_data=f"rtmp_ctl|stop|{chat_id}"),
            ],
            [
                InlineKeyboardButton(text="قفل الكول نهائيا", callback_data=f"rtmp_ctl|close|{chat_id}"),
            ],
        ]
    )


async def _kill_proc(chat_id: int) -> None:
    task = _rtmp_tasks.pop(chat_id, None)
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()

    proc = _rtmp_procs.pop(chat_id, None)
    if proc and proc.returncode is None:
        with suppress(Exception):
            proc.kill()
            await proc.wait()


async def _fetch_raw_yt_cover(vid_id: str) -> str | None:
    path = os.path.join(COVER_DIR, f"{vid_id}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    url = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    try:
        session = await YouTube._get_session()
        async with session.get(url) as resp:
            if resp.status == 200:
                async with aiofiles.open(path, "wb") as f:
                    await f.write(await resp.read())
                return path
    except Exception:
        pass
    return None


async def _ensure_assistant(chat_id: int):
    ub = await get_assistant(chat_id)
    try:
        member = await app.get_chat_member(chat_id, ub.id)
        if member.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
            raise RuntimeError("الحساب المساعد محظور أو مقيد في هذا الجروب.")
    except UserNotParticipant:
        invitelink = await app.export_chat_invite_link(chat_id)
        if invitelink.startswith("https://t.me/+"):
            invitelink = invitelink.replace("https://t.me/+", "https://t.me/joinchat/")
        await ub.join_chat(invitelink)
    await ub.resolve_peer(chat_id)
    return ub


async def _ensure_rtmp_call(chat_id: int, force_recreate: bool = False, title: str = "Hi-Fi Stereo") -> str:
    if chat_id in StreamController.active_calls:
        with suppress(Exception):
            await StreamController.stop_stream(chat_id)

    if not force_recreate and chat_id in _rtmp_url_cache:
        return _rtmp_url_cache[chat_id]

    ub = await _ensure_assistant(chat_id)
    peer = await ub.resolve_peer(chat_id)

    rtmp_info, full_chat = await asyncio.gather(
        ub.invoke(raw.functions.phone.GetGroupCallStreamRtmpUrl(peer=peer, revoke=False)),
        ub.invoke(raw.functions.channels.GetFullChannel(channel=peer)),
    )
    rtmp_full_url = f"{rtmp_info.url}{rtmp_info.key}"
    active_call = full_chat.full_chat.call

    if active_call and force_recreate:
        with suppress(Exception):
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=active_call))
        active_call = None

    if not active_call:
        await ub.invoke(
            raw.functions.phone.CreateGroupCall(
                peer=peer,
                random_id=random.randint(10000, 99999999),
                rtmp_stream=True,
                title=title[:32],
            )
        )

    _rtmp_url_cache[chat_id] = rtmp_full_url
    return rtmp_full_url


async def _discard_call(chat_id: int) -> bool:
    await _kill_proc(chat_id)
    _rtmp_queues.pop(chat_id, None)
    _rtmp_state.pop(chat_id, None)
    _rtmp_url_cache.pop(chat_id, None)
    try:
        ub = await get_assistant(chat_id)
        peer = await ub.resolve_peer(chat_id)
        full_chat = await ub.invoke(raw.functions.channels.GetFullChannel(channel=peer))
        if full_chat.full_chat.call:
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=full_chat.full_chat.call))
            return True
    except Exception:
        pass
    return False


def _build_ffmpeg_cmd(
    media_url: str,
    rtmp_url: str,
    is_video: bool,
    cover_path: str | None = None,
    seek_seconds: int = 0,
) -> list[str]:
    ua = VISIONOS_UA if "c=VISIONOS" in str(media_url) else ANDROID_UA
    seek_args = ["-ss", str(int(seek_seconds))] if seek_seconds > 0 else []

    if not is_video:
        if cover_path and os.path.exists(cover_path):
            video_input = ["-re", "-loop", "1", "-framerate", "15", "-i", str(cover_path)]
        else:
            video_input = ["-re", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=15"]

        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "+genpts+igndts",
            *video_input,
            "-thread_queue_size", "4096",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-user_agent", ua,
            *seek_args,
            "-i", str(media_url),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", "scale=640:360,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-b:v", "200k",
            "-maxrate", "200k",
            "-bufsize", "400k",
            "-r", "15",
            "-g", "30",
            "-keyint_min", "30",
            "-sc_threshold", "0",
            "-af", "aresample=osr=48000:ochl=stereo:async=1000:first_pts=0",
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            "-ac", "2",
            "-shortest",
            "-flvflags", "no_duration_filesize",
            "-f", "flv",
            str(rtmp_url),
        ]

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+genpts+igndts",
        "-re",
        "-thread_queue_size", "4096",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-user_agent", ua,
        *seek_args,
        "-i", str(media_url),
        "-vf", "scale=640:360,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-b:v", "500k",
        "-maxrate", "500k",
        "-bufsize", "1000k",
        "-r", "25",
        "-g", "50",
        "-keyint_min", "50",
        "-sc_threshold", "0",
        "-af", "aresample=osr=48000:ochl=stereo:async=1000:first_pts=0",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-ac", "2",
        "-flvflags", "no_duration_filesize",
        "-f", "flv",
        str(rtmp_url),
    ]


async def _start_track(chat_id: int, track: Dict[str, Any], seek_seconds: int = 0) -> None:
    await _kill_proc(chat_id)

    vid_id = track["vid_id"]
    is_video = track["is_video"]

    if is_video:
        media_url, cover_path, rtmp_url = await asyncio.gather(
            YouTube.download(vid_id, None, video=True, videoid=f"{vid_id}_v"),
            _fetch_raw_yt_cover(vid_id),
            _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
        )
    else:
        media_url, cover_path, rtmp_url = await asyncio.gather(
            YouTube.download(vid_id, None, video=False, videoid=vid_id),
            _fetch_raw_yt_cover(vid_id),
            _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
        )

    if not media_url:
        raise RuntimeError("فشل استخراج رابط البث المباشر.")

    track["media_url"] = media_url
    track["cover_path"] = cover_path

    cmd = _build_ffmpeg_cmd(
        media_url=media_url,
        rtmp_url=rtmp_url,
        is_video=is_video,
        cover_path=cover_path,
        seek_seconds=seek_seconds,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _rtmp_procs[chat_id] = proc
    _rtmp_state[chat_id] = {
        "track": track,
        "start_ts": time.time(),
        "offset": seek_seconds,
        "paused": False,
    }

    async def _runner(cid: int, p: asyncio.subprocess.Process):
        t0 = time.time()
        with suppress(Exception):
            await p.wait()
        if _rtmp_procs.get(cid) is not p:
            return
        # إذا فصل في أول ثانية بسبب مكالمة عادية قديمة، نعيد إنشاء مكالمة الـ RTMP فوراً
        if (time.time() - t0) < 1.5 and not _rtmp_state.get(cid, {}).get("paused"):
            with suppress(Exception):
                new_rtmp = await _ensure_rtmp_call(cid, force_recreate=True, title=track["title"])
                retry_cmd = _build_ffmpeg_cmd(
                    media_url=media_url,
                    rtmp_url=new_rtmp,
                    is_video=is_video,
                    cover_path=cover_path,
                    seek_seconds=seek_seconds,
                )
                p2 = await asyncio.create_subprocess_exec(
                    *retry_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                _rtmp_procs[cid] = p2
                _rtmp_state[cid]["start_ts"] = time.time()
                await p2.wait()
                if _rtmp_procs.get(cid) is not p2:
                    return

        _rtmp_procs.pop(cid, None)
        queue = _rtmp_queues.get(cid) or []
        if queue:
            queue.pop(0)
        if queue:
            next_track = queue[0]
            with suppress(Exception):
                await _start_track(cid, next_track, seek_seconds=0)
                await app.send_photo(
                    cid,
                    photo=next_track.get("cover_path") or f"https://i.ytimg.com/vi/{next_track['vid_id']}/hqdefault.jpg",
                    caption=(
                        f"**تم تشغيل التالي في بث الـ Stereo**\n\n"
                        f"**العنوان :** `{next_track['title'][:45]}`\n"
                        f"**المدة :** `{next_track['duration_min']}`\n"
                        f"**بواسطة :** {next_track['by']}"
                    ),
                    reply_markup=_rtmp_buttons(cid, paused=False),
                )
        else:
            _rtmp_state.pop(cid, None)

    _rtmp_tasks[chat_id] = asyncio.create_task(_runner(chat_id, proc))


# ==========================================================
# 1. أمر فتح مكالمة الـ RTMP (يدعم افتح استيريو / افتح ستيريو)
#    ✅ AdminActual (بدون فحص is_active_chat)
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(افتح استيريو|افتح ستيريو|فتح استيريو|فتح ستيريو|rtmpon)$")
    & filters.group
    & ~BANNED_USERS
)
@AdminActual
async def open_rtmp_call_cmd(client, message: Message, _):
    chat_id = message.chat.id
    mystic = await message.reply_text("**جاري فتح مكالمة الـ Stereo...**")
    async with _get_lock(chat_id):
        try:
            await _ensure_rtmp_call(chat_id, force_recreate=True)
            await mystic.edit_text(
                "**تم فتح مكالمة الـ Stereo بنجاح.**\n"
                "- للتشغيل صوت: `استيريو + اسم الاغنية`\n"
                "- للتشغيل فيديو: `فيديو استيريو + اسم الاغنية`\n"
                "- للتقديم: `قدم استيريو 30`\n"
                "- للتخطي: `تخطي استيريو`\n"
                "- للقفل: `اقفل استيريو`"
            )
        except ChatAdminRequired:
            await mystic.edit_text("**يجب رفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 2. أمر قفل مكالمة الـ RTMP نهائياً
#    ✅ AdminActual (بدون فحص is_active_chat)
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(اقفل استيريو|اقفل ستيريو|قفل استيريو|قفل ستيريو|rtmpoff)$")
    & filters.group
    & ~BANNED_USERS
)
@AdminActual
async def close_rtmp_call_cmd(client, message: Message, _):
    chat_id = message.chat.id
    async with _get_lock(chat_id):
        await _discard_call(chat_id)
        await message.reply_text("**تم ايقاف البث واغلاق مكالمة الـ Stereo بالكامل.**")


# ==========================================================
# 3. أوامر التحكم النصية (ايقاف / توقف / كمل / تخطي / قدم / رجع)
#    ✅ AdminRightsCheck (محتاجة مكالمة شغالة فعلاً)
# ==========================================================
@app.on_message(
    filters.regex(
        r"^[/!.]?(ايقاف استيريو|ايقاف ستيريو|اسكت استيريو|توقف استيريو|توقف ستيريو|كمل استيريو|كمل ستيريو|تخطي استيريو|تخطي ستيريو|التالي استيريو|(?:قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+\d+)"
    )
    & filters.group
    & ~BANNED_USERS
)
@AdminRightsCheck
async def control_rtmp_text_cmd(client, message: Message, _, chat_id: int):
    text = (message.text or "").strip().lstrip("/!.")
    async with _get_lock(chat_id):
        st = _rtmp_state.get(chat_id)
        if not st:
            return await message.reply_text("**لا يوجد بث Stereo نشط حاليا.**")

        if text.startswith(("ايقاف", "اسكت")):
            await _kill_proc(chat_id)
            _rtmp_queues.pop(chat_id, None)
            _rtmp_state.pop(chat_id, None)
            return await message.reply_text("**تم ايقاف البث وتفريغ الطابور.**")

        if text.startswith("توقف"):
            if st.get("paused"):
                return await message.reply_text("**البث متوقف مؤقتا بالفعل.**")
            st["offset"] = _get_played_seconds(chat_id)
            st["paused"] = True
            await _kill_proc(chat_id)
            return await message.reply_text(f"**تم الايقاف المؤقت عند الثانية {_sec_to_str(st['offset'])}.**")

        if text.startswith("كمل"):
            if not st.get("paused"):
                return await message.reply_text("**البث يعمل بالفعل.**")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            return await message.reply_text(f"**تم استئناف البث من {_sec_to_str(st['offset'])}.**")

        if text.startswith(("تخطي", "التالي")):
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await _kill_proc(chat_id)
                _rtmp_state.pop(chat_id, None)
                return await message.reply_text("**تم تخطي الاغنية، الطابور فارغ الآن.**")
            next_track = queue[0]
            await _start_track(chat_id, next_track, seek_seconds=0)
            return await message.reply_photo(
                photo=next_track.get("cover_path") or f"https://i.ytimg.com/vi/{next_track['vid_id']}/hqdefault.jpg",
                caption=(
                    f"**تم التخطي وتشغيل التالي**\n\n"
                    f"**العنوان :** `{next_track['title'][:45]}`\n"
                    f"**المدة :** `{next_track['duration_min']}`\n"
                    f"**بواسطة :** {next_track['by']}"
                ),
                reply_markup=_rtmp_buttons(chat_id, paused=False),
            )

        m = re.match(r"^(قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+(\d+)$", text)
        if m:
            action, sec_str = m.group(1), m.group(2)
            delta = int(sec_str)
            cur = _get_played_seconds(chat_id)
            new_pos = max(0, cur - delta) if action == "رجع" else (cur + delta)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await message.reply_text("**الوقت المطلوب يتجاوز مدة الاغنية.**")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            return await message.reply_text(f"**تم الانتقال الى الدقيقة {_sec_to_str(new_pos)}.**")


# ==========================================================
# 4. أمر التشغيل الرئيسي (يدعم استيريو / ستيريو / فيديو استيريو / فيديو ستيريو)
#    ✅ AdminRightsCheck
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(فيديو استيريو|فيديو ستيريو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$")
    & filters.group
    & ~BANNED_USERS
)
@AdminRightsCheck
async def play_rtmp_stereo_cmd(client, message: Message, _, chat_id: int):
    raw_text = (message.text or "").strip().lstrip("/!.")
    for ignored in (
        "افتح استيريو", "افتح ستيريو", "فتح استيريو", "فتح ستيريو",
        "اقفل استيريو", "اقفل ستيريو", "قفل استيريو", "قفل ستيريو",
        "ايقاف استيريو", "ايقاف ستيريو", "توقف استيريو", "توقف ستيريو",
        "كمل استيريو", "كمل ستيريو", "تخطي استيريو", "تخطي ستيريو",
    ):
        if raw_text.startswith(ignored):
            return

    m = re.match(r"^(فيديو استيريو|فيديو ستيريو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$", raw_text, flags=re.I)
    if not m:
        return

    cmd_word = m.group(1).lower()
    query = (m.group(2) or "").strip()
    is_video = cmd_word in ("فيديو استيريو", "فيديو ستيريو", "بث فيديو", "vrtmp")

    if not query and message.reply_to_message:
        query = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()

    if not query:
        return await message.reply_text(
            "**طريقة الاستخدام:**\n"
            "- صوت: `استيريو اتنسيت`\n"
            "- فيديو: `فيديو استيريو اتنسيت`\n"
            "- فتح الكول: `افتح استيريو`\n"
            "- قفل الكول: `اقفل استيريو`"
        )

    mystic = await message.reply_text("**جاري البحث والتشغيل...**")

    async with _get_lock(chat_id):
        try:
            details, vid_id = await YouTube.track(query)
            if not details or not vid_id or details.get("title") == "Unknown":
                return await mystic.edit_text("**لم يتم العثور على النتيجة.**")

            title = details.get("title", "Unknown")
            duration_min = details.get("duration_min", "0:00")
            duration_sec = int(details.get("duration_sec") or _parse_dur_to_sec(duration_min))

            track_item = {
                "vid_id": vid_id,
                "title": title,
                "duration_min": duration_min,
                "duration_sec": duration_sec,
                "is_video": is_video,
                "by": message.from_user.mention,
            }

            queue = _rtmp_queues.setdefault(chat_id, [])
            is_playing = chat_id in _rtmp_procs and _rtmp_procs[chat_id].returncode is None

            if is_playing:
                queue.append(track_item)
                pos = len(queue) - 1
                return await mystic.edit_text(
                    f"**تمت الاضافة الى طابور الـ Stereo في المركز #{pos}**\n\n"
                    f"**العنوان :** `{title[:45]}`\n"
                    f"**المدة :** `{duration_min}`\n"
                    f"**بواسطة :** {message.from_user.mention}"
                )

            queue.clear()
            queue.append(track_item)
            await _start_track(chat_id, track_item, seek_seconds=0)

            mode_label = "فيديو + Stereo 320kbps" if is_video else "صوت Stereo 320kbps + غلاف يوتيوب"
            photo_src = track_item.get("cover_path") or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

            await mystic.delete()
            await message.reply_photo(
                photo=photo_src,
                caption=(
                    f"**تم بدء بث الـ Stereo**\n\n"
                    f"**العنوان :** `{title[:45]}`\n"
                    f"**المدة :** `{duration_min}`\n"
                    f"**الوضع :** `{mode_label}`\n"
                    f"**بواسطة :** {message.from_user.mention}"
                ),
                reply_markup=_rtmp_buttons(chat_id, paused=False),
            )
        except ChatAdminRequired:
            await mystic.edit_text("**يجب رفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 5. معالج أزرار التحكم التفاعلية (Inline Buttons)
#    ✅ AdminRightsCheck
# ==========================================================
@app.on_callback_query(filters.regex(r"^rtmp_ctl\|") & ~BANNED_USERS)
@AdminRightsCheck
async def rtmp_buttons_cb(client, callback_query: CallbackQuery, _, chat_id: int):
    parts = callback_query.data.split("|")
    action = parts[1]

    async with _get_lock(chat_id):
        st = _rtmp_state.get(chat_id)

        if action == "close":
            await callback_query.answer("جاري قفل الكول...")
            await _discard_call(chat_id)
            with suppress(Exception):
                await callback_query.message.edit_caption(
                    f"**تم ايقاف البث وقفل الكول بواسطة :** {callback_query.from_user.mention}"
                )
            return

        if not st:
            return await callback_query.answer("لا يوجد بث نشط حاليا.", show_alert=True)

        if action == "stop":
            await callback_query.answer("تم ايقاف البث.")
            await _kill_proc(chat_id)
            _rtmp_queues.pop(chat_id, None)
            _rtmp_state.pop(chat_id, None)
            with suppress(Exception):
                await callback_query.message.edit_caption(
                    f"**تم ايقاف البث بواسطة :** {callback_query.from_user.mention}"
                )
            return

        if action == "pause":
            st["offset"] = _get_played_seconds(chat_id)
            st["paused"] = True
            await _kill_proc(chat_id)
            await callback_query.answer(f"تم التوقف عند {_sec_to_str(st['offset'])}")
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_buttons(chat_id, paused=True))
            return

        if action == "resume":
            await callback_query.answer("جاري الاستئناف...")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_buttons(chat_id, paused=False))
            return

        if action in ("rew15", "fwd15"):
            cur = _get_played_seconds(chat_id)
            new_pos = max(0, cur - 15) if action == "rew15" else (cur + 15)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await callback_query.answer("نهاية الاغنية.", show_alert=True)
            await callback_query.answer(f"الانتقال الى {_sec_to_str(new_pos)}")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_buttons(chat_id, paused=False))
            return

        if action == "skip":
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await callback_query.answer("تم التخطي، الطابور فارغ.")
                await _kill_proc(chat_id)
                _rtmp_state.pop(chat_id, None)
                with suppress(Exception):
                    await callback_query.message.edit_caption(
                        f"**تم تخطي الاغنية وانتهاء الطابور بواسطة :** {callback_query.from_user.mention}"
                    )
                return
            next_track = queue[0]
            await callback_query.answer("جاري تشغيل التالي...")
            await _start_track(chat_id, next_track, seek_seconds=0)
            with suppress(Exception):
                await callback_query.message.delete()
            await app.send_photo(
                chat_id,
                photo=next_track.get("cover_path") or f"https://i.ytimg.com/vi/{next_track['vid_id']}/hqdefault.jpg",
                caption=(
                    f"**تم التخطي وتشغيل التالي**\n\n"
                    f"**العنوان :** `{next_track['title'][:45]}`\n"
                    f"**المدة :** `{next_track['duration_min']}`\n"
                    f"**بواسطة :** {next_track['by']}"
                ),
                reply_markup=_rtmp_buttons(chat_id, paused=False),
            )
