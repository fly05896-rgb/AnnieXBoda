# Authored By Certified Coders © 2026
# Ultra-Fast YouTube Downloader (Zero-JS visionOS + Shared RAM Player Cache + 20ms RAM Audio Remux)

import asyncio
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

import yt_dlp

import config
from AnnieXMedia.utils.tuning import CHUNK_SIZE, CPU, SEM, YTDLP_TIMEOUT

log = logging.getLogger("AnnieXMedia.SongDownloader")

# ── تفعيل الكاش الدائم لمفسر الجافاسكريبت والـ Player داخل yt-dlp ──
_SHARED_CODE_CACHE: dict = {}
_SHARED_PLAYER_CACHE: dict = {}

try:
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
    EJSBaseJCP._ENABLE_PREPROCESSED_PLAYER_CACHE = True
except Exception:
    pass

try:
    from yt_dlp.extractor.youtube._video import YoutubeIE
    _orig_yt_init = YoutubeIE.__init__

    def _fast_yt_init(self, *args, **kwargs):
        _orig_yt_init(self, *args, **kwargs)
        self._code_cache = _SHARED_CODE_CACHE
        self._player_cache = _SHARED_PLAYER_CACHE

    YoutubeIE.__init__ = _fast_yt_init
except Exception:
    pass


class SongDownloaderAPI:
    def __init__(self):
        self.download_dir = getattr(config, "DOWNLOAD_PATH", "/dev/shm/AnnieDownloads")
        if not os.path.exists(self.download_dir):
            try:
                os.makedirs(self.download_dir, exist_ok=True)
            except Exception:
                self.download_dir = "downloads"
                os.makedirs(self.download_dir, exist_ok=True)

        self.cache_dir = "/dev/shm/ytdlp_cache"
        os.makedirs(self.cache_dir, exist_ok=True)

        self.executor = ThreadPoolExecutor(max_workers=CPU)
        self.has_aria2 = shutil.which("aria2c") is not None
        self.has_ffmpeg = shutil.which("ffmpeg") is not None
        self._file_cache: dict[str, str] = {}

        if self.has_aria2:
            log.info("🚀 [SongDownloader] Aria2c Turbo Engine Ready.")

    def _get_cookie_file(self) -> Optional[str]:
        paths = ["cookies/cookies.txt", "AnnieXMedia/assets/cookies.txt", "cookies.txt"]
        for p in paths:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                return p
        return None

    def _extract_vid_id(self, url: str) -> str:
        if "v=" in url:
            return url.split("v=")[1].split("&")[0]
        if "youtu.be/" in url:
            return url.split("youtu.be/")[1].split("?")[0]
        if "shorts/" in url:
            return url.split("shorts/")[1].split("?")[0]
        return str(abs(hash(url)))

    def _find_cached_file(self, vid_id: str, is_video: bool) -> Optional[str]:
        cache_key = f"{vid_id}_{'v' if is_video else 'a'}"
        cached = self._file_cache.get(cache_key)
        if cached and os.path.exists(cached) and os.path.getsize(cached) > 0:
            return cached

        prefix = f"{vid_id}_v." if is_video else f"{vid_id}_a."
        try:
            for fname in os.listdir(self.download_dir):
                if fname.startswith(prefix) and not fname.endswith((".part", ".ytdl", ".aria2")):
                    full_path = os.path.join(self.download_dir, fname)
                    if os.path.getsize(full_path) > 0:
                        self._file_cache[cache_key] = full_path
                        return full_path
        except Exception:
            pass
        return None

    async def _strip_video_to_m4a(self, input_path: str, vid_id: str) -> str:
        """
        فصل مسار الصوت النقي (AAC m4a) من حاوية MP4 في الرامات بدون إعادة ترميز (-vn -acodec copy)
        بيحول ملف 12 ميجا إلى ~1.4 ميجا في 0.05 - 0.15 ثانية فقط!
        """
        if not self.has_ffmpeg or not os.path.exists(input_path):
            return input_path

        out_m4a = os.path.join(self.download_dir, f"{vid_id}_a.m4a")
        if os.path.abspath(input_path) == os.path.abspath(out_m4a):
            temp_in = os.path.join(self.download_dir, f"{vid_id}_raw.mp4")
            try:
                os.replace(input_path, temp_in)
                input_path = temp_in
            except Exception:
                return input_path

        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                input_path,
                "-vn",
                "-acodec",
                "copy",
                out_m4a,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            if os.path.exists(out_m4a) and os.path.getsize(out_m4a) > 10240:
                try:
                    os.remove(input_path)
                except Exception:
                    pass
                return out_m4a
        except Exception as e:
            log.warning(f"Audio stream copy skipped: {e}")

        return input_path

    def _fast_ytdlp_download(self, url: str, vid_id: str, is_video: bool) -> Tuple[Optional[str], int]:
        cookie_file = self._get_cookie_file()
        suffix = "v" if is_video else "a"
        outtmpl = f"{self.download_dir}/{vid_id}_{suffix}.%(ext)s"

        # visionos في المقدمة دائماً لأنه لا يحتاج base.js ولا deno ولا PO Token (استخراج في 0.15 ثانية)
        target_clients = ["visionos", "tv_samsung", "android", "web_embedded"]

        # للصوت: نفضل m4a/webm الصغير إن وجد، وإلا نأخذ itag 18 الفوري من visionos ونفصل منه الصوت في 0.1 ثانية
        fmt = (
            "18/best[ext=mp4][height<=720]/best[ext=mp4]/best"
            if is_video
            else "140/139/251/250/249/bestaudio[ext=m4a]/bestaudio/18/best"
        )

        ydl_opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "noplaylist": True,
            "overwrites": True,
            "cachedir": self.cache_dir,
            " cookiefile": cookie_file,
            "cookiefile": cookie_file,
            "socket_timeout": 10,
            "retries": 2,
            "fragment_retries": 2,
            "concurrent_fragment_downloads": 16,
            "http_chunk_size": CHUNK_SIZE,
            "buffersize": 1024 * 1024,
            "js_runtimes": {"deno": {}, "node": {}},
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {
                    "player_client": target_clients,
                }
            },
        }

        if self.has_aria2:
            ydl_opts["external_downloader"] = "aria2c"
            ydl_opts["external_downloader_args"] = [
                "--max-connection-per-server=16",
                "--split=16",
                "--min-split-size=512K",
                "--disk-cache=64M",
                "--file-allocation=none",
                "--summary-interval=0",
                "--console-log-level=error",
                "--quiet=true",
            ]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    return None, 0
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                vcodec = info.get("vcodec") or "none"
                has_video_stream = vcodec != "none"

                filepath = ydl.prepare_filename(info)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return filepath, (1 if has_video_stream else 0)

                 found = self._find_cached_file(vid_id, is_video)
                return found, (1 if has_video_stream else 0)
        except Exception as e:
            log.warning(f"Primary download error ({e}), retrying native HTTP...")
            ydl_opts.pop("external_downloader", None)
            ydl_opts.pop("external_downloader_args", None)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        return None, 0
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    vcodec = info.get("vcodec") or "none"
                    has_video_stream = vcodec != "none"
                    filepath = ydl.prepare_filename(info)
                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                        return filepath, (1 if has_video_stream else 0)
            except Exception as e2:
                log.error(f"Fallback download failed: {e2}")

        return None, 0

    async def download(self, url: str, is_video: bool = False) -> Tuple[Optional[str], bool]:
        vid_id = self._extract_vid_id(url)
        cache_key = f"{vid_id}_{'v' if is_video else 'a'}"

        cached = self._find_cached_file(vid_id, is_video)
        if cached:
            return cached, False

        loop = asyncio.get_running_loop()

        async with SEM:
            try:
                file_path, has_video_stream = await asyncio.wait_for(
                    loop.run_in_executor(
                        self.executor,
                        self._fast_ytdlp_download,
                        url,
                        vid_id,
                        is_video,
                    ),
                    timeout=YTDLP_TIMEOUT,
                )

                if file_path and os.path.exists(file_path):
                    if not is_video:
                        # لو الملف النازل من visionos فيه فيديو (itag 18 حجمه 10-12 ميجا)، نفصل الصوت الصافي (1.3 ميجا m4a) في 0.1 ثانية!
                        if has_video_stream or file_path.endswith(".mp4") or os.path.getsize(file_path) > 4 * 1024 * 1024:
                            file_path = await self._strip_video_to_m4a(file_path, vid_id)
                        elif not file_path.endswith((".m4a", ".mp3", ".ogg", ".opus")):
                            base = os.path.splitext(file_path)[0]
                            new_path = f"{base}.m4a"
                            try:
                                os.replace(file_path, new_path)
                                file_path = new_path
                            except Exception:
                                pass

                    self._file_cache[cache_key] = file_path
                    return file_path, False

            except asyncio.TimeoutError:
                log.error(f"Timeout downloading: {url}")
            except Exception as e:
                log.error(f"Unexpected error in SongDownloader: {e}")

        return None, False

    async def cleanup_old_files(self, max_age_seconds: int = 1800):
        try:
            now = time.time()
            for fname in os.listdir(self.download_dir):
                fpath = os.path.join(self.download_dir, fname)
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception:
            pass


SongDownloader = SongDownloaderAPI()
