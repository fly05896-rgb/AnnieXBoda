# Authored By Certified Coders 2026
# File Path: AnnieXMedia/plugins/play/rtmp_stereo.py
# System: Standalone Native Telegram RTMP Stereo (320kbps 48kHz) Engine (Zero Conflict Edition)

import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from contextlib import suppress
from typing import Any, Dict, List

import aiofiles
from pyrogram import enums, filters, raw
from pyrogram.errors import ChatAdminRequired, UserNotParticipant
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from config import BANNED_USERS, adminlist
from AnnieXMedia import YouTube, app
from AnnieXMedia.misc import SUDOERS, db
from AnnieXMedia.utils.database import (
    add_active_chat,
    get_assistant,
    get_authuser_names,
    get_lang,
    is_maintenance,
    is_nonadmin_chat,
    music_off,
    music_on,
    remove_active_chat,
    remove_active_video_chat,
)
from AnnieXMedia.utils.formatters import int_to_alpha
from strings import get_string

LOGGER = logging.getLogger("rtmp_stereo")
if not LOGGER.handlers:
    LOGGER.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [rtmp] %(message)s", datefmt="%H:%M:%S")
    )
    LOGGER.addHandler(_handler)
    LOGGER.propagate = False

ANDROID_UA = "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip"
VISIONOS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)

COVER_DIR = "/dev/shm/rtmp_covers" if os.path.exists("/dev/shm") else "/tmp/rtmp_covers"
os.makedirs(COVER_DIR, exist_ok=True)

# مجموعة أولوية عالية (-2) مع stop_propagation لمنع تداخل أوامر البوت العادية
RTMP_GROUP = -2

_rtmp_procs: Dict[int, asyncio.subprocess.Process] = {}
_rtmp_tasks: Dict[int, asyncio.Task] = {}
_rtmp_locks: Dict[int, asyncio.Lock] = {}
_rtmp_url_cache: Dict[int, str] = {}
_rtmp_call_ready: Dict[int, bool] = {}
_rtmp_queues: Dict[int, List[Dict[str, Any]]] = {}
_rtmp_state: Dict[int, Dict[str, Any]] = {}
_rtmp_stderr: Dict[int, deque] = {}


def _log(level: str, chat_id: int, msg: str) -> None:
    getattr(LOGGER, level)(f"[chat={chat_id}] {msg}")


def is_rtmp_active(chat_id: int) -> bool:
    """فحص ما إذا كان الجروب في وضع بث RTMP نشط أو متوقف مؤقتاً"""
    if chat_id in _rtmp_state:
        return True
    proc = _rtmp_procs.get(chat_id)
    return bool(proc and proc.returncode is None)


async def _has_rtmp_permission(chat_id: int, user_id: int) -> bool:
    """فحص موحد للصلاحيات يشمل المطورين، وضع الجميع، المشرفين، والأدمنية المرفوعين بالبوت"""
    if user_id in SUDOERS:
        return True
    if await is_nonadmin_chat(chat_id):
        return True
    cached_admins = adminlist.get(chat_id)
    if cached_admins and user_id in cached_admins:
        return True
    with suppress(Exception):
        token = await int_to_alpha(user_id)
        auth_names = await get_authuser_names(chat_id)
        if token in auth_names:
            return True
    with suppress(Exception):
        member = await app.get_chat_member(chat_id, user_id)
        if member.status == enums.ChatMemberStatus.OWNER:
            return True
        if member.status == enums.ChatMemberStatus.ADMINISTRATOR:
            if not member.privileges or member.privileges.can_manage_video_chats:
                return True
    return False


def RTMPCheck(mystic):
    async def wrapper(client, update):
        is_cb = isinstance(update, CallbackQuery)
        msg = update.message if is_cb else update
        user = update.from_user

        if not user and not is_cb and msg.sender_chat:
            txt = "عذرا، انت تكتب بصفتك قناة او مشرف متخفي. اظهر حسابك الشخصي."
            await msg.reply_text(txt)
            return await msg.stop_propagation()

        if not user:
            return

        if await is_maintenance() is False and user.id not in SUDOERS:
            txt = "البوت في وضع الصيانة حاليا."
            if is_cb:
                return await update.answer(txt, show_alert=True)
            await msg.reply_text(txt)
            return await msg.stop_propagation()

        chat_id = msg.chat.id
        if is_cb:
            with suppress(Exception):
                chat_id = int(update.data.split("|")[-1])

        if not await _has_rtmp_permission(chat_id, user.id):
            txt = "تحتاج صلاحية ادارة المحادثات الصوتية او رفع ادمن في البوت لتنفيذ هذا الامر."
            if is_cb:
                return await update.answer(txt, show_alert=True)
            await msg.reply_text(txt)
            return await msg.stop_propagation()

        if not is_cb:
            with suppress(Exception):
                await msg.delete()

        try:
            language = await get_lang(chat_id)
            _ = get_string(language)
        except Exception:
            _ = get_string("ar")

        await mystic(client, update, _, chat_id)
        if not is_cb:
            await msg.stop_propagation()

    return wrapper


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
    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    proc = _rtmp_procs.pop(chat_id, None)
    if proc and proc.returncode is None:
        with suppress(Exception):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _sync_rtmp_clear(chat_id: int) -> None:
    """تنظيف حالة البث عند التوقف مع عدم المساس ببث PyTgCalls العادي ان وجد"""
    from AnnieXMedia.core.call import StreamController

    _rtmp_queues.pop(chat_id, None)
    _rtmp_state.pop(chat_id, None)
    _rtmp_stderr.pop(chat_id, None)
    if chat_id not in StreamController.active_calls:
        db[chat_id] = []
        with suppress(Exception):
            await remove_active_video_chat(chat_id)
            await remove_active_chat(chat_id)
            await music_off(chat_id)


async def stop_rtmp_if_active(chat_id: int, discard_call: bool = True) -> None:
    """دالة مساعدة يستدعيها البوت العادي (call.py) قبل بدء مكالمة عادية لمنع التداخل"""
    if not is_rtmp_active(chat_id) and not _rtmp_call_ready.get(chat_id):
        return
    if discard_call:
        await _discard_call(chat_id)
    else:
        await _kill_proc(chat_id)
        await _sync_rtmp_clear(chat_id)


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
    except Exception as e:
        _log("warning", 0, f"فشل تحميل غلاف {vid_id}: {e}")
    return None


async def _ensure_assistant(chat_id: int):
    ub = await get_assistant(chat_id)
    try:
        member = await app.get_chat_member(chat_id, ub.id)
        if member.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
            raise RuntimeError("الحساب المساعد محظور او مقيد في هذا الجروب.")
    except UserNotParticipant:
        invitelink = await app.export_chat_invite_link(chat_id)
        if invitelink.startswith("https://t.me/+"):
            invitelink = invitelink.replace("https://t.me/+", "https://t.me/joinchat/")
        await ub.join_chat(invitelink)
    await ub.resolve_peer(chat_id)
    return ub


async def _ensure_rtmp_call(chat_id: int, force_recreate: bool = False, title: str = "Hi-Fi Stereo") -> str:
    """
    تأمين مكالمة RTMP حقيقية (rtmp_stream=True):
    اذا كانت هناك مكالمة عادية مفتوحة مسبقا في الجروب، يتم اغلاقها واعادة فتحها كـ RTMP
    لان المكالمة العادية ترفض استقبال الصوت من FFmpeg RTMP.
    """
    from AnnieXMedia.core.call import StreamController

    if chat_id in StreamController.active_calls:
        with suppress(Exception):
            await StreamController.stop_stream(chat_id)
        force_recreate = True

    if not force_recreate and _rtmp_call_ready.get(chat_id) and chat_id in _rtmp_url_cache:
        return _rtmp_url_cache[chat_id]

    ub = await _ensure_assistant(chat_id)
    peer = await ub.resolve_peer(chat_id)

    rtmp_info, full_chat = await asyncio.gather(
        ub.invoke(raw.functions.phone.GetGroupCallStreamRtmpUrl(peer=peer, revoke=False)),
        ub.invoke(raw.functions.channels.GetFullChannel(channel=peer)),
    )
    rtmp_full_url = f"{rtmp_info.url}{rtmp_info.key}"
    active_call = full_chat.full_chat.call

    # اذا لم تكن المكالمة مسجلة لدينا كـ RTMP جاهزة، نغلق اي مكالمة عادية قديمة وننشئ مكالمة RTMP
    need_new_rtmp = force_recreate or not _rtmp_call_ready.get(chat_id) or not active_call

    if active_call and need_new_rtmp:
        with suppress(Exception):
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=active_call))
        await asyncio.sleep(0.4)
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
        await asyncio.sleep(0.4)

    _rtmp_url_cache[chat_id] = rtmp_full_url
    _rtmp_call_ready[chat_id] = True
    return rtmp_full_url


async def _discard_call(chat_id: int) -> bool:
    await _kill_proc(chat_id)
    await _sync_rtmp_clear(chat_id)
    _rtmp_url_cache.pop(chat_id, None)
    _rtmp_call_ready.pop(chat_id, None)
    try:
        ub = await get_assistant(chat_id)
        peer = await ub.resolve_peer(chat_id)
        full_chat = await ub.invoke(raw.functions.channels.GetFullChannel(channel=peer))
        if full_chat.full_chat.call:
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=full_chat.full_chat.call))
            return True
    except Exception as e:
        _log("error", chat_id, f"فشل قفل المكالمة: {e}")
    return False


async def _resolve_rtmp_stream_url(vid_id: str, is_video: bool, prefer_progressive: bool = False) -> str | None:
    """
    استخراج رابط البث لـ FFmpeg:
    - في المحاولة الاولى للصوت: يستخدم رابط الكاش السريع (VISIONOS itag=140 او ANDROID itag=18).
    - عند اعادة المحاولة (prefer_progressive=True): يجبر استخراج ANDROID itag=18 المدمج والمضمون 100%.
    """
    if prefer_progressive:
        recovered = await YouTube.recover_stream(vid_id, videoid=vid_id, video=True)
        if recovered:
            return recovered
    if is_video:
        return await YouTube.download(vid_id, None, video=True, videoid=f"{vid_id}_v")
    return await YouTube.download(vid_id, None, video=False, videoid=vid_id)


def _build_ffmpeg_cmd(
    media_url: str,
    rtmp_url: str,
    is_video: bool,
    cover_path: str | None = None,
    seek_seconds: int = 0,
) -> list[str]:
    ua = VISIONOS_UA if "c=VISIONOS" in str(media_url) else ANDROID_UA
    seek_args = ["-ss", str(int(seek_seconds))] if seek_seconds > 0 else []

    audio_filter = (
        "aresample=osr=48000:ochl=stereo:async=1000:first_pts=0,"
        "volume=1.4"
    )

    if not is_video:
        if cover_path and os.path.exists(cover_path):
            video_input = ["-loop", "1", "-framerate", "15", "-i", str(cover_path)]
            vf_chain = "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        else:
            video_input = ["-f", "lavfi", "-i", "color=c=black:s=640x360:r=15"]
            vf_chain = "format=yuv420p"

        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-fflags", "+genpts+igndts",
            *video_input,
            "-re",
            "-thread_queue_size", "4096",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-user_agent", ua,
            *seek_args,
            "-i", str(media_url),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf_chain,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-b:v", "250k", "-maxrate", "250k", "-bufsize", "500k",
            "-r", "15", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-af", audio_filter,
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
            "-shortest",
            "-flvflags", "no_duration_filesize",
            "-f", "flv", str(rtmp_url),
        ]

    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-fflags", "+genpts+igndts",
        "-re",
        "-thread_queue_size", "4096",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-user_agent", ua,
        *seek_args,
        "-i", str(media_url),
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=640:360,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-b:v", "650k", "-maxrate", "650k", "-bufsize", "1300k",
        "-r", "25", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
        "-af", audio_filter,
        "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
        "-flvflags", "no_duration_filesize",
        "-f", "flv", str(rtmp_url),
    ]


async def _drain_stderr(chat_id: int, proc: asyncio.subprocess.Process) -> None:
    buf = _rtmp_stderr.setdefault(chat_id, deque(maxlen=40))
    try:
        while proc.stderr:
            line = await proc.stderr.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if not decoded:
                continue
            buf.append(decoded)
            low = decoded.lower()
            if "error" in low or "fail" in low or "invalid" in low:
                _log("error", chat_id, f"ffmpeg: {decoded}")
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _start_track(chat_id: int, track: Dict[str, Any], seek_seconds: int = 0) -> None:
    await _kill_proc(chat_id)

    vid_id = track["vid_id"]
    is_video = track["is_video"]

    media_url, cover_path, rtmp_url = await asyncio.gather(
        _resolve_rtmp_stream_url(vid_id, is_video=is_video, prefer_progressive=False),
        _fetch_raw_yt_cover(vid_id),
        _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
    )

    if not media_url:
        media_url = await _resolve_rtmp_stream_url(vid_id, is_video=is_video, prefer_progressive=True)
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
        stderr=asyncio.subprocess.PIPE,
    )
    _rtmp_procs[chat_id] = proc
    _rtmp_state[chat_id] = {
        "track": track,
        "start_ts": time.time(),
        "offset": seek_seconds,
        "paused": False,
    }
    _rtmp_stderr[chat_id] = deque(maxlen=40)

    # تسجيل الجروب كمحادثة نشطة حتى لا ترفض ديكوريتورات البوت الاوامر، مع وضع seconds=0 لمنع تداخل seeker.py
    await add_active_chat(chat_id)
    await music_on(chat_id)
    db[chat_id] = [
        {
            "title": track["title"],
            "dur": track["duration_min"],
            "streamtype": "video" if is_video else "audio",
            "by": track["by"],
            "user_id": track.get("user_id", 0),
            "chat_id": chat_id,
            "file": f"rtmp_{vid_id}",
            "vidid": vid_id,
            "seconds": 0,
            "played": seek_seconds,
        }
    ]

    asyncio.create_task(_drain_stderr(chat_id, proc))

    async def _runner(cid: int, p: asyncio.subprocess.Process):
        t0 = time.time()
        with suppress(Exception):
            await p.wait()

        if _rtmp_procs.get(cid) is not p:
            return

        # في حال اغلق FFmpeg مبكرا (<3 ثوان) بسبب مكالمة عادية او رابط صوت مقيد، نعيد انشاء RTMP بـ itag=18 المضمون
        if (time.time() - t0) < 3.0 and not _rtmp_state.get(cid, {}).get("paused"):
            _log("warning", cid, "خروج مبكر لـ ffmpeg — جاري اعادة انشاء مكالمة RTMP واستخدام رابط ANDROID itag=18")
            with suppress(Exception):
                fallback_url, new_rtmp = await asyncio.gather(
                    _resolve_rtmp_stream_url(vid_id, is_video=is_video, prefer_progressive=True),
                    _ensure_rtmp_call(cid, force_recreate=True, title=track["title"]),
                )
                use_url = fallback_url or media_url
                retry_cmd = _build_ffmpeg_cmd(
                    media_url=use_url,
                    rtmp_url=new_rtmp,
                    is_video=is_video,
                    cover_path=cover_path,
                    seek_seconds=seek_seconds,
                )
                p2 = await asyncio.create_subprocess_exec(
                    *retry_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _rtmp_procs[cid] = p2
                if cid in _rtmp_state:
                    _rtmp_state[cid]["start_ts"] = time.time()
                asyncio.create_task(_drain_stderr(cid, p2))
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
            await _sync_rtmp_clear(cid)

    _rtmp_tasks[chat_id] = asyncio.create_task(_runner(chat_id, proc))


# ==========================================================
# 1. فتح مكالمة RTMP
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(افتح استيريو|افتح ستيريو|فتح استيريو|فتح ستيريو|rtmpon)$")
    & filters.group & ~BANNED_USERS,
    group=RTMP_GROUP,
)
@RTMPCheck
async def open_rtmp_call_cmd(client, message: Message, _, chat_id: int):
    mystic = await message.reply_text("**جاري فتح مكالمة الـ Stereo...**")
    async with _get_lock(chat_id):
        try:
            await _ensure_rtmp_call(chat_id, force_recreate=True)
            await mystic.edit_text(
                "**تم فتح مكالمة الـ Stereo بنجاح.**\n"
                "- صوت: `استيريو + اسم الاغنية`\n"
                "- فيديو: `فيديو استيريو + اسم الاغنية`\n"
                "- تحكم: `توقف` / `كمل` / `تخطي` / `مرر 15` / `رجع 15` / `ايقاف`\n"
                "- قفل الكول: `اقفل استيريو`"
            )
        except ChatAdminRequired:
            await mystic.edit_text("**ارفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 2. قفل مكالمة RTMP
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(اقفل استيريو|اقفل ستيريو|قفل استيريو|قفل ستيريو|rtmpoff)$")
    & filters.group & ~BANNED_USERS,
    group=RTMP_GROUP,
)
@RTMPCheck
async def close_rtmp_call_cmd(client, message: Message, _, chat_id: int):
    async with _get_lock(chat_id):
        await _discard_call(chat_id)
        await message.reply_text("**تم ايقاف البث واغلاق مكالمة الـ Stereo بالكامل.**")


# ==========================================================
# 3. التحكم النصي المباشر والمختصر (بدون تداخل مع ملفات admins)
# ==========================================================
def _rtmp_control_filter(_, __, message: Message) -> bool:
    text = (message.text or "").strip().lstrip("/!.")
    if not text:
        return False
    # 1. اوامر تحتوي على كلمة استيريو / ستيريو صراحة
    if re.match(
        r"^(ايقاف|اسكت|انهاء|وقف|استوب|توقف|وقف مؤقت|توقيف مؤقت|كمل|استئناف|تابع|تخطي|التالي)\s+(?:استيريو|ستيريو)$",
        text,
    ):
        return True
    if re.match(r"^(قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+\d+$", text):
        return True
    # 2. اذا كان بث الاستيريو نشطا في هذا الجروب، نلتقط الاوامر المختصرة (تخطي، اسكت، استوب، كمل، مرر 15) قبل admins
    if message.chat and is_rtmp_active(message.chat.id):
        if re.match(
            r"^(ايقاف|اسكت|انهاء|وقف|استوب|end|stop|توقف|وقف مؤقت|توقيف مؤقت|pause|كمل|استئناف|تابع|resume|تخطي|التالي|skip|next)$",
            text,
            flags=re.I,
        ):
            return True
        if re.match(r"^(قدم|مرر|رجع|seek|seekback)\s+\d+$", text, flags=re.I):
            return True
    return False


rtmp_ctl_filter = filters.create(_rtmp_control_filter, name="RTMPControlFilter")


@app.on_message(rtmp_ctl_filter & filters.group & ~BANNED_USERS, group=RTMP_GROUP)
@RTMPCheck
async def control_rtmp_text_cmd(client, message: Message, _, chat_id: int):
    text = (message.text or "").strip().lstrip("/!.")
    async with _get_lock(chat_id):
        st = _rtmp_state.get(chat_id)
        if not st:
            return await message.reply_text("**لا يوجد بث Stereo نشط حاليا.**")

        low = text.lower()

        if low.startswith(("ايقاف", "انهاء", "استوب", "end", "stop")) or low in ("وقف", "وقف استيريو", "وقف ستيريو"):
            await _kill_proc(chat_id)
            await _sync_rtmp_clear(chat_id)
            return await message.reply_text("**تم ايقاف بث الـ Stereo وتفريغ الطابور.**")

        if low.startswith(("توقف", "وقف مؤقت", "توقيف مؤقت", "اسكت", "pause")):
            if st.get("paused"):
                return await message.reply_text("**البث متوقف مؤقتا بالفعل.**")
            st["offset"] = _get_played_seconds(chat_id)
            st["paused"] = True
            await music_off(chat_id)
            await _kill_proc(chat_id)
            return await message.reply_text(f"**تم الايقاف المؤقت عند {_sec_to_str(st['offset'])}.**")

        if low.startswith(("كمل", "استئناف", "تابع", "resume")):
            if not st.get("paused"):
                return await message.reply_text("**البث يعمل بالفعل.**")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            return await message.reply_text(f"**تم الاستئناف من {_sec_to_str(st['offset'])}.**")

        if low.startswith(("تخطي", "التالي", "skip", "next")):
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await _kill_proc(chat_id)
                await _sync_rtmp_clear(chat_id)
                return await message.reply_text("**تم التخطي، الطابور فارغ.**")
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

        m = re.match(r"^(قدم|مرر|رجع|seek|seekback)(?:\s+(?:استيريو|ستيريو))?\s+(\d+)$", low)
        if m:
            action, sec_str = m.group(1), m.group(2)
            delta = int(sec_str)
            cur = _get_played_seconds(chat_id)
            new_pos = max(0, cur - delta) if action in ("رجع", "seekback") else (cur + delta)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await message.reply_text("**الوقت المطلوب يتجاوز مدة الاغنية.**")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            return await message.reply_text(f"**تم الانتقال الى {_sec_to_str(new_pos)}.**")


# ==========================================================
# 4. التشغيل الرئيسي
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(فيديو استيريو|فيديو ستيريو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$")
    & filters.group & ~BANNED_USERS,
    group=RTMP_GROUP,
)
@RTMPCheck
async def play_rtmp_stereo_cmd(client, message: Message, _, chat_id: int):
    raw_text = (message.text or "").strip().lstrip("/!.")
    for ignored in (
        "افتح استيريو", "افتح ستيريو", "فتح استيريو", "فتح ستيريو",
        "اقفل استيريو", "اقفل ستيريو", "قفل استيريو", "قفل ستيريو",
        "ايقاف استيريو", "ايقاف ستيريو", "توقف استيريو", "توقف ستيريو",
        "كمل استيريو", "كمل ستيريو", "تخطي استيريو", "تخطي ستيريو",
        "اسكت استيريو", "اسكت ستيريو", "انهاء استيريو", "استوب استيريو",
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
            "**الاستخدام:**\n"
            "- صوت: `استيريو اسم الاغنية`\n"
            "- فيديو: `فيديو استيريو اسم الاغنية`\n"
            "- فتح: `افتح استيريو`\n"
            "- قفل: `اقفل استيريو`"
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
                "user_id": message.from_user.id,
            }

            queue = _rtmp_queues.setdefault(chat_id, [])
            is_playing = chat_id in _rtmp_procs and _rtmp_procs[chat_id].returncode is None

            if is_playing:
                queue.append(track_item)
                pos = len(queue) - 1
                return await mystic.edit_text(
                    f"**تمت الاضافة للطابور في المركز #{pos}**\n\n"
                    f"**العنوان :** `{title[:45]}`\n"
                    f"**المدة :** `{duration_min}`\n"
                    f"**بواسطة :** {message.from_user.mention}"
                )

            queue.clear()
            queue.append(track_item)
            await _start_track(chat_id, track_item, seek_seconds=0)

            mode_label = "فيديو + Stereo 320kbps" if is_video else "صوت Stereo 320kbps"
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
            await mystic.edit_text("**ارفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 5. الأزرار التفاعلية
# ==========================================================
@app.on_callback_query(filters.regex(r"^rtmp_ctl\|") & ~BANNED_USERS, group=RTMP_GROUP)
@RTMPCheck
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
            await _sync_rtmp_clear(chat_id)
            with suppress(Exception):
                await callback_query.message.edit_caption(
                    f"**تم ايقاف البث بواسطة :** {callback_query.from_user.mention}"
                )
            return

        if action == "pause":
            st["offset"] = _get_played_seconds(chat_id)
            st["paused"] = True
            await music_off(chat_id)
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
                await _sync_rtmp_clear(chat_id)
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
