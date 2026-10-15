import asyncio
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

try:
    from youtubesearchpython.aio import VideosSearch
    HAS_SEARCH = True
except ImportError:
    HAS_SEARCH = False

logging.basicConfig(level=logging.ERROR)
def LOGGER(name): return logging.getLogger(name)

HAS_ARIA2C = bool(shutil.which("aria2c"))


class Config:
    if os.path.exists("/dev/shm"):
        DOWNLOAD_PATH = "/dev/shm/AnnieSongDownloads"
    else:
        DOWNLOAD_PATH = os.path.abspath("downloads_songs")

    MAX_WORKERS = 12


os.makedirs(Config.DOWNLOAD_PATH, exist_ok=True)


class SongDownloaderAPI:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)
        self.force_high_quality = False
        self.playlist_limit = 10

    def set_limit(self, limit: int):
        self.playlist_limit = int(limit)

    def open_limit(self):
        self.playlist_limit = 0

    def reset_limit(self):
        self.playlist_limit = 10

    def enable_quality(self):
        self.force_high_quality = True

    def disable_quality(self):
        self.force_high_quality = False

    async def download(self, link: str, is_video: bool = False):
        if "googleusercontent.com" in link and "v=" in link:
            try:
                link = f"https://www.youtube.com/watch?v={link.split('v=')[1].split('&')[0]}"
            except Exception:
                pass

        if not link.startswith(("http", "www")) and HAS_SEARCH:
            try:
                search = VideosSearch(link, limit=1)
                result = await search.next()
                if result and "result" in result and len(result["result"]) > 0:
                    vid = result["result"][0]["id"]
                    link = f"https://www.youtube.com/watch?v={vid}"
                else:
                    link = f"ytsearch1:{link}"
            except Exception:
                link = f"ytsearch1:{link}"

        # إذا كان الملف موجوداً بالفعل في الرام (/dev/shm) نعيده فوراً في 0ms
        if "v=" in link:
            vid_id = link.split("v=")[1].split("&")[0]
            if len(vid_id) == 11:
                exts = (".mp4",) if is_video else (".m4a", ".mp3", ".webm")
                for ext in exts:
                    existing = os.path.join(Config.DOWNLOAD_PATH, f"{vid_id}{ext}")
                    if os.path.exists(existing) and os.path.getsize(existing) > 1024:
                        return existing, False

        loop = asyncio.get_running_loop()

        if is_video:
            if self.force_high_quality:
                target_clients = ["visionos", "tv_samsung"]
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                # صيغة 18 (360p MP4 مدمجة جاهزة) تنزل في أجزاء من الثانية بدون دمج FFmpeg
                target_clients = ["visionos", "android"]
                fmt = "18/b[ext=mp4][height<=360]/b[ext=mp4][height<=480]/b[ext=mp4]/best"
        else:
            target_clients = ["visionos", "android"]
            # صيغة 139/140 (m4a أصلية خفيفة جداً وسريعة الرفع)
            fmt = (
                "140/ba[ext=m4a]/ba/b"
                if self.force_high_quality
                else "139/140/ba[abr<=128][ext=m4a]/ba[ext=m4a]/ba/b"
            )

        is_playlist = "list=" in link and "watch?v=" not in link

        def _download_native():
            out_tmpl = os.path.join(Config.DOWNLOAD_PATH, "%(id)s.%(ext)s")

            ydl_opts = {
                "format": fmt,
                "outtmpl": out_tmpl,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "cookiefile": None,
                "force_ipv4": True,
                "source_address": "0.0.0.0",
                "geo_bypass": False,
                "js_runtimes": {"node": {}, "deno": {}},
                "remote_components": ["ejs:github"],
                "extractor_args": {
                    "youtube": {
                        "player_client": target_clients,
                        "player_skip": ["webpage", "configs", "initial_data", "js"],
                        "skip": ["hls", "dash", "translated_subs"],
                        "formats": ["missing_pot"],
                        "fetch_pot": ["never"],
                    }
                },
                "concurrent_fragment_downloads": 16,
                "http_chunk_size": 10485760,
                "buffersize": 1024 * 1024 * 5,
                "retries": 3,
                "socket_timeout": 8,
                "noplaylist": not is_playlist,
                "ignoreerrors": True,
            }

            # استخدام aria2c بـ 16 اتصالاً متوازياً عند توفره في النظام (موجود في الـ Dockerfile)
            if HAS_ARIA2C and not (is_video and self.force_high_quality):
                ydl_opts["external_downloader"] = "aria2c"
                ydl_opts["external_downloader_args"] = {
                    "aria2c": [
                        "-x16",
                        "-s16",
                        "-j16",
                        "-k1M",
                        "--file-allocation=none",
                        "--console-log-level=error",
                        "--summary-interval=0",
                    ]
                }

            if is_playlist and self.playlist_limit > 0:
                ydl_opts["playlistend"] = self.playlist_limit

            if is_video and self.force_high_quality:
                ydl_opts["merge_output_format"] = "mp4"

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    if not info:
                        return None

                    if "entries" in info:
                        entries = [e for e in info["entries"] if e]
                        if entries:
                            return ydl.prepare_filename(entries[0])
                    return ydl.prepare_filename(info)
            except Exception as e:
                LOGGER("SongDownloader").error(f"Download Error: {e}")
            return None

        try:
            file_path = await loop.run_in_executor(self.pool, _download_native)
            if file_path and os.path.exists(file_path):
                return file_path, False
        except Exception:
            pass

        return None, False


SongDownloader = SongDownloaderAPI()
