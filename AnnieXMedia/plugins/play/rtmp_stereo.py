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
import aiohttp
from pyrogram import enums, filters, raw
from pyrogram.errors import ChatAdminRequired, UserNotParticipant
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [rtmp] %(message)s", datefmt="%H:%M:%S"))
    LOGGER.addHandler(_h)
    LOGGER.propagate = False

ANDROID_UA = "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip"
VISIONOS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)

COVER_DIR = "/dev/shm/rtmp_covers" if os.path.exists("/dev/shm") else "/tmp/rtmp_covers"
MEDIA_DIR = "/dev/shm/rtmp_media" if os.path.exists("/dev/shm") else "/tmp/rtmp_media"
os.makedirs(COVER_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)

RTMP_GROUP = -2
MAX_CACHED_MEDIA_FILES = 12
MAX_LOCAL_DOWNLOAD_MB = 85

_rtmp_procs: Dict[int, asyncio.subprocess.Process] = {}
_rtmp_tasks: Dict[int, asyncio.Task] = {}
_rtmp_locks: Dict[int, asyncio.Lock] = {}
_rtmp_url_cache: Dict[int, str] = {}
_rtmp_call_ready: Dict[int, bool] = {}
_rtmp_recreating: set[int] = set()
_rtmp_queues: Dict[int, List[Dict[str, Any]]] = {}
_rtmp_state: Dict[int, Dict[str, Any]] = {}
_rtmp_stderr: Dict[int, deque] = {}


def is_rtmp_active(chat_id: int) -> bool:
    if chat_id in _rtmp_state:
        return True
    proc = _rtmp_procs.get(chat_id)
    return bool(proc and proc.returncode is None)


def is_rtmp_recreating(chat_id: int) -> bool:
    return chat_id in _rtmp_recreating


async def _has_perm(chat_id: int, user_id: int) -> bool:
    if user_id in SUDOERS or await is_nonadmin_chat(chat_id):
        return True
    if user_id in (adminlist.get(chat_id) or []):
        return True
    with suppress(Exception):
        token = await int_to_alpha(user_id)
        if token in await get_authuser_names(chat_id):
            return True
    with suppress(Exception):
        m = await app.get_chat_member(chat_id, user_id)
        if m.status == enums.ChatMemberStatus.OWNER:
            return True
        if m.status == enums.ChatMemberStatus.ADMINISTRATOR:
            if not m.privileges or m.privileges.can_manage_video_chats:
                return True
    return False


def RTMPCheck(fn):
    async def wrapper(client, update):
        is_cb = isinstance(update, CallbackQuery)
        msg = update.message if is_cb else update
        user = update.from_user

        if not user and not is_cb and msg.sender_chat:
            await msg.reply_text("اظهر حسابك الشخصي لاستخدام الامر.")
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

        if not await _has_perm(chat_id, user.id):
            txt = "تحتاج صلاحية ادارة المحادثات الصوتية لتنفيذ هذا الامر."
            if is_cb:
                return await update.answer(txt, show_alert=True)
            await msg.reply_text(txt)
            return await msg.stop_propagation()

        if not is_cb:
            with suppress(Exception):
                await msg.delete()

        try:
            _ = get_string(await get_lang(chat_id))
        except Exception:
            _ = get_string("ar")

        await fn(client, update, _, chat_id)
        if not is_cb:
            await msg.stop_propagation()

    return wrapper


def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _rtmp_locks:
        _rtmp_locks[chat_id] = asyncio.Lock()
    return _rtmp_locks[chat_id]


def _parse_dur(dur_str: str) -> int:
    if not dur_str or str(dur_str).lower() == "live":
        return 0
    try:
        parts = [int(p) for p in str(dur_str).split(":") if p.isdigit()]
        return sum(p * (60**i) for i, p in enumerate(reversed(parts)))
    except Exception:
        return 0


def _fmt_sec(sec: int) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def _played_sec(chat_id: int) -> int:
    st = _rtmp_state.get(chat_id)
    if not st:
        return 0
    if st.get("paused"):
        return int(st.get("offset", 0))
    return int(st.get("offset", 0) + (time.time() - st.get("start_ts", time.time())))


def _rtmp_kb(chat_id: int, paused: bool = False) -> InlineKeyboardMarkup:
    p_lbl = "استئناف" if paused else "توقف مؤقت"
    p_cmd = "resume" if paused else "pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="-15 ث", callback_data=f"rtmp_ctl|rew15|{chat_id}"),
                InlineKeyboardButton(text=p_lbl, callback_data=f"rtmp_ctl|{p_cmd}|{chat_id}"),
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


def _prune_media_cache(keep_path: str | None = None) -> None:
    with suppress(Exception):
        files = []
        for name in os.listdir(MEDIA_DIR):
            full = os.path.join(MEDIA_DIR, name)
            if os.path.isfile(full) and full != keep_path:
                files.append((os.path.getmtime(full), full))
        if len(files) > MAX_CACHED_MEDIA_FILES:
            files.sort()
            for _, fpath in files[:-MAX_CACHED_MEDIA_FILES]:
                with suppress(Exception):
                    os.remove(fpath)


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


async def _clear_rtmp_state(chat_id: int) -> None:
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
    if not is_rtmp_active(chat_id) and not _rtmp_call_ready.get(chat_id):
        return
    if discard_call:
        await _discard_call(chat_id)
    else:
        await _kill_proc(chat_id)
        await _clear_rtmp_state(chat_id)
        _rtmp_call_ready.pop(chat_id, None)
        _rtmp_url_cache.pop(chat_id, None)


async def _fetch_cover(vid_id: str) -> str | None:
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
        m = await app.get_chat_member(chat_id, ub.id)
        if m.status in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.RESTRICTED):
            raise RuntimeError("الحساب المساعد محظور او مقيد في هذا الجروب.")
    except UserNotParticipant:
        link = await app.export_chat_invite_link(chat_id)
        if link.startswith("https://t.me/+"):
            link = link.replace("https://t.me/+", "https://t.me/joinchat/")
        await ub.join_chat(link)
    await ub.resolve_peer(chat_id)
    return ub


async def _is_active_call_rtmp(ub, active_call) -> bool:
    if not active_call:
        return False
    try:
        res = await ub.invoke(raw.functions.phone.GetGroupCall(call=active_call, limit=0))
        return bool(getattr(res.call, "rtmp_stream", False))
    except Exception:
        return False


async def _ensure_rtmp_call(chat_id: int, force_recreate: bool = False, title: str = "Hi-Fi Stereo") -> str:
    from AnnieXMedia.core.call import StreamController

    if chat_id in StreamController.active_calls:
        with suppress(Exception):
            await StreamController.stop_stream(chat_id)
        force_recreate = True

    ub = await _ensure_assistant(chat_id)
    peer = await ub.resolve_peer(chat_id)

    rtmp_info, full_chat = await asyncio.gather(
        ub.invoke(raw.functions.phone.GetGroupCallStreamRtmpUrl(peer=peer, revoke=False)),
        ub.invoke(raw.functions.channels.GetFullChannel(channel=peer)),
    )
    rtmp_full_url = f"{rtmp_info.url.rstrip('/')}/{rtmp_info.key.lstrip('/')}"
    active_call = full_chat.full_chat.call

    is_rtmp_call = await _is_active_call_rtmp(ub, active_call) if active_call else False
    need_recreate = force_recreate or not active_call or not is_rtmp_call

    if need_recreate:
        _rtmp_recreating.add(chat_id)
        try:
            if active_call:
                await ub.invoke(raw.functions.phone.DiscardGroupCall(call=active_call))
                await asyncio.sleep(0.8)

            await ub.invoke(
                raw.functions.phone.CreateGroupCall(
                    peer=peer,
                    random_id=random.randint(10000, 99999999),
                    rtmp_stream=True,
                    title=title[:32],
                )
            )
            _rtmp_call_ready[chat_id] = True
            _rtmp_url_cache[chat_id] = rtmp_full_url
            await asyncio.sleep(1.2)
        finally:
            _rtmp_recreating.discard(chat_id)
    else:
        _rtmp_call_ready[chat_id] = True
        _rtmp_url_cache[chat_id] = rtmp_full_url

    return rtmp_full_url


async def _discard_call(chat_id: int) -> bool:
    await _kill_proc(chat_id)
    await _clear_rtmp_state(chat_id)
    _rtmp_url_cache.pop(chat_id, None)
    _rtmp_call_ready.pop(chat_id, None)
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


async def _resolve_stream_url(vid_id: str, is_video: bool, force_prog: bool = False) -> str | None:
    if force_prog or is_video:
        url = await YouTube.download(vid_id, None, video=True, videoid=f"{vid_id}_v")
        if url:
            return url
        return await YouTube.recover_stream(vid_id, videoid=vid_id, video=True)
    return await YouTube.download(vid_id, None, video=False, videoid=vid_id)


async def _download_to_ram(media_url: str, vid_id: str, is_video: bool, is_live: bool = False) -> str | None:
    if is_live or not media_url or "m3u8" in str(media_url).lower():
        return None

    ext = "mp4" if is_video else "m4a"
    local_path = os.path.join(MEDIA_DIR, f"{vid_id}_{'v' if is_video else 'a'}.{ext}")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 32 * 1024:
        return local_path

    tmp_path = f"{local_path}.part"
    ua = VISIONOS_UA if "c=VISIONOS" in str(media_url) else ANDROID_UA
    headers = {"User-Agent": ua, "Accept": "*/*"}

    try:
        timeout = aiohttp.ClientTimeout(total=45, connect=6, sock_read=15)
        async with aiohttp.ClientSession(timeout=timeout) as dl_session:
            total_written = 0
            max_bytes = MAX_LOCAL_DOWNLOAD_MB * 1024 * 1024
            chunk_size = 2 * 1024 * 1024

            async with dl_session.head(media_url, headers=headers) as head_resp:
                content_len = int(head_resp.headers.get("Content-Length") or 0)

            if content_len > max_bytes:
                return None

            async with aiofiles.open(tmp_path, "wb") as f:
                if content_len > 0:
                    start = 0
                    while start < content_len:
                        end = min(start + chunk_size - 1, content_len - 1)
                        req_headers = {**headers, "Range": f"bytes={start}-{end}"}
                        async with dl_session.get(media_url, headers=req_headers) as resp:
                            if resp.status not in (200, 206):
                                raise RuntimeError(f"HTTP {resp.status}")
                            async for data in resp.content.iter_chunked(131072):
                                await f.write(data)
                                total_written += len(data)
                        start = end + 1
                else:
                    async with dl_session.get(media_url, headers=headers) as resp:
                        if resp.status not in (200, 206):
                            raise RuntimeError(f"HTTP {resp.status}")
                        async for data in resp.content.iter_chunked(131072):
                            total_written += len(data)
                            if total_written > max_bytes:
                                raise RuntimeError("File too large for RAM cache")
                            await f.write(data)

        if total_written > 16 * 1024:
            os.replace(tmp_path, local_path)
            _prune_media_cache(keep_path=local_path)
            return local_path
    except Exception as e:
        LOGGER.warning(f"RAM pre-download failed for {vid_id}, falling back to direct stream: {e}")
    with suppress(Exception):
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return None


def _build_ffmpeg_cmd(
    media_source: str,
    rtmp_url: str,
    is_video: bool,
    cover_path: str | None = None,
    seek_seconds: int = 0,
) -> list[str]:
    is_local = os.path.exists(str(media_source))
    seek_args = ["-ss", str(int(seek_seconds))] if seek_seconds > 0 else []

    net_args: list[str] = []
    if not is_local:
        ua = VISIONOS_UA if "c=VISIONOS" in str(media_source) else ANDROID_UA
        net_args = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "5",
            "-user_agent", ua,
        ]

    if not is_video:
        if cover_path and os.path.exists(cover_path):
            v_in = ["-thread_queue_size", "4096", "-loop", "1", "-framerate", "25", "-i", str(cover_path)]
            vf = "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=25,format=yuv420p"
        else:
            v_in = ["-thread_queue_size", "4096", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25"]
            vf = "fps=25,format=yuv420p"

        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            *v_in,
            "-re",
            "-thread_queue_size", "4096",
            *net_args,
            *seek_args,
            "-i", str(media_source),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
            "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p", "-bf", "0",
            "-b:v", "400k", "-maxrate", "500k", "-bufsize", "1000k",
            "-r", "25", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
            "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
            "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest",
            "-flvflags", "no_duration_filesize",
            "-f", "flv", str(rtmp_url),
        ]

    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-re",
        "-thread_queue_size", "4096",
        *net_args,
        *seek_args,
        "-i", str(media_source),
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=25,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast",
        "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p", "-bf", "0",
        "-b:v", "1000k", "-maxrate", "1200k", "-bufsize", "2400k",
        "-r", "25", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0",
        "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-flvflags", "no_duration_filesize",
        "-f", "flv", str(rtmp_url),
    ]


async def _drain_stderr(chat_id: int, proc: asyncio.subprocess.Process) -> None:
    buf = _rtmp_stderr.setdefault(chat_id, deque(maxlen=30))
    try:
        while proc.stderr:
            line = await proc.stderr.readline()
            if not line:
                break
            dec = line.decode("utf-8", errors="replace").rstrip()
            if dec:
                buf.append(dec)
                low = dec.lower()
                if any(k in low for k in ("error", "failed", "refused", "broken pipe", "forbidden", "403", "non-monotonous")):
                    LOGGER.warning(f"[{chat_id}] ffmpeg: {dec}")
    except Exception:
        pass


async def _start_track(chat_id: int, track: Dict[str, Any], seek_seconds: int = 0) -> None:
    await _kill_proc(chat_id)

    vid_id = track["vid_id"]
    is_video = track["is_video"]
    is_live = str(track.get("duration_min", "")).lower() == "live" or int(track.get("duration_sec") or 0) == 0

    media_url, cover_path, rtmp_url = await asyncio.gather(
        _resolve_stream_url(vid_id, is_video=is_video, force_prog=False),
        _fetch_cover(vid_id),
        _ensure_rtmp_call(chat_id, force_recreate=False, title=track["title"]),
    )

    if not media_url:
        media_url = await _resolve_stream_url(vid_id, is_video=is_video, force_prog=True)
    if not media_url:
        raise RuntimeError("فشل استخراج رابط البث المباشر.")

    local_media = track.get("local_path")
    if not (local_media and os.path.exists(local_media)):
        local_media = await _download_to_ram(media_url, vid_id=vid_id, is_video=is_video, is_live=is_live)

    media_source = local_media or media_url
    track["media_url"] = media_url
    track["local_path"] = local_media
    track["cover_path"] = cover_path

    cmd = _build_ffmpeg_cmd(
        media_source=media_source,
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
    _rtmp_stderr[chat_id] = deque(maxlen=30)

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
            "seconds": track.get("duration_sec", 0),
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

        if (time.time() - t0) < 4.0 and not _rtmp_state.get(cid, {}).get("paused"):
            LOGGER.warning(f"[{cid}] RTMP exited early ({time.time() - t0:.1f}s), recreating RTMP call and retrying...")
            with suppress(Exception):
                fb_url, new_rtmp = await asyncio.gather(
                    _resolve_stream_url(vid_id, is_video=is_video, force_prog=True),
                    _ensure_rtmp_call(cid, force_recreate=True, title=track["title"]),
                )
                retry_src = local_media if (local_media and os.path.exists(local_media)) else (fb_url or media_url)
                retry_cmd = _build_ffmpeg_cmd(
                    media_source=retry_src,
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
            nxt = queue[0]
            with suppress(Exception):
                await _start_track(cid, nxt, seek_seconds=0)
                await app.send_photo(
                    cid,
                    photo=nxt.get("cover_path") or f"https://i.ytimg.com/vi/{nxt['vid_id']}/hqdefault.jpg",
                    caption=(
                        f"**تم تشغيل التالي في بث الـ Stereo**\n\n"
                        f"**العنوان :** `{nxt['title'][:45]}`\n"
                        f"**المدة :** `{nxt['duration_min']}`\n"
                        f"**بواسطة :** {nxt['by']}"
                    ),
                    reply_markup=_rtmp_kb(cid, paused=False),
                )
        else:
            await _clear_rtmp_state(cid)

    _rtmp_tasks[chat_id] = asyncio.create_task(_runner(chat_id, proc))


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
                "- فيديو: `استيريو فيديو + اسم الاغنية`\n"
                "- تحكم: `توقف` / `كمل` / `تخطي` / `مرر 15` / `رجع 15` / `ايقاف`\n"
                "- قفل الكول: `اقفل استيريو`"
            )
        except ChatAdminRequired:
            await mystic.edit_text("**ارفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


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


def _ctl_filter(_, __, message: Message) -> bool:
    text = (message.text or "").strip().lstrip("/!.")
    if not text:
        return False
    if re.match(
        r"^(ايقاف|اسكت|انهاء|وقف|استوب|توقف|وقف مؤقت|توقيف مؤقت|كمل|استئناف|تابع|تخطي|التالي)\s+(?:استيريو|ستيريو)$",
        text,
    ):
        return True
    if re.match(r"^(قدم|مرر|رجع)\s+(?:استيريو|ستيريو)\s+\d+$", text):
        return True
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


@app.on_message(filters.create(_ctl_filter) & filters.group & ~BANNED_USERS, group=RTMP_GROUP)
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
            await _clear_rtmp_state(chat_id)
            return await message.reply_text("**تم ايقاف بث الـ Stereo وتفريغ الطابور.**")

        if low.startswith(("توقف", "وقف مؤقت", "توقيف مؤقت", "اسكت", "pause")):
            if st.get("paused"):
                return await message.reply_text("**البث متوقف مؤقتا بالفعل.**")
            st["offset"] = _played_sec(chat_id)
            st["paused"] = True
            await music_off(chat_id)
            await _kill_proc(chat_id)
            return await message.reply_text(f"**تم الايقاف المؤقت عند {_fmt_sec(st['offset'])}.**")

        if low.startswith(("كمل", "استئناف", "تابع", "resume")):
            if not st.get("paused"):
                return await message.reply_text("**البث يعمل بالفعل.**")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            return await message.reply_text(f"**تم الاستئناف من {_fmt_sec(st['offset'])}.**")

        if low.startswith(("تخطي", "التالي", "skip", "next")):
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await _kill_proc(chat_id)
                await _clear_rtmp_state(chat_id)
                return await message.reply_text("**تم التخطي، الطابور فارغ.**")
            nxt = queue[0]
            await _start_track(chat_id, nxt, seek_seconds=0)
            return await message.reply_photo(
                photo=nxt.get("cover_path") or f"https://i.ytimg.com/vi/{nxt['vid_id']}/hqdefault.jpg",
                caption=(
                    f"**تم التخطي وتشغيل التالي**\n\n"
                    f"**العنوان :** `{nxt['title'][:45]}`\n"
                    f"**المدة :** `{nxt['duration_min']}`\n"
                    f"**بواسطة :** {nxt['by']}"
                ),
                reply_markup=_rtmp_kb(chat_id, paused=False),
            )

        m = re.match(r"^(قدم|مرر|رجع|seek|seekback)(?:\s+(?:استيريو|ستيريو))?\s+(\d+)$", low)
        if m:
            action, sec_str = m.group(1), m.group(2)
            delta = int(sec_str)
            cur = _played_sec(chat_id)
            new_pos = max(0, cur - delta) if action in ("رجع", "seekback") else (cur + delta)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await message.reply_text("**الوقت المطلوب يتجاوز مدة الاغنية.**")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            return await message.reply_text(f"**تم الانتقال الى {_fmt_sec(new_pos)}.**")


@app.on_message(
    filters.regex(r"^[/!.]?(استيريو فيديو|ستيريو فيديو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$")
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

    m = re.match(r"^(استيريو فيديو|ستيريو فيديو|بث فيديو|vrtmp|استيريو|ستيريو|بث|rtmp)(?:\s+(.+))?$", raw_text, flags=re.I)
    if not m:
        return

    cmd_word = m.group(1).lower()
    query = (m.group(2) or "").strip()
    is_video = cmd_word in ("استيريو فيديو", "ستيريو فيديو", "بث فيديو", "vrtmp")

    if not query and message.reply_to_message:
        query = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()

    if not query:
        return await message.reply_text(
            "**الاستخدام:**\n"
            "- صوت: `استيريو اسم الاغنية`\n"
            "- فيديو: `استيريو فيديو اسم الاغنية`\n"
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
            duration_sec = int(details.get("duration_sec") or _parse_dur(duration_min))

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

            mode_label = "فيديو + Stereo" if is_video else "صوت Stereo"
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
                reply_markup=_rtmp_kb(chat_id, paused=False),
            )
        except ChatAdminRequired:
            await mystic.edit_text("**ارفع الحساب المساعد مشرفا بصلاحية ادارة المحادثات الصوتية.**")
        except Exception as e:
            await mystic.edit_text(f"**خطأ:** `{type(e).__name__}: {e}`")


@app.on_callback_query(filters.regex(r"^rtmp_ctl\|") & ~BANNED_USERS, group=RTMP_GROUP)
@RTMPCheck
async def rtmp_buttons_cb(client, callback_query: CallbackQuery, _, chat_id: int):
    action = callback_query.data.split("|")[1]

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
            await _clear_rtmp_state(chat_id)
            with suppress(Exception):
                await callback_query.message.edit_caption(
                    f"**تم ايقاف البث بواسطة :** {callback_query.from_user.mention}"
                )
            return

        if action == "pause":
            st["offset"] = _played_sec(chat_id)
            st["paused"] = True
            await music_off(chat_id)
            await _kill_proc(chat_id)
            await callback_query.answer(f"تم التوقف عند {_fmt_sec(st['offset'])}")
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_kb(chat_id, paused=True))
            return

        if action == "resume":
            await callback_query.answer("جاري الاستئناف...")
            await _start_track(chat_id, st["track"], seek_seconds=int(st.get("offset", 0)))
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_kb(chat_id, paused=False))
            return

        if action in ("rew15", "fwd15"):
            cur = _played_sec(chat_id)
            new_pos = max(0, cur - 15) if action == "rew15" else (cur + 15)
            dur = st["track"].get("duration_sec", 0)
            if dur > 0 and new_pos >= dur - 5:
                return await callback_query.answer("نهاية الاغنية.", show_alert=True)
            await callback_query.answer(f"الانتقال الى {_fmt_sec(new_pos)}")
            await _start_track(chat_id, st["track"], seek_seconds=new_pos)
            with suppress(Exception):
                await callback_query.message.edit_reply_markup(reply_markup=_rtmp_kb(chat_id, paused=False))
            return

        if action == "skip":
            queue = _rtmp_queues.get(chat_id) or []
            if queue:
                queue.pop(0)
            if not queue:
                await callback_query.answer("تم التخطي، الطابور فارغ.")
                await _kill_proc(chat_id)
                await _clear_rtmp_state(chat_id)
                with suppress(Exception):
                    await callback_query.message.edit_caption(
                        f"**تم تخطي الاغنية وانتهاء الطابور بواسطة :** {callback_query.from_user.mention}"
                    )
                return
            nxt = queue[0]
            await callback_query.answer("جاري تشغيل التالي...")
            await _start_track(chat_id, nxt, seek_seconds=0)
            with suppress(Exception):
                await callback_query.message.delete()
            await app.send_photo(
                chat_id,
                photo=nxt.get("cover_path") or f"https://i.ytimg.com/vi/{nxt['vid_id']}/hqdefault.jpg",
                caption=(
                    f"**تم التخطي وتشغيل التالي**\n\n"
                    f"**العنوان :** `{nxt['title'][:45]}`\n"
                    f"**المدة :** `{nxt['duration_min']}`\n"
                    f"**بواسطة :** {nxt['by']}"
                ),
                reply_markup=_rtmp_kb(chat_id, paused=False),
            )
