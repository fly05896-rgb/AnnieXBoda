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
from config import BANNED_USERS
from AnnieXMedia import YouTube, app
from AnnieXMedia.core.call import StreamController
from AnnieXMedia.utils.database import get_assistant, get_lang, is_maintenance
from AnnieXMedia.misc import SUDOERS
from strings import get_string

# ═══════════════════════════════════════════════════════════
# إعداد اللوج
# ═══════════════════════════════════════════════════════════
LOGGER = logging.getLogger("rtmp_stereo")
if not LOGGER.handlers:
    LOGGER.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [rtmp] %(message)s", datefmt="%H:%M:%S")
    )
    LOGGER.addHandler(_handler)
    LOGGER.propagate = False  # منع التكرار مع لوج البوت العام

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
# آخر 40 سطر من stderr لكل شات (للوج عند الانهيار)
_rtmp_stderr: Dict[int, deque] = {}


def _log(level: str, chat_id: int, msg: str) -> None:
    """دالة موحدة للوج مع chat_id."""
    tag = f"[chat={chat_id}]"
    getattr(LOGGER, level)(f"{tag} {msg}")


# ==========================================================
# ديكوريتور مخصص للـ RTMP
# ==========================================================
def RTMPCheck(mystic):
    async def wrapper(client, update):
        is_cb = isinstance(update, CallbackQuery)
        msg = update.message if is_cb else update
        user = update.from_user

        if await is_maintenance() is False:
            if user.id not in SUDOERS:
                txt = "البوت في وضع الصيانة حالياً."
                if is_cb:
                    return await update.answer(txt, show_alert=True)
                return await msg.reply_text(txt)

        if not is_cb:
            with suppress(Exception):
                await msg.delete()

        if not is_cb and msg.sender_chat:
            return await msg.reply_text(
                "عذراً، أنت تكتب بصفتك قناة أو مشرف متخفي. أظهر حسابك الشخصي."
            )

        if user.id not in SUDOERS:
            try:
                member = await app.get_chat_member(msg.chat.id, user.id)
                priv = member.privileges
                if not priv or not priv.can_manage_video_chats:
                    txt = "تحتاج صلاحية (إدارة المحادثات الصوتية) لتنفيذ هذا الأمر."
                    if is_cb:
                        return await update.answer(txt, show_alert=True)
                    return await msg.reply_text(txt)
            except Exception as e:
                _log("warning", msg.chat.id, f"فشل التحقق من صلاحيات {user.id}: {e}")
                txt = "خطأ في التحقق من صلاحياتك."
                if is_cb:
                    return await update.answer(txt, show_alert=True)
                return await msg.reply_text(txt)

        chat_id = msg.chat.id
        if is_cb:
            try:
                chat_id = int(update.data.split("|")[-1])
            except Exception:
                pass

        try:
            language = await get_lang(chat_id)
            _ = get_string(language)
        except Exception:
            _ = get_string("en")

        return await mystic(client, update, _, chat_id)
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


# ==========================================================
# إنهاء آمن مع تسجيل
# ==========================================================
async def _kill_proc(chat_id: int) -> None:
    _log("info", chat_id, "بدء إنهاء العملية الحالية")

    task = _rtmp_tasks.pop(chat_id, None)
    if task and not task.done():
        _log("info", chat_id, "إلغاء task المراقبة")
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    proc = _rtmp_procs.pop(chat_id, None)
    if proc and proc.returncode is None:
        _log("info", chat_id, f"إنهاء ffmpeg pid={proc.pid}")
        with suppress(Exception):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
                _log("info", chat_id, "انتهت العملية بسلام")
            except asyncio.TimeoutError:
                _log("warning", chat_id, "ffmpeg لم يستجب، جاري kill()")
                proc.kill()
                await proc.wait()
                _log("info", chat_id, "تم قتل العملية")
    else:
        _log("info", chat_id, "لا توجد عملية نشطة لإنهائها")


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
            LOGGER.warning(f"[cover] فشل تحميل غلاف {vid_id}: HTTP {resp.status}")
    except Exception as e:
        LOGGER.warning(f"[cover] خطأ تحميل غلاف {vid_id}: {e}")
    return None


async def _ensure_assistant(chat_id: int):
    _log("info", chat_id, "التحقق من الحساب المساعد")
    ub = await get_assistant(chat_id)
    try:
        member = await app.get_chat_member(chat_id, ub.id)
        if member.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
            _log("error", chat_id, f"الحساب المساعد {ub.id} محظور/مقيد")
            raise RuntimeError("الحساب المساعد محظور أو مقيد في هذا الجروب.")
    except UserNotParticipant:
        _log("warning", chat_id, "الحساب المساعد غير موجود، جاري دعوته")
        invitelink = await app.export_chat_invite_link(chat_id)
        if invitelink.startswith("https://t.me/+"):
            invitelink = invitelink.replace("https://t.me/+", "https://t.me/joinchat/")
        await ub.join_chat(invitelink)
        _log("info", chat_id, "تم ضم الحساب المساعد")
    await ub.resolve_peer(chat_id)
    return ub


async def _ensure_rtmp_call(chat_id: int, force_recreate: bool = False, title: str = "Hi-Fi Stereo") -> str:
    _log("info", chat_id, f"تأمين مكالمة RTMP (force={force_recreate})")

    if chat_id in StreamController.active_calls:
        _log("info", chat_id, "إيقاف بث PyTgCalls المتعارض")
        with suppress(Exception):
            await StreamController.stop_stream(chat_id)

    if not force_recreate and chat_id in _rtmp_url_cache:
        _log("info", chat_id, "استخدام RTMP URL من الكاش")
        return _rtmp_url_cache[chat_id]

    ub = await _ensure_assistant(chat_id)
    peer = await ub.resolve_peer(chat_id)

    _log("info", chat_id, "طلب GetGroupCallStreamRtmpUrl و GetFullChannel")
    rtmp_info, full_chat = await asyncio.gather(
        ub.invoke(raw.functions.phone.GetGroupCallStreamRtmpUrl(peer=peer, revoke=False)),
        ub.invoke(raw.functions.channels.GetFullChannel(channel=peer)),
    )
    rtmp_full_url = f"{rtmp_info.url}{rtmp_info.key}"
    active_call = full_chat.full_chat.call

    if active_call and force_recreate:
        _log("info", chat_id, "حذف المكالمة القديمة")
        with suppress(Exception):
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=active_call))
        active_call = None

    if not active_call:
        _log("info", chat_id, "إنشاء مكالمة RTMP جديدة")
        await ub.invoke(
            raw.functions.phone.CreateGroupCall(
                peer=peer,
                random_id=random.randint(10000, 99999999),
                rtmp_stream=True,
                title=title[:32],
            )
        )
        _log("info", chat_id, "تم إنشاء المكالمة")

    _rtmp_url_cache[chat_id] = rtmp_full_url
    return rtmp_full_url


async def _discard_call(chat_id: int) -> bool:
    _log("info", chat_id, "بدء حذف/قفل مكالمة RTMP")
    await _kill_proc(chat_id)
    _rtmp_queues.pop(chat_id, None)
    _rtmp_state.pop(chat_id, None)
    _rtmp_url_cache.pop(chat_id, None)
    _rtmp_stderr.pop(chat_id, None)
    try:
        ub = await get_assistant(chat_id)
        peer = await ub.resolve_peer(chat_id)
        full_chat = await ub.invoke(raw.functions.channels.GetFullChannel(channel=peer))
        if full_chat.full_chat.call:
            _log("info", chat_id, "استدعاء DiscardGroupCall")
            await ub.invoke(raw.functions.phone.DiscardGroupCall(call=full_chat.full_chat.call))
            _log("info", chat_id, "تم قفل المكالمة بنجاح")
            return True
        _log("info", chat_id, "لا توجد مكالمة لقفلها")
    except Exception as e:
        _log("error", chat_id, f"فشل قفل المكالمة: {type(e).__name__}: {e}")
    return False


# ==========================================================
# بناء أمر ffmpeg
# ==========================================================
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
        "volume=1.5"
    )

    stability_args = [
        "-nostdin",
        "-probesize", "5000000",
        "-analyzeduration", "5000000",
        "-max_delay", "500000",
        "-buffer_size", "1000000",
        "-flvflags", "no_duration_filesize",
        "-f", "flv",
        str(rtmp_url),
    ]

    if not is_video:
        if cover_path and os.path.exists(cover_path):
            video_input = ["-re", "-loop", "1", "-framerate", "15", "-i", str(cover_path)]
        else:
            video_input = ["-re", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=15"]

        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-fflags", "+genpts+igndts",
            *video_input,
            "-thread_queue_size", "4096",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-user_agent", ua,
            *seek_args,
            "-i", str(media_url),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", "scale=640:360,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-b:v", "200k", "-maxrate", "200k", "-bufsize", "400k",
            "-r", "15", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-af", audio_filter,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest",
            *stability_args,
        ]

    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-fflags", "+genpts+igndts", "-re",
        "-thread_queue_size", "4096",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-user_agent", ua,
        *seek_args,
        "-i", str(media_url),
        "-vf", "scale=640:360,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-b:v", "500k", "-maxrate", "500k", "-bufsize", "1000k",
        "-r", "25", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
        "-af", audio_filter,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        *stability_args,
    ]


# ==========================================================
# قراءة stderr من ffmpeg وتسجيله
# ==========================================================
async def _drain_stderr(chat_id: int, proc: asyncio.subprocess.Process) -> None:
    """يقرأ stderr سطراً بسطر ويحفظ آخر 40 سطر في الذاكرة."""
    buf = _rtmp_stderr.setdefault(chat_id, deque(maxlen=40))
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if not decoded:
                continue
            buf.append(decoded)
            # نطبع كل تحذير/خطأ فوراً على مستوى اللوج
            low = decoded.lower()
            if "error" in low or "fail" in low or "invalid" in low or "no such" in low:
                _log("error", chat_id, f"ffmpeg: {decoded}")
            elif "warning" in low or "deprecated" in low:
                _log("warning", chat_id, f"ffmpeg: {decoded}")
            else:
                _log("debug", chat_id, f"ffmpeg: {decoded}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _log("warning", chat_id, f"خطأ في قراءة stderr: {e}")


async def _start_track(chat_id: int, track: Dict[str, Any], seek_seconds: int = 0) -> None:
    _log("info", chat_id, f"بدء تشغيل مقطع: {track.get('vid_id')} | seek={seek_seconds}")
    await _kill_proc(chat_id)

    vid_id = track["vid_id"]
    is_video = track["is_video"]

    if is_video:
        _log("info", chat_id, "تحميل رابط فيديو + غلاف + RTMP")
        media_url, cover_path, rtmp_url = await asyncio.gather(
            YouTube.download(vid_id, None, video=True, videoid=f"{vid_id}_v"),
            _fetch_raw_yt_cover(vid_id),
            _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
        )
    else:
        _log("info", chat_id, "تحميل رابط صوت + غلاف + RTMP")
        media_url, cover_path, rtmp_url = await asyncio.gather(
            YouTube.download(vid_id, None, video=False, videoid=vid_id),
            _fetch_raw_yt_cover(vid_id),
            _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
        )

    if not media_url:
        _log("error", chat_id, "media_url فارغ — فشل استخراج رابط البث")
        raise RuntimeError("فشل استخراج رابط البث المباشر.")
    _log("info", chat_id, f"media_url جاهز (len={len(media_url)}), غلاف={bool(cover_path)}")

    track["media_url"] = media_url
    track["cover_path"] = cover_path

    cmd = _build_ffmpeg_cmd(
        media_url=media_url,
        rtmp_url=rtmp_url,
        is_video=is_video,
        cover_path=cover_path,
        seek_seconds=seek_seconds,
    )
    _log("debug", chat_id, f"ffmpeg cmd: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,  # ← بدل DEVNULL لالتقاط الأخطاء
    )
    _log("info", chat_id, f"ffmpeg بدأ pid={proc.pid}")
    _rtmp_procs[chat_id] = proc
    _rtmp_state[chat_id] = {
        "track": track,
        "start_ts": time.time(),
        "offset": seek_seconds,
        "paused": False,
    }
    _rtmp_stderr[chat_id] = deque(maxlen=40)

    # مهمة قراءة stderr
    stderr_task = asyncio.create_task(_drain_stderr(chat_id, proc))

    async def _runner(cid: int, p: asyncio.subprocess.Process):
        t0 = time.time()
        with suppress(Exception):
            await p.wait()

        rc = p.returncode
        dur = time.time() - t0
        _log(
            "info" if rc == 0 else "warning",
            cid,
            f"ffmpeg انتهى rc={rc} بعد {dur:.1f}s",
        )
        # لو خرج بسرعة، أظهر آخر رسائل stderr تلقائياً
        if rc != 0 and (time.time() - t0) < 5:
            buf = _rtmp_stderr.get(cid) or []
            _log("error", cid, f"آخر {len(buf)} سطر من ffmpeg قبل الانهيار:")
            for ln in list(buf)[-15:]:
                _log("error", cid, f"  ├─ {ln}")

        if _rtmp_procs.get(cid) is not p:
            _log("info", cid, "عملية أخرى أصبحت الحالية — تجاهل")
            return
        if (time.time() - t0) < 1.5 and not _rtmp_state.get(cid, {}).get("paused"):
            _log("warning", cid, "خروج مبكر <1.5s — إعادة إنشاء RTMP والمحاولة")
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
                    stderr=asyncio.subprocess.PIPE,
                )
                _log("info", cid, f"إعادة تشغيل ffmpeg pid={p2.pid}")
                _rtmp_procs[cid] = p2
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
            _log("info", cid, f"الانتقال للمقطع التالي: {next_track.get('vid_id')}")
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
            _log("info", cid, "الطابور فارغ — إيقاف الحالة")
            _rtmp_state.pop(cid, None)

    _rtmp_tasks[chat_id] = asyncio.create_task(_runner(chat_id, proc))


# ==========================================================
# 1. فتح مكالمة RTMP
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(افتح استيريو|افتح ستيريو|فتح استيريو|فتح ستيريو|rtmpon)$")
    & filters.group & ~BANNED_USERS
)
@RTMPCheck
async def open_rtmp_call_cmd(client, message: Message, _, chat_id: int):
    _log("info", chat_id, "أمر: فتح مكالمة RTMP")
    mystic = await message.reply_text("**جاري فتح مكالمة الـ Stereo...**")
    async with _get_lock(chat_id):
        try:
            await _ensure_rtmp_call(chat_id, force_recreate=True)
            _log("info", chat_id, "تم فتح المكالمة بنجاح")
            await mystic.edit_text(
                "**تم فتح مكالمة الـ Stereo بنجاح.**\n"
                "- صوت: `استيريو + اسم الاغنية`\n"
                "- فيديو: `فيديو استيريو + اسم الاغنية`\n"
                "- توقف/استئناف: `توقف استيريو` / `كمل استيريو`\n"
                "- قفل: `اقفل استيريو`"
            )
        except ChatAdminRequired:
            _log("error", chat_id, "ChatAdminRequired — البوت ليس مشرفاً")
            await mystic.edit_text("**ارفع الحساب المساعد مشرفاً بصلاحية إدارة المحادثات الصوتية.**")
        except Exception as e:
            _log("error", chat_id, f"فشل فتح المكالمة: {type(e).__name__}: {e}")
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 2. قفل مكالمة RTMP
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(اقفل استيريو|اقفل ستيريو|قفل استيريو|قفل ستيريو|rtmpoff)$")
    & filters.group & ~BANNED_USERS
)
@RTMPCheck
async def close_rtmp_call_cmd(client, message: Message, _, chat_id: int):
    _log("info", chat_id, "أمر: قفل مكالمة RTMP")
    async with _get_lock(chat_id):
        await _discard_call(chat_id)
        await message.reply_text("**تم ايقاف البث واغلاق مكالمة الـ Stereo بالكامل.**")


# ==========================================================
# 3. التحكم النصي
# ==========================================================
@app.on_message(
    filters.regex(
        r"^[/!.]?(ايقاف استيريو|ايقاف ستيريو|اسكت استيريو|توقف استيريو|توقف ستيريو|كمل استيريو|كمل ستيريو|تخطي استيريو|تخطي ستيريو|التالي استيريو|(?:قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+\d+)"
    )
    & filters.group & ~BANNED_USERS
)
@RTMPCheck
async def control_rtmp_text_cmd(client, message: Message, _, chat_id: int):
    text = (message.text or "").strip().lstrip("/!.")
    _log("info", chat_id, f"أمر تحكم: {text}")
    async with _get_lock(chat_id):
        st = _rtmp_state.get(chat_id)
        if not st:
            _log("warning", chat_id, "لا توجد حالة RTMP نشطة")
            return await message.reply_text("**لا يوجد بث Stereo نشط حالياً.**")

        if text.startswith(("ايقاف", "اسكت")):
            _log("info", chat_id, "تنفيذ: إيقاف")
            await _kill_proc(chat_id)
            _rtmp_queues.pop(chat_id, None)
            _rtmp_state.pop(chat_id, None)
            return await message.reply_text("**تم ايقاف البث وتفريغ الطابور.**")

        if text.startswith("توقف"):
            if st.get("paused"):
                return await message.reply_text("**البث متوقف مؤقتاً بالفعل.**")
            st["offset"] = _get_played_seconds(chat_id)
            st["paused"] = True
            _log("info", chat_id, f"إيقاف مؤقت عند {st['offset']}s")
            await _kill_proc(chat_id)
            return await message.reply_text(f"**تم الايقاف المؤقت عند {_sec_to_str(st['offset'])}.**")

        if text.startswith("كمل"):
            if not st.get("paused"):
                return await message.reply_text("**البث يعمل بالفعل.**")
            _log("info", chat_id, f"استئناف من {st.get('offset')}s")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            return await message.reply_text(f"**تم الاستئناف من {_sec_to_str(st['offset'])}.**")

        if text.startswith(("تخطي", "التالي")):
            _log("info", chat_id, "تنفيذ: تخطي")
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await _kill_proc(chat_id)
                _rtmp_state.pop(chat_id, None)
                _log("info", chat_id, "تخطي — الطابور فارغ")
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

        m = re.match(r"^(قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+(\d+)$", text)
        if m:
            action, sec_str = m.group(1), m.group(2)
            delta = int(sec_str)
            cur = _get_played_seconds(chat_id)
            new_pos = max(0, cur - delta) if action == "رجع" else (cur + delta)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await message.reply_text("**الوقت يتجاوز مدة الاغنية.**")
            _log("info", chat_id, f"قفز إلى {new_pos}s")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            return await message.reply_text(f"**تم الانتقال الى {_sec_to_str(new_pos)}.**")


# ==========================================================
# 4. التشغيل الرئيسي
# ==========================================================
@app.on_message(
    filters.regex(r"^[/!.]?(فيديو استيريو|فيديو ستيريو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$")
    & filters.group & ~BANNED_USERS
)
@RTMPCheck
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
            "**الاستخدام:**\n"
            "- صوت: `استيريو اسم الاغنية`\n"
            "- فيديو: `فيديو استيريو اسم الاغنية`\n"
            "- فتح: `افتح استيريو`\n"
            "- قفل: `اقفل استيريو`"
        )

    _log("info", chat_id, f"أمر تشغيل: '{query}' | is_video={is_video}")
    mystic = await message.reply_text("**جاري البحث والتشغيل...**")

    async with _get_lock(chat_id):
        try:
            _log("info", chat_id, f"بحث YouTube عن '{query}'")
            details, vid_id = await YouTube.track(query)
            if not details or not vid_id or details.get("title") == "Unknown":
                _log("warning", chat_id, f"لا نتيجة لـ '{query}'")
                return await mystic.edit_text("**لم يتم العثور على النتيجة.**")

            title = details.get("title", "Unknown")
            duration_min = details.get("duration_min", "0:00")
            duration_sec = int(details.get("duration_sec") or _parse_dur_to_sec(duration_min))
            _log("info", chat_id, f"نتيجة: {vid_id} | {title[:40]} | {duration_min}")

            track_item = {
                "vid_id": vid_id, "title": title,
                "duration_min": duration_min, "duration_sec": duration_sec,
                "is_video": is_video, "by": message.from_user.mention,
            }

            queue = _rtmp_queues.setdefault(chat_id, [])
            is_playing = chat_id in _rtmp_procs and _rtmp_procs[chat_id].returncode is None

            if is_playing:
                queue.append(track_item)
                pos = len(queue) - 1
                _log("info", chat_id, f"أضيف للطابور #{pos}")
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
            _log("error", chat_id, "ChatAdminRequired في التشغيل")
            await mystic.edit_text("**ارفع الحساب المساعد مشرفاً بصلاحية إدارة المحادثات الصوتية.**")
        except Exception as e:
            _log("error", chat_id, f"فشل التشغيل: {type(e).__name__}: {e}")
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


# ==========================================================
# 5. الأزرار التفاعلية
# ==========================================================
@app.on_callback_query(filters.regex(r"^rtmp_ctl\|") & ~BANNED_USERS)
@RTMPCheck
async def rtmp_buttons_cb(client, callback_query: CallbackQuery, _, chat_id: int):
    parts = callback_query.data.split("|")
    action = parts[1]
    _log("info", chat_id, f"زر: {action}")

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
            return await callback_query.answer("لا يوجد بث نشط حالياً.", show_alert=True)

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
            _log("info", chat_id, f"إيقاف مؤقت (زر) عند {st['offset']}s")
            await _kill_proc(chat_id)
            await callback_query.answer(f"تم التوقف عند {_sec_to_str(st['offset'])}")
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_buttons(chat_id, paused=True))
            return

        if action == "resume":
            await callback_query.answer("جاري الاستئناف...")
            _log("info", chat_id, f"استئناف (زر) من {st.get('offset')}s")
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
            _log("info", chat_id, f"قفز (زر) إلى {new_pos}s")
            await callback_query.answer(f"الانتقال الى {_sec_to_str(new_pos)}")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_buttons(chat_id, paused=False))
            return

        if action == "skip":
            _log("info", chat_id, "تخطي (زر)")
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
